"""Validate and repair AI responses into engine-ready models.

The Anthropic client forces tool-use, so responses already parse into the
`schemas.py` pydantic models. These functions do the *semantic* repair the
schema can't express:
  - normalize AI stats to the fixed stat budget (no drawing is stronger);
  - clamp creativity tiers; drop combo partners who aren't living teammates;
  - merge the AI's drawing judgment onto the TAPPED move + target (ground
    truth from the phone — the AI never chooses either, COMBAT V2 §11.1);
  - carry flagged through; guarantee every living player yields an action and
    every game a narration.

Pure and offline — unit-tested without an API key.
"""

from __future__ import annotations

from server.ai import schemas as S
from server.ai.provider import (
    Award,
    Beat,
    CharacterSubmission,
    GeneratedCharacter,
    GeneratedRoster,
    MatchSummary,
    MontageResult,
    Narration,
)
from server.config import Balance, GameRules
from server.engine.models import (
    ClassifiedAction,
    GameState,
    Stats,
)

_STATS = ("power", "speed", "weird")


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def normalize_stats(power: int, speed: int, weird: int, cfg: Balance) -> Stats:
    """Clamp each stat to [stat_min, stat_max] and force the total to
    stat_budget, so the AI chooses the distribution but config guarantees
    fairness (GAME_DESIGN.md §3)."""
    lo, hi, budget = cfg.stat_min, cfg.stat_max, cfg.stat_budget
    vals = {"power": power, "speed": speed, "weird": weird}
    for k in _STATS:
        vals[k] = max(lo, min(hi, int(vals[k])))

    def total() -> int:
        return sum(vals.values())

    guard = 0
    while total() < budget and guard < 100:
        k = max(_STATS, key=lambda k: (vals[k] < hi, vals[k], k))  # room, then highest
        if vals[k] < hi:
            vals[k] += 1
        guard += 1
    guard = 0
    while total() > budget and guard < 100:
        k = max(_STATS, key=lambda k: (vals[k] > lo, vals[k], k))  # room, then highest
        if vals[k] > lo:
            vals[k] -= 1
        guard += 1
    return Stats(power=vals["power"], speed=vals["speed"], weird=vals["weird"])


# ---------------------------------------------------------------------------
# generate_characters
# ---------------------------------------------------------------------------
def _fallback_character(pid: str, cfg: Balance) -> GeneratedCharacter:
    return GeneratedCharacter(
        name="Mystery Blob",
        stats=normalize_stats(3, 3, 2, cfg),
        personality="an enigma, even to itself",
        announcer_intro="Nobody knows what it is... but here it COMES!",
        flagged=False,
    )


# Character names are capped at TWO words — three only when the middle word is a
# short connector ("Gerald the Buff", "Duke of Spikes"). The announcers say these
# names constantly, so anything longer becomes a mouthful (GAME_DESIGN §3).
_NAME_WORD_CAP = 2
_NAME_CONNECTORS = {"of", "the", "de", "von", "van", "da", "der", "la", "le", "du"}


def cap_character_name(name: str) -> str:
    """Trim an AI name to the two-word cap (three if the middle word is a
    connector). Whitespace-collapsed; may return "" (the caller supplies the
    deadpan 'Tim' fallback)."""
    words = name.split()
    if len(words) <= _NAME_WORD_CAP:
        return " ".join(words)
    if len(words) == 3 and words[1].lower().strip(".,'\"") in _NAME_CONNECTORS:
        return " ".join(words)
    return " ".join(words[:_NAME_WORD_CAP])


# Team names must fit meters, zone bands, and phone headers (§3).
_TEAM_NAME_MAX = 28
_TEAM_FALLBACKS = {"team_a": "Team A", "team_b": "Team B"}


def build_team_names(resp: S.GenerateCharactersResponse) -> dict[str, str]:
    """The AI's per-team names, trimmed to fit labels; missing/blank names fall
    back to plain Team A/B (the pre-reveal display, §2)."""
    raw = {"team_a": resp.teams.team_a, "team_b": resp.teams.team_b} if resp.teams else {}
    return {
        tid: ((raw.get(tid) or "").strip()[:_TEAM_NAME_MAX] or fallback)
        for tid, fallback in _TEAM_FALLBACKS.items()
    }


