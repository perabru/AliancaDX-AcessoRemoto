import test from 'node:test';
import assert from 'node:assert/strict';
import { RemoteSession, TOUCHPAD_GAIN } from '../session.mjs';

/** The real session and wire boundary; no browser-only clone of the input logic. */
function controller(t) {
  t.mock.timers.enable({apis:['setTimeout','setInterval']});
  const original=Object.getOwnPropertyDescriptor(globalThis,'document');
  const document={hidden:false};Object.defineProperty(globalThis,'document',{value:document,configurable:true});
  const sent=[],changes=[];
  const app={selected:null,changed(){},paint(){},inputChanged(session){changes.push(Boolean(session.dragHeld));},phoneName(){return 'Mousepad regression';}};
  const session=new RemoteSession(1,app);app.selected=session;
  session.connected=true;session.sharing=true;
  session.frame={captureId:'capture-A',width:400,height:300};session.image={naturalWidth:400,naturalHeight:300};
  session.lastFrameAt=performance.now();session.layout(400,300);
  session.wire={send(message){sent.push({...message});return true;},shutdown(){}};
  const view={left:20,top:30,width:400,height:300},stage={getBoundingClientRect(){return {...view};}};
  const pointer=(type,x=100,y=90,id=1,button=0)=>session.pointer(type,{pointerId:id,clientX:x+20,clientY:y+30,button},stage);
  t.after(()=>{session.close();if(original)Object.defineProperty(globalThis,'document',original);else delete globalThis.document;});
  return {session,app,document,view,sent,changes,pointer,tick:ms=>t.mock.timers.tick(ms),buttons:()=>sent.filter(x=>x.action==='button')};
}
const near=(actual,expected)=>assert.ok(Math.abs(actual-expected)<1e-10,`${actual} != ${expected}`);

test('touch, long hold and lift do not jump, click or start dragging',t=>{
  const h=controller(t),start={...h.session.point};
  h.pointer('down',350,240);h.tick(2000);h.pointer('up',350,240);
  assert.deepEqual(h.session.point,start);assert.deepEqual(h.sent,[]);assert.equal(h.session.pointers.size,0);
});

test('the full canvas moves the cursor by relative deltas with independent axes',t=>{
  const h=controller(t);h.pointer('down',20,20);h.pointer('move',100,50);h.pointer('up',100,50);
  near(h.session.point.x,.5+80/400*TOUCHPAD_GAIN);near(h.session.point.y,.5+30/300*TOUCHPAD_GAIN);
  assert.ok(h.sent.every(row=>row.action==='move'&&row.captureId==='capture-A'));assert.deepEqual(h.buttons(),[]);
});

test('lifting and reanchoring elsewhere continues from the cursor without teleporting',t=>{
  const h=controller(t);h.pointer('down',20,20);h.pointer('move',60,20);h.pointer('up',60,20);
  const before={...h.session.point},at=h.sent.length;
  h.pointer('down',300,200);assert.deepEqual(h.session.point,before);assert.equal(h.sent.length,at);
  h.pointer('move',260,200);h.pointer('up',260,200);
  near(h.session.point.x,.5);near(h.session.point.y,.5);assert.deepEqual(h.buttons(),[]);
});

test('a final up coordinate contributes its delta without requiring a move event',t=>{
  const h=controller(t);h.pointer('down');h.pointer('up',180,120);
  near(h.session.point.x,.75);near(h.session.point.y,.625);assert.deepEqual(h.buttons(),[]);
});

test('letterbox bars are mousepad surface rather than dead zones',t=>{
  const h=controller(t);h.session.image={naturalWidth:1600,naturalHeight:300};h.session.layout(400,300);
  assert.ok(h.session.bounds.top>50);h.pointer('down',15,10);h.pointer('move',55,10);h.pointer('up',55,10);
  near(h.session.point.x,.625);assert.deepEqual(h.buttons(),[]);
});

