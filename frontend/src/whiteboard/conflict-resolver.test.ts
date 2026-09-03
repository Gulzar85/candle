/**
 * Unit tests for the operation conflict resolver.
 */

import { describe, it, expect } from "vitest";

import {
  decideReconciliation,
  describeConflict,
  type OperationType,
} from "./conflict-resolver";

const TYPES: OperationType[] = ["create_stroke", "delete_object", "clear_canvas"];

describe("decideReconciliation", () => {
  it("submits directly when the server version matches base", () => {
    for (const t of TYPES) {
      expect(decideReconciliation({ operation_type: t, base_version: 3, server_version: 3 }))
        .toEqual({ action: "submit" });
    }
  });

  it("rebases additive create_stroke when the server advanced", () => {
    const d = decideReconciliation({
      operation_type: "create_stroke",
      base_version: 1,
      server_version: 4,
    });
    expect(d).toEqual({ action: "rebase", newBaseVersion: 4 });
  });

  it("rebases delete_object only when the target is known gone; else rejects", () => {
    // Target known gone -> safe to replay (idempotent no-op on server).
    expect(
      decideReconciliation({
        operation_type: "delete_object",
        base_version: 1,
        server_version: 5,
        cached_object_exists: false,
      }),
    ).toEqual({ action: "rebase", newBaseVersion: 5 });

    // Unknown -> conservative reject.
    expect(
      decideReconciliation({
        operation_type: "delete_object",
        base_version: 1,
        server_version: 5,
      }),
    ).toEqual({ action: "reject" });
  });

  it("always rejects clear_canvas when the server has advanced", () => {
    expect(
      decideReconciliation({ operation_type: "clear_canvas", base_version: 1, server_version: 5 }),
    ).toEqual({ action: "reject" });
  });

  it("rejects unknown operation types conservatively", () => {
    expect(
      decideReconciliation({
        operation_type: "unknown" as OperationType,
        base_version: 0,
        server_version: 2,
      }),
    ).toEqual({ action: "reject" });
  });
});

describe("describeConflict", () => {
  it("returns a human-readable (non-technical) message", () => {
    const msg = describeConflict("clear_canvas", 1, 5);
    expect(msg).toContain("Confirm before applying");
    expect(msg.toLowerCase()).not.toContain("stale_version");
  });
});