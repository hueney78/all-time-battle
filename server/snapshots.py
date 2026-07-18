"""JSON state persistence for crash recovery, debug, and replay.

Writes snapshots/<room>/round-N.json after each resolved round and appends the
AI's per-drawing flavor reads to snapshots/<room>/flavor.jsonl so the human can
mine playtests for drawings that didn't fit any of the five moves — the signal
for what the catalog might be missing (see GAME_DESIGN §14).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.engine.models import Event, GameState


class SnapshotWriter:
    def __init__(self, base_dir: str | Path, room_code: str, enabled: bool = True):
        self.enabled = enabled
        self.dir = Path(base_dir) / f"room-{room_code}"

    def _ensure_dir(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)

    def write_round(self, round_num: int, state: GameState, events: list[Event]) -> Path | None:
        if not self.enabled:
            return None
        self._ensure_dir()
        path = self.dir / f"round-{round_num}.json"
        data: dict[str, Any] = {
            "round": round_num,
            "state": state.model_dump(),
            "events": [e.model_dump() for e in events],
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def append_transcript(self, round_num: int, round_title: str,
                          beats: list[Any]) -> None:
        """Persist the round's narration to transcript.jsonl — the full
        announcer transcript always survives the on-screen log's roll-off
        (GAME_DESIGN §13) and feeds the match poster's best line."""
        if not self.enabled or not beats:
            return
        self._ensure_dir()
        path = self.dir / "transcript.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for b in beats:
                f.write(json.dumps({
                    "round": round_num,
                    "round_title": round_title,
                    "event_id": b.event_id,
                    "speaker": getattr(b, "speaker", "pbp"),
                    "text": b.text,
                }) + "\n")

    def append_flavor(self, round_num: int, actions: list[Any]) -> None:
        """Log each drawing's move + the AI's flavor read — recurring notes about
        drawings that strain one of the five moves are the designer's signal for
        what the catalog might be missing (GAME_DESIGN §14)."""
        if not self.enabled:
            return
        rows = [
            {
                "round": round_num,
                "player_id": a.player_id,
                "move_id": getattr(a, "move_id", "") or ("trap" if a.trap_zone else ""),
                "flavor_summary": getattr(a, "flavor_summary", ""),
                "adaptation_note": a.adaptation_note,
            }
            for a in actions
            if getattr(a, "flavor_summary", "") or a.adaptation_note
        ]
        if not rows:
            return
        self._ensure_dir()
        path = self.dir / "flavor.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
