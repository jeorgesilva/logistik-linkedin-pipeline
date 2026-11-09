import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents import (
    build_bridge,
    evaluate_promotional_tone,
    evaluate_relevance,
    generate_post,
)


def test_evaluate_promotional_tone_flags_ad_like_post():
    raw = json.dumps({"soa_como_ad": True, "motivo": "Lobt Firma X überschwänglich"})
    with patch("agents._chat", return_value=raw):
        result = evaluate_promotional_tone("Firma X hat eine revolutionäre Lösung...")
    assert result == {"soa_como_ad": True, "motivo": "Lobt Firma X überschwänglich"}


def test_evaluate_promotional_tone_passes_independent_analysis():
    raw = json.dumps({"soa_como_ad": False, "motivo": "Kritische, unabhängige Einordnung"})
    with patch("agents._chat", return_value=raw):
        result = evaluate_promotional_tone("Die Branche steht vor einem Kapazitätsproblem...")
    assert result["soa_como_ad"] is False


def test_evaluate_promotional_tone_returns_none_after_failed_parses():
    with patch("agents._chat", return_value="not valid json"):
        result = evaluate_promotional_tone("some post text")
    assert result is None


def test_evaluate_relevance_parses_valid_response():
    raw = json.dumps({"relevante": True, "motivo": "Direkter Frachtraten-Effekt"})
    with patch("agents._chat", return_value=raw):
        result = evaluate_relevance({"title": "Hafenblockade", "full_text": "..."})
    assert result["relevante"] is True


def test_evaluate_relevance_returns_none_after_failed_parses():
    with patch("agents._chat", side_effect=Exception("timeout")):
        result = evaluate_relevance({"title": "x", "full_text": "y"})
    assert result is None


def _angle(impact="x", risk="y"):
    return {
        "o_que_aconteceu": "x",
        "questao_contestada": "x",
        "crenca_aplicada": "",
        "impacto_para_spedition": impact,
        "risco_ou_oportunidade": risk,
        "conexao_frachtwerk": "",
        "empresa_mencionada": "",
    }


def test_build_bridge_returns_data_for_concrete_impact():
    brainstorm_raw = json.dumps({"angulos": [_angle(), _angle(), _angle()]})
    select_raw = json.dumps(_angle(impact="Neue Meldepflichten ab Q3", risk="Bußgeldrisiko bei Verstoß"))
    with patch("agents._chat", side_effect=[brainstorm_raw, select_raw]):
        result = build_bridge({"title": "x", "full_text": "y"})
    assert result["impacto_para_spedition"] == "Neue Meldepflichten ab Q3"


def test_build_bridge_discards_generic_impact():
    brainstorm_raw = json.dumps({"angulos": [_angle(), _angle(), _angle()]})
    select_raw = json.dumps(_angle(impact="nicht spezifisch genug"))
    with patch("agents._chat", side_effect=[brainstorm_raw, select_raw]):
        result = build_bridge({"title": "x", "full_text": "y"})
    assert result is None


def test_build_bridge_discards_when_selection_finds_no_defensible_angle():
    brainstorm_raw = json.dumps({"angulos": [_angle(), _angle(), _angle()]})
    select_raw = json.dumps({"invalid": True, "motivo": "Kein operativer Hebel für eine Spedition"})
    with patch("agents._chat", side_effect=[brainstorm_raw, select_raw]):
        result = build_bridge({"title": "x", "full_text": "y"})
    assert result is None


def test_build_bridge_discards_when_brainstorm_yields_no_angles():
    with patch("agents._chat", return_value="not valid json"):
        result = build_bridge({"title": "x", "full_text": "y"})
    assert result is None


def _dummy_argument(n_words=80):
    return " ".join(["Wort"] * n_words)


def _frame_response(hashtags=None):
    return json.dumps({
        "hook": "Kurze Nachricht mit acht klaren Woertern heute",
        "pivot": "Die meisten sehen darin X. Fuer eine Spedition ist es Y.",
        "closing": "Was bedeutet das fuer Ihren Betrieb?",
        "hashtags": hashtags or ["#Logistik", "#Spedition"],
    })


def test_generate_post_happy_path_assembles_and_returns_revised_text():
    draft_raw = json.dumps({"argument_text": _dummy_argument(80)})
    frame_raw = _frame_response()
    revised_text = _dummy_argument(110)
    revise_raw = json.dumps({"post_text": revised_text, "changed": True, "motivo": "Argument leicht erweitert"})
    with patch("agents._chat", side_effect=[draft_raw, frame_raw, revise_raw]):
        result = generate_post({"title": "x", "full_text": "y"}, bridge=None)
    assert result == revised_text


def test_generate_post_retries_revision_when_forbidden_phrase_survives():
    draft_raw = json.dumps({"argument_text": _dummy_argument(80)})
    frame_raw = _frame_response()
    dirty_text = _dummy_argument(110) + " Frachtwerk hilft dabei."
    clean_text = _dummy_argument(112)
    revise_dirty = json.dumps({"post_text": dirty_text, "changed": True, "motivo": "erweitert"})
    revise_clean = json.dumps({"post_text": clean_text, "changed": True, "motivo": "Phrase entfernt"})
    with patch("agents._chat", side_effect=[draft_raw, frame_raw, revise_dirty, revise_clean]):
        result = generate_post({"title": "x", "full_text": "y"})
    assert result == clean_text
    assert "hilft dabei" not in result


def test_generate_post_returns_empty_when_argument_drafting_fails():
    with patch("agents._chat", return_value="not valid json"):
        result = generate_post({"title": "x", "full_text": "y"})
    assert result == ""


def test_generate_post_returns_empty_when_framing_fails():
    draft_raw = json.dumps({"argument_text": _dummy_argument(80)})
    with patch("agents._chat", side_effect=[draft_raw, "not valid json", "not valid json"]):
        result = generate_post({"title": "x", "full_text": "y"})
    assert result == ""


def test_generate_post_falls_back_to_unrevised_draft_when_revision_fails():
    draft_raw = json.dumps({"argument_text": _dummy_argument(80)})
    frame_raw = _frame_response()
    with patch("agents._chat", side_effect=[draft_raw, frame_raw, "not valid json", "not valid json"]):
        result = generate_post({"title": "x", "full_text": "y"})
    assert "Wort" in result
    assert "Kurze Nachricht" in result


def test_generate_post_discards_if_still_short_after_revision():
    draft_raw = json.dumps({"argument_text": _dummy_argument(70)})
    frame_raw = _frame_response()
    short_revised = "Kurz. " * 20  # 20 Wörter, deutlich unter MIN_POST_WORDS
    revise_raw = json.dumps({"post_text": short_revised, "changed": False, "motivo": ""})
    with patch("agents._chat", side_effect=[draft_raw, frame_raw, revise_raw]):
        result = generate_post({"title": "x", "full_text": "y"})
    assert result == ""
