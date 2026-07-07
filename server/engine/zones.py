"""Zone registry — loads zones.yaml, provides graph lookup API.

Phase 2 will add adjacency queries, modifier lookup, and zone legality checks.
"""

from __future__ import annotations

from pathlib import Path

from server.config import ZoneDef, ZoneModifiers, ZonesConfig, ZoneRules, load_zones


class ZoneRegistry:
    def __init__(self, cfg: ZonesConfig | None = None, config_dir: Path | None = None):
        if cfg is None:
            cfg = load_zones(config_dir)
        self._zones: dict[str, ZoneDef] = {z.id: z for z in cfg.zones}
        self.rules: ZoneRules = cfg.rules

    def get(self, zone_id: str) -> ZoneDef:
        try:
            return self._zones[zone_id]
        except KeyError:
            raise KeyError(f"Unknown zone: {zone_id!r}. Known: {sorted(self._zones)}")

    def adjacent(self, zone_id: str) -> list[str]:
        return self.get(zone_id).adjacent

    def modifier(self, zone_id: str, key: str, default: int = 0) -> int:
        zone = self.get(zone_id)
        return getattr(zone.modifiers, key, default) or default

    def __contains__(self, zone_id: str) -> bool:
        return zone_id in self._zones

    @property
    def all_ids(self) -> list[str]:
        return sorted(self._zones.keys())
