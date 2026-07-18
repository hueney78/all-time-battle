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
    # COMBAT V2: an action round submits the tapped move + target + drawing in
    # one message. The server validates the tap (no-repeat, edge legality,
    # living target) and answers with an `action_rejected` toast on failure.
    SUBMIT_ACTION = "submit_action"
    # Character/montage/gremlin drawings (no move tap).
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
    # The moment all drawings are in: the TV shows every submitted drawing side by
    # side ("the judges deliberate…") while the round is classified/resolved/
    # narrated — the latency mask, never a spinner. Also fronts the montage's
    # "training montage" interstitial. Payload: {round, kind, drawings}.
    DELIBERATION = "deliberation"
    REVEAL_STEP = "reveal_step"
    MONTAGE = "montage_reveal"    # Power-Up Montage stat pulses (S2)
    GALLERY = "gallery_roster"    # past characters for the host stands (S4)
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


class SubmitActionMsg(BaseModel):
    """COMBAT V5 action submission: the tapped move + target are ground truth
    from the phone; the drawing supplies creativity/flavor only. ESCAPE also
    carries a ◀/▶ direction; an Arena Gremlin sends a trap_zone (+ drawing)
    instead of a move."""

    round: int = 0
    png_base64: str = ""
    move_id: str = ""             # a moves.yaml key; "" = no tap → stumble
    target_id: str | None = None  # enemy or ally portrait tapped, move-dependent
    escape_direction: int = 0     # ESCAPE only: -1 = ◀ / +1 = ▶
    trap_zone: str | None = None  # Arena Gremlin only: the zone to plant a trap in


class SubmitHintMsg(BaseModel):
    hint: str = ""


# Parsers keyed by incoming type. Returns a validated model or None.
_C2S_MODELS: dict[str, type[BaseModel]] = {
    C2S.JOIN: JoinMsg,
    C2S.SUBMIT_ACTION: SubmitActionMsg,
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
