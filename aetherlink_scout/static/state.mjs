// Pure replay state. Capture time drives age; seeking never issues detections.
export class ReplayState {
  constructor(data) { this.data = data; this.reset(); }
  reset() { this.time = 0; this.index = 0; this.tracks = new Map(); this.alerted = new Set(); this.alerts = []; }
  ingest(report, notify) {
    let track = this.tracks.get(report.key);
    if (!track) {
      track = {key: report.key, firstSeen: report.t, lastSeen: report.t, trail: [], fields: {}, fieldTimes: {}};
      this.tracks.set(report.key, track);
      if (this.tracks.size > 512) {
        const oldest = [...this.tracks.values()].sort((a,b) => a.lastSeen - b.lastSeen)[0];
        this.tracks.delete(oldest.key);
      }
    }
    track.lastSeen = report.t;
    for (const [key, value] of Object.entries(report)) {
      if (['t', 'timestamp', 'key'].includes(key) || value === null) continue;
      if (key === 'aircraft' || key === 'operator') {
        const previous = track.fields[key] || {};
        const merged = {...previous, lat: value.lat, lon: value.lon};
        if (value.altitude !== null) {
          merged.altitude = value.altitude;
          track.fieldTimes[`${key}_altitude`] = report.t;
        }
        track.fields[key] = merged;
        track.fieldTimes[key] = report.t;
      } else { track.fields[key] = value; track.fieldTimes[key] = report.t; }
    }
    if (report.aircraft) {
      const p = report.aircraft;
      const last = track.trail.at(-1);
      if (!last || last[0] !== p.lat || last[1] !== p.lon) {
        track.trail.push([p.lat, p.lon]);
        if (track.trail.length > 500) track.trail.shift();
      }
    }
    if (!this.alerted.has(report.key)) {
      this.alerted.add(report.key);
      if (notify) {
        const alert = {key: report.key, t: report.t, label: report.serial || 'Unidentified transmitter', acknowledged: false};
        this.alerts.unshift(alert);
        if (this.alerts.length > 50) this.alerts.pop();
      }
    }
  }
  advance(time, notify = true) {
    const maximum = this.data.duration + this.data.tail_seconds;
    const target = Math.max(0, Math.min(time, maximum));
    if (target < this.time) return this.seek(target);
    let newAlerts = 0;
    while (this.index < this.data.records.length && this.data.records[this.index].t <= target) {
      const r = this.data.records[this.index++];
      const unseen = !this.alerted.has(r.key);
      this.ingest(r, notify);
      if (notify && unseen) newAlerts++;
    }
    this.time = target;
    return newAlerts;
  }
  seek(time) { this.reset(); return this.advance(time, false); }
  age(track, field = null) { return this.time - (field ? track.fieldTimes[field] ?? track.lastSeen : track.lastSeen); }
  stale(track, field = null) { return this.age(track, field) >= this.data.stale_seconds; }
}
