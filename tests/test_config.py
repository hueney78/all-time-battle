"""Phase 1 acceptance tests: config loading.

Covers: all five YAML files load without error; key values match the spec;
High Ground zone block added to zones.yaml loads and exposes its modifiers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import server.config as cfg_mod
from server.config import (
    load_balance,
    load_conditions,
    load_game_rules,
    load_moves,
    load_settings,
    load_zones,
)

# ---------------------------------------------------------------------------
# settings.yaml
# ---------------------------------------------------------------------------


def test_settings_loads():
    s = load_settings()
    assert s.server.port == 8000
    assert s.server.host == "0.0.0.0"


def test_settings_ai_models():
    s = load_settings()
    assert s.ai.classify_model == "claude-haiku-4-5"
    assert s.ai.narrate_model == "claude-sonnet-4-6"
    assert s.ai.timeout_seconds == 20
    assert s.ai.max_retries == 1


def test_settings_timers():
    s = load_settings()
    assert s.timers.draw_characters_seconds == 90
    assert s.timers.draw_action_seconds == 75
    assert s.timers.beat_seconds == 6


def test_settings_game():
    s = load_settings()
    assert s.game.max_players == 6
    assert s.game.min_players == 2


def test_settings_ui_tokens():
    """Presentation knobs shipped to the browser (canvas/floor color, prefill
    scale, reveal zoom, float timing, audience window)."""
    s = load_settings()
    assert s.ui.canvas_background_color == "#E8D5A8"
    assert s.ui.action_canvas_character_scale == 0.5
    assert s.ui.reveal_action_zoom_scale == 1.8
    assert s.ui.reveal_action_zoom_seconds == 2.5
    assert s.ui.float_number_seconds == 1.5
    assert s.ui.audience_recent_rounds == 3
    assert s.ui.arena_background == ""


def test_settings_ui_replay_and_splash_knobs():
    """Round-loop presentation knobs: COMBO! splash hold time and the
    instant-replay block (enabled, trigger events, slow-mo factor)."""
    s = load_settings()
    assert s.ui.combo_splash_seconds == 2.0
    assert s.ui.instant_replay.enabled is True
    assert s.ui.instant_replay.triggers == ["crit", "ko"]
    assert s.ui.instant_replay.slowmo_factor == 2.0


def test_settings_ui_defaults_when_block_missing(tmp_path: Path, monkeypatch):
    """A settings.yaml without a ui: block still loads (UIConfig defaults)."""
    minimal = {
        "server": {"host": "0.0.0.0", "port": 8000},
        "game": {"max_players": 6, "min_players": 2, "room_code_length": 4},
        "timers": {"draw_characters_seconds": 90, "draw_action_seconds": 75,
                   "warning_seconds": 10, "beat_seconds": 6},
        "ai": {"classify_model": "m", "narrate_model": "n"},
        "snapshots": {"enabled": False, "dir": "snapshots"},
    }
    (tmp_path / "settings.yaml").write_text(yaml.dump(minimal), encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    s = cfg_mod.load_settings()
    assert s.ui.canvas_background_color == "#E8D5A8"  # default


# ---------------------------------------------------------------------------
# balance.yaml
# ---------------------------------------------------------------------------


def test_balance_hp_formula():
    b = load_balance()
    assert b.hp_base == 18
    assert b.hp_per_power == 2
    assert b.ac_base == 11


def test_balance_stat_budget():
    b = load_balance()
    assert b.stat_budget == 8
    assert b.stat_min == 1
    assert b.stat_max == 4


def test_balance_cost_scaling_keys():
    b = load_balance()
    assert set(b.cost_scaling.keys()) == {1, 2, 3}


def test_balance_cost_scaling_values():
    b = load_balance()
    assert b.cost_scaling[1].damage_mult == 0.5
    assert b.cost_scaling[1].bank == 2
    assert b.cost_scaling[1].hit_bonus == 0

    assert b.cost_scaling[2].damage_mult == 1.0
    assert b.cost_scaling[2].bank == 1
    assert b.cost_scaling[2].hit_bonus == 0

    assert b.cost_scaling[3].damage_mult == 1.5
    assert b.cost_scaling[3].bank == 0
    assert b.cost_scaling[3].hit_bonus == 1


def test_balance_degrees_of_success():
    b = load_balance()
    assert b.crit_margin == 10
    assert b.fumble_margin == 10
    assert b.crit_damage_mult == 2.0


def test_balance_creativity_tiers():
    b = load_balance()
    assert b.creativity_tier_0 == 0
    assert b.creativity_tier_1 == 1
    assert b.creativity_tier_2 == 2
    assert b.creativity_tier_3 == 4


def test_balance_underdog():
    b = load_balance()
    assert b.underdog_enabled is True
    assert b.underdog_attack_bonus == 1


def test_balance_sudden_death():
    b = load_balance()
    assert b.max_rounds == 12
    assert b.sudden_death_attack_bonus == 2


# ---------------------------------------------------------------------------
# zones.yaml
# ---------------------------------------------------------------------------


def test_zones_default_three_zones():
    z = load_zones()
    zone_ids = {zone.id for zone in z.zones}
    assert zone_ids == {"glitter_back", "frontline", "thunder_back"}


def test_zones_frontline_adjacent():
    z = load_zones()
    zones_by_id = {zone.id: zone for zone in z.zones}
    frontline = zones_by_id["frontline"]
    assert "glitter_back" in frontline.adjacent
    assert "thunder_back" in frontline.adjacent


def test_zones_rules():
    z = load_zones()
    assert z.rules.melee_requires_same_zone is True
    assert z.rules.ranged_any_zone is True
    assert z.rules.move_cost_per_step == 1
    assert z.rules.free_steps_from_speed.threshold == 3
    assert z.rules.free_steps_from_speed.steps == 1


# ---------------------------------------------------------------------------
# High Ground — Phase 1 acceptance criteria
# ---------------------------------------------------------------------------


def test_high_ground_zone_modifiers(tmp_path: Path, monkeypatch):
    """Adding a High Ground block to zones.yaml loads and exposes its modifiers."""
    zones_data = {
        "zones": [
            {
                "id": "glitter_back",
                "name": "Team A Backline",
                "adjacent": ["frontline"],
                "tags": ["backline", "team_a"],
                "modifiers": {},
            },
            {
                "id": "frontline",
                "name": "The Pit",
                "adjacent": ["glitter_back", "thunder_back"],
                "tags": ["contested"],
                "modifiers": {},
            },
            {
                "id": "thunder_back",
                "name": "Team B Backline",
                "adjacent": ["frontline"],
                "tags": ["backline", "team_b"],
                "modifiers": {},
            },
            {
                "id": "high_ground",
                "name": "The High Ground",
                "adjacent": ["frontline"],
                "capacity": 2,
                "entry_cost": 2,
                "tags": ["elevated"],
                "modifiers": {
                    "attack_bonus": 1,
                    "ranged_ac_bonus": 1,
                    "fumble_extra": "prone",
                },
            },
        ],
        "rules": {
            "melee_requires_same_zone": True,
            "ranged_any_zone": True,
            "move_cost_per_step": 1,
            "free_steps_from_speed": {"threshold": 3, "steps": 1},
        },
    }

    (tmp_path / "zones.yaml").write_text(yaml.dump(zones_data), encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)

    zones = cfg_mod.load_zones()
    zone_map = {z.id: z for z in zones.zones}

    assert "high_ground" in zone_map, "High Ground zone not found after loading"
    hg = zone_map["high_ground"]

    assert hg.modifiers.attack_bonus == 1
    assert hg.modifiers.ranged_ac_bonus == 1
    assert hg.modifiers.fumble_extra == "prone"
    assert hg.entry_cost == 2
    assert hg.capacity == 2
    assert "frontline" in hg.adjacent
    assert "elevated" in hg.tags


# ---------------------------------------------------------------------------
# conditions.yaml
# ---------------------------------------------------------------------------


def test_conditions_loads():
    c = load_conditions()
    assert len(c.conditions) >= 12


def test_conditions_burning():
    c = load_conditions()
    burning = c.conditions["burning"]
    assert burning.duration == 2
    assert burning.tick_damage == 2
    assert "soggy" in burning.cure_tags


def test_conditions_soggy_cures_burning():
    c = load_conditions()
    soggy = c.conditions["soggy"]
    assert "burning" in soggy.immunities
    assert soggy.modifiers.power == -2


def test_conditions_prone():
    c = load_conditions()
    prone = c.conditions["prone"]
    assert prone.duration == 1
    assert prone.modifiers.ac == -1
    assert prone.stand_cost == 1


def test_conditions_hidden():
    c = load_conditions()
    hidden = c.conditions["hidden"]
    assert hidden.untargetable_melee is True
    assert hidden.ac_bonus_vs_ranged == 2


def test_conditions_confused():
    c = load_conditions()
    confused = c.conditions["confused"]
    assert confused.randomize_targets is True


def test_conditions_all_have_duration():
    c = load_conditions()
    for name, cond in c.conditions.items():
        assert cond.duration >= 1, f"Condition {name!r} has invalid duration {cond.duration}"


# ---------------------------------------------------------------------------
# moves.yaml
# ---------------------------------------------------------------------------


def test_moves_loads():
    m = load_moves()
    assert len(m.moves) >= 29  # spec says ~30, wildcard is always present


def test_moves_required_catalog_ids():
    m = load_moves()
    required = {
        "strike", "charge", "ray", "burst", "line", "dot", "drain", "summon",
        "grapple", "shove", "trip", "steal", "demoralize", "feint", "confuse",
        "trap", "wall", "defend", "counter", "hide", "protect", "sanctuary",
        "heal", "cleanse", "buff", "aid", "transform", "move", "stumble", "wildcard",
    }
    missing = required - set(m.moves.keys())
    assert not missing, f"Missing catalog moves: {missing}"


def test_moves_roll_stats():
    m = load_moves()
    assert m.moves["strike"].roll == "power"
    assert m.moves["ray"].roll == "weird"
    assert m.moves["burst"].roll == "weird"
    assert m.moves["defend"].roll == "none"
    assert m.moves["hide"].roll == "none"


def test_moves_burst_friendly_fire():
    m = load_moves()
    assert m.moves["burst"].friendly_fire is True
    assert m.moves["burst"].min_cost == 2


def test_moves_charge_includes_move():
    m = load_moves()
    assert m.moves["charge"].includes_move is True
    assert m.moves["charge"].min_cost == 2


def test_moves_min_cost_constraints():
    m = load_moves()
    for name, move in m.moves.items():
        assert move.min_cost >= 1 or move.fixed_cost is not None, (
            f"Move {name!r} has invalid min_cost {move.min_cost}"
        )


def test_moves_have_descriptions():
    m = load_moves()
    for name, move in m.moves.items():
        assert move.desc, f"Move {name!r} is missing a description"


def test_moves_wildcard_exists():
    m = load_moves()
    wc = m.moves["wildcard"]
    assert wc.roll == "weird"
    assert wc.damage == "d6"


# ---------------------------------------------------------------------------
# audio layer (GAME_DESIGN.md §13) — per-move sfx keys + event stingers
# ---------------------------------------------------------------------------


def test_settings_ui_audio_block():
    """The Web Audio manager knobs ship in ui: (→ DOODLE_CONFIG): master
    volume, ±10% pitch variation, and the event-type → stinger mapping."""
    s = load_settings()
    audio = s.ui.audio
    assert audio.enabled is True
    assert audio.volume == 0.8
    assert audio.pitch_variation == 0.10
    assert audio.events_sfx["crit"] == "crowd_roar"
    assert audio.events_sfx["fumble"] == "sad_trombone"
    assert audio.events_sfx["ko"] == "ko_bell"
    assert audio.events_sfx["combo"] == "air_horn"
    assert audio.events_sfx["sudden_death"] == "drumroll"
    assert audio.events_sfx["replay"] == "replay"


def test_moves_all_have_sfx():
    """Every catalog move names a sound clip — the host plays it when the
    move's beat lands."""
    m = load_moves()
    for name, move in m.moves.items():
        assert move.sfx, f"Move {name!r} is missing an sfx key"


