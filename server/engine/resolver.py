"""Pure game engine — resolves one round of COMBAT V2 actions into events.

Resolution (GAME_DESIGN §5):
    roll = 2d6 + move's stat + creativity bonus + modifiers(conditions, zones,
           combos, underdog, sudden death)   vs   AC (10 + Speed) + shield/dodge
    crit   = natural 12, or beat AC by >= crit_margin  → double damage
    hit    = roll >= AC                                → move's damage formula
    miss   = roll < AC   (a SHIELDed target missed by 3+ reflects 1d6)
    fumble = natural 2 (WILD CARD: natural <= fumble_on_roll_lte)
             → fumble_self_damage + Embarrassed, no target effects

Dice consumption order (deterministic, seed-stable):
    1. Initiative tie-break shuffles (per tied-speed group, speed desc).
    2. For each actor (initiative order):
       a. confused retarget choice (if any)
       b. attack 2d6 (if the move's stat != "none")
       c. one shared damage-formula roll if any target was hit
       d. reflect rolls per qualifying miss (target order)
       e. heal-formula roll (RALLY)
    3. Gremlin hazard zone/forced-move choices.
"""

from __future__ import annotations

import uuid

from server.config import Balance, MoveDef
from server.engine.conditions import ConditionRegistry
from server.engine.dice import Dice
from server.engine.hazards import HazardRegistry
from server.engine.models import (
    Character,
    ClassifiedAction,
    Event,
    EventType,
    GameState,
    RoundResult,
    Team,
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

    Handles: initiative, the eight v2 moves, combo roll bonuses, 2d6 attack
    resolution with degrees of success, condition apply/tick/expiry, absolute
    movement, KO → Gremlin conversion, victory detection, sudden death.

    Returns an ordered list of Events (input to the narrator) and the new GameState.
    """
    cond_reg = ConditionRegistry()
    zone_reg = ZoneRegistry()
    move_reg = MoveRegistry()
    haz_reg = HazardRegistry()

    chars: dict[str, Character] = {
        pid: ch.model_copy(deep=True) for pid, ch in state.characters.items()
    }
    events: list[Event] = []
    round_num = state.round

    # Arena Gremlins that were already KO'd coming into this round — a fighter
    # KO'd *this* round only starts dropping hazards next round (GAME_DESIGN §10).
    start_gremlins = [pid for pid, ch in chars.items() if ch.is_gremlin]

    # ------------------------------------------------------------------
    # 1. Tick conditions at round start
    # ------------------------------------------------------------------
    _tick_conditions(chars, events, round_num, cond_reg)

    # ------------------------------------------------------------------
    # 2. Initiative order (speed desc, seeded-roll tiebreak)
    # ------------------------------------------------------------------
    order = _initiative_order(_living(chars), rng, cond_reg)

    # ------------------------------------------------------------------
    # 3. Build action map (default missing actions to a stumble)
    # ------------------------------------------------------------------
    action_map: dict[str, ClassifiedAction] = {a.player_id: a for a in actions}

    # ------------------------------------------------------------------
    # 4. Process actions in initiative order
    # ------------------------------------------------------------------
    combos_announced: set[frozenset[str]] = set()
    for pid in order:
        ch = chars.get(pid)
        if ch is None or ch.is_ko:
            continue  # KO'd/gremlin fighters are handled in the gremlin pass below

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
                        cond_reg, zone_reg, move_reg, state)

    # ------------------------------------------------------------------
    # 4b. Arena Gremlins drop hazards (GAME_DESIGN §10)
    # ------------------------------------------------------------------
    # Runs after the round's combat so a hazard sets up the arena for the *next*
    # round rather than retroactively changing fights already resolved. Only
    # gremlins present at round start act, in stable player_id order.
    for pid in sorted(start_gremlins):
        action = action_map.get(pid)
        if action is not None:
            _resolve_gremlin(
                pid, action, chars, events, round_num, rng, zone_reg, cond_reg, haz_reg
            )

    # ------------------------------------------------------------------
    # 5. Record last combat move (no-repeat rule; movement is exempt)
    # ------------------------------------------------------------------
    for pid, action in action_map.items():
        ch = chars.get(pid)
        if ch is None or ch.is_ko:
            continue
        if action.move_id in move_reg and not move_reg.get(action.move_id).is_movement:
            ch.last_move_id = action.move_id

    # ------------------------------------------------------------------
    # 6. Victory / sudden death
    # ------------------------------------------------------------------
    new_state = state.model_copy(deep=True)
    new_state.characters = chars
    new_state.round = round_num

    if not state.sudden_death and round_num >= cfg.max_rounds:
        new_state.sudden_death = True
        events.append(Event(id=_eid("sd"), type=EventType.SUDDEN_DEATH,
                            round=round_num, data={}))

    winner = _check_victory(chars, state.teams)
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


def _effective_speed(ch: Character, cond_reg: ConditionRegistry) -> int:
    speed = ch.stats.speed
    for cond_name in ch.conditions:
        if cond_name in cond_reg:
            speed += cond_reg.get(cond_name).modifiers.speed
    return speed


def _initiative_order(
    living: dict[str, Character], rng: Dice, cond_reg: ConditionRegistry
) -> list[str]:
    # Group by effective speed (descending); ties broken by seeded roll (§5).
    groups: dict[int, list[str]] = {}
    for pid, ch in living.items():
        groups.setdefault(_effective_speed(ch, cond_reg), []).append(pid)

    order: list[str] = []
    for spd in sorted(groups.keys(), reverse=True):
        tier = sorted(groups[spd])   # stable base order before the seeded shuffle
        if len(tier) > 1:
            rng.shuffle(tier)
        order.extend(tier)
    return order


# ---------------------------------------------------------------------------
# Condition ticking
# ---------------------------------------------------------------------------


def _tick_conditions(
    chars: dict[str, Character],
    events: list[Event],
    round_num: int,
    cond_reg: ConditionRegistry,
) -> None:
    for pid, ch in chars.items():
        if ch.is_ko:
            continue
        expired = []
        for cond_name, rounds_left in list(ch.conditions.items()):
            if cond_name not in cond_reg:
                expired.append(cond_name)
                continue
            cdef = cond_reg.get(cond_name)
            if cdef.tick_damage > 0:
                ch.hp = max(0, ch.hp - cdef.tick_damage)
                events.append(Event(
                    id=_eid("tick"), type=EventType.CONDITION_TICKED, round=round_num,
                    player_id=pid,
                    data={"condition": cond_name, "damage": cdef.tick_damage, "hp": ch.hp},
                ))
            new_rounds = rounds_left - 1
            if new_rounds <= 0:
                expired.append(cond_name)
            else:
                ch.conditions[cond_name] = new_rounds
        for cond_name in expired:
            ch.conditions.pop(cond_name, None)
            events.append(Event(
                id=_eid("exp"), type=EventType.CONDITION_EXPIRED, round=round_num,
                player_id=pid, data={"condition": cond_name},
            ))
        if ch.hp <= 0 and not ch.is_ko:
            _ko(pid, ch, events, round_num)


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
    cond_reg: ConditionRegistry,
    zone_reg: ZoneRegistry,
    move_reg: MoveRegistry,
    state: GameState,
) -> None:
    attacker = chars[pid]
    move = move_reg.get(action.move_id)

    # ------ Movement (◀/▶: absolute, edge-checked, dodge AC) ------
    if move.is_movement:
        dest = zone_reg.step(attacker.zone_id, move.move)
        if dest is None:
            # Server disables edge-illegal buttons; defend anyway.
            events.append(Event(id=_eid("stmb"), type=EventType.STUMBLE,
                                round=round_num, player_id=pid,
                                data={"reason": "arena_edge"}))
            return
        _do_move(pid, attacker, dest, events, round_num)
        if move.applies_condition and move.applies_condition in cond_reg:
            _apply_condition(move.applies_condition, pid, attacker, events,
                             round_num, cond_reg)
        return

    # ------ Support moves (stat: none — SHIELD / RALLY) ------
    if move.stat == "none":
        _resolve_support(pid, action, move, attacker, chars, events, round_num,
                         rng, cond_reg, state)
        return

    # ------ Attack moves (SMASH / BLAST / TRICK / WILD) ------
    targets, auto_stepped = _resolve_targets(
        pid, action, move, attacker, chars, events, round_num, rng, zone_reg,
        cond_reg, state,
    )
    if not targets:
        events.append(Event(
            id=_eid("atk"), type=EventType.ATTACK_RESOLVED, round=round_num,
            player_id=pid,
            data={"result": "no_target", "move_id": action.move_id,
                  "adaptation_note": action.adaptation_note},
        ))
        return

    # Attack roll: 2d6 + stat + creativity + modifiers. A stale drawing scores
    # creativity 0 (§8) — variety in art enforced by the judge, not a penalty.
    natural = rng.two_d6()
    tier = 0 if action.similar_to_previous else action.creativity_tier
    total_atk = (
        natural
        + _get_stat(attacker, move.stat)
        + _creativity_bonus(tier, cfg)
        + _cond_mod(attacker, "attack", cond_reg)
        + zone_reg.modifier(attacker.zone_id, "attack_bonus")
        + (cfg.combo_bonus if action.combo_partners else 0)
        + _underdog_bonus(pid, chars, state, cfg)
        + (cfg.sudden_death_attack_bonus if state.sudden_death else 0)
    )

    # Fumble is decided on the shared natural roll, before any target math.
    fumble_band = max(2, move.fumble_on_roll_lte or 2)
    if natural <= fumble_band:
        _apply_fumble(pid, action, attacker, chars, events, round_num, cfg,
                      cond_reg, zone_reg, natural)
        return

    # Per-target degrees on the shared roll; damage rolled once, crits double it.
    ranged = move.range == "any"
    base_damage: int | None = None
    for target_id in targets:
        target = chars.get(target_id)
        if target is None or target.is_ko:
            continue
        effective_ac = _effective_ac(target, ranged, cond_reg, zone_reg)
        margin = (total_atk + _incoming_attack_bonus(target, cond_reg)) - effective_ac

        if natural == 12 or margin >= cfg.crit_margin:
            degree = "crit"
        elif margin >= 0:
            degree = "hit"
        else:
            degree = "miss"

        if degree == "miss":
            events.append(Event(
                id=_eid("atk"), type=EventType.ATTACK_RESOLVED, round=round_num,
                player_id=pid, target_id=target_id,
                data={"result": "miss", "move_id": action.move_id, "natural": natural,
                      "total_atk": total_atk, "ac": effective_ac,
                      "adaptation_note": action.adaptation_note},
            ))
            _maybe_reflect(pid, attacker, target_id, target, margin, events,
                           round_num, rng, cond_reg)
            continue

        if base_damage is None:
            base_damage = rng.roll_formula(move.damage or "0", _stat_env(attacker))
        dmg = max(0, int(base_damage * (cfg.crit_damage_mult if degree == "crit" else 1)))
        target.hp = max(0, target.hp - dmg)
        events.append(Event(
            id=_eid("atk"), type=EventType.ATTACK_RESOLVED, round=round_num,
            player_id=pid, target_id=target_id,
            data={"result": degree, "move_id": action.move_id, "natural": natural,
                  "total_atk": total_atk, "ac": effective_ac, "damage": dmg,
                  "creativity_tier": tier,
                  "adaptation_note": action.adaptation_note},
        ))

        # On-hit condition riders: TRICK's judged condition, WILD's read, or a
        # literal id from the catalog.
        for cond_name in _on_hit_conditions(move, action):
            if cond_name in cond_reg:
                _apply_condition(cond_name, target_id, target, events, round_num, cond_reg)

        if target.hp <= 0 and not target.is_ko:
            _ko(target_id, target, events, round_num)

    del auto_stepped  # informational only; the MOVED event already tells the story


def _on_hit_conditions(move: MoveDef, action: ClassifiedAction) -> list[str]:
    out: list[str] = []
    if move.on_hit_condition == "from_drawing":
        if action.trick_condition:
            out.append(action.trick_condition)
    elif move.on_hit_condition:
        out.append(move.on_hit_condition)
    if action.wild_interpretation and action.wild_interpretation.condition:
        out.append(action.wild_interpretation.condition)
    if move.applies_condition:
        out.append(move.applies_condition)
    return out


def _apply_fumble(
    pid: str,
    action: ClassifiedAction,
    attacker: Character,
    chars: dict[str, Character],
    events: list[Event],
    round_num: int,
    cfg: Balance,
    cond_reg: ConditionRegistry,
    zone_reg: ZoneRegistry,
    natural: int,
) -> None:
    attacker.hp = max(0, attacker.hp - cfg.fumble_self_damage)
    events.append(Event(
        id=_eid("atk"), type=EventType.ATTACK_RESOLVED, round=round_num,
        player_id=pid, target_id=action.target_id,
        data={"result": "fumble", "move_id": action.move_id, "natural": natural,
              "self_damage": cfg.fumble_self_damage,
              "adaptation_note": action.adaptation_note},
    ))
    # embarrassed auto-applies via its trigger="fumble"; zones can pile on.
    fumble_conditions = [n for n, c in cond_reg._defs.items() if c.trigger == "fumble"]
    zone_fumble = zone_reg.get(attacker.zone_id).modifiers.fumble_extra
    if zone_fumble and zone_fumble in cond_reg:
        fumble_conditions.append(zone_fumble)
    for cond_name in fumble_conditions:
        _apply_condition(cond_name, pid, attacker, events, round_num, cond_reg)
    if attacker.hp <= 0 and not attacker.is_ko:
        _ko(pid, attacker, events, round_num)


def _maybe_reflect(
    pid: str,
    attacker: Character,
    target_id: str,
    target: Character,
    margin: int,
    events: list[Event],
    round_num: int,
    rng: Dice,
    cond_reg: ConditionRegistry,
) -> None:
    """SHIELD's rider: an attack missing a shielded target by the reflect margin
    bounces damage back (read generically off any condition that defines it)."""
    for cond_name in target.conditions:
        if cond_name not in cond_reg:
            continue
        cdef = cond_reg.get(cond_name)
        if cdef.reflect_miss_margin > 0 and margin <= -cdef.reflect_miss_margin:
            dmg = rng.roll(cdef.reflect_damage or "0")
            attacker.hp = max(0, attacker.hp - dmg)
            events.append(Event(
                id=_eid("rfl"), type=EventType.ATTACK_RESOLVED, round=round_num,
                player_id=target_id, target_id=pid,
                data={"result": "reflect", "move_id": cond_name, "damage": dmg},
            ))
            if attacker.hp <= 0 and not attacker.is_ko:
                _ko(pid, attacker, events, round_num)
            return


# ---------------------------------------------------------------------------
# Support moves (SHIELD / RALLY)
# ---------------------------------------------------------------------------


def _resolve_support(
    pid: str,
    action: ClassifiedAction,
    move: MoveDef,
    attacker: Character,
    chars: dict[str, Character],
    events: list[Event],
    round_num: int,
    rng: Dice,
    cond_reg: ConditionRegistry,
    state: GameState,
) -> None:
    # ally_or_self: the tapped target if it's a living teammate, else self.
    target_id = pid
    if (
        action.target_id
        and action.target_id in chars
        and not chars[action.target_id].is_ko
        and _same_team_bool(pid, action.target_id, state)
    ):
        target_id = action.target_id
    target = chars[target_id]

    if move.applies_condition and move.applies_condition in cond_reg:
        _apply_condition(move.applies_condition, target_id, target, events,
                         round_num, cond_reg)

    if move.heal:
        if state.sudden_death:
            events.append(Event(
                id=_eid("heal"), type=EventType.HEALED, round=round_num,
                player_id=pid, target_id=target_id,
                data={"amount": 0, "blocked": "sudden_death"},
            ))
        else:
            amount = rng.roll_formula(move.heal, _stat_env(attacker))
            target.hp = min(target.max_hp, target.hp + amount)
            events.append(Event(
                id=_eid("heal"), type=EventType.HEALED, round=round_num,
                player_id=pid, target_id=target_id, data={"amount": amount},
            ))

    if move.cleanse == "all":
        for cond_name in list(target.conditions.keys()):
            # Only strip debuffs — never the target's own buffs/markers.
            if cond_name in cond_reg and cond_reg.get(cond_name).debuff:
                del target.conditions[cond_name]
                events.append(Event(
                    id=_eid("cln"), type=EventType.CONDITION_EXPIRED, round=round_num,
                    player_id=target_id,
                    data={"condition": cond_name, "source": "cleanse"},
                ))

    # RALLY's pumped buff is earned by the drawing (creativity tier gate).
    tier = 0 if action.similar_to_previous else action.creativity_tier
    if (
        move.pumped_if_creativity is not None
        and tier >= move.pumped_if_creativity
        and "pumped" in cond_reg
    ):
        _apply_condition("pumped", target_id, target, events, round_num, cond_reg)


# ---------------------------------------------------------------------------
# Targeting
# ---------------------------------------------------------------------------


def _resolve_targets(
    pid: str,
    action: ClassifiedAction,
    move: MoveDef,
    attacker: Character,
    chars: dict[str, Character],
    events: list[Event],
    round_num: int,
    rng: Dice,
    zone_reg: ZoneRegistry,
    cond_reg: ConditionRegistry,
    state: GameState,
) -> tuple[list[str], bool]:
    """The attack's target list. Handles confused retargeting, dead-target
    redirection (intent adaptation §9), SMASH's auto-step, and BLAST's zone.
    Returns (target_ids, auto_stepped)."""
    living = _living(chars)

    # confused: the next offensive action strikes ONE random other creature.
    randomized = any(
        c in cond_reg and cond_reg.get(c).randomize_targets for c in attacker.conditions
    )
    if randomized:
        pool = sorted(p for p in living if p != pid)
        return ([rng.choice(pool)] if pool else []), False

    enemies = [p for p in living if p != pid and not _same_team_bool(pid, p, state)]
    if not enemies:
        return [], False

    # The tapped enemy — redirected to the nearest living enemy if it fell to a
    # faster teammate earlier this round (adapt, never reject).
    intended = action.target_id
    if intended not in enemies:
        same_zone = [p for p in enemies if chars[p].zone_id == attacker.zone_id]
        redirect = sorted(same_zone)[0] if same_zone else sorted(enemies)[0]
        if intended is not None:
            events.append(Event(
                id=_eid("adapt"), type=EventType.MOVED, round=round_num,
                player_id=pid, target_id=redirect,
                data={"redirected_from": intended, "reason": "target_down"},
            ))
        intended = redirect

    if move.target == "zone_all":
        zone = chars[intended].zone_id
        return [
            p for p in sorted(living)
            if p != pid and chars[p].zone_id == zone
            and (move.friendly_fire or not _same_team_bool(pid, p, state))
        ], False

    # single_enemy — melee needs the same zone; SMASH auto-steps toward the target.
    auto_stepped = False
    if move.range == "same_zone" and chars[intended].zone_id != attacker.zone_id:
        if move.auto_step:
            toward = zone_reg.steps_between(attacker.zone_id, chars[intended].zone_id)
            dest = zone_reg.step(attacker.zone_id, 1 if toward > 0 else -1)
            if dest is not None:
                _do_move(pid, attacker, dest, events, round_num)
                auto_stepped = True
        if chars[intended].zone_id != attacker.zone_id:
            events.append(Event(
                id=_eid("atk"), type=EventType.ATTACK_RESOLVED, round=round_num,
                player_id=pid, target_id=intended,
                data={"result": "out_of_reach", "move_id": action.move_id,
                      "adaptation_note": action.adaptation_note},
            ))
            return [], auto_stepped
    return [intended], auto_stepped


# ---------------------------------------------------------------------------
# Gremlin hazards
# ---------------------------------------------------------------------------


def _resolve_gremlin(
    pid: str,
    action: ClassifiedAction,
    chars: dict[str, Character],
    events: list[Event],
    round_num: int,
    rng: Dice,
    zone_reg: ZoneRegistry,
    cond_reg: ConditionRegistry,
    haz_reg: HazardRegistry,
) -> None:
    """A gremlin drops a hazard on a random zone; every living fighter standing
    there suffers its effect — a condition and/or a forced move. The hazard id
    comes from the gremlin's classified drawing (config/hazards.yaml), so adding
    a hazard type is a YAML-only change."""
    all_zones = zone_reg.all_ids
    if not all_zones:
        return
    target_zone = rng.choice(all_zones)
    hazard_id = action.move_id
    hdef = haz_reg.get(hazard_id) if hazard_id in haz_reg else None

    # Snapshot occupants before any forced move mutates zones.
    occupants = [c for c in chars.values() if not c.is_ko and c.zone_id == target_zone]
    events.append(Event(
        id=_eid("grem"), type=EventType.GREMLIN_HAZARD, round=round_num,
        player_id=pid,
        data={
            "hazard_id": hazard_id,
            "zone": target_zone,
            "condition": hdef.applies_condition if hdef else None,
            "forces_move": bool(hdef.forces_move) if hdef else False,
            "affected": [c.player_id for c in occupants],
            "adaptation_note": action.adaptation_note,
        },
    ))
    if hdef is None:
        return

    for occ in occupants:
        if hdef.applies_condition:
            _apply_condition(hdef.applies_condition, occ.player_id, occ, events,
                             round_num, cond_reg)
        if hdef.forces_move:
            adj = zone_reg.adjacent(target_zone)
            if adj:
                _do_move(occ.player_id, occ, rng.choice(adj), events, round_num)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _living(chars: dict[str, Character]) -> dict[str, Character]:
    return {pid: ch for pid, ch in chars.items() if not ch.is_ko}


def _eid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _get_stat(ch: Character, stat_name: str) -> int:
    return getattr(ch.stats, stat_name, 0) if stat_name != "none" else 0


def _stat_env(ch: Character) -> dict[str, int]:
    """The formula-evaluation environment for one character (see dice.py)."""
    return {"POW": ch.stats.power, "SPD": ch.stats.speed, "WRD": ch.stats.weird}


def _creativity_bonus(tier: int, cfg: Balance) -> int:
    return [cfg.creativity_tier_0, cfg.creativity_tier_1,
            cfg.creativity_tier_2, cfg.creativity_tier_3][max(0, min(tier, 3))]


def _cond_mod(ch: Character, attr: str, cond_reg: ConditionRegistry) -> int:
    """Sum one ConditionModifiers field across the character's conditions."""
    total = 0
    for cond_name in ch.conditions:
        if cond_name in cond_reg:
            total += getattr(cond_reg.get(cond_name).modifiers, attr, 0)
    return total


def _incoming_attack_bonus(ch: Character, cond_reg: ConditionRegistry) -> int:
    """Extra attack bonus enemies get against this character (e.g. sparkly)."""
    total = 0
    for cond_name in ch.conditions:
        if cond_name in cond_reg:
            total += cond_reg.get(cond_name).incoming_attack_bonus
    return total


def _effective_ac(
    target: Character,
    is_ranged: bool,
    cond_reg: ConditionRegistry,
    zone_reg: ZoneRegistry,
) -> int:
    ac = target.ac
    ac += _cond_mod(target, "ac", cond_reg)
    ac += zone_reg.modifier(target.zone_id, "ac_bonus")
    if is_ranged:
        ac += zone_reg.modifier(target.zone_id, "ranged_ac_bonus")
    return ac


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


def _apply_condition(
    cond_name: str,
    target_id: str,
    target: Character,
    events: list[Event],
    round_num: int,
    cond_reg: ConditionRegistry,
) -> None:
    cdef = cond_reg.get(cond_name)
    # Check immunities: does target already have an immunity condition?
    for existing in list(target.conditions.keys()):
        if existing in cond_reg and cond_name in cond_reg.get(existing).immunities:
            return  # immune

    # Check if applied condition cures existing ones (cure_tags)
    for tag in cdef.cure_tags:
        for existing in list(target.conditions.keys()):
            cures = existing == tag or (
                existing in cond_reg and tag in cond_reg.get(existing).immunities
            )
            if cures:
                del target.conditions[existing]

    target.conditions[cond_name] = cdef.duration
    events.append(Event(
        id=_eid("cond"), type=EventType.CONDITION_APPLIED, round=round_num,
        player_id=target_id, data={"condition": cond_name, "duration": cdef.duration},
    ))


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
    ch.conditions = {}
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
        return cfg.underdog_attack_bonus
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
