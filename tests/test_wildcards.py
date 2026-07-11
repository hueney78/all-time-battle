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


def test_v2_rows_mine_wild_interpretation_description(tmp_path):
    """COMBAT V2 rows carry the AI's wild_interpretation — its description is
    the mineable text, with adaptation_note as the fallback."""
    _write(tmp_path, "NEWW", [
        {"round": 1, "player_id": "p1",
         "wild_interpretation": {"condition": None,
                                 "description": "a colossal sandwich falls from the sky"},
         "adaptation_note": None},
        {"round": 2, "player_id": "p2",
         "wild_interpretation": {"condition": "sticky", "description": ""},
         "adaptation_note": "a sandwich again, somehow"},
    ])
    result = mine(load_wildcards(tmp_path))
    kw = dict(result["top_keywords"])
    assert kw.get("sandwich") == 2     # description + fallback note both mined


def test_wild_plays_are_logged_to_jsonl(tmp_path):
    """Every resolved WILD CARD action lands in wildcards.jsonl (§14)."""
    from server.engine.models import ClassifiedAction, WildInterpretation
    from server.snapshots import SnapshotWriter

    w = SnapshotWriter(tmp_path, "ROOM", enabled=True)
    w.append_wildcards(3, [
        ClassifiedAction(player_id="p1", move_id="wild",
                         wild_interpretation=WildInterpretation(
                             condition="sparkly", description="glitter vortex")),
        ClassifiedAction(player_id="p2", move_id="smash", target_id="p1"),
    ])
    rows = load_wildcards(tmp_path)
    assert len(rows) == 1
    assert rows[0]["player_id"] == "p1"
    assert rows[0]["wild_interpretation"]["description"] == "glitter vortex"
