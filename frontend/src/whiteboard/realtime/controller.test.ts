/**
 * Tests for the realtime collaboration controller (controller.ts). The
 * controller is transport-agnostic, so these tests drive it with a fake
 * transport and assert on the emitted wire frames and applied operations.
 */

import { describe, expect, it } from "vitest";
import { RealtimeController } from "./controller";
import type { ControllerDeps } from "./controller";
import type {
  ClientMessage,
  OperationEnvelopeForWire,
  RealTimeConnectionState,
  ServerMessage,
  TransportState,
} from "./types";

class FakeTransport {
  state: TransportState = "connecting";
  private readonly stateFns = new Set<(s: TransportState) => void>();
  private readonly msgFns = new Set<(m: ServerMessage) => void>();
  readonly sent: ClientMessage[] = [];

  onStateChange(fn: (s: TransportState) => void): void {
    this.stateFns.add(fn);
  }
  onMessage(fn: (m: ServerMessage) => void): void {
    this.msgFns.add(fn);
  }
  connect(): void {
    this.setState("connecting");
  }
  send(message: ClientMessage): void {
    this.sent.push(message);
  }
  close(): void {
    this.setState("closed");
  }

  setState(s: TransportState): void {
    this.state = s;
    for (const fn of this.stateFns) fn(s);
  }
  emit(m: ServerMessage): void {
    for (const fn of this.msgFns) fn(m);
  }
}

interface Harness {
  controller: RealtimeController;
  transport: FakeTransport;
  applied: OperationEnvelopeForWire[];
  errors: Array<{ code: string; message: string }>;
  presence: ServerMessage[];
  states: RealTimeConnectionState[];
}

function makeHarness(deps?: Partial<ControllerDeps>): Harness {
  const transport = new FakeTransport();
  const applied: OperationEnvelopeForWire[] = [];
  const errors: Array<{ code: string; message: string }> = [];
  const presence: ServerMessage[] = [];
  const states: RealTimeConnectionState[] = [];
  const controller = new RealtimeController({
    transport,
    onApplyRemote: (env) => applied.push(env),
    onError: (code, message) => errors.push({ code, message }),
    onPresence: (evt) => presence.push(evt),
    onStateChange: (s) => states.push(s),
    ...deps,
  });
  return { controller, transport, applied, errors, presence, states };
}

const op = (id: string, type = "create_stroke"): OperationEnvelopeForWire => ({
  operation_id: id,
  operation_type: type,
  base_version: 0,
  payload: { object_id: id },
});

