/**
 * History panel + revert flow (Phase 9). DOM dependent (verified by build +
 * smoke test, consistent with input.ts/engine.ts/renderer.ts's own testing
 * convention).
 *
 * Fetches humanized entries from `WhiteboardRepository.loadHistory()`,
 * renders them into the slide-in panel (`templates/whiteboard/whiteboard.html`),
 * and wires "Revert" -> `repo.restore()`. Revert requires connectivity — it's
 * disabled while offline, since computing a historical snapshot requires
 * replaying the full server ledger, which an offline client doesn't have
 * cached (see `docs/architecture/whiteboard-history.md`).
 */

import type { HistoryEntry, WhiteboardRepository } from "./repository";

const PAGE_LIMIT = 50;

export interface HistoryPanelOptions {
  readonly repo: WhiteboardRepository;
  readonly getServerVersion: () => number;
  readonly isOnline: () => boolean;
  /** Called after a successful revert so the caller can trigger a full
   * state refetch — the same path used for a remote restore notification
   * (see index.ts / realtime/controller.ts). */
  readonly onReverted: () => void;
}

export class HistoryPanel {
  private oldestSequenceSeen: number | null = null;
  private lastFocused: HTMLElement | null = null;

  constructor(private readonly opts: HistoryPanelOptions) {}

  bind(): void {
    const trigger = document.getElementById("wb-open-history");
    const panel = document.getElementById("wb-history-panel");
    const backdrop = document.getElementById("wb-history-backdrop");
    const closeBtn = document.getElementById("wb-history-close");
    const loadMore = document.getElementById("wb-history-load-more");
    if (!trigger || !panel || !backdrop || !closeBtn) return;

    trigger.addEventListener("click", () => this.open());
    closeBtn.addEventListener("click", () => this.close());
    backdrop.addEventListener("click", () => this.close());
    panel.addEventListener("keydown", (e) => {
      if (e.key === "Escape") this.close();
    });
    loadMore?.addEventListener("click", () => void this.loadMore());
  }

  private open(): void {
    const panel = document.getElementById("wb-history-panel");
    const backdrop = document.getElementById("wb-history-backdrop");
    if (!panel || !backdrop) return;
    this.lastFocused = document.activeElement as HTMLElement | null;
    panel.classList.remove("hidden");
    backdrop.classList.remove("hidden");
    document.getElementById("wb-history-close")?.focus();
    document
      .getElementById("wb-history-offline-note")
      ?.classList.toggle("hidden", this.opts.isOnline());
    this.oldestSequenceSeen = null;
    const list = document.getElementById("wb-history-list");
    list?.querySelectorAll("[data-history-entry]").forEach((n) => n.remove());
    void this.loadPage();
  }

  private close(): void {
    document.getElementById("wb-history-panel")?.classList.add("hidden");
    document.getElementById("wb-history-backdrop")?.classList.add("hidden");
    this.lastFocused?.focus();
  }

  private async loadPage(): Promise<void> {
    const empty = document.getElementById("wb-history-empty");
    try {
      const res = await this.opts.repo.loadHistory(
        this.oldestSequenceSeen ?? undefined,
        PAGE_LIMIT,
      );
      this.renderEntries(res.entries);
      if (this.oldestSequenceSeen === null) {
        empty?.classList.toggle("hidden", res.entries.length > 0);
      }
      this.toggleLoadMore(res.entries.length === PAGE_LIMIT);
    } catch {
      // Leave the panel showing whatever's already there; offline/error is
      // surfaced elsewhere (the save/realtime status bar), not duplicated
      // here.
      this.toggleLoadMore(false);
    }
  }

  private loadMore(): Promise<void> {
    return this.loadPage();
  }

  private toggleLoadMore(show: boolean): void {
    document.getElementById("wb-history-load-more")?.classList.toggle("hidden", !show);
  }

  private renderEntries(entries: readonly HistoryEntry[]): void {
    const list = document.getElementById("wb-history-list");
    if (!list) return;
    // isOnline() is snapshotted per render, not re-checked live while the
    // panel stays open — revertTo() re-checks it before actually reverting,
    // so a stale-looking enabled button is a cosmetic edge case only, never
    // a functional gap.
    const online = this.opts.isOnline();
    for (const entry of entries) {
      this.oldestSequenceSeen =
        this.oldestSequenceSeen === null
          ? entry.sequence
          : Math.min(this.oldestSequenceSeen, entry.sequence);

      const row = document.createElement("div");
      row.dataset.historyEntry = "true";
      row.className = "flex items-start justify-between gap-3 px-4 py-3";

      const text = document.createElement("div");
      text.className = "min-w-0";
      const p = document.createElement("p");
      p.className = "text-sm text-foreground";
      p.textContent = entry.text;
      const time = document.createElement("p");
      time.className = "text-xs text-muted-foreground mt-0.5";
      time.textContent = formatRelativeTime(entry.created_at);
      text.append(p, time);
      row.appendChild(text);

      if (entry.can_restore) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className =
          "shrink-0 rounded-md px-2 py-1 text-xs font-medium text-primary hover:bg-accent " +
          "disabled:opacity-40 disabled:pointer-events-none transition-colors";
        btn.textContent = "Revert";
        btn.disabled = !online;
        btn.setAttribute("aria-label", `Revert to: ${entry.text}`);
        btn.addEventListener("click", () => void this.revertTo(entry));
        row.appendChild(btn);
      }
      list.appendChild(row);
    }
  }

  private async revertTo(entry: HistoryEntry): Promise<void> {
    if (!this.opts.isOnline()) return;
    const confirmed = confirm(
      "Revert the board to this point? Newer changes stay in history and can be reverted back to.",
    );
    if (!confirmed) return;
    try {
      await this.opts.repo.restore(
        crypto.randomUUID(),
        this.opts.getServerVersion(),
        entry.sequence,
      );
      this.close();
      this.opts.onReverted();
    } catch {
      alert("Could not revert — the board may have changed. Please try again.");
    }
  }
}

function formatRelativeTime(iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}
