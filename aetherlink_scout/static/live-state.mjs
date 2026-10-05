// Arrival time drives freshness. Historical source timestamps never become live fixes.
import {ReplayState} from './state.mjs';

export class LiveState extends ReplayState {
  constructor(data, clock = () => performance.now()/1000) {
    super(data); this.clock=clock; this.session=null; this.sequence=0;
    this.synchronizedAt=clock(); this.serverTime=0; this.connected=false; this.status=null;
    this.mapRevision=0; this.clearedKeys=new Set();
  }
  tick() { this.time=this.serverTime+Math.max(0,this.clock()-this.synchronizedAt); }
  apply(frame) {
    const hadSession=this.session!==null;
    const newSession=this.session!==frame.status.session_id;
    const mapCleared=!newSession && this.mapRevision!==(frame.status.map_revision || 0);
    if (newSession) {
      this.reset(); this.sequence=0; this.session=frame.status.session_id;
      this.clearedKeys.clear();
    }
    if (mapCleared) {
      for (const key of this.tracks.keys()) this.clearedKeys.add(key);
      while (this.clearedKeys.size>512) this.clearedKeys.delete(this.clearedKeys.values().next().value);
    }
    this.mapRevision=frame.status.map_revision || 0;
    this.status=frame.status; this.connected=true;
    this.serverTime=frame.status.elapsed; this.synchronizedAt=this.clock(); this.tick();
    let count=0;
    if (frame.kind==='snapshot') {
      this.data=frame.metadata;
      this.tracks=new Map(frame.tracks.map(t=>[t.key,t]));
      // Refresh/reconnect restores retained tracks silently, with no alert burst.
      this.alerted=new Set([...this.clearedKeys,...this.tracks.keys()]);
      if (newSession && hadSession) {
        for (const track of this.tracks.values()) {
          this.alerts.unshift({key:track.key,t:track.firstSeen,label:track.fields.serial || 'Unidentified transmitter',acknowledged:false});
          count++;
        }
        this.alerts=this.alerts.slice(0,50);
      }
      this.sequence=frame.sequence;
    } else {
      for (const r of frame.records) {
        if (r.sequence<=this.sequence) continue;
        const unseen=!this.alerted.has(r.key);
        this.ingest(r,true);
        if (unseen) count++;
        this.sequence=r.sequence;
      }
      for (const key of this.alerted) if (!this.tracks.has(key) && !this.clearedKeys.has(key)) this.alerted.delete(key);
      this.sequence=Math.max(this.sequence,frame.sequence);
    }
    return count;
  }
  age(track, field=null) { return Math.max(0,super.age(track,field)); }
}
