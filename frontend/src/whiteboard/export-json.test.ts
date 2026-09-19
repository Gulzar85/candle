import { describe, expect, it } from "vitest";
import { buildExport, SCHEMA_VERSION } from "./export-json";
import type { ServerObject } from "./repository";

const OBJ: ServerObject = {
  object_type: "stroke",
  object_id: "s1",
  points: [{ x: 0, y: 0 }, { x: 1, y: 1 }],
  color: "#2563eb",
  width: 3,
  opacity: 1,
  creator_id: "AB",
};

describe("buildExport", () => {
  it("wraps objects in a versioned envelope", () => {
    const out = buildExport([OBJ], "Shared board");
    expect(out.schema_version).toBe(SCHEMA_VERSION);
    expect(out.board_title).toBe("Shared board");
    expect(out.objects).toHaveLength(1);
    expect(typeof out.exported_at).toBe("string");
    expect(Number.isNaN(Date.parse(out.exported_at))).toBe(false);
  });

  it("carries all fields import.py's validator requires", () => {
    const out = buildExport([OBJ], "Board");
    const [o] = out.objects;
    expect(o.object_id).toBe("s1");
    expect(o.points).toEqual(OBJ.points);
    expect(o.color).toBe("#2563eb");
    expect(o.width).toBe(3);
    expect(o.opacity).toBe(1);
  });

  it("never includes creator_id (account-identifying metadata)", () => {
    const out = buildExport([OBJ], "Board");
    expect(out.objects[0]).not.toHaveProperty("creator_id");
    expect(JSON.stringify(out)).not.toContain("creator_id");
  });

  it("handles an empty board", () => {
    const out = buildExport([], "Board");
    expect(out.objects).toEqual([]);
  });
});
