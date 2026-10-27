/* Only the public application shell is cached. Remote data and release responses stay off disk. */
const CACHE = 'aliancadx-ios-shell-v2-7.6.0';
const SW_VERSION = '7.6.0';
const SHELL = ['./', './index.html', './app.css', './app.mjs', './protocol.mjs', './stream-quality.mjs', './updates.mjs', './signaling.mjs', './session.mjs', './files.mjs', './metrics.mjs', './session-view.mjs', './favorites.mjs', './manifest.webmanifest', './assets/aliancadx-logo.png', './assets/icon-192.png', './assets/icon-512.png', './assets/apple-touch-icon.png', './vendor/qrcode.js'];
async function installProgress(completed){
  try{
    const scope=new URL('./',self.location.href).href;
    const clients=await self.clients.matchAll({type:'window',includeUncontrolled:true});
    for(const client of clients)if(client.url.startsWith(scope)){
      try{client.postMessage({type:'AADX_UPDATE_PROGRESS',version:SW_VERSION,completed,total:SHELL.length});}catch{}
    }
  }catch{} // Progress reporting must never decide whether an install succeeds.
}
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(async cache => {
    let completed=0;await installProgress(completed);
    for (const path of SHELL) {
      const response = await fetch(new Request(new URL(path,self.location.href), {cache:'reload', credentials:'omit'}));
      if (!response.ok || response.type === 'opaque') throw new Error('Public shell incomplete');
      if(path==='./app.mjs'){
        const source=await response.clone().text();
        if(!source.includes("const PWA_APP_VERSION='"+SW_VERSION+"';"))throw new Error('Site version not ready');
      }
      await cache.put(path,response);
      await installProgress(++completed);
    }
  }));
  // Waiting workers stay waiting until an explicit, confirmed apply request.
});
self.addEventListener('activate', event => {
  // Keep older public shell caches available while older tabs are still open.
  event.waitUntil(self.clients.claim());
});
function clientIsIdle(client){
  return new Promise(resolve=>{
    const channel=new MessageChannel(),timer=setTimeout(()=>done(false),1800);
    const done=idle=>{clearTimeout(timer);channel.port1.close();channel.port2.close();resolve(idle);};
    channel.port1.onmessage=event=>done(event.data?.busy===false);
    try{client.postMessage({type:'AADX_QUERY_SESSIONS'},[channel.port2]);}catch{done(false);}
  });
}
self.addEventListener('message', event => {
  const data=event.data,port=event.ports?.[0];if(!port)return;
  if(data?.type==='AADX_UPDATE_INFO'){port.postMessage({version:SW_VERSION,cacheReady:true});return;}
  if(data?.type!=='AADX_APPLY_UPDATE')return;
  event.waitUntil((async()=>{
    if(data.userConfirmed!==true||data.version!==SW_VERSION){port.postMessage({accepted:false,message:'Confirme a versão antes de aplicar.'});return;}
    const scope=new URL('./',self.location.href).href;
    const clients=(await self.clients.matchAll({type:'window',includeUncontrolled:true})).filter(client=>client.url.startsWith(scope));
    if(!clients.length||!(await Promise.all(clients.map(clientIsIdle))).every(Boolean)){
      port.postMessage({accepted:false,message:'Encerre os acessos em todas as abas do AliançaDX. Feche abas antigas que não respondem e tente novamente.'});return;
    }
    port.postMessage({accepted:true,version:SW_VERSION});
    await self.skipWaiting();
  })());
});
self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.cache === 'no-store' || request.method !== 'GET' || request.headers.has('Authorization') || url.origin !== self.location.origin || url.search || url.hash) return;
  const shellURLs = new Set(SHELL.map(path => new URL(path,self.location.href).href));
  if (!shellURLs.has(url.href)) return;
  event.respondWith((async()=>{
    // Pin this running version until a confirmed worker activation. Never mix future modules into its cache.
    const cache=await caches.open(CACHE),cached=await cache.match?.(request);
    if(cached)return cached;
    try{return await fetch(request);}
    catch{return (await caches.match(request)) || new Response('A interface ainda não está disponível offline. Abra o aplicativo com internet pelo menos uma vez.',{status:503,headers:{'Content-Type':'text/plain; charset=utf-8'}});}
  })());
});
