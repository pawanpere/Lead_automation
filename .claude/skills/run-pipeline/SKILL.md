---
name: run-pipeline
description: Run the lead automation pipeline with options like --dry-run, --concurrency, or --file
---

Run the lead automation pipeline from the project root.

## Usage
The user may pass arguments after the skill invocation:
- `--dry-run` — Qualify leads but don't send emails
- `--file path/to/leads.xlsx` — Use a custom Excel file (default: `data/leads.xlsx`)
- `--concurrency N` — Max brands to process in parallel (default: 3)

## Execution
```bash
cd /Users/prakashtupe/lead-automation && python3 main.py {args}
```

## After Execution
1. Read `output/results.json` and show a summary table:
   - Total leads processed
   - Qualified vs not qualified
   - Emails sent vs failed
   - Any scrape failures with brand names
2. If there were failures, suggest next steps (e.g., check URLs, review logs at `output/logs/run.log`)
