"""Synthetic USB replug, continued alerts and STOP; no radio or network-state changes."""
import subprocess
import sys
import time
from urllib.parse import urlparse
import httpx
from playwright.sync_api import sync_playwright

URL='http://127.0.0.2:8774'
SERVER='''
import sys,time,uvicorn
sys.path.insert(0,'tests')
from test_recovery import recovery_sources
from aetherlink_scout.combined import CombinedReceiver
from aetherlink_scout.app import create_app
sources,recovery,_=recovery_sources()
receiver=CombinedReceiver(sources=sources)
app=create_app(receiver=receiver)
@app.post('/test/unplug')
def unplug():recovery.present=False;return {}
@app.post('/test/return')
def reconnect():recovery.index+=1;recovery.present=True;return {}
@app.post('/test/new')
def report():
    fields=next(iter(receiver.tracks.values()))['fields']
    sources[1].receiver.publish({**fields,'key':'synthetic-second','serial':'AETHERLINK-SCOUT-SECOND','timestamp':time.time(),'aircraft':{'lat':35.02,'lon':-115.03,'altitude':None},'operator':None})
    return {}
uvicorn.run(app,host='127.0.0.2',port=8774,access_log=False)
'''


def main():
    process=subprocess.Popen([sys.executable,'-c',SERVER],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if process.poll() is not None:raise AssertionError('Synthetic recovery dashboard failed')
            try:
                with httpx.Client(trust_env=False) as client:
                    if client.get(URL+'/api/health').status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.05)
        else:raise AssertionError('Synthetic startup timed out')
        with sync_playwright() as p:
            browser=p.chromium.launch();page=browser.new_page()
            page.route('**/static/local-location.json',lambda r:r.fulfill(json={'lat':35.007,'lon':-115.011}))
            page.add_init_script('window.toneCount=0;const old=AudioContext.prototype.createOscillator;AudioContext.prototype.createOscillator=function(...args){window.toneCount++;return old.apply(this,args);};')
            errors=[];external=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:external.append(request.url) if urlparse(request.url).hostname!='127.0.0.2' else None)
            page.goto(URL,wait_until='domcontentloaded')
            page.get_by_role('button',name='START',exact=True).click()
            page.wait_for_function("() => document.querySelector('#receiver-counts').textContent.includes('9 decoded')")
            assert page.evaluate('window.toneCount')==1
            assert '3/3 inputs listening' in page.locator('#health-summary').inner_text()
            session=page.request.get(URL+'/api/health').json()['receiver']['session_id']
            page.request.post(URL+'/test/unplug')
            page.wait_for_function("() => document.querySelector('#receiver-status').dataset.state==='recovering'")
            assert page.get_by_role('button',name='STOP',exact=True).is_enabled()
            assert 'recovering' in page.locator('#source-status').inner_text()
            assert 'Detection degraded' in page.locator('#health-summary').inner_text()
            assert page.locator('.marker.operator').count()==1
            page.request.post(URL+'/test/new')
            page.wait_for_function("() => document.querySelectorAll('.track-card').length===2")
            assert page.evaluate('window.toneCount')==2
            page.request.post(URL+'/test/return')
            page.wait_for_function("() => document.querySelector('#source-status').textContent.includes('1 USB recoveries')")
            page.wait_for_function("() => document.querySelector('#receiver-counts').textContent.includes('12 decoded')")
            assert page.request.get(URL+'/api/health').json()['receiver']['session_id']==session
            assert page.evaluate('window.toneCount')==2
            page.request.post(URL+'/test/unplug')
            page.wait_for_function("() => document.querySelector('#receiver-status').dataset.state==='recovering'")
            page.get_by_role('button',name='STOP',exact=True).click()
            page.request.post(URL+'/test/return');page.wait_for_timeout(1300)
            assert page.get_by_role('button',name='START',exact=True).is_enabled()
            assert not page.request.get(URL+'/api/health').json()['receiver']['detection_active']
            assert not errors,errors
            assert not external,external
            browser.close()
    finally:
        process.terminate();process.wait(timeout=20)
    print('PASS: USB loss/recovery visible, healthy-feed alert during outage, same session/tracks, no repeat alert and STOP prevents restart; synthetic only')


if __name__=='__main__':main()
