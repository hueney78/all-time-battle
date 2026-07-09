"""Shared pytest fixtures."""

from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
FIXTURES_DIR = TESTS_DIR / "fixtures"


@pytest.fixture(autouse=True)
def _force_offline_ai(monkeypatch):
    """Guarantee the whole suite is hermetic: never hit the live API even if a
    live .env is present. Tests that exercise LiveAI inject a fake client."""
    monkeypatch.setenv("AI_MODE", "mock")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
