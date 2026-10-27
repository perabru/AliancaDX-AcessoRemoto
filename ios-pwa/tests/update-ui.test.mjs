import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {UpdatePanel,deployedVersion,prepareWorker,RELEASE_API} from '../updates.mjs';
const repo='https://github.com/perabru/AliancaDX-AcessoRemoto';
function release(version='7.6.1'){return {draft:false,prerelease:false,tag_name:'v'+version,html_url:repo+'/releases/tag/v'+version,assets:['AliancaDX_iOS_PWA_'+version+'.zip','AliancaDX_Host_Setup.exe'].map(name=>({name,state:'uploaded',size:100,browser_download_url:repo+'/releases/download/v'+version+'/'+name}))};}
function panel({mode='pwa',installed='7.4.0',version='7.6.1',deployed=version,active=false,confirm=true,accepted=true,protocol='https:',hostname='perabru.github.io'}={}){
  const fields=new Map(),calls={fetch:[],messages:[],reload:0,confirm:0,update:0},bus=new EventTarget();let busy=active;
  const worker={state:'installed',postMessage(message,ports){calls.messages.push(message);if(message.type==='AADX_UPDATE_INFO')ports[0].postMessage({version,cacheReady:true});else{ports[0].postMessage(accepted?{accepted:true}:{accepted:false,message:'Outra aba possui acesso aberto.'});if(accepted)setTimeout(()=>{worker.state='activated';bus.dispatchEvent(new Event('controllerchange'));},5);}}};
  const registration={waiting:worker,update:async()=>{calls.update++;},addEventListener(){},removeEventListener(){}};
  const instance=Object.create(UpdatePanel.prototype);Object.assign(instance,{mode,installedVersion:installed,active:()=>busy,busy:false,prepared:null,release:null,find:name=>{if(!fields.has(name))fields.set(name,{hidden:false,disabled:false,textContent:'',dataset:{}});return fields.get(name);},root:{querySelector:()=>({setAttribute(){}})},fetchImpl:async(url,options)=>{calls.fetch.push({url:String(url),options});return String(url)===RELEASE_API?new Response(JSON.stringify(release(version))):new Response("const PWA_APP_VERSION='"+deployed+"';");},serviceWorker:{getRegistration:async()=>registration,addEventListener:bus.addEventListener.bind(bus),removeEventListener:bus.removeEventListener.bind(bus)},window:{location:{protocol,hostname,href:'https://perabru.github.io/AliancaDX-AcessoRemoto/ios-pwa/',reload(){calls.reload++;}},confirm(){calls.confirm++;return confirm;}}});
  return {instance,fields,calls,worker,setActive:value=>{busy=value;}};
}
test('equal stable publication reports no update and never contacts a service worker',async()=>{
  const h=panel({version:'7.4.0'});await h.instance.check();assert.match(h.fields.get('status').textContent,/Nenhuma nova versão/);assert.equal(h.calls.update,0);assert.equal(h.calls.reload,0);assert.equal(h.fields.get('apply').hidden,true);
});
test('new release with Pages still old says wait for site without preparing or claiming installed',async()=>{
  const h=panel({deployed:'7.4.0'});await h.instance.check();assert.match(h.fields.get('status').textContent,/Aguarde a atualização do site/);assert.equal(h.fields.get('installed').textContent,'7.4.0');assert.equal(h.fields.get('apply').hidden,true);assert.equal(h.calls.update,0);assert.equal(h.calls.reload,0);
});
test('a manual check prepares an exact-version waiting worker without activation or reload',async()=>{
  const h=panel();await h.instance.check();assert.equal(h.calls.update,1);assert.equal(h.fields.get('apply').hidden,false);assert.equal(h.instance.prepared,h.worker);assert.equal(h.calls.messages.filter(m=>m.type==='AADX_APPLY_UPDATE').length,0);assert.equal(h.calls.reload,0);assert.equal(h.fields.get('installed').textContent,'7.4.0');assert.equal(h.fields.get('check').textContent,'Buscar novamente');
});
test('a connection opening late in preparation explains why update is disabled and a later close enables it',async()=>{
  const h=panel(),fetch=h.instance.fetchImpl;h.instance.fetchImpl=async(...args)=>{const response=await fetch(...args);if(String(args[0])!==RELEASE_API)h.setActive(true);return response;};await h.instance.check();assert.match(h.fields.get('status').textContent,/Encerre as conexões e transferências/);assert.equal(h.fields.get('status').dataset.state,'blocked');assert.equal(h.fields.get('apply').disabled,true);assert.equal(h.fields.get('check').textContent,'Buscar novamente');
  h.setActive(false);h.instance.refreshReady();assert.equal(h.fields.get('apply').disabled,false);assert.match(h.fields.get('status').textContent,/sem procurar outro arquivo/);assert.equal(h.fields.get('status').dataset.state,'ready');
});
test('a failed update check keeps a retry label instead of claiming a successful search',async()=>{
  const h=panel({deployed:'7.3.0'});await h.instance.check();assert.equal(h.fields.get('check').textContent,'Tentar novamente');assert.equal(h.fields.get('apply').hidden,true);
});
test('active sessions allow checking but block applying before asking or touching the worker',async()=>{
  const h=panel({active:true});await h.instance.check();await h.instance.apply();assert.match(h.fields.get('status').textContent,/Encerre todas as conexões/);assert.equal(h.calls.confirm,0);assert.equal(h.calls.messages.filter(m=>m.type==='AADX_APPLY_UPDATE').length,0);assert.equal(h.calls.reload,0);
});
test('Atualizar agora installs and reopens internally without a second confirmation or external download',async()=>{
  const h=panel({confirm:false});await h.instance.check();await h.instance.apply();assert.equal(h.calls.confirm,0);assert.equal(h.calls.reload,1);assert.equal(h.fields.get('progress').value,4);assert.equal(h.fields.get('download').hidden,true);assert.deepEqual(h.calls.messages.find(m=>m.type==='AADX_APPLY_UPDATE'),{type:'AADX_APPLY_UPDATE',version:'7.6.1',userConfirmed:true});
});
test('applying after sessions end reloads once and a double tap cannot reinstall or reload again',async()=>{
  const h=panel({active:true});await h.instance.check();h.setActive(false);await Promise.all([h.instance.apply(),h.instance.apply()]);await h.instance.apply();assert.equal(h.calls.confirm,0);assert.equal(h.calls.reload,1);assert.equal(h.calls.messages.filter(m=>m.type==='AADX_APPLY_UPDATE').length,1);assert.equal(h.fields.get('installed').textContent,'7.4.0');
});
test('a connection opening during the final deployment check blocks activation and reload',async()=>{
  const h=panel();await h.instance.check();const fetch=h.instance.fetchImpl;h.instance.fetchImpl=async(...args)=>{h.setActive(true);return fetch(...args);};await h.instance.apply();assert.match(h.fields.get('status').textContent,/Uma conexão está aberta/);assert.equal(h.calls.messages.filter(m=>m.type==='AADX_APPLY_UPDATE').length,0);assert.equal(h.calls.reload,0);
});
test('another tab refusing activation produces a visible error and leaves the current application open',async()=>{
  const h=panel({accepted:false});await h.instance.check();await h.instance.apply();assert.match(h.fields.get('status').textContent,/Outra aba/);assert.equal(h.calls.reload,0);
});
test('bridge shows actual configured Windows version and directs updates through the Host installer',async()=>{
  const h=panel({mode:'bridge',installed:'7.3.0'});await h.instance.check();assert.equal(h.fields.get('installed').textContent,'7.3.0');assert.match(h.fields.get('status').textContent,/Atualize o Hospedeiro no Windows/);assert.equal(h.fields.get('download').href,repo+'/releases/download/v7.6.1/AliancaDX_Host_Setup.exe');assert.equal(h.calls.update,0);assert.equal(h.calls.reload,0);
});
test('localhost embedded PWA never attempts SW refresh loops or reload',async()=>{
  const h=panel({protocol:'http:',hostname:'127.0.0.1'});await h.instance.check();assert.match(h.fields.get('status').textContent,/aplicativo local/);assert.equal(h.calls.update,0);assert.equal(h.calls.reload,0);assert.equal(h.fields.get('apply').hidden,true);
});
test('a missing Windows version remains unknown instead of borrowing the web build version',async()=>{
  const h=panel({mode:'bridge',installed:null});await h.instance.check();assert.equal(h.fields.get('installed').textContent,'Não foi possível identificar');assert.match(h.fields.get('status').textContent,/Versão oficial 7.6.1/);assert.equal(h.calls.reload,0);
});
test('site verification is bounded, rejects ambiguity and does not send credentials',async()=>{
  const calls=[];assert.equal(await deployedVersion({base:'https://example.test/pwa/',fetchImpl:async(url,options)=>{calls.push({url:String(url),options});return new Response("const PWA_APP_VERSION='7.6.1';");}}),'7.6.1');assert.equal(calls[0].options.credentials,'omit');assert.equal(calls[0].options.cache,'no-store');assert.equal(calls[0].options.redirect,'error');
  await assert.rejects(deployedVersion({base:'https://example.test/',fetchImpl:async()=>new Response("const PWA_APP_VERSION='7.6.1';\nconst PWA_APP_VERSION='7.6.1';")}),/Aguarde/);
  await assert.rejects(deployedVersion({base:'https://example.test/',fetchImpl:async()=>new Response('x'.repeat(512*1024+1))}),/tamanho/);
});
test('site verification aborts a stalled fetch with a retriable timeout',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});const pending=deployedVersion({base:'https://example.test/',fetchImpl:(_url,{signal})=>new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))))});t.mock.timers.tick(12000);await assert.rejects(pending,error=>error.code==='timeout');
});
test('worker preparation deadline also covers a stalled native registration.update promise',async()=>{
  await assert.rejects(prepareWorker({update:()=>new Promise(()=>{})},'7.6.1',{timeout:5}),error=>error.code==='worker');
});
test('a first registered worker already active can be prepared but is never implicitly applied',async()=>{
  let count=0;const worker={postMessage(message,ports){assert.equal(message.type,'AADX_UPDATE_INFO');ports[0].postMessage({version:'7.6.1',cacheReady:true});count++;}};
  const result=await prepareWorker({active:worker,update:async()=>{},addEventListener(){},removeEventListener(){}},'7.6.1',{timeout:200});assert.equal(result,worker);assert.ok(count>=1);
});
test('a stale waiting worker cannot hide the exact release currently installing',async()=>{
  const old={postMessage(_message,ports){ports[0].postMessage({version:'7.3.0',cacheReady:true});}},bus=new EventTarget(),next=new EventTarget();
  next.postMessage=(_message,ports)=>ports[0].postMessage({version:'7.6.1',cacheReady:true});
  const registration={waiting:old,installing:next,update:async()=>{setTimeout(()=>{registration.waiting=next;registration.installing=null;next.dispatchEvent(new Event('statechange'));},5);},addEventListener:bus.addEventListener.bind(bus),removeEventListener:bus.removeEventListener.bind(bus)};
  assert.equal(await prepareWorker(registration,'7.6.1',{timeout:200}),next);
});
test('initial and session update controls stay separate from the quality gear and hide with commands',()=>{
  const app=readFileSync(new URL('../app.mjs',import.meta.url),'utf8'),html=readFileSync(new URL('../index.html',import.meta.url),'utf8'),css=readFileSync(new URL('../app.css',import.meta.url),'utf8'),bridge=readFileSync(new URL('../bridge.mjs',import.meta.url),'utf8');
  assert.match(html,/id="check-updates"/);assert.match(css,/\.focus-mode\.commands-hidden \.update-session-toggle\{display:none!important\}/);assert.match(app,/const PWA_APP_VERSION='7.6.1';/);assert.match(app,/this.qualityOpen\|\|this.updatesOpen/);assert.match(bridge,/installedVersion:\(\)=>config\?\.version/);
});
