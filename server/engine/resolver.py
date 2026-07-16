"""Pure game engine — resolves one round of COMBAT V4 actions into events.

Resolution (GAME_DESIGN §5). There is **no AC and no attack roll**: a selected
move always takes effect. Only the magnitude varies.

    effect = move's damage/heal formula + creativity bonus (+0/+1/+3/+5)
             + zone/underdog/sudden-death damage riders
    dodge  = the target's passive Speed check (5% x Speed, cap 30%), rolled per
             incoming hit — the ONLY thing that can negate a hit
    shield = if the target is covered, subtract `4 + POW` mitigation, then a
             10% x POW chance to reflect the mitigated amount at the attacker;
             resolved AFTER dodge
    backfire = WILD CARD only (15%): self-damage, no target effects

Spike moments come from the drawing, not the dice: creativity tier 3 lands as
result="devastating" (replay + stinger + gold log line), replacing v2's crit.

SHIELD's mitigation covers every ally in the caster's zone and is round-scoped:
it exists only inside this function's round state and vanishes when the round
ends — there is no condition system (v2.1). Because it applies when the caster
acts, a slow shielder protects nobody from faster attackers; that is intended
(GAME_DESIGN §12) and the initiative rail shows the couch why.

Dice consumption order (deterministic, seed-stable):
    1. Initiative tie-break shuffles (per tied-speed group, speed desc).
    2. For each actor (initiative order):
       a. WILD CARD backfire check, then its self-damage roll if it fires
       b. one shared damage-formula roll if the move has any target
       c. per target (sorted): dodge check, then reflect check if mitigated
       d. heal-formula roll (RALLY)
    3. Gremlin hazard zone choice, then damage roll / forced-move choices.
    Note: Dice.chance() short-circuits at p<=0 and p>=1 without consuming a
    draw, so a Speed-0 target's dodge check never shifts the stream.
"""

from __future__ import annotations

import uuid

from server.config import Balance, MoveDef
from server.engine.dice import Dice, formula_parts
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


class _RoundBuffs:
    """Round-scoped defensive state: SHIELD's mitigation and reflect rider,
    keyed by protected player. Created fresh each round — nothing persists."""

    def __init__(self) -> None:
        # protected pid → (mitigation amount, reflect chance, shielder pid)
        self.mitigate: dict[str, tuple[int, float, str]] = {}


def resolve_round(
    state: GameState,
    actions: list[ClassifiedAction],
    rng: Dice,
    cfg: Balance,
) -> RoundResult:
    """No I/O, no AI, no globals. Same inputs → same outputs.

    Handles: initiative, the eight v4 moves, combo creativity escalation, damage
    and healing (every move lands), passive dodge, round-scoped SHIELD
    mitigation/reflect, WILD CARD backfire, absolute movement, KO → Gremlin
    conversion, victory detection, sudden death.

    Returns an ordered list of Events (input to the narrator) and the new GameState.
    """
    zone_reg = ZoneRegistry()
    move_reg = MoveRegistry()
    haz_reg = HazardRegistry()

    chars: dict[str, Character] = {
        pid: ch.model_copy(deep=True) for pid, ch in state.characters.items()
    }
    events: list[Event] = []
    round_num = state.round
    buffs = _RoundBuffs()

    # Arena Gremlins that were already KO'd coming into this round — a fighter
    # KO'd *this* round only starts dropping hazards next round (GAME_DESIGN §10).
    start_gremlins = [pid for pid, ch in chars.items() if ch.is_gremlin]

    # ------------------------------------------------------------------
    # 1. Initiative order (speed desc, seeded-roll tiebreak)
    # ------------------------------------------------------------------
    order = _initiative_order(_living(chars), rng)

    # ------------------------------------------------------------------
    # 2. Build action map (default missing actions to a stumble)
    # ------------------------------------------------------------------
    action_map: dict[str, ClassifiedAction] = {a.player_id: a for a in actions}

    # ------------------------------------------------------------------
    # 3. Process actions in initiative order
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
                        buffs, zone_reg, move_reg, state)

    # ------------------------------------------------------------------
    # 3b. Arena Gremlins drop hazards (GAME_DESIGN §10)
    # ------------------------------------------------------------------
    # Runs after the round's combat so a hazard sets up the arena for the *next*
    # round rather than retroactively changing fights already resolved. Only
    # gremlins present at round start act, in stable player_id order.
    for pid in sorted(start_gremlins):
        action = action_map.get(pid)
        if action is not None:
            _resolve_gremlin(
                pid, action, chars, events, round_num, rng, zone_reg, haz_reg
            )

    # ------------------------------------------------------------------
    # 4. Record last combat move (no-repeat rule; movement is exempt)
    # ------------------------------------------------------------------
    for pid, action in action_map.items():
        ch = chars.get(pid)
        if ch is None or ch.is_ko:
            continue
        if action.move_id in move_reg and not move_reg.get(action.move_id).is_movement:
            ch.last_move_id = action.move_id

    # ------------------------------------------------------------------
    # 5. Victory / sudden death
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


