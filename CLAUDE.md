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
pytest tests/test_resolver.py::test_v2_golden

# Lint / format
ruff check .
ruff format .

# Offline dev (no API key needed)
AI_MODE=mock uv run uvicorn server.main:app --reload

# AI smoke test (requires .env with ANTHROPIC_API_KEY)
python scripts/ai_smoke.py

# Engine demo (scripted 3-round CLI output)
python -m server.engine.demo
```

Copy `.env.example` → `.env` and add `ANTHROPIC_API_KEY` for live AI mode.

## Architecture

**Core principle:** the AI judges (classifies drawings, writes narration), the server does all math. The engine never calls the AI; the AI never touches HP or dice.

**Second principle:** every tunable value lives in `config/*.yaml`. Adding a zone, condition, or move archetype must require zero Python changes.

### Key components

- **`server/engine/resolver.py`** — pure function `resolve_round(state, actions, rng, cfg) → RoundResult`. No I/O, no AI, no globals. Injected seeded RNG only. This is the maintainability core; every mechanic change needs a unit test. COMBAT V2 resolution: 2d6 + stat + creativity vs AC (10 + Speed); crit on natural 12 or margin ≥ 5; fumble on natural 2.

- **`server/engine/`** — registries (`conditions.py`, `zones.py`, `moves.py`) load from YAML generically. Every tapped action resolves through its `moves.yaml` entry (stat, range, targeting, damage formula, riders). The resolver queries `registry.modifier(target, "attack_bonus")` — no if-statements for individual moves.

- **`server/ai/`** — Claude calls: `generate_characters` (Haiku, once — also returns AI team names), `classify_actions` (Haiku, per round), `narrate_round` (Sonnet, per round). Responses validated by pydantic with one repair retry; on total failure the tapped move still resolves at creativity 0 with template narration. **The game never deadlocks on the API.**

- **`server/state_machine.py`** — sequential round loop (draw → deliberation interlude → reveal) plus server-side tap validation (no-repeat, edge legality, living targets) for `submit_action`.

- **`config/moves.yaml`** — COMBAT V2: exactly **eight tapped moves** (SMASH/BLAST/TRICK/SHIELD/RALLY/WILD CARD + ◀/▶ movement), each owning stat-parameterized formulas like `(1 + ceil(POW/2))d4 + 2`. Moves are tapped on the phone, never classified from drawings; the drawing supplies creativity, flavor, TRICK's condition, WILD CARD's interpretation, and combos only. WILD plays are logged to `snapshots/<room>/wildcards.jsonl` for data-driven archetype additions (`scripts/balance_sim.py` checks balance).

- **`web/`** — vanilla HTML/JS, no framework, no build step. Edit and refresh. Clients are dumb renderers; server is source of truth.

## Invariants

- Engine (`server/engine/`) must stay pure: no I/O, no AI calls, no wall-clock, injected RNG only. Every mechanic change needs/updates a unit test.
- All tunable values load from `config/*.yaml` — never hardcode a number a designer might tune (timers, bonuses, HP math, thresholds, model IDs).
- Zones, conditions, and moves are data-driven registries. If a task seems to need code for a new zone/condition/move, fix the registry instead.
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
| Registries | Load-from-YAML tests including novel zone/condition blocks |
| State machine | Fake clock, simulated clients, full mock games |
| AI layer | Fixture-based schema tests; repair & fallback paths |

Golden test numbers: `tests/test_resolver.py::test_v2_golden` — the GAME_DESIGN.md §12 fixture (seed 42): Stabby 22, Blob 20, Lawnmower 22, Gerald 17. The doc's example dice are illustrative; the test asserts the actual seeded rolls (documented in the test's narrative comment).
