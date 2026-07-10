"""Phase 3 — server, rooms, websockets, state machine.

Uses in-memory FakeSockets that satisfy the same Socket protocol as the real
starlette WebSocket, so the full RoomManager → dispatch → GameStateMachine path
runs exactly as it would over the wire, but deterministically and fast.
"""

from __future__ import annotations

import asyncio
import time

from server.ai.provider import MockAI
from server.config import load_game_rules
from server.engine.models import Character, ClassifiedAction, GameState, Stats
from server.gallery import GalleryStore
from server.protocol import C2S, Envelope, SubmitDrawingMsg, decode, encode, parse_payload
from server.room import Room, RoomManager, SocketDisconnect
from server.state_machine import GameStateMachine, Timers

# ---------------------------------------------------------------------------
# Test double: an in-memory websocket
# ---------------------------------------------------------------------------
_DISCONNECT = object()


class FakeSocket:
    def __init__(self) -> None:
        self._outgoing: asyncio.Queue[str] = asyncio.Queue()   # server → client
        self._incoming: asyncio.Queue = asyncio.Queue()        # client → server
        self.closed = False

    # -- server side (Socket protocol) --
    async def send_text(self, data: str) -> None:
        await self._outgoing.put(data)

    async def receive_text(self) -> str:
        item = await self._incoming.get()
        if item is _DISCONNECT:
            raise SocketDisconnect
        return item

    async def close(self, code: int = 1000) -> None:
        self.closed = True

    # -- client side (test helpers) --
    def client_send(self, msg_type: str, payload=None) -> None:
        self._incoming.put_nowait(encode(msg_type, payload))

    def client_disconnect(self) -> None:
        self._incoming.put_nowait(_DISCONNECT)

    async def client_recv(self) -> Envelope:
        raw = await self._outgoing.get()
        env = decode(raw)
        assert env is not None
        return env

    async def expect(self, *types: str, timeout: float = 5.0) -> Envelope:
        while True:
            env = await asyncio.wait_for(self.client_recv(), timeout)
            if env.type in types:
                return env


def _rules(snapshots: bool = False):
    rules = load_game_rules()
    rules.settings.snapshots.enabled = snapshots
    rules.settings.gallery.enabled = False   # no disk writes in driven-game tests
    return rules


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------
def test_protocol_envelope_roundtrip():
    raw = encode("join", {"role": "host"})
    env = decode(raw)
    assert env is not None
    assert env.v == 1 and env.type == "join" and env.payload["role"] == "host"


def test_protocol_parse_and_reject():
    parsed = parse_payload(
        C2S.SUBMIT_DRAWING, {"phase": "draw_action", "round": 2, "png_base64": "x"}
    )
    assert isinstance(parsed, SubmitDrawingMsg) and parsed.round == 2
    # Malformed JSON decodes to None instead of raising.
    assert decode("not json") is None
    # Unknown/typeless messages have no payload model.
    assert parse_payload("start_game", {}) is None


# ---------------------------------------------------------------------------
# Room registry & reconnection
# ---------------------------------------------------------------------------
def test_reconnect_reuses_player_and_replaces_socket():
    room = Room("TEST", _rules())
    s1 = FakeSocket()
    p = room.add_player("Alice", "player", s1, None)
    pid, team = p.id, p.team_id

    s2 = FakeSocket()
    again = room.add_player("Alice", "player", s2, pid)
    assert again is p                       # same Player object
    assert room.participants[pid].socket is s2
    assert len(room.players) == 1           # not duplicated
    assert room.team_of(pid) == team        # keeps its team


def test_teams_assigned_alternately_at_lobby():
    room = Room("TEST", _rules())
    ids = [room.add_player(f"P{i}", "player", FakeSocket(), None).id for i in range(4)]
    a = [i for i in ids if room.team_of(i) == "team_a"]
    b = [i for i in ids if room.team_of(i) == "team_b"]
    assert len(a) == 2 and len(b) == 2


# ---------------------------------------------------------------------------
# Collection: early-complete vs timeout auto-submit
# ---------------------------------------------------------------------------
async def test_collect_completes_early_then_times_out():
    room = Room("TEST", _rules())
    machine = GameStateMachine(room, _rules(), ai=MockAI(), timers=Timers(1, 1, 0.01))
    machine._phase = "draw_action"

    task = asyncio.create_task(machine._collect(["a", "b"], timeout=5.0))
    await asyncio.sleep(0)
    machine.submit_drawing("a", SubmitDrawingMsg(png_base64="x"))
    machine.submit_drawing("b", SubmitDrawingMsg(png_base64="x"))
    await asyncio.wait_for(task, 1.0)       # returns well before the 5s timeout
    assert machine._collected == {"a", "b"}

    # Nobody submits → returns on timeout with a partial set (auto-submit path).
    machine._phase = "draw_action"
    await machine._collect(["c"], timeout=0.02)
    assert machine._collected == set()


