# Doodle Brawl — Architecture Document

## 1. System Overview

Doodle Brawl is a local-network party game. One computer runs a Python server and displays the **host screen** (TV). Players join from phone browsers via QR code — no app installs. An AI (Claude API) interprets drawings and writes narration; a deterministic server-side engine resolves all game mechanics.

```
┌─────────────┐   WebSocket    ┌──────────────────────────────┐
│ Phone (P1)  │◄──────────────►│                              │
├─────────────┤                │   Python Server (FastAPI)    │
│ Phone (P2)  │◄──────────────►│                              │
├─────────────┤                │  ┌────────────┐ ┌─────────┐  │   HTTPS
│   ...       │                │  │ Game Engine│ │AI Client│◄─┼──────────► Claude API
├─────────────┤                │  │(determinis-│ │(classify│  │
│ Host screen │◄──────────────►│  │ tic, no AI)│ │+narrate)│  │
│ (TV browser)│                │  └────────────┘ └─────────┘  │
└─────────────┘                └──────────────────────────────┘
```

**Core principle: the AI judges, the server does math.** The AI never tracks HP, rolls dice, or decides outcomes. It classifies drawings into structured actions and writes narration. The engine is a pure, seeded, unit-testable state machine.

**Second principle: everything tunable lives in config.** Zones, conditions, stat formulas, bonuses, timers, and prompts are data (YAML + template files), not code. Adding a "High Ground" zone must require zero Python changes.

## 2. Technology Stack

| Layer | Choice | Rationale |
|---|---|---|
| Server | Python 3.11+, FastAPI, uvicorn | Async websockets + HTTP in one process; user preference |
| Realtime | Native WebSockets (FastAPI) | Simple, LAN-only, no extra infra |
| AI | `anthropic` Python SDK | Haiku 4.5 (`claude-haiku-4-5`) for image classification; Sonnet 4.6 (`claude-sonnet-4-6`) for narration |
| Validation | Pydantic v2 | Game state models AND AI response schemas |
| Config | YAML (`pyyaml`) + Jinja2 prompt templates | Human-editable tuning |
| Frontend | Vanilla HTML/CSS/JS, no build step | Maintainability for a hobby project — no node toolchain, edit and refresh |
| Drawing | HTML `<canvas>`, exported as ~512px PNG base64 | Small payloads, cheap vision tokens |
| Testing | pytest, seeded RNG, mock AI mode | Full game playable with `AI_MODE=mock` and no API key |
| Packaging | `uv` (or pip + venv) | Fast, simple |

No database. Game state is in-memory per room; optional JSON snapshot to disk for crash recovery and replay/debugging. Phase 8 adds a `gallery/` folder (PNG + JSON per character) persisting every character ever drawn across game nights — still plain files.

## 3. Directory Structure

```
doodle-brawl/
├── pyproject.toml
├── README.md
├── .env.example              # ANTHROPIC_API_KEY=..., AI_MODE=live|mock
├── config/
│   ├── settings.yaml          # timers, ports, player limits, model IDs
│   ├── balance.yaml           # stat formulas, HP, action economy, creativity caps
│   ├── zones.yaml             # zone graph + modifiers (add High Ground here)
│   ├── moves.yaml             # move catalog: PF2e-style archetypes owning all action math
│   └── prompts/
│       ├── character_gen.md.j2
│       ├── action_classify.md.j2
│       └── narrate.md.j2
├── server/
│   ├── main.py                # FastAPI app, routes, static file serving
│   ├── room.py                # Room lifecycle, player registry, reconnection
│   ├── protocol.py            # WebSocket message types (pydantic)
│   ├── state_machine.py       # Game phases + sequential round loop (asyncio)
│   ├── engine/
│   │   ├── models.py          # GameState, Character, Team, ResolvedAction
│   │   ├── dice.py            # Seeded RNG wrapper (injectable for tests)
│   │   ├── resolver.py        # PURE resolution: actions in → events out
│   │   ├── moves.py           # Move catalog registry loaded from moves.yaml
│   │   └── zones.py           # Zone graph loaded from zones.yaml
│   ├── ai/
│   │   ├── client.py          # Anthropic wrapper: retry, timeout, mock mode
│   │   ├── schemas.py         # Pydantic schemas for AI responses
│   │   └── validators.py      # Validate/repair AI JSON (one retry w/ error msg)
│   └── snapshots.py           # Optional state persistence for debug/replay
├── web/
│   ├── shared/                # ws.js (reconnecting socket), common CSS
│   ├── player/                # index.html, canvas.js, phone UI
│   └── host/                  # index.html, arena renderer, reveal sequencer
└── tests/
    ├── test_resolver.py       # Golden tests with seeded dice
    ├── test_conditions.py
    ├── test_zones.py
    ├── test_state_machine.py  # Uses mock AI
    └── fixtures/              # Sample drawings (PNG), sample AI responses
```

