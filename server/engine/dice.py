"""Seeded RNG wrapper + the COMBAT V4 damage-formula evaluator.

Usage:
    rng = Dice(seed=42)
    dmg  = rng.roll("2d6")                  # plain dice specs
    dmg2 = rng.roll_formula("2d4 + max(SPD,WRD)", stats)   # catalog formulas
    hit  = not rng.chance(0.15)             # seeded probability check

COMBAT V4 has no attack roll, so there is no `two_d6`: the only probability
checks left are dodge, SHIELD's reflect, and WILD CARD's backfire — all of
which go through `chance()`.

Formulas come straight from config/moves.yaml and may reference the acting
character's POW / SPD / WRD plus ceil(x/y) / floor(x/y) / max(a,b) / min(a,b)
and integer arithmetic — e.g. "2d4 + POW + 2", "2d4 + max(SPD,WRD)",
"2d6 + 2*WRD + 2". `describe_formula` renders the same formula as the live math
shown on the phone's move buttons ("2d4+8" on the brick's phone).
"""

from __future__ import annotations

import ast
import math
import random
import re
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")

# The die token: a lowercase 'd' followed by the number of sides. Stat names
# (POW/SPD/WRD) and functions (ceil/floor/max/min) contain no lowercase d-digit
# pair, so the first match splits "<count-expr> d<sides> <+/- mod-expr>" reliably.
_DIE_RE = re.compile(r"d(\d+)")

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Call,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.USub, ast.UAdd,
    ast.Load,
)
_ALLOWED_FUNCS = {"ceil": math.ceil, "floor": math.floor, "max": max, "min": min}


def _eval_expr(expr: str, stats: dict[str, int]) -> int:
    """Safely evaluate an integer arithmetic expression over POW/SPD/WRD."""
    expr = expr.strip()
    if not expr:
        return 0
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"Disallowed element {type(node).__name__!r} in {expr!r}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
                raise ValueError(f"Disallowed function call in {expr!r}")
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_FUNCS and node.id not in stats:
            raise ValueError(f"Unknown name {node.id!r} in {expr!r}")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return stats[node.id]
        if isinstance(node, ast.Call):
            return _ALLOWED_FUNCS[node.func.id](*[ev(a) for a in node.args])
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else +v
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, (ast.Div, ast.FloorDiv)):
                return a / b   # ceil()/floor() around it produce the int
        raise ValueError(f"Unhandled node {node!r}")

    return int(ev(tree))


def _parse_formula(spec: str, stats: dict[str, int]) -> tuple[int, int, int]:
    """Resolve a formula for one character → (dice_count, sides, flat_mod)."""
    spec = spec.strip()
    m = _DIE_RE.search(spec)
    if m is None:
        return 0, 0, _eval_expr(spec, stats)   # flat formula, no dice term
    count_expr = spec[: m.start()].strip() or "1"
    sides = int(m.group(1))
    tail = spec[m.end():].strip()              # "+ 2", "+ WRD", "- 1", or ""
    count = _eval_expr(count_expr, stats)
    mod = _eval_expr(tail, stats) if tail else 0
    if count < 0 or sides < 1:
        raise ValueError(f"Formula {spec!r} resolved to invalid dice {count}d{sides}")
    return count, sides, mod


def formula_parts(spec: str, stats: dict[str, int]) -> tuple[int, int, int]:
    """Resolve a formula for one character → (dice_count, sides, flat_mod).

    Public because the host readout (GAME_DESIGN §13) has to split a rolled
    result back into "🎲 3 + ⚡ Speed 5 + …": subtracting flat_mod from the
    rolled total recovers the dice portion, and re-resolving with the move's
    stat zeroed separates the stat term from the move's own constant.
    """
    return _parse_formula(spec, stats)


def describe_formula(spec: str, stats: dict[str, int]) -> str:
    """Render a formula as one character's live math, e.g. '4d4+2' — the label
    shown on the phone's move buttons."""
    count, sides, mod = _parse_formula(spec, stats)
    if count == 0:
        return str(mod)
    out = f"{count}d{sides}"
    if mod:
        out += f"+{mod}" if mod > 0 else str(mod)
    return out


class Dice:
    def __init__(self, seed: int):
        self._rng = random.Random(seed)
        self._seed = seed

    @property
    def seed(self) -> int:
        return self._seed

    def chance(self, p: float) -> bool:
        """A seeded probability check — True with probability `p`.

        COMBAT V4's only random gates: dodge (5%×Speed), SHIELD's reflect
        (10%×POW), and WILD CARD's backfire (15%). p<=0 never fires and p>=1
        always does, both WITHOUT consuming a draw, so a Speed-0 character's
        dodge check can't shift the dice stream for everyone behind them.
        """
        if p <= 0:
            return False
        if p >= 1:
            return True
        return self._rng.random() < p

    def roll(self, spec: str) -> int:
        """Roll a plain dice spec: 'd8', '2d6', 'd4', 'none' → 0."""
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

    def roll_formula(self, spec: str, stats: dict[str, int]) -> int:
        """Roll a catalog formula for one character (see module docstring)."""
        count, sides, mod = _parse_formula(spec, stats)
        return sum(self._rng.randint(1, sides) for _ in range(count)) + mod

    def randint(self, a: int, b: int) -> int:
        return self._rng.randint(a, b)

    def choice(self, seq: Sequence[T]) -> T:
        return self._rng.choice(seq)

    def shuffle(self, lst: list) -> None:
        self._rng.shuffle(lst)
