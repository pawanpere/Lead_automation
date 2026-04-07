"""Full end-to-end pipeline: Domains -> Leads -> Verify -> Personalize -> Validate -> Upload.

Usage:
    python3 run_pipeline.py domains.txt
    python3 run_pipeline.py domains.txt --screenshots screenshots.xlsx
    python3 run_pipeline.py domains.txt --skip-scrape --leads output/leads_raw.csv
    python3 run_pipeline.py domains.txt --skip-verify --leads output/leads_verified.csv
    python3 run_pipeline.py domains.txt --dry-run

Pipeline steps:
    1. Scrape leads from domains via Apify
    2. Verify emails via Truelist
    3. Match screenshots from provided Excel file
    4. Personalize emails via LLM (brand love line)
    5. Validate all screenshot URLs are working
    6. Upload to PlusVibe (only leads with working screenshots)
"""
import argparse, asyncio, csv, os, sys, time
import openpyxl
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# ── Imports from existing modules ────────────────────────────────────────────
from scrape_leads import read_domains, start_run, wait_for_run, fetch_results, save_csv
from verify_emails import verify_all, VALID_STATES
from generate_email_csv import process_lead, write_csv_header, append_csv_row
from validate_and_upload import validate_screenshots, build_lead, upload_all


def load_screenshots(xlsx_path):
    """Load screenshot URLs from the screenshots Excel file, keyed by domain."""
    screenshots = {}
    if not xlsx_path or not os.path.exists(xlsx_path):
        return screenshots

    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb.active
    rows = list(ws.rows)
    headers = [cell.value for cell in rows[0]]

    for row in rows[1:]:
        r = dict(zip(headers, [c.value for c in row]))
        domain = (r.get("Domain") or "").strip().lower()
        url = (r.get("Screenshot URL") or "").strip()
        if domain and url:
            screenshots[domain] = url

    print(f"Loaded {len(screenshots)} screenshot URLs from {xlsx_path}")
    return screenshots


