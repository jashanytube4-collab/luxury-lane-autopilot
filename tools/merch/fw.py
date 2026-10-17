"""Drive the owner's Fourthwall admin in the Chrome window the owner logged into (kept open, debug port 9333)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

SHOTS = Path(__file__).resolve().parents[2] / "work" / "fw"
ADMIN = "https://admin.fourthwall.com/store/fazza"
CDP = "http://127.0.0.1:9333"


def attach(p):
    """The page of the already-open, logged-in window (never closes the browser)."""
    browser = p.chromium.connect_over_cdp(CDP)
    ctx = browser.contexts[0]
    pages = [pg for pg in ctx.pages if "fourthwall" in pg.url] or ctx.pages
    page = pages[0]
    page.set_default_timeout(30000)
    page.bring_to_front()
    SHOTS.mkdir(parents=True, exist_ok=True)
    return browser, page


def shot(page, name: str) -> None:
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def visible_controls(page) -> list[str]:
    return page.evaluate(r"""() => [...document.querySelectorAll('a, button, [role=button], input, select, textarea')]
      .filter(e => e.offsetParent !== null)
      .map(e => (e.tagName + ' ' + (e.innerText || e.value || e.getAttribute('aria-label') || e.getAttribute('placeholder') || '').trim().replace(/\s+/g, ' ').slice(0, 50)
                 + (e.getAttribute('href') ? ' -> ' + e.getAttribute('href').slice(0, 80) : '')))
      .filter(x => x.length > 2)""")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    with sync_playwright() as p:
        browser, page = attach(p)
        if path != "-":
            page.goto(f"{ADMIN}/{path}", wait_until="domcontentloaded")
            time.sleep(6)
        print("url:", page.url)
        shot(page, (path or "home").replace("/", "_").strip("_-") or "home")
        for c in visible_controls(page)[:140]:
            print("  ", c)
