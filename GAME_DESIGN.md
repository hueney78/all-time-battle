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

Teams are assigned **in the lobby** (team colors on each phone) so teammates can scheme from the first drawing. When a team is defeated, the finale plays immediately.

## 3. Characters & Stats

Three stats, each 1–4, assigned by the AI from the character drawing:

| Stat | Governs | AI guidance |
|---|---|---|
| **Power** | Melee/physical damage, HP | Muscles, weapons, size, spikes |
| **Speed** | Initiative, zone moves, dodging | Legs, wheels, wings, streamlines |
| **Weird** | Creative/magical/nonsense actions | Extra eyes, auras, impossible anatomy, glitter |

Derived (formulas in `balance.yaml`):
- `HP = hp_base(18) + hp_per_power(2) × Power`
- `AC = ac_base(11) + Speed`
- Attack bonus = Power or Weird, determined by the classified move's `roll` stat in the move catalog (§4.1).

**Players do not name their characters.** Instead, the creation screen has one text field: *"Give the AI a hint about your fighter (a word or phrase)."* The AI receives the drawing + hint and generates a **funny name** itself — leaning grand for elaborate drawings ("Princess Stabby, Duchess of Pointy Ends") and deliberately deadpan for bland ones (a plain circle with eyes gets named "Tim"). The hint is optional; a blank hint means the AI works from the drawing alone. Hints and drawings are both covered by the family-friendly `flagged` check — a flagged character gets a censored sprite and a tame AI-chosen name.

AI also returns a one-line personality and an announcer intro. Stat totals are normalized server-side to `stat_budget` (default 8) so no drawing is strictly better — the AI chooses the *distribution*, config guarantees fairness.

**Example character generation (input: drawing + `hint: "unicorn knight"` → AI output for one player):**
```json
{
  "player_id": "p3",
  "name": "Princess Stabby",
  "stats": {"power": 2, "speed": 3, "weird": 4},
  "personality": "A unicorn princess with zero blade discipline and infinite confidence.",
  "announcer_intro": "She's royalty, she's pointy, she has NO concept of sword safety... PRINCESS STABBYYY!",
  "flagged": false
}
```

## 4. Action Economy (PF2e-inspired, one drawing per round)

**The draw-on-top canvas.** Each action round, the player's canvas starts **preloaded with their original character drawing rendered at ~50% scale** (config: `action_canvas_character_scale`) — playtesting showed kids draw characters that fill the whole canvas, leaving no room for actions. The scaled character is **positioned on the player's own team's side of the canvas** (matching the TV's arena orientation), leaving the majority of the canvas open toward the enemy side for lasers, charges, and thrown things. A subtle **orientation ribbon** along the canvas edge ("🏠 your side ⟵ ⟶ enemies 💥", flipped appropriately per team) anchors directional drawing so arrows mean what kids think they mean. They draw their action *onto and around* the character — laser beams from the eyes, a giant hammer in hand, a bubble shield. A **"restore character" button** resets the canvas to the scaled original at any time, and **multi-size erasers** let players modify or completely erase the character (all tools: pen in 3 widths / 8 colors, erasers, undo, clear).

The AI receives the **original character image and the action image as a labeled pair** and is instructed to interpret the *differences* as the action — and told explicitly that the character appears at reduced scale on the action canvas, so the size difference itself is never misread as "the character shrank." This makes classification dramatically more reliable (no guessing which blob is whose character) and enables expressive moves:
- Added laser beam → weird ranged attack
- Character redrawn small behind an added rock → defend/hide
- Character's legs erased and redrawn as springs → move/reposition
- **Entire character erased** → the AI interprets it in context: vanishing (defensive, hard to target this round) or dramatic cowardice (a 1-action stumble with a great narration) — always something, never a rejection

Each round every living player submits **one drawing**. The AI rates its scope as an **action cost of 1–3**:

| Cost | Feel | Mechanical effect (balance.yaml) |
|---|---|---|
| 1 | A jab, a step, a taunt | Small effect (0.5× damage die); **bank 2** |
| 2 | A solid attack or maneuver | Standard effect; **bank 1** |
| 3 | A haymaker / ultimate | Big effect (1.5×, +1 to hit); **bank 0** |

