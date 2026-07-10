"""Pure game engine — resolves one round of actions into events.

Damage formula:
    damage = int(die_roll * cost_mult * degree_mult) + stat
    degree_mult: 1 for hit, crit_damage_mult (2.0) for crit.

Dice consumption order (deterministic, seed-stable):
    1. Initiative tiebreaker d20s (per tied-speed group, in player_id sort order).
    2. For each actor (initiative order):
       a. attack d20 (if the move has roll != "none")
       b. damage die
       c. any additional per-event rolls (fumble zone-hazard, etc.)
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from server.config import Balance, ConditionDef, MoveDef
from server.engine.conditions import ConditionRegistry
from server.engine.dice import Dice
from server.engine.models import (
    Character,
    ClassifiedAction,
    Event,
    EventType,
    GameState,
    RoundResult,
    Stats,
    Team,
)
from server.engine.hazards import HazardRegistry
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

    Handles: initiative, action-cost budgeting, combo bonuses, attack rolls
    with degrees of success, damage, condition apply/tick/expiry, zone legality,
    KO → Gremlin conversion, victory detection, sudden death.

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
    # 2. Initiative order (speed desc, d20 tiebreak in player_id order)
    # ------------------------------------------------------------------
    living = _living(chars)
    order = _initiative_order(living, rng, cond_reg)

    # ------------------------------------------------------------------
    # 3. Build action map (default missing actions to stumble)
    # ------------------------------------------------------------------
    action_map: dict[str, ClassifiedAction] = {a.player_id: a for a in actions}
    for pid in living:
        if pid not in action_map:
            action_map[pid] = ClassifiedAction(
                player_id=pid, catalog_id="stumble", action_cost=1
            )

    # ------------------------------------------------------------------
    # 4. Resolve combo groupings
    # ------------------------------------------------------------------
    # combo_group maps each participant pid → the leader's action
    combo_of: dict[str, ClassifiedAction] = {}  # pid → leader action
    for action in actions:
        if action.combo_partners:
            all_pids = [action.player_id] + list(action.combo_partners)
            for pid in all_pids:
                if pid not in combo_of:
                    combo_of[pid] = action

    # ------------------------------------------------------------------
    # 5. Process actions in initiative order
    # ------------------------------------------------------------------
    processed: set[str] = set()

    for pid in order:
        if pid in processed:
            continue
        ch = chars.get(pid)
        if ch is None or ch.is_ko:
            continue  # KO'd/gremlin fighters are handled in the gremlin pass below

        # --- Combo ---
        if pid in combo_of:
            leader_action = combo_of[pid]
            leader_pid = leader_action.player_id
            if leader_pid not in processed:
                group = [leader_pid] + [
                    p for p in leader_action.combo_partners if p in _living(chars)
                ]
                _resolve_combo(
                    group,
                    leader_action,
                    action_map,
                    chars,
                    _living(chars),
                    events,
                    round_num,
                    rng,
                    cfg,
                    cond_reg,
                    zone_reg,
                    move_reg,
                    state,
                )
                for p in group:
                    processed.add(p)
            else:
                processed.add(pid)
            continue

        # --- Normal action ---
        action = action_map[pid]
        _resolve_action(
            pid,
            action,
            chars,
            _living(chars),
            events,
            round_num,
            rng,
            cfg,
            cond_reg,
            zone_reg,
            move_reg,
            state,
        )
        processed.add(pid)

    # ------------------------------------------------------------------
    # 5b. Arena Gremlins drop hazards (GAME_DESIGN §10)
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
    # 6. Set banked actions earned this round (for next round's defense)
    # ------------------------------------------------------------------
    for pid, ch in chars.items():
        if ch.is_ko or ch.is_gremlin:
            ch.banked_actions = 0
            continue
        action = action_map.get(pid)
        if action is None:
            ch.banked_actions = 0
            continue
        move = move_reg.get(action.catalog_id)
        if move.fixed_cost is not None:
            effective_cost = move.fixed_cost
        else:
            effective_cost = max(action.action_cost, move.min_cost)
            effective_cost = min(effective_cost, 3)
        scaling = cfg.cost_scaling.get(effective_cost, cfg.cost_scaling[2])
        bank = scaling.bank
        # Combo participants bank 0 (both rounds consumed)
        if pid in combo_of:
            bank = 0
        ch.banked_actions = bank
        if bank:
            events.append(
                Event(
                    id=_eid("bank"),
                    type=EventType.BANKED,
                    round=round_num,
                    player_id=pid,
                    data={"banked": bank},
                )
            )

    # ------------------------------------------------------------------
    # 7. Victory / sudden death
    # ------------------------------------------------------------------
    new_state = state.model_copy(deep=True)
    new_state.characters = chars
    new_state.round = round_num

    if not state.sudden_death and round_num >= cfg.max_rounds:
        new_state.sudden_death = True
        events.append(
            Event(
                id=_eid("sd"),
                type=EventType.SUDDEN_DEATH,
                round=round_num,
                data={},
            )
        )

    winner = _check_victory(chars, state.teams)
    if winner:
        new_state.winner_team_id = winner
        events.append(
            Event(
                id=_eid("win"),
                type=EventType.VICTORY,
                round=round_num,
                data={"winner_team_id": winner},
            )
        )

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
            cdef = cond_reg.get(cond_name)
            speed += cdef.modifiers.speed
    return speed


def _initiative_order(
    living: dict[str, Character], rng: Dice, cond_reg: ConditionRegistry
) -> list[str]:
    # Group by effective speed (descending). Ties broken by player_id sort so
    # no d20 is consumed here — every d20 goes to an attack or damage roll.
    groups: dict[int, list[str]] = {}
    for pid, ch in living.items():
        spd = _effective_speed(ch, cond_reg)
        groups.setdefault(spd, []).append(pid)

    order: list[str] = []
    for spd in sorted(groups.keys(), reverse=True):
        order.extend(sorted(groups[spd]))  # stable alphabetical within tier
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
            # Apply tick damage
            if cdef.tick_damage > 0:
                ch.hp = max(0, ch.hp - cdef.tick_damage)
                events.append(
                    Event(
                        id=_eid("tick"),
                        type=EventType.CONDITION_TICKED,
                        round=round_num,
                        player_id=pid,
                        data={"condition": cond_name, "damage": cdef.tick_damage, "hp": ch.hp},
                    )
                )
            # Decrement duration
            new_rounds = rounds_left - 1
            if new_rounds <= 0:
                expired.append(cond_name)
            else:
                ch.conditions[cond_name] = new_rounds
        for cond_name in expired:
            ch.conditions.pop(cond_name, None)
            # Restore pre-transform stats when Wild Shape wears off.
            if cond_name == "transformed" and ch.pre_transform_stats is not None:
                ch.stats = ch.pre_transform_stats
                ch.pre_transform_stats = None
            events.append(
                Event(
                    id=_eid("exp"),
                    type=EventType.CONDITION_EXPIRED,
                    round=round_num,
                    player_id=pid,
                    data={"condition": cond_name},
                )
            )
        # KO from tick damage
        if ch.hp <= 0 and not ch.is_ko:
            _ko(pid, ch, chars, events, round_num)


# ---------------------------------------------------------------------------
# Single action resolver
# ---------------------------------------------------------------------------


def _resolve_action(
    pid: str,
    action: ClassifiedAction,
    chars: dict[str, Character],
    living: dict[str, Character],
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
    move = move_reg.get(action.catalog_id)

    if move.fixed_cost is not None:
        effective_cost = move.fixed_cost
    else:
        effective_cost = max(action.action_cost, move.min_cost)
        effective_cost = min(effective_cost, 3)

    scaling = cfg.cost_scaling.get(effective_cost, cfg.cost_scaling[2])

    # ------ No-roll moves ------
    if move.roll == "none":
        _resolve_no_roll(
            pid, action, move, attacker, chars, living, events, round_num, rng, cfg, cond_reg, zone_reg, scaling,
            state,
        )
        return

    # ------ Attack moves ------
    targets = _resolve_targets(
        action, move, attacker, chars, living, zone_reg, cond_reg, state, rng
    )
    if not targets:
        events.append(
            Event(
                id=_eid("atk"),
                type=EventType.ATTACK_RESOLVED,
                round=round_num,
                player_id=pid,
                data={"result": "no_target", "catalog_id": action.catalog_id},
            )
        )
        return

    # Attack roll
    roll_stat_val = _get_stat(attacker, move.roll)
    atk_mod = _cond_mod(attacker, "attack", cond_reg)
    zone_atk_mod = zone_reg.modifier(attacker.zone_id, "attack_bonus")
    creativity_bonus = _creativity_bonus(action.creativity_tier, cfg)
    stale_mod = cfg.stale_penalty if action.similar_to_previous else 0
    underdog_mod = _underdog_bonus(pid, chars, state, cfg)
    sd_mod = cfg.sudden_death_attack_bonus if state.sudden_death else 0

    d20_roll = rng.d20()
    total_atk = (
        d20_roll + roll_stat_val + scaling.hit_bonus
        + atk_mod + zone_atk_mod + creativity_bonus + stale_mod + underdog_mod + sd_mod
    )

    for target_id in targets:
        target = chars.get(target_id)
        if target is None or target.is_ko:
            continue

        # Melee blocked by hidden
        is_ranged = move.range == "any"
        if not is_ranged and "hidden" in target.conditions:
            events.append(
                Event(
                    id=_eid("blk"),
                    type=EventType.ATTACK_RESOLVED,
                    round=round_num,
                    player_id=pid,
                    target_id=target_id,
                    data={"result": "blocked_hidden", "catalog_id": action.catalog_id},
                )
            )
            continue

        effective_ac = _effective_ac(target, attacker, is_ranged, cond_reg, zone_reg, action, cfg)
        margin = total_atk - effective_ac

        if d20_roll == 20 or margin >= cfg.crit_margin:
            degree = "crit"
        elif d20_roll == 1 or margin <= -cfg.fumble_margin:
            degree = "fumble"
        elif margin >= 0:
            degree = "hit"
        else:
            degree = "miss"

        _apply_degree(
            degree, pid, target_id, action, move, attacker, target,
            chars, events, round_num, rng, cfg, cond_reg, zone_reg, scaling,
            d20_roll, total_atk, effective_ac,
        )

    # Move included in attack (e.g. charge)
    if move.includes_move and action.move_to and action.move_to in zone_reg:
        _do_move(pid, attacker, action.move_to, chars, events, round_num, zone_reg)


# ---------------------------------------------------------------------------
# Apply degree of success
# ---------------------------------------------------------------------------


def _apply_degree(
    degree: str,
    pid: str,
    target_id: str,
    action: ClassifiedAction,
    move: MoveDef,
    attacker: Character,
    target: Character,
    chars: dict[str, Character],
    events: list[Event],
    round_num: int,
    rng: Dice,
    cfg: Balance,
    cond_reg: ConditionRegistry,
    zone_reg: ZoneRegistry,
    scaling: Any,
    d20_roll: int,
    total_atk: int,
    effective_ac: int,
) -> None:
    if degree in ("hit", "crit"):
        degree_mult = cfg.crit_damage_mult if degree == "crit" else 1.0

        # Roll damage die
        raw_dmg = 0
        if move.damage:
            die_roll = rng.roll(move.damage)
            raw_dmg = int(die_roll * scaling.damage_mult * degree_mult)

        # Add attacker stat
        stat_val = _get_stat(attacker, move.roll) if move.roll != "none" else 0
        total_dmg = raw_dmg + stat_val
        # Sudden death disables healing; damage is always ≥ 0
        total_dmg = max(0, total_dmg)

        # drain heals attacker
        heal_self = 0
        if move.heal_self_ratio > 0:
            heal_self = int(total_dmg * move.heal_self_ratio)

        # Apply damage to target
        target.hp = max(0, target.hp - total_dmg)
        events.append(
            Event(
                id=_eid("atk"),
                type=EventType.ATTACK_RESOLVED,
                round=round_num,
                player_id=pid,
                target_id=target_id,
                data={
                    "result": degree,
                    "catalog_id": action.catalog_id,
                    "d20": d20_roll,
                    "total_atk": total_atk,
                    "ac": effective_ac,
                    "damage": total_dmg,
                    "creativity_tier": action.creativity_tier,
                    "adaptation_note": action.adaptation_note,
                },
            )
        )

        # Self-heal (drain)
        if heal_self > 0:
            attacker.hp = min(attacker.max_hp, attacker.hp + heal_self)
            events.append(
                Event(
                    id=_eid("heal"),
                    type=EventType.HEALED,
                    round=round_num,
                    player_id=pid,
                    data={"amount": heal_self, "source": "drain"},
                )
            )

        # Banked defense (consume banked actions for the AC they provided)
        if target.banked_actions > 0:
            target.banked_actions = 0  # spent defending this round

        # On-hit conditions from move catalog
        all_conditions = []
        if move.on_hit_condition:
            all_conditions.append(move.on_hit_condition)
        all_conditions.extend(action.suggested_conditions)

        for cond_name in all_conditions:
            if cond_name in cond_reg:
                _apply_condition(cond_name, target_id, target, chars, events, round_num, cond_reg)

        # On-hit: push zones
        if move.on_hit_push_zones > 0:
            adj = zone_reg.adjacent(target.zone_id)
            if adj:
                new_zone = adj[0]
                _do_move(target_id, target, new_zone, chars, events, round_num, zone_reg)

        # On-hit: steal banked actions
        if move.on_hit_steal_banked and target.banked_actions > 0:
            stolen = target.banked_actions
            target.banked_actions = 0
            attacker.banked_actions += stolen

        # KO check
        if target.hp <= 0 and not target.is_ko:
            _ko(target_id, target, chars, events, round_num)

    elif degree == "miss":
        events.append(
            Event(
                id=_eid("atk"),
                type=EventType.ATTACK_RESOLVED,
                round=round_num,
                player_id=pid,
                target_id=target_id,
                data={
                    "result": "miss",
                    "catalog_id": action.catalog_id,
                    "d20": d20_roll,
                    "total_atk": total_atk,
                    "ac": effective_ac,
                },
            )
        )

    elif degree == "fumble":
        # Self-damage + embarrassed condition
        self_dmg = cfg.fumble_self_damage
        attacker.hp = max(0, attacker.hp - self_dmg)

        # Zone fumble_extra (e.g. high ground → prone)
        zone_fumble = zone_reg.get(attacker.zone_id).modifiers.fumble_extra
        fumble_conditions = []
        # embarrassed is auto-applied via its trigger="fumble"
        for cond_name, cdef in cond_reg._defs.items():
            if cdef.trigger == "fumble":
                fumble_conditions.append(cond_name)
        if zone_fumble and zone_fumble in cond_reg:
            fumble_conditions.append(zone_fumble)

        events.append(
            Event(
                id=_eid("atk"),
                type=EventType.ATTACK_RESOLVED,
                round=round_num,
                player_id=pid,
                target_id=target_id,
                data={
                    "result": "fumble",
                    "catalog_id": action.catalog_id,
                    "d20": d20_roll,
                    "self_damage": self_dmg,
                },
            )
        )

        for cond_name in fumble_conditions:
            _apply_condition(cond_name, pid, attacker, chars, events, round_num, cond_reg)

        if attacker.hp <= 0 and not attacker.is_ko:
            _ko(pid, attacker, chars, events, round_num)


# ---------------------------------------------------------------------------
# No-roll moves (defend, heal, move, hide, etc.)
# ---------------------------------------------------------------------------


def _resolve_no_roll(
    pid: str,
    action: ClassifiedAction,
    move: MoveDef,
    attacker: Character,
    chars: dict[str, Character],
    living: dict[str, Character],
    events: list[Event],
    round_num: int,
    rng: Dice,
    cfg: Balance,
    cond_reg: ConditionRegistry,
    zone_reg: ZoneRegistry,
    scaling: Any,
    state: GameState,
) -> None:
    # Stumble: nothing happens
    if action.catalog_id == "stumble":
        events.append(
            Event(
                id=_eid("stmb"),
                type=EventType.STUMBLE,
                round=round_num,
                player_id=pid,
                data={},
            )
        )
        return

    # applies_condition (hide, defend→shielded, etc.). Self-buffs are modelled
    # as 1-round conditions so they reset automatically and never stack — the
    # AC bump lives in the `shielded` condition, read at attack-time by
    # _effective_ac, not by mutating the character's base AC.
    if move.applies_condition and move.applies_condition in cond_reg:
        _apply_condition(
            move.applies_condition, pid, attacker, chars, events, round_num, cond_reg
        )

    # heal
    if move.heal:
        targets = _resolve_targets(
            action, move, attacker, chars, living, zone_reg, cond_reg, state, rng
        )
        for tid in targets:
            target = chars.get(tid)
            if target is None or target.is_ko:
                continue
            if state.sudden_death:
                break  # healing disabled in sudden death
            die_roll = rng.roll(move.heal)
            heal_amt = die_roll
            target.hp = min(target.max_hp, target.hp + heal_amt)
            events.append(
                Event(
                    id=_eid("heal"),
                    type=EventType.HEALED,
                    round=round_num,
                    player_id=pid,
                    target_id=tid,
                    data={"amount": heal_amt},
                )
            )

    # cleanse
    if move.removes_conditions > 0:
        targets = _resolve_targets(
            action, move, attacker, chars, living, zone_reg, cond_reg, state, rng
        )
        for tid in targets:
            target = chars.get(tid)
            if target is None or target.is_ko:
                continue
            removed = 0
            for cond_name in list(target.conditions.keys()):
                if removed >= move.removes_conditions:
                    break
                # Only strip debuffs — never the target's own buffs/markers
                # (pumped, shielded, transformed, …).
                if cond_name not in cond_reg or not cond_reg.get(cond_name).debuff:
                    continue
                del target.conditions[cond_name]
                removed += 1
                events.append(
                    Event(
                        id=_eid("cln"),
                        type=EventType.CONDITION_EXPIRED,
                        round=round_num,
                        player_id=tid,
                        data={"condition": cond_name, "source": "cleanse"},
                    )
                )

    # buff
    if move.applies_condition and move.target in ("ally", "ally_or_self"):
        targets = _resolve_targets(
            action, move, attacker, chars, living, zone_reg, cond_reg, state, rng
        )
        for tid in targets:
            target = chars.get(tid)
            if target and not target.is_ko and move.applies_condition in cond_reg:
                _apply_condition(
                    move.applies_condition, tid, target, chars, events, round_num, cond_reg
                )

    # move
    if move.move_zones_per_cost > 0 and action.move_to:
        if action.move_to in zone_reg:
            _do_move(pid, attacker, action.move_to, chars, events, round_num, zone_reg)

    # transform (stat_swap): shift Power up / Speed down for a few rounds. The
    # original stats are saved and restored when the `transformed` condition
    # expires (see _tick_conditions), so the change is temporary and cannot be
    # chained/stacked while already active.
    if move.stat_swap > 0 and "transformed" not in attacker.conditions:
        attacker.pre_transform_stats = attacker.stats.model_copy()
        attacker.stats = Stats(
            power=min(cfg.stat_max, attacker.stats.power + move.stat_swap),
            speed=max(cfg.stat_min, attacker.stats.speed - move.stat_swap),
            weird=attacker.stats.weird,
        )
        _apply_condition("transformed", pid, attacker, chars, events, round_num, cond_reg)
        events.append(
            Event(
                id=_eid("xfrm"),
                type=EventType.CONDITION_APPLIED,
                round=round_num,
                player_id=pid,
                data={"action": "transform", "stat_swap": move.stat_swap},
            )
        )

    # aid: grant_roll_bonus is handled by giving a transient bonus
    # (stored for this round; a future pass could wire it to the target's next
    # action roll — tracked via the suggested_conditions approach)


# ---------------------------------------------------------------------------
# Combo resolution
# ---------------------------------------------------------------------------


def _resolve_combo(
    group: list[str],
    leader_action: ClassifiedAction,
    action_map: dict[str, ClassifiedAction],
    chars: dict[str, Character],
    living: dict[str, Character],
    events: list[Event],
    round_num: int,
    rng: Dice,
    cfg: Balance,
    cond_reg: ConditionRegistry,
    zone_reg: ZoneRegistry,
    move_reg: MoveRegistry,
    state: GameState,
) -> None:
    leader_pid = leader_action.player_id
    attacker = chars[leader_pid]
    move = move_reg.get(leader_action.leading_catalog_id or leader_action.catalog_id)

    # Best roll stat among partners
    best_stat = max(
        _get_stat(chars[p], move.roll) for p in group if p in chars and move.roll != "none"
    )

    # Highest creativity tier, escalated
    max_tier = max(
        action_map[p].creativity_tier for p in group if p in action_map
    )
    escalated_tier = min(3, max_tier + cfg.combo_creativity_escalate)
    creativity_bonus = _creativity_bonus(escalated_tier, cfg)

    atk_mod = _cond_mod(attacker, "attack", cond_reg)
    zone_atk_mod = zone_reg.modifier(attacker.zone_id, "attack_bonus")
    underdog_mod = _underdog_bonus(leader_pid, chars, state, cfg)
    sd_mod = cfg.sudden_death_attack_bonus if state.sudden_death else 0

    d20_roll = rng.d20()
    total_atk = (
        d20_roll + best_stat + cfg.combo_bonus
        + atk_mod + zone_atk_mod + creativity_bonus + underdog_mod + sd_mod
    )

    targets = _resolve_targets(
        leader_action, move, attacker, chars, living, zone_reg, cond_reg, state, rng
    )

    events.append(
        Event(
            id=_eid("combo"),
            type=EventType.COMBO,
            round=round_num,
            player_id=leader_pid,
            data={
                "partners": group,
                "combo_name": leader_action.combo_name,
                "d20": d20_roll,
                "total_atk": total_atk,
            },
        )
    )

    for target_id in targets:
        target = chars.get(target_id)
        if target is None or target.is_ko:
            continue

        effective_ac = _effective_ac(target, attacker, move.range == "any", cond_reg, zone_reg, leader_action, cfg)
        margin = total_atk - effective_ac

        if d20_roll == 20 or margin >= cfg.crit_margin:
            degree = "crit"
        elif d20_roll == 1 or margin <= -cfg.fumble_margin:
            degree = "fumble"
        elif margin >= 0:
            degree = "hit"
        else:
            degree = "miss"

        degree_mult = cfg.crit_damage_mult if degree == "crit" else 1.0

        # Combined damage: each partner contributes their own cost-scaled die
        combined_dmg = 0
        for p in group:
            p_action = action_map.get(p)
            if not p_action or not move.damage:
                continue
            p_cost = max(p_action.action_cost, move.min_cost)
            p_scaling = cfg.cost_scaling.get(min(p_cost, 3), cfg.cost_scaling[2])
            die_roll = rng.roll(move.damage)
            combined_dmg += int(die_roll * p_scaling.damage_mult * degree_mult)

        combined_dmg += best_stat
        combined_dmg = max(0, combined_dmg)

        if degree in ("hit", "crit"):
            target.hp = max(0, target.hp - combined_dmg)
            events.append(
                Event(
                    id=_eid("atk"),
                    type=EventType.ATTACK_RESOLVED,
                    round=round_num,
                    player_id=leader_pid,
                    target_id=target_id,
                    data={
                        "result": degree,
                        "catalog_id": move.pf2e,
                        "combo": True,
                        "combo_name": leader_action.combo_name,
                        "damage": combined_dmg,
                        "d20": d20_roll,
                        "total_atk": total_atk,
                        "ac": effective_ac,
                    },
                )
            )
            if move.on_hit_condition and move.on_hit_condition in cond_reg:
                _apply_condition(move.on_hit_condition, target_id, target, chars, events, round_num, cond_reg)
            if target.hp <= 0 and not target.is_ko:
                _ko(target_id, target, chars, events, round_num)
        elif degree in ("miss", "fumble"):
            events.append(
                Event(
                    id=_eid("atk"),
                    type=EventType.ATTACK_RESOLVED,
                    round=round_num,
                    player_id=leader_pid,
                    target_id=target_id,
                    data={
                        "result": degree,
                        "combo": True,
                        "combo_name": leader_action.combo_name,
                        "d20": d20_roll,
                    },
                )
            )
            if degree == "fumble":
                for p in group:
                    p_ch = chars.get(p)
                    if p_ch:
                        p_ch.hp = max(0, p_ch.hp - cfg.fumble_self_damage)
                        for cond_name, cdef in cond_reg._defs.items():
                            if cdef.trigger == "fumble":
                                _apply_condition(cond_name, p, p_ch, chars, events, round_num, cond_reg)
                        if p_ch.hp <= 0 and not p_ch.is_ko:
                            _ko(p, p_ch, chars, events, round_num)


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
    hazard_id = action.catalog_id
    hdef = haz_reg.get(hazard_id) if hazard_id in haz_reg else None

    # Snapshot occupants before any forced move mutates zones.
    occupants = [c for c in chars.values() if not c.is_ko and c.zone_id == target_zone]
    events.append(
        Event(
            id=_eid("grem"),
            type=EventType.GREMLIN_HAZARD,
            round=round_num,
            player_id=pid,
            data={
                "hazard_id": hazard_id,
                "zone": target_zone,
                "condition": hdef.applies_condition if hdef else None,
                "forces_move": bool(hdef.forces_move) if hdef else False,
                "affected": [c.player_id for c in occupants],
                "adaptation_note": action.adaptation_note,
            },
        )
    )
    if hdef is None:
        return

    for occ in occupants:
        if hdef.applies_condition:
            _apply_condition(
                hdef.applies_condition, occ.player_id, occ, chars, events, round_num, cond_reg
            )
        if hdef.forces_move:
            adj = zone_reg.adjacent(target_zone)
            if adj:
                _do_move(occ.player_id, occ, rng.choice(adj), chars, events, round_num, zone_reg)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _living(chars: dict[str, Character]) -> dict[str, Character]:
    return {pid: ch for pid, ch in chars.items() if not ch.is_ko}


def _eid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _get_stat(ch: Character, stat_name: str) -> int:
    if stat_name == "power":
        return ch.stats.power
    if stat_name == "weird":
        return ch.stats.weird
    if stat_name == "speed":
        return ch.stats.speed
    return 0


def _creativity_bonus(tier: int, cfg: Balance) -> int:
    return [cfg.creativity_tier_0, cfg.creativity_tier_1, cfg.creativity_tier_2, cfg.creativity_tier_3][min(tier, 3)]


def _cond_mod(ch: Character, attr: str, cond_reg: ConditionRegistry) -> int:
    total = 0
    for cond_name in ch.conditions:
        if cond_name in cond_reg:
            cdef = cond_reg.get(cond_name)
            total += getattr(cdef.modifiers, attr, 0)
    return total


def _effective_ac(
    target: Character,
    attacker: Character,
    is_ranged: bool,
    cond_reg: ConditionRegistry,
    zone_reg: ZoneRegistry,
    action: ClassifiedAction,
    cfg: Balance | None = None,
) -> int:
    ac = target.ac
    ac += _cond_mod(target, "ac", cond_reg)
    ac += zone_reg.modifier(target.zone_id, "ac_bonus")
    if cfg is not None:
        ac += target.banked_actions * cfg.banked_ac_per_action
    if is_ranged:
        ac += zone_reg.modifier(target.zone_id, "ranged_ac_bonus")
        for cond_name in target.conditions:
            if cond_name in cond_reg:
                cdef = cond_reg.get(cond_name)
                ac += cdef.ac_bonus_vs_ranged
    return ac


def _resolve_targets(
    action: ClassifiedAction,
    move: MoveDef,
    attacker: Character,
    chars: dict[str, Character],
    living: dict[str, Character],
    zone_reg: ZoneRegistry,
    cond_reg: ConditionRegistry,
    state: GameState,
    rng: Dice,
) -> list[str]:
    t = move.target

    # Confused: the victim's next offensive action strikes ONE random creature
    # (friend, foe, or self-adjacent) instead of the intended target.
    if "confused" in attacker.conditions and t in (
        "single_enemy", "zone_all", "line_all_zones",
    ):
        pool = [pid for pid in living if pid != action.player_id]
        return [rng.choice(pool)] if pool else []

    if t == "self":
        return [action.player_id]

    if t in ("ally", "ally_or_self"):
        # Use declared targets from action if valid
        valid = [
            tid for tid in action.targets
            if tid in living and tid != action.player_id
        ]
        if not valid and t == "ally_or_self":
            valid = [action.player_id]
        return valid[:1]

    if t == "single_enemy":
        # Use declared targets; fall back to nearest enemy if stale
        valid = [
            tid for tid in action.targets
            if tid in living and not _same_team_bool(action.player_id, tid, state)
        ]
        if not valid:
            # Stale intent: pick nearest enemy in the same zone, else any
            attacker_zone = attacker.zone_id
            enemies = [
                pid for pid in living
                if pid != action.player_id
                and not _same_team_bool(action.player_id, pid, state)
            ]
            same_zone = [pid for pid in enemies if chars[pid].zone_id == attacker_zone]
            valid = same_zone[:1] or enemies[:1]
        # Melee range check
        if move.range == "same_zone":
            valid = [
                tid for tid in valid
                if chars[tid].zone_id == attacker.zone_id
            ]
        return valid[:1]

    if t == "zone_all":
        # Everyone in the target zone except the caster. With friendly_fire the
        # blast also catches the caster's own team (that's the trade-off); without
        # it, only enemies are hit.
        target_zone = action.move_to or attacker.zone_id
        if action.targets:
            first = chars.get(action.targets[0])
            if first:
                target_zone = first.zone_id
        return [
            pid for pid, ch in living.items()
            if ch.zone_id == target_zone
            and pid != action.player_id
            and (move.friendly_fire or not _same_team_bool(action.player_id, pid, state))
        ]

    if t == "line_all_zones":
        # One enemy per zone the line crosses — never allies, never the caster.
        by_zone: dict[str, list[str]] = {}
        for pid, ch in living.items():
            if pid == action.player_id:
                continue
            if _same_team_bool(action.player_id, pid, state):
                continue
            by_zone.setdefault(ch.zone_id, []).append(pid)
        return [sorted(by_zone[z])[0] for z in sorted(by_zone)]

    if t == "zone":
        # Hazard targeting a zone — no character targets
        return []

    return []


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
    chars: dict[str, Character],
    events: list[Event],
    round_num: int,
    cond_reg: ConditionRegistry,
) -> None:
    cdef = cond_reg.get(cond_name)
    # Check immunities: does target already have an immunity condition?
    for existing in list(target.conditions.keys()):
        if existing in cond_reg:
            existing_def = cond_reg.get(existing)
            if cond_name in existing_def.immunities:
                return  # immune

    # Check if applied condition cures existing ones (cure_tags)
    for tag in cdef.cure_tags:
        for existing in list(target.conditions.keys()):
            if existing == tag or existing in cond_reg and tag in cond_reg.get(existing).immunities:
                del target.conditions[existing]

    target.conditions[cond_name] = cdef.duration
    events.append(
        Event(
            id=_eid("cond"),
            type=EventType.CONDITION_APPLIED,
            round=round_num,
            player_id=target_id,
            data={"condition": cond_name, "duration": cdef.duration},
        )
    )


def _do_move(
    pid: str,
    ch: Character,
    target_zone: str,
    chars: dict[str, Character],
    events: list[Event],
    round_num: int,
    zone_reg: ZoneRegistry,
) -> None:
    old_zone = ch.zone_id
    ch.zone_id = target_zone
    events.append(
        Event(
            id=_eid("mv"),
            type=EventType.MOVED,
            round=round_num,
            player_id=pid,
            data={"from": old_zone, "to": target_zone},
        )
    )


def _ko(
    pid: str,
    ch: Character,
    chars: dict[str, Character],
    events: list[Event],
    round_num: int,
) -> None:
    ch.hp = 0
    ch.is_ko = True
    ch.is_gremlin = True
    ch.conditions = {}
    # Drop any pending transform-revert bookkeeping — a gremlin has no stats to
    # restore, and leaving it set would break the "pre_transform_stats implies an
    # active `transformed` condition" invariant.
    ch.pre_transform_stats = None
    events.append(
        Event(
            id=_eid("ko"),
            type=EventType.KO,
            round=round_num,
            player_id=pid,
            data={},
        )
    )


def _underdog_bonus(
    pid: str,
    chars: dict[str, Character],
    state: GameState,
    cfg: Balance,
) -> int:
    if not cfg.underdog_enabled:
        return 0
    if not state.teams:
        return 0
    # Find this player's team
    my_team = None
    for team in state.teams:
        if pid in team.player_ids:
            my_team = team
            break
    if not my_team:
        return 0
    # Compute HP share for each team
    team_hp: dict[str, int] = {}
    team_max_hp: dict[str, int] = {}
    for team in state.teams:
        team_hp[team.id] = sum(chars[p].hp for p in team.player_ids if p in chars and not chars[p].is_ko)
        team_max_hp[team.id] = sum(chars[p].max_hp for p in team.player_ids if p in chars)
    # Compare my team's HP share to opponents'
    my_share = team_hp.get(my_team.id, 0)
    opp_shares = [hp for tid, hp in team_hp.items() if tid != my_team.id]
    if not opp_shares:
        return 0
    best_opp = max(opp_shares)
    threshold_hp = cfg.underdog_hp_share_threshold * (team_max_hp.get(my_team.id, 1) / max(1, len(my_team.player_ids)))
    if best_opp - my_share >= threshold_hp:
        return cfg.underdog_attack_bonus
    return 0


def _check_victory(chars: dict[str, Character], teams: list[Team]) -> str | None:
    if not teams:
        return None
    for team in teams:
        members = [pid for pid in team.player_ids if pid in chars]
        if all(chars[pid].is_ko for pid in members) and members:
            # Find the other team
            for other in teams:
                if other.id != team.id:
                    return other.id
    return None