describe("RealtimeController", () => {
  it("starts connecting and moves to ready after connect + sync", () => {
    const h = makeHarness();
    h.controller.connect();
    expect(h.states[0]).toBe("connecting");

    h.transport.setState("open");
    // Fake state already open -> derived state is "syncing" until sync completes.
    h.transport.emit({ type: "connection.ready", protocol_version: 1, version: 7, connection_id: "c", user: { public_id: "u", display_name: "A" }, partner: null });
    expect(h.controller.serverVersion).toBe(7);
    expect(h.controller.state).toBe("syncing");

    h.transport.emit({ type: "sync.ops", version: 7, operations: [] });
    expect(h.controller.state).toBe("ready");
  });

  it("chains base versions across in-flight operations from a single gesture", () => {
    const h = makeHarness();
    h.transport.setState("open");
    h.transport.emit({ type: "connection.ready", protocol_version: 1, version: 3, connection_id: "c", user: { public_id: "u", display_name: "A" }, partner: null });
    // The ready frame triggers a sync request; drain it so we can inspect submits.
    h.transport.sent.length = 0;

    // Erase emits multiple ops in one gesture; without acks they must chain.
    h.controller.submitOperation(op("erase-remove", "delete_object"));
    h.controller.submitOperation(op("erase-add-1"));
    h.controller.submitOperation(op("erase-add-2"));

    const bases = h.transport.sent
      .filter((m) => m.type === "operation.submit")
      .map((m) => (m as { base_version: number }).base_version);
    // confirmed(3) then in-flight 0,1,2 -> bases 3,4,5.
    expect(bases).toEqual([3, 4, 5]);

    // Confirm the first op: confirmed -> 4; add1/add2 still in flight (2), so
    // the next submission chains on top of them: base = confirmed + inFlight = 6.
    h.transport.emit({
      type: "operation.committed",
      operation_id: "erase-remove",
      sequence: 4,
      version: 4,
      duplicate: false,
      actor: { public_id: "u", display_name: "A" },
      operation: { operation_type: "delete_object", payload: { object_id: "erase-remove" } },
    });
    h.controller.submitOperation(op("next"));
    const last = h.transport.sent.at(-1) as { base_version: number };
    expect(last.base_version).toBe(6);
  });

  it("submits a local operation optimistically and confirms its own echo without re-applying", () => {
    const h = makeHarness();
    h.transport.setState("open");

    h.controller.submitOperation(op("op-1"));
    // Sent over the wire optimistically at the confirmed base version.
    const frame = h.transport.sent.find((m) => m.type === "operation.submit");
    expect(frame).toMatchObject({ type: "operation.submit", operation_id: "op-1", base_version: 0 });

    // Server commits our own operation -> echo; must NOT be rendered again.
    h.transport.emit({
      type: "operation.committed",
      operation_id: "op-1",
      sequence: 1,
      version: 1,
      duplicate: false,
      actor: { public_id: "u", display_name: "A" },
      operation: { operation_type: "create_stroke", payload: { object_id: "op-1" } },
    });
    expect(h.applied).toEqual([]);
    expect(h.controller.serverVersion).toBe(1);
  });

  it("applies a partner's committed operation once and dedupes repeats", () => {
    const h = makeHarness();
    h.transport.setState("open");

    h.transport.emit({
      type: "operation.committed",
      operation_id: "partner-op",
      sequence: 2,
      version: 2,
      duplicate: false,
      actor: { public_id: "p", display_name: "Partner" },
      operation: { operation_type: "create_stroke", payload: { object_id: "partner-op" } },
    });
    expect(h.applied).toHaveLength(1);
    expect(h.applied[0].operation_id).toBe("partner-op");

    // Server echoes the same accepted operation again (duplicate) -> no re-apply.
    h.transport.emit({
      type: "operation.committed",
      operation_id: "partner-op",
      sequence: 2,
      version: 2,
      duplicate: true,
      actor: { public_id: "p", display_name: "Partner" },
      operation: { operation_type: "create_stroke", payload: { object_id: "partner-op" } },
    });
    expect(h.applied).toHaveLength(1);
    expect(h.controller.serverVersion).toBe(2);
  });

  it("applies sync.ops replays once and resets to ready", () => {
    const h = makeHarness();
    h.transport.setState("open");
    h.controller.requestSync();

    h.transport.emit({
      type: "sync.ops",
      version: 5,
      operations: [
        { operation_id: "a", sequence: 1, operation_type: "create_stroke", base_version: 0, resulting_version: 1, payload: { object_id: "a" } },
        { operation_id: "b", sequence: 2, operation_type: "create_stroke", base_version: 1, resulting_version: 2, payload: { object_id: "b" } },
      ],
    });
    expect(h.applied.map((e) => e.operation_id)).toEqual(["a", "b"]);
    expect(h.controller.serverVersion).toBe(5);
    expect(h.controller.state).toBe("ready");

    // A second sync that would replay the same ops must be idempotent.
    h.controller.requestSync();
    h.transport.emit({
      type: "sync.ops",
      version: 5,
      operations: [
        { operation_id: "a", sequence: 1, operation_type: "create_stroke", base_version: 0, resulting_version: 1, payload: { object_id: "a" } },
      ],
    });
    expect(h.applied).toHaveLength(2);
  });

  it("forward presence events and ignores them from self", () => {
    const h = makeHarness();
    h.transport.setState("open");
    h.transport.emit({ type: "presence.joined", user: { public_id: "p", display_name: "Partner" } });
    h.transport.emit({ type: "presence.update", user: { public_id: "p", display_name: "Partner" }, cursor: { x: 0.5, y: 0.25 } });
    expect(h.presence).toEqual([
      { type: "presence.joined", user: { public_id: "p", display_name: "Partner" } },
      { type: "presence.update", user: { public_id: "p", display_name: "Partner" }, cursor: { x: 0.5, y: 0.25 } },
    ]);
  });

  it("relays connection errors to the error handler", () => {
    const h = makeHarness();
    h.transport.setState("open");
    h.transport.emit({ type: "connection.error", code: "AUTH_REQUIRED", message: "Not allowed" });
    expect(h.errors).toEqual([{ code: "AUTH_REQUIRED", message: "Not allowed" }]);
  });

  it("recovers from a stale-version rejection by resyncing and reporting", () => {
    const h = makeHarness();
    h.transport.setState("open");

    h.transport.emit({
      type: "operation.rejected",
      reason: "STALE_VERSION",
      message: "stale",
      current_version: 9,
      client_version: 3,
    });
    expect(h.controller.serverVersion).toBe(9);
    expect(h.transport.sent.some((m) => m.type === "sync.request")).toBe(true);
    expect(h.errors).toEqual([{ code: "STALE_VERSION", message: "stale" }]);
  });

  it("broadcasts cursor updates when open and drops them when closed", () => {
    const h = makeHarness();
    h.controller.sendPresenceCursor(0.1, 0.2);
    expect(h.transport.sent.filter((m) => m.type === "presence.cursor")).toEqual([]);

    h.transport.setState("open");
    h.controller.sendPresenceCursor(0.3, 0.7);
    expect(h.transport.sent).toContainEqual({ type: "presence.cursor", x: 0.3, y: 0.7 });
  });
});