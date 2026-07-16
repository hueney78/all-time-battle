"""Doodle Brawl COMBAT V4 — no AC, no attack rolls, no misses/fumbles.

Every move lands. Effectiveness = base(stat) + creativity(flat 0/1/3/5).
Randomness lives in: damage magnitude dice, passive DODGE (Speed), passive
REFLECT on defend (Power), and WILD CARD backfire.

Stats 0-6, budget 9.
  Power: SMASH dmg, SHIELD mitigation+reflect, HP (28 + 2*POW + WRD)
  Speed: initiative, DODGE %
  Weird: HEAL, BLAST, SHOOT (ranged now keys off Weird only)

Balance levers under test (vs the shipped v4):
  - SHOOT ranged tied to WEIRD only (was max(Speed,Weird)) — Speed stops
    doubling as a full attack stat.
  - SHIELD applied at the START of the round, before any attack, so a slow
    tank still protects the whole zone (was: applied on the caster's turn).

Spike moments (replace crit/fumble): creativity tier 3 = DEVASTATING;
full dodge = highlight; WILD CARD can backfire.

Checks: attribution, ablation, and SPECIALIST ARCHETYPE round-robin to test
whether Speed is still a god stat.
"""

import random
from collections import defaultdict

ZONES = ["back_a", "front", "back_b"]
ZIDX = {z: i for i, z in enumerate(ZONES)}
COMBAT = ["smash", "shoot", "blast", "shield", "rally", "wild"]
MOVES = COMBAT + ["move_l", "move_r"]
CRE = [0, 1, 3, 5]
CRE_W = [0.35, 0.35, 0.22, 0.08]  # flat creativity bonus + tier weights
DODGE_PER_SPD = 0.07  # Speed's rebalance after ranged moved to Weird (see balance.yaml)
DODGE_CAP = 0.45  # binds only for montage-boosted Speed 7+ (Speed 6 = 0.42)
REFLECT_PER_POW = 0.10  # defend reflect chance
MAXR = 30
SUDDEN = 12


def d(n, s, rng):
    return sum(rng.randint(1, s) for _ in range(n))


class C:
    __slots__ = (
        "cid",
        "team",
        "pow",
        "spd",
        "wrd",
        "hp",
        "maxhp",
        "zone",
        "last",
        "shield",
        "shield_pow",
    )

    def __init__(s, cid, team, rng, stats=None):
        s.cid, s.team = cid, team
        if stats:
            s.pow, s.spd, s.wrd = stats
        else:
            while True:
                p = rng.randint(0, 6)
                sp = rng.randint(0, 6)
                w = 9 - p - sp
                if 0 <= w <= 6:
                    break
            s.pow, s.spd, s.wrd = p, sp, w
        s.maxhp = s.hp = 28 + 2 * s.pow + s.wrd
        s.zone = "back_a" if team == 0 else "back_b"
        s.last = None
        s.shield = 0
        s.shield_pow = 0

    def alive(s):
        return s.hp > 0


def smash_dmg(p, c, rng):
    return d(2, 4, rng) + p + 2 + c  # melee bonus for closing in


def shoot_dmg(w, c, rng):
    return d(2, 4, rng) + w + c  # ranged keys off Weird only


def blast_dmg(w, c, rng):
    return d(1, 6, rng) + w + c  # per-target AOE, friendly fire


def heal_amt(w, c, rng):
    return d(2, 6, rng) + 2 * w + 2 + c  # heals swing races


def wild_dmg(w, c, rng):
    return d(3, 6, rng) + w + c  # swingy


def apply(target, dmg, rng, st=None, mv=None):
    # passive dodge (Speed)
    if rng.random() < min(DODGE_CAP, DODGE_PER_SPD * target.spd):
        if st is not None:
            st["dodges"] += 1
        return 0
    reflected = 0
    if target.shield > 0:
        mit = target.shield
        if rng.random() < min(0.6, REFLECT_PER_POW * target.shield_pow):
            reflected = mit
        dmg = max(0, dmg - mit)
    target.hp -= dmg
    if st is not None and mv:
        st["imp"][mv] += dmg
    return reflected


