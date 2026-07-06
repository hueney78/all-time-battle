# Doodle Brawl — Implementation Plan

This plan is written to be handed to Claude Code phase by phase. Claude Code does everything except the short manual list in §1. Each phase ends with **acceptance criteria** and a **human demo checkpoint** — do the checkpoint before starting the next phase so problems surface early.

Companion docs (put all three in the repo root; Claude Code should read them first):
- `ARCHITECTURE.md` — components, directory layout, protocols
- `GAME_DESIGN.md` — rules, schemas, config formats, golden-test numbers

## 0. Ground Rules for Claude Code (paste into CLAUDE.md)

```markdown
# CLAUDE.md
- Read ARCHITECTURE.md and GAME_DESIGN.md before writing code. They are the spec.
- The engine (server/engine/) must stay pure: no I/O, no AI calls, no wall-clock,
  injected RNG only. Every mechanic change needs/updates a unit test.
- All tunable values load from config/*.yaml — never hardcode a number that a
  designer might tune (timers, bonuses, HP math, thresholds, model IDs).
- Zones, conditions, and moves are data-driven registries. Adding one must
  require zero Python changes. If a task seems to require code for a new
  zone/condition/move, stop and fix the registry instead.
- AI responses are pydantic-validated with one repair retry and a non-AI
  fallback. The game must never deadlock waiting on the API.
- AI_MODE=mock must always work end-to-end with fixtures — a full playable
  game with no API key.
- Frontend: vanilla HTML/JS, no build step, no frameworks.
- Run `pytest` after every change; keep it green. Prefer small commits with
  descriptive messages, one feature per commit.
- Python 3.11+, type hints everywhere, ruff for lint/format.
```

## 1. Manual Steps (human — ~15 minutes total)

1. Install Python 3.11+ and `uv` (or pip). Install Claude Code. Create empty repo/folder, drop in the three docs.
2. Create an Anthropic API key at platform.claude.com → copy `.env.example` to `.env` and paste it in (Phase 5 onward; Phases 1–4 need no key).
3. When first running with phones: ensure computer and phones share Wi-Fi; allow Python through the OS firewall when prompted; note the LAN IP the server prints.
4. Playtest at each checkpoint and tune `config/balance.yaml` by feel. This is the fun part — it's yours.

Everything else below is Claude Code's job.

## 2. Phase 1 — Scaffold, Config, Models (foundation)

Tasks:
- `pyproject.toml` (fastapi, uvicorn, pydantic v2, pyyaml, jinja2, anthropic, httpx, pytest, ruff), directory tree per ARCHITECTURE.md, `.env.example`, README with run instructions.
- Typed config loaders: `settings.yaml`, `balance.yaml`, `zones.yaml`, `conditions.yaml`, `moves.yaml` → pydantic settings objects with validation (bad YAML = clear startup error naming the file/key). Ship the default files exactly as specified in GAME_DESIGN.md §4.1 and §6–7 plus a commented `balance.yaml` containing every knob in the design doc.
- Engine models: `GameState`, `Team`, `Character`, `ClassifiedAction`, `Event`, `RoundResult`.
- `dice.py`: seeded RNG wrapper with `d20()`, `roll(spec)`.

Acceptance: `pytest` green on config-loading tests (including a test that a High Ground zone block added to zones.yaml loads and exposes its modifiers); `uv run uvicorn server.main:app` serves a hello page.
**Checkpoint:** human opens the hello page, skims `balance.yaml`, confirms knob names make sense.

## 3. Phase 2 — Pure Game Engine + Golden Tests

