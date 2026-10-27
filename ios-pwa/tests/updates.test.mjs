import test from 'node:test';
import assert from 'node:assert/strict';
import {checkRelease,parseVersion,compareVersions,validateRelease,hasActiveSessions} from '../updates.mjs';

const repository='https://github.com/perabru/AliancaDX-AcessoRemoto';
const endpoint='https://api.github.com/repos/perabru/AliancaDX-AcessoRemoto/releases/latest';
function release(version='7.5.0'){
  const tag='v'+version;
  return {
    draft:false,prerelease:false,tag_name:tag,
    html_url:repository+'/releases/tag/'+tag,
    published_at:'2026-10-27T09:00:00Z',
    assets:['AliancaDX_iOS_PWA_'+version+'.zip','AliancaDX_Host_Setup.exe'].map(name=>({
      name,state:'uploaded',size:1234,browser_download_url:repository+'/releases/download/'+tag+'/'+name
    }))
  };
}
const response=(value,status=200,headers={})=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json',...headers}});
function rejectsRelease(value){
  assert.throws(()=>validateRelease(value));
}
function invalidVersion(value){
  let result;
  try{result=parseVersion(value);}catch{return;}
  assert.ok(!result,'accepted invalid version '+JSON.stringify(value));
}

test('version comparison uses SemVer numeric order rather than string or decimal order',()=>{
  assert.ok(parseVersion('7.4.0'));
  assert.ok(parseVersion('0.0.0'));
  assert.ok(compareVersions('7.10.0','7.9.99')>0);
  assert.ok(compareVersions('8.0.0','7.99.99')>0);
  assert.ok(compareVersions('7.4.0','7.4.1')<0);
  assert.equal(compareVersions('7.4.0','7.4.0'),0);
});

test('stable version parser rejects prereleases, leading zeroes, unsafe numbers and partial versions',()=>{
  for(const value of ['7.4','7.4.0-beta.1','7.4.0+build','07.4.0','7.04.0','7.4.00','7.4.0\n',' 7.4.0',
    '9007199254740992.4.0','7.-1.0',null,{},74])invalidVersion(value);
});

test('valid release exposes only exact public repository assets and stable tag',()=>{
  const result=validateRelease(release());
  assert.equal(result.version,'7.5.0');
  assert.equal(result.tag,'v7.5.0');
  assert.equal(result.releaseUrl,repository+'/releases/tag/v7.5.0');
  assert.equal(result.pwaUrl,repository+'/releases/download/v7.5.0/AliancaDX_iOS_PWA_7.5.0.zip');
  assert.equal(result.hostUrl,repository+'/releases/download/v7.5.0/AliancaDX_Host_Setup.exe');
});

test('draft and prerelease flags must be literal false',()=>{
  for(const field of ['draft','prerelease'])for(const value of [true,undefined,null,0,'false']){
    const item=release();item[field]=value;rejectsRelease(item);
  }
});

test('release tag must be a complete strict stable v-prefixed SemVer',()=>{
  for(const tag of ['7.5.0','v7.5','v07.5.0','v7.5.0-rc.1','v7.5.0+build','v7.5.0\n','v7.5.0/other','v7.5.0?download=1']){
    const item=release();item.tag_name=tag;rejectsRelease(item);
  }
});

test('release page must point to the exact repository and tag with no extra URL parts',()=>{
  for(const url of [
    repository+'/releases/tag/v7.4.0',
    repository+'/releases/tag/v7.5.0?next=private',
    repository+'/releases/tag/v7.5.0#fragment',
    'https://github.com/other/AliancaDX-AcessoRemoto/releases/tag/v7.5.0',
    'https://github.com.evil.example/perabru/AliancaDX-AcessoRemoto/releases/tag/v7.5.0',
    'http://github.com/perabru/AliancaDX-AcessoRemoto/releases/tag/v7.5.0',
    'javascript:alert(1)'
  ]){const item=release();item.html_url=url;rejectsRelease(item);}
});

test('ZIP download URL cannot escape repository, release tag, asset name or HTTPS origin',()=>{
  const expected=repository+'/releases/download/v7.5.0/AliancaDX_iOS_PWA_7.5.0.zip';
  for(const url of [
    expected+'?token=private',expected+'#download',
    expected.replace('v7.5.0/','v7.4.0/'),
    expected.replace('PWA_7.5.0.zip','PWA_7.4.0.zip'),
    expected.replace('perabru/','attacker/'),
    expected.replace('github.com/','github.com.evil.example/'),
    expected.replace('https://github.com/','https://user:password@github.com/'),
    expected.replace('https:','http:'),
    'data:application/zip;base64,AA=='
  ]){const item=release();item.assets[0].browser_download_url=url;rejectsRelease(item);}
});

test('host installer receives the same exact repository and tag checks as the PWA ZIP',()=>{
  for(const url of [
    repository+'/releases/download/v7.4.0/AliancaDX_Host_Setup.exe',
    repository+'/releases/download/v7.5.0/AliancaDX_Controller_Setup.exe',
    'https://evil.example/AliancaDX_Host_Setup.exe'
  ]){const item=release();item.assets[1].browser_download_url=url;rejectsRelease(item);}
});

