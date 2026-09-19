import { describe, expect, it } from "vitest";
import {
  boundsOf,
  chaikin,
  computeResizeTransform,
  decimate,
  distance,
  distanceToSegment,
  distSq,
  handlePositions,
  MAX_SCALE,
  MIN_SCALE,
  nearestHandle,
  oppositeHandle,
  safeScaleFactor,
  scalePointsFromAnchor,
  translatePoints,
  unionBounds,
  visibleHandles,
} from "./geometry";
import type { Point } from "./types";

const P = (x: number, y: number): Point => ({ x, y });

describe("distSq / distance", () => {
  it("computes squared and euclidean distance", () => {
    expect(distSq(P(0, 0), P(3, 4))).toBe(25);
    expect(distance(P(0, 0), P(3, 4))).toBe(5);
  });
});

describe("decimate", () => {
  it("returns empty for empty input", () => {
    expect(decimate([], 1)).toEqual([]);
  });

  it("keeps first point and later points beyond min distance", () => {
    const points = [P(0, 0), P(0.5, 0.5), P(10, 10), P(10.2, 10.2)];
    const out = decimate(points, 2);
    expect(out.length).toBe(2);
    expect(out[0]).toEqual(P(0, 0));
    expect(out[1]).toEqual(P(10, 10));
  });

  it("drops null/jitter points", () => {
    const points = [P(0, 0), { x: NaN, y: 1 } as Point, P(50, 50)];
    const out = decimate(points, 1);
    expect(out).toHaveLength(2);
  });
});

describe("chaikin", () => {
  it("leaves 1-2 point inputs essentially unchanged", () => {
    expect(chaikin([P(0, 0)], 2)).toHaveLength(1);
    const two = chaikin([P(0, 0), P(10, 10)], 2);
    expect(two).toHaveLength(2);
  });

  it("increases point count and stays within the convex bracket", () => {
    const in3 = [P(0, 0), P(50, 100), P(100, 0)];
    const out = chaikin(in3, 1);
    expect(out.length).toBeGreaterThan(in3.length);
    // Endpoints are preserved.
    expect(out[0]).toEqual(P(0, 0));
    expect(out[out.length - 1]).toEqual(P(100, 0));
  });
});

describe("distanceToSegment", () => {
  it("returns perpendicular distance for a point beside the segment", () => {
    expect(distanceToSegment(P(5, 3), P(0, 0), P(10, 0))).toBeCloseTo(3);
  });

  it("clamps to the nearest endpoint", () => {
    expect(distanceToSegment(P(20, 1), P(0, 0), P(10, 0))).toBeCloseTo(Math.sqrt(101));
  });
});

describe("boundsOf / unionBounds", () => {
  it("returns null for empty input", () => {
    expect(boundsOf([])).toBeNull();
    expect(unionBounds([])).toBeNull();
  });

  it("computes union bounds", () => {
    const b = unionBounds([boundsOf([P(0, 0), P(4, 4)])!, boundsOf([P(2, -2), P(9, 1)])!]);
    expect(b).toEqual({ minX: 0, minY: -2, maxX: 9, maxY: 4 });
  });
});

describe("translatePoints", () => {
  it("shifts every point by (dx, dy)", () => {
    const out = translatePoints([P(0, 0), P(1, 1)], 5, -2);
    expect(out).toEqual([P(5, -2), P(6, -1)]);
  });
});

describe("scalePointsFromAnchor", () => {
  it("scales points away from a fixed anchor", () => {
    const out = scalePointsFromAnchor([P(0, 0), P(10, 10)], P(0, 0), 2, 2);
    expect(out).toEqual([P(0, 0), P(20, 20)]);
  });

  it("leaves the anchor point itself unchanged", () => {
    const out = scalePointsFromAnchor([P(5, 5)], P(5, 5), 3, 3);
    expect(out).toEqual([P(5, 5)]);
  });

  it("supports independent x/y scale factors", () => {
    const out = scalePointsFromAnchor([P(10, 10)], P(0, 0), 2, 0.5);
    expect(out).toEqual([P(20, 5)]);
  });
});

describe("safeScaleFactor", () => {
  it("computes a normal ratio", () => {
    expect(safeScaleFactor(20, 10)).toBeCloseTo(2);
  });

  it("never produces NaN/Infinity for a ~zero source length", () => {
    const result = safeScaleFactor(50, 0);
    expect(Number.isFinite(result)).toBe(true);
  });

  it("clamps to the server-accepted [MIN_SCALE, MAX_SCALE] range", () => {
    expect(safeScaleFactor(1_000_000, 1)).toBe(MAX_SCALE);
    expect(safeScaleFactor(0.0000001, 1)).toBe(MIN_SCALE);
  });
});

