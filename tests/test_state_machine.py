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
                   hp=20, max_hp=20, zone_id="glitter_back")
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
                   hp=20, max_hp=20, zone_id="glitter_back")
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
                   hp=24, max_hp=24, zone_id="glitter_back")
    cb = Character(player_id=b.id, name="B", stats=Stats(power=2, speed=1, weird=2),
                   hp=4, max_hp=24, zone_id="thunder_back")   # nearly dead
    machine.state = GameState(room_id="TEST", characters={a.id: ca, b.id: cb},
                              teams=room.teams)
    # team_a drew the creative move this round; team_b was bland.
    machine._accumulate_audience([
        ClassifiedAction(player_id=a.id, move_id="blast", creativity_tier=3),
        ClassifiedAction(player_id=b.id, move_id="blast", creativity_tier=0),
    ])

    devastating = Event(id="e1", type=EventType.ATTACK_RESOLVED, round=1, player_id=a.id,
                        target_id=b.id, data={"result": "devastating", "damage": 12})
    heal = Event(id="e2", type=EventType.HEALED, round=1, player_id=a.id,
                 target_id=a.id, data={"amount": 6})
    narration = Narration(beats=[Beat(event_id="e1", text="zap"), Beat(event_id="e2", text="heal")])

    await machine._reveal(1, narration, [devastating, heal], [a.id, b.id])

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
        {"player_id": b.id, "amount": 12, "kind": "damage", "devastating": True}
    ]
    assert beats["e2"]["helped"] == a.id
    assert beats["e2"]["floats"][0]["kind"] == "heal"


async def test_protect_is_one_beat_carrying_heal_float_and_shield_glow():
    """PROTECT resolves to a SINGLE `protected` event (heal + shield), so the
    couch sees ONE beat that carries the green heal float, the "helped" pop, and
    the shielded ally's round-long blue glow — never a separate heal beat and
    shield beat (change #2, §11.2)."""
    from server.ai.provider import Beat, Narration
    from server.engine.models import Event, EventType

    rules = _rules()
    room = Room("TEST", rules)
    a = room.add_player("A", "player", FakeSocket(), None)   # caster (team_a)
    b = room.add_player("B", "player", FakeSocket(), None)   # ally    (team_a)
    room.teams[0].player_ids = [a.id, b.id]
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))
    ca = Character(player_id=a.id, name="Pointy", stats=Stats(power=2, speed=3, weird=5),
                   hp=24, max_hp=24, zone_id="glitter_back")
    cb = Character(player_id=b.id, name="Buddy", stats=Stats(power=2, speed=1, weird=2),
                   hp=10, max_hp=24, zone_id="glitter_back")
    machine.state = GameState(room_id="TEST", characters={a.id: ca, b.id: cb},
                              teams=room.teams)

    prot = Event(id="p1", type=EventType.PROTECTED, round=1, player_id=a.id,
                 target_id=b.id, data={"amount": 7, "reflect_pct": 0.25})
    narration = Narration(beats=[Beat(event_id="p1", text="Pointy heals and shields Buddy!")])
    await machine._reveal(1, narration, [prot], [a.id, b.id])

    reveal = None
    for _ in range(6):
        env = await asyncio.wait_for(machine.room.participants[a.id].socket.client_recv(), 1.0)
        if env.type == "reveal_step":
            reveal = env
            break
    assert reveal is not None
    beat = reveal.payload["beats"][0]
    assert len(reveal.payload["beats"]) == 1          # one beat, not two
    assert beat["helped"] == b.id
    assert beat["floats"] == [{"player_id": b.id, "amount": 7, "kind": "heal",
                               "devastating": False}]
    assert beat["shield_on"] == b.id                  # ally's round-long glow
    assert beat["move_name"] == rules.moves.moves["protect"].button