Tasks:
- `conditions.py` / `zones.py` / `moves.py` registries reading YAML generically (modifier lookup API per ARCHITECTURE.md §4.3). Every classified action resolves through its `moves.yaml` entry.
- `resolver.py`: initiative, action costs & banking, attack resolution with degrees of success (GAME_DESIGN.md §5), combo fusion per §8 (both rounds consumed, one roll with combo bonus + escalated creativity, summed cost-scaled damage, crit doubles the total), creativity/stale modifiers, movement legality, condition apply/tick/expiry/interactions, KO/Gremlin, victory, sudden death.
- Emits ordered `Event` list with stable IDs.
- Tests: unit tests per mechanic; **golden test** reproducing GAME_DESIGN.md §12 (seed 42, exact final HPs); property test (resolver never yields negative HP, never references unknown IDs); a test proving High Ground modifiers apply when the zone exists in config; a test proving a **novel move added only to moves.yaml** resolves correctly; a combo-EV sanity test (combo expected damage > two separate attacks, given equal inputs).

Acceptance: `pytest` green; coverage of `engine/` ≥ 90%.
**Checkpoint:** Claude Code writes a tiny CLI (`python -m server.engine.demo`) that runs 3 scripted rounds and prints events; human reads it and sanity-checks the math.

## 4. Phase 3 — Server, Rooms, WebSockets

Tasks:
- Room lifecycle with 4-letter codes; join/reconnect via localStorage `player_id`; roles player/host; message protocol per ARCHITECTURE.md §4.1 (typed pydantic, versioned envelope).
- State machine skeleton: LOBBY → DRAW_CHARACTERS → round loop → GAME_OVER, with timers and auto-submit; pipeline orchestration via asyncio.gather per §4.2 (AI calls stubbed to instant mocks for now).
- Snapshot writer (JSON per round) + `AI_MODE=mock` fixtures.
- Tests: state-machine transitions with fake clocks; reconnect mid-phase; two simulated websocket clients complete a full mock game.

Acceptance: an automated test plays a full 4-player mock game to victory over websockets.
**Checkpoint:** human opens host page + two browser tabs as players, joins a lobby, sees phases advance (placeholder UI is fine).

## 5. Phase 4 — Phone & Host UI

Tasks:
- Player page: join form, team-colored theme, drawing canvas with pen (3 widths, 8 colors), **erasers in multiple sizes**, undo, clear; 512px PNG export, submit + auto-submit, status card (HP hearts, condition emojis, banked actions, round indicator), reconnect banner.
- Character creation screen: canvas + **hint word/phrase text field** (no name entry — the AI names characters).
- **Draw-on-top action canvas:** each action round, preload the canvas with the player's original character image (from `canvas_init`); add a **"restore character" button** that resets the canvas to that image; players can erase any or all of it. Auto-submit sends the canvas as-is (unmodified character = comedic idle).
- Host page: lobby with QR code (LAN URL), arena renderer (zones as bands, character PNGs as bobbing sprites), reveal sequencer (typewriter beats synced to event IDs, HP tweens, condition icons, KO animation, victory screen), "next beat" override button.
- **Host drawing-phase view:** render the current arena state — background + each original character sprite in its current zone with HP/conditions — whenever players are drawing.
- **Reveal sprite-swap:** while a character's beat plays, swap its arena sprite to that round's action image (delivered in `reveal_step`), reverting to the original character image when the beat ends.
- Shared reconnecting-websocket helper.

Acceptance: full mock-mode game playable by humans on two phones + TV; action canvases start prefilled with the character and the restore button works; the TV shows the live arena during draws and swaps sprites to action images during reveals.
**Checkpoint (the big one):** human plays a real couch game in mock mode with family. Note UI friction for Phase 7.

## 6. Phase 5 — AI Integration