# ---------------------------------------------------------------------------
# Reconnect resync
# ---------------------------------------------------------------------------
async def test_resync_replays_current_state_to_reconnecting_player():
    rules = _rules()
    room = Room("TEST", rules)
    sock = FakeSocket()
    p = room.add_player("Alice", "player", sock, None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))
    room.machine = machine

    ch = Character(player_id=p.id, name="Alice", stats=Stats(power=2, speed=2, weird=4),
                   hp=20, max_hp=20, ac=13, zone_id="glitter_back")
    machine.state = GameState(room_id="TEST", characters={p.id: ch}, teams=room.teams)
    machine._phase, machine._round = "draw_action", 3

    await machine.resync(p.id)
    types = {(await asyncio.wait_for(sock.client_recv(), 1.0)).type for _ in range(4)}
    assert {"phase_change", "player_state", "canvas_init", "arena_state"} <= types


async def test_reveal_beats_carry_acting_player_for_sprite_swap():
    """Each reveal beat is tagged with the acting/target player so the host can
    swap that fighter's sprite to its action image during the beat."""
    from server.ai.provider import Beat, Narration
    from server.engine.models import Event, EventType

    rules = _rules()
    room = Room("TEST", rules)
    sock = FakeSocket()
    p = room.add_player("Alice", "player", sock, None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))

    ch = Character(player_id=p.id, name="Alice", stats=Stats(power=2, speed=2, weird=4),
                   hp=20, max_hp=20, ac=13, zone_id="glitter_back")
    machine.state = GameState(room_id="TEST", characters={p.id: ch}, teams=room.teams)

    ev = Event(id="e1", type=EventType.ATTACK_RESOLVED, round=1,
               player_id=p.id, target_id=p.id, data={"result": "hit"})
    narration = Narration(beats=[Beat(event_id="e1", text="Alice zaps someone")])

    await machine._reveal(1, narration, [ev])

    reveal = None
    for _ in range(6):
        env = await asyncio.wait_for(sock.client_recv(), 1.0)
        if env.type == "reveal_step":
            reveal = env
            break
    assert reveal is not None
    beat = reveal.payload["beats"][0]
    assert beat["event_id"] == "e1"
    assert beat["player_id"] == p.id and beat["target_id"] == p.id
    assert beat["hurt"] == p.id   # a hit flags the target as negatively impacted
    assert beat["helped"] is None
    assert beat["floats"] == []   # this fixture event carries no damage amount
    # New reveal fields the host consumes for the rail + meters.
    assert "initiative_order" in reveal.payload
    assert set(reveal.payload["meters"]) == {"hp_share", "audience"}


async def test_reveal_step_carries_initiative_meters_and_floats():
    """reveal_step ships the acting order, both meter positions, and per-beat
    impact/float data derived from engine events."""
    from server.ai.provider import Beat, Narration
    from server.engine.models import Event, EventType

    rules = _rules()
    room = Room("TEST", rules)
    a = room.add_player("A", "player", FakeSocket(), None)   # team_a
    b = room.add_player("B", "player", FakeSocket(), None)   # team_b
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))

    ca = Character(player_id=a.id, name="A", stats=Stats(power=2, speed=3, weird=2),
                   hp=24, max_hp=24, ac=14, zone_id="glitter_back")
    cb = Character(player_id=b.id, name="B", stats=Stats(power=2, speed=1, weird=2),
                   hp=4, max_hp=24, ac=12, zone_id="thunder_back")   # nearly dead
    machine.state = GameState(room_id="TEST", characters={a.id: ca, b.id: cb},
                              teams=room.teams)
    # team_a drew the creative move this round; team_b was bland.
    machine._accumulate_audience([
        ClassifiedAction(player_id=a.id, catalog_id="ray", action_cost=2, creativity_tier=3),
        ClassifiedAction(player_id=b.id, catalog_id="ray", action_cost=2, creativity_tier=0),
    ])

    crit = Event(id="e1", type=EventType.ATTACK_RESOLVED, round=1, player_id=a.id,
                 target_id=b.id, data={"result": "crit", "damage": 12})
    heal = Event(id="e2", type=EventType.HEALED, round=1, player_id=a.id,
                 target_id=a.id, data={"amount": 6})
    narration = Narration(beats=[Beat(event_id="e1", text="zap"), Beat(event_id="e2", text="heal")])

    await machine._reveal(1, narration, [crit, heal], [a.id, b.id])

    reveal = None
    for _ in range(6):
        env = await asyncio.wait_for(machine.room.participants[a.id].socket.client_recv(), 1.0)
        if env.type == "reveal_step":
            reveal = env
            break
    assert reveal is not None
    pay = reveal.payload
    assert pay["initiative_order"] == [a.id, b.id]
    # team_b nearly dead → knot pulled toward team_a → team_b HP fraction < 0.5
    assert pay["meters"]["hp_share"] < 0.5
    # team_a more creative → audience (team_b fraction) < 0.5
    assert pay["meters"]["audience"] < 0.5
    beats = {bt["event_id"]: bt for bt in pay["beats"]}
    assert beats["e1"]["hurt"] == b.id
    assert beats["e1"]["floats"] == [
        {"player_id": b.id, "amount": 12, "kind": "damage", "crit": True}
    ]
    assert beats["e2"]["helped"] == a.id
    assert beats["e2"]["floats"][0]["kind"] == "heal"


