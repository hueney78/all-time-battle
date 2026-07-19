"""Core game-state data models.

All models are pure pydantic — no I/O, no AI, no side effects.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class Phase(StrEnum):
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


class ClassifiedAction(BaseModel):
    """One player's action for a round: the TAPPED move + target (ground truth
    from the phone, server-validated) plus the AI's judgment of the drawing
    (creativity, flavor, combos). Arena Gremlins reuse this shape with move_id
    empty and trap_zone set (they plant a trap in a chosen zone, §10)."""

    player_id: str
    move_id: str                  # a key in moves.yaml ("" for a gremlin's trap)
    target_id: str | None = None  # tapped target (enemy or ally, move-dependent)
    escape_direction: int = 0     # ESCAPE only: -1 = ◀ / +1 = ▶ (from the phone)
    trap_zone: str | None = None  # Arena Gremlin only: the zone the trap is planted in
    creativity_tier: int = 0      # 0–3, from the drawing
    creativity_reason: str = ""
    similar_to_previous: bool = False   # stale drawing → scores creativity 0
    flavor_summary: str = ""            # feeds the narrator
    adaptation_note: str | None = None
    flagged: bool = False
    combo_partners: list[str] = []      # both partners gain +combo_tier_bonus creativity tiers
    combo_name: str = ""
    action_png_b64: str = ""            # the player's drawing this round


class EventType(StrEnum):
    # data["result"] is one of (COMBAT V5 — there is no "miss": every move lands):
    #   hit          the move landed
    #   devastating  it landed at creativity tier 3 — the spike moment (replay,
    #                stinger, gold log line)
    #   reflect      a PROTECT shield bounced absorbed damage back at the attacker
    #   trap         an Arena Gremlin's trap sprang on an enemy in its zone
    #   no_target
    ATTACK_RESOLVED = "attack_resolved"
    # PROTECT resolved (heal + reflecting shield) as ONE event: data carries the
    # healed/shielded ally, the heal `amount`, and the reflect percentage — so the
    # narrator calls it in a single beat and the host plays the heal float, the
    # "helped" pop, and the round-long glow off that one event.
    PROTECTED = "protected"
    MOVED = "moved"
    # Legacy heal-only event; PROTECT now folds its heal into PROTECTED. Kept for a
    # future heal-only move — the host still renders it if one ever emits it.
    HEALED = "healed"
    KO = "ko"
    # A gremlin plants a trap in a zone; it persists until an enemy triggers it.
    TRAP_PLACED = "trap_placed"
    TRAP_TRIGGERED = "trap_triggered"
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


class Trap(BaseModel):
    """An Arena Gremlin's planted trap (GAME_DESIGN §10). It sits in a zone as a
    drawn icon and persists ACROSS rounds until an enemy of the owner's team is
    in that zone at end of round — then it fires trap_damage + creativity at one
    random enemy there and is consumed."""

    trap_id: str
    zone_id: str
    owner_id: str                 # the gremlin who planted it
    owner_team_id: str | None = None   # enemies of this team trigger the trap
    creativity: int = 0
    png_b64: str = ""             # the trap drawing → the host's zone icon


class RoundResult(BaseModel):
    round: int
    events: list[Event]
    new_state: GameState
    # Player ids in this round's acting order (PROTECT first, then speed desc;
    # KO'd/gremlins dropped). Surfaced so the host's Initiative Order rail can
    # render + reorder portraits.
    initiative_order: list[str] = []


class GameState(BaseModel):
    room_id: str
    phase: Phase = Phase.LOBBY
    round: int = 0
    characters: dict[str, Character] = {}  # player_id → Character
    teams: list[Team] = []
    # Arena Gremlin traps planted on the battlefield, persisting until triggered.
    traps: list[Trap] = []
    winner_team_id: str | None = None
    sudden_death: bool = False
    rng_seed: int = 42


# Rebuild after GameState is defined so RoundResult.new_state resolves
RoundResult.model_rebuild()
