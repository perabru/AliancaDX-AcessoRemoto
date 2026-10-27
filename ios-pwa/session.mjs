import { FramedChannel, fitBounds, clickCommands, validFrame, inputAllowed } from './protocol.mjs';
import { MobileFiles } from './files.mjs';
import { SessionTraffic } from './metrics.mjs';

// Every point of the canvas is a relative touchpad, including letterboxing.
// Touching/holding never presses a mouse button; dragging is an explicit control.
export const TOUCHPAD_GAIN = 1.25;
const clamp=value=>Math.max(0,Math.min(1,value));
const sameGestureView=(a,b)=>['left','top','width','height'].every(key=>a[key]===b[key]);
const frameNumber=message=>message?.streamVersion===1&&Number.isSafeInteger(message.frameId)&&message.frameId>0?message.frameId:0;
const closeImage=image=>{try{image?.close?.();}catch{}};

export async function decodeJpegImage(bytes){
  const blob=new Blob([bytes],{type:'image/jpeg'});
  if(typeof createImageBitmap==='function'){try{return await createImageBitmap(blob);}catch{}}
  const url=URL.createObjectURL(blob);
  try{return await new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>resolve(image);image.onerror=()=>reject(new Error('Não foi possível decodificar a tela.'));image.src=url;});}
  finally{URL.revokeObjectURL(url);}
}

