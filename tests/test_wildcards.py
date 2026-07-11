"""Wildcard miner — aggregates snapshots/*/wildcards.jsonl for archetype hunting."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from mine_wildcards import format_report, load_wildcards, mine  # noqa: E402


def _write(base: Path, room: str, rows: list[dict]) -> None:
    d = base / f"room-{room}"
    d.mkdir(parents=True)
    (d / "wildcards.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
    )


def test_load_and_mine_aggregates_across_rooms(tmp_path):
    _write(tmp_path, "ABCD", [
        {"round": 2, "player_id": "p1", "adaptation_note": "the character grows GIANT and stomps"},
        {"round": 4, "player_id": "p2", "adaptation_note": "player draws themselves as a giant"},
    ])
    _write(tmp_path, "WXYZ", [
        {"round": 3, "player_id": "p3", "adaptation_note": "a giant tidal wave, nothing fits"},
    ])

    rows = load_wildcards(tmp_path)
    assert len(rows) == 3

    result = mine(rows)
    assert result["total"] == 3
    assert dict(result["per_room"]) == {"room-ABCD": 2, "room-WXYZ": 1}

    kw = dict(result["top_keywords"])
    assert kw["giant"] == 3                       # the recurring shape across every note
    assert "grows" in kw or "stomps" in kw        # one-off shape words still surface
    assert "the" not in kw and "character" not in kw and "draws" not in kw   # stopwords dropped
    assert result["keyword_examples"]["giant"]    # carries example notes for the human

    report = format_report(result)
    assert "giant" in report and "candidate archetypes" in report


def test_miner_skips_empty_and_malformed_lines(tmp_path):
    d = tmp_path / "room-EMPT"
    d.mkdir()
    (d / "wildcards.jsonl").write_text("\n{bad json}\n   \n", encoding="utf-8")

    rows = load_wildcards(tmp_path)
    assert rows == []
    assert "No wildcards" in format_report(mine(rows))


def test_missing_snapshots_dir_is_not_fatal(tmp_path):
    assert load_wildcards(tmp_path / "does_not_exist") == []
