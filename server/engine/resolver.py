"""Pure game engine — resolves one round of COMBAT V5 actions into events.

Resolution (GAME_DESIGN §5). There is **no AC, no attack roll, and no dodge**: a
selected move always takes effect, with ONE positional exception — an ESCAPE's
parting shot lands only if its target was in the zone the escaper fled from,
otherwise it whiffs (see `_resolve_attack`). The only thing that reduces a hit
that DID land is PROTECT's reflect shield. Only the magnitude varies.

    effect = move's damage/heal formula + creativity bonus (+0/+1/+3/+5)
             + zone/underdog/sudden-death riders
    shield = if the target carries a PROTECT shield, it absorbs
             `reflect_per_weird × caster's Weird` (cap reflect_cap) of the hit and
             bounces exactly that much back at the attacker.

Spike moments come from the drawing, not the dice: creativity tier 3 lands as
result="devastating" (replay + stinger + gold log line).

Initiative: PROTECT casters act first (they cloak allies before any blow lands),
then by Speed, ties broken by a seeded roll. A character reduced to 0 HP loses
its action immediately, even if it had already tapped one — the loop re-checks
`is_ko` before every action (GAME_DESIGN §4.1 / §10 bug fix).

Movement lives inside attacks now: CHARGE rushes into the target's zone before
swinging; ESCAPE slips one zone (player picks ◀/▶) and fires a parting shot back
at the zone it just left — so it only hits a target that was in that old zone.
There are no movement buttons and no condition system.

Arena Gremlins plant traps (GAME_DESIGN §10): a KO'd player picks a zone and
draws a trap that PERSISTS in `GameState.traps` until an enemy is in that zone at
end of round, then fires `trap_damage + creativity` at one random enemy there and
is consumed. Traps are placed after combat and triggered in an end-of-round pass.

Dice consumption order (deterministic, seed-stable):
    1. Initiative tie-break shuffles (per group: PROTECT-casters then the rest,
       each grouped by speed desc).
    2. For each actor (initiative order): the move's formula roll, then any
       movement is deterministic (no draws).
    3. Trap placement (no draws), then the trap-trigger pass: per triggered trap,
       a victim choice then a damage roll.
"""

from __future__ import annotations

import uuid

