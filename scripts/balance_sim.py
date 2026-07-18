"""Doodle Brawl COMBAT V5 sim — 5 moves, single-target only, no dodge, no AOE.

  SMASH   melee, same zone,       dmg 2d4 + POW + 2
  BLAST   ranged, any zone,       dmg 2d4 + WRD + 2      (half if target in your zone)
  CHARGE  move to target + hit,   dmg 2d4 + (POW+SPD)//2 (~2/3 of smash)
  ESCAPE  move 1 zone + ranged,   dmg 2d4 + SPD          (~2/3 of smash)
  PROTECT ally heal + reflect shield, heal 1d6 + WRD, ALWAYS ACTS FIRST

HP = 27 + 2*POW + WRD + SPD//2.  Creativity flat +0/+1/+3/+5.  No-repeat rule.
Shield: absorbs REFLECT_PER_WRD × Weird (cap 30%) of incoming and bounces it back.

Standalone MODEL — never imports the engine, so it can diverge on purpose.
`scripts/balance_engine.py` runs the same questions through the real resolver;
when the two disagree, the engine is the game. Measures: move ablation,
archetype round-robin, and a ZONE COLLAPSE check (does anyone ever leave home?).
"""
# ruff: noqa: E701, E702, E501  — intentionally terse one-line style for a fast model
import random
from collections import defaultdict

ZONES = ['back_a', 'front', 'back_b']; ZIDX = {z: i for i, z in enumerate(ZONES)}
MOVES = ['smash', 'blast', 'charge', 'escape', 'protect']
CRE = [0, 1, 3, 5]; CRE_W = [0.35, 0.35, 0.22, 0.08]
REFLECT_PER_WRD = 0.05   # 5% x Weird, cap 30%
MAXR = 40; SUDDEN = 14

def d(n, s, rng): return sum(rng.randint(1, s) for _ in range(n))

class C:
    __slots__ = ('cid','team','pow','spd','wrd','hp','maxhp','zone','last','shield')
    def __init__(s, cid, team, rng, stats=None):
        s.cid, s.team = cid, team
        if stats: s.pow, s.spd, s.wrd = stats
        else:
            while True:
                p=rng.randint(0,6); sp=rng.randint(0,6); w=9-p-sp
                if 0 <= w <= 6: break
            s.pow, s.spd, s.wrd = p, sp, w
        s.maxhp = s.hp = 27 + 2*s.pow + s.wrd + s.spd//2
        s.zone = 'back_a' if team==0 else 'back_b'
        s.last=None; s.shield=0.0
    def alive(s): return s.hp > 0
    def home(s): return 'back_a' if s.team==0 else 'back_b'

def hit(target, dmg, attacker, st=None, mv=None):
    reflected = 0
    if target.shield > 0:
        absorbed = int(dmg * target.shield + 0.5)
        dmg -= absorbed; reflected = absorbed
    target.hp -= max(0, dmg)
    if st is not None and mv: st['imp'][mv] += max(0, dmg)
    if reflected: attacker.hp -= reflected
    return reflected

def legal(c, chars, catalog):
    enemies=[e for e in chars if e.alive() and e.team!=c.team]
    allies=[a for a in chars if a.alive() and a.team==c.team and a is not c]
    out=[]
    for m in catalog:
        if m == c.last: continue                       # no-repeat
        if m=='smash' and not [e for e in enemies if e.zone==c.zone]: continue
        if m=='protect' and not allies: continue       # needs a living ally
        out.append(m)
    return out or ['blast']                            # blast is the universal fallback

