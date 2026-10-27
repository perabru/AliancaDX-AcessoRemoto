import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

const source=readFileSync(new URL('../sw.js',import.meta.url),'utf8');
const scope='https://perabru.github.io/AliancaDX-AcessoRemoto/ios-pwa/';
class Port {
  postMessage(data){queueMicrotask(()=>{if(!this.peer.closed)this.peer.onmessage?.({data});});}
  close(){this.closed=true;}
}
class Channel {
  constructor(){this.port1=new Port();this.port2=new Port();this.port1.peer=this.port2;this.port2.peer=this.port1;}
}
function harness({clients=[],fetchImpl}={}){
  const handlers={},stores=new Map(),deleted=[],fetched=[],state={skipWaiting:0,claimed:0,queries:[]};
  const normalized=key=>new URL(typeof key==='string'?key:key.url,scope).href;
  const store=name=>{if(!stores.has(name))stores.set(name,new Map());return stores.get(name);};
  const context={
    URL,Request,Response,Headers,Promise,MessageChannel:Channel,
    setTimeout:(callback,delay)=>setTimeout(callback,Math.min(delay,8)),clearTimeout,
    fetch:async request=>{
      fetched.push(request);
      if(fetchImpl)return fetchImpl(request);
      return new Response(new URL(request.url||request,scope).pathname.endsWith('/app.mjs')?"const PWA_APP_VERSION='7.5.0';":'public shell');
    },
    caches:{
      open:async name=>({put:async(key,value)=>store(name).set(normalized(key),value),match:async key=>store(name).get(normalized(key))?.clone()}),
      match:async key=>{for(const contents of stores.values()){const found=contents.get(normalized(key));if(found)return found.clone();}},
      keys:async()=>[...stores.keys()],delete:async name=>{deleted.push(name);return stores.delete(name);}
    },
    self:{
      location:new URL(scope+'sw.js'),addEventListener:(name,handler)=>{handlers[name]=handler;},
      clients:{claim:async()=>{state.claimed++;},matchAll:async options=>{state.queries.push(options);return clients;}},
      skipWaiting:async()=>{state.skipWaiting++;}
    }
  };
  vm.createContext(context);vm.runInContext(source+'\nglobalThis.cacheName=CACHE; globalThis.workerVersion=SW_VERSION;',context);
  async function lifecycle(name){let pending;handlers[name]({waitUntil:value=>{pending=value;}});await pending;}
  async function message(data){
    let pending;const replies=[];
    handlers.message({data,ports:[{postMessage:value=>replies.push(value)}],waitUntil:value=>{pending=value;}});
    await pending;return replies;
  }
  async function asset(path,{cache='default',networkFailure=false}={}){
    const waits=[];let pending;
    if(networkFailure)context.fetch=async()=>{throw new TypeError('offline');};
    const request=new Request(new URL(path,scope),{cache});
    handlers.fetch({request,respondWith:value=>{pending=value;},waitUntil:value=>waits.push(value)});
    // A request the worker deliberately leaves alone follows the browser's network path.
    if(!pending)pending=context.fetch(request);
    const response=await pending;await Promise.all(waits);return response;
  }
  return {context,handlers,stores,store,deleted,fetched,state,lifecycle,message,asset};
}
const browserClient=(busy=false,url=scope)=>({url,messages:[],postMessage(data,[port]){this.messages.push(data);port.postMessage({busy});}});
const apply={type:'AADX_APPLY_UPDATE',version:'7.5.0',userConfirmed:true};

test('install prepares the complete public shell without automatically skipping waiting',async()=>{
  const h=harness();await h.lifecycle('install');
  assert.equal(h.state.skipWaiting,0);
  assert.ok(h.store(h.context.cacheName).has(scope+'updates.mjs'));
  assert.ok(h.store(h.context.cacheName).has(scope+'app.mjs'));
  assert.ok(h.fetched.length>15);
  for(const request of h.fetched)assert.equal(request.credentials,'omit');
});