from server.config import Balance, MoveDef
from server.engine.dice import Dice, formula_parts
from server.engine.models import (
    Character,
    ClassifiedAction,
    Event,
    EventType,
    GameState,
    RoundResult,
    Team,
    Trap,
)
from server.engine.moves import MoveRegistry
from server.engine.zones import ZoneRegistry

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def resolve_round(
    state: GameState,
    actions: list[ClassifiedAction],
    rng: Dice,
    cfg: Balance,
) -> RoundResult:
    """No I/O, no AI, no globals. Same inputs → same outputs.

    Handles: PROTECT-first initiative, the five v5 moves and their formulas,
    combo creativity escalation, damage and healing (every move lands), PROTECT's
    reflect shield, CHARGE/ESCAPE movement, KO → Gremlin conversion, Gremlin trap
    placement/persistence/triggering, victory detection, sudden death.

    Returns an ordered list of Events (input to the narrator) and the new GameState.
    """
    zone_reg = ZoneRegistry()
    move_reg = MoveRegistry()

    chars: dict[str, Character] = {
        pid: ch.model_copy(deep=True) for pid, ch in state.characters.items()
    }
    events: list[Event] = []
    round_num = state.round
    # Round-scoped PROTECT shields: protected pid → (reflect fraction, shielder pid).
    shields: dict[str, tuple[float, str]] = {}

    # Arena Gremlins that were already KO'd coming into this round — a fighter
    # KO'd *this* round only starts planting traps next round (GAME_DESIGN §10).
    start_gremlins = [pid for pid, ch in chars.items() if ch.is_gremlin]

    # ------------------------------------------------------------------
    # 1. Initiative order (PROTECT casters first, then speed desc, seeded ties)
    # ------------------------------------------------------------------
    action_map: dict[str, ClassifiedAction] = {a.player_id: a for a in actions}
    first_actors = {
        pid for pid, a in action_map.items()
        if a.move_id in move_reg and move_reg.get(a.move_id).acts_first
    }
    order = _initiative_order(_living(chars), first_actors, rng)

    # ------------------------------------------------------------------
    # 2. Process actions in initiative order
    # ------------------------------------------------------------------
    combos_announced: set[frozenset[str]] = set()
    winner: str | None = None
    for pid in order:
        ch = chars.get(pid)
        if ch is None or ch.is_ko:
            continue  # KO'd earlier this round → the tapped action never resolves

        action = action_map.get(pid)
        if action is None or action.move_id not in move_reg:
            events.append(Event(id=_eid("stmb"), type=EventType.STUMBLE,
                                round=round_num, player_id=pid, data={}))
            continue

        # COMBO! — announced once per partner group, when its first member acts.
        if action.combo_partners:
            group = frozenset([pid, *action.combo_partners])
            if group not in combos_announced:
                combos_announced.add(group)
                events.append(Event(
                    id=_eid("combo"), type=EventType.COMBO, round=round_num,
                    player_id=pid,
                    data={"partners": sorted(group), "combo_name": action.combo_name},
                ))

        _resolve_action(pid, action, chars, events, round_num, rng, cfg,
                        shields, zone_reg, move_reg, state)

        # Victory ends the round instantly (GAME_DESIGN §6): the moment a team's
        # last member falls — to this hit or its reflect — resolution stops. No
        # remaining winning-team fighter takes its queued action, and no traps
        # fire; the game cuts straight to the finale.
        winner = _check_victory(chars, state.teams)
        if winner:
            break

    # ------------------------------------------------------------------
    # 3. Record last move (no-repeat rule — every move is subject to it now).
    #    Skipped on an instant victory: the game is over, and fighters that
    #    never got to act must not be credited with a move they didn't make.
    # ------------------------------------------------------------------
    if not winner:
        for pid, action in action_map.items():
            ch = chars.get(pid)
            if ch is None or ch.is_ko:
                continue
            if action.move_id in move_reg:
                ch.last_move_id = action.move_id

    # ------------------------------------------------------------------
    # 4. Arena Gremlins plant traps, then all traps are triggered (GAME_DESIGN
    #    §10) — but not after an instant victory (resolution has already stopped).
    # ------------------------------------------------------------------
    traps: list[Trap] = [t.model_copy(deep=True) for t in state.traps]
    if not winner:
        for pid in sorted(start_gremlins):
            action = action_map.get(pid)
            if action is not None and action.trap_zone:
                _place_trap(pid, action, traps, events, round_num, cfg, zone_reg, state)
        traps = _trigger_traps(traps, chars, events, round_num, rng, cfg, state)

    # ------------------------------------------------------------------
    # 5. Victory / sudden death
    # ------------------------------------------------------------------
    new_state = state.model_copy(deep=True)
    new_state.characters = chars
    new_state.round = round_num
    new_state.traps = traps

    # Sudden death only escalates a game that is still going — never when this
    # round already produced a winner.
    if not winner and not state.sudden_death and round_num >= cfg.max_rounds:
        new_state.sudden_death = True
        events.append(Event(id=_eid("sd"), type=EventType.SUDDEN_DEATH,
                            round=round_num, data={}))

    # A trap trigger in step 4 can be what clinches the win, so re-check unless
    # the action loop already broke on an instant victory.
    winner = winner or _check_victory(chars, state.teams)
    if winner:
        new_state.winner_team_id = winner
        events.append(Event(id=_eid("win"), type=EventType.VICTORY, round=round_num,
                            data={"winner_team_id": winner}))

    return RoundResult(
        round=round_num, events=events, new_state=new_state, initiative_order=order
    )


# ---------------------------------------------------------------------------
# Initiative
# ---------------------------------------------------------------------------


def _initiative_order(
    living: dict[str, Character], first_actors: set[str], rng: Dice
) -> list[str]:
    """PROTECT casters first, then everyone else; each block ordered by speed
    desc, ties broken by a seeded shuffle (§5)."""

    def by_speed(pids: list[str]) -> list[str]:
        groups: dict[int, list[str]] = {}
        for pid in pids:
            groups.setdefault(living[pid].stats.speed, []).append(pid)
        out: list[str] = []
        for spd in sorted(groups.keys(), reverse=True):
            tier = sorted(groups[spd])   # stable base order before the seeded shuffle
            if len(tier) > 1:
                rng.shuffle(tier)
            out.extend(tier)
        return out

    firsts = [pid for pid in living if pid in first_actors]
    rest = [pid for pid in living if pid not in first_actors]
    return by_speed(firsts) + by_speed(rest)


# ---------------------------------------------------------------------------
# Single action resolver
# ---------------------------------------------------------------------------


