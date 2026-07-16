"""Resolver tests — unit, golden, and property tests (COMBAT V4).

Golden test (test_v4_golden):
    seed 42, the GAME_DESIGN.md §12 2v2 fixture (HP = 28 + 2*POW + WRD):
    Stabby (P1/S5/W3, HP 33) + Gerald (P3/S1/W5, HP 39)
    vs Lawnmower (P6/S2/W1, HP 41) + Blob (P0/S3/W6, HP 34).

V4 has no AC and no attack roll: every move lands, and only the target's
passive dodge or a SHIELD's mitigation can reduce it.

Determinism aid: `Dice.chance()` short-circuits at p<=0 without consuming a
draw, so giving a target Speed 0 switches its dodge off *without* shifting the
dice stream. Most unit tests below lean on that to isolate one mechanic.
"""

from __future__ import annotations

import pytest

from server.config import load_balance
from server.engine.dice import Dice, describe_formula
from server.engine.models import (
    Character,
    ClassifiedAction,
    GameState,
    Stats,
    Team,
)
from server.engine.resolver import resolve_round

CFG = load_balance()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _char(
    player_id: str,
    name: str,
    power: int,
    speed: int,
    weird: int,
    zone: str,
    hp: int | None = None,
) -> Character:
    max_hp = CFG.hp_base + CFG.hp_per_power * power + CFG.hp_per_weird * weird
    return Character(
        player_id=player_id,
        name=name,
        stats=Stats(power=power, speed=speed, weird=weird),
        hp=max_hp if hp is None else hp,
        max_hp=max_hp,
        zone_id=zone,
    )


def _state(chars: list[Character], teams: list[Team], round_num: int = 1) -> GameState:
    return GameState(
        room_id="TEST",
        round=round_num,
        characters={ch.player_id: ch for ch in chars},
        teams=teams,
    )


_TEAMS = [
    Team(id="team_a", name="Team A", color="pink", player_ids=["p1", "p4"]),
    Team(id="team_b", name="Team B", color="blue", player_ids=["p2", "p3"]),
]


def _duel(attacker: Character, defender: Character):
    attacker.player_id = "atk"
    defender.player_id = "def"
    return _state([attacker, defender],
                  [Team(id="team_a", name="A", color="p", player_ids=["atk"]),
                   Team(id="team_b", name="B", color="b", player_ids=["def"])])


def _smash_duel(attacker_power=6, defender_speed=0):
    """A duel where the defender defaults to Speed 0 — dodge off, damage exact."""
    atk = _char("atk", "Atk", power=attacker_power, speed=4, weird=0, zone="frontline")
    dfn = _char("def", "Def", power=2, speed=defender_speed, weird=2, zone="frontline")
    return _duel(atk, dfn)


def _attack_ev(result, move_id: str):
    return next(e for e in result.events if e.data.get("move_id") == move_id)


# ---------------------------------------------------------------------------
# §12 Golden test — seed 42, the COMBAT V4 worked round
# ---------------------------------------------------------------------------
#
# Taps: Stabby SHOOT at Blob (creativity 2 = +3); Blob BLAST on the frontline
#       (creativity 1 = +1); Lawnmower SMASH on Gerald (auto-step, creativity 0);
#       Gerald SHIELDs his zone.
#
# Initiative (pure speed, no ties): Stabby(5) → Blob(3) → Lawnmower(2) → Gerald(1).
#
# Seed-42 dice (§12's example numbers are illustrative; the test asserts the
# actual seeded rolls, exactly like the v2 golden test did). Every move LANDS —
# the only question is magnitude, dodge, and mitigation:
#   Gerald    SHIELD: applied FIRST, in the round-start pre-pass (balance lever) —
#             4 + POW 3 = 7 mitigation over his zone (just himself; Stabby is up
#             front). It now covers him for the whole round regardless of his
#             Speed-1 initiative — though nothing tests it here (his dodge stops
#             the only hit aimed at him).
#   Stabby    SHOOT at Blob: 2d4=2 + WRD 3 + 3 creativity = 8 (ranged keys off
#             Weird only now). Blob's Speed-3 dodge (21%) does not fire → 34-8 = 26.
#   Blob      BLAST on the frontline, hitting Stabby AND its own teammate
#             Lawnmower (friendly fire is BLAST's cost):
#               - Stabby's Speed-5 dodge (35%) FIRES → she takes nothing.
#               - Lawnmower: 1d6=2 + WRD 6 + 1 creativity = 9 → 41 - 9 = 32.
#   Lawnmower SMASH on Gerald: auto-steps frontline → glitter_back, then
#             Gerald's Speed-1 dodge (7%) FIRES — a long shot, and exactly the
#             defensive highlight v4 wants ("SHE'S NOT EVEN THERE!").
#   (Dodge rates are dodge_per_speed 0.07 x Speed — Speed's rebalanced job.)


def _golden_chars() -> list[Character]:
    return [
        _char("p1", "Princess Stabby", power=1, speed=5, weird=3, zone="frontline"),
        _char("p2", "The Blob", power=0, speed=3, weird=6, zone="thunder_back"),
        _char("p3", "Sir Lawnmower", power=6, speed=2, weird=1, zone="frontline"),
        _char("p4", "Gerald", power=3, speed=1, weird=5, zone="glitter_back"),
    ]


def _golden_actions() -> list[ClassifiedAction]:
    return [
        ClassifiedAction(player_id="p1", move_id="shoot", target_id="p2",
                         creativity_tier=2),
        ClassifiedAction(player_id="p2", move_id="blast", target_id="p1",
                         creativity_tier=1),
        ClassifiedAction(player_id="p3", move_id="smash", target_id="p4"),
        ClassifiedAction(player_id="p4", move_id="shield"),
    ]


