"""Phase 5 — AI response validators (pure, offline).

Covers stat normalization, flagged/missing-character fallback, catalog/target
repair, combo attachment, and narration salvage.
"""

from __future__ import annotations

from server.ai import schemas as S
from server.ai.provider import CharacterSubmission, MatchSummary
from server.ai.validators import (
    build_awards,
    build_classified_actions,
    build_generated_characters,
    build_gremlin_traps,
    build_montage,
    build_narration,
    normalize_stats,
)
from server.config import load_game_rules
from server.engine.models import Character, GameState, Stats, Team

RULES = load_game_rules()
CFG = RULES.balance


def _char(pid, zone="frontline"):
    return Character(player_id=pid, name=pid, stats=Stats(power=2, speed=2, weird=4),
                     hp=20, max_hp=20, zone_id=zone)


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
    out = build_generated_characters(resp, subs, CFG).characters
    assert set(out) == {"p1", "p2"}
    assert out["p1"].flagged is True
    assert out["p1"].stats.power + out["p1"].stats.speed + out["p1"].stats.weird == CFG.stat_budget
    assert out["p2"].name == "Mystery Blob"     # AI omitted p2 → deadpan fallback


def test_character_names_capped_at_two_words():
    """AI names are trimmed to two words — three only when the middle is a
    connector like 'of'/'the' (GAME_DESIGN §3 v6)."""
    from server.ai.validators import cap_character_name

    assert cap_character_name("Tim") == "Tim"
    assert cap_character_name("Princess Stabby") == "Princess Stabby"
    assert cap_character_name("Gerald the Buff") == "Gerald the Buff"      # connector kept
    assert cap_character_name("Duke of Spikes") == "Duke of Spikes"
    # Over the cap → truncated to the first two words.
    assert cap_character_name("Princess Stabby Duchess of Pointy Ends") == "Princess Stabby"
    assert cap_character_name("Sir Reginald Fancypants") == "Sir Reginald"  # middle not a connector
    assert cap_character_name("  spaced   out   name  ") == "spaced out"
    assert cap_character_name("") == ""                                     # caller adds 'Tim'


def test_generated_character_name_is_capped():
    resp = S.GenerateCharactersResponse(characters=[
        S.AICharacter(player_id="p1", name="Baron Von Sparkle the Third Explosion Machine",
                      stats=S.AIStats(power=2, speed=2, weird=5)),
    ])
    out = build_generated_characters(resp, {"p1": CharacterSubmission("p1")}, CFG).characters
    assert out["p1"].name == "Baron Von"       # trimmed to two words


def test_team_names_trimmed_and_backfilled():
    """AI team names are length-capped to fit meters/labels; blanks fall back
    to plain Team A/B."""
    from server.ai.validators import build_team_names

    resp = S.GenerateCharactersResponse(characters=[], teams=S.AITeamNames(
        team_a="  The Extraordinarily Long Sparkle Snack Battalion  ",
        team_b="",
    ))
    names = build_team_names(resp)
    assert names["team_a"] == "The Extraordinarily Long Spa"   # 28-char cap
    assert names["team_b"] == "Team B"
    assert build_team_names(S.GenerateCharactersResponse(characters=[])) == {
        "team_a": "Team A", "team_b": "Team B"}


# ---------------------------------------------------------------------------
# classify_actions
# ---------------------------------------------------------------------------
def _teams():
    return [Team(id="team_a", name="A", color="#f0f", player_ids=["p1"]),
            Team(id="team_b", name="B", color="#0ff", player_ids=["p2"])]


def test_classify_merges_judgment_onto_taps():
    """COMBAT V5: the AI decorates the tapped move; a skipped player still
    resolves their tap at creativity 0. ESCAPE's direction rides through."""
    state = _state({"p1": "glitter_back", "p2": "thunder_back"}, _teams())
    taps = {"p1": ("escape", "p2", -1), "p2": ("smash", "p1", 0)}
    resp = S.ClassifyActionsResponse(actions=[
        S.AIAction(player_id="p1", creativity_tier=9,           # clamped to 3
                   flavor_summary="a suspicious maneuver"),
    ])
    actions = {a.player_id: a for a in build_classified_actions(resp, state, taps, RULES)}
    a = actions["p1"]
    assert a.move_id == "escape" and a.target_id == "p2"   # taps are ground truth
    assert a.escape_direction == -1                        # ◀/▶ is ground truth too
    assert a.creativity_tier == 3                          # clamped
    assert a.flavor_summary == "a suspicious maneuver"
    b = actions["p2"]                                      # AI omitted p2
    assert b.move_id == "smash" and b.creativity_tier == 0


def test_classify_attaches_combo_to_both_partners():
    state = _state({"p1": "glitter_back", "p2": "glitter_back", "e": "thunder_back"},
                   [Team(id="team_a", name="A", color="#f0f", player_ids=["p1", "p2"]),
                    Team(id="team_b", name="B", color="#0ff", player_ids=["e"])])
    taps = {"p1": ("charge", "e", 0), "p2": ("blast", "e", 0), "e": ("smash", "p1", 0)}
    resp = S.ClassifyActionsResponse(
        combos=[S.AIComboSpec(partners=["p1", "p2"], combo_name="GLITTERNADO")],
        actions=[
            S.AIAction(player_id="p1"),
            S.AIAction(player_id="p2"),
            S.AIAction(player_id="e"),
        ],
    )
    built = build_classified_actions(resp, state, taps, RULES)
    actions = {a.player_id: a for a in built}
    # Both partners carry the combo — each gets the tier bonus in the engine.
    assert actions["p1"].combo_partners == ["p2"]
    assert actions["p2"].combo_partners == ["p1"]
    assert actions["p1"].combo_name == "GLITTERNADO"
    assert actions["e"].combo_partners == []


