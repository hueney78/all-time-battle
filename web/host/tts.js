// tts.js — host-screen Text-to-Speech for the announcer duo (GAME_DESIGN §13).
// Uses the browser's Web Speech API (window.speechSynthesis) to read each
// revealed announcer beat aloud, giving each announcer a DIFFERENT, configurable
// voice (pbp vs color). All knobs come from ui.tts (settings.yaml) via
// window.DOODLE_CONFIG. Purely presentational — it never invents text, it only
// speaks what the reveal sequencer hands it. Degrades to a silent no-op where
// the browser has no Web Speech API. Exposes window.DoodleTTS.

(function () {
  function clampNum(v, lo, hi, dflt) {
    v = Number(v);
    if (!isFinite(v)) return dflt;
    return Math.max(lo, Math.min(hi, v));
  }

  // Emoji, arrows, and bracketed stat tags ("[+1 ⚡]") read terribly aloud —
  // strip them so the announcer speaks clean prose.
  function sanitize(text) {
    return String(text == null ? '' : text)
      .replace(/\[[^\]]*\]/g, ' ')     // [+1 ⚡] montage/system tags
      .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2190}-\u{21FF}\u{2B00}-\u{2BFF}\u{FE0F}\u{200D}]/gu, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  // Returned when the browser can't speak — every method is a safe no-op so the
  // host page (and the JS test harness) never has to special-case it.
  function stub() {
    return {
      speak: function () {}, cancel: function () {},
      setEnabled: function () { return false; }, toggle: function () { return false; },
      get enabled() { return false; }, get available() { return false; },
    };
  }

  function DoodleTTS(cfg) {
    cfg = cfg || {};
    const synth = (typeof window !== 'undefined') && window.speechSynthesis;
    if (!synth) return stub();

    const voiceCfg = cfg.voices || {};
    const state = {
      enabled: cfg.enabled !== false,
      rate: clampNum(cfg.rate, 0.1, 10, 1),
      pitch: clampNum(cfg.pitch, 0, 2, 1),
      volume: clampNum(cfg.volume, 0, 1, 1),
      resolved: {},                    // speaker → SpeechSynthesisVoice | null
    };

    // Resolve one announcer's configured voice against what the browser has:
    // exact-ish name match first, then language, preferring a voice not already
    // taken by the other announcer so the two always differ when possible.
    function pickVoice(spec, used) {
      const voices = synth.getVoices() || [];
      if (!voices.length) return null;
      const name = (spec.name || '').toLowerCase();
      const lang = (spec.lang || '').toLowerCase();
      let v = null;
      if (name) v = voices.find(function (o) { return (o.name || '').toLowerCase().indexOf(name) >= 0; });
      if (!v && lang) v = voices.find(function (o) { return (o.lang || '').toLowerCase().indexOf(lang) === 0 && !used.has(o.name); });
      if (!v && lang) v = voices.find(function (o) { return (o.lang || '').toLowerCase().indexOf(lang) === 0; });
      if (!v) v = voices.find(function (o) { return /^en/i.test(o.lang || '') && !used.has(o.name); });
      if (!v) v = voices.find(function (o) { return !used.has(o.name); });
      return v || voices[0] || null;
    }

    function resolveVoices() {
      const used = new Set();
      ['pbp', 'color'].forEach(function (sp) {
        const v = pickVoice(voiceCfg[sp] || {}, used);
        state.resolved[sp] = v;
        if (v) used.add(v.name);
      });
      // Log the roster so a host can copy real voice names into settings.yaml.
      const list = synth.getVoices() || [];
      if (list.length) {
        try {
          console.info('[TTS] available voices:',
            list.map(function (v) { return v.name + ' [' + v.lang + ']'; }).join(', '));
          console.info('[TTS] pbp →', state.resolved.pbp && state.resolved.pbp.name,
            '| color →', state.resolved.color && state.resolved.color.name);
        } catch (e) { /* ignore */ }
      }
    }
    resolveVoices();
    // Voices often load asynchronously — re-resolve when the browser announces them.
    try { synth.addEventListener('voiceschanged', resolveVoices); }
    catch (e) { try { synth.onvoiceschanged = resolveVoices; } catch (e2) { /* ignore */ } }

    function speak(text, speaker) {
      if (!state.enabled) return;
      const clean = sanitize(text);
      if (!clean) return;
      const sp = speaker === 'color' ? 'color' : 'pbp';
      const spec = voiceCfg[sp] || {};
      let u;
      try { u = new SpeechSynthesisUtterance(clean); } catch (e) { return; }
      const v = state.resolved[sp];
      if (v) { u.voice = v; if (v.lang) u.lang = v.lang; }
      else if (spec.lang) u.lang = spec.lang;
      u.pitch = clampNum(spec.pitch != null ? spec.pitch : state.pitch, 0, 2, state.pitch);
      u.rate = clampNum(spec.rate != null ? spec.rate : state.rate, 0.1, 10, state.rate);
      u.volume = state.volume;
      try {
        // Drop the previous beat's speech so audio stays synced to the beat the
        // couch is looking at (matters when the host skips ahead with Next ▶).
        if (synth.speaking || synth.pending) synth.cancel();
        synth.speak(u);
      } catch (e) { /* ignore */ }
    }

    function cancel() { try { synth.cancel(); } catch (e) { /* ignore */ } }
    function setEnabled(on) {
      state.enabled = !!on;
      if (!state.enabled) cancel();
      return state.enabled;
    }
    function toggle() { return setEnabled(!state.enabled); }

    return {
      speak: speak, cancel: cancel, setEnabled: setEnabled, toggle: toggle,
      get enabled() { return state.enabled; },
      get available() { return true; },
    };
  }

  if (typeof window !== 'undefined') window.DoodleTTS = DoodleTTS;
})();
