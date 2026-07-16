# Doodle Brawl — Game Design Document

## 1. Pitch

Doodle Brawl is a couch party game where your family's terrible drawings come to life and beat the snot out of each other. Players sketch heroes on their phones; an AI game master assigns stats and announces them like a wrestling promoter. Teams then battle by drawing their moves each round — the AI judges how creative each drawing is, that creativity decides how hard it lands, and a fresh comedic narrative recounts every devastating blow and desperate dodge.

- **Players:** 2–6, two teams, phones + one shared screen (LAN, Jackbox-style)
- **Session length target:** 15–25 minutes
- **Tone:** family-friendly, chaotic, funny. The AI is a hype-man, never mean.

## 2. Game Flow (sequential rounds)

Each round is strictly sequential and immediate — playtesting showed that people want their move on screen right after drawing it:

1. **Draw** — everyone sketches this round's action, with full knowledge of the current battle state.
2. **Deliberation interlude** — the moment all drawings are in (often before the timer expires), the TV shows every submitted action drawing side by side under a "The judges deliberate…" banner while the AI classifies, the server resolves, and the narrator writes. Seeing everyone's drawings *is* the entertainment; the wait is typically a few seconds and never shows a spinner.
3. **Reveal** — the round plays out beat by beat, then the next draw phase begins.

**Character introductions play BEFORE Round 1 drawing** — players meet the fighters, stats, and team names first, then draw their opening moves with full knowledge. Character generation is one fast call masked by a “meet the fighters” drumroll. (One optimization everywhere: each AI call starts the instant the last drawing arrives rather than waiting for the timer.)

Teams are assigned **in the lobby** (team colors on each phone) so teammates can scheme from the first drawing. Until characters are processed, teams display as plain **“Team A” / “Team B”**; the character-generation call also returns an **AI-invented name per team** that comically links that team's characters together (e.g. a unicorn + a buff goldfish → “The Sparkle Snacks”). The names are revealed as the final beat of the intro sequence (“…and TOGETHER they are…”) and used everywhere from then on — zone labels, meters, phone headers, narration. When a team is defeated, the finale plays immediately.

## 3. Characters & Stats

Three stats, each **0–6**, assigned by the AI from the character drawing on a fixed budget (`stat_budget: 9`) — the AI chooses the *distribution*, config guarantees fairness. Wide ranges are the point: a 6/3/0 specialist plays a completely different game than a 3/3/3 generalist, and a stat of 0 is characterful, not broken.

| Stat | Governs | AI guidance |
|---|---|---|
| **Power** | SMASH damage, SHIELD mitigation + reflect chance, HP | Muscles, weapons, size, spikes |
| **Speed** | Initiative, DODGE chance, ranged attack (SHOOT uses the **better of Speed/Weird**) | Legs, wheels, wings, streamlines |
| **Weird** | HEAL, BLAST, ranged attack (SHOOT uses the **better of Speed/Weird**), HP contribution | Extra eyes, auras, impossible anatomy, glitter |

Derived (formulas in `balance.yaml`): `HP = 28 + 2 × Power + Weird` (28–43, the budget capping the top end at Power 6 / Weird 3). There is **no AC and no attack roll** (§5). Each stat point is a felt difference, and **the phone shows the math**: each move button displays that character's live effectiveness ("SMASH — 2d4 + 8" on the brick's phone), so stat identity is visible every round. The shared ranged stat (`max(Speed, Weird)`) guarantees every build has a viable ranged option. The AI also returns a one-line personality and an announcer intro.

