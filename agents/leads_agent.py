import os
import json
import logging
import requests
from dotenv import load_dotenv

logger = logging.getLogger("leads_agent")

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

STORELEADS_API_KEY = os.getenv("STORELEADS_API_KEY")
STORELEADS_API_URL = os.getenv("STORELEADS_API_URL", "https://storeleads.app/json/api/v1/all")


class LeadsAgent:
    """Agent that pulls and filters ecommerce/clothing leads from Store Leads API."""

    @staticmethod
    def pull_leads(
        query="clothing",
        platforms=None,
        max_leads=100,
        country=None,
        min_products=10,
        max_products=50000,
    ):
        """Pull leads from Store Leads API.

        Args:
            query: Search query (e.g. "clothing", "beauty", "skincare")
            platforms: List of platforms e.g. ["shopify", "woocommerce"]
            max_leads: Max number of leads to pull
            country: Country code filter e.g. "US"
            min_products: Minimum product count
            max_products: Max product count (skip mega brands)
        """
        if not STORELEADS_API_KEY:
            logger.error("[LeadsAgent] STORELEADS_API_KEY not set")
            return []

        if not platforms:
            platforms = ["shopify"]

        headers = {"Authorization": f"Bearer {STORELEADS_API_KEY}"}

        all_leads = []
        page = 0
        page_size = 50

        while len(all_leads) < max_leads:
            params = {
                "page": page,
                "page_size": page_size,
                "q": query,
            }
            if platforms:
                params["f:p"] = ",".join(platforms)
            if country:
                params["f:cc"] = country

            logger.info(f"[LeadsAgent] Fetching page {page} (query='{query}')")

            try:
                resp = requests.get(
                    f"{STORELEADS_API_URL}/domain",
                    headers=headers,
                    params=params,
                    timeout=30,
                )

                if resp.status_code == 429:
                    logger.warning("[LeadsAgent] Rate limited, stopping")
                    break
                if resp.status_code != 200:
                    logger.error(f"[LeadsAgent] API error {resp.status_code}: {resp.text[:200]}")
                    break

                data = resp.json()
                domains = data.get("domains", [])

                if not domains:
                    logger.info("[LeadsAgent] No more results")
                    break

                for domain in domains:
                    lead = _parse_lead(domain, min_products, max_products)
                    if lead:
                        all_leads.append(lead)

                logger.info(f"[LeadsAgent] Page {page}: {len(domains)} results, {len(all_leads)} total leads")
                page += 1

                if not data.get("has_next_page", False):
                    break

            except requests.RequestException as e:
                logger.error(f"[LeadsAgent] Request failed: {e}")
                break

        logger.info(f"[LeadsAgent] Total leads pulled: {len(all_leads)}")
        return all_leads[:max_leads]

    @staticmethod
    def save_to_excel(leads, output_path=None):
        """Save leads to Excel file for the pipeline."""
        from openpyxl import Workbook

        if not output_path:
            output_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "data", "leads.xlsx"
            )

        wb = Workbook()
        ws = wb.active
        ws.title = "Leads"
        ws.append(["Brand Name", "URL", "Email", "Product Page Link", "Contact Name"])

        for lead in leads:
            ws.append([
                lead.get("brand_name", ""),
                lead.get("url", ""),
                lead.get("email", ""),
                lead.get("product_url", ""),
                lead.get("contact_name", ""),
            ])

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        wb.save(output_path)
        logger.info(f"[LeadsAgent] Saved {len(leads)} leads to {output_path}")
        return output_path


def _parse_lead(domain_data, min_products=10, max_products=50000):
    """Parse a Store Leads domain object into our lead format."""
    name = domain_data.get("name", "") or domain_data.get("domain", "")
    merchant_name = domain_data.get("merchant_name", "")
    product_count = domain_data.get("product_count", 0) or 0

    # Filter by product count
    if product_count < min_products or product_count > max_products:
        return None

    # Extract email from contact_info
    contact_info = domain_data.get("contact_info", []) or []
    email = ""
    contact_name = ""
    phone = ""

    for info in contact_info:
        info_type = info.get("type", "")
        value = info.get("value", "")

        if info_type == "email" and not email:
            email = value
        elif info_type == "phone" and not phone:
            # Clean phone number
            phone = value.strip("[]")

    # Try to find email from the domain if not in contact_info
    if not email:
        # Check if there's a linkedin or other contact we can use
        for info in contact_info:
            if info.get("type") == "linkedin":
                # We have linkedin but no email — skip for now
                pass
        return None  # No email = can't reach them

    # Try to parse first name from email (e.g. diana@brand.com → Diana)
    if not contact_name and email:
        local_part = email.split("@")[0].lower()
        # Skip generic emails
        generic = {"info", "hello", "hi", "contact", "support", "help", "admin",
                   "sales", "team", "press", "media", "marketing", "jobs",
                   "careers", "helpme", "bonjour", "howdy", "happy", "collabs"}
        if local_part not in generic and not any(c.isdigit() for c in local_part):
            # Likely a person's name
            contact_name = local_part.replace(".", " ").replace("_", " ").replace("-", " ").title()

    # Clean domain to URL
    domain = name.replace("www.", "") if name.startswith("www.") else name
    url = f"https://{name}" if name else ""
    product_url = f"{url}/collections/all"

    # Brand name
    brand_name = merchant_name or domain.replace(".com", "").replace(".co", "").replace("-", " ").title()

    # Get TikTok info if available
    tiktok_followers = 0
    tiktok_url = ""
    for info in contact_info:
        if info.get("type") == "tiktok":
            tiktok_followers = info.get("followers", 0) or 0
            tiktok_url = info.get("value", "")

    return {
        "brand_name": brand_name,
        "url": url,
        "email": email,
        "product_url": product_url,
        "contact_name": contact_name,
        "phone": phone,
        "country": domain_data.get("country_code", ""),
        "product_count": product_count,
        "estimated_sales": domain_data.get("estimated_sales", 0),
        "categories": domain_data.get("categories", []),
        "tiktok_followers": tiktok_followers,
        "tiktok_url": tiktok_url,
        "platform": domain_data.get("platform", ""),
    }
