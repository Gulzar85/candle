/**
 * Tool semantics. Pure — no DOM.
 *
 * Tools turn raw pointer samples into strokes and edits. All rules live here so
 * they are deterministic and testable, and (crucially) so the *same* math the
 * UI uses is exactly what a future server would apply to reconcile operations.
 *
 * Pen  -> decimate + smooth the raw samples into a Stroke.
 * Eraser-> clip points out of strokes that fall within the eraser radius.
 */

import { chaikin, decimate, distanceToPolyline } from "./geometry";
import type { Point, Stroke, StrokeStyle } from "./types";

/** Minimum gap (world units) between retained pen samples. */
const PEN_MIN_DIST = 1.5;
/** Chaikin smoothing iterations applied to a finished pen stroke. */
const PEN_SMOOTH_ITERATIONS = 2;

export interface PenStroke {
  readonly stroke: Stroke;
  /** Raw sample count before decimation (informational). */
  readonly rawCount: number;
}

/**
 * Finalize a freehand stroke from raw samples plus style. Returns the polished
 * stroke (or null if there is not enough content to keep — a stray tap).
 */
export function finalizePenStroke(
  id: string,
  rawPoints: readonly Point[],
  style: StrokeStyle,
  creatorId?: string,
): PenStroke | null {
  const decimated = decimate(rawPoints, PEN_MIN_DIST);
  if (decimated.length < 2) return null;
  const smoothed = chaikin(decimated, PEN_SMOOTH_ITERATIONS);
  const stroke: Stroke = {
    id,
    points: smoothed,
    style,
    creatorId,
  };
  return { stroke, rawCount: rawPoints.length };
}

/**
 * Clip `points` to remove any run that comes within `radius` of the eraser
 * path. Returns the surviving segments as separate point lists; a stroke whose
 * points all fall under the eraser yields an empty array (caller should delete
 * the stroke).
 */
export function clipToEraser(
  points: readonly Point[],
  eraserPath: readonly Point[],
  radius: number,
): Point[][] {
  const segments: Point[][] = [];
  let current: Point[] = [];
  const exceeds = (p: Point) =>
    distanceToPolyline(p, eraserPath, radius) > radius;

  for (const p of points) {
    if (exceeds(p)) {
      current.push(p);
    } else {
      if (current.length > 0) segments.push(current);
      current = [];
    }
  }
  if (current.length > 0) segments.push(current);

  // Merge touches into their neighbours so a grazed point doesn't fragment a
  // stroke; keep only segments with at least two points.
  return segments
    .filter((seg) => seg.length >= 2)
    .map((seg) => seg);
}

/** True if `p` lies within `threshold` of an erasable stroke's path. */
export function hitsStroke(p: Point, stroke: Stroke, threshold: number): boolean {
  return distanceToPolyline(p, stroke.points, threshold) <= threshold;
}