async def test_reveal_beats_carry_sfx_and_result():
    """Each beat ships its move's sound clip (moves.yaml sfx key, looked up
    from the event's catalog_id) and the attack result, so the host's audio
    manager can play move sounds and fire the fumble stinger from engine data."""
    from server.ai.provider import Beat, Narration
    from server.engine.models import Event, EventType

    rules = _rules()
    room = Room("TEST", rules)
    sock = FakeSocket()
    p = room.add_player("Alice", "player", sock, None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))

    ch = Character(player_id=p.id, name="Alice", stats=Stats(power=2, speed=2, weird=4),
                   hp=20, max_hp=20, ac=13, zone_id="glitter_back")
    machine.state = GameState(room_id="TEST", characters={p.id: ch}, teams=room.teams)

    zap = Event(id="e1", type=EventType.ATTACK_RESOLVED, round=1, player_id=p.id,
                target_id=p.id, data={"result": "fumble", "catalog_id": "ray"})
    ko = Event(id="e2", type=EventType.KO, round=1, player_id=p.id, data={})
    narration = Narration(beats=[Beat(event_id="e1", text="zap!"),
                                 Beat(event_id="e2", text="down!")])

    await machine._reveal(1, narration, [zap, ko])

    reveal = None
    for _ in range(6):
        env = await asyncio.wait_for(sock.client_recv(), 1.0)
        if env.type == "reveal_step":
            reveal = env
            break
    assert reveal is not None
    beats = {bt["event_id"]: bt for bt in reveal.payload["beats"]}
    assert beats["e1"]["sfx"] == rules.moves.moves["ray"].sfx  # "zap"
    assert beats["e1"]["result"] == "fumble"
    assert beats["e2"]["sfx"] is None        # KO has no catalog move — stinger only
    assert beats["e2"]["result"] is None


async def test_reveal_beats_carry_combo_name_for_splash():
    """A combo beat ships its fused-move name so the host can play the
    COMBO! splash; ordinary beats carry combo_name: None."""
    from server.ai.provider import Beat, Narration
    from server.engine.models import Event, EventType

    rules = _rules()
    room = Room("TEST", rules)
    sock = FakeSocket()
    p = room.add_player("Alice", "player", sock, None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))

    ch = Character(player_id=p.id, name="Alice", stats=Stats(power=2, speed=2, weird=4),
                   hp=20, max_hp=20, ac=13, zone_id="glitter_back")
    machine.state = GameState(room_id="TEST", characters={p.id: ch}, teams=room.teams)

    combo = Event(id="e1", type=EventType.COMBO, round=1, player_id=p.id,
                  data={"partners": [p.id], "combo_name": "GLITTERNADO SURF STRIKE"})
    plain = Event(id="e2", type=EventType.ATTACK_RESOLVED, round=1, player_id=p.id,
                  target_id=p.id, data={"result": "miss"})
    narration = Narration(beats=[Beat(event_id="e1", text="COMBO!"),
                                 Beat(event_id="e2", text="whiff")])

    await machine._reveal(1, narration, [combo, plain])

    reveal = None
    for _ in range(6):
        env = await asyncio.wait_for(sock.client_recv(), 1.0)
        if env.type == "reveal_step":
            reveal = env
            break
    assert reveal is not None
    beats = {bt["event_id"]: bt for bt in reveal.payload["beats"]}
    assert beats["e1"]["combo_name"] == "GLITTERNADO SURF STRIKE"
    assert beats["e2"]["combo_name"] is None


def test_arena_deltas_expose_stats_and_persistent_sprite():
    """Character deltas carry stats (rail/phone) and a sprite_png that persists
    the latest revealed action drawing (original portrait until first action)."""
    rules = _rules()
    room = Room("TEST", rules)
    p = room.add_player("A", "player", FakeSocket(), None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))
    ch = Character(player_id=p.id, name="A", stats=Stats(power=3, speed=2, weird=4),
                   hp=20, max_hp=20, ac=13, zone_id="glitter_back",
                   character_png_b64="ORIG")
    machine.state = GameState(room_id="TEST", characters={p.id: ch}, teams=room.teams)

    d = {x["player_id"]: x for x in machine._character_deltas(include_png=True)}[p.id]
    assert d["stats"] == {"power": 3, "speed": 2, "weird": 4}
    assert d["sprite_png"] == "ORIG"      # no action yet → original portrait

    machine._latest_action_png[p.id] = "ACTION"
    d2 = {x["player_id"]: x for x in machine._character_deltas(include_png=True)}[p.id]
    assert d2["sprite_png"] == "ACTION"   # battlefield sprite = revealed action
    assert d2["png"] == "ORIG"            # rail portrait stays the original


