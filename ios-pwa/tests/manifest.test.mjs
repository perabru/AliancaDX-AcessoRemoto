import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync,lstatSync,readdirSync} from 'node:fs';
import {join,resolve,sep,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import vm from 'node:vm';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const read=name=>readFileSync(join(root,name),'utf8');
const inventory=JSON.parse(read('public-assets.json'));
const runtime=[
 'app.css','app.mjs','assets/aliancadx-logo.png','assets/apple-touch-icon.png',
 'assets/icon-192.png','assets/icon-512.png','bridge.html','bridge.mjs','favorites.mjs','files.mjs',
 'index.html','manifest.webmanifest','metrics.mjs','protocol.mjs','session-view.mjs','session.mjs','signaling.mjs',
 'stream-quality.mjs','sw.js','vendor/qrcode-LICENSE','vendor/qrcode-provenance.json','vendor/qrcode.js'
];
const hash=(type,data)=>createHash(type).update(data).digest('hex');

test('public package inventory is complete, bounded and excludes local/private/test material',()=>{
 assert.deepEqual(inventory,[...runtime].sort());
 assert.equal(inventory.length,22);
 assert.equal(new Set(inventory).size,inventory.length);
 for(const name of inventory){
  assert.equal(typeof name,'string');
  assert.ok(!name.startsWith('/')&&!name.includes('\\')&&!name.split('/').includes('..'));
  assert.ok(!/[?#\x00]/.test(name));
  assert.ok(!/^(?:https?:|file:|data:)/i.test(name));
  assert.ok(!/\.(?:p12|pfx|jks|keystore|exe|apk|log|db|sqlite)$/i.test(name));
  assert.ok(!name.split('/').some(part=>part==='tests'||part.startsWith('.')));
  const path=resolve(root,name);
  assert.ok(path.startsWith(root+sep));
  assert.ok(existsSync(path),name);
  assert.ok(lstatSync(path).isFile()&&!lstatSync(path).isSymbolicLink(),name);
 }
});

test('every shipped runtime file is explicitly inventoried',()=>{
 const actual=[];
 function walk(folder,relative=''){
  for(const item of readdirSync(folder,{withFileTypes:true})){
   if(item.name==='tests'||item.name==='public-assets.json')continue;
   const name=relative?relative+'/'+item.name:item.name;
   if(item.isDirectory())walk(join(folder,item.name),name);
   else actual.push(name);
  }
 }
 walk(root);
 assert.deepEqual(actual.sort(),inventory);
});

test('PWA identity stays in its directory and icon metadata matches actual PNG dimensions',()=>{
 const manifest=JSON.parse(read('manifest.webmanifest'));
 assert.equal(manifest.id,'./');
 assert.equal(manifest.scope,'./');
 assert.equal(manifest.start_url,'./');
 assert.equal(manifest.display,'standalone');
 assert.equal(manifest.lang,'pt-BR');
 assert.equal(manifest.theme_color,'#007A50');
 assert.ok(manifest.name.includes('AliançaDX'));
 assert.deepEqual(manifest.icons.map(icon=>icon.sizes).sort(),['192x192','512x512']);
 for(const icon of manifest.icons){
  assert.ok(inventory.includes(icon.src));
  const bytes=readFileSync(join(root,icon.src));
  assert.equal(bytes.subarray(1,4).toString(),'PNG');
  const [width,height]=icon.sizes.split('x').map(Number);
  assert.equal(bytes.readUInt32BE(16),width);
  assert.equal(bytes.readUInt32BE(20),height);
  assert.equal(icon.type,'image/png');
 }
});

test('HTML documents have unique IDs, include all literal controller selectors and resolve local assets',()=>{
 for(const [html,script] of [['index.html','app.mjs'],['bridge.html','bridge.mjs']]){
  const source=read(html);
  assert.match(source,/<html lang="pt-BR">/);
  assert.match(source,/<meta name="referrer" content="no-referrer">/);
  const ids=[...source.matchAll(/\bid=["']([^"']+)["']/g)].map(match=>match[1]);
  assert.equal(ids.length,new Set(ids).size,html+' duplicate element ID');
  const selected=[...read(script).matchAll(/\$\(\s*(['"])([^'"]+)\1\s*\)/g)].map(match=>match[2]);
  assert.ok(selected.length>10,script+' selector coverage');
  for(const id of selected)assert.ok(ids.includes(id),html+' lacks '+id);
  for(const match of source.matchAll(/<(?:script|img|link)\b[^>]*>/g)){
   const tag=match[0];
   for(const attr of tag.matchAll(/\b(?:src|href)=["']([^"']+)["']/g)){
    const path=attr[1];
    assert.ok(!/^(?:https?:)?\/\//i.test(path),'Runtime assets must be local: '+path);
    assert.ok(inventory.includes(path),'Unlisted HTML asset: '+path);
    assert.ok(existsSync(join(root,path)),path);
   }
  }
 }
});

test('manual pairing and install controls are present with no automatic remote-access claim',()=>{
 const phone=read('index.html'),desktop=read('bridge.html');
 for(const id of ['invite-file','answer-panel','answer-token','copy-answer','share-answer','save-answer','answer-qr','install-guide','remote-canvas','double-click','right-click'])assert.ok(phone.includes('id="'+id+'"'),id);
 for(const id of ['phone-answer','answer-file','apply-answer','scan-answer','scan-video','save-invitation','ice-servers'])assert.ok(desktop.includes('id="'+id+'"'),id);
 assert.match(phone,/Adicionar à Tela de Início/);
 assert.match(phone,/iOS pode suspender conexões em segundo plano/);
 assert.match(phone,/resposta do iPhone/);
 assert.match(desktop,/Aplique a resposta/);
 assert.ok(!/fontes desconhecidas|liberar depuração|instalar automaticamente/i.test(phone));
});

function workerHarness(){
 const handlers={};
 const scope='https://perabru.github.io/AliancaDX-AcessoRemoto/ios-pwa/';
 const context={
  URL,Request,Response,Headers,Promise,
  fetch:async()=>new Response('public shell',{status:200}),
  caches:{open:async()=>({put:async()=>{}}),match:async()=>undefined,keys:async()=>[],delete:async()=>true},
  self:{location:new URL(scope+'sw.js'),addEventListener:(name,callback)=>{handlers[name]=callback;},clients:{claim:async()=>{}}}
 };
 vm.createContext(context);
 vm.runInContext(read('sw.js')+'\nglobalThis.testShell = SHELL;',context);
 return {context,handlers,scope};
}

test('service worker shell includes every phone module and no bridge or endpoint',()=>{
 const {context}=workerHarness();
 const shell=Array.from(context.testShell);
 for(const name of ['app.mjs','protocol.mjs','signaling.mjs','session.mjs','files.mjs','metrics.mjs','session-view.mjs','favorites.mjs','vendor/qrcode.js'])assert.ok(shell.includes('./'+name),name);
 assert.ok(!shell.some(path=>/bridge|\/api\/|[?#]/.test(path)));
 for(const path of shell)assert.ok(path==='./'||inventory.includes(path.slice(2)),path);
});

test('service worker never handles API traffic, cross-origin requests, credentials, query strings or fragments',()=>{
 const {handlers,scope}=workerHarness();
 function handled(path,{method='GET',auth=false}={}){
  let intercepted=false;
  const request={method,url:new URL(path,scope).href,headers:new Headers(auth?{Authorization:'Bearer private-capability'}:{})};
  handlers.fetch({request,respondWith:()=>{intercepted=true;},waitUntil:()=>{}});
  return intercepted;
 }
 assert.equal(handled('/api/events'),false);
 assert.equal(handled('bridge.html'),false);
 assert.equal(handled('app.css',{method:'POST'}),false);
 assert.equal(handled('app.css',{auth:true}),false);
 assert.equal(handled('app.css?token=private'),false);
 assert.equal(handled('index.html#invite=private'),false);
 assert.equal(handled('https://other.example/app.css'),false);
 assert.equal(handled('unlisted-file.json'),false);
});

test('service worker serves a public shell asset without persisting signaling content',async()=>{
 const {handlers,scope}=workerHarness();
 let pending;
 const waits=[];
 handlers.fetch({request:new Request(scope+'app.css'),respondWith:value=>{pending=value;},waitUntil:value=>waits.push(value)});
 assert.ok(pending);
 const response=await pending;
 assert.equal(response.status,200);
 assert.equal(await response.text(),'public shell');
 await Promise.all(waits);
});

test('QR generator and MIT notice match the pinned upstream blobs byte for byte',()=>{
 const provenance=JSON.parse(read('vendor/qrcode-provenance.json'));
 assert.equal(provenance.package,'qrcode-generator');
 assert.equal(provenance.version,'2.0.4');
 assert.equal(provenance.upstream_commit,'83b7e8fe3fddd3b0368dbafd6ce56995bd25e3c8');
 assert.equal(provenance.license,'MIT');
 assert.equal(provenance.modified,false);
 assert.equal(provenance.files.length,2);
 for(const row of provenance.files){
  const bytes=readFileSync(join(root,'vendor',row.file));
  assert.equal(hash('sha256',bytes),row.sha256,row.file);
  assert.equal(hash('sha1',Buffer.concat([Buffer.from('blob '+bytes.length+'\0'),bytes])),row.upstream_git_blob_sha1,row.file);
  assert.ok(row.source_url.startsWith('https://raw.githubusercontent.com/kazuhikoarase/qrcode-generator/'+provenance.upstream_commit+'/'));
 }
 const license=read('vendor/qrcode-LICENSE');
 assert.match(license,/Copyright \(c\) 2009 Kazuhiko Arase/);
 assert.match(license,/Permission is hereby granted/);
 const context={};vm.createContext(context);vm.runInContext(read('vendor/qrcode.js'),context);
 const qr=context.qrcode(0,'M');qr.addData('https://perabru.github.io/AliancaDX-AcessoRemoto/ios-pwa/#invite=public-test-fixture');qr.make();
 assert.ok(qr.getModuleCount()>=21);
 assert.equal(typeof qr.isDark(0,0),'boolean');
});
