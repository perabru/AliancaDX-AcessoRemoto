import test from 'node:test';
import assert from 'node:assert/strict';
import { RemoteSession, TOUCH_DRAG_DELAY, TOUCH_SLOP } from '../session.mjs';

/** Exercise the production RemoteSession pointer state machine, not a copy. */
function controller(t) {
  t.mock.timers.enable({apis:['setTimeout','setInterval']});
  const original=Object.getOwnPropertyDescriptor(globalThis,'document');
  const document={hidden:false};
  Object.defineProperty(globalThis,'document',{value:document,configurable:true});
  const sent=[];
  const app={selected:null,changed(){},paint(){},phoneName(){return 'Touch regression';}};
  const session=new RemoteSession(1,app);
  app.selected=session;
  session.connected=true;session.sharing=true;
  session.frame={captureId:'capture-A',width:400,height:300};
  session.image={naturalWidth:400,naturalHeight:300};
  session.lastFrameAt=performance.now();session.layout(400,300);
  session.wire={send(message){sent.push({...message});return true;},shutdown(){}};
  const view={left:20,top:30,width:400,height:300};
  const stage={getBoundingClientRect(){return {...view};}};
  const pointer=(type,x=100,y=90,id=1,button=0)=>session.pointer(type,{pointerId:id,clientX:x+20,clientY:y+30,button},stage);
  t.after(()=>{
    session.close();
    if(original)Object.defineProperty(globalThis,'document',original);else delete globalThis.document;
  });
  return {session,app,document,view,sent,pointer,tick:ms=>t.mock.timers.tick(ms),buttons:()=>sent.filter(x=>x.action==='button')};
}

test('a short touch clicks once on lift, even with normal finger jitter',t=>{
  const h=controller(t);
  h.pointer('down');h.tick(80);h.pointer('move',103,92);
  assert.deepEqual(h.buttons(),[],'touching must not hold the mouse');
  h.pointer('up',103,92);
  assert.deepEqual(h.buttons().map(x=>x.down),[true,false]);
  assert.ok(h.buttons().every(x=>x.captureId==='capture-A'&&x.x===103/400&&x.y===92/300));
  h.tick(TOUCH_DRAG_DELAY*2);
  assert.equal(h.buttons().length,2,'the cancelled long-press must not fire later');
});

test('moving before the hold positions the pointer without selecting or dragging',t=>{
  const h=controller(t);
  h.pointer('down');h.tick(50);h.pointer('move',100+TOUCH_SLOP+1,90);
  h.tick(TOUCH_DRAG_DELAY+100);h.pointer('move',190,160);h.pointer('up',190,160);
  assert.deepEqual(h.buttons(),[]);
  assert.ok(h.sent.some(x=>x.action==='move'&&x.x===190/400));
  assert.equal(h.session.drag,null);assert.equal(h.session.pointers.size,0);
});

test('returning to the start after an early swipe does not rearm a click or hold',t=>{
  const h=controller(t);
  h.pointer('down');h.pointer('move',140,90);h.pointer('move',100,90);
  h.tick(TOUCH_DRAG_DELAY+1);h.pointer('up');
  assert.deepEqual(h.buttons(),[]);
});

test('an up event that jumps beyond the tap slop cannot create an accidental click',t=>{
  const h=controller(t);
  h.pointer('down');h.tick(90);h.pointer('up',170,140);
  assert.deepEqual(h.buttons(),[]);
  h.tick(TOUCH_DRAG_DELAY);
  assert.deepEqual(h.buttons(),[]);
});

test('a stable half-second hold starts one deliberate drag and releases at the final point',t=>{
  const h=controller(t);
  h.pointer('down');h.tick(TOUCH_DRAG_DELAY-1);
  assert.deepEqual(h.buttons(),[]);
  h.tick(1);
  assert.deepEqual(h.sent.slice(0,2).map(x=>x.action),['move','button']);
  assert.deepEqual(h.buttons().map(x=>x.down),[true]);
  h.pointer('move',180,130);h.pointer('up',200,150);
  assert.deepEqual(h.buttons().map(x=>x.down),[true,false]);
  assert.deepEqual(h.sent.slice(-2).map(x=>[x.action,x.x,x.y]),[['move',.5,.5],['button',.5,.5]]);
  h.tick(TOUCH_DRAG_DELAY);
  assert.equal(h.buttons().length,2);
});

test('tiny stable movement preserves deliberate long-press and positions before pressing',t=>{
  const h=controller(t);
  h.pointer('down');h.tick(100);h.pointer('move',105,93);h.tick(TOUCH_DRAG_DELAY-100);
  const pressed=h.buttons()[0];assert.equal(pressed.down,true);
  assert.equal(pressed.x,105/400);assert.equal(pressed.y,93/300);
  const downAt=h.sent.findIndex(x=>x.action==='button'&&x.down);
  assert.equal(h.sent[downAt-1].action,'move');
  h.pointer('up',105,93);assert.deepEqual(h.buttons().map(x=>x.down),[true,false]);
});

