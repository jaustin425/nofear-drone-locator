"""Render the actual alert waveform offline; no hardware capture or speaker output."""
from playwright.sync_api import sync_playwright
from browser_live import server, URL


def main():
    process=server()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch()
            page=browser.new_page()
            page.route('**/static/local-location.json',lambda route:route.fulfill(json={}))
            page.goto(URL,wait_until='domcontentloaded')
            result=page.evaluate("""async () => {
              const {DetectionTone}=await import('/static/detection-tone.mjs');
              const rate=48000;
              const rms=(data,start,end)=>{
                const samples=data.slice(Math.round(start*rate),Math.round(end*rate));
                return Math.sqrt(samples.reduce((sum,value)=>sum+value*value,0)/samples.length);
              };
              const context=new OfflineAudioContext(1,rate*5,rate);
              const tone=new DetectionTone();
              tone.play(context);
              const rendered=await context.startRendering();
              const data=rendered.getChannelData(0);
              const bursts=[], pitches=[];
              let previous=false;
              for(let at=0;at<5;at+=.05){
                const sounding=rms(data,at,at+.05)>.01;
                if(sounding && !previous) bursts.push(at);
                previous=sounding;
              }
              for(const start of bursts){
                const from=Math.round((start+.06)*rate), end=from+Math.round(.12*rate);
                let crossings=0;
                for(let i=from+1;i<end;i++) if(data[i-1]<=0 && data[i]>0) crossings++;
                pitches.push(crossings/.12);
              }
              const cancelled=new OfflineAudioContext(1,rate*5,rate);
              const mutedTone=new DetectionTone();
              mutedTone.play(cancelled);
              const suspended=cancelled.suspend(.75);
              const rendering=cancelled.startRendering();
              await suspended; mutedTone.stop(); await cancelled.resume();
              const silence=(await rendering).getChannelData(0);
              const replacement=new OfflineAudioContext(1,rate*5,rate);
              const replacingTone=new DetectionTone();
              replacingTone.play(replacement); replacingTone.play(replacement);
              const replaced=(await replacement.startRendering()).getChannelData(0);
              return {bursts,pitches,endSilence:rms(data,4,5),released:tone.active===null,
                muted:rms(silence,.8,5),cancelledReleased:mutedTone.active===null,
                replacementPeak:replaced.reduce((peak,value)=>Math.max(peak,Math.abs(value)),0)};
            }""")
            assert len(result['bursts'])==8, result
            assert 3 <= result['bursts'][-1] <= 5, result
            assert min(result['pitches'])>500 and max(result['pitches'])<1000, result
            assert max(result['pitches'])-min(result['pitches'])>150, result
            assert result['endSilence']<1e-6 and result['released'], result
            assert result['muted']<1e-6 and result['cancelledReleased'], result
            assert .09<result['replacementPeak']<.101, result
            browser.close()
    finally:
        if process.poll() is None:
            process.terminate(); process.wait(timeout=10)
    print('PASS: audible waveform has eight alternating tones over four seconds, ends cleanly, cancels immediately, and replacements do not stack')


if __name__=='__main__':
    main()
