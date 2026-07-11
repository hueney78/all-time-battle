'use strict';
// Node unit check for web/player/canvas.js — the action-canvas prefill bug.
// Loads the browser IIFE in a vm sandbox with a recording 2D context and a
// synchronous fake Image, then asserts:
//   1. first-load prefill draws the character at action_canvas_character_scale
//      (0.5) immediately — never full size;
//   2. a prefill that fires before the png arrives (empty) clears leftover
//      strokes and clears `dirty`, so canvas_init can still apply the character;
//   3. Restore re-applies that same scaled state.
// Run directly (`node canvas_prefill.test.js`) or via tests/test_canvas_js.py.

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const SIZE = 512;

function makeRecordingCtx(rec) {
  return {
    fillRect() {},
    drawImage(img, x, y, w, h) { rec.push({ x, y, w, h }); },
    beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, arc() {}, fill() {},
  };
}

function makeCanvas(rec) {
  return {
    width: 0, height: 0,
    getContext() { return makeRecordingCtx(rec); },
    getBoundingClientRect() { return { left: 0, top: 0, width: SIZE, height: SIZE }; },
    addEventListener() {},
    toDataURL() { return 'data:image/png;base64,ZZ'; },
  };
}

// Setting .src fires onload synchronously (data URLs load instantly).
class FakeImage {
  set src(v) { this._src = v; if (typeof this.onload === 'function') this.onload(); }
  get src() { return this._src; }
}

const sandbox = { window: { DOODLE_CONFIG: {}, addEventListener() {} }, Image: FakeImage, console };
vm.createContext(sandbox);
const src = fs.readFileSync(path.join(__dirname, '..', '..', 'web', 'player', 'canvas.js'), 'utf8');
vm.runInContext(src, sandbox);
const DrawCanvas = sandbox.window.DrawCanvas;
assert.ok(DrawCanvas, 'canvas.js should export window.DrawCanvas');

const rec = [];
const pad = new DrawCanvas(makeCanvas(rec));

// 1) First-load prefill applies the scaled state immediately (0.5, not full).
rec.length = 0;
pad.loadImage('data:image/png;base64,CHAR', { markClean: true, scale: 0.5, side: 'left' });
let last = rec[rec.length - 1];
assert.ok(last, 'character should be drawn on the first-load prefill');
assert.strictEqual(last.w, SIZE * 0.5,
  `first-load prefill must draw at scale 0.5, got width ${last && last.w} (full size = ${SIZE})`);
assert.strictEqual(pad.bgScale, 0.5, 'bgScale set to 0.5 on prefill');
assert.strictEqual(pad.dirty, false, 'prefill marks the canvas clean');

// 2) A prefill that fires before the png arrives (empty) must clear leftover
//    full-size strokes and clear dirty, so the real prefill is not blocked.
pad.strokes.push({ tool: 'pen', color: '#000', size: 8, points: [{ x: 1, y: 1 }] });
pad._dirty = true;
pad.loadImage('', { markClean: true, scale: 0.5, side: 'left' });
assert.strictEqual(pad.strokes.length, 0, 'empty prefill clears leftover strokes (no stale full-size drawing)');
assert.strictEqual(pad.dirty, false, 'empty prefill clears dirty so canvas_init can prefill the character');

// 3) Restore re-applies that same scaled state.
rec.length = 0;
pad.loadImage('data:image/png;base64,CHAR', { markClean: false, scale: 0.5, side: 'left' });
last = rec[rec.length - 1];
assert.strictEqual(last.w, SIZE * 0.5, 'Restore re-applies the 0.5 scaled prefill');

console.log('OK canvas prefill');
