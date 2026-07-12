# Doodle Brawl — Game Design Document

## 1. Pitch

Doodle Brawl is a couch party game where your family's terrible drawings come to life and beat the snot out of each other. Players sketch heroes on their phones; an AI game master assigns stats and announces them like a wrestling promoter. Teams then battle by drawing their moves each round — the AI judges creativity, real dice decide fates, and a fresh comedic narrative recounts every crit and fumble.

- **Players:** 2–6, two teams, phones + one shared screen (LAN, Jackbox-style)
- **Session length target:** 15–25 minutes
- **Tone:** family-friendly, chaotic, funny. The AI is a hype-man, never mean.

## 2. Game Flow (sequential rounds)

Each round is strictly sequential and immediate — playtesting showed that people want their move on screen right after drawing it:

1. **Draw** — everyone sketches this round's action, with full knowledge of the current battle state.
2. **Deliberation interlude** — the moment all drawings are in (often before the timer expires), the TV shows every submitted action drawing side by side under a "The judges deliberate…" banner while the AI classifies, the server resolves, and the narrator writes. Seeing everyone's drawings *is* the entertainment; the wait is typically a few seconds and never shows a spinner.
3. **Reveal** — the round plays out beat by beat, then the next draw phase begins.

Two exceptions, both invisible to players: character generation runs while players draw Round 1 (their first move needs no prior information), so the character intros play immediately after Round 1 drawings are in — and Round 1's processing hides behind those intros. And each classify/narrate call starts the instant the last drawing arrives rather than waiting for the timer.

Teams are assigned **in the lobby** (team colors on each phone) so teammates can scheme from the first drawing. Until characters are processed, teams display as plain **“Team A” / “Team B”**; the character-generation call also returns an **AI-invented name per team** that comically links that team's characters together (e.g. a unicorn + a buff goldfish → “The Sparkle Snacks”). The names are revealed as the final beat of the intro sequence (“…and TOGETHER they are…”) and used everywhere from then on — zone labels, meters, phone headers, narration. When a team is defeated, the finale plays immediately.

## 3. Characters & Stats

Three stats, each **0–6**, assigned by the AI from the character drawing on a fixed budget (`stat_budget: 9`) — the AI chooses the *distribution*, config guarantees fairness. Wide ranges are the point: a 6/3/0 specialist plays a completely different game than a 3/3/3 generalist, and a stat of 0 is characterful, not broken.

| Stat | Governs | AI guidance |
|---|---|---|
| **Power** | SMASH damage dice, HP | Muscles, weapons, size, spikes |
| **Speed** | Initiative, AC | Legs, wheels, wings, streamlines |
| **Weird** | BLAST / TRICK / WILD CARD potency | Extra eyes, auras, impossible anatomy, glitter |

Derived (formulas in `balance.yaml`): `HP = 20 + 2 × Power` (20–32) and `AC = 10 + Speed` (10–16). Under 2d6 resolution (§5) every stat point is a felt difference, and **the phone shows the math**: each move button displays that character's live numbers ("SMASH — 4d4+2" on the brick's phone, "SMASH — 1d4+2" on the gremlin's), so stat identity is visible every round, not just at intro.

**Players do not name their characters.** Instead, the creation screen has one text field: *"Give the AI a hint about your fighter (a word or phrase)."* The AI receives the drawing + hint and generates a **funny name** itself — leaning grand for elaborate drawings ("Princess Stabby, Duchess of Pointy Ends") and deliberately deadpan for bland ones (a plain circle with eyes gets named "Tim"). The hint is optional; a blank hint means the AI works from the drawing alone. Hints and drawings are both covered by the family-friendly `flagged` check — a flagged character gets a censored sprite and a tame AI-chosen name.

AI also returns a one-line personality and an announcer intro.

**Example character generation (input: drawing + `hint: "unicorn knight"` → AI output for one player):**
```json
{
  "player_id": "p3",
  "name": "Princess Stabby",
  "stats": {"power": 1, "speed": 5, "weird": 3},
  "personality": "A unicorn princess with zero blade discipline and infinite confidence.",
  "announcer_intro": "She's royalty, she's pointy, she has NO concept of sword safety... PRINCESS STABBYYY!",
  "flagged": false
}
```
The same response includes one **team name per team**, generated from that team's full roster with the same comedy standards as character names — short (fits meters and zone labels), family-friendly, equally funny for both sides, and clearly derived from the characters:
```json
{"teams": {"team_a": "The Sparkle Snacks", "team_b": "Heavy Machinery & Friend"}}
```

