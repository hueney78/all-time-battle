"""Validate and repair AI responses into engine-ready models.

The Anthropic client forces tool-use, so responses already parse into the
`schemas.py` pydantic models. These functions do the *semantic* repair the
schema can't express:
  - normalize AI stats to the fixed stat budget (no drawing is stronger);
  - clamp creativity tiers; drop unknown TRICK/WILD conditions and combo
    partners who aren't living teammates;
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
    MatchSummary,
    MontageResult,
    Narration,
)
from server.config import Balance, GameRules
from server.engine.conditions import ConditionRegistry
from server.engine.hazards import HazardRegistry
from server.engine.models import (
    ClassifiedAction,
    GameState,
    Stats,
    WildInterpretation,
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


def build_generated_characters(
    resp: S.GenerateCharactersResponse,
    submissions: dict[str, CharacterSubmission],
    cfg: Balance,
) -> dict[str, GeneratedCharacter]:
    by_pid = {c.player_id: c for c in resp.characters}
    out: dict[str, GeneratedCharacter] = {}
    for pid in submissions:
        c = by_pid.get(pid)
        if c is None:
            out[pid] = _fallback_character(pid, cfg)
            continue
        out[pid] = GeneratedCharacter(
            name=(c.name or "").strip() or "Tim",
            stats=normalize_stats(c.stats.power, c.stats.speed, c.stats.weird, cfg),
            personality=c.personality,
            announcer_intro=c.announcer_intro,
            flagged=bool(c.flagged),
        )
    return out


# ---------------------------------------------------------------------------
# classify_actions
# ---------------------------------------------------------------------------
def build_classified_actions(
    resp: S.ClassifyActionsResponse,
    state: GameState,
    taps: dict[str, tuple[str, str | None]],
    rules: GameRules,
) -> list[ClassifiedAction]:
    """Merge the AI's drawing judgment onto the tapped moves.

    `taps` maps each living player to their (move_id, target_id) from the
    phone — the server owns both; the AI response may only decorate them.
    Combo partners must be living teammates; both partners carry each other so
    the resolver grants the roll bonus to each (no fusion in v2).
    """
    cond_reg = ConditionRegistry(rules.conditions)

    # combo → both partners carry the group (each gets the roll bonus).
    combo_of: dict[str, S.AIComboSpec] = {}
    for combo in resp.combos:
        parts = [p for p in combo.partners if p in taps]
        if len(parts) >= 2 and _same_team_all(parts, state):
            for p in parts:
                combo_of.setdefault(p, combo)

    by_pid = {a.player_id: a for a in resp.actions}
    out: list[ClassifiedAction] = []
    for pid, (move_id, target_id) in taps.items():
        a = by_pid.get(pid)
        move = rules.moves.moves.get(move_id)
        if a is None:
            # AI skipped this player → the tapped move still resolves at
            # creativity 0 (the fallback contract, §11.1).
            out.append(ClassifiedAction(player_id=pid, move_id=move_id,
                                        target_id=target_id))
            continue

        trick_condition = None
        if move is not None and move.on_hit_condition == "from_drawing":
            if a.trick_condition and a.trick_condition in cond_reg:
                trick_condition = a.trick_condition

        wild = None
        if move is not None and move.fumble_on_roll_lte is not None and a.wild_interpretation:
            w = a.wild_interpretation
            wild = WildInterpretation(
                condition=w.condition if (w.condition and w.condition in cond_reg) else None,
                description=w.description or "",
            )

        ca = ClassifiedAction(
            player_id=pid,
            move_id=move_id,
            target_id=target_id,
            creativity_tier=max(0, min(3, int(a.creativity_tier))),
            creativity_reason=a.creativity_reason or "",
            similar_to_previous=bool(a.similar_to_previous),
            flavor_summary=a.flavor_summary or "",
            trick_condition=trick_condition,
            wild_interpretation=wild,
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
# classify_gremlin
# ---------------------------------------------------------------------------
def build_gremlin_hazards(
    resp: S.ClassifyGremlinsResponse,
    gremlins: list[str],
    rules: GameRules,
) -> list[ClassifiedAction]:
    """Turn gremlin hazard classifications into resolver actions. An unknown
    hazard_id falls back to the palette's first entry (never rejected, like
    intent adaptation); a gremlin with no classification drops no hazard this round.
    The catalog_id carries the hazard id — the resolver's gremlin pass reads it
    against the hazard registry."""
    haz_reg = HazardRegistry(rules.hazards)
    ids = haz_reg.all_ids
    if not ids:
        return []
    default = ids[0]
    by_pid = {h.player_id: h for h in resp.hazards}
    out: list[ClassifiedAction] = []
    for pid in gremlins:
        h = by_pid.get(pid)
        if h is None:
            continue  # blank canvas → no hazard this round
        hazard_id = h.hazard_id if h.hazard_id in haz_reg else default
        out.append(ClassifiedAction(
            player_id=pid,
            move_id=hazard_id,
            adaptation_note=h.adaptation_note,
            flagged=bool(h.flagged),
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