test('clamping at every edge still lets a reverse swipe move immediately',t=>{
  const h=controller(t);h.pointer('down');h.pointer('move',1000,1000);assert.deepEqual(h.session.point,{x:1,y:1});
  h.pointer('move',960,970);near(h.session.point.x,.875);near(h.session.point.y,.875);
  h.pointer('move',-1000,-1000);assert.deepEqual(h.session.point,{x:0,y:0});
  h.pointer('move',-960,-970);near(h.session.point.x,.125);near(h.session.point.y,.125);
  h.pointer('up',-960,-970);assert.deepEqual(h.buttons(),[]);
});

test('explicit drag presses once and allows finger lifts and reanchoring until Soltar',t=>{
  const h=controller(t);assert.equal(h.session.toggleDrag(),true);
  assert.deepEqual(h.sent.slice(-2).map(row=>row.action),['move','button']);
  assert.deepEqual(h.buttons().map(row=>row.down),[true]);assert.equal(h.session.dragHeld,true);
  h.pointer('down',20,20);h.pointer('move',60,20);h.pointer('up',60,20);
  const before={...h.session.point};h.pointer('down',250,200);assert.deepEqual(h.session.point,before);
  h.pointer('move',290,200);h.pointer('up',290,200);
  assert.deepEqual(h.buttons().map(row=>row.down),[true]);assert.equal(h.session.dragHeld,true);
  assert.equal(h.session.toggleDrag(),false);assert.equal(h.sent.at(-1).action,'release');assert.equal(h.session.dragHeld,false);
  assert.equal(h.changes.at(-1),false);
});

test('another finger can toggle Arrastar while the mousepad finger keeps moving',t=>{
  const h=controller(t);h.pointer('down',100,90);h.pointer('move',140,90);
  const point={...h.session.point};assert.equal(h.session.toggleDrag(),true);
  h.pointer('move',180,90);near(h.session.point.x,point.x+.125);assert.equal(h.session.dragHeld,true);
  assert.equal(h.session.toggleDrag(),false);h.pointer('move',200,90);h.pointer('up',200,90);
  near(h.session.point.x,point.x+.1875);assert.deepEqual(h.buttons().map(row=>row.down),[true]);
});

test('scroll releases a latched button without discarding the other finger anchor',t=>{
  const h=controller(t);h.pointer('down');h.session.toggleDrag();h.session.wheel(3);assert.equal(h.session.dragHeld,false);
  h.pointer('move',140,90);h.pointer('up',140,90);near(h.session.point.x,.625);
  assert.equal(h.sent.filter(row=>row.action==='wheel').length,1);
});

test('drag without an authorized current frame does not send button input',t=>{
  const h=controller(t);h.session.connected=false;
  assert.equal(h.session.toggleDrag(),false);assert.deepEqual(h.sent,[]);
});

test('failed drag press releases and cannot leave a false active indication',t=>{
  const h=controller(t);h.session.wire.send=message=>{h.sent.push({...message});return message.action!=='button';};
  assert.equal(h.session.toggleDrag(),false);assert.equal(h.session.dragHeld,false);assert.equal(h.sent.at(-1).action,'release');
});

test('pinch zoom never clicks and its remaining finger cannot resume movement until all lift',t=>{
  const h=controller(t);h.pointer('down',100,90,1);h.pointer('down',200,90,2);
  h.pointer('move',250,120,2);assert.ok(h.session.zoom>1);const before={...h.session.point};
  h.pointer('up',250,120,2);h.pointer('move',150,120,1);h.pointer('up',150,120,1);
  assert.deepEqual(h.session.point,before);assert.deepEqual(h.buttons(),[]);assert.equal(h.session.pointers.size,0);assert.equal(h.session.pinch,null);
  h.pointer('down',100,90);h.pointer('move',140,90);h.pointer('up',140,90);near(h.session.point.x,.625);
});

test('pinch releases the deliberate drag before changing the image',t=>{
  const h=controller(t);h.session.toggleDrag();h.pointer('down',100,90,1);h.pointer('down',200,90,2);
  assert.deepEqual(h.buttons().map(row=>row.down),[true,false]);assert.equal(h.session.dragHeld,false);
  h.pointer('move',250,90,2);h.pointer('up',250,90,2);h.pointer('up',100,90,1);
  assert.deepEqual(h.buttons().map(row=>row.down),[true,false]);
});

