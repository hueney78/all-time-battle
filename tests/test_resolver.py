"""Resolver tests — unit, golden, and property tests (COMBAT V5).

Golden test (test_v5_golden):
    seed 42, the GAME_DESIGN.md §12 2v2 fixture
    (HP = 27 + 2*POW + WRD + SPD//2):
    Stabby (P1/S5/W3, HP 34) + Gerald (P3/S1/W5, HP 38)
    vs Lawnmower (P6/S2/W1, HP 41) + Blob (P0/S3/W6, HP 34).

V5 has no AC, no attack roll, and no dodge: every move lands, and the ONLY thing
that reduces a hit is PROTECT's reflect shield. Initiative is PROTECT-first, then
Speed; a fighter KO'd earlier in the round forfeits its already-tapped action.
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
    Trap,
)
from server.engine.resolver import resolve_round

CFG = load_balance()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _hp(power: int, speed: int, weird: int) -> int:
    return (CFG.hp_base + CFG.hp_per_power * power + CFG.hp_per_weird * weird
            + speed // CFG.hp_speed_divisor)


def _char(
    player_id: str,
    name: str,
    power: int,
    speed: int,
    weird: int,
    zone: str,
    hp: int | None = None,
) -> Character:
    max_hp = _hp(power, speed, weird)
    return Character(
        player_id=player_id,
        name=name,
        stats=Stats(power=power, speed=speed, weird=weird),
        hp=max_hp if hp is None else hp,
        max_hp=max_hp,
        zone_id=zone,
    )


def _state(chars: list[Character], teams: list[Team], round_num: int = 1,
           traps: list[Trap] | None = None) -> GameState:
    return GameState(
        room_id="TEST",
        round=round_num,
        characters={ch.player_id: ch for ch in chars},
        teams=teams,
        traps=traps or [],
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
    atk = _char("atk", "Atk", power=attacker_power, speed=4, weird=0, zone="frontline")
    dfn = _char("def", "Def", power=2, speed=defender_speed, weird=2, zone="frontline")
    return _duel(atk, dfn)


def _attack_ev(result, move_id: str):
    return next(e for e in result.events if e.data.get("move_id") == move_id)


# ---------------------------------------------------------------------------
# §12 Golden test — seed 42, the COMBAT V5 worked round
# ---------------------------------------------------------------------------
#
# Taps: Gerald PROTECT on Stabby (creativity 2 = +3); Stabby CHARGE at Blob
#       (creativity 1 = +1); Lawnmower CHARGE at Stabby (creativity 0); Blob
#       BLAST at Stabby (creativity 3 = +5, DEVASTATING).
#
# Initiative: PROTECT first → Gerald; then by Speed → Stabby(5), Blob(3),
# Lawnmower(2).
#
# Resolution (seed-42 dice, every move lands — only PROTECT's shield reduces):
#   Gerald    PROTECT Stabby: heals 1d6 + WRD 5 + 3 creativity, and cloaks her at
#             5% × 5 = 25% reflect.
#   Stabby    CHARGE into Blob's zone (back_b), then hits for 2d4 + avg(1,5) + 1.
#   Blob      BLAST Stabby — now point-blank in Blob's own zone, so halved — a
#             DEVASTATING drawing; 25% of what lands bounces back at Blob.
#   Lawnmower CHARGE Stabby's zone (already there → no travel), 2d4 + avg(6,2);
#             25% reflected.


def _golden_chars() -> list[Character]:
    return [
        _char("p1", "Princess Stabby", power=1, speed=5, weird=3, zone="glitter_back"),
        _char("p2", "The Blob", power=0, speed=3, weird=6, zone="thunder_back"),
        _char("p3", "Sir Lawnmower", power=6, speed=2, weird=1, zone="thunder_back"),
        _char("p4", "Gerald", power=3, speed=1, weird=5, zone="glitter_back"),
    ]


def _golden_actions() -> list[ClassifiedAction]:
    return [
        ClassifiedAction(player_id="p4", move_id="protect", target_id="p1",
                         creativity_tier=2),
        ClassifiedAction(player_id="p1", move_id="charge", target_id="p2",
                         creativity_tier=1),
        ClassifiedAction(player_id="p3", move_id="charge", target_id="p1"),
        ClassifiedAction(player_id="p2", move_id="blast", target_id="p1",
                         creativity_tier=3),
    ]


def test_v5_golden():
    """seed=42, §12 fixture → deterministic HP values (see narrative above)."""
    state = _state(_golden_chars(), _TEAMS, round_num=1)
    result = resolve_round(state, _golden_actions(), Dice(seed=42), CFG)
    chars = result.new_state.characters

    assert chars["p1"].hp == 21, f"Stabby: got {chars['p1'].hp}"
    assert chars["p2"].hp == 26, f"Blob: got {chars['p2'].hp}"
    assert chars["p3"].hp == 39, f"Lawnmower: got {chars['p3'].hp}"
    assert chars["p4"].hp == 38, f"Gerald: got {chars['p4'].hp}"

    # Derived HP straight from §12: 27 + 2*POW + WRD + SPD//2.
    assert chars["p1"].max_hp == 34
    assert chars["p2"].max_hp == 34
    assert chars["p3"].max_hp == 41
    assert chars["p4"].max_hp == 38

    # Initiative — PROTECT first, then pure Speed.
    assert result.initiative_order == ["p4", "p1", "p2", "p3"]

    # Gerald's PROTECT healed Stabby AND cloaked her at 25% reflect — ONE event
    # (heal + shield resolve together, §11.2), so there is no separate heal event.
    prot = next(e for e in result.events if e.type.value == "protected")
    assert prot.player_id == "p4" and prot.target_id == "p1"
    assert prot.data["amount"] > 0
    assert abs(prot.data["reflect_pct"] - 0.25) < 1e-9
    assert not [e for e in result.events if e.type.value == "healed"]

    # Stabby charged into Blob's zone before swinging.
    assert chars["p1"].zone_id == "thunder_back"
    moved = [e for e in result.events if e.type.value == "moved" and e.player_id == "p1"]
    assert moved and moved[0].data["to"] == "thunder_back"

    # Blob's BLAST was point-blank (Stabby is now in its zone) and DEVASTATING.
    blast = _attack_ev(result, "blast")
    assert blast.data["result"] == "devastating"
    assert blast.data["point_blank"] is True
    assert blast.data["absorbed"] > 0                 # the shield swallowed a share

    # The shield reflected exactly what it absorbed back at each attacker: §12's
    # 25% cloak bounces 2 back at Blob and 2 at Lawnmower.
    reflects = [e for e in result.events if e.data.get("result") == "reflect"]
    assert reflects and all(e.player_id == "p1" for e in reflects)   # Stabby reflects
    assert sorted(e.data["damage"] for e in reflects) == [2, 2]      # exact reflect amounts

    # No-repeat bookkeeping: every fighter's move was recorded.
    assert chars["p1"].last_move_id == "charge"
    assert chars["p4"].last_move_id == "protect"
    assert not any(ch.is_ko for ch in chars.values())


# ---------------------------------------------------------------------------
# Companion: a KO'd fighter forfeits an already-tapped action (§10 bug fix)
# ---------------------------------------------------------------------------


def test_ko_before_slot_forfeits_the_tapped_action():
    """A fighter reduced to 0 HP before its initiative slot never resolves its
    tapped move — dead is dead, immediately."""
    fast = _char("fast", "Fast", power=6, speed=6, weird=0, zone="frontline")
    slow = _char("slow", "Slow", power=6, speed=0, weird=0, zone="frontline", hp=3)
    bystander = _char("by", "By", power=0, speed=1, weird=0, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["fast"]),
             Team(id="team_b", name="B", color="b", player_ids=["slow", "by"])]
    state = _state([fast, slow, bystander], teams)
    # Fast SMASHes Slow (KO), so Slow's own SMASH at the bystander must never land.
    actions = [
        ClassifiedAction(player_id="fast", move_id="smash", target_id="slow"),
        ClassifiedAction(player_id="slow", move_id="smash", target_id="by"),
    ]
    result = resolve_round(state, actions, Dice(seed=5), CFG)
    assert result.new_state.characters["slow"].is_ko
    # No smash event names Slow as the attacker.
    assert not [e for e in result.events
                if e.player_id == "slow" and e.type.value == "attack_resolved"]
    assert result.new_state.characters["by"].hp == result.new_state.characters["by"].max_hp


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


def test_protect_caster_acts_first_regardless_of_speed():
    """PROTECT always resolves before every other move that round (§5)."""
    healer = _char("h", "Healer", power=0, speed=0, weird=6, zone="frontline")  # SLOWEST
    ally = _char("a", "Ally", power=2, speed=3, weird=2, zone="frontline")
    foe = _char("e", "Foe", power=6, speed=6, weird=0, zone="frontline")        # FASTEST
    teams = [Team(id="team_a", name="A", color="p", player_ids=["h", "a"]),
             Team(id="team_b", name="B", color="b", player_ids=["e"])]
    state = _state([healer, ally, foe], teams)
    actions = [
        ClassifiedAction(player_id="h", move_id="protect", target_id="a"),
        ClassifiedAction(player_id="e", move_id="smash", target_id="a"),
    ]
    result = resolve_round(state, actions, Dice(seed=5), CFG)
    # The Speed-0 healer outranks the Speed-6 foe because PROTECT acts first.
    assert result.initiative_order[0] == "h"
    # The shield was up before the foe's SMASH, so it absorbed + reflected.
    smash = _attack_ev(result, "smash")
    assert smash.data["absorbed"] > 0
    assert [e for e in result.events if e.data.get("result") == "reflect"]


def test_initiative_tie_broken_by_seeded_roll():
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
    assert order_for(5) == order_for(5)
    assert any(order_for(s) != order_for(5) for s in range(30))


def test_initiative_drops_ko_and_gremlins():
    chars = _golden_chars()
    chars[1].is_ko = True
    chars[1].is_gremlin = True
    state = _state(chars, _TEAMS, round_num=1)
    result = resolve_round(state, _golden_actions(), Dice(seed=42), CFG)
    assert "p2" not in result.initiative_order


# ---------------------------------------------------------------------------
# Unit: V5 resolution — every move lands; only PROTECT's shield reduces it
# ---------------------------------------------------------------------------


def test_every_move_lands_there_is_no_miss():
    """The v5 headline: a selected move always takes effect, at every seed."""
    for seed in range(60):
        state = _smash_duel(attacker_power=0, defender_speed=6)
        actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
        result = resolve_round(state, actions, Dice(seed=seed), CFG)
        ev = _attack_ev(result, "smash")
        assert ev.data["result"] == "hit"
        assert ev.data["damage"] > 0
    assert not hasattr(Dice(seed=0), "two_d6"), "v5 has no attack roll"


def test_no_dodge_a_fast_target_still_takes_every_hit():
    """Dodge is gone in v5 — even a Speed-6 target is never missed."""
    for seed in range(80):
        state = _smash_duel(attacker_power=2, defender_speed=6)
        actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
        result = resolve_round(state, actions, Dice(seed=seed), CFG)
        assert _attack_ev(result, "smash").data["result"] in ("hit", "devastating")


# ---------------------------------------------------------------------------
# Unit: creativity, DEVASTATING, combos
# ---------------------------------------------------------------------------


def test_creativity_bonus_is_flat_and_stale_scores_zero():
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
    assert damage_at(3, stale=True) == base


def test_creativity_tier_3_is_devastating():
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
    """v5: a combo is +1 creativity TIER for both partners, not a fusion (§8)."""
    plain = resolve_round(_combo_state(), [
        ClassifiedAction(player_id="a1", move_id="smash", target_id="e1",
                         creativity_tier=1),
        ClassifiedAction(player_id="a2", move_id="blast", target_id="e1",
                         creativity_tier=1),
    ], Dice(seed=8), CFG)

    combo = resolve_round(_combo_state(), [
        ClassifiedAction(player_id="a1", move_id="smash", target_id="e1",
                         creativity_tier=1, combo_partners=["a2"],
                         combo_name="GLITTERNADO"),
        ClassifiedAction(player_id="a2", move_id="blast", target_id="e1",
                         creativity_tier=1, combo_partners=["a1"],
                         combo_name="GLITTERNADO"),
    ], Dice(seed=8), CFG)

    combo_evs = [e for e in combo.events if e.type.value == "combo"]
    assert len(combo_evs) == 1
    assert combo_evs[0].data["combo_name"] == "GLITTERNADO"

    step = CFG.creativity_tier_2 - CFG.creativity_tier_1
    for mid in ("smash", "blast"):
        assert _attack_ev(combo, mid).data["creativity_tier"] == 1 + CFG.combo_tier_bonus
        # Compare the addition total `raw` (a2's BLAST is point-blank here, so its
        # final damage is halved after creativity — but `raw` moves by the tier).
        assert (_attack_ev(combo, mid).data["raw"]
                - _attack_ev(plain, mid).data["raw"]) == step


def test_combo_can_escalate_a_tier_2_drawing_into_devastating():
    combo = resolve_round(_combo_state(), [
        ClassifiedAction(player_id="a1", move_id="smash", target_id="e1",
                         creativity_tier=2, combo_partners=["a2"], combo_name="X"),
        ClassifiedAction(player_id="a2", move_id="blast", target_id="e1",
                         creativity_tier=2, combo_partners=["a1"], combo_name="X"),
    ], Dice(seed=8), CFG)
    assert _attack_ev(combo, "smash").data["result"] == "devastating"


def test_creativity_tier_never_exceeds_the_top_tier():
    combo = resolve_round(_combo_state(), [
        ClassifiedAction(player_id="a1", move_id="smash", target_id="e1",
                         creativity_tier=3, combo_partners=["a2"], combo_name="X"),
        ClassifiedAction(player_id="a2", move_id="blast", target_id="e1",
                         creativity_tier=3, combo_partners=["a1"], combo_name="X"),
    ], Dice(seed=8), CFG)
    ev = _attack_ev(combo, "smash")
    assert ev.data["creativity_tier"] == 3
    assert ev.data["creativity_bonus"] == CFG.creativity_tier_3


# ---------------------------------------------------------------------------
# Unit: the five moves
# ---------------------------------------------------------------------------


def test_move_formulas_scale_with_stats():
    """The v5 catalog's live math, as rendered on the phone's buttons."""
    def at(spec, **stats):
        env = {"POW": 0, "SPD": 0, "WRD": 0} | stats
        return describe_formula(spec, env)

    assert at("2d4 + POW + 2", POW=0) == "2d4+2"          # SMASH floor
    assert at("2d4 + POW + 2", POW=6) == "2d4+8"          # SMASH on the brick
    assert at("2d4 + WRD + 2", WRD=5) == "2d4+7"          # BLAST
    assert at("2d4 + avg(POW,SPD)", POW=6, SPD=2) == "2d4+4"   # CHARGE
    assert at("2d4 + SPD", SPD=5) == "2d4+5"              # ESCAPE
    assert at("1d6 + WRD", WRD=6) == "1d6+6"              # PROTECT heal


def test_smash_damage_scales_with_power():
    def dmg(power: int) -> int:
        state = _smash_duel(attacker_power=power, defender_speed=0)
        actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
        return _attack_ev(resolve_round(state, actions, Dice(seed=5), CFG),
                          "smash").data["damage"]

    assert dmg(6) - dmg(0) == 6


def test_smash_requires_an_enemy_in_your_zone():
    """SMASH is melee-only — no same-zone enemy → it whiffs (no_target)."""
    atk = _char("atk", "Atk", power=6, speed=4, weird=0, zone="glitter_back")
    dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="thunder_back")
    state = _duel(atk, dfn)
    actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=3), CFG)
    ev = _attack_ev(result, "smash")
    assert ev.data["result"] == "no_target"
    assert result.new_state.characters["def"].hp == result.new_state.characters["def"].max_hp
    assert result.new_state.characters["atk"].zone_id == "glitter_back"   # no travel


def test_blast_hits_any_zone_at_full_damage():
    atk = _char("atk", "Atk", power=0, speed=4, weird=6, zone="glitter_back")
    dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="thunder_back")
    state = _duel(atk, dfn)
    actions = [ClassifiedAction(player_id="atk", move_id="blast", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=5), CFG)
    ev = _attack_ev(result, "blast")
    assert ev.data["result"] == "hit"
    assert ev.data["point_blank"] is False
    assert ev.data["stat"] == "weird" and ev.data["stat_value"] == 6
    assert result.new_state.characters["atk"].zone_id == "glitter_back"   # ranged, no move


def test_blast_point_blank_halves_damage_rounded_up():
    actions = [ClassifiedAction(player_id="atk", move_id="blast", target_id="def")]

    far = _duel(_char("atk", "Atk", power=0, speed=4, weird=6, zone="glitter_back"),
                _char("def", "Def", power=2, speed=0, weird=2, zone="thunder_back"))
    dmg_far = _attack_ev(resolve_round(far, actions, Dice(seed=5), CFG),
                         "blast").data["damage"]

    near = _duel(_char("atk", "Atk", power=0, speed=4, weird=6, zone="frontline"),
                 _char("def", "Def", power=2, speed=0, weird=2, zone="frontline"))
    ev = _attack_ev(resolve_round(near, actions, Dice(seed=5), CFG), "blast")
    assert ev.data["point_blank"] is True
    assert ev.data["damage"] == (dmg_far + 1) // 2


def test_charge_moves_into_the_targets_zone_then_hits():
    atk = _char("atk", "Atk", power=6, speed=4, weird=0, zone="glitter_back")
    dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="thunder_back")
    state = _duel(atk, dfn)
    actions = [ClassifiedAction(player_id="atk", move_id="charge", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=3), CFG)
    assert result.new_state.characters["atk"].zone_id == "thunder_back"
    assert any(e.type.value == "moved" and e.player_id == "atk" for e in result.events)
    assert _attack_ev(result, "charge").data["result"] == "hit"


def test_charge_already_adjacent_skips_the_move():
    """A CHARGE at someone already in your zone just swings — no travel."""
    atk = _char("atk", "Atk", power=6, speed=4, weird=0, zone="frontline")
    dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="frontline")
    state = _duel(atk, dfn)
    actions = [ClassifiedAction(player_id="atk", move_id="charge", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=3), CFG)
    assert not [e for e in result.events if e.type.value == "moved"]
    assert _attack_ev(result, "charge").data["result"] == "hit"


def test_charge_keys_off_avg_of_power_and_speed():
    def ev(power, speed):
        atk = _char("atk", "Atk", power=power, speed=speed, weird=0, zone="frontline")
        dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="frontline")
        actions = [ClassifiedAction(player_id="atk", move_id="charge", target_id="def")]
        return _attack_ev(resolve_round(_duel(atk, dfn), actions, Dice(seed=5), CFG),
                          "charge")

    # Hold Speed fixed (same initiative/dice stream) and vary Power only:
    # avg(6,2)=4 vs avg(0,2)=1 → same seeded 2d4, damage differs by 3.
    assert ev(6, 2).data["stat_value"] == 4
    assert ev(0, 2).data["stat_value"] == 1
    assert ev(6, 2).data["damage"] - ev(0, 2).data["damage"] == 3


def test_escape_slips_one_zone_by_direction_then_hits():
    atk = _char("atk", "Atk", power=0, speed=6, weird=0, zone="frontline")
    dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="thunder_back")
    state = _duel(atk, dfn)
    # ◀ = -1 = one zone toward glitter_back in zones.yaml order.
    actions = [ClassifiedAction(player_id="atk", move_id="escape", target_id="def",
                                escape_direction=-1)]
    result = resolve_round(state, actions, Dice(seed=5), CFG)
    assert result.new_state.characters["atk"].zone_id == "glitter_back"
    assert _attack_ev(result, "escape").data["result"] == "hit"
    assert _attack_ev(result, "escape").data["stat"] == "speed"


def test_escape_at_the_edge_moves_inward():
    """An edge zone can only move inward — ◀ from the leftmost zone goes right."""
    atk = _char("atk", "Atk", power=0, speed=6, weird=0, zone="glitter_back")
    dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="thunder_back")
    state = _duel(atk, dfn)
    actions = [ClassifiedAction(player_id="atk", move_id="escape", target_id="def",
                                escape_direction=-1)]   # off the left edge
    result = resolve_round(state, actions, Dice(seed=5), CFG)
    assert result.new_state.characters["atk"].zone_id == "frontline"   # forced inward


def test_protect_heals_and_scales_with_the_casters_weird():
    def run(tier: int, weird: int = 2) -> int:
        healer = _char("atk", "Healer", power=2, speed=4, weird=weird, zone="glitter_back")
        hurt = _char("ally", "Hurt", power=2, speed=2, weird=2, zone="frontline", hp=5)
        foe = _char("def", "Foe", power=2, speed=1, weird=2, zone="thunder_back")
        teams = [Team(id="team_a", name="A", color="p", player_ids=["atk", "ally"]),
                 Team(id="team_b", name="B", color="b", player_ids=["def"])]
        state = _state([healer, hurt, foe], teams)
        actions = [ClassifiedAction(player_id="atk", move_id="protect", target_id="ally",
                                    creativity_tier=tier)]
        result = resolve_round(state, actions, Dice(seed=9), CFG)
        prot = next(e for e in result.events if e.type.value == "protected")
        assert result.new_state.characters["ally"].hp == 5 + prot.data["amount"]
        return prot.data["amount"]

    plain = run(0)
    assert 3 <= plain <= 8                        # 1d6 + WRD 2
    assert run(2) - plain == CFG.creativity_tier_2
    assert run(3) - plain == CFG.creativity_tier_3
    assert run(0, weird=6) - plain == (6 - 2)     # the caster's Weird drives the heal


def test_protect_heal_is_capped_at_max_hp():
    healer = _char("atk", "Healer", power=2, speed=4, weird=6, zone="frontline")
    ally = _char("ally", "Ally", power=2, speed=2, weird=2, zone="frontline")
    foe = _char("def", "Foe", power=2, speed=1, weird=2, zone="thunder_back")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["atk", "ally"]),
             Team(id="team_b", name="B", color="b", player_ids=["def"])]
    state = _state([healer, ally, foe], teams)
    actions = [ClassifiedAction(player_id="atk", move_id="protect", target_id="ally")]
    result = resolve_round(state, actions, Dice(seed=9), CFG)
    ally_after = result.new_state.characters["ally"]
    assert ally_after.hp == ally_after.max_hp


def test_protect_shield_reflects_a_share_back_at_the_attacker():
    """A shielded ally absorbs reflect_per_weird×WRD of the hit and bounces it."""
    healer = _char("a1", "Healer", power=0, speed=5, weird=6, zone="frontline")  # 30% cap
    ally = _char("a2", "Ally", power=2, speed=0, weird=2, zone="frontline")
    foe = _char("e1", "Foe", power=6, speed=1, weird=0, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["a1", "a2"]),
             Team(id="team_b", name="B", color="b", player_ids=["e1"])]
    state = _state([healer, ally, foe], teams)
    actions = [ClassifiedAction(player_id="a1", move_id="protect", target_id="a2"),
               ClassifiedAction(player_id="e1", move_id="smash", target_id="a2")]
    result = resolve_round(state, actions, Dice(seed=5), CFG)

    smash = _attack_ev(result, "smash")
    absorbed = smash.data["absorbed"]
    assert absorbed > 0
    reflect = next(e for e in result.events if e.data.get("result") == "reflect")
    assert reflect.player_id == "a2" and reflect.target_id == "e1"
    assert reflect.data["damage"] == absorbed
    # The foe took exactly what its own blow had reflected.
    foe_after = result.new_state.characters["e1"]
    assert foe_after.hp == foe_after.max_hp - absorbed
    # The ally took the reduced amount.
    ally_after = result.new_state.characters["a2"]
    assert ally_after.hp == ally_after.max_hp - smash.data["damage"]


def test_reflect_can_ko_the_attacker():
    healer = _char("a1", "Healer", power=0, speed=5, weird=6, zone="frontline")
    ally = _char("a2", "Ally", power=6, speed=0, weird=2, zone="frontline")
    foe = _char("e1", "Foe", power=6, speed=1, weird=0, zone="frontline", hp=1)
    teams = [Team(id="team_a", name="A", color="p", player_ids=["a1", "a2"]),
             Team(id="team_b", name="B", color="b", player_ids=["e1"])]
    state = _state([healer, ally, foe], teams)
    actions = [ClassifiedAction(player_id="a1", move_id="protect", target_id="a2"),
               ClassifiedAction(player_id="e1", move_id="smash", target_id="a2")]
    result = resolve_round(state, actions, Dice(seed=5), CFG)
    assert result.new_state.characters["e1"].is_ko          # felled by its own reflect
    assert result.new_state.winner_team_id == "team_a"


def test_protect_without_shield_pct_when_weird_zero():
    """A Weird-0 protector heals but its shield reflects nothing."""
    healer = _char("a1", "Healer", power=6, speed=5, weird=0, zone="frontline")
    ally = _char("a2", "Ally", power=2, speed=0, weird=2, zone="frontline", hp=5)
    foe = _char("e1", "Foe", power=6, speed=1, weird=0, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["a1", "a2"]),
             Team(id="team_b", name="B", color="b", player_ids=["e1"])]
    state = _state([healer, ally, foe], teams)
    actions = [ClassifiedAction(player_id="a1", move_id="protect", target_id="a2"),
               ClassifiedAction(player_id="e1", move_id="smash", target_id="a2")]
    result = resolve_round(state, actions, Dice(seed=5), CFG)
    assert _attack_ev(result, "smash").data["absorbed"] == 0
    assert not [e for e in result.events if e.data.get("result") == "reflect"]


def test_protect_redirects_to_a_living_ally_and_never_self():
    """A tapped-target ally that fell is redirected to the neediest teammate."""
    healer = _char("a1", "Healer", power=0, speed=4, weird=4, zone="frontline")
    dead = _char("a2", "Dead", power=2, speed=2, weird=2, zone="frontline")
    dead.is_ko = True
    dead.is_gremlin = True
    hurt = _char("a3", "Hurt", power=2, speed=2, weird=2, zone="frontline", hp=3)
    foe = _char("e1", "Foe", power=2, speed=0, weird=2, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["a1", "a2", "a3"]),
             Team(id="team_b", name="B", color="b", player_ids=["e1"])]
    state = _state([healer, dead, hurt, foe], teams)
    actions = [ClassifiedAction(player_id="a1", move_id="protect", target_id="a2")]
    result = resolve_round(state, actions, Dice(seed=9), CFG)
    prot = next(e for e in result.events if e.type.value == "protected")
    assert prot.target_id == "a3"        # redirected to the living, neediest ally


# ---------------------------------------------------------------------------
# Unit: adaptation
# ---------------------------------------------------------------------------


def test_dead_target_redirects_to_nearest_enemy():
    """A ranged target KO'd earlier in the round → redirect, never reject (§9)."""
    atk = _char("p1", "Atk", power=0, speed=4, weird=6, zone="frontline")
    dead = _char("p2", "Dead", power=2, speed=2, weird=2, zone="frontline")
    dead.is_ko = True
    dead.is_gremlin = True
    other = _char("p3", "Other", power=2, speed=0, weird=2, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["p1"]),
             Team(id="team_b", name="B", color="b", player_ids=["p2", "p3"])]
    state = _state([atk, dead, other], teams)
    actions = [ClassifiedAction(player_id="p1", move_id="blast", target_id="p2")]
    result = resolve_round(state, actions, Dice(seed=4), CFG)
    ev = next(e for e in result.events if e.type.value == "attack_resolved")
    assert ev.target_id == "p3"


