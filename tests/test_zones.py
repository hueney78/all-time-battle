"""Zone registry tests — load-from-YAML and modifier queries."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from server.engine.zones import ZoneRegistry


def test_default_zones_load():
    reg = ZoneRegistry()
    assert "frontline" in reg
    assert "glitter_back" in reg
    assert "thunder_back" in reg


def test_adjacency():
    reg = ZoneRegistry()
    adj = reg.adjacent("frontline")
    assert "glitter_back" in adj
    assert "thunder_back" in adj


def test_backline_adjacent_to_frontline_only():
    reg = ZoneRegistry()
    assert reg.adjacent("glitter_back") == ["frontline"]
    assert reg.adjacent("thunder_back") == ["frontline"]


def test_unknown_zone_raises():
    reg = ZoneRegistry()
    with pytest.raises(KeyError, match="nonexistent"):
        reg.get("nonexistent")


def test_modifier_default_zero():
    reg = ZoneRegistry()
    assert reg.modifier("frontline", "attack_bonus") == 0
    assert reg.modifier("frontline", "ranged_ac_bonus") == 0


def test_high_ground_modifiers(tmp_path, monkeypatch):
    """High Ground zone exposes attack_bonus and ranged_ac_bonus modifiers."""
    import server.config as cfg_mod

    zones_data = {
        "zones": [
            {"id": "glitter_back", "name": "A", "adjacent": ["frontline"], "tags": [], "modifiers": {}},
            {"id": "frontline", "name": "Pit", "adjacent": ["glitter_back", "thunder_back"], "tags": [], "modifiers": {}},
            {"id": "thunder_back", "name": "B", "adjacent": ["frontline"], "tags": [], "modifiers": {}},
            {
                "id": "high_ground",
                "name": "High Ground",
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
    (tmp_path / "zones.yaml").write_text(yaml.dump(zones_data))
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)

    reg = ZoneRegistry()
    assert "high_ground" in reg
    assert reg.modifier("high_ground", "attack_bonus") == 1
    assert reg.modifier("high_ground", "ranged_ac_bonus") == 1
    assert reg.get("high_ground").modifiers.fumble_extra == "prone"
    assert reg.get("high_ground").entry_cost == 2
    assert reg.get("high_ground").capacity == 2


def test_all_ids_sorted():
    reg = ZoneRegistry()
    ids = reg.all_ids
    assert ids == sorted(ids)


def test_rules_loaded():
    reg = ZoneRegistry()
    assert reg.rules.melee_requires_same_zone is True
    assert reg.rules.ranged_any_zone is True
    assert reg.rules.move_cost_per_step == 1
    assert reg.rules.free_steps_from_speed.threshold == 3
