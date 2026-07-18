"""The Doodle Crowd stands (§15/S4): the host renders a rotating handful of past
characters as tiny spectators. The behavior lives in web/host/arena.js, so it's
exercised by a Node harness (tests/js/arena_stands.test.js). Skipped when node
isn't installed so pytest stays green everywhere; runs for real where node is.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tests" / "js" / "arena_stands.test.js"
REVEAL_SCRIPT = REPO / "tests" / "js" / "arena_reveal.test.js"


def _run_node(script: Path):
    return subprocess.run(
        ["node", str(script)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_arena_stands_render_and_rotate():
    result = _run_node(SCRIPT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK arena stands" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_arena_reveal_badge_shield_and_move():
    """v6 §13: move-name badge, PROTECT glow, and CHARGE/ESCAPE sprite travel."""
    result = _run_node(REVEAL_SCRIPT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK arena reveal" in result.stdout


INTROS_SCRIPT = REPO / "tests" / "js" / "host_intros.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_host_intros_reveal_runs_and_signals_next_beat():
    """Boot the real host script + arena.js and drive the intros reveal_step:
    it must run without throwing and signal next_beat, or the live game stalls
    on the battlefield (regression guard for the v6 arena.js/index.html sync)."""
    result = _run_node(INTROS_SCRIPT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK host intros" in result.stdout