def _resolve_action(
    pid: str,
    action: ClassifiedAction,
    chars: dict[str, Character],
    events: list[Event],
    round_num: int,
    rng: Dice,
    cfg: Balance,
    shields: dict[str, tuple[float, str]],
    zone_reg: ZoneRegistry,
    move_reg: MoveRegistry,
    state: GameState,
) -> None:
    move = move_reg.get(action.move_id)

    # ------ PROTECT (heal an ally + raise a reflecting shield) ------
    if move.heal:
        _resolve_protect(pid, action, move, chars, events, round_num, rng, cfg,
                         shields, zone_reg, state)
        return

    # ------ Attacks (SMASH / BLAST / CHARGE / ESCAPE) ------
    _resolve_attack(pid, action, move, chars, events, round_num, rng, cfg,
                    shields, zone_reg, state)


def _resolve_attack(
    pid: str,
    action: ClassifiedAction,
    move: MoveDef,
    chars: dict[str, Character],
    events: list[Event],
    round_num: int,
    rng: Dice,
    cfg: Balance,
    shields: dict[str, tuple[float, str]],
    zone_reg: ZoneRegistry,
    state: GameState,
) -> None:
    attacker = chars[pid]
    target_id = _resolve_target(pid, action, move, attacker, chars, zone_reg, state)
    if target_id is None:
        events.append(Event(
            id=_eid("atk"), type=EventType.ATTACK_RESOLVED, round=round_num,
            player_id=pid,
            data={"result": "no_target", "move_id": action.move_id,
                  "adaptation_note": action.adaptation_note},
        ))
        return
    target = chars[target_id]

    # ------ Movement carried inside the attack ------
    from_zone = attacker.zone_id      # ESCAPE's parting shot reaches only this zone
    if move.moves_to_target and target.zone_id != attacker.zone_id:
        _do_move(pid, attacker, target.zone_id, events, round_num)   # CHARGE rushes in
    elif move.moves_one_zone:
        direction = action.escape_direction or 1
        dest = zone_reg.step(attacker.zone_id, direction)
        if dest is None:                      # edge zones can only move inward
            dest = zone_reg.step(attacker.zone_id, -direction)
        if dest is not None and dest != attacker.zone_id:
            _do_move(pid, attacker, dest, events, round_num)          # ESCAPE slips away
        # ESCAPE fires a PARTING SHOT at the zone it fled FROM (§5): a player may
        # tap any enemy anywhere, but the shot lands only if that enemy was in the
        # escaper's old zone. Otherwise the fighter slips away clean and the shot
        # finds nobody — a whiff (the one exception to "every move lands"). The
        # escape itself still happened (the move event above). Config-gated so the
        # behavior is a move rider, not a hardcoded ESCAPE special-case.
        if move.hits_from_zone_only and target.zone_id != from_zone:
            events.append(Event(
                id=_eid("atk"), type=EventType.ATTACK_RESOLVED, round=round_num,
                player_id=pid, target_id=target_id,
                data={"result": "whiff", "move_id": action.move_id, "damage": 0,
                      "from_zone": from_zone, "adaptation_note": action.adaptation_note},
            ))
            return

    tier = _effective_tier(action, cfg)
    rolled = rng.roll_formula(move.damage or "0", _stat_env(attacker))
    riders = (
        int(zone_reg.modifier(attacker.zone_id, "damage_bonus"))
        + int(zone_reg.modifier(target.zone_id, "incoming_damage_bonus"))
        + _underdog_bonus(pid, chars, state, cfg)
        + (cfg.sudden_death_damage_bonus if state.sudden_death else 0)
    )
    breakdown = _breakdown(move, attacker, rolled, tier, riders, cfg)
    dmg = max(0, breakdown["raw"])

    # BLAST's point-blank penalty: half damage (round up) in the same zone.
    point_blank = move.same_zone_penalty == "half" and target.zone_id == attacker.zone_id
    if point_blank:
        dmg = (dmg + 1) // 2

    # PROTECT's shield — the only damage reduction: absorb a share, bounce it back.
    absorbed = 0
    shielder_id = ""
    cover = shields.get(target_id)
    if cover is not None:
        pct, shielder_id = cover
        absorbed = int(dmg * pct + 0.5)
        dmg -= absorbed

    target.hp = max(0, target.hp - dmg)
    data = {
        "result": "devastating" if tier >= 3 else "hit",
        "move_id": action.move_id, "damage": dmg,
        "point_blank": point_blank, "absorbed": absorbed, "creativity_tier": tier,
        "adaptation_note": action.adaptation_note,
        **breakdown,
    }
    if absorbed:
        data["shielder_id"] = shielder_id
    events.append(Event(
        id=_eid("atk"), type=EventType.ATTACK_RESOLVED, round=round_num,
        player_id=pid, target_id=target_id, data=data,
    ))
    if target.hp <= 0 and not target.is_ko:
        _ko(target_id, target, events, round_num)

    # The shield throws what it swallowed right back at the attacker (§5).
    if absorbed > 0:
        attacker.hp = max(0, attacker.hp - absorbed)
        events.append(Event(
            id=_eid("rfl"), type=EventType.ATTACK_RESOLVED, round=round_num,
            player_id=target_id, target_id=pid,
            data={"result": "reflect", "move_id": "protect", "damage": absorbed},
        ))
        if attacker.hp <= 0 and not attacker.is_ko:
            _ko(pid, attacker, events, round_num)


