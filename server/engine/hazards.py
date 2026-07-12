"""Hazard registry — loads hazards.yaml, provides lookup API.

The Arena Gremlin hazard palette (GAME_DESIGN.md §10). Like zones/moves, it's
a generic data-driven registry: adding a hazard is a YAML-only change. The
resolver reads a hazard's declarative effect (zone damage or a forced move —
v2.1: hazards are damage-or-push only) and reuses the existing machinery.
"""

from __future__ import annotations

from pathlib import Path

from server.config import HazardDef, HazardsConfig, load_hazards


class HazardRegistry:
    def __init__(self, cfg: HazardsConfig | None = None, config_dir: Path | None = None):
        if cfg is None or not cfg.hazards:
            cfg = load_hazards(config_dir)
        self._defs: dict[str, HazardDef] = cfg.hazards

    def get(self, hazard_id: str) -> HazardDef:
        try:
            return self._defs[hazard_id]
        except KeyError:
            raise KeyError(f"Unknown hazard: {hazard_id!r}. Known: {sorted(self._defs)}")

    def __contains__(self, hazard_id: str) -> bool:
        return hazard_id in self._defs

    @property
    def all_ids(self) -> list[str]:
        return sorted(self._defs.keys())
