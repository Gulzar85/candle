/**
 * Shared types for the whiteboard engine.
 *
 * Everything here is plain data — no DOM, no canvas — so these types are safe
 * to import from pure-logic (node-tested) modules. A coordinate is a 2D point
 * in **world space** unless a function documents otherwise; the viewport
 * transform converts world <-> screen space.
 */

export interface Point {
  readonly x: number;
  readonly y: number;
}

/** Axis-aligned bounds in world space. */
export interface BBox {
  readonly minX: number;
  readonly minY: number;
  readonly maxX: number;
  readonly maxY: number;
}

/** Visual attributes of a stroke. All values are concrete (pre-resolved). */
export interface StrokeStyle {
  /** CSS color string (hex recommended). */
  readonly color: string;
  /** Stroke width in world units. */
  readonly width: number;
  /** Opacity 0..1. */
  readonly opacity: number;
}

/** A single freehand stroke: an ordered series of world-space points. */
export interface Stroke {
  readonly id: string;
  readonly points: readonly Point[];
  readonly style: StrokeStyle;
  /** Stable identity of the creator (initials/color) for presence UI. */
  readonly creatorId?: string;
}

/** Marker of which tool created the stroke (informational / future sync). */
export type StrokeKind = "pen" | "eraser";

export type Tool = "pen" | "eraser" | "select";

/** Center-based viewport: the world point at the screen center plus zoom. */
export interface Viewport {
  readonly cx: number;
  readonly cy: number;
  readonly zoom: number;
}

/** Display size of the canvas viewport in CSS pixels. */
export interface Size {
  readonly w: number;
  readonly h: number;
}

/**
 * A reversible edit made to the board — the unit of undo/redo and (later) of
 * cross-device sync. Each operation carries the concrete strokes it affected so
 * it can be inverted exactly, without consulting other state.
 */
export type Op =
  | { readonly type: "add"; readonly strokes: readonly Stroke[] }
  | { readonly type: "remove"; readonly strokes: readonly Stroke[] }
  | { readonly type: "clear"; readonly strokes: readonly Stroke[] };
