import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {RemoteSession} from '../session.mjs';
import {BridgeCommandQueue} from '../stream-quality.mjs';

function receiver(t,preference='auto'){
  t.mock.timers.enable({apis:['setInterval']});
  const original=Object.getOwnPropertyDescriptor(globalThis,'document');Object.defineProperty(globalThis,'document',{value:{hidden:false},configurable:true});
  const sent=[],saved=[],changes=[];
  const app={selected:null,qualityOpen:false,preferredStreamMode:()=>preference,rememberStreamMode:m=>saved.push(m),streamSettingsChanged:s=>changes.push(s.id),changed(){},paint(){},phoneName:()=> 'Test'};
  const session=new RemoteSession(1,app);app.selected=session;session.wire={send(m){sent.push(m);return true;},shutdown(){}};
  const authorize=cap=>session.handle({type:'bridge_state',state:'authorized',streamSettingsVersion:cap});
  const frame=(mode='auto',cap=1)=>{session.sharing=true;session.frame={captureId:'A',width:640,height:480};session.image={naturalWidth:640,naturalHeight:480};session.lastFrameAt=performance.now();session.observeStreamSettings({streamSettingsVersion:cap,streamMode:mode});};
  t.after(()=>{session.close();if(original)Object.defineProperty(globalThis,'document',original);else delete globalThis.document;});
  return {session,app,sent,saved,changes,authorize,frame,settings:()=>sent.filter(m=>m.type==='stream_settings')};
}
test('default is automatic and a remembered choice waits for authorization and both capabilities',t=>{
  const h=receiver(t,'quality');assert.equal(h.session.streamModeApplied,'auto');assert.equal(h.settings().length,0);
  h.frame();assert.equal(h.settings().length,0);h.authorize(1);assert.equal(h.settings().length,0);
  h.frame();assert.equal(h.settings().length,1);assert.deepEqual(h.settings()[0],{type:'stream_settings',version:1,mode:'quality'});
  assert.equal(h.session.streamModeApplied,'auto');assert.equal(h.session.streamSettingsPending,true);assert.deepEqual(h.saved,[]);
});
test('an old Bridge cannot receive settings even when the new Host advertises support',t=>{
  const h=receiver(t,'quality');h.authorize(undefined);h.frame('auto');
  assert.equal(h.session.streamHostCapability,true);assert.equal(h.session.streamSettingsSupported,false);assert.equal(h.settings().length,0);
  assert.equal(h.session.setStreamMode('quality'),false);assert.deepEqual(h.saved,[]);
});
test('an old Host remains automatic and never receives unsupported commands',t=>{
  const h=receiver(t,'latency');h.authorize(1);h.frame('auto',null);
  assert.equal(h.session.streamModeApplied,'auto');assert.equal(h.session.streamSettingsSupported,false);assert.equal(h.settings().length,0);
});
for(const cap of [true,'1',2,{},null])test('non-literal capability is rejected: '+JSON.stringify(cap),t=>{
  const h=receiver(t,'quality');h.authorize(cap);h.frame();assert.equal(h.settings().length,0);
  h.authorize(1);h.frame('auto',cap);assert.equal(h.settings().length,0);
});
test('only a presented Host mode confirms the change and old frames cannot cause repeated sends',t=>{
  const h=receiver(t);h.authorize(1);h.frame();h.session.setStreamMode('quality');
  assert.equal(h.session.streamModeApplied,'auto');assert.equal(h.session.streamSettingsPending,true);assert.equal(h.sent.at(-2).action,'release');
  h.frame('auto');h.frame('auto');assert.equal(h.settings().length,1);
  h.frame('quality');assert.equal(h.session.streamModeApplied,'quality');assert.equal(h.session.streamSettingsPending,false);
});
test('a rapid change back to auto sends a cancellation even before the quality acknowledgement arrives',t=>{
  const h=receiver(t);h.authorize(1);h.frame();h.session.setStreamMode('quality');h.session.setStreamMode('auto');
  assert.deepEqual(h.settings().map(m=>m.mode),['quality','auto']);
  h.frame('quality');assert.equal(h.session.streamModeApplied,'quality');assert.equal(h.session.streamSettingsPending,true);assert.equal(h.settings().length,2);
  h.frame('auto');assert.equal(h.session.streamSettingsPending,false);
});
test('an open settings menu releases input and defers a remembered preference until it closes',t=>{
  const h=receiver(t,'quality');h.authorize(1);h.app.qualityOpen=true;h.frame();
  assert.equal(h.session.ready(),false);assert.equal(h.settings().length,0);h.session.click('left');assert.equal(h.sent.filter(m=>m.action==='button').length,0);
  h.app.qualityOpen=false;assert.equal(h.session.requestStreamMode(),true);assert.equal(h.settings().length,1);
});
test('invalid modes never reach storage or the wire and session choices stay independent',t=>{
  const h=receiver(t);h.authorize(1);h.frame();
  for(const mode of ['best','QUALITY','MDA7-private',null,{},1])assert.equal(h.session.setStreamMode(mode),false);
  assert.deepEqual(h.saved,[]);assert.equal(h.settings().length,0);
  const second=new RemoteSession(2,h.app);second.connected=true;second.sharing=true;second.frame={captureId:'B'};second.streamBridgeCapability=true;second.streamHostCapability=true;second.streamSettingsKnown=true;second.wire={send(){return true;},shutdown(){}};
  h.session.setStreamMode('quality');assert.equal(second.streamModePreference,'auto');assert.equal(second.streamModeApplied,'auto');
  second.close();
});
test('missing confirmation is visible without resending forever; a user can explicitly retry',t=>{
  const h=receiver(t);h.authorize(1);h.frame();h.session.setStreamMode('latency');h.session.streamSettingsSince=performance.now()-9000;
  h.session.checkHealth();assert.equal(h.session.streamSettingsPending,false);assert.equal(h.session.streamSettingsTimedOut,true);assert.equal(h.session.streamModeApplied,'auto');
  h.frame('auto');assert.equal(h.settings().length,1);h.session.setStreamMode('latency');assert.equal(h.settings().length,2);
  h.frame('latency');assert.equal(h.session.streamSettingsTimedOut,false);
});
test('failure clears capabilities and failed sends never pretend the mode was applied',t=>{
  const h=receiver(t);h.authorize(1);h.frame();h.session.wire.send=m=>{h.sent.push(m);return m.type!=='stream_settings';};
  assert.equal(h.session.setStreamMode('quality'),false);assert.equal(h.session.streamModeApplied,'auto');assert.equal(h.session.streamSettingsTimedOut,true);
  h.session.fail('offline');assert.equal(h.session.streamBridgeCapability,false);assert.equal(h.session.streamHostCapability,false);assert.equal(h.session.streamSettingsKnown,false);
});

