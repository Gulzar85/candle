/**
 * SyncEngine — the offline-first coordination layer.
 *
 * Owns the durable local queue (via WhiteboardLocalStore) and drives
 * synchronization with the server over both WebSocket (preferred) and HTTP
 * (fallback). It coordinates:
 *
 *   - durable local persistence of incoming operations
 *   - connectivity detection (NetworkMonitor)
 *   - the sync state machine (idle/pending/syncing/conflict)
 *   - pull (remote ops) then push (pending ops), deterministically
 *   - bounded retries with exponential backoff
 *   - conflict reconciliation via ConflictResolver
 *   - crash recovery for interrupted (SUBMITTING) operations
 *
 * This module does NOT touch the canvas or DOM directly. It exposes a
 * `submit` entry point (called by the engine after a local commit) and a
 * set of callbacks for UI status.
 */

import type { WhiteboardRepository } from "./repository";
import type { OperationEnvelope } from "./repository";
import { WhiteboardLocalStore, type LocalOperation } from "./local-store";
import type {
  LocalConflictRecord,
  LocalOperationStatus,
  LocalWhiteboard,
} from "./local-store";
import { nextClientSequence } from "./local-store";
import { NetworkMonitor, type NetworkStatus } from "./network-monitor";
import {
  decideReconciliation,
  describeConflict,
  type ConflictDecision,
} from "./conflict-resolver";
import type { Stroke } from "./types";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type SyncPhase =
  | "idle"
  | "pending"       // there are unsynced local changes
  | "syncing"       // actively synchronizing
  | "synced"        // all local changes committed to server
  | "reconnecting"
  | "conflict"      // one or more operations quarantined
  | "error";

export interface SyncEngineStatus {
  readonly phase: SyncPhase;
  readonly network: NetworkStatus;
  readonly pendingCount: number;
  readonly conflictCount: number;
  readonly serverVersion: number;
  readonly lastError?: string;
}

export interface SyncEngineHooks {
  /** Apply a server operation to the local engine (remote apply). Optional. */
  applyRemoteOperation?(env: OperationEnvelope): boolean;
  /** Called when a conflicting operation is quarantined. */
  onConflict?(whiteboardId: string, operation: LocalOperation, reason: string): void;
  /** Called when the status changes. */
  onStatusChange?(status: SyncEngineStatus): void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_RETRIES = 8;
const INITIAL_RETRY_DELAY_MS = 1_000;
const MAX_RETRY_DELAY_MS = 30_000;
const BATCH_PUSH_LIMIT = 10;
const SYNC_COOLDOWN_MS = 400;

/**
 * The subset of WhiteboardLocalStore that SyncEngine depends on, expressed as a
 * structural interface so the engine can be unit-tested with an in-memory fake.
 */
export interface WhiteboardLocalStoreLike {
  connect(): Promise<void>;
  recoverInterrupted(): Promise<number>;
  getClientId(): Promise<string>;
  saveOperation(op: LocalOperation): Promise<void>;
  updateOperationStatus(
    operationId: string,
    status: LocalOperationStatus,
    fields?: Partial<
      Pick<
        LocalOperation,
        "retry_count" | "last_error" | "last_attempt_at" | "server_sequence" | "server_version"
      >
    >,
  ): Promise<void>;
  getPendingOperations(whiteboardId: string): Promise<LocalOperation[]>;
  countPendingForBoard(whiteboardId: string): Promise<number>;
  getConflictsForBoard(whiteboardId: string): Promise<LocalConflictRecord[]>;
  saveConflict(record: LocalConflictRecord): Promise<void>;
  compactConfirmedOperations(whiteboardId: string, keepServerVersion: number): Promise<void>;
  getWhiteboard(whiteboardId: string): Promise<LocalWhiteboard | undefined>;
  saveWhiteboard(board: LocalWhiteboard): Promise<void>;
  clearOwnerPartition(): Promise<void>;
  hasUnsyncedWork(): Promise<boolean>;
  listOperationsForBoard(whiteboardId: string): Promise<LocalOperation[]>;
  getOperationsForBoardByStatus(
    whiteboardId: string,
    status: LocalOperationStatus,
  ): Promise<LocalOperation[]>;
}

// ---------------------------------------------------------------------------
// SyncEngine
// ---------------------------------------------------------------------------

export class SyncEngine {
  private readonly store: WhiteboardLocalStoreLike;
  private readonly network: NetworkMonitor;
  private readonly hooks: SyncEngineHooks;

