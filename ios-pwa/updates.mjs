/* Manual public release checks. Never pass local connector credentials to GitHub. */
export const RELEASE_API='https://api.github.com/repos/perabru/AliancaDX-AcessoRemoto/releases/latest';
const REPO='https://github.com/perabru/AliancaDX-AcessoRemoto';
const MAX_BYTES=512*1024;
async function boundedText(response){
  if(Number(response.headers?.get('Content-Length'))>MAX_BYTES)throw issue('invalid','A resposta da atualização excedeu o tamanho permitido.');
  if(response.body?.getReader){
    const reader=response.body.getReader(),decoder=new TextDecoder('utf-8',{fatal:true});let size=0,result='';
    try{for(;;){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>MAX_BYTES){await reader.cancel();throw issue('invalid','A resposta da atualização excedeu o tamanho permitido.');}result+=decoder.decode(value,{stream:true});}return result+decoder.decode();}
    finally{reader.releaseLock();}
  }
  const result=await response.text();if(new TextEncoder().encode(result).length>MAX_BYTES)throw issue('invalid','A resposta da atualização excedeu o tamanho permitido.');return result;
}
async function deadline(promise,milliseconds,code,message){
  let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(issue(code,message)),milliseconds);})]);}finally{clearTimeout(timer);}
}
const issue=(code,message)=>Object.assign(new Error(message),{code});
export function parseVersion(value){
  if(typeof value!=='string'||value.length>40||!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value))return null;
  const parts=value.split('.').map(Number);return parts.every(Number.isSafeInteger)?parts:null;
}
export function compareVersions(left,right){
  const a=parseVersion(left),b=parseVersion(right);if(!a||!b)throw issue('invalid','A versão informada não é válida.');
  for(let i=0;i<3;i++){if(a[i]!==b[i])return a[i]>b[i]?1:-1;}return 0;
}
export function validateRelease(value){
  if(!value||value.draft!==false||value.prerelease!==false||typeof value.tag_name!=='string'||!value.tag_name.startsWith('v'))throw issue('invalid','A publicação recebida não é uma versão estável válida.');
  const tag=value.tag_name,version=tag.slice(1);if(!parseVersion(version)||tag!=='v'+version)throw issue('invalid','A versão publicada não é válida.');
  const releaseUrl=REPO+'/releases/tag/'+tag;
  if(value.html_url!==releaseUrl||!Array.isArray(value.assets)||value.assets.length>100)throw issue('invalid','A origem da publicação não pôde ser confirmada.');
  const asset=name=>{
    const found=value.assets.filter(item=>item?.name===name),url=REPO+'/releases/download/'+tag+'/'+name;
    if(found.length!==1||found[0].state!=='uploaded'||!Number.isSafeInteger(found[0].size)||found[0].size<=0||found[0].browser_download_url!==url)throw issue('invalid','O arquivo oficial de atualização está ausente ou não pôde ser confirmado.');
    return url;
  };
  return {version,tag,releaseUrl,pwaUrl:asset('AliancaDX_iOS_PWA_'+version+'.zip'),hostUrl:asset('AliancaDX_Host_Setup.exe')};
}
export async function checkRelease({installedVersion,fetchImpl=globalThis.fetch,signal}={}){
  if(globalThis.navigator?.onLine===false)throw issue('offline','Sem internet. Conecte-se à rede e tente novamente.');
  const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),12000);
  const cancel=()=>abort.abort();signal?.addEventListener('abort',cancel,{once:true});if(signal?.aborted)abort.abort();
  try{
    const response=await fetchImpl(RELEASE_API,{headers:{Accept:'application/vnd.github+json'},credentials:'omit',referrerPolicy:'no-referrer',cache:'no-store',redirect:'error',signal:abort.signal});
    if(response.status===403||response.status===429)throw issue('rate-limit','O GitHub limitou as consultas. Aguarde alguns minutos e tente novamente.');
    if(!response.ok)throw issue('http',response.status===404?'Ainda não há uma publicação estável disponível.':'Não foi possível consultar a atualização (HTTP '+response.status+'). Tente novamente.');
    let value;try{value=JSON.parse(await boundedText(response));}catch(error){if(typeof error.code==='string')throw error;throw issue('invalid','A resposta da atualização não é válida.');}
    const release=validateRelease(value);
    return {...release,installedVersion:parseVersion(installedVersion)?installedVersion:null,newer:parseVersion(installedVersion)?compareVersions(release.version,installedVersion)>0:null};
  }catch(error){
    if(abort.signal.aborted)throw issue('timeout',signal?.aborted?'Consulta cancelada.':'A consulta demorou demais. Tente novamente.');
    if(typeof error.code==='string')throw error;
    throw issue('offline','Não foi possível acessar o GitHub. Confira a internet e tente novamente.');
  }finally{clearTimeout(timer);signal?.removeEventListener('abort',cancel);}
}
export function hasActiveSessions(sessions){
  const values=sessions?.values?Array.from(sessions.values()):Array.from(sessions||[]);
  return values.some(session=>session&&(session.fileBusy||session.alive!==false&&Boolean(session.connecting||session.connected||session.peer||session.wire||session.answerToken)));
}
export function workerMessage(worker,message,{timeout=2200,Channel=globalThis.MessageChannel}={}){
  return new Promise((resolve,reject)=>{
    if(!worker||!Channel){reject(issue('worker','O navegador não conseguiu preparar a atualização.'));return;}
    const channel=new Channel(),timer=setTimeout(()=>finish(null,issue('worker','A atualização ainda não está pronta. Tente novamente.')),timeout);
    const finish=(value,error)=>{clearTimeout(timer);channel.port1.close();channel.port2.close();error?reject(error):resolve(value);};
    channel.port1.onmessage=event=>finish(event.data);
    try{worker.postMessage(message,[channel.port2]);}catch(error){finish(null,issue('worker','A atualização ainda não está pronta. Tente novamente.'));}
  });
}
export async function deployedVersion({fetchImpl=globalThis.fetch,base=globalThis.location?.href}={}){
  const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),12000);
  try{
    const response=await fetchImpl(new URL('./app.mjs',base),{cache:'no-store',credentials:'omit',referrerPolicy:'no-referrer',redirect:'error',signal:abort.signal});
    if(!response.ok)throw issue('site','Não foi possível verificar a versão do site. Tente novamente.');
    const source=await boundedText(response),matches=[...source.matchAll(/^const PWA_APP_VERSION='([^']+)';$/gm)];
    if(matches.length!==1||!parseVersion(matches[0][1]))throw issue('site','Aguarde a atualização do site e tente novamente.');
    return matches[0][1];
  }catch(error){if(abort.signal.aborted)throw issue('timeout','A verificação do site demorou demais. Tente novamente.');if(typeof error.code==='string')throw error;throw issue('site','Não foi possível verificar a versão do site. Tente novamente.');}
  finally{clearTimeout(timer);}
}
export async function prepareWorker(registration,version,{timeout=14000}={}){
  if(!registration?.update)throw issue('worker','Abra o aplicativo pelo site HTTPS para atualizar.');
  const started=Date.now(),message='O site ainda está preparando a atualização. Aguarde e tente novamente.';
  await deadline(registration.update(),timeout,'worker',message);
  const remaining=Math.max(1,timeout-(Date.now()-started));
  const waiting=await new Promise((resolve,reject)=>{
    let observed=null,checking=false,finished=false;
    const timer=setTimeout(()=>done(null,issue('worker',message)),remaining);
    const inspect=()=>{
      if(finished)return;
      if(registration.waiting){done(registration.waiting);return;}
      if(registration.installing&&observed!==registration.installing){observed=registration.installing;observed.addEventListener('statechange',inspect);}
      // A first registration can activate immediately. It still needs explicit confirmation to reload.
      if(registration.active&&!checking){checking=true;workerMessage(registration.active,{type:'AADX_UPDATE_INFO'},{timeout:1000}).then(info=>{if(info?.version===version&&info?.cacheReady===true)done(registration.active);}).catch(()=>{}).finally(()=>{checking=false;});}
    };
    const done=(value,error)=>{if(finished)return;finished=true;clearTimeout(timer);registration.removeEventListener?.('updatefound',inspect);observed?.removeEventListener?.('statechange',inspect);error?reject(error):resolve(value);};
    registration.addEventListener?.('updatefound',inspect);inspect();
  });
  const info=await workerMessage(waiting,{type:'AADX_UPDATE_INFO'});
  if(info?.version!==version||info?.cacheReady!==true)throw issue('site','Aguarde a atualização do site e tente novamente.');
  return waiting;
}
const MARKUP=`<div class="update-backdrop" data-update-backdrop></div><section class="update-dialog" role="dialog" aria-modal="true" aria-labelledby="aadx-update-title" tabindex="-1"><header><div><span class="update-eyebrow">SEMPRE À MÃO</span><h2 id="aadx-update-title">Atualizações</h2></div><button type="button" class="update-close" data-update-close aria-label="Fechar atualizações">×</button></header><dl class="update-versions"><div><dt>Versão instalada</dt><dd data-update-installed>—</dd></div><div hidden data-update-available-row><dt>Versão disponível</dt><dd data-update-available>—</dd></div></dl><p class="update-status" role="status" aria-live="polite" aria-atomic="true" data-update-status>Consulte a versão oficial quando quiser.</p><p class="update-note" data-update-note></p><div class="update-actions"><button class="button primary" type="button" data-update-check>Buscar atualização</button><button class="button primary" type="button" data-update-apply hidden>Aplicar atualização</button><a class="button secondary" data-update-download hidden target="_blank" rel="noopener noreferrer">Baixar Hospedeiro</a><a class="button quiet" data-update-release hidden target="_blank" rel="noopener noreferrer">Ver publicação</a></div></section>`;
export class UpdatePanel {
  constructor({installedVersion,mode='pwa',active=()=>false,onOpen=()=>{},onClose=()=>{},fetchImpl=globalThis.fetch,serviceWorker=globalThis.navigator?.serviceWorker,document=globalThis.document,window=globalThis.window}={}){
    Object.assign(this,{installedVersion,mode,active,onOpen,onClose,fetchImpl,serviceWorker,document,window});this.opened=false;this.busy=false;this.prepared=null;this.release=null;
    this.root=document.createElement('div');this.root.className='update-layer';this.root.hidden=true;this.root.innerHTML=MARKUP;document.body.append(this.root);
    this.find=name=>this.root.querySelector('[data-update-'+name+']');
    Object.assign(this.root.style,{position:'fixed',inset:'0',zIndex:'1000'});
    Object.assign(this.find('backdrop').style,{position:'absolute',inset:'0',background:'#031a13bb',backdropFilter:'blur(8px)'});
    Object.assign(this.root.querySelector('.update-dialog').style,{position:'absolute',left:'50%',top:'50%',transform:'translate(-50%,-50%)',width:'min(460px,calc(100% - 28px))',maxHeight:'calc(100dvh - 28px)',overflow:'auto',padding:'24px',borderRadius:'22px',background:'#f8fffb',color:'#133d2b',boxShadow:'0 25px 100px #0005'});
    this.find('check').addEventListener('click',()=>this.check());this.find('apply').addEventListener('click',()=>this.apply());
    this.find('close').addEventListener('click',()=>this.close());this.find('backdrop').addEventListener('click',()=>this.close());
    this.root.addEventListener('pointerdown',event=>event.stopPropagation());
    this.root.addEventListener('click',event=>event.stopPropagation());
    window.addEventListener('keydown',event=>{
      if(!this.opened)return;
      if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();this.close();return;}
      if(event.key==='Tab'){
        const list=[...this.root.querySelectorAll('button,a[href]')].filter(item=>!item.hidden&&!item.disabled),first=list[0],last=list.at(-1);
        if(event.shiftKey&&document.activeElement===first){last?.focus();event.preventDefault();}
        else if(!event.shiftKey&&document.activeElement===last){first?.focus();event.preventDefault();}
      }
    },true);
    window.addEventListener('popstate',()=>{if(this.opened)this.close({back:false});});
    serviceWorker?.addEventListener?.('message',event=>{
      if(event.data?.type==='AADX_QUERY_SESSIONS')event.ports?.[0]?.postMessage({busy:Boolean(this.active())});
    });
    this.bindOpeners();
  }
  installed(){const value=typeof this.installedVersion==='function'?this.installedVersion():this.installedVersion;return parseVersion(value)?value:null;}
  bindOpeners(){
    const document=this.document;let button=document.getElementById('check-updates');
    if(!button){button=document.createElement('button');button.id='check-updates';button.type='button';button.className='button quiet update-shortcut';button.textContent='Buscar atualização';document.querySelector('.app-header')?.append(button);}
    button?.addEventListener('click',()=>this.open(button));
    if(this.mode==='pwa'){
      const stage=document.getElementById('screen-stage');if(stage){
        const control=document.createElement('button');control.type='button';control.className='update-session-toggle';control.setAttribute('aria-label','Buscar atualização');control.title='Buscar atualização';
        control.innerHTML='<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3A7 7 0 0 0 17.9 17"/></svg><span>Atualizar</span>';
        Object.assign(control.style,{position:'absolute',right:'12px',top:'74px',zIndex:'9',width:'52px',height:'52px',pointerEvents:'auto'});
        for(const name of ['pointerdown','pointermove','pointerup','contextmenu'])control.addEventListener(name,event=>{event.stopPropagation();if(name==='contextmenu')event.preventDefault();});
        control.addEventListener('click',event=>{event.stopPropagation();this.open(control);});stage.append(control);
      }
    }
  }
  open(opener){
    this.returnFocus=opener||this.document.activeElement;this.onOpen();this.opened=true;this.root.hidden=false;this.find('installed').textContent=this.installed()||'Não foi possível identificar';
    this.find('note').textContent=this.mode==='bridge'?'O instalador do Hospedeiro atualiza também o Conector e o aplicativo local para iPhone. Encerre os acessos antes de instalar.':'A consulta é manual. Seus acessos continuam abertos; aplicar exige encerrá-los em todas as abas.';
    if(!this.historyOpen){this.window.history?.pushState?.({aadxUpdate:true},'');this.historyOpen=true;}
    this.find('close').focus();
  }
  close({back=true}={}){
    if(!this.opened)return;this.opened=false;this.root.hidden=true;this.onClose();this.returnFocus?.focus?.({preventScroll:true});
    if(this.historyOpen){this.historyOpen=false;if(back)this.window.history?.back?.();}
  }
  status(text,state='idle'){this.find('status').textContent=text;this.find('status').dataset.state=state;}
  async check(){
    if(this.busy)return;this.busy=true;this.prepared=null;this.release=null;this.find('check').disabled=true;this.find('apply').hidden=true;this.find('download').hidden=true;this.find('release').hidden=true;this.find('available-row').hidden=true;this.root.querySelector('.update-dialog').setAttribute('aria-busy','true');this.status('Consultando a publicação oficial no GitHub…','loading');
    try{
      const release=await checkRelease({installedVersion:this.installed(),fetchImpl:this.fetchImpl});this.release=release;
      this.find('installed').textContent=this.installed()||'Não foi possível identificar';this.find('available').textContent=release.version;this.find('available-row').hidden=false;
      this.find('release').href=release.releaseUrl;this.find('release').hidden=false;
      if(release.newer===false){this.status('Nenhuma nova versão disponível. Você está usando a versão '+this.installed()+'.','current');return;}
      if(this.mode==='bridge'||!this.serviceWorker||this.window.location.protocol!=='https:'||['localhost','127.0.0.1','::1','[::1]'].includes(this.window.location.hostname)){
        this.find('download').href=release.hostUrl;this.find('download').hidden=false;
        this.status((release.newer===null?'Versão oficial '+release.version+' disponível.':'Versão '+release.version+' disponível.')+' Atualize o Hospedeiro no Windows para atualizar também o Conector e o aplicativo local. Encerre os acessos antes de instalar.','available');return;
      }
      this.status('Versão '+release.version+' disponível. Verificando a atualização do site…','loading');
      if(await deployedVersion({fetchImpl:this.fetchImpl,base:this.window.location.href})!==release.version)throw issue('site','A versão '+release.version+' já foi publicada no GitHub. Aguarde a atualização do site e tente novamente.');
      let registration=await this.serviceWorker.getRegistration('./');if(!registration)registration=await this.serviceWorker.register('./sw.js',{scope:'./',updateViaCache:'none'});
      this.prepared=await prepareWorker(registration,release.version);this.find('apply').hidden=false;
      this.status(this.active()?'Atualização preparada. Encerre todas as conexões antes de aplicar.':'Atualização preparada. Aplicar solicitará sua confirmação para recarregar.','ready');
    }catch(error){this.status(error.message||'Não foi possível consultar. Tente novamente.',error.code||'error');}
    finally{this.busy=false;this.find('check').disabled=false;this.find('check').textContent='Tentar novamente';this.root.querySelector('.update-dialog').setAttribute('aria-busy','false');}
  }
  async apply(){
    if(this.busy||!this.prepared||!this.release)return;
    if(this.active()){this.status('Encerre todas as conexões antes de aplicar a atualização. Nenhum acesso foi encerrado.','blocked');return;}
    if(!this.window.confirm('Aplicar a versão '+this.release.version+'?\n\nO aplicativo será recarregado. Confirme apenas depois de encerrar todos os acessos em todas as abas.'))return;
    if(this.active()){this.status('Uma conexão está aberta. Encerre todos os acessos antes de aplicar.','blocked');return;}
    this.busy=true;this.find('apply').disabled=true;
    try{
      if(await deployedVersion({fetchImpl:this.fetchImpl,base:this.window.location.href})!==this.release.version)throw issue('site','Aguarde a atualização do site e tente novamente.');
      const accepted=await workerMessage(this.prepared,{type:'AADX_APPLY_UPDATE',version:this.release.version,userConfirmed:true},{timeout:6500});
      if(accepted?.accepted!==true)throw issue('blocked',accepted?.message||'Feche outras abas do AliançaDX e tente novamente.');
      this.status('Atualização autorizada. Aguardando o aplicativo ficar pronto para recarregar…','loading');
      await new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>finish(issue('worker','A atualização foi preparada, mas o navegador ainda não a ativou. Tente novamente.')),10000);
        const changed=()=>finish(),finish=error=>{clearTimeout(timer);this.serviceWorker.removeEventListener('controllerchange',changed);error?reject(error):resolve();};
        this.serviceWorker.addEventListener('controllerchange',changed);
        if(this.prepared.state==='activated')finish();
      });
      if(this.active())throw issue('blocked','Uma conexão foi aberta. Encerre os acessos e aplique novamente para recarregar.');
      // Only this explicitly confirmed action can reload. No global controllerchange reload.
      this.window.location.reload();
    }catch(error){this.status(error.message||'Não foi possível aplicar. Tente novamente.',error.code||'error');}
    finally{this.busy=false;this.find('apply').disabled=false;}
  }
}
