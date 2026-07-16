import json
import logging
import os
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
WRITER_MODEL = os.getenv("OLLAMA_WRITER_MODEL", "qwen2.5:14b-instruct")
IMAGE_PROMPT_MODEL = os.getenv("OLLAMA_IMAGE_PROMPT_MODEL", "llama3.2:3b")
TIMEOUT = int(os.getenv("OLLAMA_REQUEST_TIMEOUT", "600"))
POST_LANGUAGE = os.getenv("POST_LANGUAGE", "German")

_WRITER_SYSTEM = """\
Du bist ein erfahrener LinkedIn-Ghostwriter für LogAid — KI-gestützte Dispositionsautomatisierung \
für Speditionen im deutschen Mittelstand. Schreibe aus einem Artikel EINEN LinkedIn-Beitrag. \
Der Leser ist Disponent oder Inhaber einer mittelständischen Spedition. Er interessiert sich nicht für \
"Logistik allgemein" — er denkt in: Kunden die wegen Lieferverzug anrufen, Frachtraten, \
Kundendaten im Postfach, Tagesgeschäft, Regulierung die seinen Betrieb trifft. \
Ton: direkt, sachkundig, dringlich, unprätentiös — kein Corporate Speak. \
Regeln: starker Hook in Zeile 1 (konkretes Datum, Zahl oder reframtes Ereignis, max 12 Wörter); \
120–180 Wörter; Ich-Perspektive; \
wenn eine narrative Brücke vorgegeben ist: öffne mit dem realen Ereignis (keine LogAid-Nennung), \
erkläre den konkreten Speditions-Impact, verbinde ihn erst zuletzt mit LogAid — der Leser soll denken \
"das betrifft mich" oder "das könnte mich betreffen"; \
LogAid höchstens einmal erwähnen, immer nach dem Argument — nie als Prämisse; \
nie eine im Artikel genannte private Firma (außer LogAid) für ihre Leistungen, Lösungen oder \
Erfolge loben oder hervorheben — wird eine Drittfirma erwähnt, immer analytisch und kritisch \
bleiben (Risiken, Trade-offs, Bedeutung für Wettbewerber), nie im Ton einer Erfolgsgeschichte \
oder Werbung; \
ein Call-to-Action oder eine offene Frage am Ende; 5–7 Hashtags am Ende, nie inline; \
kein Clickbait; verbotene Phrasen: "innovative Lösung", "ganzheitlich", "Synergien", \
"Wir freuen uns zu verkünden", "revolutionär", "state of the art". \
Antworte NUR als JSON: {"post_text": "..."}."""

_RELEVANCE_SYSTEM = """\
Du bewertest, ob eine Nachricht eine konkrete, nicht-offensichtliche Auswirkung \
auf den operativen Alltag einer deutschen Spedition hat. \
Der Leser ist Disponent oder Inhaber einer mittelständischen Spedition — kein Supply-Chain-Manager \
eines Großkonzerns. Er denkt in: Kunden die wegen Lieferverzug anrufen, Frachtraten, \
Kundendaten im Postfach, Tagesgeschäft, Regulierung die seinen Betrieb trifft. \
Kriterien für Relevanz (mind. eines muss zutreffen): \
direkte Auswirkung auf Frachtpreise/Lieferzeiten/Kapazität auf europäischen oder globalen Routen; \
Auswirkung auf Datensicherheit oder Compliance einer Spedition in Deutschland/EU; \
Regulierung der EU, Deutschlands oder eines Landes mit direktem Einfluss auf europäische Speditionen; \
Technologie die im Alltag einer deutschen Spedition eingesetzt wird, ändert sich fundamental; \
globale Ereignisse (Hafenblockaden, Angriffe auf Schifffahrtsrouten, Streiks) die europäische Lieferketten konkret treffen. \
Kriterien für NICHT-Relevanz — eines dieser Merkmale reicht für false: \
rein US-amerikanische Regulierung (FMCSA, DOT, FRA, STB) ohne Auswirkung auf Europa; \
rein US-interne Marktentwicklungen ohne Bezug zu Import/Export nach Europa; \
"Logistik ist wichtig" oder allgemeine Wirtschaftsnachrichten ohne Speditions-Impact; \
Energie- und Klimanachrichten ohne direkten operativen Effekt für Speditionen heute; \
Tech-Nachrichten oder Unternehmenskäufe ohne operativen Einfluss auf Speditionen. \
Antworte NUR als JSON: {"relevante": true, "motivo": "Begründung auf Deutsch"} \
oder {"relevante": false, "motivo": "Begründung auf Deutsch"}."""