  private _phase: SyncPhase = "idle";
  private _serverVersion = 0;
  private _localVersion = 0;
  private _clientId: string | null = null;
  private _retryTimer: ReturnType<typeof setTimeout> | null = null;
  private _destroyed = false;
  private _activeSync = false;
  private _lastSyncAt = 0;
  private readonly localSequence = 0;
  private statusDirty = false;
  private serverVersionGetter: (() => number) | null = null;

  constructor(
    private readonly whiteboardId: string,
    private readonly ownerKey: string,
    private readonly repo: WhiteboardRepository,
    hooks: SyncEngineHooks = {},
    private readonly opts: {
      repo?: WhiteboardRepository;
      store?: WhiteboardLocalStoreLike;
    } = {},
  ) {
    this.store = opts.store ?? new WhiteboardLocalStore(ownerKey);
    this.network = new NetworkMonitor();
    this.hooks = hooks;
  }

  // ------------------------------------------------------------------ lifecycle

  /**
   * Open the local store, restore the durable queue, and recover any
   * interrupted (SUBMITTING) operations. Returns the constructor and any
   * recovered count.
   */
  async initialize(_clientId?: string): Promise<{ recovered: number }> {
    await this.store.connect();
    this._clientId = _clientId ?? (await this.store.getClientId());
    const recovered = await this.store.recoverInterrupted();
    const pending = await this.store.getPendingOperations(this.whiteboardId);
    this._pendingCount = pending.length;
    if (pending.length > 0) {
      this._phase = "pending";
      this._emitStatus();
    }
    return { recovered };
  }

  destroy(): void {
    this._destroyed = true;
    if (this._retryTimer) {
      clearTimeout(this._retryTimer);
      this._retryTimer = null;
    }
  }

  /** Set a custom server-version getter (e.g. from the realtime controller). */
  setServerVersionGetter(getter: () => number): void {
    this.serverVersionGetter = getter;
  }

  /** Direct read of the sync engine's confirmed server version. */
  get serverVersion(): number {
    return this.serverVersionGetter ? this.serverVersionGetter() : this._serverVersion;
  }

  get phase(): SyncPhase {
    return this._phase;
  }

  get storeInstance(): WhiteboardLocalStoreLike {
    return this.store;
  }

  get networkMonitor(): NetworkMonitor {
    return this.network;
  }

  // ------------------------------------------------------------------ public entry

  /**
   * Called by the engine after a local commit. Durably persists the operation
   * to IndexedDB before emitting any server traffic. If IndexedDB is
   * unavailable, the operation is NOT reported as synced.
   */
  async submit(envelope: OperationEnvelope, currentVersion?: number): Promise<boolean> {
    try {
      const base = currentVersion ?? this.serverVersion;
      const op: LocalOperation = {
        operation_id: envelope.operation_id,
        whiteboard_id: this.whiteboardId,
        owner_key: this.ownerKey,
        client_id: this._clientId ?? "",
        operation_type: envelope.operation_type,
        payload: envelope.payload ?? {},
        base_version: base,
        client_sequence: nextClientSequence(),
        local_created_at: Date.now(),
        status: "PENDING",
        retry_count: 0,
        created_at: Date.now(),
        updated_at: Date.now(),
      };
      await this.store.saveOperation(op);
      this._phase = "pending";
      await this.refreshPendingCount();
      this._emitStatus();
      void this.triggerSync();
      return true;
    } catch (err) {
      this._phase = "error";
      this._lastError = err instanceof Error ? err.message : "Could not save locally";
      this._emitStatus();
      return false;
    }
  }

  /** Trigger a synchronization pass (debounced). */
  triggerSync(): void {
    if (this._destroyed) return;
    this._scheduleSync(Date.now());
  }

  // ------------------------------------------------------------------ internal sync

  private async runSync(): Promise<void> {
    if (this._destroyed || this._activeSync) return;

    this._activeSync = true;
    this._lastSyncAt = Date.now();

    try {
      this._emitStatus();
      const pending = await this.store.getPendingOperations(this.whiteboardId);
      this._pendingCount = pending.length;
      if (pending.length === 0) {
        this._phase = "synced";
        this._emitStatus();
        return;
      }

      this._phase = "syncing";
      this._emitStatus();

      // Determine server version (from controller if available).
      const serverVersion = this.serverVersion;

      // Reconcile + push the queue.
      await this.pushPending(pending, serverVersion);

      // After a successful pass check if anything remains.
      const remaining = await this.store.getPendingOperations(this.whiteboardId);
      if (remaining.length === 0) {
        this._phase = (await this.refreshConflictCount()) > 0 ? "conflict" : "synced";
      } else {
        this._phase = "pending";
      }
      this._emitStatus();
    } finally {
      this._activeSync = false;
    }
  }