## 4. Actions: Tap the Move, Draw the Style

Playtesting v1 taught us two things: pure drawing-classification made it hard to reliably do the move you intended, and thirty moves meant most were forgettable. **Combat v2:** each round the phone shows **eight big move buttons** — six combat moves plus ◀ MOVE LEFT / MOVE RIGHT ▶ — with a target picker (enemy portraits, defaulting to nearest). The player **taps the move and target, then draws how their character does it** on the usual draw-on-top canvas. The tap decides *what happens*; the drawing decides *how well* (creativity bonus, §8) and *how it's narrated*. The AI never guesses the move again.

### 4.1 The Move Catalog (`config/moves.yaml`) — few, loud, and all math-visible

```yaml
# config/moves.yaml — COMBAT V2. The catalog owns all math; buttons render
# each character's live numbers from these formulas. sfx keys per move.
moves:
  smash:  {stat: power, range: same_zone, target: single_enemy,
           damage: "(1 + ceil(POW/2))d4 + 2",     # POW 0: 1d4+2 … POW 6: 4d4+2
           auto_step: true,   # no enemy in your zone → step toward target and swing
           button: "SMASH", desc: "Huge single hit. Get in their face."}
  blast:  {stat: weird, range: any, target: zone_all, friendly_fire: true,
           damage: "(1 + floor(WRD/3))d4 + 3",
           button: "BLAST", desc: "Hits EVERYONE in a zone. Yes, everyone."}
  trick:  {stat: weird, range: any, target: single_enemy,
           damage: "1d6 + WRD", on_hit_condition: from_drawing,   # AI picks from conditions.yaml by flavor
           button: "TRICK", desc: "Damage plus something nasty from your drawing."}
  shield: {stat: none, range: any, target: ally_or_self, ac_bonus: 5,
           reflect: "attacks missing by 3+ deal 1d6 back",
           button: "SHIELD", desc: "Big protection. Strong blocks reflect."}
  rally:  {stat: none, range: any, target: ally_or_self,
           heal: "1d6 + 2", cleanse: all,
           pumped_if_creativity: 2,   # the buff is earned by a great drawing
           button: "RALLY", desc: "Heal, cleanse, and hype a teammate."}
  wild:   {stat: weird, range: any, target: single_enemy,
           damage: "2d8 + floor(WRD/2)", fumble_on_roll_lte: 3,
           button: "WILD CARD", desc: "The AI decides what your drawing does. Gamble."}
  move_l: {stat: none, target: self, move: -1, dodge_ac: 1, button: "◀ MOVE"}
  move_r: {stat: none, target: self, move: +1, dodge_ac: 1, button: "MOVE ▶"}
```

Rules that replace the old action economy (action costs and banked actions are **deleted**):
- **No repeats:** you can't pick the same combat move twice in a row (the button greys out). Movement is exempt; edge-illegal directions render disabled.
- **WILD CARD** is the soul of v1, contained: the AI interprets the drawing freely (`wild_interpretation` in the schema — big flat damage by default, or a condition/reposition/absurdity if the drawing demands it), with the widest fumble band. Highest ceiling, highest variance.
- **Movement** grants +1 AC that round (dodging on the move). ◀/▶ are absolute and match the TV — no AI direction-guessing, ever.
- The Monte Carlo harness (`scripts/balance_sim.py`, v2) validated this system: all six combat moves within ±5% of each other in ablation, and a +2 stat-budget edge wins ~77% of games — stats finally matter.

The **draw-on-top canvas** is unchanged: prefilled with the character at ~50% on the team side (immediately on every load), orientation ribbon, restore button, multi-size erasers, sand background. Gremlin hazard drawing and the montage are also unchanged.

## 5. Resolution & Degrees of Success (2d6)

Server-side, seeded **2d6** — a bell curve, so every +1 genuinely shifts outcomes and extremes stay special:

`roll = 2d6 + move's stat + creativity bonus + modifiers(conditions, zones, combos, sudden death)` vs target `AC (10 + Speed) + shield/dodge bonuses`.

| Result | Threshold | Effect |
|---|---|---|
| Critical hit | natural 12 **or** beat AC by ≥ `crit_margin` (5) | Double damage + narrator goes wild |
| Hit | ≥ AC | Move's damage formula |
| Miss | < AC | Whiff (a SHIELDed target missed by 3+ reflects 1d6) |
| Fumble | natural 2 (WILD CARD: roll ≤ 3) | 3 self-damage + **Embarrassed**; comedy jackpot |

Initiative = Speed (modified by conditions), ties broken by seeded roll. All dice, thresholds, and formulas are config values.

## 6. Zones (config-driven — the "High Ground" test)

Zones are a graph in `zones.yaml`. The default arena:

```yaml
# config/zones.yaml
zones:
  - id: glitter_back
    name: "Team A Backline"
    adjacent: [frontline]
    tags: [backline, team_a]
    modifiers: {}                      # e.g. {ranged_ac_bonus: 1}
  - id: frontline
    name: "The Pit"
    adjacent: [glitter_back, thunder_back]
    tags: [contested]
    modifiers: {}
  - id: thunder_back
    name: "Team B Backline"
    adjacent: [frontline]
    tags: [backline, team_b]
    modifiers: {}
rules:
  melee_requires_same_zone: true    # SMASH; auto_step closes 1 zone toward the target
  ranged_any_zone: true
  move_buttons: [move_l, move_r]    # movement is tapped, absolute, edge-disabled
```

**Adding High Ground later requires only this YAML edit — zero code:**
```yaml
  - id: high_ground
    name: "The High Ground"
    adjacent: [frontline]
    capacity: 2                        # optional: limited space
    entry_cost: 2                      # steep climb
    tags: [elevated]
    modifiers:
      attack_bonus: 1                  # it's over, Anakin
      ranged_ac_bonus: 1
      fumble_extra: "prone"            # fumbling up here means falling
```
The resolver reads `modifiers` generically (any key it knows: `attack_bonus`, `ac_bonus`, `ranged_ac_bonus`, `damage_bonus`, `speed_penalty`, `fumble_extra`, `entry_cost`, `capacity`). Unknown keys log a warning. The zone list, names, and adjacency are also injected into the AI prompts automatically so classification understands the arena.

## 7. Conditions (curated registry)

The AI may only apply conditions from this list (enforced by schema + validator). Each is declarative in `conditions.yaml`:

```yaml
# config/conditions.yaml
conditions:
  burning:   {duration: 2, tick_damage: 2, cure_tags: [water, soggy], emoji: "🔥"}
  soggy:     {duration: 2, modifiers: {power: -2}, immunities: [burning], emoji: "💧"}
  sticky:    {duration: 2, modifiers: {speed: -1}, blocks_free_step: true, emoji: "🟢"}
  prone:     {duration: 1, stand_cost: 1, modifiers: {ac: -1}, emoji: "🙃"}
  frightened:{duration: 2, modifiers: {attack: -1}, emoji: "😱"}
  embarrassed:{duration: 2, modifiers: {attack: -1}, trigger: "fumble", emoji: "😳"}
  enraged:   {duration: 2, modifiers: {attack: +1, ac: -1}, emoji: "😡"}
  sparkly:   {duration: 2, incoming_attack_bonus: 1, emoji: "✨"}
```
Adding a condition = add a YAML block; the resolver applies `modifiers`/`tick_damage`/etc. generically, the phone UI shows `emoji`, and the condition list is injected into AI prompts so it knows its palette. Interactions (e.g., soggy cures burning) are data (`cure_tags`, `immunities`).

## 8. Creativity, Combos & Variety

