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

// ---------------------------------------------------------------------------
// Move / resize (Phase 9): shared math for engine.ts (gesture handling /
// hit-testing) and renderer.ts (handle drawing). Kept here rather than in
// either of those DOM-coupled files because this is the trickiest new math
// (including the degenerate-bbox edge case) and this is the one module that
// is actually unit-tested.
// ---------------------------------------------------------------------------

/** Below this world-space extent, a bounding box dimension is treated as
 * degenerate (a perfectly vertical/horizontal stroke) -- dividing by it to
 * derive a resize scale would produce NaN/Infinity. */
export const MIN_BBOX_DIMENSION = 1e-3;

/** Matches the server's accepted resize scale range (validator.py). */
export const MIN_SCALE = 0.001;
export const MAX_SCALE = 1000;

/** `newLength / oldLength`, with `oldLength` floored to MIN_BBOX_DIMENSION
 * so a near-zero source extent never produces NaN/Infinity. */
export function safeScaleFactor(newLength: number, oldLength: number): number {
  const denom = Math.abs(oldLength) < MIN_BBOX_DIMENSION ? MIN_BBOX_DIMENSION : oldLength;
  return clampScale(newLength / denom);
}

function clampScale(scale: number): number {
  if (!Number.isFinite(scale)) return 1;
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
}

export type HandleId = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";

const OPPOSITE_HANDLE: Record<HandleId, HandleId> = {
  nw: "se",
  n: "s",
  ne: "sw",
  e: "w",
  se: "nw",
  s: "n",
  sw: "ne",
  w: "e",
};

export function oppositeHandle(handle: HandleId): HandleId {
  return OPPOSITE_HANDLE[handle];
}

/** World-space position of every resize handle around `box` (4 corners + 4
 * edge midpoints). */
export function handlePositions(box: BBox): Record<HandleId, Point> {
  const midX = (box.minX + box.maxX) / 2;
  const midY = (box.minY + box.maxY) / 2;
  return {
    nw: { x: box.minX, y: box.minY },
    n: { x: midX, y: box.minY },
    ne: { x: box.maxX, y: box.minY },
    e: { x: box.maxX, y: midY },
    se: { x: box.maxX, y: box.maxY },
    s: { x: midX, y: box.maxY },
    sw: { x: box.minX, y: box.maxY },
    w: { x: box.minX, y: midY },
  };
}

/** Handles safe to show/hit-test for `box`. A handle is omitted when
 * dragging it would require deriving a scale from a ~zero bbox dimension: a
 * near-zero-width box only exposes the two handles that resize height alone
 * (n/s); a near-zero-height box only exposes width-only handles (w/e). */
export function visibleHandles(box: BBox): HandleId[] {
  const width = box.maxX - box.minX;
  const height = box.maxY - box.minY;
  const degenerateW = width < MIN_BBOX_DIMENSION;
  const degenerateH = height < MIN_BBOX_DIMENSION;
  if (degenerateW && degenerateH) return [];
  if (degenerateW) return ["n", "s"];
  if (degenerateH) return ["w", "e"];
  return ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
}

/** Nearest visible handle to `point` within `tolerance`, or null. */
export function nearestHandle(
  point: Point,
  positions: Record<HandleId, Point>,
  visible: readonly HandleId[],
  tolerance: number,
): HandleId | null {
  let best: HandleId | null = null;
  let bestDist = tolerance;
  for (const id of visible) {
    const d = distance(point, positions[id]);
    if (d <= bestDist) {
      best = id;
      bestDist = d;
    }
  }
  return best;
}

/** Translate every point by a fixed delta. */
export function translatePoints(points: readonly Point[], dx: number, dy: number): Point[] {
  return points.map((p) => ({ x: p.x + dx, y: p.y + dy }));
}

/** Scale every point from a fixed anchor (the point that stays put). Never
 * scales anything else about the object (e.g. stroke width) -- callers
 * decide what, if anything, else changes. */
export function scalePointsFromAnchor(
  points: readonly Point[],
  anchor: Point,
  scaleX: number,
  scaleY: number,
): Point[] {
  return points.map((p) => ({
    x: anchor.x + (p.x - anchor.x) * scaleX,
    y: anchor.y + (p.y - anchor.y) * scaleY,
  }));
}

/**
 * Given `handle` dragged to `newPoint`, compute the (anchor, scaleX, scaleY)
 * transform relative to `box` (the object's original bounding box). The
 * anchor is always the opposite handle's position -- a fixed point, matching
 * exactly how the server's RESIZE_OBJECT applies the same transform
 * (state_reconstruction.py). Edge handles (n/s/w/e) only scale one axis;
 * corner handles scale both.
 */
export function computeResizeTransform(
  box: BBox,
  handle: HandleId,
  newPoint: Point,
): { anchor: Point; scaleX: number; scaleY: number } {
  const positions = handlePositions(box);
  const anchor = positions[oppositeHandle(handle)];
  const affectsX = handle !== "n" && handle !== "s";
  const affectsY = handle !== "w" && handle !== "e";
  const scaleX = affectsX
    ? safeScaleFactor(newPoint.x - anchor.x, positions[handle].x - anchor.x)
    : 1;
  const scaleY = affectsY
    ? safeScaleFactor(newPoint.y - anchor.y, positions[handle].y - anchor.y)
    : 1;
  return { anchor, scaleX, scaleY };
}
