/**
 * WhiteboardSyncManager — offline-first operation queue, retry, and save status.
 *
 * Phase 6 upgrade: the queue is now durable. Operations are written to
 * IndexedDB (via the SyncEngine + WhiteboardLocalStore) before they are
 * considered safely queued, so a browser crash does not lose pending work.
 *
 * This class preserves the pre-Phase-6 public surface (serverVersion,
 * localVersion, pendingCount, saveStatus, enqueue, retryPending) so the UI
 * wiring in index.ts keeps working, but internally it routes through the
 * SyncEngine for durable persistence, conflict handling, and network-aware
 * synchronization.
 */

import type {
  OperationEnvelope,
  SubmitResponse,
  WhiteboardRepository,
} from "./repository";
import { SyncEngine, type SyncPhase } from "./sync-engine";
import type { Stroke } from "./types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type SaveStatus = "saved" | "saving" | "pending" | "error" | "offline" | "syncing";

export interface SyncState {
  readonly serverVersion: number;
  readonly localVersion: number;
  readonly pendingCount: number;
  readonly saveStatus: SaveStatus;
  readonly lastError?: string;
  /** Number of quarantined conflicting operations awaiting attention. */
  readonly conflictCount?: number;
  /**
   * Whether the local changes are fully committed to the server. When offline
   * this is false and the UI must say "Saved on this device" not "Saved".
   */
  readonly allSynced: boolean;
}

type StatusListener = (state: SyncState) => void;

// ---------------------------------------------------------------------------
// SyncManager
// ---------------------------------------------------------------------------

export class SyncManager {
  private _serverVersion = 0;
  private _localVersion = 0;
  private _saveStatus: SaveStatus = "saved";
  private _lastError: string | undefined;
  private _listener: StatusListener = () => {};
  private syncEngine: SyncEngine | null = null;
  private _pendingCount = 0;
  private _conflictCount = 0;
  private _destroyed = false;
  private _ready = false;
  private _legacyQueue: OperationEnvelope[] = [];

  constructor(
    private readonly repo: WhiteboardRepository,
    private readonly whiteboardId?: string,
    private readonly ownerKey?: string,
  ) {}

  get serverVersion(): number {
    return this.syncEngine ? this.syncEngine.serverVersion : this._serverVersion;
  }

  get localVersion(): number {
    return this._localVersion;
  }

  get pendingCount(): number {
    return this._pendingCount;
  }

  get saveStatus(): SaveStatus {
    return this._saveStatus;
  }

  get allSynced(): boolean {
    return this._pendingCount === 0;
  }

  get engine(): SyncEngine | null {
    return this.syncEngine;
  }

  /** Subscribe to sync state changes. */
  onStatusChange(fn: StatusListener): void {
    this._listener = fn;
  }

  /**
   * Open the durable store and initialize the version baseline. Returns the
   * server version (or 0 on failure).
   */
  async initialize(): Promise<number> {
    if (!this.whiteboardId || !this.ownerKey) {
      // No scoping info — fall back to HTTP-only (no durable persistence).
      return this.initializeHttpOnly();
    }

    try {
      const syncEngine = new SyncEngine(this.whiteboardId, this.ownerKey, this.repo, {
        onStatusChange: (s) => this._handleEngineStatus(s),
      });
      this.syncEngine = syncEngine;
      const { recovered } = await syncEngine.initialize();
      this._ready = true;

      // Determine server baseline. Try the server; if offline, fall back to
      // the cached version.
      let serverVersion = 0;
      try {
        const state = await this.repo.loadState();
        serverVersion = state.version;
      } catch {
        const cached = await this.syncEngine.storeInstance.getWhiteboard(this.whiteboardId);
        serverVersion = cached?.server_version ?? 0;
        this._saveStatus = "offline";
      }

      this._serverVersion = serverVersion;
      this._localVersion = serverVersion;
      // The engine's own counter starts at 0; without seeding it here, the
      // first operation of a fresh session is wrongly rebased to version 0
      // and rejected as stale (see SyncEngine.seedServerVersion).
      this.syncEngine.seedServerVersion(serverVersion);
      // Reconcile any quarantined history from earlier sessions on reopen.
      // Idempotent by operation_id — op already applied on the server comes
      // back as a duplicate ack, only genuinely unsafe ones stay quarantined.
      const healed = await this.syncEngine.requeueConflicts();
      if (recovered > 0) {
        this._saveStatus = "pending";
      } else if (healed > 0) {
        this._saveStatus = "pending";
      }
      await this.syncEngine.refreshPendingCount();
      this._pendingCount = this.syncEngine.pendingCount;
      this._emit();

      // Kick off a sync if we have pending work.
      if (this._pendingCount > 0) {
        this.syncEngine.triggerSync();
      }
      return serverVersion;
    } catch {
      this._saveStatus = "offline";
      this._emit();
      return 0;
    }
  }

  private async initializeHttpOnly(): Promise<number> {
    try {
      const state = await this.repo.loadState();
      this._serverVersion = state.version;
      this._localVersion = state.version;
      this._setStatus("saved");
      return state.version;
    } catch {
      this._setStatus("error", "Could not load whiteboard. Check your connection.");
      return 0;
    }
  }

