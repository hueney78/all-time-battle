"""Game phase state machine + pipeline orchestration (asyncio).

Phases: LOBBY → DRAW_CHARACTERS → ROUND_LOOP(draw → process → reveal) → GAME_OVER

Phase 3 drives the phases sequentially with instant mock AI; the concurrent
draw(r+1) ‖ process(r) ‖ reveal(r-1) overlap (ARCHITECTURE.md §4.2) is layered
on in Phase 6. Each drawing phase ends as soon as every living player submits,
or when the timer fires (missing canvases auto-submit blank → the classifier
reads that as a `stumble`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from server.ai.provider import ActionSubmission, AIProvider, CharacterSubmission
from server.config import GameRules
from server.engine.dice import Dice
from server.engine.models import Character, GameState, Phase
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

    @classmethod
    def from_settings(cls, tc) -> Timers:
        return cls(
            draw_characters=float(tc.draw_characters_seconds),
            draw_action=float(tc.draw_action_seconds),
            reveal=float(tc.beat_seconds),
        )


_SAFETY_ROUND_CAP = 60  # guards against a pathological no-victory loop


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

        self.state: GameState | None = None
        self.task: asyncio.Task | None = None

        # phase/reveal bookkeeping (also used by resync)
        self._phase: str = "lobby"
        self._round: int = 0
        self._deadline: float = 0.0

        # collection state (one drawing phase at a time)
        self._expected: set[str] = set()
        self._collected: set[str] = set()
        self._collect_done = asyncio.Event()
        self._action_pngs: dict[str, str] = {}
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
        elif self._phase == "draw_action":
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

    # -- phases -----------------------------------------------------------
    async def _draw_characters(self) -> None:
        await self._enter_phase("draw_characters", round_num=0,
                                timeout=self.timers.draw_characters)
        await self._collect([p.id for p in self.room.players], self.timers.draw_characters)

        subs = {
            p.id: CharacterSubmission(p.id, p.character_png, p.hint)
            for p in self.room.players
        }
        generated = self.ai.generate_characters(subs, self.balance)

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
        await self._send_all_player_states()
        await self._send_canvas_inits()
        await self._broadcast_arena()
        await self._check_degraded()

    async def _round_loop(self) -> None:
        assert self.state is not None
        round_num = 1
        while round_num <= _SAFETY_ROUND_CAP:
            # ---- draw ----
            self._action_pngs = {}
            living = [pid for pid, ch in self.state.characters.items() if not ch.is_ko]
            await self._enter_phase("draw_action", round_num=round_num,
                                    timeout=self.timers.draw_action)
            await self._send_canvas_inits()
            await self._broadcast_arena()
            await self._collect(living, self.timers.draw_action)

            # ---- process (classify → resolve → narrate) ----
            state_for_round = self.state.model_copy(update={"round": round_num})
            subs = {pid: ActionSubmission(pid, self._action_pngs.get(pid, "")) for pid in living}
            actions = self.ai.classify_actions(state_for_round, subs, round_num)
            self._accumulate_audience(actions)
            rng = Dice(seed=self.seed + round_num)
            result = resolve_round(state_for_round, actions, rng, self.balance)
            self.state = result.new_state
            narration = self.ai.narrate_round(result.events, self.state.characters)
            await self._check_degraded()
            self.snapshots.write_round(round_num, self.state, result.events)
            self.snapshots.append_wildcards(round_num, actions)

            # ---- reveal ----
            await self._reveal(round_num, narration, result.events, result.initiative_order)
            # The revealed action drawings are now the persistent battlefield
            # sprites (they stay until the character's next action replaces them).
            self._latest_action_png.update(self._action_pngs)
            await self._send_all_player_states()
            await self._broadcast_arena()

            if self.state.winner_team_id:
                return
            round_num += 1

    async def _reveal(self, round_num: int, narration, events, initiative_order=None) -> None:
        self._phase = "reveal"
        self._round = round_num
        self._beat_done = asyncio.Event()
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
                "player_id": ev.player_id if ev else None,
                "target_id": ev.target_id if ev else None,
                "type": ev.type.value if ev else None,
                # Who this beat negatively impacts (red border + shake) vs
                # positively impacts (light-blue border + pop).
                "hurt": self._hurt_target(ev) if ev else None,
                "helped": self._helped_target(ev) if ev else None,
                # Floating combat numbers: [{player_id, amount, kind, crit}].
                "floats": self._floats(ev) if ev else [],
            })
        await self.room.broadcast(S2C.REVEAL_STEP, {
            "round": round_num,
            "round_title": getattr(narration, "round_title", ""),
            "beats": beats,
            "characters": self._character_deltas(),
            "action_pngs": dict(self._action_pngs),
            # The rail's acting order + the two tug-of-war meter positions.
            "initiative_order": list(initiative_order or []),
            "meters": self._meters(),
        })
        # Host paces beats client-side and signals completion; fall back to a
        # timeout so a missing/!clicking host never stalls the game.
        timeout = max(0.05, self.timers.reveal * max(1, len(beats)))
        try:
            await asyncio.wait_for(self._beat_done.wait(), timeout)
        except TimeoutError:
            pass

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
        weight recent rounds (the deque's maxlen owns the recency window)."""
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
