import json
import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents import build_bridge, evaluate_promotional_tone, evaluate_relevance


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


def test_build_bridge_returns_data_for_concrete_impact():
    raw = json.dumps({
        "o_que_aconteceu": "EU verschärft Grenzwerte",
        "impacto_para_spedition": "Neue Meldepflichten ab Q3",
        "risco_ou_oportunidade": "Bußgeldrisiko bei Verstoß",
        "conexao_logaid": "",
        "empresa_mencionada": "",
    })
    with patch("agents._chat", return_value=raw):
        result = build_bridge({"title": "x", "full_text": "y"})
    assert result["impacto_para_spedition"] == "Neue Meldepflichten ab Q3"


def test_build_bridge_discards_generic_impact():
    raw = json.dumps({
        "o_que_aconteceu": "x",
        "impacto_para_spedition": "nicht spezifisch genug",
        "risco_ou_oportunidade": "y",
        "conexao_logaid": "",
        "empresa_mencionada": "",
    })
    with patch("agents._chat", return_value=raw):
        result = build_bridge({"title": "x", "full_text": "y"})
    assert result is None
