import test from 'node:test';
import assert from 'node:assert/strict';
import {BridgeCommandQueue,canvasBacking,ScreenPresenter,fitBounds} from '../protocol.mjs';
import {RemoteSession,decodeJpegImage} from '../session.mjs';
import {readFileSync,mkdtempSync,writeFileSync,copyFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';

const settle=async()=>{for(let n=0;n<8;n++)await Promise.resolve();};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};

test('every received image uses the physical display raster without a second browser upscale',()=>{
  assert.deepEqual(canvasBacking(844,390,480,270,1,2),{width:1688,height:780,scaleX:2,scaleY:2});
  const sharp=canvasBacking(390,844,3840,2160,1,3);
  assert.equal(sharp.width,1170);assert.equal(sharp.height,2532);assert.equal(sharp.scaleX,3);
  assert.deepEqual(canvasBacking(390,844,960,540,6,3),canvasBacking(390,844,960,540,1,3));
});

test('all zoom levels retain physical density while oversized viewports have a strict 4K memory bound',()=>{
  const huge=canvasBacking(10000,6000,3840,2160,1,3);
  assert.ok(huge.width*huge.height<=3840*2160);
  assert.equal(canvasBacking(0,600,1920,1080),null);
  assert.equal(canvasBacking(NaN,600,1920,1080),null);
});

function canvas(){
  const calls=[];const context=new Proxy({calls},{get(target,key){if(key in target)return target[key];return (...args)=>calls.push([key,...args]);},set(target,key,value){target[key]=value;return true;}});
  return {width:0,height:0,getContext(){return context;},context};
}
test('one hundred cursor moves never rescale or redraw the unchanged JPEG',()=>{
  const base=canvas(),cursor=canvas(),painter=new ScreenPresenter(base,cursor);
  const image={naturalWidth:1920,naturalHeight:1080};
  const session={image,frame:{captureId:'A'},zoom:1,point:{x:.5,y:.5},layout:(w,h)=>fitBounds(w,h,1920,1080)};
  painter.paint(session,844,390,2);
  for(let n=0;n<100;n++){session.point={x:n/100,y:.5};painter.paint(session,844,390,2);}
  assert.equal(base.context.calls.filter(x=>x[0]==='drawImage').length,1);
  assert.equal(cursor.context.calls.filter(x=>x[0]==='clearRect').length,101);
  session.image={...image};painter.paint(session,844,390,2);
  assert.equal(base.context.calls.filter(x=>x[0]==='drawImage').length,2);
  session.image=null;session.frame=null;painter.paint(session,844,390,2);
  assert.equal(painter.previous.image,null);
});
test('a changed view, source or selection invalidates the retained JPEG raster',()=>{
  const base=canvas(),cursor=canvas(),painter=new ScreenPresenter(base,cursor);
  const session={image:{naturalWidth:1280,naturalHeight:720},frame:{},zoom:1,point:{x:.5,y:.5},layout:(w,h)=>fitBounds(w,h,1280,720)};
  painter.paint(session,844,390,2);painter.paint(session,390,844,2);
  painter.paint({...session},390,844,2);
  assert.equal(base.context.calls.filter(x=>x[0]==='drawImage').length,3);
});
test('a cached 7.1 HTML or stylesheet cannot turn the new cursor layer into an input blocker',()=>{
  const base=canvas(),created=canvas(),children=[];created.style={};created.setAttribute=(key,value)=>{created[key]=value;};
  base.ownerDocument={createElement(name){assert.equal(name,'canvas');return created;}};base.parentElement={append(item){children.push(item);}};
  const painter=new ScreenPresenter(base,null);
  assert.equal(painter.cursor,created);assert.equal(created.id,'cursor-canvas');assert.equal(created.style.pointerEvents,'none');assert.equal(created.style.position,'absolute');assert.equal(created['aria-hidden'],'true');assert.deepEqual(children,[created]);
  const cachedStyle=canvas();cachedStyle.style={pointerEvents:'auto'};new ScreenPresenter(base,cachedStyle);assert.equal(cachedStyle.style.pointerEvents,'none');
});

