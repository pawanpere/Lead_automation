import asyncio
import json
import os
import logging

logger = logging.getLogger("quality_checker")


class QualityChecker:
    """Checks if a screenshot page actually shows products by analyzing the DOM state."""

    @staticmethod
    async def check_page(page, screenshot_path, brand_name=""):
        """Analyze the LIVE page (not just file) to determine if products are visible.

        This is much more reliable than file-size checks because we can inspect
        the actual DOM and see what's in the viewport.

        Returns:
            { "good": bool, "reason": str, "suggestion": str }
        """
        try:
            analysis = await page.evaluate("""
                () => {
                    const vw = window.innerWidth;
                    const vh = window.innerHeight;

                    // 1. Count visible product images in viewport
                    const images = Array.from(document.querySelectorAll('img'));
                    let productImagesInViewport = 0;
                    let largestImgArea = 0;

                    for (const img of images) {
                        const r = img.getBoundingClientRect();
                        if (r.width > 80 && r.height > 80 &&
                            r.top < vh && r.bottom > 0 &&
                            r.left < vw && r.right > 0 &&
                            img.naturalWidth > 50) {
                            productImagesInViewport++;
                            const area = r.width * r.height;
                            if (area > largestImgArea) largestImgArea = area;
                        }
                    }

                    // 2. Check for product-related text in viewport
                    const viewportText = (() => {
                        const walker = document.createTreeWalker(
                            document.body, NodeFilter.SHOW_TEXT
                        );
                        let text = '';
                        let node;
                        while (node = walker.nextNode()) {
                            const r = node.parentElement?.getBoundingClientRect();
                            if (r && r.top < vh && r.bottom > 0) {
                                text += ' ' + node.textContent;
                            }
                        }
                        return text.substring(0, 2000).toLowerCase();
                    })();

                    const hasPrice = /\\$\\d+|\\£\\d+|\\€\\d+|rs\\.?\\s*\\d+|usd|price/i.test(viewportText);
                    const hasAddToCart = /add to (cart|bag)|buy now|shop now/i.test(viewportText);
                    const hasProductTitle = document.querySelector('h1, h2, [class*="product-title"], [class*="ProductTitle"]')?.getBoundingClientRect()?.top < vh;

                    // 3. Check for bad indicators
                    const isFooter = /footer|copyright|©|subscribe|newsletter|privacy policy/i.test(viewportText) &&
                                     !/\\$\\d+/.test(viewportText);
                    const isErrorPage = /404|not found|error|access denied/i.test(viewportText) &&
                                        viewportText.length < 500;
                    const isLoginWall = /sign in|log in|create account|password/i.test(viewportText) &&
                                        productImagesInViewport < 2;
                    const isCartPage = /your cart|shopping cart|checkout|subtotal/i.test(viewportText);
                    const isEmptyCollection = /0 products|no products|no results|nothing here/i.test(viewportText);

                    // 4. Check for overlay blocking
                    let overlayBlocking = false;
                    document.querySelectorAll('*').forEach(el => {
                        const s = getComputedStyle(el);
                        if ((s.position === 'fixed' || s.position === 'absolute') &&
                            parseInt(s.zIndex || 0) > 10) {
                            const r = el.getBoundingClientRect();
                            if (r.width > vw * 0.5 && r.height > vh * 0.5) {
                                overlayBlocking = true;
                            }
                        }
                    });

                    return {
                        productImagesInViewport,
                        largestImgArea,
                        hasPrice,
                        hasAddToCart,
                        hasProductTitle,
                        isFooter,
                        isErrorPage,
                        isLoginWall,
                        isCartPage,
                        isEmptyCollection,
                        overlayBlocking,
                        viewportTextLength: viewportText.length,
                        pageUrl: window.location.href,
                    };
                }
            """)

            # Score the screenshot
            score = 0
            issues = []

            # Good signals
            if analysis["productImagesInViewport"] >= 1:
                score += 3
            if analysis["productImagesInViewport"] >= 3:
                score += 2
            if analysis["hasPrice"]:
                score += 2
            if analysis["hasAddToCart"]:
                score += 2
            if analysis["hasProductTitle"]:
                score += 1
            if analysis["largestImgArea"] > 50000:
                score += 1

            # Bad signals
            if analysis["isFooter"]:
                score -= 5
                issues.append("showing footer/newsletter, not products")
            if analysis["isErrorPage"]:
                score -= 5
                issues.append("error/404 page")
            if analysis["isLoginWall"]:
                score -= 4
                issues.append("login wall blocking content")
            if analysis["isCartPage"]:
                score -= 5
                issues.append("showing cart page, not products")
            if analysis["isEmptyCollection"]:
                score -= 4
                issues.append("empty collection (0 products)")
            if analysis["overlayBlocking"]:
                score -= 3
                issues.append("popup/overlay blocking view")
            if analysis["productImagesInViewport"] == 0:
                score -= 3
                issues.append("no product images visible in viewport")

            good = score >= 3

            # Build suggestion for retry
            suggestion = ""
            if not good:
                if analysis["isFooter"]:
                    suggestion = "scroll to top or try /collections/best-sellers"
                elif analysis["isEmptyCollection"]:
                    suggestion = "try /collections/best-sellers or /shop"
                elif analysis["overlayBlocking"]:
                    suggestion = "nuke overlays and retry"
                elif analysis["productImagesInViewport"] == 0:
                    suggestion = "try different URL — products not loading"
                elif analysis["isCartPage"]:
                    suggestion = "navigated to cart — try /collections/all"
                else:
                    suggestion = "try /collections/best-sellers"

            reason = f"score={score}, images={analysis['productImagesInViewport']}, price={analysis['hasPrice']}"
            if issues:
                reason += f", issues: {', '.join(issues)}"

            logger.info(f"[QualityChecker] {brand_name}: good={good}, {reason}")

            return {
                "good": good,
                "reason": reason,
                "suggestion": suggestion,
                "score": score,
                "details": analysis,
            }

        except Exception as e:
            logger.warning(f"[QualityChecker] Analysis failed: {e}")
            return {"good": True, "reason": f"analysis failed: {e}", "suggestion": ""}
