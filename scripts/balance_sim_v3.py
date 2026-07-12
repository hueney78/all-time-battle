"""Doodle Brawl COMBAT V2 balance sim.

New system: players TAP a move (drawing = creativity/flavor only).
Moves: SMASH, BLAST, TRICK, SHIELD, RALLY, WILD, MOVE_L, MOVE_R.
Stats 0-6, budget 9. Resolution 2d6 + stat + creativity vs AC(10+Speed).
Crit: nat 12 or beat AC by >=5 (double dmg). Fumble: nat 2 (self dmg + embarrassed).
No action costs, no banking. No-repeat rule on the 6 combat moves.
3v3, random legal policy. Attribution + ablation analyses.
"""
import random
from collections import defaultdict

ZONES = ['back_a', 'front', 'back_b']
ZIDX = {z: i for i, z in enumerate(ZONES)}
COMBAT = ['smash', 'blast', 'shoot', 'shield', 'rally', 'wild']
MOVES = COMBAT + ['move_l', 'move_r']
CRE_B = [0, 1, 2, 4]; CRE_W = [0.40, 0.35, 0.20, 0.05]
MAXR = 30; SUDDEN = 12

def d(n, s, rng): return sum(rng.randint(1, s) for _ in range(n))

class C:
    __slots__ = ('cid','team','pow','spd','wrd','hp','maxhp','zone','conds','last','shield','stats_')
    def __init__(s, cid, team, rng):
        s.cid, s.team = cid, team
        while True:
            p = rng.randint(0, 6); sp = rng.randint(0, 6); w = 9 - p - sp
            if 0 <= w <= 6: break
        s.pow, s.spd, s.wrd = p, sp, w
        s.maxhp = s.hp = 20 + 2 * p
        s.zone = 'back_a' if team == 0 else 'back_b'
        s.conds = {}; s.last = None; s.shield = 0
    def alive(s): return s.hp > 0
    def ac(s): return 10 + s.spd + s.shield
    def atk(s, stat): return getattr(s, stat)

