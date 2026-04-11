"""Standalone email personalization module.

Given a brand name, domain, and (optionally) site description, generates:
  - brand_love: one personalized sentence about the brand
  - detected_niche: what the brand actually sells
  - custom_subject: email subject line
  - custom_body_html: brand love + opportunity paragraph (HTML)

Can be used as a library or run directly.

Usage as a library:
    from personalize import personalize, personalize_batch

    # Single lead
    result = asyncio.run(personalize("Yoloha Yoga", "yolohayoga.com", niche="Fitness"))

    # Batch
    leads = [{"brand_name": "...", "domain": "...", "niche": "..."}, ...]
    results = asyncio.run(personalize_batch(leads, concurrency=8))

Usage from CLI:
    python3 personalize.py --brand "Yoloha Yoga" --domain yolohayoga.com
    python3 personalize.py --csv input.csv --output output.csv
"""

import asyncio
import json
import logging
import os
import re
from typing import Optional

import aiohttp
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logger = logging.getLogger("personalize")

# ── LLM config (reads from .env) ─────────────────────────────────────────────

LLM_API_BASE  = os.getenv("LLM_API_BASE", "https://api.openai.com/v1")
LLM_API_KEY   = os.getenv("LLM_API_KEY", "")
LLM_MODEL     = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "200"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))

# ── Prompt ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You analyze ecommerce brand websites and write personalized cold outreach sentences. "
    "Respond with ONLY valid JSON, no extra text."
)

USER_PROMPT_TEMPLATE = """\
You analyze ecommerce brand websites and write personalized cold outreach sentences. You do two things:
1. Detect the correct niche from what the brand actually sells
2. Write one personalized sentence about the brand

## YOUR TASK
First, identify the correct niche based on what the brand actually sells (ignore the industry label provided, use the website description instead).
Then write exactly ONE sentence (max 2 lines) that shows genuine appreciation for the brand.

## NICHE DETECTION RULES
Pick the most specific niche that fits. Examples:
- Yoga mats, fitness gear → "Fitness"
- Skincare serums, moisturizers → "Beauty/Skincare"
- Protein powder, supplements → "Health & Wellness"
- Goat milk soap, natural body wash → "Beauty/Skincare"
- Hair accessories → "Hair Accessories"
- Statement jewelry → "Jewelry"
- Workout clothes → "Apparel & Fashion"
- Vegan hair products → "Hair Care"
- Baby products → "Baby & Family"
- Food, snacks → "Food & Beverage"
- Pet products → "Pet Products"
- Home goods, kitchen → "Home & Kitchen"

## BRAND INFORMATION
Brand: {brand_name}
Niche: {niche}
Website Description: {site_description}

## SENTENCE STRUCTURE
Follow this pattern: "I'm absolutely in love with [Brand]'s [specific product type] and how they [what makes them special]."
Use ONE product type only. Keep it short and natural.
Replace the bracketed parts with REAL details from the website description. Do NOT use generic filler.

## 8 REAL EXAMPLES
- I'm absolutely in love with Leven Rose's organic rosehip oil and how they keep skincare simple clean and effective.
- I'm absolutely in love with Peace Out Skincare's acne healing dots and how they make breakout care simple and effective.
- I'm absolutely in love with FATCO's grass fed tallow based moisturizers and how they focus on deeply nourishing simple ingredients.
- I'm absolutely in love with California Baby's gentle baby shampoos and how they prioritize safe plant based formulations for families.
- I'm absolutely in love with Soko Glam's curated k beauty collections and how they bring results driven korean skincare to a global audience.
- I'm absolutely in love with Ballwash's mens hygiene washes and how they make personal care straightforward and effective.
- I'm absolutely in love with Michael Todd Beauty's sonic cleansing brushes and how they elevate everyday routines with professional technology.
- I'm absolutely in love with Dr Thrower Skincare's dermatologist formulated serums and how they deliver refined professional results.

## RULES
DO: Reference SPECIFIC products/ingredients from the site. Use commas instead of em dashes. ONE sentence max.
DO NOT: Use generic phrases, em dashes (—), marketing fluff, exclamation marks, or invent product names.

## OUTPUT FORMAT
Respond with ONLY this JSON, nothing else:
{{"niche": "detected niche here", "brand_love": "I'm absolutely in love with..."}}
"""

# ── Opportunity paragraphs ────────────────────────────────────────────────────

_HEALTH_NICHES = {"health", "supplement", "vitamin", "nutrition", "fitness", "wellness", "food", "beverage", "snack"}

OPPORTUNITY_HEALTH = (
    "I help {niche} brands turn TikTok into a real sales channel through TikTok Shop. "
    "I've worked with other {niche} brands to generate millions of new impressions within weeks, "
    "and hundreds of daily orders through creator-led content, where thousands of creators show "
    "how the product fits into their routines, and viewers can check out instantly right from the "
    "video in the new built-in marketplace on TikTok. All at the cost of the samples."
)

