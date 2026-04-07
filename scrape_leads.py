"""Step 1: Scrape leads from domains using Apify.

Usage:
    python3 scrape_leads.py domains.txt              # one domain per line
    python3 scrape_leads.py domains.txt --output leads_raw.csv

The Apify actor (code_crafter/leads-finder) returns contacts matching:
  - Job Titles: Owner, Co-Owner, Founder, Co-Founder, President
  - Email Status: Validated
  - Location: United States
"""
import argparse, csv, json, os, sys, time
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

APIFY_API_KEY = os.getenv("APIFY_API_KEY")
APIFY_ACTOR   = os.getenv("APIFY_ACTOR", "code_crafter/leads-finder")
BASE_URL      = "https://api.apify.com/v2"

HEADERS = {"Content-Type": "application/json"}


def read_domains(path):
    """Read domains from a text file (one per line) or CSV (column 'domain')."""
    domains = []
    if path.endswith(".csv"):
        with open(path) as f:
            for row in csv.DictReader(f):
                d = (row.get("domain") or row.get("Domain") or "").strip()
                if d:
                    domains.append(d)
    else:
        with open(path) as f:
            for line in f:
                d = line.strip()
                if d and not d.startswith("#"):
                    domains.append(d)
    return domains


def start_run(domains):
    """Start an Apify actor run with the given domains."""
    payload = {
        "company_domain": domains,
        "contact_job_title": [
            "Owner", "Co-Owner", "Founder", "Co-Founder", "President"
        ],
        "fetch_count": 0,
    }
    url = f"{BASE_URL}/acts/{APIFY_ACTOR}/runs?token={APIFY_API_KEY}"
    print(f"Starting Apify run for {len(domains)} domains...")
    resp = requests.post(url, json=payload, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    run = resp.json()["data"]
    print(f"  Run ID: {run['id']}")
    print(f"  Status: {run['status']}")
    return run["id"], run["defaultDatasetId"]


def wait_for_run(run_id, poll_interval=15, max_wait=1800):
    """Poll until the Apify run finishes."""
    url = f"{BASE_URL}/actor-runs/{run_id}?token={APIFY_API_KEY}"
    elapsed = 0
    while elapsed < max_wait:
        resp = requests.get(url, timeout=15)
        status = resp.json()["data"]["status"]
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            print(f"  Run finished: {status}")
            return status
        print(f"  Status: {status} ({elapsed}s elapsed)...")
        time.sleep(poll_interval)
        elapsed += poll_interval
    print("  Timed out waiting for run.")
    return "TIMED-OUT"


def fetch_results(dataset_id):
    """Download all items from the dataset."""
    url = f"{BASE_URL}/datasets/{dataset_id}/items?token={APIFY_API_KEY}&format=json"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


def save_csv(leads, output_path):
    """Save leads to a CSV file."""
    if not leads:
        print("No leads found.")
        return

    fieldnames = [
        "first_name", "last_name", "email", "personal_email", "mobile_number",
        "job_title", "linkedin", "company_name", "company_domain",
        "company_website", "industry", "company_size",
        "city", "state", "country", "company_phone",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for lead in leads:
            # Normalize company_domain (strip https://www.)
            domain = (lead.get("company_domain") or "").strip()
            if not domain:
                website = (lead.get("company_website") or "").strip()
                domain = website.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")
            lead["company_domain"] = domain
            writer.writerow(lead)

    print(f"Saved {len(leads)} leads to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Scrape leads from domains via Apify")
    parser.add_argument("domains_file", help="Path to domains file (txt or csv)")
    parser.add_argument("--output", "-o", default="output/leads_raw.csv", help="Output CSV path")
    args = parser.parse_args()

    if not APIFY_API_KEY:
        print("Error: APIFY_API_KEY not set in .env")
        sys.exit(1)

    domains = read_domains(args.domains_file)
    if not domains:
        print("No domains found in input file.")
        sys.exit(1)

    print(f"Found {len(domains)} domains to scrape.\n")

    run_id, dataset_id = start_run(domains)
    status = wait_for_run(run_id)

    if status != "SUCCEEDED":
        print(f"Run did not succeed: {status}")
        sys.exit(1)

    leads = fetch_results(dataset_id)
    print(f"\nGot {len(leads)} leads from Apify.\n")

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    save_csv(leads, args.output)


if __name__ == "__main__":
    main()
