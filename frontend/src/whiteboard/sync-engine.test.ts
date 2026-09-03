/**
 * Unit tests for the SyncEngine — the offline-first synchronization engine.
 *
 * SyncEngine uses an in-memory fake WhiteboardLocalStoreLike so it runs in
 * Node without IndexedDB, exercising:
 *   - durable persistence before submission
 *   - crash recovery (SUBMITTING -> PENDING)
 *   - pending queue push with conflict reconciliation
 *   - partial failure (one op confirmed, another retried)
 *   - retry / backoff
 *   - stale-version re-reconciliation
 *   - idempotency-safe confirmation
 *   - never loses operations
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { SyncEngine, type SyncPhase } from "./sync-engine";
import type {
  WhiteboardLocalStoreLike,
  SyncEngineStatus,
} from "./sync-engine";
import type {
  LocalOperation,
  LocalOperationStatus,
  LocalConflictRecord,
  LocalWhiteboard,
} from "./local-store";
import type {
  LoadStateResponse,
  OperationEnvelope,
  SubmitResponse,
  WhiteboardRepository,
} from "./repository";

// ---------------------------------------------------------------------------
// In-memory fake store
// ---------------------------------------------------------------------------

class FakeStore implements WhiteboardLocalStoreLike {
  private ops = new Map<string, LocalOperation>();
  private boards = new Map<string, LocalWhiteboard>();
  private conflicts = new Map<string, LocalConflictRecord>();

  async connect(): Promise<void> {}
  async getClientId(): Promise<string> {
    return "fake-client";
  }
  async recoverInterrupted(): Promise<number> {
    let n = 0;
    for (const [id, op] of this.ops) {
      if (op.status === "SUBMITTING") {
        this.ops.set(id, { ...op, status: "PENDING" });
        n++;
      }
    }
    return n;
  }
  async saveOperation(op: LocalOperation): Promise<void> {
    this.ops.set(op.operation_id, op);
  }
  async updateOperationStatus(
    operationId: string,
    status: LocalOperationStatus,
    fields?: Partial<LocalOperation>,
  ): Promise<void> {
    const op = this.ops.get(operationId);
    if (!op) return;
    this.ops.set(operationId, { ...op, status, ...fields, updated_at: Date.now() });
  }
  async getPendingOperations(whiteboardId: string): Promise<LocalOperation[]> {
    return [...this.ops.values()]
      .filter(
        (op) =>
          op.whiteboard_id === whiteboardId &&
          (op.status === "PENDING" || op.status === "SUBMITTING"),
      )
      .sort((a, b) => a.client_sequence - b.client_sequence);
  }
  async countPendingForBoard(whiteboardId: string): Promise<number> {
    return (await this.getPendingOperations(whiteboardId)).length;
  }
  async getConflictsForBoard(whiteboardId: string): Promise<LocalConflictRecord[]> {
    return [...this.conflicts.values()].filter((c) => c.whiteboard_id === whiteboardId);
  }
  async saveConflict(record: LocalConflictRecord): Promise<void> {
    this.conflicts.set(record.operation_id, record);
  }
  async compactConfirmedOperations(
    _whiteboardId: string,
    _keepServerVersion: number,
  ): Promise<void> {
    for (const [id, op] of this.ops) {
      if (op.status === "CONFIRMED") this.ops.delete(id);
    }
  }
  async getWhiteboard(_whiteboardId: string): Promise<LocalWhiteboard | undefined> {
    return this.boards.get(_whiteboardId);
  }
  async saveWhiteboard(board: LocalWhiteboard): Promise<void> {
    this.boards.set(board.whiteboard_id, board);
  }
  async clearOwnerPartition(): Promise<void> {
    this.ops.clear();
    this.boards.clear();
    this.conflicts.clear();
  }
  async hasUnsyncedWork(): Promise<boolean> {
    return [...this.ops.values()].some(
      (op) => op.status === "PENDING" || op.status === "SUBMITTING",
    );
  }
  async listOperationsForBoard(_whiteboardId: string): Promise<LocalOperation[]> {
    return [...this.ops.values()].filter((o) => o.whiteboard_id === _whiteboardId);
  }
  async getOperationsForBoardByStatus(
    _whiteboardId: string,
    _status: LocalOperationStatus,
  ): Promise<LocalOperation[]> {
    return [...this.ops.values()].filter(
      (o) => o.whiteboard_id === _whiteboardId && o.status === _status,
    );
  }
  // Test helpers
  countOps(): number {
    return this.ops.size;
  }
}

// ---------------------------------------------------------------------------
// Mock repository
// ---------------------------------------------------------------------------

function createMockRepo(): {
  repo: WhiteboardRepository;
  submitBatch: ReturnType<typeof vi.fn>;
  loadState: ReturnType<typeof vi.fn>;
} {
  const submitBatch = vi.fn<() => Promise<SubmitResponse>>();
  const loadState = vi
    .fn<() => Promise<LoadStateResponse>>()
    .mockResolvedValue({ version: 0, objects: [], count: 0 });
  const repo = {
    submitBatch,
    loadState,
    submitOperation: submitBatch,
    loadOperations: vi.fn().mockResolvedValue({ operations: [], version: 0, count: 0 }),
  } as unknown as WhiteboardRepository;
  return { repo, submitBatch, loadState };
}

function makeEnvelope(base: number, type = "create_stroke"): OperationEnvelope {
  return {
    operation_id: `${type}-${base}-${Math.random().toString(36).slice(2, 8)}`,
    operation_type: type,
    base_version: base,
    payload: { object_id: `obj-${base}` },
  };
}

const ACK = (op: OperationEnvelope, version: number, duplicate = false): SubmitResponse => ({
  acks: [{ operation_id: op.operation_id, sequence: version, version, duplicate }],
  version,
  applied: duplicate ? 0 : 1,
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("SyncEngine", () => {
  let store: FakeStore;
  let mock: ReturnType<typeof createMockRepo>;
  let engine: SyncEngine;

  beforeEach(() => {
    vi.useFakeTimers();
    store = new FakeStore();
    mock = createMockRepo();
    engine = new SyncEngine("wb-1", "owner-1", mock.repo, {}, { store });
  });

  afterEach(() => {
    engine.destroy();
    vi.useRealTimers();
  });

  const init = async (): Promise<{ recovered: number }> => {
    return engine.initialize("client-x");
  };

  describe("initialize", () => {
    it("recovers SUBMITTING operations into PENDING and reports them", async () => {
      // Seed a SUBMITTING op as if a crunch interrupted before ack.
      await store.saveOperation(makeLocalOp("op-a", "SUBMITTING", 0, 1));
      const ret = await init();
      expect(ret.recovered).toBe(1);

      const pending = await store.getPendingOperations("wb-1");
      expect(pending).toHaveLength(1);
      expect(pending[0].status).toBe("PENDING");
    });
  });

  describe("submit (durable persistence)", () => {
    it("persists a local op before returning", async () => {
      await init();
      const env = makeEnvelope(0);
      const ok = await engine.submit(env, 0);
      expect(ok).toBe(true);
      expect(store.countOps()).toBe(1);
      const op = await store.getPendingOperations("wb-1");
      expect(op[0]).toMatchObject({ status: "PENDING", base_version: 0 });
    });

    it("does not lose an op when IndexedDB write throws (reports failure)", async () => {
      // Force saveOperation to reject.
      const spy = vi.spyOn(store, "saveOperation").mockRejectedValue(new Error("quota"));
      await init();
      const env = makeEnvelope(0);
      const ok = await engine.submit(env, 0);
      expect(ok).toBe(false);
      spy.mockRestore();
    });
  });

  describe("offline restore", () => {
    it("replays pending operations as stable envelopes for offline reopen", async () => {
      await init();
      const envA = makeEnvelope(0);
      const envB = makeEnvelope(1);
      await engine.submit(envA, 0);
      await engine.submit(envB, 1);

      const envelopes = await engine.listPendingAsEnvelopes();
      expect(envelopes).toHaveLength(2);
      // operation_id is preserved -> replay is idempotent on the server.
      expect(envelopes.map((e) => e.operation_id)).toEqual([envA.operation_id, envB.operation_id]);
      // Ordered by client_sequence so deterministic reconstruction holds.
      expect(envelopes[0].operation_id).toBe(envA.operation_id);
    });
  });

  describe("sync push", () => {
    it("confirms a create_stroke op and advances server version", async () => {
      await init();
      const env = makeEnvelope(0);
      await engine.submit(env, 0);
      mock.submitBatch.mockResolvedValue(ACK(env, 1));

      engine.triggerSync();
      await vi.advanceTimersByTimeAsync(100);

      // Op is confirmed and server version advanced.
      const pending = await store.getPendingOperations("wb-1");
      expect(pending).toHaveLength(0);
      expect(mock.submitBatch).toHaveBeenCalledTimes(1);
    });

    it("marks a destructive op as conflict when server advanced", async () => {
      await init();
      // Local clear generated against version 0, but server is now at version 5.
      const clearEnv = { ...makeEnvelope(0, "clear_canvas"), payload: {} };
      await engine.submit(clearEnv, 0);
      // Bump the engine's view of the server version so push sees the gap.
      engine.setServerVersionGetter(() => 5);

      engine.triggerSync();
      await vi.advanceTimersByTimeAsync(100);

      const conflicts = await store.getConflictsForBoard("wb-1");
      expect(conflicts.length).toBeGreaterThan(0);
      expect(engine.phase).toBe("conflict");
    });

    it("handles partial failure: A confirmed, B stays pending", async () => {
      await init();
      const envA = makeEnvelope(0, "create_stroke");
      const envB = makeEnvelope(1, "create_stroke");
      await engine.submit(envA, 0);
      await engine.submit(envB, 1);

      // A succeeds; B fails with a 500 (transient).
      mock.submitBatch
        .mockResolvedValueOnce(ACK(envA, 1))
        .mockRejectedValueOnce(makeHttpError(500, "boom"));

      engine.triggerSync();
      await vi.advanceTimersByTimeAsync(100);

      // A is confirmed and removed; B remains PENDING (not lost).
      const pending = await store.getPendingOperations("wb-1");
      expect(pending.some((o) => o.operation_id === envB.operation_id)).toBe(true);
      expect(pending.some((o) => o.operation_id === envA.operation_id)).toBe(false);
    });

    it("does not submit the same operation_id twice as duplicates", async () => {
      await init();
      const env = makeEnvelope(0);
      await engine.submit(env, 0);
      mock.submitBatch.mockResolvedValue(ACK(env, 1, true));

      engine.triggerSync();
      await vi.advanceTimersByTimeAsync(100);
      expect(mock.submitBatch).toHaveBeenCalledTimes(1);
    });
  });

  describe("retry / backoff", () => {
    it("bounded retry then marks FAILED (not silently dropped)", async () => {
      await init();
      const env = makeEnvelope(0);
      await engine.submit(env, 0);
      // Always fail with a network error.
      mock.submitBatch.mockRejectedValue(makeHttpError(0, "network"));

      engine.triggerSync();
      // Let several backoff cycles elapse.
      for (let i = 0; i < 12; i++) await vi.advanceTimersByTimeAsync(35000);

      const ops = await store.listOperationsForBoard("wb-1");
      expect(ops.some((o) => o.status === "FAILED")).toBe(true);
      // Critically: the op is NOT gone.
      expect(ops.length).toBeGreaterThan(0);
    });
  });

  describe("crash recovery via idempotency", () => {
    it("a SUBMITTING op is recovered and re-submitted, never duplicated", async () => {
      // Simulate: op persisted as SUBMITTING (submission started, browser died).
      const env = makeEnvelope(0);
      const localOp = makeLocalOp(env.operation_id, "SUBMITTING", 0, 1);
      await store.saveOperation(localOp);

      await init(); // recovers SUBMITTING -> PENDING

      // Server already has it: returns duplicate ack.
      mock.submitBatch.mockResolvedValue(ACK(env, 1, true));
      engine.triggerSync();
      await vi.advanceTimersByTimeAsync(100);

      const pending = await store.getPendingOperations("wb-1");
      expect(pending).toHaveLength(0);
      // Server saw the op exactly once (our submit), and the retry is a no-op
      // due to idempotency — the server's ack (duplicate) ends the recovery.
      expect(mock.submitBatch).toHaveBeenCalledTimes(1);
    });
  });

  describe("conflict surfaced, never silently deleted", () => {
    it("preserves the op in conflicts store on a permanent rejection", async () => {
      await init();
      const env = makeEnvelope(0);
      await engine.submit(env, 0);
      mock.submitBatch.mockRejectedValue(makeHttpError(403, "FORBIDDEN"));

      engine.triggerSync();
      await vi.advanceTimersByTimeAsync(100);

      const conflicts = await store.getConflictsForBoard("wb-1");
      expect(conflicts.length).toBe(1);
      expect(conflicts[0].operation_id).toBe(env.operation_id);
    });
  });
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeLocalOp(
  operationId: string,
  status: LocalOperationStatus,
  baseVersion: number,
  seq: number,
): LocalOperation {
  return {
    operation_id: operationId,
    whiteboard_id: "wb-1",
    owner_key: "owner-1",
    client_id: "client-x",
    operation_type: "create_stroke",
    payload: { object_id: "obj" },
    base_version: baseVersion,
    client_sequence: seq,
    local_created_at: Date.now(),
    status,
    retry_count: 0,
    created_at: Date.now(),
    updated_at: Date.now(),
  };
}

function makeHttpError(status: number, bodyError: string) {
  const err = new Error(bodyError) as Error & {
    status: number;
    apiError: { error: string };
  };
  err.status = status;
  err.apiError = { error: bodyError };
  return err;
}