def test_all_referenced_sfx_clips_exist():
    """Every clip referenced by moves.yaml or events_sfx exists in the sfx
    pack (web/host/assets/sfx/<name>.wav) — regenerate with scripts/make_sfx.py."""
    sfx_dir = Path(__file__).parent.parent / "web" / "host" / "assets" / "sfx"
    referenced = {move.sfx for move in load_moves().moves.values()}
    referenced |= set(load_settings().ui.audio.events_sfx.values())
    missing = {name for name in referenced if not (sfx_dir / f"{name}.wav").exists()}
    assert not missing, f"SFX clips missing from {sfx_dir}: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


def test_load_game_rules_bundle():
    rules = load_game_rules()
    assert rules.balance.hp_base == 18
    assert len(rules.zones.zones) == 3
    assert "strike" in rules.moves.moves
    assert "burning" in rules.conditions.conditions


def test_bad_yaml_raises_clear_error(tmp_path: Path, monkeypatch):
    """Invalid YAML must raise with the filename in the message."""
    (tmp_path / "settings.yaml").write_text("key: [\nbad yaml", encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    with pytest.raises(ValueError, match="settings.yaml"):
        cfg_mod.load_settings()


def test_missing_file_raises_clear_error(tmp_path: Path, monkeypatch):
    """Missing config file must raise FileNotFoundError with the path."""
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="settings.yaml"):
        cfg_mod.load_settings()
