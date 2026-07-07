"""Balance simulator that drives the REAL engine (server.engine.resolver).

This is a port of ``simulation.py`` (which reimplemented the game math inline)
onto the actual ``resolve_round`` pipeline. Instead of a private copy of the
rules, every round is resolved by the real engine, so the numbers reflect the
shipping code — and any divergence between the two is a real bug.

Two jobs:
  1. Monte-Carlo balance analysis (per-move win attribution + ablation), same
     as the original harness.
  2. An INVARIANT / ISSUE REPORT that watches the real engine for anomalies the
     inline sim could never expose: negative HP, KO bookkeeping, friendly fire,
     self-targeting, and AC inflation.

Policy: uniform-random move selection with valid targets supplied by the
harness (the engine itself is team-blind and trusts the caller's targets —
see the issue report). This measures intrinsic move power, not player skill.

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
CATALOG = MOVE_REG.all_ids

TEAM_IDS = (["a0", "a1", "a2"], ["b0", "b1", "b2"])
ALL_IDS = TEAM_IDS[0] + TEAM_IDS[1]
TEAM_OF = {pid: (0 if pid in TEAM_IDS[0] else 1) for pid in ALL_IDS}

CREATIVITY_WEIGHTS = [0.40, 0.35, 0.20, 0.05]
MAX_ROUNDS = 40
START_ZONE = "frontline"  # everyone starts adjacent so melee actually connects


# ---------------------------------------------------------------------------
# Character construction
# ---------------------------------------------------------------------------
def _make_char(pid: str, prng: random.Random) -> Character:
    """Budget-8 stats (each 1..4); HP/AC from balance.yaml formulas."""
    while True:
        p, s = prng.randint(1, 4), prng.randint(1, 4)
        w = 8 - p - s
        if 1 <= w <= 4:
            break
    hp = CFG.hp_base + CFG.hp_per_power * p
    return Character(
        player_id=pid,
        name=pid,
        stats=Stats(power=p, speed=s, weird=w),
        hp=hp,
        max_hp=hp,
        ac=CFG.ac_base + s,
        zone_id=START_ZONE,
    )


# ---------------------------------------------------------------------------
# Policy: pick a random move and legal targets for one character
# ---------------------------------------------------------------------------
def _choose_action(
    pid: str,
    chars: dict[str, Character],
    catalog: list[str],
    last_move: str | None,
    prng: random.Random,
) -> ClassifiedAction:
    mv = prng.choice(catalog)
    spec = MOVE_REG.get(mv)

    if spec.fixed_cost is not None:
        cost = spec.fixed_cost
    else:
        cost = prng.randint(max(1, spec.min_cost), 3)

    tier = prng.choices(range(4), weights=CREATIVITY_WEIGHTS)[0]

    team_ids = TEAM_IDS[TEAM_OF[pid]]
    living = {p: c for p, c in chars.items() if not c.is_ko}
    allies = [p for p in team_ids if p in living and p != pid]
    enemies = [p for p in living if p not in team_ids]
    me = chars[pid]

    targets: list[str] = []
    move_to: str | None = None
    tt = spec.target

    if tt == "ally":
        if allies:
            targets = [prng.choice(allies)]
    elif tt == "ally_or_self":
        targets = [prng.choice(allies + [pid])]
    elif tt == "single_enemy":
        if enemies:
            if spec.range == "same_zone":
                same = [e for e in enemies if chars[e].zone_id == me.zone_id]
                targets = [prng.choice(same or enemies)]
            else:
                targets = [prng.choice(enemies)]
    elif tt in ("zone_all", "line_all_zones"):
        if enemies:
            targets = [prng.choice(enemies)]  # marks the zone/line for the engine
    # "self" and "zone" take no character targets

    if spec.move_zones_per_cost > 0:  # `move`
        others = [z for z in ZONE_REG.all_ids if z != me.zone_id]
        if others:
            move_to = prng.choice(others)
    if spec.includes_move and targets:  # `charge` closes to the target's zone
        move_to = chars[targets[0]].zone_id

    return ClassifiedAction(
        player_id=pid,
        catalog_id=mv,
        action_cost=cost,
        targets=targets,
        move_to=move_to,
        creativity_tier=tier,
        similar_to_previous=(mv == last_move),
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
        self.friendly_fire: dict[str, int] = defaultdict(int)   # dmg move hit an ally
        self.friendly_dmg: float = 0.0
        self.self_target: dict[str, int] = defaultdict(int)     # move targeted its own caster
        self.negative_hp = 0
        self.ko_hp_mismatch = 0
        self.stat_oob = 0        # a stat left the [stat_min, stat_max] band
        self.transform_leak = 0  # original stats never restored after transform


def _run_battle(seed: int, cat_a: list[str], cat_b: list[str], bs: BattleStats):
    prng = random.Random(seed)
    dice = Dice(seed=(seed * 2654435761) & 0xFFFFFFFF)

    chars = {pid: _make_char(pid, prng) for pid in ALL_IDS}
    start_ac = {pid: c.ac for pid, c in chars.items()}
    teams = [
        Team(id="team_a", name="A", color="pink", player_ids=list(TEAM_IDS[0])),
        Team(id="team_b", name="B", color="blue", player_ids=list(TEAM_IDS[1])),
    ]
    state = GameState(room_id="SIM", characters=chars, teams=teams, round=0)
    last_move: dict[str, str | None] = {pid: None for pid in ALL_IDS}

    winner_idx: int | None = None
    rounds = MAX_ROUNDS
    for rnd in range(1, MAX_ROUNDS + 1):
        living_ids = [p for p, c in state.characters.items() if not c.is_ko]
        actions = []
        move_of: dict[str, str] = {}
        for pid in living_ids:
            cat = cat_a if TEAM_OF[pid] == 0 else cat_b
            act = _choose_action(pid, state.characters, cat, last_move[pid], prng)
            last_move[pid] = act.catalog_id
            move_of[pid] = act.catalog_id
            actions.append(act)
            bs.uses.append((TEAM_OF[pid], act.catalog_id))
            bs.use_count[act.catalog_id] += 1

        state = state.model_copy(update={"round": rnd})
        result = resolve_round(state, actions, dice, CFG)
        _scan_events(result.events, move_of, bs)
        state = result.new_state

        # HP / KO / stat invariants
        for pid, c in state.characters.items():
            if c.hp < 0:
                bs.negative_hp += 1
            if c.is_ko and c.hp != 0:
                bs.ko_hp_mismatch += 1
            for v in (c.stats.power, c.stats.speed, c.stats.weird):
                if not (CFG.stat_min <= v <= CFG.stat_max):
                    bs.stat_oob += 1

        if state.winner_team_id:
            winner_idx = 0 if state.winner_team_id == "team_a" else 1
            rounds = rnd
            break

    # AC inflation: any char whose AC crept above its starting value?
    ac_inflated = sum(
        1 for pid, c in state.characters.items() if c.ac > start_ac[pid]
    )
    max_ac_delta = max(
        (c.ac - start_ac[pid] for pid, c in state.characters.items()), default=0
    )
    # Transform leak: saved original stats that were never restored — the
    # character carries pre_transform_stats without an active `transformed` mark.
    bs.transform_leak += sum(
        1 for c in state.characters.values()
        if c.pre_transform_stats is not None and "transformed" not in c.conditions
    )
    return winner_idx, rounds, ac_inflated, max_ac_delta


def _scan_events(events, move_of: dict[str, str], bs: BattleStats) -> None:
    for ev in events:
        t = ev.type.value
        pid = ev.player_id
        d = ev.data
        mv = d.get("catalog_id") or (move_of.get(pid) if pid else None) or "?"
        if t == "attack_resolved":
            res = d.get("result")
            if res in ("hit", "crit"):
                bs.impact[mv] += d.get("damage", 0)
            elif res == "fumble":
                bs.fumbles[mv] += 1
            # friendly-fire / self-target detection (any resolved target)
            tid = ev.target_id
            if tid and pid and tid in TEAM_OF and pid in TEAM_OF:
                if tid == pid:
                    bs.self_target[mv] += 1
                    if res in ("hit", "crit"):
                        bs.friendly_dmg += d.get("damage", 0)
                elif TEAM_OF[tid] == TEAM_OF[pid]:
                    bs.friendly_fire[mv] += 1
                    if res in ("hit", "crit"):
                        bs.friendly_dmg += d.get("damage", 0)
        elif t == "healed":
            bs.impact[move_of.get(pid, "heal") if pid else "heal"] += d.get("amount", 0)


# ---------------------------------------------------------------------------
# Analyses
# ---------------------------------------------------------------------------
def run_attribution(n: int, seed: int = 7):
    agg = dict(
        uses=defaultdict(int),
        impact=defaultdict(float),
        fumbles=defaultdict(int),
        friendly_fire=defaultdict(int),
        self_target=defaultdict(int),
    )
    won = defaultdict(float)
    cnt = defaultdict(int)
    draws = 0
    total_rounds = 0
    neg_hp = ko_mismatch = ff_dmg = 0
    ac_inflated_battles = 0
    max_ac_delta = 0
    stat_oob = transform_leak = 0

    for i in range(n):
        bs = BattleStats()
        w, rounds, ac_infl, ac_delta = _run_battle(seed * 1_000_003 + i, CATALOG, CATALOG, bs)
        total_rounds += rounds
        if w is None:
            draws += 1
        # win attribution
        for team_idx, mv in bs.uses:
            cnt[mv] += 1
            if w is None:
                won[mv] += 0.5
            elif w == team_idx:
                won[mv] += 1.0
        for mv, v in bs.use_count.items():
            agg["uses"][mv] += v
        for mv, v in bs.impact.items():
            agg["impact"][mv] += v
        for mv, v in bs.fumbles.items():
            agg["fumbles"][mv] += v
        for mv, v in bs.friendly_fire.items():
            agg["friendly_fire"][mv] += v
        for mv, v in bs.self_target.items():
            agg["self_target"][mv] += v
        neg_hp += bs.negative_hp
        ko_mismatch += bs.ko_hp_mismatch
        ff_dmg += bs.friendly_dmg
        stat_oob += bs.stat_oob
        transform_leak += bs.transform_leak
        if ac_infl:
            ac_inflated_battles += 1
        max_ac_delta = max(max_ac_delta, ac_delta)

    report = dict(
        draws=draws,
        avg_rounds=total_rounds / n,
        neg_hp=neg_hp,
        ko_mismatch=ko_mismatch,
        ff_dmg=ff_dmg,
        ac_inflated_battles=ac_inflated_battles,
        max_ac_delta=max_ac_delta,
        stat_oob=stat_oob,
        transform_leak=transform_leak,
    )
    return agg, won, cnt, report


def run_ablation(move: str, n: int, seed: int) -> float:
    reduced = [m for m in CATALOG if m != move]
    wins_a = 0.0
    for i in range(n):
        bs = BattleStats()
        w, *_ = _run_battle(seed * 1_000_003 + i, CATALOG, reduced, bs)
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
    print(f"{'move':<12}{'uses':>7}{'winrate':>9}{'impact/use':>12}{'fumble%':>9}"
          f"{'FF':>7}{'self':>6}")
    rows = []
    for mv in CATALOG:
        u = cnt[mv]
        wr = won[mv] / u if u else 0.0
        uses = agg["uses"][mv]
        ipu = agg["impact"][mv] / uses if uses else 0.0
        fpct = 100 * agg["fumbles"][mv] / uses if uses else 0.0
        rows.append((mv, u, wr, ipu, fpct, agg["friendly_fire"][mv], agg["self_target"][mv]))
    for mv, u, wr, ipu, fpct, ff, st in sorted(rows, key=lambda r: -r[2]):
        print(f"{mv:<12}{u:>7}{wr:>9.3f}{ipu:>12.2f}{fpct:>8.1f}%{ff:>7}{st:>6}")

    print(f"\n=== Ablation (Team A full vs Team B missing move; {n_abl} battles; "
          f">0.5 = valuable) ===")
    abl = [(mv, run_ablation(mv, n_abl, seed=100 + i)) for i, mv in enumerate(CATALOG)]
    for mv, wr in sorted(abl, key=lambda r: -r[1]):
        print(f"{mv:<12}{wr:>7.3f}")

    print("\n=== INVARIANT / ISSUE REPORT ===")
    _issue("Negative HP occurrences (engine should clamp to 0)", rep["neg_hp"])
    _issue("KO'd characters with hp != 0", rep["ko_mismatch"])
    total_ff = sum(agg["friendly_fire"].values())
    total_self = sum(agg["self_target"].values())
    _issue("Attacks that resolved against an ALLY (friendly fire)", total_ff)
    _issue("Attacks that resolved against the CASTER (self-target)", total_self)
    _issue("Total HP damage dealt to allies/self", int(rep["ff_dmg"]))
    _issue("Battles ending with AC inflated above start (defend stacks)",
           rep["ac_inflated_battles"], extra=f"max +{rep['max_ac_delta']} AC")
    _issue("Stat values outside [stat_min, stat_max]", rep["stat_oob"])
    _issue("Transforms whose original stats were never restored", rep["transform_leak"])
    if total_ff or total_self:
        offenders = sorted(
            ((mv, agg["friendly_fire"][mv] + agg["self_target"][mv]) for mv in CATALOG),
            key=lambda r: -r[1],
        )
        print("  friendly/self-target by move: "
              + ", ".join(f"{mv}={n}" for mv, n in offenders if n) )


def _issue(label: str, count: int, extra: str = "") -> None:
    flag = "  OK " if count == 0 else "ISSUE"
    tail = f"   ({extra})" if extra else ""
    print(f"  [{flag}] {label}: {count}{tail}")


if __name__ == "__main__":
    main()
