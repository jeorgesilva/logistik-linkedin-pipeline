"""
Phase 2 — Grundgerüst für die Veröffentlichung auf LinkedIn.
Nur aktiv mit LINKEDIN_ENABLED=true. Standardmäßig deaktiviert.

Aktivieren:
1. Eine LinkedIn-Developer-App mit dem Scope w_member_social anlegen.
2. Ein OAuth-2.0-Access-Token besorgen (läuft nach ~60 Tagen ab; LINKEDIN_ACCESS_TOKEN setzen).
3. Die eigene Author-URN ermitteln: urn:li:person:<id> (LINKEDIN_AUTHOR_URN setzen).
4. LINKEDIN_ENABLED=true in .env setzen.
5. publish_approved_drafts() aus einem geplanten Task oder aus n8n aufrufen.

Rate-Limits: ~10–100 Posts/Tag bei persönlichen Tokens.
Empfehlung: lifecycleState="DRAFT" verwenden und manuell in LinkedIn veröffentlichen,
um im Hinblick auf die Nutzungsbedingungen auf der sicheren Seite zu bleiben.
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
    """Bild registrieren + hochladen, gibt die LinkedIn-Bild-URN zurück."""
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
    """Legt einen LinkedIn-Post als ENTWURF (DRAFT) an; gibt die Post-URN zurück."""
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
    """Überträgt freigegebene Posts als Entwürfe an LinkedIn. No-op bei LINKEDIN_ENABLED=false."""
    if not LINKEDIN_ENABLED:
        logger.info("LinkedIn-Veröffentlichung deaktiviert (LINKEDIN_ENABLED=false). Wird übersprungen.")
        return

    if not ACCESS_TOKEN or not AUTHOR_URN:
        logger.error("LINKEDIN_ACCESS_TOKEN oder LINKEDIN_AUTHOR_URN nicht konfiguriert.")
        return

    from db import list_posts, update_post

    approved = [p for p in list_posts(status="approved")]
    logger.info("Veröffentliche %d genehmigte Posts auf LinkedIn", len(approved))

    for post in approved:
        try:
            image_urn = ""
            if post.get("image_path") and os.path.exists(post["image_path"]):
                image_urn = _upload_image(post["image_path"])

            linkedin_urn = _create_draft_post(post["post_text"], image_urn)
            update_post(post["id"], status="posted", linkedin_urn=linkedin_urn)
            logger.info("Post %d veröffentlicht → LinkedIn-URN: %s", post["id"], linkedin_urn)
        except Exception as e:
            logger.error("Veröffentlichung von Post %d fehlgeschlagen: %s", post["id"], e)
