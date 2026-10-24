/** Application bytes crossing this session's RTC channel, including framing.
 * This is measured session traffic, not a speed test or Internet capacity.
 */
export const TRAFFIC_WINDOW_MS = 2500;
const BUCKET_MS = 100;

export class SessionTraffic {
  constructor(now=()=>performance.now()) { this.now=now;this.active=false;this.buckets=new Map(); }
  start() { this.buckets.clear();this.active=true; }
  stop() { this.active=false;this.buckets.clear(); }
  prune(now) {
    for(const [at] of this.buckets)if(at+BUCKET_MS<=now-TRAFFIC_WINDOW_MS || at>now)this.buckets.delete(at);
  }
  add(direction,bytes) {
    if(!this.active || !['rx','tx'].includes(direction) || !Number.isSafeInteger(bytes) || bytes<=0)return;
    const now=this.now();if(!Number.isFinite(now))return;
    this.prune(now);
    const at=Math.floor(now/BUCKET_MS)*BUCKET_MS;
    const bucket=this.buckets.get(at)||{rx:0,tx:0};bucket[direction]+=bytes;this.buckets.set(at,bucket);
  }
  snapshot() {
    const now=this.now();let rxBytes=0,txBytes=0;
    if(this.active && Number.isFinite(now)){
      this.prune(now);
      for(const bucket of this.buckets.values()){rxBytes+=bucket.rx;txBytes+=bucket.tx;}
    }
    const factor=8/(TRAFFIC_WINDOW_MS*1000);
    return {rxBytes,txBytes,rxMbps:rxBytes*factor,txMbps:txBytes*factor};
  }
}

export function trafficText(value) {
  const rate=number=>Math.max(0,Number.isFinite(number)?number:0).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});
  return '↓ '+rate(value?.rxMbps)+' Mb/s · ↑ '+rate(value?.txMbps)+' Mb/s';
}