def battle(rng, catalogs, stats_map=None, st=None):
    chars = [
        C(i, 0 if i < 3 else 1, rng, stats_map.get(i) if stats_map else None) for i in range(6)
    ]
    for rnd in range(1, MAXR + 1):
        sd = 3 if rnd > SUDDEN else 0
        living = [c for c in chars if c.alive()]
        plans = []
        for c in living:
            legal = [
                m
                for m in catalogs[c.team]
                if not (m in COMBAT and m == c.last)
                and not (m == "move_l" and c.zone == "back_a")
                and not (m == "move_r" and c.zone == "back_b")
            ]
            mv = rng.choice(legal)
            if mv in COMBAT:
                c.last = mv
            plans.append((c, mv, rng.choices(range(4), CRE_W)[0]))
            c.shield = 0
            c.shield_pow = 0
        plans.sort(key=lambda p: (p[0].spd, rng.random()), reverse=True)
        # SHIELD pre-pass (balance lever): shields go up at the START of the round,
        # before any attack, regardless of the caster's initiative — a slow tank
        # still protects the whole zone.
        for c, mv, tier in plans:
            if mv == "shield" and c.alive():
                for t in chars:
                    if t.alive() and t.team == c.team and t.zone == c.zone:
                        t.shield = 4 + c.pow
                        t.shield_pow = c.pow
        for c, mv, tier in plans:
            if not c.alive():
                continue
            cbon = CRE[tier] + sd
            enemies = [e for e in chars if e.alive() and e.team != c.team]
            allies = [a for a in chars if a.alive() and a.team == c.team and a is not c]
            if not enemies:
                break
            if st is not None:
                st["uses"][mv] += 1
                st["team"][mv].append(c.team)
            if mv in ("move_l", "move_r"):
                c.zone = ZONES[ZIDX[c.zone] + (1 if mv == "move_r" else -1)]
                continue
            if mv == "shield":
                continue  # shields already applied in the round-start pre-pass
            if mv == "rally":
                t = min(allies + [c], key=lambda a: a.hp / a.maxhp)
                t.hp = min(t.maxhp, t.hp + heal_amt(c.wrd, cbon, rng))
                continue
            if mv == "smash":
                cands = [e for e in enemies if e.zone == c.zone]
                if not cands:
                    tz = min(enemies, key=lambda e: abs(ZIDX[e.zone] - ZIDX[c.zone])).zone
                    c.zone = ZONES[ZIDX[c.zone] + (1 if ZIDX[tz] > ZIDX[c.zone] else -1)]
                    cands = [e for e in enemies if e.zone == c.zone]
                    if not cands:
                        continue
                t = rng.choice(cands)
                r = apply(t, smash_dmg(c.pow, cbon, rng), rng, st, "smash")
                if r:
                    c.hp -= r
            elif mv == "shoot":
                t = rng.choice(enemies)
                dm = shoot_dmg(c.wrd, cbon, rng)  # ranged tied to Weird only
                if t.zone == c.zone:
                    dm = (dm + 1) // 2
                r = apply(t, dm, rng, st, "shoot")
                if r:
                    c.hp -= r
            elif mv == "blast":
                # hits EVERYONE in the fullest enemy zone, allies there included
                # (friendly fire is BLAST's cost)
                zc = max(ZONES, key=lambda z: sum(1 for e in enemies if e.zone == z))
                for t in [x for x in chars if x.alive() and x.zone == zc and x is not c]:
                    apply(t, blast_dmg(c.wrd, cbon, rng), rng, st, "blast")
            elif mv == "wild":
                if rng.random() < 0.15:  # opt-in backfire
                    c.hp -= d(2, 4, rng)
                    if st is not None:
                        st["fumb"]["wild"] += 1
                else:
                    t = rng.choice(enemies)
                    r = apply(t, wild_dmg(c.wrd, cbon, rng), rng, st, "wild")
                    if r:
                        c.hp -= r
        t0 = any(c.alive() for c in chars if c.team == 0)
        t1 = any(c.alive() for c in chars if c.team == 1)
        if not (t0 and t1):
            return (0 if t0 else 1) if (t0 or t1) else None
    return None


def attribution(n, seed=7):
    rng = random.Random(seed)
    won = defaultdict(float)
    cnt = defaultdict(int)
    agg = dict(uses=defaultdict(int), imp=defaultdict(float), fumb=defaultdict(int))
    dodges = 0
    for _ in range(n):
        stx = dict(
            uses=defaultdict(int),
            team=defaultdict(list),
            imp=defaultdict(float),
            fumb=defaultdict(int),
            dodges=0,
        )
        w = battle(rng, (MOVES, MOVES), None, stx)
        for mv, teams in stx["team"].items():
            for t in teams:
                cnt[mv] += 1
                won[mv] += 0.5 if w is None else (1.0 if w == t else 0.0)
        for k in ("uses", "fumb"):
            for mv, v in stx[k].items():
                agg[k][mv] += v
        for mv, v in stx["imp"].items():
            agg["imp"][mv] += v
        dodges += stx["dodges"]
    print(f"{'move':<8}{'uses':>9}{'winrate':>9}{'impact/use':>12}")
    for mv in MOVES:
        u = cnt[mv]
        winrate = won[mv] / u if u else 0
        impact = agg["imp"][mv] / agg["uses"][mv] if agg["uses"][mv] else 0
        print(f"{mv:<8}{u:>9}{winrate:>9.3f}{impact:>12.2f}")
    print(f"avg dodges/game: {dodges / n:.1f}")


def ablation(n=2000):
    print("\nAblation (>0.5 = valuable):")
    for i, mv in enumerate(MOVES):
        rng = random.Random(100 + i)
        red = [m for m in MOVES if m != mv]
        w = (
            sum(
                0.5 if (r := battle(rng, (MOVES, red))) is None else (1.0 if r == 0 else 0.0)
                for _ in range(n)
            )
            / n
        )
        print(f"{mv:<8}{w:>7.3f}")


def archetypes(n=3000):
    """Round-robin: does one specialist dominate? Each team = 3 clones of an archetype."""
    A = {
        "Power(6/2/1)": (6, 2, 1),
        "Speed(1/6/2)": (1, 6, 2),
        "Weird(2/1/6)": (2, 1, 6),
        "Balanced(3/3/3)": (3, 3, 3),
    }
    print("\nSpecialist round-robin (row team win% vs col):")
    names = list(A)
    print("            " + "".join(f"{k[:9]:>11}" for k in names))
    dash = "\u2014"  # em-dash on the diagonal (no backslash inside the f-string: py311)
    for rn in names:
        row = f"{rn:<12}"
        for cn in names:
            if rn == cn:
                row += f"{dash:>11}"
                continue
            rng = random.Random(hash((rn, cn)) % 99999)
            sm = {0: A[rn], 1: A[rn], 2: A[rn], 3: A[cn], 4: A[cn], 5: A[cn]}
            w = (
                sum(
                    0.5
                    if (r := battle(rng, (MOVES, MOVES), sm)) is None
                    else (1.0 if r == 0 else 0.0)
                    for _ in range(n)
                )
                / n
            )
            row += f"{w:>11.3f}"
        print(row)


if __name__ == "__main__":
    attribution(10000)
    ablation()
    archetypes()