def test_v4_golden():
    """seed=42, §12 fixture → deterministic HP values (see narrative above)."""
    state = _state(_golden_chars(), _TEAMS, round_num=1)
    result = resolve_round(state, _golden_actions(), Dice(seed=42), CFG)
    chars = result.new_state.characters

    assert chars["p1"].hp == 33, f"Stabby: got {chars['p1'].hp}"
    assert chars["p2"].hp == 26, f"Blob: got {chars['p2'].hp}"
    assert chars["p3"].hp == 32, f"Lawnmower: got {chars['p3'].hp}"
    assert chars["p4"].hp == 39, f"Gerald: got {chars['p4'].hp}"

    # Derived HP straight from §12: 28 + 2*POW + WRD. There is no AC in v4.
    assert chars["p1"].max_hp == 33
    assert chars["p2"].max_hp == 34
    assert chars["p3"].max_hp == 41
    assert chars["p4"].max_hp == 39

    # Initiative = pure Speed here.
    assert result.initiative_order == ["p1", "p2", "p3", "p4"]

    # Stabby's SHOOT keys off Weird now, and its readout terms add up to the
    # damage that landed (2d4=2 + Weird 3 + Creative 3 = 8).
    shoot = _attack_ev(result, "shoot")
    assert shoot.data["result"] == "hit" and shoot.data["damage"] == 8
    assert shoot.data["stat"] == "weird" and shoot.data["stat_value"] == 3
    assert shoot.data["creativity_bonus"] == CFG.creativity_tier_2
    assert (shoot.data["dice"] + shoot.data["stat_value"]
            + shoot.data["creativity_bonus"]) == shoot.data["raw"] == 8

    # Lawnmower auto-stepped to Gerald's zone before swinging.
    assert chars["p3"].zone_id == "glitter_back"
    moved = [e for e in result.events if e.type.value == "moved" and e.player_id == "p3"]
    assert moved and moved[0].data["to"] == "glitter_back"

    # Gerald's Speed-1 dodge fired against the SMASH — the only thing in v4 that
    # can negate a hit outright.
    smash = _attack_ev(result, "smash")
    assert smash.data["result"] == "dodge"
    assert chars["p4"].hp == chars["p4"].max_hp

    # Gerald's zone-wide shield landed in the round-start pre-pass, covering only
    # himself (Stabby is up front; Lawnmower is a foe).
    shielded = next(e for e in result.events if e.type.value == "shielded")
    assert shielded.player_id == "p4"
    assert shielded.data["protected"] == ["p4"]
    assert shielded.data["mitigate"] == 4 + 3      # 4 + POW

    # BLAST hit EVERYONE in the zone — including Blob's own teammate.
    blast_evs = [e for e in result.events
                 if e.type.value == "attack_resolved" and e.data.get("move_id") == "blast"]
    assert {e.target_id for e in blast_evs} == {"p1", "p3"}
    # Stabby's Speed-5 dodge saved her; Lawnmower ate it.
    assert {e.target_id: e.data["result"] for e in blast_evs} == {
        "p1": "dodge", "p3": "hit"}

    # No-repeat bookkeeping: every fighter's combat move was recorded.
    assert chars["p1"].last_move_id == "shoot"
    assert chars["p4"].last_move_id == "shield"

    assert not any(ch.is_ko for ch in chars.values())


# ---------------------------------------------------------------------------
# Unit: initiative
# ---------------------------------------------------------------------------


def test_initiative_order_speed():
    chars = [
        _char("slow", "Slow", power=2, speed=1, weird=2, zone="frontline"),
        _char("fast", "Fast", power=2, speed=6, weird=2, zone="frontline"),
        _char("mid", "Mid", power=2, speed=3, weird=2, zone="frontline"),
    ]
    teams = [Team(id="team_a", name="A", color="p", player_ids=["slow", "fast"]),
             Team(id="team_b", name="B", color="b", player_ids=["mid"])]
    state = _state(chars, teams)
    result = resolve_round(state, [], Dice(seed=1), CFG)
    assert result.initiative_order == ["fast", "mid", "slow"]


def test_initiative_tie_broken_by_seeded_roll():
    """Same speed → order comes from the seeded shuffle, and is stable per seed."""
    def order_for(seed: int) -> list[str]:
        chars = [
            _char("a", "A", power=2, speed=3, weird=2, zone="frontline"),
            _char("b", "B", power=2, speed=3, weird=2, zone="frontline"),
            _char("c", "C", power=2, speed=3, weird=2, zone="frontline"),
        ]
        teams = [Team(id="team_a", name="A", color="p", player_ids=["a", "b"]),
                 Team(id="team_b", name="B", color="b", player_ids=["c"])]
        return resolve_round(_state(chars, teams), [], Dice(seed=seed), CFG).initiative_order

    assert sorted(order_for(5)) == ["a", "b", "c"]
    assert order_for(5) == order_for(5)          # deterministic
    assert any(order_for(s) != order_for(5) for s in range(30))   # actually shuffles


def test_initiative_drops_ko_and_gremlins():
    chars = _golden_chars()
    chars[1].is_ko = True
    chars[1].is_gremlin = True
    state = _state(chars, _TEAMS, round_num=1)
    result = resolve_round(state, _golden_actions(), Dice(seed=42), CFG)
    assert "p2" not in result.initiative_order


# ---------------------------------------------------------------------------
# Unit: V4 resolution — every move lands; dodge is the only negation
# ---------------------------------------------------------------------------


def test_every_move_lands_there_is_no_miss():
    """The v4 headline: a selected move always takes effect. Across many seeds a
    Speed-0 target is never missed — the only non-hit results are structural."""
    for seed in range(60):
        state = _smash_duel(attacker_power=0, defender_speed=0)
        actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
        result = resolve_round(state, actions, Dice(seed=seed), CFG)
        ev = _attack_ev(result, "smash")
        assert ev.data["result"] == "hit"
        assert ev.data["damage"] > 0
    assert not hasattr(Dice(seed=0), "two_d6"), "v4 has no attack roll"


def test_speed_zero_never_dodges():
    for seed in range(60):
        state = _smash_duel(attacker_power=2, defender_speed=0)
        actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
        result = resolve_round(state, actions, Dice(seed=seed), CFG)
        assert _attack_ev(result, "smash").data["result"] != "dodge"


def test_dodge_negates_the_hit_entirely():
    """A dodge takes zero damage — not reduced, negated (§5)."""
    dodged = None
    for seed in range(200):
        state = _smash_duel(attacker_power=6, defender_speed=6)
        actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
        result = resolve_round(state, actions, Dice(seed=seed), CFG)
        ev = _attack_ev(result, "smash")
        if ev.data["result"] == "dodge":
            dodged = result
            break
    assert dodged is not None, "a Speed-6 target should dodge within 200 seeds"
    assert "damage" not in _attack_ev(dodged, "smash").data
    dfn = dodged.new_state.characters["def"]
    assert dfn.hp == dfn.max_hp


