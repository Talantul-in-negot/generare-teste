// Keep these URLs in lock-step with the cache-busted URLs in index.html.
// CacheStorage matches query strings, so precaching `js/app.js` does not make
// `js/app.js?v=54` available on a fresh offline install.
const CACHE = "citim-impreuna-v72";
const ASSETS = [
  ".",
  "index.html",
  "css/style.css?v=47",
  "js/config.js?v=44",
  "js/auth.js?v=45",
  "js/scenes.js?v=44",
  "js/verses-1samuel.js?v=44",
  "js/verses-2samuel.js?v=44",
  "js/verses.js?v=44",
  "js/tracker.js?v=50",
  "js/app.js?v=55",
  "manifest.webmanifest",
  "icons/icon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  // Doar fișierele proprii (GET); apelurile către Supabase trec neatinse
  // (cache.put nu acceptă POST, iar statisticile nu trebuie servite din cache).
  if (event.request.method !== "GET" || !event.request.url.startsWith(self.location.origin)) {
    return;
  }
  // Rețea întâi, ca actualizările să apară imediat; recurge la cache doar offline.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      const existing = windows.find((client) => client.url.startsWith(self.location.origin));
      return existing ? existing.focus() : clients.openWindow("./");
    })
  );
});

// Chromium-based PWAs may wake the service worker through Periodic Background
// Sync. This is best-effort (the browser controls cadence); the page timer in
// app.js remains the fallback where this API is unavailable.
self.addEventListener("periodicsync", (event) => {
  if (event.tag !== "citim-daily-reminder") return;
  event.waitUntil((async () => {
    const enabled = await new Promise((resolve) => {
      try {
        const request = indexedDB.open("citim-impreuna-settings", 1);
        request.onerror = () => resolve(false);
        request.onupgradeneeded = () => request.result.createObjectStore("settings");
        request.onsuccess = () => {
          const db = request.result;
          const get = db.transaction("settings", "readonly").objectStore("settings").get("daily-reminder");
          get.onsuccess = () => { resolve(Boolean(get.result?.enabled)); db.close(); };
          get.onerror = () => { resolve(false); db.close(); };
        };
      } catch { resolve(false); }
    });
    if (enabled) {
      await self.registration.showNotification("E timpul pentru citirea de azi 📖", {
        body: "Deschide Citim împreună și citește următorul capitol.",
        icon: "icons/icon.svg",
        badge: "icons/icon.svg",
        tag: "citim-impreuna-daily-reminder",
        renotify: true,
      });
    }
  })());
});