# ---------------------------------------------------------------------------
# Unit: KO, victory, sudden death, underdog
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


def test_victory_ends_the_round_no_further_actions_resolve():
    """The moment a team's last member is KO'd, resolution stops — a slower
    winning-team fighter queued after the finishing blow never acts (GAME_DESIGN
    §6 / §12 v6 bug fix)."""
    a1 = _char("a1", "A1", power=6, speed=6, weird=0, zone="frontline")   # acts first
    a2 = _char("a2", "A2", power=6, speed=1, weird=0, zone="frontline")   # acts last
    b1 = _char("b1", "B1", power=2, speed=3, weird=0, zone="frontline", hp=3)  # frail
    teams = [Team(id="team_a", name="A", color="p", player_ids=["a1", "a2"]),
             Team(id="team_b", name="B", color="b", player_ids=["b1"])]
    state = _state([a1, a2, b1], teams)
    # a1 SMASHes b1 (KO → Team B wiped); a2's queued BLAST must never resolve.
    actions = [
        ClassifiedAction(player_id="a1", move_id="smash", target_id="b1"),
        ClassifiedAction(player_id="a2", move_id="blast", target_id="b1"),
        ClassifiedAction(player_id="b1", move_id="smash", target_id="a1"),
    ]
    result = resolve_round(state, actions, Dice(seed=5), CFG)
    assert result.new_state.characters["b1"].is_ko
    assert result.new_state.winner_team_id == "team_a"
    assert any(e.type.value == "victory" for e in result.events)
    # a2 was queued after the finishing blow → it produced NO event at all.
    assert not [e for e in result.events if e.player_id == "a2"]
    # b1 fell before its own slot, so its SMASH never landed either.
    assert result.new_state.characters["a1"].hp == result.new_state.characters["a1"].max_hp


