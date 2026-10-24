/** A single production policy for clean fullscreen and safe recovery. */
export class SessionView {
  constructor(onChange=()=>{}) { this.fullscreen=false;this.hidden=false;this.onChange=onChange;this.session=null; }
  emit() { this.onChange({fullscreen:this.fullscreen,hidden:this.hidden}); }
  fullscreenTo(enabled) {
    this.fullscreen=Boolean(enabled);this.hidden=false;this.emit();
  }
  hide({session,ready,promptActive=false,error=false,offline=false}={}) {
    if(!this.fullscreen || !ready || promptActive || error || offline)return false;
    this.session=session;this.hidden=true;this.emit();return true;
  }
  restore() { if(this.hidden){this.hidden=false;this.emit();} }
  reconcile({session,ready,promptActive=false,error=false,offline=false}={}) {
    if(this.hidden && (session!==this.session || !ready || promptActive || error || offline))this.restore();
  }
}

/** Keep reveal/hide pointers on their buttons until the complete gesture ends.
 * Preventing their pointer default also prevents focus/canvas click-through.
 */
export function isolateControl(button,release) {
  for(const name of ['pointerdown','pointermove','pointerup','pointercancel','lostpointercapture','contextmenu'])button.addEventListener(name,event=>{
    event.stopPropagation();event.preventDefault();
    if(name==='pointerdown'){
      release();
      try{button.setPointerCapture(event.pointerId);}catch{}
    }
  });
}
