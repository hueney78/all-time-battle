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
- Two people direct sessions on this repo. Branch from latest main; never
  commit directly to main. Flag any change to protocol.py, ai/schemas.py, or
  websocket payload shapes in the PR description — those are cross-track
  contracts (see plan section 7).
- Python 3.11+, type hints everywhere, ruff for lint/format.
```

## 1. Manual Steps (human — ~15 minutes total)

1. Install Python 3.11+ and `uv` (or pip). Install Claude Code. Create empty repo/folder, drop in the three docs.
2. Create an Anthropic API key at platform.claude.com → copy `.env.example` to `.env` and paste it in (Phase 5 onward; Phases 1–4 need no key).
3. When first running with phones: ensure computer and phones share Wi-Fi; allow Python through the OS firewall when prompted; note the LAN IP the server prints.
4. Playtest at each checkpoint and tune `config/balance.yaml` by feel. This is the fun part — it's yours.
5. (Optional, anytime) The arena ships as a CSS-drawn colosseum by default — no art needed. If you ever want custom arena art, drop an image into `web/host/assets/` and set `arena_background` in settings.yaml.

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
- Player page: join form, team-colored theme, drawing canvas with pen (3 widths, 8 colors), **erasers in multiple sizes**, undo, clear; 512px PNG export, submit + auto-submit, status card (stats 💪/⚡/🌀 with change-pulse, HP hearts, condition emojis, banked actions, round indicator), reconnect banner.
- Character creation screen: canvas + **hint word/phrase text field** (no name entry — the AI names characters).
- **Draw-on-top action canvas:** each action round, preload the canvas with the player's original character image (from `canvas_init`) **rendered at ~50% scale** (config `action_canvas_character_scale`), positioned on the player's team's side, with a per-team **orientation ribbon** ("your side ⟵ ⟶ enemies") matching the TV layout; add a **"restore character" button** that resets the canvas to that scaled image; players can erase any or all of it. Auto-submit sends the canvas as-is (unmodified character = comedic idle).
- Host page: lobby with QR code (LAN URL), arena renderer (zones as bands over the **default CSS-drawn colosseum**; optional `arena_background` image override in settings.yaml, assets in `web/host/assets/`), reveal sequencer (typewriter beats synced to event IDs, HP tweens, condition icons, KO animation, victory screen), "next beat" override button. **Visual reference: `design/mockup_host_screen.html` and `design/mockup_player_screen.html` are the layout/hierarchy contract for the host and player pages** — match their structure and annotated behaviors; the implementation replaces static content with live state.
- **Canvas background color:** the drawing canvas background (and eraser fill) is the shared `canvas_background_color` token matching the arena floor, so submitted PNGs blend into the battlefield.
- **Persistent action sprites:** a character's revealed action drawing becomes their battlefield sprite and persists until their next action replaces it; original character image until first action. This is the arena's resting state during drawing phases too.
- **Reveal presentation:** when a character's beat plays, zoom their action drawing up by `reveal_action_zoom_scale` for `reveal_action_zoom_seconds`, then settle to sprite size. Characters negatively impacted by the beat's events get a **red border + shake** animation; positively impacted get a **light-blue border + scale pop** — derive both from event types, not narration text.
- **Floating combat numbers:** spawn red (damage) / green (healing) numbers that float up and fade from the affected sprite on each damage/heal event (`float_number_seconds` config); crits render oversized.
- **Initiative Order rail:** left-side vertical column of original character portraits in the revealed round's acting order, each with a compact **stat strip (💪/⚡/🌀, pulsing on change)**, with animated reordering when initiative changes and removal on KO (Gremlin badge at rail bottom). Server-side: expose the resolver's initiative order on `RoundResult` and include it in `reveal_step`.
- **Tug-of-war meters:** two cartoony rope-and-knot meters below the battlefield — team HP share ("Who's Winning," tweening as beats land) and audience favor ("Crowd Favorite," from per-team accumulated creativity bonuses, `audience_recent_rounds` recency weighting). Server computes both values into `reveal_step`.
- Shared reconnecting-websocket helper.

Acceptance: full mock-mode game playable by humans on two phones + TV; action canvases start prefilled with the scaled character and the restore button works; the arena renders over the background image; action drawings zoom on reveal, persist as sprites afterward, and impact borders (red shake / blue pop) fire on the right characters; floating damage/heal numbers match the HP bars; the initiative rail reorders correctly; both tug-of-war meters move in the expected direction under a scripted mock round.
**Checkpoint (the big one):** human plays a real couch game in mock mode with family. Note UI friction for Phase 7.

## 6. Phase 5 — AI Integration

Tasks:
- `ai/client.py`: anthropic SDK; `claify_actions`/`generate_characters` on `claude-haiku-4-5`, `narrate_round` on `claude-sonnet-4-6` (model IDs in settings.yaml); forced tool-use for structured output; 20s timeout; retry-with-error repair; prompt caching for stable rule text; per-game token/cost logging line.
- Prompt templates per GAME_DESIGN.md §11.3, injecting zones/conditions/rules from config so YAML edits automatically reach the AI. Include the **comedy mandate** in the narrator template (no plain "A punches B" — every beat gets a comedic specific; misses/fumbles escalate; callbacks encouraged; mock situations, never drawing skill).
- `generate_characters`: input is drawing + **player hint phrase**; output includes the **AI-generated funny name** (grand names for elaborate drawings, deadpan names like "Tim" for bland ones).
- `classify_actions`: send per-player **character/action image pairs** with labels; inject the **move catalog with plain-language descriptions** so every drawing (including spell-like ones — eye lasers → `ray`, radiating lines → `burst`) maps to a `catalog_id`; prompt instructs the AI to classify the *difference* between the images (noting the character is rendered at reduced scale on the action canvas), treat erasures as meaningful, and interpret a fully erased character as `hide` or `stumble` — never reject a drawing. **Movement semantics are relational** (toward enemies / toward own backline / specific zone), never absolute left/right; include zone layout, current positions, and team sides in the prompt, with defaults: aggressive-looking movement → toward nearest enemy, fleeing cues → toward own backline. Validator enforces `move_to` adjacency-legality.
- Validators: unknown targets/conditions remapped per stale-intent rules; `flagged` handling (censor sprite + AI-chosen tame name; covers both drawings and hint text).
- **Wildcard logging:** append every `wildcard` classification to `snapshots/<room>/wildcards.jsonl` (round, action PNG path, adaptation_note) so the human can mine playtests for new catalog archetypes.
- Fallback path (neutral classification + template narration) with a visible host banner.
- Tests: schema validation against recorded fixtures; repair-retry path; fallback path; a `scripts/ai_smoke.py` that sends one fixture drawing live and prints the parsed result + cost.

Acceptance: mock tests green; live smoke test returns valid classification for fixture PNGs.
**Checkpoint:** human sets `AI_MODE=live`, runs smoke script, then plays one full live game. Verify per-game cost printed (~$0.10–0.50).

## 7. Post-Phase-5: Two Parallel Tracks

From here, remaining work (the old Phases 6–8) is organized as **two parallel tracks along the architecture's seam** — engine/server/AI vs presentation — so two people can each direct Claude Code sessions without colliding. Rules of engagement:

- **Contract-first:** every cross-seam feature has a sync point (table below). Track A lands the schema/payload change **plus updated mock fixtures** on `main` first; Track B then builds against `AI_MODE=mock`. Track B is never blocked by AI work.
- **Guarded files:** changes to `protocol.py`, `ai/schemas.py`, or any `reveal_step`/`player_state` payload shape must be flagged to the other track before merge (these are the only files where the tracks can break each other).
- **Everything else:** short-lived branches, PRs reviewed by the *other* person's Claude Code session against the spec docs, CI (`pytest` + ruff) as the merge gate, one GitHub Issue per task.

**Sync points** (Track A delivers first → Track B consumes):

| # | Contract | Track A lands | Track B builds |
|---|---|---|---|
| S1 | `speaker` field on beats | narrate schema + mock fixtures with pbp/color beats | speaker-styled beat chips |
| S2 | Montage payloads | MONTAGE sub-phase, `classify_montage` schema, updated `canvas_init`/rail data, mock fixtures | montage canvas mode + stat pulses |
| S3 | Victory payloads | `generate_awards` schema, poster.png path in game-over message, mock fixtures | awards ceremony screen + poster display |
| S4 | Gallery data | `gallery/` persistence + roster in host bootstrap payload | stands spectators rendering |

**Joint milestones** (both tracks merged, human checkpoint):
- **M1 — "It's a real game":** full live 6-player game with no visible waits (slow-AI test green). Family playtest; tune balance.yaml.
- **M2 — "It's a show":** announcer duo, replay, montage, awards, poster, audio all live. Family playtest #2.
- **M3 — "It has history":** gallery across two consecutive games (old Phase 8 acceptance). Watch whether kids notice their old characters. (They will.)

## 8. Track A — Server & AI

1. **Pipeline wiring:** draw r+1 ‖ process r ‖ reveal r−1, including the T2/T3 special cases (characters process during Round 1 drawing; intros reveal during Round 2 drawing). Tests: pipeline ordering under slow-AI simulation (inject 15s delay — must not stall or reveal out of order).
2. **Round-loop server logic:** Arena Gremlin flow; combo fusion resolution; underdog rubber-banding; sudden death.
3. **Announcer duo (→S1):** rewrite the narrate template as two bantering personas (play-by-play + deadpan color commentator); beats gain optional `speaker` field; update mock fixtures.
4. **Power-up montage server side (→S2)** (GAME_DESIGN §10.1): MONTAGE sub-phase every `montage_every_rounds` rounds; `classify_montage` call processed in the next pipeline window; +1 stat with formula deltas; updated image becomes the new original everywhere server-side (canvas_init, rail data, sprite baseline). Test: montage insertion doesn't stall the pipeline.
5. **Victory server side (→S3)** (GAME_DESIGN §10.2): `generate_awards` call (every player gets at least one award); Pillow-composed match poster saved to `snapshots/<room>/poster.png`; both in the game-over payload.
6. **Gallery backend (→S4)** (GAME_DESIGN §15): persist characters to `gallery/` (PNG + JSON, config cap, `gallery_enabled`); inject 2–3 gallery names into narrate prompt for cameos; gallery roster in host bootstrap.
7. **Ongoing:** port `balance_sim` to run against the real engine/configs; mine `wildcards.jsonl` after playtests for new `moves.yaml` archetypes.

## 9. Track B — Presentation

1. **Mockup reconciliation:** anything in `design/mockup_host_screen.html` / `design/mockup_player_screen.html` not yet matching the built pages (persistent action sprites, zoom, impact borders, floating numbers, initiative rail + stat strips, tug-of-war meters, canvas background color).
2. **Round-loop presentation:** "COMBO!" splash with combo_name; latency-masking fillers ("fighters scheming…" — reveal never shows a spinner); **instant replay** (crit/KO beats replay in slow-mo with REPLAY banner; `instant_replay` config).
3. **Speaker-styled beats (S1):** pbp/color chips and styling per the mockup.
4. **Montage UI (S2):** full-size character canvas mode with montage timer; stat change-pulse on phone card and rail.
5. **Victory screen (S3):** awards played one at a time with enlarged drawings; poster download link/QR; "joins the Hall of Doodles" flourish.
6. **The audio layer:** curated free sound packs (CC0 — Kenney.nl, Mixkit, Freesound); `sfx` keys per move in moves.yaml; `events_sfx` stingers in settings.yaml (crit → crowd roar, fumble → sad trombone, KO → bell + gasp, combo → air horn, sudden death → drumroll); Web Audio manager with volume/mute and ±10% pitch variation.
7. **Doodle crowd stands (S4):** rotating gallery spectators in the colosseum stands (subtle idle bob; never obscuring the battlefield).

**Shared backlog** (either track, post-M2, in rough value order): TTS narration read-aloud (pluggable `tts: off|browser|cloud`; browser `speechSynthesis` first, beat advancement tied to speech completion); spectate page; AI-controlled filler fighter for odd counts; replay viewer reading snapshots; persistent summoned companions; new moves.yaml archetypes from the wildcard log; family-recorded `sfx_pack`; tournament mode; High Ground zone enabled by default once tested.

## 10. Testing Strategy Summary

| Layer | Method |
|---|---|
| Engine | Unit + golden (seeded) + property tests; ≥90% coverage |
| Registries | Load-from-YAML tests incl. novel zone/condition blocks |
| State machine | Fake clock, simulated clients, full mock games |
| AI layer | Fixture-based schema tests; repair & fallback paths; live smoke script |
| End-to-end | Scripted mock game over real websockets in CI; human couch playtests at checkpoints |

## 11. Kickoff Prompt (paste into Claude Code to start)

```
Read ARCHITECTURE.md, GAME_DESIGN.md, and IMPLEMENTATION_PLAN.md in this
directory. Create CLAUDE.md with the ground rules from the plan's §0. Then
execute Phase 1 exactly as specified: scaffold the project, config system,
engine models, and dice module, with tests. Stop at the Phase 1 acceptance
criteria and tell me how to run the demo checkpoint.
```

Then proceed one phase at a time: "Execute Phase 2 per IMPLEMENTATION_PLAN.md."
Resist doing multiple phases in one shot — the checkpoints exist to catch
design drift while it's cheap. After Phase 5, work splits into the two
parallel tracks in sections 7-9: kick off sessions with "Execute Track A
item 4 (montage server side) per IMPLEMENTATION_PLAN.md" and respect the
sync-point ordering (Track A lands contracts + mock fixtures before Track B
consumes them).
