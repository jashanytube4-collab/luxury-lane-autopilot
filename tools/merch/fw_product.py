"""Create one Fourthwall product in the owner's logged-in window: base product -> upload design -> details -> publish.

    python -m tools.merch.fw_product <catalog slug> <design png> <name> <price> <description> [color]
"""
from __future__ import annotations

import sys
import time

from playwright.sync_api import sync_playwright

ADMIN = "https://admin.fourthwall.com/store/fazza"


CTX = None          # set by run_batch: a browser context the tool launched itself (no remote attach)


class _B:
    def __init__(self, ctx):
        self.contexts = [ctx]


def latest(b):
    return b.contexts[0].pages[-1]


def create(slug: str, design: str, name: str, price: str, description: str, color: str = "") -> str:
    if True:
        b = _B(CTX)
        pg = latest(b)
        pg.set_default_timeout(120000)
        pg.goto(f"{ADMIN}/catalog/products/{slug}", wait_until="commit")
        pg.get_by_role("button", name="Design now").first.click()
        for _ in range(40):
            pg = latest(b)
            if "/designer/" in pg.url:
                break
            time.sleep(1)
        pg.set_default_timeout(120000)
        pg.get_by_role("button", name="Upload image").first.wait_for()
        time.sleep(3)
        inputs = pg.locator("input[type=file]")
        if inputs.count():                       # straight into the upload field (no file dialog needed)
            inputs.first.set_input_files(design)
        else:
            with pg.expect_file_chooser() as fc:
                pg.get_by_role("button", name="Upload image").first.click()
            fc.value.set_files(design)
        # wait until the design shows on the product (Add to shop becomes active)
        for _ in range(240):                     # Fourthwall can take minutes to process a big upload
            if pg.get_by_role("button", name="Add to shop").first.is_enabled() and                     not pg.get_by_text("Processing assets").count():
                break
            time.sleep(1)
        time.sleep(6)
        if color:
            sw = pg.get_by_role("button", name=color, exact=True)
            if sw.count():
                sw.first.click()
                time.sleep(2)
        pg.get_by_role("button", name="Add to shop").first.click()
        for _ in range(90):
            pg = latest(b)
            if pg.url.endswith("/details"):
                break
            lowres = pg.get_by_text("continue with low resolution art")
            if lowres.count() and lowres.first.is_visible():   # only after the art was re-rendered at high resolution
                print("note: Fourthwall still flags low resolution for some sizes", flush=True)
                pg.evaluate("""() => { const b = [...document.querySelectorAll('button')]
                    .find(x => /continue with current quality/i.test(x.textContent || '')); if (b) b.click(); }""")
                time.sleep(3)
            review = pg.get_by_text("Review your design on different sizes")
            if review.count() and review.first.is_visible():     # mugs/posters: check every size, then confirm
                pg.mouse.move(950, 500)
                for _ in range(8):
                    pg.mouse.wheel(0, 1200)
                    time.sleep(0.5)
                ok = pg.get_by_role("button", name="Confirm").first
                if ok.is_enabled():
                    ok.click()
            time.sleep(2)
        pg.set_default_timeout(120000)
        pg.locator("input[name=name]").wait_for()
        time.sleep(3)
        pg.locator("input[name=name]").fill(name)
        # description: the rich-text editor (inside an iframe when present)
        body = None
        for _ in range(45):                      # the description editor loads a moment after the form
            for f in pg.frames:
                if f != pg.main_frame and f.locator("body[contenteditable=true], body#tinymce").count():
                    body = f.locator("body").first
                    break
            if body is None and pg.locator("[contenteditable=true]").count():
                body = pg.locator("[contenteditable=true]").first
            if body is not None:
                break
            time.sleep(2)
        if body is None:
            raise RuntimeError("description editor did not load")
        body.click()
        pg.keyboard.insert_text(description)
        boxes = pg.locator("input[name=price]")
        absolute, _, extra_s = price.partition("/")      # "24.99/8": one price 24.99, or per size: default + 8
        if boxes.count() == 1:
            boxes.first.fill(absolute)
            boxes.first.press("Tab")
        else:                       # one price per size (mugs, posters): each size's default + the same extra profit
            extra = float(extra_s or absolute)
            for i in range(boxes.count()):
                box = boxes.nth(i)
                base = float(box.input_value() or 0)
                box.fill(f"{int(base + extra) + 0.99:.2f}")
                box.press("Tab")
        time.sleep(2)
        import re as _re
        for _ in range(3):                       # below Fourthwall's minimum: use the minimum + $5 profit
            msg = pg.get_by_text(_re.compile(r"must be \$[0-9.]+ or higher"))
            if not msg.count():
                break
            low = float(_re.search(r"\$([0-9.]+)", msg.first.inner_text()).group(1))
            box = pg.locator("input[name=price]").first
            box.fill(f"{int(low + 5) + 0.99:.2f}")
            box.press("Tab")
            time.sleep(2)
        pg.screenshot(path=r"D:\Jashan\Desktop\The_luxury_lane_tool\work\fw\last_details.png", timeout=60000,
                      animations="disabled")
        pg.get_by_role("button", name="Publish now").first.click()
        pg.get_by_text("Congratulations!").first.wait_for(timeout=120000)
        time.sleep(3)
        return "published: " + name


if __name__ == "__main__":
    print(create(*sys.argv[1:]))
