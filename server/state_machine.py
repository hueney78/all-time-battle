"""Game phase state machine + round orchestration (asyncio).

Phases: LOBBY → DRAW_CHARACTERS → ROUND_LOOP(draw → deliberate → reveal) → GAME_OVER

The round loop is strictly SEQUENTIAL (ARCHITECTURE.md §4.2 / GAME_DESIGN.md §2):
each round is drawn, then the deliberation interlude shows every submitted drawing
side by side while the round is classified/resolved/narrated, then the reveal
plays before the next draw — players see their move immediately after drawing it.
The interlude, not concurrency, is the latency mask; AI calls run in
`asyncio.to_thread` so a slow API keeps the interlude live (never a spinner), and
the 20s timeout + fallback bounds the worst case. A single live `self.state` is
updated as each round is revealed.

Character intros play BEFORE Round 1 drawing (v2.1, GAME_DESIGN §2): players
meet the fighters, stats, and team names first, then draw their opening moves
with full knowledge. Character generation is one fast call masked by a "meet
the fighters" drumroll interstitial.

Each drawing phase ends as soon as every living player submits (processing starts
that instant), or when the timer fires (missing canvases auto-submit blank → the
classifier reads that as a `stumble`).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from server.ai.provider import (
    ActionSubmission,
    AIProvider,
    CharacterSubmission,
    MatchSummary,
    MontageResult,
    Narration,
)
from server.config import GameRules
from server.engine.dice import Dice, describe_formula
from server.engine.models import Character, ClassifiedAction, Event, GameState, Phase
from server.engine.resolver import resolve_round
from server.engine.zones import ZoneRegistry
from server.gallery import GalleryStore
from server.protocol import S2C, SubmitActionMsg
from server.snapshots import SnapshotWriter

if TYPE_CHECKING:
    from server.room import Room

log = logging.getLogger("doodle.state_machine")

# Attack results that put damage on a character (COMBAT V5 — see EventType).
# "devastating" is a hit at creativity tier 3; "reflect" is a shield bouncing a
# blow back at the attacker; "trap" is a Gremlin trap springing.
_DAMAGING_RESULTS = ("hit", "devastating", "reflect", "trap")


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
# Round data — collected drawings and a processed round.
# ---------------------------------------------------------------------------
@dataclass
class _Drawn:
    """Action drawings + tapped moves collected during a draw stage."""

    round_num: int
    action_pngs: dict[str, str]
    fighters: list[str]   # living fighters — tapped a move + drew this round
    gremlins: list[str]   # KO'd players — each planted a trap this round
    # COMBAT V5: pid → (move_id, target_id, escape_direction, trap_zone),
    # server-validated at submit time. Gremlins carry ("", None, 0, trap_zone).
    taps: dict[str, tuple[str, str | None, int, str | None]] = field(default_factory=dict)


@dataclass
class _Processed:
    """A classified/resolved/narrated round, ready to reveal."""

    round_num: int
    narration: Narration | None
    events: list[Event]
    initiative_order: list[str]
    action_pngs: dict[str, str]
    post_state: GameState
    actions: list[ClassifiedAction] = field(default_factory=list)


class GameStateMachine:
    def __init__(
        self,
        room: Room,
        rules: GameRules,
        ai: AIProvider,
        timers: Timers | None = None,
        snapshots: SnapshotWriter | None = None,
        gallery: GalleryStore | None = None,
    ):
        self.room = room
        self.rules = rules
        self.balance = rules.balance
        self.ai = ai
        self._zone_reg = ZoneRegistry(rules.zones)
        self.timers = timers or Timers.from_settings(rules.settings.timers)
        self.snapshots = snapshots or SnapshotWriter(
            rules.settings.snapshots.dir, room.code, rules.settings.snapshots.enabled
        )
        # The Doodle Crowd (§15): persisted at game over; a snapshot of its names
        # is cached at character generation for the narrator's cameos.
        self.gallery = gallery or GalleryStore.from_rules(rules)
        self._gallery_names: list[str] = []
        self.seed = room.seed

        # The single live game state — updated as each round is revealed.
        self.state: GameState | None = None
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
        # COMBAT V5: pid → (move_id, target_id, escape_direction, trap_zone) for
        # the round being drawn right now (gremlins carry ("", None, 0, zone)).
        self._action_taps: dict[str, tuple[str, str | None, int, str | None]] = {}
        # Separate live buffer for a Power-Up Montage sub-phase (never overlaps an
        # action draw — a player is in exactly one draw phase at a time).
        self._montage_pngs: dict[str, str] = {}
        self._beat_done = asyncio.Event()

        # Persistent battlefield sprites: a character's most-recently-revealed
        # action drawing becomes their sprite until their next action replaces it
        # (original character image until they first act). Server-owned so it
        # survives host refresh.
        self._latest_action_png: dict[str, str] = {}
        # Creativity tier of each character's latest action → the host's star
        # badges under the action sprite (⭐/⭐⭐/⭐⭐⭐; §13).
        self._latest_action_creativity: dict[str, int] = {}
        # Rolling per-team creativity totals feeding the "Crowd Favorite" meter,
        # recency-weighted by keeping only the last N rounds.
        self._audience: deque[dict[str, int]] = deque(
            maxlen=max(1, rules.settings.ui.audience_recent_rounds)
        )
        self._degraded_announced = False

        # AI team names, held back until the intro reveal (GAME_DESIGN §2).
        self._team_names: dict[str, str] = {}

        # Match-wide tallies for the victory awards ceremony (GAME_DESIGN §10.2).
        self._creativity_totals: dict[str, int] = {}
        self._reflect_counts: dict[str, int] = {}
        self._combos_seen: list[dict] = []
        self._round_titles: list[str] = []
        self._best_line: str = ""

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
    async def submit_action(self, player_id: str, msg: SubmitActionMsg) -> None:
        """COMBAT V5 action submission. A living fighter taps a move (+ target,
        + ESCAPE direction); an Arena Gremlin taps a trap_zone instead. The move
        is validated (no-repeat, legality, living target); an empty move_id
        (timer auto-submit with no tap) is accepted — the fighter stumbles."""
        ch = self.state.characters.get(player_id) if self.state else None
        if ch is not None and ch.is_ko:
            # Gremlins plant a trap in the tapped zone — no move, no target.
            self._action_taps[player_id] = ("", None, 0, msg.trap_zone)
        elif msg.move_id:
            error = self.validate_tap(player_id, msg.move_id, msg.target_id)
            if error is not None:
                await self.room.send(player_id, S2C.TOAST,
                                     {"message": error, "kind": "action_rejected"})
                return
            self._action_taps[player_id] = (
                msg.move_id, msg.target_id, msg.escape_direction, None)
        self._action_pngs[player_id] = msg.png_base64
        self._note_submission(player_id)

    def validate_tap(self, player_id: str, move_id: str,
                     target_id: str | None) -> str | None:
        """The server-side tap rules (§4.1): returns a player-facing error
        message, or None when the tap is legal."""
        move = self.rules.moves.moves.get(move_id)
        if move is None:
            return "That move doesn't exist."
        if self.state is None or player_id not in self.state.characters:
            # Intros play before Round 1 (v2.1), so characters always exist by
            # the first action draw — a tap without one is out of order.
            return "Hold on — the fighters aren't ready yet!"
        ch = self.state.characters[player_id]
        if ch.is_ko:
            return "You can't fight right now — you're a Gremlin!"
        if move_id == ch.last_move_id:
            return "No repeats! Pick a different move this round."
        # SMASH needs an enemy in your zone; PROTECT needs a living ally.
        if move_id == "smash" and not self._enemy_in_zone(player_id, ch.zone_id):
            return "SMASH needs an enemy in your zone — try BLAST or CHARGE."
        if move.target == "ally" and not self._living_ally(player_id):
            return "No teammate to protect right now!"
        if target_id is not None:
            target = self.state.characters.get(target_id)
            if target is None or target.is_ko:
                return "That target is already out of the fight."
        return None

    def _enemy_in_zone(self, pid: str, zone_id: str) -> bool:
        team = self.room.team_of(pid)
        return any(
            not c.is_ko and c.zone_id == zone_id and self.room.team_of(p) != team
            for p, c in (self.state.characters.items() if self.state else [])
        )

    def _living_ally(self, pid: str) -> bool:
        team = self.room.team_of(pid)
        return any(
            p != pid and not c.is_ko and self.room.team_of(p) == team
            for p, c in (self.state.characters.items() if self.state else [])
        )

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
                                timeout=self.timers.draw_characters, splash=True)
        await self._collect([p.id for p in self.room.players],
                            self._splash_seconds() + self.timers.draw_characters)

    # -- character intros (v2.1: BEFORE Round 1 drawing) ------------------
    async def _intros_stage(self) -> None:
        """The INTROS phase: a "meet the fighters" drumroll interstitial (the
        deliberation pattern, showing everyone's character drawings) masks the
        generate_characters call, then each fighter gets their giant-sprite
        moment and the team names land as the final intro beat."""
        await self._enter_phase("intros", round_num=0, timeout=0.0, splash=True)
        drawings = {p.id: p.character_png for p in self.room.players
                    if p.character_png}
        await self.room.broadcast(S2C.DELIBERATION, {
            "round": 0, "kind": "intros", "drawings": drawings,
        })
        intro_order = await self._process_characters()
        await self._reveal_intros(intro_order)

    # -- the sequential round loop (GAME_DESIGN.md §2) --------------------
    async def _round_loop(self) -> None:
        cadence = self.rules.settings.game.montage_every_rounds

        # Meet the fighters first (v2.1) — every round then plays the same
        # strictly sequential loop: draw → deliberation interlude → process →
        # reveal. Each move is on screen right after it's drawn.
        await self._intros_stage()

        for round_num in range(1, _SAFETY_ROUND_CAP + 1):
            fighters, gremlins = self._draw_roster()
            action_pngs, taps = await self._draw_stage(round_num, fighters + gremlins)
            await self._deliberation_interlude(round_num, action_pngs)
            processed = await self._process_round(
                _Drawn(round_num, action_pngs, fighters, gremlins, taps=taps)
            )
            await self._reveal_round(processed)
            if self._has_winner():
                return
            if cadence and round_num % cadence == 0:
                await self._run_montage(round_num)

    def _has_winner(self) -> bool:
        return self.state is not None and self.state.winner_team_id is not None

    async def _deliberation_interlude(self, round_num: int, drawings: dict[str, str],
                                      kind: str = "deliberation") -> None:
        """The latency mask (GAME_DESIGN §2): the moment all drawings are in, the
        TV shows every submitted drawing side by side while the round is
        classified/resolved/narrated — never a spinner. Also fronts the montage's
        training-montage interstitial."""
        self._phase = "deliberate"
        await self.room.broadcast(S2C.DELIBERATION, {
            "round": round_num, "kind": kind, "drawings": dict(drawings),
        })

    def _draw_roster(self) -> tuple[list[str], list[str]]:
        """Who draws this round: living fighters (a move) and Arena Gremlins
        (a trap — GAME_DESIGN §10). Gremlins are is_ko, so the sets are
        disjoint. Characters always exist by now — intros precede Round 1."""
        assert self.state is not None
        fighters = [pid for pid, ch in self.state.characters.items() if not ch.is_ko]
        gremlins = [pid for pid, ch in self.state.characters.items() if ch.is_gremlin]
        return fighters, gremlins

    async def _draw_stage(self, round_num: int,
                          living: list[str]) -> tuple[dict[str, str],
                                                      dict[str, tuple[str, str | None]]]:
        self._action_pngs = {}
        self._action_taps = {}
        await self._enter_phase("draw_action", round_num=round_num,
                                timeout=self.timers.draw_action,
                                splash=True)
        await self._send_canvas_inits()
        await self._broadcast_arena()
        await self._collect(living, self._splash_seconds() + self.timers.draw_action)
        return dict(self._action_pngs), dict(self._action_taps)

    # -- Power-Up Montage (GAME_DESIGN §10.1) -----------------------------
    async def _run_montage(self, round_num: int) -> None:
        """A montage sub-phase: survivors upgrade their character, the TV masks
        the classify with a training-montage interstitial, then the stat-pulse
        reveal plays. Sequential — the loop waits for it before Round r+1."""
        survivors = self._draw_roster()[0]
        if not survivors:
            return
        montage_pngs = await self._montage_draw_stage(round_num, survivors)
        await self._deliberation_interlude(round_num, montage_pngs, kind="montage")
        await self._resolve_montage(round_num, montage_pngs, survivors)

    async def _montage_draw_stage(self, round_num: int, survivors: list[str]) -> dict[str, str]:
        """The montage bonus phase: survivors' canvases preload their current
        full-size character (host scales by the `montage` phase) to add an upgrade."""
        self._montage_pngs = {}
        await self._enter_phase("montage", round_num=round_num,
                                timeout=self.timers.montage, splash=True)
        await self._send_canvas_inits()
        await self._collect(survivors, self._splash_seconds() + self.timers.montage)
        return dict(self._montage_pngs)

    async def _resolve_montage(self, round_num: int, montage_pngs: dict[str, str],
                               survivors: list[str]) -> list[MontageResult]:
        """Classify each upgrade → +1 stat, apply it, make the upgraded drawing the
        new 'original' everywhere, and broadcast the stat pulses (S2)."""
        subs = {pid: ActionSubmission(pid, montage_pngs.get(pid, "")) for pid in survivors}
        results = await asyncio.to_thread(self.ai.classify_montage, self.state, subs, round_num)
        self._apply_montage(self.state, results, montage_pngs)
        await self._check_degraded()
        for r in results:
            # Reset the sprite so the upgraded original shows until the next action.
            self._latest_action_png.pop(r.player_id, None)
        self._beat_done = asyncio.Event()
        upgrades = [
            {"player_id": r.player_id, "stat": r.stat, "flavor": r.flavor,
             "png": montage_pngs.get(r.player_id, "")}
            for r in results
        ]
        await self.room.broadcast(S2C.MONTAGE, {
            "round": round_num, "upgrades": upgrades,
            "characters": self._character_deltas(include_png=True),
        })
        await self._send_all_player_states()
        await self._send_canvas_inits()
        await self._broadcast_arena()
        await self._pace_beats(max(1, len(upgrades)))
        return results

    def _apply_montage(
        self, state: GameState, results: list[MontageResult], montage_pngs: dict[str, str]
    ) -> None:
        """Apply +1 stat with formula deltas and swap in the upgraded drawing as
        the character's new 'original'."""
        for r in results:
            ch = state.characters.get(r.player_id)
            if ch is None or ch.is_ko:
                continue
            old_val = getattr(ch.stats, r.stat)
            new_val = min(self.balance.stat_max, old_val + 1)
            setattr(ch.stats, r.stat, new_val)
            # HP formula deltas (v5: Power, Weird, and floor(Speed/2) all feed HP).
            gain = 0
            if r.stat == "power":
                gain = self.balance.hp_per_power * (new_val - old_val)
            elif r.stat == "weird":
                gain = self.balance.hp_per_weird * (new_val - old_val)
            elif r.stat == "speed":
                gain = new_val // self.balance.hp_speed_divisor - \
                    old_val // self.balance.hp_speed_divisor
            if gain > 0:
                ch.max_hp += gain
                ch.hp = min(ch.max_hp, ch.hp + gain)     # +max HP, healed by the gain
            png = montage_pngs.get(r.player_id)
            if png:
                ch.character_png_b64 = png               # the new original everywhere

    def _max_hp(self, stats) -> int:
        """The v5 HP formula: 27 + 2*POW + WRD + floor(SPD/2) (config-driven)."""
        b = self.balance
        return (b.hp_base + b.hp_per_power * stats.power + b.hp_per_weird * stats.weird
                + stats.speed // b.hp_speed_divisor)

    async def _process_characters(self) -> list[str]:
        """Generate characters and build the initial game state. Returns the intro
        acting order (initiative = Speed) for the character-intro reveal."""
        subs = {
            p.id: CharacterSubmission(p.id, p.character_png, p.hint,
                                      team_id=p.team_id or "")
            for p in self.room.players
        }
        roster = await asyncio.to_thread(self.ai.generate_characters, subs, self.balance)
        generated = roster.characters
        # The AI team names stay under wraps until the intro reveal's final
        # beat swaps every Team A/B label at once (GAME_DESIGN §2).
        self._team_names = dict(roster.team_names)

        chars: dict[str, Character] = {}
        for p in self.room.players:
            g = generated[p.id]
            hp = self._max_hp(g.stats)
            chars[p.id] = Character(
                player_id=p.id,
                name=g.name,
                stats=g.stats,
                personality=g.personality,
                announcer_intro=g.announcer_intro,
                hp=hp,
                max_hp=hp,
                zone_id=self.room.zone_for_team(p.team_id or "team_a"),
                character_png_b64=p.character_png,
                flagged=getattr(g, "flagged", False),
            )
        self.state = GameState(
            room_id=self.room.code, phase=Phase.ROUND_LOOP, round=0,
            characters=chars, teams=self.room.teams,
        )
        if self.gallery.enabled:
            self._gallery_names = await asyncio.to_thread(self.gallery.all_names)
        await self._send_all_player_states()
        await self._send_canvas_inits()
        await self._broadcast_arena()
        await self._check_degraded()

        # The rail's acting order (initiative = Speed) for the intros.
        return sorted(chars, key=lambda pid: -chars[pid].stats.speed)

    async def _process_round(self, drawn: _Drawn) -> _Processed:
        """Classify → resolve → narrate one round against the current state. AI
        calls run off the event loop so a slow API doesn't freeze the interlude.
        Does not commit the state — `_reveal_round` does that as the beats play."""
        assert self.state is not None
        state_for_round = self.state.model_copy(update={"round": drawn.round_num})

        _blank = ("", None, 0, None)
        fighter_subs = {
            pid: ActionSubmission(
                pid, drawn.action_pngs.get(pid, ""),
                move_id=drawn.taps.get(pid, _blank)[0],
                target_id=drawn.taps.get(pid, _blank)[1],
                escape_direction=drawn.taps.get(pid, _blank)[2],
            )
            for pid in drawn.fighters
        }
        actions = await asyncio.to_thread(
            self.ai.classify_actions, state_for_round, fighter_subs, drawn.round_num
        )
        # Arena Gremlins are classified separately (trap drawing → creativity;
        # the zone is the tapped ground truth) and merged in; the resolver's
        # trap pass reads them off the same list.
        if drawn.gremlins:
            grem_subs = {
                pid: ActionSubmission(pid, drawn.action_pngs.get(pid, ""),
                                      trap_zone=drawn.taps.get(pid, _blank)[3])
                for pid in drawn.gremlins
            }
            grem_actions = await asyncio.to_thread(
                self.ai.classify_gremlin, state_for_round, grem_subs, drawn.round_num
            )
            actions = actions + grem_actions
        rng = Dice(seed=self.seed + drawn.round_num)
        result = resolve_round(state_for_round, actions, rng, self.balance)
        await self._check_degraded()
        self.snapshots.write_round(drawn.round_num, result.new_state, result.events)
        self.snapshots.append_flavor(drawn.round_num, actions)
        cameos = self._sample_cameos(result.new_state)
        narration = await asyncio.to_thread(
            self.ai.narrate_round, result.events, result.new_state.characters,
            cameos, self._zone_display_names(),
        )
        self.snapshots.append_transcript(drawn.round_num, narration.round_title,
                                         narration.beats)
        self._accumulate_match(actions, result.events, narration)
        return _Processed(
            round_num=drawn.round_num, narration=narration, events=result.events,
            initiative_order=result.initiative_order, action_pngs=drawn.action_pngs,
            post_state=result.new_state, actions=actions,
        )

    async def _reveal_round(self, processed: _Processed) -> None:
        """Commit the round's results to the live state and play the beats — this
        ends the deliberation interlude on the host and shows the outcome."""
        self.state = processed.post_state
        self._accumulate_audience(processed.actions)
        await self._reveal(processed.round_num, processed.narration, processed.events,
                           processed.initiative_order, action_pngs=processed.action_pngs)
        # The revealed action drawings are now the persistent battlefield sprites,
        # each carrying its creativity tier for the host's star badges (§13).
        self._latest_action_png.update(processed.action_pngs)
        for a in processed.actions:
            if a.player_id in processed.action_pngs:
                self._latest_action_creativity[a.player_id] = max(0, a.creativity_tier)
        await self._send_all_player_states()
        await self._broadcast_arena()

    async def _reveal_intros(self, order: list[str]) -> None:
        """Character-intro reveal — each fighter's sprite fills the arena while
        the announcer greets them (v2.1: this plays BEFORE Round 1 drawing).
        Reuses the reveal_step shape (round 0); the host renders type=intro
        beats as the giant-sprite showcase. The final beat reveals the AI team
        names ("…and TOGETHER they are…"), which swap every Team A/B label for
        the rest of the match (§2)."""
        self._beat_done = asyncio.Event()
        chars = self.state.characters if self.state else {}
        beats = [
            {
                "event_id": f"intro-{pid}",
                "text": chars[pid].announcer_intro or f"Introducing {chars[pid].name}!",
                "speaker": "pbp",   # the play-by-play announcer hypes each fighter
                "player_id": pid, "target_id": None, "type": "intro",
                # The giant-sprite showcase card (host fills the arena with the
                # fighter's sprite; name/stats/personality render beside it).
                "name": chars[pid].name,
                "personality": chars[pid].personality,
                "stats": {"power": chars[pid].stats.power,
                          "speed": chars[pid].stats.speed,
                          "weird": chars[pid].stats.weird},
                "hurt": None, "helped": None, "floats": [],
                "combo_name": None, "sfx": None, "result": None,
            }
            for pid in order if pid in chars
        ]
        beats.append(self._team_reveal_beat())
        await self.room.broadcast(S2C.REVEAL_STEP, {
            "round": 0,
            "round_title": "Meet the Fighters",
            "beats": beats,
            "characters": self._character_deltas(),
            "action_pngs": {},
            "initiative_order": list(order),
            "meters": self._meters(),
            # The named teams: clients swap zone bands, meter ends, and phone
            # headers when the team_reveal beat plays, and keep them for good.
            "teams": self._teams_payload(),
        })
        await self._pace_beats(len(beats))

    def _team_reveal_beat(self) -> dict:
        """Apply the AI team names to the room + game state and build the
        '…and TOGETHER they are…' final intro beat."""
        for team in self.room.teams:
            if self._team_names.get(team.id):
                team.name = self._team_names[team.id]
        if self.state is not None:
            for team in self.state.teams:
                if self._team_names.get(team.id):
                    team.name = self._team_names[team.id]
        names = [t.name for t in self.room.teams]
        return {
            "event_id": "intro-teams",
            "text": "…and TOGETHER they are… "
                    + " and ".join(n.upper() for n in names) + "!",
            "speaker": "pbp",
            "player_id": None, "target_id": None, "type": "team_reveal",
            "hurt": None, "helped": None, "floats": [],
            "combo_name": None, "sfx": None, "result": None,
            "teams": self._teams_payload(),
        }

    def _teams_payload(self) -> list[dict]:
        return [{"id": t.id, "name": t.name, "color": t.color}
                for t in self.room.teams]

    async def _reveal(self, round_num: int, narration, events, initiative_order=None,
                      action_pngs: dict[str, str] | None = None) -> None:
        self._beat_done = asyncio.Event()
        pngs = self._action_pngs if action_pngs is None else action_pngs
        # Attach each beat's acting/target player so the host can sprite-swap the
        # right character while the beat plays (ARCHITECTURE.md §4.5), plus the
        # impact + floating-number data the host renders (all from engine events,
        # never narration text).
        ev_by_id = {e.id: e for e in events}
        # Where each fighter that MOVED this round ended up — attached to their
        # action beat so the host animates the sprite into its new zone as the
        # beat plays (CHARGE/ESCAPE, one combined beat — v6 §13).
        moved_to = {e.player_id: e.data.get("to")
                    for e in events if e.type.value == "moved"}
        # Allies a PROTECT actually shielded this round (reflect > 0), keyed by
        # the caster — so the caster's beat lights the ally's round-long blue
        # glow no matter which PROTECT event the narrator tagged (v6 §13).
        shield_by_caster = {e.player_id: e.target_id for e in events
                            if e.type.value == "protected"
                            and e.data.get("reflect_pct", 0) > 0}
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
                # The move-name badge under the acting fighter after their reveal
                # (SMASH/BLAST/CHARGE/ESCAPE/PROTECT — v6 §13).
                "move_name": self._move_name(ev) if ev else None,
                # Destination zone if this beat's fighter relocated (CHARGE/ESCAPE).
                "to_zone": (moved_to.get(ev.player_id) if ev else None),
                # The ally to light with PROTECT's round-long glow, if this beat's
                # caster raised a shield (v6 §13).
                "shield_on": (shield_by_caster.get(ev.player_id)
                              if ev and ev.type.value in ("healed", "protected")
                              else None),
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
                # Attack outcome (hit/devastating/reflect/trap/...) so the host
                # can fire the right stinger without parsing narration text.
                "result": (ev.data.get("result") or None) if ev else None,
                # The plain-language math lines under the arena (§13).
                "readout": self._readout(ev) if ev else [],
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
        timeout so a missing/!clicking host never stalls the game. Manual pacing
        (ui.reveal_beat_seconds == 0 → host clicks every beat) gets a generous
        per-beat grace so a host reading at their own pace is never cut off."""
        per_beat = self.timers.reveal
        if self.rules.settings.ui.reveal_beat_seconds <= 0:
            per_beat = max(per_beat, 60.0)
        timeout = max(0.05, per_beat * max(1, n_beats))
        try:
            await asyncio.wait_for(self._beat_done.wait(), timeout)
        except TimeoutError:
            pass

    def _beat_sfx(self, ev) -> str | None:
        """The sound clip for this beat's move (moves.yaml sfx key), or None
        when the event has no catalog move (KOs, victory, combos — those are
        covered by the client-side event stingers)."""
        move = self.rules.moves.moves.get(ev.data.get("move_id", ""))
        return (move.sfx or None) if move else None

    def _move_name(self, ev) -> str | None:
        """The move-name badge under the acting fighter (v6 §13). Attacks carry
        their move_id directly; PROTECT's heal/shield events don't, so we resolve
        the button from the catalog's healing move. Reflects, KOs and traps carry
        no fresh badge (the bounce lands on someone whose own badge is set)."""
        t = ev.type.value
        if t == "attack_resolved" and ev.data.get("result") in ("hit", "devastating"):
            move = self.rules.moves.moves.get(ev.data.get("move_id", ""))
            return move.button if move else None
        if t in ("healed", "protected"):
            for move in self.rules.moves.moves.values():
                if move.heal or move.applies_shield:
                    return move.button
        return None

    def _readout(self, ev) -> list[str]:
        """The host's plain-language math for this beat (GAME_DESIGN §13):

            🔥 BLAST → 🎲 5 + 🌀 Weird 5 + ⭐⭐ Creative 3 = 13 damage
            🛡️ Blob's shield reflects 3 back at Stabby!

        One addition and one total per line; zero terms omitted; reductions get
        their own line rather than rewriting the first. All terms come from the
        engine's own arithmetic, so the line always matches the HP bars.
        """
        cfg = self.rules.settings.ui.readout
        if not cfg.enabled or self.state is None:
            return []
        d = ev.data
        t = ev.type.value
        result = d.get("result")
        if t not in ("attack_resolved", "healed"):
            return []

        def name_of(pid: str | None) -> str:
            ch = self.state.characters.get(pid or "")
            return ch.name if ch else "Someone"

        # A reflect is reduction-only: the shield bounced part of the blow back.
        if result == "reflect":
            return [cfg.reflect_line.format(
                target=name_of(ev.player_id), attacker=name_of(ev.target_id),
                total=d.get("damage", 0))]
        if "raw" not in d:
            return []      # traps and other events carry no addition to show

        move = self.rules.moves.moves.get(d.get("move_id", ""))
        if move is None:
            return []

        # The addition — omit any zero term (§13).
        terms = [f"{cfg.dice_icon} {d['dice']}"]
        if d.get("stat") and d.get("stat_value"):
            terms.append(f"{cfg.stat_icons.get(d['stat'], '')} "
                         f"{cfg.stat_labels.get(d['stat'], d['stat'])} {d['stat_value']}")
        if d.get("creativity_bonus"):
            tier = d.get("creativity_tier", 0)
            chip = (cfg.devastating_chip if tier >= 3
                    else f"{cfg.star_icon * tier} {cfg.creative_label}")
            terms.append(f"{chip} {d['creativity_bonus']}")
        if d.get("riders"):
            terms.append(str(d["riders"]))

        template = cfg.heal_line if t == "healed" else cfg.damage_line
        return [template.format(
            icon=move.icon, move=move.button, terms=" + ".join(terms),
            total=d["raw"],
        )]

    def _hurt_target(self, ev) -> str | None:
        """The player negatively impacted by an event (damaged, KO'd),
        or None for neutral/positive events."""
        t = ev.type.value
        d = ev.data
        if t == "attack_resolved" and d.get("result") in _DAMAGING_RESULTS:
            return ev.target_id
        if t == "trap_triggered":
            return ev.target_id      # the enemy the trap sprang on
        if t == "ko":
            return ev.player_id
        return None

    def _helped_target(self, ev) -> str | None:
        """The player positively impacted by an event (healed, shielded),
        or None for neutral/negative events. Drives the blue border + pop."""
        t = ev.type.value
        if t == "healed":
            return ev.target_id or ev.player_id
        if t == "protected":
            return ev.target_id       # the ally cloaked in a reflecting shield
        return None

    def _floats(self, ev) -> list[dict]:
        """Floating combat numbers for this event: damage (red) / heal (green),
        DEVASTATING flagged oversized. Amounts come from engine events so they
        always match the HP bars."""
        t = ev.type.value
        d = ev.data
        if t == "attack_resolved":
            res = d.get("result")
            if res in _DAMAGING_RESULTS and d.get("damage"):
                return [{"player_id": ev.target_id, "amount": d["damage"],
                         "kind": "damage", "devastating": res == "devastating"}]
        elif t == "trap_triggered" and d.get("damage"):
            return [{"player_id": ev.target_id, "amount": d["damage"],
                     "kind": "damage", "devastating": False}]
        elif t == "healed" and d.get("amount"):
            return [{"player_id": ev.target_id or ev.player_id, "amount": d["amount"],
                     "kind": "heal", "devastating": False}]
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

    # -- awards ceremony match summary (GAME_DESIGN §10.2) ----------------
    def _accumulate_match(self, actions, events, narration) -> None:
        """Tally match-wide highlights (creativity, reflects, combos, titles, a
        best line) as rounds resolve, so the finale can hand out awards."""
        for a in actions:
            self._creativity_totals[a.player_id] = (
                self._creativity_totals.get(a.player_id, 0) + max(0, a.creativity_tier)
            )
            if a.combo_name and a.combo_partners:
                self._combos_seen.append({
                    "combo_name": a.combo_name,
                    "partners": [a.player_id, *a.combo_partners],
                })
        highlight_ids = set()
        for e in events:
            if e.type.value == "attack_resolved":
                res = e.data.get("result")
                if res == "reflect" and e.player_id:
                    self._reflect_counts[e.player_id] = (
                        self._reflect_counts.get(e.player_id, 0) + 1
                    )
                elif res == "devastating":
                    highlight_ids.add(e.id)
        if narration is not None:
            if narration.round_title:
                self._round_titles.append(narration.round_title)
            # The highlight line is the latest DEVASTATING beat (the spike
            # moment); fall back to the first beat.
            for b in narration.beats:
                if b.event_id in highlight_ids:
                    self._best_line = b.text
                    break
            if not self._best_line and narration.beats:
                self._best_line = narration.beats[0].text

    def _build_match_summary(self) -> MatchSummary:
        players = []
        for p in self.room.players:
            ch = self.state.characters.get(p.id) if self.state else None
            players.append({
                "player_id": p.id,
                "name": ch.name if ch else p.name,
                "team_id": self.room.team_of(p.id),
                "alive": bool(ch and not ch.is_ko),
            })
        return MatchSummary(
            winner_team_id=self.state.winner_team_id if self.state else None,
            players=players,
            creativity=dict(self._creativity_totals),
            reflects=dict(self._reflect_counts),
            combos=list(self._combos_seen),
            round_titles=list(self._round_titles),
            best_line=self._best_line,
        )

    # -- the Doodle Crowd (gallery, GAME_DESIGN §15) ----------------------
    def _sample_cameos(self, state: GameState) -> list[str]:
        """A few gallery names for the narrator to cameo — never the fighters
        currently in the ring."""
        if not self._gallery_names or self.gallery.cameo_count <= 0:
            return []
        current = {ch.name for ch in state.characters.values()}
        pool = [n for n in self._gallery_names if n not in current]
        k = min(self.gallery.cameo_count, len(pool))
        return random.sample(pool, k) if k > 0 else []

    def _gallery_entries(self) -> list[dict]:
        """This match's characters, shaped for gallery persistence."""
        if self.state is None:
            return []
        entries = []
        for pid, ch in self.state.characters.items():
            team = next((t for t in self.state.teams if pid in t.player_ids), None)
            entries.append({
                "name": ch.name,
                "stats": {"power": ch.stats.power, "speed": ch.stats.speed,
                          "weird": ch.stats.weird},
                "team_id": team.id if team else None,
                "team_name": team.name if team else None,
                "won": bool(team and team.id == self.state.winner_team_id),
                "room": self.room.code,
                "png": ch.character_png_b64,
            })
        return entries

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
        awards: list = []
        poster_path: str | None = None
        # Every character drawn joins the Doodle Crowd (§15), win or lose.
        if self.state is not None and self.gallery.enabled:
            await asyncio.to_thread(self.gallery.save_match, self._gallery_entries())
        # Only a real victory earns the ceremony (a crashed loop leaves winner unset).
        if winner and self.state is not None:
            summary = self._build_match_summary()
            awards = await asyncio.to_thread(self.ai.generate_awards, summary)
            poster_path = await asyncio.to_thread(self._compose_poster, summary)
        await self.room.broadcast(S2C.GAME_OVER, {
            "winner_team_id": winner,
            "winner_team_name": next(
                (t.name for t in self.room.teams if t.id == winner), None),
            # PNGs included: the awards ceremony enlarges each winner's drawing.
            "characters": self._character_deltas(include_png=True),
            # Awards ceremony + downloadable match poster (sync point S3).
            "awards": [{"title": a.title, "player_id": a.player_id, "blurb": a.blurb}
                       for a in awards],
            "poster_path": poster_path,
            # Browser-reachable poster (GET /poster/<room> serves the PNG).
            "poster_url": f"/poster/{self.room.code}" if poster_path else None,
        })

    def _compose_poster(self, summary: MatchSummary) -> str | None:
        """Render the match poster to snapshots/<room>/poster.png. Gated on the
        snapshot writer (tests disable disk output) and never raises — a failed
        poster just means no poster."""
        if not self.snapshots.enabled or self.state is None:
            return None
        try:
            from server.poster import compose_poster
            path = self.snapshots.dir / "poster.png"
            compose_poster(path, self.state, self.room.teams, summary,
                           self.rules.settings.ui.canvas_background_color)
            return str(path)
        except Exception:  # pragma: no cover - defensive; poster is best-effort
            log.exception("poster composition failed in room %s", self.room.code)
            return None

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
    def _splash_payload(self, phase: str, round_num: int) -> dict:
        """The phase splash (GAME_DESIGN §13): full-screen copy shown on all
        phones + the TV before the canvas; KO'd players get the gremlin line."""
        ui = self.rules.settings.ui
        texts = ui.splash_text

        def txt(key: str) -> str:
            return (texts.get(key) or "").replace("{round}", str(round_num))

        return {
            "seconds": ui.phase_splash_seconds,
            "text": txt(phase),
            "gremlin_text": txt("gremlin") if phase == "draw_action" else txt(phase),
        }

    def _splash_seconds(self) -> float:
        return max(0.0, self.rules.settings.ui.phase_splash_seconds)

    async def _enter_phase(self, phase: str, round_num: int, timeout: float,
                           extra: dict | None = None,
                           splash: bool = False) -> None:
        self._phase = phase
        self._round = round_num
        # The draw timer starts only after the splash ends — the deadline
        # excludes it (clients start counting when the splash clears).
        lead_in = self._splash_seconds() if splash else 0.0
        self._deadline = time.time() + lead_in + timeout
        payload = {
            "phase": phase, "round": round_num, "deadline_ts": self._deadline,
            **(extra or {}),
        }
        if splash:
            payload["splash"] = self._splash_payload(phase, round_num)
        await self.room.broadcast(S2C.PHASE_CHANGE, payload)

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
        # COMBAT V5: the phone's five-button grid — each move with this
        # character's live math and its disabled state (no-repeat / legality).
        payload["last_move_id"] = ch.last_move_id
        payload["moves"] = self._moves_payload(ch)
        await self.room.send(player_id, S2C.PLAYER_STATE, payload)

    def _moves_payload(self, ch: Character) -> list[dict]:
        """The five buttons for one character: label, live math ("2d4+8"),
        targeting mode for the picker, and why a button is greyed out (no-repeat,
        SMASH with no same-zone enemy, PROTECT with no living ally).

        The math is the move's base only — creativity is unknowable at draw time,
        which is the point: the button promises the floor, the drawing raises it.
        """
        env = _formula_env(ch)
        has_enemy_here = self._enemy_in_zone(ch.player_id, ch.zone_id)
        has_ally = self._living_ally(ch.player_id)
        out = []
        for move_id, move in self.rules.moves.moves.items():
            math = ""
            if move.damage:
                math = describe_formula(move.damage, env)
            elif move.heal:
                math = "♥ " + describe_formula(move.heal, env)
            disabled_reason = None
            if move_id == ch.last_move_id:
                disabled_reason = "no_repeat"
            elif move_id == "smash" and not has_enemy_here:
                disabled_reason = "no_enemy_here"
            elif move.target == "ally" and not has_ally:
                disabled_reason = "no_ally"
            out.append({
                "id": move_id,
                "button": move.button,
                "icon": move.icon,
                # The stat icon(s) that POWER the move (💪/⚡/🌀), shown on the
                # button so a player sees at a glance which moves their build is
                # made for — 💪⚡ CHARGE keys off the average of both (v6 §13).
                "stat": move.stat,
                "stat_icon": self._stat_icons(move.stat),
                "desc": move.desc,
                "math": math,
                "target": move.target,          # "single_enemy" | "ally"
                "moves_one_zone": move.moves_one_zone,   # ESCAPE asks ◀/▶
                "disabled": disabled_reason is not None,
                "disabled_reason": disabled_reason,
            })
        return out

    def _stat_icons(self, stat: str) -> str:
        """The stat emoji that a move keys off, from ui.readout.stat_icons (the
        same icons as the status card / rail). 'avg(power,speed)' returns BOTH."""
        icons = self.rules.settings.ui.readout.stat_icons
        if stat.startswith("avg(") and stat.endswith(")"):
            parts = [p.strip() for p in stat[4:-1].split(",")]
            return "".join(icons.get(p, "") for p in parts)
        return icons.get(stat, "")

    async def _broadcast_arena(self) -> None:
        await self.room.broadcast(S2C.ARENA_STATE, self._arena_payload())

    def _arena_payload(self) -> dict:
        return {
            # Server-composed band labels: team backlines carry the team name
            # ("Team A" until the intro reveal, the AI name after — §13).
            "zones": [{"id": z.id, "label": self._zone_label(z)}
                      for z in self.rules.zones.zones],
            "teams": self._teams_payload(),
            "characters": self._character_deltas(include_png=True),
            # Arena Gremlin traps: small drawn icons in their planted zone that
            # persist until an enemy triggers them (§10).
            "traps": self._traps_payload(),
        }

    def _traps_payload(self) -> list[dict]:
        if self.state is None:
            return []
        return [
            {"trap_id": t.trap_id, "zone_id": t.zone_id, "owner_id": t.owner_id,
             "creativity": t.creativity, "png": t.png_b64}
            for t in self.state.traps
        ]

    def _zone_label(self, zone) -> str:
        team_id = next((t.id for t in self.room.teams if t.id in zone.tags), None)
        if team_id:
            team = next(t for t in self.room.teams if t.id == team_id)
            return f"🏠 {team.name}"
        return f"⚔️ {zone.name}"

    def _zone_display_names(self) -> dict[str, str]:
        """Zone id → on-air name for the narrator: team backlines carry the
        current team name (the AI name once revealed) — internal ids like
        glitter_back must never reach the announcers."""
        out: dict[str, str] = {}
        for zone in self.rules.zones.zones:
            team = next((t for t in self.room.teams if t.id in zone.tags), None)
            if team:
                apos = "'" if team.name.endswith("s") else "'s"
                out[zone.id] = f"{team.name}{apos} backline"
            else:
                out[zone.id] = zone.name
        return out

    def _character_deltas(self, include_png: bool = False) -> list[dict]:
        if self.state is None:
            return []
        out = []
        for pid, ch in self.state.characters.items():
            payload = _char_payload(ch, self.room.team_of(pid))
            if include_png:
                # png = original portrait (rail); sprite_png = persistent action
                # drawing shown on the battlefield (falls back to the original);
                # action_creativity = the star badges under the sprite (§13).
                payload["png"] = ch.character_png_b64
                payload["sprite_png"] = (
                    self._latest_action_png.get(pid) or ch.character_png_b64
                )
                payload["action_creativity"] = self._latest_action_creativity.get(pid, 0)
            out.append(payload)
        return out


def _formula_env(ch: Character) -> dict[str, int]:
    """The formula-evaluation environment for one character (see engine/dice.py)."""
    return {"POW": ch.stats.power, "SPD": ch.stats.speed, "WRD": ch.stats.weird}


def _char_payload(ch: Character, team_id: str | None) -> dict:
    return {
        "player_id": ch.player_id,
        "name": ch.name,
        "hp": ch.hp,
        "max_hp": ch.max_hp,
        # Stats are the rail's / phone status card's home (💪 Power / ⚡ Speed / 🌀 Weird).
        "stats": {"power": ch.stats.power, "speed": ch.stats.speed, "weird": ch.stats.weird},
        "zone_id": ch.zone_id,
        "team_id": team_id,
        "is_ko": ch.is_ko,
        "is_gremlin": ch.is_gremlin,
    }
