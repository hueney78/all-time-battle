"""Live AI smoke test — sends the fixture doodles to Claude and prints results.

    python scripts/ai_smoke.py

Requires .env with ANTHROPIC_API_KEY (AI_MODE is forced to live here). Exercises
all three call types against the real API and prints the parsed output + the
per-run cost estimate. Costs a few cents.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from server.ai.client import LiveAI  # noqa: E402
from server.ai.provider import ActionSubmission, CharacterSubmission  # noqa: E402
from server.config import load_game_rules  # noqa: E402
from server.engine.models import (  # noqa: E402
    Character,
    Event,
    EventType,
    GameState,
    Stats,
    Team,
)

FIX = ROOT / "tests" / "fixtures"


def _data_url(name: str) -> str:
    raw = (FIX / name).read_bytes()
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def main() -> int:
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY — copy .env.example to .env and add your key.")
        return 1

    rules = load_game_rules()
    ai = LiveAI(rules)
    character_png = _data_url("character.png")
    action_png = _data_url("action.png")

    print("== generate_characters ==")
    chars = ai.generate_characters(
        {"p1": CharacterSubmission("p1", character_png, hint="an angry pea")},
        rules.balance,
    )
    g = chars["p1"]
    print(f"  name={g.name!r} stats=P{g.stats.power}/S{g.stats.speed}/W{g.stats.weird} "
          f"flagged={g.flagged}\n  intro={g.announcer_intro!r}")

    # A tiny 1v1 state so classification has a legal target.
    p1 = Character(player_id="p1", name=g.name, stats=g.stats, hp=24, max_hp=24, ac=13,
                   zone_id="frontline", character_png_b64=character_png)
    p2 = Character(player_id="p2", name="The Foe", stats=Stats(power=3, speed=2, weird=3),
                   hp=24, max_hp=24, ac=13, zone_id="frontline")
    state = GameState(room_id="SMOKE", round=1, characters={"p1": p1, "p2": p2}, teams=[
        Team(id="team_a", name="A", color="#E24FA0", player_ids=["p1"]),
        Team(id="team_b", name="B", color="#2F6FE0", player_ids=["p2"]),
    ])

    print("== classify_actions (character + action image pair) ==")
    actions = ai.classify_actions(state, {"p1": ActionSubmission("p1", action_png)}, 1)
    for a in actions:
        if a.player_id == "p1":
            print(f"  catalog_id={a.catalog_id!r} cost={a.action_cost} targets={a.targets} "
                  f"creativity={a.creativity_tier} move_to={a.move_to}\n  note={a.adaptation_note!r}")

    print("== narrate_round ==")
    events = [
        Event(id="e1", type=EventType.ATTACK_RESOLVED, round=1, player_id="p1", target_id="p2",
              data={"result": "crit", "damage": 12, "catalog_id": "ray"}),
        Event(id="e2", type=EventType.KO, round=1, player_id="p2", data={}),
    ]
    narration = ai.narrate_round(events, state.characters)
    print(f"  round_title={narration.round_title!r}")
    for b in narration.beats:
        print(f"  [{b.event_id}] {b.text}")

    print(f"\n== degraded={ai.degraded}  est. cost ~${ai._cost:.4f} over {ai._calls} calls ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
