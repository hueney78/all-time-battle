# Doodle Brawl — Game Design Document

## 1. Pitch

Doodle Brawl is a couch party game where your family's terrible drawings come to life and beat the snot out of each other. Players sketch heroes on their phones; an AI game master assigns stats and announces them like a wrestling promoter. Teams then battle by drawing their moves each round — the AI judges creativity, real dice decide fates, and a fresh comedic narrative recounts every crit and fumble.

- **Players:** 2–6, two teams, phones + one shared screen (LAN, Jackbox-style)
- **Session length target:** 15–25 minutes
- **Tone:** family-friendly, chaotic, funny. The AI is a hype-man, never mean.

## 2. Game Flow & the Prediction Pipeline

Players always draw one round ahead of what's being revealed. Drawings are **intents**, adapted by the AI to whatever reality looks like when they resolve. Predicting the battle *is* the strategy.

| Tick | Players draw | System processes | TV reveals |
|---|---|---|---|
| T1 | Characters | — | Lobby / QR |
| T2 | Round 1 | Character generation | "Warming up" filler |
| T3 | Round 2 | Round 1 | **Character intros + teams recap** |
| T4 | Round 3 | Round 2 | **Round 1** |
| T5 | Round 4 | Round 3 | **Round 2** |
| … | … | … | … |

Teams are assigned **in the lobby** (team colors on each phone) so teammates can scheme from the first drawing. Rounds 1–2 are drawn with limited info by design; from round 3 on you draw knowing results from two rounds back. When a team is defeated, remaining buffered reveals play out, then the finale.

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

**The draw-on-top canvas.** Each action round, the player's canvas starts **preloaded with their original character drawing**, and they draw their action *onto it* — laser beams from the eyes, a giant hammer in hand, a bubble shield around themselves. A **"restore character" button** resets the canvas to the original at any time, and **multi-size erasers** let players modify or completely erase the character (all tools: pen in 3 widths / 8 colors, erasers, undo, clear).

The AI receives the **original character image and the action image as a labeled pair** and is instructed to interpret the *differences* as the action. This makes classification dramatically more reliable (no guessing which blob is whose character) and enables expressive moves:
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

