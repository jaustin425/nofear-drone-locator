"""Own temporary synthetic server: stream controls, audible alert calls and failure UI."""
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlparse
import httpx
from playwright.sync_api import sync_playwright

URL='http://127.0.0.2:8768'


def server():
    process=subprocess.Popen([sys.executable,'-m','aetherlink_scout','--simulate-demo','--simulation-speed','10','--port','8768'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    for _ in range(80):
        if process.poll() is not None:
            raise AssertionError('Synthetic server could not start')
        try:
            with httpx.Client(trust_env=False) as client:
                if client.get(URL+'/api/health').status_code==200:
                    return process
        except httpx.HTTPError:
            pass
        time.sleep(.05)
    process.terminate()
    process.wait(timeout=10)
    raise AssertionError('Synthetic server startup timed out')


def main():
    process=server()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch()
            page=browser.new_page(viewport={'width':1440,'height':960})
            page.route('**/static/local-location.json',lambda r:r.fulfill(json={'lat':35.007,'lon':-115.011}))
            page.clock.install()
            page.add_init_script("""window.toneCount=0; window.toneCancellations=0; const original=AudioContext.prototype.createOscillator;
              AudioContext.prototype.createOscillator=function(...args){
                window.toneCount++; const oscillator=original.apply(this,args), stop=oscillator.stop;
                oscillator.stop=function(...times){if(!times.length) window.toneCancellations++; return stop.apply(this,times);};
                return oscillator;};
              window.openedDirections=[]; window.open=(url)=>{window.openedDirections.push(url);return null;};""")
            errors=[]; external=[]
            page.on('pageerror',lambda e:errors.append(type(e).__name__))
            page.on('request',lambda r:external.append(urlparse(r.url).hostname) if urlparse(r.url).hostname!='127.0.0.2' else None)
            page.goto(URL,wait_until='domcontentloaded')
            page.locator('#receiver-panel').wait_for(state='visible')
            assert page.locator('#mode-badge').inner_text()=='STREAM SIMULATION'
            assert 'Testing preset' in page.locator('#my-location-status').inner_text()
            assert page.locator('.marker.me').count()==1
            assert page.locator('#play').is_hidden()
            assert page.get_by_role('button',name='START',exact=True).is_enabled()
            assert page.get_by_role('button',name='STOP',exact=True).is_disabled()
            assert page.locator('.track-card').count()==0
            page.get_by_role('button',name='START',exact=True).click()
            page.wait_for_function("() => document.querySelectorAll('.track-card').length===2")
            assert page.locator('.marker.aircraft').count()==2
            assert page.locator('.marker.operator').count()==2
            assert page.locator('#details .detail-section').first.inner_text()=='REPORTED OPERATOR'
            assert page.locator('#alerts .log-item').count()==2
            assert page.evaluate('window.toneCount')==2
            assert 'armed' in page.locator('#audio-status').inner_text()
            cancellations=page.evaluate('window.toneCancellations')
            page.get_by_role('button',name='Sound on',exact=True).click()
            assert page.evaluate('window.toneCancellations')>cancellations
            assert page.get_by_role('button',name='Enable sound',exact=True).is_visible()
            page.get_by_role('button',name='STOP',exact=True).click()
            page.get_by_role('button',name='START',exact=True).wait_for(state='visible')
            page.wait_for_function("() => !document.querySelector('#start-detection').disabled")
            with httpx.Client(trust_env=False) as client:
                before=client.get(URL+'/api/receiver').json()
                assert before['status']['state']=='stopped'
                time.sleep(.3)
                after=client.get(URL+'/api/receiver').json()
            assert before['status']['counts']==after['status']['counts']
            assert len(after['tracks'])==2
            page.on('dialog',lambda dialog:dialog.accept())
            for kind,index in [('aircraft',0),('operator',1),('aircraft',1),('operator',0)]:
                # Dispatch on the marker itself so popup overlap cannot redirect
                # this synthetic click to a different marker.
                page.locator(f'.marker.{kind}').nth(index).dispatch_event('click')
                assert page.locator('#details .detail-name').inner_text()==f'SYNTHETIC-DEMO-{index+1}'
                coordinates=['35.0080000, -115.0120000','35.0100000, -115.0090000'][index]
                assert coordinates in page.locator('#details').inner_text()
                assert page.locator('.track-card.selected .card-title').inner_text()==f'SYNTHETIC-DEMO-{index+1}'
                page.get_by_role('button',name='Driving directions ↗',exact=True).click()
                destination=['35.008,-115.012','35.010000000000005,-115.009'][index]
                # Compare numerically to allow normal floating-point arithmetic
                # in the synthetic coordinates.
                actual=page.evaluate("new URL(window.openedDirections.at(-1)).searchParams.get('destination')")
                assert all(abs(float(a)-float(b))<1e-10 for a,b in zip(actual.split(','),destination.split(',')))
            page.reload(wait_until='domcontentloaded')
            page.locator('.track-card').nth(1).wait_for()
            assert page.locator('#alerts .log-item').count()==0
            assert page.evaluate('window.toneCount')==0
            page.get_by_role('button',name='Refresh',exact=True).click()
            page.wait_for_function("() => document.querySelectorAll('.history-card').length===2")
            page.get_by_role('button',name='Clear old tracks',exact=True).click()
            page.wait_for_function("() => document.querySelector('#clear-map-status').textContent.startsWith('0 tracks')")
            assert page.locator('.track-card').count()==2
            # Advance the actual server freshness threshold, not merely the
            # browser clock, before clearing stale reports.
            page.wait_for_timeout(31000)
            page.get_by_role('button',name='Clear old tracks',exact=True).click()
            page.wait_for_function("() => document.querySelectorAll('.track-card').length===0")
            assert page.locator('.marker.aircraft').count()==0
            assert page.locator('.marker.operator').count()==0
            assert page.locator('.history-card').count()==2
            page.locator('.history-card').filter(has_text='SYNTHETIC-DEMO-1').click()
            page.wait_for_function("() => document.querySelector('.right-panel .badge').textContent==='SAVED'")
            assert 'Saved historical observation' in page.locator('#details').inner_text()
            assert page.locator('.marker.history').count()==2
            assert page.locator('#details .detail-name').inner_text()=='SYNTHETIC-DEMO-1'
            page.get_by_role('button',name='Return to live view',exact=True).click()
            assert page.locator('.marker.history').count()==0
            assert page.locator('.track-card').count()==0
            page.get_by_role('button',name='START',exact=True).click()
            page.wait_for_function("() => document.querySelectorAll('.track-card').length===0")
            page.locator('.track-card').first.wait_for()
            cancellations=page.evaluate('window.toneCancellations')
            page.get_by_role('button',name='STOP',exact=True).click()
            assert page.evaluate('window.toneCancellations')>cancellations
            page.wait_for_function("() => !document.querySelector('#start-detection').disabled")
            assert page.locator('#alerts .log-item').count()>=1
            out=Path('local-data/dashboard-verification')
            out.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(out/'synthetic-stream-desktop.png'))
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path=str(out/'synthetic-stream-mobile.png'),full_page=True)
            process.terminate();process.wait(timeout=10)
            page.locator('#receiver-status').get_by_text(re.compile('Dashboard connection lost')).wait_for(timeout=10000)
            page.clock.fast_forward(31000)
            page.wait_for_function("() => document.querySelectorAll('.marker.is-stale').length>=2")
            assert page.get_by_role('button',name='START',exact=True).is_disabled()
            assert page.get_by_role('button',name='STOP',exact=True).is_disabled()
            assert not external
            assert not errors
            browser.close()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
    print('PASS: paired map selection/directions, START/STOP, sound, refresh/restart, clear stale while keeping active, retained history and saved map review, stale aging, mobile layout, no external requests')


if __name__=='__main__':
    main()
