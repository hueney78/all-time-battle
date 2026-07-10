"""The Doodle Crowd — persistent character gallery (GAME_DESIGN §15).

Every character ever drawn is saved here as a plain PNG + JSON pair (no
database). Across game nights the folder becomes the family scrapbook: past
fighters return as tiny spectators in the colosseum stands, and their names are
dropped into the narrator's cameos. A config cap prunes the oldest so the crowd
never becomes a mob; deleting files removes spectators.

Pure file I/O — no game logic. Callers run saves/reads off the event loop.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import random
import time
import uuid
from pathlib import Path

from server.config import GameRules

log = logging.getLogger("doodle.gallery")


class GalleryStore:
    def __init__(self, dir: str | Path, enabled: bool = True, cap: int = 60,
                 cameo_count: int = 3):
        self.enabled = enabled
        self.dir = Path(dir)
        self.cap = max(0, cap)
        self.cameo_count = max(0, cameo_count)

    @classmethod
    def from_rules(cls, rules: GameRules) -> GalleryStore:
        g = rules.settings.gallery
        return cls(dir=g.dir, enabled=g.enabled, cap=g.cap, cameo_count=g.cameo_count)

    # -- writing ----------------------------------------------------------
    def save_match(self, entries: list[dict]) -> list[str]:
        """Persist a finished match's characters. Each entry is a dict:
        {name, stats, team_id, team_name, won, room, png}. Returns the ids saved.
        Never raises — a failed gallery write must not sink the game."""
        if not self.enabled or not entries:
            return []
        saved: list[str] = []
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            for e in entries:
                uid = uuid.uuid4().hex[:12]
                meta = {
                    "id": uid,
                    "name": e.get("name", "Someone"),
                    "stats": e.get("stats", {}),
                    "team_id": e.get("team_id"),
                    "team_name": e.get("team_name"),
                    "won": bool(e.get("won")),
                    "room": e.get("room"),
                    "created": time.time(),
                    "png": e.get("png", ""),
                }
                (self.dir / f"{uid}.json").write_text(json.dumps(meta), encoding="utf-8")
                png_bytes = _decode_png(e.get("png", ""))
                if png_bytes:
                    (self.dir / f"{uid}.png").write_bytes(png_bytes)
                saved.append(uid)
            self._prune()
        except OSError:
            log.exception("gallery save failed")
        return saved

    def _prune(self) -> None:
        """Keep only the newest `cap` entries; delete the oldest .json/.png pairs."""
        metas = sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        excess = len(metas) - self.cap
        for p in metas[:max(0, excess)]:
            p.unlink(missing_ok=True)
            p.with_suffix(".png").unlink(missing_ok=True)

    # -- reading ----------------------------------------------------------
    def _entries(self) -> list[dict]:
        if not self.enabled or not self.dir.exists():
            return []
        out: list[dict] = []
        for p in self.dir.glob("*.json"):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def all_names(self) -> list[str]:
        return [e["name"] for e in self._entries() if e.get("name")]

    def roster(self, limit: int | None = None) -> list[dict]:
        """A shuffled sample of past characters for the host stands: each entry is
        {name, png, team_id, won}. `limit` defaults to the configured cap."""
        entries = self._entries()
        random.shuffle(entries)
        n = self.cap if limit is None else limit
        return [
            {"name": e.get("name", "Someone"), "png": e.get("png", ""),
             "team_id": e.get("team_id"), "won": bool(e.get("won"))}
            for e in entries[:max(0, n)]
        ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _decode_png(b64: str) -> bytes | None:
    data = (b64 or "").strip()
    if not data:
        return None
    if data.startswith("data:"):
        data = data.split(",", 1)[1] if "," in data else ""
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None