OPPORTUNITY_DEFAULT = (
    "I help {niche} brands turn TikTok into a real sales channel through TikTok Shop. "
    "The way it works is creators make content showing how your product fits into their daily "
    "routines, and viewers can check out instantly right from the video in the new built-in "
    "marketplace on TikTok. Brands in the {niche} space are seeing hundreds of daily orders "
    "through this, and the only cost to get started is sending out product samples."
)

# ── Company name cleaning ─────────────────────────────────────────────────────

_LEGAL_SUFFIXES = re.compile(
    r",?\s*\b(LLC|L\.L\.C\.|Inc\.?|Corp\.?|Ltd\.?|Co\.?|LLP|L\.P\.|PLC|GmbH|S\.A\.)\b\.?",
    re.IGNORECASE,
)

def clean_brand_name(name: str) -> str:
    """Remove legal suffixes and parenthetical alternates from company names.

    Examples:
        "Ap Supply Llc (ap Medical Supply)" → "Ap Supply"
        "Bésame Cosmetics, Inc."            → "Bésame Cosmetics"
        "FATCO, LLC"                        → "FATCO"
    """
    if not name:
        return name
    cleaned = re.sub(r"\s*\(.*?\)", "", name)
    cleaned = _LEGAL_SUFFIXES.sub("", cleaned)
    cleaned = cleaned.strip(" ,.")
    return cleaned or name


# ── LLM helpers ───────────────────────────────────────────────────────────────

async def _llm_call(session: aiohttp.ClientSession, user_prompt: str) -> Optional[dict]:
    """Call LLM and parse JSON response."""
    url = f"{LLM_API_BASE.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY and LLM_API_KEY != "ollama":
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": LLM_MAX_TOKENS,
        "temperature": LLM_TEMPERATURE,
    }

    for attempt in range(3):
        try:
            async with session.post(url, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 429:
                    await asyncio.sleep(float(resp.headers.get("retry-after", 2)))
                    continue
                if resp.status != 200:
                    return None
                data = await resp.json()
                text = data["choices"][0]["message"]["content"].strip()
                return _parse_json(text)
        except Exception:
            if attempt < 2:
                await asyncio.sleep(2)
    return None


def _parse_json(text: str) -> Optional[dict]:
    """Parse JSON from LLM response, handling markdown code fences."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip ```json ... ``` fences
    if "```" in text:
        lines = text.strip().split("\n")
        lines = [l for l in lines[1:] if l.strip() != "```"]
        try:
            return json.loads("\n".join(lines))
        except json.JSONDecodeError:
            pass
    # Extract first {...}
    start, end = text.find("{"), text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    return None


# ── Site fetching ─────────────────────────────────────────────────────────────

async def fetch_site_description(session: aiohttp.ClientSession, domain: str) -> str:
    """Fetch and extract visible text from a domain's homepage."""
    url = f"https://{domain.lstrip('https://').lstrip('http://')}"
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=10),
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        ) as resp:
            if resp.status != 200:
                return ""
            html = await resp.text(errors="ignore")
            from html.parser import HTMLParser

            class _TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.parts = []
                    self._skip = False

                def handle_starttag(self, tag, attrs):
                    if tag in ("script", "style", "noscript", "svg"):
                        self._skip = True

                def handle_endtag(self, tag):
                    if tag in ("script", "style", "noscript", "svg"):
                        self._skip = False

                def handle_data(self, data):
                    if not self._skip and data.strip():
                        self.parts.append(data.strip())

            extractor = _TextExtractor()
            extractor.feed(html)
            return " ".join(extractor.parts)[:2000]
    except Exception:
        return ""


# ── Core personalization ──────────────────────────────────────────────────────

def _build_body_html(brand_love: str, niche: str) -> str:
    """Build the email body HTML: brand love paragraph + opportunity paragraph."""
    niche_lower = niche.lower()
    if any(n in niche_lower for n in _HEALTH_NICHES):
        opportunity = OPPORTUNITY_HEALTH.format(niche=niche)
    else:
        opportunity = OPPORTUNITY_DEFAULT.format(niche=niche)
    return f"<p>{brand_love}</p>\n<p>{opportunity}</p>"


