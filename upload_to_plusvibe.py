"""Upload all leads from email_campaigns.csv to PlusVibe campaign with custom variables.

Usage:
    python3 upload_to_plusvibe.py
    python3 upload_to_plusvibe.py --batch-size 50   # leads per API call (default 25)
    python3 upload_to_plusvibe.py --dry-run          # print first batch, don't send
"""
import asyncio
import csv
import argparse
import json
import logging
import os
import sys
import aiohttp
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("plusvibe_upload")

API_KEY      = os.getenv("PLUSVIBE_API_KEY")
WORKSPACE_ID = os.getenv("PLUSVIBE_WORKSPACE_ID", "69adcb166f1add4e083d64ee")
CAMPAIGN_ID  = os.getenv("PLUSVIBE_CAMPAIGN_ID", "69d0537061e40aacb9647685")
BASE_URL     = os.getenv("PLUSVIBE_API_URL", "https://api.plusvibe.ai/api/v1")
CSV_PATH     = os.path.join(os.path.dirname(__file__), "output", "email_campaigns.csv")


async def upload_batch(session: aiohttp.ClientSession, leads: list, dry_run: bool) -> dict:
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

    if dry_run:
        print(json.dumps(payload, indent=2)[:2000])
        return {"status": "dry_run"}

    headers = {"x-api-key": API_KEY, "Content-Type": "application/json"}
    async with session.post(f"{BASE_URL}/lead/add", json=payload, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=60)) as resp:
        data = await resp.json()
        return data


def build_lead(row: dict):
    """Convert a CSV row to a PlusVibe lead object."""
    email = (row.get("email") or "").strip()
    if not email or row.get("status") != "ok":
        return None

    body_html = row.get("custom_custom_body_html", "") or row.get("custom_custom_body", "")
    screenshot_url = (row.get("screenshot_url") or "").strip()
    brand = (row.get("brand_name") or "").strip()

    # Prepend screenshot block to email_body only if screenshot exists
    if screenshot_url:
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
            "custom_subject":   row.get("custom_custom_subject", ""),
            "email_body":       body_html,
            "brand_name":       brand,
            "niche":            row.get("detected_niche", ""),
        },
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not API_KEY or "your_plusvibe" in API_KEY:
        logger.error("PLUSVIBE_API_KEY not set in .env")
        sys.exit(1)

    # Load CSV
    with open(CSV_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    leads = [l for row in rows if (l := build_lead(row))]
    skipped = len(rows) - len(leads)

    logger.info(f"Loaded {len(leads)} valid leads ({skipped} skipped — failed/no email)")
    logger.info(f"Campaign ID: {CAMPAIGN_ID}")
    logger.info(f"Batch size: {args.batch_size} | Dry run: {args.dry_run}")

    if args.dry_run:
        logger.info("DRY RUN — printing first batch only")
        await upload_batch(None, leads[:args.batch_size], dry_run=True)
        return

    # Chunk into batches
    batches = [leads[i:i + args.batch_size] for i in range(0, len(leads), args.batch_size)]
    total_uploaded = 0
    total_failed = 0

    async with aiohttp.ClientSession() as session:
        for i, batch in enumerate(batches, 1):
            logger.info(f"Uploading batch {i}/{len(batches)} ({len(batch)} leads)...")
            result = await upload_batch(session, batch, dry_run=False)

            if result.get("status") == "success" or result.get("data"):
                total_uploaded += len(batch)
                logger.info(f"  Batch {i}: OK")
            else:
                total_failed += len(batch)
                logger.error(f"  Batch {i}: FAILED — {result}")

            # Respect rate limit (5 req/s)
            if i < len(batches):
                await asyncio.sleep(0.3)

    logger.info(f"Done — {total_uploaded} uploaded, {total_failed} failed")
    logger.info(f"View campaign: https://app.plusvibe.ai/v2/campaigns/{CAMPAIGN_ID}")


if __name__ == "__main__":
    asyncio.run(main())
