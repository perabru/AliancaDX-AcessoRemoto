import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { base64url, fromBase64url, createSignal, encodeSignal, decodeSignal, validateSignal, validateIce, iceSummary, iceSummaryText, requireIceCandidates, gatherIce, SIGNAL_LIMIT, SIGNAL_LIFETIME } from '../signaling.mjs';
if(!globalThis.crypto)globalThis.crypto=webcrypto;
const sdp='v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\nm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\na=fingerprint:sha-256 '+Array(32).fill('AA').join(':')+'\r\n';
test('compact and uncompressed invitations roundtrip without query parameters',async()=>{
  const offer=createSignal('offer',sdp,'Meu PC');
  for(const compact of [false,true]){const token=await encodeSignal(offer,{compact});assert.deepEqual(await decodeSignal(token,{type:'offer'}),offer);assert.deepEqual(await decodeSignal('https://perabru.github.io/AliancaDX-AcessoRemoto/ios-pwa/#invite='+token,{type:'offer'}),offer);}
});
test('answer matches original nonce and cannot be used for another offer',async()=>{
  const a=createSignal('offer',sdp,'A'),b=createSignal('offer',sdp,'B'),answer=createSignal('answer',sdp,'iPhone',a),token=await encodeSignal(answer);
  assert.equal((await decodeSignal(token,{type:'answer',nonce:a.nonce})).nonce,a.nonce);
  await assert.rejects(()=>decodeSignal(token,{type:'answer',nonce:b.nonce}));
  await assert.rejects(()=>decodeSignal(token,{type:'offer'}));
});
test('expired, future and overlong validity are refused',()=>{
  const offer=createSignal('offer',sdp,'PC'),now=Date.now();
  for(const value of [{...offer,expiresAt:now-1},{...offer,createdAt:now+61000},{...offer,expiresAt:offer.createdAt+SIGNAL_LIFETIME+1}])assert.throws(()=>validateSignal(value));
});
test('noncanonical encodings, missing fingerprint/application and malformed URL are refused',async()=>{
  assert.throws(()=>fromBase64url('AAAAAAAAAAAAAAAAAAAAAB'));
  const offer=createSignal('offer',sdp,'PC');
  assert.throws(()=>validateSignal({...offer,sdp:'v=0\r\nm=video 9\r\n'}));
  const token=await encodeSignal(offer);
  await assert.rejects(()=>decodeSignal('https://example.org/?secret=x#invite='+token));
  await assert.rejects(()=>decodeSignal('MDA6-EXISTING-NATIVE-INVITE'));
  assert.equal(fromBase64url(base64url(new Uint8Array([0,255,17]))).length,3);
});
test('compressed decompression bombs are rejected at decoded limit',async()=>{
  const stream=new Blob(['x'.repeat(SIGNAL_LIMIT+10)]).stream().pipeThrough(new CompressionStream('deflate'));
  const packed=new Uint8Array(await new Response(stream).arrayBuffer());
  await assert.rejects(()=>decodeSignal('AADXi1.z'+base64url(packed)));
});
test('ICE settings accept explicit TURN secrets only as private data, reject unsafe schemes',()=>{
  assert.deepEqual(validateIce([{urls:'stun:stun.cloudflare.com:3478'}]),[{urls:['stun:stun.cloudflare.com:3478']}]);
  assert.deepEqual(validateIce([{urls:['turn:relay.example.org:3478?transport=tcp'],username:'private',credential:'secret'}])[0].credential,'secret');
  for(const urls of ['javascript:alert(1)','https://relay.example.org','turn:user:pass@relay.example.org','stun:example.org\nx'])assert.throws(()=>validateIce([{urls}]));
});
test('ICE diagnostics count candidate types without exposing addresses or ports',()=>{
  const candidates=sdp+
    'a=candidate:1 1 udp 1 private.local 50000 typ host\r\n'+
    'a=candidate:2 1 udp 1 203.0.113.9 51000 typ srflx raddr 10.0.0.1 rport 50000\r\n'+
    'a=candidate:3 1 tcp 1 203.0.113.10 3478 typ relay tcptype passive\r\n';
  const summary=iceSummary(candidates);
  assert.deepEqual(summary,{host:1,srflx:1,prflx:0,relay:1,total:3});
  assert.equal(iceSummaryText(summary),'1 local(is), 1 pública(s)/STUN, 1 retransmitida(s)/TURN');
  assert.doesNotMatch(JSON.stringify(summary)+iceSummaryText(summary),/203\.0\.113|private\.local|50000/);
  assert.deepEqual(requireIceCandidates(candidates),summary);
});
test('finished ICE gathering with no candidates fails before a private invitation is shared',async()=>{
  const empty={iceGatheringState:'complete',localDescription:{sdp}};
  await assert.rejects(()=>gatherIce(empty),/0 candidatos ICE/);
  const withRoute={...empty,localDescription:{sdp:sdp+'a=candidate:1 1 udp 1 route.local 50000 typ host\r\n'}};
  assert.equal((await gatherIce(withRoute)).total,1);
  const abort=new AbortController();abort.abort();
  await assert.rejects(()=>gatherIce(withRoute,abort.signal),{name:'AbortError'});
  assert.throws(()=>requireIceCandidates(sdp),/0 candidatos ICE/);
});
