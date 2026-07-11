"""Resolver tests — unit, golden, and property tests (COMBAT V2).

Golden test (test_v2_golden):
    seed 42, the GAME_DESIGN.md §12 2v2 fixture:
    Stabby (P1/S5/W3, HP 22, AC 15) + Gerald (P3/S1/W5, HP 26, AC 11)
    vs Lawnmower (P6/S2/W1, HP 32, AC 12) + Blob (P0/S3/W6, HP 20, AC 13).
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
    WildInterpretation,
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
    conditions: dict | None = None,
) -> Character:
    max_hp = CFG.hp_base + CFG.hp_per_power * power
    return Character(
        player_id=player_id,
        name=name,
        stats=Stats(power=power, speed=speed, weird=weird),
        hp=max_hp if hp is None else hp,
        max_hp=max_hp,
        ac=CFG.ac_base + speed,
        zone_id=zone,
        conditions=conditions or {},
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


def _duel_teams():
    return [Team(id="team_a", name="A", color="pink", player_ids=["atk"]),
            Team(id="team_b", name="B", color="blue", player_ids=["def"])]


def _duel(attacker: Character, defender: Character):
    attacker.player_id = "atk"
    defender.player_id = "def"
    return _state([attacker, defender],
                  [Team(id="team_a", name="A", color="p", player_ids=["atk"]),
                   Team(id="team_b", name="B", color="b", player_ids=["def"])])


# ---------------------------------------------------------------------------
# §12 Golden test — seed 42, the COMBAT V2 worked round
# ---------------------------------------------------------------------------
#
# Taps: Stabby TRICK on Blob (glitter hypnosis → confused, creativity 2);
#       Blob BLAST at Stabby's zone (creativity 1); Lawnmower SMASH on Gerald
#       (auto-step); Gerald SHIELDs himself — one round too late.
#
# Initiative (pure speed, no ties): Stabby(5) → Blob(3) → Lawnmower(2) → Gerald(1).
#
# Seed-42 dice (the doc's example rolls in §12 are illustrative; the test
# asserts the actual seeded dice, exactly like the v1 golden test did):
#   Stabby    TRICK: 2d6=7 +3 WRD +2 creativity = 12 vs AC 13 → MISS.
#   Blob      BLAST: 2d6=7 +6 WRD +1 creativity = 14 vs frontline occupants:
#             misses Stabby (AC 15), hits its own teammate Lawnmower (AC 12)
#             for 3d4+3 = 10 — BLAST hits EVERYONE in the zone.
#   Lawnmower SMASH: auto-steps frontline → glitter_back, 2d6=8 +6 POW = 14
#             vs AC 11 → hit (margin 3 < crit_margin 5) for 4d4+2 = 9.
#   Gerald    SHIELD: shielded (+5 AC) lands after the SMASH — initiative
#             order matters and the couch sees why on the rail.


def _golden_chars() -> list[Character]:
    return [
        _char("p1", "Princess Stabby", power=1, speed=5, weird=3, zone="frontline"),
        _char("p2", "The Blob", power=0, speed=3, weird=6, zone="thunder_back"),
        _char("p3", "Sir Lawnmower", power=6, speed=2, weird=1, zone="frontline"),
        _char("p4", "Gerald", power=3, speed=1, weird=5, zone="glitter_back"),
    ]


def _golden_actions() -> list[ClassifiedAction]:
    return [
        ClassifiedAction(player_id="p1", move_id="trick", target_id="p2",
                         creativity_tier=2, trick_condition="confused"),
        ClassifiedAction(player_id="p2", move_id="blast", target_id="p1",
                         creativity_tier=1),
        ClassifiedAction(player_id="p3", move_id="smash", target_id="p4"),
        ClassifiedAction(player_id="p4", move_id="shield", target_id="p4"),
    ]


def test_v2_golden():
    """seed=42, §12 fixture → deterministic HP values (see narrative above)."""
    state = _state(_golden_chars(), _TEAMS, round_num=1)
    result = resolve_round(state, _golden_actions(), Dice(seed=42), CFG)
    chars = result.new_state.characters

    assert chars["p1"].hp == 22, f"Stabby: got {chars['p1'].hp}"
    assert chars["p2"].hp == 20, f"Blob: got {chars['p2'].hp}"
    assert chars["p3"].hp == 22, f"Lawnmower: got {chars['p3'].hp}"
    assert chars["p4"].hp == 17, f"Gerald: got {chars['p4'].hp}"

    # Derived-stat spot checks straight from §12.
    assert chars["p1"].max_hp == 22 and chars["p1"].ac == 15
    assert chars["p2"].max_hp == 20 and chars["p2"].ac == 13
    assert chars["p3"].max_hp == 32 and chars["p3"].ac == 12
    assert chars["p4"].max_hp == 26 and chars["p4"].ac == 11

    # Initiative = pure Speed here.
    assert result.initiative_order == ["p1", "p2", "p3", "p4"]

    # Lawnmower auto-stepped to Gerald's zone before swinging.
    assert chars["p3"].zone_id == "glitter_back"
    moved = [e for e in result.events if e.type.value == "moved" and e.player_id == "p3"]
    assert moved and moved[0].data["to"] == "glitter_back"

    # Gerald's shield landed — one round too late to stop the SMASH.
    assert "shielded" in chars["p4"].conditions
    smash = next(e for e in result.events
                 if e.type.value == "attack_resolved" and e.data.get("move_id") == "smash")
    assert smash.data["result"] == "hit" and smash.data["ac"] == 11

    # BLAST hit EVERYONE in the zone — including Blob's own teammate.
    blast_hits = [e for e in result.events
                  if e.type.value == "attack_resolved" and e.data.get("move_id") == "blast"]
    assert {e.target_id for e in blast_hits} == {"p1", "p3"}

    # No-repeat bookkeeping: every fighter's combat move was recorded.
    assert chars["p1"].last_move_id == "trick"
    assert chars["p4"].last_move_id == "shield"

    assert not any(ch.is_ko for ch in chars.values())


# ---------------------------------------------------------------------------
# Unit: initiative
# ---------------------------------------------------------------------------


def test_initiative_order_speed():
    chars = [
        _char("slow", "Slow", power=2, speed=1, weird=2, zone="frontline"),
        _char("fast", "Fast", power=2, speed=4, weird=2, zone="frontline"),
    ]
    state = _state(chars, [])
    actions = [ClassifiedAction(player_id=p, move_id="shield") for p in ["slow", "fast"]]
    result = resolve_round(state, actions, Dice(seed=1), CFG)
    assert result.initiative_order == ["fast", "slow"]


def test_initiative_tie_broken_by_seeded_roll():
    """Tied speeds break by seeded roll (§5) — deterministic per seed, and both
    orders occur across seeds."""
    chars = [
        _char("aaa", "A", power=2, speed=2, weird=2, zone="frontline"),
        _char("zzz", "Z", power=2, speed=2, weird=2, zone="frontline"),
    ]
    actions = [ClassifiedAction(player_id=p, move_id="shield") for p in ["aaa", "zzz"]]

    orders = set()
    for seed in range(12):
        state = _state([c.model_copy(deep=True) for c in chars], [])
        result = resolve_round(state, actions, Dice(seed=seed), CFG)
        orders.add(tuple(result.initiative_order))
        # Same seed → same order.
        state2 = _state([c.model_copy(deep=True) for c in chars], [])
        assert resolve_round(state2, actions, Dice(seed=seed), CFG).initiative_order \
            == list(result.initiative_order)
    assert orders == {("aaa", "zzz"), ("zzz", "aaa")}


def test_sticky_reduces_initiative_speed():
    chars = [
        _char("sticky", "Sticky", power=2, speed=3, weird=2, zone="frontline",
              conditions={"sticky": 2}),   # effective speed 2
        _char("plain", "Plain", power=2, speed=2, weird=2, zone="frontline"),
        _char("mid", "Mid", power=2, speed=3, weird=2, zone="frontline"),
    ]
    state = _state(chars, [])
    actions = [ClassifiedAction(player_id=p, move_id="shield")
               for p in ["sticky", "plain", "mid"]]
    result = resolve_round(state, actions, Dice(seed=5), CFG)
    assert result.initiative_order[0] == "mid"   # only un-slowed speed-3 fighter


def test_initiative_drops_ko_and_gremlins():
    chars = _golden_chars()
    chars[1].is_ko = True
    chars[1].is_gremlin = True
    state = _state(chars, _TEAMS)
    result = resolve_round(state, _golden_actions(), Dice(seed=42), CFG)
    assert "p2" not in result.initiative_order


# ---------------------------------------------------------------------------
# Unit: degrees of success (2d6)
# ---------------------------------------------------------------------------


def _find_natural(pred, tries=2000):
    """Find a seed whose FIRST 2d6 roll satisfies pred (no initiative tie in a
    2-fighter duel with distinct speeds, so the first rolls are the attack)."""
    for seed in range(tries):
        if pred(Dice(seed=seed).two_d6()):
            return seed
    raise AssertionError("no seed found")


def _smash_duel(attacker_power=6, defender_speed=2):
    atk = _char("atk", "Atk", power=attacker_power, speed=4, weird=0, zone="frontline")
    dfn = _char("def", "Def", power=2, speed=defender_speed, weird=2, zone="frontline")
    return _duel(atk, dfn)


def test_natural_12_is_always_crit():
    seed = _find_natural(lambda n: n == 12)
    state = _smash_duel(attacker_power=0, defender_speed=6)   # weak vs high AC
    actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def"),
               ClassifiedAction(player_id="def", move_id="shield")]
    result = resolve_round(state, actions, Dice(seed=seed), CFG)
    ev = next(e for e in result.events if e.data.get("move_id") == "smash")
    assert ev.data["result"] == "crit"


def test_crit_on_margin_doubles_damage():
    """Beating AC by crit_margin crits and doubles the shared damage roll."""
    seed = _find_natural(lambda n: n >= 10)
    # POW 6 (+6) vs AC 10: natural 10+ → total 16+ vs 10 → margin ≥ 6 ≥ crit_margin.
    state = _smash_duel(attacker_power=6, defender_speed=0)
    actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=seed), CFG)
    ev = next(e for e in result.events if e.data.get("move_id") == "smash")
    assert ev.data["result"] == "crit"
    # 4d4+2 doubled: damage is even and within 2×(6..18).
    assert 12 <= ev.data["damage"] <= 36 and ev.data["damage"] % 2 == 0


def test_natural_2_fumbles_with_self_damage_and_embarrassed():
    seed = _find_natural(lambda n: n == 2)
    state = _smash_duel()
    actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=seed), CFG)
    ev = next(e for e in result.events if e.data.get("move_id") == "smash")
    assert ev.data["result"] == "fumble"
    chars = result.new_state.characters
    assert chars["atk"].hp == chars["atk"].max_hp - CFG.fumble_self_damage
    assert "embarrassed" in chars["atk"].conditions
    assert chars["def"].hp == chars["def"].max_hp   # no target effects on a fumble


def test_wild_card_fumbles_on_natural_3():
    """WILD CARD's fumble band is natural <= 3 (fumble_on_roll_lte)."""
    seed = _find_natural(lambda n: n == 3)
    state = _smash_duel()
    actions = [ClassifiedAction(player_id="atk", move_id="wild", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=seed), CFG)
    ev = next(e for e in result.events if e.data.get("move_id") == "wild")
    assert ev.data["result"] == "fumble"
    # The same natural 3 is a plain miss/hit for other moves, never a fumble.
    state2 = _smash_duel()
    actions2 = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
    result2 = resolve_round(state2, actions2, Dice(seed=seed), CFG)
    ev2 = next(e for e in result2.events if e.data.get("move_id") == "smash")
    assert ev2.data["result"] != "fumble"


