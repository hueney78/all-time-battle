"""Condition registry tests — load-from-YAML and resolution mechanics."""

from __future__ import annotations

from server.engine.conditions import ConditionRegistry
from server.engine.resolver import _apply_condition, _tick_conditions
from server.engine.models import Character, Event, Stats
from server.config import load_balance

CFG = load_balance()


def _ch(player_id: str, hp: int = 20, conds: dict | None = None) -> Character:
    return Character(
        player_id=player_id, name=player_id,
        stats=Stats(power=2, speed=2, weird=2),
        hp=hp, max_hp=22, ac=13, zone_id="frontline",
        conditions=conds or {},
    )


def test_registry_loads_all_conditions():
    reg = ConditionRegistry()
    for name in ["burning", "soggy", "sticky", "prone", "frightened",
                 "embarrassed", "enraged", "sparkly", "hidden",
                 "off_balance", "pumped", "confused"]:
        assert name in reg, f"Missing condition: {name}"


def test_burning_tick_damage():
    reg = ConditionRegistry()
    ch = _ch("p1", hp=20, conds={"burning": 2})
    chars = {"p1": ch}
    events: list[Event] = []
    _tick_conditions(chars, events, round_num=1, cond_reg=reg)
    assert chars["p1"].hp == 18  # 2 tick damage
    tick_evs = [e for e in events if e.type.value == "condition_ticked"]
    assert tick_evs, "Should emit tick event"
    assert tick_evs[0].data["damage"] == 2


def test_condition_expires_after_duration():
    reg = ConditionRegistry()
    ch = _ch("p1", conds={"prone": 1})
    chars = {"p1": ch}
    events: list[Event] = []
    _tick_conditions(chars, events, 1, reg)
    assert "prone" not in chars["p1"].conditions
    exp_evs = [e for e in events if e.type.value == "condition_expired"]
    assert any(e.data.get("condition") == "prone" for e in exp_evs)


def test_burning_duration_decrements():
    reg = ConditionRegistry()
    ch = _ch("p1", conds={"burning": 2})
    chars = {"p1": ch}
    _tick_conditions(chars, events := [], 1, reg)
    assert chars["p1"].conditions.get("burning") == 1


def test_soggy_immunity_blocks_burning():
    """Soggy has burning in immunities — burning must not be applied."""
    reg = ConditionRegistry()
    ch = _ch("p1", conds={"soggy": 2})
    chars = {"p1": ch}
    events: list[Event] = []
    _apply_condition("burning", "p1", ch, chars, events, 1, reg)
    assert "burning" not in ch.conditions, "soggy should block burning"
    # No condition_applied event emitted
    applied = [e for e in events if e.type.value == "condition_applied"]
    assert not applied


def test_apply_condition_sets_duration():
    reg = ConditionRegistry()
    ch = _ch("p1")
    chars = {"p1": ch}
    events: list[Event] = []
    _apply_condition("frightened", "p1", ch, chars, events, 1, reg)
    assert ch.conditions.get("frightened") == reg.get("frightened").duration
    applied = [e for e in events if e.type.value == "condition_applied"]
    assert applied


def test_apply_condition_emits_event():
    reg = ConditionRegistry()
    ch = _ch("p1")
    chars = {"p1": ch}
    events: list[Event] = []
    _apply_condition("burning", "p1", ch, chars, events, 3, reg)
    ev = next(e for e in events if e.type.value == "condition_applied")
    assert ev.player_id == "p1"
    assert ev.data["condition"] == "burning"
    assert ev.round == 3


def test_multiple_ticks_reduce_hp():
    reg = ConditionRegistry()
    ch = _ch("p1", hp=10, conds={"burning": 2})
    chars = {"p1": ch}
    _tick_conditions(chars, [], 1, reg)  # round 1: tick, duration 2→1
    assert chars["p1"].hp == 8
    _tick_conditions(chars, [], 2, reg)  # round 2: tick, duration 1→0, expires
    assert chars["p1"].hp == 6
    assert "burning" not in chars["p1"].conditions


def test_conditions_have_emoji():
    """All conditions in conditions.yaml define an emoji."""
    reg = ConditionRegistry()
    for name in reg.all_ids:
        assert reg.get(name).emoji, f"Condition {name!r} missing emoji"


def test_novel_condition_added_to_yaml(tmp_path, monkeypatch):
    """A condition added only to conditions.yaml loads and resolves."""
    import yaml
    import server.config as cfg_mod

    data = yaml.safe_load(open("config/conditions.yaml"))
    data["conditions"]["turbo_charged"] = {
        "duration": 3,
        "modifiers": {"attack": 2},
        "emoji": "⚡",
    }
    (tmp_path / "conditions.yaml").write_text(yaml.dump(data))
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)

    reg = ConditionRegistry()
    assert "turbo_charged" in reg
    cdef = reg.get("turbo_charged")
    assert cdef.duration == 3
    assert cdef.modifiers.attack == 2
