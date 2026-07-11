"""Balance simulator that drives the REAL engine (server.engine.resolver).

COMBAT V2 Monte-Carlo harness. Every round is resolved by the actual
``resolve_round`` pipeline against the shipping configs, so the numbers
reflect the real game — any divergence from expectation is a real bug.

Jobs:
  1. Per-move win attribution + per-move ablation (all six combat moves
     should sit within a tight band — no move dominant or dead weight).
  2. A stat-budget experiment: a +2 budget edge should win ~77% of games —
     stats matter (GAME_DESIGN §4.1).
  3. An INVARIANT / ISSUE REPORT: negative HP, KO bookkeeping, HP>max.

Policy: taps are uniform-random over the legal buttons (no-repeat rule
honored, edge-illegal movement excluded), targets random over living enemies.
This measures intrinsic move power, not player skill.

Run:
    python tests/sim/engine_sim.py                 # default N
    python tests/sim/engine_sim.py 3000 400        # N_attr N_abl
"""

from __future__ import annotations

import random
import sys
from collections import defaultdict
from pathlib import Path

# Allow running as a plain script (python tests/sim/engine_sim.py) as well as -m.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.config import load_balance  # noqa: E402
from server.engine.dice import Dice  # noqa: E402
from server.engine.models import (  # noqa: E402
    Character,
    ClassifiedAction,
    GameState,
    Stats,
    Team,
)
from server.engine.moves import MoveRegistry  # noqa: E402
from server.engine.resolver import resolve_round  # noqa: E402
from server.engine.zones import ZoneRegistry  # noqa: E402

CFG = load_balance()
MOVE_REG = MoveRegistry()
ZONE_REG = ZoneRegistry()
COMBAT = MOVE_REG.combat_ids           # the six no-repeat combat moves
MOVEMENT = [m for m in MOVE_REG.ordered_ids if MOVE_REG.get(m).is_movement]
CATALOG = COMBAT + MOVEMENT

TEAM_IDS = (["a0", "a1", "a2"], ["b0", "b1", "b2"])
ALL_IDS = TEAM_IDS[0] + TEAM_IDS[1]
TEAM_OF = {pid: (0 if pid in TEAM_IDS[0] else 1) for pid in ALL_IDS}
START_ZONE = {0: "glitter_back", 1: "thunder_back"}

CREATIVITY_WEIGHTS = [0.40, 0.35, 0.20, 0.05]
MAX_ROUNDS = 40


# ---------------------------------------------------------------------------
# Character construction
# ---------------------------------------------------------------------------
def _budget_stats(prng: random.Random, budget: int) -> tuple[int, int, int]:
    """Random distribution of `budget` points, each stat in [stat_min, stat_max]."""
    lo, hi = CFG.stat_min, CFG.stat_max
    while True:
        p, s = prng.randint(lo, hi), prng.randint(lo, hi)
        w = budget - p - s
        if lo <= w <= hi:
            return p, s, w


def _make_char(pid: str, prng: random.Random, budget: int) -> Character:
    p, s, w = _budget_stats(prng, budget)
    hp = CFG.hp_base + CFG.hp_per_power * p
    return Character(
        player_id=pid,
        name=pid,
        stats=Stats(power=p, speed=s, weird=w),
        hp=hp,
        max_hp=hp,
        ac=CFG.ac_base + s,
        zone_id=START_ZONE[TEAM_OF[pid]],
    )


# ---------------------------------------------------------------------------
# Policy: pick a random legal tap for one character
# ---------------------------------------------------------------------------
def _choose_action(
    pid: str,
    chars: dict[str, Character],
    catalog: list[str],
    prng: random.Random,
) -> ClassifiedAction:
    me = chars[pid]
    legal = [m for m in catalog if m != me.last_move_id]      # no-repeat rule
    # Edge-illegal movement is disabled on the phone.
    legal = [m for m in legal
             if not MOVE_REG.get(m).is_movement
             or ZONE_REG.step(me.zone_id, MOVE_REG.get(m).move) is not None]
    mv = prng.choice(legal)
    spec = MOVE_REG.get(mv)

    living = {p: c for p, c in chars.items() if not c.is_ko}
    team_ids = TEAM_IDS[TEAM_OF[pid]]
    allies = [p for p in team_ids if p in living and p != pid]
    enemies = sorted(p for p in living if p not in team_ids)

    target: str | None = None
    if spec.target == "ally_or_self":
        target = prng.choice(allies + [pid])
    elif spec.target in ("single_enemy", "zone_all") and enemies:
        target = prng.choice(enemies)

    tier = prng.choices(range(4), weights=CREATIVITY_WEIGHTS)[0]
    return ClassifiedAction(
        player_id=pid,
        move_id=mv,
        target_id=target,
        creativity_tier=tier,
        trick_condition=prng.choice(["burning", "sticky", "frightened"])
        if spec.on_hit_condition == "from_drawing" else None,
    )