## 4. Component Responsibilities

### 4.1 Room & Protocol (`room.py`, `protocol.py`)
- Rooms keyed by 4-letter code. Host screen creates a room; players join via `http://<lan-ip>:8000/play?room=XXXX` (QR rendered on host screen).
- Each player gets a persistent `player_id` (stored in `localStorage`) → **reconnection just works** mid-game (phones lock/sleep constantly).
- All WebSocket messages are typed pydantic models serialized as JSON: `{"type": "...", "payload": {...}}`. Unknown message types are logged and ignored (forward compatibility).

Message catalog (subset):

| Direction | Type | Payload |
|---|---|---|
| C→S | `join` | name, player_id?, role: player\|host |
| C→S | `submit_action` | phase, round, png_base64, **move_id, target_id** (tapped on the phone; server-validated: no-repeat, edge legality, living target) |
| C→S | `submit_hint` | character hint word/phrase (creation phase) |
| S→C | `canvas_init` | your character PNG — preloaded onto the action canvas each round (also powers the "restore character" button client-side) |
| S→C | `arena_state` | zone layout + each character's PNG and current zone (host renders this during drawing phases) |
| S→C | `phase_change` | phase, round, deadline_ts, splash (per-role text; clients show it full-screen for `phase_splash_seconds` before the canvas; timer deadline excludes the splash) |
| S→C | `reveal_step` | ordered narrative beats + state deltas + per-player **action PNGs** + the round's **initiative order** + **meter values** (team HP share, audience-favor score) — host paces these |
| S→C | `player_state` | your character, stats, HP, last combat move (for no-repeat greying) |
| S→C | `error` / `toast` | message |

### 4.2 State Machine (`state_machine.py`)
Phases: `LOBBY → DRAW_CHARACTERS → INTROS → ROUND_LOOP(draw → deliberate/process → reveal, with a MONTAGE sub-phase every N rounds) → GAME_OVER(finale → awards ceremony → match poster)`.

Rounds are **strictly sequential** (design doc §2) — players see their move revealed immediately after drawing it:

```python
# Pseudocode for one round
async def round_tick(r):
    await collect_drawings(round=r, timeout=cfg.draw_seconds)  # resolves early when all submitted
    host_show(DELIBERATION_INTERLUDE)     # TV: all submitted drawings side by side, announcer filler
    result = await process_round(r)       # classify → resolve → narrate (starts the moment drawings are in)
    await host_reveal(result)             # beats play; then next round's draw phase begins
```

The deliberation interlude, not concurrency, is the latency mask: processing typically takes a few seconds, the TV never shows a spinner, and the 20s AI timeout + fallback path bounds the worst case. Character intros play **before** Round 1 drawing (character generation is one fast call masked by a “meet the fighters” drumroll); each intro renders the fighter's sprite huge, filling the arena area. Teams are assigned at **lobby time** so collusion works from round 1. Timers auto-submit whatever is on the canvas (unmodified prefill = AI classifies as "hesitates dramatically," a 0-action stumble).

### 4.3 Engine (`engine/`) — the maintainability core
`resolver.py` exposes one pure function:

```python
def resolve_round(state: GameState, actions: list[ClassifiedAction],
                  rng: Dice, cfg: Balance) -> RoundResult:
    """No I/O, no AI, no globals. Same inputs → same outputs."""
```

It handles: initiative, the eight v4 moves and their formulas (via the moves registry), no-repeat/edge legality, creativity/combo bonuses, damage and healing, passive dodge (Speed) and shield mitigation/reflect (Power), WILD CARD backfire, zone legality/movement, KO → Gremlin conversion, and victory detection. There are no attack rolls and no degrees of success — every move lands (Design Doc §5) — and there is no condition system (removed in v2.1). Output is an ordered list of `Event` objects (attack_resolved, shielded, moved, healed, ko, ...) — this event list is BOTH the input to the narrator AI and the script for host-screen animations.

