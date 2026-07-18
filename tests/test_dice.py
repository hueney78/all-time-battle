"""Tests for the seeded dice wrapper + the COMBAT V5 formula evaluator."""

import pytest

from server.engine.dice import Dice, describe_formula, formula_parts


def test_there_is_no_attack_roll():
    """COMBAT V5 has no attack roll and no dodge — every move lands (§5)."""
    assert not hasattr(Dice(seed=0), "two_d6")


def test_chance_is_seeded_and_reproducible():
    r1 = [Dice(seed=42).chance(0.5) for _ in range(1)]
    r2 = [Dice(seed=42).chance(0.5) for _ in range(1)]
    assert r1 == r2


def test_chance_matches_its_probability():
    rng = Dice(seed=3)
    hits = sum(rng.chance(0.30) for _ in range(4000))
    assert 0.27 <= hits / 4000 <= 0.33


def test_chance_short_circuits_without_consuming_a_draw():
    """p<=0 / p>=1 must not touch the stream: a Speed-0 target's dodge check
    can't be allowed to shift the dice for everyone behind it (resolver relies
    on this for seed stability)."""
    a = Dice(seed=11)
    assert a.chance(0) is False
    assert a.chance(1) is True
    assert a.chance(-0.5) is False
    baseline = Dice(seed=11)
    assert a.roll("2d6") == baseline.roll("2d6")


def test_different_seeds_differ():
    results = {Dice(seed=i).roll("2d6") for i in range(20)}
    assert len(results) > 1  # not all the same


def test_roll_d8_range():
    rng = Dice(seed=7)
    for _ in range(200):
        r = rng.roll("d8")
        assert 1 <= r <= 8


def test_roll_2d6_range():
    rng = Dice(seed=9)
    for _ in range(200):
        r = rng.roll("2d6")
        assert 2 <= r <= 12


def test_roll_none_returns_zero():
    rng = Dice(seed=1)
    assert rng.roll("none") == 0
    assert rng.roll("0") == 0
    assert rng.roll("") == 0


def test_roll_invalid_spec_raises():
    rng = Dice(seed=1)
    with pytest.raises(ValueError, match="Invalid dice spec"):
        rng.roll("3")


def test_roll_seeded_sequence():
    rng1 = Dice(seed=42)
    results = [rng1.roll("d6") for _ in range(5)]
    rng2 = Dice(seed=42)
    expected = [rng2.roll("d6") for _ in range(5)]
    assert results == expected


def test_seed_property():
    rng = Dice(seed=123)
    assert rng.seed == 123


# ---------------------------------------------------------------------------
# Formula evaluator — the catalog's stat-parameterized dice (moves.yaml v4)
# ---------------------------------------------------------------------------


def _env(pow_=0, spd=0, wrd=0):
    return {"POW": pow_, "SPD": spd, "WRD": wrd}


def test_describe_formula_smash_scaling():
    spec = "(1 + ceil(POW/2))d4 + 2"
    assert describe_formula(spec, _env(pow_=0)) == "1d4+2"
    assert describe_formula(spec, _env(pow_=1)) == "2d4+2"
    assert describe_formula(spec, _env(pow_=5)) == "4d4+2"
    assert describe_formula(spec, _env(pow_=6)) == "4d4+2"


def test_describe_formula_stat_modifier():
    assert describe_formula("1d6 + WRD", _env(wrd=6)) == "1d6+6"
    assert describe_formula("1d6 + WRD", _env(wrd=0)) == "1d6"
    assert describe_formula("2d8 + floor(WRD/2)", _env(wrd=5)) == "2d8+2"
    assert describe_formula("1d6 + 2", _env()) == "1d6+2"


def test_roll_formula_bounds():
    rng = Dice(seed=3)
    for _ in range(200):
        v = rng.roll_formula("(1 + ceil(POW/2))d4 + 2", _env(pow_=6))   # 4d4+2
        assert 6 <= v <= 18
    for _ in range(200):
        v = rng.roll_formula("1d6 + WRD", _env(wrd=4))
        assert 5 <= v <= 10