# ---------------------------------------------------------------------------
# Per-battle stats + invariant checks
# ---------------------------------------------------------------------------
class BattleStats:
    def __init__(self) -> None:
        self.uses: list[tuple[int, str]] = []          # (team_idx, move)
        self.impact: dict[str, float] = defaultdict(float)
        self.use_count: dict[str, int] = defaultdict(int)
        self.fumbles: dict[str, int] = defaultdict(int)
        # issue counters
        self.negative_hp = 0
        self.ko_hp_mismatch = 0
        self.over_max_hp = 0


def _run_battle(seed: int, cat_a: list[str], cat_b: list[str], bs: BattleStats,
                budgets: tuple[int, int] | None = None):
    prng = random.Random(seed)
    dice = Dice(seed=(seed * 2654435761) & 0xFFFFFFFF)
    budget_a, budget_b = budgets or (CFG.stat_budget, CFG.stat_budget)

    chars = {pid: _make_char(pid, prng, budget_a if TEAM_OF[pid] == 0 else budget_b)
             for pid in ALL_IDS}
    teams = [
        Team(id="team_a", name="A", color="pink", player_ids=list(TEAM_IDS[0])),
        Team(id="team_b", name="B", color="blue", player_ids=list(TEAM_IDS[1])),
    ]
    state = GameState(room_id="SIM", characters=chars, teams=teams, round=0)

    winner_idx: int | None = None
    rounds = MAX_ROUNDS
    for rnd in range(1, MAX_ROUNDS + 1):
        living_ids = [p for p, c in state.characters.items() if not c.is_ko]
        actions = []
        move_of: dict[str, str] = {}
        for pid in living_ids:
            cat = cat_a if TEAM_OF[pid] == 0 else cat_b
            act = _choose_action(pid, state.characters, cat, prng)
            move_of[pid] = act.move_id
            actions.append(act)
            bs.uses.append((TEAM_OF[pid], act.move_id))
            bs.use_count[act.move_id] += 1

        state = state.model_copy(update={"round": rnd})
        result = resolve_round(state, actions, dice, CFG)
        _scan_events(result.events, move_of, bs)
        state = result.new_state

        # HP / KO invariants
        for pid, c in state.characters.items():
            if c.hp < 0:
                bs.negative_hp += 1
            if c.is_ko and c.hp != 0:
                bs.ko_hp_mismatch += 1
            if c.hp > c.max_hp:
                bs.over_max_hp += 1

        if state.winner_team_id:
            winner_idx = 0 if state.winner_team_id == "team_a" else 1
            rounds = rnd
            break

    return winner_idx, rounds


def _scan_events(events, move_of: dict[str, str], bs: BattleStats) -> None:
    for ev in events:
        t = ev.type.value
        pid = ev.player_id
        d = ev.data
        mv = d.get("move_id") or (move_of.get(pid) if pid else None) or "?"
        if t == "attack_resolved":
            res = d.get("result")
            if res in ("hit", "crit"):
                bs.impact[mv] += d.get("damage", 0)
            elif res == "fumble":
                bs.fumbles[mv] += 1
        elif t == "healed":
            bs.impact[move_of.get(pid, "rally") if pid else "rally"] += d.get("amount", 0)


