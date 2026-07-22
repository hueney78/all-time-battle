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
    load_lore,
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
    assert s.ai.timeout_seconds == 40
    assert s.ai.max_retries == 1


def test_settings_ai_announcer_cap():
    """Announcer line-length cap (GAME_DESIGN §11.2): a non-negative int shipped
    in the ai block (0 = no limit). Kept value-agnostic so it doesn't drift when
    the cap is re-tuned."""
    s = load_settings()
    assert isinstance(s.ai.max_announcer_chars, int)
    assert s.ai.max_announcer_chars >= 0


def test_settings_ai_announcer_cap_defaults_to_no_limit(tmp_path: Path, monkeypatch):
    """A settings.yaml whose ai block omits the cap still loads (default 0)."""
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
    assert cfg_mod.load_settings().ai.max_announcer_chars == 0


def test_settings_timers():
    s = load_settings()
    assert s.timers.draw_characters_seconds == 240
    assert s.timers.draw_action_seconds == 120
    assert s.timers.beat_seconds == 30


def test_settings_game():
    s = load_settings()
    assert s.game.max_players == 8
    assert s.game.min_players == 2


def test_settings_montage():
    """Power-Up Montage cadence + bonus-phase timer (GAME_DESIGN §10.1)."""
    s = load_settings()
    assert s.game.montage_every_rounds == 3
    assert s.timers.montage_seconds == 40


def test_settings_gallery():
    """The Doodle Crowd persistence knobs (GAME_DESIGN §15)."""
    s = load_settings()
    assert s.gallery.enabled is True
    assert s.gallery.dir == "gallery"
    assert s.gallery.cap == 60
    assert s.gallery.cameo_count == 3


def test_lore_config_loads():
    """Optional family in-jokes (GAME_DESIGN §11.3): the file loads, usage is a
    known level, and every entry carries a term."""
    lore = load_lore()
    assert lore.usage in ("never", "occasional", "frequent")
    assert isinstance(lore.lore, list)
    assert all(e.term for e in lore.lore)


def test_game_rules_bundles_lore():
    """load_game_rules exposes lore alongside settings/balance/zones/moves."""
    rules = load_game_rules()
    assert rules.lore.usage in ("never", "occasional", "frequent")


def test_settings_ui_tokens():
    """Presentation knobs shipped to the browser (canvas/floor color, prefill
    scale, reveal zoom, float timing, audience window)."""
    s = load_settings()
    assert s.ui.canvas_background_color == "#E8D5A8"
    assert s.ui.action_canvas_character_scale == 0.5
    assert s.ui.reveal_action_zoom_scale == 2.8
    assert s.ui.reveal_action_zoom_seconds == 2.5
    assert s.ui.reveal_beat_seconds >= 0     # 0 = manual (host clicks Next ▶)
    assert s.ui.float_number_seconds > 0
    assert s.ui.audience_recent_rounds == 3
    assert s.ui.arena_background == ""
    assert s.ui.montage_canvas_character_scale == 0.88
    assert s.ui.deliberation_filler_seconds == 3.5


def test_settings_ui_replay_and_splash_knobs():
    """Round-loop presentation knobs: COMBO! splash hold time and the
    instant-replay block (enabled, trigger events, slow-mo factor)."""
    s = load_settings()
    assert s.ui.combo_splash_seconds == 2.0
    assert s.ui.instant_replay.enabled is True
    # The spike moment is creativity tier 3, not a random crit.
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
    """COMBAT V6: HP = 27 + 2×POW + WRD (27–42; Speed grants no HP). There is no AC."""
    b = load_balance()
    assert b.hp_base == 27
    assert b.hp_per_power == 2
    assert b.hp_per_weird == 1
    assert b.hp_per_speed == 0
    assert not hasattr(b, "ac_base")

    def hp(power, speed, weird):
        return (b.hp_base + b.hp_per_power * power + b.hp_per_weird * weird
                + b.hp_per_speed * speed)

    assert hp(0, 0, 0) == 27
    assert hp(6, 6, 3) == 42   # Speed adds nothing → identical to hp(6, 0, 3)
    assert hp(6, 0, 3) == 42   # the top end on budget 9 (POW 6 / WRD 3)


def test_balance_stat_budget():
    """COMBAT V5: stats 0–6 on a budget of 9."""
    b = load_balance()
    assert b.stat_budget == 9
    assert b.stat_min == 0
    assert b.stat_max == 6


