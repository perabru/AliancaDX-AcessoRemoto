import test from 'node:test';
import assert from 'node:assert/strict';
import { packet, FrameDecoder, FramedChannel, MAX_MESSAGE, RTC_CHUNK, fitBounds, pointAt, clickCommands, inputAllowed, validFrame, validFileName } from '../protocol.mjs';
import { RemoteSession, jpegSize } from '../session.mjs';

class Channel extends EventTarget {
  constructor(){super();this.ordered=true;this.maxRetransmits=null;this.maxPacketLifeTime=null;this.readyState='open';this.bufferedAmount=0;this.sent=[];}
  send(data){this.sent.push(new Uint8Array(data));}
  close(){this.readyState='closed';this.dispatchEvent(new Event('close'));}
  receive(data){const event=new Event('message');event.data=data;this.dispatchEvent(event);}
}
const tick=()=>new Promise(resolve=>setTimeout(resolve,5));
test('framing tolerates every split of UTF-8 header/body and multiple messages',()=>{
  const message={type:'log',message:'Olá, AliançaDX 👋'},data=packet(message);
  for(let split=0;split<=data.length;split++){const seen=[],parser=new FrameDecoder(value=>seen.push(value));parser.feed(data.subarray(0,split));parser.feed(data.subarray(split));parser.feed(packet({type:'state',sharing:true}));assert.deepEqual(seen,[message,{type:'state',sharing:true}]);}
});
test('invalid lengths fail before body allocation and decoder can be cleared',()=>{
  const parser=new FrameDecoder(()=>{}),header=new Uint8Array(4);
  for(const size of [0,MAX_MESSAGE+1,0xffffffff]){new DataView(header.buffer).setUint32(0,size,false);assert.throws(()=>parser.feed(header));assert.equal(parser.body,null);}
  assert.throws(()=>packet({type:'log',message:'x'.repeat(MAX_MESSAGE)}));
});
test('invalid UTF-8 and JSON shape are rejected',()=>{
  const parser=new FrameDecoder(()=>{});
  assert.throws(()=>parser.feed(new Uint8Array([0,0,0,1,255])));
  assert.throws(()=>new FrameDecoder(()=>{}).feed(new Uint8Array([0,0,0,2,91,93])));
});
test('ordered RTC transport fragments large payloads below 16KiB and retains clicks/releases',async()=>{
  const channel=new Channel(),wire=new FramedChannel(channel,()=>{},assert.fail);
  wire.send({type:'screen',image:'a'.repeat(150000)});
  wire.send({type:'input',action:'button',down:true});wire.send({type:'input',action:'button',down:false});wire.send({type:'input',action:'release'});
  for(let i=0;i<100&&wire.flushing;i++)await tick();
  assert.ok(channel.sent.length>9);assert.ok(channel.sent.every(data=>data.length<=RTC_CHUNK));
  const seen=[],parser=new FrameDecoder(message=>seen.push(message));for(const data of channel.sent)parser.feed(data);
  assert.deepEqual(seen.map(row=>row.type==='input'?row.down??row.action:row.type),['screen',true,false,'release']);wire.shutdown();
});
test('unreliable RTC channels are refused',()=>{
  const channel=new Channel();channel.maxRetransmits=0;assert.throws(()=>new FramedChannel(channel,()=>{},()=>{}));
});
test('backpressure waits for low watermark without dropping reliable packet',async()=>{
  const channel=new Channel();channel.bufferedAmount=200000;const wire=new FramedChannel(channel,()=>{},assert.fail);
  wire.send({type:'input',action:'release'});await tick();assert.equal(channel.sent.length,0);
  channel.bufferedAmount=0;channel.dispatchEvent(new Event('bufferedamountlow'));await tick();
  assert.equal(channel.sent.length,1);wire.shutdown();
});
test('adjacent pointer coalescing preserves capture and button ordering',async()=>{
  const channel=new Channel();channel.bufferedAmount=200000;const wire=new FramedChannel(channel,()=>{},assert.fail);
  wire.send({type:'log',message:'first'});
  wire.send({type:'input',action:'move',captureId:'A',x:0});
  wire.send({type:'input',action:'move',captureId:'A',x:1});
  wire.send({type:'input',action:'button',captureId:'A',down:true});
  wire.send({type:'input',action:'move',captureId:'A',x:.5});
  channel.bufferedAmount=0;channel.dispatchEvent(new Event('bufferedamountlow'));await tick();
  const seen=[],parser=new FrameDecoder(value=>seen.push(value));channel.sent.forEach(value=>parser.feed(value));
  assert.equal(seen.length,4);assert.equal(seen[1].x,1);assert.equal(seen[2].action,'button');assert.equal(seen[3].x,.5);wire.shutdown();
});
test('incoming strings and oversized incoming backlog close the channel',async()=>{
  const a=new Channel(),errors=[],wire=new FramedChannel(a,()=>{},error=>errors.push(error));
  a.receive('wrong transport');await tick();assert.equal(a.readyState,'closed');assert.equal(errors.length,1);wire.shutdown();
  const b=new Channel(),second=new FramedChannel(b,()=>{},()=>{});b.receive({byteLength:8*1024*1024+1});assert.equal(b.readyState,'closed');second.shutdown();
});
test('letterbox coordinates and double click use one exact capture/point',()=>{
  const bounds=fitBounds(400,400,1920,1080);assert.equal(pointAt(200,20,bounds),null);
  const point=pointAt(400,312.5,bounds);assert.deepEqual(point,{x:1,y:1});
  assert.deepEqual(pointAt(900,900,bounds,true),{x:1,y:1});
  const sequence=clickCommands('capture',point,'left',2);assert.equal(sequence.length,5);
  assert.deepEqual(sequence.slice(1).map(row=>row.down),[true,false,true,false]);
  assert.ok(sequence.every(row=>row.captureId==='capture'&&row.x===1&&row.y===1));
  assert.throws(()=>clickCommands('A',{x:NaN,y:1}));
});
test('only selected visible authorized session with current frame accepts input',()=>{
  const session={connected:true,frame:{},sharing:true,captureAvailable:true};
  assert.equal(inputAllowed(session,true,true),true);
  for(const [selected,visible]of[[false,true],[true,false]])assert.equal(inputAllowed(session,selected,visible),false);
  for(const field of ['localPaused','promptActive','stale'])assert.equal(inputAllowed({...session,[field]:true},true,true),false);
  assert.equal(inputAllowed({...session,captureAvailable:false},true,true),false);
});
test('already-open RTC channel sends exactly one private hello and waits for Host authorization',async()=>{
  const oldDocument=Object.getOwnPropertyDescriptor(globalThis,'document');
  Object.defineProperty(globalThis,'document',{value:{hidden:false},configurable:true});
  const app={selected:null,changed(){},paint(){},phoneName(){return 'iPhone de teste';}};
  class Peer extends EventTarget{constructor(){super();this.connectionState='connected';this.iceConnectionState='connected';}close(){this.connectionState='closed';}}
  const session=new RemoteSession(1,app),channel=new Channel(),peer=new Peer();app.selected=session;
  try{
    session.attach(peer,channel,'invitation-private-nonce');
    channel.dispatchEvent(new Event('open'));await tick();
    const seen=[],decoder=new FrameDecoder(value=>seen.push(value));channel.sent.forEach(data=>decoder.feed(data));
    assert.deepEqual(seen,[{type:'ios_connect',nonce:'invitation-private-nonce',name:'iPhone de teste'}]);
    assert.equal(session.connected,false);assert.equal(session.ready(),false);
    channel.receive(packet({type:'state',sharing:true}));await tick();
    assert.equal(session.sharing,false);
    channel.receive(packet({type:'bridge_state',state:'authorized',computerName:'PC autorizado'}));await tick();
    assert.equal(session.connected,true);assert.equal(session.name,'PC autorizado');assert.equal(session.ready(),false);
    const after=[];const parser=new FrameDecoder(value=>after.push(value));channel.sent.forEach(data=>parser.feed(data));
    assert.equal(after.filter(row=>row.type==='stream_ready').length,1);
    assert.equal(after.filter(row=>row.type==='ios_connect').length,1);
  }finally{
    session.close();
    if(oldDocument)Object.defineProperty(globalThis,'document',oldDocument);else delete globalThis.document;
  }
});
test('RTC failure is surfaced even before a data channel arrives',()=>{
  const app={selected:null,changed(){},paint(){},phoneName(){return 'iPhone';}};
  class Peer extends EventTarget{constructor(){super();this.connectionState='new';this.iceConnectionState='new';}close(){this.connectionState='closed';}}
  const session=new RemoteSession(1,app),peer=new Peer();
  try{
    session.observePeer(peer);peer.iceConnectionState='failed';peer.dispatchEvent(new Event('iceconnectionstatechange'));
    assert.equal(session.alive,false);assert.match(session.message,/WebRTC.*TURN/);
    assert.equal(session.title,'Conexão encerrada');
  }finally{session.close();}
});
test('frame metadata, JPEG dimensions and Windows filenames are bounded',()=>{
  assert.equal(validFrame({type:'screen',captureId:'A',width:1920,height:1080,image:'AAAA'}),true);
  assert.equal(validFrame({type:'screen',captureId:'A',width:1e9,height:1080,image:'AAAA'}),false);
  assert.deepEqual(jpegSize(new Uint8Array([255,216,255,192,0,8,8,1,224,2,128,1])),{width:640,height:480});
  assert.equal(jpegSize(new Uint8Array([255,216,255,192,255,255])),null);
  for(const name of ['ok.txt','relatorio.pdf','foto 1.jpg'])assert.equal(validFileName(name),true);
  for(const name of ['../secret','CON.txt','trailing.','a:b','a\nb',''])assert.equal(validFileName(name),false);
});
