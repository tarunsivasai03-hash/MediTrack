/*
 * MediTrack service worker.
 *
 * - Precaches the offline page and the public static shell (CSS, JS, icons).
 * - Static files: stale-while-revalidate (fast, and refreshed in the background).
 * - Page navigations: network first; if the network is down, show /offline.
 * - Everything else (API calls, forms, PDFs, uploads, authenticated pages) goes
 *   straight to the network and is never stored.
 *
 * Bump CACHE when static files change shape so old copies are dropped.
 */
const CACHE = "meditrack-static-v1";
const PRECACHE = [
    "/offline",
    "/static/css/style.css",
    "/static/js/main.js",
    "/static/img/icon-192.png",
];

self.addEventListener("install", (event) => {
    event.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const req = event.request;
    if (req.method !== "GET") return;
    const url = new URL(req.url);
    if (url.origin !== self.location.origin) return;

    if (url.pathname.startsWith("/static/")) {
        event.respondWith(
            caches.open(CACHE).then(async (cache) => {
                const cached = await cache.match(req);
                const refresh = fetch(req).then((res) => {
                    if (res.ok) cache.put(req, res.clone());
                    return res;
                }).catch(() => cached);
                return cached || refresh;
            })
        );
        return;
    }

    if (req.mode === "navigate") {
        event.respondWith(
            fetch(req).catch(() => caches.match("/offline"))
        );
    }
});
