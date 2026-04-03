import json
import logging
import os
from typing import Optional

from utils.llm_client import generate_json

logger = logging.getLogger("qualify_agent")

PROMPT_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "prompts", "qualify.txt"
)


class QualifyAgent:
    """Agent that uses LLM to qualify a lead based on scraped website data."""

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
            with open(PROMPT_TEMPLATE_PATH, "r") as f:
                prompt_template = f.read()

            scraped_str = json.dumps(scraped_data, indent=2, default=str)
            user_prompt = prompt_template.format(
                brand_name=brand_name,
                url=url,
                scraped_data=scraped_str,
            )

            system_prompt = (
                "You are a lead qualification expert. Analyze the brand data and respond "
                "with ONLY a valid JSON object. No extra text, no markdown, no explanation."
            )

            result = await generate_json(system_prompt, user_prompt, max_tokens=500)

            if result:
                result["error"] = None
                logger.info(
                    f"[QualifyAgent] {brand_name}: score={result.get('score')}, "
                    f"qualified={result.get('qualified')}, niche={result.get('niche')}"
                )
                return result
            else:
                logger.error(f"[QualifyAgent] Failed to parse LLM response for {brand_name}")
                return default_result

        except Exception as e:
            logger.error(f"[QualifyAgent] Error qualifying {brand_name}: {e}")
            default_result["error"] = str(e)
            return default_result
