/**
 * Whiteboard page bootstrap (separate Vite entry -> assets/whiteboard.js).
 *
 * Mounts the engine onto the full-viewport whiteboard page and wires the
 * toolbar, palette, status bar, presence avatar, and the offline-first
 * persistence layer (Phase 6).
 *
 * Offline-first flow:
 *   1. Open the durable local store (IndexedDB) and restore any cached board.
 *   2. Recover interrupted operations and detect pending work.
 *   3. Attempt to load the live server state; if offline, use the cache.
 *   4. Every local operation is durably persisted before submission.
 *   5. On reconnect, synchronize pending operations.
 */

import { Engine } from "./engine";
import type { StatusInfo } from "./engine";
import { WhiteboardRepository } from "./repository";
import type { SaveStatus, SyncState } from "./sync";
import { SyncManager } from "./sync";
import type { Stroke } from "./types";
import { RealtimeController } from "./realtime/controller";
import { WebSocketTransport } from "./realtime/transport";
import type {
  PresenceJoined,
  PresenceLeft,
  PresenceUpdate,
  RealTimeConnectionState,
} from "./realtime/types";
import type { OperationEnvelope } from "./repository";

let engine: Engine | null = null;
let sync: SyncManager | null = null;
let realtime: RealtimeController | null = null;
let transport: WebSocketTransport | null = null;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Whiteboard: missing #${id}`);
  return node as T;
}

function queryAll<T extends HTMLElement>(selector: string): T[] {
  return Array.from(document.querySelectorAll<T>(selector));
}