# ---------------------------------------------------------------------------
# PROTECT
# ---------------------------------------------------------------------------


def _resolve_protect(
    pid: str,
    action: ClassifiedAction,
    move: MoveDef,
    chars: dict[str, Character],
    events: list[Event],
    round_num: int,
    rng: Dice,
    cfg: Balance,
    shields: dict[str, tuple[float, str]],
    zone_reg: ZoneRegistry,
    state: GameState,
) -> None:
    """PROTECT (acts first): heal an ally and cloak them in a reflecting shield.
    It never self-targets; with no living ally the phone greys it out, so this is
    only reached with a valid ally (redirected to the neediest if the tapped one
    fell)."""
    caster = chars[pid]
    ally = _resolve_ally(pid, action, chars, state)
    if ally is None:
        return  # no living teammate to protect — fizzles (should be greyed anyway)
    target = chars[ally]

    tier = _effective_tier(action, cfg)
    rolled = rng.roll_formula(move.heal or "0", _stat_env(caster))
    heal_rider = int(zone_reg.modifier(target.zone_id, "heal_bonus"))
    breakdown = _breakdown(move, caster, rolled, tier, heal_rider, cfg)
    amount = max(0, breakdown["raw"])
    target.hp = min(target.max_hp, target.hp + amount)

    # PROTECT heals AND raises a reflecting shield in ONE action, so it resolves
    # to a SINGLE event (§11.2): the announcers call it as one beat ("healed and
    # shielded", never two), and the host plays the heal float and the round-long
    # shield glow off that one event. reflect_pct is 0 for a heal-only move (none
    # today — PROTECT always shields).
    pct = 0.0
    if move.applies_shield:
        pct = min(cfg.reflect_cap, cfg.reflect_per_weird * caster.stats.weird)
        shields[ally] = (pct, pid)
    events.append(Event(
        id=_eid("prot"), type=EventType.PROTECTED, round=round_num,
        player_id=pid, target_id=ally,
        data={"amount": amount, "creativity_tier": tier, "reflect_pct": pct,
              **breakdown},
    ))


# ---------------------------------------------------------------------------
# Targeting
# ---------------------------------------------------------------------------


def _resolve_target(
    pid: str,
    action: ClassifiedAction,
    move: MoveDef,
    attacker: Character,
    chars: dict[str, Character],
    zone_reg: ZoneRegistry,
    state: GameState,
) -> str | None:
    """The single enemy this attack lands on, with dead-target redirection
    (adapt, never reject — §9). Melee (SMASH) can only hit same-zone enemies;
    ranged (BLAST/CHARGE/ESCAPE) can hit any enemy."""
    living = _living(chars)
    enemies = [p for p in living if p != pid and not _same_team_bool(pid, p, state)]
    if not enemies:
        return None

    if move.range == "same_zone":
        cands = [p for p in enemies if chars[p].zone_id == attacker.zone_id]
        if not cands:
            return None  # SMASH with no enemy in the zone whiffs
        if action.target_id in cands:
            return action.target_id
        return sorted(cands)[0]

    # Ranged — the tapped enemy, or the nearest living enemy if it fell.
    if action.target_id in enemies:
        return action.target_id
    return min(
        sorted(enemies),
        key=lambda e: abs(zone_reg.steps_between(attacker.zone_id, chars[e].zone_id)),
    )


