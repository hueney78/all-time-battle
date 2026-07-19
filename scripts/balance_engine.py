"""Balance report driven through the REAL engine (server.engine.resolver).

Companion to `scripts/balance_sim.py`. That one is a fast standalone *model* of
the v5 rules — it never imports the engine, so it can explore tuning freely and
is free to diverge from the shipped game on purpose. This one asks the same
questions of the actual `resolve_round` pipeline and the actual `config/*.yaml`,
so its numbers are the game people will really play. When the two disagree, this
one is the game and that one is the hypothesis.

(IMPLEMENTATION_PLAN.md §7 shared backlog: "port balance_sim to run against the
real engine/configs".)

Reports:
  1. Specialist round-robin — three clones of each archetype vs three of another.
     Answers "is any stat a god stat / a dump stat?" (GAME_DESIGN §3's claim).
  2. Per-move ablation — team A has the whole catalog, team B is missing one
     move. >0.5 means having that move is worth something; <0.5 means the move
     is a TRAP and picking it actively costs you the game.
  3. Invariant report — negative HP, KO bookkeeping, over-max healing. Any
     non-zero is a resolver bug.

Policy: taps are uniform-random over the legal buttons (no-repeat honored),
targets random over living enemies, ESCAPE direction random. This measures
intrinsic power, not player skill.

Run:
    python scripts/balance_engine.py            # default N
    python scripts/balance_engine.py 400        # N battles per cell
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.config import load_balance, load_moves, load_zones  # noqa: E402
from server.engine.dice import Dice  # noqa: E402
from server.engine.models import (  # noqa: E402
    Character,
    ClassifiedAction,
    GameState,
    Stats,
    Team,
)
from server.engine.resolver import resolve_round  # noqa: E402

CFG = load_balance()
MOVES = load_moves().moves
ZONES = [z.id for z in load_zones().zones]
CATALOG = list(MOVES)         # v5: all five moves are subject to the no-repeat rule
COMBAT = CATALOG

# Creativity tier weights — roughly how a stingy judge scores a table (§8).
CRE_WEIGHTS = [0.35, 0.35, 0.22, 0.08]
MAX_ROUNDS = 30

TEAM_A = ["p0", "p1", "p2"]
TEAM_B = ["p3", "p4", "p5"]

ARCHETYPES = {
    "Power(6/2/1)": (6, 2, 1),
    "Speed(1/6/2)": (1, 6, 2),
    "Weird(2/1/6)": (2, 1, 6),
    "Balanced(3/3/3)": (3, 3, 3),
}


def _char(pid: str, stats: tuple[int, int, int], zone: str) -> Character:
    power, speed, weird = stats
    hp = (CFG.hp_base + CFG.hp_per_power * power + CFG.hp_per_weird * weird
          + CFG.hp_per_speed * speed)
    return Character(player_id=pid, name=pid,
                     stats=Stats(power=power, speed=speed, weird=weird),
                     hp=hp, max_hp=hp, zone_id=zone)


def _legal(ch: Character, catalog: list[str], living: list[Character]) -> list[str]:
    """The legal button set the phone would show (§4.1): no-repeat, SMASH needs a
    same-zone enemy, PROTECT needs a living ally. BLAST/CHARGE/ESCAPE always
    legal. Falls back to BLAST so a fighter always has something. Matches the
    grey-out rules in server/state_machine.validate_tap and the standalone sim."""
    in_a = ch.player_id in TEAM_A
    enemies = [c for c in living if (c.player_id in TEAM_A) != in_a]
    allies = [c for c in living if (c.player_id in TEAM_A) == in_a and c is not ch]
    out = []
    for mid in catalog:
        if mid == ch.last_move_id:
            continue
        if mid == "smash" and not any(e.zone_id == ch.zone_id for e in enemies):
            continue
        if mid == "protect" and not allies:
            continue
        out.append(mid)
    return out or ["blast"]


def battle(rng, dice, stats_a, stats_b, catalog_a=None, catalog_b=None, report=None):
    """One 3v3 to the death. Returns 0 (team A), 1 (team B), or None (draw)."""
    catalog_a = catalog_a or CATALOG
    catalog_b = catalog_b or CATALOG
    chars = {pid: _char(pid, stats_a, ZONES[0]) for pid in TEAM_A}
    chars |= {pid: _char(pid, stats_b, ZONES[-1]) for pid in TEAM_B}
    state = GameState(room_id="BAL", characters=chars, teams=[
        Team(id="team_a", name="A", color="pink", player_ids=list(TEAM_A)),
        Team(id="team_b", name="B", color="blue", player_ids=list(TEAM_B))])

    for rnd in range(1, MAX_ROUNDS + 1):
        state.round = rnd
        living = [c for c in state.characters.values() if not c.is_ko]
        actions = []
        for ch in living:
            in_a = ch.player_id in TEAM_A
            foes = [c.player_id for c in living if (c.player_id in TEAM_A) != in_a]
            if not foes:
                break
            move_id = rng.choice(_legal(ch, catalog_a if in_a else catalog_b, living))
            actions.append(ClassifiedAction(
                player_id=ch.player_id, move_id=move_id,
                target_id=rng.choice(foes),
                escape_direction=rng.choice([-1, 1]),
                creativity_tier=rng.choices(range(4), CRE_WEIGHTS)[0]))
            if report is not None:
                report["uses"][move_id] = report["uses"].get(move_id, 0) + 1

        state = resolve_round(state, actions, dice, CFG).new_state

        if report is not None:
            for ch in state.characters.values():
                if ch.hp < 0:
                    report["neg_hp"] += 1
                if ch.is_ko and ch.hp != 0:
                    report["ko_mismatch"] += 1
                if ch.hp > ch.max_hp:
                    report["over_max_hp"] += 1

        if state.winner_team_id:
            return 0 if state.winner_team_id == "team_a" else 1
    return None


def _winrate(n, rng, dice, stats_a, stats_b, cat_a=None, cat_b=None, report=None):
    total = 0.0
    for _ in range(n):
        r = battle(rng, dice, stats_a, stats_b, cat_a, cat_b, report)
        total += 0.5 if r is None else (1.0 if r == 0 else 0.0)
    return total / n


def round_robin(n: int) -> None:
    print(f"\nSpecialist round-robin through the REAL engine (row win% vs col), "
          f"n={n}/cell")
    print("            " + "".join(f"{k[:9]:>11}" for k in ARCHETYPES))
    for row_name, row_stats in ARCHETYPES.items():
        line = f"{row_name:<12}"
        for col_name, col_stats in ARCHETYPES.items():
            if row_name == col_name:
                line += f"{'—':>11}"
                continue
            rng = random.Random(abs(hash((row_name, col_name))) % 99999)
            line += f"{_winrate(n, rng, Dice(seed=7), row_stats, col_stats):>11.3f}"
        print(line)


def ablation(n: int) -> None:
    print(f"\nMove ablation through the REAL engine, n={n}")
    print("  (>0.5 = worth having; <0.5 = the move is a TRAP — picking it costs you)")
    for i, move_id in enumerate(COMBAT):
        rng = random.Random(100 + i)
        reduced = [m for m in CATALOG if m != move_id]
        rate = _winrate(n, rng, Dice(seed=11), (3, 3, 3), (3, 3, 3),
                        CATALOG, reduced)
        flag = "  <-- TRAP" if rate < 0.5 else ""
        print(f"  {move_id:<8}{rate:>7.3f}{flag}")


def invariants(n: int) -> None:
    report = {"uses": {}, "neg_hp": 0, "ko_mismatch": 0, "over_max_hp": 0}
    rng = random.Random(5)
    _winrate(n, rng, Dice(seed=3), (3, 3, 3), (3, 3, 3), report=report)
    print("\nEngine invariants (any non-zero is a resolver bug):")
    for key in ("neg_hp", "ko_mismatch", "over_max_hp"):
        print(f"  {key:<14}{report[key]}")


if __name__ == "__main__":
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    round_robin(N)
    ablation(N)
    invariants(max(20, N // 10))