> ⚠️ **Balance note — v4 is NOT balanced yet (open playtest item).** Both sims agree, and they disagree with the aspiration above. Measured through the real engine (`python scripts/balance_engine.py`, 3v3 specialist round-robin, row's win% vs column):
>
> | | vs Power(6/2/1) | vs Speed(1/6/2) | vs Weird(2/1/6) | vs Balanced(3/3/3) |
> |---|---|---|---|---|
> | **Power(6/2/1)** | — | .216 | .660 | .212 |
> | **Speed(1/6/2)** | **.784** | — | **.556** | **.724** |
> | **Weird(2/1/6)** | .384 | .424 | — | .448 |
> | **Balanced(3/3/3)** | .804 | .348 | .620 | — |
>
> **Speed is a god stat** — it beats every other build, including the generalist (72%). It does three jobs at once: initiative, the passive dodge (up to 30% of *all* incoming), and ranged attack via `max(Speed, Weird)`. **Weird is the weakest**, losing every matchup, because Weird 6 implies Speed ~1 — acting last with no dodge. Power buys SMASH damage, HP, and SHIELD, and SHIELD is a trap (§4.1).
>
> Levers, in the order worth trying (all one line in `balance.yaml`, see §14): lower `dodge_cap` (0.30 → 0.20/0.25) so Speed's third job shrinks; give SHOOT its own stat or drop the shared `max()` so Speed isn't also a full attack stat; make SHIELD worth its action (below). Retest with `balance_engine.py` after each.

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

Each round the phone shows **eight big buttons** — six combat moves plus ◀ MOVE / MOVE ▶ — and a target picker (enemy portraits, defaulting to nearest). The player **taps the move and target, then draws how their character does it**. The tap decides *what happens*; the drawing decides *how effective it is*. **Every selected move always lands** — there are no misses or attacker fumbles (§5). Effectiveness = a stat-based base + a flat creativity bonus from the drawing.

### 4.1 The Move Catalog (`config/moves.yaml`) — few, loud, math on every button

```yaml
# config/moves.yaml — COMBAT V4. No AC, no to-hit rolls; every move lands.
# effect = base(stat dice) + creativity_bonus (flat +0/+1/+3/+5).
# The only dice that can reduce a hit are the target's passive DODGE (Speed)
# and, on defend, REFLECT (Power). Buttons render each char's live numbers.
moves:
  smash:  {stat: power, range: same_zone, target: single_enemy,
           effect: "2d4 + POW + 2 + creativity", auto_step: true,
           button: "SMASH", desc: "Huge melee hit. Get in their face."}
  shoot:  {stat: "max(speed,weird)", range: any, target: single_enemy,
           effect: "2d4 + max(SPD,WRD) + creativity", same_zone_penalty: half,
           button: "SHOOT", desc: "Hit anyone, anywhere. Weaker up close."}
  blast:  {stat: weird, range: any, target: zone_all, friendly_fire: true,
           effect: "1d6 + WRD + creativity  (per target in the zone)",
           button: "BLAST", desc: "Hits EVERYONE in a zone. Allies too."}
  shield: {stat: power, target: zone_allies,
           mitigate: "4 + POW (per incoming hit, this round)",
           reflect: "10% × POW chance to bounce the mitigated damage back",
           button: "SHIELD", desc: "Protect everyone in your zone. Tanks reflect."}
  rally:  {stat: weird, range: any, target: ally_or_self,
           heal: "2d6 + 2×WRD + 2 + creativity",
           button: "RALLY", desc: "Heal a teammate. Great drawings heal more."}
  wild:   {stat: weird, range: any, target: single_enemy,
           effect: "3d6 + WRD + creativity", backfire_chance: 0.15,
           button: "WILD CARD", desc: "Big and chaotic — but it can backfire."}
  move_l: {target: self, move: -1, button: "◀ MOVE"}
  move_r: {target: self, move: +1, button: "MOVE ▶"}
```

Rules (action costs, banking, and the whole PF2e chassis are **gone**):
- **No repeats:** can't pick the same combat move twice in a row (button greys). Movement exempt; edge-illegal directions disabled. The no-repeat rule matters *more* now — guaranteed hits would make spamming one move optimal otherwise.
- **DODGE** (passive, Speed): before a hit lands, the target has `5% × Speed` chance (cap 30%) to fully avoid it — checked *per hit*, including each BLAST target separately. A dodge is a defensive highlight ("SHE'S NOT EVEN THERE!"), not a wasted turn for the attacker (the move still happened).
- **SHIELD** mitigates a flat `4 + POW` off each incoming hit to zone allies, then a `10% × POW` chance reflects the mitigated amount; resolved *after* dodge.
- **WILD CARD** is the only move that can backfire (15%), taking self-damage — opt-in chaos preserves the comedy without punishing normal moves.
- **Spike moments come from drawings, not luck:** a **creativity tier 3** result is the "DEVASTATING!" beat (replay + stinger + gold log line). Earned by creativity, which fits the game better than a random crit.
- **Move ablation — measured, not yet healthy (open item).** `python scripts/balance_engine.py` pits the full catalog against a team missing one move; >0.5 means the move earns its slot, <0.5 means it's a *trap* that costs you the game:

  | wild | rally | blast | shoot | smash | shield |
  |---|---|---|---|---|---|
  | .688 | .616 | .580 | .556 | **.496** | **.432** |

  **SHIELD is a trap.** It spends your whole action on `4 + POW` mitigation that only covers attackers who act *after* you — so the Speed-1 tank most likely to want it protects nobody (this is exactly what §12's worked round shows on the rail). Fixes worth playtesting: apply mitigation at round start regardless of initiative; scale it up; or let it ride alongside a move rather than replacing one. **SMASH is borderline** (.496) — it's melee-locked while SHOOT hits any zone for comparable damage off a stat that also grants initiative and dodge.

The **draw-on-top canvas** is unchanged (prefilled at ~50% on the team side, orientation ribbon, restore, erasers, sand background). Gremlin hazards and the montage are unchanged.

## 5. Resolution (no AC, no attack rolls)

Doodle Brawl v4 abandons the PF2e to-hit model entirely. **A selected move always takes effect.** Resolution per action:

1. **Effectiveness** = the move's stat base + flat creativity bonus (`+0/+1/+3/+5` for tiers 0–3). Damage/heal *magnitude* still uses dice (e.g. `2d4`), so outcomes vary in size, never in whether they land.
2. **DODGE** (passive, target's Speed): `5% × Speed`, cap 30%, checked per incoming hit — the only thing that can negate a hit.
3. **SHIELD** (if active on the target): subtract `4 + POW` mitigation, then `10% × POW` reflect chance on the mitigated amount.
4. Initiative = Speed (ties broken by a seeded roll — the one internal roll players never see referenced).

**Spike moments** replace crits/fumbles: creativity tier 3 → **DEVASTATING** (replay, stinger, gold log); a full dodge → defensive highlight; WILD CARD backfire → comedy. All thresholds and formulas are config values in `balance.yaml`.

**Announcer rule:** the narrator describes a *battle it is watching*, never mechanics. No "rolls," "dice," "modifiers," "DCs," or "hit chance" ever appear in narration — a dodge is "she blurs sideways and it whiffs," a big creativity hit is "an absolutely devastating blow," mitigation is "the shield swallows most of it." This is a hard rule in the narrate prompt.

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
      damage_bonus: 1                  # it's over, Anakin
      incoming_dodge_penalty: 0.10     # exposed up here: harder to dodge
```
The resolver reads `modifiers` generically (any key it knows: `damage_bonus`, `incoming_damage_bonus`, `dodge_bonus`, `incoming_dodge_penalty`, `entry_cost`, `capacity`). Unknown keys log a warning. The zone list, names, and adjacency are also injected into the AI prompts automatically so classification understands the arena.

## 7. Conditions: Removed (design note)

Earlier versions had a condition system (burning, sticky, frightened, …). Playtesting showed it complicated play and bloated the announcing without earning its keep, so **v2.1 removes conditions entirely**: no registry, no ticks, no status emojis. Everything a condition used to do is now either direct (damage, healing, SHIELD mitigation, dodge) or narrative (the announcers can *say* someone is soggy and embarrassed — it just doesn't need rules). If a future playtest misses them, reintroduce sparingly as one-round rider effects on WILD CARD only.

## 8. Creativity, Combos & Variety

- **Creativity tiers** (AI-assigned from the drawing, server-capped): 0 (+0), 1 (+1), 2 (+3), 3 (+5 “DEVASTATING”), added **directly to the move's effectiveness** (there is no roll to add to). Tier 3 triggers the spike-moment presentation. The prompt instructs: judge *idea* creativity, not drawing skill. Creativity is now the drawing's entire mechanical contribution, which keeps the sketching central even though moves are tapped. RALLY heals more with a better drawing (creativity adds to the heal) — support players earn potency by drawing well.
- **Drawing staleness:** re-submitting essentially the same drawing concept as your last round scores creativity 0 (`similar_to_previous`) — variety in *art*, while the no-repeat button rule (§4) forces variety in *moves*.
- **Combos:** the AI still checks teammate drawings for intentional synergy (`combo: {partners, concept, combo_name}`). Since moves are individually tapped, a combo no longer fuses actions — instead **both partners gain +1 effective creativity tier** (bigger effect, and more likely to hit the DEVASTATING tier) and the narrator merges their beats into one named spectacle ("GLITTERNADO SURF STRIKE"). Couch-whispering stays the metagame, without new rules to track.
- **Rubber-banding (optional, on by default for kids):** losing team gets `underdog_bonus: +1` when down ≥ 2 characters' worth of HP share. Config flag.

## 9. Intent Adaptation (adapt, never reject)

With tapped moves the AI no longer decides *what* a player does — but adaptation still applies where reality intervenes:
- **Invalid target at resolution time** (KO'd earlier in the initiative order by a faster teammate): the server redirects to the nearest legal enemy; the `adaptation_note` feeds the narrator ("the fireball sails on to the next-rudest target").
- **WILD CARD interpretation:** the one move where the AI reads the drawing freely; whatever it sees becomes a legal effect — never a rejection.
- **Blank/unmodified canvas:** the tapped move still resolves at creativity 0, narrated as maximum-confidence minimum-effort.

## 10. KO & the Arena Gremlin

At 0 HP a character is KO'd (dramatic narrator send-off). The player immediately becomes an **Arena Gremlin**: each round they draw one hazard; the AI classifies it as a zone effect from a curated hazard palette (bees/spikes → 1d4 damage to everyone in the zone; trapdoor/banana → forced one-zone push), applied to a zone of the resolver's random choice. Gremlins keep drawing until the match ends. Victory = all characters of one team KO'd. Sudden death (config): after `max_rounds` (12), all attacks gain +2 and healing is disabled.

### 10.1 The Power-Up Montage

Every `montage_every_rounds: 3` rounds, after that round's reveal, surviving players get a `montage_seconds: 20` bonus phase: their canvas loads their **current original character at full size**, and they *add to it* — new armor, extra arms, a cape, flames. A montage AI call (masked by a “🎵 training montage 🎵” TV interstitial, same pattern as the deliberation interlude) classifies each addition and grants exactly **+1 to one stat**, chosen from what was drawn (spikes → Power, wings → Speed, a third eye → Weird). Everyone who adds anything gets exactly +1, so the montage is progression without imbalance; stat formula deltas apply (Power +1 → +2 max HP, healed). The updated drawing **becomes the character's new original everywhere** — action-canvas prefill, initiative rail, battlefield baseline — so characters visibly evolve across the match. A blank montage canvas grants nothing and earns narrator teasing. Montage response schema: `{player_id, stat: "power"|"speed"|"weird", flavor: "..."}` per player, validated like all AI output.

### 10.2 Victory: Awards Ceremony & Match Poster

When a team wins, the host plays the finale, then an **awards ceremony**: one extra narration call (`generate_awards`, Sonnet) receives the match summary — creativity tiers, fumbles, combos, best beats, drawing references — and returns 5–7 superlatives (`{title, player_id, blurb}`), displayed one at a time with the winning drawing enlarged. Hard prompt rules: **every player receives at least one award**, losing team included; titles are affectionate, never mocking ("Fumble of the Match" celebrates the comedy, not the failure). Suggested palette: Most Creative Doodle, Fumble of the Match, Best Combo Name, Crowd Favorite (from the audience meter), Bravest Use of a Household Object.

The server then composes a **match poster** (Pillow): arena background, final character sprites, team names and score, the round titles, and the match's best narrated line — saved to `snapshots/<room>/poster.png` and offered on the victory screen as a download/QR. A season of game nights becomes a scrapbook.

## 11. AI Contract — Schemas

### 11.1 `classify_actions` (per round)
Request contains, per living player, their **tapped move and target** (ground truth from the phone), two labeled image blocks — `"p3 ORIGINAL CHARACTER"` and `"p3 ACTION THIS ROUND"` — and compact game-state context. The AI judges **only** the drawing: creativity tier, staleness, combo synergy, WILD CARD interpretation, and flavor for the narrator — never the move, target, or whether it hits. Response (per player):
```json
{
  "player_id": "p1",
  "creativity_tier": 2,              // 0-3 → flat bonus +0/+1/+3/+5
  "creativity_reason": "the lawnmower is doing a flaming wheelie",
  "similar_to_previous": false,
  "flavor_summary": "flaming wheelie mower charge",
  "wild_interpretation": null,       // WILD CARD only: damage/reposition/absurdity, no status effects
  "combo": {"partners": ["p3"], "concept": "oil slick + sparks", "combo_name": "GREASE FIRE GAMBIT"},
  "adaptation_note": null,
  "flagged": false
}
```
Enforced by pydantic: partners against living teammates. Fallback on total AI failure: creativity 0, template narration — the tapped move still lands (server owns all mechanics).

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
Each template receives: rules summary, zone list, compact state, and hard instructions: family-friendly; judge ideas not art skill; never invent targets or mechanics; always adapt rather than reject (§9); return only the tool call. Rules text is stable → sent with prompt caching.

**The comedy mandate (narrator prompt).** Plain play-by-play is banned: *"never write 'X attacks Y' when you could write how it went sideways."* Concretely, the narrator template instructs:
- Every beat needs at least one comedic specific — a prop, a sound effect, a bystander reaction, a physics indignity ("the mower coughs. A pigeon judges him.")
- Mine the drawings themselves: reference visible details ("the sword is still taped on") and the characters' personalities
- Dodges and WILD CARD backfires are the comedy jackpot — escalate them; DEVASTATING (creativity tier 3) hits get over-the-top wrestling-announcer energy
- Callbacks to earlier rounds are encouraged (the goo from round 1 stays slippery forever)
- Punch up, never at players: mock the *situation* and the *characters*, never the drawing skill or the person
- **Never reference mechanics:** no “rolls,” “dice,” “modifiers,” “DCs,” “AC,” “hit chance,” or “creativity score” in narration. Describe the battle as if watching it live — a dodge is a dodge, a big hit is devastating, a shield swallows the blow
- Keep beats tight (1–3 sentences) — funny dies in paragraphs

**The announcer duo.** The narrator writes as two personalities bantering: an over-caffeinated **play-by-play announcer** and a deadpan **color commentator** ("A bold strategy from Sir Lawnmower." "It is not."). Each beat carries an optional `speaker: "pbp" | "color"` field so the host can style them differently (and, later, give them different TTS voices). Persona descriptions live in the narrate template — editable text like everything else — and the duo also delivers character intros and the awards ceremony, giving the whole match one consistent broadcast voice.

## 12. Worked Round (numbers a test can assert)

Given seed `42`, 2v2 fixture: Stabby (P1/S5/W3 → HP 33) and Gerald (P3/S1/W5 → HP 39) vs Lawnmower (P6/S2/W1 → HP 41) and Blob (P0/S3/W6 → HP 34).
1. Taps: Stabby → SHOOT at Blob (creativity 2 = +3); Blob → BLAST on the front zone (creativity 1 = +1); Lawnmower → SMASH on Gerald (auto-step, creativity 0); Gerald → SHIELD (his zone).
2. Initiative by Speed: Stabby(5) → Blob(3) → Lawnmower(2) → Gerald(1).
3. Effects (every move lands; only dodge/shield can reduce). No AC, no to-hit — the seeded magnitude and dodge rolls give:
   - **Stabby SHOOT at Blob** uses max(5,3)=5 → 2d4=2, +5, +3 creativity = **10**. Blob's Speed-3 dodge (15%) doesn't fire → Blob **34 → 24**.
   - **Blob BLAST on the front zone** → 1d6 + 6 + 1 to everyone there, including its own teammate. Stabby's Speed-5 dodge (25%) **fires** — she takes nothing. Lawnmower eats 1d6=2 + 6 + 1 = **9** → **41 → 32**.
   - **Lawnmower SMASH on Gerald** (2d4 + 6 + 2) auto-steps into his zone — and Gerald's Speed-1 dodge (5%) **fires**. A genuine miracle, and exactly the defensive highlight v4 wants ("SHE'S NOT EVEN THERE!"). Gerald stays at **39**.
   - **Gerald SHIELD** (4 + 3 = 7 mitigation over his zone) lands *after* the SMASH: a Speed-1 shielder protects nobody from faster attackers, and the initiative rail shows the couch why. Here his dodge saved him instead.
4. Final HPs — asserted in `tests/test_resolver.py::test_v4_golden`: **Stabby 33, Blob 24, Lawnmower 32, Gerald 39.**

> The example dice above are the *actual* seed-42 rolls, not illustrations. Change a formula or the dice-consumption order and this fixture moves — that's the point of the golden test. Note this round happens to fire two dodges including a 5% one; it is a deterministic fixture, not a typical round.

## 13. UX Details

- **Lobby rules & guidance:** both waiting screens teach the game before it starts. **Host lobby (TV, beside the QR/room code):** a friendly "How to Play" panel with this copy (editable in settings.yaml): *"1️⃣ Draw your fighter — the AI sizes it up, names it, and gives it stats. 2️⃣ Every round: TAP a move, PICK a target, then DRAW how your character does it. 3️⃣ Your drawing is your power — creative, funny drawings earn big bonuses. 4️⃣ Scheme with your teammate: drawings that work together trigger a COMBO. 5️⃣ Knock out the other team to win — and if you're KO'd, you become a Gremlin and draw hazards!"* plus two tips: *"Weirder is better"* and *"Watch the Initiative Order — fast fighters act first."* **Player waiting screen (after joining, before Start):** the same rules condensed to the five numbered lines, under the "You're in!" confirmation — players read while others join.
- **Status card visibility:** the phone status card (sprite, stats, HP) renders **only once the character exists** (after character generation). In the lobby/waiting phase it is hidden entirely — no empty sprite box or dash-filled stat placeholders.
- **Phase splash:** every drawing phase opens with a ~2s full-screen announcement on **all phones and the TV simultaneously** (config `phase_splash_seconds`, text map in settings.yaml): "Draw your Character!", "Round N — Draw your Move!", "🎵 Upgrade your Character! 🎵" (montage), and per-role text — KO'd players see "Draw a Hazard, Gremlin! 😈". Big display type, whoosh stinger, tap-to-skip on phones; the draw timer starts only after the splash ends.
- Draw timer: 75s actions, 90s characters (config). 10s warning pulse. Auto-submit on expiry (whatever is on the canvas — which is at minimum the preloaded character, classified as a comedic idle).
- **Move buttons + target picker:** eight big buttons beside/below the canvas (SMASH, SHOOT, BLAST, SHIELD, RALLY, WILD CARD, ◀ MOVE, MOVE ▶), each showing **that character's live math** ("SMASH — 2d4 + 8"); last-used combat move greyed out (no-repeat), edge-illegal movement disabled; enemy-portrait target picker defaulting to nearest. Tap move → tap target → draw the style.
- Action canvas: **background color defaults to the arena floor color** (`canvas_background_color: "#E8D5A8"`, shared token with the host battlefield) so submitted drawings blend into the battlefield instead of floating as white rectangles; the classifier prompt states the canvas background color so it's never read as drawn content. Preloaded with the player's character at ~50% scale **immediately on every canvas load, including Round 1** (scaling must never depend on pressing Restore Character — the restore button re-applies the same scaled prefill), positioned on their team's side, with an orientation ribbon ("your side ⟵ ⟶ enemies") matching the TV's layout; "restore character" button; pen (3 widths, 8 colors), erasers in multiple sizes, undo, clear. Erasers restore the canvas background color, not white. Character creation screen adds the hint text field ("a word or phrase to inspire the AI").
- Phone status card always shows: your sprite, **your stats (💪 Power / ⚡ Speed / 🌀 Weird, icon + number)**, HP hearts, team color, "you are drawing for Round N." Stat values pulse briefly when they change (montage, transform).
- **Character intro presentation:** during the intro sequence (which runs **before Round 1 drawing**), the highlighted character's sprite renders **huge — filling the full arena area** (the narration log area below stays intact for the announcer intro text), with name, stats, and personality beside it; each fighter gets their moment, ending with the team-name reveal.
- **Team naming:** all team labels (zone bands, tug-of-war meter ends, phone headers) read “Team A” / “Team B” until the intro sequence reveals the AI team names, then swap and stay for the match.
- Host battlefield: the default arena is a **CSS-drawn colosseum** (stone arches, stands, sand floor — per `design/mockup_host_screen.html`); a custom image can optionally replace it via `settings.yaml: arena_background` (dropped into `web/host/assets/`). Zones are bands over the background; characters sit in their current zones with HP bars. The arena floor is **uniform** `canvas_background_color` (default `#E8D5A8`) — no gradients, vignettes, or spotlight circles — and sprites render with **no drop shadow, border, or card background**, so each drawing's own sand-colored background blends invisibly into the floor. The **name bubble floats above** the character image (HP bar below).
- **Action images persist.** Once a character's action is revealed, that action drawing *becomes* their battlefield sprite and stays until their next action replaces it — the arena accumulates the round's chaos (laser-firing Stabby stays laser-firing through the next drawing phase). Characters who haven't acted yet show their original character image.
- **Narration log:** announcer text is a **running, chat-style log** (newest at bottom), not transient captions. The current beat types out in a bright gold-bordered card, then rolls up into dimmed history (smaller, ~55% opacity) when the next beat starts. **Round dividers** ("— Round 3: *The Fish Learns to Surf* —") separate rounds; roughly the last 2–3 rounds stay on screen behind a top fade mask; DEVASTATING/KO/combo lines keep a subtle gold tint in history so highlights stay findable; speaker chips (PBP/COLOR) persist so re-read banter still reads as dialogue. The log remains visible during the deliberation interlude — re-reading last round's jokes is the latency mask. The full transcript always persists to the room snapshot (feeds the match poster's "best line").
- Host reveal pacing: beats advance on a timer (config `beat_seconds: 6`) with a host "next" override button; kids reading speed matters. When a character's beat plays, their action drawing **enlarges by a configurable scale for a configurable duration** (`reveal_action_zoom_scale: 1.8`, `reveal_action_zoom_seconds: 2.5`) so the couch can appreciate the artwork, then shrinks back to sprite size.
- **Creativity + damage readout on the host screen:** when a character's action is revealed, the screen shows a **one-line addition** that anyone can follow — same icons as the phone buttons, left to right, ending in the number that actually hit:

  > 🎯 **SHOOT** → 🎲 3 + ⚡ Speed 5 + ⭐⭐ Creative 3 = **11 damage**

  Reductions are a **separate second line**, never a rewrite of the first:

  > 🛡️ Gerald's shield blocks 7 → **4 damage** gets through
  > 💨 Blob **dodges** — no damage!

  Rules that keep it readable: **one addition, one total, per line** (never two different numbers with arrows between them); **omit zero terms** (creativity 0 simply doesn't appear); star count *is* the creativity tier, so "draw better → bigger number" is legible without explaining tiers; and a tier-3 result swaps the star chip for a **⭐⭐⭐ DEVASTATING!** flourish. Heals use the same shape (❤️ RALLY → 🎲 7 + 🌀 Weird 12 + ⭐ Creative 1 = **20 healed**). Copy/format in settings.yaml.
- **Impact feedback during reveals:** any character *negatively* affected by the current beat (damage) flashes a **red border with a shake**; any character *positively* affected (heal, shield) flashes a **light-blue border with a scale "pop"**. Both derive from the beat's engine events, so they're always accurate to the math.
- **Floating combat numbers:** every damage event spawns a big **red number** that floats up from the affected character and fades; healing spawns a **green** one (config `float_number_seconds: 1.5`). Crits render extra-large with an exclamation. Numbers come from engine events, so they always match the HP bars.
- **Instant replay:** when a beat contains a DEVASTATING hit or a KO (config `instant_replay.triggers`), the host replays that beat once in slow-mo — bigger zoom, slower shake, a "REPLAY" banner and stinger — before advancing. Pure presentation over existing beat data; `instant_replay.enabled` toggles it.
- **Initiative Order column:** a vertical rail down the **left side** of the host screen, titled "Initiative Order," showing each character's **original character image** top-to-bottom in the acting order of the round currently being revealed — players always know when to expect their character's moment. Beside each portrait, a **compact stat strip (💪 / ⚡ / 🌀 with numbers)** — the rail is the stats' home on the common screen (the battlefield stays clean), and since the rail is ordered by Speed, the numbers visibly explain the ordering; strips pulse when a value changes (montage, transform). When order changes (transforms, ties rerolled), the portraits **animate to their new positions**; KO'd characters drop off the rail (Gremlins get a small imp badge at the bottom).
- **Tug-of-war meters:** two cartoony horizontal meters below the battlefield, each a rope with a knot marker sliding between the team colors. **Top — "Who's Winning":** knot position reflects relative team HP share, tweening as damage and healing land during beats. **Bottom — "Crowd Favorite":** knot reflects which team the audience is rooting for, driven by accumulated creativity bonuses per team (config `audience_recent_rounds: 3` weights recent rounds so momentum can swing). The two meters disagreeing — losing on HP but winning the crowd — is exactly the story the couch wants to see.
- **Audio:** move sounds come from curated free sound packs (CC0 sources like Kenney.nl, Mixkit, Freesound), mapped per move via an `sfx` key on each `moves.yaml` entry; **event stingers** (DEVASTATING → crowd roar, dodge → whoosh, WILD backfire → sad trombone, KO → bell + gasp, combo → air horn, sudden death → drumroll) map from engine event types via an `events_sfx` block in settings.yaml. Host page plays them through a small Web Audio manager with volume/mute controls and ±10% pitch variation so repeats don't sound robotic.
- Accessibility: colorblind-safe team palettes (impact borders also differ in animation — shake vs pop — not just color), min font sizes, all-caps avoided in narration body.

## 14. Tuning Guide (for the human designer)

Want faster games? Lower `hp_base`. Too swingy? Reduce `creativity_tier_3` from +5→+4, or lower the dodge cap. Speed feeling too strong? Lower `dodge_cap` (30%→25%). Kids losing? Raise `underdog_bonus`. One move dominating playtests? Its whole formula is one line in moves.yaml. Every knob named in this doc exists in `balance.yaml` with a comment. Change YAML → start a new room → new rules apply. After each game night, skim the **wildcard log** (`snapshots/<room>/wildcards.jsonl`) — recurring WILD CARD interpretations are your signal for what the six moves might be missing.

## 15. Legacy: The Doodle Crowd (Phase 8)

Every character ever drawn persists to a `gallery/` folder (PNG + AI-given name + match record; plain files, no database). When `gallery_enabled: true`, the host renders a rotating handful of past characters as **tiny spectators in the colosseum stands**, and the narrate prompt receives 2–3 random gallery names each round so the announcers can drop cameos ("Princess Stabby watches from the stands. She is judging."). New players literally see the family history they're joining, and every match adds to the crowd. Gallery entries can be deleted by removing files; a config cap keeps the stands from becoming a mob.
