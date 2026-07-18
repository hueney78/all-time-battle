"""Phase 5 — AI response schemas and prompt templates.

The schema models double as forced tool-use input schemas; the templates are
the cacheable system prompts. These tests keep both valid without an API key.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from server.ai.schemas import (
    ClassifyActionsResponse,
    GenerateCharactersResponse,
    NarrateResponse,
)
from server.config import load_game_rules

PROMPTS = Path(__file__).parent.parent / "config" / "prompts"


def test_response_models_emit_object_json_schemas():
    for model in (GenerateCharactersResponse, ClassifyActionsResponse, NarrateResponse):
        schema = model.model_json_schema()
        assert schema["type"] == "object"
        assert "properties" in schema


def test_classify_schema_exposes_action_fields():
    """COMBAT V5: the judge fills drawing-judgment fields only — the move and
    target are tapped on the phone and never appear in the response schema."""
    schema = ClassifyActionsResponse.model_json_schema()
    assert "actions" in schema["properties"]
    action = schema["$defs"]["AIAction"]["properties"]
    for field in ("creativity_tier", "similar_to_previous", "flavor_summary", "flagged"):
        assert field in action
    # v5: no conditions, no WILD CARD interpretation, no move/target choice.
    for gone in ("catalog_id", "action_cost", "targets", "move_to", "trick_condition",
                 "wild_interpretation", "move_id", "target_id"):
        assert gone not in action
    assert "AIWildInterpretation" not in schema.get("$defs", {})


def test_narrate_schema_exposes_speaker():
    """Each beat carries a speaker so the announcer duo is structured, not just
    prose (sync point S1)."""
    schema = NarrateResponse.model_json_schema()
    beat = schema["$defs"]["AIBeat"]["properties"]
    assert "speaker" in beat
    assert beat["speaker"]["default"] == "pbp"


def test_montage_schema_exposes_stat():
    """The montage response grants one stat per fighter (sync point S2)."""
    from server.ai.schemas import ClassifyMontageResponse
    schema = ClassifyMontageResponse.model_json_schema()
    assert schema["type"] == "object"
    montage = schema["$defs"]["AIMontage"]["properties"]
    assert "stat" in montage and "player_id" in montage


def test_awards_schema_exposes_title_and_player():
    """The awards response is a list of {title, player_id, blurb} (sync point S3)."""
    from server.ai.schemas import GenerateAwardsResponse
    schema = GenerateAwardsResponse.model_json_schema()
    assert schema["type"] == "object"
    award = schema["$defs"]["AIAward"]["properties"]
    for field in ("title", "player_id", "blurb"):
        assert field in award


def test_prompts_render_with_live_config():
    """Templates render with real config injected (catalog/zones/hazards),
    so YAML edits reach the AI automatically and no template is broken."""
    rules = load_game_rules()
    env = Environment(loader=FileSystemLoader(str(PROMPTS)), autoescape=False)

    chargen = env.get_template("character_gen.md.j2").render(balance=rules.balance)
    assert str(rules.balance.stat_budget) in chargen

    classify = env.get_template("action_classify.md.j2").render(
        moves=rules.moves.moves,
        zones=rules.zones.zones,
    )
    assert "SMASH" in classify and "CHARGE" in classify      # catalog injected
    assert "PROTECT" in classify and "ESCAPE" in classify    # v5 five-move catalog
    assert "WILD CARD" not in classify                       # v5: WILD is gone
    assert "frontline" in classify                            # zones injected
    assert "TAPS" in classify and "never choose" in classify  # v5: taps are ground truth
    assert "condition" not in classify.lower()                # no status palette
    assert "NEVER infer" in classify                          # no movement guessing

    narrate = env.get_template("narrate.md.j2").render()
    assert "COMEDY MANDATE" in narrate
    assert "pbp" in narrate and "color" in narrate    # the announcer duo + speaker field

    gremlin = env.get_template("gremlin_classify.md.j2").render(
        zones=rules.zones.zones,
    )
    assert "trap" in gremlin.lower() and "Gremlin" in gremlin   # v5: traps, not hazards

    montage = env.get_template("montage_classify.md.j2").render()
    assert "power" in montage and "Montage" in montage         # stat-choice rules present

    awards = env.get_template("awards.md.j2").render()
    assert "at least one" in awards and "AWARDS CEREMONY" in awards   # every-player rule present
