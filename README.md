# Lead Automation Pipeline

Automated cold outreach pipeline: scrape leads, verify emails, personalize with AI, validate screenshots, and upload to PlusVibe.

## Pipeline Overview

```
Domains (txt/csv)
    |
    v
[1] Apify Lead Scraper  ──> leads_raw.csv
    |
    v
[2] Truelist Email Verify ──> leads_verified.csv
    |
    v
[3] Match Screenshots (from Excel with Cloudinary URLs)
    |
    v
[4] LLM Personalization (brand love line + niche detection)
    |
    v
[5] Validate Screenshot URLs (HEAD request, skip broken ones)
    |
    v
[6] Upload to PlusVibe (only leads with working screenshots)
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Set up .env (copy from .env.example and fill in your keys)
cp .env.example .env

# Run the full pipeline
python3 run_pipeline.py domains.txt --screenshots good_screenshots_leads.xlsx
```

## Setup

### Environment Variables (.env)

```env
# LLM (any OpenAI-compatible API)
LLM_API_BASE=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
LLM_MAX_TOKENS=200
LLM_TEMPERATURE=0.7

# PlusVibe (cold email platform)
PLUSVIBE_API_KEY=your-api-key
PLUSVIBE_API_URL=https://api.plusvibe.ai/api/v1
PLUSVIBE_WORKSPACE_ID=your-workspace-id
PLUSVIBE_CAMPAIGN_ID=your-campaign-id

# Apify (lead scraping)
APIFY_API_KEY=apify_api_...
APIFY_ACTOR=code_crafter/leads-finder

# Truelist (email verification)
TRUELIST_API_KEY=your-truelist-api-key
```

### Screenshots

Screenshots must be pre-hosted (e.g., on Cloudinary) and provided via an Excel file with these columns:

| Column | Description |
|--------|-------------|
| `Domain` | Company domain (e.g., `yolohayoga.com`) |
| `Screenshot URL` | Full Cloudinary URL to the homepage screenshot |

The pipeline matches screenshots to leads by domain. **Leads without a working screenshot are NOT uploaded to PlusVibe.**

## Usage

### Full Pipeline (recommended)

```bash
# Run everything: scrape -> verify -> personalize -> upload
python3 run_pipeline.py domains.txt --screenshots screenshots.xlsx

# Dry run (don't upload to PlusVibe)
python3 run_pipeline.py domains.txt --screenshots screenshots.xlsx --dry-run

# With custom campaign ID
python3 run_pipeline.py domains.txt --screenshots screenshots.xlsx --campaign-id abc123
```

### Individual Steps

#### Step 1: Scrape Leads

```bash
# Scrape leads from a list of domains via Apify
python3 scrape_leads.py domains.txt --output output/leads_raw.csv
```

Input: `domains.txt` (one domain per line)
```
yolohayoga.com
fisherwallace.com
bendsoap.com
```

The Apify actor filters for:
- **Job Titles:** Owner, Co-Owner, Founder, Co-Founder, President
- **Location:** United States

#### Step 2: Verify Emails

```bash
# Verify all emails via Truelist
python3 verify_emails.py output/leads_raw.csv --output output/leads_verified.csv
```

Only emails with `email_state == "email_ok"` are kept.

#### Step 3: Personalize Emails

```bash
# Generate brand love lines + match screenshots
python3 generate_email_csv.py
```

The LLM generates per lead:
- **Brand love line:** "I'm absolutely in love with [Brand]'s [product] and how they [value]."
- **Niche detection:** From actual site content (not Apollo industry labels)

#### Step 4: Validate & Upload

```bash
# Validate screenshots and upload to PlusVibe
python3 validate_and_upload.py output/email_campaigns.csv

# Dry run
python3 validate_and_upload.py output/email_campaigns.csv --dry-run
```

This step:
1. Sends HEAD requests to every screenshot URL
2. Skips leads with broken/missing screenshots
3. Uploads only valid leads to PlusVibe

### Skip Steps (resume from where you left off)

```bash
# Skip scraping, start from existing leads CSV
python3 run_pipeline.py domains.txt --skip-scrape --leads output/leads_raw.csv --screenshots screenshots.xlsx

# Skip scraping AND verification
python3 run_pipeline.py domains.txt --skip-scrape --skip-verify --leads output/leads_verified.csv --screenshots screenshots.xlsx
```

## PlusVibe Template Variables

When leads are uploaded to PlusVibe, the following custom variables are set:

| Upload Key | PlusVibe Variable | Template Syntax | Content |
|---|---|---|---|
| `custom_subject` | `custom_custom_subject` | `{{custom_custom_subject}}` | "TikTok Shop Opportunity - [Brand]" |
| `email_body` | `custom_email_body` | `{{custom_email_body}}` | Screenshot + brand love + opportunity (HTML) |
| `brand_name` | `custom_brand_name` | `{{custom_brand_name}}` | Cleaned company name |
| `niche` | `custom_niche` | `{{custom_niche}}` | LLM-detected niche |

**Important:** PlusVibe auto-adds `custom_` prefix to all custom variable keys. Upload keys should NOT include the `custom_` prefix (e.g., upload `brand_name`, reference as `{{custom_brand_name}}`).

Built-in PlusVibe fields used in templates:
- `{{first_name}}` — from lead's `first_name` field
- `{{company_name}}` — from lead's `company_name` field

### Email Sequence (Step 1 - Initial Outreach)

```
Subject: {{custom_custom_subject}}

Hey {{first_name}}, hope your week is going good

{{custom_email_body}}
  ↳ Contains: screenshot image + brand love line + opportunity paragraph

We handle the entire platform for you, end-to-end...

Would you be interested in hearing more details?

Warmly,
Sayim Khan

P.S. ~ also, we don't identify as a traditional agency...
```

The screenshot + "This is still your website, right?" line is baked INTO `{{custom_email_body}}` so leads without screenshots simply skip that section (no broken images).

## Adding Sequences via API

```bash
# Add the initial outreach email sequence
python3 add_sequences.py
```

## Architecture

```
Lead_automation/
├── .env                      # API keys (not committed)
├── run_pipeline.py           # Full end-to-end pipeline
├── scrape_leads.py           # Step 1: Apify lead scraping
├── verify_emails.py          # Step 2: Truelist email verification
├── generate_email_csv.py     # Step 3: LLM personalization
├── validate_and_upload.py    # Step 4: Screenshot validation + PlusVibe upload
├── upload_to_plusvibe.py      # PlusVibe lead upload (standalone)
├── add_sequences.py          # PlusVibe sequence management
├── agents/
│   ├── email_agent.py        # Email personalization logic
│   ├── scraper_agent.py      # Playwright-based scraper
│   ├── qualify_agent.py      # LLM-based lead qualification
│   └── prevalidation_agent.py # DNS/HTTP pre-checks
├── utils/
│   └── llm_client.py         # Universal async LLM client (OpenAI-compatible)
├── prompts/
│   └── email_brandlove.txt   # Few-shot prompt for brand love generation
└── output/
    ├── leads_raw.csv         # Raw leads from Apify
    ├── leads_verified.csv    # Email-verified leads
    └── email_campaigns.csv   # Final personalized leads
```

## Token Usage

Only the brand love line (~40 tokens output) is generated by AI. The rest of the email is assembled in Python from templates. Per lead: ~490 input tokens, ~40 output tokens.

## Screenshot Validation

The pipeline validates every screenshot URL before uploading:
- Sends HTTP HEAD request to each Cloudinary URL
- Only leads with HTTP 200 response are uploaded
- Leads with broken/missing screenshots are logged and skipped
- This prevents broken image icons in sent emails
