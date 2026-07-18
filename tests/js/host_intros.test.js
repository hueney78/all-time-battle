'use strict';
// Reproduction harness for the host reveal sequencer (web/host/index.html):
// boots the REAL inline script + the real arena.js in a vm sandbox with a fake
// DOM, captures the socket's onMessage, then drives the intros → Round 1 flow
// exactly as the server does it. Asserts the sequencer runs WITHOUT throwing and
// signals `next_beat` at the end of each reveal (the signal the server waits on
// to advance — if it never fires, the real game "gets stuck on the battlefield").
// Run directly (`node host_intros.test.js`) or via tests/test_arena_js.py.

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

// ---- a permissive-but-real fake DOM element ----
function makeEl() {
  const classes = new Set();
  const style = new Proxy({ _m: {},
    setProperty(k, v) { this._m[k] = v; },
    getPropertyValue(k) { return this._m[k] || ''; },
    removeProperty(k) { delete this._m[k]; } },
    { get(t, k) { return k in t ? t[k] : ''; }, set(t, k, v) { t[k] = v; return true; } });
  const el = {
    tagName: 'DIV', className: '', id: '', title: '', textContent: '', value: '',
    disabled: false, offsetWidth: 1, scrollTop: 0, scrollHeight: 0,
    parentElement: null, children: [], _html: '', style,
    dataset: {}, onclick: null, oninput: null,
    classList: {
      add(c) { classes.add(c); }, remove(...c) { c.forEach(x => classes.delete(x)); },
      toggle(c, on) { const v = on === undefined ? !classes.has(c) : on;
        v ? classes.add(c) : classes.delete(c); return v; },
      contains(c) { return classes.has(c); },
    },
    appendChild(c) {
      if (c && c.parentElement) {
        const k = c.parentElement.children.indexOf(c);
        if (k >= 0) c.parentElement.children.splice(k, 1);
      }
      if (c) c.parentElement = el;
      el.children.push(c); return c;
    },
    insertBefore(c) { return el.appendChild(c); },
    prepend(c) { return el.appendChild(c); },
    removeChild(c) { const k = el.children.indexOf(c); if (k >= 0) el.children.splice(k, 1); return c; },
    remove() { if (el.parentElement) el.parentElement.removeChild(el); },
    querySelector() { return makeEl(); },
    querySelectorAll() { return []; },
    addEventListener() {}, removeEventListener() {},
    getBoundingClientRect() { return { left: 0, top: 0, right: 10, bottom: 10, width: 10, height: 10 }; },
    setAttribute() {}, getAttribute() { return null; },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = v; if (v === '') this.children.length = 0; },
    get firstChild() { return this.children[0] || null; },
    get lastChild() { return this.children[this.children.length - 1] || null; },
  };
  return el;
}

const byId = {};
const document = {
  getElementById(id) { return byId[id] || (byId[id] = Object.assign(makeEl(), { id })); },
  createElement() { return makeEl(); },
  createTextNode(t) { return Object.assign(makeEl(), { textContent: t }); },
  querySelector() { return makeEl(); },
  querySelectorAll() { return []; },
  addEventListener() {},
  documentElement: makeEl(),
  body: makeEl(),
};

// ---- captured side effects ----
const sends = [];              // every sock.send(type, …)
let onMessage = null;          // the host's message handler
const timeouts = [];

const sandbox = {
  console, Math, Date, JSON, Object, Array, String, Number, Boolean, RegExp, isNaN, parseInt, parseFloat,
  document,
  localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
  location: { protocol: 'http:', port: '8000', host: 'localhost:8000', hostname: 'localhost', search: '', href: 'http://localhost:8000/host' },
  navigator: { userAgent: 'node' },
  // Auto-advance timers fire synchronously so the sequencer runs to completion
  // (finish → next_beat) within the test; guarded against runaway depth.
  setTimeout(fn) { if (typeof fn === 'function' && timeouts.length < 5000) { timeouts.push(1); fn(); } return timeouts.length; },
  clearTimeout() {}, setInterval() { return 1; }, clearInterval() {},
  requestAnimationFrame(fn) { if (typeof fn === 'function') fn(); return 1; },
  DoodleAudio() { return { volume: 1, muted: false, toggleMute() { return false; }, setVolume() {}, play() {} }; },
  DoodleSocket(cfg) {
    onMessage = cfg.onMessage;
    const api = { send(type) { sends.push(type); } };
    if (cfg.onOpen) cfg.onOpen(api);
    return api;
  },
};
sandbox.window = sandbox;      // browser-style: globals live on window === global
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

