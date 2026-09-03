# PWA Caching Strategy

**Date:** 2026-09-03
**Scope:** Phase 6 — the conservative service worker at `static/sw.js`, its
registration (`frontend/src/main.ts`), and the privacy rules that keep account
data out of caches.

---

## Principle: app shell only, never private data

The worker's job is to let the **application shell** load offline. It does **not**
cache whiteboard content. This is a deliberate privacy and correctness choice:

- Whiteboard operations are private, account-scoped, and versioned. Caching them
  cross-session could leak one partner's data to another on a shared device, and
  serving a stale HTTP response would contradict the server-authoritative model.
- Offline whiteboard data lives in **IndexedDB** (scoped by `owner_key`), not in
  an HTTP cache. The two are kept separate.

## Registration

In `main.ts`, `registerServiceWorker()` runs on `window.load`:

```
navigator.serviceWorker.register("/sw.js")
```

- Bounded scope: served at `/sw.js` from a Django view
  (`apps/core/views.service_worker`), scoping the worker to the site root.
- `reg.update()` runs in the background so the worker refreshes without
  interrupting a user.
- Registration failure is non-fatal (logged at debug level) — the app works as a
  normal site without a worker.

The manifest also declares a worker (`static/manifest.json` →
`service_worker: { src: "/sw.js", scope: "/", update_via_cache: "none" }`) so
installed PWAs update the shell reliably.

## What is cached

**App shell** (`SHELL_ASSETS`, pre-cached on `install`):

- `/static/dist/assets/main.css`, `main.js`, `whiteboard.js`
- `/static/manifest.json`
- icons (`icon-192.png`, `icon-512.png`, `favicon.svg`)

Stale-while-revalidate is used for bundled assets (`isStaticBundledAsset`):
serve the cached copy fast, refresh it in the background when online.

## Fetch strategy by request class

| Request | Strategy |
|---------|----------|
| `/api/*` | **Network-only**, offline → terse `503 OFFLINE` JSON (no fabricated data). |
| Navigation (`request.mode === "navigate"`) | **Network-first**, offline → cached shell. |
| Static shell / bundled assets | **Cache-first + background revalidate**. |
| Anything else | Network-first with cache fallback. |

### Navigation

```js
fetch(request)
  .then(cachePut)      // refresh app shell opportunistically
  .catch(() => caches.match("/"))
```

Online always gets the latest HTML; offline gets the cached shell.

### API

```js
try { return await fetch(request); }
catch { return 503 { error: "OFFLINE", message: "...not cached." } }
```

The client's sync engine already models offline properly (via IndexedDB), so the
worker's `503` is only a last-resort signal; it never returns stale private data.

## Cache versioning

`CACHE_VERSION = "candle-v1"` prefixes cache names (`<ver>-shell`,
`<ver>-nav`). On `activate`, stale `candle-*` caches are deleted. Bump the
version when the shell changes so old assets are purged cleanly.

## Activation

`self.clients.claim()` + `self.skipWaiting()` on install/activate ensure a fresh
worker takes control promptly after update.

## Why API responses are never cached

- **Correctness**: server is authoritative; a stale private response would be
  wrong, not merely slow.
- **Privacy**: on a shared device, User B's shell must not serve User A's board.
- **Consistency**: offline board state comes exclusively from the versioned
  IndexedDB partition, which is already account-scoped.

## Related

- `docs/architecture/offline-architecture.md` — how offline data is partitioned
  and reconciled (IndexedDB).
- `docs/architecture/indexeddb-schema.md` — the account-scoped store that actually
  holds offline content.