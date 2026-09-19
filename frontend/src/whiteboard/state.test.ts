import { describe, expect, it } from "vitest";
import { commit, createBoard, hasStroke, redo, strokeById, undo } from "./state";
import type { Op, Stroke, StrokeStyle } from "./types";

const STYLE: StrokeStyle = { color: "#2563eb", width: 3, opacity: 1 };
const stroke = (id: string): Stroke => ({ id, points: [{ x: 0, y: 0 }, { x: 1, y: 1 }], style: STYLE });

const add = (s: Stroke): Op => ({ type: "add", strokes: [s] });

describe("commit / undo / redo", () => {
  it("increments version and appends strokes", () => {
    let board = createBoard();
    board = commit(board, add(stroke("a")));
    board = commit(board, add(stroke("b")));
    expect(board.strokes.map((s) => s.id)).toEqual(["a", "b"]);
    expect(board.version).toBe(2);
    expect(board.past.length).toBe(2);
  });

  it("undo reverses the last op and feeds redo", () => {
    let board = createBoard();
    board = commit(board, add(stroke("a")));
    board = commit(board, add(stroke("b")));
    board = undo(board);
    expect(board.strokes.map((s) => s.id)).toEqual(["a"]);
    expect(board.future.length).toBe(1);
    board = redo(board);
    expect(board.strokes.map((s) => s.id)).toEqual(["a", "b"]);
    expect(board.future.length).toBe(0);
  });

  it("undo/redo are no-ops when empty", () => {
    const board = createBoard();
    expect(undo(board).version).toBe(0);
    expect(redo(board).version).toBe(0);
  });

  it("a new commit clears the redo future", () => {
    let board = createBoard();
    board = commit(board, add(stroke("a")));
    board = undo(board);
    expect(board.future.length).toBe(1);
    board = commit(board, add(stroke("b")));
    expect(board.future.length).toBe(0);
  });

  it("remove and clear ops invert correctly", () => {
    let board = createBoard();
    board = commit(board, add(stroke("a")));
    board = commit(board, add(stroke("b")));
    const b = strokeById(board, "b")!;
    board = commit(board, { type: "remove", strokes: [b] });
    expect(board.strokes.map((s) => s.id)).toEqual(["a"]);
    board = undo(board);
    expect(board.strokes.map((s) => s.id)).toEqual(["a", "b"]);

    board = commit(board, { type: "clear", strokes: board.strokes });
    expect(board.strokes).toHaveLength(0);
    board = undo(board);
    expect(board.strokes.map((s) => s.id)).toEqual(["a", "b"]);
  });
});

describe("move / resize ops", () => {
  it("move commits the new stroke and inverts back to the original", () => {
    let board = createBoard();
    const a = stroke("a");
    board = commit(board, add(a));
    const moved: Stroke = { ...a, points: [{ x: 5, y: 5 }, { x: 6, y: 6 }] };
    board = commit(board, { type: "move", from: a, to: moved });
    expect(strokeById(board, "a")?.points).toEqual(moved.points);

    board = undo(board);
    expect(strokeById(board, "a")?.points).toEqual(a.points);

    board = redo(board);
    expect(strokeById(board, "a")?.points).toEqual(moved.points);
  });

  it("resize commits the new stroke and inverts back via reciprocal scale", () => {
    let board = createBoard();
    const a = stroke("a");
    board = commit(board, add(a));
    const resized: Stroke = { ...a, points: [{ x: 0, y: 0 }, { x: 2, y: 2 }] };
    board = commit(board, {
      type: "resize",
      from: a,
      to: resized,
      anchor: { x: 0, y: 0 },
      scaleX: 2,
      scaleY: 2,
    });
    expect(strokeById(board, "a")?.points).toEqual(resized.points);

    board = undo(board);
    expect(strokeById(board, "a")?.points).toEqual(a.points);
  });

  it("move/resize do not affect other strokes", () => {
    let board = createBoard();
    const a = stroke("a");
    const b = stroke("b");
    board = commit(board, add(a));
    board = commit(board, add(b));
    const moved: Stroke = { ...a, points: [{ x: 9, y: 9 }, { x: 10, y: 10 }] };
    board = commit(board, { type: "move", from: a, to: moved });
    expect(strokeById(board, "b")?.points).toEqual(b.points);
  });
});

describe("hasStroke / strokeById", () => {
  it("queries by id", () => {
    const board = commit(createBoard(), add(stroke("a")));
    expect(hasStroke(board, "a")).toBe(true);
    expect(hasStroke(board, "zzz")).toBe(false);
    expect(strokeById(board, "a")?.id).toBe("a");
  });
});
