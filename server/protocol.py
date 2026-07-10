"""WebSocket message types (pydantic models).

Every message is a versioned envelope serialized as JSON:

    {"v": 1, "type": "...", "payload": {...}}

Unknown message types are logged and ignored (forward compatibility). Client→
server payloads are validated into the models below; server→client payloads are
built with `encode()` and sent as text frames.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

PROTOCOL_VERSION = 1


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------
class Envelope(BaseModel):
    v: int = PROTOCOL_VERSION
    type: str
    payload: dict[str, Any] = {}


def encode(msg_type: str, payload: BaseModel | dict[str, Any] | None = None) -> str:
    """Serialize a server→client message to a JSON text frame."""
    if payload is None:
        data: dict[str, Any] = {}
    elif isinstance(payload, BaseModel):
        data = payload.model_dump()
    else:
        data = dict(payload)
    return Envelope(type=msg_type, payload=data).model_dump_json()


def decode(raw: str) -> Envelope | None:
    """Parse an incoming text frame into an Envelope, or None if malformed."""
    try:
        return Envelope.model_validate_json(raw)
    except ValidationError:
        return None


# ---------------------------------------------------------------------------
# Message type constants
# ---------------------------------------------------------------------------
class C2S:
    JOIN = "join"
    SUBMIT_DRAWING = "submit_drawing"
    SUBMIT_HINT = "submit_hint"
    START_GAME = "start_game"
    NEXT_BEAT = "next_beat"


class S2C:
    JOINED = "joined"
    LOBBY_STATE = "lobby_state"
    CANVAS_INIT = "canvas_init"
    ARENA_STATE = "arena_state"
    PHASE_CHANGE = "phase_change"
    REVEAL_STEP = "reveal_step"
    MONTAGE = "montage_reveal"    # Power-Up Montage stat pulses (S2)
    PLAYER_STATE = "player_state"
    GAME_OVER = "game_over"
    ERROR = "error"
    TOAST = "toast"


# ---------------------------------------------------------------------------
# Client→server payloads
# ---------------------------------------------------------------------------
class JoinMsg(BaseModel):
    role: str = "player"          # "player" | "host"
    name: str = ""
    player_id: str | None = None  # present on reconnect
    room: str | None = None       # room code (players join an existing room)


class SubmitDrawingMsg(BaseModel):
    phase: str = ""
    round: int = 0
    png_base64: str = ""


class SubmitHintMsg(BaseModel):
    hint: str = ""


# Parsers keyed by incoming type. Returns a validated model or None.
_C2S_MODELS: dict[str, type[BaseModel]] = {
    C2S.JOIN: JoinMsg,
    C2S.SUBMIT_DRAWING: SubmitDrawingMsg,
    C2S.SUBMIT_HINT: SubmitHintMsg,
}


def parse_payload(msg_type: str, payload: dict[str, Any]) -> BaseModel | None:
    """Validate a client payload for a known type; None for typeless messages
    (start_game, next_beat) or on validation failure."""
    model = _C2S_MODELS.get(msg_type)
    if model is None:
        return None
    try:
        return model.model_validate(payload)
    except ValidationError:
        return None
