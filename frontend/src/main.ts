import Alpine from "alpinejs";
import { createIcons, icons } from "lucide";
import "htmx.org";
import "@/main.css";

window.Alpine = Alpine;
Alpine.start();

export const CandleApp = {
  initLucide(): void {
    // Pass the full icon (and alias) map: lucide's createIcons defaults to an
    // empty registry, so without this it cannot resolve any `data-lucide`
    // name and renders no icons.
    createIcons({ icons });
  },
};

document.addEventListener("DOMContentLoaded", () => {
  CandleApp.initLucide();
  registerServiceWorker();
  wireMessageDismiss();
  wireAutoSubmit();
  wireConfirm();
});

// Lucide icons in HTMX-swapped fragments need re-creating -- the library only
// resolves `data-lucide` names for elements present at `createIcons()` time.
document.addEventListener("htmx:afterSwap", () => {
  CandleApp.initLucide();
});

/**
 * `<input data-autosubmit>` submits its form on change. Declarative
 * equivalent of `onchange="this.closest('form').requestSubmit()"` --
 * production's script-src has no 'unsafe-inline', which (unlike a nonce,
 * which only covers <script> elements) blocks inline event handler
 * attributes outright, so that inline form only ever worked in dev.
 */
function wireAutoSubmit(): void {
  document.addEventListener("change", (e) => {
    const target = (e.target as HTMLElement | null)?.closest<HTMLElement>("[data-autosubmit]");
    target?.closest("form")?.requestSubmit();
  });
}

/**
 * `<button data-confirm="...">` shows a confirm() dialog before its
 * (native, non-HTMX) form submits, cancelling the submit on Cancel.
 * Declarative equivalent of `onclick="return confirm(...)"`, blocked in
 * production for the same inline-attribute reason as wireAutoSubmit.
 */
function wireConfirm(): void {
  document.addEventListener("click", (e) => {
    const target = (e.target as HTMLElement | null)?.closest<HTMLElement>("[data-confirm]");
    if (target && !window.confirm(target.dataset.confirm || "Are you sure?")) {
      e.preventDefault();
    }
  });
}

/**
 * Dismissible Django messages framework toasts (see components/_messages.html).
 * Messages that carry a URL (from messages.success's extra_tags or a link-like
 * format) fall back to the legacy non-dismissible alert block.
 */
function wireMessageDismiss(): void {
  document
    .querySelectorAll<HTMLButtonElement>("[data-message-dismiss]")
    .forEach((btn) => {
      btn.addEventListener("click", () => {
        btn.closest("[data-message]")?.remove();
      });
    });
}

/**
 * Register the service worker at root scope for the PWA offline app shell.
 * Registration is progressive: it never blocks the page and is skipped where
 * unsupported (non-secure context, etc.).
 */
function registerServiceWorker(): void {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js")
      .then((reg) => {
        // Surface SW updates in the background, but never interrupt a user.
        reg.update();
      })
      .catch((err) => {
        // Non-fatal: the app works fine as a normal website without a SW.
        console.debug("[candle] service worker registration skipped", err);
      });
  });
}
