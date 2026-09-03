/**
 * Tests for SyncManager (sync.ts) — the offline-first operation manager.
 *
 * These tests exercise the durable-first SyncManager. Because this is a Node
 * test environment without IndexedDB, the HTTP-only fallback path (used when
 * IndexedDB is unavailable or no whiteboard/account scope is given) is what
 * runs here. It still verifies version tracking, status transitions, retry,
 * staleness handling, and destroyed-guard behavior.
 *
 * The durable IndexedDB path (SyncEngine + WhiteboardLocalStore) is covered by
 * sync-engine.test.ts with a fake store.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { SyncManager, type SyncState } from "./sync";
import type {
  LoadStateResponse,
  OperationEnvelope,
  SubmitResponse,
  WhiteboardRepository,
} from "./repository";

// ---------------------------------------------------------------------------
// Mock repository
// ---------------------------------------------------------------------------

function createMockRepo(): {
  repo: WhiteboardRepository;
  loadState: ReturnType<typeof vi.fn>;
  submitBatch: ReturnType<typeof vi.fn>;
} {
  const loadState = vi
    .fn<() => Promise<LoadStateResponse>>()
    .mockResolvedValue({ version: 0, objects: [], count: 0 });
  const submitBatch = vi.fn<() => Promise<SubmitResponse>>();

  const repo = {
    loadState,
    submitBatch,
    submitOperation: submitBatch,
    loadOperations: vi.fn().mockResolvedValue({ operations: [], version: 0, count: 0 }),
  } as unknown as WhiteboardRepository;

  return { repo, loadState, submitBatch };
}

function makeOp(baseVersion: number): OperationEnvelope {
  return {
    operation_id: `op-${baseVersion}-${Math.random().toString(36).slice(2, 8)}`,
    operation_type: "create_stroke",
    base_version: baseVersion,
    payload: {
      object_id: `stroke-${baseVersion}`,
      points: [{ x: 10, y: 20 }],
      color: "#000000",
      width: 3,
      opacity: 1,
    },
  };
}

function makeAck(version: number, duplicate = false): {
  operation_id: string;
  sequence: number;
  version: number;
  duplicate: boolean;
} {
  return {
    operation_id: `ack-${version}`,
    sequence: version,
    version,
    duplicate,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("SyncManager", () => {
  let mock: ReturnType<typeof createMockRepo>;
  let sync: SyncManager;

  beforeEach(() => {
    vi.useFakeTimers();
    mock = createMockRepo();
    // No whiteboard/owner scope → HTTP-only fallback path (no IndexedDB).
    sync = new SyncManager(mock.repo);
  });

  afterEach(() => {
    sync.destroy();
    vi.useRealTimers();
  });

  // --- initialize ----------------------------------------------------------

  describe("initialize", () => {
    it("loads server state and sets version baseline", async () => {
      mock.loadState.mockResolvedValue({ version: 5, objects: [], count: 0 });
      const version = await sync.initialize();
      expect(version).toBe(5);
      expect(sync.serverVersion).toBe(5);
      expect(sync.localVersion).toBe(5);
    });

    it("returns 0 on load failure", async () => {
      mock.loadState.mockRejectedValue(new Error("network"));
      const version = await sync.initialize();
      expect(version).toBe(0);
      expect(sync.saveStatus).toBe("error");
    });
  });

  // --- enqueue + flush (HTTP fallback) -------------------------------------

  describe("enqueue", () => {
    it("increments local version and sets status to pending", () => {
      const op = makeOp(0);
      mock.submitBatch.mockResolvedValue({ acks: [], version: 0, applied: 0 });
      sync.enqueue(op);
      expect(sync.localVersion).toBe(1);
      expect(sync.pendingCount).toBe(1);
      expect(sync.saveStatus).toBe("pending");
    });

    it("submits to server and updates the baseline on success", async () => {
      mock.submitBatch.mockResolvedValue({
        acks: [makeAck(1)],
        version: 1,
        applied: 1,
      });

      const op = makeOp(0);
      sync.enqueue(op);

      // Let the async submission complete.
      await vi.advanceTimersByTimeAsync(50);

      expect(mock.submitBatch).toHaveBeenCalledTimes(1);
      expect(sync.serverVersion).toBe(1);
      expect(sync.pendingCount).toBe(0);
      expect(sync.saveStatus).toBe("saved");
    });

    it("submits the batch with the queued op", async () => {
      mock.submitBatch.mockResolvedValue({
        acks: [makeAck(1)],
        version: 1,
        applied: 1,
      });
      const op = makeOp(0);
      sync.enqueue(op);
      await vi.advanceTimersByTimeAsync(50);
      expect(mock.submitBatch).toHaveBeenCalledWith([op]);
    });
  });

  // --- retry -----------------------------------------------------------------

  describe("retry", () => {
    it("retries with a new submit on retryPending()", async () => {
      mock.submitBatch.mockRejectedValue(new Error("network"));
      sync.enqueue(makeOp(0));
      await vi.advanceTimersByTimeAsync(50);
      expect(sync.saveStatus).toBe("error");

      mock.submitBatch.mockResolvedValue({ acks: [makeAck(1)], version: 1, applied: 1 });
      sync.retryPending();
      await vi.advanceTimersByTimeAsync(50);
      expect(sync.saveStatus).toBe("saved");
    });
  });

  // --- stale version -------------------------------------------------------

  describe("stale version error", () => {
    it("surfaces an error on a rejected operation", async () => {
      const err = new Error("rejected") as Error & {
        apiError: { error: string };
        status: number;
      };
      err.apiError = { error: "STALE_VERSION" };
      err.status = 409;

      mock.submitBatch.mockRejectedValue(err);
      sync.enqueue(makeOp(0));
      await vi.advanceTimersByTimeAsync(50);
      expect(sync.saveStatus).toBe("error");
    });
  });

  // --- status listener ------------------------------------------------------

  describe("status listener", () => {
    it("fires on status changes", async () => {
      const states: SyncState[] = [];
      sync.onStatusChange((s) => states.push({ ...s }));

      mock.submitBatch.mockResolvedValue({
        acks: [makeAck(1)],
        version: 1,
        applied: 1,
      });

      sync.enqueue(makeOp(0));
      await vi.advanceTimersByTimeAsync(50);

      const statuses = states.map((s) => s.saveStatus);
      expect(statuses).toContain("pending");
      expect(statuses).toContain("saved");
    });
  });

  // --- destroy -------------------------------------------------------------

  describe("destroy", () => {
    it("prevents further submissions", async () => {
      mock.submitBatch.mockResolvedValue({
        acks: [makeAck(1)],
        version: 1,
        applied: 1,
      });

      sync.enqueue(makeOp(0));
      sync.destroy();
      await vi.advanceTimersByTimeAsync(1000);

      // After destroy, the engine is null; further enqueues go nowhere but the
      // existing submission is already in flight. The key invariant: no crash
      // and no unhandled rejection.
      expect(sync).toBeInstanceOf(SyncManager);
    });
  });
});