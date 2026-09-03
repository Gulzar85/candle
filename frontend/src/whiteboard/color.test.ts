import { describe, expect, it } from "vitest";
import {
  DEFAULT_PALETTE,
  clamp,
  isHexColor,
  normalizeOpacity,
  normalizeWidth,
  resolveColor,
} from "./color";

describe("isHexColor", () => {
  it("accepts 6-digit hex only", () => {
    expect(isHexColor("#2563eb")).toBe(true);
    expect(isHexColor("#ABC")).toBe(false);
    expect(isHexColor("blue")).toBe(false);
    expect(isHexColor("#2563e")).toBe(false);
  });
});

describe("normalizeWidth / normalizeOpacity", () => {
  it("clamps width to [1, 64] and rounds", () => {
    expect(normalizeWidth(0)).toBe(1);
    expect(normalizeWidth(100)).toBe(64);
    expect(normalizeWidth(3.7)).toBe(4);
  });

  it("clamps opacity to [0, 1]", () => {
    expect(normalizeOpacity(-1)).toBe(0);
    expect(normalizeOpacity(2)).toBe(1);
    expect(normalizeOpacity(0.55)).toBe(0.55);
  });
});

describe("clamp", () => {
  it("clamps into [min, max]", () => {
    expect(clamp(1, 2, 5)).toBe(2);
    expect(clamp(9, 2, 5)).toBe(5);
    expect(clamp(3, 2, 5)).toBe(3);
  });
});

describe("resolveColor", () => {
  it("lowercases a valid hex and defaults invalid", () => {
    expect(resolveColor("#2563EB")).toBe("#2563eb");
    expect(resolveColor("#notacolor")).toBe(DEFAULT_PALETTE[0]);
  });
});