def test_miss_leaves_target_untouched():
    seed = _find_natural(lambda n: n == 4)
    # POW 0 (+0) vs AC 16: natural 4 → total 4, clean miss.
    state = _smash_duel(attacker_power=0, defender_speed=6)
    actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=seed), CFG)
    ev = next(e for e in result.events if e.data.get("move_id") == "smash")
    assert ev.data["result"] == "miss"
    assert result.new_state.characters["def"].hp == \
        result.new_state.characters["def"].max_hp


def test_creativity_bonus_applies_to_roll():
    """Tier 3 adds +4 to the 2d6 roll; a stale drawing scores creativity 0."""
    seed = _find_natural(lambda n: n == 6)
    # POW 0 vs AC 10: natural 6 alone misses AC 10? 6 < 10 → miss; +4 → hit.
    state = _smash_duel(attacker_power=0, defender_speed=0)
    fresh = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def",
                              creativity_tier=3)]
    result = resolve_round(state, fresh, Dice(seed=seed), CFG)
    ev = next(e for e in result.events if e.data.get("move_id") == "smash")
    assert ev.data["result"] == "hit" and ev.data["total_atk"] == 10

    stale = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def",
                              creativity_tier=3, similar_to_previous=True)]
    state2 = _smash_duel(attacker_power=0, defender_speed=0)
    result2 = resolve_round(state2, stale, Dice(seed=seed), CFG)
    ev2 = next(e for e in result2.events if e.data.get("move_id") == "smash")
    assert ev2.data["result"] == "miss" and ev2.data["total_atk"] == 6


