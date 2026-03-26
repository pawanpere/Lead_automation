# Lead Automation Pipeline

## Architecture
- `main.py` — Orchestrator: reads leads from Excel, runs 4-stage pipeline with async concurrency
- `agents/prevalidation_agent.py` — Stage 0: fast DNS/HTTP/parked/duplicate/blacklist checks (no AI)
- `agents/scraper_agent.py` — Stage 1: Playwright-based web scraper + TikTok Ad Library checker
- `agents/qualify_agent.py` — Stage 2+3: Claude CLI for hard disqualifiers + weighted scoring → JSON output
- `agents/email_agent.py` — Email drafting via Claude CLI + sending via PlusVibe API
- `prompts/qualify.txt` — Qualification prompt with hard disqualifiers + weighted 1-5 scoring (BOPA framework)
- `prompts/email.txt` — Email drafting prompt template (variables: `{brand_name}`, `{url}`, `{niche}`, `{tiktok_angle}`, `{product_count}`)
- `data/leads.xlsx` — Input Excel file (columns: Brand Name, URL, Email, Product Page Link)
- `data/blacklist.txt` — Domains to never contact (bounces, complaints, opt-outs)
- `data/processed_domains.txt` — Already-processed domains (auto-populated)
- `output/results.json` — Pipeline results
- `output/logs/run.log` — Run logs
- `screenshots/` — Product page screenshots attached to outreach emails

## Pipeline Flow
```
Excel leads → Pre-Validation (DNS, HTTP, parked, dupe, blacklist)
            → Scraper (Playwright screenshot + data extraction)
            → Qualify (hard disqualifiers → weighted scoring → tier assignment)
            → Email (draft via Claude + send via PlusVibe)
```

## Qualification System
- **Hard Disqualifiers:** not e-commerce, digital-only, local service, marketplace-only, non-US, B2B/wholesale, franchise/chain
- **Weighted Scoring (1-5):** Visual Appeal (30%), Brand Identity (20%), TikTok Gap (20%), Category Fit (15%), Scale (15%)
- **Tiers:** hot (4.0-5.0), warm (3.0-3.9), parked (2.0-2.9), disqualified (<2.0)
- **Minimum for outreach:** composite_score >= 3.0

## Key Rules
- NEVER edit `.env` — contains PlusVibe API key and sender credentials
- Prompts in `prompts/` must output strict JSON only — no markdown, no extra text
- All agents use async pattern with static `run()` method
- Claude CLI is called via `asyncio.create_subprocess_exec("claude", "--print", "--output-format", "json", "-p", prompt)`
- `{variable}` placeholders in prompts must match what the calling agent passes
- JSON output schemas in prompts must match what the agent's `_extract_json()` parses

## Running
```bash
python3 main.py                          # Process data/leads.xlsx
python3 main.py --dry-run                # Qualify only, don't send emails
python3 main.py --concurrency 5          # Max 5 brands at once (default: 3)
python3 main.py --file path/to/file.xlsx # Custom Excel file
```

## Dependencies
```
openpyxl, requests, python-dotenv, playwright, aiohttp
```
Playwright browsers must be installed: `python3 -m playwright install chromium`
