import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {RemoteSession} from '../session.mjs';
import {ScreenPresenter,canvasBacking} from '../stream-quality.mjs';
const near=(actual,expected)=>assert.ok(Math.abs(actual-expected)<1e-7,`${actual} != ${expected}`);
function receiver(t,{width=390,height=844,dpr=3,imageWidth=3840,imageHeight=2160}={}){
  t.mock.timers.enable({apis:['setInterval']});
  const originals={};for(const key of ['document','window'])originals[key]=Object.getOwnPropertyDescriptor(globalThis,key);
  Object.defineProperty(globalThis,'document',{value:{hidden:false},configurable:true});Object.defineProperty(globalThis,'window',{value:{devicePixelRatio:dpr},configurable:true});
  const sent=[],app={stage:{clientWidth:width,clientHeight:height},selected:null,paint(){},changed(){},inputChanged(){},viewScaleChanged(){},phoneName(){return 'QA';}};
  const session=new RemoteSession(1,app);app.selected=session;session.connected=true;session.sharing=true;session.image={width:imageWidth,height:imageHeight};session.frame={captureId:'physical-display',width:imageWidth,height:imageHeight};session.wire={send(message){sent.push(message);return true;},shutdown(){}};
  const stage={getBoundingClientRect:()=>({left:0,top:0,width,height})};
  const pointer=(type,x,y,id=1)=>session.pointer(type,{clientX:x,clientY:y,pointerId:id,button:0},stage);
  t.after(()=>{session.close();for(const [key,descriptor]of Object.entries(originals)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}});
  return {session,app,sent,width,height,dpr,pointer};
}
function canvas(){const calls=[],context=new Proxy({calls},{get(target,key){return key in target?target[key]:(...args)=>calls.push([key,...args]);},set(target,key,value){target[key]=value;return true;}});return {width:0,height:0,getContext:()=>context,context};}
test('initial Fit preserves the complete 4K image without forcing pixel real on a small phone',t=>{
  const h=receiver(t),bounds=h.session.layout(h.width,h.height);assert.equal(h.session.viewMode,'fit');near(bounds.width,390);assert.ok(bounds.height<844);assert.ok(h.session.viewScale().percent<100);
});
test('Fit fills a phone viewport even when adaptive quality sends a smaller image',t=>{
  const h=receiver(t,{width:844,height:390,dpr:3,imageWidth:480,imageHeight:270}),b=h.session.layout(844,390);
  near(b.width,390*16/9);near(b.height,390);near((b.left*3)%1,0);assert.ok(h.session.viewScale().percent>100);
  h.session.setPixelScale(1);const native=h.session.layout(844,390);near(native.width*3,480);near(native.height*3,270);assert.equal(h.session.viewScale().percent,100);
});
for(const dpr of [1,1.5,2,3,4])test('adaptive quality changes retain the same fitted size in portrait and fullscreen landscape at DPR '+dpr,t=>{
  const h=receiver(t,{dpr});
  for(const [width,height] of [[390,844],[844,390],[320,568],[720,1600]]){
    const expectedWidth=Math.min(width,height*16/9),expectedHeight=expectedWidth*9/16;
    for(const [iw,ih] of [[480,270],[720,405],[960,540],[1920,1080],[3840,2160],[480,270]]){
      h.session.image={width:iw,height:ih};h.session.frame.width=3840;h.session.frame.height=2160;
      const b=h.session.layout(width,height);near(b.width,expectedWidth);near(b.height,expectedHeight);
      assert.ok(Math.abs(b.left-(width-b.width)/2)<=1/dpr);assert.ok(Math.abs(b.top-(height-b.height)/2)<=1/dpr);
    }
  }
});
for(const dpr of [1,1.25,2,3,4])test('100% maps each decoded pixel to one backing pixel at DPR '+dpr,t=>{
  const h=receiver(t,{dpr});assert.equal(h.session.setPixelScale(1),true);const b=h.session.layout(h.width,h.height),backing=canvasBacking(h.width,h.height,3840,2160,1,dpr);near(b.width*backing.scaleX,3840);near(b.height*backing.scaleY,2160);near(b.left*backing.scaleX,Math.round(b.left*backing.scaleX));near(b.top*backing.scaleY,Math.round(b.top*backing.scaleY));assert.equal(h.session.viewScale().percent,100);assert.equal(h.session.viewScale().limited,false);
});
test('native magnification uses decoded JPEG dimensions rather than a larger remote-PC metadata field',t=>{
  const h=receiver(t,{imageWidth:1920,imageHeight:1080});h.session.frame.width=3840;h.session.frame.height=2160;h.session.setPixelScale(1);assert.equal(h.session.viewScale().width,1920);near(h.session.layout(390,844).width*3,1920);
});
test('zoom buttons offer readable 125/150/200/300/400% without changing remote stream settings',t=>{
  const h=receiver(t);h.session.dragHeld=true;const point={...h.session.point};for(const expected of [100,125,150,200,300,400]){assert.equal(h.session.stepPixelScale(1),true);assert.equal(h.session.viewScale().percent,expected);}assert.equal(h.session.stepPixelScale(1),false);assert.equal(h.session.dragHeld,false);assert.deepEqual(h.session.point,point);assert.ok(h.sent.some(m=>m.action==='release'));assert.equal(h.sent.some(m=>m.type==='stream_settings'||m.action==='button'&&m.down),false);h.session.stepPixelScale(-1);assert.equal(h.session.viewScale().percent,300);
});
test('Fit restores the overview and keeps another session magnification independent',t=>{
  const h=receiver(t),other=new RemoteSession(2,h.app);h.session.setPixelScale(2);assert.equal(other.viewMode,'fit');assert.equal(other.zoom,1);h.session.panX=90;h.session.panY=-120;h.session.fit();assert.equal(h.session.viewMode,'fit');assert.equal(h.session.zoom,1);assert.equal(h.session.panX,0);assert.equal(h.session.panY,0);other.close();
});
test('native view begins around the existing remote cursor, preserving input coordinates',t=>{
  const h=receiver(t,{width:390,height:300,dpr:1});h.session.point={x:.2,y:.3};h.session.setPixelScale(1);const b=h.session.layout(390,300);near(b.left+h.session.point.x*b.width,195);near(b.top+h.session.point.y*b.height,150);h.session.click('right');const commands=h.sent.filter(m=>m.type==='input'&&m.action!=='release');assert.ok(commands.length>0);for(const c of commands){assert.equal(c.captureId,'physical-display');assert.equal(c.x,.2);assert.equal(c.y,.3);}
});
test('two-finger zoom keeps the same source location under the moving midpoint and never presses a mouse',t=>{
  const h=receiver(t,{width:390,height:300,dpr:1});h.session.setPixelScale(1);const before=h.session.layout(390,300),anchorX=(200-before.left)/before.width,anchorY=(140-before.top)/before.height;h.pointer('down',150,140,1);h.pointer('down',250,140,2);h.pointer('move',270,160,2);const after=h.session.layout(390,300);assert.ok(Math.abs(after.left+anchorX*after.width-210)<=.5);assert.ok(Math.abs(after.top+anchorY*after.height-150)<=.5);assert.equal(h.sent.some(m=>m.action==='button'&&m.down),false);assert.equal(h.session.viewMode,'custom');
});
test('native zoom remains reachable past the old six-times-fit ceiling on low-DPR 4K views',t=>{
  const h=receiver(t,{width:320,height:568,dpr:1});h.session.setPixelScale(1);assert.ok(h.session.zoom>6);h.pointer('down',100,200,1);h.pointer('down',200,200,2);h.pointer('move',210,200,2);assert.ok(h.session.zoom>6);assert.ok(h.session.viewScale().percent>100);
});
test('pinching from native pixels on a small frame enlarges smoothly without jumping to Fit',t=>{
  const h=receiver(t,{width:390,height:300,dpr:3,imageWidth:480,imageHeight:270});h.session.setPixelScale(1);
  const before=h.session.layout(390,300);assert.ok(h.session.zoom<1);near(before.width,160);
  h.pointer('down',170,140,1);h.pointer('down',210,140,2);h.pointer('move',214,140,2);
  const after=h.session.layout(390,300);near(after.width,before.width*1.1);assert.ok(after.width<390);assert.equal(h.session.viewMode,'custom');
  assert.equal(h.sent.some(m=>m.action==='button'&&m.down),false);
});
test('Fit is smoothly scaled while explicit pixel real and enlarged pixels remain unfiltered',t=>{
  const h=receiver(t),base=canvas(),cursor=canvas(),presenter=new ScreenPresenter(base,cursor);presenter.paint(h.session,390,844,3);assert.equal(base.context.imageSmoothingEnabled,true);h.session.setPixelScale(1);presenter.paint(h.session,390,844,3);assert.equal(base.context.imageSmoothingEnabled,false);h.session.setPixelScale(2);presenter.paint(h.session,390,844,3);assert.equal(base.context.imageSmoothingEnabled,false);
});
test('Fit smooths an enlarged adaptive image without changing its decoded raster or retransmitting',t=>{
  const h=receiver(t,{imageWidth:480,imageHeight:270}),base=canvas(),cursor=canvas(),presenter=new ScreenPresenter(base,cursor),original=h.session.image;
  presenter.paint(h.session,390,844,3);assert.equal(base.context.imageSmoothingEnabled,true);assert.equal(h.session.image,original);
  const draw=base.context.calls.find(call=>call[0]==='drawImage');near(draw[4],390);near(draw[5],390*9/16);assert.equal(h.sent.length,0);
});
test('an explicit pixel-scale choice redraws with its own filter even when Fit has identical bounds',t=>{
  const h=receiver(t,{width:320,height:300,dpr:3,imageWidth:480,imageHeight:270}),base=canvas(),cursor=canvas(),presenter=new ScreenPresenter(base,cursor);
  presenter.paint(h.session,320,300,3);assert.equal(base.context.imageSmoothingEnabled,true);const fit={...h.session.bounds};
  h.session.setPixelScale(2);presenter.paint(h.session,320,300,3);assert.deepEqual(h.session.bounds,fit);assert.equal(base.context.imageSmoothingEnabled,false);
  assert.equal(base.context.calls.filter(call=>call[0]==='drawImage').length,2);
});
test('oversized physical viewports report the real graphics limit while remaining bounded',t=>{
  const h=receiver(t,{width:4000,height:3000,dpr:3});h.session.setPixelScale(1);assert.equal(h.session.viewScale().limited,true);const backing=canvasBacking(4000,3000,3840,2160,1,3);assert.ok(backing.width*backing.height<=3840*2160);
});
test('local controls in the actual PhoneApp apply view scale without closing or reconnecting the session',t=>{
  const h=receiver(t,{width:400,height:300,dpr:2});
  class Element{constructor(id){this.id=id;this.style={};this.attrs={};this.listeners={};this.dataset={};this.hidden=false;this.textContent='';}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,v){(this.listeners[k]||=[]).push(v);}focus(){}contains(){return false;}getBoundingClientRect(){return {right:390,bottom:60};}querySelectorAll(selector){return selector.includes('data-view-action')?buttons:[];}querySelector(){return null;}}
  const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);};const buttons=['fit','native','minus','plus'].map(action=>{const b=new Element(action);b.dataset.viewAction=action;return b;});
  const doc={getElementById:get,addEventListener(){},documentElement:{clientWidth:400}},window={devicePixelRatio:2,innerWidth:400,innerHeight:700,addEventListener(){}};
  const context={URLSearchParams,location:{hash:'',pathname:'/'},history:{replaceState(){}},window,document:doc};const source=readFileSync(new URL('../app.mjs',import.meta.url),'utf8').split('const app=new PhoneApp();')[0].replace(/^import[^\n]*\n/gm,'');vm.runInNewContext(source+'\nglobalThis.PhoneApp=PhoneApp;',context);
  const app=Object.create(context.PhoneApp.prototype);app.selected=h.session;app.sessions=new Map([[1,h.session]]);app.stage={clientWidth:400,clientHeight:300};app.qualityOpen=true;app.qualitySessionId=1;app.view={restore(){}};app.changed=()=>{};app.inputChanged=()=>{};app.trafficChanged=()=>{};app.requestPaint=()=>{};app.paint=()=>{};h.session.app=app;app.bindStreamSettings();app.viewScaleChanged(h.session);
  const click=action=>{const b=buttons.find(button=>button.dataset.viewAction===action);for(const listener of b.listeners.click||[])listener({preventDefault(){},stopPropagation(){}});};click('plus');assert.equal(h.session.viewScale().percent,100);assert.equal(app.qualityOpen,true);click('plus');assert.equal(h.session.viewScale().percent,125);click('native');assert.equal(app.qualityOpen,false);assert.equal(h.session.connected,true);assert.equal(h.session.viewScale().percent,100);assert.equal(h.sent.some(m=>m.type==='stream_settings'),false);
});
