const CACHE_NAME = 'dcs-v3.4.2-20261024-radar4';
const ASSETS_TO_CACHE = [
  '/app.js?v=20261024-radar4',
  '/assistant.js?v=20261020-ai91',
  '/prediction.js?v=20261020-ui3',
  '/map.html',
  '/map.css?v=20261020-ui3',
  '/map.js?v=20261020-ui3',
  '/echarts.min.js',
  '/static/maps/china.json',
  '/static/maps/city_coords.json',
  '/profile.html',
  '/profile.css?v=20261020-ai91',
  '/profile.js?v=20261020-ai91',
  '/theme.css?v=20261020-ui3',
  '/theme.css?v=20261020-ui3',
  '/theme.css?v=20261020-ui3',
  '/theme.css?v=20261024-radar4',
  '/login.html',
  '/register.html',
  '/auth.css?v=20261020-ui3',
  '/auth.js?v=20261020-ai91',
  '/logo.png?v=20261020-ai91',
  '/cross_platform.css',
  '/bootstrap.min.css',
  '/bootstrap.bundle.min.js',
  '/font-awesome.min.css',
  '/chart.js',
  '/platform-utils.js',
  '/sw-register.js',
  '/download.html',
  '/download.js',
  '/speech_to_text.html',
  '/speech_to_text.js',
  '/manifest.json',
  '/static/assistant/avatar.png',
  '/static/icons/icon-192x192.png?v=20261020-ai91',
  '/static/icons/icon-512x512.png?v=20261020-ai91',
  '/webfonts/fa-solid-900.woff2',
  '/webfonts/fa-brands-400.woff2',
  '/perf-benchmark.js',
  '/compat-tester.js'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE).catch((err) => {
        console.warn('[SW] Some assets failed to cache:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => {
        return caches.match(event.request).then((cached) => {
          return cached || caches.match('/login.html');
        });
      })
    );
    return;
  }

  if (event.request.url.includes('/api/')) {
    event.respondWith(
      fetch(event.request).catch(() => {
        return new Response(JSON.stringify({ success: false, message: '您处于离线状态' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' }
        });
      })
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => {
      const fetchPromise = fetch(event.request).then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200) {
          const clone = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return networkResponse;
      }).catch(() => cached);

      return cached || fetchPromise;
    })
  );
});

self.addEventListener('push', (event) => {
  const data = event.data ? event.data.json() : {};
  const options = {
    body: data.body || '您有新的仿真结果',
    icon: '/static/icons/icon-192x192.png?v=20261020-ai91',
    badge: '/static/icons/badge-72x72.png',
    vibrate: [200, 100, 200],
    data: { url: data.url || '/' },
    actions: [
      { action: 'open', title: '查看详情' },
      { action: 'close', title: '关闭' }
    ]
  };
  event.waitUntil(self.registration.showNotification(data.title || '算力调度系统', options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  if (event.action === 'open' || !event.action) {
    const url = event.notification.data.url || '/';
    event.waitUntil(clients.openWindow(url));
  }
});


























