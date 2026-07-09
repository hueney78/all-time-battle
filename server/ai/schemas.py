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
    name: str = Field(description="a funny AI-invented name (grand for elaborate art, deadpan for plain)")
    stats: AIStats
    personality: str = Field(default="", description="one witty line")
    announcer_intro: str = Field(default="", description="a punchy wrestling-hype intro")
    flagged: bool = Field(default=False, description="true if the drawing/hint is inappropriate")


class GenerateCharactersResponse(BaseModel):
    characters: list[AICharacter]


# ---------------------------------------------------------------------------
# classify_actions
# ---------------------------------------------------------------------------
class AIComboSpec(BaseModel):
    partners: list[str] = Field(description="player_ids of the two coordinating teammates")
    leading_catalog_id: str = Field(description="the base catalog move the fused attack behaves like")
    concept: str = Field(default="", description="what the two drawings do together")
    combo_name: str = Field(default="", description="a hype fused-move name, e.g. GLITTERNADO SURF STRIKE")


class AIAction(BaseModel):
    player_id: str
    catalog_id: str = Field(description="exactly one id from the move catalog")
    action_cost: int = Field(default=2, description="1 jab, 2 solid, 3 haymaker")
    targets: list[str] = Field(default_factory=list, description="target player_ids (enemies for attacks, allies for support)")
    move_to: str | None = Field(default=None, description="zone id if the action includes movement, else null")
    creativity_tier: int = Field(default=0, description="0 plain … 3 table-losing-it — judge the IDEA")
    creativity_reason: str = Field(default="")
    similar_to_previous: bool = Field(default=False, description="true if repeating last round's concept")
    suggested_conditions: list[str] = Field(default_factory=list, description="only extra riders clearly drawn; from the allowed list")
    adaptation_note: str | None = Field(default=None, description="explain any stale-intent adaptation or wildcard read")
    flagged: bool = Field(default=False)


class ClassifyActionsResponse(BaseModel):
    round: int = 0
    combos: list[AIComboSpec] = Field(default_factory=list)
    actions: list[AIAction]


# ---------------------------------------------------------------------------
# narrate_round
# ---------------------------------------------------------------------------
class AIBeat(BaseModel):
    event_id: str = Field(description="the primary engine event id this beat narrates")
    text: str = Field(description="1–3 funny sentences with a comedic specific")
    mood: str = Field(default="comedy", description="comedy | epic | somber")


class NarrateResponse(BaseModel):
    beats: list[AIBeat]
    round_title: str = Field(default="", description="a short punchy title for the round")
