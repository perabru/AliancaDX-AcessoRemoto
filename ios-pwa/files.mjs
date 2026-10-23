import { validFileName } from './protocol.mjs';
export const MAX_MOBILE_FILE = 25 * 1024 * 1024;
const CHUNK = 64 * 1024;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const hex = bytes => Array.from(bytes, value => value.toString(16).padStart(2,'0')).join('');
function encode(bytes) { let raw = ''; for (let at=0; at<bytes.length; at+=8192) raw += String.fromCharCode(...bytes.subarray(at,at+8192)); return btoa(raw); }
function decode(text) { if (typeof text !== 'string' || text.length > 87384 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(text)) throw new Error('Bloco de arquivo inválido.'); return Uint8Array.from(atob(text), value=>value.charCodeAt(0)); }

/** One bounded, consent-based exchange per live connection. */
export class MobileFiles {
  constructor(send, status, askReceive, onDownload) {
    this.send = send; this.status = status; this.askReceive = askReceive; this.onDownload = onDownload;
    this.active = null; this.pendingRequest = null; this.closed = false;
  }
  report(text, state = this.active) { this.status(text, state ? state.offset / Math.max(1,state.size) : 0, Boolean(state || this.pendingRequest)); }
  arm(state, delay=90000) { clearTimeout(state.timer); state.timer=setTimeout(()=>{ if(this.active===state) this.cancel('O outro dispositivo não respondeu à transferência.'); },delay); }
  async upload(file) {
    if (this.closed || this.active || this.pendingRequest) throw new Error('Conclua ou cancele a transferência atual.');
    if (!file || !validFileName(file.name) || !Number.isSafeInteger(file.size) || file.size<0 || file.size>MAX_MOBILE_FILE) throw new Error('Escolha um arquivo de até 25 MiB, com nome compatível com o Windows.');
    const state = { id:crypto.randomUUID(),mode:'send',name:file.name,size:file.size,offset:0,file };
    this.active=state; this.arm(state); this.report('Calculando a integridade do arquivo…',state);
    try {
      const buffer = await file.arrayBuffer();
      const digest = hex(new Uint8Array(await crypto.subtle.digest('SHA-256',buffer)));
      if(this.closed || this.active!==state)return;
      state.sha256=digest;
      if(!this.send({type:'file_offer',id:state.id,name:state.name,size:state.size,sha256:digest}))throw new Error('Não foi possível enviar a oferta.');
      this.report('Aguardando a pessoa no PC autorizar e escolher o destino…',state);
    } catch(error) { if(this.active===state)this.cancel(error.message); }
  }
  request() {
    if(this.closed || this.active || this.pendingRequest)throw new Error('Conclua ou cancele a transferência atual.');
    const id=crypto.randomUUID();this.pendingRequest=id;
    this.requestTimer=setTimeout(()=>{if(this.pendingRequest===id)this.cancel('O pedido de arquivo expirou.');},90000);
    if(!this.send({type:'file_request',id})) { this.pendingRequest=null;clearTimeout(this.requestTimer);throw new Error('Não foi possível solicitar o arquivo.'); }
    this.report('A pessoa no PC precisa escolher e autorizar o arquivo.');
  }
  async next(state) {
    if(this.active!==state || this.closed)return;
    this.arm(state,30000);
    if(state.offset===state.size){this.send({type:'file_end',id:state.id});this.report('Conferindo o arquivo no PC…',state);return;}
    try {
      const chunk=new Uint8Array(await state.file.slice(state.offset,Math.min(state.offset+CHUNK,state.size)).arrayBuffer());
      if(this.active!==state || this.closed)return;
      state.expectedAck=state.offset+chunk.length;
      if(!this.send({type:'file_chunk',id:state.id,offset:state.offset,data:encode(chunk)}))throw new Error('Envio interrompido.');
      this.report('Enviando arquivo…',state);
    }catch(error){if(this.active===state)this.cancel(error.message);}
  }
  handle(message) {
    if(typeof message?.type!=='string' || !message.type.startsWith('file_'))return false;
    if(this.closed)return true;
    if(message.type==='file_request'){ this.send({type:'file_request_result',id:message.id,accepted:false});this.report('O PC pediu um arquivo. Para autorizar, escolha um arquivo no iPhone e toque em Enviar ao PC.');return true; }
    if(message.type==='file_request_result'){
      if(message.id===this.pendingRequest && message.accepted===false){this.pendingRequest=null;clearTimeout(this.requestTimer);this.report('A pessoa no PC recusou o pedido ou não escolheu um arquivo.');}
      return true;
    }
    if(!UUID.test(message.id))return true;
    if(message.type==='file_offer'){this.offer(message);return true;}
    const state=this.active;if(!state || message.id!==state.id)return true;
    this.arm(state,30000);
    try{
      switch(message.type){
        case 'file_cancel': this.finish('A transferência foi recusada ou cancelada no PC.');break;
        case 'file_accept': if(state.mode==='send' && !state.accepted){state.accepted=true;this.next(state);}break;
        case 'file_ack':
          if(state.mode!=='send' || !Number.isSafeInteger(message.offset) || message.offset!==state.expectedAck)throw new Error('Confirmação de bloco inválida.');
          state.offset=message.offset;delete state.expectedAck;this.next(state);break;
        case 'file_done': if(state.mode==='send' && state.offset===state.size)this.finish('Arquivo enviado e conferido no PC.');break;
        case 'file_chunk': this.chunk(state,message);break;
        case 'file_end': this.received(state);break;
      }
    }catch(error){this.cancel(error.message);}
    return true;
  }
  async offer(message){
    if(this.active || !validFileName(message.name) || !Number.isSafeInteger(message.size) || message.size<0 || message.size>MAX_MOBILE_FILE || !/^[a-f0-9]{64}$/.test(message.sha256)){
      this.send({type:'file_cancel',id:message.id});
      this.report(message.size>MAX_MOBILE_FILE?'O arquivo excede o limite de 25 MiB desta versão para iPhone.':'Oferta de arquivo recusada: formato inválido ou transferência em andamento.');return;
    }
    clearTimeout(this.requestTimer);this.pendingRequest=null;
    const state={id:message.id,mode:'receive',name:message.name,size:message.size,sha256:message.sha256,offset:0,parts:[],ready:false};
    this.active=state;this.arm(state);this.report('Confirme se deseja receber o arquivo.',state);
    const accepted=await this.askReceive({...message});
    if(this.active!==state || this.closed)return;
    if(!accepted){this.cancel('Recebimento recusado.');return;}
    state.ready=true;this.send({type:'file_accept',id:state.id});this.report('Recebendo arquivo…',state);
  }
  chunk(state,message){
    if(state.mode!=='receive' || !state.ready || !Number.isSafeInteger(message.offset) || message.offset!==state.offset)throw new Error('Bloco de arquivo fora de ordem.');
    const bytes=decode(message.data);
    if(!bytes.length || bytes.length>CHUNK || state.offset+bytes.length>state.size)throw new Error('Tamanho de bloco inválido.');
    state.parts.push(bytes);state.offset+=bytes.length;
    this.send({type:'file_ack',id:state.id,offset:state.offset});this.report('Recebendo arquivo…',state);
  }
  async received(state){
    if(state.mode!=='receive' || !state.ready || state.offset!==state.size) { this.cancel('Arquivo incompleto.');return; }
    state.ready=false;
    try{
      const bytes=new Uint8Array(state.size);let at=0;for(const part of state.parts){bytes.set(part,at);at+=part.length;}state.parts.length=0;
      const digest=hex(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)));
      if(this.active!==state || this.closed)return;
      if(digest!==state.sha256)throw new Error('A integridade SHA-256 não confere. O arquivo foi descartado.');
      const blob=new Blob([bytes],{type:'application/octet-stream'});
      this.send({type:'file_done',id:state.id});this.onDownload(blob,state.name);this.finish('Arquivo conferido. Toque em Salvar arquivo para escolher o destino.');
    }catch(error){if(this.active===state)this.cancel(error.message);}
  }
  finish(text){const state=this.active;if(state){clearTimeout(state.timer);if(state.parts)state.parts.length=0;}this.active=null;this.report(text);}
  cancel(text='Transferência cancelada.'){
    if(this.pendingRequest){this.send({type:'file_request_cancel',id:this.pendingRequest});this.pendingRequest=null;clearTimeout(this.requestTimer);}
    if(this.active)this.send({type:'file_cancel',id:this.active.id});
    this.finish(text);
  }
  close(){this.cancel('Transferência encerrada com a conexão.');this.closed=true;}
}
