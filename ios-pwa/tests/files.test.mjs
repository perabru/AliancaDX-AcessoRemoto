import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto, createHash } from 'node:crypto';
import { MobileFiles, MAX_MOBILE_FILE } from '../files.mjs';
if(!globalThis.crypto)globalThis.crypto=webcrypto;
const wait=async(fn)=>{for(let i=0;i<300;i++){if(fn())return;await new Promise(resolve=>setTimeout(resolve,5));}throw new Error('Exchange timeout');};
const file=(bytes,name='arquivo.txt')=>({name,size:bytes.length,arrayBuffer:async()=>bytes.slice().buffer,slice:(a,b)=>new Blob([bytes.slice(a,b)])});
test('real wire upload receives consent, ordered chunks and verified SHA-256 before download',async()=>{
  const bytes=new Uint8Array(130001).map((_,i)=>i%251);const sentA=[],sentB=[];let received=null;
  const a=new MobileFiles(message=>{sentA.push(message);queueMicrotask(()=>b.handle(message));return true;},()=>{},async()=>true,()=>{});
  const b=new MobileFiles(message=>{sentB.push(message);queueMicrotask(()=>a.handle(message));return true;},()=>{},async()=>true,(blob,name)=>{received={blob,name};});
  try{
    await a.upload(file(bytes));await wait(()=>received!==null && !a.active);
    assert.deepEqual(new Uint8Array(await received.blob.arrayBuffer()),bytes);assert.equal(received.name,'arquivo.txt');
    assert.deepEqual(sentA.filter(row=>row.type==='file_chunk').map(row=>row.offset),[0,65536]);
    assert.ok(sentB.some(row=>row.type==='file_accept'));assert.ok(sentB.some(row=>row.type==='file_done'));
  }finally{a.close();b.close();}
});
test('recipient refusal cancels without transmitting chunks',async()=>{
  let chunks=0;const a=new MobileFiles(message=>{if(message.type==='file_chunk')chunks++;queueMicrotask(()=>b.handle(message));return true;},()=>{},async()=>true,()=>{});
  const b=new MobileFiles(message=>{queueMicrotask(()=>a.handle(message));return true;},()=>{},async()=>false,()=>{});
  try{await a.upload(file(new Uint8Array([1,2,3])));await wait(()=>!a.active);assert.equal(chunks,0);}finally{a.close();b.close();}
});
test('25MiB mobile limit is enforced before reading file contents',async()=>{
  let read=false;const exchange=new MobileFiles(()=>true,()=>{},async()=>true,()=>{});
  await assert.rejects(()=>exchange.upload({name:'large.bin',size:MAX_MOBILE_FILE+1,arrayBuffer:()=>{read=true;}}));assert.equal(read,false);exchange.close();
});
test('corrupted receive digest is discarded and never offered for saving',async()=>{
  const outgoing=[];let downloaded=false;const exchange=new MobileFiles(message=>{outgoing.push(message);return true;},()=>{},async()=>true,()=>{downloaded=true;});
  const id='12345678-1234-1234-1234-123456789abc';
  exchange.handle({type:'file_offer',id,name:'a.txt',size:3,sha256:'0'.repeat(64)});await wait(()=>outgoing.some(row=>row.type==='file_accept'));
  exchange.handle({type:'file_chunk',id,offset:0,data:'YWJj'});exchange.handle({type:'file_end',id});await wait(()=>!exchange.active);
  assert.equal(downloaded,false);assert.ok(outgoing.some(row=>row.type==='file_cancel'));exchange.close();
});
test('out-of-order chunks, changed IDs and malformed names cannot cross active transfer',async()=>{
  const outgoing=[],exchange=new MobileFiles(message=>{outgoing.push(message);return true;},()=>{},async()=>true,()=>{});
  const id='12345678-1234-1234-1234-123456789abc',sha=createHash('sha256').update('abc').digest('hex');
  exchange.handle({type:'file_offer',id,name:'../a',size:3,sha256:sha});assert.equal(exchange.active,null);
  exchange.handle({type:'file_offer',id,name:'a.txt',size:3,sha256:sha});await wait(()=>outgoing.some(row=>row.type==='file_accept'));
  exchange.handle({type:'file_chunk',id:'22345678-1234-1234-1234-123456789abc',offset:0,data:'YWJj'});assert.equal(exchange.active.offset,0);
  exchange.handle({type:'file_chunk',id,offset:1,data:'YWJj'});assert.equal(exchange.active,null);exchange.close();
});
test('zero-byte file completes with SHA-256 of empty input',async()=>{
  let got=null;
  const a=new MobileFiles(message=>{queueMicrotask(()=>b.handle(message));return true;},()=>{},async()=>true,()=>{});
  const b=new MobileFiles(message=>{queueMicrotask(()=>a.handle(message));return true;},()=>{},async()=>true,blob=>{got=blob;});
  try{await a.upload(file(new Uint8Array()));await wait(()=>got!==null&&!a.active);assert.equal(got.size,0);}finally{a.close();b.close();}
});
