import { ScreenPresenter } from './stream-quality.mjs';
import { RemoteSession } from './session.mjs';
import { trafficText } from './metrics.mjs';
import { SessionView, isolateControl } from './session-view.mjs';
import { FavoriteStore } from './favorites.mjs';
import { decodeSignal, createSignal, encodeSignal, gatherIce, iceSummaryText, requireIceCandidates, drawQr, copyText, saveText, DEFAULT_ICE, validateIce, SIGNAL_LIMIT } from './signaling.mjs';

const $=id=>document.getElementById(id);
const fragment=new URLSearchParams(location.hash.slice(1)).get('invite');
if(location.hash)history.replaceState(null,'',location.pathname);
const stages=['invite','network','approval','ready'];

const STREAM_MODE_KEY='aliancadx-stream-mode';
const streamModeLabel=mode=>({auto:'Automático',latency:'Resposta rápida',quality:'Mais nitidez'}[mode]||'Automático');
const validStreamMode=mode=>['auto','latency','quality'].includes(mode);
const STREAM_SETTINGS_MARKUP='<div id="stream-quality-control" class="stream-quality-control">\n  <button id="stream-quality-toggle" class="stream-quality-toggle" type="button" aria-label="Qualidade da imagem: Automático" aria-haspopup="menu" aria-controls="stream-quality-menu" aria-expanded="false" title="Qualidade da imagem"><svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6"><path d="m9.5 3-.7 2.6-2 .9-2.4-.7L2 10l1.9 1.9v2.2L2 16l2.4 4.2 2.5-.7 1.9 1 .7 2.5h5l.7-2.6 1.9-.9 2.5.7L22 16l-1.9-1.9v-2.2L22 10l-2.4-4.2-2.5.7-1.9-.9L14.5 3Z"/><circle cx="12" cy="13" r="3.2"/></svg><span>Imagem</span></button>\n  <div id="stream-quality-backdrop" class="stream-quality-backdrop" hidden></div>\n  <section id="stream-quality-menu" class="stream-quality-menu" role="menu" aria-labelledby="stream-quality-title" hidden>\n    <header><div><span class="stream-quality-eyebrow">IMAGEM E RESPOSTA</span><h3 id="stream-quality-title">Qualidade da sessão</h3></div><button id="stream-quality-close" type="button" aria-label="Fechar qualidade da imagem">×</button></header>\n    <button type="button" class="stream-mode-option" data-stream-mode="auto" role="menuitemradio" aria-label="Automático" aria-checked="true"><span class="stream-mode-check" aria-hidden="true">✓</span><span><strong>Automático</strong><small>Equilibra nitidez e resposta conforme a conexão.</small></span></button>\n    <button type="button" class="stream-mode-option" data-stream-mode="latency" role="menuitemradio" aria-label="Resposta rápida" aria-checked="false"><span class="stream-mode-check" aria-hidden="true">✓</span><span><strong>Resposta rápida</strong><small>Prioriza menos atraso; a imagem pode ter menos detalhe.</small></span></button>\n    <button type="button" class="stream-mode-option" data-stream-mode="quality" role="menuitemradio" aria-label="Mais nitidez" aria-checked="false"><span class="stream-mode-check" aria-hidden="true">✓</span><span><strong>Mais nitidez</strong><small>Preserva mais detalhes; pode aumentar o atraso.</small></span></button>\n    <p id="stream-quality-state" class="stream-quality-state" role="status" aria-live="polite">Aguardando o PC informar os modos disponíveis.</p>\n    <p class="stream-quality-privacy">Somente a preferência é lembrada neste navegador.</p>\n  </section>\n</div>';

