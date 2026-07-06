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

No database. Game state is in-memory per room; optional JSON snapshot to disk for crash recovery and replay/debugging.

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
│   ├── conditions.yaml        # condition registry
│   ├── moves.yaml             # move catalog: PF2e-style archetypes owning all action math
│   └── prompts/
│       ├── character_gen.md.j2
│       ├── action_classify.md.j2
│       └── narrate.md.j2
├── server/
│   ├── main.py                # FastAPI app, routes, static file serving
│   ├── room.py                # Room lifecycle, player registry, reconnection
│   ├── protocol.py            # WebSocket message types (pydantic)
│   ├── state_machine.py       # Game phases + pipeline orchestration (asyncio)
│   ├── engine/
│   │   ├── models.py          # GameState, Character, Team, ResolvedAction
│   │   ├── dice.py            # Seeded RNG wrapper (injectable for tests)
│   │   ├── resolver.py        # PURE resolution: actions in → events out
│   │   ├── conditions.py      # Registry loaded from conditions.yaml
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
| C→S | `submit_drawing` | phase, round, png_base64 |
| C→S | `submit_hint` | character hint word/phrase (creation phase) |
| S→C | `canvas_init` | your character PNG — preloaded onto the action canvas each round (also powers the "restore character" button client-side) |
| S→C | `arena_state` | zone layout + each character's PNG and current zone (host renders this during drawing phases) |
| S→C | `phase_change` | phase, round, deadline_ts |
| S→C | `reveal_step` | ordered narrative beats + state deltas + per-player **action PNGs** for sprite-swapping (host paces these) |
| S→C | `player_state` | your character, HP, conditions, banked actions |
| S→C | `error` / `toast` | message |

### 4.2 State Machine (`state_machine.py`)
Phases: `LOBBY → DRAW_CHARACTERS → ROUND_LOOP(draw/process/reveal pipeline) → GAME_OVER`.

The pipeline (from the design doc): players draw round *r+1* while the AI+engine process round *r* and the TV reveals round *r−1*. Concretely, three concurrent tracks per tick:

```python
# Pseudocode for one round tick
async def round_tick(r):
    draw_task    = collect_drawings(round=r+1, timeout=cfg.draw_seconds)
    process_task = process_round(r)          # classify → resolve → narrate
    reveal_task  = host_reveal(r - 1)        # plays buffered reveal on TV
    await asyncio.gather(draw_task, process_task, reveal_task)
```

Special cases: round 1 is drawn during character processing; the character-intro reveal fills the round-2 drawing gap; teams are assigned at **lobby time** so collusion works from round 1. Timers auto-submit whatever is on the canvas (blank drawing = AI classifies as "hesitates dramatically," a 0-action stumble).

### 4.3 Engine (`engine/`) — the maintainability core
`resolver.py` exposes one pure function:

```python
def resolve_round(state: GameState, actions: list[ClassifiedAction],
                  rng: Dice, cfg: Balance) -> RoundResult:
    """No I/O, no AI, no globals. Same inputs → same outputs."""
```

It handles: initiative, action-cost budgeting, combo bonuses, attack rolls with degrees of success, damage, condition application/expiry (via registry), zone legality/movement, KO → Gremlin conversion, and victory detection. Output is an ordered list of `Event` objects (attack_resolved, condition_applied, moved, ko, ...) — this event list is BOTH the input to the narrator AI and the script for host-screen animations.

**Registries, not if-statements.** Conditions, zones, and **moves** are loaded from YAML into registries of declarative effects (see Design Doc §4.1, §6–7). Every classified action resolves through its `moves.yaml` entry (roll stat, range, targeting, damage die, riders); the resolver queries `registry.modifier(target, "attack_bonus")` etc. Adding High Ground — or a whole new attack archetype — = adding a YAML block.

### 4.4 AI Layer (`ai/`)
Three call types, all with pydantic-validated JSON responses (via forced tool-use so output is guaranteed structured):

1. **`generate_characters`** (Haiku, 1 call/game): all character PNGs as labeled image blocks → stats, personality, announcer intro per character.
2. **`classify_actions`** (Haiku, 1 call/round): per player, a labeled **pair of images — the original character PNG and the action PNG** (the action canvas starts as the character, so the AI is told to interpret the *differences* as the action: added laser beams, erased limbs, a fully erased canvas = the character has vanished/is hiding). Plus compact game-state summary + drawing-round context ("players drew this before seeing round r−1") → per-player `ClassifiedAction` (**`catalog_id` from moves.yaml**, targets, action_cost 1–3, creativity_tier 0–3, condition suggestions from the allowed list, combo detection with leading move, stale-intent adaptation notes). The move catalog and its plain-language descriptions are injected into the prompt so the AI maps drawings — including spell-like ones (eye lasers → `ray`, radiating lines → `burst`) — onto known archetypes.
3. **`narrate_round`** (Sonnet, 1 call/round, text-only): resolved `Event` list + personalities → comedic narrative broken into reveal beats aligned to event IDs.

