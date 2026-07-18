"""Randomized battle guard on the REAL engine (COMBAT V5).

`scripts/balance_sim.py` is a standalone Monte-Carlo *design* tool — it models
the v5 rules in its own fast loop and never imports the engine, so it can
explore tuning without dragging the server along. That makes it the wrong thing
to assert against: it is free to diverge from the engine on purpose.

So this guard drives `resolve_round` itself. A batch of random battles through
the real resolver must never produce negative HP, KO/HP mismatches, or over-max
healing, and must still terminate decisively with every move reachable. Taps are
uniform-random over the legal buttons (no-repeat honored) — this measures the
engine's invariants, not player skill. Kept to a small N to stay fast.
"""

from __future__ import annotations

import random
from collections import defaultdict

from server.config import load_balance, load_moves, load_zones
from server.engine.dice import Dice
from server.engine.models import (
    Character,
    ClassifiedAction,
    GameState,
    Stats,
    Team,
)
from server.engine.resolver import resolve_round

_N = 12          # each battle re-resolves up to _MAX_ROUNDS through the real engine
_MAX_ROUNDS = 30

CFG = load_balance()
MOVES = load_moves().moves
ZONES = [z.id for z in load_zones().zones]
COMBAT = list(MOVES)   # v5: all five moves are subject to the no-repeat rule

_TEAM_A = ["p0", "p1", "p2"]
_TEAM_B = ["p3", "p4", "p5"]


def _roster(rng: random.Random) -> list[Character]:
    """3v3 of random on-budget stat lines, seated on their team's backline."""
    chars = []
    for i in range(6):
        while True:
            power = rng.randint(CFG.stat_min, CFG.stat_max)
            speed = rng.randint(CFG.stat_min, CFG.stat_max)
            weird = CFG.stat_budget - power - speed
            if CFG.stat_min <= weird <= CFG.stat_max:
                break
        max_hp = (CFG.hp_base + CFG.hp_per_power * power + CFG.hp_per_weird * weird
                  + speed // CFG.hp_speed_divisor)
        chars.append(Character(
            player_id=f"p{i}",
            name=f"Fighter {i}",
            stats=Stats(power=power, speed=speed, weird=weird),
            hp=max_hp,
            max_hp=max_hp,
            zone_id=ZONES[0] if i < 3 else ZONES[-1],
        ))
    return chars


def _legal_moves(ch: Character) -> list[str]:
    """Honor the no-repeat rule, exactly like the phone does. SMASH/PROTECT can
    still fizzle if their preconditions aren't met — the resolver handles that
    gracefully, so we don't pre-filter them here."""
    return [mid for mid in MOVES if mid != ch.last_move_id]


def _battle(rng: random.Random, dice: Dice, report: dict) -> None:
    state = GameState(
        room_id="SIM",
        characters={c.player_id: c for c in _roster(rng)},
        teams=[
            Team(id="team_a", name="A", color="pink", player_ids=list(_TEAM_A)),
            Team(id="team_b", name="B", color="blue", player_ids=list(_TEAM_B)),
        ],
    )
    for rnd in range(1, _MAX_ROUNDS + 1):
        state.round = rnd
        living = [c for c in state.characters.values() if not c.is_ko]
        actions = []
        for ch in living:
            enemies = [
                c.player_id for c in living
                if (c.player_id in _TEAM_A) != (ch.player_id in _TEAM_A)
            ]
            move_id = rng.choice(_legal_moves(ch))
            actions.append(ClassifiedAction(
                player_id=ch.player_id,
                move_id=move_id,
                target_id=rng.choice(enemies) if enemies else None,
                escape_direction=rng.choice([-1, 1]),
                creativity_tier=rng.randint(0, 3),
            ))
            report["uses"][move_id] += 1

        state = resolve_round(state, actions, dice, CFG).new_state

        for ch in state.characters.values():
            if ch.hp < 0:
                report["neg_hp"] += 1
            if ch.is_ko and ch.hp != 0:
                report["ko_mismatch"] += 1
            if ch.hp > ch.max_hp:
                report["over_max_hp"] += 1

        if state.winner_team_id:
            report["rounds"].append(rnd)
            return
    report["draws"] += 1
    report["rounds"].append(_MAX_ROUNDS)


def test_random_battles_hold_engine_invariants():
    """A batch of random battles through the REAL resolver must:
    - never violate the core engine invariants (any non-zero = a resolver bug),
    - terminate decisively (random play rarely draws), and
    - leave every combat move reachable under the no-repeat rule."""
    rng = random.Random(7)
    report: dict = {
        "uses": defaultdict(int), "rounds": [],
        "neg_hp": 0, "ko_mismatch": 0, "over_max_hp": 0, "draws": 0,
    }
    for i in range(_N):
        _battle(rng, Dice(seed=1000 + i), report)

    assert report["neg_hp"] == 0, "resolver produced negative HP"
    assert report["ko_mismatch"] == 0, "KO'd character left with hp != 0"
    assert report["over_max_hp"] == 0, "a character was healed above max HP"

    assert report["draws"] <= 2, f"too many draws in {_N} battles: {report['draws']}"
    avg_rounds = sum(report["rounds"]) / len(report["rounds"])
    assert 2 <= avg_rounds <= _MAX_ROUNDS, f"implausible game length: {avg_rounds}"

    for move_id in COMBAT:
        assert report["uses"][move_id] > 0, f"{move_id} never came up in {_N} battles"


def test_engine_demo_runs(capsys):
    """`python -m server.engine.demo` is a documented command (CLAUDE.md) with a
    hand-written event printer — it reads the resolver's event data directly, so
    it rots silently whenever the schema moves. Smoke-run it so the next change
    can't."""
    from server.engine.demo import main

    main()
    out = capsys.readouterr().out
    assert "COMBAT V5" in out
    assert "every move lands" in out
    # The §12 fixture's opening lineup, straight from the v5 HP formula.
    assert "HP=34/34" in out and "HP=41/41" in out
    # Older vocabulary must never reappear in the play-by-play.
    for gone in ("2d6", "vs AC", "FUMBLE", "MISS", "CRIT", "DODGE", "BACKFIRE"):
        assert gone not in out, f"demo still prints a removed concept {gone!r}"
