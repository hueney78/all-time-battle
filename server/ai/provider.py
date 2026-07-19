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
from server.engine.models import (
    Character,
    ClassifiedAction,
    Event,
    GameState,
    Stats,
)

log = logging.getLogger("doodle.ai")


# ---------------------------------------------------------------------------
# Data carried across the interface
# ---------------------------------------------------------------------------
@dataclass
class CharacterSubmission:
    player_id: str
    png_base64: str = ""
    hint: str = ""
    team_id: str = ""     # lobby team — the AI names each team from its roster


@dataclass
class GeneratedCharacter:
    name: str
    stats: Stats
    personality: str = ""
    announcer_intro: str = ""
    flagged: bool = False


@dataclass
class GeneratedRoster:
    """The full generate_characters result: one character per player plus an
    AI-invented name per team (revealed as the final intro beat, §2)."""

    characters: dict[str, GeneratedCharacter]
    team_names: dict[str, str] = field(default_factory=dict)  # team_id → name


@dataclass
class ActionSubmission:
    player_id: str
    png_base64: str = ""
    # COMBAT V5: the tapped move + target from the phone (ground truth — the AI
    # judges the drawing, never the move). ESCAPE carries a ◀/▶ direction; a
    # gremlin carries the trap_zone it tapped. Empty for montage drawings.
    move_id: str = ""
    target_id: str | None = None
    escape_direction: int = 0
    trap_zone: str | None = None


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
    reflects: dict[str, int] = field(default_factory=dict)     # pid → shield-reflect count
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
    ) -> GeneratedRoster: ...

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
        zone_names: dict[str, str] | None = None,
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
    "Most Creative Doodle", "Backfire of the Match", "Best Combo Name",
    "Crowd Favorite", "Bravest Use of a Household Object", "Heart of a Champion",
]


_MOCK_TEAM_NAMES = {"team_a": "The Doodle Dynamos", "team_b": "The Scribble Squad"}


