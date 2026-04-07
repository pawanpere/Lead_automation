import asyncio
import json
import os
import logging
from urllib.parse import urlparse, parse_qs, urlencode
from playwright.async_api import async_playwright

logger = logging.getLogger("scraper_agent")

# Try to import browser-use (optional fallback, requires Python 3.11+)
BROWSER_USE_AVAILABLE = False
try:
    from browser_use import Agent
    BROWSER_USE_AVAILABLE = True
except ImportError:
    pass


class ScraperAgent:
    """Agent that scrapes ecommerce websites and takes product screenshots.

    Uses Playwright as primary engine (fast, free).
    Falls back to browser-use AI agent when Playwright screenshot fails validation.
    """

    @staticmethod
    async def run(brand_name: str, url: str, product_url: str) -> dict:
        logger.info(f"[ScraperAgent] Starting scrape for: {brand_name}")

        screenshots_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)

        safe_name = brand_name.lower().replace(" ", "_").replace("/", "_")
        screenshot_path = os.path.join(screenshots_dir, f"{safe_name}.png")

        result = {
            "brand_name": brand_name,
            "url": url,
            "product_url": product_url,
            "screenshot_path": None,
            "scraped_data": {},
            "success": False,
            "error": None,
            "screenshot_method": None,  # "playwright" or "browser-use"
        }

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=False,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--window-size=1280,900",
                    ],
                )
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                    locale="en-US",
                    timezone_id="America/New_York",
                )
                await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => false });
                """)
                page = await context.new_page()

                # --- Scrape main website ---
                logger.info(f"[ScraperAgent] Visiting {url}")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    logger.warning(f"[ScraperAgent] Homepage load issue: {e}")
                await page.wait_for_timeout(3000)

                try:
                    await _dismiss_popups(page)
                except Exception:
                    pass

                try:
                    scraped = await _extract_website_data(page, url)
                except Exception as e:
                    logger.warning(f"[ScraperAgent] Data extraction failed: {e}")
                    scraped = {"title": brand_name, "platform": "Unknown"}

                # --- Try to find owner/founder name ---
                owner_name = await _find_owner_name(page, url)
                if owner_name:
                    scraped["owner_name"] = owner_name
                    logger.info(f"[ScraperAgent] Found owner: {owner_name}")

                # --- Screenshot the homepage ---
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(1000)
                await page.screenshot(path=screenshot_path, full_page=False)
                result["screenshot_path"] = screenshot_path
                result["screenshot_method"] = "playwright-homepage"
                logger.info(f"[ScraperAgent] Homepage screenshot saved: {screenshot_path}")

                result["scraped_data"] = scraped
                result["success"] = True
                await browser.close()

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"[ScraperAgent] Error scraping {brand_name}: {e}")

        return result


# ============================================================
# VALIDATION — detect if screenshot is usable
# ============================================================

async def _validate_screenshot(page, screenshot_path: str) -> dict:
    """Validate that the screenshot shows a real product page.
    Returns { passed: bool, reasons: [str] }
    """
    reasons = []

    # Check 1: File size — blank/white pages are tiny
    try:
        file_size = os.path.getsize(screenshot_path)
        if file_size < 15000:  # < 15KB is almost certainly blank
            reasons.append(f"screenshot too small ({file_size} bytes), likely blank")
    except OSError:
        reasons.append("screenshot file not found")
        return {"passed": False, "reasons": reasons}

    # Check 2: Is the viewport blocked by overlays?
    has_overlay = await page.evaluate("""
        () => {
            const vw = window.innerWidth, vh = window.innerHeight;
            let coveredArea = 0;
            const allElements = document.querySelectorAll('*');
            for (const el of allElements) {
                const s = getComputedStyle(el);
                if ((s.position === 'fixed' || s.position === 'absolute') &&
                    parseInt(s.zIndex || 0) > 10) {
                    const r = el.getBoundingClientRect();
                    if (r.width > vw * 0.3 && r.height > vh * 0.3) {
                        coveredArea += r.width * r.height;
                    }
                }
            }
            return coveredArea > (vw * vh * 0.3);
        }
    """)
    if has_overlay:
        reasons.append("viewport blocked by overlay/popup (>30% covered)")

    # Check 3: Are there visible product images? (collection page = multiple, product page = 1 big)
    product_image_count = await page.evaluate("""
        () => {
            const images = Array.from(document.querySelectorAll('img'));
            let count = 0;
            for (const img of images) {
                const r = img.getBoundingClientRect();
                if (r.width > 80 && r.height > 80 &&
                    img.naturalWidth > 0 &&
                    r.top < window.innerHeight && r.bottom > 0) {
                    count++;
                }
            }
            return count;
        }
    """)
    if product_image_count < 1:
        reasons.append("no product images visible in viewport")

    return {
        "passed": len(reasons) == 0,
        "reasons": reasons,
    }


# ============================================================
# CORE FUNCTIONS
# ============================================================

async def _is_error_page(page) -> bool:
    """Detect obvious 404/error pages."""
    return await page.evaluate("""
        () => {
            const title = document.title.toLowerCase();
            const bodyText = document.body.innerText.substring(0, 500).toLowerCase();
            const titleIs404 = title.includes('404') || title.includes('not found') || title.includes('page not found');
            const bodyIs404 = bodyText.length < 300 &&
                (bodyText.includes('404') || bodyText.includes('page not found'));
            return titleIs404 || bodyIs404;
        }
    """)


async def _extract_website_data(page, url: str) -> dict:
    """Extract key data from an ecommerce website."""
    data = {}

    data["title"] = await page.title()
    meta_desc = await page.query_selector('meta[name="description"]')
    if meta_desc:
        data["description"] = await meta_desc.get_attribute("content")

    page_source = await page.content()
    data["platform"] = _detect_platform(page_source)

    data["social_links"] = await page.evaluate("""
        () => {
            const links = Array.from(document.querySelectorAll('a[href]'));
            const socials = {};
            links.forEach(link => {
                const href = link.href.toLowerCase();
                if (href.includes('tiktok.com')) socials.tiktok = link.href;
                if (href.includes('instagram.com')) socials.instagram = link.href;
                if (href.includes('facebook.com')) socials.facebook = link.href;
                if (href.includes('twitter.com') || href.includes('x.com')) socials.twitter = link.href;
                if (href.includes('youtube.com')) socials.youtube = link.href;
            });
            return socials;
        }
    """)

    data["product_count_hint"] = await page.evaluate("""
        () => {
            if (!document.body) return null;
            const text = document.body.innerText || '';
            const match = text.match(/(\\d+)\\s*products?/i);
            return match ? match[1] : null;
        }
    """)

    data["page_text"] = await page.evaluate("() => (document.body && document.body.innerText || '').substring(0, 2000)")

    data["nav_categories"] = await page.evaluate("""
        () => {
            const navs = document.querySelectorAll('nav a, header a, .menu a');
            return Array.from(navs).map(a => a.textContent.trim()).filter(t => t.length > 0).slice(0, 20);
        }
    """)

    return data


# ============================================================
# POPUP DISMISSAL
# ============================================================

async def _dismiss_popups(page):
    """Dismiss popups, modals, cookie banners, newsletter signups, geo-selectors."""
    # Press Escape — closes most modals
    for _ in range(3):
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(400)
        except Exception:
            pass

    close_selectors = [
        'button[aria-label*="lose" i]',
        'button[aria-label*="ismiss" i]',
        '[class*="close-button" i]', '[class*="close-icon" i]',
        '[class*="modal-close" i]', '[class*="popup-close" i]',
        '[class*="CloseButton" i]',
        'button:has-text("Accept")', 'button:has-text("Accept All")',
        'button:has-text("Accept Cookies")', 'button:has-text("Got it")',
        'button:has-text("No thanks")', 'button:has-text("No, thanks")',
        'button:has-text("Maybe later")', 'button:has-text("Not now")',
        'button:has-text("Close")',
        '[id*="cookie" i] button', '[class*="cookie" i] button',
        '[id*="consent" i] button',
        'button:has-text("Stay")', 'button:has-text("Continue")',
        'button:has-text("US")', 'button:has-text("United States")',
        '[class*="locale" i] button', '[class*="country" i] button',
        '[class*="geo" i] button', '[class*="region" i] button',
        '[class*="modal" i] [class*="close" i]',
        '[class*="dialog" i] [class*="close" i]',
        '[role="dialog"] button[aria-label]',
        'button[class*="close-modal" i]', 'button[class*="modal-close" i]',
        '.modal button.close',
    ]

    for selector in close_selectors:
        try:
            elements = await page.query_selector_all(selector)
            for el in elements:
                if await el.is_visible():
                    await el.click(timeout=1500)
                    await page.wait_for_timeout(300)
        except Exception:
            pass

    await page.wait_for_timeout(500)


async def _nuke_all_overlays(page):
    """Nuclear option: forcefully remove ALL fixed/absolute overlays and re-enable scrolling."""
    logger.info("[ScraperAgent] Nuking all overlays...")
    await page.evaluate("""
        () => {
            // Remove all fixed/absolute elements with z-index
            document.querySelectorAll('*').forEach(el => {
                const s = getComputedStyle(el);
                if ((s.position === 'fixed' || s.position === 'absolute') &&
                    parseInt(s.zIndex || 0) > 5) {
                    const tag = el.tagName.toLowerCase();
                    // Don't remove the main content containers
                    if (tag !== 'main' && tag !== 'section' && tag !== 'article' &&
                        !el.querySelector('img[src*="product"], img[src*="cdn"]')) {
                        el.remove();
                    }
                }
            });

            // Also remove by common class patterns
            const patterns = [
                'modal', 'popup', 'overlay', 'backdrop', 'dialog',
                'newsletter', 'subscribe', 'cookie', 'consent', 'gdpr',
                'chat', 'intercom', 'drift', 'hubspot', 'crisp', 'zendesk',
                'announcement', 'promo-bar', 'banner-top',
            ];
            patterns.forEach(p => {
                document.querySelectorAll('[class*="' + p + '"], [id*="' + p + '"]').forEach(el => {
                    const s = getComputedStyle(el);
                    if (s.position === 'fixed' || s.position === 'absolute') el.remove();
                });
            });

            // Re-enable scrolling
            document.body.style.overflow = 'auto';
            document.documentElement.style.overflow = 'auto';
            document.body.classList.remove('no-scroll', 'modal-open', 'overflow-hidden');
            document.documentElement.classList.remove('no-scroll', 'modal-open', 'overflow-hidden');
        }
    """)
    await page.wait_for_timeout(500)


# ============================================================
# PRODUCT FINDING + SCREENSHOT
# ============================================================

async def _find_owner_name(page, base_url):
    """Try to find the owner/founder name from the About page."""
    about_paths = ["/pages/about", "/pages/about-us", "/about", "/about-us", "/pages/our-story"]

    for path in about_paths:
        try:
            about_url = base_url.rstrip("/") + path
            resp = await page.goto(about_url, wait_until="domcontentloaded", timeout=10000)
            if not resp or resp.status >= 400:
                continue

            await page.wait_for_timeout(2000)

            # Use JS to extract founder/owner name from the about page
            name = await page.evaluate("""
                () => {
                    const text = document.body.innerText;

                    // Look for common patterns: "Founded by X", "CEO: X", "Owner: X"
                    const patterns = [
                        /(?:founded|co-founded|started|created|built)\s+by\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})/i,
                        /(?:founder|co-founder|ceo|owner)\s*[:\\-–]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})/i,
                        /(?:founder|co-founder|ceo|owner)\s*,?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})/i,
                        /([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s*,?\s*(?:founder|co-founder|ceo|owner)/i,
                        /(?:Hi,?\s+I'm|Hey,?\s+I'm|I'm)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)/i,
                        /(?:meet|about)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s*[,\\.]/i,
                    ];

                    for (const pattern of patterns) {
                        const match = text.match(pattern);
                        if (match && match[1]) {
                            const name = match[1].trim();
                            // Basic validation — should be 2-30 chars, not common words
                            const skip = ['The Company', 'Our Team', 'Our Story', 'The Brand', 'About Us', 'Read More'];
                            if (name.length >= 3 && name.length <= 30 && !skip.includes(name)) {
                                return name;
                            }
                        }
                    }
                    return null;
                }
            """)

            if name:
                logger.info(f"[ScraperAgent] Found owner on {path}: {name}")
                return name

        except Exception:
            continue

    return None


async def _get_product_url_from_api(base_url, platform):
    """Fetch a real product URL from the store's API (works for Shopify, some WooCommerce)."""
    import requests as sync_requests

    clean_url = base_url.rstrip("/")

    # Shopify: /products.json is publicly accessible
    if platform.lower() in ("shopify", ""):
        try:
            resp = sync_requests.get(
                f"{clean_url}/products.json?limit=5&sort_by=created-descending",
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                timeout=10,
                allow_redirects=True,
            )
            if resp.status_code == 200:
                data = resp.json()
                products = data.get("products", [])
                if products:
                    # Pick first product with images
                    for p in products:
                        handle = p.get("handle")
                        if handle and p.get("images"):
                            product_url = f"{clean_url}/products/{handle}"
                            logger.info(f"[ScraperAgent] Found product via API: {p.get('title')} → {product_url}")
                            return product_url
        except Exception as e:
            logger.warning(f"[ScraperAgent] Shopify products.json failed: {e}")

    # WooCommerce: /wp-json/wc/v3/products (usually requires auth, but try)
    if platform.lower() == "woocommerce":
        try:
            resp = sync_requests.get(
                f"{clean_url}/wp-json/wc/store/products?per_page=3",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            if resp.status_code == 200:
                products = resp.json()
                if products and isinstance(products, list):
                    p = products[0]
                    permalink = p.get("permalink") or p.get("link")
                    if permalink:
                        logger.info(f"[ScraperAgent] Found WooCommerce product: {permalink}")
                        return permalink
        except Exception:
            pass

    return None


async def _screenshot_product_page(page, screenshot_path, brand_name):
    """Screenshot a specific product page — wait for load, dismiss popups, scroll to top."""
    await page.wait_for_timeout(5000)

    try:
        await _dismiss_popups(page)
    except Exception:
        pass

    await page.wait_for_timeout(1000)
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(2000)

    await page.screenshot(path=screenshot_path, full_page=False)
    logger.info(f"[ScraperAgent] Product page screenshot taken: {page.url}")
    return True


async def _screenshot_collection_page(page, screenshot_path: str, brand_name: str) -> bool:
    """Screenshot the collection/catalog page showing multiple products.
    This looks like a human browsed their site — matches the outreach email style.
    """
    # Wait for page to settle
    await page.wait_for_timeout(3000)

    # Scroll down slightly to get past hero banners and show the product grid
    await page.evaluate("""
        () => {
            // Try to find the product grid and scroll to it
            const gridSelectors = [
                '[class*="product-grid"]', '[class*="ProductGrid"]',
                '[class*="collection-grid"]', '[class*="product-list"]',
                '[class*="products-container"]', '[class*="grid-container"]',
                '[class*="product-catalog"]', '.collection-products',
                'ul[class*="product"]', 'div[class*="grid"]',
            ];
            for (const sel of gridSelectors) {
                const el = document.querySelector(sel);
                if (el) {
                    const rect = el.getBoundingClientRect();
                    if (rect.height > 200) {
                        window.scrollTo({ top: window.scrollY + rect.top - 20, behavior: 'instant' });
                        return;
                    }
                }
            }

            // Fallback: scroll past header/hero to where products likely start
            // Look for the first product card image
            const productImages = document.querySelectorAll('a[href*="/products/"] img, a[href*="/product/"] img');
            if (productImages.length > 0) {
                const first = productImages[0];
                const rect = first.getBoundingClientRect();
                window.scrollTo({ top: window.scrollY + rect.top - 30, behavior: 'instant' });
                return;
            }

            // Last resort: scroll down 300px to get past nav/hero
            window.scrollBy(0, 300);
        }
    """)

    await page.wait_for_timeout(2000)

    # Take the screenshot — shows product grid like a human browsing
    await page.screenshot(path=screenshot_path, full_page=False)
    logger.info(f"[ScraperAgent] Collection page screenshot taken: {page.url}")
    return True


async def _find_and_screenshot_product(page, screenshot_path: str, brand_name: str) -> bool:
    """Find a product link, navigate to it, and take a human-looking screenshot."""

    # Step 1: Find a product URL
    product_href = await page.evaluate("""
        () => {
            const productPatterns = ['/products/', '/product/', '/shop/', '/item/', '/p/'];
            const allLinks = Array.from(document.querySelectorAll('a[href]'));

            for (const pattern of productPatterns) {
                for (const link of allLinks) {
                    const href = link.href || '';
                    const rect = link.getBoundingClientRect();
                    if (href.includes(pattern) && rect.width > 50 && rect.height > 50 &&
                        rect.top < 3000 && rect.top > 0 &&
                        !href.includes('/collections') && !href.includes('/categories') &&
                        !href.includes('/cart') && !href.includes('/account') &&
                        !href.endsWith(pattern) && !href.endsWith(pattern.slice(0, -1))) {
                        return href;
                    }
                }
            }

            // Fallback: any link wrapping an image that looks like a product
            for (const link of allLinks) {
                const href = link.href || '';
                const img = link.querySelector('img');
                const rect = link.getBoundingClientRect();
                if (img && rect.width > 100 && rect.height > 100 &&
                    rect.top > 0 && rect.top < 3000 &&
                    href.length > 20 && !href.endsWith('#') &&
                    !href.includes('javascript:') &&
                    !href.includes('instagram') && !href.includes('facebook') &&
                    !href.includes('twitter') && !href.includes('tiktok') &&
                    !href.includes('youtube') && !href.includes('pinterest') &&
                    !href.includes('/blog') && !href.includes('/about') &&
                    !href.includes('/contact') && !href.includes('/faq') &&
                    !href.includes('/policy') && !href.includes('/terms')) {
                    return href;
                }
            }
            return null;
        }
    """)

    if not product_href:
        # Scroll to trigger lazy-loaded products
        logger.info("[ScraperAgent] No product found, scrolling to trigger lazy load...")
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 600)")
            await page.wait_for_timeout(1500)

        product_href = await page.evaluate("""
            () => {
                const allLinks = Array.from(document.querySelectorAll('a[href]'));
                const patterns = ['/products/', '/product/', '/shop/', '/item/', '/p/'];
                for (const p of patterns) {
                    for (const link of allLinks) {
                        const href = link.href || '';
                        if (href.includes(p) && !href.includes('/collections') &&
                            !href.endsWith(p) && !href.endsWith(p.slice(0, -1))) {
                            return href;
                        }
                    }
                }
                for (const link of allLinks) {
                    const img = link.querySelector('img');
                    const href = link.href || '';
                    if (img && href.length > 20 && !href.endsWith('#') &&
                        !href.includes('javascript:') && !href.includes('instagram') &&
                        !href.includes('facebook') && !href.includes('twitter') &&
                        !href.includes('youtube') && !href.includes('/blog') &&
                        !href.includes('/about')) {
                        return href;
                    }
                }
                return null;
            }
        """)

    if not product_href:
        logger.warning(f"[ScraperAgent] No product link found for {brand_name}, screenshotting current page")
        await page.screenshot(path=screenshot_path, full_page=False)
        return True

    # Step 2: Clean URL
    if "?" in product_href:
        parsed = urlparse(product_href)
        clean_params = {k: v for k, v in parse_qs(parsed.query).items()
                        if k in ("variant", "color", "size", "v")}
        clean_query = urlencode(clean_params, doseq=True)
        product_href = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if clean_query:
            product_href += f"?{clean_query}"

    # Step 3: Navigate to product page
    logger.info(f"[ScraperAgent] Navigating to product: {product_href}")
    try:
        await page.goto(product_href, wait_until="load", timeout=30000)
    except Exception as e:
        logger.warning(f"[ScraperAgent] Navigation timeout, continuing: {e}")

    # Step 4: Wait for render
    await page.wait_for_timeout(5000)

    # Step 5: Dismiss popups
    await _dismiss_popups(page)
    await page.wait_for_timeout(1000)

    # Step 6: Scroll to top
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(2000)

    # Step 7: Screenshot
    await page.screenshot(path=screenshot_path, full_page=False)
    logger.info(f"[ScraperAgent] Product screenshot taken: {page.url}")
    return True


# ============================================================
# BROWSER-USE FALLBACK (requires Python 3.11+ and browser-use)
# ============================================================

async def _browser_use_fallback(brand_name: str, product_url: str, screenshot_path: str) -> bool:
    """Use browser-use AI agent as fallback when Playwright fails.
    The AI visually navigates the page, dismisses popups, and screenshots the product.
    """
    if not BROWSER_USE_AVAILABLE:
        return False

    try:
        agent = Agent(
            task=f"""Go to {product_url}
            1. Dismiss any popups, cookie banners, or newsletter modals
            2. Make sure you can see the main product image, product name, and price
            3. If there are still popups blocking the view, close them
            4. Take a screenshot of the product page
            Save the screenshot to: {screenshot_path}""",
        )
        await agent.run()
        logger.info(f"[ScraperAgent] browser-use fallback succeeded for {brand_name}")
        return os.path.exists(screenshot_path)

    except Exception as e:
        logger.error(f"[ScraperAgent] browser-use fallback failed for {brand_name}: {e}")
        return False


# ============================================================
# TIKTOK AD LIBRARY
# ============================================================

async def _check_tiktok_ad_library(page, brand_name: str) -> dict:
    """Check TikTok Ad Library for brand's ads."""
    result = {
        "is_running_ads": False,
        "ad_count": 0,
        "ad_details": [],
        "error": None,
    }

    try:
        search_url = f"https://library.tiktok.com/ads?region=all&keyword={brand_name.replace(' ', '+')}"
        logger.info(f"[ScraperAgent] Visiting TikTok Ad Library: {search_url}")

        await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(3000)

        page_text = await page.evaluate("() => document.body.innerText")

        no_results = ["no results", "no ads found", "0 results", "nothing to show"]
        if any(phrase in page_text.lower() for phrase in no_results):
            logger.info(f"[ScraperAgent] No TikTok ads found for {brand_name}")
            return result

        ad_data = await page.evaluate("""
            () => {
                const cards = document.querySelectorAll(
                    '[class*="ad-card"], [class*="AdCard"], [class*="search-result"], [class*="creative-card"]'
                );
                if (cards.length > 0) {
                    return {
                        count: cards.length,
                        details: Array.from(cards).slice(0, 5).map(c => ({
                            text: c.innerText.substring(0, 200).trim()
                        }))
                    };
                }
                const text = document.body.innerText;
                const m = text.match(/(\\d+)\\s*(?:results?|ads?|creatives?)/i);
                if (m && parseInt(m[1]) > 0) return { count: parseInt(m[1]), details: [] };
                const len = text.replace(/\\s+/g, '').length;
                if (len > 500) return { count: -1, details: [{ text: "Ads detected" }] };
                return { count: 0, details: [] };
            }
        """)

        if ad_data["count"] != 0:
            result["is_running_ads"] = True
            result["ad_count"] = ad_data["count"] if ad_data["count"] > 0 else "unknown"
            result["ad_details"] = ad_data["details"]
            logger.info(f"[ScraperAgent] TikTok ads FOUND for {brand_name}: ~{result['ad_count']}")
        else:
            logger.info(f"[ScraperAgent] No TikTok ads found for {brand_name}")

    except Exception as e:
        result["error"] = str(e)
        logger.warning(f"[ScraperAgent] TikTok Ad Library check failed for {brand_name}: {e}")

    return result


# ============================================================
# UTILS
# ============================================================

def _detect_platform(html: str) -> str:
    """Detect ecommerce platform from page source."""
    html_lower = html.lower()
    if "shopify" in html_lower or "cdn.shopify.com" in html_lower:
        return "Shopify"
    if "woocommerce" in html_lower or "wc-" in html_lower:
        return "WooCommerce"
    if "bigcommerce" in html_lower:
        return "BigCommerce"
    if "magento" in html_lower:
        return "Magento"
    if "squarespace" in html_lower:
        return "Squarespace"
    if "wix" in html_lower:
        return "Wix"
    return "Unknown"
