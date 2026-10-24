import test from 'node:test';
import assert from 'node:assert/strict';
import { SessionView, isolateControl } from '../session-view.mjs';
import { readFileSync } from 'node:fs';

const ready={session:1,ready:true};

test('commands hide only during a ready fullscreen session and reveal without leaving fullscreen',()=>{
  const changes=[],view=new SessionView(value=>changes.push(value));
  assert.equal(view.hide(ready),false);assert.equal(view.hidden,false);
  view.fullscreenTo(true);assert.equal(view.hide(ready),true);assert.equal(view.hidden,true);
  view.restore();assert.equal(view.hidden,false);assert.equal(view.fullscreen,true);
  view.hide(ready);view.fullscreenTo(false);
  assert.deepEqual(changes.at(-1),{fullscreen:false,hidden:false});
  view.fullscreenTo(true);assert.equal(view.hidden,false,'next fullscreen entry starts with visible commands');
});

for(const context of [
  {...ready,ready:false}, {...ready,promptActive:true}, {...ready,error:true}, {...ready,offline:true}
])test('unsafe or interrupted state cannot hide controls: '+JSON.stringify(context),()=>{
  const view=new SessionView();view.fullscreenTo(true);
  assert.equal(view.hide(context),false);assert.equal(view.hidden,false);
  view.hide(ready);view.reconcile(context);assert.equal(view.hidden,false);
});

test('a different session restores controls while an ordinary throughput refresh preserves the choice',()=>{
  const view=new SessionView();view.fullscreenTo(true);view.hide(ready);
  view.reconcile(ready);assert.equal(view.hidden,true);
  view.reconcile({...ready,session:2});assert.equal(view.hidden,false);
});

test('recovery handle captures and consumes its complete pointer lifecycle without forwarding remote input',()=>{
  const button=new EventTarget(),captured=[],trace=[];button.setPointerCapture=id=>captured.push(id);
  isolateControl(button,()=>trace.push('release'));
  for(const name of ['pointerdown','pointermove','pointerup','pointercancel','lostpointercapture','contextmenu']){
    const event=new Event(name,{cancelable:true});event.pointerId=7;
    button.dispatchEvent(event);assert.equal(event.defaultPrevented,true,name);
  }
  assert.deepEqual(captured,[7]);assert.deepEqual(trace,['release']);
});

test('shipped fullscreen policy removes every command group while retaining recovery and alerts',()=>{
  const css=readFileSync(new URL('../app.css',import.meta.url),'utf8'),html=readFileSync(new URL('../index.html',import.meta.url),'utf8'),app=readFileSync(new URL('../app.mjs',import.meta.url),'utf8');
  const rule=css.match(/([^{}]+\.focus-mode\.commands-hidden[^{}]+)\{display:none!important\}/);
  assert.ok(rule);
  for(const group of ['.mouse-actions','.remote-tools','.session-nav','.session-traffic','.tool-panel','.hide-commands'])assert.ok(rule[1].includes(group),group);
  assert.ok(!rule[1].includes('.show-commands'));assert.ok(!rule[1].includes('error-box'));assert.ok(!rule[1].includes('screen-placeholder'));
  assert.match(html,/id="show-commands"[^>]*aria-label="Mostrar comandos"/);
  assert.match(app,/fullscreenerror[^\n]+fullscreen\(false\)/);
  assert.match(app,/event\.key==='Escape'[^\n]+fullscreen\(false\)/);
  assert.match(app,/error\(message\)\{this\.restoreCommands\(\)/);
  assert.match(css,/\.show-commands\{[^}]*min-height:44px/);
  assert.match(css,/\.show-commands\{[^}]*safe-area-inset-bottom/);
});
