/** Private, short-lived signaling stays in fragments/files, never a query or server. */
const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });
export const INVITE_PREFIX = 'AADXi1.';
export const SIGNAL_LIMIT = 128 * 1024;
export const SIGNAL_LIFETIME = 20 * 60 * 1000;
export const DEFAULT_ICE = Object.freeze([{ urls: ['stun:stun.cloudflare.com:3478'] }]);
const NONCE = /^[A-Za-z0-9_-]{22}$/;

export function base64url(bytes) {
  let text = '';
  for (let at = 0; at < bytes.length; at += 8192) text += String.fromCharCode(...bytes.subarray(at, at + 8192));
  return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}
export function fromBase64url(text) {
  if (typeof text !== 'string' || !/^[A-Za-z0-9_-]+$/.test(text) || text.length > SIGNAL_LIMIT * 2) throw new Error('Convite com formato inválido.');
  const raw = atob(text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - text.length % 4) % 4));
  const bytes = Uint8Array.from(raw, character => character.charCodeAt(0));
  if (base64url(bytes) !== text) throw new Error('Convite com codificação inválida.');
  return bytes;
}
async function boundedRead(stream, limit) {
  const reader = stream.getReader(); const chunks = []; let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      size += value.byteLength;
      if (size > limit) { await reader.cancel(); throw new Error('Convite excede o tamanho permitido.'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size); let at = 0;
  for (const chunk of chunks) { bytes.set(chunk, at); at += chunk.length; }
  return bytes;
}
export function validateSignal(value, { type, nonce, now = Date.now() } = {}) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || value.v !== 1 ||
      !['offer', 'answer'].includes(value.type) || type && value.type !== type ||
      typeof value.sdp !== 'string' || value.sdp.length < 20 || value.sdp.length > SIGNAL_LIMIT ||
      !value.sdp.startsWith('v=0') || !value.sdp.includes('m=application') ||
      !/^a=fingerprint:sha-256 [0-9a-fA-F:]+$/m.test(value.sdp.replace(/\r/g, '')) ||
      !NONCE.test(value.nonce) || nonce && value.nonce !== nonce ||
      !Number.isSafeInteger(value.createdAt) || !Number.isSafeInteger(value.expiresAt) ||
      value.createdAt > now + 60000 || value.createdAt < now - SIGNAL_LIFETIME ||
      value.expiresAt <= now || value.expiresAt <= value.createdAt ||
      value.expiresAt - value.createdAt > SIGNAL_LIFETIME ||
      typeof value.name !== 'string' || value.name.length > 80 || /[\x00-\x1f\x7f]/.test(value.name)) {
    throw new Error(nonce ? 'Resposta inválida, expirada ou pertencente a outro convite.' : 'Convite inválido ou expirado. Gere um novo convite no computador.');
  }
  fromBase64url(value.nonce); // Require canonical 128-bit encoding as well as length.
  if (value.iceServers != null) validateIce(value.iceServers);
  return value;
}
export function validateIce(servers) {
  if (!Array.isArray(servers) || servers.length < 1 || servers.length > 4) throw new Error('Informe de um a quatro servidores ICE.');
  return servers.map(server => {
    if (!server || typeof server !== 'object') throw new Error('Servidor ICE inválido.');
    const urls = Array.isArray(server.urls) ? server.urls : [server.urls];
    if (!urls.length || urls.length > 4 || urls.some(url => typeof url !== 'string' || url.length > 512 || !/^(stun|stuns|turn|turns):[A-Za-z0-9.[\]:_-]+(?:\?transport=(udp|tcp))?$/.test(url))) throw new Error('Use endereços stun:, stuns:, turn: ou turns: válidos.');
    const result = { urls };
    if (server.username != null || server.credential != null) {
      if (typeof server.username !== 'string' || server.username.length > 256 || typeof server.credential !== 'string' || server.credential.length > 1024) throw new Error('Credenciais TURN inválidas.');
      result.username = server.username; result.credential = server.credential;
    }
    return result;
  });
}
export async function encodeSignal(value, { compact = true } = {}) {
  validateSignal(value);
  const bytes = encoder.encode(JSON.stringify(value));
  if (bytes.length > SIGNAL_LIMIT) throw new Error('Convite excede o tamanho permitido.');
  if (compact && typeof CompressionStream !== 'undefined') {
    const packed = await boundedRead(new Blob([bytes]).stream().pipeThrough(new CompressionStream('deflate')), SIGNAL_LIMIT);
    if (packed.length < bytes.length) return INVITE_PREFIX + 'z' + base64url(packed);
  }
  return INVITE_PREFIX + 'j' + base64url(bytes);
}
export function extractToken(input) {
  let text = String(input || '').trim();
  if (text.startsWith('https://') || text.startsWith('http://')) {
    let url;
    try { url = new URL(text); } catch { throw new Error('O link do convite é inválido.'); }
    if (url.username || url.password || url.search) throw new Error('Use o link original do convite, sem parâmetros adicionais.');
    text = new URLSearchParams(url.hash.slice(1)).get('invite') || '';
  }
  if (!text.startsWith(INVITE_PREFIX) || !['z', 'j'].includes(text.charAt(INVITE_PREFIX.length))) throw new Error('Cole um convite iPhone do Conector AliançaDX, começando por AADXi1. O código MDA6 pertence ao aplicativo Windows/Android.');
  if (text.length > SIGNAL_LIMIT * 2) throw new Error('Convite excede o tamanho permitido.');
  return text;
}
export async function decodeSignal(input, expected = {}) {
  const token = extractToken(input);
  const mode = token.charAt(INVITE_PREFIX.length);
  let bytes = fromBase64url(token.slice(INVITE_PREFIX.length + 1));
  if (mode === 'z') {
    if (typeof DecompressionStream === 'undefined') throw new Error('Este navegador não abre o convite compacto. Atualize o Safari ou gere um convite sem compactação no computador.');
    bytes = await boundedRead(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate')), SIGNAL_LIMIT);
  }
  if (bytes.byteLength > SIGNAL_LIMIT) throw new Error('Convite excede o tamanho permitido.');
  let value;
  try { value = JSON.parse(decoder.decode(bytes)); } catch { throw new Error('Não foi possível ler o convite. Copie o conteúdo completo.'); }
  return validateSignal(value, expected);
}
export function createSignal(type, sdp, name, offer = null, iceServers = null) {
  const createdAt = Date.now();
  return validateSignal({
    v: 1, type, sdp, name: String(name || 'AliançaDX').replace(/[\x00-\x1f\x7f]/g, '').slice(0, 80),
    nonce: offer?.nonce || base64url(crypto.getRandomValues(new Uint8Array(16))),
    createdAt,
    expiresAt: offer ? Math.min(offer.expiresAt, createdAt + SIGNAL_LIFETIME) : createdAt + SIGNAL_LIFETIME,
    ...(iceServers ? { iceServers: validateIce(iceServers) } : {})
  });
}
/** Counts only; never return or print candidate addresses, ports or credentials. */
export function iceSummary(sdp) {
  const result={host:0,srflx:0,prflx:0,relay:0,total:0};
  for(const line of String(sdp||'').split(/\r?\n/)){
    if(!line.startsWith('a=candidate:'))continue;
    const type=/\btyp (host|srflx|prflx|relay)(?:\s|$)/.exec(line)?.[1];
    if(type){result[type]++;result.total++;}
  }
  return result;
}
export function iceSummaryText(summary) {
  return summary.host+' local(is), '+(summary.srflx+summary.prflx)+' pública(s)/STUN, '+summary.relay+' retransmitida(s)/TURN';
}
export function requireIceCandidates(sdp) {
  const summary=iceSummary(sdp);
  if(!summary.total)throw new Error('O navegador não encontrou nenhuma rota de rede WebRTC (0 candidatos ICE). Este ambiente pode bloquear conexões entre aparelhos. Abra no Edge/Chrome do PC ou no Safari do iPhone, confira a rede e tente novamente; em redes restritas, configure um servidor TURN próprio.');
  return summary;
}
export async function gatherIce(peer, signal, timeout = 18000) {
  if(signal?.aborted)throw new DOMException('Conexão cancelada.','AbortError');
  if (peer.iceGatheringState !== 'complete') await new Promise((resolve, reject) => {
    const finish = error => { clearTimeout(timer); peer.removeEventListener('icegatheringstatechange', changed); signal?.removeEventListener('abort', cancelled); error ? reject(error) : resolve(); };
    const changed = () => { if (peer.iceGatheringState === 'complete') finish(); };
    const cancelled = () => finish(new DOMException('Conexão cancelada.', 'AbortError'));
    const timer = setTimeout(() => finish(new Error('A descoberta da rede demorou demais. Confira a conexão e tente novamente.')), timeout);
    peer.addEventListener('icegatheringstatechange', changed); signal?.addEventListener('abort', cancelled, { once: true });
    if (signal?.aborted) cancelled(); else changed();
  });
  return requireIceCandidates(peer.localDescription?.sdp);
}
export function drawQr(target, text) {
  target.replaceChildren();
  if (typeof globalThis.qrcode !== 'function') { target.textContent = 'QR indisponível. Use Copiar ou Salvar arquivo.'; return false; }
  try {
    const qr = globalThis.qrcode(0, 'L'); qr.addData(text, 'Byte'); qr.make();
    const count = qr.getModuleCount(), margin = 4, scale = Math.max(2, Math.floor(720 / (count + margin * 2)));
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = (count + margin * 2) * scale;
    canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', 'Código QR privado desta conexão');
    const context = canvas.getContext('2d'); context.fillStyle = '#ffffff'; context.fillRect(0, 0, canvas.width, canvas.height); context.fillStyle = '#073B2B';
    for (let y = 0; y < count; y++) for (let x = 0; x < count; x++) if (qr.isDark(y, x)) context.fillRect((x+margin)*scale, (y+margin)*scale, scale, scale);
    target.append(canvas); return true;
  } catch { target.textContent = 'O convite ficou grande para um único QR. Use Copiar ou Salvar arquivo.'; return false; }
}
export async function copyText(text, field) {
  if (navigator.clipboard?.writeText) { try { await navigator.clipboard.writeText(text); return true; } catch {} }
  if (field) { field.value = text; field.focus(); field.select(); }
  return false;
}
export function saveText(text, name) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