describe("handlePositions / oppositeHandle", () => {
  const box = { minX: 0, minY: 0, maxX: 10, maxY: 20 };

  it("places all 8 handles at the corners and edge midpoints", () => {
    const h = handlePositions(box);
    expect(h.nw).toEqual(P(0, 0));
    expect(h.se).toEqual(P(10, 20));
    expect(h.n).toEqual(P(5, 0));
    expect(h.e).toEqual(P(10, 10));
  });

  it("every handle's opposite is diagonally/across from it", () => {
    expect(oppositeHandle("nw")).toBe("se");
    expect(oppositeHandle("n")).toBe("s");
    expect(oppositeHandle("e")).toBe("w");
    // Opposite is symmetric.
    expect(oppositeHandle(oppositeHandle("ne"))).toBe("ne");
  });
});

describe("visibleHandles (degenerate bbox handling)", () => {
  it("shows all 8 handles for a normal box", () => {
    const box = { minX: 0, minY: 0, maxX: 10, maxY: 10 };
    expect(visibleHandles(box)).toHaveLength(8);
  });

  it("shows only n/s for a perfectly vertical stroke's bbox", () => {
    const verticalLine = { minX: 5, minY: 0, maxX: 5, maxY: 10 };
    expect(visibleHandles(verticalLine).sort()).toEqual(["n", "s"]);
  });

  it("shows only w/e for a perfectly horizontal stroke's bbox", () => {
    const horizontalLine = { minX: 0, minY: 5, maxX: 10, maxY: 5 };
    expect(visibleHandles(horizontalLine).sort()).toEqual(["e", "w"]);
  });

  it("shows no handles for a single-point (zero-area) bbox", () => {
    const point = { minX: 5, minY: 5, maxX: 5, maxY: 5 };
    expect(visibleHandles(point)).toEqual([]);
  });
});

describe("nearestHandle", () => {
  const box = { minX: 0, minY: 0, maxX: 10, maxY: 10 };
  const positions = handlePositions(box);
  const visible = visibleHandles(box);

  it("finds the nearest handle within tolerance", () => {
    expect(nearestHandle(P(0.5, 0.5), positions, visible, 5)).toBe("nw");
  });

  it("returns null when nothing is within tolerance", () => {
    expect(nearestHandle(P(5, 5), positions, visible, 1)).toBeNull();
  });

  it("only considers handles in the visible list", () => {
    // "n" is at (5, 0); excluding it from `visible` must not match it even
    // though it's the closest handle geometrically.
    const withoutN = visible.filter((h) => h !== "n");
    expect(nearestHandle(P(5, 0.1), positions, withoutN, 5)).not.toBe("n");
  });
});

describe("computeResizeTransform", () => {
  const box = { minX: 0, minY: 0, maxX: 10, maxY: 10 };

  it("dragging the se corner scales both axes from the nw anchor", () => {
    const t = computeResizeTransform(box, "se", P(20, 20));
    expect(t.anchor).toEqual(P(0, 0));
    expect(t.scaleX).toBeCloseTo(2);
    expect(t.scaleY).toBeCloseTo(2);
  });

  it("dragging an edge handle (e) only scales its own axis", () => {
    const t = computeResizeTransform(box, "e", P(30, 999 /* irrelevant for e */));
    expect(t.anchor).toEqual(P(0, 5)); // opposite of "e" is "w"
    expect(t.scaleX).toBeCloseTo(3);
    expect(t.scaleY).toBe(1);
  });

  it("dragging the n handle only scales y", () => {
    // anchor for "n" is "s" at (5, 10). Original extent anchor->handle is
    // 0 - 10 = -10; dragging to y=-10 makes the new extent -10 - 10 = -20,
    // i.e. doubling the height (scaleY = 2).
    const t = computeResizeTransform(box, "n", P(999, -10));
    expect(t.scaleX).toBe(1);
    expect(t.scaleY).toBeCloseTo(2);
  });

  it("never returns a non-finite scale for a degenerate source box", () => {
    const verticalLine = { minX: 5, minY: 0, maxX: 5, maxY: 10 };
    // Only n/s are "visible", but computeResizeTransform itself must still
    // behave safely even if called with a non-visible handle defensively.
    const t = computeResizeTransform(verticalLine, "e", P(50, 5));
    expect(Number.isFinite(t.scaleX)).toBe(true);
    expect(Number.isFinite(t.scaleY)).toBe(true);
  });
});
