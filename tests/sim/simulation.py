"""Doodle Brawl balance simulator — Monte Carlo playtest of the 30-move catalog.

Implements the GAME_DESIGN.md math: stats (Power/Speed/Weird, budget 8),
HP=18+2*POW, AC=11+SPD, 3 zones, action costs 1-3 with banking, creativity
tiers, degrees of success (crit at +10/nat20, fumble at -10/nat1), and the
full move catalog with conditions. Policy: uniform-random move selection
(measures intrinsic move power, not player skill).

Analyses:
  1. Per-move win-rate attribution (team that used it won?)  baseline 0.500
  2. Per-move ablation: Team B's catalog lacks move X; Team A win rate. baseline 0.500
  3. Damage/heal per use.
"""

import random
from collections import defaultdict

# ------------------------------ catalog ------------------------------------
# fields: roll ('power'|'weird'|None), rng ('same'|'any'), tgt, die, extras
M = {
    'strike':     dict(roll='power', rng='same', tgt='enemy', die=8),
    'charge':     dict(roll='power', rng='any', tgt='enemy', die=8, min_cost=2, moves_to_target=True),
    'ray':        dict(roll='weird', rng='any', tgt='enemy', die=6),
    'burst':      dict(roll='weird', rng='any', tgt='zone_all', die=6, min_cost=2),
    'line':       dict(roll='weird', rng='any', tgt='line', die=6, min_cost=2),
    'dot':        dict(roll='weird', rng='any', tgt='enemy', die=4, cond='burning'),
    'drain':      dict(roll='weird', rng='same', tgt='enemy', die=6, drain=0.5),
    'summon':     dict(roll='weird', rng='any', tgt='enemy', die=8, min_cost=2),
    'grapple':    dict(roll='power', rng='same', tgt='enemy', die=4, cond='sticky'),
    'shove':      dict(roll='power', rng='same', tgt='enemy', die=4, push=1),
    'trip':       dict(roll='power', rng='same', tgt='enemy', die=4, cond='prone'),
    'steal':      dict(roll='power', rng='same', tgt='enemy', die=4, steal=True),
    'demoralize': dict(roll='weird', rng='any', tgt='enemy', die=4, cond='frightened'),
    'feint':      dict(roll='weird', rng='same', tgt='enemy', die=4, cond='off_balance'),
    'confuse':    dict(roll='weird', rng='any', tgt='enemy', die=4, cond='confused'),
    'trap':       dict(roll=None, tgt='zone', min_cost=2, trap=True),
    'wall':       dict(roll=None, tgt='zone', min_cost=2, wall=True),
    'defend':     dict(roll=None, tgt='self', defend=2),
    'counter':    dict(roll=None, tgt='self', min_cost=2, counter=True),
    'hide':       dict(roll=None, tgt='self', cond_self='hidden'),
    'protect':    dict(roll=None, tgt='ally', protect=True),
    'sanctuary':  dict(roll=None, tgt='zone', min_cost=2, sanctuary=True),
    'heal':       dict(roll=None, tgt='ally_self', heal=6),
    'cleanse':    dict(roll=None, tgt='ally_self', cleanse=2),
    'buff':       dict(roll=None, tgt='ally', cond_ally='pumped'),
    'aid':        dict(roll=None, tgt='ally', aid=2),
    'transform':  dict(roll=None, tgt='self', transform=True),
    'move':       dict(roll=None, tgt='self', reposition=True),
    'stumble':    dict(roll=None, tgt='self', fixed_cost=0),
    'wildcard':   dict(roll='weird', rng='any', tgt='enemy', die=6),
}
CATALOG = list(M.keys())

COST_MULT = {0: 0.0, 1: 0.5, 2: 1.0, 3: 1.5}
CREATIVITY_BONUS = [0, 1, 2, 4]
CREATIVITY_WEIGHTS = [0.40, 0.35, 0.20, 0.05]
CRIT_MARGIN = 10
ZONES = ['back_a', 'front', 'back_b']
ZIDX = {z: i for i, z in enumerate(ZONES)}
MAX_ROUNDS = 40
SUDDEN_DEATH_ROUND = 12