**Banked actions** convert to reactions: each banked action grants +1 AC against one incoming attack this round (auto-spent, best-first), and 2 banked actions additionally allow a free zone step when targeted. This makes "small move now, safe and flexible" a real strategy against haymaker spam.

**Example classifications (drawing → catalog move, see §4.1):**
- A fist poking → `strike`, cost 1
- Character surrounded by a tornado hurling toward an enemy → `burst`, cost 3
- Laser beam added from the eyes → `ray`, cost 2 (the Scorching Ray analog)
- Lines radiating from the character in all directions → `burst` targeting the character's own zone — hits **everyone** there, allies included (friendly fire is comedy)
- Character behind an added brick wall → `defend`, cost 2 (+2 AC this round, stacking with banked)
- Legs erased and redrawn as springs → `move`, cost 1
- A heart beaming to a teammate → `heal`, cost 2
- Character erased entirely → `hide`, or a 0-cost `stumble` with legendary narration — classifier's pick from context

### 4.1 The Move Catalog (`config/moves.yaml`)

Every drawing is classified to exactly **one catalog move** (or a combo — §8). The catalog owns all math (roll stat, range, targeting, damage die, condition riders); the AI owns flavor, cost rating, and creativity. This keeps balance empirical — every move is one YAML block — and guarantees an eye-laser resolves identically no matter how it's drawn. Each entry names its PF2e analog for design reference:

