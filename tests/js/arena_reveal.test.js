'use strict';
// Node unit check for the v6 reveal helpers in web/host/arena.js:
//   - setActionBadge / clearBadges: the move-name badge under a fighter (§13);
//   - setShield / clearShields: PROTECT's round-long blue glow (§13);
//   - moveTo: reparents a fighter's sprite into the destination zone band, so a
//     CHARGE/ESCAPE relocates DURING the reveal, not at end of round (§13).
// Loads the browser IIFE in a vm sandbox with a minimal fake DOM.
// Run directly (`node arena_reveal.test.js`) or via tests/test_arena_js.py.

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

function makeEl() {
  const classes = new Set();
  const el = {
    className: '', title: '', textContent: '', _html: '', parentElement: null,
    children: [], offsetWidth: 1,
    style: {
      _m: {},
      setProperty(k, v) { this._m[k] = v; },
      getPropertyValue(k) { return this._m[k]; },
    },
    classList: {
      add(c) { classes.add(c); },
      remove(c) { classes.delete(c); },
      toggle(c, on) { const v = on === undefined ? !classes.has(c) : on;
        v ? classes.add(c) : classes.delete(c); return v; },
      contains(c) { return classes.has(c); },
    },
    appendChild(c) {
      if (c.parentElement) {
        const k = c.parentElement.children.indexOf(c);
        if (k >= 0) c.parentElement.children.splice(k, 1);
      }
      c.parentElement = el; el.children.push(c); return c;
    },
    querySelector() { return makeEl(); },
    querySelectorAll() { return []; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 10, height: 10 }; },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = v; if (v === '') this.children.length = 0; },
  };
  return el;
}

const sandbox = {
  window: { DOODLE_CONFIG: { reveal_move_seconds: 0.7 } },
  document: { createElement() { return makeEl(); } },
  setTimeout() { return 1; }, clearTimeout() {},
  setInterval() { return 1; }, clearInterval() {},
  Math, console,
};
vm.createContext(sandbox);
const src = fs.readFileSync(path.join(__dirname, '..', '..', 'web', 'host', 'arena.js'), 'utf8');
vm.runInContext(src, sandbox);
const Arena = sandbox.window.Arena;
assert.ok(Arena, 'arena.js should export window.Arena');

const arena = new Arena(makeEl());
arena.setup([{ id: 'zone_a', label: 'A' }, { id: 'zone_b', label: 'B' }]);
arena.render([
  { player_id: 'p1', name: 'Stabby', zone_id: 'zone_a', hp: 20, max_hp: 20, team_id: 'team_a' },
  { player_id: 'p2', name: 'Blob', zone_id: 'zone_a', hp: 20, max_hp: 20, team_id: 'team_b' },
]);

// 1) the move-name badge shows/clears
arena.setActionBadge('p1', 'SMASH');
assert.strictEqual(arena.sprites.p1.badge.textContent, 'SMASH', 'badge shows the move name');
assert.ok(arena.sprites.p1.badge.classList.contains('show'), 'badge is visible');
arena.clearBadges();
assert.strictEqual(arena.sprites.p1.badge.textContent, '', 'clearBadges wipes the label');
assert.ok(!arena.sprites.p1.badge.classList.contains('show'), 'clearBadges hides the badge');

// 2) PROTECT's blue glow toggles on the shielded ally
arena.setShield('p2');
assert.ok(arena.sprites.p2.el.classList.contains('shielded'), 'setShield lights the ally');
arena.clearShields();
assert.ok(!arena.sprites.p2.el.classList.contains('shielded'), 'clearShields drops the glow');

// 3) moveTo relocates the sprite into the destination zone band DURING the reveal
assert.strictEqual(arena.sprites.p1.el.parentElement, arena.zoneEls.zone_a, 'starts in zone_a');
arena.moveTo('p1', 'zone_b', 1);
assert.strictEqual(arena.sprites.p1.el.parentElement, arena.zoneEls.zone_b,
  'moveTo reparents the sprite into the destination zone');
// a no-op move (already there) is safe and idempotent
arena.moveTo('p1', 'zone_b', 1);
assert.strictEqual(arena.sprites.p1.el.parentElement, arena.zoneEls.zone_b);

console.log('OK arena reveal');