# ---------------------------------------------------------------------------
# The acceptance test: a full 4-player mock game to victory over websockets
# ---------------------------------------------------------------------------
async def _connect(manager: RoomManager, join_payload: dict):
    sock = FakeSocket()
    task = asyncio.create_task(manager.handle_socket(sock))
    sock.client_send("join", join_payload)
    return sock, task


async def test_full_4player_mock_game_reaches_victory_over_websockets():
    # Seed the global RNG so player ids (and thus the whole mock game, including
    # its length) are deterministic — otherwise "a montage fires" flakes on short
    # games. This seed yields a game long enough to reach a Power-Up Montage.
    import random
    random.seed(1)
    manager = RoomManager(_rules())
    conn_tasks = []

    host, ht = await _connect(manager, {"role": "host"})
    conn_tasks.append(ht)
    code = (await host.expect("joined")).payload["room"]

    players = []
    for i in range(4):
        s, t = await _connect(manager, {"role": "player", "name": f"P{i}", "room": code})
        conn_tasks.append(t)
        (await s.expect("joined"))
        players.append(s)

    async def player_driver(sock: FakeSocket) -> Envelope:
        while True:
            env = await sock.client_recv()
            if env.type == "game_over":
                return env
            if env.type == "phase_change" and env.payload.get("phase") in (
                "draw_characters", "draw_action", "montage",
            ):
                sock.client_send("submit_drawing", {
                    "phase": env.payload["phase"],
                    "round": env.payload["round"],
                    "png_base64": "doodle",   # non-blank → the mock attacks / upgrades
                })

    montages_seen: list[Envelope] = []

    async def host_driver(sock: FakeSocket) -> Envelope:
        while True:
            env = await sock.client_recv()
            if env.type == "game_over":
                return env
            if env.type == "montage_reveal":
                montages_seen.append(env)
            if env.type in ("reveal_step", "montage_reveal"):
                sock.client_send("next_beat")   # advance reveals + montages instantly

    drivers = [asyncio.create_task(host_driver(host))]
    drivers += [asyncio.create_task(player_driver(s)) for s in players]

    host.client_send("start_game")
    results = await asyncio.wait_for(asyncio.gather(*drivers), timeout=20.0)

    winners = {r.payload["winner_team_id"] for r in results}
    assert winners == {"team_a"} or winners == {"team_b"}, f"no decisive winner: {winners}"
    # A montage fires every few rounds and the pipeline absorbs it without stalling.
    assert montages_seen, "expected at least one Power-Up Montage in a full game"

    # Everyone saw the game end; tidy up the connection tasks.
    for s in players:
        s.client_disconnect()
    host.client_disconnect()
    await asyncio.wait_for(asyncio.gather(*conn_tasks, return_exceptions=True), timeout=5.0)


def test_mock_maps_blank_to_stumble_and_drawing_to_attack():
    """Auto-submit semantics: a blank canvas classifies as `stumble`; a real
    drawing becomes an attack aimed at a living enemy."""
    from server.ai.provider import ActionSubmission
    from server.engine.models import Team

    a = Character(player_id="a", name="A", stats=Stats(power=2, speed=2, weird=4),
                  hp=20, max_hp=20, ac=13, zone_id="glitter_back")
    b = Character(player_id="b", name="B", stats=Stats(power=2, speed=2, weird=4),
                  hp=20, max_hp=20, ac=13, zone_id="thunder_back")
    state = GameState(room_id="T", characters={"a": a, "b": b}, teams=[
        Team(id="team_a", name="A", color="#f0f", player_ids=["a"]),
        Team(id="team_b", name="B", color="#0ff", player_ids=["b"]),
    ])
    subs = {"a": ActionSubmission("a", "doodle"), "b": ActionSubmission("b", "")}
    actions = {act.player_id: act for act in MockAI().classify_actions(state, subs, 1)}

    assert actions["a"].catalog_id == "ray" and actions["a"].targets == ["b"]
    assert actions["b"].catalog_id == "stumble"  # blank canvas → hesitates


async def test_websocket_endpoint_accepts_host_and_creates_room():
    """Smoke test over the real starlette transport: the /ws endpoint accepts a
    host connection, issues a room code, and echoes lobby state."""
    from fastapi.testclient import TestClient

    from server.main import app

    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"v": 1, "type": "join", "payload": {"role": "host"}})
        joined = ws.receive_json()
        assert joined["type"] == "joined"
        assert len(joined["payload"]["room"]) == 4
        lobby = ws.receive_json()
        assert lobby["type"] == "lobby_state"