# ---------------------------------------------------------------------------
# Unit: the eight moves
# ---------------------------------------------------------------------------


def test_smash_damage_formula_scales_with_power():
    assert describe_formula("(1 + ceil(POW/2))d4 + 2", {"POW": 0, "SPD": 0, "WRD": 0}) == "1d4+2"
    assert describe_formula("(1 + ceil(POW/2))d4 + 2", {"POW": 6, "SPD": 0, "WRD": 0}) == "4d4+2"
    assert describe_formula("(1 + floor(WRD/3))d4 + 3", {"POW": 0, "SPD": 0, "WRD": 6}) == "3d4+3"
    assert describe_formula("1d6 + WRD", {"POW": 0, "SPD": 0, "WRD": 4}) == "1d6+4"
    assert describe_formula("2d8 + floor(WRD/2)", {"POW": 0, "SPD": 0, "WRD": 5}) == "2d8+2"


def test_smash_auto_steps_toward_target():
    atk = _char("atk", "Atk", power=6, speed=4, weird=0, zone="frontline")
    dfn = _char("def", "Def", power=2, speed=1, weird=2, zone="thunder_back")
    state = _duel(atk, dfn)
    actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=3), CFG)
    assert result.new_state.characters["atk"].zone_id == "thunder_back"
    assert any(e.type.value == "moved" and e.player_id == "atk" for e in result.events)
    # The swing happened (some attack event beyond the move).
    assert any(e.type.value == "attack_resolved" for e in result.events)


