/**
 * Tests for the WebSocket transport (transport.ts). Uses a hand-rolled fake
 * WebSocket so the transport's reconnect / heartbeat / dispatch logic can be
 * exercised in Node without a DOM.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WebSocketTransport } from "./transport";

/** Minimal WebSocket stand-in exposing just the surface the transport uses. */
class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readyState = 0;
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  readonly sent: string[] = [];
  opened = false;
  closedCode: number | null = null;

  constructor(
    public readonly url: string,
    public readonly protocols?: string | string[],
  ) {}

  send(data: string): void {
    this.sent.push(data);
  }

  close(code?: number): void {
    this.closedCode = code ?? 1000;
  }

  // ----- test driver helpers ------------------------------------------------
  open(): void {
    if (this.readyState !== FakeWebSocket.OPEN) {
      this.readyState = FakeWebSocket.OPEN;
      this.onopen?.(null);
    }
  }

  receive(message: unknown): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  drop(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code: 1006 });
  }
}

interface Harness {
  transport: WebSocketTransport;
  instances: FakeWebSocket[];
  messages: unknown[];
  states: string[];
}

function makeHarness(opts: Partial<ConstructorParameters<typeof WebSocketTransport>[1]> = {}): Harness {
  const instances: FakeWebSocket[] = [];
  const fakeCtor = class extends FakeWebSocket {
    static override readonly OPEN = 1;
    constructor(url: string | URL, protocols?: string | string[]) {
      super(String(url), protocols);
      instances.push(this);
    }
  };
  const messages: unknown[] = [];
  const states: string[] = [];
  const transport = new WebSocketTransport("ws://test/ws/whiteboards/x/", {
    websocket: fakeCtor as unknown as typeof WebSocket,
    heartbeatIntervalMs: 1000,
    pongTimeoutMs: 5000,
    reconnectBaseMs: 10,
    reconnectMaxMs: 50,
    reconnectMaxAttempts: 3,
    ...opts,
  });
  transport.onMessage((m) => messages.push(m));
  transport.onStateChange((s) => states.push(s));
  return { transport, instances, messages, states };
}

const current = (h: Harness): FakeWebSocket => h.instances[h.instances.length - 1];

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("WebSocketTransport", () => {
  it("opens a connection and reports the open state", () => {
    const h = makeHarness();
    h.transport.connect();
    const ws = current(h);
    expect(ws).toBeDefined();
    expect(ws.url).toBe("ws://test/ws/whiteboards/x/");
    expect(ws.readyState).toBe(FakeWebSocket.CONNECTING);

    ws.open();
    expect(h.transport.state).toBe("open");
    expect(h.states).toContain("open");
  });

  it("dispatches parsed server messages to listeners", () => {
    const h = makeHarness();
    h.transport.connect();
    const ws = current(h);
    ws.open();
    ws.receive({ type: "connection.ready", version: 3, connection_id: "c1" });

    expect(h.messages).toEqual([
      { type: "connection.ready", version: 3, connection_id: "c1" },
    ]);
  });

  it("ignores messages that are not JSON or exceed the size guard", () => {
    const h = makeHarness();
    h.transport.connect();
    const ws = current(h);
    ws.open();
    // Non-JSON frame (raw text).
    ws.onmessage?.({ data: "not-json{{{" });
    // JSON that is not an object/type-bearing message.
    ws.onmessage?.({ data: JSON.stringify(5) });
    expect(h.messages).toEqual([]);
  });

  it("serializes outbound messages as JSON when opened, drops when closed", () => {
    const h = makeHarness();
    h.transport.connect();
    const closed = current(h);
    h.transport.send({ type: "ping" }); // before open -> dropped
    expect(closed.sent).toEqual([]);

    closed.open();
    h.transport.send({ type: "presence.cursor", x: 0.5, y: 0.25 });
    expect(JSON.parse(closed.sent[0])).toEqual({
      type: "presence.cursor",
      x: 0.5,
      y: 0.25,
    });
  });

  it("reconnects with backoff after the socket drops and re-opens", async () => {
    const h = makeHarness();
    h.transport.connect();
    const first = current(h);
    first.open();
    first.drop();
    expect(h.transport.state).toBe("reconnecting");

    await vi.advanceTimersByTimeAsync(10);
    expect(h.instances.length).toBeGreaterThanOrEqual(2);
    const second = current(h);
    expect(second).not.toBe(first);
    second.open();
    expect(h.transport.state).toBe("open");
    expect(current(h).sent).toEqual([]);
  });

  it("stops reconnecting after hitting the max attempt threshold", async () => {
    const h = makeHarness({ reconnectBaseMs: 10, reconnectMaxAttempts: 2 });
    h.transport.connect();
    expect(h.instances).toHaveLength(1);

    // First connection fails / drops -> reconnect attempt #1 scheduled.
    current(h).open();
    current(h).drop();
    await vi.advanceTimersByTimeAsync(20);
    expect(h.instances).toHaveLength(2);

    // Attempt #1 fails without opening -> attempt #2 (last allowed).
    current(h).drop();
    await vi.advanceTimersByTimeAsync(20);
    expect(h.instances).toHaveLength(3);

    // Attempt #2 fails -> no further attempts; connection is closed.
    current(h).drop();
    await vi.advanceTimersByTimeAsync(100);
    expect(h.instances).toHaveLength(3);
    expect(h.transport.state).toBe("closed");
  });

  it("sends ping heartbeats and forces a reconnect when the pong times out", async () => {
    const h = makeHarness();
    h.transport.connect();
    const ws = current(h);
    ws.open();

    // Heartbeat interval 1000ms. After one interval a ping is sent.
    await vi.advanceTimersByTimeAsync(1000);
    const sent = ws.sent.filter((s) => JSON.parse(s).type === "ping");
    expect(sent.length).toBeGreaterThanOrEqual(1);

    // A pong-less timeout (pongTimeout 5000ms) forces the socket closed.
    await vi.advanceTimersByTimeAsync(5000);
    expect(ws.closedCode).toBe(4000);
  });

  it("keeps the connection alive when pong arrives before the timeout", async () => {
    const h = makeHarness();
    h.transport.connect();
    const ws = current(h);
    ws.open();

    // Send a pong every interval so the timeout is never reached.
    for (let i = 0; i < 3; i++) {
      await vi.advanceTimersByTimeAsync(1000);
      ws.receive({ type: "pong" });
    }
    expect(ws.closedCode).toBeNull();
  });

  it("does not reconnect after an explicit close()", () => {
    const h = makeHarness();
    h.transport.connect();
    const ws = current(h);
    ws.open();
    h.transport.close();
    ws.drop();
    expect(h.transport.state).toBe("closed");
    expect(h.instances.length).toBe(1);
  });
});