**Registries, not if-statements.** Zones and **moves** are loaded from YAML into registries of declarative effects (see Design Doc §4.1, §6). Every classified action resolves through its `moves.yaml` entry (stat, range, targeting, damage/heal formula, riders); the resolver queries `registry.modifier(target, "damage_bonus")` etc. Adding High Ground — or a whole new attack archetype — = adding a YAML block.

### 4.4 AI Layer (`ai/`)
Five call types, all with pydantic-validated JSON responses (via forced tool-use so output is guaranteed structured):

1. **`generate_characters`** (Haiku, 1 call/game): all character PNGs as labeled image blocks (grouped by team) → stats, personality, announcer intro per character, plus an **AI-invented team name per team** that links its roster (teams display as “Team A/B” until the intro reveal; names then propagate to all clients via room state).
2. **`classify_actions`** (Haiku, 1 call/round): per player, the **tapped move + target** (ground truth from the phone) plus a labeled **pair of images — original character and action drawing**. The AI judges creativity tier, drawing staleness, combo synergy, and WILD CARD interpretation — it never chooses the move/target or whether a hit lands (there is no to-hit; every move lands, only passive dodge/shield reduce it). Plus compact game-state summary → per-player classification (see Design Doc §11.1). Total AI failure still resolves the round: server owns the move; fallback is creativity 0 + template narration.
3. **`narrate_round`** (Sonnet, 1 call/round, text-only): resolved `Event` list + personalities → comedic narrative broken into reveal beats aligned to event IDs, voiced as a two-announcer duo (optional `speaker` tag per beat for host styling and future TTS voices).
4. **`classify_montage`** (Haiku, 1 call per montage, every `montage_every_rounds` rounds): per player, previous character image + updated montage image → which stat gets +1 and a flavor line; the updated image becomes the character's new original everywhere. Masked by a “training montage” TV interstitial (same pattern as the deliberation interlude).
5. **`generate_awards`** (Sonnet, 1 call/game, at victory): match summary (creativity data, fumbles, combos, best beats) → 5–7 awards `{title, player_id, blurb}`, every player receiving at least one.