def test_balance_has_no_removed_knobs():
    """v5 deleted AC, to-hit, crits, dodge, and WILD's backfire outright (§5)."""
    b = load_balance()
    for gone in ("ac_base", "crit_margin", "crit_damage_mult", "fumble_self_damage",
                 "combo_bonus", "dodge_per_speed", "dodge_cap", "wild_backfire_damage"):
        assert not hasattr(b, gone), f"{gone} should be gone in v5"


def test_balance_reflect_shield():
    """PROTECT's reflect shield is the only damage reduction in the game (§5)."""
    b = load_balance()
    assert b.reflect_per_weird == 0.05
    assert b.reflect_cap == 0.30
    # At the stat ceiling (Weird 6) the reflect share hits the cap exactly.
    assert b.reflect_per_weird * b.stat_max == pytest.approx(0.30)


def test_balance_trap_damage():
    """Arena Gremlin traps: trap_damage + creativity to one enemy (§10)."""
    b = load_balance()
    assert b.trap_damage == "1d4"


def test_balance_combo_tier_bonus():
    """Combos escalate the creativity TIER (no fusion, no roll) — how they reach
    DEVASTATING (§8)."""
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
    assert b.sudden_death_damage_bonus == 3


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
    assert not hasattr(z.rules, "move_buttons")   # v5: movement is inside moves


# ---------------------------------------------------------------------------
# High Ground — Phase 1 acceptance criteria
# ---------------------------------------------------------------------------


def test_high_ground_zone_modifiers(tmp_path: Path, monkeypatch):
    """Adding a High Ground block to zones.yaml loads and exposes its modifiers."""
    zones_data = {
        "zones": [
            {"id": "glitter_back", "name": "Team A Backline",
             "adjacent": ["frontline"], "tags": ["backline", "team_a"], "modifiers": {}},
            {"id": "frontline", "name": "The Pit",
             "adjacent": ["glitter_back", "thunder_back"], "tags": ["contested"],
             "modifiers": {}},
            {"id": "thunder_back", "name": "Team B Backline",
             "adjacent": ["frontline"], "tags": ["backline", "team_b"], "modifiers": {}},
            {
                "id": "high_ground",
                "name": "The High Ground",
                "adjacent": ["frontline"],
                "capacity": 2,
                "entry_cost": 2,
                "tags": ["elevated"],
                "modifiers": {"damage_bonus": 1, "incoming_damage_bonus": 2},
            },
        ],
        "rules": {"melee_requires_same_zone": True, "ranged_any_zone": True},
    }

    (tmp_path / "zones.yaml").write_text(yaml.dump(zones_data), encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)

    zones = cfg_mod.load_zones()
    zone_map = {z.id: z for z in zones.zones}

    assert "high_ground" in zone_map, "High Ground zone not found after loading"
    hg = zone_map["high_ground"]

    # v5's modifier keys — there is no AC and no dodge.
    assert hg.modifiers.damage_bonus == 1
    assert hg.modifiers.incoming_damage_bonus == 2
    assert hg.entry_cost == 2
    assert hg.capacity == 2
    assert "frontline" in hg.adjacent
    assert "elevated" in hg.tags


# ---------------------------------------------------------------------------
# moves.yaml
# ---------------------------------------------------------------------------


def test_moves_loads_the_five_v5_moves():
    """COMBAT V5: exactly five tapped moves, all single-target."""
    m = load_moves()
    assert set(m.moves) == {"smash", "blast", "charge", "escape", "protect"}


def test_moves_headline_stats():
    """`stat` is the readout's term, not a roll — v5 has no attack roll."""
    m = load_moves()
    assert m.moves["smash"].stat == "power"
    assert m.moves["blast"].stat == "weird"
    assert m.moves["charge"].stat == "avg(power,speed)"
    assert m.moves["escape"].stat == "speed"
    assert m.moves["protect"].stat == "weird"


