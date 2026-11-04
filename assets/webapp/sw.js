// Service worker for the sessions app: caches only the app shell (network-first) + Web Push.
// /api/* and /f/* are never cached: they carry session data and temporary file links.
const CACHE = 'zidos-pager-shell-v5';
const SHELL = [
  '/app/', '/app/index.html', '/app/manifest.webmanifest',
  '/app/icon-192.png', '/app/icon-512.png', '/app/icon-maskable-512.png',
  '/app/clio.css', '/app/clio.js', '/app/menu.css', '/app/menu.js', '/app/voice.js',
  '/app/onest-latin.woff2', '/app/onest-cyrillic.woff2',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => Promise.allSettled(SHELL.map((url) => cache.add(url))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // data and files: network only, never cached
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/f/')) return;
  if (!SHELL.includes(url.pathname)) return;
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match('/app/')))
  );
});

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = {}; }
  const title = data.title || 'Claude Sessions';
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    tag: data.tag || undefined,
    data: data.data || {},
    icon: '/app/icon-192.png',
    badge: '/app/icon-192.png',
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || '/app/',
                         self.location.origin);
  // только своё приложение: чужой адрес в push не открываем
  const target = (url.origin === self.location.origin ? url : new URL('/app/', self.location.origin)).href;
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (client.url.startsWith(self.location.origin + '/app/') && 'focus' in client) {
          client.postMessage({ type: 'open', url: target });
          return client.focus().then((c) => (c && 'navigate' in c ? c.navigate(target) : c));
        }
      }
      return self.clients.openWindow ? self.clients.openWindow(target) : undefined;
    })
  );
});
