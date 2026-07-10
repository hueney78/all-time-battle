"""Match poster composition (Pillow) — GAME_DESIGN §10.2 / sync point S3."""

from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image

from server.ai.provider import MatchSummary
from server.engine.models import Character, GameState, Stats, Team
from server.poster import compose_poster

_FIXTURE = Path(__file__).parent / "fixtures" / "character.png"


def _char(pid: str, name: str, png: str, ko: bool = False) -> Character:
    return Character(player_id=pid, name=name, stats=Stats(power=2, speed=2, weird=2),
                     hp=0 if ko else 12, max_hp=22, ac=13, zone_id="frontline",
                     is_ko=ko, character_png_b64=png)


def test_compose_poster_writes_valid_png(tmp_path):
    """A real drawing renders; an invalid one falls back to a placeholder rather
    than crashing — the poster is always a valid PNG."""
    real = "data:image/png;base64," + base64.b64encode(_FIXTURE.read_bytes()).decode()
    a = _char("p1", "Princess Stabby", real)
    b = _char("p2", "The Blob", "doodle", ko=True)   # invalid png → placeholder
    teams = [Team(id="team_a", name="Glitter Crew", color="#ec4899", player_ids=["p1"]),
             Team(id="team_b", name="Thunder Squad", color="#3b82f6", player_ids=["p2"])]
    state = GameState(room_id="R", characters={"p1": a, "p2": b}, teams=teams,
                      winner_team_id="team_a")
    summary = MatchSummary(winner_team_id="team_a",
                           round_titles=["The Fish Learns to Surf", "Critical Chaos"],
                           best_line="Stabby fires the rainbow laser FROM INSIDE THE BLOB.")

    out = tmp_path / "poster.png"
    compose_poster(out, state, teams, summary, "#E8D5A8")

    assert out.exists()
    im = Image.open(out)
    im.verify()                                       # not truncated / corrupt
    assert im.format == "PNG" and im.size == (1000, 720)


def test_compose_poster_survives_empty_state(tmp_path):
    """No characters + a draw still yields a valid poster (never raises)."""
    out = tmp_path / "p.png"
    compose_poster(out, GameState(room_id="R", characters={}, teams=[]), [], MatchSummary())
    assert out.exists() and Image.open(out).format == "PNG"
