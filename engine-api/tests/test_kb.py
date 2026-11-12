import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import kb

SECTION = "Dieser Abschnitt ist lang genug, um als eigener Chunk eingelesen zu werden, also über 80 Zeichen."


def _write_guide(tmp_path, heading):
    (tmp_path / "06_point_of_view.md").write_text(f"# Titel\n\n## {heading}\n\n{SECTION}\n", encoding="utf-8")


def _collection(existing_ids):
    col = MagicMock()
    col.get.return_value = {"ids": existing_ids}
    return col


def test_reload_removes_sections_that_no_longer_exist(tmp_path):
    _write_guide(tmp_path, "The belief bank")
    col = _collection(["06_point_of_view.md::the_belief_bank", "06_point_of_view.md::o_banco_de_crenças"])
    with patch("kb._get_collections", return_value=(None, col, None)), \
         patch("kb.ingest_guideline", return_value=True):
        result = kb.ingest_style_guide(str(tmp_path))
    col.delete.assert_called_once_with(ids=["06_point_of_view.md::o_banco_de_crenças"])
    assert result == {"ingested": 1, "failed": 0, "removed": 1}


def test_reload_keeps_everything_after_a_failed_section(tmp_path):
    _write_guide(tmp_path, "The belief bank")
    col = _collection(["06_point_of_view.md::o_banco_de_crenças"])
    with patch("kb._get_collections", return_value=(None, col, None)), \
         patch("kb.ingest_guideline", return_value=False):
        result = kb.ingest_style_guide(str(tmp_path))
    col.delete.assert_not_called()
    assert result == {"ingested": 0, "failed": 1, "removed": 0}


def test_reload_without_stale_sections_deletes_nothing(tmp_path):
    _write_guide(tmp_path, "The belief bank")
    col = _collection(["06_point_of_view.md::the_belief_bank"])
    with patch("kb._get_collections", return_value=(None, col, None)), \
         patch("kb.ingest_guideline", return_value=True):
        result = kb.ingest_style_guide(str(tmp_path))
    col.delete.assert_not_called()
    assert result["removed"] == 0