Tasks:
- `ai/client.py`: anthropic SDK; `claify_actions`/`generate_characters` on `claude-haiku-4-5`, `narrate_round` on `claude-sonnet-4-6` (model IDs in settings.yaml); forced tool-use for structured output; 20s timeout; retry-with-error repair; prompt caching for stable rule text; per-game token/cost logging line.
- Prompt templates per GAME_DESIGN.md §11.3, injecting zones/conditions/rules from config so YAML edits automatically reach the AI. Include the **comedy mandate** in the narrator template (no plain "A punches B" — every beat gets a comedic specific; misses/fumbles escalate; callbacks encouraged; mock situations, never drawing skill).
- `generate_characters`: input is drawing + **player hint phrase**; output includes the **AI-generated funny name** (grand names for elaborate drawings, deadpan names like "Tim" for bland ones).
- `classify_actions`: send per-player **character/action image pairs** with labels; inject the **move catalog with plain-language descriptions** so every drawing (including spell-like ones — eye lasers → `ray`, radiating lines → `burst`) maps to a `catalog_id`; prompt instructs the AI to classify the *difference* between the images, treat erasures as meaningful, and interpret a fully erased character as `hide` or `stumble` — never reject a drawing.
- Validators: unknown targets/conditions remapped per stale-intent rules; `flagged` handling (censor sprite + AI-chosen tame name; covers both drawings and hint text).
- **Wildcard logging:** append every `wildcard` classification to `snapshots/<room>/wildcards.jsonl` (round, action PNG path, adaptation_note) so the human can mine playtests for new catalog archetypes.
- Fallback path (neutral classification + template narration) with a visible host banner.
- Tests: schema validation against recorded fixtures; repair-retry path; fallback path; a `scripts/ai_smoke.py` that sends one fixture drawing live and prints the parsed result + cost.

Acceptance: mock tests green; live smoke test returns valid classification for fixture PNGs.
**Checkpoint:** human sets `AI_MODE=live`, runs smoke script, then plays one full live game. Verify per-game cost printed (~$0.10–0.50).

## 7. Phase 6 — Full Pipeline & Game Feel

Tasks:
- Wire the real pipeline: draw r+1 ‖ process r ‖ reveal r−1, including the T2/T3 special cases (characters process during Round 1 drawing; intros reveal during Round 2 drawing).
- Arena Gremlin flow; combo fusion display ("COMBO!" splash + combo_name); underdog rubber-banding; sudden death.
- Latency masking polish: "fighters scheming…" fillers, reveal never shows a spinner.
- Tests: pipeline ordering under slow-AI simulation (inject 15s delay — game must not stall or reveal out of order).

Acceptance: slow-AI test green; full live 6-player game runs without a visible wait.
**Checkpoint:** family playtest #2, live AI. Tune balance.yaml afterward.

## 8. Phase 7 — Polish & Stretch (pick from playtest notes)

Candidates, in rough value order: sound effects; better sprite presentation (drop shadows, hit shake); spectate page for extra devices; AI-controlled filler fighter for odd counts; replay viewer reading snapshots; **persistent summoned companions** (upgrade `summon` from one-shot strike to a pet with HP and turns, if the kids demand it); new `moves.yaml` archetypes mined from the wildcard log; tournament mode (best of 3); export "match report" (the narrative as a shareable page); High Ground zone enabled by default once tested.

## 9. Testing Strategy Summary

| Layer | Method |
|---|---|
| Engine | Unit + golden (seeded) + property tests; ≥90% coverage |
| Registries | Load-from-YAML tests incl. novel zone/condition blocks |
| State machine | Fake clock, simulated clients, full mock games |
| AI layer | Fixture-based schema tests; repair & fallback paths; live smoke script |
| End-to-end | Scripted mock game over real websockets in CI; human couch playtests at checkpoints |

## 10. Kickoff Prompt (paste into Claude Code to start)

```
Read ARCHITECTURE.md, GAME_DESIGN.md, and IMPLEMENTATION_PLAN.md in this
directory. Create CLAUDE.md with the ground rules from the plan's §0. Then
execute Phase 1 exactly as specified: scaffold the project, config system,
engine models, and dice module, with tests. Stop at the Phase 1 acceptance
criteria and tell me how to run the demo checkpoint.
```

Then proceed one phase at a time: "Execute Phase 2 per IMPLEMENTATION_PLAN.md."
Resist doing multiple phases in one shot — the checkpoints exist to catch
design drift while it's cheap.