**Catalog guardrails.** Thirty moves is the ceiling: beyond that, classifier accuracy degrades as descriptions blur together and the balance surface explodes. Growth is driven by data, not speculation, via the **wildcard feedback loop**: every classification that falls through to `wildcard` is logged (round snapshot reference + the AI's `adaptation_note` describing what it saw). If playtests show the same shape repeatedly landing in wildcard ("kids keep drawing themselves growing giant"), that's the signal to add an archetype — always a YAML-only change. For PF2e completeness, the remaining basic actions (Stand, Escape, Interact, Ready, Seek, Take Cover, Tumble Through…) are deliberately folded into these archetypes or handled automatically (standing from prone is a cost deduction, escaping a grapple is a `strike` vs the grappler).

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

## 9. Stale Intents (the signature rule)

Drawings are made before the previous round's results are known. The AI must **adapt, never reject**:
- Target dead or moved → redirect to the drawing's evident *intention* (nearest enemy in that zone, or narrate the whiff hilariously with a consolation `cost 1` effect).
- Impossible action (you're Engulfed and drew a charge) → transform into the closest legal action ("charges... inside the blob. It tickles. 2 damage from within.")
- The classification schema includes `adaptation_note` explaining any transformation — this feeds the narrator so the comedy lands.

**Worked example (from the sample playthrough):** Zoe drew a horn-laser before knowing her unicorn would be swallowed. Classification kept `type: attack, subtype: weird`, validator confirmed target=Blob legal (she's inside it!), resolver rolled a crit with a point-blank tag, narrator: *"Princess Stabby fires the rainbow laser FROM INSIDE THE BLOB."* Stale drawing → best moment of the night.

## 10. KO & the Arena Gremlin

At 0 HP a character is KO'd (dramatic narrator send-off). The player immediately becomes an **Arena Gremlin**: each round they draw one hazard; the AI classifies it as a zone effect from a curated hazard palette (banana peel → prone risk, sprinkler → soggy, bees → 1 tick damage, trapdoor → forced move), applied to a zone of the resolver's random choice. Gremlins keep drawing until the match ends. Victory = all characters of one team KO'd. Sudden death (config): after `max_rounds` (12), all attacks gain +2 and healing is disabled.

## 11. AI Contract — Schemas

### 11.1 `classify_actions` (per round)
Request contains, per living player, two labeled image blocks — `"p3 ORIGINAL CHARACTER"` and `"p3 ACTION THIS ROUND"` — plus compact state and pipeline context. The prompt instructs: *the action is what changed between the two images; erasures are meaningful; a blank canvas means the character vanished.* Response:
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
Each template receives: rules summary, zone list, condition palette, compact state, and hard instructions: family-friendly; judge ideas not art skill; never invent conditions/targets; always adapt stale intents; return only the tool call. Rules text is stable → sent with prompt caching.

**The comedy mandate (narrator prompt).** Plain play-by-play is banned: *"never write 'X attacks Y' when you could write how it went sideways."* Concretely, the narrator template instructs:
- Every beat needs at least one comedic specific — a prop, a sound effect, a bystander reaction, a physics indignity ("the mower coughs. A pigeon judges him.")
- Mine the drawings themselves: reference visible details ("the sword is still taped on") and the characters' personalities
- Misses and fumbles are the comedy jackpot — escalate them; crits get over-the-top wrestling-announcer energy
- Callbacks to earlier rounds are encouraged (the goo from round 1 stays slippery forever)
- Punch up, never at players: mock the *situation* and the *characters*, never the drawing skill or the person
- Keep beats tight (1–3 sentences) — funny dies in paragraphs

## 12. Worked Round (numbers a test can assert)

Given seed `42`, Round 2 of the sample playthrough fixture (4 players, states as in the example doc):
1. Classification fixture returns Blob devour (cost 3), Stabby laser (cost 2, weird), Gerald water throw (cost 2, weird, creativity 2), Mike donuts (cost 2, move+taunt).
2. Resolver: initiative Speed w/ sticky → Blob first; devour hits (17 vs 13), 8 dmg + engulfed; Stabby interior laser nat-20 crit 12 dmg + freed; Gerald crit vs Lawnmower (margin ≥10) 7 dmg + soggy + prone; Mike nat-1 fumble → 2 self-dmg + embarrassed.
3. Assert final HP: Stabby 1, Blob 2, Lawnmower 17, Gerald 24. This exact scenario ships as a golden test (`tests/test_resolver.py::test_round2_golden`).

## 13. UX Details

- Draw timer: 75s actions, 90s characters (config). 10s warning pulse. Auto-submit on expiry (whatever is on the canvas — which is at minimum the preloaded character, classified as a comedic idle).
- Action canvas: preloaded with the player's character; "restore character" button; pen (3 widths, 8 colors), erasers in multiple sizes, undo, clear. Character creation screen adds the hint text field ("a word or phrase to inspire the AI").
- Phone status card always shows: your sprite, HP hearts, condition emojis, banked actions, team color, "you are drawing for Round N."
- Host screen during drawing phases: the arena background with each **original character image** positioned in its current zone, HP bars and condition emojis attached — the couch always sees the current battlefield while sketching their predictions.
- Host reveal pacing: beats advance on a timer (config `beat_seconds: 6`) with a host "next" override button; kids reading speed matters. During a character's beat, its arena sprite is **temporarily swapped to that round's action image** (Stabby's sprite becomes laser-firing Stabby), then reverts to the original character image when the beat ends.
- Sound hooks (stretch): crowd gasp on crit, sad trombone on fumble.
- Accessibility: colorblind-safe team palettes, min font sizes, all-caps avoided in narration body.

## 14. Tuning Guide (for the human designer)

Want faster games? Lower `hp_base`. Too swingy? Reduce `creativity_tier_3` from 4→3 or raise `crit_margin`. Kids losing? Raise `underdog_bonus`. Haymaker spam? Increase banked-action AC value. Every knob named in this doc exists in `balance.yaml` with a comment. Change YAML → start a new room → new rules apply. After each game night, skim the **wildcard log** (`snapshots/<room>/wildcards.jsonl`) — recurring shapes the classifier couldn't place are your shopping list for new `moves.yaml` archetypes.
