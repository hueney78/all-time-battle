"""Flavor miner — aggregates snapshots/*/flavor.jsonl for archetype hunting."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from mine_flavor import format_report, load_flavor, mine  # noqa: E402


def _write(base: Path, room: str, rows: list[dict]) -> None:
    d = base / f"room-{room}"
    d.mkdir(parents=True)
    (d / "flavor.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
    )


def test_load_and_mine_aggregates_across_rooms(tmp_path):
    _write(tmp_path, "ABCD", [
        {"round": 2, "player_id": "p1", "flavor_summary": "the character grows GIANT and stomps"},
        {"round": 4, "player_id": "p2", "flavor_summary": "player draws themselves as a giant"},
    ])
    _write(tmp_path, "WXYZ", [
        {"round": 3, "player_id": "p3", "flavor_summary": "a giant tidal wave, nothing fits"},
    ])

    rows = load_flavor(tmp_path)
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
    (d / "flavor.jsonl").write_text("\n{bad json}\n   \n", encoding="utf-8")

    rows = load_flavor(tmp_path)
    assert rows == []
    assert "No flavor" in format_report(mine(rows))


def test_missing_snapshots_dir_is_not_fatal(tmp_path):
    assert load_flavor(tmp_path / "does_not_exist") == []


def test_row_note_falls_back_to_adaptation_note(tmp_path):
    """flavor_summary is the mineable text, with adaptation_note as the fallback."""
    _write(tmp_path, "NEWW", [
        {"round": 1, "player_id": "p1", "flavor_summary": "a colossal sandwich from the sky"},
        {"round": 2, "player_id": "p2", "flavor_summary": "",
         "adaptation_note": "a sandwich again, somehow"},
    ])
    result = mine(load_flavor(tmp_path))
    kw = dict(result["top_keywords"])
    assert kw.get("sandwich") == 2     # flavor + fallback note both mined


def test_flavor_reads_are_logged_to_jsonl(tmp_path):
    """Every resolved action's flavor read lands in flavor.jsonl (§14)."""
    from server.engine.models import ClassifiedAction
    from server.snapshots import SnapshotWriter

    w = SnapshotWriter(tmp_path, "ROOM", enabled=True)
    w.append_flavor(3, [
        ClassifiedAction(player_id="p1", move_id="blast", target_id="p2",
                         flavor_summary="a glitter vortex"),
        ClassifiedAction(player_id="p2", move_id="smash", target_id="p1"),
        ClassifiedAction(player_id="g1", move_id="", trap_zone="frontline",
                         flavor_summary="a spring-loaded boot"),
    ])
    rows = load_flavor(tmp_path)
    by_pid = {r["player_id"]: r for r in rows}
    assert set(by_pid) == {"p1", "g1"}        # p2 had no flavor → not logged
    assert by_pid["p1"]["flavor_summary"] == "a glitter vortex"
    assert by_pid["g1"]["move_id"] == "trap"  # a gremlin's trap
