"""Tests for the seeded dice wrapper + the COMBAT V2 formula evaluator."""

import pytest

from server.engine.dice import Dice, describe_formula


def test_two_d6_range():
    rng = Dice(seed=0)
    for _ in range(200):
        r = rng.two_d6()
        assert 2 <= r <= 12


def test_two_d6_is_a_bell_curve_not_flat():
    """2d6 should produce 7 far more often than 2 — the point of the redesign."""
    rng = Dice(seed=1)
    rolls = [rng.two_d6() for _ in range(3000)]
    assert rolls.count(7) > 3 * rolls.count(2)


def test_seeded_reproducible():
    r1 = Dice(seed=42).two_d6()
    r2 = Dice(seed=42).two_d6()
    assert r1 == r2


def test_different_seeds_differ():
    results = {Dice(seed=i).two_d6() for i in range(20)}
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
# Formula evaluator — the catalog's stat-parameterized dice (moves.yaml v2)
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