# ---------------------------------------------------------------------------
# Deliberation interlude under a slow AI (the Track A item-1 acceptance)
# ---------------------------------------------------------------------------
class _SlowAI:
    """Wraps the mock with a per-call delay to simulate a sluggish API. Because
    the state machine runs AI calls in a thread, this sleep does NOT block the
    event loop — the deliberation interlude stays live (never a frozen spinner).
    Records each classify call's [start, end] span so a test can prove the loop
    kept ticking during a slow classification."""

    degraded = False

    def __init__(self, delay: float) -> None:
        self._inner = MockAI()
        self._delay = delay
        self.classify_spans: list[tuple[float, float]] = []

    def generate_characters(self, submissions, cfg):
        return self._inner.generate_characters(submissions, cfg)

    def classify_actions(self, state, submissions, round_num):
        start = time.monotonic()
        time.sleep(self._delay)
        out = self._inner.classify_actions(state, submissions, round_num)
        self.classify_spans.append((start, time.monotonic()))
        return out

    def classify_gremlin(self, state, submissions, round_num):
        return self._inner.classify_gremlin(state, submissions, round_num)

    def classify_montage(self, state, submissions, round_num):
        return self._inner.classify_montage(state, submissions, round_num)

    def generate_awards(self, summary):
        return self._inner.generate_awards(summary)

    def narrate_round(self, events, characters, gallery_names=None):
        time.sleep(self._delay)
        return self._inner.narrate_round(events, characters, gallery_names)


async def test_deliberation_interlude_masks_slow_ai_and_orders_reveals():
    """A 15s-class AI stall is masked by the deliberation interlude, not a
    spinner: the moment drawings are in the TV shows them (a DELIBERATION
    message) while classify/resolve/narrate runs off the event loop; reveals
    stay in strict round order and the game still reaches a decisive game_over."""
    rules = _rules()
    room = Room("SLOW", rules)
    player_socks: dict[str, FakeSocket] = {}
    for i in range(2):
        s = FakeSocket()
        p = room.add_player(f"P{i}", "player", s, None)
        player_socks[p.id] = s
    host_sock = FakeSocket()
    room.add_player("Host", "host", host_sock, None)

    # Delay >> the interlude broadcast latency, so the interlude is guaranteed to
    # be on screen while the slow classify runs.
    slow = _SlowAI(delay=0.15)
    machine = GameStateMachine(room, rules, ai=slow, timers=Timers(0.05, 0.05, 0.01))
    room.machine = machine

    reveal_rounds: list[int] = []
    interludes: list[dict] = []
    heartbeats: list[float] = []
    done = asyncio.Event()

    async def player_pump(pid: str, sock: FakeSocket) -> None:
        while not done.is_set():
            try:
                env = await asyncio.wait_for(sock.client_recv(), 0.25)
            except TimeoutError:
                continue
            if env.type == "phase_change" and env.payload.get("phase") in (
                "draw_characters", "draw_action", "montage",
            ):
                machine.submit_drawing(pid, SubmitDrawingMsg(png_base64="doodle"))

    async def host_pump() -> None:
        while not done.is_set():
            try:
                env = await asyncio.wait_for(host_sock.client_recv(), 0.25)
            except TimeoutError:
                continue
            if env.type == "deliberation":
                interludes.append(env.payload)          # the interlude, not a spinner
            elif env.type == "reveal_step":
                reveal_rounds.append(env.payload["round"])
                machine.advance_beat()
            elif env.type == "montage_reveal":
                machine.advance_beat()
            elif env.type == "game_over":
                done.set()

    async def heartbeat() -> None:
        # A fine-grained ticker: if a blocking AI call ever froze the event loop,
        # these ticks would go silent for the whole call.
        while not done.is_set():
            heartbeats.append(time.monotonic())
            await asyncio.sleep(0.005)

    pumps = [asyncio.create_task(player_pump(pid, s)) for pid, s in player_socks.items()]
    pumps += [asyncio.create_task(host_pump()), asyncio.create_task(heartbeat())]
    machine.start()
    try:
        await asyncio.wait_for(done.wait(), timeout=20.0)
    finally:
        for t in pumps:
            t.cancel()
        if machine.task is not None:
            machine.task.cancel()
        await asyncio.gather(*pumps, machine.task, return_exceptions=True)

    # The interlude masked the wait: at least one deliberation was shown, carrying
    # the submitted drawings (never a spinner).
    assert interludes, "no deliberation interlude was shown under a slow AI"
    assert any(iv["kind"] == "deliberation" and iv["drawings"] for iv in interludes)

    # Ordering: intros (round 0) first, then rounds strictly ascending, no gaps.
    assert reveal_rounds[0] == 0, f"intros should reveal first: {reveal_rounds}"
    assert reveal_rounds == sorted(reveal_rounds), f"out-of-order reveals: {reveal_rounds}"
    non_intro = [r for r in reveal_rounds if r > 0]
    assert non_intro == list(range(1, len(non_intro) + 1)), f"gapped rounds: {reveal_rounds}"

    # No stall: the match finished with a winner despite the slow AI.
    assert done.is_set() and machine.state is not None
    assert machine.state.winner_team_id in {"team_a", "team_b"}

    # The interlude stayed LIVE (not a frozen spinner): the event loop kept ticking
    # throughout a slow classify — the call runs in a thread precisely for this.
    assert slow.classify_spans, "no classify calls recorded"
    assert any(
        sum(1 for hb in heartbeats if start <= hb <= end) >= 3
        for (start, end) in slow.classify_spans
    ), "event loop went silent during a slow AI call — interlude would be frozen"