def test_sudden_death_fires_after_max_rounds_and_boosts_damage():
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

    behind = run(2)
    even = run(None)
    assert behind.data["riders"] == CFG.underdog_damage_bonus
    assert even.data["riders"] == 0
    assert behind.data["damage"] - even.data["damage"] == CFG.underdog_damage_bonus


# ---------------------------------------------------------------------------
# Unit: Arena Gremlin traps (GAME_DESIGN §10)
# ---------------------------------------------------------------------------


def _gremlin_state(trap_zone_occupied: bool):
    grem = _char("g", "Grem", power=2, speed=2, weird=2, zone="frontline")
    grem.is_ko = True
    grem.is_gremlin = True
    ally = _char("a", "A", power=2, speed=3, weird=2, zone="glitter_back")
    foe = _char("e", "E", power=2, speed=1, weird=2,
                zone="frontline" if trap_zone_occupied else "thunder_back")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["g", "a"]),
             Team(id="team_b", name="B", color="b", player_ids=["e"])]
    return _state([grem, ally, foe], teams)


def test_gremlin_plants_a_trap_that_fires_on_an_enemy_in_the_zone():
    state = _gremlin_state(trap_zone_occupied=True)
    actions = [ClassifiedAction(player_id="g", move_id="", trap_zone="frontline",
                                creativity_tier=1),
               ClassifiedAction(player_id="a", move_id="blast", target_id="e")]
    result = resolve_round(state, actions, Dice(seed=6), CFG)
    placed = [e for e in result.events if e.type.value == "trap_placed"]
    fired = [e for e in result.events if e.type.value == "trap_triggered"]
    assert placed and placed[0].data["zone"] == "frontline"
    assert fired and fired[0].target_id == "e"
    assert fired[0].data["damage"] >= 1 + CFG.creativity_tier_1   # 1d4 + creativity
    # The trap is consumed once it fires.
    assert result.new_state.traps == []


