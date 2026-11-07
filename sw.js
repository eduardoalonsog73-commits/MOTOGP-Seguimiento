const VERSION = "motogp-v3";
const APP_SHELL = ["./","./index.html","./manifest.json","./icon-192.png","./icon-512.png","./data/motogp-live.json"];
self.addEventListener("install", event => {
  event.waitUntil(caches.open(VERSION).then(cache => cache.addAll(APP_SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== VERSION).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", event => {
  const request=event.request, url=new URL(request.url);
  if(request.method!=="GET"||url.origin!==self.location.origin)return;
  if(url.pathname.endsWith("/data/motogp-live.json")){
    event.respondWith(fetch(request,{cache:"no-store"}).then(response=>{
      if(response&&response.ok){const copy=response.clone();caches.open(VERSION).then(cache=>cache.put("./data/motogp-live.json",copy));}
      return response;
    }).catch(()=>caches.match("./data/motogp-live.json")));
    return;
  }
  if(request.mode==="navigate"||url.pathname.endsWith("/index.html")||url.pathname.endsWith("/")){
    event.respondWith(fetch(request,{cache:"no-store"}).then(response=>{
      if(response&&response.ok){const copy=response.clone();caches.open(VERSION).then(cache=>cache.put("./index.html",copy));}
      return response;
    }).catch(()=>caches.match("./index.html")));
    return;
  }
  event.respondWith(caches.match(request).then(cached=>{
    const update=fetch(request).then(response=>{
      if(response&&response.ok){const copy=response.clone();caches.open(VERSION).then(cache=>cache.put(request,copy));}
      return response;
    }).catch(()=>cached);
    return cached||update;
  }));
});
