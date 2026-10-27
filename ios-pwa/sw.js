/* Only the public application shell is cached. Signaling and remote data stay off disk. */
const CACHE = 'aliancadx-ios-shell-v2-7.3.0';
const SHELL = ['./', './index.html', './app.css', './app.mjs', './protocol.mjs', './stream-quality.mjs', './signaling.mjs', './session.mjs', './files.mjs', './metrics.mjs', './session-view.mjs', './favorites.mjs', './manifest.webmanifest', './assets/aliancadx-logo.png', './assets/icon-192.png', './assets/icon-512.png', './assets/apple-touch-icon.png', './vendor/qrcode.js'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(async cache => {
    for (const path of SHELL) {
      try {
        const response = await fetch(new Request(path, {cache:'reload', credentials:'omit'}));
        if (response.ok && response.type !== 'opaque') await cache.put(path,response);
      } catch {}
    }
  }));
});
self.addEventListener('activate', event => event.waitUntil(Promise.all([
  caches.keys().then(names => Promise.all(names.filter(name => name.startsWith('aliancadx-ios-shell-') && name !== CACHE).map(name => caches.delete(name)))),
  self.clients.claim()
])));
self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || request.headers.has('Authorization') || url.origin !== self.location.origin || url.search || url.hash) return;
  const shellURLs = new Set(SHELL.map(path => new URL(path,self.location.href).href));
  if (!shellURLs.has(url.href)) return;
  event.respondWith(fetch(request).then(response => {
    if (response.ok && response.type !== 'opaque') {
      const copy=response.clone();
      event.waitUntil(caches.open(CACHE).then(cache=>cache.put(request,copy)));
    }
    return response;
  }).catch(async () => (await caches.match(request)) || new Response('A interface ainda não está disponível offline. Abra o aplicativo com internet pelo menos uma vez.',{status:503,headers:{'Content-Type':'text/plain; charset=utf-8'}})));
});
