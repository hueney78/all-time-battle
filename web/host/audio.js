// audio.js — the host page's Web Audio manager (GAME_DESIGN.md §13 audio).
// Plays per-move clips (moves.yaml `sfx` keys, shipped on each reveal beat)
// and event stingers (settings.yaml ui.audio.events_sfx) from the sfx pack in
// web/host/assets/sfx/. ±pitch_variation random playback rate keeps repeats
// from sounding robotic. Volume/mute persist in localStorage.
//
// Browsers block audio until a user gesture — the manager arms itself on the
// first pointer/key event, so sound simply starts once the host clicks
// anything (e.g. Start Game). Exposes window.DoodleAudio.

(function () {
  function DoodleAudio(cfg) {
    cfg = cfg || {};
    const DIR = cfg.sfx_dir || '/static/host/assets/sfx';
    const PITCH_VAR = cfg.pitch_variation == null ? 0.10 : cfg.pitch_variation;
    const enabled = cfg.enabled !== false;

    let ctx = null, master = null;
    const buffers = {};                // clip name → Promise<AudioBuffer|null>
    let volume = clampVol(localStorage.getItem('db_volume'), cfg.volume);
    let muted = localStorage.getItem('db_muted') === '1';

    function clampVol(stored, fallback) {
      const v = stored == null ? fallback : parseFloat(stored);
      return Math.max(0, Math.min(1, isNaN(v) || v == null ? 0.8 : v));
    }
    function applyGain() {
      if (master) master.gain.value = muted ? 0 : volume;
    }
    // Create/resume the AudioContext — must be called from a user gesture.
    function arm() {
      if (!enabled) return;
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!ctx) {
        ctx = new AC();
        master = ctx.createGain();
        master.connect(ctx.destination);
        applyGain();
      }
      if (ctx.state === 'suspended') ctx.resume();
    }
    ['pointerdown', 'keydown'].forEach(evt =>
      document.addEventListener(evt, arm, { capture: true }));

    function load(name) {
      if (!buffers[name]) {
        buffers[name] = fetch(DIR + '/' + name + '.wav')
          .then(r => { if (!r.ok) throw new Error(name); return r.arrayBuffer(); })
          .then(ab => ctx.decodeAudioData(ab))
          .catch(() => null);           // missing clip = silent, never fatal
      }
      return buffers[name];
    }

    // Play a clip by name; delayMs staggers stingers behind the move sound.
    function play(name, delayMs) {
      if (!enabled || !name || !ctx || ctx.state !== 'running' || muted) return;
      load(name).then(buf => {
        if (!buf) return;
        const src = ctx.createBufferSource();
        src.buffer = buf;
        src.playbackRate.value = 1 + (Math.random() * 2 - 1) * PITCH_VAR;
        src.connect(master);
        src.start(ctx.currentTime + (delayMs || 0) / 1000);
      });
    }

    return {
      play: play,
      arm: arm,
      get volume() { return volume; },
      get muted() { return muted; },
      setVolume: function (v) {
        volume = Math.max(0, Math.min(1, v));
        localStorage.setItem('db_volume', String(volume));
        applyGain();
      },
      toggleMute: function () {
        muted = !muted;
        localStorage.setItem('db_muted', muted ? '1' : '0');
        applyGain();
        return muted;
      },
    };
  }

  window.DoodleAudio = DoodleAudio;
})();