def smash_dmg(p, rng):  return d(1 + (p + 1) // 2, 4, rng) + 2      # P0:1d4+2 .. P6:4d4+2
def blast_dmg(w, rng):  return d(1 + w // 3, 4, rng) + 3            # W0:1d4+3 .. W6:3d4+3
def shoot_dmg(w, rng):  return d(1 + (w + 1) // 2, 4, rng) + 1      # W0:1d4+1 .. W6:4d4+1
def wild_dmg(w, rng):   return d(2, 8, rng) + w // 2                 # 2d8 + W/2, bigger fumble band

def battle(rng, catalogs, st=None):
    chars = [C(i, 0 if i < 3 else 1, rng) for i in range(6)]
    for rnd in range(1, MAXR + 1):
        sd = 2 if rnd > SUDDEN else 0
        living = [c for c in chars if c.alive()]
        plans = []
        for c in living:
            legal = [m for m in catalogs[c.team]
                     if not (m in COMBAT and m == c.last)
                     and not (m == 'move_l' and c.zone == 'back_a')
                     and not (m == 'move_r' and c.zone == 'back_b')]
            mv = rng.choice(legal)
            c.last = mv if mv in COMBAT else c.last
            plans.append((c, mv, rng.choices(range(4), CRE_W)[0]))
            c.shield = 0
        plans.sort(key=lambda p: (p[0].spd, rng.random()), reverse=True)
        for c, mv, tier in plans:
            if not c.alive(): continue
            enemies = [e for e in chars if e.alive() and e.team != c.team]
            allies = [a for a in chars if a.alive() and a.team == c.team and a is not c]
            if not enemies: break
            if st is not None: st['uses'][mv] += 1; st['team'][mv].append(c.team)

            if mv in ('move_l', 'move_r'):
                c.zone = ZONES[ZIDX[c.zone] + (1 if mv == 'move_r' else -1)]
                c.shield += 1  # dodging on the move: +1 AC this round
                continue
            if mv == 'shield':
                for t in allies + [c]:
                    if t.zone == c.zone: t.shield += 4
                if st is not None: st['imp'][mv] += 4
                continue
            if mv == 'rally':
                t = min(allies + [c], key=lambda a: a.hp / a.maxhp)
                amt = d(1, 6, rng) + CRE_B[tier]
                t.hp = min(t.maxhp, t.hp + amt)
                if st is not None: st['imp'][mv] += amt
                continue

            # attacks
            stat = 'pow' if mv == 'smash' else 'wrd'
            if mv == 'blast':
                zc = max(ZONES, key=lambda z: sum(1 for e in enemies if e.zone == z))
                targets = [x for x in chars if x.alive() and x.zone == zc and x is not c]
            else:
                cands = [e for e in enemies if e.zone == c.zone] if mv == 'smash' else enemies
                if mv == 'smash' and not cands:
                    # step toward nearest enemy and swing if adjacent-reach
                    tz = min(enemies, key=lambda e: abs(ZIDX[e.zone]-ZIDX[c.zone])).zone
                    step = 1 if ZIDX[tz] > ZIDX[c.zone] else -1
                    c.zone = ZONES[ZIDX[c.zone] + step]
                    cands = [e for e in enemies if e.zone == c.zone]
                    if not cands: continue
                targets = [rng.choice(cands)]
            roll = d(2, 6, rng)
            atk = roll + c.atk(stat) + CRE_B[tier] + sd
            if roll == 2 or (mv == 'wild' and roll <= 3):  # wild fumbles more
                c.hp -= 3
                if st is not None: st['fumb'][mv] += 1
                continue
            for t in targets:
                if not t.alive(): continue
                tt = t
                ac = tt.ac()
                if atk < ac:
                    # shield reflect on strong block
                    if tt.shield >= 4 and atk <= ac - 3:
                        c.hp -= d(1, 6, rng)
                    continue
                crit = roll == 12 or atk >= ac + 5
                dmg = dict(smash=smash_dmg(c.pow, rng), blast=blast_dmg(c.wrd, rng),
                           shoot=shoot_dmg(c.wrd, rng), wild=wild_dmg(c.wrd, rng))[mv]
                if mv == 'shoot' and tt.zone == c.zone: dmg = (dmg + 1) // 2  # point-blank penalty
                if crit: dmg *= 2
                tt.hp -= dmg
                if st is not None: st['imp'][mv] += dmg
        t0 = any(c.alive() for c in chars if c.team == 0)
        t1 = any(c.alive() for c in chars if c.team == 1)
        if not (t0 and t1):
            return (0 if t0 else 1) if (t0 or t1) else None
    return None

def attribution(n, seed=7):
    rng = random.Random(seed)
    won = defaultdict(float); cnt = defaultdict(int)
    agg = dict(uses=defaultdict(int), imp=defaultdict(float), fumb=defaultdict(int))
    for _ in range(n):
        st = dict(uses=defaultdict(int), team=defaultdict(list),
                  imp=defaultdict(float), fumb=defaultdict(int))
        w = battle(rng, (MOVES, MOVES), st)
        for mv, teams in st['team'].items():
            for t in teams:
                cnt[mv] += 1; won[mv] += 0.5 if w is None else (1.0 if w == t else 0.0)
        for k in ('uses','fumb'):
            for mv,v in st[k].items(): agg[k][mv]+=v
        for mv,v in st['imp'].items(): agg['imp'][mv]+=v
    print(f"{'move':<9}{'uses':>9}{'winrate':>9}{'impact/use':>12}")
    for mv in MOVES:
        u=cnt[mv]; print(f"{mv:<9}{u:>9}{won[mv]/u if u else 0:>9.3f}"
                         f"{agg['imp'][mv]/agg['uses'][mv] if agg['uses'][mv] else 0:>12.2f}")

def ablation(n_each=3000):
    print("\nAblation (Team B lacks move; >0.5 = valuable):")
    for i, mv in enumerate(MOVES):
        rng = random.Random(100+i)
        red = [m for m in MOVES if m != mv]
        w = sum(0.5 if (r:=battle(rng,(MOVES,red))) is None else (1.0 if r==0 else 0.0)
                for _ in range(n_each)) / n_each
        print(f"{mv:<9}{w:>7.3f}")

def stat_check(n=6000):
    """Does a +2 stat edge matter now? Team A gets stat budget 11 vs Team B's 9."""
    rng = random.Random(999); wins = 0.0
    orig = C.__init__
    def boosted(s, cid, team, r):
        orig(s, cid, team, r)
        if team == 0:
            for _ in range(2):
                k = r.choice(['pow','spd','wrd'])
                if getattr(s,k) < 6: setattr(s,k,getattr(s,k)+1)
            s.maxhp = s.hp = 20 + 2*s.pow
    C.__init__ = boosted
    for _ in range(n):
        w = battle(rng, (MOVES, MOVES))
        wins += 0.5 if w is None else (1.0 if w == 0 else 0.0)
    C.__init__ = orig
    print(f"\nStat sensitivity: +2 total budget team wins {wins/n:.3f} (want ~0.57-0.65)")

if __name__ == '__main__':
    attribution(12000)
    ablation(2500)
    stat_check()