async def test_reveal_beats_carry_sfx_and_result():
    """Each beat ships its move's sound clip (moves.yaml sfx key, looked up
    from the event's move_id) and the attack result, so the host's audio
    manager can play move sounds and fire event stingers from engine data."""
    from server.ai.provider import Beat, Narration
    from server.engine.models import Event, EventType

    rules = _rules()
    room = Room("TEST", rules)
    sock = FakeSocket()
    p = room.add_player("Alice", "player", sock, None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))

    ch = Character(player_id=p.id, name="Alice", stats=Stats(power=2, speed=2, weird=4),
                   hp=20, max_hp=20, zone_id="glitter_back")
    machine.state = GameState(room_id="TEST", characters={p.id: ch}, teams=room.teams)

    boom = Event(id="e1", type=EventType.ATTACK_RESOLVED, round=1, player_id=p.id,
                 target_id=p.id, data={"result": "hit", "move_id": "blast", "damage": 5})
    ko = Event(id="e2", type=EventType.KO, round=1, player_id=p.id, data={})
    narration = Narration(beats=[Beat(event_id="e1", text="boom!"),
                                 Beat(event_id="e2", text="down!")])

    await machine._reveal(1, narration, [boom, ko])

    reveal = None
    for _ in range(6):
        env = await asyncio.wait_for(sock.client_recv(), 1.0)
        if env.type == "reveal_step":
            reveal = env
            break
    assert reveal is not None
    beats = {bt["event_id"]: bt for bt in reveal.payload["beats"]}
    assert beats["e1"]["sfx"] == rules.moves.moves["blast"].sfx  # "boom"
    assert beats["e1"]["result"] == "hit"
    assert beats["e2"]["sfx"] is None        # KO has no catalog move — stinger only
    assert beats["e2"]["result"] is None


async def test_reveal_beats_carry_the_plain_language_readout():
    """GAME_DESIGN §13's damage readout: one addition, one total, per line;
    zero terms omitted; reductions on their own line, never a rewrite of the
    first. Terms come from the engine, so the line can't disagree with the HP bar.
    """
    from server.ai.provider import Beat, Narration
    from server.engine.models import Event, EventType

    rules = _rules()
    room = Room("TEST", rules)
    sock = FakeSocket()
    a = room.add_player("Stabby", "player", sock, None)
    b = room.add_player("Gerald", "player", FakeSocket(), None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))

    ca = Character(player_id=a.id, name="Stabby", stats=Stats(power=1, speed=5, weird=3),
                   hp=34, max_hp=34, zone_id="glitter_back")
    cb = Character(player_id=b.id, name="Blob", stats=Stats(power=0, speed=3, weird=6),
                   hp=34, max_hp=34, zone_id="thunder_back")
    machine.state = GameState(room_id="TEST", characters={a.id: ca, b.id: cb},
                              teams=room.teams)

    # The §13 worked example: BLAST, 2d4 showing 5, Weird 5, creativity tier 2,
    # then a shielded Blob reflects 3 back at Stabby.
    shot = Event(id="e1", type=EventType.ATTACK_RESOLVED, round=1, player_id=a.id,
                 target_id=b.id,
                 data={"result": "hit", "move_id": "blast", "damage": 10, "raw": 13,
                       "dice": 5, "stat": "weird", "stat_value": 5, "riders": 0,
                       "creativity_tier": 2, "creativity_bonus": 3, "absorbed": 3})
    reflected = Event(id="e2", type=EventType.ATTACK_RESOLVED, round=1, player_id=b.id,
                      target_id=a.id, data={"result": "reflect", "move_id": "protect",
                                            "damage": 3})
    narration = Narration(beats=[Beat(event_id="e1", text="boom"),
                                 Beat(event_id="e2", text="ping")])

    await machine._reveal(1, narration, [shot, reflected])

    reveal = None
    for _ in range(6):
        env = await asyncio.wait_for(sock.client_recv(), 1.0)
        if env.type == "reveal_step":
            reveal = env
            break
    assert reveal is not None
    beats = {bt["event_id"]: bt for bt in reveal.payload["beats"]}

    assert beats["e1"]["readout"] == [
        "🔥 BLAST → 🎲 5 + 🌀 Weird 5 + ⭐⭐ Creative 3 = 13 damage",
    ]
    # A reflect adds up to nothing, so it gets only its reflect line.
    assert beats["e2"]["readout"] == ["🛡️ Blob's shield reflects 3 back at Stabby!"]


