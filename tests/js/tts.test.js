'use strict';
// Node unit check for web/host/tts.js — the announcer Text-to-Speech manager.
// Loads the browser IIFE in a vm sandbox with a fake Web Speech API (three
// voices, recording SpeechSynthesisUtterance) and asserts:
//   1. pbp and color resolve to DIFFERENT voices (the core requirement);
//   2. each announcer honors its configured lang/pitch/rate;
//   3. emoji + bracketed [+1 ⚡] tags are stripped before speaking;
//   4. a disabled manager stays silent.
// Run directly (`node tts.test.js`) or via tests/test_tts_js.py.

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const spoken = [];
class FakeUtterance {
  constructor(t) { this.text = t; this.voice = null; this.lang = ''; this.pitch = 1; this.rate = 1; this.volume = 1; }
}
const voices = [
  { name: 'Google US English', lang: 'en-US' },
  { name: 'Google UK English Male', lang: 'en-GB' },
  { name: 'Samantha', lang: 'en-US' },
];
const synth = {
  speaking: false, pending: false,
  getVoices() { return voices; },
  addEventListener() {}, onvoiceschanged: null,
  speak(u) { spoken.push(u); },
  cancel() { this.speaking = false; this.pending = false; },
};

const sandbox = {
  console, Math, Date, JSON, Object, Array, String, Number, Boolean, RegExp, Set,
  isNaN, isFinite, parseInt, parseFloat,
  SpeechSynthesisUtterance: FakeUtterance,
};
sandbox.window = { speechSynthesis: synth };
sandbox.speechSynthesis = synth;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', '..', 'web', 'host', 'tts.js'), 'utf8'), sandbox);

const DoodleTTS = sandbox.window.DoodleTTS;
assert.ok(DoodleTTS, 'tts.js should export window.DoodleTTS');

const tts = DoodleTTS({
  enabled: true,
  voices: {
    pbp: { lang: 'en-US', pitch: 1.2, rate: 1.1 },
    color: { lang: 'en-GB', pitch: 0.8, rate: 0.9 },
  },
});
assert.ok(tts.available, 'tts must be available when speechSynthesis exists');

tts.speak('Sir Lawnmower charges! 🔥 [+1 ⚡]', 'pbp');
tts.speak('It is not.', 'color');
assert.strictEqual(spoken.length, 2, 'both announcer beats spoken');

// 1) DIFFERENT voice per announcer — the core requirement
assert.ok(spoken[0].voice && spoken[1].voice, 'each utterance gets a voice');
assert.notStrictEqual(spoken[0].voice.name, spoken[1].voice.name,
  'pbp and color must use different voices');
// 2) configured lang honored (pbp → en-US, color → en-GB)
assert.strictEqual(spoken[0].voice.lang, 'en-US', 'pbp voice lang');
assert.strictEqual(spoken[1].voice.lang, 'en-GB', 'color voice lang');
// pitch/rate applied per announcer
assert.ok(Math.abs(spoken[0].pitch - 1.2) < 1e-9, 'pbp pitch');
assert.ok(Math.abs(spoken[1].pitch - 0.8) < 1e-9, 'color pitch');
assert.ok(Math.abs(spoken[0].rate - 1.1) < 1e-9, 'pbp rate');

// 3) emoji + bracket tags stripped before speaking
assert.ok(!/[\u{1F000}-\u{1FAFF}\[\]]/u.test(spoken[0].text),
  'emoji and [ ] tags must be stripped: ' + JSON.stringify(spoken[0].text));
assert.ok(spoken[0].text.indexOf('Sir Lawnmower charges') === 0, 'prose kept');

// 4) disabled → silent
tts.setEnabled(false);
const before = spoken.length;
tts.speak('should not speak', 'pbp');
assert.strictEqual(spoken.length, before, 'a disabled manager stays silent');

// unknown speaker falls back to pbp (never throws)
tts.setEnabled(true);
tts.speak('mystery', 'nobody');
assert.strictEqual(spoken[spoken.length - 1].voice.lang, 'en-US', 'unknown speaker → pbp voice');

console.log('OK host tts');
