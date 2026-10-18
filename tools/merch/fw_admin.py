"""Hidden helpers for the Fourthwall admin: search products, open a row's ⋯ menu, archive/delete/hide."""
from __future__ import annotations

import sys
import time

from playwright.sync_api import sync_playwright

PROFILE = r"D:\Jashan\Desktop\The_luxury_lane_tool\work\store_profile"
CHROME = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
LIST = "https://admin.fourthwall.com/store/fazza/products/all/"
SHOT = r"D:\Jashan\Desktop\The_luxury_lane_tool\work\fw_audit"


def launch(p):
    ctx = p.chromium.launch_persistent_context(PROFILE, executable_path=CHROME, headless=True,
                                               viewport={"width": 1440, "height": 950}, user_agent=UA)
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.set_default_timeout(60000)
    return ctx, pg


def search(pg, text: str) -> list[str]:
    pg.goto(LIST, wait_until="commit")
    box = pg.get_by_placeholder("Search products")
    box.wait_for()
    time.sleep(3)
    box.fill(text)
    time.sleep(5)
    return [r for r in pg.evaluate(r"""() => [...document.querySelectorAll('tr')].map(t => (t.innerText||'').trim().replace(/\s+/g,' '))""")
            if r and not r.startswith("Product Status")]


def row_menu(pg, name: str) -> list[str]:
    row = pg.locator("tr").filter(has_text=name).first
    row.locator("button").last.click()
    time.sleep(2)
    return pg.evaluate(r"""() => [...document.querySelectorAll('[role=menuitem], [role=menu] button, [role=menu] a')]
        .filter(e => e.offsetParent !== null).map(e => (e.innerText||'').trim()).filter(Boolean)""")


if __name__ == "__main__":
    with sync_playwright() as p:
        ctx, pg = launch(p)
        for q in sys.argv[1:]:
            rows = search(pg, q)
            print(f"[{q}] {len(rows)} rows")
            for r in rows:
                print("   ", r[:120])
        ctx.close()
