"""Generate personalized email fields for all leads and output a CSV.

Usage:
    python3 generate_email_csv.py                         # full list
    python3 generate_email_csv.py --limit 20              # first N leads
    python3 generate_email_csv.py --concurrency 10        # parallel workers
"""
import asyncio
import csv
import argparse
import os
import re
import sys
import time
import aiohttp
import openpyxl
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
sys.path.insert(0, os.path.dirname(__file__))

from agents.email_agent import _generate_brand_love, _build_custom_body, clean_brand_name

EXCEL_PATH = (
    "/Users/pawanpere/Library/Application Support/Claude/local-agent-mode-sessions"
    "/fcf8b0e4-afb1-437d-9454-920dd7d7e6d4/a9885e9d-f4d8-463d-ace2-e653906243f0"
    "/local_5436ff55-e6f5-447d-b057-d1ec61c3a17e/outputs/verified_leads_only.xlsx"
)
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "output", "email_campaigns.csv")

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
}


async def fetch_site_description(session: aiohttp.ClientSession, domain: str) -> str:
    url = f"https://{domain}"
    try:
        async with session.get(
            url, headers=FETCH_HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
            allow_redirects=True
        ) as resp:
            if resp.status == 200:
                html = await resp.text(errors="ignore")
                text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
                text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                return text[:500]
    except Exception:
        pass
    return ""


async def process_lead(semaphore: asyncio.Semaphore, session: aiohttp.ClientSession,
                       lead: dict, index: int, total: int) -> dict:
    async with semaphore:
        brand_raw = lead.get("Company", "") or ""
        brand = clean_brand_name(brand_raw)
        domain = lead.get("Company Domain", "") or ""
        industry = lead.get("Industry", "") or "ecommerce"
        first_name = lead.get("First Name", "") or ""
        email = lead.get("Verified Email", "") or ""

        site_desc = await fetch_site_description(session, domain)
        brand_love, detected_niche = await _generate_brand_love(brand, industry, site_desc)
        effective_niche = detected_niche or industry

        custom_subject = f"TikTok Shop Opportunity - {brand}"

        if brand_love:
            custom_body_html = _build_custom_body(brand_love, brand, effective_niche)
            # Plain text version for CSV readability
            custom_body_text = re.sub(r"<[^>]+>", "", custom_body_html).strip()
        else:
            custom_body_html = ""
            custom_body_text = ""

        status = "ok" if brand_love else "failed"
        print(f"  [{index}/{total}] {brand} ({domain}) — {status}")

        return {
            "first_name": first_name,
            "email": email,
            "brand_name": brand,
            "domain": domain,
            "detected_niche": effective_niche,
            "custom_custom_subject": custom_subject,
            "custom_custom_body": custom_body_text,
            "custom_custom_body_html": custom_body_html,
            "status": status,
        }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Process first N leads (0 = all)")
    parser.add_argument("--concurrency", type=int, default=5, help="Parallel workers (default 5)")
    args = parser.parse_args()

    wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True)
    ws = wb.active
    rows = list(ws.rows)
    headers = [cell.value for cell in rows[0]]
    data_rows = rows[1:]
    if args.limit:
        data_rows = data_rows[:args.limit]

    leads = [dict(zip(headers, [c.value for c in row])) for row in data_rows]
    total = len(leads)
    print(f"Processing {total} leads with concurrency={args.concurrency}...")
    print()

    os.makedirs(os.path.join(os.path.dirname(__file__), "output"), exist_ok=True)

    semaphore = asyncio.Semaphore(args.concurrency)
    start = time.time()

    async with aiohttp.ClientSession() as session:
        tasks = [
            process_lead(semaphore, session, lead, i + 1, total)
            for i, lead in enumerate(leads)
        ]
        results = await asyncio.gather(*tasks)

    elapsed = time.time() - start
    ok = sum(1 for r in results if r["status"] == "ok")
    failed = total - ok

    # Write CSV
    fieldnames = [
        "first_name", "email", "brand_name", "domain",
        "detected_niche", "custom_custom_subject",
        "custom_custom_body", "custom_custom_body_html", "status",
    ]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print()
    print(f"Done in {elapsed:.0f}s — {ok} ok, {failed} failed")
    print(f"CSV saved to: {OUTPUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