def test_dodge_rate_scales_with_speed_and_honors_the_cap():
    """5% x Speed, capped at dodge_cap — measured through the real resolver."""
    def dodge_rate(speed: int, n: int = 400) -> float:
        hits = 0
        for seed in range(n):
            state = _smash_duel(attacker_power=2, defender_speed=speed)
            actions = [ClassifiedAction(player_id="atk", move_id="smash",
                                        target_id="def")]
            result = resolve_round(state, actions, Dice(seed=seed), CFG)
            if _attack_ev(result, "smash").data["result"] == "dodge":
                hits += 1
        return hits / n

    assert dodge_rate(0) == 0.0
    # Rate is dodge_per_speed x Speed (computed from config, not hardcoded).
    assert abs(dodge_rate(2) - CFG.dodge_per_speed * 2) < 0.06
    # Speed 6 (the stat ceiling) sits at/under the cap — the cap is headroom now.
    expected6 = min(CFG.dodge_per_speed * 6, CFG.dodge_cap)
    assert abs(dodge_rate(6) - expected6) < 0.06
    assert dodge_rate(6) <= CFG.dodge_cap + 0.06


# ---------------------------------------------------------------------------
# Unit: creativity, DEVASTATING, combos
# ---------------------------------------------------------------------------


def test_creativity_bonus_is_flat_and_stale_scores_zero():
    """Creativity adds a flat +0/+1/+3/+5 straight to the damage (§5, §8)."""
    def damage_at(tier: int, stale: bool = False) -> int:
        state = _smash_duel(attacker_power=2, defender_speed=0)
        actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def",
                                    creativity_tier=tier, similar_to_previous=stale)]
        result = resolve_round(state, actions, Dice(seed=5), CFG)
        return _attack_ev(result, "smash").data["damage"]

    base = damage_at(0)
    assert damage_at(1) - base == CFG.creativity_tier_1
    assert damage_at(2) - base == CFG.creativity_tier_2
    assert damage_at(3) - base == CFG.creativity_tier_3
    # A stale drawing scores creativity 0 — no extra penalty (§8).
    assert damage_at(3, stale=True) == base


def test_creativity_tier_3_is_devastating():
    """Tier 3 is v4's spike moment — the beat that replaces the crit."""
    for tier, expected in ((0, "hit"), (1, "hit"), (2, "hit"), (3, "devastating")):
        state = _smash_duel(attacker_power=2, defender_speed=0)
        actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def",
                                    creativity_tier=tier)]
        result = resolve_round(state, actions, Dice(seed=5), CFG)
        assert _attack_ev(result, "smash").data["result"] == expected


def _combo_state():
    a1 = _char("a1", "A1", power=2, speed=4, weird=0, zone="frontline")
    a2 = _char("a2", "A2", power=0, speed=3, weird=2, zone="frontline")
    foe = _char("e1", "E1", power=2, speed=0, weird=2, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["a1", "a2"]),
             Team(id="team_b", name="B", color="b", player_ids=["e1"])]
    return _state([a1, a2, foe], teams)


def test_combo_escalates_both_partners_creativity_tier():
    """v4: a combo is +1 creativity TIER for both partners, not a roll bonus (§8)."""
    plain = resolve_round(_combo_state(), [
        ClassifiedAction(player_id="a1", move_id="smash", target_id="e1",
                         creativity_tier=1),
        ClassifiedAction(player_id="a2", move_id="shoot", target_id="e1",
                         creativity_tier=1),
    ], Dice(seed=8), CFG)

    combo = resolve_round(_combo_state(), [
        ClassifiedAction(player_id="a1", move_id="smash", target_id="e1",
                         creativity_tier=1, combo_partners=["a2"],
                         combo_name="GLITTERNADO"),
        ClassifiedAction(player_id="a2", move_id="shoot", target_id="e1",
                         creativity_tier=1, combo_partners=["a1"],
                         combo_name="GLITTERNADO"),
    ], Dice(seed=8), CFG)

    combo_evs = [e for e in combo.events if e.type.value == "combo"]
    assert len(combo_evs) == 1                      # announced once per group
    assert combo_evs[0].data["combo_name"] == "GLITTERNADO"

    # Both partners gained tier 1 → 2, i.e. +1 bonus → +3. Compared on `raw`,
    # the addition's total: a2's SHOOT is point-blank here, and that halving
    # applies after creativity, so its final damage moves by less.
    step = CFG.creativity_tier_2 - CFG.creativity_tier_1
    for mid in ("smash", "shoot"):
        assert _attack_ev(combo, mid).data["creativity_tier"] == 1 + CFG.combo_tier_bonus
        assert (_attack_ev(combo, mid).data["raw"]
                - _attack_ev(plain, mid).data["raw"]) == step


def test_combo_can_escalate_a_tier_2_drawing_into_devastating():
    """§8's promise: a combo makes the DEVASTATING beat more reachable."""
    combo = resolve_round(_combo_state(), [
        ClassifiedAction(player_id="a1", move_id="smash", target_id="e1",
                         creativity_tier=2, combo_partners=["a2"], combo_name="X"),
        ClassifiedAction(player_id="a2", move_id="shoot", target_id="e1",
                         creativity_tier=2, combo_partners=["a1"], combo_name="X"),
    ], Dice(seed=8), CFG)
    assert _attack_ev(combo, "smash").data["result"] == "devastating"


def test_creativity_tier_never_exceeds_the_top_tier():
    """A tier-3 combo can't run off the end of the bonus table."""
    combo = resolve_round(_combo_state(), [
        ClassifiedAction(player_id="a1", move_id="smash", target_id="e1",
                         creativity_tier=3, combo_partners=["a2"], combo_name="X"),
        ClassifiedAction(player_id="a2", move_id="shoot", target_id="e1",
                         creativity_tier=3, combo_partners=["a1"], combo_name="X"),
    ], Dice(seed=8), CFG)
    ev = _attack_ev(combo, "smash")
    assert ev.data["creativity_tier"] == 3
    assert ev.data["creativity_bonus"] == CFG.creativity_tier_3


# ---------------------------------------------------------------------------
# Unit: the eight moves
# ---------------------------------------------------------------------------


def test_move_formulas_scale_with_stats():
    """The v4 catalog's live math, as rendered on the phone's buttons."""
    def at(spec, **stats):
        env = {"POW": 0, "SPD": 0, "WRD": 0} | stats
        return describe_formula(spec, env)

    assert at("2d4 + POW + 2", POW=0) == "2d4+2"          # SMASH floor
    assert at("2d4 + POW + 2", POW=6) == "2d4+8"          # SMASH on the brick
    assert at("2d4 + WRD", WRD=3) == "2d4+3"             # SHOOT keys off Weird
    assert at("2d4 + WRD", WRD=6) == "2d4+6"
    assert at("1d6 + WRD", WRD=6) == "1d6+6"              # BLAST
    assert at("2d6 + 2*WRD + 2", WRD=4) == "2d6+10"       # RALLY
    assert at("3d6 + WRD", WRD=3) == "3d6+3"              # WILD
    assert at("4 + POW", POW=3) == "7"                    # SHIELD mitigation


