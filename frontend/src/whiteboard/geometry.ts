/**
 * Pure geometry helpers. No DOM.
 *
 * Freehand input is recorded as a dense series of pointer samples; raw samples
 * are often noisy and numerous. These helpers turn samples into the few
 * "significant" points a Stroke should keep (distance-based decimation + smooth
 * curve resampling) and support hit-testing / erasing against a polyline.
 */

import type { BBox, Point } from "./types";

/** Squared distance between two points (avoids a sqrt in hot loops). */
export function distSq(a: Point, b: Point): number {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  return dx * dx + dy * dy;
}

export function distance(a: Point, b: Point): number {
  return Math.sqrt(distSq(a, b));
}

export function midpoint(a: Point, b: Point): Point {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

export function isNullPoint(p: Point): boolean {
  return !Number.isFinite(p.x) || !Number.isFinite(p.y);
}

/**
 * Decimate a dense point series by minimum screen distance.
 *
 * Keeps the first point and any later point at least `minDist` (squared is used
 * internally) away from the last kept one. This collapses jitter and slashes
 * the number of points we must store and render, while preserving shape.
 */
export function decimate(points: readonly Point[], minDist: number): Point[] {
  if (points.length === 0) return [];
  const minSq = minDist * minDist;
  const out: Point[] = [{ x: points[0].x, y: points[0].y }];
  let last = points[0];
  for (let i = 1; i < points.length; i++) {
    const p = points[i];
    if (isNullPoint(p)) continue;
    if (distSq(p, last) >= minSq) {
      out.push({ x: p.x, y: p.y });
      last = p;
    }
  }
  return out;
}

/**
 * Smooth a polyline through its own points using Chaikin's corner-cutting
 * algorithm, producing a visually smooth curve. `iterations` (1-4) controls
 * smoothness; more iterations push the curve inward and grow the point count.
 */
export function chaikin(points: readonly Point[], iterations = 1): Point[] {
  if (points.length < 3) return points.map((p) => ({ ...p }));
  let current: readonly Point[] = points;
  for (let it = 0; it < iterations; it++) {
    const next: Point[] = [current[0]];
    for (let i = 0; i < current.length - 1; i++) {
      const a = current[i];
      const b = current[i + 1];
      next.push({ x: a.x + 0.25 * (b.x - a.x), y: a.y + 0.25 * (b.y - a.y) });
      next.push({ x: a.x + 0.75 * (b.x - a.x), y: a.y + 0.75 * (b.y - a.y) });
    }
    next.push(current[current.length - 1]);
    current = next;
  }
  return [...current];
}

/** Shortest distance from point `p` to the polyline segment [a, b]. */
export function distanceToSegment(p: Point, a: Point, b: Point): number {
  const abx = b.x - a.x;
  const aby = b.y - a.y;
  const lenSq = abx * abx + aby * aby;
  if (lenSq === 0) return distance(p, a);
  let t = ((p.x - a.x) * abx + (p.y - a.y) * aby) / lenSq;
  t = Math.max(0, Math.min(1, t));
  return distance(p, { x: a.x + t * abx, y: a.y + t * aby });
}

/**
 * Nearest distance from `p` to the whole polyline. Used for hit-testing pen
 * strokes. Early-exits as soon as we are within `threshold`.
 */
export function distanceToPolyline(
  p: Point,
  polyline: readonly Point[],
  threshold: number,
): number {
  let best = Infinity;
  for (let i = 0; i < polyline.length - 1; i++) {
    const d = distanceToSegment(p, polyline[i], polyline[i + 1]);
    if (d < best) best = d;
    if (best <= threshold) return best;
  }
  return best;
}

/** Bounds enclosing a set of points (empty input yields a null box). */
export function boundsOf(points: readonly Point[]): BBox | null {
  if (points.length === 0) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const p of points) {
    if (p.x < minX) minX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.x > maxX) maxX = p.x;
    if (p.y > maxY) maxY = p.y;
  }
  return { minX, minY, maxX, maxY };
}

/** Bounds enclosing a union of bounding boxes (null input yields null). */
export function unionBounds(boxes: readonly BBox[]): BBox | null {
  if (boxes.length === 0) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const b of boxes) {
    if (b.minX < minX) minX = b.minX;
    if (b.minY < minY) minY = b.minY;
    if (b.maxX > maxX) maxX = b.maxX;
    if (b.maxY > maxY) maxY = b.maxY;
  }
  return { minX, minY, maxX, maxY };
}
