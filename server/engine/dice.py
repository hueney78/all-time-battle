"""Seeded RNG wrapper — injectable for deterministic tests.

Usage:
    rng = Dice(seed=42)
    roll = rng.d20()          # 1–20
    dmg  = rng.roll("d8")     # 1–8
    dmg2 = rng.roll("2d6")    # 2–12
"""

from __future__ import annotations

import random
import re
from typing import Sequence, TypeVar

T = TypeVar("T")


class Dice:
    def __init__(self, seed: int):
        self._rng = random.Random(seed)
        self._seed = seed

    @property
    def seed(self) -> int:
        return self._seed

    def d20(self) -> int:
        return self._rng.randint(1, 20)

    def roll(self, spec: str) -> int:
        """Roll a dice spec: 'd8', '2d6', 'd4', 'none' → 0."""
        spec = spec.strip().lower()
        if spec in ("none", "0", ""):
            return 0
        m = re.fullmatch(r"(\d+)?d(\d+)", spec)
        if not m:
            raise ValueError(f"Invalid dice spec: {spec!r}")
        count = int(m.group(1) or 1)
        sides = int(m.group(2))
        if sides < 1:
            raise ValueError(f"Dice must have at least 1 side, got {sides}")
        return sum(self._rng.randint(1, sides) for _ in range(count))

    def randint(self, a: int, b: int) -> int:
        return self._rng.randint(a, b)

    def choice(self, seq: Sequence[T]) -> T:
        return self._rng.choice(seq)

    def shuffle(self, lst: list) -> None:
        self._rng.shuffle(lst)
