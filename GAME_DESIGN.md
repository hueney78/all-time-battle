# Doodle Brawl — Game Design Document

## 1. Pitch

Doodle Brawl is a couch party game where your family's terrible drawings come to life and beat the snot out of each other. Players sketch heroes on their phones; an AI game master assigns stats and announces them like a wrestling promoter. Teams then battle by drawing their moves each round — the AI judges how creative each drawing is, that creativity decides how hard it lands, and a fresh comedic narrative recounts every devastating blow and last-second save.

- **Players:** 2–8, two teams, phones + one shared screen (LAN, Jackbox-style)
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
| **Power** | SMASH damage, half of CHARGE, HP | Muscles, weapons, size, spikes |
| **Speed** | Initiative, ESCAPE damage, half of CHARGE (**no HP**) | Legs, wheels, wings, streamlines |
| **Weird** | BLAST damage, PROTECT heal **and** reflect strength, HP | Extra eyes, auras, impossible anatomy, glitter |

Derived (formulas in `balance.yaml`): `HP = 27 + 2 × Power + Weird` (27–42). **Speed grants no HP** (v6): fast fighters already act first, so making them tanky too let Speed dominate — keeping them squishy is the trade for their initiative edge. There is **no AC, no attack roll, and no dodge** (§5) — every move lands (the lone exception is ESCAPE's parting shot, §5). Each stat drives exactly two moves, and **the phone shows the math** on every button ("SMASH — 2d4 + 8"), so stat identity is visible each round.

Balance note: the three specialists form a clean rock-paper-scissors — Speed edges Power, Power beats Weird, Weird beats Speed — and a balanced 3/3/3 build beats Power and Weird but loses to Speed. No stat is a dump stat, and no build is a trap.

AI also returns a one-line personality and an announcer intro, plus a **funny character name capped at two words** (three only if the middle word is a connector like "of" or "the" — "Gerald the Buff," "Duke of Spikes"). The announcers say these names constantly, so longer ones become a mouthful; grand for elaborate drawings, deadpan for bland ones (a plain circle with eyes gets "Tim").

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

Each round the phone shows **five big move buttons** and a target picker (enemy or ally portraits as appropriate). The player **taps a move and a target, then draws how their character does it**. The tap decides *what happens*; the drawing decides *how hard it lands* (creativity, §8) and how it's narrated. **Every selected move always lands** — no misses, no fumbles, no dodges (§5) — with one positional exception: **ESCAPE's parting shot** only connects if its target was in the zone the escaper fled from (you may tap any enemy, but a far one whiffs, §5). There are no separate movement buttons: CHARGE and ESCAPE carry movement inside an attack, so a turn is never spent doing nothing.

### 4.1 The Move Catalog (`config/moves.yaml`)

```yaml
# config/moves.yaml — COMBAT V5. Five moves, all single-target.
# effect = dice + stat modifier + creativity (flat +0/+1/+3/+5).
# Nothing can make a move miss; only PROTECT's reflect shield alters damage.
moves:
  smash:   {stat: power, range: same_zone, target: single_enemy,
            damage: "2d4 + POW + 2 + creativity",
            button: "SMASH", desc: "Powerful melee hit on someone in your zone."}
  blast:   {stat: weird, range: any_zone, target: single_enemy,
            damage: "2d4 + WRD + 2 + creativity",
            same_zone_penalty: half,       # point-blank is clumsy
            always_legal: true,            # universal fallback (see legality rules)
            button: "BLAST", desc: "Powerful ranged hit on anyone, anywhere. Weak up close."}
  charge:  {stat: "avg(power,speed)", range: any_zone, target: single_enemy,
            moves_to_target: true,
            damage: "2d4 + avg(POW,SPD) + creativity",   # ~2/3 of SMASH
            button: "CHARGE", desc: "Rush into their zone and hit them."}
  escape:  {stat: speed, range: any_zone, target: single_enemy,
            moves_one_zone: player_choice,               # ◀ or ▶ chosen with the tap
            hits_from_zone_only: true,                   # parting shot: only hits the zone you fled FROM
            damage: "2d4 + SPD + creativity",            # ~2/3 of SMASH
            button: "ESCAPE", desc: "Slip one zone away; the parting shot only hits an enemy in the zone you left."}
  protect: {stat: weird, range: any_zone, target: ally,
            acts_first: true,                            # PROTECT always resolves before everything
            heal: "1d6 + WRD + creativity",
            reflect_pct: "5% × WRD (cap 30%)",           # absorbs that share and bounces it back
            button: "PROTECT", desc: "Heal a teammate and cloak them in a reflecting shield."}
```

**Legality & ordering rules:**
- **No repeats:** you can't pick the same move twice in a row (the button greys out).
- **BLAST is always legal** — it's the universal fallback, since SMASH needs an enemy in your zone, PROTECT needs a living ally, and no-repeat removes one more option each round.
- **CHARGE is always legal**, even against someone already in your zone: targets move before your turn arrives, so the intent stands — if they're still adjacent when it resolves, you simply swing without traveling.
- **PROTECT is greyed out with no living ally** (it cannot target the self), and **always acts first in initiative**, before every other move that round.
- **SMASH requires an enemy in your zone**, otherwise greyed out.
- **A character at 0 HP does not act.** If they are KO'd earlier in the same round (by a faster enemy, a reflect, or a trap), their tapped move never resolves — dead is dead, immediately.
- **Victory ends the round instantly.** The moment the last member of a team is KO'd, resolution stops — no remaining winning-team character takes their queued action; the game cuts straight to the finale.
- Everything is **single-target**: no move damages or heals more than one character.

The **draw-on-top canvas** is unchanged (prefilled at ~50% on the team side, orientation ribbon, restore button, erasers, sand background).

## 5. Resolution (no AC, no rolls to hit, no dodge)

**A selected move always takes effect** (with one positional exception, below). Resolution per action:

1. **Effect** = the move's dice + its stat modifier + the flat creativity bonus (`+0/+1/+3/+5` for tiers 0–3). Magnitude varies via dice; whether it lands never does.
2. **ESCAPE's parting shot** (the lone exception to "every move lands"): ESCAPE slips one zone away, then fires back at the zone it just *left*. A player may tap **any** enemy, but the shot only connects if that enemy is in the zone the escaper **fled from**; against a far target the fighter still gets away clean and the shot **whiffs** (damage 0, a `whiff` event). This is a positional rule, not a to-hit roll — it's the ESCAPE analog of SMASH needing a same-zone enemy. Config-gated by the move's `hits_from_zone_only` rider.
3. **PROTECT's reflect shield** (the only thing that alters a hit that landed): if the target carries a shield, it absorbs `5% × caster's Weird` (cap 30%) of the incoming damage and **bounces exactly that much back at the attacker**. A shielded ally takes less; the attacker takes the difference.
4. **Initiative:** PROTECT first (always), then by Speed, ties broken by a seeded roll players never see referenced. **A character reduced to 0 HP loses their action immediately**, even if they had already tapped it.

**Spike moments** come from drawings, not luck: creativity tier 3 is the **DEVASTATING** beat (replay + stinger + gold log line), and a big reflect turning a killing blow around is the defensive highlight. All values live in `balance.yaml`.

**Announcer rule:** the narrator describes a *battle it is watching*, never mechanics. No "rolls," "dice," "modifiers," "DCs," or "hit chance" — a reflect is "the shield throws it right back in his face," a devastating hit is "an absolutely ruinous blow." Hard rule in the narrate prompt.

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
  movement: via CHARGE (into a target's zone) and ESCAPE (player picks left/right)
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
      incoming_damage_bonus: 2         # exposed up here: hits land harder
```
The resolver reads `modifiers` generically (any key it knows: `damage_bonus`, `incoming_damage_bonus`, `heal_bonus`, `entry_cost`, `capacity`). Unknown keys log a warning. The zone list, names, and adjacency are also injected into the AI prompts automatically so classification understands the arena.

## 7. No Status Conditions (by design)

Doodle Brawl has no status-effect system (no burning/stunned/etc.), and this is deliberate: it keeps play fast and the announcing clean. Everything is either direct (damage, healing, PROTECT's reflect shield) or purely narrative — the announcers can still *say* a character is soggy and rattled; it simply carries no rules. If a future version wants a status effect, add it sparingly and only where it earns the extra announcing.

## 8. Creativity, Combos & Variety

- **Creativity tiers** (AI-assigned from the drawing, server-capped): 0 (+0), 1 (+1), 2 (+3), 3 (+5 “DEVASTATING”), added **directly to the move's effectiveness** (there is no roll to add to). Tier 3 triggers the spike-moment presentation. The prompt instructs: judge *idea* creativity, not drawing skill. Creativity is now the drawing's entire mechanical contribution, which keeps the sketching central even though moves are tapped. PROTECT heals more with a better drawing (creativity adds to the heal) — support players earn potency by drawing well.
- **Drawing staleness:** re-submitting essentially the same drawing concept as your last round scores creativity 0 (`similar_to_previous`) — variety in *art*, while the no-repeat button rule (§4) forces variety in *moves*.
- **Combos:** with every move single-target, combos are pure drawing synergy — the AI checks teammate drawings for intentional connection (`combo: {partners, concept, combo_name}`). A combo does not fuse actions — instead **both partners gain +1 effective creativity tier** (bigger effect, and more likely to hit DEVASTATING) and the narrator merges their beats into one named spectacle ("GLITTERNADO SURF STRIKE"). Each partner's move still resolves on its own target — combos amplify, they never merge effects. Couch-whispering stays the metagame, without new rules to track.
- **Rubber-banding (optional, on by default for kids):** losing team gets `underdog_bonus: +1` when down ≥ 2 characters' worth of HP share. Config flag.

## 9. Intent Adaptation (adapt, never reject)

With tapped moves the AI no longer decides *what* a player does — but adaptation still applies where reality intervenes:
- **Invalid target at resolution time** (KO'd earlier in the initiative order by a faster teammate): the server redirects to the nearest legal enemy; the `adaptation_note` feeds the narrator ("the fireball sails on to the next-rudest target").
- **Blank/unmodified canvas:** the tapped move still resolves at creativity 0, narrated as maximum-confidence minimum-effort.

## 10. KO & the Arena Gremlin

At 0 HP a character is KO'd (dramatic narrator send-off) and **immediately removed from the battlefield** — their sprite disappears from the arena and their portrait drops off the initiative rail. If they were KO'd before their turn came up that round, their tapped action never resolves.

The player instantly becomes an **Arena Gremlin**: each round they **select a zone to trap** and draw the trap on a **blank canvas** (no character prefill — the Gremlin has no character anymore). The drawing becomes a **medium-sized image of the drawn trap, placed in the chosen zone** on the host battlefield beneath a **“Trap” label**, where it **persists until triggered**. When any enemy is in that zone at end of round, the trap fires at **one random enemy there** for light damage (`trap_damage: 1d4 + creativity`, config) and the icon vanishes with a puff. Gremlins keep trapping until the match ends; multiple traps can coexist in different zones. Victory = all characters of one team KO'd. Sudden death (config): after `max_rounds`, all damage gains +3.

### 10.1 The Power-Up Montage

Every `montage_every_rounds: 3` rounds, after that round's reveal, surviving players get a `montage_seconds: 20` bonus phase: their canvas loads their **current original character at full size**, and they *add to it* — new armor, extra arms, a cape, flames. A montage AI call (masked by a “🎵 training montage 🎵” TV interstitial, same pattern as the deliberation interlude) classifies each addition and grants exactly **+1 to one stat**, chosen from what was drawn (spikes → Power, wings → Speed, a third eye → Weird). Everyone who adds anything gets exactly +1, so the montage is progression without imbalance; stat formula deltas apply (Power +1 → +2 max HP, healed). The updated drawing **becomes the character's new original everywhere** — action-canvas prefill, initiative rail, battlefield baseline — so characters visibly evolve across the match. A blank montage canvas grants nothing and earns narrator teasing. Montage response schema: `{player_id, stat: "power"|"speed"|"weird", flavor: "..."}` per player, validated like all AI output.

### 10.2 Victory: Awards Ceremony & Match Poster

When a team wins, the host first shows a **victory splash screen**: the winning **team name** in huge display type, the winning **characters' original drawings** side by side (their character art — not their last action pose), the announcers' **final commentary line** of the match (the last narration beat of the deciding blow), and a footer line — *“🏆 The judges are deciding awards…”*. This splash **stays up until the awards are ready** (it masks the `generate_awards` call the same way the deliberation interlude masks a round), then transitions into the ceremony. Then the **awards ceremony**: one extra narration call (`generate_awards`, Sonnet) receives the match summary — creativity tiers, fumbles, combos, best beats, drawing references — and returns 5–7 superlatives (`{title, player_id, blurb}`), displayed one at a time with the winning drawing enlarged. Hard prompt rules: **every player receives at least one award**, losing team included; titles are affectionate, never mocking ("Fumble of the Match" celebrates the comedy, not the failure). Suggested palette: Most Creative Doodle, Fumble of the Match, Best Combo Name, Crowd Favorite (from the audience meter), Bravest Use of a Household Object.

The server then composes a **match poster** (Pillow): arena background, final character sprites, team names and score, the round titles, and the match's best narrated line — saved to `snapshots/<room>/poster.png` and offered on the victory screen as a download/QR. A season of game nights becomes a scrapbook.

## 11. AI Contract — Schemas

### 11.1 `classify_actions` (per round)
Request contains, per living player, their **tapped move and target** (ground truth from the phone), two labeled image blocks — `"p3 ORIGINAL CHARACTER"` and `"p3 ACTION THIS ROUND"` — and compact game-state context. The AI judges **only** the drawing: creativity tier, staleness, combo synergy, and flavor for the narrator — never the move, target, or whether it hits. Response (per player):
```json
{
  "player_id": "p1",
  "creativity_tier": 2,              // 0-3 → flat bonus +0/+1/+3/+5
  "creativity_reason": "the lawnmower is doing a flaming wheelie",
  "similar_to_previous": false,
  "flavor_summary": "flaming wheelie mower charge",
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
- Reflected hits (the attacker hurting themselves on a shield) and Gremlin traps springing are the comedy jackpot — escalate them; DEVASTATING (creativity tier 3) hits get over-the-top wrestling-announcer energy
- Callbacks to earlier rounds are encouraged (the goo from round 1 stays slippery forever)
- Punch up, never at players: mock the *situation* and the *characters*, never the drawing skill or the person
- **Never reference mechanics:** no “rolls,” “dice,” “modifiers,” “DCs,” “AC,” “hit chance,” or “creativity score” in narration. Describe the battle as if watching it live — a big hit is devastating, a shield swallows the blow and stings the attacker back, a trap springs
- Keep beats tight (1–3 sentences) — funny dies in paragraphs
- **One move = one beat:** CHARGE and ESCAPE cover movement and attack in a single beat, never two lines. **PROTECT is likewise one beat** — a fighter who heals *and* shields a teammate does both in one action, narrated together ("patches them up and throws a shield around them"), never split into a heal line and a shield line. (The engine emits PROTECT as a single event so the narrator has one thing to call.)

**Inside-joke lore (`config/lore.yaml`).** A player-editable list of family in-jokes — terms with definitions — that the AI may **occasionally** weave into announcer commentary and character intros, so the game speaks your household's language. Format:
```yaml
# config/lore.yaml — add your own; leave empty to disable.
lore:
  - term: "the Kevin Special"
    definition: "any move that looks cool but accomplishes nothing"
  - term: "grandma rules"
    definition: "when someone wins by doing the most boring safe thing"
usage: occasional        # how often to lean on lore: never | occasional | frequent
```
A few random entries are injected into the narrate/intro prompt each call with instructions to use them **sparingly and only when they fit naturally** — never forced. Empty file = feature off. This preserves the AI's own witty commentary (which stays the default voice) and just seasons it with home references.

**The announcer duo.** The narrator writes as two personalities bantering: an over-caffeinated **play-by-play announcer** and a deadpan **color commentator** ("A bold strategy from Sir Lawnmower." "It is not."). Each beat carries an optional `speaker: "pbp" | "color"` field so the host can style them differently (and, later, give them different TTS voices). Persona descriptions live in the narrate template — editable text like everything else — and the duo also delivers character intros and the awards ceremony, giving the whole match one consistent broadcast voice. When both announcers riff on the **same moment**, they tag both beats to the same `event_id`; the on-screen effect for that moment plays **once** (see §13 "Effects fire once per moment"), so a color aside never re-triggers the animation.

## 12. Worked Round (numbers a test can assert)

Seed `42`, 2v2 fixture (HP from `27 + 2×POW + WRD`, no Speed term): Stabby (P1/S5/W3 → HP 32) and Gerald (P3/S1/W5 → HP 38) vs Lawnmower (P6/S2/W1 → HP 40) and Blob (P0/S3/W6 → HP 33). Stabby and Gerald start in `back_a`, the others in `back_b`. Final HPs after the round: **Stabby 19, Blob 25, Lawnmower 38, Gerald 38** (reflects: 2 back at Blob, 2 at Lawnmower).
1. **Taps:** Gerald → PROTECT on Stabby (creativity 2 = +3); Stabby → CHARGE at Blob (creativity 1 = +1); Lawnmower → CHARGE at Stabby (creativity 0); Blob → BLAST at Stabby (creativity 3 = +5, DEVASTATING).
2. **Initiative:** PROTECT always first → Gerald. Then by Speed: Stabby(5) → Blob(3) → Lawnmower(2).
3. **Resolution:** Gerald heals Stabby `1d6 + 5 + 3` and shields her at `5% × 5 = 25%` reflect. Stabby charges into `back_b` and hits Blob for `2d4 + avg(1,5) + 1`. Blob BLASTs Stabby — now in Blob's own zone, so the point-blank penalty halves it — and 25% of what lands bounces back at Blob. Lawnmower charges Stabby's zone (already there) and swings for `2d4 + avg(6,2)`, with 25% reflected.
4. Assert exact HPs and reflect amounts from the seeded dice in `tests/test_resolver.py::test_v5_golden`. A companion test asserts a character KO'd before their initiative slot **never resolves their tapped action**.

## 13. UX Details

- **Lobby rules & guidance:** both waiting screens teach the game before it starts. **Host lobby (TV, beside the QR/room code):** a friendly "How to Play" panel with this copy (editable in settings.yaml): *"1️⃣ Draw your fighter — the AI sizes it up, names it, and gives it stats. 2️⃣ Every round: TAP a move, PICK a target, then DRAW how your character does it. 3️⃣ Your drawing is your power — creative, funny drawings earn big bonuses. 4️⃣ Scheme with your teammate: drawings that work together trigger a COMBO. 5️⃣ Knock out the other team to win — and if you're KO'd, you become a Gremlin and draw hazards!"* plus two tips: *"Weirder is better"* and *"Watch the Initiative Order — fast fighters act first."* **Player waiting screen (after joining, before Start):** the same rules condensed to the five numbered lines, under the "You're in!" confirmation — players read while others join.
- **Join QR:** the host lobby shows a **QR code beside the join URL and room code** that encodes the full phone join URL (`http://<lan-ip>:<port>/play?room=XXXX`) so players scan straight into the room without typing an IP. It's a server-rendered SVG (the `/qr` endpoint) revealed once it loads, with the text URL + room code as the always-present fallback.
- **Status card visibility:** the phone status card (sprite, stats, HP) renders **only once the character exists** (after character generation). In the lobby/waiting phase it is hidden entirely — no empty sprite box or dash-filled stat placeholders.
- **Phase splash:** every drawing phase opens with a ~2s full-screen announcement on **all phones and the TV simultaneously** (config `phase_splash_seconds`, text map in settings.yaml): "Draw your Character!", "Round N — Draw your Move!", "🎵 Upgrade your Character! 🎵" (montage), and per-role text — KO'd players see "Draw a Hazard, Gremlin! 😈". Big display type, whoosh stinger, tap-to-skip on phones; the draw timer starts only after the splash ends.
- Draw timer: 75s actions, 90s characters (config). 10s warning pulse. Auto-submit on expiry (whatever is on the canvas — which is at minimum the preloaded character, classified as a comedic idle).
- **Host draw-phase countdown:** during every drawing phase (characters, actions, montage) the TV shows a **medium countdown timer** in the top bar, counting down to the **same server deadline the phones use**, so the shared-screen clock tracks the phone timers closely (it warns/pulses on the same threshold). It appears when the draw phase opens and clears the instant all drawings are in — i.e. when the deliberation interlude begins — so it never lingers into the reveal. Display-only: the host never auto-submits.
- **Move buttons + target picker:** five big buttons beside/below the canvas (SMASH, BLAST, CHARGE, ESCAPE, PROTECT — ESCAPE also asks left/right), each labelled with **the stat icon(s) that power it** — 💪 SMASH, 🌀 BLAST, 💪⚡ CHARGE (the average of both), ⚡ ESCAPE, 🌀 PROTECT — using the same icons as the status card and initiative rail, so a player can see at a glance which moves their character is built for, plus **that character's live math** ("💪 SMASH — 2d4 + 8"); last-used move greyed out (no-repeat), SMASH greyed with no enemy in your zone, PROTECT greyed with no living ally, BLAST always available; portrait target picker (enemies, or allies for PROTECT). **Gremlins** instead see a blank canvas and a zone picker for planting traps. Tap move → tap target → draw the style.
- Action canvas: **background color defaults to the arena floor color** (`canvas_background_color: "#E8D5A8"`, shared token with the host battlefield) so submitted drawings blend into the battlefield instead of floating as white rectangles; the classifier prompt states the canvas background color so it's never read as drawn content. Preloaded with the player's character at ~50% scale **immediately on every canvas load, including Round 1** (scaling must never depend on pressing Restore Character — the restore button re-applies the same scaled prefill), positioned on their team's side, with an orientation ribbon ("your side ⟵ ⟶ enemies") matching the TV's layout; "restore character" button; pen (3 widths, 8 colors), erasers in multiple sizes, undo, clear. Erasers restore the canvas background color, not white. Character creation screen adds the hint text field ("a word or phrase to inspire the AI").
- Phone status card always shows: your sprite, **your stats (💪 Power / ⚡ Speed / 🌀 Weird, icon + number)**, HP hearts, team color, "you are drawing for Round N." Stat values pulse briefly when they change (montage, transform).
- **Character intro presentation:** during the intro sequence (which runs **before Round 1 drawing**), the highlighted character's sprite renders **huge — filling the full arena area** (the narration log area below stays intact for the announcer intro text), with name, stats, and personality beside it; each fighter gets their moment, ending with the team-name reveal.
- **Team naming:** all team labels (zone bands, tug-of-war meter ends, phone headers) read “Team A” / “Team B” until the intro sequence reveals the AI team names, then swap and stay for the match.
- Host battlefield: the default arena is a **CSS-drawn colosseum** (stone arches, stands, sand floor — per `design/mockup_host_screen.html`); a custom image can optionally replace it via `settings.yaml: arena_background` (dropped into `web/host/assets/`). Zones are bands over the background; characters sit in their current zones with HP bars, **wrapping into multiple rows** when a zone holds more than two or three fighters (rows stack from the bottom up) rather than sprawling into one overflowing row. **KO'd characters are removed entirely** (no Gremlin sprites on the battlefield); **Gremlin traps** appear as a **medium-sized image of the drawn trap** under a **“Trap” label** in their chosen zone until triggered. The arena floor is **uniform** `canvas_background_color` (default `#E8D5A8`) — no gradients, vignettes, or spotlight circles — and sprites render with **no drop shadow, border, or card background**, so each drawing's own sand-colored background blends invisibly into the floor. The **name bubble floats above** the character image (HP bar below).
- **Creativity star badges:** each character's action drawing on the battlefield carries **star badges along its bottom edge** (⭐ = tier 1, ⭐⭐ = tier 2, ⭐⭐⭐ = tier 3 DEVASTATING; tier 0 shows none) — so the whole couch can see, at a glance, whose drawing earned what. Badges persist with the action image until the next action replaces it.
- **Gremlin traps on screen:** the drawn trap renders **medium-sized under a “Trap” label** in its planted zone and stays until triggered (multiple traps in one zone spread out side by side); KO'd characters have no sprite, HP bar, or rail slot at all.
- **Creativity star badges:** every character's action image on the battlefield carries a row of **star badges along its bottom edge** showing that action's creativity score (⭐ to ⭐⭐⭐; tier 0 shows a dimmed single outline star). The badges persist as long as the action image does, so the couch can see at a glance who drew well this round.
- **Move animation on reveal:** when a character's beat is revealed, if their move relocated them (CHARGE or ESCAPE), the host **animates their sprite travelling from its old zone to the new zone**, landing there as the beat plays — the sprite moves into its new zone immediately during that reveal, not at end of round. For CHARGE and ESCAPE the movement and the attack are **one combined beat** (a single narration line + one animation), never split.
- **Health bars update per reveal, not per round.** Each HP bar changes only as *that* beat is revealed (damage, heal, reflect) — never pre-applied at round start; the couch sees cause then effect.
- **Effects fire once per moment (on the play-by-play line).** All battlefield feedback for a given engine event — the acting-drawing zoom, floating damage/heal numbers, red-shake / blue-pop borders, PROTECT's glow, HP steps, and the stinger — plays **exactly once**, on the beat that owns that event: the **play-by-play** beat when one narrates it, otherwise whichever beat does. A **color-commentator aside on the same moment adds only its line** — no second zoom or number — so the couch never sees the same sprite grow twice for two announcer comments. Standalone moments the color commentator owns outright (a reflect bouncing back, a sprung trap) still animate normally — they're the sole beat for their event.
- **Action badge:** once a character's move is revealed, a small badge with the move name (**SMASH / BLAST / CHARGE / ESCAPE / PROTECT**) sits below their sprite for the rest of the round.
- **Big action label on reveal:** while a character's action is being revealed, a **large label rides across the top of their drawing naming the selected move and its target** ("CHARGE → Blob"; the move alone if it has no target). It's a child of the sprite, so it **scales up with the acting zoom** for a couch-readable callout, and clears the moment the next beat plays (only the currently-revealing fighter carries one).
- **PROTECT glow:** when a PROTECT beat is revealed, the shielded ally gains a **blue glow around their sprite that persists the entire round** (matching the shield's duration).
- **Action images persist.** Once a character's action is revealed, that action drawing *becomes* their battlefield sprite and stays until their next action replaces it — the arena accumulates the round's chaos (laser-firing Stabby stays laser-firing through the next drawing phase). Characters who haven't acted yet show their original character image.
- **Narration log:** announcer text is a **running, chat-style log** (newest at bottom), not transient captions. The current beat types out in a bright gold-bordered card, then rolls up into dimmed history (smaller, ~55% opacity) when the next beat starts. **Round dividers** ("— Round 3: *The Fish Learns to Surf* —") separate rounds; roughly the last 2–3 rounds stay on screen behind a top fade mask; DEVASTATING/KO/combo lines keep a subtle gold tint in history so highlights stay findable; speaker chips (PBP/COLOR) persist so re-read banter still reads as dialogue. The log remains visible during the deliberation interlude — re-reading last round's jokes is the latency mask. The full transcript always persists to the room snapshot (feeds the match poster's "best line").
- Host reveal pacing: beats advance on a timer (config `beat_seconds: 6`) with a host "next" override button; kids reading speed matters. When a character's beat plays, their action drawing **enlarges by a configurable scale for a configurable duration** (`reveal_action_zoom_scale: 1.8`, `reveal_action_zoom_seconds: 2.5`) so the couch can appreciate the artwork, then shrinks back to sprite size.
- **Creativity + damage readout on the host screen:** when a character's action is revealed, the screen shows a **one-line addition** that anyone can follow — same icons as the phone buttons, left to right, ending in the number that actually hit:

  > 🎯 **BLAST** → 🎲 5 + 🌀 Weird 5 + ⭐⭐ Creative 3 = **13 damage**

  Reductions are a **separate second line**, never a rewrite of the first:

  > 🛡️ Gerald's shield blocks 7 → **4 damage** gets through
  > 🛡️ Blob's shield reflects 3 back at Stabby!

  Rules that keep it readable: **one addition, one total, per line** (never two different numbers with arrows between them); **omit zero terms** (creativity 0 simply doesn't appear); star count *is* the creativity tier, so "draw better → bigger number" is legible without explaining tiers; and a tier-3 result swaps the star chip for a **⭐⭐⭐ DEVASTATING!** flourish. Heals use the same shape (❤️ PROTECT → 🎲 4 + 🌀 Weird 6 + ⭐ Creative 1 = **11 healed**). Copy/format in settings.yaml.
- **Impact feedback during reveals:** any character *negatively* affected by the current beat (damage) flashes a **red border with a shake**; any character *positively* affected (heal, shield) flashes a **light-blue border with a scale "pop"**. Both derive from the beat's engine events, so they're always accurate to the math.
- **Floating combat numbers:** every damage event spawns a big **red number** that floats up from the affected character and fades; healing spawns a **green** one (config `float_number_seconds: 1.5`). Crits render extra-large with an exclamation. Numbers come from engine events, so they always match the HP bars.
- **Instant replay:** when a beat contains a DEVASTATING hit or a KO (config `instant_replay.triggers`), the host replays that beat once in slow-mo — bigger zoom, slower shake, a "REPLAY" banner and stinger — before advancing. Pure presentation over existing beat data; `instant_replay.enabled` toggles it.
- **Initiative Order column:** a vertical rail down the **left side** of the host screen, titled "Initiative Order," showing each character's **original character image** top-to-bottom in the acting order of the round currently being revealed — players always know when to expect their character's moment. Beside each portrait, a **compact stat strip (💪 / ⚡ / 🌀 with numbers)** — the rail is the stats' home on the common screen (the battlefield stays clean), and since the rail is ordered by Speed, the numbers visibly explain the ordering; strips pulse when a value changes (montage, transform). When order changes (transforms, ties rerolled), the portraits **animate to their new positions**; KO'd characters drop off the rail (Gremlins get a small imp badge at the bottom).
- **Tug-of-war meters:** two cartoony horizontal meters below the battlefield, each a rope with a knot marker sliding between the team colors. **Top — "Who's Winning":** knot position reflects relative team HP share, tweening as damage and healing land during beats. **Bottom — "Crowd Favorite":** knot reflects which team the audience is rooting for, driven by accumulated creativity bonuses per team (config `audience_recent_rounds: 3` weights recent rounds so momentum can swing). The two meters disagreeing — losing on HP but winning the crowd — is exactly the story the couch wants to see.
- **Audio:** move sounds come from curated free sound packs (CC0 sources like Kenney.nl, Mixkit, Freesound), mapped per move via an `sfx` key on each `moves.yaml` entry; **event stingers** (DEVASTATING → crowd roar, reflect → boing, trap springs → comic snap, KO → bell + gasp, combo → air horn, sudden death → drumroll) map from engine event types via an `events_sfx` block in settings.yaml. Host page plays them through a small Web Audio manager with volume/mute controls and ±10% pitch variation so repeats don't sound robotic.
- Accessibility: colorblind-safe team palettes (impact borders also differ in animation — shake vs pop — not just color), min font sizes, all-caps avoided in narration body.

## 14. Tuning Guide (for the human designer)

Want faster games? Lower `hp_base`. Too swingy? Reduce `creativity_tier_3` from +5→+4. PROTECT too strong? Lower `reflect_per_weird` (5%→4%) or its cap. Kids losing? Raise `underdog_bonus`. One move dominating playtests? Its whole formula is one line in moves.yaml. Every knob named in this doc exists in `balance.yaml` with a comment. Change YAML → start a new room → new rules apply. After each game night, skim the **flavor log** (`snapshots/<room>/flavor.jsonl`) — recurring notes about drawings that didn't fit any of the five moves are your signal for what the catalog might be missing.

## 15. The Doodle Crowd (cross-session legacy)

Every character ever drawn persists to a `gallery/` folder (PNG + AI-given name + match record; plain files, no database). When `gallery_enabled: true`, the host renders a rotating handful of past characters as **tiny spectators in the colosseum stands**, and the narrate prompt receives 2–3 random gallery names each round so the announcers can drop cameos ("Princess Stabby watches from the stands. She is judging."). New players literally see the family history they're joining, and every match adds to the crowd. Gallery entries can be deleted by removing files; a config cap keeps the stands from becoming a mob.
