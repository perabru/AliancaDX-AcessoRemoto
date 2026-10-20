import test from 'node:test';
import assert from 'node:assert/strict';
import {Adb,AdbDaemonTransport} from '@yume-chan/adb';
import {MaybeConsumable} from '@yume-chan/stream-extra';
import {makeAuthorizedClient,APK_SHA256,APK_SIZE} from '../../assets/vendor/aliancadx-adb.bundle.js';

// Real SDK packet dispatcher with an in-memory link; no USB hardware is used.
test('actual SDK pending OPEN without OKAY cannot hang the adapter after disconnect',async()=>{
  const prior=Object.getOwnPropertyDescriptor(globalThis,'crypto');
  const hashBytes=Uint8Array.from(APK_SHA256.match(/../g),hex=>Number.parseInt(hex,16));
  Object.defineProperty(globalThis,'crypto',{configurable:true,value:{subtle:{digest:async()=>hashBytes.buffer}}});
  try {
    let controller;let openSent;let opens=0;
    const opened=new Promise(resolve=>{openSent=resolve;});
    const connection={
      readable:new ReadableStream({start(c){controller=c;}}),
      writable:new MaybeConsumable.WritableStream({write(packet){opens++;openSent(packet);}}),
    };
    const transport=new AdbDaemonTransport({serial:'test-only',connection,version:0x01000001,maxPayloadSize:1024*1024,banner:'device::features=shell_v2',features:['shell_v2'],initialDelayedAckBytes:0});
    const realAdb=new Adb(transport);
    const adapterAdb={
      disconnected:realAdb.disconnected,
      getProp:async key=>({'ro.product.manufacturer':'Fixture','ro.product.model':'SDK test','ro.build.version.sdk':'33','ro.product.cpu.abilist64':'arm64-v8a'}[key]),
      sync:()=>realAdb.sync(),close:()=>realAdb.close(),subprocess:realAdb.subprocess,
      rm:async()=>assert.fail('No temp file exists when opening sync failed'),
    };
    const client=makeAuthorizedClient(adapterAdb);
    const installing=client.install({size:APK_SIZE,arrayBuffer:async()=>new ArrayBuffer(APK_SIZE)});
    const packet=await opened;
    assert.equal(new TextDecoder().decode(packet.payload),'sync:');
    controller.close();await realAdb.disconnected;
    await assert.rejects(installing,{code:'DISCONNECTED'});
    assert.equal(opens,1);
  } finally {
    if(prior)Object.defineProperty(globalThis,'crypto',prior);else delete globalThis.crypto;
  }
});