def build_generated_characters(
    resp: S.GenerateCharactersResponse,
    submissions: dict[str, CharacterSubmission],
    cfg: Balance,
) -> GeneratedRoster:
    by_pid = {c.player_id: c for c in resp.characters}
    out: dict[str, GeneratedCharacter] = {}
    for pid in submissions:
        c = by_pid.get(pid)
        if c is None:
            out[pid] = _fallback_character(pid, cfg)
            continue
        out[pid] = GeneratedCharacter(
            name=cap_character_name((c.name or "").strip()) or "Tim",
            stats=normalize_stats(c.stats.power, c.stats.speed, c.stats.weird, cfg),
            personality=c.personality,
            announcer_intro=c.announcer_intro,
            flagged=bool(c.flagged),
        )
    return GeneratedRoster(characters=out, team_names=build_team_names(resp))


# ---------------------------------------------------------------------------
# classify_actions
# ---------------------------------------------------------------------------
def build_classified_actions(
    resp: S.ClassifyActionsResponse,
    state: GameState,
    taps: dict[str, tuple[str, str | None, int]],
    rules: GameRules,
) -> list[ClassifiedAction]:
    """Merge the AI's drawing judgment onto the tapped moves.

    `taps` maps each living player to their (move_id, target_id,
    escape_direction) from the phone — the server owns all three; the AI
    response may only decorate them. Combo partners must be living teammates;
    both partners carry each other so the resolver grants the tier bonus to each
    (no fusion in v5).
    """
    # combo → both partners carry the group (each gets the tier bonus).
    combo_of: dict[str, S.AIComboSpec] = {}
    for combo in resp.combos:
        parts = [p for p in combo.partners if p in taps]
        if len(parts) >= 2 and _same_team_all(parts, state):
            for p in parts:
                combo_of.setdefault(p, combo)

    by_pid = {a.player_id: a for a in resp.actions}
    out: list[ClassifiedAction] = []
    for pid, (move_id, target_id, escape_direction) in taps.items():
        a = by_pid.get(pid)
        if a is None:
            # AI skipped this player → the tapped move still resolves at
            # creativity 0 (the fallback contract, §11.1).
            out.append(ClassifiedAction(player_id=pid, move_id=move_id,
                                        target_id=target_id,
                                        escape_direction=escape_direction))
            continue

        ca = ClassifiedAction(
            player_id=pid,
            move_id=move_id,
            target_id=target_id,
            escape_direction=escape_direction,
            creativity_tier=max(0, min(3, int(a.creativity_tier))),
            creativity_reason=a.creativity_reason or "",
            similar_to_previous=bool(a.similar_to_previous),
            flavor_summary=a.flavor_summary or "",
            adaptation_note=a.adaptation_note,
            flagged=bool(a.flagged),
        )
        combo = combo_of.get(pid)
        if combo:
            ca.combo_partners = [p for p in combo.partners if p in taps and p != pid]
            ca.combo_name = combo.combo_name
        out.append(ca)
    return out


def _same_team_all(pids: list[str], state: GameState) -> bool:
    teams = [next((t.id for t in state.teams if p in t.player_ids), None) for p in pids]
    return len(set(teams)) == 1 and teams[0] is not None


# ---------------------------------------------------------------------------
# classify_gremlin — a KO'd player plants a trap in a TAPPED zone (GAME_DESIGN §10)
# ---------------------------------------------------------------------------
def build_gremlin_traps(
    resp: S.ClassifyGremlinsResponse,
    gremlin_taps: dict[str, str | None],
    rules: GameRules,
) -> list[ClassifiedAction]:
    """Turn gremlin trap drawings into resolver actions. The zone is ground
    truth from the phone (`gremlin_taps`, pid → zone); the AI supplies only the
    trap's creativity/flavor. A gremlin with no valid zone plants nothing."""
    zone_ids = {z.id for z in rules.zones.zones}
    by_pid = {t.player_id: t for t in resp.traps}
    out: list[ClassifiedAction] = []
    for pid, zone in gremlin_taps.items():
        if not zone or zone not in zone_ids:
            continue  # no valid zone tapped → no trap this round
        t = by_pid.get(pid)
        out.append(ClassifiedAction(
            player_id=pid,
            move_id="",
            trap_zone=zone,
            creativity_tier=max(0, min(3, int(t.creativity_tier))) if t else 0,
            similar_to_previous=bool(t.similar_to_previous) if t else False,
            flavor_summary=(t.flavor_summary if t else "") or "",
            adaptation_note=t.adaptation_note if t else None,
            flagged=bool(t.flagged) if t else False,
        ))
    return out


