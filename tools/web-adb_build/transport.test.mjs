import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {makeAuthorizedClient,SessionCredentialStore,APK_SHA256,APK_SIZE,APK_PACKAGE} from '../../assets/vendor/aliancadx-adb.bundle.js';

function mockDevice({installFailure=false,installStderr='',wrongVersion=false,developerFailure=false,disconnectOnOff=false,usbOffFailure=false,debugReadFailure=false,devReadValue='0',debugReadValue='0',syncFailure=false,syncDisposeFailure=false,tempRemoveFailure=false}={}) {
  const commands=[];const stages=[];let uploaded=0;let removed=false;let resolveDisconnect;
  const disconnected=new Promise(resolve=>{resolveDisconnect=resolve;});
  const adb={
    disconnected,
    getProp: async key=>({'ro.product.manufacturer':'Samsung','ro.product.model':'Galaxy test','ro.build.version.sdk':'33','ro.product.cpu.abilist64':'arm64-v8a'}[key]||''),
    sync: async()=>({write:async({filename,file})=>{assert.equal(filename,'/data/local/tmp/aliancadx-62000-install.apk');if(syncFailure)throw Object.assign(new Error('Upload transport failed'),{code:'TRANSFER_FAILED'});for await(const chunk of file)uploaded+=chunk.length;},dispose:async()=>{if(syncDisposeFailure)throw new Error('Sync channel busy');}}),
    subprocess:{shellProtocol:{spawnWaitText:async args=>{
      commands.push(args);
      if(args[0]==='pm'&&args[1]==='install')return {exitCode:installFailure?1:0,stdout:installFailure?'Failure [INSTALL_FAILED_USER_RESTRICTED]':'Success\n',stderr:installStderr};
      if(args[0]==='pm'&&args[1]==='path')return {exitCode:0,stdout:'package:/data/app/test/base.apk\n',stderr:''};
      if(args[0]==='dumpsys')return {exitCode:0,stdout:'versionCode='+(wrongVersion?63000:62000)+' minSdk=26',stderr:''};
      if(args[0]==='settings'&&args[3]==='development_settings_enabled'&&developerFailure)return {exitCode:1,stdout:'',stderr:'Permission denied'};
      if(args[0]==='settings'&&args[1]==='get'){
        if(args[3]==='adb_enabled'&&debugReadFailure)throw Object.assign(new Error('Readback connection lost'),{code:'READ_FAILED'});
        return {exitCode:0,stdout:(args[3]==='development_settings_enabled'?devReadValue:debugReadValue)+'\n',stderr:''};
      }
      if(args[0]==='settings'&&args[3]==='adb_enabled'&&usbOffFailure)return {exitCode:1,stdout:'',stderr:'OEM\u0000 refused adb_enabled'};
      if(args[0]==='settings'&&args[3]==='adb_enabled'&&disconnectOnOff){resolveDisconnect();throw new Error('Connection closed');}
      return {exitCode:0,stdout:'',stderr:''};
    }}},
    rm:async file=>{assert.equal(file,'/data/local/tmp/aliancadx-62000-install.apk');if(tempRemoveFailure)throw new Error('Cannot remove temporary APK');removed=true;},
    close:async()=>{},
  };
  const client=makeAuthorizedClient(adb,{raw:{opened:false,forget:async()=>{}},credentials:{clear(){}},onStage:stage=>stages.push(stage)});
  return {client,adb,commands,stages,disconnect:()=>resolveDisconnect(),uploaded:()=>uploaded,removed:()=>removed};
}

const hashBytes=Uint8Array.from(APK_SHA256.match(/../g),hex=>Number.parseInt(hex,16));
const fixture={size:APK_SIZE,arrayBuffer:async()=>new ArrayBuffer(APK_SIZE)};
async function withFixtureHash(run) {
  const prior=Object.getOwnPropertyDescriptor(globalThis,'crypto');
  Object.defineProperty(globalThis,'crypto',{configurable:true,value:{subtle:{digest:async()=>hashBytes.buffer}}});
  try{return await run();}finally{if(prior)Object.defineProperty(globalThis,'crypto',prior);else delete globalThis.crypto;}
}

test('temporary key generation uses memory and clear zeros its bytes',async()=>{
  const store=new SessionCredentialStore(webcrypto);
  const key=await store.generateKey();assert.ok(key.buffer.length>1000);
  assert.equal([...store.iterateKeys()].length,1);store.clear();
  assert.equal([...store.iterateKeys()].length,0);assert.ok(key.buffer.every(byte=>byte===0));
});

test('clearing a session while RSA generation is pending never retains its result',async()=>{
  let release;
  const pending=new Promise(resolve=>{release=resolve;});
  const bytes=new Uint8Array([1,2,3]);
  const store=new SessionCredentialStore({subtle:{generateKey:async()=>pending,exportKey:async()=>bytes.buffer}});
  const generating=store.generateKey();store.clear();release({privateKey:{}});
  await assert.rejects(generating,{code:'CANCELLED'});
  assert.equal([...store.iterateKeys()].length,0);assert.ok(bytes.every(byte=>byte===0));
});