class Char:
    __slots__ = ('cid', 'team', 'pow', 'spd', 'wrd', 'hp', 'maxhp', 'zone',
                 'conds', 'banked', 'defend_ac', 'counter', 'protector',
                 'aid_bonus', 'last_move')

    def __init__(self, cid, team, rng):
        self.cid, self.team = cid, team
        # random stat distribution: budget 8, each 1-4
        while True:
            p, s = rng.randint(1, 4), rng.randint(1, 4)
            w = 8 - p - s
            if 1 <= w <= 4:
                break
        self.pow, self.spd, self.wrd = p, s, w
        self.maxhp = self.hp = 18 + 2 * p
        self.zone = 'back_a' if team == 0 else 'back_b'
        self.conds = {}          # name -> rounds_left
        self.banked = 0
        self.defend_ac = 0       # this-round defend bonus
        self.counter = False     # counter readied this round
        self.protector = None    # Char redirecting attacks this round
        self.aid_bonus = 0       # +N to this round's roll
        self.last_move = None

    def alive(self):
        return self.hp > 0

    def ac(self, vs_ranged=False):
        ac = 11 + self.spd + self.defend_ac
        if 'prone' in self.conds:
            ac -= 1
        if 'off_balance' in self.conds:
            ac -= 2
        if 'hidden' in self.conds and vs_ranged:
            ac += 2
        return ac

    def atk_stat(self, roll_stat):
        v = self.pow if roll_stat == 'power' else self.wrd
        if 'pumped' in self.conds:
            v += 1
        if 'frightened' in self.conds:
            v -= 1
        if 'embarrassed' in self.conds:
            v -= 1
        return v


def dmg(die, cost, stat, rng):
    if die == 0:
        return 0
    return max(1, round(rng.randint(1, die) * COST_MULT[cost]) + stat)


def step_toward(char, target_zone):
    i, j = ZIDX[char.zone], ZIDX[target_zone]
    if i < j:
        char.zone = ZONES[i + 1]
    elif i > j:
        char.zone = ZONES[i - 1]


