// arena.js — renders the battlefield on the TV: three zone bands with each
// fighter's drawing as a bobbing sprite, HP bars, and condition emojis. Also
// exposes sprite-swap / KO helpers used by the reveal sequencer.
//
// Exposes window.Arena.

(function () {
  const COND_EMOJI = {burning:'🔥',soggy:'💧',sticky:'🟢',prone:'🙃',frightened:'😱',
    embarrassed:'😳',enraged:'😡',sparkly:'✨',hidden:'👻',off_balance:'😵',
    pumped:'💪',confused:'🌀',shielded:'🛡',transformed:'🦋'};
  const ZONE_LABEL = {glitter_back:'✨ Glitter Backline', frontline:'⚔ Frontline',
    thunder_back:'⚡ Thunder Backline'};

  class Arena {
    constructor(root) { this.root = root; this.sprites = {}; this.zoneEls = {}; this.zoneIds = []; }

    setup(zoneIds) {
      this.zoneIds = zoneIds;
      this.root.innerHTML = '';
      this.zoneEls = {};
      for (const z of zoneIds) {
        const band = document.createElement('div');
        band.className = 'zone';
        band.innerHTML = '<div class="zoneLabel">' + (ZONE_LABEL[z] || z) + '</div>'
          + '<div class="zoneRow"></div>';
        this.root.appendChild(band);
        this.zoneEls[z] = band.querySelector('.zoneRow');
      }
      this.sprites = {};
    }

    // chars: list of {player_id,name,zone_id,hp,max_hp,conditions,team_id,is_ko,png}
    render(chars) {
      if (!this.zoneIds.length) return;
      for (const c of chars) {
        let s = this.sprites[c.player_id];
        if (!s) s = this._make(c);
        const row = this.zoneEls[c.zone_id] || this.zoneEls[this.zoneIds[0]];
        if (s.el.parentElement !== row) row.appendChild(s.el);
        s.origPng = c.png || s.origPng || '';
        if (!s.swapped) this._setImg(s, s.origPng);
        s.el.style.setProperty('--team', c.team_id === 'team_b' ? '#3b82f6' : '#ec4899');
        this.setHP(c.player_id, c.hp, c.max_hp);
        this.setConditions(c.player_id, c.conditions);
        s.el.classList.toggle('ko', !!c.is_ko);
        s.name.textContent = c.name;
      }
    }

    _make(c) {
      const el = document.createElement('div');
      el.className = 'sprite';
      el.innerHTML =
        '<div class="pic"></div>' +
        '<div class="nm"></div>' +
        '<div class="hpbar"><i></i></div>' +
        '<div class="chips"></div>';
      const s = {
        el, pic: el.querySelector('.pic'), name: el.querySelector('.nm'),
        hp: el.querySelector('.hpbar > i'), chips: el.querySelector('.chips'),
        origPng: c.png || '', swapped: false,
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
      s.hp.style.background = pct > 40 ? 'var(--good)' : 'var(--bad)';
    }

    setConditions(pid, conds) {
      const s = this.sprites[pid]; if (!s) return;
      s.chips.textContent = Object.keys(conds || {}).map(k => COND_EMOJI[k] || '•').join(' ');
    }

    // Reveal helpers -----------------------------------------------------
    swapSprite(pid, dataUrl) {
      const s = this.sprites[pid]; if (!s || !dataUrl) return;
      s.swapped = true; this._setImg(s, dataUrl);
      s.el.classList.add('acting');
    }
    revertSprite(pid) {
      const s = this.sprites[pid]; if (!s) return;
      s.swapped = false; this._setImg(s, s.origPng);
      s.el.classList.remove('acting');
    }
    ko(pid) {
      const s = this.sprites[pid]; if (!s) return;
      s.el.classList.add('ko');
    }
    flashHit(pid) {
      const s = this.sprites[pid]; if (!s) return;
      s.el.classList.remove('hit');
      void s.el.offsetWidth;          // reflow so the animation restarts if re-hit
      s.el.classList.add('hit');
      clearTimeout(s._hitT);
      s._hitT = setTimeout(() => s.el.classList.remove('hit'), 900);
    }
  }

  window.Arena = Arena;
})();
