"""Phase 5 — LiveAI client with a fake Anthropic transport.

Exercises forced tool-use parsing, the one-shot repair retry, and the non-AI
fallback path (degraded mode) without any network or API key.
"""

from __future__ import annotations

from server.ai.client import LiveAI
from server.ai.provider import ActionSubmission, CharacterSubmission, MockAI, make_ai
from server.config import load_game_rules
from server.engine.models import Character, Event, EventType, GameState, Stats, Team

RULES = load_game_rules()


# ---------------------------------------------------------------------------
# Fake Anthropic transport
# ---------------------------------------------------------------------------
class _Usage:
    input_tokens = 12
    output_tokens = 8
    cache_read_input_tokens = 0


class _ToolUse:
    type = "tool_use"

    def __init__(self, inp):
        self.input = inp


class _Resp:
    def __init__(self, inp):
        self.content = [_ToolUse(inp)]
        self.usage = _Usage()


class _Messages:
    def __init__(self, script):
        self.script = script
        self.calls = 0

    def create(self, **_kw):
        item = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


class FakeAnthropic:
    def __init__(self, script):
        self.messages = _Messages(script)


_PNG = "data:image/png;base64,QUJD"


def _two_player_state():
    a = Character(player_id="p1", name="A", stats=Stats(power=2, speed=2, weird=4),
                  hp=20, max_hp=20, ac=13, zone_id="glitter_back", character_png_b64=_PNG)
    b = Character(player_id="p2", name="B", stats=Stats(power=2, speed=2, weird=4),
                  hp=20, max_hp=20, ac=13, zone_id="thunder_back", character_png_b64=_PNG)
    teams = [Team(id="team_a", name="A", color="#f0f", player_ids=["p1"]),
             Team(id="team_b", name="B", color="#0ff", player_ids=["p2"])]
    return GameState(room_id="T", characters={"p1": a, "p2": b}, teams=teams)


_SUBS = {"p1": ActionSubmission("p1", "data:image/png;base64,QUJD"),
         "p2": ActionSubmission("p2", "")}   # p2 blank → validator stumble


# ---------------------------------------------------------------------------
# classify_actions
# ---------------------------------------------------------------------------
def test_classify_parses_forced_tool_use():
    script = [{"round": 1, "combos": [], "actions": [
        {"player_id": "p1", "catalog_id": "ray", "action_cost": 2, "targets": ["p2"]}]}]
    ai = LiveAI(RULES, client=FakeAnthropic(script))
    actions = {a.player_id: a for a in ai.classify_actions(_two_player_state(), _SUBS, 1)}
    assert actions["p1"].catalog_id == "ray" and actions["p1"].targets == ["p2"]
    assert actions["p2"].catalog_id == "stumble"   # blank canvas
    assert ai.degraded is False
    assert ai.client.messages.calls == 1


def test_classify_repairs_once_on_invalid_then_succeeds():
    bad = {"round": 1}   # missing required 'actions' → ValidationError
    good = {"round": 1, "actions": [
        {"player_id": "p1", "catalog_id": "strike", "action_cost": 1, "targets": ["p2"]}]}
    ai = LiveAI(RULES, client=FakeAnthropic([bad, good]))
    actions = {a.player_id: a for a in ai.classify_actions(_two_player_state(), _SUBS, 1)}
    assert ai.client.messages.calls == 2           # one repair retry
    assert actions["p1"].catalog_id == "strike"
    assert ai.degraded is False


def test_classify_falls_back_to_stumble_when_api_errors():
    ai = LiveAI(RULES, client=FakeAnthropic([RuntimeError("api down")]))
    actions = ai.classify_actions(_two_player_state(), _SUBS, 1)
    assert actions and all(a.catalog_id == "stumble" for a in actions)
    assert ai.degraded is True                      # host banner trigger
    assert ai.client.messages.calls == RULES.settings.ai.max_retries + 1


# ---------------------------------------------------------------------------
# narrate_round
# ---------------------------------------------------------------------------
def _events():
    return [Event(id="e1", type=EventType.ATTACK_RESOLVED, round=1, player_id="p1",
                  target_id="p2", data={"result": "hit", "damage": 5})]


def test_narrate_parses_and_titles():
    script = [{"beats": [{"event_id": "e1", "text": "KABOOM, a pigeon faints."}],
               "round_title": "Bird Down"}]
    ai = LiveAI(RULES, client=FakeAnthropic(script))
    chars = _two_player_state().characters
    n = ai.narrate_round(_events(), chars)
    assert n.round_title == "Bird Down" and n.beats[0].text.startswith("KABOOM")


def test_narrate_fallback_uses_template():
    ai = LiveAI(RULES, client=FakeAnthropic([RuntimeError("boom")]))
    n = ai.narrate_round(_events(), _two_player_state().characters)
    assert n.beats                                  # template narration, never empty
    assert ai.degraded is True


def test_narrate_parses_speaker_per_beat():
    """The announcer duo comes through structured: each beat keeps its voice."""
    script = [{"round_title": "Boom", "beats": [
        {"event_id": "e1", "text": "KABOOM, a pigeon faints.", "speaker": "pbp"},
        {"event_id": "e1", "text": "Mm. A pigeon.", "speaker": "color"}]}]
    ai = LiveAI(RULES, client=FakeAnthropic(script))
    n = ai.narrate_round(_events(), _two_player_state().characters)
    assert [b.speaker for b in n.beats] == ["pbp", "color"]


