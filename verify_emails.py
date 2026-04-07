"""Step 2: Verify lead emails using Truelist API.

Usage:
    python3 verify_emails.py output/leads_raw.csv
    python3 verify_emails.py output/leads_raw.csv --output output/leads_verified.csv

Only leads with email_state == "email_ok" are kept.
"""
import argparse, asyncio, csv, json, os, sys
import aiohttp
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

TRUELIST_API_KEY = os.getenv("TRUELIST_API_KEY")
TRUELIST_URL     = "https://api.truelist.io/api/v1/verify_inline"

VALID_STATES = {"email_ok"}  # only keep fully verified emails
CONCURRENCY  = 5


async def verify_single(session, email):
    """Verify a single email via Truelist inline API."""
    try:
        async with session.post(
            TRUELIST_URL,
            params={"email": email},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                return email, "api_error", f"HTTP {resp.status}"
            data = await resp.json()
            result = data.get("emails", [{}])[0]
            state = result.get("email_state", "unknown")
            sub_state = result.get("email_sub_state", "")
            return email, state, sub_state
    except Exception as e:
        return email, "error", str(e)


async def verify_all(emails):
    """Verify a list of emails with concurrency control."""
    sem = asyncio.Semaphore(CONCURRENCY)
    headers = {
        "Authorization": f"Bearer {TRUELIST_API_KEY}",
        "Accept": "application/json",
    }
    results = {}

    async def _verify(email):
        async with sem:
            return await verify_single(session, email)

    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = [_verify(e) for e in emails]
        for i, coro in enumerate(asyncio.as_completed(tasks)):
            email, state, sub = await coro
            results[email] = (state, sub)
            if (i + 1) % 25 == 0 or (i + 1) == len(emails):
                ok = sum(1 for s, _ in results.values() if s in VALID_STATES)
                print(f"  Verified {i+1}/{len(emails)} — {ok} valid so far")

    return results


def main():
    parser = argparse.ArgumentParser(description="Verify lead emails via Truelist")
    parser.add_argument("input_csv", help="Path to leads CSV (must have 'email' column)")
    parser.add_argument("--output", "-o", default=None, help="Output CSV path (default: leads_verified.csv)")
    args = parser.parse_args()

    if not TRUELIST_API_KEY:
        print("Error: TRUELIST_API_KEY not set in .env")
        sys.exit(1)

    # Read input
    with open(args.input_csv, encoding="utf-8") as f:
        leads = list(csv.DictReader(f))

    emails = [l["email"] for l in leads if l.get("email")]
    unique_emails = list(set(emails))
    print(f"Loaded {len(leads)} leads, {len(unique_emails)} unique emails to verify.\n")

    # Verify
    results = asyncio.run(verify_all(unique_emails))

    # Filter
    verified = []
    rejected = []
    for lead in leads:
        email = lead.get("email", "")
        state, sub = results.get(email, ("unknown", ""))
        lead["email_state"] = state
        lead["email_sub_state"] = sub
        if state in VALID_STATES:
            verified.append(lead)
        else:
            rejected.append(lead)

    print(f"\nResults:")
    print(f"  Verified (email_ok): {len(verified)}")
    print(f"  Rejected:            {len(rejected)}")

    # Show rejection breakdown
    from collections import Counter
    rejection_reasons = Counter(l["email_state"] for l in rejected)
    for reason, count in rejection_reasons.most_common():
        print(f"    {reason}: {count}")

    # Save verified leads
    output_path = args.output or args.input_csv.replace(".csv", "_verified.csv")
    if verified:
        fieldnames = list(verified[0].keys())
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(verified)
        print(f"\nSaved {len(verified)} verified leads to {output_path}")
    else:
        print("\nNo verified leads to save.")

    # Save rejected for reference
    rejected_path = output_path.replace(".csv", "_rejected.csv")
    if rejected:
        fieldnames = list(rejected[0].keys())
        with open(rejected_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rejected)
        print(f"Saved {len(rejected)} rejected leads to {rejected_path}")


if __name__ == "__main__":
    main()