def _resolve_ally(
    pid: str,
    action: ClassifiedAction,
    chars: dict[str, Character],
    state: GameState,
) -> str | None:
    """PROTECT's target: the tapped living teammate, else the neediest living
    teammate (lowest HP fraction). Never the self."""
    allies = [
        p for p, c in chars.items()
        if p != pid and not c.is_ko and _same_team_bool(pid, p, state)
    ]
    if not allies:
        return None
    if action.target_id in allies:
        return action.target_id
    return min(sorted(allies), key=lambda a: chars[a].hp / max(1, chars[a].max_hp))


# ---------------------------------------------------------------------------
# Arena Gremlin traps (GAME_DESIGN §10)
# ---------------------------------------------------------------------------


def _place_trap(
    pid: str,
    action: ClassifiedAction,
    traps: list[Trap],
    events: list[Event],
    round_num: int,
    cfg: Balance,
    zone_reg: ZoneRegistry,
    state: GameState,
) -> None:
    """A gremlin plants a trap in the chosen zone; it joins the persistent trap
    list and only fires when an enemy is in that zone at end of round."""
    zone = action.trap_zone
    if zone not in zone_reg:
        return
    tier = _effective_tier(action, cfg)
    traps.append(Trap(
        trap_id=_eid("trap"), zone_id=zone, owner_id=pid,
        owner_team_id=_team_of(pid, state), creativity=tier,
        png_b64=action.action_png_b64,
    ))
    events.append(Event(
        id=_eid("tset"), type=EventType.TRAP_PLACED, round=round_num,
        player_id=pid,
        data={"zone": zone, "creativity_tier": tier,
              "adaptation_note": action.adaptation_note},
    ))


def _trigger_traps(
    traps: list[Trap],
    chars: dict[str, Character],
    events: list[Event],
    round_num: int,
    rng: Dice,
    cfg: Balance,
    state: GameState,
) -> list[Trap]:
    """End-of-round pass over every planted trap: if a living enemy of the
    owner's team stands in its zone, it springs on one random such enemy for
    `trap_damage + creativity` and is consumed. Untriggered traps persist."""
    survivors: list[Trap] = []
    for trap in traps:
        foes = sorted(
            p for p, c in chars.items()
            if not c.is_ko and c.zone_id == trap.zone_id
            and _team_of(p, state) != trap.owner_team_id
        )
        if not foes:
            survivors.append(trap)
            continue
        victim_id = rng.choice(foes)
        victim = chars[victim_id]
        dmg = max(0, rng.roll(cfg.trap_damage) + _creativity_bonus(trap.creativity, cfg))
        victim.hp = max(0, victim.hp - dmg)
        events.append(Event(
            id=_eid("trap"), type=EventType.TRAP_TRIGGERED, round=round_num,
            player_id=trap.owner_id, target_id=victim_id,
            data={"result": "trap", "zone": trap.zone_id, "damage": dmg,
                  "trap_id": trap.trap_id},
        ))
        if victim.hp <= 0 and not victim.is_ko:
            _ko(victim_id, victim, events, round_num)
    return survivors


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _living(chars: dict[str, Character]) -> dict[str, Character]:
    return {pid: ch for pid, ch in chars.items() if not ch.is_ko}


def _eid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _stat_env(ch: Character) -> dict[str, int]:
    """The formula-evaluation environment for one character (see dice.py)."""
    return {"POW": ch.stats.power, "SPD": ch.stats.speed, "WRD": ch.stats.weird}


def _effective_tier(action: ClassifiedAction, cfg: Balance) -> int:
    """The creativity tier that actually applies: a stale drawing scores 0 (§8),
    and a combo escalates both partners by combo_tier_bonus — which is how a
    tier-2 combo earns the DEVASTATING beat."""
    if action.similar_to_previous:
        tier = 0
    else:
        tier = action.creativity_tier
    if action.combo_partners:
        tier += cfg.combo_tier_bonus
    return max(0, min(tier, 3))


_STAT_KEYS = {"power": "POW", "speed": "SPD", "weird": "WRD"}


def headline_stat(move_stat: str, ch: Character) -> tuple[str, set[str]]:
    """A move's headline stat resolved for one character → (name, formula keys).

    The keys are every stat the expression reads, which is what the readout has
    to zero out to isolate the stat's contribution — for an "avg(power,speed)"
    move that's BOTH, and the printed name is the higher of the two.
    """
    if move_stat == "avg(power,speed)":
        name = "power" if ch.stats.power >= ch.stats.speed else "speed"
        return name, {"POW", "SPD"}
    if move_stat in _STAT_KEYS:
        return move_stat, {_STAT_KEYS[move_stat]}
    return "", set()


