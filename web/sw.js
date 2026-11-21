/* Coukab service worker — caches the app shell so the page opens instantly.
   API and camera images are always network-only. */
"use strict";

const VERSION = "coukab-v9";
const SHELL = ["/", "/static/style.css", "/static/app.js", "/manifest.webmanifest", "/icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (!SHELL.includes(url.pathname)) return; // /api/*, /moments/*: network only

  e.respondWith((async () => {
    const cache = await caches.open(VERSION);
    try {
      const response = await fetch(e.request, { cache: "no-cache" });
      if (response.ok) await cache.put(e.request, response.clone());
      return response;
    } catch (error) {
      const cached = await cache.match(e.request);
      if (cached) return cached;
      throw error;
    }
  })());
});
