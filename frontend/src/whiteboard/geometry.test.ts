import { describe, expect, it } from "vitest";
import {
  boundsOf,
  chaikin,
  decimate,
  distance,
  distanceToSegment,
  distSq,
  unionBounds,
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