def test_smash_out_of_reach_when_two_zones_away():
    atk = _char("atk", "Atk", power=6, speed=4, weird=0, zone="glitter_back")
    dfn = _char("def", "Def", power=2, speed=1, weird=2, zone="thunder_back")
    state = _duel(atk, dfn)
    actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=3), CFG)
    # One step closed (glitter_back → frontline), but still out of reach.
    assert result.new_state.characters["atk"].zone_id == "frontline"
    ev = next(e for e in result.events if e.type.value == "attack_resolved")
    assert ev.data["result"] == "out_of_reach"
    assert result.new_state.characters["def"].hp == \
        result.new_state.characters["def"].max_hp


def test_blast_hits_everyone_in_zone_with_one_shared_roll():
    chars = [
        _char("cast", "Caster", power=0, speed=4, weird=6, zone="thunder_back"),
        _char("e1", "E1", power=2, speed=2, weird=2, zone="frontline"),
        _char("e2", "E2", power=2, speed=1, weird=2, zone="frontline"),
        _char("ally", "Ally", power=2, speed=3, weird=2, zone="frontline"),
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
    # One shared damage roll: every non-crit hit deals the same damage.
    hit_dmg = {e.data["damage"] for e in blast_evs if e.data["result"] == "hit"}
    assert len(hit_dmg) <= 1


def test_trick_applies_condition_from_drawing():
    atk = _char("atk", "Atk", power=0, speed=4, weird=6, zone="frontline")
    dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="frontline")
    state = _duel(atk, dfn)
    seed = _find_natural(lambda n: 8 <= n <= 10)   # comfortable hit, no fumble
    actions = [ClassifiedAction(player_id="atk", move_id="trick", target_id="def",
                                trick_condition="burning")]
    result = resolve_round(state, actions, Dice(seed=seed), CFG)
    assert "burning" in result.new_state.characters["def"].conditions
    # An unknown condition never crashes the engine — it is simply skipped.
    state2 = _duel(
        _char("atk", "Atk", power=0, speed=4, weird=6, zone="frontline"),
        _char("def", "Def", power=2, speed=0, weird=2, zone="frontline"))
    actions2 = [ClassifiedAction(player_id="atk", move_id="trick", target_id="def",
                                 trick_condition="made_up_nonsense")]
    result2 = resolve_round(state2, actions2, Dice(seed=seed), CFG)
    assert "made_up_nonsense" not in result2.new_state.characters["def"].conditions


