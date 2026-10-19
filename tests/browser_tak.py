"""Browser regression: unavailable synthetic TAK never interrupts three sources."""
import subprocess
import sys
import time
from urllib.parse import urlparse
import httpx
from playwright.sync_api import sync_playwright

URL='http://127.0.0.2:8773'
SERVER='''
import sys,uvicorn
sys.path.insert(0,'tests')
from test_combined import synthetic_sources
from aetherlink_scout.combined import CombinedReceiver
from aetherlink_scout.app import create_app
from aetherlink_scout.tak import TakOutput,DEFAULTS
sources,_=synthetic_sources(renew=True)
receiver=CombinedReceiver(sources=sources)
async def unavailable(_):raise ConnectionRefusedError(111,'synthetic TAK unavailable')
receiver.tak_output=TakOutput(receiver,DEFAULTS,connector=unavailable,interval=.02)
uvicorn.run(create_app(receiver=receiver),host='127.0.0.2',port=8773,access_log=False)
'''


def main():
    process=subprocess.Popen([sys.executable,'-c',SERVER],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if process.poll() is not None:raise AssertionError('Synthetic TAK dashboard failed')
            try:
                with httpx.Client(trust_env=False) as client:
                    if client.get(URL+'/api/health').status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.05)
        else:raise AssertionError('Synthetic dashboard startup timed out')
        with sync_playwright() as p:
            browser=p.chromium.launch();page=browser.new_page()
            page.route('**/static/local-location.json',lambda r:r.fulfill(json={'lat':35.007,'lon':-115.011}))
            errors=[];external=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:external.append(request.url) if urlparse(request.url).hostname!='127.0.0.2' else None)
            page.goto(URL,wait_until='domcontentloaded')
            page.wait_for_function("() => document.querySelector('#tak-status').textContent.includes('idle')")
            page.get_by_role('button',name='START',exact=True).click()
            page.wait_for_function("() => document.querySelector('#receiver-counts').textContent.includes('18 decoded')")
            page.wait_for_function("() => document.querySelector('#tak-status').textContent.includes('local detection continues')")
            assert page.locator('.marker.aircraft').count()==1
            assert page.locator('.marker.operator').count()==1
            assert page.locator('#source-status p').count()==3
            assert 'Optional TAK TLS enabled' in page.locator('#mode-description').inner_text()
            assert page.get_by_role('button',name='STOP',exact=True).is_enabled()
            with httpx.Client(trust_env=False) as client:
                health=client.get(URL+'/api/health').json()
                assert health['tak_enabled'] and health['receiver']['detection_active']
                assert 'PYTAK_TLS_CLIENT' not in str(health)
            page.get_by_role('button',name='STOP',exact=True).click()
            page.wait_for_function("() => document.querySelector('#tak-status').textContent.includes('idle')")
            assert not errors,errors
            assert not external,external
            browser.close()
    finally:
        process.terminate();process.wait(timeout=20)
    print('PASS: optional TAK error/status and health, three sources/markers remain active, shared STOP; synthetic only, no external requests')


if __name__=='__main__':main()
