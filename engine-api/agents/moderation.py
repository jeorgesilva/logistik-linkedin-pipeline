"""Werbeprüfung: verwirft Posts, die wie Werbung/PR für ein Drittunternehmen klingen."""

import logging

from agents import llm

logger = logging.getLogger(__name__)

_AD_CHECK_SYSTEM = """\
Du bewertest, ob ein LinkedIn-Beitrag wie eine unabhängige, kritische Analyse eines \
Branchenthemas klingt — oder wie Werbung/PR für ein bestimmtes DRITTUNTERNEHMEN oder \
dessen Produkt. \
WICHTIG: Frachtwerk ist der Auftraggeber dieses Beitrags — eine kurze, sachliche Erwähnung von \
Frachtwerk am Ende des Beitrags ist der reguläre, erwünschte Zweck des Textes und zählt NIEMALS \
als sounds_like_ad=true, selbst wenn sie positiv formuliert ist. Bewerte NUR den Ton gegenüber \
Drittfirmen (jedes im Beitrag genannte Unternehmen außer Frachtwerk). \
Kriterien für "sounds_like_ad=true": der Beitrag lobt ein Drittunternehmen, hebt dessen \
Produkt/Lösung positiv hervor, oder liest sich wie eine Erfolgsgeschichte/Case Study eines \
Dritten. \
Nenne in "promoted_company" das beworbene Drittunternehmen (mehrere durch Komma getrennt). \
Antworte NUR als JSON: {"sounds_like_ad": true, "promoted_company": "Name", "reason": "Begründung auf Deutsch"} \
oder {"sounds_like_ad": false, "promoted_company": "", "reason": "Begründung auf Deutsch"}."""


def _only_frachtwerk_promoted(data: dict) -> bool:
    """True, wenn das Modell ausschließlich Frachtwerk als beworbenes Unternehmen nennt."""
    companies = [c.strip() for c in str(data.get("promoted_company") or "").split(",") if c.strip()]
    return bool(companies) and all("frachtwerk" in c.lower() for c in companies)


def evaluate_promotional_tone(post_text: str) -> dict | None:
    """Schritt 4 (nach der Erzeugung): erkennt Posts, die wie Werbung für ein Drittunternehmen klingen.

    Die Ausnahme für Frachtwerk steht zwar im Prompt, wird aber im Code erzwungen: Ein 7B-Modell
    ignorierte sie in Tests bei rund einem Drittel der Aufrufe und verwarf so sogar manuell
    genehmigte Posts wegen „Lob für Frachtwerk“. Nennt das Modell nur Frachtwerk als beworbenes
    Unternehmen, gilt der Post als unbedenklich. Ohne Firmenangabe bleibt das Urteil des Modells
    bestehen (konservativ: lieber verwerfen als Drittwerbung durchlassen).
    """
    required = ["sounds_like_ad", "reason"]
    for attempt in range(2):
        try:
            raw = llm.chat(llm.WRITER_MODEL, _AD_CHECK_SYSTEM, post_text)
            data = llm.parse_json_obj(raw, required)
            if data is not None:
                if data.get("sounds_like_ad") and _only_frachtwerk_promoted(data):
                    logger.info("Werbeprüfung: nur Frachtwerk genannt — nicht als Werbung gewertet: %s", str(data.get("reason", ""))[:120])
                    data = {**data, "sounds_like_ad": False}
                return data
            logger.warning("Werbeprüfung: Parsing fehlgeschlagen (Versuch %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Werbeprüfung: Fehler (Versuch %d): %s", attempt + 1, e)
    return None