def test_classify_drops_cross_team_combos():
    state = _state({"p1": "glitter_back", "e": "thunder_back"},
                   [Team(id="team_a", name="A", color="#f0f", player_ids=["p1"]),
                    Team(id="team_b", name="B", color="#0ff", player_ids=["e"])])
    taps = {"p1": ("smash", "e", 0), "e": ("smash", "p1", 0)}
    resp = S.ClassifyActionsResponse(
        combos=[S.AIComboSpec(partners=["p1", "e"], combo_name="IMPOSSIBLE")],
        actions=[S.AIAction(player_id="p1")],
    )
    actions = {a.player_id: a for a in build_classified_actions(resp, state, taps, RULES)}
    assert actions["p1"].combo_partners == []             # enemies can't combo


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


# ---------------------------------------------------------------------------
# classify_gremlin — a trap planted in the TAPPED zone (creativity only)
# ---------------------------------------------------------------------------
def test_build_gremlin_traps_uses_tapped_zone_and_judges_creativity():
    """The zone is ground truth from the phone; the AI supplies creativity. A
    gremlin with no valid zone plants nothing; a missing AI entry → creativity 0."""
    resp = S.ClassifyGremlinsResponse(traps=[
        S.AIGremlinTrap(player_id="g1", creativity_tier=9, flavor_summary="a bear trap"),
    ])
    gremlin_taps = {"g1": "frontline", "g2": "not_a_zone", "g3": None}
    out = {a.player_id: a for a in build_gremlin_traps(resp, gremlin_taps, RULES)}

    assert out["g1"].trap_zone == "frontline" and out["g1"].move_id == ""
    assert out["g1"].creativity_tier == 3                    # clamped
    assert out["g1"].flavor_summary == "a bear trap"
    assert "g2" not in out                                   # invalid zone → no trap
    assert "g3" not in out                                   # no zone tapped → no trap


# ---------------------------------------------------------------------------
# generate_awards — victory ceremony (sync point S3)
# ---------------------------------------------------------------------------
def test_build_awards_guarantees_every_player_gets_one():
    """Keep valid AI awards; drop awards for unknown ids; add a fallback for any
    player the AI skipped — every player must end up with at least one."""
    summary = MatchSummary(players=[
        {"player_id": "p1", "name": "Stabby", "team_id": "team_a", "alive": True},
        {"player_id": "p2", "name": "Blob", "team_id": "team_b", "alive": False},
        {"player_id": "p3", "name": "Tim", "team_id": "team_a", "alive": True},
    ])
    resp = S.GenerateAwardsResponse(awards=[
        S.AIAward(title="Most Creative Doodle", player_id="p1", blurb="wow"),
        S.AIAward(title="Ghost Award", player_id="nobody", blurb="dropped"),  # unknown id
    ])
    awards = build_awards(resp, summary)
    by_player = {a.player_id for a in awards}
    assert by_player == {"p1", "p2", "p3"}          # p2 + p3 got fallbacks; nobody dropped
    assert all(a.title for a in awards)             # never blank


# ---------------------------------------------------------------------------
# classify_montage — power-up montage (sync point S2)
# ---------------------------------------------------------------------------
def test_build_montage_validates_stat_and_skips_non_survivors():
    """Known stats pass; an unknown stat defaults to weird; a result for a
    non-survivor (or a survivor who didn't draw) is dropped."""
    resp = S.ClassifyMontageResponse(montages=[
        S.AIMontage(player_id="p1", stat="power", flavor="swole"),
        S.AIMontage(player_id="p2", stat="banana", flavor="???"),
        S.AIMontage(player_id="ghost", stat="speed"),   # not a survivor
    ])
    out = {m.player_id: m for m in build_montage(resp, ["p1", "p2"])}
    assert out["p1"].stat == "power"
    assert out["p2"].stat == "weird"       # unknown → catch-all
    assert "ghost" not in out              # not among the survivors


# ---------------------------------------------------------------------------
# narrate — announcer duo (speaker field, sync point S1)
# ---------------------------------------------------------------------------
def test_build_narration_carries_and_clamps_speaker():
    """Each beat keeps its announcer voice; an unknown speaker defaults to pbp."""
    resp = S.NarrateResponse(beats=[
        S.AIBeat(event_id="e1", text="KABOOM!", speaker="pbp"),
        S.AIBeat(event_id="e2", text="It is not.", speaker="color"),
        S.AIBeat(event_id="e3", text="huh", speaker="bogus"),
    ], round_title="T")
    by = {b.event_id: b for b in build_narration(resp, {"e1", "e2", "e3"}).beats}
    assert by["e1"].speaker == "pbp"
    assert by["e2"].speaker == "color"
    assert by["e3"].speaker == "pbp"     # unknown voice → default