# ---------------------------------------------------------------------------
# Arena Gremlin flow: KO'd players draw hazards into the round (GAME_DESIGN §10)
# ---------------------------------------------------------------------------
async def test_gremlin_draws_a_hazard_into_the_round():
    """A KO'd player is an Arena Gremlin: the pipeline keeps them in the draw
    roster, classifies their drawing as a hazard (separately from fighters'
    moves), and the resolver drops it — a GREMLIN_HAZARD event lands in the
    processed round."""
    from server.state_machine import _Drawn

    rules = _rules()
    room = Room("GREM", rules)
    a = room.add_player("A", "player", FakeSocket(), None)   # team_a
    b = room.add_player("B", "player", FakeSocket(), None)   # team_b
    c = room.add_player("C", "player", FakeSocket(), None)   # team_a (gremlin, teammate of A)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))

    fa = Character(player_id=a.id, name="A", stats=Stats(power=2, speed=2, weird=2),
                   hp=24, max_hp=24, ac=13, zone_id="glitter_back")
    fb = Character(player_id=b.id, name="B", stats=Stats(power=2, speed=2, weird=2),
                   hp=24, max_hp=24, ac=13, zone_id="thunder_back")
    grem = Character(player_id=c.id, name="Imp", stats=Stats(power=2, speed=2, weird=2),
                     hp=0, max_hp=24, ac=13, zone_id="glitter_back",
                     is_ko=True, is_gremlin=True)
    state = GameState(room_id="GREM",
                      characters={a.id: fa, b.id: fb, c.id: grem}, teams=room.teams)
    machine.state = state

    # The gremlin is in the draw roster (as a gremlin), the fighters as fighters.
    fighters, gremlins = machine._draw_roster()
    assert set(fighters) == {a.id, b.id} and gremlins == [c.id]

    drawn = _Drawn(round_num=1,
                   action_pngs={a.id: "doodle", b.id: "doodle", c.id: "hazard-doodle"},
                   fighters=fighters, gremlins=gremlins)
    processed = await machine._process_round(drawn)

    haz = [e for e in processed.events if e.type.value == "gremlin_hazard"]
    assert haz, "the gremlin's drawing should resolve into a hazard"
    assert haz[0].player_id == c.id
    assert haz[0].data["hazard_id"] in rules.hazards.hazards


# ---------------------------------------------------------------------------
# Announcer duo: reveal_step beats carry a speaker (sync point S1)
# ---------------------------------------------------------------------------
async def test_reveal_beats_carry_speaker_for_both_announcers():
    """Each reveal beat ships its announcer voice so the host can style pbp vs
    color chips differently — the S1 reveal_step contract for Track B."""
    from server.ai.provider import Beat, Narration
    from server.engine.models import Event, EventType

    rules = _rules()
    room = Room("SPKR", rules)
    p = room.add_player("Alice", "player", FakeSocket(), None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))
    ch = Character(player_id=p.id, name="Alice", stats=Stats(power=2, speed=2, weird=4),
                   hp=20, max_hp=20, ac=13, zone_id="glitter_back")
    machine.state = GameState(room_id="SPKR", characters={p.id: ch}, teams=room.teams)

    ev = Event(id="e1", type=EventType.ATTACK_RESOLVED, round=1, player_id=p.id,
               target_id=p.id, data={"result": "hit"})
    narration = Narration(beats=[
        Beat(event_id="e1", text="KABOOM!", speaker="pbp"),
        Beat(event_id="e1", text="It is not.", speaker="color"),
    ])
    await machine._reveal(1, narration, [ev])

    reveal = None
    for _ in range(6):
        env = await asyncio.wait_for(room.participants[p.id].socket.client_recv(), 1.0)
        if env.type == "reveal_step":
            reveal = env
            break
    assert reveal is not None
    assert [b["speaker"] for b in reveal.payload["beats"]] == ["pbp", "color"]


