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


class AITeamNames(BaseModel):
    """One AI-invented name per team, comically linking its roster (§3).
    Short (fits meters and zone labels), family-friendly, equally funny."""

    team_a: str = Field(description="a short funny name linking team A's fighters")
    team_b: str = Field(description="a short funny name linking team B's fighters")


class GenerateCharactersResponse(BaseModel):
    characters: list[AICharacter]
    teams: AITeamNames | None = None


# ---------------------------------------------------------------------------
# classify_actions
# ---------------------------------------------------------------------------
class AIComboSpec(BaseModel):
    partners: list[str] = Field(description="player_ids of the two coordinating teammates")
    concept: str = Field(default="", description="what the two drawings do together")
    combo_name: str = Field(default="", description="a hype combined-move name, e.g. GLITTERNADO")


class AIWildInterpretation(BaseModel):
    """WILD CARD only: the AI's free read of the drawing — big flat damage by
    default, or a reposition/absurdity; no status effects."""

    description: str = Field(default="", description="what the drawing does, for the narrator")


class AIAction(BaseModel):
    """COMBAT V2: the move and target are TAPPED on the phone (ground truth,
    echoed in the request) — judge only the DRAWING: creativity, staleness,
    flavor, WILD CARD's interpretation."""

    player_id: str
    creativity_tier: int = Field(default=0, description="0 plain..3 wild; judge the IDEA not art")
    creativity_reason: str = Field(default="")
    similar_to_previous: bool = Field(
        default=False, description="true if repeating last round's drawing concept"
    )
    flavor_summary: str = Field(
        default="", description="a short vivid read of the drawing, feeds the narrator"
    )
    wild_interpretation: AIWildInterpretation | None = Field(
        default=None, description="WILD CARD only: your free read of the drawing"
    )
    adaptation_note: str | None = Field(
        default=None, description="explain any adaptation (e.g. blank canvas, odd drawing)"
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
# generate_awards — the victory ceremony (GAME_DESIGN §10.2)
# ---------------------------------------------------------------------------
class AIAward(BaseModel):
    title: str = Field(description="a short, affectionate superlative (never mocking)")
    player_id: str = Field(description="the winner's id — EVERY player must get at least one")
    blurb: str = Field(default="", description="one witty, warm sentence")


class GenerateAwardsResponse(BaseModel):
    awards: list[AIAward]


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