def test_moves_v5_mechanics():
    m = load_moves()
    # SMASH: melee, same zone.
    assert m.moves["smash"].range == "same_zone"
    assert m.moves["smash"].damage == "2d4 + POW + 2"
    # BLAST: ranged anywhere, half point-blank, the always-legal fallback.
    assert m.moves["blast"].range == "any_zone"
    assert m.moves["blast"].same_zone_penalty == "half"
    assert m.moves["blast"].always_legal is True
    assert m.moves["blast"].damage == "2d4 + WRD + 2"
    # CHARGE: rush into the target's zone (always legal), avg(POW,SPD).
    assert m.moves["charge"].moves_to_target is True
    assert m.moves["charge"].always_legal is True
    assert m.moves["charge"].damage == "2d4 + avg(POW,SPD)"
    # ESCAPE: slip one zone (player picks ◀/▶), then a ranged hit off Speed.
    assert m.moves["escape"].moves_one_zone is True
    assert m.moves["escape"].damage == "2d4 + SPD"
    # PROTECT: acts first, heals + raises a reflecting shield.
    assert m.moves["protect"].acts_first is True
    assert m.moves["protect"].applies_shield is True
    assert m.moves["protect"].target == "ally"
    assert m.moves["protect"].heal == "1d6 + WRD"


def test_moves_have_no_removed_riders():
    """v5 deleted AC, dodge, WILD's backfire, SHIELD's mitigation, and movement
    buttons — those catalog keys are gone from the schema."""
    m = load_moves()
    for gone in ("mitigate", "reflect_chance_per_power", "friendly_fire",
                 "auto_step", "backfire_chance", "ai_interprets", "move"):
        assert not hasattr(m.moves["smash"], gone), f"{gone} should be gone in v5"


def test_moves_never_spell_out_creativity():
    """§5 makes creativity a system rule the resolver applies to every
    damage/heal — a formula that names it would double-count."""
    m = load_moves()
    for name, move in m.moves.items():
        for spec in (move.damage, move.heal):
            assert "CRE" not in (spec or ""), f"{name} spells out creativity"


def test_moves_have_buttons_icons_and_descriptions():
    """Every move ships a phone button label, a readout icon, and a description."""
    m = load_moves()
    for name, move in m.moves.items():
        assert move.desc, f"Move {name!r} is missing a description"
        assert move.button, f"Move {name!r} is missing a button label"
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
                label = describe_formula(spec, {"POW": v, "SPD": v, "WRD": v})
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
    # v5 stingers: DEVASTATING roar, reflect boing, trap snap — no dodge/backfire.
    assert audio.events_sfx["devastating"] == "crowd_roar"
    assert audio.events_sfx["reflect"] == "boing"
    assert audio.events_sfx["trap"] == "comic_snap"
    assert "dodge" not in audio.events_sfx and "backfire" not in audio.events_sfx
    assert "crit" not in audio.events_sfx and "fumble" not in audio.events_sfx
    assert audio.events_sfx["ko"] == "ko_bell"
    assert audio.events_sfx["combo"] == "air_horn"
    assert audio.events_sfx["sudden_death"] == "drumroll"
    assert audio.events_sfx["replay"] == "replay"


def test_settings_ui_tts_block():
    """Host announcer Text-to-Speech (§13): shipped in ui: (→ DOODLE_CONFIG) with
    one voice per announcer so pbp and color read in different voices."""
    s = load_settings()
    tts = s.ui.tts
    assert isinstance(tts.enabled, bool)
    assert 0 <= tts.volume <= 1
    # a voice config per announcer, each with a lang the client can match on
    assert set(tts.voices) == {"pbp", "color"}
    assert tts.voices["pbp"].lang and tts.voices["color"].lang
    # pbp and color are tuned to sound different out of the box
    assert tts.voices["pbp"].lang != tts.voices["color"].lang


def test_settings_ui_tts_defaults_when_block_missing(tmp_path: Path, monkeypatch):
    """A settings.yaml without a ui.tts block still loads (TTSConfig defaults)."""
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
    assert s.ui.tts.enabled is True
    assert set(s.ui.tts.voices) == {"pbp", "color"}


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
    assert rules.balance.hp_base == 27
    assert len(rules.zones.zones) == 3
    assert "smash" in rules.moves.moves
    assert not hasattr(rules, "hazards")   # v5: traps replaced the hazard palette


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


def test_move_registry_raises_on_unknown_id():
    """Registry lookups fail loudly with the known ids in the message."""
    from server.engine.moves import MoveRegistry

    with pytest.raises(KeyError, match="smash"):
        MoveRegistry().get("uppercut")
    assert "smash" in MoveRegistry().all_ids
