/** Compatible MiniAnyDesk JSON framing over an ordered, reliable RTCDataChannel. */
export const MAX_MESSAGE = 4 * 1024 * 1024;
export const RTC_CHUNK = 16 * 1024;
const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });

export function packet(message) {
  if (!message || Array.isArray(message) || typeof message !== 'object' || typeof message.type !== 'string') throw new Error('Mensagem inválida.');
  const body = encoder.encode(JSON.stringify(message));
  if (!body.length || body.length > MAX_MESSAGE) throw new Error('Mensagem excede o limite.');
  const data = new Uint8Array(body.length + 4);
  new DataView(data.buffer).setUint32(0, body.length, false);
  data.set(body, 4);
  return data;
}

export class FrameDecoder {
  constructor(onMessage) { this.onMessage = onMessage; this.header = new Uint8Array(4); this.headerAt = 0; this.body = null; this.at = 0; }
  clear() { this.headerAt = 0; this.body = null; this.at = 0; }
  feed(input) {
    const data = input instanceof Uint8Array ? input : new Uint8Array(input);
    let offset = 0;
    while (offset < data.length) {
      if (!this.body) {
        const count = Math.min(4 - this.headerAt, data.length - offset);
        this.header.set(data.subarray(offset, offset + count), this.headerAt); this.headerAt += count; offset += count;
        if (this.headerAt < 4) continue;
        const size = new DataView(this.header.buffer).getUint32(0, false);
        if (!size || size > MAX_MESSAGE) { this.clear(); throw new Error('Pacote recebido fora do limite permitido.'); }
        this.body = new Uint8Array(size); this.at = 0;
      }
      const count = Math.min(this.body.length - this.at, data.length - offset);
      this.body.set(data.subarray(offset, offset + count), this.at); this.at += count; offset += count;
      if (this.at === this.body.length) {
        const raw = this.body; this.clear();
        const message = JSON.parse(decoder.decode(raw));
        if (!message || typeof message !== 'object' || Array.isArray(message) || typeof message.type !== 'string') throw new Error('Mensagem recebida inválida.');
        this.onMessage(message);
      }
    }
  }
}

/** Payloads are fragmented below Safari's negotiated per-message size. */
export class FramedChannel {
  constructor(channel, onMessage, onError) {
    if (channel.ordered === false || channel.maxRetransmits != null || channel.maxPacketLifeTime != null) throw new Error('O canal precisa ser ordenado e confiável.');
    this.channel = channel; this.onError = onError; this.queue = []; this.queuedBytes = 0; this.flushing = false; this.closed = false;
    this.parser = new FrameDecoder(onMessage); this.incoming = Promise.resolve(); this.incomingBytes=0;
    channel.binaryType = 'arraybuffer'; channel.bufferedAmountLowThreshold = 64 * 1024;
    channel.addEventListener('message', event => {
      const bytes=event.data?.byteLength ?? event.data?.size ?? MAX_MESSAGE*3;
      if(!Number.isSafeInteger(bytes)||bytes<0||this.incomingBytes+bytes>8*1024*1024){this.fail(new Error('O canal recebeu dados demais sem processamento.'));return;}
      this.incomingBytes+=bytes;
      this.incoming = this.incoming.then(async () => {
        if (this.closed) return;
        if (typeof event.data === 'string') throw new Error('O conector enviou um pacote de formato inesperado.');
        const data = typeof Blob !== 'undefined' && event.data instanceof Blob ? await event.data.arrayBuffer() : event.data;
        if (this.closed) return;
        this.parser.feed(data);
      }).catch(error => this.fail(error)).finally(()=>{this.incomingBytes=Math.max(0,this.incomingBytes-bytes);});
    });
    channel.addEventListener('close', () => this.shutdown());
    channel.addEventListener('error', () => this.fail(new Error('Falha no canal de dados.')));
  }
  send(message) {
    if (this.closed || this.channel.readyState !== 'open') return false;
    let data;
    try { data = packet(message); } catch (error) { this.fail(error); return false; }
    const last = this.queue.at(-1);
    if (message.type === 'input' && message.action === 'move' && last?.message.type === 'input' && last.message.action === 'move' && last.message.captureId === message.captureId) {
      this.queuedBytes -= last.data.byteLength; this.queue.pop();
    }
    if (this.queue.length >= 128 || this.queuedBytes + data.byteLength > 8 * 1024 * 1024) { this.fail(new Error('A conexão não acompanha os comandos. A sessão foi encerrada para evitar comandos atrasados.')); return false; }
    this.queue.push({ data, message }); this.queuedBytes += data.byteLength; this.flush(); return true;
  }
  async flush() {
    if (this.flushing || this.closed) return;
    this.flushing = true;
    try {
      while (this.queue.length && !this.closed) {
        const { data } = this.queue.shift(); this.queuedBytes -= data.byteLength;
        for (let at = 0; at < data.length; at += RTC_CHUNK) {
          await this.drain();
          if (this.closed || this.channel.readyState !== 'open') throw new Error('Conexão encerrada.');
          this.channel.send(data.subarray(at, Math.min(at + RTC_CHUNK, data.length)));
        }
      }
    } catch (error) { this.fail(error); } finally { this.flushing = false; }
  }
  drain() {
    if (this.channel.bufferedAmount <= 128 * 1024) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const channel = this.channel;
      const cleanup = () => { clearTimeout(timer); channel.removeEventListener('bufferedamountlow', ready); channel.removeEventListener('close', closed); };
      const ready = () => { cleanup(); resolve(); };
      const closed = () => { cleanup(); reject(new Error('Conexão encerrada durante o envio.')); };
      const timer = setTimeout(() => { cleanup(); reject(new Error('A rede não respondeu ao envio.')); }, 10000);
      channel.addEventListener('bufferedamountlow', ready, { once: true }); channel.addEventListener('close', closed, { once: true });
      if (channel.bufferedAmount <= 64 * 1024) ready();
    });
  }
  fail(error) { if (this.closed) return; this.shutdown(); try { this.channel.close(); } catch {} this.onError?.(error); }
  shutdown() { this.closed = true; this.queue.length = 0; this.queuedBytes = 0; this.parser.clear(); }
}