async def test_readout_omits_zero_terms_and_flags_devastating():
    """Creativity 0 simply doesn't appear; tier 3 swaps the star chip for the
    DEVASTATING flourish (§13)."""
    from server.ai.provider import Beat, Narration
    from server.engine.models import Event, EventType

    rules = _rules()
    room = Room("TEST", rules)
    sock = FakeSocket()
    a = room.add_player("Brick", "player", sock, None)
    b = room.add_player("Foe", "player", FakeSocket(), None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))
    ca = Character(player_id=a.id, name="Brick", stats=Stats(power=6, speed=2, weird=1),
                   hp=41, max_hp=41, zone_id="frontline")
    cb = Character(player_id=b.id, name="Foe", stats=Stats(power=0, speed=0, weird=0),
                   hp=28, max_hp=28, zone_id="frontline")
    machine.state = GameState(room_id="TEST", characters={a.id: ca, b.id: cb},
                              teams=room.teams)

    bland = Event(id="e1", type=EventType.ATTACK_RESOLVED, round=1, player_id=a.id,
                  target_id=b.id,
                  data={"result": "hit", "move_id": "smash", "damage": 11, "raw": 11,
                        "dice": 5, "stat": "power", "stat_value": 6, "riders": 0,
                        "creativity_tier": 0, "creativity_bonus": 0})
    huge = Event(id="e2", type=EventType.ATTACK_RESOLVED, round=1, player_id=a.id,
                 target_id=b.id,
                 data={"result": "devastating", "move_id": "smash", "damage": 16,
                       "raw": 16, "dice": 5, "stat": "power", "stat_value": 6,
                       "riders": 0, "creativity_tier": 3, "creativity_bonus": 5})
    narration = Narration(beats=[Beat(event_id="e1", text="thud"),
                                 Beat(event_id="e2", text="BOOM")])

    await machine._reveal(1, narration, [bland, huge])
    reveal = None
    for _ in range(6):
        env = await asyncio.wait_for(sock.client_recv(), 1.0)
        if env.type == "reveal_step":
            reveal = env
            break
    beats = {bt["event_id"]: bt for bt in reveal.payload["beats"]}

    # Creativity 0 → the term is simply absent, and there's exactly one total.
    assert beats["e1"]["readout"] == ["💥 SMASH → 🎲 5 + 💪 Power 6 = 11 damage"]
    assert beats["e2"]["readout"] == [
        "💥 SMASH → 🎲 5 + 💪 Power 6 + ⭐⭐⭐ DEVASTATING! 5 = 16 damage"
    ]


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
                   hp=20, max_hp=20, zone_id="glitter_back")
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
                   hp=20, max_hp=20, zone_id="glitter_back",
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
        # A dumb phone: remembers its latest button grid and taps the first
        # legal attack each action round (exercising the submit_action path);
        # once KO'd it plants traps like a proper Gremlin.
        moves: list[dict] = []
        is_ko = False
        while True:
            env = await sock.client_recv()
            if env.type == "game_over":
                return env
            if env.type == "player_state":
                moves = env.payload.get("moves") or moves
                is_ko = bool(env.payload.get("is_ko"))
            if env.type == "phase_change" and env.payload.get("phase") in (
                "draw_characters", "montage",
            ):
                sock.client_send("submit_drawing", {
                    "phase": env.payload["phase"],
                    "round": env.payload["round"],
                    "png_base64": "doodle",   # non-blank → the mock upgrades
                })
            elif env.type == "phase_change" and env.payload.get("phase") == "draw_action":
                if is_ko:   # Gremlins plant a trap in a zone, no move tap
                    sock.client_send("submit_action", {
                        "round": env.payload["round"],
                        "png_base64": "doodle",
                        "trap_zone": "frontline",
                    })
                    continue
                pick = next((m for m in moves
                             if not m["disabled"] and m["target"] == "single_enemy"), None)
                sock.client_send("submit_action", {
                    "round": env.payload["round"],
                    "png_base64": "doodle",
                    "move_id": pick["id"] if pick else "",
                    "target_id": None,      # server redirects to the nearest enemy
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


async def test_submit_action_validates_no_repeat_smash_reach_and_dead_target():
    """The server owns the tap rules (COMBAT V5 §4.1): no move repeats, SMASH
    needs a same-zone enemy, no dead targets. Rejections answer with an
    action_rejected toast and leave the round unsubmitted."""
    from server.protocol import SubmitActionMsg

    rules = _rules()
    room = Room("TEST", rules)
    sock = FakeSocket()
    p = room.add_player("Alice", "player", sock, None)      # team_a
    other_sock = FakeSocket()
    o = room.add_player("Bob", "player", other_sock, None)   # team_b (KO'd gremlin)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))

    ca = Character(player_id=p.id, name="Alice", stats=Stats(power=2, speed=2, weird=4),
                   hp=20, max_hp=20, zone_id="glitter_back", last_move_id="blast")
    cb = Character(player_id=o.id, name="Bob", stats=Stats(power=2, speed=2, weird=4),
                   hp=0, max_hp=20, zone_id="thunder_back",
                   is_ko=True, is_gremlin=True)
    machine.state = GameState(room_id="TEST", characters={p.id: ca, o.id: cb},
                              teams=room.teams)
    machine._phase = "draw_action"
    machine._expected = {p.id}
    machine._collected = set()

    async def rejected(move_id, target_id=None):
        await machine.submit_action(
            p.id, SubmitActionMsg(round=2, png_base64="x",
                                  move_id=move_id, target_id=target_id))
        env = await asyncio.wait_for(sock.client_recv(), 1.0)
        assert env.type == "toast" and env.payload["kind"] == "action_rejected"
        assert p.id not in machine._action_taps and p.id not in machine._collected

    await rejected("blast")                    # no-repeat (last move was blast)
    await rejected("smash")                    # no enemy in Alice's zone (Bob is KO'd)
    await rejected("charge", target_id=o.id)   # dead target
    await rejected("nonsense")                 # unknown move

    # A legal tap is recorded (with its escape direction) and counts as submitted.
    await machine.submit_action(
        p.id, SubmitActionMsg(round=2, png_base64="x", move_id="escape",
                              target_id=None, escape_direction=1))
    assert machine._action_taps[p.id] == ("escape", None, 1, None)
    assert p.id in machine._collected

    # A gremlin's submit_action plants a trap in the tapped zone (no move tap).
    machine._expected = {o.id}
    machine._collected = set()
    await machine.submit_action(
        o.id, SubmitActionMsg(round=2, png_base64="x", trap_zone="frontline"))
    assert machine._action_taps[o.id] == ("", None, 0, "frontline")
    assert o.id in machine._collected

    # An empty tap (timer auto-submit) is accepted — the fighter stumbles.
    machine._expected = {p.id}
    machine._collected = set()
    machine._action_taps.clear()
    await machine.submit_action(p.id, SubmitActionMsg(round=2, png_base64="x"))
    assert p.id in machine._collected and p.id not in machine._action_taps


async def test_player_state_ships_move_buttons_with_live_math():
    """player_state carries the five-button grid with this character's live
    math and disabled states — the phone renders, the server decides."""
    rules = _rules()
    room = Room("TEST", rules)
    sock = FakeSocket()
    p = room.add_player("Alice", "player", sock, None)       # team_a
    o = room.add_player("Bob", "player", FakeSocket(), None)  # team_b
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))

    ch = Character(player_id=p.id, name="Alice", stats=Stats(power=6, speed=2, weird=1),
                   hp=32, max_hp=32, zone_id="glitter_back", last_move_id="blast")
    foe = Character(player_id=o.id, name="Bob", stats=Stats(power=2, speed=2, weird=2),
                    hp=32, max_hp=32, zone_id="thunder_back")   # NOT in Alice's zone
    machine.state = GameState(room_id="TEST", characters={p.id: ch, o.id: foe},
                              teams=room.teams)

    await machine._send_player_state(p.id)
    env = await asyncio.wait_for(sock.client_recv(), 1.0)
    assert env.type == "player_state"
    assert env.payload["last_move_id"] == "blast"
    moves = {m["id"]: m for m in env.payload["moves"]}
    assert set(moves) == {"smash", "blast", "charge", "escape", "protect"}
    assert moves["smash"]["math"] == "2d4+8"        # POW 6 → live math on the label
    assert moves["blast"]["math"] == "2d4+3"        # WRD 1 + 2
    assert moves["charge"]["math"] == "2d4+4"       # avg(POW 6, SPD 2) = 4
    assert moves["escape"]["math"] == "2d4+2"       # SPD 2
    assert moves["protect"]["math"] == "♥ 1d6+1"    # 1d6 + WRD 1
    # The label promises the base; creativity is unknowable until the drawing
    # is judged, so it never appears on a button.
    assert not any("✨" in m["math"] for m in moves.values())
    assert moves["blast"]["disabled"] and moves["blast"]["disabled_reason"] == "no_repeat"
    # SMASH is greyed with no enemy in Alice's zone; PROTECT with no living ally.
    assert moves["smash"]["disabled"] and moves["smash"]["disabled_reason"] == "no_enemy_here"
    assert moves["protect"]["disabled"] and moves["protect"]["disabled_reason"] == "no_ally"
    assert not moves["charge"]["disabled"] and not moves["escape"]["disabled"]


