"""Phase 1 acceptance tests: config loading.

Covers: all YAML config files load without error; key values match the spec;
High Ground zone block added to zones.yaml loads and exposes its modifiers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import server.config as cfg_mod
from server.config import (
    load_balance,
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
    assert s.timers.beat_seconds == 15


def test_settings_game():
    s = load_settings()
    assert s.game.max_players == 6
    assert s.game.min_players == 2


def test_settings_montage():
    """Power-Up Montage cadence + bonus-phase timer (GAME_DESIGN §10.1)."""
    s = load_settings()
    assert s.game.montage_every_rounds == 3
    assert s.timers.montage_seconds == 20


def test_settings_gallery():
    """The Doodle Crowd persistence knobs (GAME_DESIGN §15)."""
    s = load_settings()
    assert s.gallery.enabled is True
    assert s.gallery.dir == "gallery"
    assert s.gallery.cap == 60
    assert s.gallery.cameo_count == 3


def test_settings_ui_tokens():
    """Presentation knobs shipped to the browser (canvas/floor color, prefill
    scale, reveal zoom, float timing, audience window)."""
    s = load_settings()
    assert s.ui.canvas_background_color == "#E8D5A8"
    assert s.ui.action_canvas_character_scale == 0.5
    assert s.ui.reveal_action_zoom_scale == 1.8
    assert s.ui.reveal_action_zoom_seconds == 2.5
    # Pacing knobs are hand-tuned at playtests — assert they ship, not their value.
    assert s.ui.reveal_beat_seconds >= 0     # 0 = manual (host clicks Next ▶)
    assert s.ui.float_number_seconds > 0
    assert s.ui.audience_recent_rounds == 3
    assert s.ui.arena_background == ""
    # Deliberation interlude + Power-Up Montage presentation knobs.
    assert s.ui.montage_canvas_character_scale == 0.88
    assert s.ui.deliberation_filler_seconds == 3.5


def test_settings_ui_replay_and_splash_knobs():
    """Round-loop presentation knobs: COMBO! splash hold time and the
    instant-replay block (enabled, trigger events, slow-mo factor)."""
    s = load_settings()
    assert s.ui.combo_splash_seconds == 2.0
    assert s.ui.instant_replay.enabled is True
    assert s.ui.instant_replay.triggers == ["crit", "ko"]
    assert s.ui.instant_replay.slowmo_factor == 2.0


def test_settings_phase_splash_knobs():
    """Phase splash (§13): duration + the per-phase text map, incl. the
    per-role Gremlin line ({round} substituted at send time)."""
    s = load_settings()
    assert s.ui.phase_splash_seconds == 2.0
    assert s.ui.splash_text["draw_characters"] == "Draw your Character!"
    assert "{round}" in s.ui.splash_text["draw_action"]
    assert "Gremlin" in s.ui.splash_text["gremlin"]
    assert "montage" in s.ui.splash_text


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
    assert s.ui.reveal_beat_seconds == 3.2            # UIConfig default


# ---------------------------------------------------------------------------
# balance.yaml
# ---------------------------------------------------------------------------


def test_balance_hp_formula():
    """COMBAT V2: HP = 20 + 2×POW (20–32), AC = 10 + SPD (10–16)."""
    b = load_balance()
    assert b.hp_base == 20
    assert b.hp_per_power == 2
    assert b.ac_base == 10


def test_balance_stat_budget():
    """COMBAT V2: stats 0–6 on a budget of 9."""
    b = load_balance()
    assert b.stat_budget == 9
    assert b.stat_min == 0
    assert b.stat_max == 6


def test_balance_degrees_of_success():
    """2d6 resolution: crit at +5/nat-12, fumble at nat-2 (+3 self-damage)."""
    b = load_balance()
    assert b.crit_margin == 5
    assert b.crit_damage_mult == 2.0
    assert b.fumble_self_damage == 3


def test_balance_combo_bonus():
    """Combos no longer fuse — both partners gain a flat roll bonus."""
    b = load_balance()
    assert b.combo_bonus == 2


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
    assert z.rules.move_buttons == ["move_l", "move_r"]


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
                },
            },
        ],
        "rules": {
            "melee_requires_same_zone": True,
            "ranged_any_zone": True,
            "move_buttons": ["move_l", "move_r"],
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
    assert hg.entry_cost == 2
    assert hg.capacity == 2
    assert "frontline" in hg.adjacent
    assert "elevated" in hg.tags


# ---------------------------------------------------------------------------
# moves.yaml
# ---------------------------------------------------------------------------


def test_moves_loads_the_eight_v2_moves():
    """COMBAT V2.1: exactly eight tapped moves — six combat + ◀/▶ movement."""
    m = load_moves()
    assert set(m.moves) == {"smash", "blast", "shoot", "shield", "rally", "wild",
                            "move_l", "move_r"}


def test_moves_roll_stats():
    m = load_moves()
    assert m.moves["smash"].stat == "power"
    assert m.moves["blast"].stat == "weird"
    assert m.moves["shoot"].stat == "weird"
    assert m.moves["wild"].stat == "weird"
    assert m.moves["shield"].stat == "none"
    assert m.moves["rally"].stat == "none"


def test_moves_v2_mechanics():
    m = load_moves()
    assert m.moves["smash"].range == "same_zone" and m.moves["smash"].auto_step is True
    assert m.moves["blast"].friendly_fire is True and m.moves["blast"].target == "zone_all"
    # SHOOT: ranged anywhere, half damage point-blank (v2.1)
    assert m.moves["shoot"].range == "any"
    assert m.moves["shoot"].same_zone_penalty == "half"
    # SHIELD: +4 AC to every ally in the caster's zone, reflect on big misses
    assert m.moves["shield"].target == "zone_allies"
    assert m.moves["shield"].ac_bonus == 4
    assert m.moves["shield"].reflect_miss_margin == 3
    assert m.moves["shield"].reflect_damage == "1d6"
    # RALLY: the drawing IS the medicine
    assert m.moves["rally"].heal == "1d6 + CRE"
    assert m.moves["wild"].fumble_on_roll_lte == 3
    assert m.moves["move_l"].move == -1 and m.moves["move_r"].move == 1
    assert m.moves["move_l"].ac_bonus == 1        # dodging on the move
    assert m.moves["move_l"].is_movement and not m.moves["smash"].is_movement


def test_moves_have_buttons_and_descriptions():
    """Every move ships a phone button label and a description."""
    m = load_moves()
    for name, move in m.moves.items():
        assert move.desc, f"Move {name!r} is missing a description"
        assert move.button, f"Move {name!r} is missing a button label"


def test_move_formulas_evaluate_for_every_stat_line():
    """Every damage/heal formula evaluates across the whole 0–6 stat range —
    the same rendering powers the phone's live button math."""
    from server.engine.dice import describe_formula

    m = load_moves()
    for name, move in m.moves.items():
        for spec in (move.damage, move.heal):
            if not spec:
                continue
            for v in range(0, 7):
                label = describe_formula(spec, {"POW": v, "SPD": v, "WRD": v, "CRE": v})
                assert label, f"{name}: formula {spec!r} failed at stat {v}"


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