async def personalize(
    brand_name: str,
    domain: str,
    niche: str = "",
    site_description: str = "",
    fetch_site: bool = True,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[dict]:
    """Personalize a single lead.

    Args:
        brand_name:       Raw company name (will be cleaned automatically)
        domain:           Company domain e.g. "yolohayoga.com"
        niche:            Hint niche (e.g. from Apollo/data source) — LLM may override
        site_description: Pre-fetched site text (skips HTTP fetch if provided)
        fetch_site:       Whether to fetch site text if site_description is empty
        session:          Reuse an existing aiohttp.ClientSession (optional)

    Returns:
        dict with keys: brand_name, domain, niche, brand_love, custom_subject,
                        custom_body_html, or None on failure.
    """
    brand_name = clean_brand_name(brand_name)

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        # Fetch site description if not provided
        if not site_description and fetch_site and domain:
            site_description = await fetch_site_description(session, domain)

        # Build prompt and call LLM
        user_prompt = USER_PROMPT_TEMPLATE.format(
            brand_name=brand_name,
            niche=niche or "Unknown",
            site_description=site_description[:1500] if site_description else "Not available",
        )
        result = await _llm_call(session, user_prompt)

        if not result:
            return None

        brand_love = result.get("brand_love", "").strip().strip('"').strip("'")
        detected_niche = result.get("niche", niche).strip()

        # Take first line only; strip em dashes
        brand_love = brand_love.split("\n")[0].strip()
        brand_love = brand_love.replace("\u2014", ",").replace("\u2013", ",")

        if not brand_love:
            return None

        body_html = _build_body_html(brand_love, detected_niche)

        return {
            "brand_name":       brand_name,
            "domain":           domain,
            "niche":            detected_niche,
            "brand_love":       brand_love,
            "custom_subject":   f"TikTok Shop Opportunity - {brand_name}",
            "custom_body_html": body_html,
        }
    finally:
        if own_session:
            await session.close()


async def personalize_batch(
    leads: list,
    concurrency: int = 8,
    fetch_site: bool = True,
) -> list:
    """Personalize a list of leads concurrently.

    Each lead dict must have at minimum: brand_name, domain
    Optional fields: niche, site_description

    Returns list of dicts with personalization added (status="ok"|"failed").
    """
    sem = asyncio.Semaphore(concurrency)
    results = []

    async def _process(lead):
        async with sem:
            result = await personalize(
                brand_name=lead.get("brand_name") or lead.get("company_name", ""),
                domain=lead.get("domain") or lead.get("company_domain", ""),
                niche=lead.get("niche") or lead.get("industry", ""),
                site_description=lead.get("site_description", ""),
                fetch_site=fetch_site,
                session=session,
            )
            out = dict(lead)
            if result:
                out.update(result)
                out["status"] = "ok"
            else:
                out["status"] = "failed"
            return out

    async with aiohttp.ClientSession() as session:
        tasks = [_process(l) for l in leads]
        ok = 0
        for i, coro in enumerate(asyncio.as_completed(tasks)):
            result = await coro
            results.append(result)
            if result["status"] == "ok":
                ok += 1
            if (i + 1) % 10 == 0 or (i + 1) == len(leads):
                print(f"  Personalized {i+1}/{len(leads)} — {ok} ok")

    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli():
    import argparse, csv

    parser = argparse.ArgumentParser(description="Personalize cold outreach emails for TikTok Shop")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--brand", help="Single brand name")
    group.add_argument("--csv", help="Input CSV with brand_name and domain columns")

    parser.add_argument("--domain", help="Domain (required with --brand)")
    parser.add_argument("--niche", default="", help="Niche hint (optional)")
    parser.add_argument("--output", "-o", default=None, help="Output CSV path (only with --csv)")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--no-fetch", action="store_true", help="Don't fetch site descriptions")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    if args.brand:
        if not args.domain:
            parser.error("--domain is required with --brand")
        result = asyncio.run(personalize(
            brand_name=args.brand,
            domain=args.domain,
            niche=args.niche,
            fetch_site=not args.no_fetch,
        ))
        if result:
            print(json.dumps(result, indent=2))
        else:
            print("Failed to personalize.")
    else:
        with open(args.csv, encoding="utf-8") as f:
            leads = list(csv.DictReader(f))

        print(f"Personalizing {len(leads)} leads...")
        results = asyncio.run(personalize_batch(
            leads,
            concurrency=args.concurrency,
            fetch_site=not args.no_fetch,
        ))

        ok = [r for r in results if r["status"] == "ok"]
        print(f"\nDone: {len(ok)}/{len(results)} succeeded")

        if args.output:
            fieldnames = list(results[0].keys())
            with open(args.output, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(results)
            print(f"Saved to {args.output}")
        else:
            for r in results[:3]:
                print(f"\n{r['brand_name']} ({r['domain']})")
                print(f"  Niche:   {r.get('niche')}")
                print(f"  Subject: {r.get('custom_subject')}")
                print(f"  Body:    {r.get('brand_love', '')[:100]}...")


if __name__ == "__main__":
    _cli()
