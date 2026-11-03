// App shell from the cache, so the app opens at once even when the server can't be reached.
// API reads go to the network first; the last good copy answers when it fails.
const SHELL_CACHE = "openstride-shell-v7";  // bump when SHELL changes
const API_CACHE = "openstride-api-v1";
const SHELL = [
  "/",
  "/app.js",
  "/charts.js",
  "/style.css",
  "/manifest.webmanifest",
  "/render/coach_data.js",
  "/render/format.js",
  "/render/garmin.js",
  "/render/plan_overview.js",
  "/render/session_edit.js",
  "/render/ui.js",
  "/render/weeks.js",
  "/render/workout.js",
  "/render/workouts_list.js",
  "/fonts/barlow-latin-400-normal.woff2",
  "/fonts/barlow-latin-600-normal.woff2",
  "/fonts/barlow-semi-condensed-latin-600-normal.woff2",
  "/fonts/barlow-semi-condensed-latin-700-normal.woff2",
  "/fonts/jetbrains-mono-latin-400-normal.woff2",
  "/fonts/jetbrains-mono-latin-500-normal.woff2",
  "/icons/icon.svg",
  "/icons/favicon.svg",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/icon-maskable-512.png",
  "/icons/apple-touch-icon.png",
];
const NOT_CACHED = /^\/api\/backup|\.fit$/;
const NETWORK_TIMEOUT_MS = 8000;  // a sleeping laptop over Tailscale hangs rather than refuses

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name !== SHELL_CACHE && name !== API_CACHE) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

// Serves the cached copy and refreshes it for next time.
async function shellFirst(event, path) {
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(path);
  const fresh = fetch(path, { cache: "no-cache" }).then((response) => {
    if (response.ok && !response.redirected) event.waitUntil(cache.put(path, response.clone()));
    return response;
  });
  if (!cached) return fresh;
  event.waitUntil(fresh.catch(() => null));
  return cached;
}

async function remember(cache, request, response) {
  const headers = new Headers(response.headers);
  headers.set("X-OpenStride-Saved-At", new Date().toISOString());
  await cache.put(request, new Response(await response.blob(), { status: response.status, headers }));
}

// shortcut: every API read is kept with no size limit or expiry; add one if a device's cache ever grows too big.
async function apiFirst(event) {
  const cache = await caches.open(API_CACHE);
  try {
    const response = await fetch(event.request, { signal: AbortSignal.timeout(NETWORK_TIMEOUT_MS) });
    if (response.status >= 502) throw new Error("server unreachable");  // Tailscale answers 502 while the app is down
    if (response.ok) event.waitUntil(remember(cache, event.request, response.clone()));
    return response;
  } catch {
    const cached = await cache.match(event.request);
    return cached ?? new Response(JSON.stringify({ detail: "Offline: this page isn't saved on this device yet." }), {
      status: 503,
      headers: { "Content-Type": "application/json", "X-OpenStride-Offline": "1" },
    });
  }
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) {
    if (!NOT_CACHED.test(url.pathname)) event.respondWith(apiFirst(event));
  } else if (SHELL.includes(url.pathname)) {
    event.respondWith(shellFirst(event, url.pathname));
  }
});