```yaml
# config/moves.yaml — damage dice are BASE dice; cost scaling from balance.yaml
# applies on top (cost 1 = 0.5x, cost 2 = 1x, cost 3 = 1.5x and +1 to hit).
# `desc` is injected into the classifier prompt — the AI picks moves by
# matching the drawing to these descriptions, so write them visually.
moves:
  # --- core attacks & maneuvers ---
  strike:     {pf2e: Strike,          roll: power, range: same_zone, target: single_enemy, damage: d8,
               desc: "hitting, slashing, or bonking a nearby enemy with body or weapon"}
  charge:     {pf2e: Sudden Charge,   roll: power, range: any, target: single_enemy, damage: d8, includes_move: true, min_cost: 2,
               desc: "rushing across the arena to smash into an enemy (motion lines toward target)"}
  ray:        {pf2e: Scorching Ray,   roll: weird, range: any, target: single_enemy, damage: d6,
               desc: "a single beam/projectile/blast aimed at one enemy (eye lasers, fireballs, arrows)"}
  burst:      {pf2e: Fireball,        roll: weird, range: any, target: zone_all, damage: d6, min_cost: 2, friendly_fire: true,
               desc: "an explosion or effect radiating in ALL directions, hitting everyone in a zone"}
  line:       {pf2e: Lightning Bolt,  roll: weird, range: any, target: line_all_zones, damage: d6, min_cost: 2,
               desc: "a beam/bolt crossing the whole arena in a line, hitting one enemy in every zone it passes"}
  dot:        {pf2e: Acid Arrow,      roll: weird, range: any, target: single_enemy, damage: d4, on_hit_condition: burning,
               desc: "goo, acid, bees, or anything that clearly KEEPS hurting after it lands"}
  drain:      {pf2e: Vampiric Touch,  roll: weird, range: same_zone, target: single_enemy, damage: d6, heal_self_ratio: 0.5,
               desc: "sucking life/energy from an enemy into yourself (fangs, straws, glowing transfer)"}
  summon:     {pf2e: Summon Animal,   roll: weird, range: any, target: single_enemy, damage: d8, min_cost: 2,
               desc: "a drawn creature/ally attacking for you; it strikes once then vanishes"}
  grapple:    {pf2e: Grapple,         roll: power, range: same_zone, target: single_enemy, damage: d4, on_hit_condition: sticky,
               desc: "grabbing, holding, wrapping, or swallowing an enemy"}
  shove:      {pf2e: Shove,           roll: power, range: same_zone, target: single_enemy, damage: d4, on_hit_push_zones: 1,
               desc: "pushing/launching an enemy into another zone"}
  trip:       {pf2e: Trip,            roll: power, range: same_zone, target: single_enemy, on_hit_condition: prone,
               desc: "knocking an enemy off their feet (sweeps, banana peels aimed at someone)"}
  steal:      {pf2e: Disarm,          roll: power, range: same_zone, target: single_enemy, on_hit_steal_banked: true,
               desc: "grabbing something FROM an enemy — steals their banked actions for yourself"}
  # --- control & debuffs ---
  demoralize: {pf2e: Demoralize,      roll: weird, range: any, target: single_enemy, on_hit_condition: frightened,
               desc: "scaring or intimidating an enemy (roars, scary faces, looming)"}
  feint:      {pf2e: Feint,           roll: weird, range: same_zone, target: single_enemy, on_hit_condition: off_balance,
               desc: "tricking or misdirecting an enemy (fake-outs, decoys, distractions)"}
  confuse:    {pf2e: Confusion,       roll: weird, range: any, target: single_enemy, on_hit_condition: confused,
               desc: "hypnosis, spirals, dizzying effects — the victim's next action targets someone RANDOM"}
  trap:       {pf2e: Snare,           roll: none, range: any, target: zone, creates_hazard: true, hidden_hazard: true, min_cost: 2,
               desc: "placing a hidden trap in a zone (pits, nets, tripwires) that springs on entry"}
  wall:       {pf2e: Wall of Fire,    roll: weird, range: any, target: zone, damage: d4, creates_hazard: true, min_cost: 2,
               desc: "a visible persistent hazard filling a zone (fire wall, spike field, tornado that stays)"}
  # --- defense & protection ---
  defend:     {pf2e: Raise a Shield,  roll: none, target: self, ac_bonus: 2,
               desc: "shields, walls, armor, or bracing drawn on/around YOURSELF"}
  counter:    {pf2e: Shield (spell),  roll: none, target: self, counters_next_attack: true, min_cost: 2,
               desc: "a readied mirror/parry/reversal — negates and reflects the next attack against you this round"}
  hide:       {pf2e: Hide,            roll: none, target: self, applies_condition: hidden,
               desc: "vanishing, hiding, camouflage, or the character erased from the canvas"}
  protect:    {pf2e: "Champion react",roll: none, range: any, target: ally, redirect_attacks_to_self: true,
               desc: "bodyguarding a teammate — attacks aimed at them hit YOU instead this round"}
  sanctuary:  {pf2e: Bless,           roll: none, range: any, target: zone, zone_modifier: {ally_ac_bonus: 1}, min_cost: 2,
               desc: "a protective bubble/aura over an area — teammates in that zone get +1 AC this round"}
  # --- support & self-modification ---
  heal:       {pf2e: Heal,            roll: none, range: any, target: ally_or_self, heal: d6,
               desc: "restoring a teammate's (or your own) health (hearts, bandages, potions)"}
  cleanse:    {pf2e: Restoration,     roll: none, range: any, target: ally_or_self, removes_conditions: 2,
               desc: "removing bad conditions (fire extinguishers, washing off goo, un-scaring)"}
  buff:       {pf2e: Inspire Courage, roll: none, range: any, target: ally, applies_condition: pumped,
               desc: "powering up a teammate (energy beams TO an ally, cheering, power-up auras)"}
  aid:        {pf2e: Aid,             roll: none, range: any, target: ally, grants_roll_bonus: 2,
               desc: "helping a teammate's own move succeed (holding the ladder, setting the pick)"}
  transform:  {pf2e: Wild Shape,      roll: none, target: self, stat_swap: 2, duration: 2,
               desc: "the character redrawn AS something else — shift 2 stat points (e.g. +2 Power/−2 Speed) for 2 rounds"}
  # --- movement & fallbacks ---
  move:       {pf2e: Stride,          roll: none, target: self, move_zones_per_cost: 1,
               desc: "repositioning to another zone (arrows, motion lines, running pose)"}
  stumble:    {pf2e: Delay,           roll: none, target: self, fixed_cost: 0,
               desc: "blank/unmodified canvas or pure indecision — a dramatic pause"}
  wildcard:   {pf2e: "(improvised)",  roll: weird, range: any, target: single_enemy, damage: d6,
               desc: "ONLY if nothing above fits — describe what you see in adaptation_note"}
```

Catalog moves may reference conditions; `conditions.yaml` therefore also includes `hidden` (untargetable by melee, +2 AC vs ranged, 1 round), `off_balance` (−2 AC vs the feinter's team, 1 round), `pumped` (+1 attack, 2 rounds), and `confused` (next action's targets are rerolled uniformly among ALL living characters — allies included; pure comedy). `dot` reuses the `burning` tick mechanics regardless of flavor (acid, bees — the narrator reskins it). `summon` is deliberately a one-shot strike, not a persistent pet — persistent companions with their own HP would bloat state and slow rounds (Phase 7 stretch if the kids demand it).

