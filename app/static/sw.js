// ModelDeploy PWA — service worker.
// Caches the app shell + static assets for offline use; APIs/SSE stay network-only.
// Registration is guarded by the host (base.html) with 'serviceWorker' in navigator,
// so this only activates over a secure context (HTTPS / localhost).

const CACHE = 'modeldeploy-v2';
const SHELL = ['/', '/targets', '/downloads', '/jobs', '/settings'];
const STATIC = [
  '/static/app.js',
  '/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-512-maskable.png',
  '/static/icons/apple-touch-icon.png',
];

// Clone a response and drop Cache-Control so the browser's no-store header on HTML
// pages doesn't stop us from persisting them in the SW cache for offline use.
function stash(res) {
  const headers = new Headers(res.headers);
  headers.delete('Cache-Control');
  return new Response(res.body, { status: res.status, statusText: res.statusText, headers });
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(STATIC))
      .then(() => self.skipWaiting())
      .catch(() => {}),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
    ).then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (request) => {
  if (request.method !== 'GET' || request.url.startsWith(self.location.origin + '/api/')) {
    return; // APIs + SSE: network-only, never cached.
  }

  const url = new URL(request.url);
  const isShell = SHELL.includes(url.pathname);

  return caches.match(request).then((cached) => {
    if (cached) {
      // Background refresh of the shell so updates land without a hard reload; keep
      // the cached copy for instant offline use meanwhile.
      if (isShell) {
        fetch(request, { cache: 'no-store' })
          .then((r) => { if (r && r.ok) return caches.open(CACHE).then((c) => c.put(request.url, stash(r))); })
          .catch(() => {});
      }
      return cached;
    }

    // Cache miss: fetch and store so the next visit works offline.
    return fetch(request)
      .then((r) => {
        if (!r || !r.ok) return r;
        return caches.open(CACHE).then((c) => c.put(request.url, stash(r))).then(() => r);
      })
      .catch(() => isShell ? caches.match('/') : Response.error());
  });
});
