"""Pydantic schemas for AI JSON responses (forced tool-use output).

Each response model doubles as the tool `input_schema` sent to Claude
(`model_json_schema()`), so the model is *forced* to return exactly this shape.
Field descriptions are part of the schema — they steer the model — so keep them
tight and behavioral. Values are re-validated/remapped in `validators.py`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# generate_characters
# ---------------------------------------------------------------------------
class AIStats(BaseModel):
    power: int = Field(description="💪 melee/size/spikes")
    speed: int = Field(description="⚡ legs/wheels/wings")
    weird: int = Field(description="🌀 extra eyes/auras/magic/glitter")


class AICharacter(BaseModel):
    player_id: str = Field(description="the id from the labeled drawing")
    name: str = Field(description="a funny AI name (grand for elaborate art, deadpan for plain)")
    stats: AIStats
    personality: str = Field(default="", description="one witty line")
    announcer_intro: str = Field(default="", description="a punchy wrestling-hype intro")
    flagged: bool = Field(default=False, description="true if drawing/hint is inappropriate")


class GenerateCharactersResponse(BaseModel):
    characters: list[AICharacter]


# ---------------------------------------------------------------------------
# classify_actions
# ---------------------------------------------------------------------------
class AIComboSpec(BaseModel):
    partners: list[str] = Field(description="player_ids of the two coordinating teammates")
    leading_catalog_id: str = Field(description="the base catalog move the fusion behaves like")
    concept: str = Field(default="", description="what the two drawings do together")
    combo_name: str = Field(default="", description="a hype fused-move name, e.g. GLITTERNADO")


class AIAction(BaseModel):
    player_id: str
    catalog_id: str = Field(description="exactly one id from the move catalog")
    action_cost: int = Field(default=2, description="1 jab, 2 solid, 3 haymaker")
    targets: list[str] = Field(
        default_factory=list, description="target player_ids (enemies to attack, allies to support)"
    )
    move_to: str | None = Field(default=None, description="zone id if the action moves, else null")
    creativity_tier: int = Field(default=0, description="0 plain..3 wild; judge the IDEA not art")
    creativity_reason: str = Field(default="")
    similar_to_previous: bool = Field(default=False, description="true if repeating last concept")
    suggested_conditions: list[str] = Field(
        default_factory=list, description="only extra riders clearly drawn; from the allowed list"
    )
    adaptation_note: str | None = Field(
        default=None, description="explain any stale-intent adaptation or wildcard read"
    )
    flagged: bool = Field(default=False)


class ClassifyActionsResponse(BaseModel):
    round: int = 0
    combos: list[AIComboSpec] = Field(default_factory=list)
    actions: list[AIAction]


# ---------------------------------------------------------------------------
# classify_gremlin — a KO'd player draws one hazard per round (GAME_DESIGN §10)
# ---------------------------------------------------------------------------
class AIGremlinHazard(BaseModel):
    player_id: str = Field(description="the gremlin's id from the labeled drawing")
    hazard_id: str = Field(description="exactly one id from the hazard palette")
    adaptation_note: str | None = Field(
        default=None, description="a funny read of what the gremlin scribbled"
    )
    flagged: bool = Field(default=False, description="true if the drawing is inappropriate")


class ClassifyGremlinsResponse(BaseModel):
    round: int = 0
    hazards: list[AIGremlinHazard]


# ---------------------------------------------------------------------------
# classify_montage — a survivor adds to their character for +1 stat (§10.1)
# ---------------------------------------------------------------------------
class AIMontage(BaseModel):
    player_id: str = Field(description="the id from the labeled before/after pair")
    stat: str = Field(description='which stat the addition boosts: "power", "speed", or "weird"')
    flavor: str = Field(default="", description="a short punchy line about the upgrade")


class ClassifyMontageResponse(BaseModel):
    montages: list[AIMontage]


# ---------------------------------------------------------------------------
# narrate_round
# ---------------------------------------------------------------------------
class AIBeat(BaseModel):
    event_id: str = Field(description="the primary engine event id this beat narrates")
    text: str = Field(description="1–3 funny sentences with a comedic specific")
    mood: str = Field(default="comedy", description="comedy | epic | somber")
    speaker: str = Field(
        default="pbp",
        description='who says this beat: "pbp" (hyper play-by-play announcer) or '
                    '"color" (deadpan color commentator)',
    )


class NarrateResponse(BaseModel):
    beats: list[AIBeat]
    round_title: str = Field(default="", description="a short punchy title for the round")
