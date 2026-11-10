import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agents
from agents import correct_german

POST = (
    "Wer seine Nachunternehmer nicht lückenlos dokumentiert haftet im Zweifel mit. "
    "Das kostet nicht nur geld, sondern auch Vertrauen.\n\n"
    "Frachtwerk bündelt diese Nachweise an einem Ort.\n\n"
    "#Logistik #Spedition"
)
FIXED = POST.replace("dokumentiert haftet", "dokumentiert, haftet").replace("nur geld", "nur Geld")


def _enabled():
    return patch.object(agents, "CORRECTOR_MODEL", "german-corrector")


def test_disabled_returns_text_without_calling_model():
    with patch.object(agents, "CORRECTOR_MODEL", ""), patch("agents._chat_text") as chat:
        assert correct_german(POST) == POST
    chat.assert_not_called()


def test_minimal_correction_is_applied():
    with _enabled(), patch("agents._chat_text", return_value=FIXED):
        assert correct_german(POST) == FIXED


def test_rewrite_that_drops_hashtags_is_rejected():
    rewritten = POST.rsplit("\n\n", 1)[0] + "\n\nMehr dazu bald."
    with _enabled(), patch("agents._chat_text", return_value=rewritten):
        assert correct_german(POST) == POST


def test_free_rewrite_with_low_similarity_is_rejected():
    rewritten = (
        "Speditionen müssen jetzt handeln, denn neue Regeln verschärfen die Haftung deutlich.\n\n"
        "Frachtwerk hilft bei der Dokumentation.\n\n#Logistik #Spedition"
    )
    with _enabled(), patch("agents._chat_text", return_value=rewritten):
        assert correct_german(POST) == POST


def test_removed_frachtwerk_mention_is_rejected():
    with _enabled(), patch("agents._chat_text", return_value=FIXED.replace("Frachtwerk", "Die Software")):
        assert correct_german(POST) == POST


def test_model_error_keeps_original():
    with _enabled(), patch("agents._chat_text", side_effect=Exception("model not found")):
        assert correct_german(POST) == POST
