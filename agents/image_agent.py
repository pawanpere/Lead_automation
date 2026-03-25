import os
import logging
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

logger = logging.getLogger("image_agent")

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)


class ImageAgent:
    """Uploads screenshots to Cloudinary and returns hosted URLs."""

    @staticmethod
    def upload(screenshot_path, brand_name=""):
        """Upload a screenshot and return the hosted URL."""
        if not screenshot_path or not os.path.exists(screenshot_path):
            logger.warning(f"[ImageAgent] File not found: {screenshot_path}")
            return None

        safe_name = brand_name.lower().replace(" ", "_").replace("/", "_") if brand_name else "screenshot"

        try:
            result = cloudinary.uploader.upload(
                screenshot_path,
                folder="lead-outreach",
                public_id=safe_name,
                overwrite=True,
            )
            url = result.get("secure_url", "")
            logger.info(f"[ImageAgent] Uploaded {brand_name}: {url}")
            return url
        except Exception as e:
            logger.error(f"[ImageAgent] Upload failed for {brand_name}: {e}")
            return None