test('the actual 7.2 PhoneApp starts with the exact cached 7.1 protocol and HTML without a cursor canvas',async t=>{
  const root=new URL('../',import.meta.url),temp=mkdtempSync(join(tmpdir(),'aliancadx-cache-interop-'));
  const names=JSON.parse(readFileSync(new URL('../public-assets.json',import.meta.url),'utf8'));
  for(const name of names.filter(n=>n.endsWith('.mjs')))copyFileSync(new URL(name,root),join(temp,name));
  copyFileSync(new URL('./fixtures/protocol-7.1.mjs',import.meta.url),join(temp,'protocol.mjs'));
  const source=readFileSync(new URL('../app.mjs',import.meta.url),'utf8').split('const app=new PhoneApp();')[0]+'export {PhoneApp};\n';writeFileSync(join(temp,'app-interop.mjs'),source);
  const descriptors={};for(const key of ['document','window','location','history','navigator','ResizeObserver','requestAnimationFrame'])descriptors[key]=Object.getOwnPropertyDescriptor(globalThis,key);
  const elements=new Map();let document;
  class Element extends EventTarget{
    constructor(){super();this.hidden=false;this.disabled=false;this.value='';this.textContent='';this.children=[];this.style={setProperty(){}};this.classList={toggle(){},add(){},remove(){},contains(){return false;}};this.clientWidth=400;this.clientHeight=300;this.context=canvas().context;}
    get ownerDocument(){return document;}get parentElement(){return elements.get('screen-stage');}
    getContext(){return this.context;}setAttribute(){}removeAttribute(){}append(...rows){this.children.push(...rows);}replaceChildren(...rows){this.children=rows;}querySelector(){return new Element();}querySelectorAll(){return [];}focus(){}scrollIntoView(){}
  }
  document=new EventTarget();document.hidden=false;document.documentElement=new Element();document.body=new Element();document.createElement=()=>new Element();document.querySelectorAll=()=>[];
  document.getElementById=id=>{if(id==='cursor-canvas')return null;if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const window=new EventTarget();window.innerHeight=800;window.devicePixelRatio=2;
  const values={document,window,location:{hash:'',pathname:'/',protocol:'http:'},history:{replaceState(){}},navigator:{onLine:true},ResizeObserver:class{observe(){}},requestAnimationFrame:()=>1};
  for(const [key,value]of Object.entries(values))Object.defineProperty(globalThis,key,{value,configurable:true});
  let app;
  try{
    const module=await import(pathToFileURL(join(temp,'app-interop.mjs')).href);app=new module.PhoneApp();
    assert.equal(app.sessions.size,1);assert.equal(app.presenter.cursor.id,'cursor-canvas');assert.equal(app.presenter.cursor.style.pointerEvents,'none');
    assert.equal(elements.get('screen-stage').children.includes(app.presenter.cursor),true);
    const bridge=readFileSync(new URL('../bridge.mjs',import.meta.url),'utf8').split('\n').filter(line=>line.startsWith('import ')).join('\n')+'\nexport {BridgeCommandQueue,FramedChannel};\n';
    writeFileSync(join(temp,'bridge-interop.mjs'),bridge);const bridgeModule=await import(pathToFileURL(join(temp,'bridge-interop.mjs')).href);
    assert.equal(typeof bridgeModule.BridgeCommandQueue,'function');assert.equal(typeof bridgeModule.FramedChannel,'function');
  }finally{
    if(app)for(const session of app.sessions.values())session.close();
    for(const [key,descriptor]of Object.entries(descriptors)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}rmSync(temp,{recursive:true,force:true});
  }
});

test('bridge compresses three hundred waiting moves into the latest coordinate and retains button boundaries',async()=>{
  const first=deferred(),sent=[];
  const queue=new BridgeCommandQueue(async message=>{sent.push(message);if(sent.length===1)await first.promise;},assert.fail);
  const move=x=>({type:'input',action:'move',captureId:'A',x,y:.5});
  queue.enqueue(move(0));
  for(let x=1;x<=300;x++)queue.enqueue(move(x/300));
  queue.enqueue({type:'input',action:'button',button:'left',down:true});
  queue.enqueue(move(.8));queue.enqueue(move(.7));
  queue.enqueue({type:'input',action:'button',button:'left',down:false});
  first.resolve();await settle();
  assert.deepEqual(sent.map(x=>x.action),['move','move','button','move','button']);
  assert.equal(sent[1].x,1);assert.equal(sent[3].x,.7);assert.equal(queue.bytes,0);
});
test('capture changes and releases remain boundaries between cursor moves',async()=>{
  const first=deferred(),sent=[];const queue=new BridgeCommandQueue(async m=>{sent.push(m);if(sent.length===1)await first.promise;},assert.fail);
  queue.enqueue({type:'input',action:'move',captureId:'A'});
  queue.enqueue({type:'input',action:'move',captureId:'A'});
  queue.enqueue({type:'input',action:'release'});
  queue.enqueue({type:'input',action:'move',captureId:'A'});
  queue.enqueue({type:'input',action:'move',captureId:'B'});
  first.resolve();await settle();assert.deepEqual(sent.map(x=>x.action),['move','move','release','move','move']);
  assert.deepEqual(sent.filter(x=>x.action==='move').map(x=>x.captureId),['A','A','A','B']);
});
test('cumulative feedback bypasses waiting inputs without starving release or key-up',async()=>{
  const first=deferred(),sent=[];const queue=new BridgeCommandQueue(async m=>{sent.push(m);if(sent.length===1)await first.promise;},assert.fail);
  queue.enqueue({type:'input',action:'move'});queue.enqueue({type:'input',action:'release'});
  queue.enqueue({type:'stream_feedback',frameId:1,cumulative:true});
  queue.enqueue({type:'stream_feedback',frameId:2,cumulative:true});
  queue.enqueue({type:'input',action:'key',key:'shift',down:false});
  first.resolve();await settle();
  assert.deepEqual(sent.map(x=>x.type==='stream_feedback'?'ack'+x.frameId:x.action),['move','ack2','release','key']);
  assert.equal(queue.bytes,0);
});
test('successive high-priority feedback alternates with ordered user commands',async()=>{
  const first=deferred(),sent=[];let queue;
  queue=new BridgeCommandQueue(async m=>{sent.push(m);if(sent.length===1)await first.promise;if(m.type==='stream_feedback'&&m.frameId<5)queue.enqueue({type:'stream_feedback',frameId:m.frameId+1,cumulative:true});},assert.fail);
  queue.enqueue({type:'input',action:'move'});queue.enqueue({type:'input',action:'release'});
  queue.enqueue({type:'input',action:'key',down:false});queue.enqueue({type:'stream_feedback',frameId:1,cumulative:true});
  first.resolve();await settle();
  assert.equal(sent[1].type,'stream_feedback');assert.equal(sent[2].action,'release');
  assert.equal(sent[3].type,'stream_feedback');assert.equal(sent[4].action,'key');
});
test('bridge queue has bounded bytes, reports failure once and discards pending work after close',async()=>{
  const first=deferred(),sent=[],errors=[];
  const queue=new BridgeCommandQueue(async m=>{sent.push(m);await first.promise;},e=>errors.push(e),{limit:3,maxBytes:200});
  queue.enqueue({type:'first'});queue.enqueue({type:'second'});queue.shutdown();
  assert.equal(queue.enqueue({type:'ignored'}),false);first.resolve();await settle();assert.equal(sent.length,1);assert.equal(queue.bytes,0);
  const blocked=new BridgeCommandQueue(()=>deferred().promise,e=>errors.push(e),{maxBytes:10});
  assert.equal(blocked.enqueue({type:'long-message'}),false);assert.equal(blocked.closed,true);assert.equal(errors.length,1);
});

function receiver(t){
  t.mock.timers.enable({apis:['setInterval']});
  const originals={};for(const key of ['document','requestAnimationFrame','createImageBitmap'])originals[key]=Object.getOwnPropertyDescriptor(globalThis,key);
  const document={hidden:false},raf=[],decoders=[],blobs=[],sent=[],painted=[],changed=[];
  Object.defineProperty(globalThis,'document',{value:document,configurable:true});
  Object.defineProperty(globalThis,'requestAnimationFrame',{value:callback=>{raf.push(callback);return raf.length;},configurable:true});
  Object.defineProperty(globalThis,'createImageBitmap',{value:blob=>{blobs.push(blob);const pending=deferred();decoders.push(pending);return pending.promise;},configurable:true});
  const app={selected:null,paint(s){painted.push(s.frame?.frameId);},changed(s){changed.push(s.frame?.frameId);},phoneName(){return 'test';}};
  const session=new RemoteSession(1,app);app.selected=session;session.connected=true;session.sharing=true;session.wire={send(m){sent.push(m);return true;},shutdown(){}};
  const jpeg=Buffer.from([255,216,255,192,0,8,8,1,224,2,128,1]).toString('base64');
  const frame=(id,captureId='A')=>({type:'screen',captureId,width:640,height:480,streamVersion:1,frameId:id,image:jpeg,computerName:'PC'});
  const image=()=>({width:640,height:480,closed:0,close(){this.closed++;}});
  const tick=async()=>{const callbacks=raf.splice(0);for(const callback of callbacks)callback(performance.now());await settle();};
  t.after(()=>{session.close();for(const [key,descriptor]of Object.entries(originals)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}});
  return {session,app,document,raf,decoders,blobs,sent,painted,changed,frame,image,tick};
}
test('decode and presentation overlap: the newest decoded candidate is painted and acknowledged cumulatively',async t=>{
  const h=receiver(t),a=h.image(),c=h.image();
  h.session.handle(h.frame(1));h.session.handle(h.frame(2));h.session.handle(h.frame(3));
  h.decoders[0].resolve(a);await settle();assert.equal(h.decoders.length,2);
  h.decoders[1].resolve(c);await settle();assert.equal(a.closed,1);assert.equal(h.raf.length,1);
  await h.tick();assert.deepEqual(h.painted,[3]);assert.equal(h.sent.filter(x=>x.type==='stream_feedback').length,0);
  await h.tick();const ack=h.sent.find(x=>x.type==='stream_feedback');assert.equal(ack.frameId,3);assert.equal(ack.cumulative,true);
  h.session.invalidateFrame();assert.equal(c.closed,1);
});
test('a continuous stream cannot postpone an already scheduled browser presentation forever',async t=>{
  const h=receiver(t);h.session.handle(h.frame(1));h.decoders[0].resolve(h.image());await settle();
  h.session.handle(h.frame(2));h.session.handle(h.frame(3));
  await h.tick();assert.deepEqual(h.painted,[1]);
  h.decoders[1].resolve(h.image());await settle();await h.tick();assert.deepEqual(h.painted,[1,2]);
  h.decoders[2].resolve(h.image());await settle();await h.tick();assert.deepEqual(h.painted,[1,2,3]);
});
test('stale decode results and paused presentation close bitmaps without painting or acknowledging',async t=>{
  const h=receiver(t),a=h.image();h.session.handle(h.frame(1));h.session.invalidateFrame();
  h.decoders[0].resolve(a);await settle();await h.tick();assert.equal(a.closed,1);assert.deepEqual(h.painted,[undefined]);
  const b=h.image();h.session.handle(h.frame(2));h.decoders[1].resolve(b);await settle();h.document.hidden=true;await h.tick();
  assert.equal(b.closed,1);assert.equal(h.sent.filter(x=>x.type==='stream_feedback').length,0);
});
test('duplicate or regressing frame IDs never replace the displayed image',async t=>{
  const h=receiver(t);h.session.handle(h.frame(3));h.decoders[0].resolve(h.image());await settle();await h.tick();await h.tick();
  h.session.handle(h.frame(2));h.session.handle(h.frame(3));assert.equal(h.decoders.length,1);assert.equal(h.session.frame.frameId,3);
});
test('a negotiated lossless PNG is decoded as PNG and acknowledged only after presentation',async t=>{
  const h=receiver(t),png='iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAIAAAB7QOjdAAAAD0lEQVR4nGPkVw1iYGAAAAJpAIhDetp6AAAAAElFTkSuQmCC';
  h.session.handle({...h.frame(1),width:3840,height:2160,frameWidth:2,frameHeight:1,image:png});assert.equal(h.decoders.length,1);assert.equal(h.blobs[0].type,'image/png');
  const image={width:2,height:1,closed:0,close(){this.closed++;}};h.decoders[0].resolve(image);await settle();assert.equal(h.sent.filter(row=>row.type==='stream_feedback').length,0);
  await h.tick();assert.deepEqual(h.painted,[1]);assert.equal(h.sent.filter(row=>row.type==='stream_feedback').length,0);await h.tick();assert.equal(h.sent.find(row=>row.type==='stream_feedback').frameId,1);h.session.invalidateFrame();assert.equal(image.closed,1);
});
test('metadata mismatch is rejected before decode and mismatched decoder output is disposed',async t=>{
  const h=receiver(t);h.session.handle({...h.frame(1),width:3840,height:2160,frameWidth:3,frameHeight:1,image:'iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAIAAAB7QOjdAAAAD0lEQVR4nGPkVw1iYGAAAAJpAIhDetp6AAAAAElFTkSuQmCC'});await settle();assert.equal(h.decoders.length,0);assert.match(h.session.message,/dimensões/);assert.equal(h.sent.filter(row=>row.type==='stream_feedback').length,0);
  h.session.handle(h.frame(2));const wrong={width:1280,height:720,closed:0,close(){this.closed++;}};h.decoders[0].resolve(wrong);await settle();assert.equal(wrong.closed,1);assert.match(h.session.message,/confirmada/);assert.equal(h.sent.filter(row=>row.type==='stream_feedback').length,0);
});
test('legacy JPEG fallback dimensions remain displayable at the actual decoded size',async t=>{
  const h=receiver(t);h.session.handle({...h.frame(1),width:1920,height:1080});assert.equal(h.decoders.length,1);assert.equal(h.blobs[0].type,'image/jpeg');h.decoders[0].resolve(h.image());await settle();await h.tick();assert.equal(h.session.image.width,640);assert.deepEqual(h.painted,[1]);await h.tick();assert.equal(h.sent.find(row=>row.type==='stream_feedback').frameId,1);
});
test('oversized compressed headers cannot allocate a bitmap beyond the 4K pixel budget',async t=>{
  const h=receiver(t),large=Buffer.from([255,216,255,192,0,8,8,16,0,16,0,1]).toString('base64');
  h.session.handle({...h.frame(1),width:4096,height:4096,image:large});await settle();assert.equal(h.decoders.length,0);assert.match(h.session.message,/limite.*4K/);assert.equal(h.sent.filter(row=>row.type==='stream_feedback').length,0);
});
test('new captures release a held mouse before painting and replacement disposes the old bitmap',async t=>{
  const h=receiver(t),a=h.image(),b=h.image();h.session.handle(h.frame(1));h.decoders[0].resolve(a);await settle();await h.tick();
  h.session.dragHeld=true;h.session.handle(h.frame(2,'B'));h.decoders[1].resolve(b);await settle();await h.tick();
  assert.equal(h.session.dragHeld,false);assert.equal(a.closed,1);assert.equal(h.session.frame.captureId,'B');assert.ok(h.sent.some(x=>x.action==='release'));
});
test('ImageBitmap sources retain correct aspect ratio and ordinary frames do not rebuild all session controls',async t=>{
  const h=receiver(t);for(let id=1;id<=3;id++){
    h.session.handle(h.frame(id));h.decoders[id-1].resolve(h.image());await settle();await h.tick();await h.tick();
  }
  assert.deepEqual(h.session.layout(400,400),{left:0,top:50,width:400,height:300});
  assert.equal(h.changed.length,1);
});
test('browsers without usable ImageBitmap decode through Image and revoke their object URL',async t=>{
  const saved={};for(const key of ['Image','createImageBitmap'])saved[key]=Object.getOwnPropertyDescriptor(globalThis,key);
  const create=URL.createObjectURL,revoke=URL.revokeObjectURL,revoked=[];
  Object.defineProperty(globalThis,'createImageBitmap',{value:async()=>{throw new Error('unsupported');},configurable:true});
  Object.defineProperty(globalThis,'Image',{value:class{set src(value){this.naturalWidth=640;this.naturalHeight=480;queueMicrotask(()=>this.onload());}},configurable:true});
  URL.createObjectURL=()=> 'blob:jpeg-test';URL.revokeObjectURL=value=>revoked.push(value);
  try{const image=await decodeJpegImage(new Uint8Array([255,216]));assert.equal(image.naturalWidth,640);assert.deepEqual(revoked,['blob:jpeg-test']);}
  finally{URL.createObjectURL=create;URL.revokeObjectURL=revoke;for(const [key,descriptor]of Object.entries(saved)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}
});