def test_smash_damage_scales_with_power():
    def dmg(power: int) -> int:
        state = _smash_duel(attacker_power=power, defender_speed=0)
        actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
        return _attack_ev(resolve_round(state, actions, Dice(seed=5), CFG),
                          "smash").data["damage"]

    # Same seeded 2d4; only the POW term moves.
    assert dmg(6) - dmg(0) == 6


def test_smash_auto_steps_toward_target():
    atk = _char("atk", "Atk", power=6, speed=4, weird=0, zone="frontline")
    dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="thunder_back")
    state = _duel(atk, dfn)
    actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=3), CFG)
    assert result.new_state.characters["atk"].zone_id == "thunder_back"
    assert any(e.type.value == "moved" and e.player_id == "atk" for e in result.events)
    assert _attack_ev(result, "smash").data["result"] == "hit"


def test_smash_out_of_reach_when_two_zones_away():
    atk = _char("atk", "Atk", power=6, speed=4, weird=0, zone="glitter_back")
    dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="thunder_back")
    state = _duel(atk, dfn)
    actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=3), CFG)
    # One step closed (glitter_back → frontline), but still out of reach.
    assert result.new_state.characters["atk"].zone_id == "frontline"
    assert _attack_ev(result, "smash").data["result"] == "out_of_reach"
    assert result.new_state.characters["def"].hp == \
        result.new_state.characters["def"].max_hp


def test_blast_hits_everyone_in_zone_with_one_shared_roll():
    chars = [
        _char("cast", "Caster", power=0, speed=4, weird=6, zone="thunder_back"),
        _char("e1", "E1", power=2, speed=0, weird=2, zone="frontline"),
        _char("e2", "E2", power=2, speed=0, weird=2, zone="frontline"),
        _char("ally", "Ally", power=2, speed=0, weird=2, zone="frontline"),
    ]
    teams = [Team(id="team_a", name="A", color="p", player_ids=["cast", "ally"]),
             Team(id="team_b", name="B", color="b", player_ids=["e1", "e2"])]
    state = _state(chars, teams)
    actions = [ClassifiedAction(player_id="cast", move_id="blast", target_id="e1",
                                creativity_tier=3)]
    result = resolve_round(state, actions, Dice(seed=11), CFG)
    blast_evs = [e for e in result.events
                 if e.type.value == "attack_resolved" and e.data.get("move_id") == "blast"]
    # Friendly fire: the caster's own teammate in the zone is a target too.
    assert {e.target_id for e in blast_evs} == {"e1", "e2", "ally"}
    # One shared damage roll: with dodge off, everyone takes the identical number.
    assert len({e.data["damage"] for e in blast_evs}) == 1


def test_shoot_scales_with_weird_only():
    """Ranged keys off Weird alone now — Speed no longer feeds SHOOT (§3 lever)."""
    def shoot_ev(speed: int, weird: int):
        atk = _char("atk", "Atk", power=0, speed=speed, weird=weird, zone="glitter_back")
        dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="thunder_back")
        actions = [ClassifiedAction(player_id="atk", move_id="shoot", target_id="def")]
        return _attack_ev(resolve_round(_duel(atk, dfn), actions, Dice(seed=5), CFG),
                          "shoot")

    swift = shoot_ev(speed=6, weird=1)
    weird = shoot_ev(speed=1, weird=6)
    # Both name Weird; the higher-Weird archer hits harder. Speed is irrelevant.
    assert swift.data["stat"] == "weird" and swift.data["stat_value"] == 1
    assert weird.data["stat"] == "weird" and weird.data["stat_value"] == 6
    assert weird.data["damage"] - swift.data["damage"] == 5   # same 2d4, WRD 6 vs 1

    # Holding Weird fixed, changing Speed leaves SHOOT damage unchanged.
    assert shoot_ev(speed=2, weird=4).data["damage"] == \
        shoot_ev(speed=6, weird=4).data["damage"]


def test_shoot_hits_any_zone_at_full_damage():
    """SHOOT is ranged: full damage from another zone, no auto-step needed."""
    atk = _char("atk", "Atk", power=0, speed=4, weird=6, zone="glitter_back")
    dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="thunder_back")
    state = _duel(atk, dfn)
    actions = [ClassifiedAction(player_id="atk", move_id="shoot", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=5), CFG)
    ev = _attack_ev(result, "shoot")
    assert ev.data["result"] == "hit"
    assert ev.data["point_blank"] is False
    assert 8 <= ev.data["damage"] <= 14      # 2d4 + 6
    # The archer never moved.
    assert result.new_state.characters["atk"].zone_id == "glitter_back"


def test_shoot_point_blank_halves_damage_rounded_up():
    """Same seed, same dice: a same-zone SHOOT deals ceil(half) of the
    cross-zone damage (the point-blank penalty, GAME_DESIGN §4)."""
    actions = [ClassifiedAction(player_id="atk", move_id="shoot", target_id="def")]

    far = _duel(_char("atk", "Atk", power=0, speed=4, weird=6, zone="glitter_back"),
                _char("def", "Def", power=2, speed=0, weird=2, zone="thunder_back"))
    dmg_far = _attack_ev(resolve_round(far, actions, Dice(seed=5), CFG),
                         "shoot").data["damage"]

    near = _duel(_char("atk", "Atk", power=0, speed=4, weird=6, zone="frontline"),
                 _char("def", "Def", power=2, speed=0, weird=2, zone="frontline"))
    ev = _attack_ev(resolve_round(near, actions, Dice(seed=5), CFG), "shoot")
    assert ev.data["point_blank"] is True
    assert ev.data["damage"] == (dmg_far + 1) // 2


