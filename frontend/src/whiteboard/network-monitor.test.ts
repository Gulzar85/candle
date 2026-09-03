/**
 * Unit tests for the NetworkMonitor.
 */

import { describe, it, expect } from "vitest";
import { NetworkMonitor } from "./network-monitor";

describe("NetworkMonitor", () => {
  it("starts ONLINE when navigator reports online", () => {
    const m = new NetworkMonitor(true);
    expect(m.status).toBe("ONLINE");
  });

  it("starts OFFLINE when navigator reports offline", () => {
    const m = new NetworkMonitor(false);
    expect(m.status).toBe("OFFLINE");
  });

  it("transitions to OFFLINE when the browser goes offline", () => {
    const m = new NetworkMonitor(true);
    m.setNavigatorOnline(false);
    expect(m.status).toBe("OFFLINE");
  });

  it("returns ONLINE when the transport connects", () => {
    const m = new NetworkMonitor(false);
    m.markConnected();
    expect(m.status).toBe("ONLINE");
  });

  it("emits a state to subscribers", () => {
    const m = new NetworkMonitor(true);
    const seen: string[] = [];
    m.subscribe((s) => seen.push(s.status));
    m.setNavigatorOnline(false);
    expect(seen).toContain("OFFLINE");
  });

  it("marks request failure as OFFLINE when navigator is offline", () => {
    const m = new NetworkMonitor(false);
    m.markRequestFailed();
    expect(m.status).toBe("OFFLINE");
  });
});