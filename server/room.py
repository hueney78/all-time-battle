"""Room lifecycle — player registry, reconnection, 4-letter codes.

A Room owns its participants (players + host screens), their live sockets, the
two teams (assigned at lobby time so collusion works from round 1), and — once
started — the running GameStateMachine. Reconnection is by persistent
`player_id`: a returning socket replaces the old one and gets resynced.
"""

from __future__ import annotations

import logging
import random
import string
from dataclasses import dataclass
from typing import Protocol

from server.config import GameRules
from server.engine.models import Team
from server.protocol import (
    C2S,
    S2C,
    JoinMsg,
    SubmitDrawingMsg,
    SubmitHintMsg,
    decode,
    encode,
    parse_payload,
)
from server.state_machine import GameStateMachine

log = logging.getLogger("doodle.room")

# Team A fights from the glitter backline, Team B from the thunder backline
# (zone ids from zones.yaml). Kept here because seating is a room concern.
_TEAM_ZONES = ["glitter_back", "thunder_back"]
_TEAM_COLORS = ["#ec4899", "#3b82f6"]
_TEAM_NAMES = ["Glitter Crew", "Thunder Squad"]
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # no I/O to avoid confusion


# ---------------------------------------------------------------------------
# Socket abstraction — real starlette WebSocket and the test FakeSocket both
# satisfy this. Disconnect surfaces as SocketDisconnect.
# ---------------------------------------------------------------------------
class SocketDisconnect(Exception):
    pass


class Socket(Protocol):
    async def send_text(self, data: str) -> None: ...
    async def receive_text(self) -> str: ...
    async def close(self, code: int = 1000) -> None: ...


@dataclass
class Player:
    id: str
    name: str
    role: str = "player"          # "player" | "host"
    team_id: str | None = None
    socket: Socket | None = None
    connected: bool = False
    hint: str = ""
    character_png: str = ""


class Room:
    def __init__(self, code: str, rules: GameRules, seed: int = 42):
        self.code = code
        self.rules = rules
        self.seed = seed
        self.participants: dict[str, Player] = {}
        self.teams: list[Team] = [
            Team(id="team_a", name=_TEAM_NAMES[0], color=_TEAM_COLORS[0], player_ids=[]),
            Team(id="team_b", name=_TEAM_NAMES[1], color=_TEAM_COLORS[1], player_ids=[]),
        ]
        self.machine: GameStateMachine | None = None

    # -- membership -------------------------------------------------------
    @property
    def players(self) -> list[Player]:
        return [p for p in self.participants.values() if p.role == "player"]

    @property
    def hosts(self) -> list[Player]:
        return [p for p in self.participants.values() if p.role == "host"]

    def team_of(self, player_id: str) -> str | None:
        for team in self.teams:
            if player_id in team.player_ids:
                return team.id
        return None

    def zone_for_team(self, team_id: str) -> str:
        idx = 0 if team_id == "team_a" else 1
        return _TEAM_ZONES[idx]

    def _assign_team(self, player_id: str) -> str:
        # Put the new player on the smaller team (ties → team_a).
        smaller = min(self.teams, key=lambda t: len(t.player_ids))
        smaller.player_ids.append(player_id)
        return smaller.id

    def add_player(self, name: str, role: str, socket: Socket,
                   player_id: str | None) -> Player:
        # Reconnect path: known id keeps its team + character.
        if player_id and player_id in self.participants:
            p = self.participants[player_id]
            p.socket = socket
            p.connected = True
            if name:
                p.name = name
            return p

        pid = player_id or _new_id()
        team_id = self._assign_team(pid) if role == "player" else None
        p = Player(id=pid, name=name or pid, role=role, team_id=team_id,
                   socket=socket, connected=True)
        self.participants[pid] = p
        return p

    @property
    def can_start(self) -> bool:
        n = len(self.players)
        g = self.rules.settings.game
        return g.min_players <= n <= g.max_players and self.machine is None

    # -- messaging --------------------------------------------------------
    async def send(self, player_id: str, msg_type: str, payload=None) -> None:
        p = self.participants.get(player_id)
        if not p or not p.socket or not p.connected:
            return
        try:
            await p.socket.send_text(encode(msg_type, payload))
        except Exception:
            p.connected = False

    async def broadcast(self, msg_type: str, payload=None) -> None:
        for p in list(self.participants.values()):
            await self.send(p.id, msg_type, payload)

    def lobby_state(self) -> dict:
        return {
            "room": self.code,
            "can_start": self.can_start,
            "players": [
                {"player_id": p.id, "name": p.name, "team_id": p.team_id,
                 "connected": p.connected}
                for p in self.players
            ],
            "teams": [{"id": t.id, "name": t.name, "color": t.color} for t in self.teams],
        }


