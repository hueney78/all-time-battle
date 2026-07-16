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
    power: int  # 0–6 (budget 9, clamped by config)
    speed: int  # 0–6
    weird: int  # 0–6


class Character(BaseModel):
    player_id: str
    name: str
    stats: Stats
    personality: str = ""
    announcer_intro: str = ""
    hp: int
    max_hp: int
    zone_id: str
    # Last tapped combat move — the no-repeat rule greys it out next round
    # (movement is exempt). Server-owned, validated at submit time.
    last_move_id: str | None = None
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


class WildInterpretation(BaseModel):
    """WILD CARD: the AI's free read of the drawing, bounded by schema — big
    flat damage by default, or a reposition/absurdity; no status effects."""

    description: str = ""          # what the AI saw — feeds the narrator


class ClassifiedAction(BaseModel):
    """One player's action for a round: the TAPPED move + target (ground truth
    from the phone, server-validated) plus the AI's judgment of the drawing
    (creativity, flavor, WILD read, combos). Arena Gremlins reuse this shape
    with move_id carrying a hazard id."""

    player_id: str
    move_id: str                  # a key in moves.yaml (or hazards.yaml for gremlins)
    target_id: str | None = None  # tapped target (enemy or ally, move-dependent)
    creativity_tier: int = 0      # 0–3, from the drawing
    creativity_reason: str = ""
    similar_to_previous: bool = False   # stale drawing → scores creativity 0
    flavor_summary: str = ""            # feeds the narrator
    wild_interpretation: WildInterpretation | None = None  # WILD CARD only
    adaptation_note: str | None = None
    flagged: bool = False
    combo_partners: list[str] = []      # both partners gain +combo_tier_bonus creativity tiers
    combo_name: str = ""
    action_png_b64: str = ""            # the player's drawing this round


class EventType(str, Enum):
    # data["result"] is one of (COMBAT V4 — there is no "miss": every move lands):
    #   hit          the move landed
    #   devastating  it landed at creativity tier 3 — v4's spike moment (replay,
    #                stinger, gold log line). Replaces v2's crit.
    #   dodge        the target's passive Speed dodge negated it outright
    #   backfire     WILD CARD turned on its caster (the only self-damage)
    #   reflect      a SHIELD bounced mitigated damage back at the attacker
    #   hazard       an Arena Gremlin's zone hazard
    #   no_target / out_of_reach
    ATTACK_RESOLVED = "attack_resolved"
    # SHIELD resolved: data carries the protected player ids + the mitigation
    # amount, so the narrator and the host's "helped" pop know who got covered.
    SHIELDED = "shielded"
    MOVED = "moved"
    HEALED = "healed"
    KO = "ko"
    GREMLIN_HAZARD = "gremlin_hazard"
    COMBO = "combo"
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
