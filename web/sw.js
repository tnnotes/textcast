// TextCast offline support. The build stamps VERSION with a hash of the page, so
// a new page means a new worker: it saves the new files, and the next launch uses them.
const VERSION = "0.7.0-856877fc41";
const CACHE = "textcast-" + VERSION;
const FILES = ["./", "./index.html", "./manifest.webmanifest", "./icons/icon-192.png",
               "./icons/icon-512.png", "./icons/icon-maskable-512.png", "./icons/apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(FILES.map((f) => new Request(f, { cache: "reload" })))));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k.startsWith("textcast-") && k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

// Saved copy first (works offline, opens instantly); the network only for files not saved.
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;
  e.respondWith((async () => {
    const c = await caches.open(CACHE);
    const hit = await c.match(req, { ignoreSearch: true }) ||
                (req.mode === "navigate" ? await c.match("./index.html") : undefined);
    if (hit) return hit;
    return fetch(req);
  })());
});
