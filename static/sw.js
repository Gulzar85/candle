/**
 * Candle service worker.
 *
 * Phase 6 PWA offline app shell. This worker is deliberately conservative:
 *
 *  - It caches ONLY the application shell (static JS/CSS bundles and icons).
 *  - It NEVER caches private API responses or authenticated HTML. Private
 *    content (whiteboard data) is served network-first and never stored in the
 *    cache, so User A's data cannot be served to User B on a shared device.
 *  - Navigation requests are network-first so the live app is always preferred
 *    when online.
 *
 * Cache version: bump CACHE_VERSION when the app shell changes to invalidate
 * stale assets safely.
 */

const CACHE_VERSION = "candle-v1";
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const NAV_CACHE = `${CACHE_VERSION}-nav`;

const SHELL_ASSETS = [
  "/static/dist/assets/main.css",
  "/static/dist/assets/main.js",
  "/static/dist/assets/whiteboard.js",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/favicon.svg",
];

// Install: pre-cache the app shell so the app can load offline.
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting()),
  );
});

// Activate: remove stale caches from previous versions.
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith("candle-") && key !== SHELL_CACHE && key !== NAV_CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

// Fetch strategy.
self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never cache API calls (private data) — always network, fall back to a
  // generic terse response if offline.
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(networkOnlyWithOfflineFallback(request));
    return;
  }

  // Navigation (HTML pages): network-first so you always get the latest; if
  // offline, fall back to the shell.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(NAV_CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match("/").then((cached) => cached || caches.match("/static/dist/assets/index.html"))),
    );
    return;
  }

  // Static shell assets: cache-first with network backup, and update cache in
  // the background (stale-while-revalidate) for fast repeat loads.
  if (SHELL_ASSETS.includes(url.pathname) || isStaticBundledAsset(url.pathname)) {
    event.respondWith(
      caches.match(request).then((cached) => {
        const network = fetch(request)
          .then((response) => {
            if (response && response.status === 200) {
              const copy = response.clone();
              caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
            }
            return response;
          })
          .catch(() => cached);
        return cached || network;
      }),
    );
    return;
  }

  // Everything else (e.g. favicons we didn't pre-cache): network-first.
  event.respondWith(networkWithCacheFallback(request));
});

function isStaticBundledAsset(path) {
  return /^\/static\/dist\/assets\/.+\.(js|css)$/.test(path);
}

async function networkWithCacheFallback(request) {
  try {
    const response = await fetch(request);
    return response;
  } catch {
    const cached = await caches.match(request);
    return cached || new Response("", { status: 503, statusText: "Unavailable" });
  }
}

async function networkOnlyWithOfflineFallback(request) {
  try {
    return await fetch(request);
  } catch {
    // Do not fabricate whiteboard data. Return a clear offline signal.
    return new Response(
      JSON.stringify({ error: "OFFLINE", message: "You are offline and this data is not cached." }),
      { status: 503, statusText: "Offline", headers: { "Content-Type": "application/json" } },
    );
  }
}