_BRIDGE_SYSTEM = """\
Du bist Analytiker für deutsche Speditionen.

Baue eine präzise argumentative Brücke zwischen einem Nachrichtenereignis und dem Alltag einer Spedition.

Fülle dieses JSON aus:

{
  "o_que_aconteceu": "1-2 Sätze: Was genau passiert ist (konkret, faktenbasiert)",
  "impacto_para_spedition": "Konkrete Auswirkung für eine Spedition. NUR wenn logisch belegbar.",
  "risco_ou_oportunidade": "Konkretes Risiko oder Chance im Betrieb",
  "conexao_logaid": "Optional: ehrliche Verbindung zu LogAid",
  "empresa_mencionada": "Optional: Name der im Artikel zitierten Drittfirma, sonst leerer String"
}

WICHTIG:

Erlaubte Arten von Impact:
1. Direkter operativer Effekt (NUR wenn belegbar)
2. Strukturelles Risiko (Abhängigkeit, Kontrollverlust, Systemausfall)
3. Strategische Konsequenz (z. B. Vendor Lock-in, Regulierungsmacht)

VERBOTEN:
- Auswirkungen auf "Frachtpreise", "Lieferzeiten", "Kapazität", wenn nicht DIREKT logisch ableitbar
- generische Aussagen über Logistik
- erfundene Kausalität

Wenn KEINE überzeugende Auswirkung existiert:
Antworte:
{"invalid": true}

REGEL ÜBER DRITTFIRMEN:
Wenn der Artikel eine bestimmte private Firma nennt (außer LogAid), behandle sie nur als \
illustratives Beispiel eines größeren Branchenmusters — nie als Protagonistin oder \
"Erfolgsgeschichte". Übernimm nie Lob, Superlative oder werbliche Sprache des Quellartikels \
über diese Firma. Generalisiere wo möglich: statt "Firma X hat Y gemacht" schreibe "manche \
Speditionen machen Y" oder "die Branche bewegt sich in Richtung Y" — außer der Firmenname ist \
für die Glaubwürdigkeit des Fakts zwingend nötig.

Antworte NUR als JSON mit exakt diesen Schlüsseln.
WICHTIG: Wenn impacto_para_spedition allgemeines beschreibt oder 'nicht anwendbar' enthält, \
schreibe in impacto_para_spedition: nicht spezifisch genug. \
Antworte NUR als JSON mit den vier Pflicht-Schlüsseln plus optional empresa_mencionada."""

_AD_CHECK_SYSTEM = """\
Du bewertest, ob ein LinkedIn-Beitrag wie eine unabhängige, kritische Analyse eines \
Branchenthemas klingt — oder wie Werbung/PR für ein bestimmtes DRITTUNTERNEHMEN oder \
dessen Produkt. \
WICHTIG: LogAid ist der Auftraggeber dieses Beitrags — eine kurze, sachliche Erwähnung von \
LogAid am Ende des Beitrags ist der reguläre, erwünschte Zweck des Textes und zählt NIEMALS \
als soa_como_ad=true, selbst wenn sie positiv formuliert ist. Bewerte NUR den Ton gegenüber \
Drittfirmen (jedes im Beitrag genannte Unternehmen außer LogAid). \
Kriterien für "soa_como_ad=true": der Beitrag lobt ein Drittunternehmen, hebt dessen \
Produkt/Lösung positiv hervor, oder liest sich wie eine Erfolgsgeschichte/Case Study eines \
Dritten. \
Antworte NUR als JSON: {"soa_como_ad": true, "motivo": "Begründung auf Deutsch"} \
oder {"soa_como_ad": false, "motivo": "Begründung auf Deutsch"}."""

