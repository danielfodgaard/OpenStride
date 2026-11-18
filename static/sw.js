// OpenStride no longer works offline. This file exists only to retire the service worker that earlier
// versions installed: a device that still has one fetches this, drops its caches and reloads without it.
// It can be deleted once every device has opened the app once.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) await caches.delete(name);
    await self.registration.unregister();
    for (const client of await self.clients.matchAll({ type: "window" })) client.navigate(client.url);
  })());
});