def battle(rng, catalogs, stats=None):
    """catalogs: (team0_move_list, team1_move_list). Returns winner 0/1/None."""
    chars = [Char(i, 0 if i < 3 else 1, rng) for i in range(6)]
    hazards = []   # dicts: zone, kind('trap'|'wall'), team, power, life
    zone_sanct = {}  # zone -> team with +1 ally AC this round

    for rnd in range(1, MAX_ROUNDS + 1):
        sd_bonus = 2 if rnd > SUDDEN_DEATH_ROUND else 0
        living = [c for c in chars if c.alive()]
        # ---- choose actions (state at round start) ----
        plans = []
        for c in living:
            mv = rng.choice(catalogs[c.team])
            spec = M[mv]
            if 'fixed_cost' in spec:
                cost = spec['fixed_cost']
            else:
                cost = rng.randint(spec.get('min_cost', 1), 3)
            tier = rng.choices(range(4), CREATIVITY_WEIGHTS)[0]
            stale = (mv == c.last_move)
            plans.append((c, mv, cost, tier, stale))
            c.last_move = mv
        # ---- reset round-scoped state, set banking ----
        for c, mv, cost, tier, stale in plans:
            c.banked = max(0, 3 - cost)
            c.defend_ac = 0
            c.counter = False
            c.protector = None
            c.aid_bonus = 0
        zone_sanct.clear()
        # ---- initiative ----
        plans.sort(key=lambda p: (p[0].spd - (1 if 'sticky' in p[0].conds else 0),
                                  rng.random()), reverse=True)
        # ---- resolve ----
        for c, mv, cost, tier, stale in plans:
            if not c.alive():
                continue
            spec = M[mv]
            enemies = [e for e in chars if e.alive() and e.team != c.team]
            allies = [a for a in chars if a.alive() and a.team == c.team and a is not c]
            if not enemies:
                break
            if stats is not None:
                stats['uses'][mv] += 1
                stats['use_team'][mv].append(c.team)

            # -- prone: pay an action to stand --
            if 'prone' in c.conds and cost > 0:
                del c.conds['prone']
                cost = max(1, cost - 1) if cost > 1 else 1  # weak version of losing an action
            # -- self/ally/zone moves (no roll) --
            if spec.get('defend'):
                c.defend_ac = spec['defend']
                continue
            if spec.get('counter'):
                c.counter = True
                continue
            if 'cond_self' in spec:
                c.conds[spec['cond_self']] = 1
                continue
            if spec.get('protect') and allies:
                rng.choice(allies).protector = c
                continue
            if spec.get('sanctuary'):
                zone_sanct[c.zone] = c.team
                continue
            if 'heal' in spec:
                t = min(allies + [c], key=lambda a: a.hp / a.maxhp)
                amt = max(1, round(rng.randint(1, spec['heal']) * COST_MULT[cost]))
                t.hp = min(t.maxhp, t.hp + amt)
                if stats is not None:
                    stats['impact'][mv] += amt
                continue
            if 'cleanse' in spec:
                pool = allies + [c]
                t = max(pool, key=lambda a: len(a.conds))
                bad = ['burning', 'sticky', 'prone', 'frightened', 'off_balance',
                       'confused', 'embarrassed']
                removed = 0
                for b in list(t.conds):
                    if b in bad and removed < spec['cleanse']:
                        del t.conds[b]
                        removed += 1
                continue
            if 'cond_ally' in spec and allies:
                rng.choice(allies).conds[spec['cond_ally']] = 2
                continue
            if spec.get('aid') and allies:
                rng.choice(allies).aid_bonus = spec['aid']
                continue
            if spec.get('transform'):
                # shift 2 points power<->speed in a random legal direction
                dirs = []
                if c.pow <= 2 and c.spd >= 3:
                    dirs.append(1)
                if c.spd <= 2 and c.pow >= 3:
                    dirs.append(-1)
                if dirs:
                    d = dirs[0] if len(dirs) == 1 else 1  # prefer +power
                    c.pow += 2 * d
                    c.spd -= 2 * d
                    if d == 1:
                        c.maxhp += 4; c.hp += 4   # HP follows Power per formula
                continue
            if spec.get('reposition'):
                tgt_zone = rng.choice([z for z in ZONES if z != c.zone])
                for _ in range(min(cost, abs(ZIDX[tgt_zone] - ZIDX[c.zone]))):
                    step_toward(c, tgt_zone)
                trigger_hazards(c, hazards, rng, stats)
                continue
            if spec.get('trap'):
                ez = rng.choice(['front', 'back_a' if c.team == 1 else 'back_b'])
                hazards.append(dict(zone=ez, kind='trap', team=c.team,
                                    power=cost, life=6))
                continue
            if spec.get('wall'):
                ez = rng.choice(['front', 'back_a' if c.team == 1 else 'back_b'])
                hazards.append(dict(zone=ez, kind='wall', team=c.team,
                                    power=cost, life=1))
                continue
            if mv == 'stumble':
                continue

            # -- attacks --
            melee = spec.get('rng') == 'same'
            cands = enemies
            if melee:
                cands = [e for e in enemies if e.zone == c.zone and 'hidden' not in e.conds]
                if not cands:
                    reach = [e for e in enemies
                             if abs(ZIDX[e.zone] - ZIDX[c.zone]) == 1 and 'hidden' not in e.conds]
                    if spec.get('moves_to_target') or True:  # adapt: close the gap
                        if reach:
                            t = rng.choice(reach)
                            step_toward(c, t.zone)
                            trigger_hazards(c, hazards, rng, stats)
                            cands = [t] if t.alive() and t.zone == c.zone else []
                        else:
                            step_toward(c, 'front')
                            trigger_hazards(c, hazards, rng, stats)
                            cands = []
                if not cands:
                    continue  # adapted into movement
            if not cands:
                continue

            if spec.get('tgt') == 'zone_all':
                zc = max(ZONES, key=lambda z: sum(1 for e in enemies if e.zone == z))
                targets = [x for x in chars if x.alive() and x.zone == zc and x is not c]
            elif spec.get('tgt') == 'line':
                targets = []
                for z in ZONES:
                    inz = [e for e in enemies if e.zone == z]
                    if inz:
                        targets.append(rng.choice(inz))
            else:
                targets = [rng.choice(cands)]

            # confused: retarget randomly among all living (allies included)
            if 'confused' in c.conds:
                pool = [x for x in chars if x.alive() and x is not c]
                targets = [rng.choice(pool)]
                del c.conds['confused']

            roll = rng.randint(1, 20)
            atk = (roll + c.atk_stat(spec['roll']) + CREATIVITY_BONUS[tier]
                   + (1 if cost == 3 else 0) + c.aid_bonus + sd_bonus
                   - (2 if stale else 0))
            for t in targets:
                if not t.alive():
                    continue
                # protect redirect
                redirected = False
                if t.protector and t.protector.alive():
                    t = t.protector
                    redirected = True
                ranged = not melee
                ac = t.ac(vs_ranged=ranged)
                if t.zone in zone_sanct and zone_sanct[t.zone] == t.team:
                    ac += 1
                if t.banked > 0:
                    ac += 1
                    t.banked -= 1
                if roll == 1 or atk <= ac - CRIT_MARGIN:
                    c.hp -= 2
                    c.conds['embarrassed'] = 2   # fumble
                    if stats is not None:
                        stats['fumbles'][mv] += 1
                    break  # fumble ends the whole action
                if atk < ac:
                    continue  # miss
                crit = (roll == 20) or (atk >= ac + CRIT_MARGIN)
                dstat = c.atk_stat(spec['roll'])
                if spec.get('tgt') in ('zone_all', 'line'):
                    dstat = (dstat + 1) // 2
                d = dmg(spec.get('die', 0), cost, dstat, rng)
                if crit:
                    d *= 2
                if t.counter and d > 0:
                    t.counter = False
                    c.hp -= d
                    if stats is not None:
                        stats['impact'][mv] += 0
                    continue
                if redirected:
                    d = (d + 1) // 2
                t.hp -= d
                if stats is not None:
                    stats['impact'][mv] += d
                if spec.get('drain'):
                    c.hp = min(c.maxhp, c.hp + round(d * spec['drain']))
                if 'cond' in spec:
                    t.conds[spec['cond']] = 2 if spec['cond'] != 'prone' else 1
                if spec.get('push') and t.alive():
                    away = ZONES[max(0, min(2, ZIDX[t.zone] + (1 if ZIDX[t.zone] >= ZIDX[c.zone] else -1)))]
                    t.zone = away
                    trigger_hazards(t, hazards, rng, stats)
                if spec.get('steal') and t.banked > 0:
                    c.banked += t.banked
                    t.banked = 0
            if spec.get('moves_to_target') and targets and targets[0].alive():
                c.zone = targets[0].zone

        # ---- end of round: hazard walls, condition ticks ----
        for h in hazards:
            if h['kind'] == 'wall':
                for x in chars:
                    if x.alive() and x.zone == h['zone'] and x.team != h['team']:
                        w = max(1, round(rng.randint(1, 4) * COST_MULT[h['power']]))
                        x.hp -= w
                h['life'] -= 1
        hazards[:] = [h for h in hazards if h['life'] > 0]
        for x in chars:
            if not x.alive():
                continue
            if 'burning' in x.conds:
                x.hp -= 2
            for k in list(x.conds):
                x.conds[k] -= 1
                if x.conds[k] <= 0:
                    del x.conds[k]

        t0 = any(c.alive() for c in chars if c.team == 0)
        t1 = any(c.alive() for c in chars if c.team == 1)
        if not (t0 and t1):
            return (0 if t0 else 1) if (t0 or t1) else None
    return None


