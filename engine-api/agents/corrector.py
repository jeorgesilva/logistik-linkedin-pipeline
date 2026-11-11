"""Schritt 3d: optionaler Korrekturdurchgang mit dem deutschen Korrekturmodell."""

import difflib
import logging
import os
import re

from agents import llm, rules

logger = logging.getLogger(__name__)


# Schritt 3d — deutsches Korrekturmodell (Grammatik/Rechtschreibung/Zeichensetzung), z. B.
# hf.co/jeorgesilva/llama3.1-german-corrector-q4. Leer = deaktiviert. Eigener Host optional,
# damit das Modell auch auf einem anderen Ollama-Rechner laufen kann.
CORRECTOR_MODEL = os.getenv("OLLAMA_CORRECTOR_MODEL", "").strip()
CORRECTOR_HOST = os.getenv("OLLAMA_CORRECTOR_HOST", "").strip() or llm.OLLAMA_HOST
# Leitplanken: Eine Korrektur darf den Text nur minimal verändern. Ohne strikten Prompt
# schreibt das Modell den Post sonst frei um (Inhalt, Länge, Hashtags) — siehe correct_german().
CORRECTOR_MIN_SIMILARITY = float(os.getenv("CORRECTOR_MIN_SIMILARITY", "0.85"))
CORRECTOR_MAX_LENGTH_DRIFT = float(os.getenv("CORRECTOR_MAX_LENGTH_DRIFT", "0.05"))

_CORRECTOR_SYSTEM = (
    "Du bist ein Korrektor für deutsche Texte. Korrigiere ausschließlich Fehler in Grammatik, "
    "Rechtschreibung und Zeichensetzung. Ändere weder Inhalt, Stil, Wortwahl, Absätze noch Hashtags. "
    "Gib NUR den vollständigen korrigierten Text zurück, ohne Erklärung, ohne Einleitung."
)


def _hashtags(text: str) -> list[str]:
    return re.findall(r"#\w+", text)


def correct_german(post_text: str) -> str:
    """Schritt 3d: Korrekturdurchgang mit dem deutschen Korrekturmodell.

    Das Modell ist auf Lernertexte trainiert (Grammatik/Rechtschreibung), nicht auf das
    Schreiben von Posts. Mit striktem Prompt korrigiert es zuverlässig nur Fehler; ohne
    schreibt es den Text frei um. Deshalb wird das Ergebnis nur übernommen, wenn es eine
    minimale Änderung ist (Ähnlichkeit, Länge, Absätze, Hashtags, Frachtwerk-Nennungen
    unverändert, keine neuen verbotenen Phrasen). Im Zweifel — oder bei jedem Fehler —
    bleibt der unkorrigierte Post erhalten; dieser Schritt verwirft nie einen Post.
    """
    if not CORRECTOR_MODEL or not post_text.strip():
        return post_text
    try:
        corrected = llm.chat_text(CORRECTOR_HOST, CORRECTOR_MODEL, _CORRECTOR_SYSTEM, post_text).strip()
    except Exception as e:
        logger.warning("Korrektur: Fehler beim Aufruf von %s — unkorrigierter Post bleibt: %s", CORRECTOR_MODEL, e)
        return post_text

    original_words, corrected_words = post_text.split(), corrected.split()
    similarity = difflib.SequenceMatcher(None, original_words, corrected_words).ratio()
    length_drift = abs(len(corrected_words) - len(original_words)) / max(len(original_words), 1)
    problems = []
    if not corrected:
        problems.append("leere Antwort")
    if similarity < CORRECTOR_MIN_SIMILARITY:
        problems.append(f"Ähnlichkeit {similarity:.2f}")
    if length_drift > CORRECTOR_MAX_LENGTH_DRIFT:
        problems.append(f"Längenänderung {length_drift:.0%}")
    if corrected.count("\n\n") != post_text.strip().count("\n\n"):
        problems.append("Absätze verändert")
    if _hashtags(corrected) != _hashtags(post_text):
        problems.append("Hashtags verändert")
    if corrected.count("Frachtwerk") != post_text.count("Frachtwerk"):
        problems.append("Frachtwerk-Nennungen verändert")
    if set(rules.find_forbidden_phrases(corrected)) - set(rules.find_forbidden_phrases(post_text)):
        problems.append("neue verbotene Phrase")
    if problems:
        logger.warning("Korrektur verworfen (%s) — unkorrigierter Post bleibt", ", ".join(problems))
        return post_text

    if corrected != post_text.strip():
        logger.info("Korrektur übernommen (Ähnlichkeit %.3f)", similarity)
    return corrected