async def test_taps_flow_through_to_classification():
    """The tapped move + target recorded at submit time reach the AI provider
    (and thus the resolver) untouched."""
    from server.protocol import SubmitActionMsg

    rules = _rules()
    room = Room("TEST", rules)
    a_sock, b_sock = FakeSocket(), FakeSocket()
    a = room.add_player("A", "player", a_sock, None)
    b = room.add_player("B", "player", b_sock, None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.05))

    ca = Character(player_id=a.id, name="A", stats=Stats(power=2, speed=3, weird=4),
                   hp=24, max_hp=24, zone_id="glitter_back")
    cb = Character(player_id=b.id, name="B", stats=Stats(power=2, speed=1, weird=4),
                   hp=24, max_hp=24, zone_id="thunder_back")
    machine.state = GameState(room_id="TEST", characters={a.id: ca, b.id: cb},
                              teams=room.teams)

    async def drive():
        await asyncio.sleep(0)   # let _draw_stage arm the collector
        await machine.submit_action(a.id, SubmitActionMsg(
            round=2, png_base64="doodle", move_id="blast", target_id=b.id))
        await machine.submit_action(b.id, SubmitActionMsg(
            round=2, png_base64="doodle", move_id="charge", target_id=a.id))

    (pngs, taps), _ = await asyncio.gather(
        machine._draw_stage(2, [a.id, b.id]), drive())
    assert taps == {a.id: ("blast", b.id, 0, None), b.id: ("charge", a.id, 0, None)}

    from server.state_machine import _Drawn
    processed = await machine._process_round(
        _Drawn(2, pngs, [a.id, b.id], [], taps=taps))
    by_pid = {act.player_id: act for act in processed.actions}
    assert by_pid[a.id].move_id == "blast" and by_pid[a.id].target_id == b.id
    assert by_pid[b.id].move_id == "charge"


