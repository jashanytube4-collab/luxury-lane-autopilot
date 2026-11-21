"""Upload the icon avatars to the brand channels already created (hidden browser, no Claude).
python -m tools.avatar_set [handle ...]"""
import json
import re
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from tools.studio_tool import _open, _go

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "rocky" / "channels" / "new_channels.json"


def one(page, ch) -> str:
    cid = ch["channel_id"]
    _go(page, "https://www.youtube.com/channel_switcher", 5000)
    page.locator("ytd-account-item-renderer").filter(has_text=ch["name"]).first.click(timeout=10000)
    page.wait_for_timeout(8000)
    _go(page, "https://studio.youtube.com/", 8000)
    _go(page, f"https://studio.youtube.com/channel/{cid}/editing/profile", 14000)
    try:
        page.get_by_role("button", name="Continue").click(timeout=2500)
    except Exception:  # noqa: BLE001
        pass
    shown = ""
    for _ in range(10):                      # slow pages: poll up to ~40 s
        m = re.search(r"Your channel\s+(.+)", page.locator("body").inner_text())
        shown = m.group(1).strip() if m else ""
        if shown:
            break
        page.wait_for_timeout(4000)
    if shown != ch["name"] or cid not in page.url:   # wrong channel open -> never upload
        raise RuntimeError(f"wrong channel open, expected {ch['name']}, got {shown!r}")
    btns = [b for b in page.get_by_role("button").all() if b.is_visible() and b.inner_text().strip() in ("Upload", "Change")]
    with page.expect_file_chooser(timeout=15000) as fc:
        btns[1].click()                      # 0 banner, 1 picture
    fc.value.set_files(ch["avatar"])
    page.wait_for_timeout(6000)
    page.get_by_role("button", name="Done").last.click(timeout=15000)
    page.wait_for_timeout(3000)
    page.get_by_role("button", name="Publish").click(timeout=10000)
    page.wait_for_timeout(6000)
    page.screenshot(path=str(ROOT / "work" / "wm" / f"av_{re.sub(chr(92) + 'W+', '_', ch['name'])}.png"))
    return "ok"


if __name__ == "__main__":
    data = json.loads(DB.read_text(encoding="utf-8"))
    todo = [c for c in data if c.get("created") and (not sys.argv[1:] or c["handle"] in sys.argv[1:])]
    with sync_playwright() as p:
        ctx, page = _open(p)
        try:
            for c in todo:
                try:
                    print(c["name"], "->", one(page, c), flush=True)
                except Exception as e:  # noqa: BLE001
                    print(c["name"], "-> ERROR", str(e)[:200].replace("\n", " "), flush=True)
        finally:
            ctx.close()