def _initiative_order(living: dict[str, Character], rng: Dice) -> list[str]:
    # Group by speed (descending); ties broken by seeded roll (§5).
    groups: dict[int, list[str]] = {}
    for pid, ch in living.items():
        groups.setdefault(ch.stats.speed, []).append(pid)

    order: list[str] = []
    for spd in sorted(groups.keys(), reverse=True):
        tier = sorted(groups[spd])   # stable base order before the seeded shuffle
        if len(tier) > 1:
            rng.shuffle(tier)
        order.extend(tier)
    return order


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
    buffs: _RoundBuffs,
    zone_reg: ZoneRegistry,
    move_reg: MoveRegistry,
    state: GameState,
) -> None:
    attacker = chars[pid]
    move = move_reg.get(action.move_id)

    # ------ Movement (◀/▶: absolute, edge-checked) ------
    if move.is_movement:
        dest = zone_reg.step(attacker.zone_id, move.move)
        if dest is None:
            # Server disables edge-illegal buttons; defend anyway.
            events.append(Event(id=_eid("stmb"), type=EventType.STUMBLE,
                                round=round_num, player_id=pid,
                                data={"reason": "arena_edge"}))
            return
        _do_move(pid, attacker, dest, events, round_num)
        return

    # ------ Support moves (SHIELD / RALLY) ------
    if move.mitigate or move.heal:
        _resolve_support(pid, action, move, attacker, chars, events, round_num,
                         rng, cfg, buffs, state)
        return

    # ------ Attack moves (SMASH / BLAST / SHOOT / WILD) ------
    targets, auto_stepped = _resolve_targets(
        pid, action, move, attacker, chars, events, round_num, zone_reg, state,
    )
    if not targets:
        events.append(Event(
            id=_eid("atk"), type=EventType.ATTACK_RESOLVED, round=round_num,
            player_id=pid,
            data={"result": "no_target", "move_id": action.move_id,
                  "adaptation_note": action.adaptation_note},
        ))
        return

    tier = _effective_tier(action, cfg)

    # WILD CARD is the only move that can turn on its caster (§4.1). Checked
    # before any target math: a backfire has no target effects at all.
    if move.backfire_chance and rng.chance(move.backfire_chance):
        _apply_backfire(pid, action, attacker, events, round_num, rng, cfg)
        return

    # One shared damage roll: the swarm stings everyone alike (BLAST). Dodge is
    # still checked per target, so victims still differ.
    rolled = rng.roll_formula(move.damage or "0", _stat_env(attacker))
    attacker_riders = (
        int(zone_reg.modifier(attacker.zone_id, "damage_bonus"))
        + _underdog_bonus(pid, chars, state, cfg)
        + (cfg.sudden_death_damage_bonus if state.sudden_death else 0)
    )

    for target_id in targets:
        target = chars.get(target_id)
        if target is None or target.is_ko:
            continue

        # 1. DODGE — the only thing that negates a hit.
        if rng.chance(_dodge_chance(target, cfg, zone_reg)):
            events.append(Event(
                id=_eid("atk"), type=EventType.ATTACK_RESOLVED, round=round_num,
                player_id=pid, target_id=target_id,
                data={"result": "dodge", "move_id": action.move_id,
                      "creativity_tier": tier,
                      "adaptation_note": action.adaptation_note},
            ))
            continue

        riders = attacker_riders + int(
            zone_reg.modifier(target.zone_id, "incoming_damage_bonus")
        )
        # The arithmetic, split for the host's plain-language readout (§13).
        # `raw` is the addition's total — reductions below get their own line
        # rather than rewriting it.
        breakdown = _breakdown(move, attacker, rolled, tier, riders, cfg)
        dmg = max(0, breakdown["raw"])

        # SHOOT's point-blank penalty: half damage (rounded up) in the same zone.
        point_blank = (
            move.same_zone_penalty == "half"
            and target.zone_id == attacker.zone_id
        )
        if point_blank:
            dmg = (dmg + 1) // 2

        # 2. SHIELD — flat mitigation, then a chance to bounce it back.
        blocked = 0
        shielder_id = ""
        cover = buffs.mitigate.get(target_id)
        if cover is not None:
            amount, reflect_chance, shielder_id = cover
            blocked = min(amount, dmg)
            dmg -= blocked

        target.hp = max(0, target.hp - dmg)
        data = {
            "result": "devastating" if tier >= 3 else "hit",
            "move_id": action.move_id, "damage": dmg,
            "point_blank": point_blank, "creativity_tier": tier,
            "adaptation_note": action.adaptation_note,
            **breakdown,
        }
        if blocked:
            data["blocked"] = blocked
            data["shielder_id"] = shielder_id
        events.append(Event(
            id=_eid("atk"), type=EventType.ATTACK_RESOLVED, round=round_num,
            player_id=pid, target_id=target_id, data=data,
        ))

        if target.hp <= 0 and not target.is_ko:
            _ko(target_id, target, events, round_num)

        if blocked:
            _maybe_reflect(pid, attacker, target_id, blocked, reflect_chance,
                           events, round_num, rng)

    del auto_stepped  # informational only; the MOVED event already tells the story


