"""Agent 2: englischer Text-to-Image-Prompt für das Header-Bild eines Posts."""

import logging
import os

from agents import llm

logger = logging.getLogger(__name__)

IMAGE_PROMPT_MODEL = os.getenv("OLLAMA_IMAGE_PROMPT_MODEL", "llama3.2:3b")

_ART_DIRECTOR_SYSTEM = """\
You write text-to-image prompts for a clean, professional, editorial LinkedIn header \
image about the German logistics industry. Given the article topic and the post, produce \
ONE concise English prompt. Rules: vivid concrete scene (trucks, ports, warehouses, \
supply chains, etc. as fits the topic); editorial/photographic, modern, neutral palette; \
16:9; NO text, NO logos, NO real faces or brands. \
Answer ONLY as JSON: {"image_prompt": "..."}."""


def generate_image_prompt(article: dict, post_text: str) -> str:
    """Agent 2: erzeugt einen englischen Text-to-Image-Prompt."""
    user_msg = (
        f"Topic: {article.get('topic', '')}\n"
        f"Article title: {article.get('title', '')}\n\n"
        f"LinkedIn post:\n{post_text[:500]}"
    )
    for attempt in range(2):
        try:
            raw = llm.chat(IMAGE_PROMPT_MODEL, _ART_DIRECTOR_SYSTEM, user_msg)
            value = llm.extract_json(raw, "image_prompt")
            if value:
                return value
            logger.warning("Agent 2: Parsing fehlgeschlagen (Versuch %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Agent 2: Fehler (Versuch %d): %s", attempt + 1, e)
    return "Professional logistics scene, German supply chain, editorial style, 16:9, no text"