function ui(t){
  t.mock.timers.enable({apis:['setInterval']});
  const doc={hidden:false,listeners:{},activeElement:null};
  class Element{
    constructor(id=''){this.id=id;this.style={};this.attrs={};this.dataset={};this.listeners={};this.children=[];this.parent=null;this.hidden=true;this.disabled=false;this.offsetHeight=330;this.textContent='';}
    setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,fn){(this.listeners[k]||=[]).push(fn);}contains(node){while(node){if(node===this)return true;node=node.parent;}return false;}
    querySelectorAll(selector){if(selector==='[data-stream-mode]')return options;if(selector==='button:not([disabled])')return [get('stream-quality-close'),...options].filter(x=>!x.disabled);return [];}
    querySelector(selector){if(selector==='.stream-quality-privacy')return get('privacy');if(selector.includes('aria-checked'))return options.find(x=>x.attrs['aria-checked']==='true'&&!x.disabled)||null;return null;}
    focus(){doc.activeElement=this;}getBoundingClientRect(){return {right:350,bottom:60};}
  }
  const elements=new Map();const get=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);};
  const options=['auto','latency','quality'].map(mode=>{const e=new Element(mode);e.dataset.streamMode=mode;e.parent=get('stream-quality-menu');e.disabled=false;return e;});
  doc.getElementById=get;doc.addEventListener=(name,fn)=>(doc.listeners[name]||=[]).push(fn);doc.documentElement={clientWidth:390};
  const window={innerWidth:390,innerHeight:844,listeners:{},addEventListener(name,fn){(this.listeners[name]||=[]).push(fn);}};
  const writes=[],history={state:null,pushes:0,backs:0,replaceState(){},pushState(value){this.state=value;this.pushes++;},back(){this.backs++;this.state=null;}};
  const storage={getItem(){return null;},setItem(...args){writes.push(args);}};
  const source=readFileSync(new URL('../app.mjs',import.meta.url),'utf8').split('const app=new PhoneApp();')[0].replace(/^import[^\n]*\n/gm,'');
  const context={URLSearchParams,location:{hash:'',pathname:'/ios-pwa/'},history,document:doc,window,localStorage:storage};vm.runInNewContext(source+'\nglobalThis.PhoneApp=PhoneApp;',context);
  const app=Object.create(context.PhoneApp.prototype);app.modeDefault='auto';app.modePersistent=true;app.qualityOpen=false;app.qualityHistory=false;app.stage=get('screen-stage');app.canvas=get('remote-canvas');app.canvas.parent=app.stage;
  app.view={restore(){},hidden:false,fullscreen:true};app.changed=s=>app.streamSettingsChanged(s);app.paint=()=>{};app.inputChanged=()=>{};app.trafficChanged=()=>{};
  const original=Object.getOwnPropertyDescriptor(globalThis,'document');Object.defineProperty(globalThis,'document',{value:doc,configurable:true});
  const sent=[];const session=new RemoteSession(1,app);app.selected=session;app.sessions=new Map([[1,session]]);session.connected=true;session.sharing=true;session.frame={captureId:'A'};session.streamBridgeCapability=true;session.streamHostCapability=true;session.streamSettingsKnown=true;session.wire={send(m){sent.push(m);return true;},shutdown(){}};app.bindStreamSettings();app.streamSettingsChanged(session);
  t.after(()=>{session.close();if(original)Object.defineProperty(globalThis,'document',original);else delete globalThis.document;});
  const event=(extra={})=>({target:null,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;},...extra});
  const fire=(element,name,extra={})=>{const e=event({target:element,...extra});for(const fn of element.listeners[name]||[])fn(e);return e;};
  return {app,session,sent,get,options,doc,window,history,writes,fire,event};
}
test('the real gear releases a held mouse, uses menu radios and applies only after closing',t=>{
  const h=ui(t);h.session.dragHeld=true;h.fire(h.get('stream-quality-toggle'),'click');assert.equal(h.app.qualityOpen,true);assert.equal(h.session.dragHeld,false);assert.equal(h.session.ready(),false);
  assert.equal(h.get('stream-quality-toggle').attrs['aria-expanded'],'true');assert.equal(h.get('stream-quality-menu').hidden,false);
  h.fire(h.options[2],'click');assert.equal(h.app.qualityOpen,false);assert.equal(h.session.streamSettingsPending,true);assert.equal(h.sent.at(-1).mode,'quality');assert.equal(h.sent.at(-2).action,'release');
  assert.match(h.get('stream-quality-state').textContent,/Aplicando Mais nitidez.*Em uso: Automático/);
  h.session.observeStreamSettings({streamSettingsVersion:1,streamMode:'quality'});assert.match(h.get('stream-quality-toggle').attrs['aria-label'],/Mais nitidez/);
  assert.deepEqual(h.writes.map(row=>Array.from(row)),[['aliancadx-stream-mode','quality']]);
});
test('Escape and Back close the real popover without leaving fullscreen',t=>{
  const h=ui(t);h.app.openStreamSettings();const esc=h.fire(h.get('stream-quality-menu'),'keydown',{key:'Escape'});assert.equal(h.app.qualityOpen,false);assert.equal(esc.prevented,true);assert.equal(h.app.view.fullscreen,true);assert.equal(h.history.backs,1);
  h.app.openStreamSettings();for(const fn of h.window.listeners.popstate)fn({});assert.equal(h.app.qualityOpen,false);assert.equal(h.history.backs,1);assert.equal(h.app.view.fullscreen,true);
});
test('an outside touch dismisses the real menu and cannot become a remote canvas click',t=>{
  const h=ui(t);h.app.openStreamSettings();const e=h.event({target:h.app.canvas});for(const fn of h.doc.listeners.pointerdown)fn(e);
  assert.equal(h.app.qualityOpen,false);assert.equal(e.prevented,true);assert.equal(e.stopped,true);
  const click=h.event({target:h.app.canvas});for(const fn of h.doc.listeners.click)fn(click);assert.equal(click.prevented,true);assert.equal(h.sent.filter(m=>m.action==='button').length,0);
});
test('unsupported peers show the update requirement, keep effective auto and disable unavailable radios',t=>{
  const h=ui(t);h.session.streamBridgeCapability=false;h.session.streamHostCapability=true;h.app.openStreamSettings();
  assert.match(h.get('stream-quality-state').textContent,/Atualize o Hospedeiro e o Conector/);assert.equal(h.options[1].disabled,true);assert.equal(h.options[2].disabled,true);assert.equal(h.options[0].attrs['aria-checked'],'true');
  h.fire(h.options[2],'click');assert.equal(h.sent.filter(m=>m.type==='stream_settings').length,0);
});
test('settings are ordinary ordered commands and never overtake release or key-up in the Bridge',async()=>{
  let release;const wait=new Promise(resolve=>{release=resolve;}),sent=[];
  const queue=new BridgeCommandQueue(async m=>{sent.push(m);if(sent.length===1)await wait;},assert.fail);
  queue.enqueue({type:'input',action:'move'});queue.enqueue({type:'input',action:'key',down:false});queue.enqueue({type:'input',action:'release'});queue.enqueue({type:'stream_settings',version:1,mode:'quality'});
  release();for(let i=0;i<10;i++)await Promise.resolve();assert.deepEqual(sent.map(m=>m.action||m.type),['move','key','release','stream_settings']);
});
test('fullscreen hiding covers the gear and popup in the current runtime package',()=>{
  const css=readFileSync(new URL('../app.css',import.meta.url),'utf8'),html=readFileSync(new URL('../index.html',import.meta.url),'utf8'),inventory=JSON.parse(readFileSync(new URL('../public-assets.json',import.meta.url),'utf8'));
  assert.match(css,/\.focus-mode\.commands-hidden \.stream-quality-control\{display:none!important\}/);
  for(const mode of ['auto','latency','quality'])assert.match(html,new RegExp('data-stream-mode="'+mode+'"'));
  assert.match(html,/aria-haspopup="menu"/);assert.equal(inventory.length,23);
});
