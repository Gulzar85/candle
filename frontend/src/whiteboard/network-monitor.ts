/**
 * NetworkState — a small, testable connectivity monitor.
 *
 * The browser's `navigator.onLine` is only a hint. Real application
 * connectivity is determined from a combination of:
 *   - `navigator.onLine` changes
 *   - WebSocket state
 *   - HTTP request failures
 *
 * Consumers (the SyncEngine and UI) subscribe to changes.
 */

export type NetworkStatus =
  | "ONLINE"
  | "CONNECTING"
  | "OFFLINE"
  | "SYNCING"
  | "DEGRADED"
  | "ERROR";

export interface ConnectivityState {
  readonly status: NetworkStatus;
  readonly navigatorOnline: boolean;
  readonly lastChangeAt: number;
}

type Listener = (state: ConnectivityState) => void;

export class NetworkMonitor {
  private _status: NetworkStatus;
  private _navigatorOnline: boolean;
  private listeners = new Set<Listener>();

  constructor(initialOnline: boolean = typeof navigator !== "undefined" ? navigator.onLine : true) {
    this._navigatorOnline = initialOnline;
    this._status = initialOnline ? "ONLINE" : "OFFLINE";
  }

  get status(): NetworkStatus {
    return this._status;
  }

  get navigatorOnline(): boolean {
    return this._navigatorOnline;
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** Called when the browser reports an online/offline transition. */
  setNavigatorOnline(online: boolean): void {
    if (this._navigatorOnline === online) return;
    this._navigatorOnline = online;
    this._transition();
  }

  /**
   * Called by the transport when the WebSocket connects. When the socket is
   * open we are online.
   */
  markConnected(): void {
    this._transitionTo("ONLINE");
  }

  /** Called by the transport when the WebSocket disconnects / reconnects. */
  markDisconnected(reconnecting = false): void {
    this._transitionTo(reconnecting ? "CONNECTING" : "OFFLINE");
  }

  /** Called when an HTTP sync request succeeds. */
  markRequestSucceeded(): void {
    if (this._navigatorOnline && this._status === "OFFLINE") {
      this._transitionTo("ONLINE");
    }
  }

  /** Called when a sync request fails with a network error. */
  markRequestFailed(recovering = true): void {
    this._transitionTo(
      this._navigatorOnline ? (recovering ? "DEGRADED" : "ERROR") : "OFFLINE",
    );
  }

  /** Called while actively syncing (regardless of transport). */
  markSyncing(): void {
    this._transitionTo("SYNCING");
  }

  private _transition(): void {
    const next = this._navigatorOnline ? "ONLINE" : "OFFLINE";
    this._transitionTo(next);
  }

  private _transitionTo(status: NetworkStatus): void {
    if (this._status === status) return;
    this._status = status;
    this._emit();
  }

  private _emit(): void {
    const state: ConnectivityState = {
      status: this._status,
      navigatorOnline: this._navigatorOnline,
      lastChangeAt: Date.now(),
    };
    for (const fn of this.listeners) fn(state);
  }

  /** Attach DOM event listeners (only on the live page). Returns a detach fn. */
  attachPageListeners(): () => void {
    if (typeof window === "undefined") return () => {};
    const onOnline = (): void => this.setNavigatorOnline(true);
    const onOffline = (): void => this.setNavigatorOnline(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }
}