const root = path.join(__dirname, '..', '..', 'web', 'host');
// window.DOODLE_CONFIG (as _inject_config ships it) — use a realistic pace.
sandbox.window.DOODLE_CONFIG = {
  reveal_beat_seconds: 30, reveal_action_zoom_scale: 2.8, reveal_move_seconds: 0.7,
  float_number_seconds: 2.5, instant_replay: { enabled: true, triggers: ['devastating', 'ko'], slowmo_factor: 2 },
  audio: { events_sfx: {} }, how_to_play: { steps: [], tips: [] }, stands: {},
  readout: { stat_icons: { power: '💪', speed: '⚡', weird: '🌀' } },
};

// Load arena.js (ARENA_JS env overrides the path — used to prove that a stale
// cached pre-v6 arena.js makes the intros reveal throw), then the host IIFE.
const arenaPath = process.env.ARENA_JS || path.join(root, 'arena.js');
vm.runInContext(fs.readFileSync(arenaPath, 'utf8'), sandbox);
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map(m => m[1]).filter(s => s.trim() && !s.includes('window.DOODLE_CONFIG ='));
assert.strictEqual(inline.length, 1, 'expected exactly one inline host script');
vm.runInContext(inline[0], sandbox);            // runs the IIFE → boots, captures onMessage
assert.ok(typeof onMessage === 'function', 'host must wire a socket onMessage handler');

// ---- drive the flow the server produces ----
function send(m) { onMessage(m); }

// 1) lobby → arena_state (populates the battlefield sprites), then the intros phase
send({ type: 'joined', payload: { room: 'ABCD', player_id: 'host' } });
send({ type: 'lobby_state', payload: { players: [], teams: [
  { id: 'team_a', name: 'Team A', color: '#E24FA0' }, { id: 'team_b', name: 'Team B', color: '#2F6FE0' }], can_start: true } });
const chars = [
  { player_id: 'p1', name: 'Princess Stabby', zone_id: 'glitter_back', hp: 34, max_hp: 34, team_id: 'team_a', stats: { power: 1, speed: 5, weird: 3 }, is_ko: false, png: 'data:,x', sprite_png: 'data:,x' },
  { player_id: 'p2', name: 'The Blob', zone_id: 'thunder_back', hp: 34, max_hp: 34, team_id: 'team_b', stats: { power: 0, speed: 3, weird: 6 }, is_ko: false, png: 'data:,x', sprite_png: 'data:,x' },
];
const zones = [
  { id: 'glitter_back', label: 'Glitter Backline' }, { id: 'frontline', label: 'The Pit' }, { id: 'thunder_back', label: 'Thunder Backline' }];
send({ type: 'arena_state', payload: { zones, characters: chars, teams: [
  { id: 'team_a', name: 'Team A', color: '#E24FA0' }, { id: 'team_b', name: 'Team B', color: '#2F6FE0' }], traps: [] } });
send({ type: 'phase_change', payload: { phase: 'intros', round: 0, splash: { text: '🥁 Meet the Fighters!', seconds: 2 } } });

// 2) the intros reveal_step — the exact shape _reveal_intros broadcasts
const introBeats = chars.map(c => ({
  event_id: 'intro-' + c.player_id, text: 'Introducing ' + c.name + '!', speaker: 'pbp',
  player_id: c.player_id, target_id: null, type: 'intro',
  name: c.name, personality: 'a legend', stats: c.stats,
  hurt: null, helped: null, floats: [], combo_name: null, sfx: null, result: null,
}));
introBeats.push({ event_id: 'intro-teams', text: '…and TOGETHER they are… SPARKLE SNACKS!', speaker: 'pbp',
  player_id: null, target_id: null, type: 'team_reveal', hurt: null, helped: null, floats: [],
  combo_name: null, sfx: null, result: null, teams: [
    { id: 'team_a', name: 'Sparkle Snacks', color: '#E24FA0' }, { id: 'team_b', name: 'Thunder Buns', color: '#2F6FE0' }] });

const sendsBefore = sends.length;
send({ type: 'reveal_step', payload: { round: 0, round_title: 'Meet the Fighters', beats: introBeats,
  characters: chars, action_pngs: {}, initiative_order: ['p1', 'p2'], meters: {}, teams: [
    { id: 'team_a', name: 'Sparkle Snacks', color: '#E24FA0' }, { id: 'team_b', name: 'Thunder Buns', color: '#2F6FE0' }] } });

// The reveal must have run to completion and signalled the server to advance.
const nextBeats = sends.slice(sendsBefore).filter(s => s === 'next_beat');
assert.ok(nextBeats.length >= 1,
  'intros reveal must signal next_beat when done (else the server stalls on the battlefield); sends=' + JSON.stringify(sends));

console.log('OK host intros');
