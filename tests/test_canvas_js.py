"""Action-canvas prefill regression (playtest bug): the character preload must
apply action_canvas_character_scale immediately on first load — not full size —
and Restore must re-apply that same scaled state.

The behavior lives in web/player/canvas.js, so it's exercised by a Node harness
(tests/js/canvas_prefill.test.js). Skipped when node isn't installed so pytest
stays green everywhere; runs for real wherever node is present.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tests" / "js" / "canvas_prefill.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_canvas_prefill_scale_applies_on_first_load():
    result = subprocess.run(
        ["node", str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK canvas prefill" in result.stdout