def _breakdown(
    move: MoveDef,
    actor: Character,
    rolled: int,
    tier: int,
    riders: int,
    cfg: Balance,
) -> dict:
    """Split a rolled effect into the terms the host readout adds up (§13):

        🔥 BLAST → 🎲 5 + 🌀 Weird 5 + ⭐⭐ Creative 3 = 13 damage

    The formula's flat mod already folds in the stat, so re-resolving it with
    that stat's inputs zeroed isolates the stat term without the catalog having
    to declare it separately. `dice` carries the move's own constant along with
    the roll (SMASH's "+2" rides inside 🎲) — keeping the line to one addition of
    three readable terms, as both §13 examples show.
    """
    env = _stat_env(actor)
    spec = move.damage or move.heal or "0"
    _, _, mod = formula_parts(spec, env)
    stat_name, stat_keys = headline_stat(move.stat, actor)
    if stat_keys:
        _, _, mod_without_stat = formula_parts(spec, env | dict.fromkeys(stat_keys, 0))
    else:
        mod_without_stat = mod
    stat_value = mod - mod_without_stat
    creativity = _creativity_bonus(tier, cfg)
    return {
        "dice": rolled - stat_value,          # the roll + the move's own constant
        "stat": stat_name,
        "stat_value": stat_value,
        "riders": riders,                     # zone/underdog/sudden-death; usually 0
        "creativity_bonus": creativity,
        "raw": rolled + creativity + riders,  # the addition's total
    }


def _creativity_bonus(tier: int, cfg: Balance) -> int:
    return [cfg.creativity_tier_0, cfg.creativity_tier_1,
            cfg.creativity_tier_2, cfg.creativity_tier_3][max(0, min(tier, 3))]


def _team_of(pid: str, state: GameState) -> str | None:
    for team in state.teams:
        if pid in team.player_ids:
            return team.id
    return None


def _same_team_bool(pid_a: str, pid_b: str, state: GameState) -> bool:
    """True only when both players share a defined team. If teams are unset
    (bare unit-test states), returns False so single-target flows still work."""
    ta = _team_of(pid_a, state)
    tb = _team_of(pid_b, state)
    return ta is not None and ta == tb


def _do_move(
    pid: str,
    ch: Character,
    target_zone: str,
    events: list[Event],
    round_num: int,
) -> None:
    old_zone = ch.zone_id
    ch.zone_id = target_zone
    events.append(Event(
        id=_eid("mv"), type=EventType.MOVED, round=round_num, player_id=pid,
        data={"from": old_zone, "to": target_zone},
    ))


def _ko(
    pid: str,
    ch: Character,
    events: list[Event],
    round_num: int,
) -> None:
    ch.hp = 0
    ch.is_ko = True
    ch.is_gremlin = True
    ch.last_move_id = None
    events.append(Event(id=_eid("ko"), type=EventType.KO, round=round_num,
                        player_id=pid, data={}))


def _underdog_bonus(
    pid: str,
    chars: dict[str, Character],
    state: GameState,
    cfg: Balance,
) -> int:
    if not cfg.underdog_enabled or not state.teams:
        return 0
    my_team = None
    for team in state.teams:
        if pid in team.player_ids:
            my_team = team
            break
    if not my_team:
        return 0
    team_hp: dict[str, int] = {}
    team_max_hp: dict[str, int] = {}
    for team in state.teams:
        team_hp[team.id] = sum(chars[p].hp for p in team.player_ids
                               if p in chars and not chars[p].is_ko)
        team_max_hp[team.id] = sum(chars[p].max_hp for p in team.player_ids if p in chars)
    my_share = team_hp.get(my_team.id, 0)
    opp_shares = [hp for tid, hp in team_hp.items() if tid != my_team.id]
    if not opp_shares:
        return 0
    best_opp = max(opp_shares)
    threshold_hp = cfg.underdog_hp_share_threshold * (
        team_max_hp.get(my_team.id, 1) / max(1, len(my_team.player_ids))
    )
    if best_opp - my_share >= threshold_hp:
        return cfg.underdog_damage_bonus
    return 0


def _check_victory(chars: dict[str, Character], teams: list[Team]) -> str | None:
    if not teams:
        return None
    for team in teams:
        members = [pid for pid in team.player_ids if pid in chars]
        if all(chars[pid].is_ko for pid in members) and members:
            for other in teams:
                if other.id != team.id:
                    return other.id
    return None
