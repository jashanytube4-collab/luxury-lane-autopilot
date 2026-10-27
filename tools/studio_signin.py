"""Opens a VISIBLE Chrome window on the Studio profile so the owner can sign in to YouTube once (Rocky never types passwords).
Closes by itself when Studio has loaded, or after 15 minutes."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright  # noqa: E402

from tools.post_helper import CHROME, PROFILE  # noqa: E402

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(str(PROFILE), executable_path=str(CHROME), headless=False,
                                               viewport={"width": 1200, "height": 850})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://studio.youtube.com/", wait_until="domcontentloaded")
    print("Sign in in the Chrome window. It closes by itself when YouTube Studio opens.", flush=True)
    end = time.time() + 900
    while time.time() < end:
        time.sleep(3)
        try:
            if "studio.youtube.com" in page.url and "accounts.google.com" not in page.url:
                time.sleep(8)
                break
        except Exception:  # noqa: BLE001
            break
    ctx.close()
print("Done. You can close this window.")
