/**
 * Realtime transport — the WebSocket connection management layer.
 *
 * Responsibilities:
 *   - Open and (with auto-reconnect) re-open a WebSocket to the whiteboard
 *     endpoint, applying an exponential backoff on failure.
 *   - Enforce a maximum frame size before parsing (memory safety).
 *   - JSON parse inbound frames and dispatch typed server messages.
 *   - Heartbeat: send `ping` periodically and treat a missed `pong` as a dead
 *     connection to reconnect.
 *   - Track a coarse connection state (`TransportState`) and expose change
 *     subscription for UI (connection indicator).
 *
 * The transport is deliberately protocol-agnostic about business semantics:
 * it only moves bytes + strings for the controller. `RealtimeController`
 * (controller.ts) turns the messages into collaboration behavior.
 *
 * Note on tests: this module runs in Node where `WebSocket` is unavailable, so
 * the concrete implementation accepts an injectable WebSocket constructor.
 */

import type { ServerMessage, TransportState } from "./types";

const MAX_FRAME_BYTES = 250_000;
const PONG_TIMEOUT_MS = 20_000;
const HEARTBEAT_INTERVAL_MS = 15_000;
const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 15_000;
const RECONNECT_MAX_ATTEMPTS = 8;

export interface RealtimeTransport {
  readonly state: TransportState;
  connect(): void;
  send(message: object): void;
  close(): void;
  onStateChange(fn: (state: TransportState) => void): void;
  onMessage(fn: (message: ServerMessage) => void): void;
}

export interface WebSocketTransportOptions {
  readonly protocols?: string | string[];
  /** Inject a WebSocket constructor (used in tests without a global). */
  readonly websocket?: typeof WebSocket;
  readonly heartbeatIntervalMs?: number;
  readonly pongTimeoutMs?: number;
  readonly reconnectBaseMs?: number;
  readonly reconnectMaxMs?: number;
  readonly reconnectMaxAttempts?: number;
}

export class WebSocketTransport implements RealtimeTransport {
  private _state: TransportState = "connecting";
  private stateFns = new Set<(state: TransportState) => void>();
  private messageFns = new Set<(message: ServerMessage) => void>();

  private socket: WebSocket | null = null;
  private readonly wsCtor: typeof WebSocket;
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private pongTimer: ReturnType<typeof setTimeout> | null = null;
  private lastPongAt = 0;
  private manuallyClosed = false;
  private readonly protocols: string | string[] | undefined;

  private readonly opts: Required<Omit<WebSocketTransportOptions, "protocols" | "websocket">>;

  constructor(
    private readonly url: string,
    options: WebSocketTransportOptions = {},
  ) {
    this.wsCtor = options.websocket ?? (globalThis as { WebSocket?: typeof WebSocket }).WebSocket!;
    this.protocols = options.protocols;
    this.opts = {
      heartbeatIntervalMs: options.heartbeatIntervalMs ?? HEARTBEAT_INTERVAL_MS,
      pongTimeoutMs: options.pongTimeoutMs ?? PONG_TIMEOUT_MS,
      reconnectBaseMs: options.reconnectBaseMs ?? RECONNECT_BASE_MS,
      reconnectMaxMs: options.reconnectMaxMs ?? RECONNECT_MAX_MS,
      reconnectMaxAttempts: options.reconnectMaxAttempts ?? RECONNECT_MAX_ATTEMPTS,
    };
  }

  get state(): TransportState {
    return this._state;
  }

  onStateChange(fn: (state: TransportState) => void): void {
    this.stateFns.add(fn);
  }

  onMessage(fn: (message: ServerMessage) => void): void {
    this.messageFns.add(fn);
  }

  connect(): void {
    this.manuallyClosed = false;
    this._openSocket();
  }

  close(): void {
    this.manuallyClosed = true;
    this._stopTimers();
    this._setState("closed");
    this.socket?.close(1000, "client close");
    this.socket = null;
  }

  send(message: object): void {
    if (this.socket?.readyState === this.wsCtor.OPEN) {
      this.socket.send(JSON.stringify(message));
    }
  }

  // ------------------------------------------------------------------ internals

  private _openSocket(): void {
    this._setState(this.reconnectAttempt > 0 ? "reconnecting" : "connecting");
    let socket: WebSocket;
    try {
      socket = new this.wsCtor(this.url, this.protocols);
    } catch {
      this._scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.reconnectAttempt = 0;
      this._setState("open");
      this._startHeartbeat();
    };

    socket.onmessage = (event: MessageEvent) => this._handleMessage(event);

    socket.onclose = () => {
      this._stopHeartbeat();
      if (this.manuallyClosed) return;
      this._scheduleReconnect();
    };

    socket.onerror = () => {
      // close will follow; nothing to do here beyond closing the socket.
      socket.close();
    };
  }

  private _handleMessage(event: MessageEvent): void {
    const data = typeof event.data === "string" ? event.data : "";
    if (data.length > MAX_FRAME_BYTES) return; // oversized — ignore.
    let parsed: unknown;
    try {
      parsed = JSON.parse(data);
    } catch {
      return; // not JSON — ignore.
    }
    const message = parsed as ServerMessage;
    this.lastPongAt = Date.now();
    if (message && typeof message === "object" && "type" in message) {
      for (const fn of this.messageFns) fn(message);
    }
  }

  private _startHeartbeat(): void {
    this.lastPongAt = Date.now();
    this.heartbeatTimer = setInterval(() => {
      const now = Date.now();
      if (now - this.lastPongAt > this.opts.pongTimeoutMs) {
        // Dead server: force reconnect.
        this.socket?.close(4000, "pong timeout");
        return;
      }
      this.send({ type: "ping" });
    }, this.opts.heartbeatIntervalMs);
  }

  private _stopHeartbeat(): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
    if (this.pongTimer) clearTimeout(this.pongTimer);
    this.pongTimer = null;
  }

  private _stopTimers(): void {
    this._stopHeartbeat();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private _scheduleReconnect(): void {
    if (this.manuallyClosed) return;
    if (this.reconnectAttempt >= this.opts.reconnectMaxAttempts) {
      this._setState("closed");
      return;
    }
    const delay = Math.min(
      this.opts.reconnectBaseMs * 2 ** this.reconnectAttempt,
      this.opts.reconnectMaxMs,
    );
    this.reconnectAttempt++;
    this._setState("reconnecting");
    this.reconnectTimer = setTimeout(() => this._openSocket(), delay);
  }

  private _setState(state: TransportState): void {
    if (this._state === state) return;
    this._state = state;
    for (const fn of this.stateFns) fn(state);
  }
}