"""Announcer Text-to-Speech (GAME_DESIGN §13): the host reads each revealed
announcer beat aloud, with a DIFFERENT configurable voice per announcer. The
behavior lives in web/host/tts.js, exercised by a Node harness
(tests/js/tts.test.js). Skipped when node isn't installed so pytest stays green
everywhere; runs for real where node is present.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tests" / "js" / "tts.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_tts_gives_each_announcer_a_distinct_voice():
    result = subprocess.run(
        ["node", str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK host tts" in result.stdout
