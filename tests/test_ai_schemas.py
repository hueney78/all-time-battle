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
    schema = ClassifyActionsResponse.model_json_schema()
    # actions[] is required and its items carry the fields the referee must fill.
    assert "actions" in schema["properties"]
    action = schema["$defs"]["AIAction"]["properties"]
    for field in ("catalog_id", "action_cost", "targets", "creativity_tier", "flagged"):
        assert field in action


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


def test_prompts_render_with_live_config():
    """Templates render with real config injected (catalog/zones/conditions),
    so YAML edits reach the AI automatically and no template is broken."""
    rules = load_game_rules()
    env = Environment(loader=FileSystemLoader(str(PROMPTS)), autoescape=False)

    chargen = env.get_template("character_gen.md.j2").render(balance=rules.balance)
    assert str(rules.balance.stat_budget) in chargen

    classify = env.get_template("action_classify.md.j2").render(
        moves=rules.moves.moves,
        conditions=sorted(rules.conditions.conditions),
        zones=rules.zones.zones,
    )
    assert "strike:" in classify and "wildcard:" in classify   # catalog injected
    assert "frontline" in classify                              # zones injected

    narrate = env.get_template("narrate.md.j2").render()
    assert "COMEDY MANDATE" in narrate
    assert "pbp" in narrate and "color" in narrate    # the announcer duo + speaker field

    gremlin = env.get_template("gremlin_classify.md.j2").render(
        hazards=rules.hazards.hazards, zones=rules.zones.zones,
    )
    assert "banana_peel" in gremlin and "Gremlin" in gremlin   # hazard palette injected

    montage = env.get_template("montage_classify.md.j2").render()
    assert "power" in montage and "Montage" in montage         # stat-choice rules present
