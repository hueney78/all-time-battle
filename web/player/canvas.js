// canvas.js — the drawing surface. Fixed 512x512 backing store (= PNG export
// size), scaled to fit on screen via CSS. Strokes are retained so undo and the
// character preload/restore can re-composite cleanly.
//
// Exposes window.DrawCanvas.

(function () {
  const SIZE = 512;

  class DrawCanvas {
    constructor(el) {
      this.c = el;
      this.c.width = SIZE;
      this.c.height = SIZE;
      this.ctx = this.c.getContext('2d');
      this.color = '#111111';
      this.penSize = 8;
      this.tool = 'pen';           // 'pen' | 'eraser'
      this.strokes = [];           // [{tool,color,size,points:[{x,y}]}]
      this.bg = null;              // preloaded character image
      this.bgScale = 1;            // <1 shrinks the character to free up drawing room
      this.bgSide = 'center';      // 'left' | 'right' | 'center' — team side to sit on
      // Canvas fill = the arena floor color so exported PNGs blend into the
      // battlefield; erasers restore THIS color (not white/transparent).
      this.bgColor = '#E8D5A8';
      this._cur = null;
      this._dirty = false;         // has the user drawn since the last load?
      this._bind();
      this.redraw();
    }

    // -- tools --
    // Picking a color keeps the FILL bucket selected (so you can recolor it),
    // otherwise switches to the pen (you can't paint a color with the eraser).
    setColor(c) { this.color = c; if (this.tool === 'eraser') this.tool = 'pen'; }
    setPen(size) { this.penSize = size; this.tool = 'pen'; }
    setEraser(size) { this.penSize = size; this.tool = 'eraser'; }
    setFill() { this.tool = 'fill'; }   // paint bucket — flood-fill with the current color
    setBackgroundColor(c) { if (c) { this.bgColor = c; this.redraw(); } }

    get dirty() { return this._dirty; }

    // -- history --
    undo() { this.strokes.pop(); this.redraw(); }
    clear() { this.strokes = []; this.bg = null; this._dirty = true; this.redraw(); }

    // Load a character image (data URL) as the base layer, wiping strokes so the
    // player draws on top of a clean character. Used for preload + "restore".
    // `side` seats the character on the player's team side (matching the TV
    // arena), leaving the open half of the canvas facing the enemies.
    loadImage(dataUrl, { markClean = true, scale = 1, side = 'center' } = {}) {
      this.bgScale = scale;
      this.bgSide = side;
      // Even with no image yet (prefill fires before canvas_init delivers the
      // png), start from a clean slate: drop any leftover strokes from the
      // previous phase and clear the dirty flag so the character prefill isn't
      // blocked by a stale `dirty` when canvas_init arrives.
      if (!dataUrl) {
        this.bg = null;
        this.strokes = [];
        if (markClean) this._dirty = false;
        this.redraw();
        return;
      }
      const img = new Image();
      img.onload = () => {
        this.bg = img;
        this.strokes = [];
        if (markClean) this._dirty = false;
        this.redraw();
      };
      img.src = dataUrl;
    }

    // -- rendering --
    redraw() {
      const ctx = this.ctx;
      ctx.globalCompositeOperation = 'source-over';
      ctx.fillStyle = this.bgColor;         // arena-floor sand, not white
      ctx.fillRect(0, 0, SIZE, SIZE);
      if (this.bg) {
        const w = SIZE * this.bgScale;
        const margin = SIZE * 0.06;
        let x = (SIZE - w) / 2;
        if (this.bgSide === 'left') x = margin;
        else if (this.bgSide === 'right') x = SIZE - w - margin;
        let y = SIZE - w - SIZE * 0.12;     // seat near the floor
        if (y < margin) y = margin;
        if (y + w > SIZE) y = SIZE - w;     // a full-size character still fits
        ctx.drawImage(this.bg, x, y, w, w);
      }
      for (const s of this.strokes) this._paint(s);
    }

    _paint(s) {
      // Paint bucket: flood-fill the tapped region. Replayed in stroke order, so
      // it composites over the background + every earlier stroke exactly as it
      // did when drawn (which keeps undo/redraw correct).
      if (s.tool === 'fill') { this._floodFill(s.x, s.y, s.color); return; }
      const ctx = this.ctx;
      // Erasers paint the sand color (source-over) so erased areas restore the
      // arena-floor background rather than cutting to transparent/white.
      const eraser = s.tool === 'eraser';
      ctx.globalCompositeOperation = 'source-over';
      ctx.strokeStyle = eraser ? this.bgColor : s.color;
      ctx.fillStyle = eraser ? this.bgColor : s.color;
      ctx.lineWidth = s.size;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      const p = s.points;
      if (p.length === 1) {
        ctx.beginPath();
        ctx.arc(p[0].x, p[0].y, s.size / 2, 0, Math.PI * 2);
        ctx.fill();
        return;
      }
      ctx.beginPath();
      ctx.moveTo(p[0].x, p[0].y);
      for (let i = 1; i < p.length; i++) ctx.lineTo(p[i].x, p[i].y);
      ctx.stroke();
    }

    // -- input --
    _pos(e) {
      const r = this.c.getBoundingClientRect();
      const t = e.touches ? e.touches[0] : e;
      return {
        x: (t.clientX - r.left) / r.width * SIZE,
        y: (t.clientY - r.top) / r.height * SIZE,
      };
    }

    _bind() {
      const start = (e) => {
        e.preventDefault();
        const pos = this._pos(e);
        this._dirty = true;
        // Paint bucket: one tap flood-fills the tapped region. Recorded as a
        // replayable "stroke" so undo removes it and redraw re-composites it.
        if (this.tool === 'fill') {
          this.strokes.push({ tool: 'fill', color: this.color, x: pos.x, y: pos.y });
          this._cur = null;
          this.redraw();
          return;
        }
        this._cur = { tool: this.tool, color: this.color, size: this.penSize, points: [pos] };
        this.strokes.push(this._cur);
        this.redraw();
      };
      const move = (e) => {
        if (!this._cur) return;
        e.preventDefault();
        this._cur.points.push(this._pos(e));
        // Draw only the newest segment onto the existing canvas instead of
        // replaying the whole history each move — keeps drawing smooth even with
        // flood fills banked (replaying a fill per pointermove would lag).
        this._drawSegment(this._cur);
      };
      const end = () => { this._cur = null; };

      this.c.addEventListener('pointerdown', start);
      this.c.addEventListener('pointermove', move);
      window.addEventListener('pointerup', end);
      // Fallback for browsers without pointer events.
      this.c.addEventListener('touchstart', start, { passive: false });
      this.c.addEventListener('touchmove', move, { passive: false });
      window.addEventListener('touchend', end);
    }

    // Draw the current stroke's newest segment onto the existing canvas (during
    // an active drag — the rest of the picture is already painted). Round caps at
    // each segment's ends reproduce the round joins of a full-polyline replay.
    _drawSegment(s) {
      const ctx = this.ctx;
      const p = s.points, n = p.length;
      const eraser = s.tool === 'eraser';
      ctx.globalCompositeOperation = 'source-over';
      ctx.strokeStyle = eraser ? this.bgColor : s.color;
      ctx.fillStyle = eraser ? this.bgColor : s.color;
      ctx.lineWidth = s.size;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      if (n <= 1) {
        ctx.beginPath();
        ctx.arc(p[0].x, p[0].y, s.size / 2, 0, Math.PI * 2);
        ctx.fill();
        return;
      }
      const a = p[n - 2], b = p[n - 1];
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }

    // Flood fill (paint bucket): recolor the contiguous region of pixels matching
    // the tapped pixel's color (within a tolerance, so anti-aliased edges act as
    // borders) to `hex`. 4-connected scan over the 512x512 backing store with a
    // visited mask, so it always terminates regardless of the chosen color.
    _floodFill(fx, fy, hex) {
      const ctx = this.ctx;
      const x0 = Math.max(0, Math.min(SIZE - 1, Math.round(fx)));
      const y0 = Math.max(0, Math.min(SIZE - 1, Math.round(fy)));
      const [fr, fg, fb] = this._hexRgb(hex);
      const img = ctx.getImageData(0, 0, SIZE, SIZE);
      const d = img.data;
      const start = (y0 * SIZE + x0) * 4;
      const tr = d[start], tg = d[start + 1], tb = d[start + 2];
      // Tapping a spot already in the fill color is a no-op.
      if (Math.abs(tr - fr) + Math.abs(tg - fg) + Math.abs(tb - fb) <= 2) return;
      const TOL = 48;   // sum-of-abs-channel tolerance for "same region"
      const seen = new Uint8Array(SIZE * SIZE);
      const stack = [y0 * SIZE + x0];
      seen[y0 * SIZE + x0] = 1;
      while (stack.length) {
        const p = stack.pop();
        const i = p * 4;
        d[i] = fr; d[i + 1] = fg; d[i + 2] = fb; d[i + 3] = 255;
        const px = p % SIZE, py = (p / SIZE) | 0;
        const nbrs = [];
        if (px > 0) nbrs.push(p - 1);
        if (px < SIZE - 1) nbrs.push(p + 1);
        if (py > 0) nbrs.push(p - SIZE);
        if (py < SIZE - 1) nbrs.push(p + SIZE);
        for (const q of nbrs) {
          if (seen[q]) continue;
          const j = q * 4;
          if (Math.abs(d[j] - tr) + Math.abs(d[j + 1] - tg) + Math.abs(d[j + 2] - tb) <= TOL) {
            seen[q] = 1;
            stack.push(q);
          }
        }
      }
      ctx.putImageData(img, 0, 0);
    }

    _hexRgb(hex) {
      hex = String(hex).replace('#', '');
      if (hex.length === 3) hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
      const n = parseInt(hex, 16) || 0;
      return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }

    // -- export --
    // Full redraw before export guarantees the PNG is a clean composite of the
    // background + every stroke/fill in order (drags paint incrementally).
    toPNG() { this.redraw(); return this.c.toDataURL('image/png'); }
  }

  window.DrawCanvas = DrawCanvas;
})();