# ---------------------------------------------------------------------------
# classify_montage
# ---------------------------------------------------------------------------
_STAT_NAMES = ("power", "speed", "weird")


def build_montage(
    resp: S.ClassifyMontageResponse,
    survivors: list[str],
) -> list[MontageResult]:
    """One +1-stat grant per survivor who added to their character. An unknown
    stat defaults to `weird` (the catch-all); a survivor absent from the response
    (blank montage canvas) earns nothing (GAME_DESIGN §10.1)."""
    survivor_set = set(survivors)
    out: list[MontageResult] = []
    for m in resp.montages:
        if m.player_id not in survivor_set:
            continue
        stat = m.stat if m.stat in _STAT_NAMES else "weird"
        out.append(MontageResult(player_id=m.player_id, stat=stat, flavor=m.flavor))
    return out


# ---------------------------------------------------------------------------
# generate_awards
# ---------------------------------------------------------------------------
def build_awards(resp: S.GenerateAwardsResponse, summary: MatchSummary) -> list[Award]:
    """Keep the AI's awards (for real players only) and GUARANTEE every player
    gets at least one — the hard rule of the ceremony (GAME_DESIGN §10.2)."""
    by_id = {p["player_id"]: p for p in summary.players}
    awards = [
        Award(title=(a.title or "").strip() or "Participation Trophy",
              player_id=a.player_id, blurb=a.blurb)
        for a in resp.awards if a.player_id in by_id
    ]
    covered = {a.player_id for a in awards}
    for pid, p in by_id.items():
        if pid not in covered:
            awards.append(Award(
                title="Heart of a Doodle",
                player_id=pid,
                blurb=f"{p.get('name', 'Someone')} showed up and threw down.",
            ))
    return awards


# ---------------------------------------------------------------------------
# narrate_round
# ---------------------------------------------------------------------------
_SPEAKERS = ("pbp", "color")


def _speaker(value: str) -> str:
    """Clamp to a known announcer voice; anything else defaults to play-by-play."""
    return value if value in _SPEAKERS else "pbp"


def clamp_announcer_text(text: str, max_chars: int) -> str:
    """Hard-cap an announcer line to `max_chars` (GAME_DESIGN §11.2).

    `max_chars <= 0` means no limit. An over-long line is cut on a word boundary
    (never mid-word) and given a single-character ellipsis, so the booth never
    runs long on screen no matter what the model returns. Pure/offline — the cap
    is also sent to the narrate prompt as a soft target, but this is the guarantee.
    """
    text = (text or "").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = text[:max_chars].rstrip()
    # Back off to the last whole word, but only if that doesn't gut the line.
    space = cut.rfind(" ")
    if space >= max_chars * 0.6:
        cut = cut[:space].rstrip()
    return cut.rstrip(",;:—- ") + "…"


def build_narration(resp: S.NarrateResponse, valid_event_ids: set[str]) -> Narration:
    beats = [
        Beat(event_id=b.event_id, text=b.text, mood=b.mood, speaker=_speaker(b.speaker))
        for b in resp.beats
        if b.event_id in valid_event_ids and b.text.strip()
    ]
    if not beats and resp.beats:
        # Salvage the text even if the model tagged an unknown event id.
        eid = next(iter(sorted(valid_event_ids)), resp.beats[0].event_id)
        beats = [Beat(event_id=eid, text=resp.beats[0].text or "Something happened.",
                      speaker=_speaker(resp.beats[0].speaker))]
    if not beats:
        beats = [Beat(event_id="filler", text="The crowd blinks. Something happened, probably.")]
    return Narration(beats=beats, round_title=resp.round_title or "")
