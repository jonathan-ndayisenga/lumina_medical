{% load static %}// Ternah service worker: makes the app installable and shows a friendly
// offline page when the connection drops. Only that page is cached; patient
// data always comes live from the server.
const CACHE = 'ternah-offline-v1';
const OFFLINE_URL = '{% url "pwa_offline" %}';
const OFFLINE_ASSETS = [OFFLINE_URL, '{% static "pwa/icon-192.png" %}'];

self.addEventListener('install', function (event) {
    event.waitUntil(caches.open(CACHE).then(function (cache) { return cache.addAll(OFFLINE_ASSETS); }));
    self.skipWaiting();
});

self.addEventListener('activate', function (event) {
    event.waitUntil(caches.keys().then(function (keys) {
        return Promise.all(keys.filter(function (key) { return key !== CACHE; }).map(function (key) { return caches.delete(key); }));
    }));
    self.clients.claim();
});

self.addEventListener('fetch', function (event) {
    if (event.request.mode === 'navigate') {
        event.respondWith(fetch(event.request).catch(function () { return caches.match(OFFLINE_URL); }));
    } else if (OFFLINE_ASSETS.includes(new URL(event.request.url).pathname)) {
        event.respondWith(fetch(event.request).catch(function () { return caches.match(event.request); }));
    }
});
