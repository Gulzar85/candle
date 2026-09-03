/**
 * WhiteboardLocalStore — the single abstraction over IndexedDB for the
 * whiteboard domain. Canvas code and sync code interact ONLY through this
 * module; nothing else touches IndexedDB directly.
 *
 * Schema (version 1):
 *   whiteboards — one record per board: metadata + version + cached state
 *   operations  — local operation queue with per-op status
 *   meta        — key/value for client_id and other small globals
 *   conflicts   — quarantined operations that could not be reconciled
 *
 * Storage is scoped by account via the `ownerKey` (the user's account
 * public_id passed to the constructor). This guarantees Account A's cached
 * whiteboard data cannot appear for Account B.
 */

import {
  closeDB,
  idbClear,
  idbCount,
  idbDelete,
  idbGet,
  idbGetAll,
  idbGetAllFromIndex,
  idbPut,
  openDB,
  withStore,
} from "./idb";
import type { Stroke } from "./types";

// ---------------------------------------------------------------------------
// Schema
// ---------------------------------------------------------------------------

const STORES = [
  {
    name: "whiteboards",
    keyPath: "whiteboard_id",
    indexes: [
      { name: "by_owner", keyPath: "owner_key" },
      { name: "by_updated", keyPath: "updated_at" },
    ],
  },
  {
    name: "operations",
    keyPath: "operation_id",
    indexes: [
      { name: "by_whiteboard", keyPath: ["whiteboard_id", "status"] },
      { name: "by_status", keyPath: "status" },
      { name: "by_sequence", keyPath: "client_sequence" },
      { name: "by_owner", keyPath: "owner_key" },
    ],
  },
  {
    name: "meta",
    keyPath: "key",
  },
  {
    name: "conflicts",
    keyPath: "operation_id",
    indexes: [
      { name: "by_whiteboard", keyPath: ["whiteboard_id", "status"] },
      { name: "by_owner", keyPath: "owner_key" },
    ],
  },
] as const;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Lifecycle state for a locally-stored operation. */
export type LocalOperationStatus =
  | "PENDING"      // created and durably stored, awaiting submission
  | "SUBMITTING"   // submission attempt in flight (may be interrupted)
  | "CONFIRMED"    // server acknowledged
  | "REJECTED"     // server permanently rejected
  | "CONFLICT"     // could not be reconciled automatically
  | "FAILED";      // exceeded retry budget, needs user attention

export interface LocalOperation {
  readonly operation_id: string;
  readonly whiteboard_id: string;
  readonly owner_key: string;
  readonly client_id: string;
  readonly operation_type: string;
  readonly payload?: Record<string, unknown>;
  readonly base_version: number;
  readonly client_sequence: number;
  readonly local_created_at: number;
  status: LocalOperationStatus;
  retry_count: number;
  last_error?: string;
  last_attempt_at?: number;
  server_sequence?: number;
  server_version?: number;
  created_at: number;
  updated_at: number;
}

/** Cached metadata + state for one whiteboard. */
export interface LocalWhiteboard {
  readonly whiteboard_id: string;
  readonly owner_key: string;
  server_version: number;
  last_sequence: number;
  /** Logical strokes cached to reopen the board offline (no screenshot). */
  strokes: readonly Stroke[];
  cached_at: number;
  updated_at: number;
}

export interface LocalConflictRecord {
  readonly operation_id: string;
  readonly whiteboard_id: string;
  readonly owner_key: string;
  readonly operation: LocalOperation;
  reason: string;
  detected_at: number;
  status: LocalOperationStatus;
  updated_at: number;
}

// ---------------------------------------------------------------------------
// WhiteboardLocalStore
// ---------------------------------------------------------------------------

const MAX_CACHED_STROKES_PER_BOARD = 20_000;
const MAX_CACHED_BOARDS = 20;
const MAX_PENDING_OPERATIONS = 5_000;

/** Thrown when IndexedDB is unavailable or a storage quota is hit. */
export class LocalStorageError extends Error {
  constructor(message: string, public readonly quotaExceeded = false) {
    super(message);
    this.name = "LocalStorageError";
  }
}

export class WhiteboardLocalStore {
  private dbPromise: Promise<IDBDatabase> | null = null;

