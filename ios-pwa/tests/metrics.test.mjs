import test from 'node:test';
import assert from 'node:assert/strict';
import { SessionTraffic, TRAFFIC_WINDOW_MS, trafficText } from '../metrics.mjs';
import { FramedChannel, packet, RTC_CHUNK } from '../protocol.mjs';
import { RemoteSession } from '../session.mjs';

class Channel extends EventTarget {
  constructor(){super();this.ordered=true;this.readyState='open';this.bufferedAmount=0;this.sent=[];}
  send(data){this.sent.push(new Uint8Array(data));}
  close(){this.readyState='closed';this.dispatchEvent(new Event('close'));}
  receive(data){const event=new Event('message');event.data=data;this.dispatchEvent(event);}
}
class Peer extends EventTarget {
  constructor(){super();this.connectionState='connected';this.iceConnectionState='connected';}
  close(){this.connectionState='closed';}
  state(value){this.connectionState=value;this.dispatchEvent(new Event('connectionstatechange'));}
}
async function flushed(wire){for(let n=0;n<1000&&wire.flushing;n++)await Promise.resolve();assert.equal(wire.flushing,false);}

test('session rates use decimal megabits and the 2.5-second observation window',()=>{
  let now=0;const traffic=new SessionTraffic(()=>now);
  traffic.start();traffic.add('rx',625000);traffic.add('tx',312500);
  assert.deepEqual(traffic.snapshot(),{rxBytes:625000,txBytes:312500,rxMbps:2,txMbps:1});
  now=1000;traffic.add('rx',312500);
  assert.equal(traffic.snapshot().rxMbps,3);
  now=TRAFFIC_WINDOW_MS+100;
  assert.equal(traffic.snapshot().rxMbps,1,'old traffic expires independently');
  now=3600;
  assert.deepEqual(traffic.snapshot(),{rxBytes:0,txBytes:0,rxMbps:0,txMbps:0});
  assert.equal(trafficText(traffic.snapshot()),'↓ 0,00 Mb/s · ↑ 0,00 Mb/s');
});

test('traffic is bounded, per-session and cleared across stop/restart',()=>{
  let now=0;const a=new SessionTraffic(()=>now),b=new SessionTraffic(()=>now);
  a.start();b.start();a.add('rx',100);b.add('tx',200);
  for(now=1;now<20000;now++){a.add('rx',1);a.add('tx',1);}
  assert.ok(a.buckets.size<=27,'packet volume cannot grow the sample list');
  assert.equal(b.snapshot().rxBytes,0);assert.equal(b.snapshot().txBytes,0,'idle sessions expire');
  a.stop();a.add('rx',500000);assert.equal(a.snapshot().rxBytes,0);
  a.start();assert.equal(a.snapshot().rxBytes,0);
  a.add('rx',100);assert.equal(a.snapshot().rxBytes,100);assert.equal(b.snapshot().rxBytes,0);
  for(const bytes of [-1,NaN,Infinity,1.5,Number.MAX_SAFE_INTEGER+1])a.add('rx',bytes);
  a.add('other',1000);assert.equal(a.snapshot().rxBytes,100);
});

test('transport meters exact successful chunks once, including framing and UTF-8',async()=>{
  const channel=new Channel(),counts=[],seen=[],wire=new FramedChannel(channel,value=>seen.push(value),assert.fail,(direction,bytes)=>counts.push({direction,bytes}));
  const message={type:'screen',image:'a'.repeat(RTC_CHUNK*3),computerName:'AliançaDX 🌍'};
  wire.send(message);await flushed(wire);
  assert.ok(channel.sent.length>3);
  assert.equal(counts.filter(x=>x.direction==='tx').reduce((n,x)=>n+x.bytes,0),packet(message).byteLength);
  for(const chunk of channel.sent)channel.receive(chunk);
  await wire.incoming;
  assert.deepEqual(seen,[message]);
  assert.equal(counts.filter(x=>x.direction==='rx').reduce((n,x)=>n+x.bytes,0),packet(message).byteLength);
  assert.equal(counts.length,channel.sent.length*2,'neither reconstructed JSON nor image decode is counted again');
  wire.shutdown();
});

test('queued, coalesced and failed sends do not inflate throughput',async()=>{
  const channel=new Channel(),counts=[],errors=[];channel.bufferedAmount=200000;
  const wire=new FramedChannel(channel,()=>{},error=>errors.push(error),(direction,bytes)=>counts.push({direction,bytes}));
  const first={type:'log',message:'first'},move={type:'input',action:'move',captureId:'A',x:1};
  wire.send(first);wire.send({...move,x:0});wire.send(move);
  await Promise.resolve();assert.deepEqual(counts,[],'queued is not sent');
  channel.bufferedAmount=0;channel.dispatchEvent(new Event('bufferedamountlow'));await flushed(wire);
  assert.equal(counts.reduce((n,x)=>n+x.bytes,0),packet(first).length+packet(move).length);
  const before=counts.length;channel.send=()=>{throw new Error('send rejected');};
  wire.send({type:'log',message:'fails'});await flushed(wire);
  assert.equal(counts.length,before);assert.equal(errors.length,1);
});

test('Blob reception and split headers count received bytes before reconstruction exactly once',async()=>{
  const channel=new Channel(),counts=[],seen=[],wire=new FramedChannel(channel,value=>seen.push(value),assert.fail,(direction,bytes)=>counts.push({direction,bytes}));
  const message={type:'state',sharing:true},data=packet(message);
  channel.receive(new Blob([data.subarray(0,2)]));await wire.incoming;
  assert.equal(seen.length,0);assert.deepEqual(counts,[{direction:'rx',bytes:2}]);
  channel.receive(data.subarray(2));await wire.incoming;
  assert.deepEqual(seen,[message]);assert.equal(counts.reduce((n,x)=>n+x.bytes,0),data.length);
  wire.shutdown();
});

test('the real RemoteSession updates its meter on health ticks and clears on network loss and close',async t=>{
  const original=Object.getOwnPropertyDescriptor(globalThis,'document');
  Object.defineProperty(globalThis,'document',{value:{hidden:false},configurable:true});
  let revealed=0;
  const updates=[],app={selected:null,changed(){},paint(){},phoneName(){return 'meter test';},trafficChanged(value){updates.push(value.id);},restoreCommands(){revealed++;}};
  const a=new RemoteSession(1,app),b=new RemoteSession(2,app),peer=new Peer(),channel=new Channel();app.selected=a;
  t.after(()=>{a.close();b.close();if(original)Object.defineProperty(globalThis,'document',original);else delete globalThis.document;});
  a.attach(peer,channel,'private-test');await flushed(a.wire);
  const received=packet({type:'bridge_state',state:'authorized',computerName:'PC'});
  channel.receive(received);await a.wire.incoming;await flushed(a.wire);
  assert.equal(a.traffic.snapshot().rxBytes,received.length);
  assert.ok(a.traffic.snapshot().txBytes>0);assert.equal(b.traffic.snapshot().rxBytes,0);
  a.handle({type:'error',message:'test remote error'});assert.equal(revealed,1,'remote errors restore commands');
  a.checkHealth();assert.ok(updates.includes(1));
  peer.state('disconnected');assert.equal(a.traffic.snapshot().rxBytes,0);assert.equal(a.traffic.active,false);
  peer.state('connected');assert.equal(a.traffic.active,true);assert.equal(a.traffic.snapshot().rxBytes,0);
  a.traffic.add('rx',1000);a.fail('test disconnect');assert.equal(a.traffic.snapshot().rxBytes,0);assert.equal(a.traffic.active,false);
});
