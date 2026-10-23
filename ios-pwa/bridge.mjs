import { FramedChannel, MAX_MESSAGE } from './protocol.mjs';
import { createSignal, encodeSignal, decodeSignal, gatherIce, iceSummaryText, requireIceCandidates, drawQr, copyText, saveText, validateIce, DEFAULT_ICE, SIGNAL_LIMIT } from './signaling.mjs';

const $ = id => document.getElementById(id);
const local = new URLSearchParams(location.hash.slice(1)).get('local');
history.replaceState(null, '', location.pathname);
let stopped = false, current = null, config = null, eventController = null, reconnectDelay = 300, camera = null;
let pulsePending = false, pulseFailures = 0;
const credentials = /^[A-Za-z0-9_-]{32,128}$/.test(local || '') ? local : null;
const form = $('bridge-form'), start = $('start-bridge'), stop = $('stop-bridge');
function status(title, text) { $('bridge-status-title').textContent = title; $('bridge-status-text').textContent = text; }
function log(text) {
  const row = document.createElement('li'); row.textContent = new Date().toLocaleTimeString('pt-BR') + ' · ' + String(text).slice(0,500);
  $('bridge-log').append(row); while ($('bridge-log').children.length > 30) $('bridge-log').firstElementChild.remove();
}
function error(text) { $('bridge-error').textContent = String(text).slice(0,1000); $('bridge-error').hidden = false; log(text); }
function clearError() { $('bridge-error').hidden = true; $('bridge-error').textContent = ''; }
function markPrepared(){
  $('host-invitation').required=false;$('host-password').required=false;
  $('host-invitation').placeholder='Este PC já está preparado pelo Hospedeiro. Deixe vazio para continuar.';
  $('host-password').placeholder='Senha já preparada somente na memória do Conector';
  if(!form.querySelector('[data-prepared-notice]')){
    const notice=document.createElement('p');notice.dataset.preparedNotice='true';notice.className='notice';notice.textContent='Este PC já está preparado pelo Hospedeiro. Você pode gerar o convite sem preencher novamente. Para trocar de PC, informe outro convite e senha.';form.insertBefore(notice,form.firstChild);
  }
}
async function api(path, body, generation, signal) {
  const headers = { Authorization: 'Bearer ' + credentials };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (Number.isSafeInteger(generation)) headers['X-AADX-Generation'] = String(generation);
  const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', headers, body: body === undefined ? undefined : JSON.stringify(body), cache:'no-store', credentials:'omit', signal });
  if (!response.ok) {
    let detail;
    try { const data=await response.json();detail=data.error||data.message; } catch {}
    throw new Error(detail || (response.status === 401 ? 'A janela do Conector expirou. Abra o atalho novamente.' : response.status === 409 ? 'Esta operação pertence a uma conexão anterior. Gere um novo convite.' : 'O Conector recusou a operação (' + response.status + ').'));
  }
  if (response.status === 204) return {};
  return response.json();
}
function sendPhone(session, message) { if (current === session && session.channel?.readyState === 'open') return session.wire?.send(message) || false; return false; }
function nativeEvent(event) {
  if (!event || typeof event !== 'object' || event.type === 'bridge_ping') return;
  const session = current;
  if (!session) return;
  if (session.connectPending) {
    const size = JSON.stringify(event).length;
    if (session.pending.length >= 128 || session.pendingBytes + size > 8 * 1024 * 1024) { closeSession('O envio de dados do PC excedeu o limite.',true); return; }
    session.pending.push(event); session.pendingBytes += size; return;
  }
  if (!Number.isSafeInteger(session.generation) || event.generation !== session.generation) return;
  if (event.type === 'bridge_packet') {
    if (session.authorized && event.message && !sendPhone(session,event.message)) closeSession('O canal com o iPhone não conseguiu acompanhar o PC.',true);
  } else if (event.type === 'bridge_state') {
    if (event.state === 'authorized') {
      session.authorized = true;
      status('iPhone conectado', 'Acesso autorizado no PC. Mantenha esta janela e o Hospedeiro abertos.');
      log('O Hospedeiro aprovou o acesso solicitado pelo iPhone.');
    }
    sendPhone(session,event);
    if (event.state === 'error' || event.state === 'closed') {
      session.authorized = false;
      status('Acesso encerrado', event.message || 'O Hospedeiro encerrou a conexão.');
      log(event.message || 'Acesso encerrado no Hospedeiro.');
    } else if (event.state === 'awaiting') status('Aguardando aprovação', 'Confira a solicitação na janela do Hospedeiro e autorize o iPhone.');
  }
}
async function eventLoop() {
  while (!stopped) {
    eventController = new AbortController();
    try {
      const response = await fetch('/api/events', { headers:{ Authorization:'Bearer '+credentials }, cache:'no-store', credentials:'omit', signal:eventController.signal });
      if (!response.ok || !response.body) throw new Error(response.status === 401 ? 'Abra novamente o Conector pelo atalho do Windows.' : 'A comunicação local com o Conector foi interrompida.');
      reconnectDelay=300;
      const reader=response.body.getReader(), decoder=new TextDecoder('utf-8',{fatal:true});let buffer='';
      try {
        while (!stopped) {
          const item=await reader.read();if(item.done)break;
          buffer += decoder.decode(item.value,{stream:true});
          if(buffer.length > MAX_MESSAGE*2)throw new Error('Evento local excede o limite.');
          let at;
          while((at=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,at);buffer=buffer.slice(at+1);if(line.trim())nativeEvent(JSON.parse(line));}
        }
      } finally { reader.releaseLock(); }
    } catch(reason) {
      if(stopped)return;
      if(current?.authorized)status('Verificando o Conector', 'A conexão local foi interrompida. Tentando restabelecer a comunicação…');
      if(reconnectDelay>=5000)error(reason.message);
    }
    if(!stopped)await new Promise(resolve=>setTimeout(resolve,reconnectDelay));
    reconnectDelay=Math.min(5000,reconnectDelay*2);
  }
}
function stopCamera() {
  if(camera){camera.cancelled=true;camera.stream?.getTracks().forEach(track=>track.stop());camera=null;}
  if($('scan-video')){$('scan-video').srcObject=null;$('scan-video').hidden=true;}
}
async function closeSession(message='Conexão encerrada.', notify = true) {
  const session=current;if(!session)return;
  current=null;clearTimeout(session.expiry);clearTimeout(session.negotiationTimeout);clearTimeout(session.networkLoss);session.abort.abort();stopCamera();
  if(session.authorized)session.wire?.send({type:'bridge_state',state:'closed',message});
  const generation=session.generation;
  session.wire?.shutdown();session.channel?.close();session.peer.close();
  if(Number.isSafeInteger(generation)) {
    try { await api('/api/disconnect',{},generation); } catch {}
  }
  $('invitation-panel').hidden=true;$('answer-form').hidden=true;$('phone-invitation').value='';$('phone-answer').value='';
  $('qr-area').replaceChildren();start.disabled=false;stop.disabled=true;
  if(notify){status('Conexão encerrada',message);log(message);}
}
function onPhoneMessage(session,message) {
  if(current!==session)return;
  if(message.type==='ios_connect'){
    if(session.requested || message.nonce!==session.offer?.nonce || Date.now()>=session.offer.expiresAt || typeof message.name!=='string'){
      sendPhone(session,{type:'bridge_state',state:'error',message:'O convite não é válido para esta conexão. Gere outro convite no PC.'});
      closeSession('Convite inválido ou repetido.',true);return;
    }
    session.requested=true;session.connectPending=true;session.pending=[];session.pendingBytes=0;
    session.commands=session.commands.then(async()=>{
      if(current!==session)return;
      const result=await api('/api/connect',{name:message.name.replace(/[\x00-\x1f\x7f]/g,'').slice(0,80)||'iPhone'});
      if(current!==session){if(Number.isSafeInteger(result.generation))await api('/api/disconnect',{},result.generation);return;}
      if(!Number.isSafeInteger(result.generation))throw new Error('O Conector não identificou a conexão autorizada.');
      session.generation=result.generation;session.connectPending=false;
      const pending=session.pending;session.pending=[];session.pendingBytes=0;
      status('Aguardando aprovação', 'Confira e autorize o pedido na janela do Hospedeiro.');
      for(const event of pending)nativeEvent(event);
    }).catch(reason=>{if(current===session){sendPhone(session,{type:'bridge_state',state:'error',message:reason.message});error(reason.message);closeSession('Não foi possível solicitar o acesso.',false);}});
    return;
  }
  if(!session.authorized || !Number.isSafeInteger(session.generation))return;
  const generation=session.generation;
  if(++session.commandCount>256){closeSession('Há comandos demais aguardando a rede.',true);return;}
  session.commands=session.commands.then(async()=>{
    if(current!==session || !session.authorized || session.generation!==generation)return;
    await api('/api/command',message,generation);
  }).catch(reason=>{if(current===session){error(reason.message);closeSession('A comunicação com o Hospedeiro foi interrompida.',false);}}).finally(()=>session.commandCount--);
}
async function begin(event) {
  event.preventDefault();clearError();stopCamera();
  start.disabled=true;
  try {
    await closeSession('Convite anterior substituído.',false);start.disabled=true;
    if(!window.RTCPeerConnection)throw new Error('Abra o Conector no Microsoft Edge ou Google Chrome atualizado.');
    const invitation=$('host-invitation').value.trim(), password=$('host-password').value;
    const usePrepared = config.configured && !invitation && !password;
    if(!usePrepared && (!invitation || password.length<8 || password.length>256))throw new Error('Informe o convite do Hospedeiro e a senha de 8 a 256 caracteres, ou use o PC já preparado.');
    let iceServers=DEFAULT_ICE;
    const advanced=$('ice-servers')?.value.trim();
    if(advanced){try{iceServers=validateIce(JSON.parse(advanced));}catch{throw new Error('A configuração ICE avançada precisa ser um JSON válido. Deixe em branco para usar a descoberta padrão.');}}
    status('Preparando o PC','Conferindo o convite e preparando a conexão privada…');
    if(!usePrepared) {
      await api('/api/configure',{invitation,password,name:$('bridge-name').value.trim()||'Conector iPhone',mode:$('local-mode').checked?'local':'mesh'});
      config.configured=true;
      markPrepared();$('host-invitation').value='';
    }
    $('host-password').value='';
    const peer=new RTCPeerConnection({iceServers,bundlePolicy:'max-bundle'});
    const session={peer,channel:peer.createDataChannel('aliancadx-v1',{ordered:true}),abort:new AbortController(),commands:Promise.resolve(),commandCount:0,connectPending:false,pending:[],pendingBytes:0,authorized:false,generation:null};
    current=session;stop.disabled=false;
    let opened=false;
    const channelOpen=()=>{
      if(opened||current!==session||session.channel.readyState!=='open')return;
      opened=true;clearTimeout(session.negotiationTimeout);
      session.wire=new FramedChannel(session.channel,message=>onPhoneMessage(session,message),reason=>{if(current===session){error(reason.message);closeSession('Falha no canal com o iPhone.',false);}});
      status('iPhone encontrado','Aguardando o pedido de acesso e a aprovação no Hospedeiro.');
    };
    session.channel.addEventListener('open',channelOpen);
    if(session.channel.readyState==='open')channelOpen();
    session.channel.addEventListener('close',()=>{if(current===session)closeSession('O iPhone encerrou ou perdeu a conexão.',true);});
    peer.addEventListener('connectionstatechange',()=>{
      if(current!==session)return;
      clearTimeout(session.networkLoss);
      if(peer.connectionState==='failed')closeSession('A rede não permitiu a conexão direta. Tente outra rede ou configure um servidor TURN próprio nas opções avançadas.',true);
      else if(peer.connectionState==='disconnected'){status('Conexão instável','Aguardando a rede do iPhone se recuperar…');session.networkLoss=setTimeout(()=>{if(current===session&&peer.connectionState!=='connected')closeSession('A conexão com o iPhone foi perdida.',true);},12000);}
      else if(peer.connectionState==='connecting')status('Estabelecendo o canal seguro','A resposta foi aplicada. Verificando o caminho entre o PC e o iPhone…');
      else if(peer.connectionState==='connected'&&!session.authorized)status('Canal seguro estabelecido','Aguardando o pedido de acesso do iPhone.');
    });
    peer.addEventListener('iceconnectionstatechange',()=>{if(current===session)log('Rede WebRTC: '+peer.iceConnectionState+'.');});
    await peer.setLocalDescription(await peer.createOffer());
    status('Descobrindo a rede','Preparando o convite privado. Essa etapa pode levar alguns segundos.');
    const routes=await gatherIce(peer,session.abort.signal);
    if(current!==session)return;
    log('Rotas do PC: '+iceSummaryText(routes)+'. Endereços privados não são exibidos.');
    session.offer=createSignal('offer',peer.localDescription.sdp,$('bridge-name').value.trim()||'Meu PC',null,iceServers);
    const token=await encodeSignal(session.offer,{compact:$('compact-invitation')?.checked!==false});
    const url=new URL(config.appUrl);
    if(url.protocol!=='https:' && !['localhost','127.0.0.1','[::1]','terminal.local'].includes(url.hostname))throw new Error('O endereço do aplicativo precisa usar HTTPS.');
    url.hash='invite='+token;
    session.invitation=url.href;session.token=token;
    $('phone-invitation').value=url.href;$('invitation-panel').hidden=false;$('answer-form').hidden=false;
    drawQr($('qr-area'),url.href);
    status('1 · Abra o convite no iPhone','Leia o QR com a câmera do iPhone ou envie o link privado. Depois devolva a resposta ao PC.');
    log('Convite privado gerado, válido por 20 minutos. Cada resposta pertence somente a este convite.');
    session.expiry=setTimeout(()=>{if(current===session && !session.authorized)closeSession('O convite expirou. Gere um novo convite para continuar.',true);},Math.max(1,session.offer.expiresAt-Date.now()));
  }catch(reason){error(reason.message);if(current)await closeSession('Não foi possível preparar a conexão.',false);status('Não foi possível gerar o convite','Confira os dados e tente novamente.');start.disabled=false;}
}
async function applyAnswer(event) {
  event?.preventDefault();clearError();
  const session=current;
  if(!session?.offer) {error('Gere o convite no PC antes de devolver a resposta do iPhone.');return;}
  try{
    if(session.peer.signalingState!=='have-local-offer')throw new Error('Este convite já recebeu uma resposta. Aguarde a conexão ou gere outro convite.');
    const answer=await decodeSignal($('phone-answer').value,{type:'answer',nonce:session.offer.nonce});
    if(current!==session)return;
    if(answer.expiresAt>session.offer.expiresAt)throw new Error('A resposta não pertence ao período deste convite.');
    log('Rotas do iPhone: '+iceSummaryText(requireIceCandidates(answer.sdp))+'.');
    await session.peer.setRemoteDescription({type:'answer',sdp:answer.sdp});
    session.negotiationTimeout=setTimeout(()=>{if(current===session&&!session.wire)closeSession('A conexão direta não foi concluída em 60 segundos. Tente outra rede ou configure um servidor TURN próprio; depois gere outro convite.',true);},60000);
    $('phone-answer').value='';$('answer-form').hidden=true;stopCamera();
    status('2 · Conectando ao iPhone','A resposta foi conferida. Aguarde e aprove o pedido na janela do Hospedeiro.');
    log('Resposta do iPhone vinculada ao convite atual e aceita.');
  }catch(reason){error(reason.message);}
}
async function importAnswer() {
  const file=$('answer-file').files?.[0];if(!file)return;
  try{if(file.size>SIGNAL_LIMIT*2)throw new Error('O arquivo de resposta é grande demais.');$('phone-answer').value=await file.text();await applyAnswer();}
  catch(reason){error(reason.message);}finally{$('answer-file').value='';}
}
async function scanAnswer() {
  clearError();stopCamera();
  if(!('BarcodeDetector' in window) || !navigator.mediaDevices?.getUserMedia){error('Este navegador não oferece leitura de QR pela webcam. Use Colar resposta ou Importar arquivo.');return;}
  try{
    const formats=await BarcodeDetector.getSupportedFormats();if(!formats.includes('qr_code'))throw new Error('A leitura de QR não está disponível. Cole a resposta ou importe o arquivo.');
    const scan={cancelled:false,stream:await navigator.mediaDevices.getUserMedia({video:{width:{ideal:720},height:{ideal:720}},audio:false})};camera=scan;
    const video=$('scan-video');video.srcObject=scan.stream;video.hidden=false;await video.play();
    const detector=new BarcodeDetector({formats:['qr_code']}),deadline=Date.now()+45000;
    while(camera===scan && !scan.cancelled && Date.now()<deadline){
      const codes=await detector.detect(video);
      if(codes.length){$('phone-answer').value=codes[0].rawValue;stopCamera();await applyAnswer();return;}
      await new Promise(resolve=>setTimeout(resolve,180));
    }
    stopCamera();
  }catch(reason){stopCamera();error(reason.message||'Não foi possível abrir a webcam. Cole a resposta ou importe o arquivo.');}
}
form.addEventListener('submit',begin);$('answer-form').addEventListener('submit',applyAnswer);
stop.addEventListener('click',()=>closeSession('Conexão encerrada no Conector.',true));
$('copy-invitation').addEventListener('click',async()=>{if(current?.invitation){const copied=await copyText(current.invitation,$('phone-invitation'));log(copied?'Link privado copiado.':'Link selecionado. Copie-o manualmente.');}});
$('save-invitation').addEventListener('click',()=>{if(current?.token)saveText(current.token,'AliancaDX-convite-iPhone.aadxinvite');});
$('answer-file').addEventListener('change',importAnswer);$('scan-answer').addEventListener('click',scanAnswer);
window.addEventListener('pagehide',()=>{
  stopped=true;eventController?.abort();stopCamera();const session=current;current=null;
  if(session){session.peer.close();if(Number.isSafeInteger(session.generation))fetch('/api/disconnect',{method:'POST',headers:{Authorization:'Bearer '+credentials,'Content-Type':'application/json','X-AADX-Generation':String(session.generation)},body:'{}',keepalive:true}).catch(()=>{});}
});
if(!credentials){start.disabled=true;stop.disabled=true;status('Abra pelo atalho do Windows','Esta página é a parte local do Conector AliançaDX. Abra o aplicativo para receber uma autorização válida.');error('A autorização desta janela não está disponível. Reabra o Conector pelo atalho do Windows.');}
else api('/api/config').then(value=>{
  config=value;
  if(config.configured){
    markPrepared();
  }
  status('Pronto para conectar',config.configured?'Este PC já está preparado. Gere o convite para o iPhone e aprove o acesso no Hospedeiro.':'1. Informe o convite do Hospedeiro. 2. Abra o QR no iPhone. 3. Devolva a resposta e autorize no PC.');
  eventLoop();
}).catch(reason=>{error(reason.message);start.disabled=true;});
setInterval(async()=>{
  if(stopped || !current || pulsePending)return;
  pulsePending=true;
  try { await api('/api/pulse',{},current.generation);pulseFailures=0; }
  catch(reason){if(++pulseFailures>=3 && current){error('O Conector local deixou de responder. Reabra o aplicativo pelo atalho.');closeSession('A comunicação local foi interrompida.',false);}}
  finally{pulsePending=false;}
},3000);
