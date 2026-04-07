"""Step 5: Validate screenshot URLs and upload leads to PlusVibe.

Usage:
    python3 validate_and_upload.py output/email_campaigns.csv
    python3 validate_and_upload.py output/email_campaigns.csv --dry-run

Only leads with:
  - status == "ok"
  - valid email
  - working screenshot URL (HTTP 200)
are uploaded. Leads with broken/missing screenshots are SKIPPED.
"""
import argparse, asyncio, csv, json, os, sys
import aiohttp
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

API_KEY      = os.getenv("PLUSVIBE_API_KEY")
BASE_URL     = os.getenv("PLUSVIBE_API_URL", "https://api.plusvibe.ai/api/v1")
WORKSPACE_ID = os.getenv("PLUSVIBE_WORKSPACE_ID", "69adcb166f1add4e083d64ee")
CAMPAIGN_ID  = os.getenv("PLUSVIBE_CAMPAIGN_ID", "69d0537061e40aacb9647685")

HEADERS      = {"x-api-key": API_KEY, "Content-Type": "application/json"}
BATCH_SIZE   = 25
SCREENSHOT_CHECK_CONCURRENCY = 20


# ── Screenshot validation ────────────────────────────────────────────────────

async def check_screenshot(session, url):
    """HEAD request to verify screenshot URL is accessible."""
    if not url or not url.startswith("http"):
        return url, False, "empty/invalid URL"
    try:
        async with session.head(url, timeout=aiohttp.ClientTimeout(total=10),
                                allow_redirects=True) as resp:
            if resp.status == 200:
                return url, True, "ok"
            else:
                return url, False, f"HTTP {resp.status}"
    except Exception as e:
        return url, False, str(e)


async def validate_screenshots(urls):
    """Check all screenshot URLs concurrently."""
    sem = asyncio.Semaphore(SCREENSHOT_CHECK_CONCURRENCY)
    results = {}

    async def _check(url):
        async with sem:
            return await check_screenshot(session, url)

    async with aiohttp.ClientSession() as session:
        tasks = [_check(u) for u in urls]
        for i, coro in enumerate(asyncio.as_completed(tasks)):
            url, ok, reason = await coro
            results[url] = (ok, reason)
            if (i + 1) % 50 == 0 or (i + 1) == len(urls):
                valid = sum(1 for v, _ in results.values() if v)
                print(f"  Checked {i+1}/{len(urls)} screenshots — {valid} valid")

    return results


# ── Lead building ────────────────────────────────────────────────────────────

def build_lead(row, screenshot_valid):
    """Convert a CSV row to a PlusVibe lead object. Returns None if invalid."""
    email = (row.get("email") or "").strip()
    if not email or row.get("status") != "ok":
        return None, "no email or failed status"

    screenshot_url = (row.get("screenshot_url") or "").strip()

    # Skip leads without working screenshots
    if not screenshot_url:
        return None, "no screenshot URL"
    if not screenshot_valid.get(screenshot_url, (False,))[0]:
        reason = screenshot_valid.get(screenshot_url, (False, "not checked"))[1]
        return None, f"broken screenshot: {reason}"

    body_html = row.get("custom_custom_body_html", "") or row.get("custom_custom_body", "")
    brand = (row.get("brand_name") or "").strip()

    # Prepend screenshot to email body
    screenshot_block = (
        '<p>This is still your website, right?</p>'
        f'<p><img src="{screenshot_url}" alt="{brand} website" '
        'style="max-width:100%;border-radius:4px;" /></p>'
    )
    body_html = screenshot_block + body_html

    return {
        "email": email,
        "first_name": (row.get("first_name") or "").strip(),
        "company_name": brand,
        "company_website": (row.get("domain") or "").strip(),
        "custom_variables": {
            "custom_subject": row.get("custom_custom_subject", ""),
            "email_body":     body_html,
            "brand_name":     brand,
            "niche":          row.get("detected_niche", ""),
        },
    }, "ok"


# ── Upload ───────────────────────────────────────────────────────────────────

async def upload_batch(session, leads, batch_num):
    """Upload a batch of leads to PlusVibe."""
    payload = {
        "workspace_id": WORKSPACE_ID,
        "campaign_id": CAMPAIGN_ID,
        "skip_if_in_workspace": False,
        "skip_lead_in_active_pause_camp": False,
        "skip_lead_for_active_only_camp": False,
        "resume_camp_if_completed": False,
        "is_overwrite": True,
        "leads": leads,
    }
    async with session.post(
        f"{BASE_URL}/lead/add",
        json=payload,
        headers=HEADERS,
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        data = await resp.json()
        status = "OK" if resp.status in (200, 201) else f"FAIL ({resp.status})"
        print(f"  Batch {batch_num}: {status}")
        return resp.status in (200, 201)


async def upload_all(leads, dry_run=False):
    """Upload leads in batches."""
    if dry_run:
        print(f"\n[DRY RUN] Would upload {len(leads)} leads in {(len(leads)-1)//BATCH_SIZE + 1} batches.")
        return

    print(f"\nUploading {len(leads)} leads in batches of {BATCH_SIZE}...")
    ok = 0
    fail = 0
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(leads), BATCH_SIZE):
            batch = leads[i:i+BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            success = await upload_batch(session, batch, batch_num)
            if success:
                ok += len(batch)
            else:
                fail += len(batch)

    print(f"\nDone — {ok} uploaded, {fail} failed")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Validate screenshots and upload leads to PlusVibe")
    parser.add_argument("input_csv", help="Path to email_campaigns.csv")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually upload")
    parser.add_argument("--campaign-id", default=None, help="Override campaign ID")
    args = parser.parse_args()

    if not API_KEY:
        print("Error: PLUSVIBE_API_KEY not set in .env")
        sys.exit(1)

    global CAMPAIGN_ID
    if args.campaign_id:
        CAMPAIGN_ID = args.campaign_id

    # Read CSV
    with open(args.input_csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows)} rows from {args.input_csv}")

    # Collect screenshot URLs
    screenshot_urls = list(set(
        r.get("screenshot_url", "").strip()
        for r in rows
        if r.get("screenshot_url", "").strip()
    ))
    print(f"\nValidating {len(screenshot_urls)} unique screenshot URLs...")
    screenshot_valid = asyncio.run(validate_screenshots(screenshot_urls))

    valid_count = sum(1 for v, _ in screenshot_valid.values() if v)
    broken_count = len(screenshot_urls) - valid_count
    print(f"\n  Working: {valid_count}")
    print(f"  Broken:  {broken_count}")

    # Build leads (only those with working screenshots)
    leads = []
    skipped = {"no_email_or_failed": 0, "no_screenshot": 0, "broken_screenshot": 0}
    for row in rows:
        lead, reason = build_lead(row, screenshot_valid)
        if lead:
            leads.append(lead)
        elif "no email" in reason or "failed" in reason:
            skipped["no_email_or_failed"] += 1
        elif "no screenshot" in reason:
            skipped["no_screenshot"] += 1
        elif "broken" in reason:
            skipped["broken_screenshot"] += 1

    print(f"\nLeads to upload: {len(leads)}")
    print(f"Skipped:")
    for reason, count in skipped.items():
        if count:
            print(f"  {reason}: {count}")

    if not leads:
        print("\nNo valid leads to upload.")
        sys.exit(0)

    # Upload
    asyncio.run(upload_all(leads, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