# ---------------------------------------------------------------------------
# Room manager — owns all rooms and the per-connection handler
# ---------------------------------------------------------------------------
class RoomManager:
    def __init__(self, rules: GameRules):
        self.rules = rules
        self.rooms: dict[str, Room] = {}

    def create_room(self, seed: int = 42) -> Room:
        code = _new_code(self.rooms)
        room = Room(code, self.rules, seed=seed)
        self.rooms[code] = room
        return room

    def get(self, code: str) -> Room | None:
        return self.rooms.get(code.upper()) if code else None

    async def handle_socket(self, socket: Socket) -> None:
        """Full lifecycle for one connection: join handshake, then message loop."""
        player: Player | None = None
        room: Room | None = None
        try:
            join = await self._await_join(socket)
            if join is None:
                await socket.send_text(encode(S2C.ERROR, {"message": "expected join"}))
                return
            room, player = await self._do_join(socket, join)
            if room is None or player is None:
                return
            await self._message_loop(room, player)
        except SocketDisconnect:
            pass
        finally:
            if player is not None:
                player.connected = False
                player.socket = None
                if room is not None and player.role == "player" and room.machine is None:
                    await room.broadcast(S2C.LOBBY_STATE, room.lobby_state())

    async def _await_join(self, socket: Socket) -> JoinMsg | None:
        raw = await socket.receive_text()
        env = decode(raw)
        if env is None or env.type != C2S.JOIN:
            return None
        parsed = parse_payload(C2S.JOIN, env.payload)
        return parsed if isinstance(parsed, JoinMsg) else None

    async def _do_join(self, socket: Socket, join: JoinMsg) -> tuple[Room | None, Player | None]:
        if join.role == "host":
            room = self.get(join.room) if join.room else None
            if room is None:
                room = self.create_room()
        else:
            room = self.get(join.room)
            if room is None:
                await socket.send_text(encode(S2C.ERROR, {"message": "no such room"}))
                return None, None
            # Reject a brand-new joiner only when the lobby is already full;
            # reconnecting players (known id) are always let back in.
            is_new = join.player_id not in room.participants
            if is_new and len(room.players) >= room.rules.settings.game.max_players:
                await socket.send_text(encode(S2C.ERROR, {"message": "room full"}))
                return None, None

        player = room.add_player(join.name, join.role, socket, join.player_id)
        await room.send(player.id, S2C.JOINED, {
            "player_id": player.id, "room": room.code,
            "role": player.role, "team_id": player.team_id,
        })
        await room.broadcast(S2C.LOBBY_STATE, room.lobby_state())
        # Reconnect mid-game: resync this participant to the current phase.
        if room.machine is not None:
            await room.machine.resync(player.id)
        return room, player

    async def _message_loop(self, room: Room, player: Player) -> None:
        while True:
            raw = await player.socket.receive_text()
            env = decode(raw)
            if env is None:
                continue
            await self._dispatch(room, player, env.type, env.payload)

    async def _dispatch(self, room: Room, player: Player, msg_type: str, payload: dict) -> None:
        if msg_type == C2S.START_GAME:
            if player.role == "host" and room.can_start:
                from server.ai.provider import make_ai

                room.machine = GameStateMachine(room, self.rules, ai=make_ai(self.rules))
                room.machine.start()
        elif msg_type == C2S.SUBMIT_HINT:
            parsed = parse_payload(C2S.SUBMIT_HINT, payload)
            if isinstance(parsed, SubmitHintMsg):
                player.hint = parsed.hint
        elif msg_type == C2S.SUBMIT_DRAWING:
            parsed = parse_payload(C2S.SUBMIT_DRAWING, payload)
            if isinstance(parsed, SubmitDrawingMsg) and room.machine is not None:
                room.machine.submit_drawing(player.id, parsed)
        elif msg_type == C2S.NEXT_BEAT:
            if room.machine is not None:
                room.machine.advance_beat()
        else:
            log.info("ignoring unknown message type %r", msg_type)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _new_id() -> str:
    return "p_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))


def _new_code(existing: dict[str, Room]) -> str:
    for _ in range(1000):
        code = "".join(random.choices(_CODE_ALPHABET, k=4))
        if code not in existing:
            return code
    raise RuntimeError("could not allocate a unique room code")
