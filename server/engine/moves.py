"""Move catalog registry — loads moves.yaml, provides lookup API.

COMBAT V2: eight tapped moves — six combat moves (subject to the no-repeat
rule) plus ◀/▶ movement (exempt, edge-checked).
"""

from __future__ import annotations

from pathlib import Path

from server.config import MoveDef, MovesConfig, load_moves


class MoveRegistry:
    def __init__(self, cfg: MovesConfig | None = None, config_dir: Path | None = None):
        if cfg is None:
            cfg = load_moves(config_dir)
        self._moves: dict[str, MoveDef] = cfg.moves

    def get(self, catalog_id: str) -> MoveDef:
        try:
            return self._moves[catalog_id]
        except KeyError:
            raise KeyError(f"Unknown move: {catalog_id!r}. Known: {sorted(self._moves)}")

    def __contains__(self, catalog_id: str) -> bool:
        return catalog_id in self._moves

    @property
    def all_ids(self) -> list[str]:
        return sorted(self._moves.keys())

    @property
    def ordered_ids(self) -> list[str]:
        """Catalog order as authored in moves.yaml — the phone's button order."""
        return list(self._moves.keys())

    @property
    def combat_ids(self) -> list[str]:
        """Moves subject to the no-repeat rule (everything but movement)."""
        return [mid for mid, m in self._moves.items() if not m.is_movement]
