"""Anthropic-backed AI provider — the live implementation of AIProvider.

Three call types, all forced tool-use (guaranteed-structured JSON):
  generate_characters — Haiku, once per game
  classify_actions    — Haiku, once per round (character/action image pairs)
  narrate_round       — Sonnet, once per round (text only)

Reliability contract (ARCHITECTURE.md §4.4): 20s timeout, one repair retry with
the validation error appended, and on total failure a non-AI fallback (neutral
classification + template narration) so the game NEVER deadlocks on the API.
Stable rule text (the rendered system prompts) is sent with cache_control to cut
input cost on repeat calls.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
from pydantic import ValidationError

from server.ai import schemas as S
from server.ai import validators as V
from server.ai.provider import (
    ActionSubmission,
    Award,
    Beat,
    CharacterSubmission,
    GeneratedRoster,
    MatchSummary,
    MontageResult,
    Narration,
    _beat_text,
    _mock_round_title,
    _mock_speaker,
)
from server.config import Balance, GameRules
from server.engine.models import Character, ClassifiedAction, Event, GameState

log = logging.getLogger("doodle.ai")

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "config" / "prompts"

# List prices (USD per million tokens) — for the cost LOG line only, not balance.
_PRICES = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
}


class LiveAI:
    """Implements the AIProvider protocol against the Anthropic API."""

    def __init__(self, rules: GameRules, client: Any | None = None):
        self.rules = rules
        self.ai = rules.settings.ai
        if client is None:                       # real client reads ANTHROPIC_API_KEY
            import anthropic

            client = anthropic.Anthropic(max_retries=0)
        self.client = client

        env = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)), autoescape=False)
        self._sys_chargen = env.get_template("character_gen.md.j2").render(balance=rules.balance)
        self._sys_classify = env.get_template("action_classify.md.j2").render(
            moves=rules.moves.moves,
            conditions=sorted(rules.conditions.conditions),
            zones=rules.zones.zones,
        )
        self._sys_gremlin = env.get_template("gremlin_classify.md.j2").render(
            hazards=rules.hazards.hazards,
            zones=rules.zones.zones,
        )
        self._sys_montage = env.get_template("montage_classify.md.j2").render()
        self._sys_narrate = env.get_template("narrate.md.j2").render()
        self._sys_awards = env.get_template("awards.md.j2").render()

        # cost/telemetry + degraded state (read by the state machine for a banner)
        self._cost = 0.0
        self._calls = 0
        self.degraded = False
        self.degraded_reason = ""

    # -- AIProvider ------------------------------------------------------
    def generate_characters(
        self, submissions: dict[str, CharacterSubmission], cfg: Balance
    ) -> GeneratedRoster:
        content: list[dict] = [{"type": "text",
                                "text": "Create a fighter for EACH labeled drawing below, "
                                        "then name BOTH teams from their rosters."}]
        # Grouped by team so the roster-linking team names come naturally.
        by_team = sorted(submissions.items(), key=lambda kv: (kv[1].team_id, kv[0]))
        for pid, sub in by_team:
            hint = (sub.hint or "").strip()
            team = f" [{sub.team_id}]" if sub.team_id else ""
            label = f"--- player {pid}{team}" + (f" — hint: “{hint}”" if hint else "") + " ---"
            content.append({"type": "text", "text": label})
            img = _image_block(sub.png_base64)
            content.append(img if img else {"type": "text", "text": "(no drawing submitted)"})

        parsed = self._call_tool(
            self._sys_chargen, content, S.GenerateCharactersResponse,
            "submit_characters", self.ai.classify_model,
        )
        if parsed is None:
            parsed = S.GenerateCharactersResponse(characters=[])   # → all fallbacks
        return V.build_generated_characters(parsed, submissions, cfg)

    def classify_actions(
        self, state: GameState, submissions: dict[str, ActionSubmission], round_num: int
    ) -> list[ClassifiedAction]:
        # The tapped move + target are ground truth from the phone (COMBAT V2):
        # they're echoed to the AI for context but never chosen by it.
        taps: dict[str, tuple[str, str | None]] = {}
        content: list[dict] = [{"type": "text", "text": _roster_text(state, round_num)}]
        for pid, ch in state.characters.items():
            if ch.is_ko:
                continue
            sub = submissions.get(pid)
            if sub is None or not sub.move_id:
                continue
            taps[pid] = (sub.move_id, sub.target_id)
            move = self.rules.moves.moves.get(sub.move_id)
            target_name = ""
            if sub.target_id and sub.target_id in state.characters:
                target_name = f" targeting {state.characters[sub.target_id].name} ({sub.target_id})"
            content.append({"type": "text", "text":
                            f"=== {ch.name} ({pid}) — tapped move: "
                            f"{(move.button if move else sub.move_id)}{target_name} ==="})
            orig = _image_block(ch.character_png_b64)
            if orig:
                content.append({"type": "text", "text": f"{pid} ORIGINAL CHARACTER:"})
                content.append(orig)
            action_img = _image_block(sub.png_base64)
            content.append({"type": "text", "text": f"{pid} ACTION THIS ROUND:"})
            content.append(action_img if action_img
                           else {"type": "text", "text": "(blank canvas — creativity 0)"})

        if not taps:
            return []
        parsed = self._call_tool(
            self._sys_classify, content, S.ClassifyActionsResponse,
            "submit_actions", self.ai.classify_model,
        )
        if parsed is None:
            # Total AI failure: the tapped moves still resolve at creativity 0 —
            # the server, not the AI, owns the move (§11.1).
            parsed = S.ClassifyActionsResponse(actions=[])
        return V.build_classified_actions(parsed, state, taps, self.rules)

    def classify_gremlin(
        self, state: GameState, submissions: dict[str, ActionSubmission], round_num: int
    ) -> list[ClassifiedAction]:
        gremlins = [pid for pid, ch in state.characters.items() if ch.is_gremlin]
        header = f"Round {round_num}. Classify each Arena Gremlin's hazard drawing."
        content: list[dict] = [{"type": "text", "text": header}]
        drawn: list[str] = []
        for pid in gremlins:
            png = submissions[pid].png_base64 if pid in submissions else ""
            img = _image_block(png)
            if img is None:
                continue                          # blank canvas → no hazard this round
            name = state.characters[pid].name
            content.append({"type": "text", "text": f"=== gremlin {name} ({pid}) ==="})
            content.append(img)
            drawn.append(pid)

        if not drawn:
            return []
        parsed = self._call_tool(
            self._sys_gremlin, content, S.ClassifyGremlinsResponse,
            "submit_gremlin_hazards", self.ai.classify_model,
        )
        if parsed is None:
            parsed = S.ClassifyGremlinsResponse(hazards=[])        # → drop nothing
        return V.build_gremlin_hazards(parsed, drawn, self.rules)

    def classify_montage(
        self, state: GameState, submissions: dict[str, ActionSubmission], round_num: int
    ) -> list[MontageResult]:
        content: list[dict] = [{"type": "text",
                                "text": "Grant +1 stat per upgraded fighter (before → after)."}]
        drawn: list[str] = []
        for pid, sub in submissions.items():
            after = _image_block(sub.png_base64)
            if after is None:
                continue                          # blank montage canvas → no grant
            ch = state.characters.get(pid)
            content.append({"type": "text", "text": f"=== {ch.name if ch else pid} ({pid}) ==="})
            before = _image_block(ch.character_png_b64) if ch else None
            if before:
                content.append({"type": "text", "text": "PREVIOUS CHARACTER:"})
                content.append(before)
            content.append({"type": "text", "text": "UPGRADED CHARACTER:"})
            content.append(after)
            drawn.append(pid)

        if not drawn:
            return []
        parsed = self._call_tool(
            self._sys_montage, content, S.ClassifyMontageResponse,
            "submit_montage", self.ai.classify_model,
        )
        if parsed is None:
            parsed = S.ClassifyMontageResponse(montages=[])       # → no grants
        return V.build_montage(parsed, drawn)

    def narrate_round(
        self, events: list[Event], characters: dict[str, Character],
        gallery_names: list[str] | None = None,
    ) -> Narration:
        content = [{"type": "text", "text": _narration_text(events, characters, gallery_names)}]
        parsed = self._call_tool(
            self._sys_narrate, content, S.NarrateResponse,
            "submit_narration", self.ai.narrate_model,
        )
        if parsed is None:
            return _fallback_narration(events, characters)
        return V.build_narration(parsed, {e.id for e in events})

    def generate_awards(self, summary: MatchSummary) -> list[Award]:
        content = [{"type": "text", "text": _awards_text(summary)}]
        parsed = self._call_tool(
            self._sys_awards, content, S.GenerateAwardsResponse,
            "submit_awards", self.ai.narrate_model,
        )
        if parsed is None:
            parsed = S.GenerateAwardsResponse(awards=[])   # → all fallback awards
        return V.build_awards(parsed, summary)

    # -- core call -------------------------------------------------------
    def _call_tool(self, system_text, content, model_cls, tool_name, model):
        """Forced tool-use with one repair retry, then None (caller falls back)."""
        tool = {
            "name": tool_name,
            "description": f"Return the {tool_name} result.",
            "input_schema": model_cls.model_json_schema(),
        }
        system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
        messages = [{"role": "user", "content": content}]

        for attempt in range(self.ai.max_retries + 1):
            try:
                resp = self.client.messages.create(
                    model=model,
                    max_tokens=2048,
                    system=system,
                    tools=[tool],
                    tool_choice={"type": "tool", "name": tool_name},
                    messages=messages,
                    timeout=self.ai.timeout_seconds,
                )
            except Exception as exc:   # timeout / transport / API error
                log.warning("AI %s call failed (attempt %d): %s", tool_name, attempt + 1, exc)
                continue

            self._log_cost(tool_name, model, getattr(resp, "usage", None))
            tool_input = _tool_input(resp)
            if tool_input is None:
                log.warning("AI %s returned no tool_use block", tool_name)
                continue
            try:
                return model_cls.model_validate(tool_input)
            except ValidationError as exc:
                if attempt < self.ai.max_retries:
                    # Repair: show the model its output + the exact error, ask again.
                    messages += [
                        {"role": "assistant", "content": resp.content},
                        {"role": "user", "content":
                            f"That tool input failed validation:\n{exc}\nCall {tool_name} again "
                            f"with corrected fields only."},
                    ]
                    continue
                log.warning("AI %s validation failed after repair: %s", tool_name, exc)

        self._mark_degraded(f"{tool_name} unavailable")
        return None

    # -- telemetry / degraded -------------------------------------------
    def _log_cost(self, tool_name, model, usage) -> None:
        if usage is None:
            return
        tin = getattr(usage, "input_tokens", 0) or 0
        tout = getattr(usage, "output_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
        pin, pout = _PRICES.get(model, (0.0, 0.0))
        cost = (tin * pin + tout * pout + cache_r * pin * 0.1) / 1_000_000
        self._cost += cost
        self._calls += 1
        log.info("AI[%s] in=%d out=%d cache_r=%d ~$%.4f | game: $%.4f over %d calls",
                 tool_name, tin, tout, cache_r, cost, self._cost, self._calls)

    def _mark_degraded(self, reason: str) -> None:
        self.degraded = True
        self.degraded_reason = reason
        log.warning("AI degraded → fallback: %s", reason)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _image_block(data_url: str) -> dict | None:
    data = (data_url or "").strip()
    if not data:
        return None
    media_type = "image/png"
    if data.startswith("data:"):
        header, _, b64 = data.partition(",")
        if ";" in header:
            media_type = header[5:].split(";", 1)[0] or media_type
        data = b64
    if not data:
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def _tool_input(resp) -> dict | None:
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    return None


def _roster_text(state: GameState, round_num: int) -> str:
    lines = [f"Round {round_num}. Adapt, never reject — a target may fall to a faster teammate "
             "earlier this round. Living fighters (current state):"]
    team_of = {pid: t.id for t in state.teams for pid in t.player_ids}
    for pid, ch in state.characters.items():
        if ch.is_ko:
            continue
        conds = ",".join(ch.conditions) or "none"
        lines.append(f"- {ch.name} ({pid}) team={team_of.get(pid, '?')} zone={ch.zone_id} "
                     f"hp={ch.hp}/{ch.max_hp} conditions={conds}")
    return "\n".join(lines)


def _narration_text(events: list[Event], characters: dict[str, Character],
                    gallery_names: list[str] | None = None) -> str:
    def nm(pid):
        return characters[pid].name if pid and pid in characters else "someone"
    who = "; ".join(f"{c.name}: {c.personality}" for c in characters.values() if c.personality)
    lines = [f"Fighters — {who}", "",
             "Resolved events (narrate these; tag each beat with its event_id):"]
    for e in events:
        lines.append(json.dumps({
            "event_id": e.id, "type": e.type.value,
            "actor": nm(e.player_id), "target": nm(e.target_id), "data": e.data,
        }))
    if gallery_names:
        lines += ["", "Spectators in the stands (past fighters — you MAY cameo one): "
                  + ", ".join(gallery_names)]
    return "\n".join(lines)


def _awards_text(summary: MatchSummary) -> str:
    lines = [f"Match over. Winning team: {summary.winner_team_id or 'nobody (draw)'}.",
             "Give EVERY player below at least one affectionate award.", "", "Players:"]
    for p in summary.players:
        pid = p["player_id"]
        lines.append(
            f"- {p.get('name', pid)} ({pid}) team={p.get('team_id')} "
            f"alive={p.get('alive')} creativity={summary.creativity.get(pid, 0)} "
            f"fumbles={summary.fumbles.get(pid, 0)}"
        )
    combo_names = [c.get("combo_name", "") for c in summary.combos if c.get("combo_name")]
    if combo_names:
        lines.append("Combos pulled off: " + "; ".join(combo_names))
    if summary.round_titles:
        lines.append("Round titles: " + " | ".join(summary.round_titles))
    if summary.best_line:
        lines.append(f"Best narrated line: {summary.best_line}")
    return "\n".join(lines)


def _fallback_narration(events: list[Event], characters: dict[str, Character]) -> Narration:
    beats = [Beat(event_id=e.id, text=t, speaker=_mock_speaker(e))
             for e in events if (t := _beat_text(e, characters))]
    if not beats:
        beats = [Beat(event_id="filler", text="The crowd blinks. Something happened, probably.")]
    return Narration(beats=beats, round_title=_mock_round_title(events))
