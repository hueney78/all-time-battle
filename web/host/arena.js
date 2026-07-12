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

    // chars: {player_id,name,zone_id,hp,max_hp,team_id,is_ko,png,sprite_png}
    render(chars) {
      if (!this.zoneIds.length) return;
      for (const c of chars) {
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
        s.el.classList.toggle('ko', !!c.is_ko);
        s.name.textContent = c.name;
      }
    }

    _make(c) {
      const el = document.createElement('div');
      el.className = 'fighter';
      el.innerHTML =
        '<div class="pic"></div>' +
        '<div class="nametag"></div>' +
        '<div class="hpbar"><i></i></div>';
      const s = {
        el, pic: el.querySelector('.pic'), name: el.querySelector('.nametag'),
        hp: el.querySelector('.hpbar > i'),
        spritePng: c.sprite_png || c.png || '',
      };
      this.sprites[c.player_id] = s;
      return s;
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

    // Floating combat number: red damage / green heal, crit oversized.
    floatNumber(pid, amount, kind, crit, mult) {
      const s = this.sprites[pid]; if (!s) return;
      const ms = FLOAT_MS * (mult || 1);
      const n = document.createElement('span');
      n.className = 'floatnum' + (kind === 'heal' ? ' heal' : '') + (crit ? ' crit' : '');
      n.textContent = (kind === 'heal' ? '+' : '−') + amount + (crit ? '!' : '');
      n.style.animationDuration = (ms / 1000) + 's';
      s.el.appendChild(n);
      setTimeout(() => n.remove(), ms);
    }
  }

  window.Arena = Arena;
})();
