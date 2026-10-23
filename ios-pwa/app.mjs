import { RemoteSession } from './session.mjs';
import { decodeSignal, createSignal, encodeSignal, gatherIce, iceSummaryText, requireIceCandidates, drawQr, copyText, saveText, DEFAULT_ICE, validateIce, SIGNAL_LIMIT } from './signaling.mjs';

const $=id=>document.getElementById(id);
const fragment=new URLSearchParams(location.hash.slice(1)).get('invite');
if(location.hash)history.replaceState(null,'',location.pathname);
const stages=['invite','network','approval','ready'];

class PhoneApp {
  constructor(){
    this.sessions=new Map();this.nextId=1;this.selected=null;this.fileOwner=null;this.fileSelected=null;
    this.canvas=$('remote-canvas');this.stage=$('screen-stage');this.context=this.canvas.getContext('2d');
    this.add();this.bind();
    if(fragment){$('invite').value=fragment;this.selected.status('Convite recebido','Toque em Conectar ao PC para preparar a resposta privada.');}
    this.resize=new ResizeObserver(()=>this.paint(this.selected));this.resize.observe(this.stage);
    document.addEventListener('visibilitychange',()=>{
      if(document.hidden){for(const session of this.sessions.values()){session.release();session.invalidateFrame();}}
      else{this.selected?.resume();this.changed(this.selected);}
    });
    window.addEventListener('blur',()=>{for(const session of this.sessions.values())session.release();});
    window.addEventListener('pagehide',()=>{for(const session of this.sessions.values())session.close();});
    window.addEventListener('online',()=>this.updateOnline());window.addEventListener('offline',()=>this.updateOnline());this.updateOnline();
    window.addEventListener('keydown',event=>{
      if(event.key==='Escape'&&document.body.classList.contains('focus-mode')){this.fullscreen(false);event.preventDefault();return;}
      if(event.target!==this.canvas||!this.selected?.ready())return;
      const keys={Enter:'enter',Escape:'esc',Backspace:'backspace',Delete:'delete',Tab:'tab',ArrowLeft:'left',ArrowRight:'right',ArrowUp:'up',ArrowDown:'down',Home:'home',End:'end',PageUp:'pageup',PageDown:'pagedown'};
      if(keys[event.key]){this.selected.keyStroke([keys[event.key]]);event.preventDefault();}
      else if(event.key.length===1&&event.key.charCodeAt(0)>=32&&event.key.charCodeAt(0)<=126){this.selected.text(event.key).catch(reason=>this.error(reason.message));event.preventDefault();}
    });
  }
  phoneName(){return $('phone-name').value.replace(/[\x00-\x1f\x7f]/g,'').trim().slice(0,60)||'iPhone';}
  add(){
    if(this.sessions.size>=4){this.error('Você pode manter até quatro sessões. Encerre e feche uma aba para adicionar outro PC.');return;}
    const session=new RemoteSession(this.nextId++,this);this.sessions.set(session.id,session);this.select(session.id);
    $('invite').value='';this.clearError();return session;
  }
  select(id){
    const next=this.sessions.get(id);if(!next)return;
    if(this.selected!==next){this.selected?.setSelected(false);this.selected=next;next.setSelected(true);$('text-input').value='';$('file-input').value='';this.fileSelected=null;this.fileOwner=null;this.fullscreen(false);}
    this.changed(next);this.paint(next);
  }
  error(message){$('error-box').textContent=String(message).slice(0,1000);$('error-box').hidden=false;}
  clearError(){$('error-box').textContent='';$('error-box').hidden=true;}
  changed(session){
    this.drawTabs();if(session!==this.selected)return;
    $('status-title').textContent=session.title;$('status-text').textContent=session.message;
    const level=stages.indexOf(session.stage);
    for(const item of $('progress-steps').children){const at=stages.indexOf(item.dataset.step);item.classList.toggle('active',at===level);item.classList.toggle('done',at<level);}
    const pending=session.alive&&Boolean(session.peer);
    $('connect-panel').hidden=session.connected||pending;
    $('connect-button').disabled=Boolean(session.connecting);
    $('session-panel').hidden=!pending&&!session.connected;
    $('session-name').textContent=session.label;
    $('connection-badge').textContent=session.connected?'Conectado':pending?'Conectando':'Encerrado';
    $('answer-panel').hidden=!session.answerToken||session.connected||!session.alive;
    if(session.answerToken && session.answerToken!==this.shownAnswer){$('answer-token').value=session.answerToken;drawQr($('answer-qr'),session.answerToken);this.shownAnswer=session.answerToken;}
    const ready=session.ready();
    $('double-click').disabled=!ready;$('right-click').disabled=!ready;
    $('send-text').disabled=!ready;$('monitor-select').disabled=!ready;
    $('pause-button').disabled=!session.connected||session.localPaused||session.promptActive||!session.captureAvailable;
    $('pause-button').textContent=session.sharing?'Pausar tela':'Retomar tela';
    $('send-file').disabled=!session.connected||session.fileBusy;
    $('request-file').disabled=!session.connected||session.fileBusy;
    $('cancel-file').disabled=!session.fileBusy;
    $('file-status').textContent=session.fileText;
    $('file-progress').hidden=!session.fileBusy;$('file-progress').value=Math.round(session.fileProgress*100);
    const placeholder=$('screen-placeholder');placeholder.hidden=Boolean(session.frame&&session.image);
    if(!placeholder.hidden){placeholder.querySelector('h3').textContent=session.title;placeholder.querySelector('p').textContent=session.message;}
    const download=$('download-area');
    if(session.download?.url!==this.shownDownload){
      download.replaceChildren();this.shownDownload=session.download?.url;
      if(session.download){const link=document.createElement('a');link.className='button primary';link.textContent='Salvar '+session.download.name;link.href=session.download.url;link.download=session.download.name;download.append(link);const note=document.createElement('p');note.className='field-help';note.textContent='No Safari, use Compartilhar ou Salvar em Arquivos se o arquivo abrir em outra tela.';download.append(note);}
    }
    if(session.frame)this.updateMonitors(session);
  }
  drawTabs(){
    const tabs=$('session-tabs');tabs.replaceChildren();
    for(const session of this.sessions.values()){
      const button=document.createElement('button');button.type='button';button.className='session-tab';button.textContent=session.label.length>27?session.label.slice(0,26)+'…':session.label;
      button.setAttribute('role','tab');button.setAttribute('aria-selected',String(session===this.selected));button.setAttribute('aria-label',session.label+', '+(session.connected?'conectado':session.peer&&session.alive?'conectando':'pronto'));
      button.addEventListener('click',()=>this.select(session.id));tabs.append(button);
      if(!session.alive){const close=document.createElement('button');close.type='button';close.className='session-close';close.textContent='×';close.setAttribute('aria-label','Fechar '+session.label);close.addEventListener('click',()=>this.remove(session));tabs.append(close);}
    }
    $('add-session').disabled=this.sessions.size>=4;
  }
  remove(session){
    session.close();this.sessions.delete(session.id);
    if(this.selected===session){this.selected=null;if(this.sessions.size)this.select(this.sessions.keys().next().value);else this.add();}
    this.drawTabs();
  }
  updateMonitors(session){
    const values=[{id:0,name:'Todos os monitores'}];
    for(const item of (Array.isArray(session.frame.monitors)?session.frame.monitors:[]).slice(0,64)){
      const id=Number(item.id);if(Number.isInteger(id)&&id>=1&&id<=64&&!values.some(row=>row.id===id))values.push({id,name:'Monitor '+id});
    }
    const fingerprint=JSON.stringify(values);
    if(fingerprint!==this.monitorsFor){const select=$('monitor-select');select.replaceChildren();for(const item of values){const option=document.createElement('option');option.value=String(item.id);option.textContent=item.name;select.append(option);}this.monitorsFor=fingerprint;}
    $('monitor-select').value=String(session.frame.monitorId==='all'?0:Number(session.frame.monitorId)||0);
  }
  paint(session){
    if(session!==this.selected)return;
    const width=this.stage.clientWidth,height=this.stage.clientHeight;if(!width||!height)return;
    const dpr=Math.min(2,window.devicePixelRatio||1);const w=Math.max(1,Math.round(width*dpr)),h=Math.max(1,Math.round(height*dpr));
    if(this.canvas.width!==w||this.canvas.height!==h){this.canvas.width=w;this.canvas.height=h;}
    const context=this.context;context.setTransform(dpr,0,0,dpr,0,0);context.fillStyle='#061f18';context.fillRect(0,0,width,height);
    if(session.image&&session.frame){
      const bounds=session.layout(width,height);context.drawImage(session.image,bounds.left,bounds.top,bounds.width,bounds.height);
      const x=bounds.left+session.point.x*bounds.width,y=bounds.top+session.point.y*bounds.height;
      context.beginPath();context.arc(x,y,8,0,Math.PI*2);context.strokeStyle='#02ef5f';context.lineWidth=2;context.stroke();
      context.beginPath();context.arc(x,y,2,0,Math.PI*2);context.fillStyle='#ffffff';context.fill();
    }
    $('screen-placeholder').hidden=Boolean(session.frame&&session.image);
  }
  async connect(event){
    event.preventDefault();this.clearError();
    let session=this.selected;
    if(session.connecting || session.peer&&session.alive){this.error('Use + para abrir outro PC sem encerrar esta conexão.');return;}
    const id=session.id;
    session.connecting=true;this.changed(session);
    try{
      if(!window.RTCPeerConnection)throw new Error('Este navegador não oferece WebRTC. Use o Safari atualizado no iPhone.');
      const offer=await decodeSignal($('invite').value,{type:'offer'});
      requireIceCandidates(offer.sdp);
      if(this.sessions.get(id)!==session)return;
      if(!session.alive){const wasSelected=this.selected===session;session.close();session=new RemoteSession(id,this);session.connecting=true;this.sessions.set(id,session);if(wasSelected)this.selected=session;}
      session.offerName=offer.name;session.name=offer.name;session.status('Preparando a conexão','Conferindo o convite e a rede do iPhone…','network');
      const peer=new RTCPeerConnection({iceServers:offer.iceServers?validateIce(offer.iceServers):DEFAULT_ICE,bundlePolicy:'max-bundle'});
      session.observePeer(peer);
      peer.addEventListener('datachannel',event=>{
        if(!session.alive){event.channel.close();return;}
        if(event.channel.label!=='aliancadx-v1'||session.channel){event.channel.close();session.fail('O Conector abriu um canal inesperado. Gere outro convite.');return;}
        session.attach(peer,event.channel,offer.nonce);
      });
      await peer.setRemoteDescription({type:'offer',sdp:offer.sdp});
      await peer.setLocalDescription(await peer.createAnswer());const routes=await gatherIce(peer,session.abort.signal);
      if(!session.alive)return;
      session.answerToken=await encodeSignal(createSignal('answer',peer.localDescription.sdp,this.phoneName(),offer));
      $('invite').value='';session.status('Devolva a resposta ao PC','Rede do iPhone: '+iceSummaryText(routes)+'. Copie, compartilhe ou salve a resposta abaixo. No PC, aplique-a no Conector e aprove o pedido no Hospedeiro.','network');
      session.negotiationTimeout=setTimeout(()=>{
        if(session.alive&&!session.wire)session.fail('A conexão direta não foi concluída em 60 segundos. Confira se a resposta foi aplicada no PC. Se já foi aplicada, tente outra rede ou um servidor TURN próprio e gere outro convite.');
      },60000);
      session.expiry=setTimeout(()=>{if(session.alive&&!session.connected)session.fail('O convite expirou. Gere um novo convite no Conector para continuar.');},Math.max(1,offer.expiresAt-Date.now()));
    }catch(reason){if(reason.name!=='AbortError'){this.error(reason.message);session.fail(reason.message);}}
    session.connecting=false;this.changed(session);
  }
  fullscreen(enabled=!document.body.classList.contains('focus-mode')){
    document.body.classList.toggle('focus-mode',enabled);
    $('fullscreen-button').querySelector('span').textContent=enabled?'Sair da tela cheia':'Tela cheia';
    if(enabled)this.selected?.release();
    requestAnimationFrame(()=>this.paint(this.selected));
  }
  updateOnline(){$('offline-banner').hidden=navigator.onLine; if(!navigator.onLine)for(const session of this.sessions.values())session.release();}
  bind(){
    $('connect-form').addEventListener('submit',event=>this.connect(event));
    $('add-session').addEventListener('click',()=>this.add());
    $('double-click').addEventListener('click',()=>this.selected?.click('left',2));$('right-click').addEventListener('click',()=>this.selected?.click('right',1));
    $('keyboard-toggle').addEventListener('click',()=>this.toggle('keyboard'));
    $('files-toggle').addEventListener('click',()=>this.toggle('files'));
    $('fullscreen-button').addEventListener('click',()=>this.fullscreen());
    $('end-session').addEventListener('click',()=>{
      const session=this.selected;if(!session)return;
      if((session.connected||session.peer)&&session.alive&&!confirm('Encerrar somente '+session.label+'?\n\nAs outras sessões continuarão abertas.'))return;
      this.remove(session);
    });
    $('monitor-select').addEventListener('change',()=>{const session=this.selected;if(!session?.ready())return;const id=Number($('monitor-select').value);session.release();session.send({type:'monitor',id});session.invalidateFrame();session.lastFrameAt=performance.now();session.status('Alterando o monitor','Aguardando uma imagem da área escolhida.','ready');});
    $('pause-button').addEventListener('click',()=>{const session=this.selected;if(!session?.connected)return;session.release();session.send({type:'sharing',enabled:!session.sharing});});
    $('send-text').addEventListener('click',async()=>{const owner=this.selected;try{await owner?.text($('text-input').value);}catch(reason){this.error(reason.message);}});
    for(const button of $('shortcut-buttons').querySelectorAll('[data-shortcut]'))button.addEventListener('click',()=>this.selected?.keyStroke(button.dataset.shortcut.split('+')));
    $('fit-view').addEventListener('click',()=>this.selected?.fit());$('scroll-up').addEventListener('click',()=>this.selected?.wheel(3));$('scroll-down').addEventListener('click',()=>this.selected?.wheel(-3));
    for(const [name,type]of[['pointerdown','down'],['pointermove','move'],['pointerup','up'],['pointercancel','cancel']])this.canvas.addEventListener(name,event=>{
      if(!this.selected?.ready())return;event.preventDefault();
      if(type==='down'){this.canvas.setPointerCapture(event.pointerId);this.canvas.focus({preventScroll:true});}
      this.selected.pointer(type,event,this.stage);
    });
    this.canvas.addEventListener('contextmenu',event=>event.preventDefault());
    $('copy-answer').addEventListener('click',async()=>{const session=this.selected;if(session?.answerToken){const copied=await copyText(session.answerToken,$('answer-token'));session.status(session.title,copied?'Resposta copiada. Cole-a no Conector do PC.':'A resposta foi selecionada. Copie-a manualmente e cole no Conector do PC.');}});
    $('share-answer').addEventListener('click',async()=>{
      const session=this.selected;if(!session?.answerToken)return;
      if(navigator.share){try{await navigator.share({title:'Resposta privada AliançaDX',text:session.answerToken});return;}catch(reason){if(reason.name==='AbortError')return;}}
      const copied=await copyText(session.answerToken,$('answer-token'));session.status(session.title,copied?'Resposta copiada. Envie somente ao responsável pelo PC.':'Use Copiar resposta ou Salvar arquivo para devolver a resposta ao PC.');
    });
    $('save-answer').addEventListener('click',()=>{if(this.selected?.answerToken)saveText(this.selected.answerToken,'AliancaDX-resposta-iPhone.aadxanswer');});
    $('import-invitation').addEventListener('click',()=>$('invite-file').click());
    $('invite-file').addEventListener('change',async()=>{
      const file=$('invite-file').files?.[0];if(!file)return;
      try{if(file.size>SIGNAL_LIMIT*2)throw new Error('O arquivo de convite é grande demais.');$('invite').value=await file.text();this.clearError();}catch(reason){this.error(reason.message);}finally{$('invite-file').value='';}
    });
    $('file-input').addEventListener('click',()=>{this.pendingFilePickerOwner=this.selected;});
    $('file-input').addEventListener('change',()=>{
      const owner=this.pendingFilePickerOwner||this.selected;this.pendingFilePickerOwner=null;
      if(owner!==this.selected || this.sessions.get(owner?.id)!==owner || !owner?.alive){$('file-input').value='';this.fileSelected=null;this.fileOwner=null;this.error('A sessão mudou enquanto o seletor estava aberto. Escolha o arquivo novamente na sessão correta.');return;}
      this.fileOwner=owner;this.fileSelected=$('file-input').files?.[0]||null;
    });
    $('send-file').addEventListener('click',async()=>{
      const owner=this.fileOwner||this.selected;
      if(owner!==this.selected){this.error('O arquivo foi escolhido em outra sessão. Volte àquela sessão ou escolha o arquivo novamente.');return;}
      try{if(!this.fileSelected)throw new Error('Escolha um arquivo de até 25 MiB.');owner.release();await owner.files.upload(this.fileSelected);}catch(reason){this.error(reason.message);}
    });
    $('request-file').addEventListener('click',()=>{try{this.selected?.release();this.selected?.files.request();}catch(reason){this.error(reason.message);}});
    $('cancel-file').addEventListener('click',()=>this.selected?.files.cancel());
    $('install-button').addEventListener('click',()=>{$('install-guide').open=true;$('install-guide').scrollIntoView({behavior:'smooth',block:'start'});});
  }
  toggle(name){
    const panel=$(name+'-panel'),button=$(name+'-toggle');panel.hidden=!panel.hidden;button.setAttribute('aria-expanded',String(!panel.hidden));this.selected?.release();
  }
}
const app=new PhoneApp();
if('serviceWorker'in navigator && location.protocol==='https:')navigator.serviceWorker.register('./sw.js',{scope:'./'}).catch(()=>{});
