/**
 * Viewport transform. Pure — no DOM.
 *
 * A viewport is defined by the world point at the center of the screen
 * (`cx`, `cy`) plus a uniform scale (`zoom`). This center-based form makes
 * "zoom at the cursor" and "pan" trivial and deterministic, and it is trivial
 * to serialize for future sync.
 */

import type { BBox, Point, Size, Viewport } from "./types";

export const MIN_ZOOM = 0.2;
export const MAX_ZOOM = 4.0;

export function makeViewport(cx = 0, cy = 0, zoom = 1): Viewport {
  return { cx, cy, zoom };
}

export function clampZoom(zoom: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
}

/** World point -> screen (CSS px) point for the given canvas size and viewport. */
export function worldToScreen(
  w: Point,
  size: Size,
  viewport: Viewport,
): Point {
  return {
    x: size.w / 2 + (w.x - viewport.cx) * viewport.zoom,
    y: size.h / 2 + (w.y - viewport.cy) * viewport.zoom,
  };
}

/** Screen (CSS px) point -> world point. */
export function screenToWorld(
  s: Point,
  size: Size,
  viewport: Viewport,
): Point {
  return {
    x: viewport.cx + (s.x - size.w / 2) / viewport.zoom,
    y: viewport.cy + (s.y - size.h / 2) / viewport.zoom,
  };
}

/**
 * Zoom by `factor` keeping the world point under the screen anchor `anchor`
 * stationary (standard wheel/gesture zoom-at-cursor). Returns a new viewport.
 */
export function zoomAt(
  viewport: Viewport,
  size: Size,
  anchor: Point,
  factor: number,
): Viewport {
  const zoom = clampZoom(viewport.zoom * factor);
  const worldBefore = screenToWorld(anchor, size, viewport);
  const worldAfter = screenToWorld(anchor, size, { ...viewport, zoom });
  return {
    cx: viewport.cx + (worldBefore.x - worldAfter.x),
    cy: viewport.cy + (worldBefore.y - worldAfter.y),
    zoom,
  };
}

/** Pan by a screen-space delta (e.g. pointer drag or wheel scroll). */
export function panBy(viewport: Viewport, dx: number, dy: number): Viewport {
  return {
    cx: viewport.cx - dx / viewport.zoom,
    cy: viewport.cy - dy / viewport.zoom,
    zoom: viewport.zoom,
  };
}

/**
 * Compute a viewport that frames `bbox` within `size`, leaving `padding` CSS
 * px on each side. Respects MIN_ZOOM/MAX_ZOOM. A degenerate (single-point) box
 * is framed at the default zoom centered on that point, so fit-to-view never
 * blanks the canvas.
 */
export function fitToShow(
  bbox: BBox,
  size: Size,
  padding = 48,
): Viewport {
  const bw = bbox.maxX - bbox.minX;
  const bh = bbox.maxY - bbox.minY;
  const availW = Math.max(1, size.w - padding * 2);
  const availH = Math.max(1, size.h - padding * 2);

  let zoom: number;
  if (bw <= 0 && bh <= 0) {
    // Single-point / empty box: frame the point at the default zoom.
    zoom = 1;
  } else {
    zoom = clampZoom(Math.min(availW / Math.max(bw, 1), availH / Math.max(bh, 1)));
  }

  return {
    cx: bbox.minX + bw / 2,
    cy: bbox.minY + bh / 2,
    zoom,
  };
}

/** Renderable zoom percentage, rounded to a whole percent for the UI. */
export function zoomPercent(viewport: Viewport): number {
  return Math.round(viewport.zoom * 100);
}
