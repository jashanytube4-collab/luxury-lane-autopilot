"""Hidden: set the shop title, description and social sharing image in Fourthwall Settings > General, then save."""
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE = r"D:\Jashan\Desktop\The_luxury_lane_tool\work\store_profile"
CHROME = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
URL = "https://admin.fourthwall.com/store/fazza/settings/general"
TITLE = "The Luxury Lane — Official Store"
DESC = ("The official store of The Luxury Lane: Hamdanians and Fazza Fam merch, Dubai-inspired designs and gold art "
        "prints. Printed to order and shipped worldwide, including India.")
SHARE = r"D:\Jashan\Desktop\The_luxury_lane_tool\work\brand\share_1200x630.jpg"
OUT = Path(r"D:\Jashan\Desktop\The_luxury_lane_tool\work\fw_audit")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(PROFILE, executable_path=CHROME, headless=True,
                                               viewport={"width": 1440, "height": 950})
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.set_default_timeout(60000)
    pg.goto(URL, wait_until="commit")
    pg.get_by_label("Shop title").wait_for()
    time.sleep(3)
    pg.get_by_label("Shop title").fill(TITLE)
    desc = pg.locator("textarea").first
    desc.fill(DESC)
    files = pg.locator("input[type=file]")
    if files.count():
        files.first.set_input_files(SHARE)
        time.sleep(8)
    time.sleep(2)
    pg.screenshot(path=str(OUT / "settings_before_save.png"), full_page=True, animations="disabled")
    save = pg.get_by_role("button", name="Save")
    print("save buttons:", save.count())
    if save.count():
        save.first.click()
        time.sleep(6)
    pg.reload(wait_until="commit")
    pg.get_by_label("Shop title").wait_for()
    time.sleep(3)
    print("title now:", pg.get_by_label("Shop title").input_value())
    print("desc now:", pg.locator("textarea").first.input_value()[:90])
    pg.screenshot(path=str(OUT / "settings_after_save.png"), full_page=True, animations="disabled")
    ctx.close()