test('wrong file is rejected before any push or install',async()=>{
  const h=mockDevice();await assert.rejects(h.client.install(new Blob(['wrong'])),{code:'APK_MISMATCH'});
  assert.equal(h.uploaded(),0);assert.equal(h.commands.length,0);
  await assert.rejects(h.client.cleanupDeveloperOptions(),{code:'INSTALL_NOT_VERIFIED'});
});

test('successful install needs real stream byte count plus fixed package and version checks',async()=>withFixtureHash(async()=>{
  const h=mockDevice();const progress=[];
  const result=await h.client.install(fixture,value=>progress.push(value));
  assert.deepEqual(result,{installed:true,packageName:APK_PACKAGE,versionCode:62000,verified:true});
  assert.equal(h.uploaded(),APK_SIZE);assert.equal(h.removed(),true);
  assert.ok(h.commands.some(args=>args.join(' ')==='pm path '+APK_PACKAGE));
  assert.ok(h.commands.some(args=>args.join(' ')==='dumpsys package '+APK_PACKAGE));
  assert.equal(progress.at(-1).phase,'upload-complete');assert.equal(progress.at(-1).sent,APK_SIZE);
  await h.client.cleanupDeveloperOptions();
  assert.ok(h.commands.filter(args=>args[0]==='settings'&&args[1]==='put').every(args=>args.at(-1)==='0'));
  assert.equal(h.commands.filter(args=>args[0]==='settings'&&args[1]==='put').at(-1)[3],'adb_enabled');
}));

test('install refusal leaves developer cleanup guarded and removes only the temporary file',async()=>withFixtureHash(async()=>{
  const h=mockDevice({installFailure:true});await assert.rejects(h.client.install(fixture),{code:'INSTALL_FAILED'});
  assert.equal(h.removed(),true);await assert.rejects(h.client.cleanupDeveloperOptions(),{code:'INSTALL_NOT_VERIFIED'});
  assert.equal(h.commands.filter(args=>args[0]==='settings').length,0);
}));

test('pm success with the wrong version is not a verified install',async()=>withFixtureHash(async()=>{
  const h=mockDevice({wrongVersion:true});await assert.rejects(h.client.install(fixture),error=>error.code==='VERIFY_FAILED'&&error.installationMayHaveCompleted===true);
  await assert.rejects(h.client.cleanupDeveloperOptions(),{code:'INSTALL_NOT_VERIFIED'});
}));

test('OEM rejection or disconnection never becomes a false cleanup confirmation',async()=>withFixtureHash(async()=>{
  const h=mockDevice({developerFailure:true,disconnectOnOff:true});await h.client.install(fixture);
  const result=await h.client.cleanupDeveloperOptions();
  assert.equal(result.developerOptions,'unconfirmed');assert.equal(result.usbDebugging,'attempted');
  assert.equal(result.manualVerificationRequired,true);
  assert.equal(result.errors.length,2);assert.match(result.error,/Permission denied/);assert.match(result.error,/Connection closed/);
  assert.ok(h.commands.some(args=>args.join(' ')==='settings put global adb_enabled 0'));
}));

test('close and forget failures remain unconfirmed in the result',async()=>{
  const client=makeAuthorizedClient({disconnected:new Promise(()=>{}),close:async()=>{throw new Error('busy');}}, {raw:{opened:true,forget:async()=>{throw new Error('denied');}},credentials:{clear(){}}});
  const result=await client.close({forget:true});
  assert.equal(result.closed,false);assert.equal(result.forgotten,false);assert.equal(result.errors.length,2);
  assert.deepEqual(result.errors.map(entry=>entry.step),['usb-close','webusb-forget']);
  assert.equal(result.errors[0].code,'CLOSE_FAILED');assert.match(result.errors[0].message,/busy/);assert.match(result.errors[1].message,/denied/);
});

test('cleanup preserves both OEM refusals and original stderr without control characters',async()=>withFixtureHash(async()=>{
  const h=mockDevice({developerFailure:true,usbOffFailure:true});await h.client.install(fixture);
  const result=await h.client.cleanupDeveloperOptions();
  assert.equal(result.errors.length,2);assert.deepEqual(result.errors.map(entry=>entry.step),['developer-options-disable','usb-debugging-disable']);
  assert.match(result.errors[0].message,/Permission denied/);assert.match(result.errors[1].message,/OEM refused adb_enabled/);
  assert.equal(result.errors[1].code,'COMMAND_FAILED');assert.ok(!result.errors[1].message.includes('\u0000'));
  assert.equal(result.usbDebugging,'unconfirmed');assert.equal(result.developerOptions,'unconfirmed');
}));

test('cleanup records readback errors or values that do not confirm zero',async()=>withFixtureHash(async()=>{
  const h=mockDevice({devReadValue:'1',debugReadFailure:true});await h.client.install(fixture);
  const result=await h.client.cleanupDeveloperOptions();
  assert.deepEqual(result.errors.map(entry=>entry.step),['developer-options-readback','usb-debugging-readback']);
  assert.equal(result.errors[0].code,'VERIFY_UNCONFIRMED');assert.equal(result.errors[1].code,'READ_FAILED');
  assert.match(result.errors[1].message,/Readback connection lost/);
  assert.equal(result.developerOptions,'unconfirmed');assert.equal(result.usbDebugging,'attempted');
}));

