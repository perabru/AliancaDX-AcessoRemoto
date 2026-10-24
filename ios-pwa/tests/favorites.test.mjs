import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { FavoriteStore, FAVORITES_KEY, FAVORITES_LIMIT, favoriteName } from '../favorites.mjs';

function storage(initial=null){
  return {raw:initial,writes:[],getItem(key){assert.equal(key,FAVORITES_KEY);return this.raw;},setItem(key,value){assert.equal(key,FAVORITES_KEY);this.raw=value;this.writes.push(value);}};
}
function store(memory=storage(),extra={}){let id=0;return new FavoriteStore({storage:memory,now:()=>1000,makeId:()=>`local-${++id}`,...extra});}

test('a reopened app loads only bounded PC labels, local IDs, last use and device name',()=>{
  const memory=storage(),first=store(memory);first.setDeviceName('iPhone da Ana');const row=first.save('PC escritório');
  const reopened=store(memory);
  assert.equal(reopened.persistent,true);assert.equal(reopened.deviceName,'iPhone da Ana');assert.deepEqual(reopened.favorites,[row]);
  const serialized=JSON.parse(memory.raw);
  assert.deepEqual(Object.keys(serialized).sort(),['deviceName','favorites','version']);
  assert.deepEqual(Object.keys(serialized.favorites[0]).sort(),['id','lastUsed','name']);
  const copied=reopened.favorites;copied[0].name='mutated';assert.equal(reopened.favorites[0].name,'PC escritório');
});

test('version zero migrates allowlisted metadata and drops capabilities and unknown fields',()=>{
  const memory=storage(JSON.stringify({version:0,computers:[{id:'old-1',name:'Meu PC',lastUsed:200,password:'private-password',sdp:'private-sdp',invite:'private-invite',url:'javascript:private'}],deviceName:'Meu celular',token:'private-token',nonce:'private-nonce'}));
  const migrated=store(memory);
  assert.deepEqual(migrated.favorites,[{id:'old-1',name:'Meu PC',lastUsed:200}]);
  assert.equal(JSON.parse(memory.raw).version,1);
  assert.ok(!/private|password|sdp|invite|token|nonce|javascript/.test(memory.raw));
});

test('unknown future schema is left untouched and operates in temporary memory',()=>{
  const raw=JSON.stringify({version:77,favorites:[{id:'x',name:'future',lastUsed:1}]});
  const memory=storage(raw),saved=store(memory);assert.equal(saved.persistent,false);assert.match(saved.message,/outra versão/);
  saved.save('PC temporário');saved.setDeviceName('Celular');assert.equal(saved.favorites.length,1);assert.equal(memory.raw,raw);assert.equal(memory.writes.length,0);
  assert.equal(saved.reset(),false,'future schemas cannot be replaced through corrupt-data recovery');
});

for(const raw of ['{broken','null','[]','x'.repeat(40000)])test('corrupt or oversized storage remains untouched until explicit recovery: '+raw.slice(0,12),()=>{
  const memory=storage(raw),saved=store(memory);assert.deepEqual(saved.favorites,[]);assert.match(saved.message,/não puderam ser lidos/);
  assert.equal(saved.persistent,false);assert.equal(saved.needsReset,true);assert.equal(memory.raw,raw);assert.equal(memory.writes.length,0);
  assert.equal(saved.save('PC recuperado').name,'PC recuperado');saved.setDeviceName('Celular temporário');
  assert.equal(memory.raw,raw,'automatic saves after authorization must not replace unreadable storage');
  assert.equal(store(memory).needsReset,true);assert.equal(memory.raw,raw,'reopening cannot clear the original');
  assert.equal(saved.reset(),true);assert.equal(saved.persistent,true);assert.equal(saved.needsReset,false);
  assert.equal(JSON.parse(memory.raw).favorites[0].name,'PC recuperado');assert.equal(JSON.parse(memory.raw).deviceName,'Celular temporário');
});

