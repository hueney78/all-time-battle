"""Zone registry — loads zones.yaml, provides graph lookup API.

Phase 2 will add adjacency queries, modifier lookup, and zone legality checks.
"""

from __future__ import annotations

from pathlib import Path

from server.config import ZoneDef, ZoneRules, ZonesConfig, load_zones


class ZoneRegistry:
    def __init__(self, cfg: ZonesConfig | None = None, config_dir: Path | None = None):
        if cfg is None:
            cfg = load_zones(config_dir)
        self._zones: dict[str, ZoneDef] = {z.id: z for z in cfg.zones}
        # zones.yaml list order is the arena's left→right order on the TV —
        # ◀/▶ movement steps along it (absolute, no AI direction-guessing).
        self.ordered_ids: list[str] = [z.id for z in cfg.zones]
        self.rules: ZoneRules = cfg.rules

    def get(self, zone_id: str) -> ZoneDef:
        try:
            return self._zones[zone_id]
        except KeyError:
            raise KeyError(f"Unknown zone: {zone_id!r}. Known: {sorted(self._zones)}")

    def adjacent(self, zone_id: str) -> list[str]:
        return self.get(zone_id).adjacent

    def modifier(self, zone_id: str, key: str, default: float = 0) -> float:
        """Read a zone rider generically (GAME_DESIGN §6). Returns the value as
        declared: int for the damage keys, float for the dodge keys."""
        zone = self.get(zone_id)
        return getattr(zone.modifiers, key, default) or default

    def step(self, zone_id: str, delta: int) -> str | None:
        """The zone `delta` steps left(-)/right(+) of zone_id, or None past an
        arena edge — edge-illegal movement renders disabled on the phone."""
        idx = self.ordered_ids.index(zone_id) + delta
        if 0 <= idx < len(self.ordered_ids):
            return self.ordered_ids[idx]
        return None

    def steps_between(self, a: str, b: str) -> int:
        """Signed left/right distance from zone a to zone b."""
        return self.ordered_ids.index(b) - self.ordered_ids.index(a)

    def __contains__(self, zone_id: str) -> bool:
        return zone_id in self._zones

    @property
    def all_ids(self) -> list[str]:
        return sorted(self._zones.keys())