def test_roll_formula_flat_expression():
    rng = Dice(seed=3)
    assert rng.roll_formula("5", _env()) == 5
    assert rng.roll_formula("POW + 1", _env(pow_=3)) == 4


def test_roll_formula_seeded_reproducible():
    a = [Dice(seed=11).roll_formula("2d8 + floor(WRD/2)", _env(wrd=3)) for _ in range(3)]
    b = [Dice(seed=11).roll_formula("2d8 + floor(WRD/2)", _env(wrd=3)) for _ in range(3)]
    assert a == b


def test_formula_rejects_disallowed_code():
    rng = Dice(seed=1)
    with pytest.raises(ValueError):
        rng.roll_formula("__import__('os')d6", _env())
    with pytest.raises(ValueError):
        rng.roll_formula("1d6 + unknown_name", _env())


# --- v5: avg() for CHARGE, plus multi-argument max()/min() generically ---


def test_describe_formula_avg_for_charge():
    """CHARGE keys off avg(POW,SPD) — integer floor average (GAME_DESIGN §4.1)."""
    spec = "2d4 + avg(POW,SPD)"
    assert describe_formula(spec, _env(pow_=6, spd=2)) == "2d4+4"   # (6+2)//2
    assert describe_formula(spec, _env(pow_=1, spd=5)) == "2d4+3"   # (1+5)//2
    assert describe_formula(spec, _env(pow_=3, spd=4)) == "2d4+3"   # (3+4)//2 floors
    assert describe_formula(spec, _env()) == "2d4"


def test_roll_formula_avg_bounds():
    rng = Dice(seed=3)
    for _ in range(200):
        v = rng.roll_formula("2d4 + avg(POW,SPD)", _env(pow_=6, spd=2))
        assert 6 <= v <= 12          # 2d4 + 4


def test_describe_formula_max_takes_the_better_stat():
    """The formula evaluator supports max() generically for future catalog
    formulas even though no shipped v5 move uses it."""
    spec = "2d4 + max(SPD,WRD)"
    assert describe_formula(spec, _env(spd=5, wrd=3)) == "2d4+5"
    assert describe_formula(spec, _env(spd=1, wrd=6)) == "2d4+6"
    assert describe_formula(spec, _env(spd=4, wrd=4)) == "2d4+4"
    assert describe_formula(spec, _env()) == "2d4"


def test_formula_min_and_nested_functions():
    assert describe_formula("1d6 + min(SPD,WRD)", _env(spd=5, wrd=2)) == "1d6+2"
    assert describe_formula("1d6 + max(POW, ceil(WRD/2))", _env(pow_=1, wrd=6)) == "1d6+3"


def test_roll_formula_with_max_bounds():
    rng = Dice(seed=3)
    for _ in range(200):
        v = rng.roll_formula("2d4 + max(SPD,WRD)", _env(spd=5, wrd=3))
        assert 7 <= v <= 13          # 2d4 + 5


# --- formula_parts: the split behind the host's §13 readout ---


def test_formula_parts_splits_dice_from_flat():
    assert formula_parts("2d4 + POW + 2", _env(pow_=6)) == (2, 4, 8)
    assert formula_parts("2d6 + 2*WRD + 2", _env(wrd=4)) == (2, 6, 10)
    assert formula_parts("4 + POW", _env(pow_=3)) == (0, 0, 7)   # flat, no dice


def test_formula_parts_recovers_the_dice_portion_of_a_roll():
    """The readout subtracts flat_mod from a rolled total to get "🎲 N"."""
    spec = "2d4 + POW + 2"
    env = _env(pow_=6)
    _, _, mod = formula_parts(spec, env)
    for seed in range(30):
        rolled = Dice(seed=seed).roll_formula(spec, env)
        assert 2 <= rolled - mod <= 8      # a real 2d4 result