def test_shield_mitigates_a_flat_amount_for_every_ally_in_the_zone():
    """SHIELD is zone-wide `4 + POW`: the caster and same-zone teammates are
    covered; a teammate in another zone is not."""
    caster = _char("a1", "Caster", power=3, speed=6, weird=2, zone="frontline")
    near = _char("a2", "Near", power=2, speed=0, weird=2, zone="frontline")
    far = _char("a3", "Far", power=2, speed=0, weird=2, zone="glitter_back")
    foe = _char("e1", "Foe", power=6, speed=1, weird=0, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["a1", "a2", "a3"]),
             Team(id="team_b", name="B", color="b", player_ids=["e1"])]
    state = _state([caster, near, far, foe], teams)
    actions = [ClassifiedAction(player_id="a1", move_id="shield"),
               ClassifiedAction(player_id="e1", move_id="smash", target_id="a2")]
    result = resolve_round(state, actions, Dice(seed=5), CFG)

    shielded = next(e for e in result.events if e.type.value == "shielded")
    assert shielded.player_id == "a1"
    assert shielded.data["protected"] == ["a1", "a2"]   # not the far teammate
    assert shielded.data["mitigate"] == 4 + 3           # 4 + the CASTER's POW

    smash = _attack_ev(result, "smash")
    assert smash.data["blocked"] == 7 and smash.data["shielder_id"] == "a1"
    assert smash.data["damage"] == smash.data["raw"] - 7


def test_shield_mitigation_never_goes_below_zero_damage():
    """A hit smaller than the mitigation is fully swallowed, not negative."""
    caster = _char("a1", "Caster", power=6, speed=6, weird=0, zone="frontline")
    foe = _char("e1", "Foe", power=0, speed=0, weird=0, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["a1"]),
             Team(id="team_b", name="B", color="b", player_ids=["e1"])]
    state = _state([caster, foe], teams)
    actions = [ClassifiedAction(player_id="a1", move_id="shield"),
               ClassifiedAction(player_id="e1", move_id="smash", target_id="a1")]
    result = resolve_round(state, actions, Dice(seed=5), CFG)
    smash = _attack_ev(result, "smash")
    assert smash.data["damage"] == 0          # 2d4+2 vs 10 mitigation
    assert result.new_state.characters["a1"].hp == \
        result.new_state.characters["a1"].max_hp


def test_shield_reflects_the_mitigated_amount_back_at_the_attacker():
    """10% x POW to bounce back exactly what the shield swallowed (§4.1)."""
    reflected = None
    for seed in range(300):
        caster = _char("a1", "Caster", power=6, speed=6, weird=0, zone="frontline")
        foe = _char("e1", "Foe", power=2, speed=0, weird=0, zone="frontline")
        teams = [Team(id="team_a", name="A", color="p", player_ids=["a1"]),
                 Team(id="team_b", name="B", color="b", player_ids=["e1"])]
        state = _state([caster, foe], teams)
        actions = [ClassifiedAction(player_id="a1", move_id="shield"),
                   ClassifiedAction(player_id="e1", move_id="smash", target_id="a1")]
        result = resolve_round(state, actions, Dice(seed=seed), CFG)
        hits = [e for e in result.events if e.data.get("result") == "reflect"]
        if hits:
            reflected = (result, hits[0])
            break
    assert reflected is not None, "POW 6 should reflect (60%) within 300 seeds"
    result, ev = reflected
    smash = _attack_ev(result, "smash")
    assert ev.player_id == "a1" and ev.target_id == "e1"
    assert ev.data["damage"] == smash.data["blocked"]      # exactly what was blocked
    assert result.new_state.characters["e1"].hp == \
        result.new_state.characters["e1"].max_hp - ev.data["damage"]


def test_shield_without_power_never_reflects():
    """reflect chance = 10% x POW → a POW-0 shielder reflects nothing, ever."""
    for seed in range(80):
        caster = _char("a1", "Caster", power=0, speed=6, weird=0, zone="frontline")
        foe = _char("e1", "Foe", power=2, speed=0, weird=0, zone="frontline")
        teams = [Team(id="team_a", name="A", color="p", player_ids=["a1"]),
                 Team(id="team_b", name="B", color="b", player_ids=["e1"])]
        state = _state([caster, foe], teams)
        actions = [ClassifiedAction(player_id="a1", move_id="shield"),
                   ClassifiedAction(player_id="e1", move_id="smash", target_id="a1")]
        result = resolve_round(state, actions, Dice(seed=seed), CFG)
        assert not [e for e in result.events if e.data.get("result") == "reflect"]


def test_shield_applies_at_round_start_so_a_slow_shielder_still_covers_allies():
    """Balance lever (§4.1): SHIELD lands in a round-start pre-pass, before any
    attack, so even a Speed-0 tank protects a faster-hit teammate — the case the
    old 'shield on the caster's turn' rule failed (which made SHIELD a trap)."""
    tank = _char("a1", "Tank", power=3, speed=0, weird=2, zone="frontline")   # SLOW
    ally = _char("a2", "Ally", power=2, speed=0, weird=2, zone="frontline")
    foe = _char("e1", "Foe", power=6, speed=6, weird=0, zone="frontline")      # FAST
    teams = [Team(id="team_a", name="A", color="p", player_ids=["a1", "a2"]),
             Team(id="team_b", name="B", color="b", player_ids=["e1"])]
    state = _state([tank, ally, foe], teams)
    # The fast foe acts before the slow tank, yet the shield is already up.
    actions = [ClassifiedAction(player_id="a1", move_id="shield"),
               ClassifiedAction(player_id="e1", move_id="smash", target_id="a2")]
    result = resolve_round(state, actions, Dice(seed=5), CFG)

    assert result.initiative_order[0] == "e1"          # foe outruns the tank
    smash = _attack_ev(result, "smash")
    assert smash.data["result"] == "hit"
    assert smash.data["blocked"] == 7 and smash.data["shielder_id"] == "a1"  # 4 + POW 3
    assert smash.data["damage"] == smash.data["raw"] - 7