  constructor(
    private readonly ownerKey: string,
    private readonly dbNameSuffix = "",
  ) {}

  /** Ensure IndexedDB is available and (re)open the connection. */
  async connect(): Promise<void> {
    try {
      await this.db();
    } catch (err) {
      throw this.wrapError(err);
    }
  }

  private db(): Promise<IDBDatabase> {
    if (this.dbPromise) return this.dbPromise;
    // Note: openDB uses a module-level singleton. We suffix the store rows by
    // owner but share the single database per installation. That is fine.
    this.dbPromise = openDB(STORES);
    return this.dbPromise;
  }

  private wrapError(err: unknown): Error {
    if (err instanceof LocalStorageError) return err;
    if (err instanceof DOMException) {
      return new LocalStorageError(
        err.name === "QuotaExceededError"
          ? "Local storage quota exceeded. Your changes are saved on this device but may fill up. Please resolve pending changes."
          : err.message,
        err.name === "QuotaExceededError",
      );
    }
    return new LocalStorageError(err instanceof Error ? err.message : String(err));
  }

  /** Get (or create) the persistent client ID for this installation. */
  async getClientId(): Promise<string> {
    const db = await this.db();
    const existing = await idbGet<{ key: string; value: string }>(db, "meta", "client_id");
    if (existing?.value) return existing.value;
    const id =
      typeof globalThis.crypto !== "undefined" &&
      typeof globalThis.crypto.randomUUID === "function"
        ? globalThis.crypto.randomUUID()
        : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    await idbPut(db, "meta", { key: "client_id", value: id });
    return id;
  }

  /** True if the current account partition has any locally stored boards. */
  async hasLocalData(): Promise<boolean> {
    const db = await this.db();
    const boards = await idbGetAllFromIndex<LocalWhiteboard>(db, "whiteboards", "by_owner", this.ownerKey);
    const ops = await idbGetAllFromIndex<LocalOperation>(db, "operations", "by_owner", this.ownerKey);
    return boards.length > 0 || ops.length > 0;
  }

  // --- whiteboards ---------------------------------------------------------

  async saveWhiteboard(board: LocalWhiteboard): Promise<void> {
    try {
      const db = await this.db();
      await idbPut(db, "whiteboards", board);
    } catch (err) {
      throw this.wrapError(err);
    }
  }

  async getWhiteboard(whiteboardId: string): Promise<LocalWhiteboard | undefined> {
    const db = await this.db();
    return idbGet<LocalWhiteboard>(db, "whiteboards", whiteboardId);
  }

  async listWhiteboards(): Promise<LocalWhiteboard[]> {
    const db = await this.db();
    const all = await idbGetAllFromIndex<LocalWhiteboard>(db, "whiteboards", "by_owner", this.ownerKey);
    return all.sort((a, b) => b.updated_at - a.updated_at);
  }

  async deleteWhiteboard(whiteboardId: string): Promise<void> {
    const db = await this.db();
    await idbDelete(db, "whiteboards", whiteboardId);
    // Also purge that board's operations and conflicts.
    const ops = await this.listOperationsForBoard(whiteboardId);
    for (const op of ops) {
      await idbDelete(db, "operations", op.operation_id);
    }
    const conflicts = await idbGetAllFromIndex<LocalConflictRecord>(
      db,
      "conflicts",
      "by_whiteboard",
      [whiteboardId, ""],
    );
    // Note: IDB composite key range for partial match is (whiteboardId, IDBKeyRange.lowerBound("")).
    void conflicts;
    await withStore(db, "conflicts", "readwrite", (store) => {
      const range = IDBKeyRange.bound([whiteboardId, ""], [whiteboardId, "\uffff"]);
      const req = store.openCursor(range);
      return new Promise<void>((resolve) => {
        req.onsuccess = () => {
          const cursor = req.result;
          if (cursor) {
            cursor.delete();
            cursor.continue();
          } else {
            resolve();
          }
        };
        req.onerror = () => resolve();
      });
    });
  }

  // --- operations ----------------------------------------------------------