# ---------------------------------------------------------------------------
# hazards.yaml — the Arena Gremlin palette (data-driven, GAME_DESIGN §10)
# ---------------------------------------------------------------------------


def test_hazards_load_and_map_to_effects():
    """v2.1: hazards are damage-or-push only — no status effects."""
    from server.config import load_hazards
    h = load_hazards().hazards
    assert h["bees"].damage == "1d4"
    assert h["spikes"].damage == "1d4"
    assert h["banana_peel"].forces_move is True
    assert h["trapdoor"].forces_move is True


def test_novel_hazard_added_to_yaml(tmp_path: Path, monkeypatch):
    """A hazard added only to hazards.yaml loads — zero Python (data-driven),
    the gremlin analogue of the High Ground zone test."""
    from server.engine.hazards import HazardRegistry

    data = yaml.safe_load(open("config/hazards.yaml", encoding="utf-8"))
    data["hazards"]["quicksand"] = {
        "damage": "1d6", "emoji": "⌛", "desc": "sinking sand",
    }
    (tmp_path / "hazards.yaml").write_text(yaml.dump(data), encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)

    reg = HazardRegistry()
    assert "quicksand" in reg
    assert reg.get("quicksand").damage == "1d6"


def test_load_game_rules_bundle():
    rules = load_game_rules()
    assert rules.balance.hp_base == 20
    assert len(rules.zones.zones) == 3
    assert "smash" in rules.moves.moves
    assert rules.hazards.hazards["bees"].damage == "1d4"


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


def test_move_and_hazard_registries_raise_on_unknown_ids():
    """Registry lookups fail loudly with the known ids in the message."""
    import pytest

    from server.engine.hazards import HazardRegistry
    from server.engine.moves import MoveRegistry

    with pytest.raises(KeyError, match="smash"):
        MoveRegistry().get("uppercut")
    with pytest.raises(KeyError, match="bees"):
        HazardRegistry().get("lava_pit")
    assert "smash" in MoveRegistry().all_ids
