"""Synthetic browser location and mocked address/directions; no real fixes or outbound API calls."""
import json
import subprocess
import sys
import time
from urllib.parse import urlparse
import httpx
from playwright.sync_api import sync_playwright

URL='http://127.0.0.2:8769'


def server():
    process=subprocess.Popen([sys.executable,'-m','aetherlink_scout','--demo','--port','8769'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    for _ in range(80):
        if process.poll() is not None:
            raise AssertionError('Synthetic location server could not start')
        try:
            with httpx.Client(trust_env=False) as client:
                if client.get(URL+'/api/health').status_code==200:
                    return process
        except httpx.HTTPError:
            pass
        time.sleep(.05)
    process.terminate();process.wait(timeout=10)
    raise AssertionError('Synthetic location server startup timed out')


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch()
        context=browser.new_context(geolocation={'latitude':35.009,'longitude':-115.013,'accuracy':25}, permissions=['geolocation'])
        # Override the private laptop preset; never use real coordinates here.
        context.route('**/static/local-location.json',lambda r:r.fulfill(json={'lat':35.007,'lon':-115.011}))
        page=context.new_page()
        page.add_init_script("""window.openedDirections=[]; window.open=(url)=>{window.openedDirections.push(url); return null;};
          window.locationRequests=0; const watch=navigator.geolocation.watchPosition.bind(navigator.geolocation);
          navigator.geolocation.watchPosition=(...args)=>{window.locationRequests++; return watch(...args);};""")
        errors=[];external=[]
        page.on('pageerror',lambda e: errors.append(type(e).__name__))
        page.on('request',lambda r:external.append(urlparse(r.url).hostname) if urlparse(r.url).hostname!='127.0.0.2' else None)
        page.goto(URL,wait_until='networkidle')
        assert '35.0070000, -115.0110000' in page.locator('#my-location-status').inner_text()
        assert 'Testing preset' in page.locator('#my-location-status').inner_text()
        assert 'not GPS' in page.locator('#my-location-status').inner_text()
        assert page.locator('#my-location-kind').inner_text()=='FIXED'
        assert page.locator('#my-latitude').input_value()=='35.007'
        assert page.locator('.marker.me').count()==1
        assert page.evaluate('window.locationRequests')==0
        page.reload(wait_until='networkidle')
        assert 'Testing preset' in page.locator('#my-location-status').inner_text()
        page.get_by_role('button',name='Use browser location',exact=True).click()
        page.locator('.marker.me').wait_for()
        assert '25 m' in page.locator('#my-location-status').inner_text()
        assert page.locator('#my-location-kind').inner_text()=='CURRENT'
        # Browser fixes remain transient; refresh restores the fixed preset.
        page.reload(wait_until='networkidle')
        assert 'Testing preset' in page.locator('#my-location-status').inner_text()
        assert page.evaluate('window.locationRequests')==0
        page.get_by_role('button',name='Use browser location',exact=True).click()
        page.wait_for_function("() => document.querySelector('#my-location-status').textContent.includes('25 m')")
        page.get_by_role('button',name='Center on me',exact=True).click()
        page.get_by_role('button',name='Stop browser location',exact=True).click()
        page.get_by_role('button',name='Clear',exact=True).click()
        assert page.locator('.marker.me').count()==0
        page.reload(wait_until='networkidle')
        assert 'Not set' in page.locator('#my-location-status').inner_text()
        page.locator('.my-location-panel summary').click()
        page.locator('#my-latitude').fill('35.009')
        page.locator('#my-longitude').fill('-115.013')
        page.get_by_role('button',name='Set my location',exact=True).click()
        assert 'Manually set' in page.locator('#my-location-status').inner_text()
        assert 'accuracy unknown' in page.locator('#my-location-status').inner_text()
        page.reload(wait_until='networkidle')
        assert '35.0090000, -115.0130000' in page.locator('#my-location-status').inner_text()
        assert 'Manually set' in page.locator('#my-location-status').inner_text()
        page.clock.install()
        page.clock.fast_forward(61000)
        assert 'stale' not in page.locator('#my-location-status').inner_text()
        page.get_by_role('button',name='Play replay',exact=True).click()
        page.locator('.track-card').wait_for()
        page.get_by_role('button',name='Pause',exact=True).click()
        assert page.locator('#lookup-address').is_disabled()
        assert page.locator('#details .detail-section').first.inner_text()=='REPORTED OPERATOR'
        assert page.evaluate("() => document.querySelector('#details').compareDocumentPosition(document.querySelector('.operator-actions')) & Node.DOCUMENT_POSITION_FOLLOWING")
        assert page.evaluate("() => document.querySelector('.operator-actions').compareDocumentPosition(document.querySelector('#aircraft-details')) & Node.DOCUMENT_POSITION_FOLLOWING")
        page.on('dialog',lambda dialog: dialog.accept())
        page.get_by_role('button',name='Driving directions ↗',exact=True).click()
        assert page.evaluate("() => {const u=new URL(window.openedDirections[0]); return u.searchParams.get('origin')==='35.009,-115.013' && u.searchParams.get('destination')==='35.008,-115.012' && u.searchParams.get('travelmode')==='driving';}")
        page.route('**/api/health',lambda r: r.fulfill(json={'address_lookup_enabled':True,'address_provider':'Geoapify'}))
        requests=[]
        def address(route):
            requests.append(json.loads(route.request.post_data))
            route.fulfill(json={'address':'<img onerror=alert(1)> Synthetic address','provider':'Geoapify','attribution':'Synthetic test'})
        page.route('**/api/address',address)
        page.reload(wait_until='networkidle')
        assert 'Manually set' in page.locator('#my-location-status').inner_text()
        assert not requests
        page.get_by_role('button',name='Play replay',exact=True).click()
        page.locator('.track-card').wait_for()
        page.get_by_role('button',name='Pause',exact=True).click()
        page.get_by_role('button',name='Look up nearby address',exact=True).click()
        page.get_by_text('<img onerror=alert(1)> Synthetic address',exact=True).wait_for()
        assert requests==[{'latitude':35.008,'longitude':-115.012}]
        assert page.locator('#operator-address img').count()==0
        assert page.get_by_role('link',name='Powered by Geoapify',exact=True).count()==1
        page.get_by_role('button',name='Clear',exact=True).click()
        page.reload(wait_until='networkidle')
        assert 'Not set' in page.locator('#my-location-status').inner_text()
        assert page.locator('.marker.me').count()==0
        page.evaluate("localStorage.setItem('aetherlink-scout.manual-location.v1','{invalid')")
        page.reload(wait_until='networkidle')
        assert 'Testing preset' in page.locator('#my-location-status').inner_text()
        # Blocked storage still permits the preset and manual changes, with a
        # truthful persistence warning instead of breaking the dashboard.
        page.add_init_script("Storage.prototype.getItem=()=>{throw new Error('blocked')}; Storage.prototype.setItem=()=>{throw new Error('blocked')};")
        page.reload(wait_until='networkidle')
        assert 'Testing preset' in page.locator('#my-location-status').inner_text()
        page.locator('.my-location-panel summary').click()
        page.locator('#my-latitude').fill('35.01')
        page.locator('#my-longitude').fill('-115.02')
        page.get_by_role('button',name='Set my location',exact=True).click()
        assert '35.0100000, -115.0200000' in page.locator('#my-location-status').inner_text()
        assert 'cannot be remembered' in page.locator('#my-location-status').inner_text()
        assert not external
        assert not errors
        browser.close()
    print('PASS: synthetic preset, refresh persistence, remembered clear/manual override, transient browser fixes, fixed-origin directions after 60s, malformed storage fallback, safe address text, operator-first layout, no external requests')


if __name__=='__main__':
    process=server()
    try:
        main()
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=10)
