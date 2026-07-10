"""AI provider interface + an instant deterministic mock.

Phase 3 stubs the AI with `MockAI` so the whole game runs offline with no API
key. Phase 5 adds the real Anthropic-backed provider implementing the same
`AIProvider` protocol; the state machine only ever talks to this interface.
"""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field
from typing import Protocol

from server.config import Balance, GameRules
from server.engine.hazards import HazardRegistry
from server.engine.models import Character, ClassifiedAction, Event, GameState, Stats

log = logging.getLogger("doodle.ai")


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
    flagged: bool = False


@dataclass
class ActionSubmission:
    player_id: str
    png_base64: str = ""


@dataclass
class MontageResult:
    player_id: str
    stat: str            # "power" | "speed" | "weird"
    flavor: str = ""


@dataclass
class MatchSummary:
    """Everything the awards ceremony needs about a finished match (§10.2)."""

    winner_team_id: str | None = None
    # {player_id, name, team_id, alive}
    players: list[dict] = field(default_factory=list)
    creativity: dict[str, int] = field(default_factory=dict)   # pid → total tiers
    fumbles: dict[str, int] = field(default_factory=dict)      # pid → fumble count
    combos: list[dict] = field(default_factory=list)           # {combo_name, partners}
    round_titles: list[str] = field(default_factory=list)
    best_line: str = ""


@dataclass
class Award:
    title: str
    player_id: str
    blurb: str = ""


@dataclass
class Beat:
    event_id: str
    text: str
    mood: str = "comedy"
    # Which announcer voices this beat: "pbp" (hyper play-by-play) or "color"
    # (deadpan color commentator). The host styles the two differently (S1).
    speaker: str = "pbp"


@dataclass
class Narration:
    beats: list[Beat] = field(default_factory=list)
    round_title: str = ""


def make_ai(rules: GameRules) -> AIProvider:
    """Pick the provider from AI_MODE: live Claude when AI_MODE=live and a key is
    present, otherwise the offline mock. Any live-init failure degrades to mock so
    the game still runs."""
    mode = os.environ.get("AI_MODE", "mock").strip().lower()
    if mode == "live" and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from server.ai.client import LiveAI

            return LiveAI(rules)
        except Exception:
            log.exception("live AI init failed; falling back to mock")
    return MockAI()