def trigger_hazards(char, hazards, rng, stats):
    for h in hazards:
        if h['kind'] == 'trap' and h['zone'] == char.zone and h['team'] != char.team:
            d = max(1, round(rng.randint(1, 6) * COST_MULT[h['power']]))
            char.hp -= d
            char.conds['prone'] = 1
            h['life'] = 0
            if stats is not None:
                stats['impact']['trap'] += d
    hazards[:] = [h for h in hazards if h['life'] > 0]


# ------------------------------- analyses ----------------------------------
def run_attribution(n, seed=7):
    rng = random.Random(seed)
    stats = dict(uses=defaultdict(int), use_team=defaultdict(list),
                 impact=defaultdict(float), fumbles=defaultdict(int))
    won = defaultdict(float)
    cnt = defaultdict(int)
    draws = 0
    for _ in range(n):
        per = dict(uses=defaultdict(int), use_team=defaultdict(list),
                   impact=defaultdict(float), fumbles=defaultdict(int))
        w = battle(rng, (CATALOG, CATALOG), per)
        if w is None:
            draws += 1
        for mv, teams in per['use_team'].items():
            for t in teams:
                cnt[mv] += 1
                if w is None:
                    won[mv] += 0.5
                elif w == t:
                    won[mv] += 1.0
        for k in ('uses', 'fumbles'):
            for mv, v in per[k].items():
                stats[k][mv] += v
        for mv, v in per['impact'].items():
            stats['impact'][mv] += v
    return stats, won, cnt, draws


def run_ablation(move, n, seed):
    rng = random.Random(seed)
    reduced = [m for m in CATALOG if m != move]
    wins_a = 0.0
    for _ in range(n):
        w = battle(rng, (CATALOG, reduced))
        wins_a += 0.5 if w is None else (1.0 if w == 0 else 0.0)
    return wins_a / n


if __name__ == '__main__':
    N_ATTR, N_ABL = 20000, 3000
    stats, won, cnt, draws = run_attribution(N_ATTR)
    print(f"=== Attribution ({N_ATTR} battles, {draws} draws) ===")
    print(f"{'move':<12}{'uses':>8}{'winrate':>9}{'impact/use':>12}{'fumble%':>9}")
    rows = []
    for mv in CATALOG:
        u = cnt[mv]
        wr = won[mv] / u if u else 0
        ipu = stats['impact'][mv] / stats['uses'][mv] if stats['uses'][mv] else 0
        f = 100 * stats['fumbles'][mv] / stats['uses'][mv] if stats['uses'][mv] else 0
        rows.append((mv, u, wr, ipu, f))
    for mv, u, wr, ipu, f in sorted(rows, key=lambda r: -r[2]):
        print(f"{mv:<12}{u:>8}{wr:>9.3f}{ipu:>12.2f}{f:>8.1f}%")

    print(f"\n=== Ablation (Team A full catalog vs Team B missing move; "
          f"{N_ABL} battles each; >0.5 = move is valuable) ===")
    abl = []
    for i, mv in enumerate(CATALOG):
        abl.append((mv, run_ablation(mv, N_ABL, seed=100 + i)))
    for mv, wr in sorted(abl, key=lambda r: -r[1]):
        print(f"{mv:<12}{wr:>7.3f}")