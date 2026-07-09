"""Validate and repair AI responses into engine-ready models.

The Anthropic client forces tool-use, so responses already parse into the
`schemas.py` pydantic models. These functions do the *semantic* repair the
schema can't express:
  - normalize AI stats to the fixed stat budget (no drawing is stronger);
  - coerce an unknown catalog_id to `wildcard`; clamp cost to the move's range;
  - drop unknown conditions and dead/unknown targets (the resolver then adapts
    stale intents, GAME_DESIGN.md §9);
  - enforce move_to adjacency so a misread direction costs at most one zone;
  - carry flagged through; guarantee every living player yields an action and
    every game a narration.

Pure and offline — unit-tested without an API key.
"""

from __future__ import annotations

from server.ai import schemas as S
from server.ai.provider import Beat, CharacterSubmission, GeneratedCharacter, Narration
from server.config import Balance, GameRules
from server.engine.conditions import ConditionRegistry
from server.engine.models import ClassifiedAction, GameState, Stats
from server.engine.moves import MoveRegistry
from server.engine.zones import ZoneRegistry

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
    living: list[str],
    rules: GameRules,
) -> list[ClassifiedAction]:
    move_reg = MoveRegistry(rules.moves)
    zone_reg = ZoneRegistry(rules.zones)
    cond_reg = ConditionRegistry(rules.conditions)
    living_set = set(living)

    # combo → leader is the first living partner; only the leader carries the
    # combo fields (the resolver reads combo_partners off the leader's action).
    combo_leader: dict[str, S.AIComboSpec] = {}
    combo_members: set[str] = set()
    for combo in resp.combos:
        parts = [p for p in combo.partners if p in living_set]
        if len(parts) >= 2:
            combo_leader[parts[0]] = combo
            combo_members.update(parts)

    by_pid = {a.player_id: a for a in resp.actions}
    out: list[ClassifiedAction] = []
    for pid in living:
        a = by_pid.get(pid)
        if a is None:
            out.append(ClassifiedAction(player_id=pid, catalog_id="stumble", action_cost=1))
            continue

        catalog_id = a.catalog_id if a.catalog_id in move_reg else "wildcard"
        move = move_reg.get(catalog_id)
        cost = max(1, min(3, int(a.action_cost)))
        if move.min_cost:
            cost = min(3, max(cost, move.min_cost))

        move_to = None
        if a.move_to and a.move_to in zone_reg:
            cur = state.characters[pid].zone_id if pid in state.characters else None
            if a.move_to == cur or (cur and a.move_to in zone_reg.adjacent(cur)):
                move_to = a.move_to

        ca = ClassifiedAction(
            player_id=pid,
            catalog_id=catalog_id,
            action_cost=cost,
            targets=[t for t in a.targets if t in living_set],
            move_to=move_to,
            creativity_tier=max(0, min(3, int(a.creativity_tier))),
            creativity_reason=a.creativity_reason or "",
            similar_to_previous=bool(a.similar_to_previous),
            suggested_conditions=[c for c in a.suggested_conditions if c in cond_reg],
            adaptation_note=a.adaptation_note,
            flagged=bool(a.flagged),
        )
        combo = combo_leader.get(pid)
        if combo:
            ca.combo_partners = [p for p in combo.partners if p in living_set and p != pid]
            ca.combo_name = combo.combo_name
            ca.leading_catalog_id = (
                combo.leading_catalog_id if combo.leading_catalog_id in move_reg else catalog_id
            )
        out.append(ca)
    return out


# ---------------------------------------------------------------------------
# narrate_round
# ---------------------------------------------------------------------------
def build_narration(resp: S.NarrateResponse, valid_event_ids: set[str]) -> Narration:
    beats = [
        Beat(event_id=b.event_id, text=b.text, mood=b.mood)
        for b in resp.beats
        if b.event_id in valid_event_ids and b.text.strip()
    ]
    if not beats and resp.beats:
        # Salvage the text even if the model tagged an unknown event id.
        eid = next(iter(sorted(valid_event_ids)), resp.beats[0].event_id)
        beats = [Beat(event_id=eid, text=resp.beats[0].text or "Something happened.")]
    if not beats:
        beats = [Beat(event_id="filler", text="The crowd blinks. Something happened, probably.")]
    return Narration(beats=beats, round_title=resp.round_title or "")