class AIProvider(Protocol):
    def generate_characters(
        self, submissions: dict[str, CharacterSubmission], cfg: Balance
    ) -> dict[str, GeneratedCharacter]: ...

    def classify_actions(
        self, state: GameState, submissions: dict[str, ActionSubmission], round_num: int
    ) -> list[ClassifiedAction]: ...

    def classify_gremlin(
        self, state: GameState, submissions: dict[str, ActionSubmission], round_num: int
    ) -> list[ClassifiedAction]: ...

    def classify_montage(
        self, state: GameState, submissions: dict[str, ActionSubmission], round_num: int
    ) -> list[MontageResult]: ...

    def narrate_round(
        self, events: list[Event], characters: dict[str, Character],
        gallery_names: list[str] | None = None,
    ) -> Narration: ...

    def generate_awards(self, summary: MatchSummary) -> list[Award]: ...


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
# Affectionate superlatives (GAME_DESIGN §10.2) — celebrate the comedy, never mock.
_AWARD_TITLES = [
    "Most Creative Doodle", "Fumble of the Match", "Best Combo Name",
    "Crowd Favorite", "Bravest Use of a Household Object", "Heart of a Champion",
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
            # game progressing to a decisive result. A small deterministic
            # creativity tier (stable per player) gives the audience meter
            # something to move on in offline/mock play.
            creativity = random.Random(f"crea:{pid}").randint(0, 2)
            actions.append(ClassifiedAction(
                player_id=pid, catalog_id="ray", action_cost=2, targets=[enemy],
                creativity_tier=creativity,
                adaptation_note="a no-frills energy bolt",
            ))
        return actions

    def classify_gremlin(
        self, state: GameState, submissions: dict[str, ActionSubmission], round_num: int
    ) -> list[ClassifiedAction]:
        """Map each gremlin's doodle to a deterministic hazard from the palette.
        A blank canvas drops no hazard that round."""
        haz_ids = HazardRegistry().all_ids
        out: list[ClassifiedAction] = []
        for pid, sub in submissions.items():
            png = (sub.png_base64 if sub else "").strip()
            if not png or not haz_ids:
                continue
            hid = haz_ids[random.Random(f"grem:{pid}").randrange(len(haz_ids))]
            out.append(ClassifiedAction(
                player_id=pid, catalog_id=hid, action_cost=1,
                adaptation_note="a menacing little doodle",
            ))
        return out

    def classify_montage(
        self, state: GameState, submissions: dict[str, ActionSubmission], round_num: int
    ) -> list[MontageResult]:
        """Grant +1 to a deterministic stat for each non-blank montage addition;
        a blank canvas earns nothing (GAME_DESIGN §10.1)."""
        stats = ("power", "speed", "weird")
        out: list[MontageResult] = []
        for pid, sub in submissions.items():
            png = (sub.png_base64 if sub else "").strip()
            if not png:
                continue
            stat = stats[random.Random(f"montage:{pid}:{round_num}").randrange(3)]
            out.append(MontageResult(player_id=pid, stat=stat,
                                     flavor="bristling with fresh upgrades"))
        return out

    def narrate_round(
        self, events: list[Event], characters: dict[str, Character],
        gallery_names: list[str] | None = None,
    ) -> Narration:
        beats: list[Beat] = []
        for ev in events:
            text = _beat_text(ev, characters)
            if text:
                beats.append(Beat(event_id=ev.id, text=text, speaker=_mock_speaker(ev)))
        if not beats:
            beats.append(Beat(event_id="filler", text="The fighters circle warily.",
                              speaker="color"))
        return Narration(beats=beats, round_title=_mock_round_title(events))

    def generate_awards(self, summary: MatchSummary) -> list[Award]:
        """One affectionate superlative per player (every player gets one),
        picked deterministically from a rotating palette (GAME_DESIGN §10.2)."""
        out: list[Award] = []
        for i, p in enumerate(summary.players):
            title = _AWARD_TITLES[i % len(_AWARD_TITLES)]
            out.append(Award(title=title, player_id=p["player_id"],
                             blurb=f"{p.get('name', 'Someone')} — a certified doodle legend."))
        return out


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


def _mock_round_title(events: list[Event]) -> str:
    kinds = {e.type.value for e in events}
    results = {e.data.get("result") for e in events if e.type.value == "attack_resolved"}
    if "ko" in kinds:
        return "Someone Hits the Sand"
    if "crit" in results:
        return "Critical Chaos"
    if "fumble" in results:
        return "A Comedy of Errors"
    return "The Doodles Circle"


def _mock_speaker(ev: Event) -> str:
    """Split beats between the two announcers so both voices show up every round:
    the deadpan color commentator handles the whiffs, fizzles, and dry asides;
    the play-by-play announcer calls the big swings."""
    t = ev.type.value
    if t == "attack_resolved" and ev.data.get("result") in ("miss", "fumble"):
        return "color"
    if t in ("condition_applied", "condition_ticked", "banked", "gremlin_hazard", "stumble"):
        return "color"
    return "pbp"


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
    if t == "gremlin_hazard":
        hz = str(d.get("hazard_id", "something")).replace("_", " ")
        return f"{who} the Gremlin drops {hz} on {d.get('zone', 'the arena')}!"
    if t == "condition_applied":
        return f"{who} is now {d.get('condition', 'affected')}."
    if t == "healed":
        return f"{who} recovers {d.get('amount', 0)} HP."
    if t == "victory":
        return f"Team {d.get('winner_team_id', '?')} wins the brawl!"
    if t == "sudden_death":
        return "SUDDEN DEATH — the gloves are off!"
    return ""