Reliability rules:
- 20s timeout, 1 retry; on validation failure, retry once with the validation error appended.
- On total failure: engine falls back to a neutral classification (basic attack, creativity 0) and template narration ("The crowd blinks. Something happened, probably.") — **the game never stalls on the API**.
- `AI_MODE=mock` returns canned fixture responses for offline dev and tests.
- **Wildcard feedback loop:** every classification resolved as `wildcard` is appended to `snapshots/<room>/wildcards.jsonl` (round ref, action PNG path, the AI's `adaptation_note`). Recurring unplaceable shapes = data-driven candidates for new `moves.yaml` archetypes.
- System prompts + rules text sent with `cache_control` (prompt caching) to cut input cost ~90% on repeat calls.

### 4.5 Frontend
- **Player page:** join form → character creation (canvas + a **hint word/phrase text field** — no name entry; the AI names the character) → per-round action canvas → status card showing your HP, conditions, banked actions, team color. Big buttons, kid-friendly.
- **Action canvas behavior:** each action round, the canvas **starts preloaded with the player's original character drawing** so they draw *on top of it* (laser eyes, a sword swing, a shield). A **"restore character" button** wipes the canvas back to the original character image at any time. Tools: pen (3 widths, 8 colors), **eraser in multiple sizes**, undo, full clear — players may erase any or all of the original character (the AI interprets whatever the final image shows; see AI layer).
- **Host page:** lobby with QR; arena view rendering zones as horizontal bands with character drawings as sprites (their actual PNGs, lightly bobbing). **During drawing phases**, the host shows the live arena state: the background plus each original character image positioned in its current zone (with HP bars and condition emojis) — the couch always sees the battlefield they're drawing against. **During reveals**, the sequencer steps through narrative beats with typewriter text and HP tweens, and while a character's beat plays, its sprite is **temporarily swapped to that round's action image** (Princess Stabby's sprite becomes the laser-firing version), then swapped back to the original when the beat ends.
- No framework. Each page is one HTML + one or two JS modules. State is whatever the server last sent (`server is source of truth`; clients are dumb renderers).

## 5. Data Flow — One Round, End to End

1. Phones submit action PNGs (or timer fires) → server buffers by round. (Each canvas began as the player's character image, possibly modified or erased.)
2. `classify_actions` call with per-player character/action image pairs → validated `ClassifiedAction[]`.
3. `resolve_round(state, actions, rng, cfg)` → `RoundResult{events, new_state}`.
4. `narrate_round(events, personalities)` → beats keyed to event IDs.
5. Result (beats + state deltas + the round's action PNGs for sprite-swapping) buffered; host plays it as the next reveal while phones draw the following round.
6. State snapshot written to `snapshots/room-XXXX/round-N.json` (debug/replay).

## 6. Error Handling & Edge Cases

| Case | Handling |
|---|---|
| Phone disconnects | player_id reconnect resumes seamlessly; if drawing missed, auto-submit blank |
| Odd player counts | Teams balanced by count; 2-player = 1v1; config allows AI-controlled filler fighter (stretch) |
| AI returns invalid target (dead/nonexistent) | Validator remaps per stale-intent rules (Design Doc §9) |
| API down | Fallback classification + template narration; banner on host: "AI is napping — chaos mode" |
| Host refresh | Host re-syncs full state on reconnect |
| Inappropriate drawing/hint | AI classification includes `flagged` bool → server substitutes censored sprite and the AI generates a tame replacement name; family-friendly instructions in every prompt (players never type names — the AI names all characters) |

## 7. Configuration Philosophy

Every number a designer might tune lives in `config/`. Code reads config through typed pydantic settings objects loaded at room creation (so you can edit YAML and start a new game without restarting the server — hot-reload per room). Examples of things that are config, not code: draw timer seconds, HP formula coefficients, creativity bonus values, stale penalty, combo bonus and combo escalation rules, crit thresholds, zone graph, condition list, **the entire move catalog**, model IDs, max image size, prompt text.

## 8. Security/Privacy Notes

- LAN only by default (bind configurable). No accounts, no PII.
- Drawings are sent to the Claude API; that's the only external egress. `.env` holds the key; never logged.
- Room codes expire; max players enforced; payload size caps on uploads (e.g., 200KB/PNG).
