"""Isolated scan-status dashboard. Capture is replaced by a refusing factory."""
import subprocess
import sys
import time
from urllib.parse import urlparse

import httpx
from playwright.sync_api import sync_playwright

URL = 'http://127.0.0.2:8770'
SERVER = """
import time
from types import SimpleNamespace
import uvicorn
from aetherlink_scout.app import create_app
from aetherlink_scout.receiver import Receiver
from aetherlink_scout.capture import CaptureError

def refuse(interface):
    raise CaptureError('Synthetic scan failure; no radio commands executed')
r = Receiver(live_interface='wlx9cefd5f67d4c', scan=True, capture_factory=refuse)
r.capture = SimpleNamespace(verified=(149,5745,time.monotonic()), stop=lambda: None)
uvicorn.run(create_app(receiver=r), host='127.0.0.2', port=8770, access_log=False)
"""


def main():
    process = subprocess.Popen([sys.executable, '-c', SERVER], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError('Synthetic scan server failed')
            try:
                with httpx.Client(trust_env=False) as client:
                    if client.get(URL+'/api/health').status_code == 200:
                        break
            except httpx.HTTPError:
                pass
            time.sleep(.05)
        else:
            raise AssertionError('Synthetic scan server startup timed out')
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            errors, external = [], []
            page.route('**/static/local-location.json', lambda r: r.fulfill(json={'lat':35.007,'lon':-115.011}))
            page.on('pageerror', lambda e: errors.append(type(e).__name__))
            page.on('request', lambda r: external.append(urlparse(r.url).hostname) if urlparse(r.url).hostname != '127.0.0.2' else None)
            page.goto(URL, wait_until='domcontentloaded')
            page.wait_for_function("() => document.querySelector('#radio-status').textContent.includes('last verified channel 149')")
            text = page.locator('#radio-status').inner_text()
            assert 'Scan 6 (3s) / 149 (1s)' in text
            assert 'packet frequency not verified yet' in text
            assert page.locator('#mode-badge').inner_text() == 'LIVE DETECTION'
            assert page.get_by_role('button',name='START',exact=True).is_enabled()
            page.get_by_role('button',name='START',exact=True).click()
            page.get_by_text('Synthetic scan failure; no radio commands executed',exact=True).wait_for()
            assert page.get_by_role('button',name='START',exact=True).is_enabled()
            assert not errors, errors
            assert not external, external
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=15)
    print('PASS: scan plan, last verified channel, unverified packet frequency, explicit START and visible failure; no radio or external requests')


if __name__ == '__main__':
    main()
