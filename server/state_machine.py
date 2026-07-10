"""Game phase state machine + pipeline orchestration (asyncio).

Phases: LOBBY → DRAW_CHARACTERS → ROUND_LOOP(draw ‖ process ‖ reveal) → GAME_OVER

The round loop runs the three-track prediction pipeline from ARCHITECTURE.md
§4.2 / GAME_DESIGN.md §2: on every tick players draw round *r+1* while the
AI+engine process round *r* and the TV reveals round *r−1*, all concurrently via
`asyncio.gather`. Two 1-deep buffers connect the stages (drawings awaiting
processing; a processed round awaiting reveal). Because the AI provider is a
blocking client, its calls run in `asyncio.to_thread` so a slow API never
freezes drawing or reveal — the pipeline keeps flowing.

State versioning: `self.state` is the last *revealed* state (what phones, the
arena, and reconnecting clients see), so players draw their intents without
peeking at results that haven't been shown yet. A separate `_resolve_state`
chains the engine's forward truth as rounds are processed ahead of the reveal.

Special cases (the pipeline's warm-up, GAME_DESIGN.md §2):
  T2 — character *generation* is the first process stage, running concurrently
       with Round 1 drawing; the TV shows warm-up filler.
  T3 — the character-intro reveal fills the Round 2 drawing gap.

Each drawing phase ends as soon as every living player submits, or when the
timer fires (missing canvases auto-submit blank → the classifier reads that as a
`stumble`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from server.ai.provider import (
    ActionSubmission,
    AIProvider,
    CharacterSubmission,
    MontageResult,
    Narration,
)
from server.config import GameRules
from server.engine.dice import Dice
from server.engine.models import Character, ClassifiedAction, Event, GameState, Phase
from server.engine.resolver import resolve_round
from server.protocol import S2C
from server.snapshots import SnapshotWriter

if TYPE_CHECKING:
    from server.room import Room

log = logging.getLogger("doodle.state_machine")


@dataclass
class Timers:
    draw_characters: float
    draw_action: float
    reveal: float
    montage: float = 20.0

    @classmethod
    def from_settings(cls, tc) -> Timers:
        return cls(
            draw_characters=float(tc.draw_characters_seconds),
            draw_action=float(tc.draw_action_seconds),
            reveal=float(tc.beat_seconds),
            montage=float(tc.montage_seconds),
        )


_SAFETY_ROUND_CAP = 60  # guards against a pathological no-victory loop


# ---------------------------------------------------------------------------
# Pipeline buffers — the two slots that connect draw → process → reveal.
# ---------------------------------------------------------------------------
class _CharGen:
    """Sentinel: the first process stage generates characters instead of
    resolving a round (GAME_DESIGN.md §2, T2)."""


@dataclass
class _Drawn:
    """Drawings collected during a draw stage, awaiting their process stage."""

    round_num: int
    action_pngs: dict[str, str]
    fighters: list[str]   # living fighters — drew a move this round
    gremlins: list[str]   # KO'd players — each drew one hazard this round


@dataclass
class _MontageDrawn:
    """Power-Up Montage additions collected in a montage sub-phase, awaiting
    classification in the next process window (GAME_DESIGN §10.1)."""

    round_num: int        # the round whose reveal triggered this montage
    montage_pngs: dict[str, str]
    survivors: list[str]


@dataclass
class _Processed:
    """A processed round (or the character intros / a montage), awaiting its
    reveal stage. Carries everything the reveal needs so it replays independently
    of how far the engine has since run ahead."""

    round_num: int
    narration: Narration | None
    events: list[Event]
    initiative_order: list[str]
    action_pngs: dict[str, str]
    post_state: GameState
    actions: list[ClassifiedAction] = field(default_factory=list)
    is_intro: bool = False
    is_montage: bool = False
    montage: list[MontageResult] = field(default_factory=list)
    montage_pngs: dict[str, str] = field(default_factory=dict)


class GameStateMachine:
    def __init__(
        self,
        room: Room,
        rules: GameRules,
        ai: AIProvider,
        timers: Timers | None = None,
        snapshots: SnapshotWriter | None = None,
    ):
        self.room = room
        self.rules = rules
        self.balance = rules.balance
        self.ai = ai
        # Condition names tagged as negative — used to flag "hurt" reveal beats.
        self._debuffs = {n for n, c in rules.conditions.conditions.items() if c.debuff}
        self.timers = timers or Timers.from_settings(rules.settings.timers)
        self.snapshots = snapshots or SnapshotWriter(
            rules.settings.snapshots.dir, room.code, rules.settings.snapshots.enabled
        )
        self.seed = room.seed

        # `state` is the last REVEALED state; `_resolve_state` is the engine's
        # forward truth (rounds processed ahead of what the TV has shown).
        self.state: GameState | None = None
        self._resolve_state: GameState | None = None
        self.task: asyncio.Task | None = None

        # phase/reveal bookkeeping (owned by the draw stage; also used by resync)
        self._phase: str = "lobby"
        self._round: int = 0
        self._deadline: float = 0.0

        # collection state (one drawing phase at a time)
        self._expected: set[str] = set()
        self._collected: set[str] = set()
        self._collect_done = asyncio.Event()
        # Live draw buffer for the drawing stage currently in flight. Processed
        # rounds keep their own snapshot, so this is only ever the round being
        # drawn right now (no cross-stage collision).
        self._action_pngs: dict[str, str] = {}
        # Separate live buffer for a Power-Up Montage sub-phase (never overlaps an
        # action draw — a player is in exactly one draw phase at a time).
        self._montage_pngs: dict[str, str] = {}
        self._beat_done = asyncio.Event()

        # Persistent battlefield sprites: a character's most-recently-revealed
        # action drawing becomes their sprite until their next action replaces it
        # (original character image until they first act). Server-owned so it
        # survives host refresh.
        self._latest_action_png: dict[str, str] = {}
        # Rolling per-team creativity totals feeding the "Crowd Favorite" meter,
        # recency-weighted by keeping only the last N rounds.
        self._audience: deque[dict[str, int]] = deque(
            maxlen=max(1, rules.settings.ui.audience_recent_rounds)
        )
        self._degraded_announced = False

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            await self._draw_characters()
            await self._round_loop()
        except Exception:  # pragma: no cover - defensive
            log.exception("game loop crashed in room %s", self.room.code)
        finally:
            await self._game_over()

    # -- inbound events (called from the connection dispatch) -------------
    def submit_drawing(self, player_id: str, msg) -> None:
        if self._phase == "draw_characters":
            p = self.room.participants.get(player_id)
            if p is not None:
                p.character_png = msg.png_base64
        elif self._phase == "montage":
            self._montage_pngs[player_id] = msg.png_base64
        else:
            # Any round-loop phase: the only drawings arriving are action
            # canvases for the round currently being drawn.
            self._action_pngs[player_id] = msg.png_base64
        self._note_submission(player_id)

    def advance_beat(self) -> None:
        self._beat_done.set()

    async def _check_degraded(self) -> None:
        """If the AI provider fell back to non-AI results, tell the host once so
        it can show the 'AI is napping — chaos mode' banner."""
        if getattr(self.ai, "degraded", False) and not self._degraded_announced:
            self._degraded_announced = True
            await self.room.broadcast(S2C.TOAST, {"message": "🤖 AI is napping — chaos mode"})

    def _note_submission(self, player_id: str) -> None:
        if player_id in self._expected:
            self._collected.add(player_id)
            if self._collected >= self._expected:
                self._collect_done.set()

    async def _collect(self, expected: list[str], timeout: float) -> None:
        self._expected = set(expected)
        self._collected = set()
        self._collect_done = asyncio.Event()
        if not self._expected:
            return
        try:
            await asyncio.wait_for(self._collect_done.wait(), timeout)
        except TimeoutError:
            pass  # missing players auto-submit (blank canvas → stumble)

    # -- character drawing (T1) -------------------------------------------
    async def _draw_characters(self) -> None:
        await self._enter_phase("draw_characters", round_num=0,
                                timeout=self.timers.draw_characters)
        await self._collect([p.id for p in self.room.players], self.timers.draw_characters)
        # Character *generation* is deferred to the first pipeline process stage
        # so it runs concurrently with Round 1 drawing (GAME_DESIGN.md §2, T2).

    # -- the prediction pipeline (round loop) -----------------------------
    async def _round_loop(self) -> None:
        # Buffers: `to_process` holds drawings awaiting processing (seeded with
        # the char-gen sentinel); `to_reveal` holds a processed round awaiting
        # its TV reveal. Both are 1-deep, so reveals emerge in strict round order.
        to_process: _CharGen | _Drawn | _MontageDrawn | None = _CharGen()
        to_reveal: _Processed | None = None
        round_num = 1
        montage_due: int | None = None   # a round whose reveal earned a montage
        cadence = self.rules.settings.game.montage_every_rounds

        while round_num <= _SAFETY_ROUND_CAP:
            revealed = to_reveal   # what this tick's reveal stage will play

            if montage_due is not None:
                # Montage sub-phase: survivors upgrade their character instead of
                # drawing an action, while process+reveal keep flowing (no stall).
                # The additions are classified next tick — piggybacked (§10.1).
                survivors = self._draw_roster()[0]
                montage_pngs, processed, _ = await asyncio.gather(
                    self._montage_draw_stage(montage_due, survivors),
                    self._process_stage(to_process),
                    self._reveal_stage(to_reveal),
                )
                to_reveal = processed
                to_process = _MontageDrawn(round_num=montage_due,
                                           montage_pngs=montage_pngs, survivors=survivors)
                montage_due = None
            else:
                fighters, gremlins = self._draw_roster()
                drawn_pngs, processed, _ = await asyncio.gather(
                    self._draw_stage(round_num, fighters + gremlins),
                    self._process_stage(to_process),
                    self._reveal_stage(to_reveal),
                )
                to_reveal = processed
                to_process = _Drawn(round_num=round_num, action_pngs=drawn_pngs,
                                    fighters=fighters, gremlins=gremlins)
                round_num += 1

            # A combat round just revealed on a montage cadence earns the next
            # tick a montage draw (GAME_DESIGN §10.1). Intros/montages don't count.
            if (cadence and revealed is not None and not revealed.is_intro
                    and not revealed.is_montage and revealed.round_num > 0
                    and revealed.round_num % cadence == 0):
                montage_due = revealed.round_num

            # A decided match: the buffered reveal still in flight plays out
            # (GAME_DESIGN.md §2), then the finale. Rounds drawn but not yet
            # processed are dropped — the outcome is already settled.
            if (
                processed is not None
                and processed.post_state is not None
                and processed.post_state.winner_team_id
            ):
                await self._reveal_stage(to_reveal)
                return

    def _draw_roster(self) -> tuple[list[str], list[str]]:
        """Who draws this round, from the last REVEALED state: living fighters
        (who draw a move) and Arena Gremlins (KO'd players who each draw one
        hazard — GAME_DESIGN §10). Gremlins are is_ko, so the sets are disjoint.
        Before characters exist, everyone in the lobby draws (their character)."""
        if self.state is None:
            return [p.id for p in self.room.players], []
        fighters = [pid for pid, ch in self.state.characters.items() if not ch.is_ko]
        gremlins = [pid for pid, ch in self.state.characters.items() if ch.is_gremlin]
        return fighters, gremlins

    async def _draw_stage(self, round_num: int, living: list[str]) -> dict[str, str]:
        self._action_pngs = {}
        await self._enter_phase("draw_action", round_num=round_num,
                                timeout=self.timers.draw_action)
        await self._send_canvas_inits()
        await self._broadcast_arena()
        await self._collect(living, self.timers.draw_action)
        return dict(self._action_pngs)

    async def _process_stage(
        self, to_process: _CharGen | _Drawn | _MontageDrawn | None
    ) -> _Processed | None:
        if to_process is None:
            return None
        if isinstance(to_process, _CharGen):
            return await self._process_characters()
        if isinstance(to_process, _MontageDrawn):
            return await self._process_montage(to_process)
        return await self._process_round(to_process)

    async def _montage_draw_stage(self, round_num: int, survivors: list[str]) -> dict[str, str]:
        """The Power-Up Montage bonus phase: survivors' canvases preload their
        current full-size character (the host renders it full-size from the
        `montage` phase) and they add an upgrade."""
        self._montage_pngs = {}
        await self._enter_phase("montage", round_num=round_num, timeout=self.timers.montage)
        await self._send_canvas_inits()   # current original at full size (client scales by phase)
        await self._collect(survivors, self.timers.montage)
        return dict(self._montage_pngs)

    async def _process_montage(self, md: _MontageDrawn) -> _Processed:
        """Classify each survivor's upgrade → +1 stat, and apply it to the
        engine-truth chain so later rounds fight with the boosted stats. The
        reveal (next tick) commits the same buffs to the revealed state."""
        assert self._resolve_state is not None
        subs = {pid: ActionSubmission(pid, md.montage_pngs.get(pid, "")) for pid in md.survivors}
        results = await asyncio.to_thread(
            self.ai.classify_montage, self._resolve_state, subs, md.round_num
        )
        self._apply_montage(self._resolve_state, results, md.montage_pngs)
        await self._check_degraded()
        return _Processed(
            round_num=md.round_num, narration=None, events=[], initiative_order=[],
            action_pngs={}, post_state=self._resolve_state,
            is_montage=True, montage=results, montage_pngs=md.montage_pngs,
        )

    def _apply_montage(
        self, state: GameState, results: list[MontageResult], montage_pngs: dict[str, str]
    ) -> None:
        """Apply +1 stat with formula deltas and swap in the upgraded drawing as
        the character's new 'original'. Idempotent per state object — call once
        on the engine-truth state and once on the revealed state."""
        for r in results:
            ch = state.characters.get(r.player_id)
            if ch is None or ch.is_ko:
                continue
            setattr(ch.stats, r.stat, getattr(ch.stats, r.stat) + 1)
            if r.stat == "power":
                gain = self.balance.hp_per_power
                ch.max_hp += gain
                ch.hp = min(ch.max_hp, ch.hp + gain)     # +max HP, healed by the gain
            elif r.stat == "speed":
                ch.ac = self.balance.ac_base + ch.stats.speed
            png = montage_pngs.get(r.player_id)
            if png:
                ch.character_png_b64 = png               # the new original everywhere

    async def _process_characters(self) -> _Processed:
        """Generate characters (T2 process) and build the initial game state.
        Returns the character-intro reveal to play one tick later (T3)."""
        subs = {
            p.id: CharacterSubmission(p.id, p.character_png, p.hint)
            for p in self.room.players
        }
        generated = await asyncio.to_thread(self.ai.generate_characters, subs, self.balance)

        chars: dict[str, Character] = {}
        for p in self.room.players:
            g = generated[p.id]
            hp = self.balance.hp_base + self.balance.hp_per_power * g.stats.power
            chars[p.id] = Character(
                player_id=p.id,
                name=g.name,
                stats=g.stats,
                personality=g.personality,
                announcer_intro=g.announcer_intro,
                hp=hp,
                max_hp=hp,
                ac=self.balance.ac_base + g.stats.speed,
                zone_id=self.room.zone_for_team(p.team_id or "team_a"),
                character_png_b64=p.character_png,
                flagged=getattr(g, "flagged", False),
            )
        self.state = GameState(
            room_id=self.room.code, phase=Phase.ROUND_LOOP, round=0,
            characters=chars, teams=self.room.teams,
        )
        self._resolve_state = self.state  # seed the engine-truth chain
        await self._send_all_player_states()
        await self._send_canvas_inits()
        await self._broadcast_arena()
        await self._check_degraded()

        # Preview the rail's acting order (initiative = Speed) for the intros.
        order = sorted(chars, key=lambda pid: -chars[pid].stats.speed)
        return _Processed(
            round_num=0, narration=None, events=[], initiative_order=order,
            action_pngs={}, post_state=self.state, actions=[], is_intro=True,
        )

    async def _process_round(self, drawn: _Drawn) -> _Processed:
        """Classify → resolve → narrate one round, advancing the engine-truth
        chain. AI calls run off the event loop so a slow API can't stall the
        concurrent draw/reveal stages."""
        assert self._resolve_state is not None
        state_for_round = self._resolve_state.model_copy(update={"round": drawn.round_num})

        fighter_subs = {pid: ActionSubmission(pid, drawn.action_pngs.get(pid, ""))
                        for pid in drawn.fighters}
        actions = await asyncio.to_thread(
            self.ai.classify_actions, state_for_round, fighter_subs, drawn.round_num
        )
        # Arena Gremlins are classified separately (drawings → hazard palette) and
        # merged in; the resolver's gremlin pass reads them off the same list.
        if drawn.gremlins:
            grem_subs = {pid: ActionSubmission(pid, drawn.action_pngs.get(pid, ""))
                         for pid in drawn.gremlins}
            grem_actions = await asyncio.to_thread(
                self.ai.classify_gremlin, state_for_round, grem_subs, drawn.round_num
            )
            actions = actions + grem_actions
        rng = Dice(seed=self.seed + drawn.round_num)
        result = resolve_round(state_for_round, actions, rng, self.balance)
        self._resolve_state = result.new_state
        await self._check_degraded()
        self.snapshots.write_round(drawn.round_num, result.new_state, result.events)
        self.snapshots.append_wildcards(drawn.round_num, actions)
        narration = await asyncio.to_thread(
            self.ai.narrate_round, result.events, result.new_state.characters
        )
        return _Processed(
            round_num=drawn.round_num, narration=narration, events=result.events,
            initiative_order=result.initiative_order, action_pngs=drawn.action_pngs,
            post_state=result.new_state, actions=actions,
        )

    async def _reveal_stage(self, processed: _Processed | None) -> None:
        if processed is None:
            return  # warm-up tick (T2): nothing buffered to reveal yet
        if processed.is_intro:
            await self._reveal_intros(processed)
            return
        if processed.is_montage:
            await self._reveal_montage(processed)
            return
        # This round's results become visible now: commit the revealed state and
        # bank its creativity for the meter, THEN play the beats (so the reveal
        # payload reflects exactly this round, not how far the engine has run on).
        self.state = processed.post_state
        self._accumulate_audience(processed.actions)
        await self._reveal(processed.round_num, processed.narration, processed.events,
                           processed.initiative_order, action_pngs=processed.action_pngs)
        # The revealed action drawings are now the persistent battlefield sprites.
        self._latest_action_png.update(processed.action_pngs)
        await self._send_all_player_states()
        await self._broadcast_arena()

    async def _reveal_intros(self, processed: _Processed) -> None:
        """Character-intro reveal (T3) — the announcer duo greets each fighter
        while players draw Round 2. Reuses the reveal_step shape (round 0)."""
        self._beat_done = asyncio.Event()
        chars = self.state.characters if self.state else {}
        beats = [
            {
                "event_id": f"intro-{pid}",
                "text": chars[pid].announcer_intro or f"Introducing {chars[pid].name}!",
                "speaker": "pbp",   # the play-by-play announcer hypes each fighter
                "player_id": pid, "target_id": None, "type": "intro",
                "hurt": None, "helped": None, "floats": [],
                "combo_name": None, "sfx": None, "result": None,
            }
            for pid in processed.initiative_order if pid in chars
        ]
        await self.room.broadcast(S2C.REVEAL_STEP, {
            "round": 0,
            "round_title": "Meet the Fighters",
            "beats": beats,
            "characters": self._character_deltas(),
            "action_pngs": {},
            "initiative_order": list(processed.initiative_order),
            "meters": self._meters(),
        })
        await self._pace_beats(len(beats))

    async def _reveal_montage(self, processed: _Processed) -> None:
        """Commit the montage buffs to the revealed state, make each upgraded
        drawing the new 'original' everywhere, and broadcast the stat pulses (S2)."""
        if self.state is None:
            return
        self._apply_montage(self.state, processed.montage, processed.montage_pngs)
        for r in processed.montage:
            # Reset the battlefield sprite so the upgraded original shows until
            # this character's next action replaces it.
            self._latest_action_png.pop(r.player_id, None)
        self._beat_done = asyncio.Event()
        upgrades = [
            {"player_id": r.player_id, "stat": r.stat, "flavor": r.flavor,
             "png": processed.montage_pngs.get(r.player_id, "")}
            for r in processed.montage
        ]
        await self.room.broadcast(S2C.MONTAGE, {
            "round": processed.round_num,
            "upgrades": upgrades,
            "characters": self._character_deltas(include_png=True),
        })
        # The upgraded original propagates everywhere: status cards, canvas
        # prefill, rail portraits, and battlefield sprites.
        await self._send_all_player_states()
        await self._send_canvas_inits()
        await self._broadcast_arena()
        await self._pace_beats(max(1, len(upgrades)))

    async def _reveal(self, round_num: int, narration, events, initiative_order=None,
                      action_pngs: dict[str, str] | None = None) -> None:
        self._beat_done = asyncio.Event()
        pngs = self._action_pngs if action_pngs is None else action_pngs
        # Attach each beat's acting/target player so the host can sprite-swap the
        # right character while the beat plays (ARCHITECTURE.md §4.5), plus the
        # impact + floating-number data the host renders (all from engine events,
        # never narration text).
        ev_by_id = {e.id: e for e in events}
        beats = []
        for b in narration.beats:
            ev = ev_by_id.get(b.event_id)
            beats.append({
                "event_id": b.event_id,
                "text": b.text,
                # Which announcer voices this beat — the host styles pbp vs color
                # chips differently (sync point S1).
                "speaker": b.speaker,
                "player_id": ev.player_id if ev else None,
                "target_id": ev.target_id if ev else None,
                "type": ev.type.value if ev else None,
                # Who this beat negatively impacts (red border + shake) vs
                # positively impacts (light-blue border + pop).
                "hurt": self._hurt_target(ev) if ev else None,
                "helped": self._helped_target(ev) if ev else None,
                # Floating combat numbers: [{player_id, amount, kind, crit}].
                "floats": self._floats(ev) if ev else [],
                # Fused-move name for the host's COMBO! splash (combo beats only).
                "combo_name": (ev.data.get("combo_name") or None) if ev else None,
                # The move's sound clip (moves.yaml sfx key) — the host's audio
                # manager plays it when the beat lands. Event stingers map
                # client-side from ui.audio.events_sfx using type/result.
                "sfx": self._beat_sfx(ev) if ev else None,
                # Attack outcome (hit/crit/miss/fumble) so the host can fire
                # the fumble stinger without parsing narration text.
                "result": (ev.data.get("result") or None) if ev else None,
            })
        await self.room.broadcast(S2C.REVEAL_STEP, {
            "round": round_num,
            "round_title": getattr(narration, "round_title", ""),
            "beats": beats,
            "characters": self._character_deltas(),
            "action_pngs": dict(pngs),
            # The rail's acting order + the two tug-of-war meter positions.
            "initiative_order": list(initiative_order or []),
            "meters": self._meters(),
        })
        await self._pace_beats(len(beats))

    async def _pace_beats(self, n_beats: int) -> None:
        """Host paces beats client-side and signals completion; fall back to a
        timeout so a missing/!clicking host never stalls the game."""
        timeout = max(0.05, self.timers.reveal * max(1, n_beats))
        try:
            await asyncio.wait_for(self._beat_done.wait(), timeout)
        except TimeoutError:
            pass

    def _beat_sfx(self, ev) -> str | None:
        """The sound clip for this beat's move (moves.yaml sfx key), or None
        when the event has no catalog move (KOs, victory, combos — those are
        covered by the client-side event stingers)."""
        move = self.rules.moves.moves.get(ev.data.get("catalog_id", ""))
        return (move.sfx or None) if move else None

    def _hurt_target(self, ev) -> str | None:
        """The player negatively impacted by an event (damaged, debuffed, KO'd),
        or None for neutral/positive events."""
        t = ev.type.value
        d = ev.data
        if t == "attack_resolved":
            if d.get("result") in ("hit", "crit"):
                return ev.target_id
            if d.get("result") == "fumble":
                return ev.player_id      # hurt themselves
        elif t == "condition_applied" and d.get("condition") in self._debuffs:
            return ev.player_id          # condition events use player_id as the affected char
        elif t in ("condition_ticked", "ko"):
            return ev.player_id
        return None

    def _helped_target(self, ev) -> str | None:
        """The player positively impacted by an event (healed, buffed, cleansed),
        or None for neutral/negative events. Drives the blue border + pop."""
        t = ev.type.value
        d = ev.data
        if t == "healed":
            return ev.target_id or ev.player_id
        if t == "condition_applied" and d.get("condition") not in self._debuffs:
            return ev.player_id       # a buff (pumped, shielded, …) lands on player_id
        if t == "condition_expired" and d.get("source") == "cleanse":
            return ev.player_id       # a debuff was washed off — that's a good thing
        return None

    def _floats(self, ev) -> list[dict]:
        """Floating combat numbers for this event: damage (red) / heal (green),
        crits flagged oversized. Amounts come from engine events so they always
        match the HP bars."""
        t = ev.type.value
        d = ev.data
        if t == "attack_resolved":
            res = d.get("result")
            if res in ("hit", "crit") and d.get("damage"):
                return [{"player_id": ev.target_id, "amount": d["damage"],
                         "kind": "damage", "crit": res == "crit"}]
            if res == "fumble" and d.get("self_damage"):
                return [{"player_id": ev.player_id, "amount": d["self_damage"],
                         "kind": "damage", "crit": False}]
        elif t == "condition_ticked" and d.get("damage"):
            return [{"player_id": ev.player_id, "amount": d["damage"],
                     "kind": "damage", "crit": False}]
        elif t == "healed" and d.get("amount"):
            return [{"player_id": ev.target_id or ev.player_id, "amount": d["amount"],
                     "kind": "heal", "crit": False}]
        return []

    # -- tug-of-war meters ------------------------------------------------
    def _accumulate_audience(self, actions) -> None:
        """Bank this round's per-team creativity so the Crowd Favorite meter can
        weight recent rounds (the deque's maxlen owns the recency window).
        Called at reveal time so the meter only reflects rounds the crowd has
        actually seen — never rounds the engine has processed ahead."""
        entry: dict[str, int] = {t.id: 0 for t in self.state.teams} if self.state else {}
        for a in actions:
            tid = self.room.team_of(a.player_id)
            if tid in entry:
                entry[tid] += max(0, a.creativity_tier)
        self._audience.append(entry)

    def _meters(self) -> dict:
        """Both knot positions as team_b's fraction in [0,1] — the knot is pulled
        toward whichever team leads (0.5 = tie). Computed server-side."""
        return {"hp_share": self._hp_share(), "audience": self._audience_share()}

    @staticmethod
    def _fraction_b(values: dict[str, float]) -> float:
        total = sum(values.values())
        if total <= 0:
            return 0.5
        return values.get("team_b", 0.0) / total

    def _hp_share(self) -> float:
        if self.state is None or not self.state.teams:
            return 0.5
        hp: dict[str, float] = {t.id: 0.0 for t in self.state.teams}
        for pid, ch in self.state.characters.items():
            tid = self.room.team_of(pid)
            if tid in hp:
                hp[tid] += max(0, ch.hp)
        return self._fraction_b(hp)

    def _audience_share(self) -> float:
        totals: dict[str, float] = {}
        for entry in self._audience:
            for tid, v in entry.items():
                totals[tid] = totals.get(tid, 0.0) + v
        return self._fraction_b(totals) if totals else 0.5

    async def _game_over(self) -> None:
        self._phase = "game_over"
        winner = self.state.winner_team_id if self.state else None
        await self.room.broadcast(S2C.GAME_OVER, {
            "winner_team_id": winner,
            "characters": self._character_deltas(),
        })

    # -- reconnection -----------------------------------------------------
    async def resync(self, player_id: str) -> None:
        await self.room.send(player_id, S2C.PHASE_CHANGE, {
            "phase": self._phase, "round": self._round, "deadline_ts": self._deadline,
        })
        if self.state is not None:
            await self._send_player_state(player_id)
            ch = self.state.characters.get(player_id)
            if ch is not None:
                await self.room.send(player_id, S2C.CANVAS_INIT, {"png": ch.character_png_b64})
            await self.room.send(player_id, S2C.ARENA_STATE, self._arena_payload())

    # -- outbound helpers -------------------------------------------------
    async def _enter_phase(self, phase: str, round_num: int, timeout: float) -> None:
        self._phase = phase
        self._round = round_num
        self._deadline = time.time() + timeout
        await self.room.broadcast(S2C.PHASE_CHANGE, {
            "phase": phase, "round": round_num, "deadline_ts": self._deadline,
        })

    async def _send_canvas_inits(self) -> None:
        for p in self.room.players:
            png = ""
            if self.state and p.id in self.state.characters:
                png = self.state.characters[p.id].character_png_b64
            png = png or p.character_png
            await self.room.send(p.id, S2C.CANVAS_INIT, {"png": png})

    async def _send_all_player_states(self) -> None:
        for p in self.room.players:
            await self._send_player_state(p.id)

    async def _send_player_state(self, player_id: str) -> None:
        if self.state is None:
            return
        ch = self.state.characters.get(player_id)
        if ch is None:
            return
        payload = _char_payload(ch, self.room.team_of(player_id))
        await self.room.send(player_id, S2C.PLAYER_STATE, payload)

    async def _broadcast_arena(self) -> None:
        await self.room.broadcast(S2C.ARENA_STATE, self._arena_payload())

    def _arena_payload(self) -> dict:
        return {
            "zones": [z.id for z in self.rules.zones.zones],
            "characters": self._character_deltas(include_png=True),
        }

    def _character_deltas(self, include_png: bool = False) -> list[dict]:
        if self.state is None:
            return []
        out = []
        for pid, ch in self.state.characters.items():
            payload = _char_payload(ch, self.room.team_of(pid))
            if include_png:
                # png = original portrait (rail); sprite_png = persistent action
                # drawing shown on the battlefield (falls back to the original).
                payload["png"] = ch.character_png_b64
                payload["sprite_png"] = (
                    self._latest_action_png.get(pid) or ch.character_png_b64
                )
            out.append(payload)
        return out


def _char_payload(ch: Character, team_id: str | None) -> dict:
    return {
        "player_id": ch.player_id,
        "name": ch.name,
        "hp": ch.hp,
        "max_hp": ch.max_hp,
        "ac": ch.ac,
        # Stats are the rail's / phone status card's home (💪 Power / ⚡ Speed / 🌀 Weird).
        "stats": {"power": ch.stats.power, "speed": ch.stats.speed, "weird": ch.stats.weird},
        "conditions": ch.conditions,
        "banked_actions": ch.banked_actions,
        "zone_id": ch.zone_id,
        "team_id": team_id,
        "is_ko": ch.is_ko,
        "is_gremlin": ch.is_gremlin,
    }