test('two-finger zoom cancels a pending hold and never turns its remaining finger into a click',t=>{
  const h=controller(t);
  h.pointer('down',100,90,1);h.tick(100);h.pointer('down',200,90,2);
  h.pointer('move',250,120,2);h.tick(TOUCH_DRAG_DELAY+50);
  assert.ok(h.session.zoom>1);
  h.pointer('up',250,120,2);h.pointer('move',130,100,1);h.pointer('up',130,100,1);
  assert.deepEqual(h.buttons(),[]);
  assert.equal(h.session.pointers.size,0);assert.equal(h.session.pinch,null);
});

test('adding a second finger releases an intentional drag before zooming',t=>{
  const h=controller(t);
  h.pointer('down');h.tick(TOUCH_DRAG_DELAY);h.pointer('down',200,90,2);
  assert.deepEqual(h.buttons().map(x=>x.down),[true,false]);
  h.pointer('move',250,90,2);h.pointer('up',250,90,2);h.pointer('up');
  assert.deepEqual(h.buttons().map(x=>x.down),[true,false]);
});

for(const held of [false,true])test(`pointer cancellation ${held?'releases a drag':'cancels a pending tap'} without a later click`,t=>{
  const h=controller(t);
  h.pointer('down');if(held)h.tick(TOUCH_DRAG_DELAY);
  const from=h.sent.length;
  h.pointer('cancel');h.tick(TOUCH_DRAG_DELAY+20);h.pointer('up');
  assert.ok(h.sent.slice(from).some(x=>x.action==='release'));
  assert.equal(h.buttons().length,held?1:0);
  assert.equal(h.session.pointers.size,0);assert.equal(h.session.drag,null);
  h.pointer('down',160,120);h.pointer('up',160,120);
  assert.deepEqual(h.buttons().slice(-2).map(x=>x.down),[true,false]);
});

for(const mode of ['release','deselect','invalidate','fit','pause','hidden','authorization','capture'])
  test(`${mode} interrupts a held gesture and cannot leak a click into the next view`,t=>{
    const h=controller(t);
    h.pointer('down');h.tick(TOUCH_DRAG_DELAY);
    const at=h.sent.length;
    if(mode==='release')h.session.release();
    if(mode==='deselect')h.session.setSelected(false);
    if(mode==='invalidate')h.session.invalidateFrame();
    if(mode==='fit')h.session.fit();
    if(mode==='pause')h.session.handle({type:'state',sharing:false});
    if(mode==='hidden')h.document.hidden=true;
    if(mode==='authorization')h.session.connected=false;
    if(mode==='capture')h.session.frame={...h.session.frame,captureId:'capture-B'};
    h.pointer('move',180,130);h.pointer('up',180,130);h.tick(TOUCH_DRAG_DELAY);
    assert.ok(h.sent.slice(at).some(x=>x.action==='release'));
    assert.deepEqual(h.buttons().map(x=>x.down),[true]);
    assert.equal(h.session.drag,null);
  });

test('a changed capture cannot be pressed by an already scheduled long-press callback',t=>{
  const h=controller(t);
  h.pointer('down');h.session.frame={...h.session.frame,captureId:'capture-B'};
  h.tick(TOUCH_DRAG_DELAY);h.pointer('up');
  assert.deepEqual(h.buttons(),[]);
  assert.ok(h.sent.some(x=>x.action==='release'));
});

test('a moved viewport cancels its pending hold before the timer can press at a stale point',t=>{
  const h=controller(t);
  h.pointer('down');h.view.top+=40;h.tick(TOUCH_DRAG_DELAY);
  assert.deepEqual(h.buttons(),[]);
  assert.ok(h.sent.some(x=>x.action==='release'));
  assert.equal(h.session.drag,null);
});

test('a resized fullscreen viewport releases a held mouse before any remapped movement',t=>{
  const h=controller(t);
  h.pointer('down');h.tick(TOUCH_DRAG_DELAY);const at=h.sent.length;
  h.view.width=300;h.pointer('move',140,120);h.pointer('up',140,120);
  assert.deepEqual(h.sent.slice(at).map(x=>x.action),['release']);
  assert.deepEqual(h.buttons().map(x=>x.down),[true]);
});

for(const [button,count]of[['left',2],['right',1]])
  test(`explicit ${button} x${count} cancels the active finger candidate and completes the exact button sequence`,t=>{
    const h=controller(t);
    h.pointer('down',120,120);h.tick(100);h.session.click(button,count);
    assert.deepEqual(h.buttons().map(x=>[x.button,x.down]),Array.from({length:count},()=>[[button,true],[button,false]]).flat());
    h.tick(TOUCH_DRAG_DELAY*2);h.pointer('up',120,120);
    assert.equal(h.buttons().length,count*2);
    assert.equal(h.session.pointers.size,0);
  });

test('touches outside the remote image and non-left mouse buttons cannot create a click',t=>{
  const h=controller(t);
  h.pointer('down',-20,100);h.tick(TOUCH_DRAG_DELAY);h.pointer('up',-20,100);
  h.pointer('down',100,100,1,2);h.tick(TOUCH_DRAG_DELAY);h.pointer('up',100,100,1,2);
  assert.deepEqual(h.buttons(),[]);
});
