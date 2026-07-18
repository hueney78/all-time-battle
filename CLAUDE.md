# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Doodle Brawl** — a local-network couch party game where players sketch heroes on phones, an AI assigns stats and writes narration, and a deterministic server engine resolves all mechanics. 2–6 players, Jackbox-style (phones + one shared TV browser).

Read `ARCHITECTURE.md` and `GAME_DESIGN.md` before writing code. They are the spec.

## Commands

```bash
# Install dependencies
uv sync                          # or: pip install -e .[dev]

# Run the server (LAN: open http://<local-ip>:8000)
uv run uvicorn server.main:app --reload

# Run all tests
pytest

# Run a single test
pytest tests/test_resolver.py::test_v4_golden

# Lint / format
ruff check .
ruff format .

# Offline dev (no API key needed)
AI_MODE=mock uv run uvicorn server.main:app --reload

# AI smoke test (requires .env with ANTHROPIC_API_KEY)
python scripts/ai_smoke.py

# Engine demo (scripted 3-round CLI output)
python -m server.engine.demo

# Balance: through the real resolver (authoritative) / the standalone v4 model
python scripts/balance_engine.py
python scripts/balance_sim.py
```

Copy `.env.example` → `.env` and add `ANTHROPIC_API_KEY` for live AI mode.

## Architecture

**Core principle:** the AI judges (classifies drawings, writes narration), the server does all math. The engine never calls the AI; the AI never touches HP or dice.

**Second principle:** every tunable value lives in `config/*.yaml`. Adding a zone, hazard, or move archetype must require zero Python changes.

### Key components