  /**
   * Enqueue an operation for durable persistence + submission. The operation
   * is written to IndexedDB before submission is attempted.
   *
   * ``pushHttp`` — when false, the durable queue write happens but the HTTP
   * sync pass is deferred; the operation is expected to be acknowledged over
   * the live WebSocket (see RealtimeController), which marks it confirmed.
   */
  enqueue(op: OperationEnvelope, pushHttp = true): void {
    this._localVersion++;
    if (this.syncEngine && this._ready) {
      // Pass the LIVE version (the realtime controller's, once wired) so the
      // durable record's base_version is never a stale snapshot, and only
      // schedule the HTTP leg when this operation is NOT already on its way
      // over the WebSocket.
      void this.syncEngine.submit(op, this.serverVersion, pushHttp).then((ok) => {
        if (!ok) this._setStatus("error");
      });
    } else {
      // No durable store — queue HTTP-only (legacy fallback).
      this._legacyQueue.push(op);
      this._pendingCount++;
      this._setStatus("pending");
      void this.submitHttpLegacy(op);
    }
  }

  /** Notify the sync engine that an operation was committed via WebSocket. */
  markOperationConfirmed(operationId: string, serverVersion: number): void {
    void this.syncEngine?.markOperationConfirmed(operationId, serverVersion);
  }

  /**
   * Feed the sync engine a live server-version source (e.g. the realtime
   * controller's confirmed version) so its reconciliation never judges fresh
   * operations against a stale baseline. When the getter returns ``null`` the
   * engine falls back to its own seeded/tracked baseline.
   */
  setServerVersionGetter(getter: () => number | null): void {
    const engine = this.syncEngine;
    if (!engine) return;
    engine.setServerVersionGetter(() => {
      const live = getter();
      return live !== null ? live : engine.internalServerVersion;
    });
  }

  /** Retry all pending operations (calls the sync engine). */
  retryPending(): void {
    if (this.syncEngine) {
      this._setStatus("pending");
      this.syncEngine.triggerSync();
    } else if (this._legacyQueue.length > 0) {
      this._setStatus("pending");
      for (const op of this._legacyQueue) void this.submitHttpLegacy(op);
    } else {
      this._setStatus("pending");
    }
  }

  /**
   * Requeue every quarantined conflict as pending and retry. Unlike
   * ``retryPending``, this also reaches operations already set aside as
   * CONFLICT — the only way those are ever resubmitted.
   */
  async resolveConflicts(): Promise<void> {
    if (!this.syncEngine) return;
    this._setStatus("pending");
    await this.syncEngine.resolveAllConflicts();
  }

  destroy(): void {
    this._destroyed = true;
    this.syncEngine?.destroy();
    this.syncEngine = null;
  }

  /** Wire connectivity (e.g. from the realtime controller's transport). */
  onConnectionState(online: boolean, reconnecting: boolean): void {
    this.syncEngine?.onConnectionState(online, reconnecting);
  }

  // ------------------------------------------------------------------ internals

  private _handleEngineStatus(status: {
    phase: SyncPhase;
    pendingCount: number;
    conflictCount: number;
  }): void {
    this._pendingCount = status.pendingCount;
    this._conflictCount = status.conflictCount;

    switch (status.phase) {
      case "syncing":
        this._saveStatus = "syncing";
        break;
      case "synced":
        this._saveStatus = this._pendingCount === 0 ? "saved" : "pending";
        break;
      case "pending":
        this._saveStatus = "pending";
        break;
      case "error":
        this._saveStatus = "error";
        break;
      default:
        break;
    }
    this._emit();
  }

  private _setStatus(status: SaveStatus, error?: string): void {
    this._saveStatus = status;
    this._lastError = error;
    this._emit();
  }

  private _emit(): void {
    this._listener({
      serverVersion: this.serverVersion,
      localVersion: this._localVersion,
      pendingCount: this._pendingCount,
      saveStatus: this._saveStatus,
      lastError: this._lastError,
      conflictCount: this._conflictCount,
      allSynced: this._pendingCount === 0,
    });
  }

  private async submitHttpLegacy(op: OperationEnvelope): Promise<void> {
    try {
      const result: SubmitResponse = await this.repo.submitBatch([op]);
      this._serverVersion = result.version;
      this._localVersion = result.version;
      this._legacyQueue = this._legacyQueue.filter((o) => o.operation_id !== op.operation_id);
      this._pendingCount = Math.max(0, this._pendingCount - 1);
      this._setStatus(this._legacyQueue.length === 0 ? "saved" : "pending");
    } catch {
      this._setStatus("error", "Could not save your changes.");
    }
  }

  // ------------------------------------------------------------------ cache helpers

  /** Cache the current board state so it can be reopened offline. */
  async cacheWhiteboard(version: number, strokes: readonly Stroke[]): Promise<void> {
    if (this.syncEngine) {
      await this.syncEngine.cacheWhiteboard(version, strokes);
    }
  }

  /** Load cached strokes for offline reopening (null if none). */
  async loadCachedWhiteboard(): Promise<readonly Stroke[] | null> {
    const cached = this.syncEngine ? await this.syncEngine.loadCachedWhiteboard() : null;
    return cached;
  }

  /**
   * Replayable envelopes for operations still pending locally. On an offline
   * reopen the caller reapplies these so offline-authored strokes are restored
   * on top of the cached snapshot.
   */
  async listPendingEnvelopes(): Promise<OperationEnvelope[]> {
    return this.syncEngine ? this.syncEngine.listPendingAsEnvelopes() : [];
  }

  /** True if there's unsynced work that prevents a safe logout. */
  async hasUnsyncedWork(): Promise<boolean> {
    if (this.syncEngine) return this.syncEngine.hasUnsyncedWork();
    return this._pendingCount > 0;
  }

  /** Clear the local partition (logout), safe only when no unsynced work. */
  async clearLocalData(): Promise<void> {
    if (this.syncEngine) await this.syncEngine.clearLocalData();
    this._pendingCount = 0;
    this._setStatus("saved");
  }
}