_ART_DIRECTOR_SYSTEM = """\
You write text-to-image prompts for a clean, professional, editorial LinkedIn header \
image about the German logistics industry. Given the article topic and the post, produce \
ONE concise English prompt. Rules: vivid concrete scene (trucks, ports, warehouses, \
supply chains, etc. as fits the topic); editorial/photographic, modern, neutral palette; \
16:9; NO text, NO logos, NO real faces or brands. \
Answer ONLY as JSON: {"image_prompt": "..."}."""


def _extract_json(text: str, key: str) -> Optional[str]:
    """Try to extract {key: value} from potentially messy LLM output."""
    try:
        data = json.loads(text)
        if key in data:
            return data[key]
    except json.JSONDecodeError:
        pass

    # Fallback: find JSON object in the text
    match = re.search(r'\{[^{}]*"' + re.escape(key) + r'"[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            return data.get(key)
        except json.JSONDecodeError:
            pass

    return None


def _parse_json_obj(text: str, required_keys: list) -> Optional[dict]:
    """Parse a JSON object from LLM output, validating all required keys are present."""
    try:
        data = json.loads(text)
        if isinstance(data, dict) and all(k in data for k in required_keys):
            return data
    except json.JSONDecodeError:
        pass
    # Fallback: find outermost JSON object (handles markdown code blocks, preambles)
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, dict) and all(k in data for k in required_keys):
                return data
        except json.JSONDecodeError:
            pass
    return None


def _chat(model: str, system: str, user: str) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.6},
    }
    resp = requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json=payload,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def _build_kb_context(article: dict) -> str:
    """Retrieve relevant examples and guidelines from the KB and format them for the prompt."""
    try:
        from kb import retrieve_for_article
        query = f"{article.get('title', '')} {article.get('full_text', '')[:300]}"
        context = retrieve_for_article(query)
        parts: list[str] = []
        if context.get("guidelines"):
            parts.append("=== RICHTLINIEN AUS DEM STYLE GUIDE ===")
            for g in context["guidelines"]:
                label = g.get("section") or g.get("source", "")
                parts.append(f"[{label}]\n{g['text']}")
        if context.get("examples"):
            parts.append("=== BEISPIELE GENEHMIGTER POSTS ===")
            for i, ex in enumerate(context["examples"], 1):
                parts.append(f"Beispiel {i} — {ex['title']}:\n{ex['post_text']}")
        if parts:
            return "\n\n".join(parts) + "\n\n"
    except Exception as e:
        logger.debug("KB context skipped: %s", e)
    return ""


def evaluate_relevance(article: dict) -> Optional[dict]:
    """Step 1: Check if article has concrete relevance for a Spedition.

    Returns {"relevante": bool, "motivo": str} or None on failure.
    """
    user_msg = (
        f"Schlagzeile: {article.get('title', '')}\n\n"
        f"Zusammenfassung:\n{article.get('full_text', '')[:1000]}"
    )
    required = ["relevante", "motivo"]
    for attempt in range(2):
        try:
            raw = _chat(WRITER_MODEL, _RELEVANCE_SYSTEM, user_msg)
            data = _parse_json_obj(raw, required)
            if data is not None:
                return data
            logger.warning("Relevance parse failed (attempt %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Relevance check error (attempt %d): %s", attempt + 1, e)
    return None


def evaluate_promotional_tone(post_text: str) -> Optional[dict]:
    """Step 4 (post-generation): flag posts that read as advertising for a third party."""
    required = ["soa_como_ad", "motivo"]
    for attempt in range(2):
        try:
            raw = _chat(WRITER_MODEL, _AD_CHECK_SYSTEM, post_text)
            data = _parse_json_obj(raw, required)
            if data is not None:
                return data
            logger.warning("Ad-check parse failed (attempt %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Ad-check error (attempt %d): %s", attempt + 1, e)
    return None


