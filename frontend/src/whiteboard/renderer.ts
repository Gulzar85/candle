/**
 * Canvas rendering. DOM/canvas dependent (verified by build + smoke test).
 *
 * The renderer wraps the visible <canvas> and an offscreen "static" canvas.
 * Committed strokes are baked onto the static canvas; it is re-baked only when
 * the board version, viewport, or canvas size changes. During an active stroke
 * we blit the static layer and simply draw the live preview on top — no full
 * board redraw per pointer move.
 */

import type { Size, Stroke, Viewport } from "./types";

export interface RenderState {
  readonly viewport: Viewport;
  readonly size: Size;
  readonly version: number;
}

function sameState(a: RenderState, b: RenderState): boolean {
  return (
    a.version === b.version &&
    a.size.w === b.size.w &&
    a.size.h === b.size.h &&
    a.viewport.zoom === b.viewport.zoom &&
    a.viewport.cx === b.viewport.cx &&
    a.viewport.cy === b.viewport.cy
  );
}

export class Renderer {
  private readonly ctx: CanvasRenderingContext2D;
  private readonly staticCanvas: HTMLCanvasElement;
  private readonly staticCtx: CanvasRenderingContext2D;
  private cachedState: RenderState | null = null;
  private dpr = 1;

  constructor(private readonly canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas 2D context unavailable");
    this.ctx = ctx;
    this.staticCanvas = document.createElement("canvas");
    const sctx = this.staticCanvas.getContext("2d");
    if (!sctx) throw new Error("Offscreen 2D context unavailable");
    this.staticCtx = sctx;
  }

  /** Resize the backing store to match CSS size at the current device ratio. */
  resize(size: Size): void {
    this.dpr = Math.max(1, window.devicePixelRatio || 1);
    const w = Math.max(1, Math.round(size.w * this.dpr));
    const h = Math.max(1, Math.round(size.h * this.dpr));
    if (this.canvas.width !== w) this.canvas.width = w;
    if (this.canvas.height !== h) this.canvas.height = h;
    this.staticCanvas.width = w;
    this.staticCanvas.height = h;
    // Force the static layer to re-bake at the new size.
    this.cachedState = null;
  }

  private setTransform(ctx: CanvasRenderingContext2D, viewport: Viewport, size: Size): void {
    this.lastZoom = viewport.zoom;
    ctx.setTransform(
      this.dpr * viewport.zoom,
      0,
      0,
      this.dpr * viewport.zoom,
      this.dpr * (size.w / 2 - viewport.cx * viewport.zoom),
      this.dpr * (size.h / 2 - viewport.cy * viewport.zoom),
    );
  }

  /**
   * Render the board. Automatically re-bakes the static layer when the commit
   * version, viewport, or size changed; otherwise blits the cached static layer
   * and draws `liveStrokes` on top.
   */
  render(
    board: { readonly strokes: readonly Stroke[]; readonly version: number },
    viewport: Viewport,
    size: Size,
    liveStrokes: readonly Stroke[] = [],
    selectedId: string | null = null,
  ): void {
    const state: RenderState = { viewport, size, version: board.version };

    if (!this.cachedState || !sameState(this.cachedState, state)) {
      this.bakeStatic(board.strokes, viewport, size);
      this.cachedState = state;
    }

    this.ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.ctx.drawImage(this.staticCanvas, 0, 0);

    this.setTransform(this.ctx, viewport, size);
    for (const stroke of liveStrokes) this.drawStroke(this.ctx, stroke);
    if (selectedId) this.drawSelection(board.strokes, selectedId);
  }

  private bakeStatic(strokes: readonly Stroke[], viewport: Viewport, size: Size): void {
    this.staticCtx.setTransform(1, 0, 0, 1, 0, 0);
    this.staticCtx.clearRect(0, 0, this.staticCanvas.width, this.staticCanvas.height);
    this.setTransform(this.staticCtx, viewport, size);
    for (const stroke of strokes) this.drawStroke(this.staticCtx, stroke);
  }

  private drawSelection(strokes: readonly Stroke[], selectedId: string): void {
    const stroke = strokes.find((s) => s.id === selectedId);
    if (!stroke || stroke.points.length < 2) return;
    let minX = stroke.points[0].x;
    let minY = stroke.points[0].y;
    let maxX = minX;
    let maxY = minY;
    for (const p of stroke.points) {
      if (p.x < minX) minX = p.x;
      if (p.y < minY) minY = p.y;
      if (p.x > maxX) maxX = p.x;
      if (p.y > maxY) maxY = p.y;
    }
    const pad = stroke.style.width / 2 + 6;
    this.ctx.globalAlpha = 1;
    this.ctx.strokeStyle = "rgba(37, 99, 235, 0.9)";
    this.ctx.lineWidth = 2 / this._zoom();
    this.ctx.setLineDash([6 / this._zoom(), 4 / this._zoom()]);
    this.ctx.strokeRect(
      minX - pad,
      minY - pad,
      maxX - minX + pad * 2,
      maxY - minY + pad * 2,
    );
    this.ctx.setLineDash([]);
  }

  private _zoom(): number {
    // Zoom is baked into the ctx transform; a fixed dash in screen px must be
    // divided by the current scale. Tracked from the last render setTransform.
    return this.lastZoom || 1;
  }

  private lastZoom = 1;

  private drawStroke(ctx: CanvasRenderingContext2D, stroke: Stroke): void {
    if (stroke.points.length < 2) return;
    ctx.strokeStyle = stroke.style.color;
    ctx.globalAlpha = stroke.style.opacity;
    ctx.lineWidth = stroke.style.width;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    const first = stroke.points[0];
    ctx.moveTo(first.x, first.y);
    for (let i = 1; i < stroke.points.length; i++) {
      ctx.lineTo(stroke.points[i].x, stroke.points[i].y);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
}
