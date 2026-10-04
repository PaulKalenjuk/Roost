/* Minimal service worker so Chrome treats Roost as an installable PWA
   ("Install app" → real home-screen icon from the manifest, standalone, no
   browser badge) instead of a plain shortcut.

   It deliberately caches NOTHING — Roost is private and authenticated, and the
   published app isn't meant to work offline. Its only job is to exist and to
   register a fetch handler, which is what Chrome's installability asks for. */
self.addEventListener('install', () => {
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim())
})

self.addEventListener('fetch', () => {
  /* no-op passthrough; presence of a fetch listener is the installability signal */
})
