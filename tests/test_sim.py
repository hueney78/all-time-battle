"""Balance-sim CI guard.

The Monte-Carlo balance report (tests/sim/engine_sim.py) is a manual tuning tool,
but its *invariant checks* are a cheap regression guard for the real resolver:
a batch of random battles through `resolve_round` must never produce negative HP,
KO/HP mismatches, out-of-band stats, or leaked transforms — and games must still
end decisively with no move utterly broken. Run on a small N to stay fast.
"""

from __future__ import annotations

from tests.sim.engine_sim import CATALOG, run_attribution

_N = 12   # each battle re-resolves ~15 rounds through the real engine — keep small


def test_balance_sim_guards_engine_invariants_and_balance():
    """A batch of random battles through the REAL resolver must:
    - never violate the core engine invariants (any non-zero = a resolver bug),
    - terminate decisively (random play rarely draws), and
    - keep every move in a sane win-rate band (no move broken at ~0 or ~1)."""
    _, won, cnt, rep = run_attribution(_N, seed=7)

    assert rep["neg_hp"] == 0, "resolver produced negative HP"
    assert rep["ko_mismatch"] == 0, "KO'd character left with hp != 0"
    assert rep["stat_oob"] == 0, "a stat left the [stat_min, stat_max] band"
    assert rep["transform_leak"] == 0, "a transform never restored its original stats"

    assert rep["draws"] <= 2, f"too many draws in {_N} battles: {rep['draws']}"
    assert 3 <= rep["avg_rounds"] <= 40, f"implausible game length: {rep['avg_rounds']}"

    winrates = [won[mv] / cnt[mv] for mv in CATALOG if cnt[mv]]
    assert min(winrates) >= 0.10, f"a move is dead weight: {min(winrates):.2f}"
    assert max(winrates) <= 0.90, f"a move dominates: {max(winrates):.2f}"
