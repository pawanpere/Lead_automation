import base64
import json
import logging
import os
import re
from typing import Optional

import requests
from dotenv import load_dotenv

from utils.llm_client import generate_json

logger = logging.getLogger("email_agent")

PROMPT_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "prompts", "email_brandlove.txt"
)

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

# ── Company name cleaning ─────────────────────────────────────────────────────

_LEGAL_SUFFIXES = re.compile(
    r",?\s*\b(LLC|L\.L\.C\.|Inc\.?|Corp\.?|Ltd\.?|Co\.?|LLP|L\.P\.|PLC|GmbH|S\.A\.)\b\.?",
    re.IGNORECASE,
)

def clean_brand_name(name: str) -> str:
    """Remove legal suffixes, parenthetical alternates, and extra punctuation from company names.

    Examples:
      "Ap Supply Llc (ap Medical Supply)" → "Ap Supply"
      "Bésame Cosmetics, Inc."            → "Bésame Cosmetics"
      "FATCO, LLC"                        → "FATCO"
    """
    if not name:
        return name
    # Remove anything in parentheses
    cleaned = re.sub(r"\s*\(.*?\)", "", name)
    # Remove legal suffixes
    cleaned = _LEGAL_SUFFIXES.sub("", cleaned)
    # Strip trailing commas, periods, whitespace
    cleaned = cleaned.strip(" ,.")
    return cleaned or name  # fall back to original if result is empty


# ── Boilerplate templates (Python fills these in, no AI needed) ──────────────

OPPORTUNITY_HEALTH_BOOKS_FOOD = (
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

HEALTH_BOOKS_FOOD_NICHES = {"health", "books", "food & beverage", "food", "beverage", "supplements", "wellness"}


class EmailAgent:
    """Agent that uses LLM for Brand Love personalization, then templates the rest."""

    @staticmethod
    async def run(
        brand_name: str,
        email: str,
        url: str,
        niche: str,
        tiktok_angle: str,
        product_count: str,
        screenshot_path: Optional[str],
        site_description: str = "",
    ) -> dict:
        brand_name = clean_brand_name(brand_name)
        logger.info(f"[EmailAgent] Drafting email for: {brand_name}")

        result = {
            "brand_name": brand_name,
            "email": email,
            "subject": None,
            "custom_custom_subject": None,
            "custom_custom_body": None,
            "custom_brand_name": brand_name,
            "sent": False,
            "error": None,
        }

        try:
            # Step 1: Generate Brand Love line + detect correct niche via LLM
            brand_love, detected_niche = await _generate_brand_love(brand_name, niche, site_description)

            if not brand_love:
                result["error"] = "Failed to generate Brand Love line via LLM"
                return result

            # Use LLM-detected niche if available, fall back to passed-in niche
            effective_niche = detected_niche or niche

            # Step 2: Assemble custom_custom_body (Brand Love + Opportunity paragraph)
            custom_body = _build_custom_body(brand_love, brand_name, effective_niche)
            custom_subject = f"TikTok Shop Opportunity - {brand_name}"

            result["subject"] = custom_subject
            result["custom_custom_subject"] = custom_subject
            result["custom_custom_body"] = custom_body

            # Step 3: Upload lead to PlusVibe with custom fields
            sent = await _upload_to_plusvibe(
                to_email=email,
                brand_name=brand_name,
                custom_subject=custom_subject,
                custom_body=custom_body,
                screenshot_path=screenshot_path,
            )

            result["sent"] = sent
            if sent:
                logger.info(f"[EmailAgent] Lead uploaded to PlusVibe for {brand_name}")
            else:
                result["error"] = "PlusVibe API upload failed"

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"[EmailAgent] Error for {brand_name}: {e}")

        return result


async def _generate_brand_love(brand_name: str, niche: str, site_description: str) -> tuple[str, str]:
    """Call LLM to generate Brand Love sentence and detect correct niche.

    Returns (brand_love_line, detected_niche). Either may be empty string on failure.
    """
    with open(PROMPT_TEMPLATE_PATH, "r") as f:
        prompt_template = f.read()

    system_prompt = (
        "You analyze ecommerce brands and write personalized cold outreach sentences. "
        "Respond with ONLY valid JSON, no extra text."
    )

    user_prompt = prompt_template.format(
        brand_name=brand_name,
        niche=niche,
        site_description=site_description,
    )

    response = await generate_json(system_prompt, user_prompt, max_tokens=150)

    if not response:
        return "", ""

    brand_love = response.get("brand_love", "").strip().strip('"').strip("'")
    detected_niche = response.get("niche", "").strip()

    # Take only the first line if model outputs more
    if "\n" in brand_love:
        brand_love = brand_love.split("\n")[0].strip()

    # Strip em dashes regardless of what the model outputs
    brand_love = brand_love.replace("—", ",").replace("–", ",")

    return brand_love, detected_niche


def _build_custom_body(brand_love: str, brand_name: str, niche: str) -> str:
    """Assemble custom_custom_body: Brand Love + Opportunity paragraph in HTML."""
    # Pick the right opportunity paragraph based on niche
    niche_lower = niche.lower().strip()
    if any(n in niche_lower for n in HEALTH_BOOKS_FOOD_NICHES):
        opportunity = OPPORTUNITY_HEALTH_BOOKS_FOOD.format(niche=niche, brand_name=brand_name)
    else:
        opportunity = OPPORTUNITY_DEFAULT.format(niche=niche, brand_name=brand_name)

    return f"<p>{brand_love}</p>\n<p>{opportunity}</p>"


async def _upload_to_plusvibe(
    to_email: str, brand_name: str, custom_subject: str, custom_body: str,
    screenshot_path: Optional[str]
) -> bool:
    """Upload lead to PlusVibe with custom fields for template-based sending."""
    api_key = os.getenv("PLUSVIBE_API_KEY")
    api_url = os.getenv("PLUSVIBE_API_URL", "https://api.plusvibe.ai/api/v1")
    sender_email = os.getenv("SENDER_EMAIL")
    sender_name = os.getenv("SENDER_NAME")

    if not api_key or api_key == "your_plusvibe_api_key":
        logger.warning("[EmailAgent] PlusVibe API key not configured. Skipping upload.")
        return False

    payload = {
        "from": {"email": sender_email, "name": sender_name},
        "to": [{"email": to_email}],
        "subject": custom_subject,
        "html": custom_body,
        "customFields": {
            "custom_custom_subject": custom_subject,
            "custom_custom_body": custom_body,
            "custom_brand_name": brand_name,
        },
    }

    # Attach screenshot if available
    if screenshot_path and os.path.exists(screenshot_path):
        with open(screenshot_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        payload["attachments"] = [
            {
                "filename": os.path.basename(screenshot_path),
                "content": encoded,
                "type": "image/png",
            }
        ]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            f"{api_url}/email/send", json=payload, headers=headers, timeout=30
        )
        if response.status_code in (200, 201, 202):
            return True
        else:
            logger.error(
                f"[EmailAgent] PlusVibe API error {response.status_code}: {response.text}"
            )
            return False
    except requests.RequestException as e:
        logger.error(f"[EmailAgent] PlusVibe API request failed: {e}")
        return False