test('explicit recovery reports storage rejection and can be retried without losing temporary labels',()=>{
  const memory=storage('{broken'),saved=store(memory),write=memory.setItem;
  saved.save('PC temporário');memory.setItem=()=>{throw new Error('quota');};
  assert.equal(saved.reset(),false);assert.equal(saved.persistent,false);assert.equal(saved.needsReset,true);assert.equal(memory.raw,'{broken');
  memory.setItem=write;assert.equal(saved.reset(),true);assert.equal(JSON.parse(memory.raw).favorites[0].name,'PC temporário');
});

for(const value of [{version:1,favorites:'garbage'},{version:1},{version:0,computers:{invalid:true}}])test('recognized schema with unreadable list stays intact until explicit reset: '+JSON.stringify(value),()=>{
  const raw=JSON.stringify(value),memory=storage(raw),saved=store(memory);
  assert.equal(saved.persistent,false);assert.equal(saved.needsReset,true);assert.equal(memory.writes.length,0);assert.equal(memory.raw,raw);
  saved.save('PC temporário');saved.setDeviceName('Celular');assert.equal(memory.raw,raw);assert.equal(memory.writes.length,0);
  assert.equal(store(memory).needsReset,true);assert.equal(memory.raw,raw);
  assert.equal(saved.reset(),true);assert.equal(JSON.parse(memory.raw).favorites[0].name,'PC temporário');
});

test('storage getter denial, read failure and write quota failure retain safe in-memory operation',()=>{
  const denied=new FavoriteStore({storage:()=>{throw new Error('denied');},makeId:()=> 'a'});
  assert.equal(denied.persistent,false);assert.equal(denied.save('PC').name,'PC');assert.match(denied.message,/somente enquanto/);
  const unreadable=store({getItem(){throw new Error('blocked');}});assert.equal(unreadable.persistent,false);assert.equal(unreadable.save('PC2').name,'PC2');
  const quota=store({getItem(){return null;},setItem(){throw new Error('quota');}});quota.save('PC3');assert.equal(quota.persistent,false);assert.equal(quota.favorites[0].name,'PC3');
  assert.equal(quota.remove(quota.favorites[0].id),true);assert.deepEqual(quota.favorites,[]);
});

test('save deduplicates names, orders recent use, bounds history and removes without deleting device preference',()=>{
  let now=0;const memory=storage(),saved=store(memory,{now:()=>++now});saved.setDeviceName('Meu celular');
  const first=saved.save('PC 0');for(let n=1;n<=FAVORITES_LIMIT;n++)saved.save('PC '+n);
  assert.equal(saved.favorites.length,FAVORITES_LIMIT);assert.ok(!saved.favorites.some(row=>row.id===first.id));
  const before=saved.favorites.find(row=>row.name==='PC 4');saved.save('PC 4');
  assert.equal(saved.favorites[0].id,before.id);assert.equal(saved.favorites.length,FAVORITES_LIMIT);
  assert.equal(saved.remove(before.id),true);assert.equal(saved.remove(before.id),false);assert.equal(saved.deviceName,'Meu celular');
  assert.equal(store(memory).favorites.length,FAVORITES_LIMIT-1);
});

test('loaded IDs/names/last-use are validated and duplicate, malformed or unknown fields do not survive',()=>{
  const memory=storage(JSON.stringify({version:1,deviceName:{secret:'not-a-name'},favorites:[
    {id:'good',name:'PC bom',lastUsed:10,secret:'discard'},
    {id:'good',name:'duplicated-id',lastUsed:100},
    {id:'different',name:'PC bom',lastUsed:100},
    {id:'https://unsafe/?invite=secret',name:'bad id',lastUsed:1},
    {id:'negative',name:'Another PC',lastUsed:-1},
    {id:'secret',name:'AADXi1.zprivate-token',lastUsed:1},null,[],false
  ]}));
  const saved=store(memory);assert.deepEqual(saved.favorites,[{id:'good',name:'PC bom',lastUsed:10},{id:'negative',name:'Another PC',lastUsed:0}]);assert.equal(saved.deviceName,'');assert.ok(!memory.raw.includes('discard'));assert.ok(!memory.raw.includes('secret'));
});