def test_shield_pre_pass_covers_a_whole_zone_in_a_3v3():
    """3v3 at team scale: a Speed-0 tank SHIELDs the frontline, where two allies
    sit; all three enemies are faster and SMASH into that zone. Because the shield
    is applied in the round-start pre-pass, every one of the three incoming hits
    is mitigated — the mechanic that turned SHIELD from a trap into a real move."""
    a1 = _char("a1", "Ally1", power=2, speed=0, weird=2, zone="frontline")
    a2 = _char("a2", "Ally2", power=2, speed=0, weird=2, zone="frontline")
    tank = _char("a3", "Tank", power=4, speed=0, weird=1, zone="frontline")   # shielder
    e1 = _char("e1", "Foe1", power=6, speed=6, weird=0, zone="frontline")
    e2 = _char("e2", "Foe2", power=6, speed=5, weird=0, zone="frontline")
    e3 = _char("e3", "Foe3", power=6, speed=4, weird=0, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["a1", "a2", "a3"]),
             Team(id="team_b", name="B", color="b", player_ids=["e1", "e2", "e3"])]
    state = _state([a1, a2, tank, e1, e2, e3], teams)
    actions = [
        ClassifiedAction(player_id="a3", move_id="shield"),
        ClassifiedAction(player_id="e1", move_id="smash", target_id="a1"),
        ClassifiedAction(player_id="e2", move_id="smash", target_id="a2"),
        ClassifiedAction(player_id="e3", move_id="smash", target_id="a3"),
    ]
    result = resolve_round(state, actions, Dice(seed=5), CFG)

    # All three faster foes act before the Speed-0 tank.
    assert result.initiative_order[:3] == ["e1", "e2", "e3"]

    # The zone-wide shield covered every team-A fighter in the frontline.
    shielded = next(e for e in result.events if e.type.value == "shielded")
    assert shielded.player_id == "a3"
    assert shielded.data["protected"] == ["a1", "a2", "a3"]
    assert shielded.data["mitigate"] == 4 + 4        # 4 + the tank's POW

    # Every incoming SMASH (all from POW-6 foes, so dmg > 8) was mitigated by 8.
    smashes = [e for e in result.events
               if e.data.get("move_id") == "smash" and e.data.get("result") == "hit"]
    assert len(smashes) == 3
    assert all(e.data["blocked"] == 8 for e in smashes)
    """RALLY heals 2d6 + 2*WRD + 2, plus the creativity bonus (§4.1, §8)."""
    def run(tier: int, weird: int = 2) -> int:
        healer = _char("atk", "Healer", power=2, speed=4, weird=weird, zone="glitter_back")
        hurt = _char("ally", "Hurt", power=2, speed=2, weird=2, zone="frontline", hp=5)
        foe = _char("def", "Foe", power=2, speed=1, weird=2, zone="thunder_back")
        teams = [Team(id="team_a", name="A", color="p", player_ids=["atk", "ally"]),
                 Team(id="team_b", name="B", color="b", player_ids=["def"])]
        state = _state([healer, hurt, foe], teams)
        actions = [ClassifiedAction(player_id="atk", move_id="rally", target_id="ally",
                                    creativity_tier=tier)]
        result = resolve_round(state, actions, Dice(seed=9), CFG)
        heal = next(e for e in result.events if e.type.value == "healed")
        assert result.new_state.characters["ally"].hp == 5 + heal.data["amount"]
        return heal.data["amount"]

    plain = run(0)
    assert 8 <= plain <= 18                      # 2d6 + 2*2 + 2
    assert run(2) - plain == CFG.creativity_tier_2
    assert run(3) - plain == CFG.creativity_tier_3
    # The healer's own Weird drives the heal, not the target's.
    assert run(0, weird=6) - plain == 2 * (6 - 2)


def test_rally_heal_capped_at_max_hp_and_blocked_in_sudden_death():
    healer = _char("atk", "Healer", power=2, speed=4, weird=2, zone="frontline")
    foe = _char("def", "Foe", power=2, speed=1, weird=2, zone="thunder_back")
    state = _duel(healer, foe)
    actions = [ClassifiedAction(player_id="atk", move_id="rally", target_id="atk")]
    result = resolve_round(state, actions, Dice(seed=9), CFG)
    assert result.new_state.characters["atk"].hp == \
        result.new_state.characters["atk"].max_hp   # capped

    state2 = _duel(
        _char("atk", "Healer", power=2, speed=4, weird=2, zone="frontline", hp=5),
        _char("def", "Foe", power=2, speed=1, weird=2, zone="thunder_back"))
    state2.sudden_death = True
    result2 = resolve_round(state2, actions, Dice(seed=9), CFG)
    assert result2.new_state.characters["atk"].hp == 5   # healing disabled
    heal = next(e for e in result2.events if e.type.value == "healed")
    assert heal.data["blocked"] == "sudden_death"


def test_wild_card_backfires_with_self_damage_only():
    """WILD CARD is the only self-damage in the game (§4.1). No target effects."""
    backfired = None
    for seed in range(200):
        state = _smash_duel(attacker_power=2, defender_speed=0)
        actions = [ClassifiedAction(player_id="atk", move_id="wild", target_id="def")]
        result = resolve_round(state, actions, Dice(seed=seed), CFG)
        ev = _attack_ev(result, "wild")
        if ev.data["result"] == "backfire":
            backfired = (result, ev)
            break
    assert backfired is not None, "a 15% backfire should appear within 200 seeds"
    result, ev = backfired
    chars = result.new_state.characters
    assert 2 <= ev.data["self_damage"] <= 8            # 2d4
    assert chars["atk"].hp == chars["atk"].max_hp - ev.data["self_damage"]
    assert chars["def"].hp == chars["def"].max_hp      # no target effects


def test_only_wild_card_can_backfire():
    """Every other move is safe to pick — opt-in chaos, no attacker fumbles (§5)."""
    for move_id in ("smash", "shoot", "blast", "rally", "shield"):
        for seed in range(40):
            state = _smash_duel(attacker_power=2, defender_speed=0)
            actions = [ClassifiedAction(player_id="atk", move_id=move_id,
                                        target_id="def")]
            result = resolve_round(state, actions, Dice(seed=seed), CFG)
            assert not [e for e in result.events
                        if e.data.get("result") == "backfire"], move_id


def test_movement_is_absolute_and_grants_no_defensive_bonus():
    """v4 dropped movement's +1 dodge along with AC — a step is just a step."""
    mover = _char("atk", "Mover", power=2, speed=6, weird=2, zone="frontline")
    foe = _char("def", "Foe", power=6, speed=0, weird=2, zone="frontline")
    state = _duel(mover, foe)
    actions = [ClassifiedAction(player_id="atk", move_id="move_l"),
               ClassifiedAction(player_id="def", move_id="shoot", target_id="atk")]
    result = resolve_round(state, actions, Dice(seed=5), CFG)
    ch = result.new_state.characters["atk"]
    assert ch.zone_id == "glitter_back"     # ◀ = one zone left in zones.yaml order
    mv = next(e for e in result.events if e.type.value == "moved")
    assert "dodge_ac" not in mv.data and "ac_bonus" not in mv.data
    # Movement is exempt from the no-repeat rule → not recorded as last move.
    assert ch.last_move_id is None


