"""Seeded RNG wrapper + the COMBAT V2 damage-formula evaluator.

Usage:
    rng = Dice(seed=42)
    atk  = rng.two_d6()                     # 2–12 (the v2 attack roll)
    dmg  = rng.roll("2d6")                  # plain dice specs
    dmg2 = rng.roll_formula("(1 + ceil(POW/2))d4 + 2", stats)   # catalog formulas

Formulas come straight from config/moves.yaml and may reference the acting
character's POW / SPD / WRD plus ceil(x/y) / floor(x/y) and integer arithmetic —
e.g. "(1 + ceil(POW/2))d4 + 2", "1d6 + WRD", "2d8 + floor(WRD/2)".
`describe_formula` renders the same formula as the live math shown on the
phone's move buttons ("4d4+2" on the brick's phone).
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
# (POW/SPD/WRD) and functions (ceil/floor) contain no lowercase d-digit pair,
# so the first match splits "<count-expr> d<sides> <+/- mod-expr>" reliably.
_DIE_RE = re.compile(r"d(\d+)")

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Call,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.USub, ast.UAdd,
    ast.Load,
)
_ALLOWED_FUNCS = {"ceil": math.ceil, "floor": math.floor}


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
            return _ALLOWED_FUNCS[node.func.id](ev(node.args[0]))
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

    def two_d6(self) -> int:
        """The COMBAT V2 attack roll: 2d6, a bell curve where every +1 counts."""
        return self._rng.randint(1, 6) + self._rng.randint(1, 6)

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
