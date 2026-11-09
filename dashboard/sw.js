"use strict";
// Briefing's service worker: shows the bot's notifications (even with the app closed)
// and keeps the app's own files so it still opens when the bot is offline.

const CACHE = "briefing-v1";
const FILES = ["/", "/app.js", "/app.css", "/manifest.webmanifest", "/icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(FILES)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});

// The app's files: the newest from the bot, or the saved copy when it can't be reached.
// The data (/api/...) is never saved.
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  event.respondWith((async () => {
    try {
      const response = await fetch(event.request);
      if (response.ok) (await caches.open(CACHE)).put(event.request, response.clone());
      return response;
    } catch (error) {
      const saved = await caches.match(event.request, { ignoreSearch: true });
      if (saved) return saved;
      throw error;
    }
  })());
});

self.addEventListener("push", (event) => {
  let message = {};
  try { message = event.data ? event.data.json() : {}; } catch { message = { body: event.data ? event.data.text() : "" }; }
  event.waitUntil(self.registration.showNotification(message.title || "Briefing", {
    body: message.body || "",
    icon: "/icons/icon-192.png",
    badge: "/icons/badge-96.png",
    tag: message.tag === "test" ? "test" : undefined,
    data: { url: message.url || "/" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || "/", location.origin).href;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const open = windows.find((client) => new URL(client.url).origin === location.origin);
    if (open) {
      await open.focus();
      open.postMessage({ open: target });
    } else {
      await self.clients.openWindow(target);
    }
  })());
});
