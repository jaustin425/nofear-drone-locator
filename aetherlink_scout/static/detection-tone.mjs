// One bounded alert sequence; new detections replace it rather than stacking.
export class DetectionTone {
  constructor() { this.active=null; }

  play(context) {
    this.stop();
    const oscillator=context.createOscillator(), gain=context.createGain();
    const active={oscillator,gain};
    this.active=active;
    oscillator.connect(gain); gain.connect(context.destination);
    const start=context.currentTime;
    gain.gain.setValueAtTime(0,start);
    for (let pulse=0;pulse<8;pulse++) {
      const at=start+pulse*.5;
      oscillator.frequency.setValueAtTime(pulse%2?880:650,at);
      gain.gain.setValueAtTime(0,at);
      gain.gain.linearRampToValueAtTime(.1,at+.02);
      gain.gain.setValueAtTime(.1,at+.2);
      gain.gain.linearRampToValueAtTime(0,at+.35);
    }
    oscillator.onended=()=>{
      oscillator.disconnect(); gain.disconnect();
      if (this.active===active) this.active=null;
    };
    oscillator.start(start);
    oscillator.stop(start+4);
  }

  stop() {
    if (!this.active) return;
    const {oscillator,gain}=this.active;
    this.active=null;
    try { oscillator.stop(); } catch { /* Already ended. */ }
    oscillator.disconnect(); gain.disconnect();
  }
}
