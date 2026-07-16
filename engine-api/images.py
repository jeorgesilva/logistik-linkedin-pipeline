import hashlib
import logging
import os
import time
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

POLLINATIONS_BASE = os.getenv("POLLINATIONS_BASE", "https://image.pollinations.ai/prompt")
IMAGE_WIDTH = int(os.getenv("IMAGE_WIDTH", "1200"))
IMAGE_HEIGHT = int(os.getenv("IMAGE_HEIGHT", "627"))
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "flux")
IMAGE_DIR = os.getenv("IMAGE_DIR", "/data/images")

_TIMEOUT = 120
_BACKOFF = [5, 15, 30]


def _make_seed(post_id: int, extra: int = 0) -> int:
    raw = f"{post_id}-{extra}"
    return int(hashlib.sha256(raw.encode()).hexdigest(), 16) % (2**31)


def generate_image(post_id: int, image_prompt: str, seed: int) -> str:
    """Download image from Pollinations and save locally. Returns relative path."""
    os.makedirs(IMAGE_DIR, exist_ok=True)
    encoded_prompt = quote(image_prompt)
    url = (
        f"{POLLINATIONS_BASE}/{encoded_prompt}"
        f"?width={IMAGE_WIDTH}&height={IMAGE_HEIGHT}"
        f"&model={IMAGE_MODEL}&nologo=true&seed={seed}"
    )

    for attempt, backoff in enumerate(_BACKOFF):
        try:
            resp = requests.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            path = os.path.join(IMAGE_DIR, f"{post_id}.jpg")
            with open(path, "wb") as f:
                f.write(resp.content)
            logger.info("Image saved: %s", path)
            return path
        except Exception as e:
            logger.warning("Image attempt %d failed: %s", attempt + 1, e)
            if attempt < len(_BACKOFF) - 1:
                time.sleep(backoff)

    return ""


def regenerate_image(post_id: int, image_prompt: str, current_seed: int) -> tuple[str, int]:
    """Generate with a new seed. Returns (path, new_seed)."""
    new_seed = _make_seed(post_id, extra=current_seed + 1)
    path = generate_image(post_id, image_prompt, new_seed)
    return path, new_seed
