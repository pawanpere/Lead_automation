import asyncio
import json
import logging
import os
from typing import Optional

logger = logging.getLogger("qualify_agent")

PROMPT_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "prompts", "qualify.txt"
)


class QualifyAgent:
    """Agent that fires Claude CLI to qualify a lead based on scraped website data."""

    @staticmethod
    async def run(brand_name: str, url: str, scraped_data: dict) -> dict:
        logger.info(f"[QualifyAgent] Qualifying: {brand_name}")

        default_result = {
            "qualified": False,
            "score": 0,
            "niche": "unknown",
            "reasons": ["qualification failed"],
            "tiktok_angle": "",
            "error": None,
        }

        try:
            # Load prompt template
            with open(PROMPT_TEMPLATE_PATH, "r") as f:
                prompt_template = f.read()

            # Build the prompt
            scraped_str = json.dumps(scraped_data, indent=2, default=str)
            prompt = prompt_template.format(
                brand_name=brand_name,
                url=url,
                scraped_data=scraped_str,
            )

            # Fire Claude CLI
            result = await _call_claude_cli(prompt)

            if result:
                result["error"] = None
                logger.info(
                    f"[QualifyAgent] {brand_name}: score={result.get('score')}, "
                    f"qualified={result.get('qualified')}, niche={result.get('niche')}"
                )
                return result
            else:
                logger.error(f"[QualifyAgent] Failed to parse Claude response for {brand_name}")
                return default_result

        except Exception as e:
            logger.error(f"[QualifyAgent] Error qualifying {brand_name}: {e}")
            default_result["error"] = str(e)
            return default_result


async def _call_claude_cli(prompt: str) -> Optional[dict]:
    """Fire Claude CLI and parse JSON response."""
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
        logger.error(f"[QualifyAgent] Claude CLI error: {stderr.decode()}")
        return None

    response_text = stdout.decode().strip()

    # Parse the JSON response from Claude
    try:
        # Claude --output-format json wraps response in a JSON object
        outer = json.loads(response_text)
        # The actual content is in the "result" field
        content = outer.get("result", response_text)
    except json.JSONDecodeError:
        content = response_text

    # Extract JSON from the content (Claude may add extra text)
    return _extract_json(content)


def _extract_json(text: str) -> Optional[dict]:
    """Extract JSON object from text that may contain extra content."""
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON in the text
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    return None