test('failed transfer preserves every secondary cleanup error as a warning',async()=>withFixtureHash(async()=>{
  const h=mockDevice({syncFailure:true,syncDisposeFailure:true,tempRemoveFailure:true});
  await assert.rejects(h.client.install(fixture),{code:'TRANSFER_FAILED'});
  const warnings=h.stages.filter(stage=>stage.stage==='warning');
  assert.deepEqual(warnings.map(stage=>stage.step),['sync-close','temporary-apk-remove']);
  assert.match(warnings[0].message,/Sync channel busy/);assert.match(warnings[1].message,/Cannot remove temporary APK/);
  assert.equal(warnings[0].code,'SYNC_CLOSE_FAILED');assert.equal(warnings[1].code,'TEMP_CLEANUP_FAILED');
}));

test('unsupported permission forgetting stays explicit and unconfirmed',async()=>{
  const client=makeAuthorizedClient({disconnected:new Promise(()=>{}),close:async()=>{}},{raw:{opened:false},credentials:{clear(){}}});
  const result=await client.close({forget:true});
  assert.equal(result.closed,true);assert.equal(result.forgotten,false);assert.equal(result.errors[0].code,'FORGET_UNSUPPORTED');
});

test('unexpected disconnection notifies the UI but voluntary close does not',async()=>{
  for(const voluntary of [false,true]){
    const stages=[];let disconnect;
    const disconnected=new Promise(resolve=>{disconnect=resolve;});
    const client=makeAuthorizedClient({disconnected,close:async()=>{}},{onStage:stage=>stages.push(stage)});
    if(voluntary)await client.close();
    disconnect();await Promise.resolve();
    const notices=stages.filter(stage=>stage.stage==='disconnected');
    assert.equal(notices.length,voluntary?0:1);
    if(!voluntary)assert.match(notices[0].message,/não confirma desligamento da depuração/);
    await assert.rejects(client.model(),{code:'DISCONNECTED'});
  }
});

test('opening sync rejects promptly on disconnect and disposes a late session',async()=>withFixtureHash(async()=>{
  const h=mockDevice();let opening;let started;let disposed=0;let writes=0;
  const began=new Promise(resolve=>{started=resolve;});
  h.adb.sync=()=>{started();return new Promise(resolve=>{opening=resolve;});};
  const installing=h.client.install(fixture);await began;h.disconnect();
  await assert.rejects(installing,{code:'DISCONNECTED'});
  opening({dispose:async()=>{disposed++;},write:async()=>{writes++;}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(disposed,1);assert.equal(writes,0);assert.equal(h.commands.length,0);
}));

test('opening sync has a 20 second deadline and discards a late session',async()=>withFixtureHash(async()=>{
  const h=mockDevice();let opening;let disposed=0;let closes=0;let cleared=false;
  h.adb.sync=()=>new Promise(resolve=>{opening=resolve;});h.adb.close=async()=>{closes++;};
  const timers={setTimeout(fn,ms){assert.equal(ms,20000);queueMicrotask(fn);return 17;},clearTimeout(id){assert.equal(id,17);cleared=true;}};
  const client=makeAuthorizedClient(h.adb,{timers});
  await assert.rejects(client.install(fixture),{code:'SYNC_OPEN_TIMEOUT'});
  opening({dispose:async()=>{disposed++;}});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(disposed,1);assert.ok(closes>0);assert.equal(cleared,true);assert.equal(h.commands.length,0);
}));

test('opening sync responds to cancellation and preserves a late disposal error',async()=>withFixtureHash(async()=>{
  const h=mockDevice();const abort=new AbortController();let opening;let started;
  const began=new Promise(resolve=>{started=resolve;});
  h.adb.sync=()=>{started();return new Promise(resolve=>{opening=resolve;});};
  const stages=[];const client=makeAuthorizedClient(h.adb,{signal:abort.signal,onStage:stage=>stages.push(stage)});
  const installing=client.install(fixture);await began;abort.abort();
  await assert.rejects(installing,{code:'CANCELLED'});
  opening({dispose:async()=>{throw new Error('Late channel busy');}});await new Promise(resolve=>setImmediate(resolve));
  assert.ok(stages.some(stage=>stage.stage==='warning'&&stage.step==='late-sync-close'&&stage.message.includes('Late channel busy')));
  assert.equal(h.commands.length,0);
}));

test('installer stdout Failure is retained together with stderr warnings',async()=>withFixtureHash(async()=>{
  const h=mockDevice({installFailure:true,installStderr:'Warning: package verifier is checking this app'});
  await assert.rejects(h.client.install(fixture),cause=>{
    assert.equal(cause.code,'INSTALL_FAILED');assert.equal(cause.exitCode,1);
    assert.match(cause.message,/stdout: Failure \[INSTALL_FAILED_USER_RESTRICTED\]/);
    assert.match(cause.message,/stderr: Warning: package verifier/);return true;
  });
}));