async def test_phase_change_carries_splash_and_deadline_excludes_it():
    """Every drawing phase opens with a splash on all clients (§13): the
    phase_change ships per-role splash text ({round} substituted, Gremlin
    variant for KO'd players) and the deadline includes splash + draw time —
    the timer effectively starts when the splash clears."""
    rules = _rules()
    rules.settings.ui.phase_splash_seconds = 2.0
    room = Room("TEST", rules)
    sock = FakeSocket()
    room.add_player("Alice", "player", sock, None)
    machine = GameStateMachine(room, rules, ai=MockAI(),
                               timers=Timers(30, 40, 0.01, montage=20))

    before = time.time()
    await machine._enter_phase("draw_action", round_num=3, timeout=40, splash=True)
    env = await sock.expect("phase_change")
    sp = env.payload["splash"]
    assert sp["seconds"] == 2.0
    assert sp["text"] == "Round 3 — Draw your Move!"
    assert sp["gremlin_text"] == "Draw a Hazard, Gremlin! 😈"
    # deadline = now + splash + draw timeout (timer excludes the splash).
    assert env.payload["deadline_ts"] >= before + 2.0 + 40 - 0.5

    await machine._enter_phase("montage", round_num=3, timeout=20, splash=True)
    env = await sock.expect("phase_change")
    assert env.payload["splash"]["text"] == "🎵 Upgrade your Character! 🎵"

    # Non-draw phases ship no splash.
    await machine._enter_phase("deliberate", round_num=3, timeout=1)
    env = await sock.expect("phase_change")
    assert "splash" not in env.payload


def test_transcript_persists_every_beat(tmp_path):
    """The full announcer transcript persists to transcript.jsonl (§13) —
    the on-screen log rolls off, the snapshot never does."""
    import json

    from server.ai.provider import Beat
    from server.snapshots import SnapshotWriter

    w = SnapshotWriter(tmp_path, "ROOM", enabled=True)
    w.append_transcript(1, "The Fish Learns to Surf", [
        Beat(event_id="e1", text="KABOOM!", speaker="pbp"),
        Beat(event_id="e2", text="It is not.", speaker="color"),
    ])
    w.append_transcript(2, "Round Two", [Beat(event_id="e3", text="zap")])
    rows = [json.loads(line) for line in
            (tmp_path / "room-ROOM" / "transcript.jsonl").read_text(
                encoding="utf-8").splitlines()]
    assert len(rows) == 3
    assert rows[0] == {"round": 1, "round_title": "The Fish Learns to Surf",
                       "event_id": "e1", "speaker": "pbp", "text": "KABOOM!"}
    assert rows[1]["speaker"] == "color"
    assert rows[2]["round"] == 2

    # Disabled writer stays silent.
    w2 = SnapshotWriter(tmp_path, "OFF", enabled=False)
    w2.append_transcript(1, "t", [Beat(event_id="e", text="x")])
    assert not (tmp_path / "room-OFF").exists()


