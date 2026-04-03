import json
import logging
import os
from typing import Optional

import aiohttp
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

logger = logging.getLogger("llm_client")

LLM_API_BASE = os.getenv("LLM_API_BASE", "http://localhost:11434/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "200"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))


async def generate(system_prompt: str, user_prompt: str, max_tokens: Optional[int] = None) -> str:
    """Call any OpenAI-compatible LLM API and return the text response.

    Works with Ollama, vLLM, Together AI, Groq, OpenRouter, and any provider
    that exposes a /chat/completions endpoint.
    """
    url = f"{LLM_API_BASE.rstrip('/')}/chat/completions"

    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY and LLM_API_KEY != "ollama":
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens or LLM_MAX_TOKENS,
        "temperature": LLM_TEMPERATURE,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"[LLM] API error {resp.status}: {error_text}")
                    return ""

                data = await resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                logger.info(f"[LLM] Generated {len(content)} chars using {LLM_MODEL}")
                return content

    except aiohttp.ClientError as e:
        logger.error(f"[LLM] Request failed: {e}")
        return ""
    except (KeyError, IndexError) as e:
        logger.error(f"[LLM] Unexpected response format: {e}")
        return ""


async def generate_json(system_prompt: str, user_prompt: str, max_tokens: Optional[int] = None) -> Optional[dict]:
    """Call LLM and parse the response as JSON. Handles models that wrap JSON in markdown."""
    text = await generate(system_prompt, user_prompt, max_tokens=max_tokens)
    if not text:
        return None

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences if present
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line (```json) and last line (```)
        lines = [l for l in lines[1:] if not l.strip() == "```"]
        cleaned = "\n".join(lines).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

    # Try to find JSON object in text
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    logger.error(f"[LLM] Could not parse JSON from response: {text[:200]}")
    return None
