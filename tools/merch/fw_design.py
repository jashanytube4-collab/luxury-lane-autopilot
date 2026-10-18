"""Hidden: home page design — banner image + no heading + button at the bottom, remove the Donations section, save."""
import sys
import time

sys.path.insert(0, r"D:\Jashan\Desktop\The_luxury_lane_tool")
from playwright.sync_api import sync_playwright  # noqa: E402

from tools.merch.fw_admin import SHOT, launch  # noqa: E402

HERO = r"D:\Jashan\Desktop\The_luxury_lane_tool\work\brand\hero_2400x1100.jpg"
URL = "https://admin.fourthwall.com/store/fazza/store-design/"


def panel(pg):
    return pg.locator("body")


with sync_playwright() as p:
    ctx, pg = launch(p)
    pg.goto(URL, wait_until="commit")
    pg.get_by_text("Image banner", exact=True).first.wait_for()
    time.sleep(10)
    # --- banner
    pg.get_by_text("Image banner", exact=True).first.click()
    time.sleep(5)
    heading = pg.locator("[contenteditable=true]").first
    if not heading.count():
        for f in pg.frames:
            if f.locator("body#tinymce").count():
                heading = f.locator("body#tinymce").first
                break
    heading.click()
    pg.keyboard.press("Control+A")
    pg.keyboard.press("Delete")
    time.sleep(1)
    pg.get_by_text("Background image", exact=True).first.scroll_into_view_if_needed()
    time.sleep(1)
    pg.mouse.click(273, 709)                       # ✕ on the old gradient image
    time.sleep(3)
    files = pg.locator("input[type=file]")
    print("file inputs:", files.count())
    if files.count():
        files.first.set_input_files(HERO)
    else:
        with pg.expect_file_chooser(timeout=20000) as fc:
            pg.get_by_text("Background image", exact=True).first.locator("xpath=following::*[self::button or @role='button'][1]").click()
        fc.value.set_files(HERO)
    time.sleep(15)
    pg.screenshot(path=SHOT + r"\design_banner_uploaded.png", animations="disabled")
    for label, want in (("Section size", "Adapt to image"), ("Content position", "Bottom")):
        sel = pg.locator(f"select[aria-label='{label}']").first
        sel.scroll_into_view_if_needed()
        sel.select_option(label=want, force=True)
        time.sleep(2)
    time.sleep(3)
    time.sleep(3)
    pg.get_by_role("button", name="Save").first.click()
    time.sleep(10)
    pg.screenshot(path=SHOT + r"\design_banner_after.png", animations="disabled")
    print("saved banner; Save still enabled:", pg.get_by_role("button", name="Save").first.is_enabled())
    pg.mouse.click(32, 124)                         # back to the section list
    time.sleep(5)
    pg.get_by_text("Donations", exact=True).first.click()
    time.sleep(4)
    pg.mouse.click(288, 124)                        # the ⋯ menu of the section
    time.sleep(2)
    print("menu:", pg.evaluate(r"""() => [...document.querySelectorAll('[role=menuitem], [role=menu] button, li button')].filter(e=>e.offsetParent!==null).map(e=>(e.innerText||'').trim()).filter(Boolean)"""))
    pg.screenshot(path=SHOT + r"\design_donations.png", animations="disabled")
    ctx.close()
