import asyncio
import base64
import json
import logging
import os
from typing import Optional

import requests
from dotenv import load_dotenv

logger = logging.getLogger("email_agent")

PROMPT_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "prompts", "email.txt"
)

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))


class EmailAgent:
    """Agent that fires Claude CLI to draft a personalized email, then sends it via PlusVibe API."""

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
        logger.info(f"[EmailAgent] Drafting email for: {brand_name}")

        result = {
            "brand_name": brand_name,
            "email": email,
            "subject": None,
            "sent": False,
            "error": None,
        }

        try:
            # Step 1: Fire Claude CLI to draft the email
            email_content = await _draft_email(
                brand_name, url, niche, tiktok_angle, product_count, site_description
            )

            if not email_content:
                result["error"] = "Failed to draft email via Claude CLI"
                return result

            result["subject"] = email_content["subject"]

            # Step 2: Send via PlusVibe API
            sent = await _send_via_plusvibe(
                to_email=email,
                subject=email_content["subject"],
                body_html=email_content["body"],
                screenshot_path=screenshot_path,
            )

            result["sent"] = sent
            if sent:
                logger.info(f"[EmailAgent] Email sent to {email} for {brand_name}")
            else:
                result["error"] = "PlusVibe API send failed"

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"[EmailAgent] Error for {brand_name}: {e}")

        return result


async def _draft_email(
    brand_name: str, url: str, niche: str, tiktok_angle: str, product_count: str,
    site_description: str = ""
) -> Optional[dict]:
    """Fire Claude CLI to draft personalized email."""
    with open(PROMPT_TEMPLATE_PATH, "r") as f:
        prompt_template = f.read()

    prompt = prompt_template.format(
        brand_name=brand_name,
        url=url,
        niche=niche,
        tiktok_angle=tiktok_angle,
        product_count=product_count,
        site_description=site_description,
    )

    proc = await asyncio.create_subprocess_exec(
        "claude",
        "--print",
        "--output-format", "json",
        "-p", prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

    if proc.returncode != 0:
        logger.error(f"[EmailAgent] Claude CLI error: {stderr.decode()}")
        return None

    response_text = stdout.decode().strip()

    try:
        outer = json.loads(response_text)
        content = outer.get("result", response_text)
    except json.JSONDecodeError:
        content = response_text

    return _extract_json(content)


async def _send_via_plusvibe(
    to_email: str, subject: str, body_html: str, screenshot_path: Optional[str]
) -> bool:
    """Send email via PlusVibe API with optional screenshot attachment."""
    api_key = os.getenv("PLUSVIBE_API_KEY")
    api_url = os.getenv("PLUSVIBE_API_URL", "https://api.plusvibe.com/v1/email/send")
    sender_email = os.getenv("SENDER_EMAIL")
    sender_name = os.getenv("SENDER_NAME")

    if not api_key or api_key == "your_plusvibe_api_key_here":
        logger.warning("[EmailAgent] PlusVibe API key not configured. Skipping send.")
        return False

    payload = {
        "from": {"email": sender_email, "name": sender_name},
        "to": [{"email": to_email}],
        "subject": subject,
        "html": body_html,
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
        response = requests.post(api_url, json=payload, headers=headers, timeout=30)
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


def _extract_json(text: str) -> Optional[dict]:
    """Extract JSON object from text."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    return None