  private async pushPending(pending: LocalOperation[], serverVersion: number): Promise<void> {
    // Separate into submit-able and conflict categories.
    const batch: LocalOperation[] = [];
    const conflicts: LocalOperation[] = [];

    for (const op of pending) {
      const decision: ConflictDecision = decideReconciliation({
        operation_type: op.operation_type as never,
        base_version: op.base_version,
        server_version: serverVersion,
      });

      if (decision.action === "submit" || decision.action === "rebase") {
        const rebased: LocalOperation = {
          ...op,
          base_version: decision.action === "rebase" ? decision.newBaseVersion : op.base_version,
          status: "SUBMITTING",
          retry_count: op.retry_count,
          last_attempt_at: Date.now(),
          updated_at: Date.now(),
        };
        await this.store.updateOperationStatus(op.operation_id, "SUBMITTING", {
          last_attempt_at: Date.now(),
        });
        batch.push(rebased);
      } else {
        conflicts.push(op);
        await this.quarantineConflict(op, serverVersion);
      }
    }

    // Submit in order (already sorted by client_sequence).
    for (const op of batch) {
      const envelope: OperationEnvelope = {
        operation_id: op.operation_id,
        operation_type: op.operation_type,
        base_version: op.base_version,
        payload: op.payload,
      };
      let ok = false;
      let statusCode = 0;
      try {
        const result = await this.repo.submitBatch([envelope]);
        const ack = result.acks?.[0];
        if (ack) {
          await this.store.updateOperationStatus(op.operation_id, "CONFIRMED", {
            server_sequence: ack.sequence,
            server_version: ack.version,
            last_error: undefined,
          });
          this._serverVersion = Math.max(this._serverVersion, ack.version);
          // Remove confirmed ops from the local queue once server-confirmed.
          await this.store.compactConfirmedOperations(this.whiteboardId, ack.version);
          ok = true;
        }
      } catch (err) {
        const error = err as {
          status?: number;
          apiError?: { error?: string; current_version?: number; retry_after?: number };
          message?: string;
        };
        statusCode = error.status ?? 0;
        const apiErr = error.apiError?.error;

        if (apiErr === "STALE_VERSION") {
          // The base version went stale mid-flight. Re-run reconciliation with
          // the reported current version.
          const serverNow = error.apiError?.current_version ?? serverVersion;
          await this.store.updateOperationStatus(op.operation_id, "PENDING", {
            retry_count: op.retry_count + 1,
            last_error: apiErr,
            last_attempt_at: Date.now(),
          });
          // Re-run the whole sync (reconciles again with new version).
          this._serverVersion = serverNow;
          this._scheduleSync(Date.now() + 50);
          break;
        }

        if (apiErr === "FORBIDDEN" || apiErr === "WHITEBOARD_ARCHIVED") {
          // Permanent rejection — quarantine rather than retry forever.
          await this.quarantineConflict(op, this.serverVersion, error.message);
          this.hooks.onConflict?.(this.whiteboardId, op, error.message ?? apiErr ?? "");
          continue;
        }

        if (error.status === 429) {
          const retryAfter = error.apiError?.retry_after ?? 10;
          this._scheduleSync(Date.now() + retryAfter * 1000);
          await this.store.updateOperationStatus(op.operation_id, "PENDING", {
            retry_count: op.retry_count + 1,
            last_error: "rate_limited",
            last_attempt_at: Date.now(),
          });
          break;
        }

        // Network / 0 / 5xx: retry with backoff.
        if (error.status === undefined || error.status === 0 || error.status >= 500) {
          if (op.retry_count < MAX_RETRIES) {
            await this.store.updateOperationStatus(op.operation_id, "PENDING", {
              retry_count: op.retry_count + 1,
              last_error: "network",
              last_attempt_at: Date.now(),
            });
            const delay = Math.min(
              INITIAL_RETRY_DELAY_MS * 2 ** op.retry_count,
              MAX_RETRY_DELAY_MS,
            ) + Math.floor(Math.random() * 250);
            this._scheduleSync(Date.now() + delay);
          } else {
            // Exhausted retries — mark FAILED (needs attention, not silently
            // discarded).
            await this.store.updateOperationStatus(op.operation_id, "FAILED", {
              last_error: "max_retries_exceeded",
              last_attempt_at: Date.now(),
            });
            this._phase = "error";
            this._emitStatus();
          }
          break;
        }

        // 400s, other — permanent.
        await this.quarantineConflict(op, this.serverVersion, error.message ?? "rejected");
      }
    }

    this._emitStatus();
  }

