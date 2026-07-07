"""Phase 3 — server, rooms, websockets, state machine.

Uses in-memory FakeSockets that satisfy the same Socket protocol as the real
starlette WebSocket, so the full RoomManager → dispatch → GameStateMachine path
runs exactly as it would over the wire, but deterministically and fast.
"""

from __future__ import annotations

import asyncio

from server.ai.provider import MockAI
from server.config import load_game_rules
from server.engine.models import Character, GameState, Stats
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


# ---------------------------------------------------------------------------
# The acceptance test: a full 4-player mock game to victory over websockets
# ---------------------------------------------------------------------------
async def _connect(manager: RoomManager, join_payload: dict):
    sock = FakeSocket()
    task = asyncio.create_task(manager.handle_socket(sock))
    sock.client_send("join", join_payload)
    return sock, task


async def test_full_4player_mock_game_reaches_victory_over_websockets():
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
                "draw_characters", "draw_action",
            ):
                sock.client_send("submit_drawing", {
                    "phase": env.payload["phase"],
                    "round": env.payload["round"],
                    "png_base64": "doodle",   # non-blank → the mock attacks
                })

    async def host_driver(sock: FakeSocket) -> Envelope:
        while True:
            env = await sock.client_recv()
            if env.type == "game_over":
                return env
            if env.type == "reveal_step":
                sock.client_send("next_beat")   # advance reveals instantly

    drivers = [asyncio.create_task(host_driver(host))]
    drivers += [asyncio.create_task(player_driver(s)) for s in players]

    host.client_send("start_game")
    results = await asyncio.wait_for(asyncio.gather(*drivers), timeout=20.0)

    winners = {r.payload["winner_team_id"] for r in results}
    assert winners == {"team_a"} or winners == {"team_b"}, f"no decisive winner: {winners}"

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