for(const held of [false,true])test(`pointer cancellation releases ${held?'explicit drag':'the touchpad'} and cannot leak a later click`,t=>{
  const h=controller(t);if(held)h.session.toggleDrag();h.pointer('down');
  const at=h.sent.length;h.pointer('cancel');h.tick(2000);h.pointer('up');
  assert.ok(h.sent.slice(at).some(row=>row.action==='release'));assert.equal(h.session.dragHeld,false);assert.equal(h.session.pointers.size,0);
  assert.equal(h.buttons().length,held?1:0);h.pointer('down');h.pointer('move',140,90);h.pointer('up',140,90);assert.equal(h.buttons().length,held?1:0);
});

for(const mode of ['release','deselect','invalidate','fit','pause','hidden','authorization','capture'])
  test(`${mode} interrupts an explicit drag and prevents input into a stale view`,t=>{
    const h=controller(t);h.session.toggleDrag();h.pointer('down');const at=h.sent.length;
    if(mode==='release')h.session.release();
    if(mode==='deselect')h.session.setSelected(false);
    if(mode==='invalidate')h.session.invalidateFrame();
    if(mode==='fit')h.session.fit();
    if(mode==='pause')h.session.handle({type:'state',sharing:false});
    if(mode==='hidden')h.document.hidden=true;
    if(mode==='authorization')h.session.connected=false;
    if(mode==='capture')h.session.frame={...h.session.frame,captureId:'capture-B'};
    h.pointer('move',180,130);h.pointer('up',180,130);
    assert.ok(h.sent.slice(at).some(row=>row.action==='release'));assert.deepEqual(h.buttons().map(row=>row.down),[true]);assert.equal(h.session.dragHeld,false);
  });

test('capture changes release a latched drag even with no finger on the screen',t=>{
  const h=controller(t);h.session.toggleDrag();h.session.frame={...h.session.frame,captureId:'capture-B'};
  h.pointer('down');assert.equal(h.session.dragHeld,false);assert.equal(h.sent.at(-1).action,'release');assert.equal(h.session.drag,null);
});

test('viewport movement or resize cancels the anchor before remapped input',t=>{
  const h=controller(t);h.session.toggleDrag();h.pointer('down');const at=h.sent.length;
  h.view.top+=40;h.view.width=300;h.pointer('move',140,120);h.pointer('up',140,120);
  assert.deepEqual(h.sent.slice(at).map(row=>row.action),['release']);assert.equal(h.session.dragHeld,false);
});

for(const [button,count]of[['left',1],['left',2],['right',1]])
  test(`explicit ${button} x${count} uses the persistent cursor and preserves the other finger on the mousepad`,t=>{
    const h=controller(t);h.pointer('down',120,120);h.pointer('move',160,120);const point={...h.session.point};
    h.session.click(button,count);
    assert.deepEqual(h.buttons().map(row=>[row.button,row.down]),Array.from({length:count},()=>[[button,true],[button,false]]).flat());
    assert.ok(h.buttons().every(row=>row.x===point.x&&row.y===point.y));
    const at=h.sent.length;h.pointer('move',200,160);h.pointer('up',200,160);assert.equal(h.sent.length,at+1);near(h.session.point.x,point.x+.125);near(h.session.point.y,point.y+40/300*TOUCHPAD_GAIN);assert.equal(h.buttons().length,count*2);
  });

test('scroll commands use the retained cursor and cap the wheel magnitude',t=>{
  const h=controller(t);h.session.wheel(100);h.session.wheel(-100);
  assert.deepEqual(h.sent.map(row=>[row.action,row.delta,row.x,row.y]),[['wheel',20,.5,.5],['wheel',-20,.5,.5]]);
});

test('invalid coordinates, untracked motion and non-left mouse presses do not move or click',t=>{
  const h=controller(t);h.pointer('move',100,100,9);h.pointer('up',100,100,9);
  h.pointer('down',-20,100);h.pointer('up',-20,100);h.pointer('down',100,100,1,2);h.pointer('up',100,100,1,2);
  assert.deepEqual(h.sent,[]);h.pointer('down');h.pointer('move',NaN,100);assert.equal(h.sent.at(-1).action,'release');assert.equal(h.session.pointers.size,0);
});