def test_movement_at_arena_edge_stumbles():
    mover = _char("atk", "Mover", power=2, speed=6, weird=2, zone="glitter_back")
    foe = _char("def", "Foe", power=2, speed=1, weird=2, zone="thunder_back")
    state = _duel(mover, foe)
    actions = [ClassifiedAction(player_id="atk", move_id="move_l")]   # off the edge
    result = resolve_round(state, actions, Dice(seed=1), CFG)
    ch = result.new_state.characters["atk"]
    assert ch.zone_id == "glitter_back"     # unmoved
    assert any(e.type.value == "stumble" and e.data.get("reason") == "arena_edge"
               for e in result.events)


# ---------------------------------------------------------------------------
# Unit: adaptation
# ---------------------------------------------------------------------------


def test_dead_target_redirects_to_nearest_enemy():
    """A target KO'd earlier in the round → redirect, never reject (§9)."""
    atk = _char("p1", "Atk", power=0, speed=4, weird=6, zone="frontline")
    dead = _char("p2", "Dead", power=2, speed=2, weird=2, zone="frontline")
    dead.is_ko = True
    dead.is_gremlin = True
    other = _char("p3", "Other", power=2, speed=0, weird=2, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["p1"]),
             Team(id="team_b", name="B", color="b", player_ids=["p2", "p3"])]
    state = _state([atk, dead, other], teams)
    actions = [ClassifiedAction(player_id="p1", move_id="shoot", target_id="p2")]
    result = resolve_round(state, actions, Dice(seed=4), CFG)
    ev = next(e for e in result.events if e.type.value == "attack_resolved")
    assert ev.target_id == "p3"


# ---------------------------------------------------------------------------
# Unit: KO, victory, sudden death, gremlins, underdog
# ---------------------------------------------------------------------------


def test_ko_converts_to_gremlin_and_victory_fires():
    atk = _char("atk", "Atk", power=6, speed=4, weird=0, zone="frontline")
    dfn = _char("def", "Def", power=0, speed=0, weird=2, zone="frontline", hp=1)
    state = _duel(atk, dfn)
    actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=5), CFG)
    dead = result.new_state.characters["def"]
    assert dead.is_ko and dead.is_gremlin and dead.hp == 0
    kinds = [e.type.value for e in result.events]
    assert "ko" in kinds and "victory" in kinds
    assert result.new_state.winner_team_id == "team_a"


def test_sudden_death_fires_after_max_rounds_and_boosts_damage():
    """v4: 'attacks gain +2' is now flat DAMAGE — there is no roll to boost."""
    state = _smash_duel(attacker_power=2, defender_speed=0)
    state.round = CFG.max_rounds
    actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=5), CFG)
    assert any(e.type.value == "sudden_death" for e in result.events)
    assert result.new_state.sudden_death is True
    base = _attack_ev(result, "smash").data["damage"]

    state2 = _smash_duel(attacker_power=2, defender_speed=0)
    state2.sudden_death = True
    result2 = resolve_round(state2, actions, Dice(seed=5), CFG)
    ev = _attack_ev(result2, "smash")
    assert ev.data["damage"] - base == CFG.sudden_death_damage_bonus
    assert ev.data["riders"] == CFG.sudden_death_damage_bonus


def test_underdog_bonus_applies_when_far_behind():
    """Down two characters' worth of HP share → +1 damage (kids mode)."""
    def run(hp: int | None):
        a1 = _char("p1", "A1", power=2, speed=4, weird=2, zone="frontline", hp=hp)
        a2 = _char("p2", "A2", power=2, speed=3, weird=2, zone="frontline", hp=hp)
        a3 = _char("p3", "A3", power=2, speed=2, weird=2, zone="frontline", hp=hp)
        b1 = _char("p4", "B1", power=2, speed=0, weird=2, zone="frontline")
        b2 = _char("p5", "B2", power=2, speed=0, weird=2, zone="frontline")
        b3 = _char("p6", "B3", power=2, speed=0, weird=2, zone="frontline")
        teams = [Team(id="team_a", name="A", color="p", player_ids=["p1", "p2", "p3"]),
                 Team(id="team_b", name="B", color="b", player_ids=["p4", "p5", "p6"])]
        state = _state([a1, a2, a3, b1, b2, b3], teams)
        actions = [ClassifiedAction(player_id="p1", move_id="smash", target_id="p4")]
        return _attack_ev(resolve_round(state, actions, Dice(seed=7), CFG), "smash")

    behind = run(2)          # nearly wiped out
    even = run(None)         # full health
    assert behind.data["riders"] == CFG.underdog_damage_bonus
    assert even.data["riders"] == 0
    assert behind.data["damage"] - even.data["damage"] == CFG.underdog_damage_bonus


def test_gremlin_drops_hazard_next_round_only():
    grem = _char("g", "Grem", power=2, speed=2, weird=2, zone="frontline")
    grem.is_ko = True
    grem.is_gremlin = True
    a = _char("p1", "A", power=2, speed=3, weird=2, zone="frontline")
    b = _char("p2", "B", power=2, speed=1, weird=2, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["p1", "g"]),
             Team(id="team_b", name="B", color="b", player_ids=["p2"])]
    state = _state([grem, a, b], teams)
    actions = [ClassifiedAction(player_id="g", move_id="bees"),
               ClassifiedAction(player_id="p1", move_id="shield"),
               ClassifiedAction(player_id="p2", move_id="shield")]
    result = resolve_round(state, actions, Dice(seed=6), CFG)
    grem_evs = [e for e in result.events if e.type.value == "gremlin_hazard"]
    assert len(grem_evs) == 1
    assert grem_evs[0].data["hazard_id"] == "bees"


def _gremlin_round(hazard_id: str, seed: int):
    """One round where a start-of-round gremlin drops `hazard_id`; the two
    living fighters both stand on the frontline."""
    grem = _char("g", "Grem", power=2, speed=2, weird=2, zone="frontline")
    grem.is_ko = True
    grem.is_gremlin = True
    a = _char("p1", "A", power=2, speed=3, weird=2, zone="frontline")
    b = _char("p2", "B", power=2, speed=1, weird=2, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["p1", "g"]),
             Team(id="team_b", name="B", color="b", player_ids=["p2"])]
    state = _state([grem, a, b], teams)
    actions = [ClassifiedAction(player_id="g", move_id=hazard_id),
               ClassifiedAction(player_id="p1", move_id="shield"),
               ClassifiedAction(player_id="p2", move_id="shield")]
    return resolve_round(state, actions, Dice(seed=seed), CFG)


