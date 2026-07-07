"""AI provider interface + an instant deterministic mock.

Phase 3 stubs the AI with `MockAI` so the whole game runs offline with no API
key. Phase 5 adds the real Anthropic-backed provider implementing the same
`AIProvider` protocol; the state machine only ever talks to this interface.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Protocol

from server.config import Balance
from server.engine.models import Character, ClassifiedAction, Event, GameState, Stats


# ---------------------------------------------------------------------------
# Data carried across the interface
# ---------------------------------------------------------------------------
@dataclass
class CharacterSubmission:
    player_id: str
    png_base64: str = ""
    hint: str = ""


@dataclass
class GeneratedCharacter:
    name: str
    stats: Stats
    personality: str = ""
    announcer_intro: str = ""


@dataclass
class ActionSubmission:
    player_id: str
    png_base64: str = ""


@dataclass
class Beat:
    event_id: str
    text: str


@dataclass
class Narration:
    beats: list[Beat] = field(default_factory=list)


class AIProvider(Protocol):
    def generate_characters(
        self, submissions: dict[str, CharacterSubmission], cfg: Balance
    ) -> dict[str, GeneratedCharacter]: ...

    def classify_actions(
        self, state: GameState, submissions: dict[str, ActionSubmission], round_num: int
    ) -> list[ClassifiedAction]: ...

    def narrate_round(
        self, events: list[Event], characters: dict[str, Character]
    ) -> Narration: ...


# ---------------------------------------------------------------------------
# Mock implementation — instant, deterministic, always valid
# ---------------------------------------------------------------------------
_MOCK_NAMES = [
    "Sir Reginald Fluffbottom", "The Blob", "Tim", "Captain Doodle",
    "Lord Scribblesworth", "Gerald the Adequate",
]
_MOCK_PERSONALITIES = [
    "boundlessly overconfident", "quietly menacing", "deeply confused",
    "aggressively cheerful", "world-weary", "unreasonably dramatic",
]


class MockAI:
    """Deterministic fixtures. Stats come from a seed derived from player_id so
    the same lobby always produces the same characters."""

    def generate_characters(
        self, submissions: dict[str, CharacterSubmission], cfg: Balance
    ) -> dict[str, GeneratedCharacter]:
        out: dict[str, GeneratedCharacter] = {}
        for idx, (pid, sub) in enumerate(sorted(submissions.items())):
            rng = random.Random(f"chargen:{pid}")
            stats = _budget_stats(rng, cfg)
            hint = (sub.hint or "").strip()
            name = hint.title()[:24] if hint else _MOCK_NAMES[idx % len(_MOCK_NAMES)]
            personality = _MOCK_PERSONALITIES[idx % len(_MOCK_PERSONALITIES)]
            out[pid] = GeneratedCharacter(
                name=name,
                stats=stats,
                personality=personality,
                announcer_intro=f"Introducing {name}, {personality}!",
            )
        return out

    def classify_actions(
        self, state: GameState, submissions: dict[str, ActionSubmission], round_num: int
    ) -> list[ClassifiedAction]:
        actions: list[ClassifiedAction] = []
        for pid, ch in state.characters.items():
            if ch.is_ko:
                continue
            sub = submissions.get(pid)
            png = (sub.png_base64 if sub else "").strip()
            enemy = _lowest_hp_enemy(pid, state)
            if not png or enemy is None:
                # Blank canvas (auto-submit / timeout) or no valid enemy → the
                # fighter hesitates dramatically (a 0-impact stumble).
                actions.append(ClassifiedAction(player_id=pid, catalog_id="stumble",
                                                action_cost=1))
                continue
            # `ray` is any-range so it works regardless of zones — keeps the mock
            # game progressing to a decisive result.
            actions.append(ClassifiedAction(
                player_id=pid, catalog_id="ray", action_cost=2, targets=[enemy],
                adaptation_note="a no-frills energy bolt",
            ))
        return actions

    def narrate_round(
        self, events: list[Event], characters: dict[str, Character]
    ) -> Narration:
        beats: list[Beat] = []
        for ev in events:
            text = _beat_text(ev, characters)
            if text:
                beats.append(Beat(event_id=ev.id, text=text))
        if not beats:
            beats.append(Beat(event_id="filler", text="The fighters circle warily."))
        return Narration(beats=beats)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _budget_stats(rng: random.Random, cfg: Balance) -> Stats:
    lo, hi, budget = cfg.stat_min, cfg.stat_max, cfg.stat_budget
    while True:
        p = rng.randint(lo, hi)
        s = rng.randint(lo, hi)
        w = budget - p - s
        if lo <= w <= hi:
            return Stats(power=p, speed=s, weird=w)


def _team_of(pid: str, state: GameState) -> str | None:
    for team in state.teams:
        if pid in team.player_ids:
            return team.id
    return None


def _lowest_hp_enemy(pid: str, state: GameState) -> str | None:
    my_team = _team_of(pid, state)
    enemies = [
        (ch.hp, eid) for eid, ch in state.characters.items()
        if not ch.is_ko and _team_of(eid, state) != my_team
    ]
    if not enemies:
        return None
    return min(enemies)[1]


def _name(pid: str | None, characters: dict[str, Character]) -> str:
    if pid and pid in characters:
        return characters[pid].name
    return "Someone"


def _beat_text(ev: Event, characters: dict[str, Character]) -> str:
    t = ev.type.value
    who = _name(ev.player_id, characters)
    whom = _name(ev.target_id, characters)
    d = ev.data
    if t == "attack_resolved":
        res = d.get("result")
        if res == "crit":
            return f"{who} lands a spectacular hit on {whom} for {d.get('damage', 0)}!"
        if res == "hit":
            return f"{who} tags {whom} for {d.get('damage', 0)}."
        if res == "miss":
            return f"{who} swings at {whom} and whiffs."
        if res == "fumble":
            return f"{who} fumbles catastrophically and hurts themselves."
        return ""
    if t == "ko":
        return f"{who} is knocked out and becomes an Arena Gremlin!"
    if t == "condition_applied":
        return f"{who} is now {d.get('condition', 'affected')}."
    if t == "healed":
        return f"{who} recovers {d.get('amount', 0)} HP."
    if t == "victory":
        return f"Team {d.get('winner_team_id', '?')} wins the brawl!"
    if t == "sudden_death":
        return "SUDDEN DEATH — the gloves are off!"
    return ""
