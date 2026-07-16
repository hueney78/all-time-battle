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

    def __init__(self, inp, tid):
        self.input = inp
        self.id = tid


class _Resp:
    def __init__(self, inp, tid):
        self.content = [_ToolUse(inp, tid)]
        self.usage = _Usage()


class _Messages:
    def __init__(self, script):
        self.script = script
        self.calls = 0
        self.last_kwargs: dict = {}

    def create(self, **_kw):
        self.last_kwargs = _kw
        _enforce_tool_result_pairing(_kw.get("messages", []))
        item = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return _Resp(item, f"toolu_fake_{self.calls}")


def _enforce_tool_result_pairing(messages) -> None:
    """Mirror the real API rule that broke a live playtest: every assistant
    tool_use block must be answered by a tool_result with the same id in the
    IMMEDIATELY following user message — otherwise the API 400s."""
    for i, m in enumerate(messages):
        if m.get("role") != "assistant":
            continue
        ids = [b.id for b in m.get("content", [])
               if getattr(b, "type", None) == "tool_use"]
        if not ids:
            continue
        nxt = messages[i + 1] if i + 1 < len(messages) else {}
        results = set()
        if nxt.get("role") == "user" and isinstance(nxt.get("content"), list):
            results = {b.get("tool_use_id") for b in nxt["content"]
                       if isinstance(b, dict) and b.get("type") == "tool_result"}
        missing = [t for t in ids if t not in results]
        if missing:
            raise RuntimeError(
                f"400 invalid_request_error: `tool_use` ids were found without "
                f"`tool_result` blocks immediately after: {missing}")


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


# COMBAT V2: taps (move + target) arrive from the phone with each submission;
# p2's canvas is blank — the tapped move still resolves at creativity 0.
_SUBS = {"p1": ActionSubmission("p1", "data:image/png;base64,QUJD",
                                move_id="shoot", target_id="p2"),
         "p2": ActionSubmission("p2", "", move_id="smash", target_id="p1")}


# ---------------------------------------------------------------------------
# classify_actions
# ---------------------------------------------------------------------------
def test_classify_parses_forced_tool_use():
    script = [{"round": 1, "combos": [], "actions": [
        {"player_id": "p1", "creativity_tier": 2,
         "flavor_summary": "glitter arrows"}]}]
    ai = LiveAI(RULES, client=FakeAnthropic(script))
    actions = {a.player_id: a for a in ai.classify_actions(_two_player_state(), _SUBS, 1)}
    # The AI decorated p1's tapped SHOOT; the tap itself is untouched.
    assert actions["p1"].move_id == "shoot" and actions["p1"].target_id == "p2"
    assert actions["p1"].creativity_tier == 2
    assert actions["p1"].flavor_summary == "glitter arrows"
    # p2 was skipped by the AI → tapped move resolves at creativity 0.
    assert actions["p2"].move_id == "smash" and actions["p2"].creativity_tier == 0
    assert ai.degraded is False
    assert ai.client.messages.calls == 1


