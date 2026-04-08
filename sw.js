const CACHE = 'gestor-v1';
const OFFLINE_ASSETS = ['/'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(OFFLINE_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});

self.addEventListener('push', e => {
  const data = e.data ? e.data.json() : {title: 'Gestor de Propiedades', body: 'Tienes alertas pendientes'};
  e.waitUntil(
    self.registration.showNotification(data.title || 'Gestor de Propiedades', {
      body: data.body || '',
      icon: 'https://cdn.jsdelivr.net/npm/twemoji@14.0.2/assets/72x72/1f3e0.png',
      badge: 'https://cdn.jsdelivr.net/npm/twemoji@14.0.2/assets/72x72/1f3e0.png',
      vibrate: [200, 100, 200],
      tag: 'gestor-alerta',
      renotify: true,
    })
  );
});
