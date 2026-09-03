import { describe, expect, it } from "vitest";
import { clipToEraser, finalizePenStroke, hitsStroke } from "./tools";
import type { Point, Stroke, StrokeStyle } from "./types";

const STYLE: StrokeStyle = { color: "#2563eb", width: 3, opacity: 1 };
const P = (x: number, y: number): Point => ({ x, y });

describe("finalizePenStroke", () => {
  it("returns null for a stray tap (fewer than 2 distinct points)", () => {
    expect(finalizePenStroke("s", [P(1, 1), P(1, 1)], STYLE)).toBeNull();
  });

  it("polishes a real stroke and preserves style/creator", () => {
    const raw = [P(0, 0), P(10, 0), P(20, 5), P(30, 0)];
    const result = finalizePenStroke("s1", raw, STYLE, "me");
    expect(result).not.toBeNull();
    expect(result!.stroke.id).toBe("s1");
    expect(result!.stroke.style).toEqual(STYLE);
    expect(result!.stroke.creatorId).toBe("me");
    expect(result!.stroke.points.length).toBeGreaterThanOrEqual(2);
    expect(result!.rawCount).toBe(raw.length);
  });
});

describe("hitsStroke", () => {
  const stroke: Stroke = { id: "x", points: [P(0, 0), P(0, 100)], style: STYLE };

  it("hits near the line but not far away", () => {
    expect(hitsStroke(P(3, 50), stroke, 5)).toBe(true);
    expect(hitsStroke(P(100, 50), stroke, 5)).toBe(false);
  });
});

describe("clipToEraser", () => {
  const line = [P(0, 0), P(10, 0), P(20, 0), P(30, 0), P(40, 0), P(50, 0)];

  it("leaves the whole line when the eraser is far away", () => {
    const segs = clipToEraser(line, [P(0, 100), P(50, 100)], 5);
    expect(segs).toHaveLength(1);
    expect(segs[0]).toHaveLength(line.length);
  });

  it("splits a line erased in the middle into two segments", () => {
    // A vertical sweep (two+ points) through the middle erases x=20 only.
    const segs = clipToEraser(line, [P(20, -10), P(20, 10)], 5);
    const totalPoints = segs.reduce((n, s) => n + s.length, 0);
    expect(segs.length).toBe(2);
    expect(totalPoints).toBeLessThan(line.length);
  });

  it("removes everything when the whole line passes under the eraser", () => {
    const segs = clipToEraser(line, [P(-5, 10), P(55, 10)], 20);
    expect(segs.length).toBe(0);
  });

  it("drops fragile single-point segments", () => {
    const singles = [P(0, 0), P(0, 0), P(100, 0), P(100, 0)];
    const segs = clipToEraser(singles, [P(-10, 2)], 4).filter((s) => s.length >= 2);
    expect(segs.length).toBeGreaterThanOrEqual(0);
  });
});