async def test_team_names_revealed_as_final_intro_beat_then_used_everywhere():
    """Teams display as Team A/B until the intro reveal's final beat swaps in
    the AI names (GAME_DESIGN §2, Track A/B #7): the round-0 reveal carries a
    team_reveal beat + the named teams, and every later payload (lobby, arena
    zone labels, meters) uses the names."""
    rules = _rules()
    room = Room("TEST", rules)
    sock = FakeSocket()
    p = room.add_player("Alice", "player", sock, None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))

    assert [t.name for t in room.teams] == ["Team A", "Team B"]   # pre-reveal

    ch = Character(player_id=p.id, name="Alice", stats=Stats(power=2, speed=2, weird=4),
                   hp=24, max_hp=24, zone_id="glitter_back",
                   announcer_intro="ALICE!")
    machine.state = GameState(room_id="TEST", characters={p.id: ch}, teams=room.teams)
    machine._team_names = {"team_a": "The Sparkle Snacks",
                           "team_b": "Heavy Machinery & Friend"}

    await machine._reveal_intros([p.id])
    reveal = await sock.expect("reveal_step")
    beats = reveal.payload["beats"]
    final = beats[-1]
    assert final["type"] == "team_reveal"
    assert "TOGETHER" in final["text"]
    assert "THE SPARKLE SNACKS" in final["text"]
    by_id = {t["id"]: t["name"] for t in reveal.payload["teams"]}
    assert by_id == {"team_a": "The Sparkle Snacks",
                     "team_b": "Heavy Machinery & Friend"}

    # The names now stick everywhere: room state, lobby, and zone-band labels.
    assert room.teams[0].name == "The Sparkle Snacks"
    labels = {z["id"]: z["label"] for z in machine._arena_payload()["zones"]}
    assert labels["glitter_back"] == "🏠 The Sparkle Snacks"
    assert labels["thunder_back"] == "🏠 Heavy Machinery & Friend"
    assert labels["frontline"] == "⚔️ The Pit"


def test_mock_respects_taps_and_blank_canvas_scores_zero():
    """COMBAT V5 auto-submit semantics: the tapped move always resolves — a
    blank canvas just scores creativity 0; missing taps (headless mock games)
    get a deterministic any-zone attack on a living enemy."""
    from server.ai.provider import ActionSubmission
    from server.engine.models import Team

    a = Character(player_id="a", name="A", stats=Stats(power=2, speed=2, weird=4),
                  hp=20, max_hp=20, zone_id="glitter_back")
    b = Character(player_id="b", name="B", stats=Stats(power=2, speed=2, weird=4),
                  hp=20, max_hp=20, zone_id="thunder_back")
    state = GameState(room_id="T", characters={"a": a, "b": b}, teams=[
        Team(id="team_a", name="A", color="#f0f", player_ids=["a"]),
        Team(id="team_b", name="B", color="#0ff", player_ids=["b"]),
    ])
    subs = {"a": ActionSubmission("a", "doodle", move_id="charge", target_id="b"),
            "b": ActionSubmission("b", "")}   # blank canvas, no tap
    actions = {act.player_id: act for act in MockAI().classify_actions(state, subs, 1)}

    assert actions["a"].move_id == "charge" and actions["a"].target_id == "b"
    assert actions["b"].move_id in ("blast", "charge", "escape")   # headless fallback pick
    assert actions["b"].creativity_tier == 0            # blank canvas → tier 0


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

    def narrate_round(self, events, characters, gallery_names=None, zone_names=None):
        time.sleep(self._delay)
        return self._inner.narrate_round(events, characters, gallery_names, zone_names)


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
    host_seq: list[tuple[str, object]] = []   # ordered host-side milestones
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
            if env.type == "phase_change":
                host_seq.append(("phase", env.payload.get("phase")))
            elif env.type == "deliberation":
                interludes.append(env.payload)          # the interlude, not a spinner
            elif env.type == "reveal_step":
                host_seq.append(("reveal", env.payload["round"]))
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

    # v2.1: the whole INTROS sequence (phase + drumroll + giant-sprite reveal)
    # plays BEFORE Round 1's draw phase even opens — players meet the fighters,
    # then draw their opening moves with full knowledge.
    assert ("phase", "intros") in host_seq, f"no intros phase: {host_seq}"
    intro_reveal = host_seq.index(("reveal", 0))
    first_draw = host_seq.index(("phase", "draw_action"))
    assert intro_reveal < first_draw, (
        f"intros must finish before Round 1 drawing: {host_seq}")
    assert any(iv["kind"] == "intros" and iv["drawings"] for iv in interludes), (
        "no drumroll interstitial masked character generation")

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
# Arena Gremlin flow: KO'd players plant traps into the round (GAME_DESIGN §10)
# ---------------------------------------------------------------------------
async def test_gremlin_plants_a_trap_into_the_round():
    """A KO'd player is an Arena Gremlin: the pipeline keeps them in the draw
    roster, classifies their trap drawing (creativity; the zone is the tapped
    ground truth), and the resolver plants it — a TRAP_PLACED event lands in the
    processed round and the trap persists in game state."""
    from server.state_machine import _Drawn

    rules = _rules()
    room = Room("GREM", rules)
    a = room.add_player("A", "player", FakeSocket(), None)   # team_a
    b = room.add_player("B", "player", FakeSocket(), None)   # team_b
    c = room.add_player("C", "player", FakeSocket(), None)   # team_a (gremlin, teammate of A)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))

    fa = Character(player_id=a.id, name="A", stats=Stats(power=2, speed=2, weird=2),
                   hp=24, max_hp=24, zone_id="glitter_back")
    fb = Character(player_id=b.id, name="B", stats=Stats(power=2, speed=2, weird=2),
                   hp=24, max_hp=24, zone_id="thunder_back")
    grem = Character(player_id=c.id, name="Imp", stats=Stats(power=2, speed=2, weird=2),
                     hp=0, max_hp=24, zone_id="glitter_back",
                     is_ko=True, is_gremlin=True)
    state = GameState(room_id="GREM",
                      characters={a.id: fa, b.id: fb, c.id: grem}, teams=room.teams)
    machine.state = state

    # The gremlin is in the draw roster (as a gremlin), the fighters as fighters.
    fighters, gremlins = machine._draw_roster()
    assert set(fighters) == {a.id, b.id} and gremlins == [c.id]

    drawn = _Drawn(round_num=1,
                   action_pngs={a.id: "doodle", b.id: "doodle", c.id: "trap-doodle"},
                   fighters=fighters, gremlins=gremlins,
                   taps={c.id: ("", None, 0, "thunder_back")})
    processed = await machine._process_round(drawn)

    placed = [e for e in processed.events if e.type.value == "trap_placed"]
    assert placed, "the gremlin's drawing should plant a trap"
    assert placed[0].player_id == c.id and placed[0].data["zone"] == "thunder_back"
    assert any(t.zone_id == "thunder_back" for t in processed.post_state.traps)


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
                   hp=20, max_hp=20, zone_id="glitter_back")
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
                   hp=18, max_hp=22, zone_id="glitter_back", character_png_b64="OLD")
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


