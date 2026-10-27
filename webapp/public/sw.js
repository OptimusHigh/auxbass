const CACHE_NAME = 'tg-player-v3';
const STATIC_CACHE = 'tg-player-static-v3';
const AUDIO_CACHE = 'tg-player-audio-v2';
const IMAGE_CACHE = 'tg-player-images-v1';

async function trimCache(cacheName, maxItems) {
  try {
    const cache = await caches.open(cacheName);
    const keys = await cache.keys();
    if (keys.length > maxItems) {
      await cache.delete(keys[0]);
      trimCache(cacheName, maxItems);
    }
  } catch (_) {}
}

// Статические ресурсы для кэширования
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
  '/telegram-web-app.js'
];

// Установка Service Worker
self.addEventListener('install', (event) => {
  console.log('[SW] Installing...');
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then(async (cache) => {
        console.log('[SW] Caching static assets');
        for (const asset of STATIC_ASSETS) {
          try {
            await cache.add(asset);
          } catch (e) {
            console.warn('[SW] Could not cache asset:', asset, e);
          }
        }
      })
      .then(() => self.skipWaiting())
  );
});

// Активация и очистка старых кэшей
self.addEventListener('activate', (event) => {
  console.log('[SW] Activating...');
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name !== STATIC_CACHE && name !== AUDIO_CACHE && name !== CACHE_NAME && name !== IMAGE_CACHE)
          .map((name) => {
            console.log('[SW] Deleting old cache:', name);
            return caches.delete(name);
          })
      );
    }).then(() => self.clients.claim())
  );
});

// Стратегия кэширования
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  
  // Пропускаем не-GET запросы
  if (event.request.method !== 'GET') return;
  
  // Пропускаем внешние запросы (не к нашему origin)
  if (url.origin !== self.location.origin) return;

  // Для картинок обложек (/api/images/...) - кэшируем в CacheStorage (Cache-First)
  if (url.pathname.startsWith('/api/images/')) {
    event.respondWith(
      caches.open(IMAGE_CACHE).then((cache) => {
        return cache.match(event.request).then((cached) => {
          if (cached) return cached;
          return fetch(event.request).then((response) => {
            if (response.status === 200) {
              const clone = response.clone();
              cache.put(event.request, clone).then(() => {
                trimCache(IMAGE_CACHE, 300);
              });
            }
            return response;
          }).catch((err) => {
            console.warn('[SW] Image fetch failed:', event.request.url, err);
            return new Response('', { status: 408, statusText: 'Image fetch timeout or error' });
          });
        });
      })
    );
    return;
  }

  // Пропускаем остальные API запросы (они должны быть свежими)
  if (url.pathname.startsWith('/api/')) return;
  
  // Для аудио файлов - network first, потом cache
  if (url.pathname.includes('/audio/') || event.request.destination === 'audio') {
    event.respondWith(
      caches.open(AUDIO_CACHE).then((cache) => {
        return fetch(event.request)
          .then((response) => {
            // Кэшируем только успешные ответы
            if (response.status === 200) {
              cache.put(event.request, response.clone());
            }
            return response;
          })
          .catch(() => {
            // Если офлайн - пробуем из кэша
            return cache.match(event.request);
          });
      })
    );
    return;
  }
  
  // Для статических ресурсов - cache first
  if (
    event.request.destination === 'style' ||
    event.request.destination === 'script' ||
    event.request.destination === 'image' ||
    event.request.destination === 'font'
  ) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;
        
        return fetch(event.request).then((response) => {
          // Кэшируем статику
          if (response.status === 200) {
            const clone = response.clone();
            caches.open(STATIC_CACHE).then((cache) => {
              cache.put(event.request, clone);
            });
          }
          return response;
        }).catch((err) => {
          console.warn('[SW] Static fetch failed for:', event.request.url, err);
          return new Response('', { status: 408, statusText: 'Static fetch timeout or error' });
        });
      })
    );
    return;
  }
  
  // Для HTML - network first с fallback на cache
  if (event.request.destination === 'document') {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const clone = response.clone();
          caches.open(STATIC_CACHE).then((cache) => {
            cache.put(event.request, clone);
          });
          return response;
        })
        .catch(() => {
          return caches.match(event.request) || caches.match('/');
        })
    );
    return;
  }
});

// Обработка push уведомлений (для будущего)
self.addEventListener('push', (event) => {
  if (!event.data) return;
  
  const data = event.data.json();
  const options = {
    body: data.body || 'Новое уведомление',
    icon: '/icons/icon-192x192.png',
    badge: '/icons/icon-72x72.png',
    vibrate: [100, 50, 100],
    data: data.data || {},
    actions: data.actions || []
  };
  
  event.waitUntil(
    self.registration.showNotification(data.title || 'TG Player', options)
  );
});

// Клик по уведомлению
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then((clientList) => {
        // Если есть открытое окно - фокус на него
        for (const client of clientList) {
          if (client.url.includes(self.location.origin) && 'focus' in client) {
            return client.focus();
          }
        }
        // Иначе открываем новое
        if (clients.openWindow) {
          return clients.openWindow('/');
        }
      })
  );
});

// Фоновая синхронизация (для будущего)
self.addEventListener('sync', (event) => {
  if (event.tag === 'sync-library') {
    console.log('[SW] Syncing library...');
  }
});

// Обработка сообщений от клиента (форс-обновление и очистка кэша)
self.addEventListener('message', (event) => {
  if (!event.data) return;
  if (event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  if (event.data.type === 'CLEAR_CACHE') {
    event.waitUntil(
      caches.keys().then((names) => {
        return Promise.all(names.map((name) => caches.delete(name)));
      })
    );
  }
});