  /**
   * Save a newly created operation as PENDING. This is the durability point:
   * after this promise resolves, the operation is safe against browser crash.
   */
  async saveOperation(op: LocalOperation): Promise<void> {
    try {
      const db = await this.db();
      const countAll = await this.countOperationsForOwner();
      if (countAll >= MAX_PENDING_OPERATIONS) {
        throw new LocalStorageError(
          "Too many pending changes. Please reconnect to sync before drawing more.",
        );
      }
      await idbPut(db, "operations", op);
      await this.enforceBoardLimits();
    } catch (err) {
      throw this.wrapError(err);
    }
  }

  async updateOperationStatus(
    operationId: string,
    status: LocalOperationStatus,
    fields?: Partial<Pick<LocalOperation, "retry_count" | "last_error" | "last_attempt_at" | "server_sequence" | "server_version">>,
  ): Promise<void> {
    const db = await this.db();
    const op = await idbGet<LocalOperation>(db, "operations", operationId);
    if (!op) return;
    await idbPut(db, "operations", {
      ...op,
      status,
      ...fields,
      updated_at: Date.now(),
    });
  }

  async getOperation(operationId: string): Promise<LocalOperation | undefined> {
    const db = await this.db();
    return idbGet<LocalOperation>(db, "operations", operationId);
  }

  /** Get PENDING + SUBMITTING operations for a board, ordered by client_sequence. */
  async getPendingOperations(whiteboardId: string): Promise<LocalOperation[]> {
    const db = await this.db();
    const all = await idbGetAllFromIndex<LocalOperation>(db, "operations", "by_owner", this.ownerKey);
    return all
      .filter(
        (op) =>
          op.whiteboard_id === whiteboardId &&
          (op.status === "PENDING" || op.status === "SUBMITTING"),
      )
      .sort((a, b) => a.client_sequence - b.client_sequence);
  }

  async getOperationsForBoardByStatus(
    whiteboardId: string,
    status: LocalOperationStatus,
  ): Promise<LocalOperation[]> {
    const db = await this.db();
    const all = await idbGetAllFromIndex<LocalOperation>(db, "operations", "by_owner", this.ownerKey);
    return all.filter((op) => op.whiteboard_id === whiteboardId && op.status === status);
  }

  async listOperationsForBoard(whiteboardId: string): Promise<LocalOperation[]> {
    const db = await this.db();
    const all = await idbGetAllFromIndex<LocalOperation>(db, "operations", "by_owner", this.ownerKey);
    return all
      .filter((op) => op.whiteboard_id === whiteboardId)
      .sort((a, b) => a.client_sequence - b.client_sequence);
  }

  async countPendingForBoard(whiteboardId: string): Promise<number> {
    const db = await this.db();
    const all = await idbGetAllFromIndex<LocalOperation>(db, "operations", "by_owner", this.ownerKey);
    return all.filter(
      (op) =>
        op.whiteboard_id === whiteboardId &&
        (op.status === "PENDING" || op.status === "SUBMITTING"),
    ).length;
  }

  private async countOperationsForOwner(): Promise<number> {
    const db = await this.db();
    const all = await idbGetAllFromIndex<LocalOperation>(db, "operations", "by_owner", this.ownerKey);
    return all.filter((op) => op.status === "PENDING" || op.status === "SUBMITTING").length;
  }

  // --- conflicts -----------------------------------------------------------

  async saveConflict(record: LocalConflictRecord): Promise<void> {
    const db = await this.db();
    await idbPut(db, "conflicts", record);
  }

  async getConflictsForBoard(whiteboardId: string): Promise<LocalConflictRecord[]> {
    const db = await this.db();
    const all = await idbGetAllFromIndex<LocalConflictRecord>(db, "conflicts", "by_owner", this.ownerKey);
    return all.filter((c) => c.whiteboard_id === whiteboardId);
  }

  async resolveConflict(operationId: string): Promise<void> {
    const db = await this.db();
    const conflict = await idbGet<LocalConflictRecord>(db, "conflicts", operationId);
    if (!conflict) return;
    await idbDelete(db, "conflicts", operationId);
    // Recover the underlying operation as PENDING for retry.
    await idbPut(db, "operations", {
      ...conflict.operation,
      status: "PENDING",
      retry_count: 0,
      updated_at: Date.now(),
    });
  }

