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

## 7. Phase 6 — Full Pipeline & Game Feel

Tasks:
- Wire the real pipeline: draw r+1 ‖ process r ‖ reveal r−1, including the T2/T3 special cases (characters process during Round 1 drawing; intros reveal during Round 2 drawing).
- Arena Gremlin flow; combo fusion display ("COMBO!" splash + combo_name); underdog rubber-banding; sudden death.
- **Announcer duo:** rewrite the narrate template as two bantering personas (play-by-play + deadpan color commentator); beats gain an optional `speaker` field; host styles the two voices differently.
- **Instant replay:** crit/KO beats replay once in slow-mo with a REPLAY banner and stinger (`instant_replay` config: enabled, triggers, zoom/slowmo factors).
- **Power-up montage** (GAME_DESIGN §10.1): MONTAGE sub-phase every `montage_every_rounds` rounds — full-size character canvas for additions, `classify_montage` call processed in the next pipeline window, +1 stat with formula deltas, updated image becomes the new original everywhere (canvas_init, rail, sprites).
- **Awards ceremony** (GAME_DESIGN §10.2): `generate_awards` call at victory; host plays awards one at a time with enlarged drawings; every player gets at least one.
- Latency masking polish: "fighters scheming…" fillers, reveal never shows a spinner.
- Tests: pipeline ordering under slow-AI simulation (inject 15s delay — game must not stall or reveal out of order); montage phase insertion doesn't stall the pipeline.

Acceptance: slow-AI test green; full live 6-player game runs without a visible wait.
**Checkpoint:** family playtest #2, live AI. Tune balance.yaml afterward.

## 8. Phase 7 — Polish & Stretch (pick from playtest notes)

Committed task — **the audio layer**: source clips from curated free sound packs (CC0 sources like Kenney.nl, Mixkit, Freesound); map one clip per move via `sfx` keys in moves.yaml and event stingers via an `events_sfx` block in settings.yaml (crit → crowd roar, fumble → sad trombone, KO → bell + crowd gasp, combo → air horn, sudden death → drumroll); host-page Web Audio manager with volume/mute controls and ±10% pitch variation on repeated clips.

Committed task — **the match poster** (GAME_DESIGN §10.2): Pillow-composed shareable image at victory (arena background, final sprites, teams/score, round titles, best narrated line), saved to `snapshots/<room>/poster.png` and offered on the victory screen via download link/QR.

Further candidates, in rough value order: TTS narration read-aloud (pluggable `tts: off|browser|cloud` setting; browser `speechSynthesis` first, with beat advancement tied to speech completion); spectate page for extra devices; AI-controlled filler fighter for odd counts; replay viewer reading snapshots; **persistent summoned companions** (upgrade `summon` from one-shot strike to a pet with HP and turns, if the kids demand it); new `moves.yaml` archetypes mined from the wildcard log; a family-recorded `sfx_pack` (kids' mouth sound effects, switched via `sfx_pack` setting); tournament mode (best of 3); High Ground zone enabled by default once tested.

## 9. Phase 8 — Legacy: The Doodle Crowd

Tasks (GAME_DESIGN §15):
- Persist every character at match end to `gallery/` (PNG + JSON: AI name, hint, stats, matches played/won). Plain files; config cap and `gallery_enabled` toggle.
- Host renders a rotating handful of gallery characters as tiny spectators in the colosseum stands (subtle idle bob; never obscuring the battlefield).
- Inject 2–3 random gallery names into the narrate prompt each round for announcer cameos ("Princess Stabby watches from the stands. She is judging.").
- Victory screen adds the winners to the gallery with a "joins the Hall of Doodles" flourish.

Acceptance: after two full games, the second game's stands show characters from the first, and at least one cameo line references a gallery name.
**Checkpoint:** family game night #2 — watch whether kids notice their old characters. (They will.)

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
design drift while it's cheap.
