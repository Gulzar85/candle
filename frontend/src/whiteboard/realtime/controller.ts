/**
 * RealtimeController — turns messages from the WebSocket transport into
 * collaboration behavior.
 *
 * Responsibilities:
 *   - Drive the connection state machine:
 *       connecting -> connected -> syncing -> ready        (happy path)
 *       ready       -> reconnecting -> connected -> syncing -> ready  (reconnect)
 *   - Keep an authoritative *confirmed* server version (from `connection.ready`,
 *     `operation.committed`, `sync.ops`).
 *   - Submit local operations optimistically over the transport, deduplicating
 *     confirmations so our own commits are never re-applied.
 *   - Apply remote / replayed operations exactly once (keyed by `operation_id`).
 *   - Relay presence events and expose a cursor-send API.
 *
 * The controller never touches the DOM or canvas. It talks to the engine only
 * through callbacks, so all logic is testable with a fake transport.
 */

import type {
  ClientMessage,
  OperationCommitted,
  OperationEnvelopeForWire,
  OperationRejectedBody,
  RealTimeConnectionState,
  ServerMessage,
} from "./types";
import type { RealtimeTransport } from "./transport";

export interface ControllerDeps {
  readonly transport: RealtimeTransport;
/**
 * Apply a confirmed remote (or replay) server operation to the local board.
 * `fromSelf` is true when the operation originated on this connection (already
 * applied optimistically) — the controller calls this only for OTHER sources.
 */
onApplyRemote(op: OperationEnvelopeForWire): void;
/** Surface a collaboration error (auth, stale version, etc.). */
onError?(code: string, message: string): void;
  /** Surface a collaboration error (auth, stale version, etc.). */
  onError?(code: string, message: string): void;
  /** Receive presence join/leave/cursor updates. */
  onPresence?(event: ServerMessage): void;
  /** Watch the derived connection state for the UI indicator. */
  onStateChange?(state: RealTimeConnectionState): void;
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export class RealtimeController {
  private _serverVersion = 0;
  private readonly applied = new Set<string>();
  private readonly pending = new Set<string>();
  /** How far behind the client thinks it is relative to the wire version. */
  private _syncing = false;
  private readonly clientId = lastSigil();

  constructor(private readonly deps: ControllerDeps) {
    deps.transport.onMessage((msg) => this.handleMessage(msg));
    deps.transport.onStateChange(() => this._emitState());
    this._emitState();
  }

  get serverVersion(): number {
    return this._serverVersion;
  }

  get state(): RealTimeConnectionState {
    const t = this.deps.transport.state;
    if (t === "connecting") return "connecting";
    if (t === "reconnecting") return "reconnecting";
    if (t === "closed") return "closed";
    // open
    return this._syncing ? "syncing" : "ready";
  }

  connect(): void {
    this.deps.transport.connect();
  }

  close(): void {
    this.deps.transport.close();
  }

  /**
   * Submit a locally-committed operation. Marks it applied (we already drew it)
   * and sends it over the wire optimistically.
   *
   * The base version is computed here, not taken from `op.base_version`: within
   * a single gesture the engine emits several sub-operations that must form a
   * version *chain* (each subsequent op's base version is the previous op's
   * expected resulting version). We track how many of our own operations are
   * still in flight (`pending`) against the confirmed server version, giving
   * `base = confirmed + inFlight` which also stays correct when the confirmed
   * version advances due to a partner's committed operations.
   */
  submitOperation(op: OperationEnvelopeForWire): void {
    this.applied.add(op.operation_id);
    const inFlight = this.pending.size;
    this.pending.add(op.operation_id);
    if (this.deps.transport.state !== "open") return; // transport will resync/send on reconnect
    this._send({
      type: "operation.submit",
      operation_id: op.operation_id,
      base_version: this._serverVersion + inFlight,
      operation: { operation_type: op.operation_type, payload: op.payload ?? {} },
      client_id: this.clientId,
    });
  }

  /**
   * Request a catch-up sync from the server. Used after connect/reconnect.
   * The client asks for everything it has not yet confirmed and applies the
   * returned `sync.ops` deterministically.
   */
  requestSync(): void {
    this._syncing = true;
    this._emitState();
    this._send({ type: "sync.request", version: this._serverVersion });
  }

  sendPresenceCursor(x: number, y: number): void {
    if (this.deps.transport.state !== "open") return;
    this._send({ type: "presence.cursor", x, y });
  }

  // ------------------------------------------------------------------ handler

  private handleMessage(msg: ServerMessage): void {
    switch (msg.type) {
      case "connection.ready": {
        this._setServerVersion(msg.version);
        // Catch up on anything this client hasn't applied yet.
        this.requestSync();
        break;
      }
      case "operation.committed":
        this.handleCommitted(msg);
        break;
      case "sync.ops": {
        this._setServerVersion(msg.version);
        let appliedAny = false;
        for (const op of msg.operations) {
          if (this.applied.has(op.operation_id)) continue;
          this.deps.onApplyRemote({
            operation_id: op.operation_id,
            operation_type: op.operation_type,
            base_version: op.base_version,
            payload: op.payload ?? {},
          });
          this.applied.add(op.operation_id);
          appliedAny = true;
        }
        // A sync is complete when the server version matches what we applied.
        this._syncing = false;
        this._emitState();
        break;
      }
      case "sync.required":
        this.requestSync();
        break;
      case "operation.rejected":
        this.handleRejected(msg as OperationRejectedBody);
        break;
      case "connection.error":
        this.deps.onError?.(msg.code, msg.message);
        break;
      case "presence.joined":
      case "presence.left":
      case "presence.update":
      case "pong":
        this.deps.onPresence?.(msg);
        break;
      default:
        break;
    }
  }

  private handleCommitted(msg: OperationCommitted): void {
    this._setServerVersion(msg.version);
    this.pending.delete(msg.operation_id);
    if (this.applied.has(msg.operation_id)) {
      return; // ours (or already applied) — nothing to render again.
    }
    this.deps.onApplyRemote({
      operation_id: msg.operation_id,
      operation_type: msg.operation.operation_type,
      base_version: 0,
      payload: msg.operation.payload ?? {},
    });
    this.applied.add(msg.operation_id);
  }

  private handleRejected(msg: OperationRejectedBody): void {
    if (msg.reason === "STALE_VERSION") {
      // Local optimism diverged from the server; recover by resyncing.
      this.pending.clear();
      this._setServerVersion(Math.max(this._serverVersion, msg.current_version ?? 0));
      this.requestSync();
    }
    this.deps.onError?.(msg.reason, msg.message);
  }

  private _send(message: ClientMessage): void {
    this.deps.transport.send(message);
  }

  private _setServerVersion(version: number): void {
    this._serverVersion = version;
  }

  private _emitState(): void {
    this.deps.onStateChange?.(this.state);
  }
}

function lastSigil(): string {
  // Short unique per-page marker so a client can correlate its own frames.
  const raw =
    globalThis.crypto && typeof globalThis.crypto.randomUUID === "function"
      ? globalThis.crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return raw.length > 8 ? raw.slice(-8) : raw;
}