export class RemoteSession {
  constructor(id, app) {
    this.id=id;this.app=app;this.connected=false;this.sharing=false;this.localPaused=false;this.promptActive=false;this.captureAvailable=true;
    this.traffic=new SessionTraffic();
    this.frame=null;this.image=null;this.pendingFrame=null;this.decoding=false;this.presentation=null;this.presentationScheduled=false;this.presentedId=0;this.frameEpoch=0;this.alive=true;this.stale=false;this.point={x:.5,y:.5};
    this.name='Conexão '+id;this.stage='invite';this.message='Abra o convite do Conector para iPhone.';this.title='Pronto para conectar';
    this.zoom=1;this.panX=0;this.panY=0;this.lastResume=0;this.lastFrameAt=0;this.typingEpoch=0;this.wire=null;this.abort=new AbortController();this.download=null;
    this.fileText='Arquivos de até 25 MiB. Quem recebe precisa autorizar.';this.fileProgress=0;this.fileBusy=false;
    this.files=new MobileFiles(message=>this.send(message),(text,progress,busy)=>{this.fileText=text;this.fileProgress=progress;this.fileBusy=busy;this.changed();},
      async offer=>{this.release();this.app.select(this.id);this.app.restoreCommands?.();return this.alive && confirm('Receber de '+this.label+'?\n\n'+offer.name+'\n'+(offer.size/1048576).toFixed(2)+' MiB\n\nO arquivo será conferido antes de ficar disponível para salvar.');},
      (blob,name)=>{if(this.download)URL.revokeObjectURL(this.download.url);this.download={url:URL.createObjectURL(blob),name};this.changed();});
    this.health=setInterval(()=>this.checkHealth(),1000);
  }
  get label(){return this.id+' · '+this.name;}
  get selected(){return this.app.selected===this;}
  get visible(){return !document.hidden;}
  ready(){return inputAllowed(this,this.selected,this.visible);}
  changed(){this.app.changed(this);}
  status(title,message,stage=this.stage){
    const changed=title!==this.title||message!==this.message||stage!==this.stage;
    this.title=title;this.message=message;this.stage=stage;if(changed)this.changed();return changed;
  }
  repaint(){if(this.app.requestPaint)this.app.requestPaint(this);else this.app.paint(this);}
  observePeer(peer){
    if(this.observedPeer===peer)return;
    this.observedPeer=peer;this.peer=peer;
    peer.addEventListener('iceconnectionstatechange',()=>{
      if(!this.alive||this.connected)return;
      if(peer.iceConnectionState==='checking')this.status('Verificando a conexão','A resposta foi preparada. Devolva-a ao PC para concluir a conexão direta.','network');
      else if(peer.iceConnectionState==='failed')this.fail('A rede bloqueou a conexão WebRTC. Tente outra rede ou configure um servidor TURN próprio no Conector.');
    });
    peer.addEventListener('connectionstatechange',()=>{
      if(!this.alive)return;
      clearTimeout(this.networkLoss);
      if(peer.connectionState==='failed')this.fail('A rede não permitiu a conexão direta. Tente outra rede ou peça ao responsável um servidor TURN próprio.');
      else if(peer.connectionState==='disconnected'){
        this.traffic.stop();this.app.trafficChanged?.(this);
        this.release();this.invalidateFrame();this.status('Conexão instável','Aguardando a rede se recuperar…');
        this.networkLoss=setTimeout(()=>{if(this.alive&&peer.connectionState!=='connected')this.fail('A conexão com o PC foi perdida. Gere um novo convite para tentar novamente.');},12000);
      }else if(peer.connectionState==='connected'){
        if(this.wire && !this.traffic.active)this.traffic.start();
        if(this.connected){this.status('PC conectado','Solicitando uma imagem atual…','ready');this.resume();}
        else this.status('Canal seguro estabelecido','Aguardando o pedido de acesso e a aprovação no Hospedeiro.','approval');
      }else if(peer.connectionState==='connecting')this.status('Estabelecendo o canal seguro','Confira se a resposta já foi aplicada no Conector do PC.','network');
    });
  }
  attach(peer,channel,nonce) {
    this.observePeer(peer);this.channel=channel;
    let opened=false;
    const open=()=>{
      if(opened||!this.alive||channel.readyState!=='open')return;
      opened=true;clearTimeout(this.negotiationTimeout);
      try{
        this.traffic.start();
        this.wire=new FramedChannel(channel,message=>this.handle(message),reason=>this.fail(reason.message),(direction,bytes)=>this.traffic.add(direction,bytes));
        this.status('Aguardando aprovação no PC','Confira e aprove a solicitação na janela do Hospedeiro.','approval');
        this.send({type:'ios_connect',nonce,name:this.app.phoneName()});
      }catch(reason){this.fail(reason.message);}
    };
    channel.addEventListener('open',open);
    if(channel.readyState==='open')open();
    channel.addEventListener('close',()=>{if(this.alive)this.fail('A conexão foi encerrada. Gere um novo convite no Conector para retomar o acesso.');});
  }
  send(message){return this.alive && this.wire?.send(message) || false;}
  handle(message){
    if(!this.alive)return;
    if(message.type==='bridge_state'){
      if(message.state==='authorized'){
        this.connected=true;this.name=String(message.computerName||this.offerName||'PC remoto').replace(/[\x00-\x1f\x7f]/g,'').slice(0,80);
        this.answerToken=null;this.lastFrameAt=performance.now();
        this.status('Acesso autorizado','Aguardando a primeira imagem do PC…','ready');this.send({type:'stream_ready',version:1,maxInFlight:2,ackCumulative:true});
      }else if(message.state==='awaiting')this.status('Aguardando aprovação no PC',message.message||'O responsável precisa aprovar o acesso no Hospedeiro.','approval');
      else if(message.state==='closed'||message.state==='error')this.fail(message.message||'O Hospedeiro encerrou a conexão.');
      return;
    }
    if(!this.connected)return;
    if(this.files.handle(message))return;
    if(message.type==='state'){
      const wasSharing=this.sharing;
      this.localPaused=Boolean(message.localPaused);this.promptActive=Boolean(message.promptActive);this.captureAvailable=message.captureAvailable!==false;
      this.sharing=Boolean(message.sharing)&&!this.localPaused&&!this.promptActive&&this.captureAvailable;
      if(message.computerName)this.name=String(message.computerName).replace(/[\x00-\x1f\x7f]/g,'').slice(0,80);
      if(!this.sharing){
        this.release();this.invalidateFrame();
        this.status(this.localPaused?'Tela pausada no PC':this.promptActive?'Confirmação no PC':!this.captureAvailable?'Captura indisponível':'Tela pausada',
          this.localPaused?'O responsável precisa retomar a tela no Hospedeiro.':this.promptActive?'Aguarde a confirmação ou escolha de arquivo no PC.':!this.captureAvailable?'Aguardando o Hospedeiro recuperar a imagem.':'Use Retomar tela para solicitar a imagem.','ready');
      }else if(!wasSharing){this.invalidateFrame();this.lastFrameAt=performance.now();this.status('Acesso autorizado','Aguardando a imagem do PC…','ready');this.resume();}
      this.changed();return;
    }
    if(message.type==='screen'){
      if(!validFrame(message)){this.status('Imagem inválida','A imagem recebida não pôde ser exibida. Aguardando outra imagem.','ready');this.invalidateFrame();this.resume();return;}
      if(!this.selected || !this.visible || !this.sharing)return;
      if(frameNumber(message)&&frameNumber(message)<=this.presentedId)return;
      this.pendingFrame=message;this.decodeNext();return;
    }
    if(message.type==='session_end'){this.fail(message.reason||'O acesso foi encerrado no PC.');return;}
    if(message.type==='error')this.app.restoreCommands?.();
    if(message.type==='error'||message.type==='log')this.status(this.title,String(message.message||message.text||'').slice(0,600));
  }
  async decodeNext(){
    if(this.decoding||!this.pendingFrame||!this.alive)return;
    this.decoding=true;const message=this.pendingFrame;this.pendingFrame=null;const epoch=this.frameEpoch,started=performance.now();let image;
    try{
      if(!/^[A-Za-z0-9+/]+={0,2}$/.test(message.image))throw new Error('Formato da imagem inválido.');
      const raw=atob(message.image),bytes=new Uint8Array(raw.length);
      for(let at=0;at<raw.length;at++)bytes[at]=raw.charCodeAt(at);
      const dimensions=jpegSize(bytes);
      if(!dimensions||dimensions.width>8192||dimensions.height>8192||dimensions.width*dimensions.height>32*1024*1024)throw new Error('A imagem excede o limite de exibição.');
      image=await decodeJpegImage(bytes);
      if(!this.canPresent(epoch)||frameNumber(message)&&frameNumber(message)<=this.presentedId){closeImage(image);return;}
      const old=this.presentation;
      this.presentation={message,image,epoch,decodeMs:performance.now()-started};image=null;
      if(old)closeImage(old.image);
      this.schedulePresentation();
    }catch(reason){if(image)closeImage(image);if(this.alive&&epoch===this.frameEpoch){this.invalidateFrame();this.stale=true;this.status('Aguardando outra imagem',reason.message,'ready');this.resume();}}
    finally{this.decoding=false;if(this.pendingFrame)this.decodeNext();}
  }
  canPresent(epoch){return this.alive&&epoch===this.frameEpoch&&this.selected&&this.visible&&this.sharing;}
  schedulePresentation(){
    if(this.presentationScheduled)return;
    this.presentationScheduled=true;
    requestAnimationFrame(()=>{
      this.presentationScheduled=false;const candidate=this.presentation;this.presentation=null;if(!candidate)return;
      const {message,image,epoch,decodeMs}=candidate,id=frameNumber(message);
      if(!this.canPresent(epoch)||id&&id<=this.presentedId){closeImage(image);return;}
      if(this.frame&&this.frame.captureId!==message.captureId)this.release();
      const previous=this.image;
      this.image=image;this.frame=message;this.stale=false;this.lastFrameAt=performance.now();
      this.name=String(message.computerName||this.name).replace(/[\x00-\x1f\x7f]/g,'').slice(0,80);
      const metadata=JSON.stringify([this.name,message.monitorId,(Array.isArray(message.monitors)?message.monitors:[]).slice(0,64).map(item=>item?.id)]);
      const metadataChanged=this.presentedMetadata!==metadata;this.presentedMetadata=metadata;
      const renderStart=performance.now();
      try{this.app.paint(this);}catch(reason){if(previous!==image)closeImage(previous);this.invalidateFrame();this.status('Aguardando outra imagem',reason.message,'ready');this.resume();return;}
      if(previous!==image)closeImage(previous);if(id)this.presentedId=id;
      const changed=this.status('PC conectado','Deslize em qualquer parte da tela para mover o cursor. Use os botões para clicar ou ativar Arrastar.','ready');
      if(!changed&&metadataChanged)this.changed();
      // The next callback runs after a browser paint opportunity. Decoding is
      // already free to consume its single newest waiting frame meanwhile.
      requestAnimationFrame(()=>{
        if(!this.canPresent(epoch)||this.frame!==message)return;
        if(id&&id>(this.feedbackId||0)&&this.send({type:'stream_feedback',frameId:id,decodeMs:Math.min(30000,Math.round(decodeMs*100)/100),renderMs:Math.min(30000,Math.round((performance.now()-renderStart)*100)/100),cumulative:true}))this.feedbackId=id;
      });
    });
  }
  invalidateFrame(){
    if(this.drag || this.dragHeld || this.pointers?.size)this.release();
    this.frameEpoch++;closeImage(this.image);if(this.presentation)closeImage(this.presentation.image);this.presentation=null;this.frame=null;this.image=null;this.pendingFrame=null;this.bounds=null;this.typingEpoch++;
    if(this.selected)this.app.paint(this);this.changed();
  }
  setSelected(value){
    if(!value){this.release();this.invalidateFrame();}
    else{this.lastFrameAt=performance.now();this.resume();}
  }
  resume(){
    if(!this.connected || !this.selected || !this.visible || !this.sharing)return;
    const now=performance.now();if(now-this.lastResume<1000)return;this.lastResume=now;
    this.send({type:'stream_resume',version:1});
  }
  checkHealth(){
    this.app.trafficChanged?.(this);
    if(this.alive && this.selected && this.visible && this.connected && this.sharing && !this.stale && performance.now()-this.lastFrameAt>6500){
      this.release();this.invalidateFrame();this.stale=true;this.status('Aguardando imagem atual','A conexão continua aberta, mas ainda não chegou uma nova imagem.','ready');this.resume();
    }
  }
  releaseButtons(){
    this.typingEpoch++;this.send({type:'input',action:'release'});this.dragHeld=false;this.dragCapture=null;
    this.app.inputChanged?.(this);
  }
  release(){
    this.releaseButtons();this.drag=null;this.pinch=null;this.pointers?.clear();
  }
  pointMessage(action,point=this.point){return {type:'input',action,captureId:this.frame?.captureId||'',x:point.x,y:point.y};}
  click(button='left',count=1){
    if(!this.ready())return false;
    const capture=this.frame.captureId,point={...this.point};
    this.releaseButtons();
    for(const command of clickCommands(capture,point,button,count)){if(!this.ready()||this.frame.captureId!==capture)return false;if(!this.send(command))return false;}
    return true;
  }
  wheel(delta){if(this.ready()){if(this.dragHeld)this.releaseButtons();this.send({...this.pointMessage('wheel'),delta:Math.max(-20,Math.min(20,delta))});}}
  keyStroke(keys){
    if(!this.ready())return false;this.typingEpoch++;const capture=this.frame.captureId;
    for(const key of keys)if(!this.send({type:'input',action:'key',captureId:capture,key,down:true}))return false;
    for(const key of [...keys].reverse())this.send({type:'input',action:'key',captureId:capture,key,down:false});
    return true;
  }
  async text(text){
    if(typeof text!=='string'||text.length>1000||/[^\x20-\x7e\r\n\t]/.test(text))throw new Error('Use até 1.000 caracteres básicos, sem acentos. O teclado segue a configuração do PC.');
    if(!this.ready())throw new Error('Aguarde a tela do PC para digitar.');
    this.release();const epoch=++this.typingEpoch,capture=this.frame.captureId;
    for(const character of text.replace(/\r\n/g,'\n')){
      if(!this.ready()||epoch!==this.typingEpoch||capture!==this.frame.captureId)return;
      const key=character==='\n'||character==='\r'?'enter':character==='\t'?'tab':character===' '?'space':/[A-Z]/.test(character)?character.toLowerCase():character;
      const shifted=/[A-Z]/.test(character);
      if(shifted)this.send({type:'input',action:'key',captureId:capture,key:'shift',down:true});
      if(!this.send({type:'input',action:'key',captureId:capture,key,down:true})){this.release();return;}
      this.send({type:'input',action:'key',captureId:capture,key,down:false});
      if(shifted)this.send({type:'input',action:'key',captureId:capture,key:'shift',down:false});
      await new Promise(resolve=>setTimeout(resolve,14));
    }
  }
  fit(){this.release();this.zoom=1;this.panX=0;this.panY=0;if(this.selected)this.repaint();}
  layout(width,height){
    if(!this.image)return null;
    const base=fitBounds(width,height,this.image.naturalWidth||this.image.width,this.image.naturalHeight||this.image.height);
    const w=base.width*this.zoom,h=base.height*this.zoom;
    this.panX=Math.max(-Math.max(0,(w-width)/2),Math.min(Math.max(0,(w-width)/2),this.panX));
    this.panY=Math.max(-Math.max(0,(h-height)/2),Math.min(Math.max(0,(h-height)/2),this.panY));
    return this.bounds={left:(width-w)/2+this.panX,top:(height-h)/2+this.panY,width:w,height:h};
  }
  toggleDrag(){
    if(this.dragHeld){this.releaseButtons();return false;}
    if(!this.ready())return false;
    this.releaseButtons();
    const capture=this.frame.captureId;
    if(!this.send(this.pointMessage('move')))return false;
    if(!this.ready()||this.frame.captureId!==capture)return false;
    if(!this.send({...this.pointMessage('button'),button:'left',down:true})){this.release();return false;}
    this.dragHeld=true;this.dragCapture=capture;this.app.inputChanged?.(this);return true;
  }
  pointer(type,event,stage){
    if(!this.ready()){
      if(this.drag || this.dragHeld || this.pointers?.size)this.release();
      return;
    }
    if(type==='cancel'){this.release();return;}
    const rect=stage.getBoundingClientRect(),xy={x:event.clientX-rect.left,y:event.clientY-rect.top};
    if(!Number.isFinite(xy.x)||!Number.isFinite(xy.y)||!Number.isFinite(rect.width)||!Number.isFinite(rect.height)||rect.width<=0||rect.height<=0){this.release();return;}
    if(this.dragHeld && this.dragCapture!==this.frame.captureId){this.release();return;}
    if(this.drag && !sameGestureView(this.drag.view,rect)){this.release();return;}
    this.pointers ||= new Map();
    if(type==='down'){
      if(event.button!=null && event.button!==0)return;
      if(xy.x<0||xy.y<0||xy.x>rect.width||xy.y>rect.height)return;
      if(this.pointers.has(event.pointerId)){this.release();return;}
      this.pointers.set(event.pointerId,xy);
      if(this.pointers.size===2){
        if(this.dragHeld){this.send({...this.pointMessage('button'),button:'left',down:false});this.dragHeld=false;this.dragCapture=null;this.app.inputChanged?.(this);}
        this.drag=null;
        const [a,b]=[...this.pointers.values()];this.pinch={distance:Math.hypot(a.x-b.x,a.y-b.y),zoom:this.zoom,panX:this.panX,panY:this.panY,cx:(a.x+b.x)/2,cy:(a.y+b.y)/2};return;
      }
      if(this.pointers.size!==1 || this.pinch)return;
      this.drag={pointer:event.pointerId,last:xy,capture:this.frame.captureId,
        view:{left:rect.left,top:rect.top,width:rect.width,height:rect.height}};
      // The anchor only remembers the finger; never jump the remote cursor to it.
      return;
    }
    if(type==='move'||type==='up'){
      if(!this.pointers.has(event.pointerId))return;
      this.pointers.set(event.pointerId,xy);
      if(this.pointers.size>=2 && this.pinch){
        if(type==='move'){
          const [a,b]=[...this.pointers.values()],p=this.pinch;
          this.zoom=Math.max(1,Math.min(6,p.zoom*Math.hypot(a.x-b.x,a.y-b.y)/Math.max(1,p.distance)));
          this.panX=p.panX+(a.x+b.x)/2-p.cx;this.panY=p.panY+(a.y+b.y)/2-p.cy;this.repaint();
        }
      }else if(this.drag?.pointer===event.pointerId){
        if(this.drag.capture!==this.frame.captureId){this.release();return;}
        const dx=xy.x-this.drag.last.x,dy=xy.y-this.drag.last.y;
        this.drag.last=xy;
        if(dx||dy){
          this.point={x:clamp(this.point.x+dx/rect.width*TOUCHPAD_GAIN),y:clamp(this.point.y+dy/rect.height*TOUCHPAD_GAIN)};
          if(!this.send(this.pointMessage('move'))){this.release();return;}
          this.repaint();
        }
      }
      if(type==='up'){
        this.pointers.delete(event.pointerId);
        if(this.drag?.pointer===event.pointerId)this.drag=null;
        // After a pinch, every finger must lift before a new mousepad gesture.
        if(this.pointers.size===0)this.pinch=null;
      }
    }
  }
  fail(message){if(!this.alive)return;this.release();this.files.close();this.traffic.stop();this.app.trafficChanged?.(this);this.alive=false;this.connected=false;this.sharing=false;this.invalidateFrame();this.status('Conexão encerrada',String(message).slice(0,1000),'invite');this.abort.abort();this.peer?.close();this.wire?.shutdown();clearTimeout(this.networkLoss);clearTimeout(this.negotiationTimeout);clearInterval(this.health);}
  close(){if(this.disposed)return;this.disposed=true;this.release();this.files.close();this.traffic.stop();this.app.trafficChanged?.(this);this.alive=false;this.connected=false;this.abort.abort();clearTimeout(this.networkLoss);clearTimeout(this.negotiationTimeout);clearTimeout(this.expiry);clearInterval(this.health);this.wire?.shutdown();this.peer?.close();this.invalidateFrame();if(this.download)URL.revokeObjectURL(this.download.url);}
}

/** Read JPEG SOF dimensions before allocating its decoded bitmap. */
export function jpegSize(bytes){
  if(bytes.length<4||bytes[0]!==255||bytes[1]!==216)return null;
  let at=2;
  while(at+4<=bytes.length){
    if(bytes[at++]!==255)return null;while(bytes[at]===255)at++;const marker=bytes[at++];
    if(marker===217||marker===218)return null;
    if(marker===1||marker>=208&&marker<=215)continue;
    if(at+2>bytes.length)return null;
    const length=(bytes[at]<<8)|bytes[at+1];if(length<2||at+length>bytes.length)return null;
    if([192,193,194,195,197,198,199,201,202,203,205,206,207].includes(marker)){
      if(length<7)return null;const height=(bytes[at+3]<<8)|bytes[at+4],width=(bytes[at+5]<<8)|bytes[at+6];
      return width&&height?{width,height}:null;
    }
    at+=length;
  }
  return null;
}
