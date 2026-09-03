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

describe("hasStroke / strokeById", () => {
  it("queries by id", () => {
    const board = commit(createBoard(), add(stroke("a")));
    expect(hasStroke(board, "a")).toBe(true);
    expect(hasStroke(board, "zzz")).toBe(false);
    expect(strokeById(board, "a")?.id).toBe("a");
  });
});
