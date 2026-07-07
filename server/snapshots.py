"""JSON state persistence for crash recovery, debug, and replay.

Writes snapshots/<room>/round-N.json after each resolved round and appends
unplaced `wildcard` classifications to snapshots/<room>/wildcards.jsonl so the
human can mine playtests for new catalog archetypes (see ARCHITECTURE.md §4.4).
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

    def append_wildcards(self, round_num: int, actions: list[Any]) -> None:
        """Log any classification whose catalog_id is `wildcard`."""
        if not self.enabled:
            return
        rows = [
            {
                "round": round_num,
                "player_id": a.player_id,
                "adaptation_note": a.adaptation_note,
            }
            for a in actions
            if getattr(a, "catalog_id", None) == "wildcard"
        ]
        if not rows:
            return
        self._ensure_dir()
        path = self.dir / "wildcards.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
