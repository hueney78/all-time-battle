'use strict';
// Node unit check for web/host/arena.js — the Doodle Crowd stands (§15/S4).
// Loads the browser IIFE in a vm sandbox with a minimal fake DOM, then asserts
// that setSpectators:
//   1. filters the roster to entries that actually have a drawing (png);
//   2. renders at most stands.max spectators into a single .stands layer,
//      each with its drawing and team tint;
//   3. is idempotent — re-calling reuses the one .stands element, never stacks;
//   4. schedules rotation only when the roster is larger than what's shown.
// Run directly (`node arena_stands.test.js`) or via tests/test_arena_js.py.

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

function makeEl() {
  const el = {
    className: '', title: '', children: [], _html: '',
    style: { setProperty(k, v) { this['_' + k] = v; } },
    classList: { add() {}, remove() {}, toggle() {} },
    appendChild(c) { this.children.push(c); return c; },
    querySelector() { return makeEl(); },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = v; if (v === '') this.children.length = 0; },
  };
  return el;
}

const timers = { setCalls: 0 };
const sandbox = {
  window: { DOODLE_CONFIG: { stands: { max: 3, rotate_seconds: 12 } } },
  document: { createElement() { return makeEl(); } },
  setInterval() { timers.setCalls++; return 1; },
  clearInterval() {},
  Math, console,
};
vm.createContext(sandbox);
const src = fs.readFileSync(path.join(__dirname, '..', '..', 'web', 'host', 'arena.js'), 'utf8');
vm.runInContext(src, sandbox);
const Arena = sandbox.window.Arena;
assert.ok(Arena, 'arena.js should export window.Arena');

const root = makeEl();
const arena = new Arena(root);

// A roster of 5 doodles + 2 png-less ghosts that must be skipped.
const roster = [
  { name: 'Princess Stabby', png: 'data:image/png;base64,A', team_id: 'team_a', won: true },
  { name: 'The Blob', png: 'data:image/png;base64,B', team_id: 'team_b', won: false },
  { name: 'Gerald', png: 'data:image/png;base64,C', team_id: 'team_b', won: true },
  { name: 'Lawnmower', png: 'data:image/png;base64,D', team_id: 'team_a', won: false },
  { name: 'Tim', png: 'data:image/png;base64,E', team_id: 'team_a', won: false },
  { name: 'Nameless', png: '', team_id: 'team_a' },          // no drawing → skipped
  { name: 'Ghost' },                                          // no png at all → skipped
];

arena.setSpectators(roster);

// 1) exactly one .stands layer on the arena root
const stands = root.children.filter(c => c.className === 'stands');
assert.strictEqual(stands.length, 1, 'exactly one .stands layer is appended');
const layer = stands[0];

// 2) at most stands.max (3) spectators, all with a drawing + a team tint
assert.strictEqual(layer.children.length, 3,
  `should show stands.max=3 spectators, got ${layer.children.length}`);
for (const s of layer.children) {
  assert.strictEqual(s.className, 'spectator');
  assert.ok(/^url\(data:image\/png/.test(s.style.backgroundImage),
    'each spectator renders its drawing');
  assert.ok(s.style['_--team'], 'each spectator carries a team tint');
}

// 3) idempotent: re-calling reuses the single .stands element
arena.setSpectators(roster);
assert.strictEqual(root.children.filter(c => c.className === 'stands').length, 1,
  're-calling setSpectators must not stack a second .stands layer');

// 4) rotation scheduled because roster (5 with png) > shown (3)
assert.ok(timers.setCalls >= 1, 'rotation interval scheduled when roster exceeds the visible handful');

// 5) a roster no larger than max does not schedule rotation
timers.setCalls = 0;
arena.setSpectators(roster.slice(0, 2));
assert.strictEqual(root.children.filter(c => c.className === 'stands')[0].children.length, 2);
assert.strictEqual(timers.setCalls, 0, 'no rotation when everyone fits');

console.log('OK arena stands');
