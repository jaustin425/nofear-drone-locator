"""BLE dashboard controls with synthetic pipe producer; no Bluetooth operations."""
import subprocess
import sys
import time
from urllib.parse import urlparse
import httpx
from playwright.sync_api import sync_playwright

URL='http://127.0.0.2:8771'
SERVER=r'''
import io,json,subprocess,sys
import uvicorn
sys.path.insert(0,'tests')
from test_ble import advertisement,basic,location,system
from aetherlink_scout.ble import BleReceiver,BleCapture
from aetherlink_scout.app import create_app
class Synthetic(BleCapture):
    def start(self):
        events=[{'kind':'ready'},advertisement(basic()),advertisement(location(),1),advertisement(system(),2),{'kind':'status','service_data_updates':10,'rid_candidates':3}]
        raw=(''.join(json.dumps(e)+'\n' for e in events)).encode()
        self.log=io.BytesIO()
        self.process=subprocess.Popen([sys.executable,'-c',f'import os,time;time.sleep(.1);os.write(1,{raw!r});time.sleep(60)'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        return self.process.stdout.fileno()
receiver=BleReceiver(capture_factory=Synthetic)
uvicorn.run(create_app(receiver=receiver),host='127.0.0.2',port=8771,access_log=False)
'''


def main():
    process=subprocess.Popen([sys.executable,'-c',SERVER],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if process.poll() is not None:raise AssertionError('Synthetic BLE server failed')
            try:
                with httpx.Client(trust_env=False) as client:
                    if client.get(URL+'/api/health').status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.05)
        else:raise AssertionError('Server startup timed out')
        with sync_playwright() as p:
            browser=p.chromium.launch()
            page=browser.new_page(viewport={'width':1440,'height':960})
            errors=[];external=[]
            page.route('**/static/local-location.json',lambda route:route.fulfill(json={'lat':35.007,'lon':-115.011}))
            page.on('pageerror',lambda e:errors.append(type(e).__name__))
            page.on('request',lambda r:external.append(urlparse(r.url).hostname) if urlparse(r.url).hostname!='127.0.0.2' else None)
            page.goto(URL,wait_until='domcontentloaded')
            page.wait_for_function("() => document.querySelector('#radio-status').textContent.includes('Legacy BLE only')")
            assert page.locator('.track-card').count()==0
            page.get_by_role('button',name='START',exact=True).click()
            page.wait_for_function("() => document.querySelectorAll('.track-card').length===1 && document.querySelector('#receiver-counts').textContent.includes('3 decoded')")
            page.wait_for_function("() => document.querySelector('#radio-status').textContent.includes('10 service-data updates')")
            assert 'RID candidate advertisements' in page.locator('#receiver-counts').inner_text()
            assert '10 BLE service updates' in page.locator('#receiver-counts').inner_text()
            assert page.locator('.marker.aircraft').count()==1
            assert page.locator('.marker.operator').count()==1
            page.get_by_role('button',name='STOP',exact=True).click()
            page.wait_for_function("() => !document.querySelector('#start-detection').disabled")
            before=page.locator('#receiver-counts').inner_text()
            page.wait_for_timeout(400)
            assert page.locator('#receiver-counts').inner_text()==before
            page.reload(wait_until='domcontentloaded')
            page.wait_for_function("() => document.querySelectorAll('.track-card').length===1")
            assert page.get_by_role('button',name='START',exact=True).is_enabled()
            assert not errors,errors
            assert not external,external
            browser.close()
    finally:
        process.terminate();process.wait(timeout=15)
    print('PASS: BLE coverage label, explicit START, decoded aircraft/operator map, counters, STOP and retained refresh; synthetic only, no external requests')


if __name__=='__main__':main()