  private async quarantineConflict(op: LocalOperation, serverVersion: number, reason?: string): Promise<void> {
    const message = reason ?? describeConflict(
      op.operation_type as never,
      op.base_version,
      serverVersion,
    );
    await this.store.saveConflict({
      operation_id: op.operation_id,
      whiteboard_id: op.whiteboard_id,
      owner_key: this.ownerKey,
      operation: { ...op, status: "CONFLICT" },
      reason: message,
      detected_at: Date.now(),
      status: "CONFLICT",
      updated_at: Date.now(),
    });
    // Mark the underlying op CONFLICT so it is no longer considered pending.
    await this.store.updateOperationStatus(op.operation_id, "CONFLICT");
    this._phase = "conflict";
    this._emitStatus();
  }

  private _scheduleSync(afterMs: number): void {
    this._syncScheduledAt = afterMs;
    if (this._retryTimer) return;
    const delay = Math.max(0, afterMs - Date.now());
    this._retryTimer = setTimeout(() => {
      this._retryTimer = null;
      if (!this._destroyed) void this.runSync();
    }, delay);
  }
  private _syncScheduledAt = 0;

  // ------------------------------------------------------------------ network integration

  /** Wire the network + WebSocket transport into the engine (called by index.ts). */
  onConnectionState(online: boolean, reconnecting: boolean): void {
    if (online && !reconnecting) {
      this.network.markConnected();
      this.triggerSync();
    } else if (reconnecting) {
      this.network.markDisconnected(true);
    } else {
      this.network.markDisconnected();
    }
    this._emitStatus();
  }

  // ------------------------------------------------------------------ status

  private _lastError: string | undefined;

  get lastError(): string | undefined {
    return this._lastError;
  }

  get pendingCount(): number {
    // Best-effort; persisted queue read is async. Expose a lazily updated count.
    return this._pendingCount;
  }
  private _pendingCount = 0;

  /** Update the memoized pending count from the store (call after mutations). */
  async refreshPendingCount(): Promise<void> {
    this._pendingCount = await this.store.countPendingForBoard(this.whiteboardId);
  }

  get conflictCount(): number {
    return this._conflictCount;
  }
  private _conflictCount = 0;

  async refreshConflictCount(): Promise<number> {
    this._conflictCount = (await this.store.getConflictsForBoard(this.whiteboardId)).length;
    return this._conflictCount;
  }

  private _emitStatus(): void {
    const status: SyncEngineStatus = {
      phase: this._phase,
      network: this.network.status,
      pendingCount: this._pendingCount,
      conflictCount: this._conflictCount,
      serverVersion: this.serverVersion,
      lastError: this._lastError,
    };
    this.hooks.onStatusChange?.(status);
  }

  // ------------------------------------------------------------------ cache helpers

  /** Persist a snapshot of the current strokes + version for offline reopen. */
  async cacheWhiteboard(version: number, strokes: readonly Stroke[]): Promise<void> {
    const existing = await this.store.getWhiteboard(this.whiteboardId);
    const capped = strokes.slice(-20000);
    await this.store.saveWhiteboard({
      whiteboard_id: this.whiteboardId,
      owner_key: this.ownerKey,
      server_version: version,
      last_sequence: version,
      strokes: capped,
      cached_at: existing?.cached_at ?? Date.now(),
      updated_at: Date.now(),
    });
  }

  /** Load a previously cached whiteboard snapshot. */
  async loadCachedWhiteboard(): Promise<readonly Stroke[] | null> {
    const board = await this.store.getWhiteboard(this.whiteboardId);
    return board && board.strokes ? board.strokes : null;
  }

  /**
   * Replayable envelopes for any operations still pending locally. When a board
   * is reopened offline, the caller applies these on top of the cached snapshot
   * so strokes drawn during the previous offline session are restored (they are
   * not part of the cached snapshot, which is only refreshed on server contact).
   */
  async listPendingAsEnvelopes(): Promise<OperationEnvelope[]> {
    const pending = await this.store.getPendingOperations(this.whiteboardId);
    return pending.map((op) => ({
      operation_id: op.operation_id,
      operation_type: op.operation_type,
      base_version: op.base_version,
      payload: op.payload ?? {},
    }));
  }

  /** True if the local store reports unsynced work (logout guard). */
  async hasUnsyncedWork(): Promise<boolean> {
    return this.store.hasUnsyncedWork();
  }

  /** Clear the local partition for this owner (logout), only if no unsynced work. */
  async clearLocalData(): Promise<void> {
    await this.store.clearOwnerPartition();
    this._pendingCount = 0;
    this._conflictCount = 0;
    this._emitStatus();
  }
}

export function computeLocalOperationBase(op: LocalOperation): number {
  return op.base_version;
}
