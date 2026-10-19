"""Three real synthetic subprocess inputs, one combined dashboard, no radio access."""
import subprocess
import sys
import time
from urllib.parse import urlparse
import httpx
from playwright.sync_api import sync_playwright

URL='http://127.0.0.2:8772'
SERVER=r'''
import sys,uvicorn
sys.path.insert(0,'tests')
from test_combined import synthetic_sources
from aetherlink_scout.combined import CombinedReceiver
from aetherlink_scout.app import create_app
sources,captures=synthetic_sources(renew=True)
receiver=CombinedReceiver(sources=sources)
uvicorn.run(create_app(receiver=receiver),host='127.0.0.2',port=8772,access_log=False)
'''


def main():
    process=subprocess.Popen([sys.executable,'-c',SERVER],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if process.poll() is not None:raise AssertionError('Synthetic combined server failed')
            try:
                with httpx.Client(trust_env=False) as c:
                    if c.get(URL+'/api/health').status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.05)
        else:raise AssertionError('Synthetic server startup timed out')
        with sync_playwright() as p:
            browser=p.chromium.launch()
            page=browser.new_page(viewport={'width':1440,'height':960})
            page.route('**/static/local-location.json',lambda r:r.fulfill(json={'lat':35.007,'lon':-115.011}))
            page.add_init_script('window.toneCount=0;const old=AudioContext.prototype.createOscillator;AudioContext.prototype.createOscillator=function(...args){window.toneCount++;return old.apply(this,args);};')
            errors=[];external=[]
            page.on('pageerror',lambda e:errors.append(type(e).__name__))
            page.on('request',lambda r:external.append(urlparse(r.url).hostname) if urlparse(r.url).hostname!='127.0.0.2' else None)
            page.goto(URL,wait_until='domcontentloaded')
            page.wait_for_function("() => document.querySelectorAll('#source-status p').length===3")
            assert page.locator('.track-card').count()==0
            page.get_by_role('button',name='START',exact=True).click()
            page.wait_for_function("() => document.querySelector('#receiver-counts').textContent.includes('18 decoded')")
            assert page.locator('.track-card').count()==1
            assert page.locator('.marker.aircraft').count()==1
            assert page.locator('.marker.operator').count()==1
            assert '3 capture renewals' in page.locator('#receiver-counts').inner_text()
            # One oscillator schedules all eight pulses of one alert sequence.
            assert page.evaluate('window.toneCount')==1, page.evaluate("({tones:window.toneCount,audio:document.querySelector('#audio-status').textContent,sound:document.querySelector('#sound').textContent,alerts:document.querySelector('#alerts').textContent,state:document.querySelector('#receiver-status').textContent})")
            assert '10 BLE service updates' not in page.locator('#receiver-counts').inner_text() # per-source is separate
            assert '20 BLE service updates' in page.locator('#source-status').inner_text()
            page.locator('.track-card').click()
            assert 'Observed by:' in page.locator('#details').inner_text()
            for text in ('Panda','Techkey','Legacy Bluetooth'):assert text in page.locator('#details').inner_text()
            page.get_by_role('button',name='STOP',exact=True).click()
            page.wait_for_function("() => !document.querySelector('#start-detection').disabled")
            assert all('stopped' in row.inner_text() for row in page.locator('#source-status p').all())
            before=page.locator('#receiver-counts').inner_text();page.wait_for_timeout(400)
            assert page.locator('#receiver-counts').inner_text()==before
            page.reload(wait_until='domcontentloaded')
            page.wait_for_function("() => document.querySelectorAll('.track-card').length===1")
            assert page.evaluate('window.toneCount')==0
            assert page.get_by_role('button',name='START',exact=True).is_enabled()
            assert not errors,errors
            assert not external,external
            browser.close()
    finally:
        process.terminate();process.wait(timeout=20)
    print('PASS: three inputs, per-source status, one track/marker pair/history model, one alert across renewals, STOP and silent refresh; synthetic only')


if __name__=='__main__':main()