# ---------------------------------------------------------------------------
# Power-Up Montage: +1 stat, formula deltas, new "original" everywhere (S2)
# ---------------------------------------------------------------------------
class _MontageAI(MockAI):
    """MockAI that always grants a chosen stat, so a test can assert deltas."""

    def __init__(self, stat: str) -> None:
        self._stat = stat

    def classify_montage(self, state, submissions, round_num):
        from server.ai.provider import MontageResult
        return [MontageResult(player_id=pid, stat=self._stat, flavor="buffed")
                for pid, s in submissions.items() if s.png_base64.strip()]


async def test_montage_applies_stat_and_becomes_new_original():
    """A montage grants +1 stat (with formula deltas), swaps in the upgraded
    drawing as the new original everywhere, and broadcasts the stat pulse (S2)."""
    rules = _rules()
    room = Room("MTG", rules)
    p = room.add_player("A", "player", FakeSocket(), None)
    machine = GameStateMachine(room, rules, ai=_MontageAI("power"), timers=Timers(1, 1, 0.01))
    ch = Character(player_id=p.id, name="A", stats=Stats(power=2, speed=2, weird=2),
                   hp=18, max_hp=22, ac=13, zone_id="glitter_back", character_png_b64="OLD")
    machine.state = GameState(room_id="MTG", characters={p.id: ch}, teams=room.teams)
    machine._latest_action_png[p.id] = "STALE_SPRITE"

    results = await machine._resolve_montage(3, {p.id: "UPGRADED"}, [p.id])

    assert results[0].stat == "power"
    sch = machine.state.characters[p.id]
    assert sch.stats.power == 3
    assert sch.max_hp == 22 + rules.balance.hp_per_power   # Power +1 → +2 max HP
    assert sch.hp == 18 + rules.balance.hp_per_power         # healed by the gain
    assert sch.character_png_b64 == "UPGRADED"               # new original everywhere
    assert p.id not in machine._latest_action_png            # sprite baseline reset

    # the montage broadcast + refreshed canvas_init reached the player
    seen: dict[str, Envelope] = {}
    sock = room.participants[p.id].socket
    for _ in range(12):
        try:
            env = await asyncio.wait_for(sock.client_recv(), 1.0)
        except TimeoutError:
            break
        seen[env.type] = env
    assert "montage_reveal" in seen
    up = seen["montage_reveal"].payload["upgrades"][0]
    assert up["stat"] == "power" and up["png"] == "UPGRADED"
    assert seen["canvas_init"].payload["png"] == "UPGRADED"


def test_apply_montage_speed_recomputes_ac_and_noops_without_results():
    from server.ai.provider import MontageResult

    rules = _rules()
    room = Room("MTG2", rules)
    p = room.add_player("A", "player", FakeSocket(), None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))
    ch = Character(player_id=p.id, name="A", stats=Stats(power=2, speed=2, weird=2),
                   hp=20, max_hp=22, ac=13, zone_id="glitter_back", character_png_b64="OLD")
    state = GameState(room_id="MTG2", characters={p.id: ch}, teams=room.teams)

    machine._apply_montage(state, [MontageResult(p.id, "speed", "zoom")], {p.id: "NEW"})
    assert ch.stats.speed == 3
    assert ch.ac == rules.balance.ac_base + 3     # AC = ac_base + Speed
    assert ch.character_png_b64 == "NEW"

    # a blank montage (no results) changes nothing
    before = (ch.stats.speed, ch.ac, ch.character_png_b64)
    machine._apply_montage(state, [], {})
    assert (ch.stats.speed, ch.ac, ch.character_png_b64) == before


# ---------------------------------------------------------------------------
# Victory: awards ceremony + match poster in game_over (sync point S3)
# ---------------------------------------------------------------------------
async def test_game_over_carries_awards_and_poster(tmp_path):
    """The finale awards every player and writes a match poster, both surfaced
    in the game_over payload."""
    from pathlib import Path

    rules = _rules(snapshots=True)
    rules.settings.snapshots.dir = str(tmp_path)
    room = Room("FIN", rules)
    a = room.add_player("A", "player", FakeSocket(), None)
    b = room.add_player("B", "player", FakeSocket(), None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))
    ca = Character(player_id=a.id, name="A", stats=Stats(power=2, speed=2, weird=2),
                   hp=20, max_hp=20, ac=13, zone_id="glitter_back")
    cb = Character(player_id=b.id, name="B", stats=Stats(power=2, speed=2, weird=2),
                   hp=0, max_hp=20, ac=13, zone_id="thunder_back", is_ko=True)
    machine.state = GameState(room_id="FIN", characters={a.id: ca, b.id: cb},
                              teams=room.teams, winner_team_id="team_a")

    await machine._game_over()

    env = None
    sock = room.participants[a.id].socket
    for _ in range(8):
        e = await asyncio.wait_for(sock.client_recv(), 1.0)
        if e.type == "game_over":
            env = e
            break
    assert env is not None
    pay = env.payload
    assert pay["winner_team_id"] == "team_a"
    # every player receives at least one award (the ceremony's hard rule)
    assert {aw["player_id"] for aw in pay["awards"]} == {a.id, b.id}
    # a match poster was composed to snapshots/<room>/poster.png
    assert pay["poster_path"] and Path(pay["poster_path"]).exists()