def _find_gremlin_seed(hazard_id: str, zone: str) -> tuple[int, object]:
    """Find a seed whose hazard lands on `zone` (the drop zone is seeded-random)."""
    for seed in range(200):
        result = _gremlin_round(hazard_id, seed)
        ev = next(e for e in result.events if e.type.value == "gremlin_hazard")
        if ev.data["zone"] == zone:
            return seed, result
    raise AssertionError("no seed landed the hazard on the target zone")


def test_gremlin_damage_hazard_stings_every_occupant():
    """v2.1: bees/spikes roll one shared 1d4 against everyone in the zone."""
    _, result = _find_gremlin_seed("bees", "frontline")
    hits = [e for e in result.events if e.data.get("result") == "hazard"]
    assert {e.target_id for e in hits} == {"p1", "p2"}
    dmgs = {e.data["damage"] for e in hits}
    assert len(dmgs) == 1 and 1 <= dmgs.pop() <= 4   # one shared roll
    chars = result.new_state.characters
    dmg = next(e.data["damage"] for e in hits)
    assert chars["p1"].hp == chars["p1"].max_hp - dmg
    assert chars["p2"].hp == chars["p2"].max_hp - dmg


def test_gremlin_push_hazard_forces_occupants_out():
    """v2.1: trapdoor/banana push every occupant to an adjacent zone."""
    _, result = _find_gremlin_seed("trapdoor", "frontline")
    chars = result.new_state.characters
    assert chars["p1"].zone_id != "frontline"
    assert chars["p2"].zone_id != "frontline"
    # No damage from a pure push.
    assert chars["p1"].hp == chars["p1"].max_hp
    assert not [e for e in result.events if e.data.get("result") == "hazard"]


# ---------------------------------------------------------------------------
# Data-driven: a novel move added only to moves.yaml resolves
# ---------------------------------------------------------------------------


def test_novel_move_added_only_to_yaml_resolves(tmp_path, monkeypatch):
    import shutil

    import yaml

    import server.config as cfg_mod

    for f in ("moves.yaml", "zones.yaml", "hazards.yaml", "balance.yaml"):
        shutil.copy(f"config/{f}", tmp_path / f)
    data = yaml.safe_load((tmp_path / "moves.yaml").read_text(encoding="utf-8"))
    data["moves"]["zap"] = {
        "stat": "speed", "range": "any", "target": "single_enemy",
        "damage": "1d4 + SPD", "button": "ZAP", "icon": "⚡",
        "desc": "test archetype", "sfx": "zap",
    }
    (tmp_path / "moves.yaml").write_text(yaml.dump(data), encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)

    atk = _char("atk", "Atk", power=0, speed=6, weird=0, zone="frontline")
    dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="frontline")
    state = _duel(atk, dfn)
    actions = [ClassifiedAction(player_id="atk", move_id="zap", target_id="def",
                                creativity_tier=1)]
    result = resolve_round(state, actions, Dice(seed=5), CFG)
    ev = _attack_ev(result, "zap")
    assert ev.data["result"] == "hit"
    assert ev.data["stat"] == "speed" and ev.data["stat_value"] == 6
    # 1d4 + SPD 6 + tier-1 creativity, with no code change anywhere.
    assert 7 + CFG.creativity_tier_1 <= ev.data["damage"] <= 10 + CFG.creativity_tier_1


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(25))
def test_resolver_never_negative_hp_and_unique_event_ids(seed):
    rng = Dice(seed=seed)
    pick = Dice(seed=seed + 1000)
    moves = ["smash", "blast", "shoot", "shield", "rally", "wild", "move_l", "move_r"]
    chars = [
        _char("p1", "P1", power=5, speed=2, weird=2, zone="frontline"),
        _char("p2", "P2", power=1, speed=4, weird=4, zone="thunder_back"),
        _char("p3", "P3", power=3, speed=3, weird=3, zone="frontline"),
        _char("p4", "P4", power=0, speed=3, weird=6, zone="glitter_back"),
    ]
    state = _state(chars, _TEAMS)
    for round_num in range(1, 6):
        living = [p for p, c in state.characters.items() if not c.is_ko]
        actions = []
        for pid in living:
            mv = pick.choice(moves)
            enemies = [p for p in living if p != pid]
            actions.append(ClassifiedAction(
                player_id=pid, move_id=mv,
                target_id=pick.choice(enemies) if enemies else None,
                creativity_tier=pick.randint(0, 3),
            ))
        state = state.model_copy(update={"round": round_num})
        result = resolve_round(state, actions, rng, CFG)
        ids = [e.id for e in result.events]
        assert len(ids) == len(set(ids)), "event ids must be unique"
        for ch in result.new_state.characters.values():
            assert 0 <= ch.hp <= ch.max_hp
            assert not (ch.is_ko and ch.hp > 0)
        state = result.new_state


@pytest.mark.parametrize("seed", range(15))
def test_damage_readout_terms_always_add_up(seed):
    """The host's §13 readout must never disagree with the engine: every damage
    event's terms sum to `raw`, and `raw` minus what was blocked is the damage
    that actually landed."""
    rng = Dice(seed=seed)
    pick = Dice(seed=seed + 500)
    chars = [
        _char("p1", "P1", power=5, speed=2, weird=2, zone="frontline"),
        _char("p2", "P2", power=1, speed=4, weird=4, zone="frontline"),
        _char("p3", "P3", power=3, speed=3, weird=3, zone="frontline"),
        _char("p4", "P4", power=0, speed=3, weird=6, zone="frontline"),
    ]
    state = _state(chars, _TEAMS)
    for round_num in range(1, 5):
        living = [p for p, c in state.characters.items() if not c.is_ko]
        if len(living) < 2:
            break
        actions = [
            ClassifiedAction(
                player_id=pid,
                move_id=pick.choice(["smash", "shoot", "blast", "rally", "shield"]),
                target_id=pick.choice([p for p in living if p != pid]),
                creativity_tier=pick.randint(0, 3),
            )
            for pid in living
        ]
        state = state.model_copy(update={"round": round_num})
        result = resolve_round(state, actions, rng, CFG)
        for e in result.events:
            d = e.data
            if "raw" not in d:
                continue
            assert d["dice"] + d["stat_value"] + d["creativity_bonus"] + d["riders"] \
                == d["raw"], f"readout terms must sum to raw: {d}"
            if e.type.value == "attack_resolved" and not d.get("point_blank"):
                assert d["damage"] == max(0, d["raw"] - d.get("blocked", 0))
        state = result.new_state