def test_apply_montage_speed_grants_no_hp():
    """v6: Speed feeds NO HP, so a Speed montage raises only the stat — max HP
    (and current HP) never move, however high Speed climbs."""
    from server.ai.provider import MontageResult

    rules = _rules()
    room = Room("MTG2", rules)
    p = room.add_player("A", "player", FakeSocket(), None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))

    ch = Character(player_id=p.id, name="A", stats=Stats(power=2, speed=2, weird=2),
                   hp=20, max_hp=22, zone_id="glitter_back", character_png_b64="OLD")
    state = GameState(room_id="MTG2", characters={p.id: ch}, teams=room.teams)
    machine._apply_montage(state, [MontageResult(p.id, "speed", "zoom")], {p.id: "NEW"})
    assert ch.stats.speed == 3
    assert (ch.hp, ch.max_hp) == (20, 22)         # Speed grants no HP
    assert ch.character_png_b64 == "NEW"

    # Climbing Speed again still moves no HP.
    machine._apply_montage(state, [MontageResult(p.id, "speed", "zoom")], {})
    assert ch.stats.speed == 4 and (ch.hp, ch.max_hp) == (20, 22)

    # a blank montage (no results) changes nothing
    before = (ch.stats.speed, ch.max_hp, ch.character_png_b64)
    machine._apply_montage(state, [], {})
    assert (ch.stats.speed, ch.max_hp, ch.character_png_b64) == before


def test_apply_montage_weird_and_power_raise_max_hp():
    """v6's HP formula is 27 + 2*POW + WRD (Speed grants no HP): a Weird montage
    moves max HP by hp_per_weird and a Power one by hp_per_power."""
    from server.ai.provider import MontageResult

    rules = _rules()
    room = Room("MTG3", rules)
    p = room.add_player("A", "player", FakeSocket(), None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))

    def fresh():
        ch = Character(player_id=p.id, name="A", stats=Stats(power=2, speed=2, weird=2),
                       hp=20, max_hp=34, zone_id="glitter_back")
        return ch, GameState(room_id="MTG3", characters={p.id: ch}, teams=room.teams)

    ch, state = fresh()
    machine._apply_montage(state, [MontageResult(p.id, "weird", "eyes")], {})
    assert ch.stats.weird == 3
    assert ch.max_hp == 34 + rules.balance.hp_per_weird
    assert ch.hp == 20 + rules.balance.hp_per_weird     # healed by the gain

    ch, state = fresh()
    machine._apply_montage(state, [MontageResult(p.id, "power", "spikes")], {})
    assert ch.max_hp == 34 + rules.balance.hp_per_power


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
                   hp=20, max_hp=20, zone_id="glitter_back")
    cb = Character(player_id=b.id, name="B", stats=Stats(power=2, speed=2, weird=2),
                   hp=0, max_hp=20, zone_id="thunder_back", is_ko=True)
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
    assert pay["winner_team_name"] == "Team A"   # display name, not the raw id
    # every player receives at least one award (the ceremony's hard rule)
    assert {aw["player_id"] for aw in pay["awards"]} == {a.id, b.id}
    # a match poster was composed to snapshots/<room>/poster.png…
    assert pay["poster_path"] and Path(pay["poster_path"]).exists()
    # …and surfaced as a browser-reachable URL (GET /poster/<room>, S3).
    assert pay["poster_url"] == "/poster/FIN"
    # the ceremony enlarges each winner's drawing → characters carry PNGs
    assert all("png" in c for c in pay["characters"])