test('both required assets must be present exactly once and uploaded with nonzero safe size',()=>{
  for(const index of [0,1]){
    const missing=release();missing.assets.splice(index,1);rejectsRelease(missing);
    const duplicate=release();duplicate.assets.push({...duplicate.assets[index]});rejectsRelease(duplicate);
    for(const state of ['new',undefined,null]){const item=release();item.assets[index].state=state;rejectsRelease(item);}
    for(const size of [0,-1,1.5,'1234',Number.MAX_SAFE_INTEGER+1]){const item=release();item.assets[index].size=size;rejectsRelease(item);}
  }
});

test('manual checker uses only the fixed public endpoint and does not send local capability or credentials',async()=>{
  const calls=[];
  const result=await checkRelease({installedVersion:'7.4.0',fetchImpl:async(url,options)=>{calls.push({url,options});return response(release());}});
  assert.equal(calls.length,1);
  assert.equal(String(calls[0].url),endpoint);
  const options=calls[0].options,headers=new Headers(options.headers);
  assert.equal(options.credentials,'omit');
  assert.equal(options.referrerPolicy,'no-referrer');
  assert.equal(options.cache,'no-store');
  assert.equal(headers.get('Accept'),'application/vnd.github+json');
  assert.equal(headers.has('Authorization'),false);
  assert.equal(headers.has('X-AADX-Generation'),false);
  assert.deepEqual([...headers.keys()],['accept']);
  assert.equal(result.installedVersion,'7.4.0');
  assert.equal(result.version,'7.5.0');
  assert.equal(result.newer,true);
});

test('equal or older published releases never offer a downgrade',async()=>{
  for(const version of ['7.4.0','7.3.0']){
    const result=await checkRelease({installedVersion:'7.4.0',fetchImpl:async()=>response(release(version))});
    assert.equal(result.newer,false);
    assert.equal(result.installedVersion,'7.4.0');
  }
});

test('rate limiting is reported as a retriable rate-limit state',async()=>{
  for(const status of [403,429])await assert.rejects(
    checkRelease({installedVersion:'7.4.0',fetchImpl:async()=>response({message:'rate limit'},status,{'X-RateLimit-Remaining':'0'})}),
    error=>error.code==='rate-limit'
  );
});

test('HTTP server errors are separate from release validation failures',async()=>{
  await assert.rejects(checkRelease({installedVersion:'7.4.0',fetchImpl:async()=>response({},503)}),error=>error.code==='http');
  await assert.rejects(checkRelease({installedVersion:'7.4.0',fetchImpl:async()=>response({draft:true})}),error=>error.code==='invalid');
});

test('malformed JSON and network failure are reported without a false new-version result',async()=>{
  await assert.rejects(checkRelease({installedVersion:'7.4.0',fetchImpl:async()=>new Response('{broken',{status:200})}),error=>error.code==='invalid');
  await assert.rejects(checkRelease({installedVersion:'7.4.0',fetchImpl:async()=>{throw new TypeError('Failed to fetch');}}),error=>error.code==='offline');
});

test('a stalled public release request aborts and reports timeout rather than hanging',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  let receivedSignal;
  const pending=checkRelease({installedVersion:'7.4.0',fetchImpl:async(_url,{signal})=>{
    receivedSignal=signal;
    return new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}));
  }});
  const rejected=assert.rejects(pending,error=>error.code==='timeout');
  assert.equal(receivedSignal.aborted,false);
  t.mock.timers.tick(12000);
  await rejected;
  assert.equal(receivedSignal.aborted,true);
});

test('oversized release responses are rejected both by header and by actual UTF-8 body size',async()=>{
  await assert.rejects(checkRelease({installedVersion:'7.4.0',fetchImpl:async()=>response(release(),200,{'Content-Length':String(512*1024+1)})}),error=>error.code==='invalid');
  const item=release();item.body='á'.repeat(262145);
  await assert.rejects(checkRelease({installedVersion:'7.4.0',fetchImpl:async()=>response(item)}),error=>error.code==='invalid');
});

test('empty alive session does not prevent an explicitly confirmed shell update',()=>{
  assert.equal(hasActiveSessions(new Map()),false);
  assert.equal(hasActiveSessions(new Map([[1,{alive:true,connected:false,connecting:false,fileBusy:false,peer:null,wire:null,answerToken:''}]])),false);
});

test('any live connection or pairing among multiple sessions blocks applying a PWA update',()=>{
  for(const field of ['connected','connecting','fileBusy','peer','wire','answerToken']){
    const sessions=new Map([[1,{alive:true}],[2,{alive:true}],[3,{alive:true,[field]:field==='answerToken'?'answer':field==='peer'||field==='wire'?{}:true}],[4,{alive:true}]]);
    assert.equal(hasActiveSessions(sessions),true,field);
  }
});

test('closed sessions with stale objects do not permanently block updates',()=>{
  assert.equal(hasActiveSessions(new Map([[1,{alive:false,connected:false,connecting:false,peer:{},wire:{},answerToken:'old'}],[2,{alive:true}]])),false);
});

test('an outstanding file transfer blocks applying even during connection teardown',()=>{
  assert.equal(hasActiveSessions(new Map([[1,{alive:false,fileBusy:true,peer:null,wire:null}]])),true);
});