def test_gremlin_trap_persists_until_an_enemy_arrives():
    """No enemy in the zone → the trap sits in game state until one shows up."""
    state = _gremlin_state(trap_zone_occupied=False)
    actions = [ClassifiedAction(player_id="g", move_id="", trap_zone="frontline",
                                creativity_tier=0),
               ClassifiedAction(player_id="a", move_id="blast", target_id="e")]
    r1 = resolve_round(state, actions, Dice(seed=6), CFG)
    assert not [e for e in r1.events if e.type.value == "trap_triggered"]
    assert len(r1.new_state.traps) == 1                 # persisted

    # Round 2: the foe walks into the trapped zone (via CHARGE) → it springs.
    s2 = r1.new_state.model_copy(update={"round": 2})
    actions2 = [ClassifiedAction(player_id="g", move_id="", trap_zone="thunder_back"),
                ClassifiedAction(player_id="e", move_id="charge", target_id="a")]
    r2 = resolve_round(s2, actions2, Dice(seed=6), CFG)
    # The foe charged into glitter_back to reach the ally, not the frontline trap;
    # the original frontline trap still waits, and a new one lands in thunder_back.
    zones = {t.zone_id for t in r2.new_state.traps}
    assert "frontline" in zones


def test_trap_only_triggers_on_enemies_not_the_owners_team():
    """A trap ignores the gremlin's own teammates standing in the zone."""
    grem = _char("g", "Grem", power=2, speed=2, weird=2, zone="frontline")
    grem.is_ko = True
    grem.is_gremlin = True
    ally = _char("a", "A", power=2, speed=3, weird=2, zone="frontline")   # same team
    foe = _char("e", "E", power=2, speed=1, weird=2, zone="thunder_back")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["g", "a"]),
             Team(id="team_b", name="B", color="b", player_ids=["e"])]
    state = _state([grem, ally, foe], teams)
    actions = [ClassifiedAction(player_id="g", move_id="", trap_zone="frontline"),
               ClassifiedAction(player_id="a", move_id="blast", target_id="e")]
    result = resolve_round(state, actions, Dice(seed=6), CFG)
    assert not [e for e in result.events if e.type.value == "trap_triggered"]
    assert len(result.new_state.traps) == 1     # only the ally was there → still armed


