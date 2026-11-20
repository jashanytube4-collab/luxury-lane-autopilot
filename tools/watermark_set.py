"""Set YouTube's native video watermark (small, bottom right, long videos only) on every channel in the switcher.
python -m tools.watermark_set [channel name ...]   (no args = all). Hidden browser, no Claude."""
import re
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from tools.studio_tool import _open, _go

IMG = str(Path(__file__).resolve().parent.parent / "work" / "wm" / "watermark.png")
SH = Path(__file__).resolve().parent.parent / "work" / "wm"


def set_one(page, name: str) -> str:
    _go(page, "https://www.youtube.com/channel_switcher", 4000)
    page.get_by_text(name, exact=True).first.click(timeout=10000)
    page.wait_for_timeout(7000)
    _go(page, "https://studio.youtube.com/", 8000)
    cid = page.url.split("/channel/")[1].split("/")[0].split("?")[0]
    _go(page, f"https://studio.youtube.com/channel/{cid}/editing/profile", 8000)
    try:
        page.get_by_role("button", name="Continue").click(timeout=2500)
    except Exception:  # noqa: BLE001
        pass
    title = page.get_by_text("Video watermark", exact=True).first
    title.scroll_into_view_if_needed(timeout=10000)
    page.wait_for_timeout(1000)
    slug = re.sub(r"\W+", "_", name)
    btns = [b for b in page.get_by_role("button").all() if b.is_visible() and b.inner_text().strip() in ("Upload", "Change")]
    with page.expect_file_chooser(timeout=15000) as fc:
        btns[-1].click()
    fc.value.set_files(IMG)
    page.wait_for_timeout(6000)
    try:
        page.get_by_role("button", name="Done").last.click(timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(2500)
    try:
        page.get_by_text("Entire video", exact=True).first.click(timeout=4000)
    except Exception:  # noqa: BLE001
        pass
    page.screenshot(path=str(SH / f"{slug}_before_publish.png"))
    page.get_by_role("button", name="Publish").click(timeout=10000)
    page.wait_for_timeout(6000)
    page.screenshot(path=str(SH / f"{slug}_done.png"))
    return "ok"


if __name__ == "__main__":
    with sync_playwright() as p:
        ctx, page = _open(p)
        try:
            _go(page, "https://www.youtube.com/channel_switcher", 5000)
            allnames = page.evaluate("""()=>[...document.querySelectorAll('yt-formatted-string#channel-title')].map(e=>e.innerText.trim())""")
            for n in (sys.argv[1:] or allnames):
                try:
                    print(n, "->", set_one(page, n), flush=True)
                except Exception as e:  # noqa: BLE001
                    print(n, "-> ERROR", str(e)[:200].replace("\n", " "), flush=True)
        finally:
            ctx.close()