def _apply_backfire(
    pid: str,
    action: ClassifiedAction,
    attacker: Character,
    events: list[Event],
    round_num: int,
    rng: Dice,
    cfg: Balance,
) -> None:
    """WILD CARD's opt-in chaos: the only self-damage in the game. Creativity
    does not soften it — the comedy is the point (§4.1)."""
    dmg = rng.roll(cfg.wild_backfire_damage or "0")
    attacker.hp = max(0, attacker.hp - dmg)
    events.append(Event(
        id=_eid("atk"), type=EventType.ATTACK_RESOLVED, round=round_num,
        player_id=pid, target_id=action.target_id,
        data={"result": "backfire", "move_id": action.move_id,
              "self_damage": dmg,
              "adaptation_note": action.adaptation_note},
    ))
    if attacker.hp <= 0 and not attacker.is_ko:
        _ko(pid, attacker, events, round_num)


def _maybe_reflect(
    pid: str,
    attacker: Character,
    target_id: str,
    blocked: int,
    reflect_chance: float,
    events: list[Event],
    round_num: int,
    rng: Dice,
) -> None:
    """SHIELD's rider: a 10% x POW chance that the damage the shield swallowed
    bounces straight back at the attacker (§4.1). Tanks punish attackers."""
    if blocked <= 0 or not rng.chance(reflect_chance):
        return
    attacker.hp = max(0, attacker.hp - blocked)
    events.append(Event(
        id=_eid("rfl"), type=EventType.ATTACK_RESOLVED, round=round_num,
        player_id=target_id, target_id=pid,
        data={"result": "reflect", "move_id": "shield", "damage": blocked},
    ))
    if attacker.hp <= 0 and not attacker.is_ko:
        _ko(pid, attacker, events, round_num)


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
    cfg: Balance,
    buffs: _RoundBuffs,
    state: GameState,
) -> None:
    # SHIELD: protect every living teammate (and self) in the caster's zone.
    if move.mitigate:
        amount = rng.roll_formula(move.mitigate, _stat_env(attacker))
        reflect_chance = move.reflect_chance_per_power * attacker.stats.power
        protected = sorted(
            p for p, c in chars.items()
            if not c.is_ko and c.zone_id == attacker.zone_id
            and (p == pid or _same_team_bool(pid, p, state))
        )
        for p in protected:
            buffs.mitigate[p] = (amount, reflect_chance, pid)
        events.append(Event(
            id=_eid("shl"), type=EventType.SHIELDED, round=round_num,
            player_id=pid,
            data={"protected": protected, "mitigate": amount},
        ))
        return

    # RALLY (ally_or_self): the tapped target if it's a living teammate, else self.
    target_id = pid
    if (
        action.target_id
        and action.target_id in chars
        and not chars[action.target_id].is_ko
        and _same_team_bool(pid, action.target_id, state)
    ):
        target_id = action.target_id
    target = chars[target_id]

    if state.sudden_death:
        events.append(Event(
            id=_eid("heal"), type=EventType.HEALED, round=round_num,
            player_id=pid, target_id=target_id,
            data={"amount": 0, "blocked": "sudden_death"},
        ))
        return

    # A better drawing heals more — the creativity bonus is the medicine (§8).
    tier = _effective_tier(action, cfg)
    rolled = rng.roll_formula(move.heal or "0", _stat_env(attacker))
    breakdown = _breakdown(move, attacker, rolled, tier, 0, cfg)
    amount = max(0, breakdown["raw"])
    target.hp = min(target.max_hp, target.hp + amount)
    events.append(Event(
        id=_eid("heal"), type=EventType.HEALED, round=round_num,
        player_id=pid, target_id=target_id,
        data={"amount": amount, "creativity_tier": tier, **breakdown},
    ))


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
    zone_reg: ZoneRegistry,
    state: GameState,
) -> tuple[list[str], bool]:
    """The attack's target list. Handles dead-target redirection (intent
    adaptation §9), SMASH's auto-step, and BLAST's zone.
    Returns (target_ids, auto_stepped)."""
    living = _living(chars)

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
    haz_reg: HazardRegistry,
) -> None:
    """A gremlin drops a hazard on a random zone; every living fighter standing
    there suffers its effect — zone damage or a forced move (v2.1: hazards are
    damage-or-push only). The hazard id comes from the gremlin's classified
    drawing (config/hazards.yaml), so adding a hazard type is a YAML-only change."""
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
            "damage_spec": hdef.damage if hdef else "",
            "forces_move": bool(hdef.forces_move) if hdef else False,
            "affected": [c.player_id for c in occupants],
            "adaptation_note": action.adaptation_note,
        },
    ))
    if hdef is None or not occupants:
        return

    # One shared damage roll for the whole zone — the swarm stings everyone alike.
    if hdef.damage:
        dmg = rng.roll(hdef.damage)
        for occ in occupants:
            occ.hp = max(0, occ.hp - dmg)
            events.append(Event(
                id=_eid("hzd"), type=EventType.ATTACK_RESOLVED, round=round_num,
                player_id=pid, target_id=occ.player_id,
                data={"result": "hazard", "move_id": hazard_id, "damage": dmg},
            ))
            if occ.hp <= 0 and not occ.is_ko:
                _ko(occ.player_id, occ, events, round_num)

    if hdef.forces_move:
        for occ in occupants:
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
    to zero out to isolate the stat's contribution — for SHOOT's
    "max(speed,weird)" that's BOTH, since zeroing only Speed would just leave
    Weird as the max and understate the term.

    SHOOT names whichever is higher (Speed on a tie, so the readout names the
    same stat the initiative rail is ordered by).
    """
    if move_stat == "max(speed,weird)":
        name = "weird" if ch.stats.weird > ch.stats.speed else "speed"
        return name, {"SPD", "WRD"}
    if move_stat in _STAT_KEYS:
        return move_stat, {_STAT_KEYS[move_stat]}
    return "", set()


def _breakdown(
    move: MoveDef,
    attacker: Character,
    rolled: int,
    tier: int,
    riders: int,
    cfg: Balance,
) -> dict:
    """Split a rolled effect into the terms the host readout adds up (§13):

        🎯 SHOOT → 🎲 3 + ⚡ Speed 5 + ⭐⭐ Creative 3 = 11 damage

    The formula's flat mod already folds in the stat, so re-resolving it with
    that stat's inputs zeroed isolates the stat term without the catalog having
    to declare it separately. `dice` carries the move's own constant along with
    the roll (SMASH's "+2" rides inside 🎲) — that keeps the line to one
    addition of three readable terms, as both §13 examples show.
    """
    env = _stat_env(attacker)
    spec = move.damage or move.heal or "0"
    _, _, mod = formula_parts(spec, env)
    stat_name, stat_keys = headline_stat(move.stat, attacker)
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


def _dodge_chance(target: Character, cfg: Balance, zone_reg: ZoneRegistry) -> float:
    """Passive, Speed-driven, capped — the one way a hit is negated (§5)."""
    chance = cfg.dodge_per_speed * target.stats.speed
    chance += zone_reg.modifier(target.zone_id, "dodge_bonus")
    chance -= zone_reg.modifier(target.zone_id, "incoming_dodge_penalty")
    return min(chance, cfg.dodge_cap)


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
