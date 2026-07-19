// arena.js — renders the battlefield on the TV: zone bands over the CSS
// colosseum, each fighter's PERSISTENT action drawing as a sprite (its most
// recent revealed action, original portrait until it first acts), and HP bars.
// Exposes reveal helpers used by the sequencer: sprite swap, zoom, red-shake /
// blue-pop impact borders, and floating combat numbers.
//
// All drawings/amounts come from server state + engine events — arena.js never
// invents outcomes. Exposes window.Arena.

(function () {
  const CFG = window.DOODLE_CONFIG || {};
  const FLOAT_MS = (CFG.float_number_seconds || 1.5) * 1000;

  class Arena {
    constructor(root) {
      this.root = root;                 // the .arena element
      this.sprites = {};
      this.zoneEls = {};
      this.zoneIds = [];
      if (CFG.arena_background) {        // optional custom image override
        this.root.classList.add('custombg');
        this.root.style.backgroundImage = 'url(' + CFG.arena_background + ')';
      }
    }

    // zones: [{id, label}] (server-composed labels — team backlines carry the
    // team name, "Team A/B" until the intro reveal swaps in the AI names).
    setup(zones) {
      this.zoneIds = zones.map(z => z.id || z);
      // Keep the arena's ::before arches / background; only (re)build the zones.
      this.zones = document.createElement('div');
      this.zones.className = 'zones';
      this.zoneEls = {};
      for (const z of zones) {
        const id = z.id || z;
        const band = document.createElement('div');
        band.className = 'zone';
        band.innerHTML = '<span class="zonelabel"></span>';
        band.querySelector('.zonelabel').textContent = z.label || id;
        this.zones.appendChild(band);
        this.zoneEls[id] = band;
      }
      this.root.appendChild(this.zones);
      this.sprites = {};
    }

    // Re-label existing bands (the team-name reveal renames the backlines).
    setLabels(zones) {
      for (const z of zones || []) {
        const band = this.zoneEls[z.id || z];
        if (band && z.label) band.querySelector('.zonelabel').textContent = z.label;
      }
    }

    // Arena Gremlin traps (GAME_DESIGN §10): small drawn icons that sit in their
    // planted zone until an enemy triggers them. traps: [{trap_id, zone_id,
    // creativity, png}]. Re-rendered wholesale from arena_state each update.
    setTraps(traps) {
      for (const band of Object.values(this.zoneEls)) {
        band.querySelectorAll('.trap').forEach(t => t.remove());
      }
      const byZone = {};
      for (const tr of traps || []) {
        (byZone[tr.zone_id] = byZone[tr.zone_id] || []).push(tr);
      }
      const SPACING = 78;   // px between multiple traps sharing one zone
      for (const [zoneId, list] of Object.entries(byZone)) {
        const band = this.zoneEls[zoneId];
        if (!band) continue;
        list.forEach((tr, i) => {
          const el = document.createElement('div');
          el.className = 'trap';
          // Center the group under the zone; spread extras left/right around it.
          const offset = Math.round((i - (list.length - 1) / 2) * SPACING);
          el.style.left = 'calc(50% + ' + offset + 'px)';
          // The drawn trap image, medium-sized, with a "Trap" label above it.
          const label = document.createElement('span');
          label.className = 'traplabel';
          label.textContent = 'Trap';
          const img = document.createElement('div');
          img.className = 'trapimg';
          if (tr.png) img.style.backgroundImage = 'url(' + tr.png + ')';
          else img.textContent = '🪤';       // fallback if the drawing is missing
          el.appendChild(label);
          el.appendChild(img);
          el.title = 'a lurking trap';
          band.appendChild(el);
        });
      }
    }

    // -- the Doodle Crowd: rotating spectators in the stands (GAME_DESIGN §15) --
    // The host receives the full gallery roster once (bootstrap message); we show
    // a shuffled handful and reshuffle every stands.rotate_seconds. Purely
    // cosmetic — the stands band sits ABOVE the zones so it never obscures the
    // battlefield. Spectators need a drawing, so png-less entries are skipped.
    setSpectators(list) {
      this._roster = (list || []).filter(e => e && e.png);
      if (!this._stands) {
        this._stands = document.createElement('div');
        this._stands.className = 'stands';
        this.root.appendChild(this._stands);
      }
      clearInterval(this._standsTimer);
      const cfg = CFG.stands || {};
      this._standsMax = cfg.max == null ? 14 : cfg.max;
      this._drawStands();
      const every = (cfg.rotate_seconds || 0) * 1000;
      if (every > 0 && this._roster.length > this._standsMax) {
        this._standsTimer = setInterval(() => this._drawStands(), every);
      }
    }

    _drawStands() {
      if (!this._stands) return;
      const pool = (this._roster || []).slice();
      for (let i = pool.length - 1; i > 0; i--) {        // Fisher–Yates shuffle
        const j = Math.floor(Math.random() * (i + 1));
        [pool[i], pool[j]] = [pool[j], pool[i]];
      }
      const show = pool.slice(0, Math.max(0, this._standsMax || 0));
      this._stands.innerHTML = '';
      show.forEach((e, i) => {
        const s = document.createElement('div');
        s.className = 'spectator';
        s.style.backgroundImage = 'url(' + e.png + ')';
        s.style.setProperty('--team', e.team_id === 'team_b' ? '#2F6FE0' : '#E24FA0');
        s.style.animationDelay = (i * 0.18).toFixed(2) + 's';   // staggered idle bob
        if (e.name) s.title = e.name;
        this._stands.appendChild(s);
      });
    }

    // chars: {player_id,name,zone_id,hp,max_hp,team_id,is_ko,png,sprite_png,action_creativity}
    render(chars) {
      if (!this.zoneIds.length) return;
      for (const c of chars) {
        // KO'd fighters are removed from the battlefield entirely (§13): no
        // sprite, HP bar, or star badges — only the rail's imp badge remains.
        if (c.is_ko) {
          const gone = this.sprites[c.player_id];
          if (gone) { gone.el.remove(); delete this.sprites[c.player_id]; }
          continue;
        }
        let s = this.sprites[c.player_id];
        if (!s) s = this._make(c);
        const zone = this.zoneEls[c.zone_id] || this.zoneEls[this.zoneIds[0]];
        if (s.el.parentElement !== zone) zone.appendChild(s.el);
        // Persistent battlefield sprite = server's sprite_png (latest revealed
        // action), falling back to the original portrait.
        s.spritePng = c.sprite_png || c.png || s.spritePng || '';
        this._setImg(s, s.spritePng);
        s.el.style.setProperty('--team', c.team_id === 'team_b' ? '#2F6FE0' : '#E24FA0');
        this.setHP(c.player_id, c.hp, c.max_hp);
        this.setStars(c.player_id, c.action_creativity || 0);
        s.name.textContent = c.name;
      }
    }

    _make(c) {
      const el = document.createElement('div');
      el.className = 'fighter';
      el.innerHTML =
        '<div class="actionlabel"></div>' +
        '<div class="pic"></div>' +
        '<div class="stars"></div>' +
        '<div class="actionbadge"></div>' +
        '<div class="nametag"></div>' +
        '<div class="hpbar"><i></i></div>';
      const s = {
        el, pic: el.querySelector('.pic'), name: el.querySelector('.nametag'),
        hp: el.querySelector('.hpbar > i'), stars: el.querySelector('.stars'),
        badge: el.querySelector('.actionbadge'),
        label: el.querySelector('.actionlabel'),
        spritePng: c.sprite_png || c.png || '',
      };
      this.sprites[c.player_id] = s;
      return s;
    }

    // Creativity star badges under the action drawing (§13): ⭐ per tier, tier 3
    // is DEVASTATING (⭐⭐⭐); tier 0 shows none.
    setStars(pid, tier) {
      const s = this.sprites[pid]; if (!s || !s.stars) return;
      const t = Math.max(0, Math.min(3, tier | 0));
      s.stars.textContent = '⭐'.repeat(t);
    }

    // The move-name badge under a fighter once their move is revealed
    // (SMASH/BLAST/CHARGE/ESCAPE/PROTECT — v6 §13). Persists the round; cleared
    // at the next reveal's start.
    setActionBadge(pid, name) {
      const s = this.sprites[pid]; if (!s || !s.badge) return;
      s.badge.textContent = name || '';
      s.badge.classList.toggle('show', !!name);
    }
    clearBadges() {
      for (const s of Object.values(this.sprites)) {
        if (s.badge) { s.badge.textContent = ''; s.badge.classList.remove('show'); }
      }
    }

    // The big label OVER the acting fighter's drawing as their move is revealed
    // (§13) — the move name and, when the move has one, its target ("CHARGE →
    // Blob"). It rides the fighter's acting zoom (it's a child of .fighter), so
    // it blows up with the sprite. Only the currently-revealing fighter carries
    // one: the sequencer clears them at the start of each beat.
    setActionLabel(pid, text) {
      const s = this.sprites[pid]; if (!s || !s.label) return;
      s.label.textContent = text || '';
      s.label.classList.toggle('show', !!text);
    }
    clearActionLabels() {
      for (const s of Object.values(this.sprites)) {
        if (s.label) { s.label.textContent = ''; s.label.classList.remove('show'); }
      }
    }

    // PROTECT's round-long blue glow on the shielded ally (v6 §13).
    setShield(pid) {
      const s = this.sprites[pid]; if (s) s.el.classList.add('shielded');
    }
    clearShields() {
      for (const s of Object.values(this.sprites)) s.el.classList.remove('shielded');
    }

    _setImg(s, dataUrl) {
      s.pic.style.backgroundImage = dataUrl ? ('url(' + dataUrl + ')') : 'none';
    }

    setHP(pid, hp, max) {
      const s = this.sprites[pid]; if (!s) return;
      const pct = Math.max(0, Math.round(100 * hp / Math.max(1, max)));
      s.hp.style.width = pct + '%';
      s.el.querySelector('.hpbar').classList.toggle('low', pct <= 30);
    }

    ko(pid) {
      const s = this.sprites[pid]; if (!s) return;
      s.el.classList.add('ko');
    }

    // Current sprite image for a fighter (the intro showcase blows it up).
    spriteUrl(pid) {
      const s = this.sprites[pid];
      return s ? s.spritePng : '';
    }

    // -- reveal helpers ---------------------------------------------------
    // The played action drawing BECOMES the sprite and stays (no revert).
    swapSprite(pid, dataUrl) {
      const s = this.sprites[pid]; if (!s || !dataUrl) return;
      s.spritePng = dataUrl;
      this._setImg(s, dataUrl);
    }
    // Animate a fighter travelling from its current zone into `zoneId` as its
    // beat plays — CHARGE/ESCAPE relocate the sprite DURING the reveal, not at
    // end of round (v6 §13). FLIP: reparent into the destination band, then
    // transition the sprite from its old screen position to the new one. The
    // translate composes with the acting zoom via the --mx/--my CSS vars, so it
    // never fights the scale. mult > 1 stretches the travel for the replay.
    moveTo(pid, zoneId, mult) {
      const s = this.sprites[pid]; if (!s) return;
      const dest = this.zoneEls[zoneId];
      if (!dest || s.el.parentElement === dest) return;
      if (!s.el.getBoundingClientRect) { dest.appendChild(s.el); return; }  // no layout (tests)
      const first = s.el.getBoundingClientRect();
      dest.appendChild(s.el);
      const last = s.el.getBoundingClientRect();
      const dx = Math.round(first.left - last.left), dy = Math.round(first.top - last.top);
      if (!dx && !dy) return;
      const secs = (CFG.reveal_move_seconds || 0.7) * (mult || 1);
      s.el.style.transition = 'none';
      s.el.style.setProperty('--mx', dx + 'px');
      s.el.style.setProperty('--my', dy + 'px');
      void s.el.offsetWidth;                       // commit the pre-move offset
      s.el.style.transition = 'transform ' + secs + 's cubic-bezier(.34,1.25,.5,1)';
      s.el.style.setProperty('--mx', '0px');
      s.el.style.setProperty('--my', '0px');
      clearTimeout(s._moveT);
      s._moveT = setTimeout(() => { s.el.style.transition = ''; }, secs * 1000 + 80);
    }
    // Zoom the acting fighter up (scale/duration from config) then settle back.
    // mult > 1 = slow-mo (instant replay) — every duration stretches by it.
    actUp(pid, mult) {
      const s = this.sprites[pid]; if (!s) return;
      s.el.classList.add('acting');
      clearTimeout(s._zoomT);
      const secs = (CFG.reveal_action_zoom_seconds || 2.5) * 1000 * (mult || 1);
      s._zoomT = setTimeout(() => s.el.classList.remove('acting'), secs);
    }
    settle(pid) {
      const s = this.sprites[pid]; if (!s) return;
      clearTimeout(s._zoomT);
      s.el.classList.remove('acting');
    }
    settleAll() { for (const pid of Object.keys(this.sprites)) this.settle(pid); }

    // Impact border: red + shake (hurt) or light-blue + pop (helped).
    // mult > 1 slows the shake/pop for instant replay.
    impact(pid, kind, mult) {
      const s = this.sprites[pid]; if (!s) return;
      const m = mult || 1;
      const cls = kind === 'helped' ? 'helped' : 'hit';
      s.el.classList.remove('hit', 'helped');
      void s.el.offsetWidth;                    // restart the animation if re-hit
      s.pic.style.animationDuration = m > 1 ? ((kind === 'helped' ? 1.2 : 0.5) * m + 's') : '';
      s.el.classList.add(cls);
      clearTimeout(s._impT);
      s._impT = setTimeout(() => {
        s.el.classList.remove(cls);
        s.pic.style.animationDuration = '';
      }, 1200 * m);
    }

    // Floating combat number: red damage / green heal, DEVASTATING oversized.
    floatNumber(pid, amount, kind, devastating, mult) {
      const s = this.sprites[pid]; if (!s) return;
      const ms = FLOAT_MS * (mult || 1);
      const n = document.createElement('span');
      n.className = 'floatnum' + (kind === 'heal' ? ' heal' : '')
                  + (devastating ? ' devastating' : '');
      n.textContent = (kind === 'heal' ? '+' : '−') + amount + (devastating ? '!' : '');
      n.style.animationDuration = (ms / 1000) + 's';
      s.el.appendChild(n);
      setTimeout(() => n.remove(), ms);
    }
  }

  window.Arena = Arena;
})();