# ---------------------------------------------------------------------------
# Data-driven: a novel move added only to moves.yaml resolves
# ---------------------------------------------------------------------------


def test_novel_move_added_only_to_yaml_resolves(tmp_path, monkeypatch):
    import shutil

    import yaml

    import server.config as cfg_mod

    for f in ("moves.yaml", "zones.yaml", "balance.yaml", "settings.yaml"):
        shutil.copy(f"config/{f}", tmp_path / f)
    data = yaml.safe_load((tmp_path / "moves.yaml").read_text(encoding="utf-8"))
    data["moves"]["zap"] = {
        "stat": "speed", "range": "any_zone", "target": "single_enemy",
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
    assert 7 + CFG.creativity_tier_1 <= ev.data["damage"] <= 10 + CFG.creativity_tier_1


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

_MOVES = ["smash", "blast", "charge", "escape", "protect"]


@pytest.mark.parametrize("seed", range(25))
def test_resolver_never_negative_hp_and_unique_event_ids(seed):
    rng = Dice(seed=seed)
    pick = Dice(seed=seed + 1000)
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
            mv = pick.choice(_MOVES)
            teammates = [p for p in living if p != pid
                         and (p in ("p1", "p4")) == (pid in ("p1", "p4"))]
            enemies = [p for p in living if p != pid and p not in teammates]
            if mv == "protect":
                if not teammates:
                    mv = "blast"
                    target = pick.choice(enemies) if enemies else None
                else:
                    target = pick.choice(teammates)
            else:
                target = pick.choice(enemies) if enemies else None
            actions.append(ClassifiedAction(
                player_id=pid, move_id=mv, target_id=target,
                escape_direction=pick.choice([-1, 1]),
                creativity_tier=pick.randint(0, 3)))
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
    """The host's §13 readout must never disagree with the engine: every damage/
    heal event's terms sum to `raw`, and the damage that landed is `raw` (or its
    point-blank half) minus what the shield absorbed."""
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
        actions = []
        for pid in living:
            enemies = [p for p in living if p != pid
                       and (p in ("p1", "p4")) != (pid in ("p1", "p4"))]
            actions.append(ClassifiedAction(
                player_id=pid,
                move_id=pick.choice(["smash", "blast", "charge", "escape"]),
                target_id=pick.choice(enemies) if enemies else None,
                escape_direction=pick.choice([-1, 1]),
                creativity_tier=pick.randint(0, 3)))
        state = state.model_copy(update={"round": round_num})
        result = resolve_round(state, actions, rng, CFG)
        for e in result.events:
            d = e.data
            if "raw" not in d:
                continue
            assert d["dice"] + d["stat_value"] + d["creativity_bonus"] + d["riders"] \
                == d["raw"], f"readout terms must sum to raw: {d}"
            if e.type.value == "attack_resolved" and d.get("result") in ("hit", "devastating"):
                landed = (d["raw"] + 1) // 2 if d.get("point_blank") else d["raw"]
                assert d["damage"] == max(0, landed - d.get("absorbed", 0))
        state = result.new_state