export function fitBounds(width, height, sourceWidth, sourceHeight) {
  if (![width,height,sourceWidth,sourceHeight].every(value => Number.isFinite(value) && value > 0)) return null;
  const scale = Math.min(width / sourceWidth, height / sourceHeight);
  const w = sourceWidth * scale, h = sourceHeight * scale;
  return { left: (width-w)/2, top: (height-h)/2, width: w, height: h };
}
export function pointAt(x, y, bounds, clamp = false) {
  if (!bounds || !Number.isFinite(x) || !Number.isFinite(y)) return null;
  const { left, top, width, height } = bounds;
  if (!clamp && (x < left || y < top || x > left + width || y > top + height)) return null;
  return { x: Math.max(0,Math.min(1,(x-left)/width)), y: Math.max(0,Math.min(1,(y-top)/height)) };
}
export function clickCommands(captureId, point, button = 'left', count = 1) {
  if (typeof captureId !== 'string' || !captureId || !['left','middle','right'].includes(button) || ![1,2].includes(count) || !point || !Number.isFinite(point.x) || !Number.isFinite(point.y) || point.x < 0 || point.x > 1 || point.y < 0 || point.y > 1) throw new Error('Clique inválido.');
  const common = { type:'input', captureId, x:point.x, y:point.y };
  return [{...common,action:'move'}, ...Array.from({length:count}, () => [{...common,action:'button',button,down:true},{...common,action:'button',button,down:false}]).flat()];
}
export function inputAllowed(session, selected, visible) { return Boolean(session?.connected && selected && visible && session.frame && session.sharing && !session.localPaused && !session.promptActive && session.captureAvailable !== false && !session.stale); }
export function validFrame(message) {
  return message?.type === 'screen' && typeof message.captureId === 'string' && message.captureId.length > 0 && message.captureId.length <= 512 && Number.isInteger(message.width) && message.width > 0 && message.width <= 16384 && Number.isInteger(message.height) && message.height > 0 && message.height <= 16384 && message.width * message.height <= 64 * 1024 * 1024 && typeof message.image === 'string' && message.image.length > 0 && message.image.length < MAX_MESSAGE;
}
export function validFileName(name) { return typeof name === 'string' && name.length > 0 && name.length <= 200 && !/[\\/:*?"<>|\x00-\x1f]/.test(name) && !/[. ]$/.test(name) && !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name) && name !== '.' && name !== '..'; }