def build_bridge(article: dict, relevance_motivo: str = "") -> Optional[dict]:
    """Step 2: Build narrative bridge between the news event and Spedition reality.

    Returns {"o_que_aconteceu", "impacto_para_spedition", "risco_ou_oportunidade",
    "conexao_logaid"} or None on failure.
    """
    context = f"Relevanz-Kontext: {relevance_motivo}\n\n" if relevance_motivo else ""
    user_msg = (
        f"{context}"
        f"Schlagzeile: {article.get('title', '')}\n\n"
        f"Artikeltext:\n{article.get('full_text', '')[:2000]}"
    )
    required = ["o_que_aconteceu", "impacto_para_spedition", "risco_ou_oportunidade", "conexao_logaid"]
    for attempt in range(2):
        try:
            raw = _chat(WRITER_MODEL, _BRIDGE_SYSTEM, user_msg)
            data = _parse_json_obj(raw, required)
            if data is not None:
                impact = data.get("impacto_para_spedition", "")
                generic_signals = [
                    "nicht spezifisch",
                    "nicht anwendbar",
                    "allgemein",
                    "hängt von technologie",
                    "keine direkten",
                ]
                if any(s in impact.lower() for s in generic_signals):
                    logger.info("Bridge descartada: impacto genérico — %s", impact[:120])
                    return None
                return data
            logger.warning("Bridge parse failed (attempt %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Bridge build error (attempt %d): %s", attempt + 1, e)
    return None


def generate_post(article: dict, bridge: Optional[dict] = None) -> str:
    """Agent 1: produce a German LinkedIn post for the article.

    If bridge is provided (from build_bridge), it is used as the narrative structure.
    """
    kb_context = _build_kb_context(article)

    bridge_context = ""
    if bridge:
        bridge_context = (
            "=== NARRATIVE BRÜCKE — als Gerüst des Posts verwenden ===\n"
            f"Was passiert ist: {bridge.get('o_que_aconteceu', '')}\n"
            f"Konkreter Impact für eine Spedition: {bridge.get('impacto_para_spedition', '')}\n"
            f"Risiko / Chance: {bridge.get('risco_ou_oportunidade', '')}\n"
            f"LogAid-Verbindung: {bridge.get('conexao_logaid', '')}\n\n"
        )

    user_msg = (
        f"{kb_context}"
        f"{bridge_context}"
        f"Jetzt schreibe einen neuen Post für diesen Artikel:\n\n"
        f"Artikeltitel: {article.get('title', '')}\n\n"
        f"Artikeltext:\n{article.get('full_text', '')[:3000]}"
    )
    for attempt in range(2):
        try:
            raw = _chat(WRITER_MODEL, _WRITER_SYSTEM, user_msg)
            value = _extract_json(raw, "post_text")
            if value:
                return value
            logger.warning("Agent 1 parse failed (attempt %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Agent 1 error (attempt %d): %s", attempt + 1, e)
    return ""


def generate_image_prompt(article: dict, post_text: str) -> str:
    """Agent 2: produce an English text-to-image prompt."""
    user_msg = (
        f"Topic: {article.get('topic', '')}\n"
        f"Article title: {article.get('title', '')}\n\n"
        f"LinkedIn post:\n{post_text[:500]}"
    )
    for attempt in range(2):
        try:
            raw = _chat(IMAGE_PROMPT_MODEL, _ART_DIRECTOR_SYSTEM, user_msg)
            value = _extract_json(raw, "image_prompt")
            if value:
                return value
            logger.warning("Agent 2 parse failed (attempt %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Agent 2 error (attempt %d): %s", attempt + 1, e)
    return f"Professional logistics scene, German supply chain, editorial style, 16:9, no text"
