import { describe, expect, it } from "vitest";
import {
  clampZoom,
  fitToShow,
  makeViewport,
  panBy,
  screenToWorld,
  worldToScreen,
  zoomAt,
  zoomPercent,
} from "./viewport";

const SIZE = { w: 1000, h: 800 };

describe("worldToScreen / screenToWorld", () => {
  it("round-trips", () => {
    const vp = makeViewport(50, -30, 2);
    const origin = worldToScreen({ x: 50, y: -30 }, SIZE, vp);
    expect(origin).toEqual({ x: 500, y: 400 });
    const back = screenToWorld(origin, SIZE, vp);
    expect(back.x).toBeCloseTo(50);
    expect(back.y).toBeCloseTo(-30);
  });
});

describe("zoomAt keeps the anchor stationary", () => {
  it("world point under the anchor does not move", () => {
    const vp = makeViewport(0, 0, 1);
    const anchor = { x: 300, y: 200 };
    const before = screenToWorld(anchor, SIZE, vp);
    const next = zoomAt(vp, SIZE, anchor, 2);
    const after = screenToWorld(anchor, SIZE, next);
    expect(next.zoom).toBe(2);
    expect(after.x).toBeCloseTo(before.x);
    expect(after.y).toBeCloseTo(before.y);
  });
});

describe("clampZoom", () => {
  it("clamps to [0.2, 4]", () => {
    expect(zoomAt(makeViewport(), SIZE, { x: 0, y: 0 }, 100).zoom).toBe(4);
    expect(zoomAt(makeViewport(), SIZE, { x: 0, y: 0 }, 0.001).zoom).toBeCloseTo(0.2);
  });
});

describe("panBy", () => {
  it("moves the center opposite the drag", () => {
    const vp = makeViewport(10, 10, 2);
    const next = panBy(vp, 20, -10);
    expect(next.cx).toBeCloseTo(0);
    expect(next.cy).toBeCloseTo(15);
    expect(next.zoom).toBe(2);
  });
});

describe("fitToShow", () => {
  it("frames a non-empty box within the padded viewport", () => {
    const fit = fitToShow({ minX: 0, minY: 0, maxX: 100, maxY: 100 }, SIZE, 48);
    expect(fit).not.toBeNull();
    expect(fit!.cx).toBe(50);
    expect(fit!.cy).toBe(50);
    expect(fit!.zoom).toBeGreaterThan(0);
  });

  it("frames a single-point box at default zoom centered on it", () => {
    // A zero-area box has nothing meaningful to frame; the fallback centers the
    // point at zoom 1 rather than returning null (so Cmd+F never blanks view).
    const fit = fitToShow({ minX: 5, minY: 5, maxX: 5, maxY: 5 }, SIZE);
    expect(fit).not.toBeNull();
    expect(fit!.cx).toBe(5);
    expect(fit!.cy).toBe(5);
    expect(fit!.zoom).toBe(1);
  });
});

describe("zoomPercent", () => {
  it("rounds to whole percent", () => {
    expect(zoomPercent(makeViewport(0, 0, 1.2345))).toBe(123);
  });
});