test('sensitive invitations, credential-shaped text and SDP are refused as labels',()=>{
  const memory=storage(),saved=store(memory);
  for(const input of ['AADXi1.zprivate','MDA6.private','MDA6-private','MDA7-private','https://site/#invite=private','v=0\r\na=private','password=private','senha: private','{"password":"private"}','{"sdp":"v=0 private"}',"{'token':'private'}",{name:'PC',password:'private'}]){
    assert.equal(saved.save(input),null);assert.equal(saved.setDeviceName(input),'');
  }
  assert.deepEqual(saved.favorites,[]);assert.ok(!memory.raw.includes('private'));
});

test('real valid MDA6 codes cannot persist as PC/device names or survive legacy metadata migration',()=>{
  // Use the actual product generator and parser; this deterministic code is synthetic.
  const desktop=fileURLToPath(new URL('../../desktop/',import.meta.url));
  const code=execFileSync('python3',['-c','import sys; sys.path.insert(0,sys.argv[1]); import mesh; pairing=mesh.HostPairing(bytes(range(32)),54321,"AB"*32); assert mesh.parse_pairing(pairing.code)==pairing; print(pairing.code)',desktop],{encoding:'utf8'}).trim();
  assert.match(code,/^MDA6-[A-Z2-7]{114}$/);assert.equal(code.length,119);
  const memory=storage(),saved=store(memory);
  for(const name of [code,code.toLowerCase(),'PC '+code,code.replace('MDA6-','MDA7-'),code.replace('MDA6-','MDA6\u0000-')]){
    assert.equal(saved.save(name),null);assert.equal(saved.setDeviceName(name),'');
  }
  assert.ok(!memory.raw.includes(code.slice(0,60)));
  const legacy=storage(JSON.stringify({version:0,deviceName:code,computers:[{id:'secret-name',name:code,lastUsed:1},{id:code.slice(0,80),name:'PC válido',lastUsed:2},{id:'safe',name:'PC seguro',lastUsed:3}],invitation:code}));
  const migrated=store(legacy);assert.equal(migrated.deviceName,'');assert.deepEqual(migrated.favorites,[{id:'safe',name:'PC seguro',lastUsed:3}]);
  assert.ok(!legacy.raw.includes('MDA6-'));assert.ok(!legacy.raw.includes(code.slice(0,60)));
});

test('names remain plain bounded text and cannot add URL or executable fields',()=>{
  const memory=storage(),saved=store(memory);
  const text='javascript:alert(1) <img src=x onerror=alert(1)>';
  const row=saved.save(text);assert.equal(row.name,text);
  assert.equal('url' in row,false);assert.equal('href' in row,false);
  assert.equal(favoriteName('  PC\u202e\u0000 casa  '),'PC casa');assert.equal(favoriteName('a'.repeat(200)).length,80);
  assert.equal(saved.setDeviceName('b'.repeat(200)).length,60);
});

test('local ID failures and equal timestamps cannot lose newly saved PCs or create collisions',()=>{
  const saved=store(storage(),{makeId:()=> 'same-id',now:()=>0});
  for(let n=0;n<25;n++)saved.save('PC '+n);
  assert.equal(saved.favorites[0].name,'PC 24');assert.equal(new Set(saved.favorites.map(row=>row.id)).size,FAVORITES_LIMIT);
  const invalid=store(storage(),{makeId:()=> 'javascript:unsafe',now:()=>Infinity});assert.match(invalid.save('PC').id,/^pc-/);assert.equal(invalid.favorites[0].lastUsed,0);
});