class PhoneApp {
  constructor(){
    this.sessions=new Map();this.nextId=1;this.selected=null;this.fileOwner=null;this.fileSelected=null;
    this.saved=new FavoriteStore();this.modeDefault='auto';this.modePersistent=true;
    try{const value=globalThis.localStorage?.getItem(STREAM_MODE_KEY);if(validStreamMode(value))this.modeDefault=value;}catch{this.modePersistent=false;}
    this.view=new SessionView(state=>this.applySessionView(state));
    this.canvas=$('remote-canvas');this.stage=$('screen-stage');this.presenter=new ScreenPresenter(this.canvas,$('cursor-canvas'));this.qualityOpen=false;this.qualityHistory=false;this.ensureStreamControls();
    this.add();this.bind();this.syncViewport();
    if(this.saved.deviceName)$('phone-name').value=this.saved.deviceName;
    this.drawFavorites();
    window.addEventListener('resize',()=>this.syncViewport());
    window.addEventListener('orientationchange',()=>{this.restoreCommands();this.syncViewport();});
    window.visualViewport?.addEventListener('resize',()=>this.syncViewport());
    if(fragment){$('invite').value=fragment;this.selected.status('Convite recebido','Toque em Conectar ao PC para preparar a resposta privada.');}
    this.resize=new ResizeObserver(()=>{
      const size={width:this.stage.clientWidth,height:this.stage.clientHeight};
      if(this.stageSize && (size.width!==this.stageSize.width || size.height!==this.stageSize.height))this.selected?.release();
      this.stageSize=size;this.paint(this.selected);
    });this.resize.observe(this.stage);
    document.addEventListener('visibilitychange',()=>{
      if(document.hidden){this.restoreCommands();for(const session of this.sessions.values()){session.release();session.invalidateFrame();}}
      else{this.selected?.resume();this.changed(this.selected);}
    });
    window.addEventListener('blur',()=>{this.restoreCommands();for(const session of this.sessions.values())session.release();});
    window.addEventListener('pagehide',()=>{for(const session of this.sessions.values())session.close();});
    window.addEventListener('online',()=>this.updateOnline());window.addEventListener('offline',()=>this.updateOnline());this.updateOnline();
    document.addEventListener('fullscreenchange',()=>{if(!document.fullscreenElement)this.fullscreen(false);else this.restoreCommands();});
    document.addEventListener('fullscreenerror',()=>this.fullscreen(false));
    window.addEventListener('keydown',event=>{
      if(event.key==='Escape'&&this.qualityOpen){this.closeStreamSettings();event.preventDefault();event.stopPropagation();return;}
      if(event.key==='Escape'&&document.body.classList.contains('focus-mode')){this.fullscreen(false);event.preventDefault();return;}
      if(this.qualityOpen||event.target!==this.canvas||!this.selected?.ready())return;
      const keys={Enter:'enter',Escape:'esc',Backspace:'backspace',Delete:'delete',Tab:'tab',ArrowLeft:'left',ArrowRight:'right',ArrowUp:'up',ArrowDown:'down',Home:'home',End:'end',PageUp:'pageup',PageDown:'pagedown'};
      if(keys[event.key]){this.selected.keyStroke([keys[event.key]]);event.preventDefault();}
      else if(event.key.length===1&&event.key.charCodeAt(0)>=32&&event.key.charCodeAt(0)<=126){this.selected.text(event.key).catch(reason=>this.error(reason.message));event.preventDefault();}
    });
  }
  preferredStreamMode(){return this.modeDefault||'auto';}
  rememberStreamMode(mode){
    if(!validStreamMode(mode))return false;this.modeDefault=mode;
    try{if(!globalThis.localStorage)throw new Error('unavailable');globalThis.localStorage.setItem(STREAM_MODE_KEY,mode);return true;}
    catch{this.modePersistent=false;return false;}
  }
  ensureStreamControls(){
    if(!$('stream-quality-control')){const holder=document.createElement('div');holder.innerHTML=STREAM_SETTINGS_MARKUP;this.stage.append(holder.firstElementChild);}
    const control=$('stream-quality-control'),gear=$('stream-quality-toggle'),menu=$('stream-quality-menu'),backdrop=$('stream-quality-backdrop');
    // Essential inline styles also protect an app updated over cached 7.2 HTML/CSS.
    Object.assign(control.style,{position:'absolute',inset:'0',pointerEvents:'none',zIndex:'10'});
    Object.assign(gear.style,{position:'absolute',top:'12px',right:'12px',width:'52px',minHeight:'52px',display:'flex',flexDirection:'column',justifyContent:'center',alignItems:'center',gap:'1px',padding:'7px',border:'1px solid #d9ffec38',borderRadius:'16px',background:'var(--stream-gear-bg,#09241cb8)',color:'#f1fff8',fontSize:'9px',pointerEvents:'auto'});
    Object.assign(backdrop.style,{position:'fixed',inset:'0',background:'#00170e52',pointerEvents:'auto',zIndex:'20'});
    Object.assign(menu.style,{position:'fixed',width:'320px',maxWidth:'calc(100vw - 24px)',maxHeight:'calc(100dvh - 24px)',overflow:'auto',background:'#102d24',color:'#f4fff8',borderRadius:'20px',padding:'14px',boxShadow:'0 16px 50px #00170e66',pointerEvents:'auto',zIndex:'21'});
  }
  streamSettingsChanged(session){
    if(session!==this.selected)return;
    const gear=$('stream-quality-toggle'),menu=$('stream-quality-menu'),applied=validStreamMode(session.streamModeApplied)?session.streamModeApplied:'auto';
    const supported=session.streamSettingsSupported===true,known=session.streamSettingsKnown===true||session.streamSettingsKnown==null&&Boolean(session.frame);
    gear.disabled=!session.connected;gear.setAttribute('aria-label','Qualidade da imagem: '+streamModeLabel(applied));
    const baseReady=session.connected&&session.frame&&session.sharing&&!session.localPaused&&!session.promptActive&&!session.stale;
    const choice=supported&&session.streamSettingsPending?session.streamModePreference:applied;
    for(const option of menu.querySelectorAll('[data-stream-mode]')){
      option.setAttribute('aria-checked',String(option.dataset.streamMode===choice));
      option.disabled=!baseReady||!supported&&option.dataset.streamMode!=='auto';
    }
    let text;
    if(!session.connected)text='Conecte-se ao PC para escolher a qualidade.';
    else if(!known)text='Aguardando o PC informar os modos disponíveis.';
    else if(!supported)text='Em uso: '+streamModeLabel(applied)+'. Atualize o Hospedeiro e o Conector Windows para 7.3 ou posterior para escolher outros modos.';
    else if(session.streamSettingsPending)text='Aplicando '+streamModeLabel(session.streamModePreference)+'… Em uso: '+streamModeLabel(applied)+'.';
    else if(session.streamSettingsTimedOut)text='O PC ainda não confirmou a mudança. Em uso: '+streamModeLabel(applied)+'. Escolha novamente para tentar.';
    else if(!baseReady)text='Em uso: '+streamModeLabel(applied)+'. Retome a tela para alterar a qualidade.';
    else text='Em uso: '+streamModeLabel(applied)+'. A mudança vale somente para esta sessão.';
    $('stream-quality-state').textContent=text;
    const note=menu.querySelector('.stream-quality-privacy');if(note)note.textContent=this.modePersistent?'Somente a preferência é lembrada neste navegador.':'O navegador não permitiu salvar; a preferência fica nesta aba.';
  }
  openStreamSettings(){
    const session=this.selected;if(!session?.connected)return false;
    if(this.qualityOpen){this.closeStreamSettings();return false;}
    this.view.restore();session.release();this.qualityOpen=true;this.qualitySessionId=session.id;
    const gear=$('stream-quality-toggle'),menu=$('stream-quality-menu');menu.hidden=false;$('stream-quality-backdrop').hidden=false;gear.setAttribute('aria-expanded','true');
    this.streamSettingsChanged(session);this.changed(session);
    const rect=gear.getBoundingClientRect?.()||{right:window.innerWidth||360,bottom:50};
    const width=window.innerWidth||document.documentElement.clientWidth||360,height=window.innerHeight||800;
    const menuWidth=Math.min(320,Math.max(1,width-24)),menuHeight=Math.min(menu.offsetHeight||330,Math.max(1,height-24));
    menu.style.left=Math.max(12,Math.min(rect.right-menuWidth,width-menuWidth-12))+'px';menu.style.top=Math.max(12,Math.min(rect.bottom+8,height-menuHeight-12))+'px';
    try{if(typeof history.pushState==='function'){history.pushState({aliancadxQualityMenu:true},'',location.pathname);this.qualityHistory=true;}}catch{}
    (menu.querySelector('[aria-checked="true"]:not(:disabled)')||$('stream-quality-close')).focus({preventScroll:true});return true;
  }
  closeStreamSettings({restoreFocus=true,applyPreference=true,historyBack=true}={}){
    if(!this.qualityOpen)return false;const session=this.selected;this.qualityOpen=false;this.qualitySessionId=null;
    $('stream-quality-menu').hidden=true;$('stream-quality-backdrop').hidden=true;$('stream-quality-toggle').setAttribute('aria-expanded','false');
    const hadHistory=this.qualityHistory;this.qualityHistory=false;
    if(historyBack&&hadHistory&&history.state?.aliancadxQualityMenu===true)try{history.back();}catch{}
    if(applyPreference)session?.requestStreamMode?.();
    if(session)this.changed(session);if(restoreFocus)$('stream-quality-toggle').focus({preventScroll:true});return true;
  }
  bindStreamSettings(){
    $('stream-quality-toggle').addEventListener('click',event=>{event.preventDefault();event.stopPropagation();this.openStreamSettings();});
    $('stream-quality-close').addEventListener('click',()=>this.closeStreamSettings());
    for(const option of $('stream-quality-menu').querySelectorAll('[data-stream-mode]'))option.addEventListener('click',event=>{
      event.preventDefault();event.stopPropagation();const session=this.selected;if(option.disabled||session?.id!==this.qualitySessionId)return;
      const mode=option.dataset.streamMode;this.closeStreamSettings({restoreFocus:true,applyPreference:false});
      if(session.setStreamMode)session.setStreamMode(mode);else this.rememberStreamMode(mode);this.streamSettingsChanged(session);
    });
    for(const target of [$('stream-quality-toggle'),$('stream-quality-menu'),$('stream-quality-backdrop')])for(const name of ['pointerdown','pointermove','pointerup','pointercancel','contextmenu'])target.addEventListener(name,event=>{event.stopPropagation();if(name==='contextmenu')event.preventDefault();});
    document.addEventListener('pointerdown',event=>{
      if(!this.qualityOpen||$('stream-quality-menu').contains(event.target)||$('stream-quality-toggle').contains(event.target))return;
      const remote=this.stage.contains(event.target);this.closeStreamSettings({restoreFocus:false});
      if(remote){this.qualitySuppressClick=true;event.preventDefault();event.stopPropagation();}
    },true);
    document.addEventListener('click',event=>{if(this.qualitySuppressClick){this.qualitySuppressClick=false;event.preventDefault();event.stopPropagation();}},true);
    window.addEventListener('popstate',()=>{if(this.qualityOpen)this.closeStreamSettings({historyBack:false});});
    $('stream-quality-menu').addEventListener('keydown',event=>{
      if(event.key==='Escape'){this.closeStreamSettings();event.preventDefault();event.stopPropagation();return;}
      const buttons=[...$('stream-quality-menu').querySelectorAll('button:not([disabled])')];if(!buttons.length)return;
      if(['ArrowDown','ArrowUp','Home','End','Tab'].includes(event.key)){
        let at=buttons.indexOf(document.activeElement);if(event.key==='Home')at=0;else if(event.key==='End')at=buttons.length-1;else at=(at+(event.key==='ArrowUp'||event.key==='Tab'&&event.shiftKey?-1:1)+buttons.length)%buttons.length;
        buttons[at].focus();event.preventDefault();event.stopPropagation();
      }
    });
  }
  phoneName(){return $('phone-name').value.replace(/[\x00-\x1f\x7f]/g,'').trim().slice(0,60)||'iPhone';}
  drawFavorites(){
    const rows=this.saved.favorites;
    const state=JSON.stringify({rows,message:this.saved.message,needsReset:this.saved.needsReset,live:[...this.sessions.values()].map(session=>[session.id,session.favoriteId,session.alive&&session.connected])});
    if(state===this.favoritesShown)return;this.favoritesShown=state;
    const list=$('favorites-list');list.replaceChildren();$('favorites-empty').hidden=Boolean(rows.length);
    $('favorites-storage-note').textContent=this.saved.message||'Somente o nome do PC e o último uso ficam salvos neste navegador. Convites, senhas e acesso não ficam salvos.';
    $('favorites-reset').hidden=!this.saved.needsReset;
    for(const row of rows){
      const item=document.createElement('article');item.className='favorite-card';item.setAttribute('role','listitem');
      const title=document.createElement('h3');title.textContent=row.name;
      const note=document.createElement('p');note.className='favorite-note';
      const live=[...this.sessions.values()].find(session=>session.favoriteId===row.id&&session.alive&&session.connected);
      note.textContent=live?'Sessão aberta neste momento':'Use um novo convite do Conector';
      const actions=document.createElement('div');actions.className='favorite-actions';
      const open=document.createElement('button');open.type='button';open.className='button secondary';open.textContent=live?'Abrir sessão':'Reconectar';open.setAttribute('aria-label',(live?'Abrir sessão de ':'Reconectar a ')+row.name);open.addEventListener('click',()=>this.reconnectFavorite(row.id));
      const remove=document.createElement('button');remove.type='button';remove.className='favorite-remove';remove.textContent='×';remove.setAttribute('aria-label','Remover '+row.name+' dos salvos');remove.title='Remover dos salvos';remove.addEventListener('click',()=>{this.saved.remove(row.id);this.drawFavorites();});
      actions.append(open,remove);item.append(title,note,actions);list.append(item);
    }
  }
  reconnectFavorite(id){
    const favorite=this.saved.favorites.find(row=>row.id===id);if(!favorite)return;
    const live=[...this.sessions.values()].find(session=>session.favoriteId===id&&session.alive&&session.connected);
    if(live){this.select(live.id);this.saved.save(favorite.name);this.drawFavorites();$('session-panel').scrollIntoView({behavior:'smooth',block:'start'});return;}
    let session=this.selected;
    if($('invite').value.trim()){this.error('Há um convite no formulário. Conclua essa conexão ou apague o convite antes de preparar outro computador salvo.');return;}
    if(!session||session.connecting||session.connected||session.peer&&session.alive)session=this.add();
    if(!session)return;
    session.reconnectName=favorite.name;this.clearError();this.fullscreen(false);
    session.status('Reconectar a '+favorite.name,'No Conector do Windows, gere um novo convite. Abra o QR pela câmera do celular, cole o novo link ou importe o arquivo. Depois devolva a resposta e aprove o acesso no PC.','invite');
    this.changed(session);$('connect-panel').scrollIntoView({behavior:'smooth',block:'start'});$('invite').focus({preventScroll:true});
  }
  syncViewport(){
    const height=Math.max(1,Math.round(window.visualViewport?.height||window.innerHeight||1));
    if(this.viewportHeight && height!==this.viewportHeight)this.selected?.release();
    this.viewportHeight=height;
    document.documentElement.style.setProperty('--aadx-viewport-height',height+'px');
  }
  add(){
    if(this.sessions.size>=4){this.error('Você pode manter até quatro sessões. Encerre e feche uma aba para adicionar outro PC.');return;}
    const session=new RemoteSession(this.nextId++,this);this.sessions.set(session.id,session);this.select(session.id);
    $('invite').value='';this.clearError();return session;
  }
  select(id){
    this.closeStreamSettings({restoreFocus:false,applyPreference:false});
    const next=this.sessions.get(id);if(!next)return;
    if(this.selected!==next){this.selected?.setSelected(false);this.selected=next;next.setSelected(true);$('text-input').value='';$('file-input').value='';this.fileSelected=null;this.fileOwner=null;this.fullscreen(false);}
    this.changed(next);this.paint(next);
  }
  error(message){this.restoreCommands();$('error-box').textContent=String(message).slice(0,1000);$('error-box').hidden=false;}
  clearError(){$('error-box').textContent='';$('error-box').hidden=true;}
  changed(session){
    if(session.connected&&!session.favoriteRecorded){
      session.favoriteRecorded=true;const favorite=this.saved.save(session.name);session.favoriteId=favorite?.id||null;
    }
    this.drawFavorites();
    this.drawTabs();if(session!==this.selected)return;
    $('connect-title').textContent=session.reconnectName?'Reconectar a '+session.reconnectName:'Conecte seu iPhone ao PC';
    $('reconnect-help').hidden=!session.reconnectName;
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
    this.view.reconcile(this.viewContext());
    $('hide-commands').hidden=!this.view.fullscreen||this.view.hidden||!ready;
    this.trafficChanged(session);this.streamSettingsChanged(session);
    if(this.qualityOpen&&(!session.connected||!session.sharing||session.localPaused||session.promptActive||session.stale))this.closeStreamSettings({restoreFocus:false,applyPreference:false});
    for(const id of ['left-click','double-click','right-click','drag-toggle','pad-scroll-up','pad-scroll-down'])$(id).disabled=!ready;
    this.inputChanged(session);
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
  inputChanged(session){
    if(session!==this.selected)return;
    const active=Boolean(session.dragHeld);
    $('drag-toggle').setAttribute('aria-pressed',String(active));
    $('drag-toggle').setAttribute('aria-label',active?'Soltar o botão esquerdo e encerrar arrasto':'Ativar arrastar com o botão esquerdo');
    $('drag-toggle').title=active?'Soltar e encerrar arrasto':'Ativar arrastar';
    $('drag-toggle').querySelector('.mouse-caption').textContent=active?'Soltar':'Arrastar';
    $('touchpad-status').classList.toggle('is-dragging',active);
    const text=active?'Arrastando · toque em Soltar':'Mousepad · deslize para mover';
    if($('touchpad-mode').textContent!==text)$('touchpad-mode').textContent=text;
  }
  trafficChanged(session){
    if(session!==this.selected)return;
    const now=performance.now(),source=session.id+':'+session.traffic.active;
    if(this.shownTrafficSource===source && now-this.shownTrafficAt<900)return;
    this.shownTrafficSource=source;this.shownTrafficAt=now;
    $('session-traffic-value').textContent=trafficText(session.traffic.snapshot());
    $('session-traffic').setAttribute('aria-label','Tráfego da sessão '+session.label+': '+$('session-traffic-value').textContent+'. Média de 2,5 segundos.');
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
  requestPaint(session){
    if(session!==this.selected||this.paintScheduled)return;
    this.paintScheduled=true;
    requestAnimationFrame(()=>{this.paintScheduled=false;this.paint(this.selected);});
  }
  paint(session){
    if(session!==this.selected)return;
    const visible=this.presenter.paint(session,this.stage.clientWidth,this.stage.clientHeight,window.devicePixelRatio||1);
    $('screen-placeholder').hidden=visible;
  }
  async connect(event){
    event.preventDefault();this.clearError();
    this.saved.setDeviceName(this.phoneName());this.drawFavorites();
    let session=this.selected;
    if(session.connecting || session.peer&&session.alive){this.error('Use + para abrir outro PC sem encerrar esta conexão.');return;}
    const id=session.id;
    session.connecting=true;this.changed(session);
    try{
      if(!window.RTCPeerConnection)throw new Error('Este navegador não oferece WebRTC. Use o Safari atualizado no iPhone.');
      const offer=await decodeSignal($('invite').value,{type:'offer'});
      requireIceCandidates(offer.sdp);
      if(this.sessions.get(id)!==session)return;
      if(!session.alive){const wasSelected=this.selected===session,reconnectName=session.reconnectName;session.close();session=new RemoteSession(id,this);session.reconnectName=reconnectName;session.connecting=true;this.sessions.set(id,session);if(wasSelected)this.selected=session;}
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
  viewContext(){return {session:this.selected?.id,ready:Boolean(this.selected?.ready()),promptActive:Boolean(this.selected?.promptActive),error:!$('error-box').hidden,offline:!navigator.onLine};}
  restoreCommands(){this.closeStreamSettings({restoreFocus:false});this.view.restore();}
  applySessionView({fullscreen,hidden}){
    this.closeStreamSettings({restoreFocus:false,applyPreference:false});
    $('stream-quality-toggle').style.right=fullscreen?'max(90px,calc(env(safe-area-inset-right) + 90px))':'max(12px,env(safe-area-inset-right))';
    const navigation=$('session-tabs').parentElement;if(navigation?.style){navigation.style.right=fullscreen?'calc(max(12px,env(safe-area-inset-right)) + 142px)':'';navigation.style.maxWidth=fullscreen?'calc(100vw - max(12px,env(safe-area-inset-left)) - max(12px,env(safe-area-inset-right)) - 142px)':'';}
    this.selected?.release();
    document.body.classList.toggle('focus-mode',fullscreen);
    document.body.classList.toggle('commands-hidden',fullscreen&&hidden);
    $('fullscreen-button').querySelector('span').textContent=fullscreen?'Sair da tela cheia':'Tela cheia';
    $('hide-commands').hidden=!fullscreen||hidden||!this.selected?.ready();
    $('show-commands').hidden=!fullscreen||!hidden;
    if(hidden){
      for(const name of ['keyboard','files']){$(name+'-panel').hidden=true;$(name+'-toggle').setAttribute('aria-expanded','false');}
      if(document.activeElement!==this.canvas)document.activeElement?.blur?.();
    }
    requestAnimationFrame(()=>this.paint(this.selected));
  }
  fullscreen(enabled=!this.view.fullscreen){this.view.fullscreenTo(enabled);}
  updateOnline(){$('offline-banner').hidden=navigator.onLine; if(!navigator.onLine){this.restoreCommands();for(const session of this.sessions.values())session.release();}}
  bind(){
    this.bindStreamSettings();
    $('connect-form').addEventListener('submit',event=>this.connect(event));
    $('phone-name').addEventListener('change',()=>{this.saved.setDeviceName(this.phoneName());this.drawFavorites();});
    $('favorites-reset').addEventListener('click',()=>{
      if(this.saved.needsReset&&confirm('Recriar os favoritos?\n\nA lista salva que não pôde ser lida será substituída pelos computadores mostrados agora. Nenhuma sessão será encerrada.')){this.saved.reset();this.drawFavorites();}
    });
    $('add-session').addEventListener('click',()=>this.add());
    for(const button of document.querySelectorAll('.mouse-actions button')){
      for(const name of ['pointerdown','pointermove','pointerup','contextmenu'])button.addEventListener(name,event=>{
        event.stopPropagation();
        if(name==='contextmenu')event.preventDefault();
        if(name==='pointerdown'){
          try{button.setPointerCapture(event.pointerId);}catch{}
        }
      });
      button.addEventListener('pointercancel',()=>this.selected?.release());
    }
    $('left-click').addEventListener('click',()=>{if(!this.qualityOpen)this.selected?.click('left',1);});
    $('double-click').addEventListener('click',()=>{if(!this.qualityOpen)this.selected?.click('left',2);});$('right-click').addEventListener('click',()=>{if(!this.qualityOpen)this.selected?.click('right',1);});
    $('drag-toggle').addEventListener('click',()=>{if(!this.qualityOpen)this.selected?.toggleDrag();});
    $('pad-scroll-up').addEventListener('click',()=>{if(!this.qualityOpen)this.selected?.wheel(3);});$('pad-scroll-down').addEventListener('click',()=>{if(!this.qualityOpen)this.selected?.wheel(-3);});
    $('keyboard-toggle').addEventListener('click',()=>this.toggle('keyboard'));
    $('files-toggle').addEventListener('click',()=>this.toggle('files'));
    $('fullscreen-button').addEventListener('click',()=>this.fullscreen());
    for(const id of ['hide-commands','show-commands']){
      isolateControl($(id),()=>this.selected?.release());
      $(id).addEventListener('click',event=>{event.preventDefault();event.stopPropagation();if(id==='hide-commands')this.view.hide(this.viewContext());else this.restoreCommands();});
    }
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
      if(!this.selected)return;
      if(this.qualityOpen)return;
      if(!this.selected.ready()){if(type==='up'||type==='cancel')this.selected.release();return;}
      event.preventDefault();
      if(type==='down'){
        if(event.button!=null&&event.button!==0)return;
        try{this.canvas.setPointerCapture(event.pointerId);}catch{}this.canvas.focus({preventScroll:true});
      }
      this.selected.pointer(type,event,this.stage);
    });
    this.canvas.addEventListener('lostpointercapture',event=>{
      if(this.selected?.pointers?.has(event.pointerId))this.selected.release();
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
    $('reconnect-import').addEventListener('click',()=>$('invite-file').click());
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
    this.restoreCommands();
    const panel=$(name+'-panel'),button=$(name+'-toggle');panel.hidden=!panel.hidden;button.setAttribute('aria-expanded',String(!panel.hidden));this.selected?.release();
  }
}
const app=new PhoneApp();
if('serviceWorker'in navigator && location.protocol==='https:')navigator.serviceWorker.register('./sw.js',{scope:'./'}).catch(()=>{});
