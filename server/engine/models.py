"""Core game-state data models.

All models are pure pydantic — no I/O, no AI, no side effects.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class Phase(str, Enum):
    LOBBY = "lobby"
    DRAW_CHARACTERS = "draw_characters"
    ROUND_LOOP = "round_loop"
    GAME_OVER = "game_over"


class Stats(BaseModel):
    power: int  # 1–4
    speed: int  # 1–4
    weird: int  # 1–4


class Character(BaseModel):
    player_id: str
    name: str
    stats: Stats
    personality: str = ""
    announcer_intro: str = ""
    hp: int
    max_hp: int
    ac: int
    zone_id: str
    # condition_id → rounds remaining
    conditions: dict[str, int] = {}
    banked_actions: int = 0
    # Original stats saved while `transformed` is active; restored on expiry.
    pre_transform_stats: Stats | None = None
    is_ko: bool = False
    is_gremlin: bool = False
    flagged: bool = False
    # Base64-encoded PNG of the original character drawing
    character_png_b64: str = ""


class Team(BaseModel):
    id: str
    name: str
    color: str  # CSS color or palette key
    player_ids: list[str]


class ClassifiedAction(BaseModel):
    player_id: str
    catalog_id: str          # must be a key in moves.yaml
    action_cost: int         # 1–3 (clamped to the move's min_cost)
    targets: list[str] = []
    move_to: str | None = None
    creativity_tier: int = 0  # 0–3
    creativity_reason: str = ""
    similar_to_previous: bool = False
    suggested_conditions: list[str] = []
    adaptation_note: str | None = None
    flagged: bool = False
    combo_partners: list[str] = []
    combo_name: str = ""
    leading_catalog_id: str = ""  # for combos — the "base" move
    action_png_b64: str = ""      # the player's drawing this round


class EventType(str, Enum):
    ATTACK_RESOLVED = "attack_resolved"
    CONDITION_APPLIED = "condition_applied"
    CONDITION_EXPIRED = "condition_expired"
    CONDITION_TICKED = "condition_ticked"
    MOVED = "moved"
    HEALED = "healed"
    KO = "ko"
    GREMLIN_HAZARD = "gremlin_hazard"
    COMBO = "combo"
    BANKED = "banked"
    VICTORY = "victory"
    SUDDEN_DEATH = "sudden_death"
    STUMBLE = "stumble"


class Event(BaseModel):
    id: str                       # stable ID used by narrator for beat alignment
    type: EventType
    round: int
    player_id: str | None = None
    target_id: str | None = None
    data: dict[str, Any] = {}


class RoundResult(BaseModel):
    round: int
    events: list[Event]
    new_state: GameState
    # Player ids in this round's acting order (speed desc, KO'd/gremlins dropped).
    # Surfaced so the host's Initiative Order rail can render + reorder portraits.
    initiative_order: list[str] = []


class GameState(BaseModel):
    room_id: str
    phase: Phase = Phase.LOBBY
    round: int = 0
    characters: dict[str, Character] = {}  # player_id → Character
    teams: list[Team] = []
    winner_team_id: str | None = None
    sudden_death: bool = False
    rng_seed: int = 42


# Rebuild after GameState is defined so RoundResult.new_state resolves
RoundResult.model_rebuild()