- **`server/engine/resolver.py`** — pure function `resolve_round(state, actions, rng, cfg) → RoundResult`. No I/O, no AI, no globals. Injected seeded RNG only. This is the maintainability core; every mechanic change needs a unit test. **COMBAT V5 resolution: there is no AC, no attack roll, and no dodge — every selected move lands.** Effect = the move's damage/heal formula + a flat creativity bonus (`+0/+1/+3/+5`). The **only** thing that reduces a hit is **PROTECT**'s reflect shield: it absorbs `reflect_per_weird × caster's Weird` (cap `reflect_cap`) of the incoming damage and bounces exactly that much back at the attacker. Initiative is **PROTECT casters first, then Speed** (seeded tie-break); a character reduced to 0 HP **forfeits an already-tapped action** — the loop re-checks `is_ko` before every action. Movement lives inside **CHARGE** (rush into the target's zone) and **ESCAPE** (slip one zone by `escape_direction`). The spike moment is **creativity tier 3 → `result: "devastating"`**, not a random crit.

- **`server/engine/`** — registries (`zones.py`, `moves.py`) load from YAML generically. Every tapped action resolves through its `moves.yaml` entry (stat, range, targeting, damage/heal formula, riders like `same_zone_penalty`/`moves_to_target`) — no if-statements for individual moves. There is **no condition system** (removed in v2.1), **no AC** (removed in v4), and **no dodge/SHIELD/WILD/hazard registry** (removed in v5). PROTECT's shield is round-local resolver state, never persisted; Arena Gremlin **traps** persist in `GameState.traps` until triggered. Zone modifiers are `damage_bonus` / `incoming_damage_bonus` / `heal_bonus`.

- **`server/ai/`** — Claude calls: `generate_characters` (Haiku, once — also returns AI team names), `classify_actions` (Haiku, per round), `narrate_round` (Sonnet, per round). Responses validated by pydantic with one repair retry; on total failure the tapped move still resolves at creativity 0 with template narration. **The game never deadlocks on the API.**

- **`server/state_machine.py`** — character intros play **before Round 1 drawing** (INTROS phase: drumroll interstitial masks `generate_characters`, then giant-sprite intro beats + team-name reveal), then the sequential round loop (draw → deliberation interlude → reveal) plus server-side tap validation (no-repeat, edge legality, living targets) for `submit_action`.

- **`config/moves.yaml`** — COMBAT V5: exactly **five tapped moves**, all single-target — SMASH (melee same-zone, `2d4 + POW + 2`), BLAST (ranged any-zone, `2d4 + WRD + 2`, half point-blank, always legal), CHARGE (moves to target, `2d4 + avg(POW,SPD)`, always legal), ESCAPE (slip one zone by `escape_direction`, `2d4 + SPD`), PROTECT (heal `1d6 + WRD` + a reflect shield, `acts_first`). Movement lives inside CHARGE/ESCAPE — there are no movement buttons. **Formulas must never spell out creativity** — §5 makes it a system rule the resolver adds to every damage/heal, so a formula naming `CRE` would double-count. Moves are tapped on the phone, never classified from drawings; the drawing supplies creativity, flavor, and combos only. The AI's per-drawing flavor reads are logged to `snapshots/<room>/flavor.jsonl` for data-driven archetype additions.

- **Balance scripts** — `scripts/balance_engine.py` drives the **real** resolver (round-robin, move ablation, invariant report); `scripts/balance_sim.py` is a fast standalone *model* that never imports the engine and may diverge on purpose. When they disagree, `balance_engine.py` is the game. The standalone sim shows a clean rock-paper-scissors (Speed>Power>Weird>Speed); the **real engine broadly agrees** (Speed edges Power, Power beats Weird, a balanced 3/3/3 beats Power/Weird but loses to Speed) with all invariants clean, **but under uniform-random play Speed still leans strong and PROTECT ablates ~0.40** — an open tuning item, not a reconciliation bug (levers: `reflect_per_weird`/`reflect_cap`, `hp_speed_divisor`). See GAME_DESIGN §3/§4.1.

- **`web/`** — vanilla HTML/JS, no framework, no build step. Edit and refresh. Clients are dumb renderers; server is source of truth.

## Invariants

- Engine (`server/engine/`) must stay pure: no I/O, no AI calls, no wall-clock, injected RNG only. Every mechanic change needs/updates a unit test.
- All tunable values load from `config/*.yaml` — never hardcode a number a designer might tune (timers, bonuses, HP math, thresholds, model IDs).
- Zones, moves, and hazards are data-driven registries. If a task seems to need code for a new zone/move/hazard, fix the registry instead.
- `AI_MODE=mock` must always work end-to-end with fixtures — a full playable game with no API key.
- Run `pytest` after every change; keep it green.
- Python 3.11+, type hints everywhere, ruff for lint/format.

## AI Models

Per `config/settings.yaml` (never hardcode):
- Classification (`classify_actions`, `generate_characters`): `claude-haiku-4-5`
- Narration (`narrate_round`): `claude-sonnet-4-6`

Prompt templates live in `config/prompts/*.md.j2` (Jinja2). Stable rules text is sent with `cache_control` (prompt caching) to reduce costs ~90% on repeated calls.

## Testing

| Layer | Approach |
|---|---|
| Engine | Unit + golden tests (seed 42, exact HP from GAME_DESIGN.md §12) + property tests; ≥90% coverage |
| Registries | Load-from-YAML tests including novel zone/move/hazard blocks |
| State machine | Fake clock, simulated clients, full mock games |
| AI layer | Fixture-based schema tests; repair & fallback paths |

Golden test numbers: `tests/test_resolver.py::test_v5_golden` — the GAME_DESIGN.md §12 fixture (seed 42): Stabby 21, Blob 26, Lawnmower 39, Gerald 38. §12 lists the *actual* seed-42 dice, so doc and test agree; the test's narrative comment explains the round beat by beat. A companion test (`test_ko_before_slot_forfeits_the_tapped_action`) asserts a fighter KO'd before its initiative slot never resolves its tapped move.

Determinism aid for engine tests: PROTECT resolves in a fixed slot (always first) and the reflect share is a flat percentage (no probability draw), so most unit tests isolate a single mechanic by controlling stats and seed directly. `avg(POW,SPD)` is a floor average — CHARGE's headline stat.
