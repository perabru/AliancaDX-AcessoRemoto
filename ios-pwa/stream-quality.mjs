/** Source-pinned presentation and local-command scheduling. No protocol imports. */
const encoder=new TextEncoder();
export function fitBounds(width, height, sourceWidth, sourceHeight) {
  if (![width,height,sourceWidth,sourceHeight].every(value => Number.isFinite(value) && value > 0)) return null;
  const scale = Math.min(width / sourceWidth, height / sourceHeight);
  const w = sourceWidth * scale, h = sourceHeight * scale;
  return { left: (width-w)/2, top: (height-h)/2, width: w, height: h };
}
/** A small image is not made sharper by allocating a much larger canvas. */
export function canvasBacking(width,height,imageWidth=0,imageHeight=0,zoom=1,dpr=1) {
  if(!Number.isFinite(width)||!Number.isFinite(height)||width<=0||height<=0)return null;
  let ratio=Math.max(1,Math.min(2,Number.isFinite(dpr)?dpr:1));
  if(Number.isFinite(imageWidth)&&Number.isFinite(imageHeight)&&imageWidth>0&&imageHeight>0){
    const bounds=fitBounds(width,height,imageWidth,imageHeight);
    const sourceRatio=Math.min(imageWidth/bounds.width,imageHeight/bounds.height)/Math.max(1,zoom);
    ratio=Math.min(ratio,Math.max(1,sourceRatio));
  }
  ratio=Math.min(ratio,Math.sqrt(4*1024*1024/(width*height)));
  const w=Math.max(1,Math.round(width*ratio)),h=Math.max(1,Math.round(height*ratio));
  return {width:w,height:h,scaleX:w/width,scaleY:h/height};
}

/** Keep the JPEG raster intact while the local cursor moves above it. */
export class ScreenPresenter {
  constructor(canvas,cursor){
    if(!cursor){
      cursor=canvas.ownerDocument.createElement('canvas');cursor.id='cursor-canvas';
      canvas.parentElement.append(cursor);
    }
    cursor.setAttribute?.('aria-hidden','true');
    if(cursor.style)Object.assign(cursor.style,{position:'absolute',inset:'0',width:'100%',height:'100%',pointerEvents:'none'});
    this.canvas=canvas;this.cursor=cursor;this.context=canvas.getContext('2d');this.cursorContext=cursor.getContext('2d');this.previous=null;
  }
  paint(session,width,height,dpr=1){
    const image=session?.frame&&session.image;
    const backing=canvasBacking(width,height,image?.naturalWidth||image?.width,image?.naturalHeight||image?.height,session?.zoom,dpr);
    if(!backing)return false;
    let resized=false;
    for(const canvas of [this.canvas,this.cursor])if(canvas.width!==backing.width||canvas.height!==backing.height){canvas.width=backing.width;canvas.height=backing.height;resized=true;}
    const bounds=image?session.layout(width,height):null;
    const key={session,image,viewportWidth:width,viewportHeight:height,...bounds};
    const same=!resized&&this.previous&&Object.keys(key).every(name=>key[name]===this.previous[name]);
    const context=this.context;context.setTransform(backing.scaleX,0,0,backing.scaleY,0,0);
    if(!same){
      context.fillStyle='#061f18';context.fillRect(0,0,width,height);
      context.imageSmoothingEnabled=true;context.imageSmoothingQuality='high';
      if(image)context.drawImage(image,bounds.left,bounds.top,bounds.width,bounds.height);
      this.previous=key;
    }
    const cursor=this.cursorContext;cursor.setTransform(backing.scaleX,0,0,backing.scaleY,0,0);cursor.clearRect(0,0,width,height);
    if(image){
      const x=bounds.left+session.point.x*bounds.width,y=bounds.top+session.point.y*bounds.height;
      cursor.beginPath();cursor.arc(x,y,11,0,Math.PI*2);cursor.strokeStyle=session.dragHeld?'#93ffbf':'#02ef5faa';cursor.lineWidth=1.5;cursor.stroke();
      cursor.save();cursor.translate(x,y);cursor.beginPath();cursor.moveTo(0,0);cursor.lineTo(0,18);cursor.lineTo(5,13);cursor.lineTo(9,21);cursor.lineTo(13,19);cursor.lineTo(9,11);cursor.lineTo(17,10);cursor.closePath();cursor.fillStyle='#fff';cursor.strokeStyle='#073b2b';cursor.lineWidth=1.5;cursor.lineJoin='round';cursor.fill();cursor.stroke();cursor.restore();
    }
    return Boolean(image);
  }
}

/** One local HTTP command in flight. Replace only consecutive unsent moves. */
export class BridgeCommandQueue {
  constructor(send,onError,{limit=256,maxBytes=8*1024*1024}={}){this.send=send;this.onError=onError;this.limit=limit;this.maxBytes=maxBytes;this.queue=[];this.bytes=0;this.activeBytes=0;this.running=false;this.closed=false;this.feedbackLast=false;}
  enqueue(message){
    if(this.closed)return false;
    const size=encoder.encode(JSON.stringify(message)).byteLength;
    const last=this.queue.at(-1);
    const move=message.type==='input'&&message.action==='move';
    if(move&&last?.message.type==='input'&&last.message.action==='move'&&last.message.captureId===message.captureId){this.bytes-=last.size;this.queue.pop();}
    if(message.type==='stream_feedback'&&message.cumulative===true){
      const at=this.queue.findIndex(row=>row.message.type==='stream_feedback'&&row.message.cumulative===true);
      if(at>=0){
        if(this.queue[at].message.frameId>=message.frameId)return true;
        this.bytes-=this.queue[at].size;this.queue.splice(at,1);
      }
    }
    if(this.queue.length+(this.running?1:0)>=this.limit||this.bytes+size>this.maxBytes){this.fail(new Error('Há comandos demais aguardando a rede.'));return false;}
    this.queue.push({message,size});this.bytes+=size;this.drain();return true;
  }
  async drain(){
    if(this.running||this.closed)return;
    this.running=true;
    try{
      while(this.queue.length&&!this.closed){
        const feedback=this.queue.findIndex(row=>row.message.type==='stream_feedback');
        // Feedback cannot wait behind mouse traffic, and repeated feedback
        // cannot starve a release, key-up or an ordinary ordered command.
        const user=this.queue.findIndex(row=>row.message.type!=='stream_feedback');
        const at=feedback>=0&&(!this.feedbackLast||user<0)?feedback:user>=0?user:0;
        const row=this.queue.splice(at,1)[0];this.activeBytes=row.size;this.feedbackLast=row.message.type==='stream_feedback';
        try{await this.send(row.message);}finally{this.bytes=Math.max(0,this.bytes-row.size);this.activeBytes=0;}
      }
    }catch(error){this.fail(error);}finally{this.running=false;}
  }
  fail(error){if(this.closed)return;this.shutdown();this.onError?.(error);}
  shutdown(){this.closed=true;this.queue.length=0;this.bytes=this.activeBytes;}
}
