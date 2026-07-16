"""
Phase 2 — LinkedIn publishing stub.
Gated behind LINKEDIN_ENABLED=true. Disabled by default.

To enable:
1. Create a LinkedIn developer app with w_member_social scope.
2. Obtain an OAuth 2.0 access token (expires ~60 days; set LINKEDIN_ACCESS_TOKEN).
3. Find your author URN: urn:li:person:<id> (set LINKEDIN_AUTHOR_URN).
4. Set LINKEDIN_ENABLED=true in .env.
5. Call publish_approved_drafts() from a scheduled task or n8n.

Rate limits: ~10–100 posts/day for personal tokens.
Recommendation: use lifecycleState="DRAFT" and publish manually from LinkedIn
to stay within ToS safe posture.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

LINKEDIN_ENABLED = os.getenv("LINKEDIN_ENABLED", "false").lower() == "true"
ACCESS_TOKEN = os.getenv("LINKEDIN_ACCESS_TOKEN", "")
AUTHOR_URN = os.getenv("LINKEDIN_AUTHOR_URN", "")
API_VERSION = os.getenv("LINKEDIN_API_VERSION", "202505")

_BASE = "https://api.linkedin.com/rest"
_HEADERS_BASE = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "LinkedIn-Version": API_VERSION,
    "X-Restli-Protocol-Version": "2.0.0",
    "Content-Type": "application/json",
}


def _upload_image(image_path: str) -> str:
    """Register + upload image, return LinkedIn image URN."""
    init_resp = requests.post(
        f"{_BASE}/images?action=initializeUpload",
        headers=_HEADERS_BASE,
        json={"initializeUploadRequest": {"owner": AUTHOR_URN}},
        timeout=30,
    )
    init_resp.raise_for_status()
    data = init_resp.json().get("value", {})
    upload_url = data["uploadUrl"]
    image_urn = data["image"]

    with open(image_path, "rb") as f:
        put_resp = requests.put(upload_url, data=f, timeout=60)
        put_resp.raise_for_status()

    return image_urn


def _create_draft_post(post_text: str, image_urn: str) -> str:
    """Create a LinkedIn DRAFT post; return post URN."""
    body = {
        "author": AUTHOR_URN,
        "commentary": post_text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "DRAFT",
        "isReshareDisabledByAuthor": False,
    }
    if image_urn:
        body["content"] = {
            "media": {
                "altText": "Logistik-Headerbild",
                "id": image_urn,
            }
        }
    resp = requests.post(
        f"{_BASE}/posts",
        headers=_HEADERS_BASE,
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.headers.get("x-restli-id", "")


def publish_approved_drafts():
    """Push approved posts to LinkedIn as drafts. No-op if LINKEDIN_ENABLED=false."""
    if not LINKEDIN_ENABLED:
        logger.info("LinkedIn publishing disabled (LINKEDIN_ENABLED=false). Skipping.")
        return

    if not ACCESS_TOKEN or not AUTHOR_URN:
        logger.error("LINKEDIN_ACCESS_TOKEN or LINKEDIN_AUTHOR_URN not configured.")
        return

    from db import list_posts, update_post

    approved = [p for p in list_posts(status="approved")]
    logger.info("Publishing %d approved posts to LinkedIn", len(approved))

    for post in approved:
        try:
            image_urn = ""
            if post.get("image_path") and os.path.exists(post["image_path"]):
                image_urn = _upload_image(post["image_path"])

            linkedin_urn = _create_draft_post(post["post_text"], image_urn)
            update_post(post["id"], status="posted", linkedin_urn=linkedin_urn)
            logger.info("Posted %d → LinkedIn URN: %s", post["id"], linkedin_urn)
        except Exception as e:
            logger.error("Failed to publish post %d: %s", post["id"], e)