def test_shield_adds_5_ac_when_it_resolves_first():
    """A shielded target's effective AC includes the +5 from the condition."""
    atk = _char("atk", "Atk", power=6, speed=1, weird=0, zone="frontline")
    dfn = _char("def", "Def", power=2, speed=6, weird=2, zone="frontline")   # acts first
    state = _duel(atk, dfn)
    seed = _find_natural(lambda n: n == 7)
    actions = [ClassifiedAction(player_id="def", move_id="shield", target_id="def"),
               ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=seed), CFG)
    smash = next(e for e in result.events if e.data.get("move_id") == "smash")
    assert smash.data["ac"] == 16 + 5   # AC 10+6 speed, +5 shielded


def test_shield_reflects_big_misses():
    """An attack missing a shielded target by 3+ deals 1d6 back."""
    atk = _char("atk", "Atk", power=0, speed=1, weird=0, zone="frontline")
    dfn = _char("def", "Def", power=2, speed=6, weird=2, zone="frontline")
    state = _duel(atk, dfn)
    seed = _find_natural(lambda n: n <= 6 and n > 2)   # low natural → big miss
    actions = [ClassifiedAction(player_id="def", move_id="shield", target_id="def"),
               ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=seed), CFG)
    reflect = [e for e in result.events if e.data.get("result") == "reflect"]
    assert reflect and reflect[0].target_id == "atk"
    assert 1 <= reflect[0].data["damage"] <= 6
    assert result.new_state.characters["atk"].hp == \
        result.new_state.characters["atk"].max_hp - reflect[0].data["damage"]


