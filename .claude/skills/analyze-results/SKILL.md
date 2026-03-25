---
name: analyze-results
description: Analyze pipeline results from output/results.json — show conversion funnel, failures, and TikTok ad insights
---

Analyze the lead automation pipeline results.

## Steps
1. Read `output/results.json`
2. Present a conversion funnel:
   - Total leads → Successfully scraped → Qualified → Emails sent
3. Group failures by type:
   - `scrape_failed` — list brand names and errors
   - `not_qualified` — list brand names with scores and reasons
   - `email_failed` — list brand names and errors
   - `qualified_no_email` — brands that qualified but had no email address
4. Show TikTok insights:
   - Brands running TikTok ads (from qualification data)
   - Niches detected across qualified leads
5. If `output/results.json` doesn't exist, tell the user to run the pipeline first with `/run-pipeline`