async def test_game_over_without_winner_skips_ceremony():
    """A crash/no-winner finale still sends game_over — just no awards/poster."""
    rules = _rules()
    room = Room("NOP", rules)
    p = room.add_player("A", "player", FakeSocket(), None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))
    machine.state = None   # loop crashed before any state

    await machine._game_over()

    env = await asyncio.wait_for(room.participants[p.id].socket.client_recv(), 1.0)
    assert env.type == "game_over"
    assert env.payload["winner_team_id"] is None
    assert env.payload["awards"] == [] and env.payload["poster_path"] is None


# ---------------------------------------------------------------------------
# The Doodle Crowd: gallery persistence + narrator cameos + host roster (S4)
# ---------------------------------------------------------------------------
class _CameoSpyAI(MockAI):
    """Records the gallery_names handed to narrate_round."""

    def __init__(self) -> None:
        self.seen_cameos = None

    def narrate_round(self, events, characters, gallery_names=None):
        self.seen_cameos = gallery_names
        return super().narrate_round(events, characters, gallery_names)


async def test_gallery_names_are_sampled_into_narration():
    """Cameo names come from the gallery, capped, and never a current fighter."""
    from server.state_machine import _Drawn

    rules = _rules()
    room = Room("CAM", rules)
    a = room.add_player("A", "player", FakeSocket(), None)
    b = room.add_player("B", "player", FakeSocket(), None)
    spy = _CameoSpyAI()
    machine = GameStateMachine(room, rules, ai=spy, timers=Timers(1, 1, 0.01))
    ca = Character(player_id=a.id, name="Stabby", stats=Stats(power=2, speed=2, weird=2),
                   hp=24, max_hp=24, ac=13, zone_id="glitter_back")
    cb = Character(player_id=b.id, name="Blob", stats=Stats(power=2, speed=2, weird=2),
                   hp=24, max_hp=24, ac=13, zone_id="thunder_back")
    state = GameState(room_id="CAM", characters={a.id: ca, b.id: cb}, teams=room.teams)
    machine.state = state
    machine._gallery_names = ["Old Timer", "Grandpa Doodle", "Stabby"]   # Stabby also in ring

    drawn = _Drawn(round_num=1, action_pngs={a.id: "x", b.id: "x"},
                   fighters=[a.id, b.id], gremlins=[])
    await machine._process_round(drawn)

    assert spy.seen_cameos is not None
    assert len(spy.seen_cameos) <= rules.settings.gallery.cameo_count
    assert "Stabby" not in spy.seen_cameos                        # current fighter excluded
    assert set(spy.seen_cameos) <= {"Old Timer", "Grandpa Doodle"}


async def test_game_over_persists_characters_to_gallery(tmp_path):
    """Every character joins the Doodle Crowd at game over, win or lose (§15)."""
    rules = _rules()
    rules.settings.gallery.enabled = True
    rules.settings.gallery.dir = str(tmp_path)
    room = Room("GAL", rules)
    a = room.add_player("A", "player", FakeSocket(), None)
    b = room.add_player("B", "player", FakeSocket(), None)
    gallery = GalleryStore.from_rules(rules)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01),
                               gallery=gallery)
    ca = Character(player_id=a.id, name="Winner", stats=Stats(power=2, speed=2, weird=2),
                   hp=20, max_hp=20, ac=13, zone_id="glitter_back")
    cb = Character(player_id=b.id, name="Loser", stats=Stats(power=2, speed=2, weird=2),
                   hp=0, max_hp=20, ac=13, zone_id="thunder_back", is_ko=True)
    machine.state = GameState(room_id="GAL", characters={a.id: ca, b.id: cb},
                              teams=room.teams, winner_team_id="team_a")

    await machine._game_over()

    assert set(gallery.all_names()) == {"Winner", "Loser"}


async def test_host_join_receives_gallery_roster(tmp_path):
    """A host's bootstrap includes the Doodle Crowd roster for the stands (S4)."""
    rules = _rules()
    rules.settings.gallery.enabled = True
    rules.settings.gallery.dir = str(tmp_path)
    GalleryStore.from_rules(rules).save_match([
        {"name": "Ghost of Stabby", "png": "", "team_id": "team_a", "won": True, "room": "OLD"}])

    manager = RoomManager(rules)
    host, ht = await _connect(manager, {"role": "host"})
    env = await host.expect("gallery_roster")
    assert "Ghost of Stabby" in [s["name"] for s in env.payload["spectators"]]

    host.client_disconnect()
    await asyncio.wait_for(asyncio.gather(ht, return_exceptions=True), timeout=5.0)