def test_rally_heals_cleanses_and_earns_pumped():
    healer = _char("atk", "Healer", power=2, speed=4, weird=2, zone="glitter_back")
    hurt = _char("ally", "Hurt", power=2, speed=2, weird=2, zone="frontline",
                 hp=5, conditions={"burning": 2, "enraged": 2})
    foe = _char("def", "Foe", power=2, speed=1, weird=2, zone="thunder_back")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["atk", "ally"]),
             Team(id="team_b", name="B", color="b", player_ids=["def"])]
    state = _state([healer, hurt, foe], teams)
    actions = [ClassifiedAction(player_id="atk", move_id="rally", target_id="ally",
                                creativity_tier=2)]
    result = resolve_round(state, actions, Dice(seed=9), CFG)
    ally = result.new_state.characters["ally"]
    # burning ticked 2 at round start (5→3), then RALLY healed 1d6+2.
    heal = next(e for e in result.events if e.type.value == "healed")
    assert 3 <= heal.data["amount"] <= 8
    assert ally.hp == 3 + heal.data["amount"]
    assert "burning" not in ally.conditions      # debuff cleansed
    assert "enraged" in ally.conditions          # buffs are never stripped
    assert "pumped" in ally.conditions           # earned: creativity 2 >= gate
    # Tier below the gate → no pumped.
    state2 = _state([healer.model_copy(deep=True),
                     hurt.model_copy(deep=True), foe.model_copy(deep=True)], teams)
    actions2 = [ClassifiedAction(player_id="atk", move_id="rally", target_id="ally",
                                 creativity_tier=1)]
    result2 = resolve_round(state2, actions2, Dice(seed=9), CFG)
    assert "pumped" not in result2.new_state.characters["ally"].conditions


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


def test_wild_interpretation_condition_rider_applies_on_hit():
    atk = _char("atk", "Atk", power=0, speed=4, weird=6, zone="frontline")
    dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="frontline")
    state = _duel(atk, dfn)
    # natural 6 → total 12 vs AC 10: a plain hit (no crit, so no KO wiping conditions).
    seed = _find_natural(lambda n: n == 6)
    actions = [ClassifiedAction(
        player_id="atk", move_id="wild", target_id="def",
        wild_interpretation=WildInterpretation(condition="sticky",
                                               description="a glue tornado"),
    )]
    result = resolve_round(state, actions, Dice(seed=seed), CFG)
    assert "sticky" in result.new_state.characters["def"].conditions


def test_movement_is_absolute_with_dodge_ac():
    mover = _char("atk", "Mover", power=2, speed=6, weird=2, zone="frontline")
    foe = _char("def", "Foe", power=6, speed=1, weird=2, zone="frontline")
    state = _duel(mover, foe)
    seed = _find_natural(lambda n: n == 7)
    actions = [ClassifiedAction(player_id="atk", move_id="move_l"),
               ClassifiedAction(player_id="def", move_id="trick", target_id="atk")]
    result = resolve_round(state, actions, Dice(seed=seed), CFG)
    ch = result.new_state.characters["atk"]
    assert ch.zone_id == "glitter_back"     # ◀ = one zone left in zones.yaml order
    assert "dodging" in ch.conditions
    atk_ev = next(e for e in result.events if e.data.get("move_id") == "trick")
    assert atk_ev.data["ac"] == 16 + 1      # 10+6 speed, +1 dodging
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
# Unit: adaptation, combos, confused
# ---------------------------------------------------------------------------


def test_dead_target_redirects_to_nearest_enemy():
    """A target KO'd earlier in the round → redirect, never reject (§9)."""
    atk = _char("p1", "Atk", power=0, speed=4, weird=6, zone="frontline")
    dead = _char("p2", "Dead", power=2, speed=2, weird=2, zone="frontline")
    dead.is_ko = True
    dead.is_gremlin = True
    other = _char("p3", "Other", power=2, speed=1, weird=2, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["p1"]),
             Team(id="team_b", name="B", color="b", player_ids=["p2", "p3"])]
    state = _state([atk, dead, other], teams)
    actions = [ClassifiedAction(player_id="p1", move_id="trick", target_id="p2")]
    result = resolve_round(state, actions, Dice(seed=4), CFG)
    ev = next(e for e in result.events if e.type.value == "attack_resolved")
    assert ev.target_id == "p3"


