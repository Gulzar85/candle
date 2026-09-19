/**
 * PNG export (Phase 9). DOM/canvas dependent (verified by build + smoke
 * test, consistent with renderer.ts's own testing convention).
 *
 * Draws directly onto a dedicated offscreen canvas rather than reusing
 * `Renderer` (which is coupled to a live, resizable `<canvas>` element and
 * its static-layer cache — machinery this one-shot export doesn't need).
 * Callers are expected to pass strokes from a fresh server state fetch, not
 * possibly-stale local state — see index.ts.
 */

import { boundsOf, unionBounds } from "./geometry";
import type { BBox, Stroke } from "./types";

const MAX_EXPORT_DIMENSION = 4096;
const PADDING = 48;
const MAX_SCALE = 8;

/** Render `strokes` to a PNG Blob, fit to their content bounds with padding. */
export function strokesToPngBlob(strokes: readonly Stroke[]): Promise<Blob> {
  const boxes = strokes
    .map((s) => boundsOf(s.points))
    .filter((b): b is BBox => b !== null);
  const bbox = unionBounds(boxes) ?? { minX: -120, minY: -90, maxX: 120, maxY: 90 };

  const contentW = Math.max(1, bbox.maxX - bbox.minX);
  const contentH = Math.max(1, bbox.maxY - bbox.minY);
  // Fit content into the max export dimension; cap the upper scale so a
  // tiny board doesn't export at an absurd zoom level.
  const scale = Math.min(
    (MAX_EXPORT_DIMENSION - PADDING * 2) / contentW,
    (MAX_EXPORT_DIMENSION - PADDING * 2) / contentH,
    MAX_SCALE,
  );
  const w = Math.ceil(contentW * scale + PADDING * 2);
  const h = Math.ceil(contentH * scale + PADDING * 2);

  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D context unavailable");

  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, w, h);
  ctx.translate(PADDING - bbox.minX * scale, PADDING - bbox.minY * scale);
  ctx.scale(scale, scale);

  for (const stroke of strokes) {
    if (stroke.points.length < 2) continue;
    ctx.strokeStyle = stroke.style.color;
    ctx.globalAlpha = stroke.style.opacity;
    ctx.lineWidth = stroke.style.width;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(stroke.points[0].x, stroke.points[0].y);
    for (let i = 1; i < stroke.points.length; i++) {
      ctx.lineTo(stroke.points[i].x, stroke.points[i].y);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("Failed to encode PNG"));
    }, "image/png");
  });
}

/** Trigger a browser download of `blob` as `filename`. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