Reliability rules:
- 20s timeout, 1 retry; on validation failure, retry once with the validation error appended.
- On total failure: engine falls back to a neutral classification (basic attack, creativity 0) and template narration ("The crowd blinks. Something happened, probably.") — **the game never stalls on the API**.
- `AI_MODE=mock` returns canned fixture responses for offline dev and tests.
- **Wildcard feedback loop:** every classification resolved as `wildcard` is appended to `snapshots/<room>/wildcards.jsonl` (round ref, action PNG path, the AI's `adaptation_note`). Recurring unplaceable shapes = data-driven candidates for new `moves.yaml` archetypes.
- System prompts + rules text sent with `cache_control` (prompt caching) to cut input cost ~90% on repeat calls.

### 4.5 Frontend
- **Player page:** join form → character creation (canvas + a **hint word/phrase text field** — no name entry; the AI names the character) → per-round action canvas → status card showing your stats (Power/Speed/Weird), HP, team color. Big buttons, kid-friendly.
- **Action canvas behavior:** the canvas background color defaults to the arena floor color (`canvas_background_color` token) so exported drawings blend into the battlefield; erasers restore this color, not white. Each action round, the canvas **starts preloaded with the player's original character drawing at ~50% scale** (config `action_canvas_character_scale`), positioned on the player's team's side and paired with an **orientation ribbon** ("your side ⟵ ⟶ enemies", per-team) matching the TV's arena layout — so kids have room to draw *around* the character and directional drawings (arrows, charges) are anchored to a spatial reference. A **"restore character" button** wipes the canvas back to the scaled original at any time. Tools: pen (3 widths, 8 colors), **eraser in multiple sizes**, undo, full clear — players may erase any or all of the original character (the AI interprets whatever the final image shows; see AI layer). Visual reference: `design/mockup_player_screen.html`.
- **Host page:** lobby with QR; arena view rendering zones as bands over the **default CSS-drawn colosseum** (stone arches, stands, sand floor — visual reference: `design/mockup_host_screen.html`); an optional custom image overrides it via `arena_background` in settings.yaml (assets in `web/host/assets/`). The sand floor color is the shared `canvas_background_color` token, so player drawings (drawn on same-colored canvases) blend seamlessly into the battlefield. Characters render as sprites showing their **most recent revealed action drawing** — action images persist on the battlefield until replaced by the character's next action (original character image until they first act). **During reveals**, the sequencer appends beats to a **running narration log** (current beat highlighted with typewriter effect, prior beats dimmed in scrollback with round dividers; log persists through the deliberation interlude; full transcript in snapshots) with HP tweens; the acting character's action drawing **zooms up by a configurable scale for a configurable duration** (`reveal_action_zoom_scale`, `reveal_action_zoom_seconds`) then settles to sprite size; characters negatively impacted by the beat get a **red border + shake**, positively impacted get a **light-blue border + pop** — both driven by the beat's engine events. A **Web Audio manager** plays per-move sounds (`sfx` keys in moves.yaml) and event stingers (`events_sfx` in settings.yaml) from curated free sound packs, with volume/mute and slight pitch variation. Damage/heal events spawn **floating combat numbers** (red/green, crits oversized) above the affected sprite. A left-side **"Initiative Order" rail** shows original character portraits in the revealed round's acting order with a compact per-character **stat strip (Power/Speed/Weird)** — the stats' home on the common screen, keeping the battlefield clean — animating reorders and dropping KO'd characters; the round's initiative order ships in `reveal_step` (the resolver already computes it — expose it on `RoundResult`). Below the battlefield, two **tug-of-war meters** render from `reveal_step` meter values: team HP share ("Who's Winning") and audience favor from accumulated per-team creativity bonuses ("Crowd Favorite"), both computed server-side. Crit/KO beats trigger a one-time **instant replay** (slow-mo re-run of the beat with a REPLAY banner; config-toggled). The **victory screen** plays the awards ceremony (one award at a time with the winning drawing enlarged) and offers the server-composed **match poster** as a download/QR. With `gallery_enabled`, past characters from the persistent `gallery/` folder render as tiny spectators in the stands (Phase 8).
- No framework. Each page is one HTML + one or two JS modules. State is whatever the server last sent (`server is source of truth`; clients are dumb renderers).

## 5. Data Flow — One Round, End to End

1. Phones submit action PNGs (or timer fires; processing starts the moment the last drawing arrives) → TV switches to the deliberation interlude showing all submitted drawings. (Each canvas began as the player's character image, possibly modified or erased.)
2. `classify_actions` call with per-player character/action image pairs → validated `ClassifiedAction[]`.
3. `resolve_round(state, actions, rng, cfg)` → `RoundResult{events, new_state}`.
4. `narrate_round(events, personalities)` → beats keyed to event IDs.
5. Result (beats + state deltas + the round's action PNGs for sprite-swapping) sent to the host, which ends the deliberation interlude and plays the reveal immediately.
6. State snapshot written to `snapshots/room-XXXX/round-N.json` (debug/replay).

## 6. Error Handling & Edge Cases

| Case | Handling |
|---|---|
| Phone disconnects | player_id reconnect resumes seamlessly; if drawing missed, auto-submit blank |
| Odd player counts | Teams balanced by count; 2-player = 1v1; config allows AI-controlled filler fighter (stretch) |
| AI returns invalid target (dead/nonexistent) | Validator remaps per intent-adaptation rules (Design Doc §9) |
| API down | Fallback classification + template narration; banner on host: "AI is napping — chaos mode" |
| Host refresh | Host re-syncs full state on reconnect |
| Inappropriate drawing/hint | AI classification includes `flagged` bool → server substitutes censored sprite and the AI generates a tame replacement name; family-friendly instructions in every prompt (players never type names — the AI names all characters) |

## 7. Configuration Philosophy

Every number a designer might tune lives in `config/`. Code reads config through typed pydantic settings objects loaded at room creation (so you can edit YAML and start a new game without restarting the server — hot-reload per room). Examples of things that are config, not code: draw timer seconds, HP formula coefficients, creativity bonus values, stale penalty, combo bonus and combo escalation rules, crit thresholds, zone graph, **the entire move catalog**, model IDs, max image size, prompt text, arena background image path, reveal zoom scale/duration, action-canvas character scale, and all sfx mappings (per-move and event stingers).

## 8. Security/Privacy Notes

- LAN only by default (bind configurable). No accounts, no PII.
- Drawings are sent to the Claude API; that's the only external egress. `.env` holds the key; never logged.
- Room codes expire; max players enforced; payload size caps on uploads (e.g., 200KB/PNG).