def test_combo_gives_both_partners_the_roll_bonus_and_one_combo_event():
    a1 = _char("a1", "A1", power=0, speed=4, weird=0, zone="frontline")
    a2 = _char("a2", "A2", power=0, speed=3, weird=0, zone="frontline")
    foe = _char("e1", "E1", power=2, speed=0, weird=2, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["a1", "a2"]),
             Team(id="team_b", name="B", color="b", player_ids=["e1"])]
    state = _state([a1, a2, foe], teams)
    actions = [
        ClassifiedAction(player_id="a1", move_id="smash", target_id="e1",
                         combo_partners=["a2"], combo_name="GLITTERNADO"),
        ClassifiedAction(player_id="a2", move_id="trick", target_id="e1",
                         combo_partners=["a1"], combo_name="GLITTERNADO"),
    ]
    result = resolve_round(state, actions, Dice(seed=8), CFG)
    combo_evs = [e for e in result.events if e.type.value == "combo"]
    assert len(combo_evs) == 1
    assert combo_evs[0].data["combo_name"] == "GLITTERNADO"
    # Both partners rolled with the +2: total = natural + stat(0) + combo_bonus.
    for mid in ("smash", "trick"):
        ev = next(e for e in result.events if e.data.get("move_id") == mid)
        assert ev.data["total_atk"] == ev.data["natural"] + CFG.combo_bonus


def test_confused_randomizes_target():
    atk = _char("p1", "Confused", power=6, speed=4, weird=2, zone="frontline",
                conditions={"confused": 2})
    ally = _char("p2", "Ally", power=2, speed=2, weird=2, zone="frontline")
    foe = _char("p3", "Foe", power=2, speed=1, weird=2, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["p1", "p2"]),
             Team(id="team_b", name="B", color="b", player_ids=["p3"])]
    hit_targets = set()
    for seed in range(10):
        state = _state([c.model_copy(deep=True) for c in (atk, ally, foe)], teams)
        actions = [ClassifiedAction(player_id="p1", move_id="smash", target_id="p3")]
        result = resolve_round(state, actions, Dice(seed=seed), CFG)
        for e in result.events:
            if e.type.value == "attack_resolved" and e.player_id == "p1" and e.target_id:
                hit_targets.add(e.target_id)
    assert "p2" in hit_targets   # friendly fire happened at least once


# ---------------------------------------------------------------------------
# Unit: KO, victory, sudden death, gremlins
# ---------------------------------------------------------------------------


def test_ko_converts_to_gremlin_and_victory_fires():
    atk = _char("atk", "Atk", power=6, speed=4, weird=0, zone="frontline")
    dfn = _char("def", "Def", power=0, speed=0, weird=2, zone="frontline", hp=1)
    state = _duel(atk, dfn)
    seed = _find_natural(lambda n: n >= 10)
    actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=seed), CFG)
    dead = result.new_state.characters["def"]
    assert dead.is_ko and dead.is_gremlin and dead.hp == 0 and dead.conditions == {}
    kinds = [e.type.value for e in result.events]
    assert "ko" in kinds and "victory" in kinds
    assert result.new_state.winner_team_id == "team_a"


def test_sudden_death_fires_after_max_rounds_and_boosts_attacks():
    atk = _char("atk", "Atk", power=2, speed=4, weird=0, zone="frontline")
    dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="frontline")
    state = _duel(atk, dfn)
    state.round = CFG.max_rounds
    seed = _find_natural(lambda n: n == 7)
    actions = [ClassifiedAction(player_id="atk", move_id="smash", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=seed), CFG)
    assert result.new_state.sudden_death is False or True   # flag set below
    assert any(e.type.value == "sudden_death" for e in result.events)
    assert result.new_state.sudden_death is True

    # Next round the +2 applies to the roll.
    state2 = _duel(
        _char("atk", "Atk", power=2, speed=4, weird=0, zone="frontline"),
        _char("def", "Def", power=2, speed=0, weird=2, zone="frontline"))
    state2.sudden_death = True
    result2 = resolve_round(state2, actions, Dice(seed=seed), CFG)
    ev = next(e for e in result2.events if e.data.get("move_id") == "smash")
    assert ev.data["total_atk"] == 7 + 2 + CFG.sudden_death_attack_bonus


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


