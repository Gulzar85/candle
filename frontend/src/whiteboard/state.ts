/**
 * Board state and history. Pure — no DOM.
 *
 * The board's document model is deliberately simple: an ordered list of
 * strokes, mutated only by a stream of reversible operations (Op). Undo/redo
 * reverse/replay those operations rather than storing full snapshots, which
 * keeps memory bounded and — importantly — gives us a natural, order-able
 * ledger that a future persistence / real-time layer can reuse unchanged.
 */

import type { Op, Stroke } from "./types";

export interface Board {
  readonly strokes: readonly Stroke[];
  /** Applied operations in commit order (the ledger / undo stack). */
  readonly past: readonly Op[];
  /** Undone operations available to redo, most-recently-undone last. */
  readonly future: readonly Op[];
  /** Monotonic revision — bumped on every mutation (renderer invalidation). */
  readonly version: number;
}

export function createBoard(): Board {
  return { strokes: [], past: [], future: [], version: 0 };
}

/** Bump the version when deriving a mutated board. */
function rev(board: Board): number {
  return board.version + 1;
}

/** True when the given stroke id is present on the board. */
export function hasStroke(board: Board, id: string): boolean {
  return board.strokes.some((s) => s.id === id);
}

export function strokeById(board: Board, id: string): Stroke | undefined {
  return board.strokes.find((s) => s.id === id);
}

/** Return the op that reverses `op`, leaving the board in its prior state. */
export function invertOp(op: Op): Op {
  switch (op.type) {
    case "add":
      return { type: "remove", strokes: op.strokes };
    case "remove":
      return { type: "add", strokes: op.strokes };
    case "clear":
      return { type: "add", strokes: op.strokes };
    default:
      return op;
  }
}

/** Apply `op` to `strokes`, producing a new stroke array. */
function nextStrokes(strokes: readonly Stroke[], op: Op): readonly Stroke[] {
  switch (op.type) {
    case "add": {
      const existing = new Set(strokes.map((s) => s.id));
      const fresh = op.strokes.filter((s) => !existing.has(s.id));
      return [...strokes, ...fresh];
    }
    case "remove": {
      const removed = new Set(op.strokes.map((s) => s.id));
      return strokes.filter((s) => !removed.has(s.id));
    }
    case "clear":
      return [];
  }
}

/**
 * Commit `op` onto the board. Any prior future (redo) history is discarded —
 * the linear-history rule. Returns a new board.
 */
export function commit(board: Board, op: Op): Board {
  return {
    strokes: nextStrokes(board.strokes, op),
    past: [...board.past, op],
    future: [],
    version: rev(board),
  };
}

/** Undo the most recent operation. Returns the board unchanged if empty. */
export function undo(board: Board): Board {
  const op = board.past[board.past.length - 1];
  if (!op) return board;
  return {
    strokes: nextStrokes(board.strokes, invertOp(op)),
    past: board.past.slice(0, -1),
    future: [...board.future, op],
    version: rev(board),
  };
}

/** Redo the most recently undone operation. Returns the board unchanged. */
export function redo(board: Board): Board {
  const op = board.future[board.future.length - 1];
  if (!op) return board;
  return {
    strokes: nextStrokes(board.strokes, op),
    past: [...board.past, op],
    future: board.future.slice(0, -1),
    version: rev(board),
  };
}

/** Level of the most recent committed operation, or -1 for an empty ledger. */
export function headLevel(board: Board): number {
  return board.past.length - 1;
}
