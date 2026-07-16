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
    # v4's spike moment is creativity tier 3, not a random crit.
    assert s.ui.instant_replay.triggers == ["devastating", "ko"]
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


def test_settings_how_to_play():
    """Lobby rules copy (§13): a title, the five numbered steps, and two tips —
    all editable in settings.yaml, shipped to both pages via DOODLE_CONFIG."""
    s = load_settings()
    h = s.ui.how_to_play
    assert h.title == "How to Play"
    assert len(h.steps) == 5
    assert h.steps[0].startswith("1️⃣")
    assert "Draw your fighter" in h.steps[0]
    assert "COMBO" in h.steps[3]
    assert "Gremlin" in h.steps[4]
    assert len(h.tips) == 2
    assert any("Initiative" in t for t in h.tips)


def test_settings_how_to_play_defaults_when_block_missing(tmp_path: Path, monkeypatch):
    """A settings.yaml without a how_to_play block still loads sensible rules."""
    minimal = {
        "server": {"host": "0.0.0.0", "port": 8000},
        "game": {"max_players": 6, "min_players": 2, "room_code_length": 4},
        "timers": {"draw_characters_seconds": 90, "draw_action_seconds": 75,
                   "warning_seconds": 10, "beat_seconds": 6},
        "ai": {"classify_model": "m", "narrate_model": "n"},
        "snapshots": {"enabled": False, "dir": "snapshots"},
        "ui": {},
    }
    (tmp_path / "settings.yaml").write_text(yaml.dump(minimal), encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    s = cfg_mod.load_settings()
    assert len(s.ui.how_to_play.steps) == 5   # UIConfig default


def test_settings_stands():
    """Doodle Crowd stands (§15): how many spectators show at once and how often
    the visible handful rotates — presentation knobs shipped via DOODLE_CONFIG."""
    s = load_settings()
    assert s.ui.stands.max == 14
    assert s.ui.stands.rotate_seconds == 12


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
    """COMBAT V4: HP = 28 + 2×POW + WRD (28–43). There is no AC."""
    b = load_balance()
    assert b.hp_base == 28
    assert b.hp_per_power == 2
    assert b.hp_per_weird == 1
    assert not hasattr(b, "ac_base")

    # The budget caps the top end at POW 6 / WRD 3, not POW 6 / WRD 6.
    def hp(power, weird):
        return b.hp_base + b.hp_per_power * power + b.hp_per_weird * weird

    assert hp(0, 0) == 28
    assert hp(6, 3) == 43


def test_balance_stat_budget():
    """COMBAT V4: stats 0–6 on a budget of 9."""
    b = load_balance()
    assert b.stat_budget == 9
    assert b.stat_min == 0
    assert b.stat_max == 6


def test_balance_has_no_attack_roll_knobs():
    """v4 deleted AC, to-hit, crits, and attacker fumbles outright (§5)."""
    b = load_balance()
    for gone in ("ac_base", "crit_margin", "crit_damage_mult",
                 "fumble_self_damage", "combo_bonus"):
        assert not hasattr(b, gone), f"{gone} should be gone in v4"


def test_balance_dodge():
    """Dodge is the only thing that negates a hit: dodge_per_speed x Speed, capped.
    Speed's rebalance (ranged moved to Weird) bumped the rate 0.05 → 0.07."""
    b = load_balance()
    assert b.dodge_per_speed == 0.07
    assert b.dodge_cap == 0.45
    # At the stat ceiling (Speed 6) dodge is 0.42 — under the cap, which is now a
    # headroom rail that only binds for montage-boosted Speed 7+.
    assert b.dodge_per_speed * b.stat_max == pytest.approx(0.42)
    assert b.dodge_per_speed * b.stat_max <= b.dodge_cap


def test_balance_wild_backfire():
    """WILD CARD's self-damage — the only self-damage in the game."""
    b = load_balance()
    assert b.wild_backfire_damage == "2d4"


def test_balance_combo_tier_bonus():
    """Combos no longer fuse, and no longer touch a roll — they escalate the
    creativity TIER, which is how they reach DEVASTATING (§8)."""
    b = load_balance()
    assert b.combo_tier_bonus == 1


def test_balance_creativity_tiers():
    """Flat bonuses added straight to the effect — there is no roll to add to."""
    b = load_balance()
    assert b.creativity_tier_0 == 0
    assert b.creativity_tier_1 == 1
    assert b.creativity_tier_2 == 3
    assert b.creativity_tier_3 == 5


def test_balance_underdog():
    b = load_balance()
    assert b.underdog_enabled is True
    assert b.underdog_damage_bonus == 1


def test_balance_sudden_death():
    b = load_balance()
    assert b.max_rounds == 12
    assert b.sudden_death_damage_bonus == 2


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
                    "damage_bonus": 1,
                    "incoming_dodge_penalty": 0.10,
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

    # v4's modifier keys — there is no AC, so no attack_bonus/ranged_ac_bonus.
    assert hg.modifiers.damage_bonus == 1
    assert hg.modifiers.incoming_dodge_penalty == 0.10
    assert hg.entry_cost == 2
    assert hg.capacity == 2
    assert "frontline" in hg.adjacent
    assert "elevated" in hg.tags


# ---------------------------------------------------------------------------
# moves.yaml
# ---------------------------------------------------------------------------


def test_moves_loads_the_eight_v4_moves():
    """COMBAT V4: exactly eight tapped moves — six combat + ◀/▶ movement."""
    m = load_moves()
    assert set(m.moves) == {"smash", "blast", "shoot", "shield", "rally", "wild",
                            "move_l", "move_r"}


def test_moves_headline_stats():
    """`stat` is the readout's term, not a roll — v4 has no attack roll."""
    m = load_moves()
    assert m.moves["smash"].stat == "power"
    assert m.moves["blast"].stat == "weird"
    assert m.moves["wild"].stat == "weird"
    assert m.moves["shield"].stat == "power"
    assert m.moves["rally"].stat == "weird"
    # SHOOT keys off Weird only (balance lever: Speed no longer doubles as ranged).
    assert m.moves["shoot"].stat == "weird"
    assert m.moves["move_l"].stat == "none"


def test_moves_v4_mechanics():
    m = load_moves()
    assert m.moves["smash"].range == "same_zone" and m.moves["smash"].auto_step is True
    assert m.moves["smash"].damage == "2d4 + POW + 2"
    assert m.moves["blast"].friendly_fire is True and m.moves["blast"].target == "zone_all"
    assert m.moves["blast"].damage == "1d6 + WRD"
    # SHOOT: ranged anywhere, half damage point-blank, keyed off Weird
    assert m.moves["shoot"].range == "any"
    assert m.moves["shoot"].same_zone_penalty == "half"
    assert m.moves["shoot"].damage == "2d4 + WRD"
    # SHIELD: flat `4 + POW` mitigation for every ally in the caster's zone,
    # then a 10% x POW chance to reflect what it swallowed.
    assert m.moves["shield"].target == "zone_allies"
    assert m.moves["shield"].mitigate == "4 + POW"
    assert m.moves["shield"].reflect_chance_per_power == 0.10
    # RALLY: the drawing IS the medicine (creativity is added by the resolver).
    assert m.moves["rally"].heal == "2d6 + 2*WRD + 2"
    # WILD CARD: the only move that can backfire, and the only one whose
    # drawing the AI reads freely (§9).
    assert m.moves["wild"].backfire_chance == 0.15
    assert m.moves["wild"].ai_interprets is True
    assert [mid for mid, d in m.moves.items() if d.ai_interprets] == ["wild"]
    assert [mid for mid, d in m.moves.items() if d.backfire_chance] == ["wild"]
    assert m.moves["move_l"].move == -1 and m.moves["move_r"].move == 1
    assert m.moves["move_l"].is_movement and not m.moves["smash"].is_movement


def test_moves_have_no_ac_or_fumble_riders():
    """v4 deleted AC, so SHIELD's ac_bonus, movement's dodge AC, and WILD's
    fumble band are gone from the catalog schema."""
    m = load_moves()
    for gone in ("ac_bonus", "reflect_miss_margin", "reflect_damage",
                 "fumble_on_roll_lte"):
        assert not hasattr(m.moves["shield"], gone), f"{gone} should be gone in v4"
        assert not hasattr(m.moves["move_l"], gone)


def test_moves_never_spell_out_creativity():
    """§5 makes creativity a system rule the resolver applies to every
    damage/heal — a formula that names it would double-count."""
    m = load_moves()
    for name, move in m.moves.items():
        for spec in (move.damage, move.heal, move.mitigate):
            assert "CRE" not in (spec or ""), f"{name} spells out creativity"


def test_moves_have_buttons_icons_and_descriptions():
    """Every move ships a phone button label, a readout icon, and a description."""
    m = load_moves()
    for name, move in m.moves.items():
        assert move.desc, f"Move {name!r} is missing a description"
        assert move.button, f"Move {name!r} is missing a button label"
        if not move.is_movement:
            assert move.icon, f"Move {name!r} is missing a readout icon"


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
    # v4 stingers: DEVASTATING replaces the crit roar, and the sad trombone
    # follows the fumble → WILD CARD backfire.
    assert audio.events_sfx["devastating"] == "crowd_roar"
    assert audio.events_sfx["dodge"] == "whoosh"
    assert audio.events_sfx["backfire"] == "sad_trombone"
    assert "crit" not in audio.events_sfx and "fumble" not in audio.events_sfx
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
    assert rules.balance.hp_base == 28
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
