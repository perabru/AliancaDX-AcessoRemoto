/** Local labels only. A saved computer is not a reusable WebRTC invitation. */
export const FAVORITES_KEY='aliancadx-pwa-favorites';
export const FAVORITES_VERSION=1;
export const FAVORITES_LIMIT=20;
const MAX_STORAGE=32*1024;
const sensitive=/(?:AADXi1\.|MDA[67][.-]|(?:^|[\r\n])v=0(?:[\r\n]|$)|(?:password|senha|sdp|nonce|credential|invite|token)["']?\s*[:=])/i;
const safeId=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,80}$/.test(value)&&!sensitive.test(value);
export function favoriteName(value,limit=80){
  if(typeof value!=='string'||sensitive.test(value))return '';
  const text=value.replace(/[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]/g,'').trim();
  return sensitive.test(text)?'':text.slice(0,limit);
}
function cleanRecords(input){
  if(!Array.isArray(input))return [];
  const result=[],ids=new Set(),names=new Set();
  for(const item of input.slice(0,100)){
    if(!item||typeof item!=='object'||Array.isArray(item))continue;
    const name=favoriteName(item.name);
    if(!safeId(item.id)||!name||ids.has(item.id)||names.has(name))continue;
    const lastUsed=Number.isSafeInteger(item.lastUsed)&&item.lastUsed>=0?item.lastUsed:0;
    result.push({id:item.id,name,lastUsed});ids.add(item.id);names.add(name);
  }
  return result.sort((a,b)=>b.lastUsed-a.lastUsed).slice(0,FAVORITES_LIMIT);
}

export class FavoriteStore {
  constructor({storage=()=>globalThis.localStorage,now=()=>Date.now(),makeId=()=>globalThis.crypto.randomUUID()}={}){
    this.now=now;this.makeId=makeId;this.rows=[];this.sequence=0;this.deviceName='';this.persistent=true;this.needsReset=false;this.message='';this.storage=null;
    try{this.storage=typeof storage==='function'?storage():storage;if(!this.storage)throw new Error('unavailable');}
    catch{this.unavailable();return;}
    this.load();
  }
  get favorites(){return this.rows.map(item=>({...item}));}
  unavailable(){this.persistent=false;this.message='O navegador não permitiu salvar os favoritos. Eles ficam disponíveis somente enquanto esta página estiver aberta.';}
  unreadable(){this.persistent=false;this.needsReset=true;this.message='Os favoritos salvos não puderam ser lidos e foram preservados. A lista atual é temporária. Use Recriar favoritos para substituí-los pela lista mostrada.';}
  load(){
    let raw;
    try{raw=this.storage.getItem(FAVORITES_KEY);}catch{this.unavailable();return;}
    if(raw==null)return;
    let value;
    try{if(typeof raw!=='string'||raw.length>MAX_STORAGE)throw new Error('size');value=JSON.parse(raw);if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('shape');}
    catch{this.unreadable();return;}
    if(value.version!==FAVORITES_VERSION&&value.version!==0){
      this.persistent=false;this.message='Os favoritos foram salvos por outra versão. Atualize o aplicativo; esta página usará uma lista temporária.';return;
    }
    const records=value.version===0?value.computers:value.favorites;
    if(!Array.isArray(records)){this.unreadable();return;}
    this.rows=cleanRecords(records);
    this.deviceName=favoriteName(value.deviceName,60);
    // Rewrite an allowlisted schema so unknown/private legacy fields never survive.
    this.persist();
  }
  persist(){
    if(!this.persistent)return false;
    const value={version:FAVORITES_VERSION,favorites:this.rows.map(({id,name,lastUsed})=>({id,name,lastUsed})),deviceName:this.deviceName};
    try{this.storage.setItem(FAVORITES_KEY,JSON.stringify(value));return true;}catch{this.unavailable();return false;}
  }
  reset(){
    if(!this.needsReset)return false;
    this.persistent=true;this.message='';
    const saved=this.persist();this.needsReset=!saved;return saved;
  }
  save(name){
    name=favoriteName(name);if(!name)return null;
    let row=this.rows.find(item=>item.name===name);
    if(!row){
      let id;try{id=this.makeId();}catch{}
      if(!safeId(id)||this.rows.some(item=>item.id===id)){
        do{id='pc-'+this.timestamp().toString(36)+'-'+(++this.sequence).toString(36);}while(this.rows.some(item=>item.id===id));
      }
      row={id,name,lastUsed:0};
    }
    row.lastUsed=this.timestamp();
    this.rows=[row,...this.rows.filter(item=>item.id!==row.id)].sort((a,b)=>b.lastUsed-a.lastUsed).slice(0,FAVORITES_LIMIT);this.persist();return {...row};
  }
  timestamp(){const value=Number(this.now());return Number.isFinite(value)?Math.max(0,Math.min(Number.MAX_SAFE_INTEGER,Math.trunc(value))):0;}
  remove(id){
    const before=this.rows.length;this.rows=this.rows.filter(row=>row.id!==id);
    if(this.rows.length!==before)this.persist();
    return this.rows.length!==before;
  }
  setDeviceName(value){this.deviceName=favoriteName(value,60);this.persist();return this.deviceName;}
}