def test_classify_request_echoes_taps_and_labeled_image_pairs():
    """The user message tells the judge each fighter's tapped move + target
    (context, never a choice) and labels the ORIGINAL/ACTION image pair; a
    blank canvas is labeled rather than dropped (§11.1)."""
    script = [{"round": 1, "actions": []}]
    ai = LiveAI(RULES, client=FakeAnthropic(script))
    ai.classify_actions(_two_player_state(), _SUBS, 1)
    content = ai.client.messages.last_kwargs["messages"][0]["content"]
    texts = " | ".join(b["text"] for b in content if b.get("type") == "text")
    assert "tapped move: SHOOT" in texts and "targeting B (p2)" in texts
    assert "p1 ORIGINAL CHARACTER" in texts and "p1 ACTION THIS ROUND" in texts
    assert "blank canvas" in texts        # p2 drew nothing — still judged
    # The system prompt is the v2 template, sent with prompt caching.
    system = ai.client.messages.last_kwargs["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "TAPS" in system[0]["text"]


def test_classify_repairs_once_on_invalid_then_succeeds():
    bad = {"round": 1}   # missing required 'actions' → ValidationError
    good = {"round": 1, "actions": [
        {"player_id": "p1", "creativity_tier": 1}]}
    ai = LiveAI(RULES, client=FakeAnthropic([bad, good]))
    actions = {a.player_id: a for a in ai.classify_actions(_two_player_state(), _SUBS, 1)}
    assert ai.client.messages.calls == 2           # one repair retry
    assert actions["p1"].creativity_tier == 1
    assert ai.degraded is False


def test_classify_falls_back_to_tapped_moves_when_api_errors():
    """Total AI failure never blocks the round: every tapped move resolves at
    creativity 0 (the server owns the move, §11.1)."""
    ai = LiveAI(RULES, client=FakeAnthropic([RuntimeError("api down")]))
    actions = {a.player_id: a for a in ai.classify_actions(_two_player_state(), _SUBS, 1)}
    assert actions["p1"].move_id == "shoot" and actions["p2"].move_id == "smash"
    assert all(a.creativity_tier == 0 for a in actions.values())
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


def test_narration_text_includes_gallery_cameos():
    """Gallery names are injected into the narrate prompt as spectators (S4)."""
    from server.ai.client import _narration_text
    txt = _narration_text(_events(), _two_player_state().characters,
                          ["Old Stabby", "Grandpa Doodle"])
    assert "Old Stabby" in txt and "Grandpa Doodle" in txt and "Spectators" in txt


def test_narrate_accepts_gallery_names():
    script = [{"beats": [{"event_id": "e1", "text": "zap", "speaker": "pbp"}], "round_title": "T"}]
    ai = LiveAI(RULES, client=FakeAnthropic(script))
    n = ai.narrate_round(_events(), _two_player_state().characters, ["Ghosty"])
    assert n.beats                        # cameo names go into the prompt; response still parses


def test_mock_narration_uses_both_announcers():
    """MockAI splits beats across pbp/color so Track B can build speaker chips
    against AI_MODE=mock (the S1 mock fixtures)."""
    hit = Event(id="hit", type=EventType.ATTACK_RESOLVED, round=1, player_id="p1",
                target_id="p2", data={"result": "hit", "damage": 5})
    dodge = Event(id="dodge", type=EventType.ATTACK_RESOLVED, round=1, player_id="p2",
                  target_id="p1", data={"result": "dodge"})
    n = MockAI().narrate_round([hit, dodge], _two_player_state().characters)
    by = {b.event_id: b.speaker for b in n.beats}
    assert by["hit"] == "pbp" and by["dodge"] == "color"


def test_narration_never_leaks_zone_ids():
    """Playtest fix: announcers said 'glitter backline'. Zone ids in event data
    are translated to display names (team backlines carry the team name) in
    both the mock beats and the live request text."""
    from server.ai.client import _narration_text

    zn = {"glitter_back": "The Sparkle Snacks' backline", "frontline": "The Pit"}
    grem = Event(id="g1", type=EventType.GREMLIN_HAZARD, round=2, player_id="p1",
                 data={"hazard_id": "bees", "zone": "glitter_back", "affected": []})
    mv = Event(id="m1", type=EventType.MOVED, round=2, player_id="p1",
               data={"from": "glitter_back", "to": "frontline"})

    # MockAI beat text (also the live fallback path — same helper).
    n = MockAI().narrate_round([grem], _two_player_state().characters, None, zn)
    text = " ".join(b.text for b in n.beats)
    assert "glitter_back" not in text and "The Sparkle Snacks' backline" in text

    # The live narrator's request payload.
    req = _narration_text([grem, mv], _two_player_state().characters, None, zn)
    assert "glitter_back" not in req
    assert "The Sparkle Snacks' backline" in req and "The Pit" in req


# ---------------------------------------------------------------------------
# generate_characters
# ---------------------------------------------------------------------------
def test_generate_characters_normalizes_stats_and_names_teams():
    script = [{"characters": [{"player_id": "p1", "name": "Zap",
                               "stats": {"power": 9, "speed": 0, "weird": 0}}],
               "teams": {"team_a": "The Sparkle Snacks",
                         "team_b": "Heavy Machinery & Friend"}}]
    ai = LiveAI(RULES, client=FakeAnthropic(script))
    subs = {"p1": CharacterSubmission("p1", "data:image/png;base64,QUJD", "a dragon",
                                      team_id="team_a")}
    roster = ai.generate_characters(subs, RULES.balance)
    st = roster.characters["p1"].stats
    assert roster.characters["p1"].name == "Zap"
    assert st.power + st.speed + st.weird == RULES.balance.stat_budget
    # The same call names both teams (Track A #7).
    assert roster.team_names == {"team_a": "The Sparkle Snacks",
                                 "team_b": "Heavy Machinery & Friend"}


def test_generate_characters_team_names_fall_back_when_missing():
    """No/blank team names → plain Team A/B (the pre-reveal display)."""
    script = [{"characters": []}]
    ai = LiveAI(RULES, client=FakeAnthropic(script))
    subs = {"p1": CharacterSubmission("p1", _PNG, team_id="team_a")}
    roster = ai.generate_characters(subs, RULES.balance)
    assert roster.team_names == {"team_a": "Team A", "team_b": "Team B"}


def test_mock_generate_characters_names_both_teams():
    subs = {"p1": CharacterSubmission("p1", _PNG, team_id="team_a"),
            "p2": CharacterSubmission("p2", _PNG, team_id="team_b")}
    roster = MockAI().generate_characters(subs, RULES.balance)
    assert set(roster.team_names) == {"team_a", "team_b"}
    assert all(roster.team_names.values())


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
    assert len(out) == 1 and out[0].player_id == "p1" and out[0].move_id == "bees"
    assert ai.degraded is False and ai.client.messages.calls == 1


def test_classify_gremlin_skips_api_when_no_drawings():
    ai = LiveAI(RULES, client=FakeAnthropic([{"round": 2, "hazards": []}]))
    out = ai.classify_gremlin(_gremlin_state(), {"p1": ActionSubmission("p1", "")}, 2)
    assert out == [] and ai.client.messages.calls == 0   # nothing drawn → no API call