def test_classify_montage_parses_stat_grants():
    """The montage classifier returns one +1-stat grant per upgraded fighter."""
    script = [{"montages": [{"player_id": "p1", "stat": "power", "flavor": "swole"}]}]
    ai = LiveAI(RULES, client=FakeAnthropic(script))
    st = _two_player_state()
    out = ai.classify_montage(st, {"p1": ActionSubmission("p1", _PNG)}, 3)
    assert len(out) == 1 and out[0].player_id == "p1" and out[0].stat == "power"


def test_classify_montage_skips_api_when_no_drawings():
    ai = LiveAI(RULES, client=FakeAnthropic([{"montages": []}]))
    out = ai.classify_montage(_two_player_state(), {"p1": ActionSubmission("p1", "")}, 3)
    assert out == [] and ai.client.messages.calls == 0    # blank canvas → no grant, no call


# ---------------------------------------------------------------------------
# generate_awards (sync point S3)
# ---------------------------------------------------------------------------
def _match_summary():
    from server.ai.provider import MatchSummary
    return MatchSummary(winner_team_id="team_a", players=[
        {"player_id": "p1", "name": "A", "team_id": "team_a", "alive": True},
        {"player_id": "p2", "name": "B", "team_id": "team_b", "alive": False},
    ])


def test_generate_awards_parses_and_covers_all_players():
    script = [{"awards": [{"title": "Most Creative Doodle", "player_id": "p1", "blurb": "wow"}]}]
    ai = LiveAI(RULES, client=FakeAnthropic(script))
    awards = ai.generate_awards(_match_summary())
    # p1 from the AI, p2 backfilled by the validator — everyone covered.
    assert {a.player_id for a in awards} == {"p1", "p2"}


def test_mock_awards_cover_every_player():
    awards = MockAI().generate_awards(_match_summary())
    assert {a.player_id for a in awards} == {"p1", "p2"}
    assert all(a.title for a in awards)


def test_mock_narration_uses_both_announcers():
    """MockAI splits beats across pbp/color so Track B can build speaker chips
    against AI_MODE=mock (the S1 mock fixtures)."""
    hit = Event(id="hit", type=EventType.ATTACK_RESOLVED, round=1, player_id="p1",
                target_id="p2", data={"result": "hit", "damage": 5})
    miss = Event(id="miss", type=EventType.ATTACK_RESOLVED, round=1, player_id="p2",
                 target_id="p1", data={"result": "miss"})
    n = MockAI().narrate_round([hit, miss], _two_player_state().characters)
    by = {b.event_id: b.speaker for b in n.beats}
    assert by["hit"] == "pbp" and by["miss"] == "color"


# ---------------------------------------------------------------------------
# generate_characters
# ---------------------------------------------------------------------------
def test_generate_characters_normalizes_stats():
    script = [{"characters": [{"player_id": "p1", "name": "Zap",
                               "stats": {"power": 9, "speed": 0, "weird": 0}}]}]
    ai = LiveAI(RULES, client=FakeAnthropic(script))
    subs = {"p1": CharacterSubmission("p1", "data:image/png;base64,QUJD", "a dragon")}
    out = ai.generate_characters(subs, RULES.balance)
    st = out["p1"].stats
    assert out["p1"].name == "Zap"
    assert st.power + st.speed + st.weird == RULES.balance.stat_budget


# ---------------------------------------------------------------------------
# provider selection
# ---------------------------------------------------------------------------
def test_make_ai_mock_without_key(monkeypatch):
    monkeypatch.setenv("AI_MODE", "live")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(make_ai(RULES), MockAI)       # no key → safe mock


def test_make_ai_live_with_key(monkeypatch):
    monkeypatch.setenv("AI_MODE", "live")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-for-construction-only")
    ai = make_ai(RULES)
    assert isinstance(ai, LiveAI)                    # constructed, not called


# ---------------------------------------------------------------------------
# classify_gremlin
# ---------------------------------------------------------------------------
def _gremlin_state():
    st = _two_player_state()
    st.characters["p1"].is_ko = True
    st.characters["p1"].is_gremlin = True
    return st


def test_classify_gremlin_parses_hazard():
    script = [{"round": 2, "hazards": [
        {"player_id": "p1", "hazard_id": "bees", "adaptation_note": "an angry swarm"}]}]
    ai = LiveAI(RULES, client=FakeAnthropic(script))
    out = ai.classify_gremlin(_gremlin_state(), {"p1": ActionSubmission("p1", _PNG)}, 2)
    assert len(out) == 1 and out[0].player_id == "p1" and out[0].catalog_id == "bees"
    assert ai.degraded is False and ai.client.messages.calls == 1


def test_classify_gremlin_skips_api_when_no_drawings():
    ai = LiveAI(RULES, client=FakeAnthropic([{"round": 2, "hazards": []}]))
    out = ai.classify_gremlin(_gremlin_state(), {"p1": ActionSubmission("p1", "")}, 2)
    assert out == [] and ai.client.messages.calls == 0   # nothing drawn → no API call
