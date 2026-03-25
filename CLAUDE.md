# Lead Automation Pipeline

## Architecture
- `main.py` — Orchestrator: reads leads from Excel, runs 3-agent pipeline with async concurrency
- `agents/scraper_agent.py` — Playwright-based web scraper + TikTok Ad Library checker
- `agents/qualify_agent.py` — Claude CLI (`claude --print`) for lead scoring → JSON output
- `agents/email_agent.py` — Claude CLI for email drafting + PlusVibe API for sending
- `prompts/qualify.txt` — Qualification prompt template (variables: `{brand_name}`, `{url}`, `{scraped_data}`)
- `prompts/email.txt` — Email drafting prompt template (variables: `{brand_name}`, `{url}`, `{niche}`, `{tiktok_angle}`, `{product_count}`)
- `data/leads.xlsx` — Input Excel file (columns: Brand Name, URL, Email, Product Page Link)
- `output/results.json` — Pipeline results
- `output/logs/run.log` — Run logs
- `screenshots/` — Product page screenshots attached to outreach emails

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
openpyxl, requests, python-dotenv, playwright
```
Playwright browsers must be installed: `python3 -m playwright install chromium`
