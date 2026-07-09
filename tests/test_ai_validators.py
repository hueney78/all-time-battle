"""Phase 5 — AI response validators (pure, offline).

Covers stat normalization, flagged/missing-character fallback, catalog/target/
condition/move_to repair, combo attachment, and narration salvage.
"""

from __future__ import annotations

from server.ai import schemas as S
from server.ai.provider import CharacterSubmission
from server.ai.validators import (
    build_classified_actions,
    build_generated_characters,
    build_narration,
    normalize_stats,
)
from server.config import load_game_rules
from server.engine.models import Character, GameState, Stats, Team

RULES = load_game_rules()
CFG = RULES.balance


def _char(pid, zone="frontline"):
    return Character(player_id=pid, name=pid, stats=Stats(power=2, speed=2, weird=4),
                     hp=20, max_hp=20, ac=13, zone_id=zone)


def _state(pids_zones: dict[str, str], teams):
    chars = {pid: _char(pid, z) for pid, z in pids_zones.items()}
    return GameState(room_id="T", characters=chars, teams=teams)


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
def test_normalize_stats_forces_budget_and_clamps():
    for p, s, w in [(4, 4, 4), (1, 1, 1), (0, 99, 0), (2, 3, 3)]:
        st = normalize_stats(p, s, w, CFG)
        assert st.power + st.speed + st.weird == CFG.stat_budget
        for v in (st.power, st.speed, st.weird):
            assert CFG.stat_min <= v <= CFG.stat_max


# ---------------------------------------------------------------------------
# generate_characters
# ---------------------------------------------------------------------------
def test_generated_characters_flagged_and_missing_fallback():
    resp = S.GenerateCharactersResponse(characters=[
        S.AICharacter(player_id="p1", name="Sir Rude", stats=S.AIStats(power=9, speed=0, weird=0),
                      personality="rude", announcer_intro="boo", flagged=True),
    ])
    subs = {"p1": CharacterSubmission("p1"), "p2": CharacterSubmission("p2")}
    out = build_generated_characters(resp, subs, CFG)
    assert set(out) == {"p1", "p2"}
    assert out["p1"].flagged is True
    assert out["p1"].stats.power + out["p1"].stats.speed + out["p1"].stats.weird == CFG.stat_budget
    assert out["p2"].name == "Mystery Blob"     # AI omitted p2 → deadpan fallback


# ---------------------------------------------------------------------------
# classify_actions
# ---------------------------------------------------------------------------
def _teams():
    return [Team(id="team_a", name="A", color="#f0f", player_ids=["p1"]),
            Team(id="team_b", name="B", color="#0ff", player_ids=["p2"])]


def test_classify_repairs_catalog_conditions_targets_and_move_to():
    state = _state({"p1": "glitter_back", "p2": "thunder_back"}, _teams())
    resp = S.ClassifyActionsResponse(actions=[
        S.AIAction(player_id="p1", catalog_id="NONSENSE", action_cost=9,
                   targets=["p2", "ghost"], move_to="thunder_back",         # not adjacent
                   suggested_conditions=["burning", "made_up"]),
    ])
    actions = {a.player_id: a for a in build_classified_actions(resp, state, ["p1", "p2"], RULES)}
    a = actions["p1"]
    assert a.catalog_id == "wildcard"                 # unknown id coerced
    assert 1 <= a.action_cost <= 3                     # clamped
    assert a.targets == ["p2"]                         # dead/unknown target dropped
    assert a.suggested_conditions == ["burning"]       # unknown condition dropped
    assert a.move_to is None                            # illegal (non-adjacent) drop
    assert actions["p2"].catalog_id == "stumble"       # AI omitted p2 → stumble


def test_classify_keeps_adjacent_move_and_attaches_combo():
    state = _state({"p1": "glitter_back", "p2": "glitter_back", "e": "thunder_back"},
                   [Team(id="team_a", name="A", color="#f0f", player_ids=["p1", "p2"]),
                    Team(id="team_b", name="B", color="#0ff", player_ids=["e"])])
    resp = S.ClassifyActionsResponse(
        combos=[S.AIComboSpec(partners=["p1", "p2"], leading_catalog_id="burst",
                              combo_name="GLITTERNADO")],
        actions=[
            S.AIAction(player_id="p1", catalog_id="ray", targets=["e"], move_to="frontline"),
            S.AIAction(player_id="p2", catalog_id="ray", targets=["e"]),
            S.AIAction(player_id="e", catalog_id="strike", targets=["p1"]),
        ],
    )
    built = build_classified_actions(resp, state, ["p1", "p2", "e"], RULES)
    actions = {a.player_id: a for a in built}
    assert actions["p1"].move_to == "frontline"        # adjacent → kept
    assert actions["p1"].combo_partners == ["p2"]      # leader carries the combo
    assert actions["p1"].combo_name == "GLITTERNADO"
    assert actions["p2"].combo_partners == []          # non-leader stays plain


# ---------------------------------------------------------------------------
# narrate
# ---------------------------------------------------------------------------
def test_narration_drops_unknown_events_and_carries_title():
    resp = S.NarrateResponse(beats=[
        S.AIBeat(event_id="e1", text="a real beat"),
        S.AIBeat(event_id="ghost", text="tied to nothing"),
    ], round_title="The Fish Learns to Surf")
    n = build_narration(resp, {"e1", "e2"})
    assert [b.event_id for b in n.beats] == ["e1"]
    assert n.round_title == "The Fish Learns to Surf"


def test_narration_salvages_when_all_ids_unknown():
    resp = S.NarrateResponse(beats=[S.AIBeat(event_id="ghost", text="still funny")])
    n = build_narration(resp, {"e1"})
    assert n.beats and n.beats[0].text == "still funny" and n.beats[0].event_id == "e1"