def test_underdog_bonus_applies_when_far_behind():
    """Down two characters' worth of HP share → +1 on the roll (kids mode)."""
    a1 = _char("p1", "A1", power=2, speed=4, weird=2, zone="frontline", hp=2)
    a2 = _char("p2", "A2", power=2, speed=3, weird=2, zone="frontline", hp=2)
    a3 = _char("p3", "A3", power=2, speed=2, weird=2, zone="frontline", hp=2)
    b1 = _char("p4", "B1", power=2, speed=1, weird=2, zone="frontline")
    b2 = _char("p5", "B2", power=2, speed=1, weird=2, zone="frontline")
    b3 = _char("p6", "B3", power=2, speed=1, weird=2, zone="frontline")
    teams = [Team(id="team_a", name="A", color="p", player_ids=["p1", "p2", "p3"]),
             Team(id="team_b", name="B", color="b", player_ids=["p4", "p5", "p6"])]
    state = _state([a1, a2, a3, b1, b2, b3], teams)
    actions = [ClassifiedAction(player_id="p1", move_id="smash", target_id="p4")]
    result = resolve_round(state, actions, Dice(seed=7), CFG)
    ev = next(e for e in result.events if e.data.get("move_id") == "smash")
    # natural + 2 POW + 1 underdog
    assert ev.data["total_atk"] == ev.data["natural"] + 2 + CFG.underdog_attack_bonus


# ---------------------------------------------------------------------------
# Data-driven: a novel move added only to moves.yaml resolves
# ---------------------------------------------------------------------------


def test_novel_move_added_only_to_yaml_resolves(tmp_path, monkeypatch):
    import shutil

    import yaml

    import server.config as cfg_mod

    for f in ("moves.yaml", "conditions.yaml", "zones.yaml", "hazards.yaml",
              "balance.yaml"):
        shutil.copy(f"config/{f}", tmp_path / f)
    data = yaml.safe_load((tmp_path / "moves.yaml").read_text(encoding="utf-8"))
    data["moves"]["zap"] = {
        "stat": "speed", "range": "any", "target": "single_enemy",
        "damage": "1d4 + SPD", "button": "ZAP", "desc": "test archetype",
        "sfx": "zap",
    }
    (tmp_path / "moves.yaml").write_text(yaml.dump(data), encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)

    atk = _char("atk", "Atk", power=0, speed=6, weird=0, zone="frontline")
    dfn = _char("def", "Def", power=2, speed=0, weird=2, zone="frontline")
    state = _duel(atk, dfn)
    seed = _find_natural(lambda n: n >= 9)
    actions = [ClassifiedAction(player_id="atk", move_id="zap", target_id="def")]
    result = resolve_round(state, actions, Dice(seed=seed), CFG)
    ev = next(e for e in result.events if e.data.get("move_id") == "zap")
    assert ev.data["result"] in ("hit", "crit")
    assert ev.data["total_atk"] >= 9 + 6         # speed added to the roll
    assert 1 <= ev.data["damage"] <= 2 * (4 + 6)  # 1d4+6, possibly crit-doubled


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(25))
def test_resolver_never_negative_hp_and_unique_event_ids(seed):
    rng = Dice(seed=seed)
    pick = Dice(seed=seed + 1000)
    moves = ["smash", "blast", "trick", "shield", "rally", "wild", "move_l", "move_r"]
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
            assert ch.hp >= 0
            assert ch.hp <= ch.max_hp
            assert (ch.hp == 0) == ch.is_ko or ch.hp > 0
        for e in result.events:
            for ref in (e.player_id, e.target_id):
                assert ref is None or ref in result.new_state.characters
        state = result.new_state
        if state.winner_team_id:
            break