- **Creativity tiers** (AI-assigned from the drawing, server-capped): 0 (+0), 1 (+1 solid), 2 (+2 clever), 3 (+4 table-losing-it), added to the 2d6 roll — where +2 is enormous. The prompt instructs: judge *idea* creativity, not drawing skill. Creativity is now the drawing's entire mechanical contribution, which keeps the sketching central even though moves are tapped. RALLY's `pumped` buff only fires at tier ≥ 2 — support players earn it with the drawing.
- **Drawing staleness:** re-submitting essentially the same drawing concept as your last round scores creativity 0 (`similar_to_previous`) — variety in *art*, while the no-repeat button rule (§4) forces variety in *moves*.
- **Combos:** the AI still checks teammate drawings for intentional synergy (`combo: {partners, concept, combo_name}`). Since moves are individually tapped, a combo no longer fuses actions — instead **both partners gain +2 on their rolls** and the narrator merges their beats into one named spectacle ("GLITTERNADO SURF STRIKE"). Couch-whispering stays the metagame, without new rules to track.
- **Rubber-banding (optional, on by default for kids):** losing team gets `underdog_bonus: +1` when down ≥ 2 characters' worth of HP share. Config flag.

## 9. Intent Adaptation (adapt, never reject)

With tapped moves the AI no longer decides *what* a player does — but adaptation still applies where reality intervenes:
- **Invalid target at resolution time** (KO'd earlier in the initiative order by a faster teammate): the server redirects to the nearest legal enemy and the `adaptation_note` feeds the narrator ("the fireball sails on to the next-rudest target").
- **WILD CARD interpretation:** the one move where the AI reads the drawing freely; whatever it sees becomes a legal effect — never a rejection.
- **Blank/unmodified canvas:** the tapped move still resolves at creativity 0, narrated as maximum-confidence minimum-effort.

## 10. KO & the Arena Gremlin

At 0 HP a character is KO'd (dramatic narrator send-off). The player immediately becomes an **Arena Gremlin**: each round they draw one hazard; the AI classifies it as a zone effect from a curated hazard palette (banana peel → prone risk, sprinkler → soggy, bees → 1 tick damage, trapdoor → forced move), applied to a zone of the resolver's random choice. Gremlins keep drawing until the match ends. Victory = all characters of one team KO'd. Sudden death (config): after `max_rounds` (12), all attacks gain +2 and healing is disabled.

### 10.1 The Power-Up Montage

Every `montage_every_rounds: 3` rounds, after that round's reveal, surviving players get a `montage_seconds: 20` bonus phase: their canvas loads their **current original character at full size**, and they *add to it* — new armor, extra arms, a cape, flames. A montage AI call (masked by a “🎵 training montage 🎵” TV interstitial, same pattern as the deliberation interlude) classifies each addition and grants exactly **+1 to one stat**, chosen from what was drawn (spikes → Power, wings → Speed, a third eye → Weird). Everyone who adds anything gets exactly +1, so the montage is progression without imbalance; stat formula deltas apply (Power +1 → +2 max HP, healed). The updated drawing **becomes the character's new original everywhere** — action-canvas prefill, initiative rail, battlefield baseline — so characters visibly evolve across the match. A blank montage canvas grants nothing and earns narrator teasing. Montage response schema: `{player_id, stat: "power"|"speed"|"weird", flavor: "..."}` per player, validated like all AI output.

### 10.2 Victory: Awards Ceremony & Match Poster

When a team wins, the host plays the finale, then an **awards ceremony**: one extra narration call (`generate_awards`, Sonnet) receives the match summary — creativity tiers, fumbles, combos, best beats, drawing references — and returns 5–7 superlatives (`{title, player_id, blurb}`), displayed one at a time with the winning drawing enlarged. Hard prompt rules: **every player receives at least one award**, losing team included; titles are affectionate, never mocking ("Fumble of the Match" celebrates the comedy, not the failure). Suggested palette: Most Creative Doodle, Fumble of the Match, Best Combo Name, Crowd Favorite (from the audience meter), Bravest Use of a Household Object.

The server then composes a **match poster** (Pillow): arena background, final character sprites, team names and score, the round titles, and the match's best narrated line — saved to `snapshots/<room>/poster.png` and offered on the victory screen as a download/QR. A season of game nights becomes a scrapbook.

## 11. AI Contract — Schemas

### 11.1 `classify_actions` (per round)
Request contains, per living player, their **tapped move and target** (from the phone — ground truth, not for the AI to decide), plus two labeled image blocks — `"p3 ORIGINAL CHARACTER"` and `"p3 ACTION THIS ROUND"` — and compact game-state context. The prompt instructs: *the drawing shows HOW the tapped move is performed; the character is rendered at reduced scale; the canvas background is the arena floor color, not drawn content.* Response (per player):
```json
{
  "player_id": "p1",
  "creativity_tier": 2,              // 0-3, from the drawing
  "creativity_reason": "the lawnmower is doing a wheelie",
  "similar_to_previous": false,
  "flavor_summary": "flaming wheelie mower charge",   // feeds the narrator
  "trick_condition": "burning",      // TRICK only: from conditions.yaml, by drawing flavor
  "wild_interpretation": null,       // WILD CARD only: freeform effect within schema bounds
  "combo": {"partners": ["p3"], "concept": "oil slick + sparks", "combo_name": "GREASE FIRE GAMBIT"},
  "adaptation_note": null,
  "flagged": false
}
```
Enforced by pydantic: condition names against the registry, partners against living teammates. Fallback on total AI failure: creativity 0, no condition (TRICK deals damage only), template narration — **the tapped move always resolves** because the server, not the AI, owns it.

### 11.2 `narrate_round` request/response
Request: ordered engine `events` (JSON), personalities, adaptation notes, tone guide. Response:
```json
{
  "beats": [
    {"event_ids": ["e1","e2"], "text": "Sir Lawnmower pull-starts his noble steed seventeen times...", "mood": "comedy"},
    {"event_ids": ["e3"], "text": "GLITTERNADO SURF STRIKE!", "mood": "epic"}
  ],
  "round_title": "The Fish Learns to Surf"
}
```
Every beat maps to event IDs so the host screen syncs text with HP-bar animations. Narration is **derived from resolved events** — it cannot change outcomes.

### 11.3 Prompt templates (Jinja2, in `config/prompts/`)
Each template receives: rules summary, zone list, condition palette, compact state, and hard instructions: family-friendly; judge ideas not art skill; never invent conditions/targets; always adapt rather than reject (§9); return only the tool call. Rules text is stable → sent with prompt caching.

**The comedy mandate (narrator prompt).** Plain play-by-play is banned: *"never write 'X attacks Y' when you could write how it went sideways."* Concretely, the narrator template instructs:
- Every beat needs at least one comedic specific — a prop, a sound effect, a bystander reaction, a physics indignity ("the mower coughs. A pigeon judges him.")
- Mine the drawings themselves: reference visible details ("the sword is still taped on") and the characters' personalities
- Misses and fumbles are the comedy jackpot — escalate them; crits get over-the-top wrestling-announcer energy
- Callbacks to earlier rounds are encouraged (the goo from round 1 stays slippery forever)
- Punch up, never at players: mock the *situation* and the *characters*, never the drawing skill or the person
- Keep beats tight (1–3 sentences) — funny dies in paragraphs

**The announcer duo.** The narrator writes as two personalities bantering: an over-caffeinated **play-by-play announcer** and a deadpan **color commentator** ("A bold strategy from Sir Lawnmower." "It is not."). Each beat carries an optional `speaker: "pbp" | "color"` field so the host can style them differently (and, later, give them different TTS voices). Persona descriptions live in the narrate template — editable text like everything else — and the duo also delivers character intros and the awards ceremony, giving the whole match one consistent broadcast voice.

## 12. Worked Round (numbers a test can assert)

Given seed `42`, 2v2 fixture: Stabby (P1/S5/W3, HP 22, AC 15) and Gerald (P3/S1/W5, HP 26, AC 11) vs Lawnmower (P6/S2/W1, HP 32, AC 12) and Blob (P0/S3/W6, HP 20, AC 13).
1. Taps: Stabby → TRICK on Blob (drawing: glitter hypnosis, creativity 2); Blob → BLAST on the front zone (creativity 1); Lawnmower → SMASH on Gerald (auto-step, creativity 0); Gerald → SHIELD self.
2. Initiative: Stabby(5) → Blob(3) → Lawnmower(2) → Gerald(1). Gerald's shield hasn't resolved when SMASH lands — initiative order matters and the couch sees why on the rail.
3. Fixture dice: Stabby rolls 2d6=9 +3 +2 = 14 vs AC 13 → hit, 1d6+3 = 7, Blob 20→13, gains `confused`. Blob's BLAST 2d6=7 +6 +1 = 14 vs zone occupants → hits for 3d4+3 = 11. Lawnmower 2d6=11 +6 = 17 vs AC 11 → beats by 6, **crit**: (4d4+2)×2 = 24, Gerald 26→2. Gerald shields himself (+5 AC) one round too late.
4. Assert exact HPs per the seeded dice in `tests/test_resolver.py::test_v2_golden`.

## 13. UX Details

- **Phase splash:** every drawing phase opens with a ~2s full-screen announcement on **all phones and the TV simultaneously** (config `phase_splash_seconds`, text map in settings.yaml): "Draw your Character!", "Round N — Draw your Move!", "🎵 Upgrade your Character! 🎵" (montage), and per-role text — KO'd players see "Draw a Hazard, Gremlin! 😈". Big display type, whoosh stinger, tap-to-skip on phones; the draw timer starts only after the splash ends.
- Draw timer: 75s actions, 90s characters (config). 10s warning pulse. Auto-submit on expiry (whatever is on the canvas — which is at minimum the preloaded character, classified as a comedic idle).
- **Move buttons + target picker:** eight big buttons beside/below the canvas (SMASH, BLAST, TRICK, SHIELD, RALLY, WILD CARD, ◀ MOVE, MOVE ▶), each showing **that character's live math** ("SMASH — 4d4+2"); last-used combat move greyed out (no-repeat), edge-illegal movement disabled; enemy-portrait target picker defaulting to nearest. Tap move → tap target → draw the style.
- Action canvas: **background color defaults to the arena floor color** (`canvas_background_color: "#E8D5A8"`, shared token with the host battlefield) so submitted drawings blend into the battlefield instead of floating as white rectangles; the classifier prompt states the canvas background color so it's never read as drawn content. Preloaded with the player's character at ~50% scale **immediately on every canvas load, including Round 1** (scaling must never depend on pressing Restore Character — the restore button re-applies the same scaled prefill), positioned on their team's side, with an orientation ribbon ("your side ⟵ ⟶ enemies") matching the TV's layout; "restore character" button; pen (3 widths, 8 colors), erasers in multiple sizes, undo, clear. Erasers restore the canvas background color, not white. Character creation screen adds the hint text field ("a word or phrase to inspire the AI").
- Phone status card always shows: your sprite, **your stats (💪 Power / ⚡ Speed / 🌀 Weird, icon + number)**, HP hearts, condition emojis, team color, "you are drawing for Round N." Stat values pulse briefly when they change (montage, transform).
- **Team naming:** all team labels (zone bands, tug-of-war meter ends, phone headers) read “Team A” / “Team B” until the intro sequence reveals the AI team names, then swap and stay for the match.
- Host battlefield: the default arena is a **CSS-drawn colosseum** (stone arches, stands, sand floor — per `design/mockup_host_screen.html`); a custom image can optionally replace it via `settings.yaml: arena_background` (dropped into `web/host/assets/`). Zones are bands over the background; characters sit in their current zones with HP bars and condition emojis. The arena floor is **uniform** `canvas_background_color` (default `#E8D5A8`) — no gradients, vignettes, or spotlight circles — and sprites render with **no drop shadow, border, or card background**, so each drawing's own sand-colored background blends invisibly into the floor. The **name bubble floats above** the character image (HP bar and condition emojis below).
- **Action images persist.** Once a character's action is revealed, that action drawing *becomes* their battlefield sprite and stays until their next action replaces it — the arena accumulates the round's chaos (laser-firing Stabby stays laser-firing through the next drawing phase). Characters who haven't acted yet show their original character image.
- **Narration log:** announcer text is a **running, chat-style log** (newest at bottom), not transient captions. The current beat types out in a bright gold-bordered card, then rolls up into dimmed history (smaller, ~55% opacity) when the next beat starts. **Round dividers** ("— Round 3: *The Fish Learns to Surf* —") separate rounds; roughly the last 2–3 rounds stay on screen behind a top fade mask; crit/KO/combo lines keep a subtle gold tint in history so highlights stay findable; speaker chips (PBP/COLOR) persist so re-read banter still reads as dialogue. The log remains visible during the deliberation interlude — re-reading last round's jokes is the latency mask. The full transcript always persists to the room snapshot (feeds the match poster's "best line").
- Host reveal pacing: beats advance on a timer (config `beat_seconds: 6`) with a host "next" override button; kids reading speed matters. When a character's beat plays, their action drawing **enlarges by a configurable scale for a configurable duration** (`reveal_action_zoom_scale: 1.8`, `reveal_action_zoom_seconds: 2.5`) so the couch can appreciate the artwork, then shrinks back to sprite size.
- **Impact feedback during reveals:** any character *negatively* affected by the current beat (damage, a bad condition) flashes a **red border with a shake**; any character *positively* affected (heal, cleanse, buff, protection) flashes a **light-blue border with a scale "pop"**. Both derive from the beat's engine events, so they're always accurate to the math.
- **Floating combat numbers:** every damage event spawns a big **red number** that floats up from the affected character and fades; healing spawns a **green** one (config `float_number_seconds: 1.5`). Crits render extra-large with an exclamation. Numbers come from engine events, so they always match the HP bars.
- **Instant replay:** when a beat contains a crit or a KO (config `instant_replay.triggers`), the host replays that beat once in slow-mo — bigger zoom, slower shake, a "REPLAY" banner and stinger — before advancing. Pure presentation over existing beat data; `instant_replay.enabled` toggles it.
- **Initiative Order column:** a vertical rail down the **left side** of the host screen, titled "Initiative Order," showing each character's **original character image** top-to-bottom in the acting order of the round currently being revealed — players always know when to expect their character's moment. Beside each portrait, a **compact stat strip (💪 / ⚡ / 🌀 with numbers)** — the rail is the stats' home on the common screen (the battlefield stays clean), and since the rail is ordered by Speed, the numbers visibly explain the ordering; strips pulse when a value changes (montage, transform). When order changes (Speed conditions like `sticky`, transforms, ties rerolled), the portraits **animate to their new positions**; KO'd characters drop off the rail (Gremlins get a small imp badge at the bottom).
- **Tug-of-war meters:** two cartoony horizontal meters below the battlefield, each a rope with a knot marker sliding between the team colors. **Top — "Who's Winning":** knot position reflects relative team HP share, tweening as damage and healing land during beats. **Bottom — "Crowd Favorite":** knot reflects which team the audience is rooting for, driven by accumulated creativity bonuses per team (config `audience_recent_rounds: 3` weights recent rounds so momentum can swing). The two meters disagreeing — losing on HP but winning the crowd — is exactly the story the couch wants to see.
- **Audio:** move sounds come from curated free sound packs (CC0 sources like Kenney.nl, Mixkit, Freesound), mapped per move via an `sfx` key on each `moves.yaml` entry; **event stingers** (crit → crowd roar, fumble → sad trombone, KO → bell + gasp, combo → air horn, sudden death → drumroll) map from engine event types via an `events_sfx` block in settings.yaml. Host page plays them through a small Web Audio manager with volume/mute controls and ±10% pitch variation so repeats don't sound robotic.
- Accessibility: colorblind-safe team palettes (impact borders also differ in animation — shake vs pop — not just color), min font sizes, all-caps avoided in narration body.

## 14. Tuning Guide (for the human designer)

Want faster games? Lower `hp_base`. Too swingy? Reduce `creativity_tier_3` from 4→3 or raise `crit_margin`. Kids losing? Raise `underdog_bonus`. One move dominating playtests? Its whole formula is one line in moves.yaml. Every knob named in this doc exists in `balance.yaml` with a comment. Change YAML → start a new room → new rules apply. After each game night, skim the **wildcard log** (`snapshots/<room>/wildcards.jsonl`) — recurring WILD CARD interpretations are your signal for what the six moves might be missing.

## 15. Legacy: The Doodle Crowd (Phase 8)

Every character ever drawn persists to a `gallery/` folder (PNG + AI-given name + match record; plain files, no database). When `gallery_enabled: true`, the host renders a rotating handful of past characters as **tiny spectators in the colosseum stands**, and the narrate prompt receives 2–3 random gallery names each round so the announcers can drop cameos ("Princess Stabby watches from the stands. She is judging."). New players literally see the family history they're joining, and every match adds to the crowd. Gallery entries can be deleted by removing files; a config cap keeps the stands from becoming a mob.
