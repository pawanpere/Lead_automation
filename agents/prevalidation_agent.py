import asyncio
import logging
import os
import socket
from typing import Optional
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger("prevalidation_agent")

# Blacklist file path (one domain per line)
BLACKLIST_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "blacklist.txt"
)

# Processed domains tracker file
PROCESSED_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "processed_domains.txt"
)

# Keywords that indicate a parked or for-sale domain
PARKED_KEYWORDS = [
    "this domain is for sale",
    "domain is parked",
    "buy this domain",
    "domain parking",
    "this webpage is parked",
    "is available for purchase",
    "domain name for sale",
    "godaddy",
    "namecheap parking",
    "sedo domain parking",
    "this site can't be reached",
    "hugedomains",
    "dan.com",
    "afternic",
    "undeveloped.com",
]


class PrevalidationAgent:
    """Stage 1: Fast, cheap checks to eliminate dead or invalid domains
    before any screenshot or AI processing.

    Checks:
    1. DNS Resolution - domain must resolve
    2. HTTP Status - site must be reachable (200 or redirect)
    3. Parked/For-Sale Detection - not a placeholder page
    4. Duplicate Check - not already processed
    5. Blacklist Check - not on internal blacklist
    """

    @staticmethod
    async def run(brand_name: str, url: str) -> dict:
        """Run all pre-validation checks on a domain.

        Returns:
            dict with keys:
                - valid (bool): whether the domain passed all checks
                - reason (str|None): why it failed, if it did
                - check_failed (str|None): which check failed
        """
        logger.info(f"[PrevalidationAgent] Checking: {brand_name} ({url})")

        result = {
            "valid": False,
            "reason": None,
            "check_failed": None,
        }

        # Normalize the domain
        domain = _normalize_domain(url)
        if not domain:
            result["reason"] = "Invalid URL - could not extract domain"
            result["check_failed"] = "url_parse"
            logger.warning(f"[PrevalidationAgent] {brand_name}: {result['reason']}")
            return result

        # Check 1: Blacklist
        if _is_blacklisted(domain):
            result["reason"] = "Domain is on internal blacklist (prior bounce, complaint, or opt-out)"
            result["check_failed"] = "blacklist"
            logger.warning(f"[PrevalidationAgent] {brand_name}: {result['reason']}")
            return result

        # Check 2: Duplicate
        if _is_duplicate(domain):
            result["reason"] = "Domain already processed"
            result["check_failed"] = "duplicate"
            logger.info(f"[PrevalidationAgent] {brand_name}: {result['reason']}")
            return result

        # Check 3: DNS Resolution
        dns_ok = await _check_dns(domain)
        if not dns_ok:
            result["reason"] = "DNS resolution failed - domain is dead"
            result["check_failed"] = "dns"
            logger.warning(f"[PrevalidationAgent] {brand_name}: {result['reason']}")
            return result

        # Check 4: HTTP Status
        http_ok, status_code, page_text = await _check_http(url)
        if not http_ok:
            result["reason"] = f"Site unreachable (HTTP status: {status_code})"
            result["check_failed"] = "http_status"
            logger.warning(f"[PrevalidationAgent] {brand_name}: {result['reason']}")
            return result

        # Check 5: Parked/For-Sale Detection
        if page_text and _is_parked(page_text):
            result["reason"] = "Domain appears to be parked or for sale"
            result["check_failed"] = "parked"
            logger.warning(f"[PrevalidationAgent] {brand_name}: {result['reason']}")
            return result

        # All checks passed
        result["valid"] = True
        _mark_as_processed(domain)
        logger.info(f"[PrevalidationAgent] {brand_name}: PASSED all pre-validation checks")
        return result


def _normalize_domain(url: str) -> Optional[str]:
    """Extract and normalize domain from URL."""
    if not url:
        return None
    if not url.startswith("http"):
        url = "https://" + url
    try:
        parsed = urlparse(url)
        domain = parsed.hostname
        if domain:
            # Strip www prefix
            if domain.startswith("www."):
                domain = domain[4:]
            return domain.lower()
    except Exception:
        pass
    return None


def _is_blacklisted(domain: str) -> bool:
    """Check domain against internal blacklist file."""
    if not os.path.exists(BLACKLIST_PATH):
        return False
    try:
        with open(BLACKLIST_PATH, "r") as f:
            blacklist = {line.strip().lower() for line in f if line.strip()}
        return domain in blacklist
    except Exception:
        return False


def _is_duplicate(domain: str) -> bool:
    """Check if domain has already been processed."""
    if not os.path.exists(PROCESSED_PATH):
        return False
    try:
        with open(PROCESSED_PATH, "r") as f:
            processed = {line.strip().lower() for line in f if line.strip()}
        return domain in processed
    except Exception:
        return False


def _mark_as_processed(domain: str):
    """Add domain to processed list."""
    try:
        os.makedirs(os.path.dirname(PROCESSED_PATH), exist_ok=True)
        with open(PROCESSED_PATH, "a") as f:
            f.write(domain + "\n")
    except Exception as e:
        logger.warning(f"[PrevalidationAgent] Could not write to processed list: {e}")


async def _check_dns(domain: str) -> bool:
    """Check if domain resolves via DNS."""
    loop = asyncio.get_event_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(None, socket.getaddrinfo, domain, None),
            timeout=10,
        )
        return True
    except (socket.gaierror, asyncio.TimeoutError, OSError):
        return False


async def _check_http(url: str) -> tuple:
    """Send HEAD request to check if site is reachable.

    Returns:
        (is_ok: bool, status_code: int|None, page_text: str|None)
    """
    if not url.startswith("http"):
        url = "https://" + url

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Try HEAD first (faster)
            try:
                async with session.head(
                    url,
                    allow_redirects=True,
                    ssl=False,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; LeadBot/1.0)"},
                ) as resp:
                    if resp.status in (200, 301, 302, 303, 307, 308):
                        # Do a GET to grab page text for parked detection
                        async with session.get(
                            url,
                            allow_redirects=True,
                            ssl=False,
                            headers={"User-Agent": "Mozilla/5.0 (compatible; LeadBot/1.0)"},
                        ) as get_resp:
                            page_text = await get_resp.text(errors="replace")
                            # Only keep first 5000 chars for parked detection
                            return True, get_resp.status, page_text[:5000]
                    else:
                        return False, resp.status, None
            except Exception:
                # HEAD failed, try GET directly
                async with session.get(
                    url,
                    allow_redirects=True,
                    ssl=False,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; LeadBot/1.0)"},
                ) as resp:
                    page_text = await resp.text(errors="replace")
                    if resp.status in (200, 301, 302, 303, 307, 308):
                        return True, resp.status, page_text[:5000]
                    else:
                        return False, resp.status, None
    except Exception as e:
        logger.debug(f"[PrevalidationAgent] HTTP check failed for {url}: {e}")
        return False, None, None


def _is_parked(page_text: str) -> bool:
    """Check if page content indicates a parked or for-sale domain."""
    text_lower = page_text.lower()
    matches = sum(1 for kw in PARKED_KEYWORDS if kw in text_lower)
    # Require at least 1 match, but also check page is suspiciously short
    # (real sites tend to have more content)
    if matches >= 2:
        return True
    if matches >= 1 and len(page_text.strip()) < 2000:
        return True
    return False