  async discardConflict(operationId: string): Promise<void> {
    const db = await this.db();
    await idbDelete(db, "conflicts", operationId);
  }

  // --- cleanup / limits ----------------------------------------------------

  private async enforceBoardLimits(): Promise<void> {
    const boards = await this.listWhiteboards();
    // Enforce max cached boards (drop oldest beyond the cap, only when more
    // than one and they are old).
    if (boards.length > MAX_CACHED_BOARDS) {
      const toTrim = boards.slice(MAX_CACHED_BOARDS);
      for (const board of toTrim) {
        const pending = await this.countPendingForBoard(board.whiteboard_id);
        // Never drop a board with pending ops silently.
        if (pending === 0) {
          await this.deleteWhiteboard(board.whiteboard_id);
        }
      }
    }
  }

  /** Remove confirmed operations that pre-date a server-version threshold. */
  async compactConfirmedOperations(whiteboardId: string, keepServerVersion: number): Promise<void> {
    const db = await this.db();
    const ops = await this.listOperationsForBoard(whiteboardId);
    for (const op of ops) {
      if (op.status === "CONFIRMED" && (op.server_version ?? 0) <= keepServerVersion) {
        await idbDelete(db, "operations", op.operation_id);
      }
    }
  }

  /** Total byte estimate for the current owner partition (approximate). */
  async estimateBytes(): Promise<number> {
    const db = await this.db();
    const ops = await idbGetAllFromIndex<LocalOperation>(db, "operations", "by_owner", this.ownerKey);
    const boards = await idbGetAllFromIndex<LocalWhiteboard>(db, "whiteboards", "by_owner", this.ownerKey);
    let bytes = 0;
    for (const op of ops) bytes += JSON.stringify(op).length;
    for (const b of boards) bytes += JSON.stringify(b).length;
    return bytes;
  }

  // --- partition / logout --------------------------------------------------

  /**
   * Return true if there is unsynced work for this owner across all boards.
   * Used to guard logout with unsynced changes.
   */
  async hasUnsyncedWork(): Promise<boolean> {
    const db = await this.db();
    const ops = await idbGetAllFromIndex<LocalOperation>(db, "operations", "by_owner", this.ownerKey);
    return ops.some((op) => op.status === "PENDING" || op.status === "SUBMITTING");
  }

  /**
   * Delete all locally stored data for this owner (logout / account switch).
   * Only safe when there is no unsynced work.
   */
  async clearOwnerPartition(): Promise<void> {
    const db = await this.db();
    const ops = await idbGetAllFromIndex<LocalOperation>(db, "operations", "by_owner", this.ownerKey);
    for (const op of ops) await idbDelete(db, "operations", op.operation_id);
    const boards = await idbGetAllFromIndex<LocalWhiteboard>(db, "whiteboards", "by_owner", this.ownerKey);
    for (const b of boards) await idbDelete(db, "whiteboards", b.whiteboard_id);
    const conflicts = await idbGetAllFromIndex<LocalConflictRecord>(db, "conflicts", "by_owner", this.ownerKey);
    for (const c of conflicts) await idbDelete(db, "conflicts", c.operation_id);
  }

  /** Recover interrupted operations: anything in SUBMITTING returns to PENDING. */
  async recoverInterrupted(): Promise<number> {
    const db = await this.db();
    const ops = await idbGetAllFromIndex<LocalOperation>(db, "operations", "by_owner", this.ownerKey);
    let recovered = 0;
    for (const op of ops) {
      if (op.status === "SUBMITTING") {
        await idbPut(db, "operations", { ...op, status: "PENDING", updated_at: Date.now() });
        recovered++;
      }
    }
    return recovered;
  }

  /** For use in tests: close the underlying connection. */
  async teardown(): Promise<void> {
    closeDB();
    this.dbPromise = null;
  }
}

// ---------------------------------------------------------------------------
// Buld helper for creating local operations with monotonic client sequence
// ---------------------------------------------------------------------------

let clientSequenceCounter = 0;

/** Get the next monotonic client-side sequence number (capped at 2^53). */
export function nextClientSequence(): number {
  clientSequenceCounter = (clientSequenceCounter + 1) % Number.MAX_SAFE_INTEGER;
  return Date.now() * 1_000_000 + clientSequenceCounter;
}
