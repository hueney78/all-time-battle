"""Condition registry — loads conditions.yaml, provides lookup API.

Phase 2 will add resolution logic (tick damage, modifier queries, expiry).
"""

from __future__ import annotations

from pathlib import Path

from server.config import ConditionDef, ConditionsConfig, load_conditions


class ConditionRegistry:
    def __init__(self, cfg: ConditionsConfig | None = None, config_dir: Path | None = None):
        if cfg is None:
            cfg = load_conditions(config_dir)
        self._defs: dict[str, ConditionDef] = cfg.conditions

    def get(self, name: str) -> ConditionDef:
        try:
            return self._defs[name]
        except KeyError:
            raise KeyError(f"Unknown condition: {name!r}. Known: {sorted(self._defs)}")

    def __contains__(self, name: str) -> bool:
        return name in self._defs

    @property
    def all_ids(self) -> list[str]:
        return sorted(self._defs.keys())