class MockAI:
    """Deterministic fixtures. Stats come from a seed derived from player_id so
    the same lobby always produces the same characters."""

    def generate_characters(
        self, submissions: dict[str, CharacterSubmission], cfg: Balance
    ) -> GeneratedRoster:
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
        return GeneratedRoster(characters=out, team_names=dict(_MOCK_TEAM_NAMES))

    def classify_actions(
        self, state: GameState, submissions: dict[str, ActionSubmission], round_num: int
    ) -> list[ClassifiedAction]:
        actions: list[ClassifiedAction] = []
        for pid, ch in state.characters.items():
            if ch.is_ko:
                continue
            sub = submissions.get(pid)
            enemy = _lowest_hp_enemy(pid, state)
            if enemy is None:
                continue
            # The tapped move/target are ground truth when present; headless
            # mock games (no phone taps) rotate three any-zone attacks so the
            # no-repeat rule holds and the game reaches a decisive result.
            move_id = (sub.move_id if sub else "") or \
                ["blast", "charge", "escape"][round_num % 3]
            target_id = (sub.target_id if sub else None) or enemy
            escape_direction = sub.escape_direction if sub else 0
            png = (sub.png_base64 if sub else "").strip()
            # A blank canvas (auto-submit) still resolves the tapped move — at
            # creativity 0, narrated as maximum-confidence minimum-effort (§9).
            # Seeded by round as well as player so tiers VARY across the match and
            # span the full 0–3: creativity is the drawing's entire mechanical
            # contribution, and tier 3 is the DEVASTATING beat that drives the
            # replay/stinger/gold-log presentation.
            creativity = (
                random.Random(f"crea:{pid}:{round_num}").randint(0, 3) if png else 0
            )
            actions.append(ClassifiedAction(
                player_id=pid, move_id=move_id, target_id=target_id,
                escape_direction=escape_direction,
                creativity_tier=creativity,
                flavor_summary="a no-frills energy bolt",
            ))
        return actions

    def classify_gremlin(
        self, state: GameState, submissions: dict[str, ActionSubmission], round_num: int
    ) -> list[ClassifiedAction]:
        """A gremlin plants a trap in the zone it tapped (§10). Headless mock
        games (no tap) trap a living enemy's zone so it actually springs. A blank
        canvas plants nothing that round."""
        out: list[ClassifiedAction] = []
        for pid, sub in submissions.items():
            png = (sub.png_base64 if sub else "").strip()
            if not png:
                continue
            zone = sub.trap_zone if sub else None
            if not zone:
                enemy = _lowest_hp_enemy(pid, state)
                zone = state.characters[enemy].zone_id if enemy else None
            if not zone:
                continue
            creativity = random.Random(f"grem:{pid}:{round_num}").randint(0, 3)
            out.append(ClassifiedAction(
                player_id=pid, move_id="", trap_zone=zone,
                creativity_tier=creativity,
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
        zone_names: dict[str, str] | None = None,
    ) -> Narration:
        beats: list[Beat] = []
        for ev in events:
            text = _beat_text(ev, characters, zone_names)
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
    if "devastating" in results:
        return "Absolutely Devastating"
    if "trap_triggered" in kinds:
        return "Sprung!"
    if "reflect" in results:
        return "Right Back At You"
    return "The Doodles Circle"


def _mock_speaker(ev: Event) -> str:
    """Split beats between the two announcers so both voices show up every round:
    the deadpan color commentator handles reflects, traps, and dry asides; the
    play-by-play announcer calls the big swings."""
    t = ev.type.value
    if t == "attack_resolved" and ev.data.get("result") == "reflect":
        return "color"
    if t in ("trap_placed", "trap_triggered", "stumble"):
        return "color"
    return "pbp"


def _name(pid: str | None, characters: dict[str, Character]) -> str:
    if pid and pid in characters:
        return characters[pid].name
    return "Someone"


def _beat_text(ev: Event, characters: dict[str, Character],
               zone_names: dict[str, str] | None = None) -> str:
    t = ev.type.value
    who = _name(ev.player_id, characters)
    whom = _name(ev.target_id, characters)
    d = ev.data
    zn = zone_names or {}
    if t == "attack_resolved":
        res = d.get("result")
        if res == "devastating":
            return f"{who} lands an absolutely devastating blow on {whom} for {d.get('damage', 0)}!"
        if res == "hit":
            return f"{who} tags {whom} for {d.get('damage', 0)}."
        if res == "reflect":
            return f"{who}'s shield throws it right back at {whom} for {d.get('damage', 0)}!"
        if res == "whiff":
            # ESCAPE got away clean, but its parting shot found nobody in the
            # zone it fled — the target was somewhere else.
            return (f"{who} slips away and snaps off a parting shot — "
                    f"but {whom} is too far, and it hits nothing.")
        if res == "no_target":
            return f"{who} swings at empty air."
        return ""
    if t == "trap_triggered":
        zone = zn.get(d.get("zone"), d.get("zone", "the arena"))
        return f"{whom} stumbles into {who}'s trap in {zone} for {d.get('damage', 0)}!"
    if t == "trap_placed":
        zone = zn.get(d.get("zone"), d.get("zone", "the arena"))
        return f"{who} the Gremlin plants a nasty little trap in {zone}…"
    if t == "ko":
        return f"{who} is knocked out and becomes an Arena Gremlin!"
    if t == "protected":
        # PROTECT is ONE beat: heal AND shield together (§11.2), never two lines.
        return (f"{who} patches {whom} up for {d.get('amount', 0)} HP and wraps them "
                f"in a shimmering, blow-flinging shield!")
    if t == "healed":
        return f"{who} patches {whom} up for {d.get('amount', 0)} HP."
    if t == "victory":
        return f"Team {d.get('winner_team_id', '?')} wins the brawl!"
    if t == "sudden_death":
        return "SUDDEN DEATH — the gloves are off!"
    return ""
