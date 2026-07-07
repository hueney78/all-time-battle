"""Tests for the seeded dice wrapper."""

import pytest

from server.engine.dice import Dice


def test_d20_range():
    rng = Dice(seed=0)
    for _ in range(100):
        r = rng.d20()
        assert 1 <= r <= 20


def test_seeded_reproducible():
    r1 = Dice(seed=42).d20()
    r2 = Dice(seed=42).d20()
    assert r1 == r2


def test_different_seeds_differ():
    results = {Dice(seed=i).d20() for i in range(20)}
    assert len(results) > 1  # not all the same


def test_roll_d8_range():
    rng = Dice(seed=7)
    for _ in range(200):
        r = rng.roll("d8")
        assert 1 <= r <= 8


def test_roll_d6_range():
    rng = Dice(seed=3)
    for _ in range(200):
        r = rng.roll("d6")
        assert 1 <= r <= 6


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
