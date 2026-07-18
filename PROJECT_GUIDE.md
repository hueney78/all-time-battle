# Doodle Brawl — Project Guide (Maintenance Mode)

The game is **built and playable**. This guide is for keeping it healthy and changing it safely — not for building it from scratch. The living specs are `GAME_DESIGN.md` (what the game is) and `ARCHITECTURE.md` (how it's put together); they are the source of truth, and every change flows through them first.

---

## 1. How to make a change (the loop)

This is the rhythm that has worked all along, and it still applies now that the game is done:

1. **Edit the specs first.** Update `GAME_DESIGN.md` and/or `ARCHITECTURE.md` (and the mockups in `design/` if it's a UI change) to describe the new desired behavior. Commit that on its own as a `spec:` commit.
2. **Fresh Claude Code session.** Start a new session (don't resume an old one — you want it reading the current specs, not remembering an older shape).
3. **Gap list before code.** Ask it to read the updated specs and produce a categorized gap list of what the change touches *before* editing anything. Approve or correct the plan.
4. **Implement one commit at a time**, keeping `pytest` green throughout.
5. **Playtest**, then tune `config/balance.yaml` by feel. That part's yours.

A ready-to-paste change prompt lives in §6.

---

## 2. Ground rules (`CLAUDE.md`)

These live in `CLAUDE.md` at the repo root so every session inherits them:

```markdown
# CLAUDE.md
- GAME_DESIGN.md and ARCHITECTURE.md are the spec. Read them before changing code.
- The engine (server/engine/) stays pure: no I/O, no AI calls, no wall-clock,
  injected RNG only. Every mechanic change needs/updates a unit test.
- All tunable values load from config/*.yaml — never hardcode a number a
  designer might tune (timers, bonuses, HP math, thresholds, model IDs).
- Zones and moves are data-driven registries. Adding one requires zero Python
  changes; if a task seems to need code for a new zone/move, fix the registry.
- AI responses are pydantic-validated with one repair retry and a non-AI
  fallback. The game must never deadlock waiting on the API.
- AI_MODE=mock must always work end-to-end with fixtures — a full playable
  game with no API key. Keep fixtures in sync when schemas change.
- Frontend: vanilla HTML/JS, no build step, no frameworks. The files in
  design/ are the visual contract for the host and player screens.
- Run pytest after every change; keep it green. Small commits, one change each.
- Two people work this repo. Branch from latest main; never commit to main
  directly. Flag any change to protocol.py, ai/schemas.py, or websocket
  payload shapes in the PR description — those are cross-cutting contracts.
- Python 3.11+, type hints everywhere, ruff for lint/format.
```

---

## 3. Working as two people

Ownership splits cleanly along the architecture's seam: **server/engine/AI** vs **presentation (host + player screens)**. That boundary keeps two people (each directing their own Claude Code sessions) out of each other's way.

- **Guarded files — coordinate before merging:** `server/protocol.py`, `server/ai/schemas.py`, and any `reveal_step` / `player_state` / `submit_action` payload shape. These are the only places the two halves can break each other. If a change touches them, the server side lands the schema change **plus updated mock fixtures** first; the presentation side then builds against `AI_MODE=mock` and is never blocked.
- **Branching:** short-lived branches, PRs reviewed by the *other* person's Claude Code session against the specs ("review this diff against GAME_DESIGN.md/ARCHITECTURE.md; flag spec deviations, hardcoded values, and engine-purity violations"), CI (`pytest` + `ruff check`) as the merge gate — note the codebase uses hand-aligned inline comments, so `ruff format` is intentionally **not** gated (running it would flatten that alignment repo-wide); don't mass-reformat. One GitHub Issue per task.
- **Keep sessions scoped to one task.** Two agents each producing sweeping diffs on divergent branches is how you get painful merges; two agents each doing one focused task merge cleanly. `git pull` main before branching.
- **Version parity:** if one of you sees weird behavior the other doesn't, compare `claude --version` first.

---

## 4. Testing & balance

| Layer | How it's protected |
|---|---|
| Engine | Unit + golden (seeded) tests; the current golden test is `test_v5_golden` (exact HPs for the §12 fixture). Property tests: no negative HP, no unknown IDs, KO'd characters never act, resolution halts on team wipe. |
| Registries | Load-from-YAML tests, including a novel zone/move added only in config. |
| State machine | Fake-clock transition tests; a full mock-mode game over real websockets in CI. |
| AI layer | Fixture-based schema tests; repair-retry and fallback paths; a live smoke script. |
| Balance | `scripts/balance_sim.py` (see §5) — Monte Carlo run + specialist round-robin; treat it as a regression test for any combat/stat/config change. |

**Golden rule for balance changes:** run the sim before *and* after. A move's ablation win-rate should stay in a tight band around others, and the specialist round-robin should keep every stat viable (no dump stat).

---

## 5. The balance simulator

`scripts/balance_sim.py` is a fast **standalone model** of the combat math — it reads the real `config/*.yaml` but does not import the engine — and prints per-move win-rate/ablation plus a specialist archetype round-robin. Its companion `scripts/balance_engine.py` runs the same matchups through the **real** resolver; when the two disagree, `balance_engine.py` is authoritative. Use them whenever you touch a formula, a stat, or a `balance.yaml` knob. Current healthy baseline: five moves in a ~0.44–0.55 ablation band; Speed > Power > Weird > Speed rock-paper-scissors; balanced builds beat Power/Weird but lose to Speed; zones actively used (~57% of actions away from home).

The `snapshots/<room>/flavor.jsonl` log (if present, mined by `scripts/mine_flavor.py`) and playtest notes are the other tuning inputs — mine them for what feels off in real games.

---

## 6. Change-request prompt (paste into a fresh session)

```
The specs (GAME_DESIGN.md, ARCHITECTURE.md) and the design/ mockups have
been updated. They are the source of truth for how the game should now
behave.

Step 1: Read the updated specs and the relevant mockup(s). Produce a
categorized gap list (engine / server / player UI / host UI / config) of
everything this change touches — BEFORE editing code. Note anything that
REPLACES existing behavior, and anything that touches a guarded file
(protocol.py, ai/schemas.py, or a websocket payload shape).

Step 2: After I approve the gap list, implement one commit at a time,
keeping pytest green. If this is a balance/stat/formula change, run
scripts/balance_sim.py before and after and show me the comparison.

Step 3: List anything you noticed that drifted from the specs but was
out of scope for this change, so I can decide whether to file it.
```

For pure balance tuning, add: *"This is a tuning-only change — edit `config/balance.yaml` and re-run the sim; do not change engine code unless the sim reveals a real bug."*

---

## 7. Config quick-reference

Everything a designer tunes lives in `config/`, editable without touching code (a new room picks up changes):

- `settings.yaml` — timers, ports, model IDs, splash/reveal durations, sfx maps, arena background, phase-splash text, lobby rules copy.
- `balance.yaml` — HP formula, move effect formulas, creativity bonus values, PROTECT reflect %/cap, trap damage, sudden-death, underdog and combo bonuses.
- `moves.yaml` — the five-move catalog and its math (registry-driven).
- `zones.yaml` — the arena graph and per-zone modifiers (the "High Ground" extensibility test).
- `lore.yaml` — optional family in-jokes (terms + definitions) the AI sprinkles into commentary; empty = off.
- `prompts/*.j2` — narrator (announcer duo), character generation, montage, awards, classify. Rules/zone/lore data are injected automatically.

---

## Appendix A — Combat design history (why it's shaped this way)

Recorded so nobody re-litigates settled decisions or re-adds removed systems. The **current** design is always what `GAME_DESIGN.md` says; this is just the trail.

- **v1 — PF2e-style (retired).** 30-move catalog, AI-classified from drawings, 3-action economy with banking, d20 + AC + degrees of success, status conditions, "draw one round ahead" pipeline, stale-intent adaptation. *Playtest verdict:* too much to follow; misclassification made intended moves unreliable; most moves forgettable; the pipeline confused people who wanted their move shown immediately.
- **v2 — tapped moves.** Players tap a move + target; the drawing supplies creativity/flavor/combos only. Sequential rounds (draw → deliberation interlude → reveal), no more pipeline. 2d6 + stat, stats 0–6.
- **v2.1.** TRICK → SHOOT; SHIELD became zone-wide; character intros moved *before* round 1; **status conditions removed entirely** (complicated play, bloated announcing).
- **v4 — no rolls.** Removed AC and to-hit entirely: every selected move lands. Effectiveness = stat base + flat creativity (+0/+1/+3/+5). Crits/fumbles replaced by creativity-tier-3 "DEVASTATING" spike moments; only passive dodge/shield could reduce a hit. Announcers barred from ever mentioning dice/rolls/AC.
- **v5 — current.** Five single-target moves (SMASH/BLAST/CHARGE/ESCAPE/PROTECT), no AOE, **no dodge**. `HP = 27 + 2×POW + WRD + ⌊SPD/2⌋` (Speed needed survivability once dodge was gone). PROTECT's reflect shield is the only damage reduction; it always acts first. Gremlins plant persistent zone traps on a blank canvas. Sim-verified rock-paper-scissors with no dump stat. See GAME_DESIGN §§3–5 for the authoritative rules.

**Settled — do not silently revert:** no status conditions; no attack rolls or AC; no AOE/multi-target; no "draw ahead" pipeline; announcers never reference mechanics; AI never chooses a player's move or target (the phone tap is ground truth); character names capped at two words.

## Appendix B — Known hardening items

- **Character-submission race (watch):** on slow devices a character drawing can arrive after the roster is snapshotted, producing a phantom "mysterious blob" that never acts. The fix direction (see ARCHITECTURE room/submission notes): the collection barrier waits for all players with a generous timeout and a server ack + client retry, and re-prompts a missing drawing instead of fabricating a placeholder. Never invent a character that can't act.
