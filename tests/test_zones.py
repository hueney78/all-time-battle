"""Zone registry tests — load-from-YAML and modifier queries."""

from __future__ import annotations

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
    assert reg.modifier("frontline", "damage_bonus") == 0
    assert reg.modifier("frontline", "heal_bonus") == 0


def _high_ground_zones() -> dict:
    """The GAME_DESIGN §6 High Ground block, on v5's modifier keys."""
    return {
        "zones": [
            {"id": "glitter_back", "name": "A", "adjacent": ["frontline"],
             "tags": [], "modifiers": {}},
            {"id": "frontline", "name": "Pit",
             "adjacent": ["glitter_back", "thunder_back"], "tags": [], "modifiers": {}},
            {"id": "thunder_back", "name": "B", "adjacent": ["frontline"],
             "tags": [], "modifiers": {}},
            {
                "id": "high_ground",
                "name": "High Ground",
                "adjacent": ["frontline"],
                "capacity": 2,
                "entry_cost": 2,
                "tags": ["elevated"],
                "modifiers": {
                    "damage_bonus": 1,
                    "incoming_damage_bonus": 2,
                    "some_future_key": "prone",
                },
            },
        ],
        "rules": {
            "melee_requires_same_zone": True,
            "ranged_any_zone": True,
        },
    }


def test_high_ground_modifiers(tmp_path, monkeypatch):
    """High Ground exposes v5's damage modifiers — the "zero Python" test. v5 has
    no AC and no dodge, so a zone's edge is flat damage or healing now."""
    import server.config as cfg_mod

    (tmp_path / "zones.yaml").write_text(yaml.dump(_high_ground_zones()))
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)

    reg = ZoneRegistry()
    assert "high_ground" in reg
    assert reg.modifier("high_ground", "damage_bonus") == 1
    assert reg.modifier("high_ground", "incoming_damage_bonus") == 2
    # Unknown keys still load (extra="allow") rather than failing the room.
    assert reg.get("high_ground").modifiers.some_future_key == "prone"
    assert reg.get("high_ground").entry_cost == 2
    assert reg.get("high_ground").capacity == 2


def test_high_ground_modifiers_actually_reach_the_resolver(tmp_path, monkeypatch):
    """The modifiers must change resolution, not just load — otherwise "adding
    High Ground is a YAML-only change" is a claim nothing checks."""
    import shutil

    import server.config as cfg_mod
    from server.config import load_balance
    from server.engine.dice import Dice
    from server.engine.models import Character, ClassifiedAction, GameState, Stats, Team
    from server.engine.resolver import resolve_round

    # The resolver loads the whole rule bundle — only zones.yaml is overridden.
    for f in ("moves.yaml", "balance.yaml"):
        shutil.copy(f"config/{f}", tmp_path / f)
    (tmp_path / "zones.yaml").write_text(yaml.dump(_high_ground_zones()))
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    cfg = load_balance()

    def run(attacker_zone: str) -> int:
        def ch(pid, zone, speed):
            return Character(player_id=pid, name=pid,
                             stats=Stats(power=2, speed=speed, weird=0),
                             hp=40, max_hp=40, zone_id=zone)
        state = GameState(
            room_id="T", round=1,
            characters={"atk": ch("atk", attacker_zone, 4), "def": ch("def", "frontline", 0)},
            teams=[Team(id="a", name="A", color="p", player_ids=["atk"]),
                   Team(id="b", name="B", color="b", player_ids=["def"])],
        )
        actions = [ClassifiedAction(player_id="atk", move_id="blast", target_id="def")]
        result = resolve_round(state, actions, Dice(seed=5), cfg)
        return next(e for e in result.events
                    if e.data.get("move_id") == "blast").data["damage"]

    # Same seed, same dice: BLASTing FROM the high ground adds its damage_bonus.
    assert run("high_ground") - run("glitter_back") == 1


def test_all_ids_sorted():
    reg = ZoneRegistry()
    ids = reg.all_ids
    assert ids == sorted(ids)


def test_rules_loaded():
    reg = ZoneRegistry()
    assert reg.rules.melee_requires_same_zone is True
    assert reg.rules.ranged_any_zone is True


def test_ordered_ids_follow_yaml_order():
    """zones.yaml list order = the TV's left→right order — ESCAPE steps along it."""
    reg = ZoneRegistry()
    assert reg.ordered_ids == ["glitter_back", "frontline", "thunder_back"]


def test_step_moves_along_order_and_stops_at_edges():
    reg = ZoneRegistry()
    assert reg.step("frontline", -1) == "glitter_back"
    assert reg.step("frontline", 1) == "thunder_back"
    assert reg.step("glitter_back", -1) is None    # arena edge
    assert reg.step("thunder_back", 1) is None
    assert reg.steps_between("glitter_back", "thunder_back") == 2
    assert reg.steps_between("thunder_back", "frontline") == -1
