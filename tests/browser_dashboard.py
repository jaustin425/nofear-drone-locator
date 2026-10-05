"""Browser checks against the running local synthetic and historical replay servers."""
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        errors, external = [], []
        page.on("pageerror", lambda error: errors.append(type(error).__name__))
        page.on("request", lambda request: external.append(request.url) if urlparse(request.url).hostname != "127.0.0.2" else None)
        page.goto("http://127.0.0.2:8766", wait_until="networkidle")
        assert page.title() == "AetherLink Scout • Local drone awareness"
        assert page.locator("header h1").inner_text() == "AetherLink Scout"
        page.get_by_role("button", name="Play replay", exact=True).click()
        page.wait_for_selector(".track-card")
        page.get_by_role("button", name="Pause", exact=True).click()
        assert page.locator(".track-card").count() == 1
        assert page.locator(".marker.aircraft").count() == 1
        assert page.locator(".marker.operator").count() == 1
        assert page.locator("#alerts .log-item").count() == 1
        page.get_by_role("button", name="Acknowledge", exact=True).click()
        assert page.locator("#alert-toast").is_hidden()
        page.get_by_role("button", name="Sound off", exact=True).click()
        assert page.locator("#sound").get_attribute("aria-pressed") == "true"
        page.locator("#speed").select_option("10")
        page.get_by_role("button", name="Play replay", exact=True).click()
        page.locator(".track-card").nth(1).wait_for()
        page.get_by_role("button", name="Pause", exact=True).click()
        assert page.locator("#alerts .log-item").count() == 2
        assert page.locator(".marker.aircraft").count() == 2
        assert not external, "Default replay made an external request"
        page.locator(".right-panel").evaluate("el => {el.scrollTop=0;}")
        out = Path("local-data/dashboard-verification")
        out.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out/"synthetic-desktop.png"))
        page.locator("#seek").evaluate("el => {el.value=80; el.dispatchEvent(new Event('input', {bubbles:true}));}")
        assert page.locator(".stale-status").count() == 2
        assert page.locator("#alerts .log-item").count() == 0
        assert page.locator(".marker.is-stale").count() == 4
        page.get_by_role("button", name="Restart replay", exact=True).click()
        assert page.locator(".track-card").count() == 0
        page.get_by_role("button", name="Play replay", exact=True).click()
        page.wait_for_selector(".track-card")
        page.get_by_role("button", name="Pause", exact=True).click()
        assert page.locator("#alerts .log-item").count() == 1
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(out/"synthetic-mobile.png"), full_page=True)
        assert not errors, "Browser JavaScript errors"

        # Real capture: verify marker count and seven-decimal source coordinates in details.
        page.set_viewport_size({"width": 1440, "height": 960})
        page.goto("http://127.0.0.2:8765", wait_until="networkidle")
        page.get_by_role("button", name="Play replay", exact=True).click()
        page.wait_for_selector(".track-card")
        page.get_by_role("button", name="Pause", exact=True).click()
        assert page.locator(".marker.aircraft").count() == 1
        assert page.locator(".marker.operator").count() == 1
        assert page.evaluate("""async () => {
          const data=await (await fetch('/api/replay')).json();
          const p=data.records[0].aircraft;
          return document.querySelector('#aircraft-details').textContent.includes(`${p.lat.toFixed(7)}, ${p.lon.toFixed(7)}`);
        }""")
        assert not external
        assert not errors
        browser.close()
    print("PASS: map markers, details, playback/pause/speed/restart, once-per-track alerts, acknowledgement/sound, silent seek, stale positions, mobile layout, real-capture coordinates, no default external requests")


if __name__ == "__main__":
    main()
