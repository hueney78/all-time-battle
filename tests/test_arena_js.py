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


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_arena_stands_render_and_rotate():
    result = subprocess.run(
        ["node", str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK arena stands" in result.stdout