# ---------------------------------------------------------------------------
# Analyses
# ---------------------------------------------------------------------------
def run_attribution(n: int, seed: int = 7):
    agg = dict(uses=defaultdict(int), impact=defaultdict(float), fumbles=defaultdict(int))
    won = defaultdict(float)
    cnt = defaultdict(int)
    draws = 0
    total_rounds = 0
    neg_hp = ko_mismatch = over_max = 0

    for i in range(n):
        bs = BattleStats()
        w, rounds = _run_battle(seed * 1_000_003 + i, CATALOG, CATALOG, bs)
        total_rounds += rounds
        if w is None:
            draws += 1
        for team_idx, mv in bs.uses:
            cnt[mv] += 1
            if w is None:
                won[mv] += 0.5
            elif w == team_idx:
                won[mv] += 1.0
        for key in ("uses", "impact", "fumbles"):
            src = getattr(bs, "use_count" if key == "uses" else key)
            for mv, v in src.items():
                agg[key][mv] += v
        neg_hp += bs.negative_hp
        ko_mismatch += bs.ko_hp_mismatch
        over_max += bs.over_max_hp

    report = dict(
        draws=draws,
        avg_rounds=total_rounds / n,
        neg_hp=neg_hp,
        ko_mismatch=ko_mismatch,
        over_max_hp=over_max,
    )
    return agg, won, cnt, report


def run_ablation(move: str, n: int, seed: int) -> float:
    """Team A full catalog vs Team B missing `move`; Team A's win rate."""
    reduced = [m for m in CATALOG if m != move]
    wins_a = 0.0
    for i in range(n):
        bs = BattleStats()
        w, _ = _run_battle(seed * 1_000_003 + i, CATALOG, reduced, bs)
        wins_a += 0.5 if w is None else (1.0 if w == 0 else 0.0)
    return wins_a / n


def run_budget_edge(n: int, seed: int, edge: int = 2) -> float:
    """Team A plays with stat_budget + edge; Team A's win rate (~0.77 expected)."""
    wins_a = 0.0
    for i in range(n):
        bs = BattleStats()
        w, _ = _run_battle(seed * 1_000_003 + i, CATALOG, CATALOG, bs,
                           budgets=(CFG.stat_budget + edge, CFG.stat_budget))
        wins_a += 0.5 if w is None else (1.0 if w == 0 else 0.0)
    return wins_a / n


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    n_attr = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    n_abl = int(sys.argv[2]) if len(sys.argv) > 2 else 300

    print(f"Engine simulator — driving the REAL resolve_round ({len(CATALOG)} moves)")
    print(f"Battles: {n_attr} attribution, {n_abl}/move ablation\n")

    agg, won, cnt, rep = run_attribution(n_attr)

    print(f"=== Attribution ({n_attr} battles, {rep['draws']} draws, "
          f"avg {rep['avg_rounds']:.1f} rounds) ===")
    print(f"{'move':<10}{'uses':>8}{'winrate':>9}{'impact/use':>12}{'fumble%':>9}")
    rows = []
    for mv in CATALOG:
        u = cnt[mv]
        wr = won[mv] / u if u else 0.0
        uses = agg["uses"][mv]
        ipu = agg["impact"][mv] / uses if uses else 0.0
        fpct = 100 * agg["fumbles"][mv] / uses if uses else 0.0
        rows.append((mv, u, wr, ipu, fpct))
    for mv, u, wr, ipu, fpct in sorted(rows, key=lambda r: -r[2]):
        print(f"{mv:<10}{u:>8}{wr:>9.3f}{ipu:>12.2f}{fpct:>8.1f}%")

    print(f"\n=== Ablation (Team A full vs Team B missing move; {n_abl} battles; "
          f"all six combat moves should sit within ±5% of each other) ===")
    abl = [(mv, run_ablation(mv, n_abl, seed=100 + i)) for i, mv in enumerate(COMBAT)]
    for mv, wr in sorted(abl, key=lambda r: -r[1]):
        print(f"{mv:<10}{wr:>7.3f}")
    spread = max(w for _, w in abl) - min(w for _, w in abl)
    print(f"ablation spread: {spread:.3f}")

    print("\n=== Stat-budget edge (+2 budget vs baseline; expect ~0.77) ===")
    print(f"win rate: {run_budget_edge(n_abl, seed=999):.3f}")

    print("\n=== INVARIANT / ISSUE REPORT ===")
    _issue("Negative HP occurrences (engine should clamp to 0)", rep["neg_hp"])
    _issue("KO'd characters with hp != 0", rep["ko_mismatch"])
    _issue("Characters healed above max HP", rep["over_max_hp"])


def _issue(label: str, count: int, extra: str = "") -> None:
    flag = "  OK " if count == 0 else "ISSUE"
    tail = f"   ({extra})" if extra else ""
    print(f"  [{flag}] {label}: {count}{tail}")


if __name__ == "__main__":
    main()
