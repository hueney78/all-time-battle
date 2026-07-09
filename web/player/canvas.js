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
      this._cur = null;
      this._dirty = false;         // has the user drawn since the last load?
      this._bind();
      this.redraw();
    }

    // -- tools --
    setColor(c) { this.color = c; this.tool = 'pen'; }
    setPen(size) { this.penSize = size; this.tool = 'pen'; }
    setEraser(size) { this.penSize = size; this.tool = 'eraser'; }

    get dirty() { return this._dirty; }

    // -- history --
    undo() { this.strokes.pop(); this.redraw(); }
    clear() { this.strokes = []; this.bg = null; this._dirty = true; this.redraw(); }

    // Load a character image (data URL) as the base layer, wiping strokes so the
    // player draws on top of a clean character. Used for preload + "restore".
    loadImage(dataUrl, { markClean = true, scale = 1 } = {}) {
      this.bgScale = scale;
      if (!dataUrl) { this.bg = null; this.redraw(); return; }
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
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, SIZE, SIZE);
      if (this.bg) {
        const w = SIZE * this.bgScale, off = (SIZE - w) / 2;   // centered, shrunk
        ctx.drawImage(this.bg, off, off, w, w);
      }
      for (const s of this.strokes) this._paint(s);
      ctx.globalCompositeOperation = 'source-over';
    }

    _paint(s) {
      const ctx = this.ctx;
      ctx.globalCompositeOperation = (s.tool === 'eraser') ? 'destination-out' : 'source-over';
      ctx.strokeStyle = s.color;
      ctx.fillStyle = s.color;
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
        this._cur = { tool: this.tool, color: this.color, size: this.penSize, points: [this._pos(e)] };
        this.strokes.push(this._cur);
        this._dirty = true;
        this.redraw();
      };
      const move = (e) => {
        if (!this._cur) return;
        e.preventDefault();
        this._cur.points.push(this._pos(e));
        this.redraw();
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

    // -- export --
    toPNG() { return this.c.toDataURL('image/png'); }
  }

  window.DrawCanvas = DrawCanvas;
})();