def main():
    parser = argparse.ArgumentParser(description="Full lead automation pipeline")
    parser.add_argument("domains_file", help="Path to domains file (txt or csv)")
    parser.add_argument("--screenshots", "-s", default=None,
                        help="Path to screenshots Excel file with 'Domain' and 'Screenshot URL' columns")
    parser.add_argument("--output-dir", "-d", default="output", help="Output directory")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip Apify scraping step")
    parser.add_argument("--skip-verify", action="store_true", help="Skip Truelist verification step")
    parser.add_argument("--leads", default=None, help="Path to existing leads CSV (use with --skip-scrape or --skip-verify)")
    parser.add_argument("--campaign-id", default=None, help="PlusVibe campaign ID override")
    parser.add_argument("--concurrency", type=int, default=8, help="LLM concurrency (default: 8)")
    parser.add_argument("--dry-run", action="store_true", help="Don't upload to PlusVibe")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    raw_csv      = os.path.join(args.output_dir, "leads_raw.csv")
    verified_csv = os.path.join(args.output_dir, "leads_verified.csv")
    campaign_csv = os.path.join(args.output_dir, "email_campaigns.csv")

    # ── Step 1: Scrape leads ─────────────────────────────────────────────────
    if args.skip_scrape:
        print("=== Step 1: SKIPPED (--skip-scrape) ===\n")
        if args.leads:
            raw_csv = args.leads
        if args.skip_verify and args.leads:
            verified_csv = args.leads
    else:
        print("=== Step 1: Scraping leads via Apify ===\n")
        domains = read_domains(args.domains_file)
        if not domains:
            print("No domains found.")
            sys.exit(1)
        run_id, dataset_id = start_run(domains)
        status = wait_for_run(run_id)
        if status != "SUCCEEDED":
            print(f"Apify run failed: {status}")
            sys.exit(1)
        leads = fetch_results(dataset_id)
        save_csv(leads, raw_csv)
        print()

    # ── Step 2: Verify emails ────────────────────────────────────────────────
    if args.skip_verify:
        print("=== Step 2: SKIPPED (--skip-verify) ===\n")
    else:
        print("=== Step 2: Verifying emails via Truelist ===\n")
        with open(raw_csv, encoding="utf-8") as f:
            leads = list(csv.DictReader(f))

        emails = list(set(l["email"] for l in leads if l.get("email")))
        print(f"  Verifying {len(emails)} unique emails...")
        results = asyncio.run(verify_all(emails))

        verified = []
        for lead in leads:
            email = lead.get("email", "")
            state, sub = results.get(email, ("unknown", ""))
            lead["email_state"] = state
            lead["email_sub_state"] = sub
            if state in VALID_STATES:
                verified.append(lead)

        print(f"\n  Verified: {len(verified)} / {len(leads)}")
        if verified:
            fieldnames = list(verified[0].keys())
            with open(verified_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(verified)
        print()

    # ── Step 3: Match screenshots ────────────────────────────────────────────
    print("=== Step 3: Matching screenshots ===\n")
    screenshots = load_screenshots(args.screenshots) if args.screenshots else {}

    with open(verified_csv, encoding="utf-8") as f:
        verified_leads = list(csv.DictReader(f))

    matched = 0
    for lead in verified_leads:
        domain = (lead.get("company_domain") or lead.get("domain") or "").strip().lower()
        if domain in screenshots:
            lead["screenshot_url"] = screenshots[domain]
            matched += 1
        else:
            lead["screenshot_url"] = ""

    print(f"  Leads with screenshots: {matched} / {len(verified_leads)}")
    # Remove leads without screenshots
    leads_with_screenshots = [l for l in verified_leads if l.get("screenshot_url")]
    print(f"  Leads without screenshots (will be skipped): {len(verified_leads) - matched}")
    print()

    # ── Step 4: Personalize emails via LLM ───────────────────────────────────
    print(f"=== Step 4: Personalizing {len(leads_with_screenshots)} leads via LLM ===\n")

    import re
    _LEGAL_SUFFIXES = re.compile(
        r",?\s*\b(LLC|L\.L\.C\.|Inc\.?|Corp\.?|Ltd\.?|Co\.?|LLP|L\.P\.|PLC|GmbH|S\.A\.)\b\.?",
        re.IGNORECASE,
    )

    def clean_brand_name(name):
        cleaned = re.sub(r"\s*\(.*?\)", "", name)
        cleaned = _LEGAL_SUFFIXES.sub("", cleaned)
        return cleaned.strip(" ,.")

    from utils.llm_client import generate_json
    import aiohttp

    PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "email_brandlove.txt")
    with open(PROMPT_PATH) as f:
        SYSTEM_PROMPT = f.read()

    async def fetch_site_description(session, domain):
        """Fetch homepage text for LLM context."""
        url = f"https://{domain}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10),
                                   allow_redirects=True,
                                   headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status != 200:
                    return ""
                html = await resp.text()
                from html.parser import HTMLParser
                class TextExtractor(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.text = []
                        self.skip = False
                    def handle_starttag(self, tag, attrs):
                        if tag in ("script", "style", "noscript"):
                            self.skip = True
                    def handle_endtag(self, tag):
                        if tag in ("script", "style", "noscript"):
                            self.skip = False
                    def handle_data(self, data):
                        if not self.skip:
                            self.text.append(data.strip())
                extractor = TextExtractor()
                extractor.feed(html)
                return " ".join(t for t in extractor.text if t)[:2000]
        except Exception:
            return ""

    async def personalize_lead(session, lead):
        """Generate brand love line + detect niche for a single lead."""
        domain = (lead.get("company_domain") or lead.get("domain") or "").strip()
        brand = clean_brand_name(lead.get("company_name") or "")
        niche = lead.get("industry") or ""

        site_desc = await fetch_site_description(session, domain)

        user_prompt = (
            f"Brand: {brand}\n"
            f"URL: {domain}\n"
            f"Apollo Niche: {niche}\n"
            f"Site Description: {site_desc[:1500]}\n"
        )

        result = await generate_json(SYSTEM_PROMPT, user_prompt, max_tokens=200)

        if not result:
            return None

        brand_love = result.get("brand_love", "")
        detected_niche = result.get("niche", niche)

        # Strip em dashes
        brand_love = brand_love.replace("\u2014", ",").replace("\u2013", ",")

        # Build opportunity paragraph based on niche
        niche_lower = detected_niche.lower()
        if any(n in niche_lower for n in ("health", "supplement", "vitamin", "nutrition", "fitness")):
            opp = (f"I help {detected_niche} brands turn TikTok into a real sales channel through TikTok Shop. "
                   f"The way it works is creators make content showing how your product fits into their daily routines, "
                   f"and viewers can check out instantly right from the video in the new built-in marketplace on TikTok. "
                   f"Brands in the {detected_niche} space are seeing hundreds of daily orders through this, "
                   f"and the only cost to get started is sending out product samples.")
        elif any(n in niche_lower for n in ("food", "snack", "beverage", "drink")):
            opp = (f"I help {detected_niche} brands turn TikTok into a real sales channel through TikTok Shop. "
                   f"The way it works is creators make content showing how your product fits into their daily routines, "
                   f"and viewers can check out instantly right from the video in the new built-in marketplace on TikTok. "
                   f"Brands in the {detected_niche} space are seeing hundreds of daily orders through this, "
                   f"and the only cost to get started is sending out product samples.")
        else:
            opp = (f"I help {detected_niche} brands turn TikTok into a real sales channel through TikTok Shop. "
                   f"The way it works is creators make content showing how your product fits into their daily routines, "
                   f"and viewers can check out instantly right from the video in the new built-in marketplace on TikTok. "
                   f"Brands in the {detected_niche} space are seeing hundreds of daily orders through this, "
                   f"and the only cost to get started is sending out product samples.")

        body_html = f"<p>{brand_love}</p>\n<p>{opp}</p>"

        return {
            "brand_love": brand_love,
            "detected_niche": detected_niche,
            "custom_custom_body_html": body_html,
            "custom_custom_subject": f"TikTok Shop Opportunity - {brand}",
        }

    async def personalize_all(leads, concurrency=8):
        sem = asyncio.Semaphore(concurrency)
        results = []

        async def _process(lead):
            async with sem:
                result = await personalize_lead(session, lead)
                return lead, result

        async with aiohttp.ClientSession() as session:
            tasks = [_process(l) for l in leads]
            ok = 0
            fail = 0
            for i, coro in enumerate(asyncio.as_completed(tasks)):
                lead, result = await coro
                if result:
                    lead.update(result)
                    lead["status"] = "ok"
                    ok += 1
                else:
                    lead["status"] = "failed"
                    fail += 1
                results.append(lead)
                if (i + 1) % 25 == 0 or (i + 1) == len(leads):
                    print(f"  Personalized {i+1}/{len(leads)} — {ok} ok, {fail} failed")

        return results

    personalized = asyncio.run(personalize_all(leads_with_screenshots, args.concurrency))
    ok_leads = [l for l in personalized if l["status"] == "ok"]
    print(f"\n  Successfully personalized: {len(ok_leads)}")
    print()

    # Save to campaign CSV
    if ok_leads:
        fieldnames = [
            "first_name", "email", "brand_name", "domain", "detected_niche",
            "custom_custom_subject", "custom_custom_body_html", "screenshot_url", "status",
        ]
        with open(campaign_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for lead in ok_leads:
                lead["brand_name"] = clean_brand_name(lead.get("company_name", ""))
                lead["domain"] = lead.get("company_domain") or lead.get("domain", "")
                writer.writerow(lead)
        print(f"  Saved {len(ok_leads)} leads to {campaign_csv}")

    # ── Step 5: Validate screenshots & upload ────────────────────────────────
    print(f"\n=== Step 5: Validating screenshots & uploading to PlusVibe ===\n")

    with open(campaign_csv, encoding="utf-8") as f:
        final_leads = list(csv.DictReader(f))

    screenshot_urls = list(set(
        r["screenshot_url"] for r in final_leads if r.get("screenshot_url")
    ))
    print(f"  Validating {len(screenshot_urls)} screenshot URLs...")
    screenshot_valid = asyncio.run(validate_screenshots(screenshot_urls))

    valid_count = sum(1 for v, _ in screenshot_valid.values() if v)
    print(f"\n  Working: {valid_count}")
    print(f"  Broken:  {len(screenshot_urls) - valid_count}")

    # Build PlusVibe leads
    plusvibe_leads = []
    skipped = 0
    for row in final_leads:
        lead, reason = build_lead(row, screenshot_valid)
        if lead:
            plusvibe_leads.append(lead)
        else:
            skipped += 1

    print(f"\n  Ready to upload: {len(plusvibe_leads)}")
    print(f"  Skipped (broken screenshot / missing data): {skipped}")

    if plusvibe_leads:
        asyncio.run(upload_all(plusvibe_leads, dry_run=args.dry_run))

    print(f"\n{'='*60}")
    print(f"Pipeline complete!")
    print(f"  Leads uploaded: {len(plusvibe_leads)}")
    print(f"  Campaign CSV:   {campaign_csv}")


if __name__ == "__main__":
    main()