**Catalog guardrails.** Thirty moves is the ceiling: beyond that, classifier accuracy degrades as descriptions blur together and the balance surface explodes. Each entry may also carry an **`sfx` key** naming the sound clip the host plays when the move resolves (see §13 audio). Growth is driven by data, not speculation, via the **wildcard feedback loop**: every classification that falls through to `wildcard` is logged (round snapshot reference + the AI's `adaptation_note` describing what it saw). If playtests show the same shape repeatedly landing in wildcard ("kids keep drawing themselves growing giant"), that's the signal to add an archetype — always a YAML-only change. For PF2e completeness, the remaining basic actions (Stand, Escape, Interact, Ready, Seek, Take Cover, Tumble Through…) are deliberately folded into these archetypes or handled automatically (standing from prone is a cost deduction, escaping a grapple is a `strike` vs the grappler).

## 5. Resolution & Degrees of Success

Server-side, seeded d20:

`roll = d20 + attack_stat + creativity_bonus + modifiers(conditions, zones, combos) − penalties(stale, embarrassed…)` vs target `AC + banked/defend bonuses`.

| Result | Threshold | Effect |
|---|---|---|
| Critical hit | beat AC by ≥ `crit_margin` (10) **or** natural 20 | Double damage + narrator goes wild |
| Hit | ≥ AC | Standard damage: `cost-scaled die + stat` |
| Miss | < AC | Nothing (narrated as a whiff) |
| Fumble | miss by ≥ 10 **or** natural 1 | Self-inflicted comedy: small self-damage OR a condition, chosen by table in `balance.yaml` |

Initiative = Speed (modified by conditions), ties broken by seeded roll. All dice, thresholds, and damage dice are config values.

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
  melee_requires_same_zone: true
  ranged_any_zone: true
  move_cost_per_step: 1
  free_steps_from_speed: {threshold: 3, steps: 1}   # Speed 3+ = 1 free step/round
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

## 8. Creativity, Staleness, Combos

- **Creativity tiers** (AI-assigned, server-capped): 0 (+0), 1 (+1 solid), 2 (+2 clever), 3 (+4 table-losing-it). The prompt instructs: judge *idea* creativity, not drawing skill — a hilarious stick figure concept outranks a beautiful boring sword. Tier values in `balance.yaml`.
- **Stale penalty:** the AI flags `similar_to_previous: true` when a player repeats their last concept; server applies −2 (config). Forces variety.
- **Combos:** the AI checks teammate drawings for intentional synergy (`combo: {partners: [...], concept: "..."}`); the narrator names the fused move ("GLITTERNADO SURF STRIKE"). **A combo consumes both players' rounds**, so the math must beat two separate attacks — and it does, trading variance for a higher ceiling:
  - **One attack roll**: best participant's roll stat + `combo_bonus` (+3) + creativity at the highest partner's tier **escalated by one tier** (capped at tier 3 / +4)
  - **Combined damage**: the sum of *both* participants' cost-scaled damage contributions (each contributes their own drawing's cost multiplier)
  - **A crit doubles the combined total** — the jackpot that makes couch-whispering irresistible
  - Base behavior comes from the "leading" catalog move (a burst-shaped combo hits the whole zone)
  - **The risk**: one roll. A miss means both players whiff simultaneously (narrated as a legendary catastrophe); everything rides on a single die and usually a single target
  - Each participant's banked actions still derive from their *own* drawing's cost, and stale penalties apply individually
  - Combos are **not** `aid`: aid is the safe one-sided version (+2 to a teammate's roll while keeping your own small action and banking). Combo = all-in fusion; aid = supportive hedge. Both should see play.
- **Rubber-banding (optional, on by default for kids):** losing team gets `underdog_bonus: +1` to attack rolls when down ≥ 2 characters' worth of HP share. Config flag.

## 9. Intent Adaptation (adapt, never reject)

Drawings are ambiguous, targets sometimes fall to a faster teammate earlier in the same initiative order, and the classifier occasionally misreads. The rule stands regardless: the AI must **adapt, never reject**:
- Target invalid by resolution time (KO'd earlier in the round, out of reach) → redirect to the drawing's evident *intention* (nearest enemy in that zone), or narrate the whiff hilariously with a consolation `cost 1` effect.
- Impossible action given current conditions (you're Engulfed and drew a charge) → transform into the closest legal action ("charges... inside the blob. It tickles. 2 damage from within.")
- The classification schema includes `adaptation_note` explaining any transformation — this feeds the narrator so the comedy lands.

## 10. KO & the Arena Gremlin

At 0 HP a character is KO'd (dramatic narrator send-off). The player immediately becomes an **Arena Gremlin**: each round they draw one hazard; the AI classifies it as a zone effect from a curated hazard palette (banana peel → prone risk, sprinkler → soggy, bees → 1 tick damage, trapdoor → forced move), applied to a zone of the resolver's random choice. Gremlins keep drawing until the match ends. Victory = all characters of one team KO'd. Sudden death (config): after `max_rounds` (12), all attacks gain +2 and healing is disabled.

### 10.1 The Power-Up Montage

Every `montage_every_rounds: 3` rounds, after that round's reveal, surviving players get a `montage_seconds: 20` bonus phase: their canvas loads their **current original character at full size**, and they *add to it* — new armor, extra arms, a cape, flames. A montage AI call (masked by a “🎵 training montage 🎵” TV interstitial, same pattern as the deliberation interlude) classifies each addition and grants exactly **+1 to one stat**, chosen from what was drawn (spikes → Power, wings → Speed, a third eye → Weird). Everyone who adds anything gets exactly +1, so the montage is progression without imbalance; stat formula deltas apply (Power +1 → +2 max HP, healed). The updated drawing **becomes the character's new original everywhere** — action-canvas prefill, initiative rail, battlefield baseline — so characters visibly evolve across the match. A blank montage canvas grants nothing and earns narrator teasing. Montage response schema: `{player_id, stat: "power"|"speed"|"weird", flavor: "..."}` per player, validated like all AI output.

### 10.2 Victory: Awards Ceremony & Match Poster

When a team wins, the host plays the finale, then an **awards ceremony**: one extra narration call (`generate_awards`, Sonnet) receives the match summary — creativity tiers, fumbles, combos, best beats, drawing references — and returns 5–7 superlatives (`{title, player_id, blurb}`), displayed one at a time with the winning drawing enlarged. Hard prompt rules: **every player receives at least one award**, losing team included; titles are affectionate, never mocking ("Fumble of the Match" celebrates the comedy, not the failure). Suggested palette: Most Creative Doodle, Fumble of the Match, Best Combo Name, Crowd Favorite (from the audience meter), Bravest Use of a Household Object.

The server then composes a **match poster** (Pillow): arena background, final character sprites, team names and score, the round titles, and the match's best narrated line — saved to `snapshots/<room>/poster.png` and offered on the victory screen as a download/QR. A season of game nights becomes a scrapbook.

## 11. AI Contract — Schemas

### 11.1 `classify_actions` (per round)
Request contains, per living player, two labeled image blocks — `"p3 ORIGINAL CHARACTER"` and `"p3 ACTION THIS ROUND"` — plus compact game-state context. The prompt instructs: *the action is what changed between the two images; the character is rendered at reduced scale on the action canvas; the canvas background is the arena floor color (`canvas_background_color`), not drawn content; erasures are meaningful; a background-only canvas means the character vanished.*

**Movement is relational, never absolute.** The prompt includes the zone layout, each character's current zone, and each team's side, and the AI never reasons in "left/right" — it interprets drawn movement as *toward enemies*, *toward own backline*, or *to a specific zone*, outputting a concrete `move_to` zone id. When direction is genuinely unreadable, defaults apply: aggressive-looking movement (speed lines, charging posture, drawn toward a target) → toward the nearest enemy; fleeing cues (sweat drops, looking backward, cowering) → toward own backline. The validator then enforces that `move_to` is a legal, adjacency-reachable zone — a misread direction can cost one zone of position, never a teleport, and the narrator plays confident wrong-way charges for laughs. Response:
```json
{
  "round": 4,
  "combos": [
    {"partners": ["p2", "p4"], "leading_catalog_id": "burst",
     "concept": "tornado + surfing it",
     "combo_name": "Glitternado Surf Strike"}
  ],
  "actions": [
    {
      "player_id": "p1",
      "catalog_id": "charge",        // MUST be an id from moves.yaml
      "action_cost": 3,              // 1-3 (clamped to the move's min_cost)
      "targets": ["p2"],
      "move_to": null,               // zone id if the move includes movement
      "creativity_tier": 1,          // 0-3
      "creativity_reason": "committed lawnmower theming",
      "similar_to_previous": true,
      "suggested_conditions": [],    // only riders beyond the catalog's own; from conditions.yaml
      "adaptation_note": null,
      "flagged": false
    }
  ]
}
```
Enforced by pydantic: `catalog_id` and condition names validated against the loaded registries, targets against living characters, zones against zones.yaml. One repair retry on failure; fallback = `wildcard`, creativity 0.

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

Given seed `42`, Round 2 of the sample playthrough fixture (4 players, states as in the example doc):
1. Classification fixture returns Blob devour (cost 3), Stabby laser (cost 2, weird), Gerald water throw (cost 2, weird, creativity 2), Mike donuts (cost 2, move+taunt).
2. Resolver: initiative Speed w/ sticky → Blob first; devour hits (17 vs 13), 8 dmg + engulfed; Stabby interior laser nat-20 crit 12 dmg + freed; Gerald crit vs Lawnmower (margin ≥10) 7 dmg + soggy + prone; Mike nat-1 fumble → 2 self-dmg + embarrassed.
3. Assert final HP: Stabby 1, Blob 2, Lawnmower 17, Gerald 24. This exact scenario ships as a golden test (`tests/test_resolver.py::test_round2_golden`).

## 13. UX Details

- **Phase splash:** every drawing phase opens with a ~2s full-screen announcement on **all phones and the TV simultaneously** (config `phase_splash_seconds`, text map in settings.yaml): "Draw your Character!", "Round N — Draw your Move!", "🎵 Upgrade your Character! 🎵" (montage), and per-role text — KO'd players see "Draw a Hazard, Gremlin! 😈". Big display type, whoosh stinger, tap-to-skip on phones; the draw timer starts only after the splash ends.
- Draw timer: 75s actions, 90s characters (config). 10s warning pulse. Auto-submit on expiry (whatever is on the canvas — which is at minimum the preloaded character, classified as a comedic idle).
- Action canvas: **background color defaults to the arena floor color** (`canvas_background_color: "#E8D5A8"`, shared token with the host battlefield) so submitted drawings blend into the battlefield instead of floating as white rectangles; the classifier prompt states the canvas background color so it's never read as drawn content. Preloaded with the player's character at ~50% scale **immediately on every canvas load, including Round 1** (scaling must never depend on pressing Restore Character — the restore button re-applies the same scaled prefill), positioned on their team's side, with an orientation ribbon ("your side ⟵ ⟶ enemies") matching the TV's layout; "restore character" button; pen (3 widths, 8 colors), erasers in multiple sizes, undo, clear. Erasers restore the canvas background color, not white. Character creation screen adds the hint text field ("a word or phrase to inspire the AI").
- Phone status card always shows: your sprite, **your stats (💪 Power / ⚡ Speed / 🌀 Weird, icon + number)**, HP hearts, condition emojis, banked actions, team color, "you are drawing for Round N." Stat values pulse briefly when they change (montage, transform).
- Host battlefield: the default arena is a **CSS-drawn colosseum** (stone arches, stands, sand floor — per `design/mockup_host_screen.html`); a custom image can optionally replace it via `settings.yaml: arena_background` (dropped into `web/host/assets/`). Zones are bands over the background; characters sit in their current zones with HP bars and condition emojis. The arena floor is **uniform** `canvas_background_color` (default `#E8D5A8`) — no gradients, vignettes, or spotlight circles — and sprites render with **no drop shadow, border, or card background**, so each drawing's own sand-colored background blends invisibly into the floor. The **name bubble floats above** the character image (HP bar and condition emojis below).
- **Action images persist.** Once a character's action is revealed, that action drawing *becomes* their battlefield sprite and stays until their next action replaces it — the arena accumulates the round's chaos (laser-firing Stabby stays laser-firing through the next drawing phase). Characters who haven't acted yet show their original character image.
- Host reveal pacing: beats advance on a timer (config `beat_seconds: 6`) with a host "next" override button; kids reading speed matters. When a character's beat plays, their action drawing **enlarges by a configurable scale for a configurable duration** (`reveal_action_zoom_scale: 1.8`, `reveal_action_zoom_seconds: 2.5`) so the couch can appreciate the artwork, then shrinks back to sprite size.
- **Impact feedback during reveals:** any character *negatively* affected by the current beat (damage, a bad condition) flashes a **red border with a shake**; any character *positively* affected (heal, cleanse, buff, protection) flashes a **light-blue border with a scale "pop"**. Both derive from the beat's engine events, so they're always accurate to the math.
- **Floating combat numbers:** every damage event spawns a big **red number** that floats up from the affected character and fades; healing spawns a **green** one (config `float_number_seconds: 1.5`). Crits render extra-large with an exclamation. Numbers come from engine events, so they always match the HP bars.
- **Instant replay:** when a beat contains a crit or a KO (config `instant_replay.triggers`), the host replays that beat once in slow-mo — bigger zoom, slower shake, a "REPLAY" banner and stinger — before advancing. Pure presentation over existing beat data; `instant_replay.enabled` toggles it.
- **Initiative Order column:** a vertical rail down the **left side** of the host screen, titled "Initiative Order," showing each character's **original character image** top-to-bottom in the acting order of the round currently being revealed — players always know when to expect their character's moment. Beside each portrait, a **compact stat strip (💪 / ⚡ / 🌀 with numbers)** — the rail is the stats' home on the common screen (the battlefield stays clean), and since the rail is ordered by Speed, the numbers visibly explain the ordering; strips pulse when a value changes (montage, transform). When order changes (Speed conditions like `sticky`, transforms, ties rerolled), the portraits **animate to their new positions**; KO'd characters drop off the rail (Gremlins get a small imp badge at the bottom).
- **Tug-of-war meters:** two cartoony horizontal meters below the battlefield, each a rope with a knot marker sliding between the team colors. **Top — "Who's Winning":** knot position reflects relative team HP share, tweening as damage and healing land during beats. **Bottom — "Crowd Favorite":** knot reflects which team the audience is rooting for, driven by accumulated creativity bonuses per team (config `audience_recent_rounds: 3` weights recent rounds so momentum can swing). The two meters disagreeing — losing on HP but winning the crowd — is exactly the story the couch wants to see.
- **Audio:** move sounds come from curated free sound packs (CC0 sources like Kenney.nl, Mixkit, Freesound), mapped per move via an `sfx` key on each `moves.yaml` entry; **event stingers** (crit → crowd roar, fumble → sad trombone, KO → bell + gasp, combo → air horn, sudden death → drumroll) map from engine event types via an `events_sfx` block in settings.yaml. Host page plays them through a small Web Audio manager with volume/mute controls and ±10% pitch variation so repeats don't sound robotic.
- Accessibility: colorblind-safe team palettes (impact borders also differ in animation — shake vs pop — not just color), min font sizes, all-caps avoided in narration body.

## 14. Tuning Guide (for the human designer)

Want faster games? Lower `hp_base`. Too swingy? Reduce `creativity_tier_3` from 4→3 or raise `crit_margin`. Kids losing? Raise `underdog_bonus`. Haymaker spam? Increase banked-action AC value. Every knob named in this doc exists in `balance.yaml` with a comment. Change YAML → start a new room → new rules apply. After each game night, skim the **wildcard log** (`snapshots/<room>/wildcards.jsonl`) — recurring shapes the classifier couldn't place are your shopping list for new `moves.yaml` archetypes.

## 15. Legacy: The Doodle Crowd (Phase 8)

Every character ever drawn persists to a `gallery/` folder (PNG + AI-given name + match record; plain files, no database). When `gallery_enabled: true`, the host renders a rotating handful of past characters as **tiny spectators in the colosseum stands**, and the narrate prompt receives 2–3 random gallery names each round so the announcers can drop cameos ("Princess Stabby watches from the stands. She is judging."). New players literally see the family history they're joining, and every match adds to the crowd. Gallery entries can be deleted by removing files; a config cap keeps the stands from becoming a mob.