test('a partial public shell or app from the wrong deployment cannot finish installation',async()=>{
  const incomplete=harness({fetchImpl:async request=>new Response('',{status:request.url.endsWith('/app.css')?503:200})});
  await assert.rejects(incomplete.lifecycle('install'));
  assert.equal(incomplete.state.skipWaiting,0);
  const stale=harness({fetchImpl:async()=>new Response("const PWA_APP_VERSION='7.3.0';")});
  await assert.rejects(stale.lifecycle('install'));
  assert.equal(stale.state.skipWaiting,0);
});

test('activation preserves older shell caches for existing tabs',async()=>{
  const h=harness();h.store('aliancadx-ios-shell-v2-7.3.0').set(scope+'app.mjs',new Response('old app'));
  await h.lifecycle('activate');
  assert.equal(h.state.claimed,1);
  assert.deepEqual(h.deleted,[]);
  assert.ok(h.stores.has('aliancadx-ios-shell-v2-7.3.0'));
});

test('worker information confirms exact prepared version without activating it',async()=>{
  const h=harness();const replies=await h.message({type:'AADX_UPDATE_INFO'});
  assert.equal(replies[0].version,'7.5.0');assert.equal(replies[0].cacheReady,true);
  assert.equal(h.state.skipWaiting,0);
});

test('activation requires literal explicit confirmation and matching version',async()=>{
  for(const data of [{...apply,userConfirmed:false},{...apply,userConfirmed:'true'},{...apply,version:'7.6.0'}]){
    const h=harness({clients:[browserClient()]});const replies=await h.message(data);
    assert.equal(replies[0].accepted,false);assert.equal(h.state.skipWaiting,0);
  }
});

test('a live session in any tab blocks activation while leaving all tabs and caches intact',async()=>{
  const clients=[browserClient(false),browserClient(true,scope+'index.html'),browserClient(false)];
  const h=harness({clients});const replies=await h.message(apply);
  assert.equal(replies[0].accepted,false);assert.equal(h.state.skipWaiting,0);assert.deepEqual(h.deleted,[]);
  assert.ok(clients.every(client=>client.messages[0].type==='AADX_QUERY_SESSIONS'));
});

test('an old or unresponsive app tab blocks activation rather than assuming its session is idle',async()=>{
  const h=harness({clients:[browserClient(),{url:scope+'index.html',postMessage(){}}]});
  const replies=await h.message(apply);
  assert.equal(replies[0].accepted,false);assert.equal(h.state.skipWaiting,0);
});

test('a worker cannot activate on a confirmed request when no app clients were found',async()=>{
  const h=harness();const replies=await h.message(apply);
  assert.equal(replies[0].accepted,false);assert.equal(h.state.skipWaiting,0);
});

test('confirmed activation queries all controlled and uncontrolled app tabs before skipping waiting',async()=>{
  const clients=[browserClient(false),browserClient(false,scope+'index.html'),{url:'https://other.example/',postMessage(){throw new Error('out of scope');}}];
  const h=harness({clients});const replies=await h.message(apply);
  assert.equal(replies[0].accepted,true);assert.equal(h.state.skipWaiting,1);
  assert.equal(h.state.queries[0].type,'window');assert.equal(h.state.queries[0].includeUncontrolled,true);
});

test('cached shell remains on its installed version until a confirmed update',async()=>{
  const h=harness({fetchImpl:async()=>new Response('new remote shell')});
  h.store(h.context.cacheName).set(scope+'app.mjs',new Response('installed shell'));
  const result=await h.asset('app.mjs');
  assert.equal(await result.text(),'installed shell');
  assert.equal(h.fetched.length,0);
});

test('no-store deployment checks reach the network without overwriting the installed shell',async()=>{
  const h=harness({fetchImpl:async()=>new Response('published shell')});
  h.store(h.context.cacheName).set(scope+'app.mjs',new Response('installed shell'));
  const result=await h.asset('app.mjs',{cache:'no-store'});
  assert.equal(await result.text(),'published shell');
  assert.equal(await h.store(h.context.cacheName).get(scope+'app.mjs').clone().text(),'installed shell');
});
