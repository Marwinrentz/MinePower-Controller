/* MinePower Service Worker
 *
 * Zweck: Die Webapp startet auf dem Handy sofort (auch bei schlechtem WLAN)
 * und zeigt bei nicht erreichbarem Server die gewohnte Oberfläche statt einer
 * Browser-Fehlerseite. Live-Daten kommen weiter nur vom Server.
 *
 * Regeln:
 *  - /api/** wird nie zwischengespeichert (Messwerte, Schalten, Anmeldung).
 *  - Seitenaufrufe: erst Netz, bei Fehler die zuletzt geladene index.html.
 *  - /assets/** trägt einen Hash im Namen und ändert sich nie: aus dem Cache.
 *  - Übrige statische Dateien (Icons, Manifest): Cache, im Hintergrund erneuert.
 */
const CACHE = "minepower-v1";
const SHELL = ["/", "/manifest.webmanifest", "/icon-192.png", "/logo.svg", "/theme-init.js"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => undefined).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

function put(request, response) {
  if (response && response.ok && response.type === "basic") {
    const copy = response.clone();
    caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => undefined);
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/sw.js") return;

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => put("/", res))
        .catch(() => caches.match("/").then((r) => r || Response.error())),
    );
    return;
  }

  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => put(req, res))));
    return;
  }

  event.respondWith(
    caches.match(req).then((hit) => {
      const net = fetch(req).then((res) => put(req, res)).catch(() => hit);
      return hit || net;
    }),
  );
});
