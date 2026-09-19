/**
 * SyncEngine — the offline-first coordination layer.
 *
 * Owns the durable local queue (via WhiteboardLocalStore) and drives
 * synchronization with the server over both WebSocket (preferred) and HTTP
 * (fallback). It coordinates:
 *
 *   - durable local persistence of incoming operations
 *   - connectivity detection (NetworkMonitor)
 *   - the sync state machine (idle/pending/syncing/synced/error)
 *   - pull (remote ops) then push (pending ops), deterministically
 *   - bounded retries with exponential backoff
 *   - crash recovery for interrupted (SUBMITTING) operations
 *
 * There is deliberately NO client-side conflict quarantine. Every operation
 * is submitted against the newest server version (anchored at pass start and
 * advanced by each ack), so a flush can never be judged stale for reasoning.
 * The server stays the single authority: operation_id idempotency makes
 * re-submits safe (`dup` acks), and a genuine permanent rejection marks the
 * operation FAILED — surfaced as a save error, never as a blocking "conflict"
 * panel. Operations quarantined by earlier buggy builds are healed into the
 * pending queue at boot (see requeueConflicts).
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
  resolveConflict(operationId: string): Promise<void>;
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

  /**
   * The engine's own tracked baseline, independent of any live getter.
   * Used as the fallback when a live-version getter has no value yet, and as
   * the advancing counter for version-chained submissions inside a sync pass.
   */
  get internalServerVersion(): number {
    return this._serverVersion;
  }

  /**
   * Seed the confirmed server version from an authoritative source (the
   * initial ``GET /whiteboards/<id>/`` load). Without this, the engine's
   * internal counter starts at 0 on every fresh page load, so the very first
   * locally-created operation is wrongly rebased down to version 0 before
   * submission — guaranteed to be rejected as stale by the server, which
   * this then has to recover from on a wasted round trip.
   */
  seedServerVersion(version: number): void {
    this._serverVersion = Math.max(this._serverVersion, version);
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
   *
   * ``pushHttp`` — when false, the operation is persisted for durability but
   * the HTTP sync pass is NOT scheduled. This is used while the WebSocket is
   * live: the op has already been sent over the wire, and the server ack
   * (via ``markOperationConfirmed``) removes it from the queue. The durable
   * record remains as crash recovery; a reconnect or a later sync flushes it
   * if the ack never arrives.
   */
  async submit(
    envelope: OperationEnvelope,
    currentVersion?: number,
    pushHttp: boolean = true,
  ): Promise<boolean> {
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
      if (pushHttp) {
        void this.triggerSync();
      }
      return true;
    } catch (err) {
      this._phase = "error";
      this._lastError = err instanceof Error ? err.message : "Could not save locally";
      this._emitStatus();
      return false;
    }
  }

  /**
   * Mark an operation as confirmed in the local store. Called by the
   * RealtimeController when the server acknowledges an operation over WebSocket,
   * so the sync engine's HTTP path does not redundantly re-submit it.
   */
  async markOperationConfirmed(operationId: string, serverVersion: number): Promise<void> {
    await this.store.updateOperationStatus(operationId, "CONFIRMED", {
      server_version: serverVersion,
    });
    this._serverVersion = Math.max(this._serverVersion, serverVersion);
    await this.refreshPendingCount();
    if (this._pendingCount === 0) {
      // Never resurrect "conflict" from historied quarantines on a normal write:
      // quarantined history is reconciled once at reopen (see requeueConflicts),
      // and live writing should not keep surface the panel for stale leftovers.
      this._phase = "synced";
    }
    this._emitStatus();
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
      const hadFailure = await this.pushPending(pending, serverVersion);

      // After a successful pass check if anything remains.
      const remaining = await this.store.getPendingOperations(this.whiteboardId);
      if (remaining.length === 0) {
        this._phase = hadFailure ? "error" : "synced";
      } else {
        this._phase = "pending";
      }
      this._emitStatus();
    } finally {
      this._activeSync = false;
    }
  }

  /**
   * Flush the pending queue. Every operation is submitted against the newest
   * known server version (anchored at pass start and advanced by each ack),
   * so the HTTP leg can never carry a stale base and no client-side
   * conflict/quarantine ever occurs.
   *
   * Returns true if any operation hit a permanent failure (surfaced in the
   * status bar as a save error, never as a blocking conflict panel).
   */
  private async pushPending(pending: LocalOperation[], serverVersion: number): Promise<boolean> {
    // Anchor the pass to the newest known version (the live value, when a
    // controller is attached). Acks below advance `_serverVersion`, so the
    // submission loop chains correctly for multi-op gestures even though the
    // live-version getter is read-only.
    this._serverVersion = Math.max(this._serverVersion, serverVersion);

    let hadFailure = false;

    for (const op of pending) {
      await this.store.updateOperationStatus(op.operation_id, "SUBMITTING", {
        last_attempt_at: Date.now(),
      });

      // Always submit against the anchored live version. Re-submits carry the
      // server's latest base; the server's operation_id idempotency makes
      // duplicate acks safe, and a genuine rejection marks the op FAILED
      // rather than quarantining it.
      const envelope: OperationEnvelope = {
        operation_id: op.operation_id,
        operation_type: op.operation_type,
        base_version: this._serverVersion,
        payload: op.payload,
      };

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
          await this.store.compactConfirmedOperations(this.whiteboardId, ack.version);
        }
      } catch (err) {
        const error = err as {
          status?: number;
          apiError?: { error?: string; current_version?: number; retry_after?: number };
          message?: string;
        };
        const apiErr = error.apiError?.error;

        if (apiErr === "STALE_VERSION") {
          // The base version went stale mid-flight (rare: partner committed
          // between anchor and this submit). Re-schedule against the reported
          // current version.
          const serverNow = error.apiError?.current_version ?? this._serverVersion;
          await this.store.updateOperationStatus(op.operation_id, "PENDING", {
            retry_count: op.retry_count + 1,
            last_error: apiErr,
            last_attempt_at: Date.now(),
          });
          this._serverVersion = serverNow;
          this._scheduleSync(Date.now() + 50);
          break;
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

        // Network / 0 / 5xx: retry with exponential backoff.
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
            await this.store.updateOperationStatus(op.operation_id, "FAILED", {
              last_error: "max_retries_exceeded",
              last_attempt_at: Date.now(),
            });
            hadFailure = true;
          }
          break;
        }

        // Permanent rejection (FORBIDDEN / 4xx / other). Mark the operation
        // FAILED so it does not silently vanish, but never quarantine — the
        // server is the sole authority and a fresh re-submit after the user
        // takes corrective action (e.g. re-opens the board) will resolve it.
        await this.store.updateOperationStatus(op.operation_id, "FAILED", {
          retry_count: op.retry_count + 1,
          last_error: apiErr ?? error.message ?? "rejected",
          last_attempt_at: Date.now(),
        });
        hadFailure = true;
      }
    }

    this._emitStatus();
    return hadFailure;
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

  /**
   * Requeue every quarantined conflict for this board as PENDING and attempt
   * a sync. This never discards data — a "reject" decision (e.g. a delete
   * whose target may have changed) can still bounce back into quarantine if
   * it is genuinely still unsafe, but a conflict caused by a since-corrected
   * stale version (the common case) will now go through cleanly.
   */
  async resolveAllConflicts(): Promise<void> {
    const conflicts = await this.store.getConflictsForBoard(this.whiteboardId);
    for (const conflict of conflicts) {
      await this.store.resolveConflict(conflict.operation_id);
    }
    await this.refreshConflictCount();
    await this.refreshPendingCount();
    this.triggerSync();
  }

  /**
   * Requeue quarantined history for idempotent reconciliation on reopen.
   * Old spurious conflicts (e.g. from buggy sessions where an operation was
   * both delivered over WebSocket and rejected over HTTP) correspond to
   * operations the server already applied, so the follow-up pass resolves them
   * as duplicates; genuinely unsafe ones bounce straight back into quarantine.
   * Runs once per initialize; safe offline (requeued ops simply re-enter the
   * pending queue and retry on reconnect).
   */
  async requeueConflicts(): Promise<number> {
    const conflicts = await this.store.getConflictsForBoard(this.whiteboardId);
    if (conflicts.length === 0) return 0;
    for (const conflict of conflicts) {
      await this.store.resolveConflict(conflict.operation_id);
    }
    await this.refreshConflictCount();
    await this.refreshPendingCount();
    return conflicts.length;
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