/** Build the WebSocket URL for a whiteboard from the current page origin. */
function buildWsUrl(publicId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/whiteboards/${publicId}/`;
}

// ---------------------------------------------------------------------------
// Toolbar / palette
// ---------------------------------------------------------------------------

function setActiveTool(tool: string): void {
  for (const btn of queryAll<HTMLButtonElement>("[data-wb-tool]")) {
    const active = btn.dataset.wbTool === tool;
    btn.setAttribute("aria-pressed", String(active));
    btn.classList.toggle("bg-accent", active);
    btn.classList.toggle("text-foreground", active);
  }
}

function setPalette(color: string): void {
  for (const swatch of queryAll<HTMLButtonElement>("[data-wb-color]")) {
    const active = swatch.dataset.wbColor === color;
    swatch.classList.toggle("ring-2", active);
    swatch.classList.toggle("ring-primary", active);
    swatch.classList.toggle("ring-offset-1", active);
  }
}

// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------

function renderStatus(info: StatusInfo): void {
  const zoom = el<HTMLSpanElement>("wb-zoom-label");
  const count = el<HTMLSpanElement>("wb-count");
  zoom.textContent = `${info.zoom}%`;
  count.textContent = `${info.strokeCount} stroke${info.strokeCount === 1 ? "" : "s"}`;
  setActiveTool(info.tool);
  updateDeleteButton();
}

const deleteBtn = () => el<HTMLButtonElement>("wb-delete");

function updateDeleteButton(): void {
  const btn = deleteBtn();
  const hasSelection = engine ? engine.selected !== null : false;
  if (hasSelection) {
    btn.removeAttribute("disabled");
    btn.title = "Delete selected (Del)";
    btn.setAttribute("aria-label", "Delete selection");
  } else {
    btn.setAttribute("disabled", "true");
    btn.title = "Select a stroke, then delete it (Del)";
    btn.setAttribute("aria-label", "Delete selected stroke");
  }
}

// ---------------------------------------------------------------------------
// Save / sync status
// ---------------------------------------------------------------------------

const SAVE_STATUS_CONFIG: Record<SaveStatus, { dot: string; label: string }> = {
  saved: { dot: "bg-success", label: "Saved" },
  saving: { dot: "bg-warning animate-pulse", label: "Saving..." },
  pending: { dot: "bg-muted-foreground", label: "Changes waiting to sync" },
  error: { dot: "bg-danger", label: "Save failed" },
  offline: { dot: "bg-warning", label: "Saved on this device · Waiting for connection" },
  syncing: { dot: "bg-warning animate-pulse", label: "Back online · Syncing changes…" },
};

function renderSaveStatus(state: SyncState): void {
  const dot = el<HTMLElement>("wb-save-dot");
  const label = el<HTMLElement>("wb-save-label");
  const retryBtn = el<HTMLButtonElement>("wb-retry-save");

  const config = SAVE_STATUS_CONFIG[state.saveStatus] ?? SAVE_STATUS_CONFIG.saved;

  // Update dot color (strip all existing bg- classes, apply new).
  dot.className = `wb-save-dot inline-block w-2 h-2 rounded-full ${config.dot}`;
  if (state.saveStatus === "saving" || state.saveStatus === "syncing") {
    dot.classList.add("animate-pulse");
  }

  // Never claim "Saved" while there are unsynced local changes.
  let text = config.label;
  if (state.pendingCount > 0 && state.saveStatus === "saved") {
    text = state.allSynced ? "All changes synced" : "Changes waiting to sync";
  }

  label.textContent = state.lastError && state.saveStatus === "error"
    ? state.lastError
    : text;

  // Show retry button only on error with pending ops.
  if (state.saveStatus === "error" && state.pendingCount > 0) {
    retryBtn.classList.remove("hidden");
  } else {
    retryBtn.classList.add("hidden");
  }

  // Conflict panel: visible when conflicting ops are quarantined.
  const conflictPanel = document.getElementById("wb-conflict-panel");
  const conflictCount = (state as { conflictCount?: number }).conflictCount ?? 0;
  if (conflictPanel) {
    if (conflictCount > 0) {
      conflictPanel.classList.remove("hidden");
    } else {
      conflictPanel.classList.add("hidden");
    }
  }
}

// ---------------------------------------------------------------------------
// Realtime collaboration status + presence
// ---------------------------------------------------------------------------

const REALTIME_STATE_CONFIG: Record<RealTimeConnectionState, { dot: string; label: string }> = {
  connecting: { dot: "bg-muted-foreground", label: "Connecting" },
  connected: { dot: "bg-accent", label: "Connected" },
  syncing: { dot: "bg-warning animate-pulse", label: "Syncing" },
  ready: { dot: "bg-success", label: "Live" },
  reconnecting: { dot: "bg-warning animate-pulse", label: "Reconnecting" },
  closed: { dot: "bg-danger", label: "Offline" },
};

function renderRealtimeState(state: RealTimeConnectionState): void {
  const dot = el<HTMLElement>("wb-realtime-dot");
  const label = el<HTMLElement>("wb-realtime-label");
  const config = REALTIME_STATE_CONFIG[state];
  dot.className = `inline-block w-2 h-2 rounded-full ${config.dot}`;
  label.textContent = state === "ready" ? `${config.label} · ${presenceUsers.size} online` : config.label;
}

const presenceUsers = new Set<string>();
const presenceCursors = new Map<string, HTMLDivElement>();

function buildCursorEl(initials: string): HTMLDivElement {
  const node = document.createElement("div");
  node.className =
    "pointer-events-none absolute z-20 w-6 h-6 -ml-3 -mt-3 rounded-full bg-accent " +
    "text-foreground text-[10px] font-semibold items-center justify-center border";
  node.style.display = "flex";
  node.textContent = initials || "?";
  node.setAttribute("aria-hidden", "true");
  return node;
}

function onPresence(message: PresenceJoined | PresenceLeft | PresenceUpdate): void {
  const stage = document.getElementById("wb-stage");
  if (!stage) return;
  if (message.type === "presence.joined") {
    presenceUsers.add(message.user.public_id);
    if (!presenceCursors.has(message.user.public_id)) {
      const cursor = buildCursorEl(message.user.display_name);
      stage.appendChild(cursor);
      presenceCursors.set(message.user.public_id, cursor);
    }
  } else if (message.type === "presence.left") {
    presenceUsers.delete(message.user.public_id);
    presenceCursors.get(message.user.public_id)?.remove();
    presenceCursors.delete(message.user.public_id);
  } else if (message.type === "presence.update") {
    // Cursor moves are frequent; only move the dot, never re-render the
    // realtime-state label (its "N online" count doesn't change here).
    const cursor = presenceCursors.get(message.user.public_id);
    if (cursor) {
      cursor.style.left = `${Math.min(100, Math.max(0, message.cursor.x * 100))}%`;
      cursor.style.top = `${Math.min(100, Math.max(0, message.cursor.y * 100))}%`;
    }
    return;
  }
  renderRealtimeState(realtime?.state ?? "connecting");
}

// ---------------------------------------------------------------------------
// Engine + persistence wiring
// ---------------------------------------------------------------------------

function mount(
  stage: HTMLElement,
  canvas: HTMLCanvasElement,
  creatorId?: string,
  apiBase?: string,
  publicId?: string,
  ownerKey?: string,
): {
  engine: Engine;
  sync: SyncManager;
  realtime: RealtimeController | null;
  transport: WebSocketTransport | null;
} {
  // Set up the offline-first persistence layer. The SyncManager is constructed
  // with the whiteboard + account identity so its IndexedDB usage is scoped.
  const repo = new WhiteboardRepository(apiBase || "/api/whiteboards/unknown");
  const syncMgr = new SyncManager(repo, publicId || undefined, ownerKey || "anonymous");

  // Submit a locally-committed operation.
  //
  // Phase 6: every operation is durably persisted by the SyncManager first.
  // When the WebSocket is live we ALSO submit over the wire for low-latency
  // delivery; the server's operation_id idempotency guarantees the result is
  // just one committed operation. When offline, only the durable queue runs.
  const submitLocal = (op: OperationEnvelope): void => {
    syncMgr.enqueue(op);
    if (realtime?.state === "ready") {
      realtime.submitOperation(op);
    }
  };

  // Create engine with persistence callback.
  const instance = new Engine(stage, canvas, {
    creatorId: creatorId || "you",
    initialColor: "#2563eb",
    initialWidth: 3,
    initialOpacity: 1,
    onServerOperation: submitLocal,
    onConfirmClear: () => confirm("Clear the whole board? This cannot be undone in this session."),
    onHelp: () => toggleShortcutHelp(true),
  });

  instance.setStatusHandler(renderStatus);

  // Wire save status updates.
  syncMgr.onStatusChange(renderSaveStatus);

  // Show a "needs attention" panel when operations are quarantined as
  // conflicts. Clicking Review re-syncs and (if the store is able) retries
  // from the durable queue; it never silently discards data.
  const conflictPanel = document.getElementById("wb-conflict-panel");
  const conflictReview = document.getElementById("wb-conflict-review");
  if (conflictPanel && conflictReview) {
    conflictReview.addEventListener("click", () => {
      syncMgr.retryPending();
      conflictPanel.classList.add("hidden");
    });
  }

  // Realtime collaboration over WebSocket (Phase 5).
  let controller: RealtimeController | null = null;
  let wsTransport: WebSocketTransport | null = null;
  if (publicId) {
    wsTransport = new WebSocketTransport(buildWsUrl(publicId));
    transport = wsTransport;
    controller = new RealtimeController({
      transport: wsTransport,
      onApplyRemote: (env) => instance.applyRemoteOperation(env),
      onError: (code, message) => {
        console.warn(`[whiteboard] realtime error ${code}: ${message}`);
        if (code === "AUTH_REQUIRED" || code === "FORBIDDEN" || code === "WHITEBOARD_NOT_FOUND") {
          wsTransport?.close();
        }
      },
      onPresence: (msg) => onPresence(msg as PresenceJoined | PresenceLeft | PresenceUpdate),
      onStateChange: renderRealtimeState,
    });
    controller.connect();

    // Feed transport connectivity into the sync engine's network monitor so it
    // knows when to flush the durable queue.
    wsTransport.onStateChange((state) => {
      const online = state === "open";
      const reconnecting = state === "reconnecting";
      syncMgr.onConnectionState(online, reconnecting);
    });

    // Broadcast local cursor while the pointer moves over the stage. Throttled
    // to avoid flooding the socket / re-rendering the DOM on every raw
    // pointermove (a cursor is ephemeral presence, not an operation).
    const CURSOR_SEND_INTERVAL_MS = 50;
    let lastCursorSend = 0;
    stage.addEventListener("pointermove", (e) => {
      const now = performance.now();
      if (now - lastCursorSend < CURSOR_SEND_INTERVAL_MS) return;
      lastCursorSend = now;
      const w = stage.clientWidth || 1;
      const h = stage.clientHeight || 1;
      controller?.sendPresenceCursor(e.clientX / w, e.clientY / h);
    });
  } else {
    renderRealtimeState("closed");
    syncMgr.onConnectionState(false, false);
  }

  instance.attach();

  return { engine: instance, sync: syncMgr, realtime: controller, transport: wsTransport };
}

function bindToolbar(): void {
  for (const btn of queryAll<HTMLButtonElement>("[data-wb-tool]")) {
    btn.addEventListener("click", () => { engine?.setTool(btn.dataset.wbTool as "pen" | "eraser" | "select"); });
  }
  el("wb-undo").addEventListener("click", () => engine?.undo());
  el("wb-redo").addEventListener("click", () => engine?.redo());
  // The engine's own onConfirmClear gate runs for both this button and the
  // Ctrl/Cmd+Shift+X shortcut, so the two can never drift out of sync.
  el("wb-clear").addEventListener("click", () => engine?.clear());
  el("wb-zoom-in").addEventListener("click", () => engine?.zoomBy(1.2));
  el("wb-zoom-out").addEventListener("click", () => engine?.zoomBy(1 / 1.2));
  el("wb-zoom-reset").addEventListener("click", () => engine?.resetZoom());
  el("wb-fit").addEventListener("click", () => engine?.fit());

  const del = el<HTMLButtonElement>("wb-delete");
  del.addEventListener("click", () => engine?.deleteSelection());

  bindTitleRename();
  bindStylePopover();
  bindShortcutHelp();

  // Retry button.
  el("wb-retry-save").addEventListener("click", () => sync?.retryPending());
}

// ---------------------------------------------------------------------------
// Stroke style popover (color / width / opacity) — one control reachable on
// every breakpoint, replacing the old always-hidden-on-mobile palette row.
// ---------------------------------------------------------------------------

function bindStylePopover(): void {
  const trigger = document.getElementById("wb-settings");
  const popover = document.getElementById("wb-style-popover");
  if (!trigger || !popover) return;

  let lastFocused: HTMLElement | null = null;

  const close = (): void => {
    if (popover.classList.contains("hidden")) return;
    popover.classList.add("hidden");
    trigger.setAttribute("aria-expanded", "false");
    lastFocused?.focus();
  };

  const open = (): void => {
    lastFocused = trigger as HTMLElement;
    popover.classList.remove("hidden");
    trigger.setAttribute("aria-expanded", "true");
    const first = popover.querySelector<HTMLElement>("[data-wb-color], input");
    first?.focus();
  };

  trigger.setAttribute("aria-expanded", "false");
  trigger.addEventListener("click", () => {
    if (popover.classList.contains("hidden")) open();
    else close();
  });

  document.addEventListener("click", (e) => {
    if (popover.classList.contains("hidden")) return;
    const target = e.target as Node;
    if (popover.contains(target) || trigger.contains(target)) return;
    close();
  });
  popover.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    }
  });

  for (const swatch of queryAll<HTMLButtonElement>("[data-wb-color]")) {
    swatch.addEventListener("click", () => {
      const color = swatch.dataset.wbColor;
      if (color && engine) {
        engine.setColor(color);
        setPalette(color);
      }
    });
  }

  const widthInput = document.getElementById("wb-width") as HTMLInputElement | null;
  const widthLabel = document.getElementById("wb-width-label");
  widthInput?.addEventListener("input", () => {
    const value = Number(widthInput.value);
    engine?.setWidth(value);
    if (widthLabel) widthLabel.textContent = `${value}px`;
  });

  const opacityInput = document.getElementById("wb-opacity") as HTMLInputElement | null;
  const opacityLabel = document.getElementById("wb-opacity-label");
  opacityInput?.addEventListener("input", () => {
    const percent = Number(opacityInput.value);
    engine?.setOpacity(percent / 100);
    if (opacityLabel) opacityLabel.textContent = `${percent}%`;
  });
}

// ---------------------------------------------------------------------------
// Keyboard-shortcut help dialog
// ---------------------------------------------------------------------------

function toggleShortcutHelp(show: boolean): void {
  const dialog = document.getElementById("wb-help-dialog");
  if (!dialog) return;
  if (show) {
    dialog.classList.remove("hidden");
    dialog.querySelector<HTMLElement>("[data-help-close]")?.focus();
  } else {
    dialog.classList.add("hidden");
    document.getElementById("wb-help")?.focus();
  }
}

function bindShortcutHelp(): void {
  const trigger = document.getElementById("wb-help");
  const dialog = document.getElementById("wb-help-dialog");
  if (!trigger || !dialog) return;
  trigger.addEventListener("click", () => toggleShortcutHelp(true));
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) toggleShortcutHelp(false);
  });
  dialog.querySelector("[data-help-close]")?.addEventListener("click", () => toggleShortcutHelp(false));
  dialog.addEventListener("keydown", (e) => {
    if (e.key === "Escape") toggleShortcutHelp(false);
  });
}

function bindTitleRename(): void {
  const title = el<HTMLButtonElement>("wb-title");
  let initial = (title.dataset.wbTitle || "").trim() || "Shared board";
  const apiBase = title.dataset.wbApiBase;
  title.textContent = initial;

  title.addEventListener("click", () => {
    if (!apiBase) return;
    const input = document.createElement("input");
    input.type = "text";
    input.value = initial;
    input.maxLength = 120;
    input.className = "input h-9 px-2 py-1 text-sm font-medium w-64";
    input.setAttribute("aria-label", "Board title");

    const wrap = title.parentElement;
    if (!wrap) return;
    wrap.replaceChild(input, title);
    input.focus();
    input.select();

    const done = (savedValue: string): void => {
      if (wrap.contains(input)) wrap.replaceChild(title, input);
      title.textContent = savedValue || initial;
    };

    const commit = async (): Promise<void> => {
      const value = input.value.trim();
      done(value || initial);
      if (!value || value === initial) return;
      try {
        const resp = await fetch(`${apiBase}/rename/`, {
          method: "PATCH",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: value }),
        });
        if (resp.ok) {
          const data = (await resp.json()) as { title?: string };
          initial = (data.title || value).trim();
          title.textContent = initial;
          title.dataset.wbTitle = initial;
        } else {
          title.textContent = initial;
        }
      } catch {
        // Leave the old title in place; the next load reconciles.
        title.textContent = initial;
      }
    };

    const onKey = async (e: KeyboardEvent): Promise<void> => {
      if (e.key === "Enter") {
        input.removeEventListener("keydown", onKey);
        await commit();
      } else if (e.key === "Escape") {
        input.removeEventListener("keydown", onKey);
        done(initial);
      }
    };
    input.addEventListener("keydown", onKey);
    input.addEventListener("blur", () => void commit());
  });
}

function teardown(): void {
  sync?.destroy();
  sync = null;
  realtime?.close();
  realtime = null;
  transport = null;
  engine?.destroy();
  engine = null;
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  const stage = document.getElementById("wb-stage");
  if (!stage) return;
  const canvas = el<HTMLCanvasElement>("wb-canvas");

  teardown();

  const apiBase = stage.dataset.wbApiBase;
  const publicId = stage.dataset.wbPublicId;
  const ownerKey = stage.dataset.wbAccount;
  const result = mount(stage, canvas, stage.dataset.wbUser, apiBase, publicId, ownerKey);
  engine = result.engine;
  sync = result.sync;
  realtime = result.realtime;
  transport = result.transport;

  bindToolbar();
  setPalette("#2563eb");
  engine.setStatusHandler(renderStatus);

  if (apiBase) {
    const localEngine = engine;
    const localSync = sync;

    // Phase 6: open the durable store, restore cached board + pending state,
    // then try to load the live server state.
    void (async () => {
      const cachedStrokes = await localSync.loadCachedWhiteboard();
      if (cachedStrokes && cachedStrokes.length > 0) {
        localEngine.loadServerState(cachedStrokes);
        renderSaveStatus({
          serverVersion: 0,
          localVersion: 0,
          pendingCount: localSync.pendingCount,
          saveStatus: localSync.saveStatus,
          allSynced: localSync.allSynced,
        });
      }

      // Offline reopen: reapply any operations still pending in the durable
      // queue on top of the cached snapshot, so strokes drawn during the
      // previous offline session are restored to the canvas immediately (they
      // are not part of the cached snapshot, which refreshes only on server
      // contact). Idempotent by operation_id.
      const pendingEnvelopes = await localSync.listPendingEnvelopes();
      for (const env of pendingEnvelopes) {
        localEngine.applyRemoteOperation(env);
      }

      const serverVersion = await localSync.initialize();
      localEngine.setServerVersion(serverVersion);

      // Fetch and render the full state from the server (network-first).
      fetch(`${apiBase}/`, {
        method: "GET",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((data: null | { version?: number; objects?: Array<{ object_type: string; object_id: string; points: Array<{ x: number; y: number }>; color: string; width: number; opacity: number; creator_id?: string }> }) => {
          if (data && data.objects && data.objects.length > 0) {
            const strokes: Stroke[] = data.objects.map((obj) => ({
              id: obj.object_id,
              points: obj.points.map((p) => ({ x: p.x, y: p.y })),
              style: { color: obj.color, width: obj.width, opacity: obj.opacity },
              creatorId: obj.creator_id,
            }));
            localEngine.loadServerState(strokes);
            // Cache the freshly loaded state for offline reopen.
            void localSync.cacheWhiteboard(data.version ?? serverVersion, strokes);
          }
        })
        .catch(() => {
          // Offline or failed — keep the cached board. The sync engine will
          // handle reconnection.
        });
    })();
  }
});
