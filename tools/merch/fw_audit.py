"""Hidden check of the Fourthwall store: screenshots of the public shop and the admin settings, plus every
visible text that contains the owner's personal name."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE = r"D:\Jashan\Desktop\The_luxury_lane_tool\work\store_profile"
CHROME = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
OUT = Path(r"D:\Jashan\Desktop\The_luxury_lane_tool\work\fw_audit")
SHOP = "https://fazza-shop.fourthwall.com"
ADMIN = "https://admin.fourthwall.com/store/fazza"
PAGES = {
    "shop_home": SHOP, "shop_all": f"{SHOP}/collections/all", "shop_about": f"{SHOP}/pages/about",
    "admin_general": f"{ADMIN}/settings/general", "admin_design": f"{ADMIN}/store-design/",
    "admin_settings": f"{ADMIN}/settings/", "admin_products": f"{ADMIN}/products/all/",
}

if __name__ == "__main__":
    only = sys.argv[1:] or list(PAGES)
    OUT.mkdir(parents=True, exist_ok=True)
    report = {}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, executable_path=CHROME, headless=True,
                                                   viewport={"width": 1440, "height": 950},
                                                   user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                                               "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"))
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        for name in only:
            url = PAGES.get(name, name)
            try:
                pg.goto(url, wait_until="commit", timeout=90000)
                time.sleep(9)
                for _ in range(4):
                    pg.mouse.wheel(0, 2500)
                    time.sleep(1)
                pg.screenshot(path=str(OUT / f"{name}.png"), full_page=True, timeout=90000, animations="disabled")
                text = pg.evaluate("() => document.body.innerText")
                (OUT / f"{name}.txt").write_text(text, encoding="utf-8")
                report[name] = {"url": pg.url, "jashan": [l for l in text.splitlines() if "jashan" in l.lower()],
                                "links": pg.evaluate(r"""() => [...document.querySelectorAll('a')].map(a => (a.innerText||'').trim().replace(/\s+/g,' ').slice(0,40) + ' -> ' + (a.getAttribute('href')||'')).filter(x => x.length > 6).slice(0, 80)""")}
            except Exception as e:  # noqa: BLE001
                report[name] = {"error": str(e)[:200]}
        ctx.close()
    (OUT / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    for k, v in report.items():
        print(k, v.get("url", ""), "| name mentions:", v.get("jashan", v.get("error")))
