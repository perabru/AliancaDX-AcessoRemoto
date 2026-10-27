import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { FavoriteStore } from '../favorites.mjs';
import { isolateControl } from '../session-view.mjs';

/** Load the production PhoneApp class, without its browser bootstrap. */
function ui(){
  const elements=new Map();
  class Element {
    constructor(tag='div'){this.tagName=tag;this.children=[];this.attrs={};this.listeners={};this.hidden=false;this.value='';this.dataset={};this.classList={toggle(){}};}
    set innerHTML(value){throw new Error('favorite metadata must never enter innerHTML');}
    replaceChildren(){this.children=[];}
    append(...children){this.children.push(...children);}
    setAttribute(name,value){this.attrs[name]=value;}
    addEventListener(name,callback){this.listeners[name]=callback;}
    querySelector(){return new Element();}
    querySelectorAll(){return [];}
    scrollIntoView(){this.scrolled=true;}
    focus(){this.focused=true;}
  }
  const $=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const source=readFileSync(new URL('../app.mjs',import.meta.url),'utf8');
  const body=source.slice(0,source.indexOf('const app=new PhoneApp();')).replace(/^import[^\n]*\n/gm,'');
  let confirmation=false;
  const context={URLSearchParams,isolateControl,confirm:()=>confirmation,location:{hash:'',pathname:'/'},history:{replaceState(){}},window:{addEventListener(){}},document:{getElementById:$,createElement:tag=>new Element(tag),querySelectorAll:()=>[],addEventListener(){}}};
  vm.runInNewContext(body+'\nglobalThis.PhoneApp=PhoneApp;',context);
  let raw=null,count=0;const storage={getItem(){return raw;},setItem(key,value){raw=value;count++;}};
  let id=0;const saved=new FavoriteStore({storage,makeId:()=>`qa-${++id}`,now:()=>1000});
  const app=Object.create(context.PhoneApp.prototype);app.saved=saved;app.sessions=new Map();app.selected=null;
  app.canvas=new Element('canvas');
  app.drawTabs=()=>{};app.view={reconcile(){}};app.viewContext=()=>({});app.trafficChanged=()=>{};app.updateMonitors=()=>{};
  return {app,$,saved,getWrites:()=>count,setConfirmation:value=>{confirmation=value;}};
}
function session(id,name,connected=false){return {id,name,alive:true,connected,ready:()=>false,title:'Title',message:'Message',stage:'invite',fileText:'',fileProgress:0};}

test('actual PhoneApp remembers only authorized sessions once and removal stays removed during frames',()=>{
  const h=ui(),a=session(1,'PC da Ana');h.app.sessions.set(a.id,a);h.app.selected=a;
  h.app.changed(a);assert.equal(h.saved.favorites.length,0,'pre-authorization cannot save a PC');
  a.connected=true;h.app.changed(a);assert.equal(h.saved.favorites[0].name,'PC da Ana');assert.equal(a.favoriteId,h.saved.favorites[0].id);
  const writes=h.getWrites();h.app.changed(a);h.app.changed(a);assert.equal(h.getWrites(),writes,'frames cannot rewrite storage');
  h.saved.remove(a.favoriteId);h.app.changed(a);assert.equal(h.saved.favorites.length,0,'removal must not immediately re-add the active PC');
});

test('actual favorite cards render injection-like names only as text and never create navigation',()=>{
  const h=ui(),name='javascript:alert(1) <img src=x onerror=alert(1)>',row=h.saved.save(name);
  h.app.drawFavorites();const card=h.$('favorites-list').children[0];
  assert.equal(card.children[0].textContent,name);assert.equal(card.children[2].children[0].tagName,'button');
  assert.equal(card.children[2].children[0].textContent,'Reconectar');
  assert.ok(card.children.every(element=>!('href' in element)&&!('src' in element)));
  card.children[2].children[1].listeners.click();assert.equal(h.saved.favorites.length,0);assert.equal(h.$('favorites-list').children.length,0);
  assert.equal(row.name,name);
});

test('reconnect to an open PC selects that exact session without a new invitation or network call',()=>{
  const h=ui(),row=h.saved.save('PC aberto'),a=session(2,'PC aberto',true);a.favoriteId=row.id;h.app.sessions.set(a.id,a);
  const selected=[];h.app.select=id=>selected.push(id);h.app.add=()=>assert.fail('must reuse the live session');
  h.app.reconnectFavorite(row.id);assert.deepEqual(selected,[2]);assert.equal(h.$('session-panel').scrolled,true);assert.equal(h.$('invite').value,'');
});

test('closed favorite prepares a new manual pairing without restoring old invitation or authorization',()=>{
  const h=ui(),row=h.saved.save('PC fechado'),a=session(1,'Nova conexão');h.app.sessions.set(a.id,a);h.app.selected=a;
  a.status=(title,message,stage)=>{a.title=title;a.message=message;a.stage=stage;};
  h.app.fullscreen=value=>assert.equal(value,false);h.app.clearError=()=>{};
  h.app.reconnectFavorite(row.id);
  assert.equal(a.reconnectName,'PC fechado');assert.equal(a.connected,false);assert.equal(h.$('invite').value,'');
  assert.equal(h.$('reconnect-help').hidden,false);assert.equal(h.$('connect-title').textContent,'Reconectar a PC fechado');
  assert.match(a.message,/novo convite/);assert.match(a.message,/devolva a resposta/);assert.equal(h.$('invite').focused,true);
});

test('reconnect never replaces another live session or a draft and respects the four-session failure',()=>{
  const h=ui(),row=h.saved.save('PC salvo'),a=session(1,'Outro PC',true);h.app.selected=a;h.app.sessions.set(a.id,a);
  let attempts=0;h.app.add=()=>{attempts++;return undefined;};
  const errors=[];h.app.error=message=>errors.push(message);
  h.app.reconnectFavorite(row.id);assert.equal(attempts,1);assert.equal(a.name,'Outro PC');assert.equal(a.connected,true);
  a.connected=false;h.$('invite').value='unsent-private-draft';h.app.reconnectFavorite(row.id);
  assert.equal(attempts,1);assert.equal(h.$('invite').value,'unsent-private-draft');assert.match(errors[0],/Há um convite/);
});

test('real recovery button preserves unreadable favorites until its confirmation is accepted',()=>{
  const h=ui();let raw='{unreadable';
  h.app.saved=new FavoriteStore({storage:{getItem:()=>raw,setItem:(key,value)=>{raw=value;}},makeId:()=> 'temporary',now:()=>1000});
  h.app.saved.save('PC temporário');h.app.drawFavorites();h.app.bind();
  assert.equal(h.$('favorites-reset').hidden,false);
  h.setConfirmation(false);h.$('favorites-reset').listeners.click();assert.equal(raw,'{unreadable');assert.equal(h.app.saved.needsReset,true);
  h.setConfirmation(true);h.$('favorites-reset').listeners.click();assert.equal(JSON.parse(raw).favorites[0].name,'PC temporário');assert.equal(h.app.saved.needsReset,false);assert.equal(h.$('favorites-reset').hidden,true);
});