def battle(rng, catalogs, stats_map=None, st=None):
    chars=[C(i, 0 if i<3 else 1, rng, stats_map.get(i) if stats_map else None) for i in range(6)]
    for rnd in range(1, MAXR+1):
        sd = 3 if rnd>SUDDEN else 0
        living=[c for c in chars if c.alive()]
        plans=[]
        for c in living:
            mv=rng.choice(legal(c, chars, catalogs[c.team]))
            c.last=mv
            plans.append((c, mv, rng.choices(range(4), CRE_W)[0]))
            c.shield=0.0
        # PROTECT always acts first, then by Speed
        plans.sort(key=lambda p:(p[1]=='protect', p[0].spd, rng.random()), reverse=True)
        for c, mv, tier in plans:
            if not c.alive(): continue
            cb=CRE[tier]+sd
            enemies=[e for e in chars if e.alive() and e.team!=c.team]
            allies=[a for a in chars if a.alive() and a.team==c.team and a is not c]
            if not enemies: break
            if st is not None:
                st['uses'][mv]+=1; st['team'][mv].append(c.team)
                if c.zone!=c.home(): st['away']+=1
                st['zonepos'][c.zone]+=1
            if mv=='protect':
                t=min(allies, key=lambda a:a.hp/a.maxhp) if allies else c
                t.hp=min(t.maxhp, t.hp + d(1,6,rng)+c.wrd+cb); t.shield=min(0.30, REFLECT_PER_WRD*c.wrd)
            elif mv=='smash':
                cands=[e for e in enemies if e.zone==c.zone]
                if cands: hit(rng.choice(cands), d(2,4,rng)+c.pow+2+cb, c, st,'smash')
            elif mv=='blast':
                t=rng.choice(enemies); dm=d(2,4,rng)+c.wrd+2+cb
                if t.zone==c.zone: dm=(dm+1)//2          # point-blank penalty
                hit(t, dm, c, st,'blast')
            elif mv=='charge':
                t=rng.choice(enemies); c.zone=t.zone   # already there -> no move, just swing
                hit(t, d(2,4,rng)+(c.pow+c.spd)//2+cb, c, st,'charge')
            elif mv=='escape':
                opts=[z for z in ZONES if abs(ZIDX[z]-ZIDX[c.zone])==1]
                c.zone=rng.choice(opts)
                hit(rng.choice(enemies), d(2,4,rng)+c.spd+cb, c, st,'escape')
        t0=any(c.alive() for c in chars if c.team==0); t1=any(c.alive() for c in chars if c.team==1)
        if not (t0 and t1): return (0 if t0 else 1) if (t0 or t1) else None
    return None

def attribution(n, seed=7):
    rng=random.Random(seed); won=defaultdict(float); cnt=defaultdict(int)
    agg=dict(uses=defaultdict(int), imp=defaultdict(float)); away=0; acts=0
    zp=defaultdict(int)
    for _ in range(n):
        s=dict(uses=defaultdict(int),team=defaultdict(list),imp=defaultdict(float),
               away=0, zonepos=defaultdict(int))
        w=battle(rng,(MOVES,MOVES),None,s)
        for mv,teams in s['team'].items():
            for t in teams: cnt[mv]+=1; won[mv]+=0.5 if w is None else (1.0 if w==t else 0.0)
        for mv,v in s['uses'].items(): agg['uses'][mv]+=v; acts+=v
        for mv,v in s['imp'].items(): agg['imp'][mv]+=v
        away+=s['away']
        for z,v in s['zonepos'].items(): zp[z]+=v
    print(f"{'move':<9}{'uses':>9}{'winrate':>9}{'dmg|heal/use':>14}")
    for mv in MOVES:
        u=cnt[mv]; print(f"{mv:<9}{u:>9}{won[mv]/u if u else 0:>9.3f}"
                         f"{agg['imp'][mv]/agg['uses'][mv] if agg['uses'][mv] else 0:>14.2f}")
    print(f"\nZONE COLLAPSE CHECK: actions taken away from home zone: {100*away/acts:.1f}%")
    tot=sum(zp.values())
    print("zone occupancy:", {z: f"{100*zp[z]/tot:.0f}%" for z in ZONES})

def ablation(n=2500):
    print("\nAblation (>0.5 = valuable):")
    for i,mv in enumerate(MOVES):
        rng=random.Random(100+i); red=[m for m in MOVES if m!=mv]
        w=sum(0.5 if (r:=battle(rng,(MOVES,red))) is None else (1.0 if r==0 else 0.0) for _ in range(n))/n
        print(f"{mv:<9}{w:>7.3f}")

def archetypes(n=3000):
    A={'Power(6/2/1)':(6,2,1),'Speed(1/6/2)':(1,6,2),'Weird(2/1/6)':(2,1,6),'Bal(3/3/3)':(3,3,3)}
    names=list(A)
    print("\nSpecialist round-robin (row win% vs col):")
    print("            "+"".join(f"{k[:10]:>12}" for k in names))
    for rn in names:
        row=f"{rn:<12}"
        for cn in names:
            if rn==cn: row+=f"{'-':>12}"; continue
            rng=random.Random(abs(hash((rn,cn)))%99999)
            sm={0:A[rn],1:A[rn],2:A[rn],3:A[cn],4:A[cn],5:A[cn]}
            w=sum(0.5 if (r:=battle(rng,(MOVES,MOVES),sm)) is None else (1.0 if r==0 else 0.0) for _ in range(n))/n
            row+=f"{w:>12.3f}"
        print(row)

if __name__=='__main__':
    attribution(10000); ablation(); archetypes()