async def test_victory_splash_precedes_awards_ceremony():
    """A real win shows the champions (winner-team sprites + final line) BEFORE
    the awards ceremony, masking the generate_awards call (GAME_DESIGN §10.2)."""
    rules = _rules()
    room = Room("VIC", rules)
    a = room.add_player("A", "player", FakeSocket(), None)
    b = room.add_player("B", "player", FakeSocket(), None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))
    machine._best_line = "And Blob hits the sand!"
    ca = Character(player_id=a.id, name="A", stats=Stats(power=2, speed=2, weird=2),
                   hp=20, max_hp=20, zone_id="glitter_back")
    cb = Character(player_id=b.id, name="B", stats=Stats(power=2, speed=2, weird=2),
                   hp=0, max_hp=20, zone_id="thunder_back", is_ko=True)
    machine.state = GameState(room_id="VIC", characters={a.id: ca, b.id: cb},
                              teams=room.teams, winner_team_id="team_a")

    await machine._game_over()

    sock = room.participants[a.id].socket
    types, splash, over = [], None, None
    for _ in range(10):
        e = await asyncio.wait_for(sock.client_recv(), 1.0)
        types.append(e.type)
        if e.type == "victory_splash":
            splash = e
        if e.type == "game_over":
            over = e
            break
    assert splash is not None and over is not None
    assert types.index("victory_splash") < types.index("game_over")   # champions first
    p = splash.payload
    assert p["winner_team_id"] == "team_a"
    assert p["winner_team_name"] == "Team A"           # display name, not raw id
    assert p["final_line"] == "And Blob hits the sand!"
    assert p["footer"] and p["min_seconds"] >= 0        # editable copy + client hold
    # only the winning team's fighters, each with a sprite for the splash
    assert {c["player_id"] for c in p["characters"]} == {a.id}
    assert all("png" in c for c in p["characters"])


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
    assert env.payload["poster_url"] is None


# ---------------------------------------------------------------------------
# The Doodle Crowd: gallery persistence + narrator cameos + host roster (S4)
# ---------------------------------------------------------------------------
class _CameoSpyAI(MockAI):
    """Records the gallery_names handed to narrate_round."""

    def __init__(self) -> None:
        self.seen_cameos = None

    def narrate_round(self, events, characters, gallery_names=None, zone_names=None):
        self.seen_cameos = gallery_names
        self.seen_zone_names = zone_names
        return super().narrate_round(events, characters, gallery_names, zone_names)


async def test_zone_display_names_use_team_names_for_backlines():
    """The narrator never sees zone ids: backlines are named after the team
    (the AI name once revealed, playtest fix — no more 'glitter backline')."""
    rules = _rules()
    room = Room("ZN", rules)
    room.add_player("A", "player", FakeSocket(), None)
    room.add_player("B", "player", FakeSocket(), None)
    machine = GameStateMachine(room, rules, ai=MockAI(), timers=Timers(1, 1, 0.01))

    names = machine._zone_display_names()
    assert names["glitter_back"] == "Team A's backline"   # pre-reveal
    assert names["frontline"] == "The Pit"

    # After the team-name reveal, the on-air backline names follow.
    machine._team_names = {"team_a": "The Doodle Dynamos",
                           "team_b": "The Scribble Squad"}
    machine._team_reveal_beat()
    names = machine._zone_display_names()
    assert names["glitter_back"] == "The Doodle Dynamos' backline"
    assert names["thunder_back"] == "The Scribble Squad's backline"


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
                   hp=24, max_hp=24, zone_id="glitter_back")
    cb = Character(player_id=b.id, name="Blob", stats=Stats(power=2, speed=2, weird=2),
                   hp=24, max_hp=24, zone_id="thunder_back")
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
                   hp=20, max_hp=20, zone_id="glitter_back")
    cb = Character(player_id=b.id, name="Loser", stats=Stats(power=2, speed=2, weird=2),
                   hp=0, max_hp=20, zone_id="thunder_back", is_ko=True)
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
