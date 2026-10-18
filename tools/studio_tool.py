"""YouTube Studio from the command line, in a hidden browser (the owner's signed-in profile in work/studio_profile).

Used by Rocky (the owner's phone assistant) and handy by hand:

    python -m tools.studio_tool videos [long|short|post|live] [pages]   content list: id, date, visibility, views...
    python -m tools.studio_tool video <id>                              one video's analytics (overview, reach, engagement)
    python -m tools.studio_tool channel [28|7|90|365]                   channel analytics for the last N days
    python -m tools.studio_tool reschedule <id> "Oct 19, 2026" ["8:10 PM"]   move a scheduled video (times are IST)

Read-only except `reschedule`, which changes only the publish date/time and checks the result after reloading.
Never opens a window on the owner's screen.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.post_helper import CHROME, PROFILE  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/155.0.0.0 Safari/537.36")
SHOTS = ROOT / "work" / "studio_tool"
TABS = {"long": "upload", "short": "short", "post": "posts", "live": "live"}
PERIODS = {"7": "period-last_7_days", "28": "period-default", "90": "period-last_90_days",
           "365": "period-last_365_days"}


def _open(p):
    ctx = p.chromium.launch_persistent_context(str(PROFILE), executable_path=str(CHROME), headless=True,
                                               user_agent=UA, viewport={"width": 1500, "height": 1100})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.set_default_timeout(20000)
    return ctx, page


def _go(page, url: str, wait: int = 8000) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(wait)
    if "accounts.google.com" in page.url:
        raise SystemExit("Studio is signed out: the owner must sign in once (python -m tools.post_helper --test)")


def _text(page) -> str:
    txt = page.locator("#main, ytcp-app").first.inner_text()
    txt = txt.split("Advanced mode", 1)[-1]          # drop the navigation menu
    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
    keep, skip = [], {"Overview", "Reach", "Engagement", "Audience", "Revenue", "Research", "Inspiration",
                      "See more", "See all", "Content", "Chart guide"}
    for ln in lines:
        if ln in skip or re.fullmatch(r"[\d.,]+[KL]?", ln) and keep and keep[-1] in ("0", ln):
            continue
        keep.append(ln)
    return "\n".join(keep)


def videos(kind: str = "long", pages: int = 1) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx, page = _open(p)
        try:
            _go(page, f"https://studio.youtube.com/channel/UC/videos/{TABS.get(kind, kind)}")
            for n in range(max(1, pages)):
                rows = page.eval_on_selector_all("ytcp-video-row", """rows => rows.map(r => {
                    const a = r.querySelector("a[href*='/video/']");
                    const title = r.querySelector('#video-title');
                    const cells = [...r.querySelectorAll('[class*=tablecell-]')]
                        .filter(c => !/tablecell-video|tablecell-checkbox/.test(c.className))
                        .map(c => c.innerText.replace(/\\s+/g, ' ').trim()).filter(Boolean);
                    return [(a && a.getAttribute('href')) || '', title ? title.innerText.trim() : '', cells];
                })""")
                for href, title, cells in rows:
                    m = re.search(r"/video/([\w-]{11})", href)
                    print(" | ".join([m.group(1) if m else "?", *cells, title]))
                nxt = page.locator("#navigate-after")
                if n + 1 >= pages or not nxt.count() or nxt.first.get_attribute("aria-disabled") == "true":
                    break
                nxt.first.click()
                page.wait_for_timeout(5000)
        finally:
            ctx.close()


def video(vid: str) -> None:
    from playwright.sync_api import sync_playwright
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx, page = _open(p)
        try:
            for tab in ("overview", "reach_viewers", "interest_viewers"):
                _go(page, f"https://studio.youtube.com/video/{vid}/analytics/tab-{tab}/period-default")
                page.screenshot(path=str(SHOTS / f"{vid}_{tab}.png"), full_page=True)
                print(f"===== {tab} (screenshot: work/studio_tool/{vid}_{tab}.png)")
                print(_text(page)[:2500])
        finally:
            ctx.close()


def channel(days: str = "28") -> None:
    from playwright.sync_api import sync_playwright
    SHOTS.mkdir(parents=True, exist_ok=True)
    period = PERIODS.get(days, "period-default")
    with sync_playwright() as p:
        ctx, page = _open(p)
        try:
            for tab in ("overview", "content", "audience"):
                _go(page, f"https://studio.youtube.com/channel/UC/analytics/tab-{tab}/{period}")
                page.screenshot(path=str(SHOTS / f"channel_{tab}.png"), full_page=True)
                print(f"===== channel {tab}, last {days} days (screenshot: work/studio_tool/channel_{tab}.png)")
                print(_text(page)[:3000])
        finally:
            ctx.close()


def reschedule(vid: str, date: str, at: str | None = None) -> None:
    from playwright.sync_api import sync_playwright
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx, page = _open(p)
        try:
            _go(page, f"https://studio.youtube.com/video/{vid}/edit")
            box = page.locator("ytcp-video-metadata-visibility").first
            if "Scheduled" not in box.inner_text():
                raise SystemExit(f"{vid} is not a scheduled video ({box.inner_text().strip()}); nothing changed")
            box.click()
            page.wait_for_timeout(2000)
            dlg = page.locator("ytcp-video-visibility-select").first
            dlg.locator("#datepicker-trigger, ytcp-text-dropdown-trigger").first.click()
            page.wait_for_timeout(1500)
            inp = page.locator("ytcp-date-picker tp-yt-paper-input input, ytcp-date-picker input").first
            inp.fill(date)
            inp.press("Enter")
            page.wait_for_timeout(1500)
            if at:
                t = dlg.locator("#time-of-day-container input, ytcp-form-input-container#time-of-day-container input")
                t.first.fill(at)
                t.first.press("Enter")
                page.wait_for_timeout(1000)
            page.get_by_role("button", name="Done").last.click()
            page.wait_for_timeout(2000)
            page.get_by_role("button", name="Save", exact=True).first.click()
            page.wait_for_timeout(7000)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(9000)
            page.locator("ytcp-video-metadata-visibility").first.click()
            page.wait_for_timeout(2000)
            now = page.locator("ytcp-video-visibility-select").first.inner_text()
            page.screenshot(path=str(SHOTS / f"{vid}_schedule.png"))
            ok = date in now and (not at or at.replace(" ", "").lower() in now.replace(" ", "").lower()
                                  or at.replace(" ", "") in now.replace(" ", ""))
            print(("OK: " if ok else "CHECK FAILED: ") + re.sub(r"\s+", " ", now)[:300])
            print(f"screenshot: work/studio_tool/{vid}_schedule.png")
        finally:
            ctx.close()


def main() -> None:
    a = sys.argv[1:]
    sys.stdout.reconfigure(encoding="utf-8")
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
    elif a[0] == "videos":
        videos(a[1] if len(a) > 1 else "long", int(a[2]) if len(a) > 2 else 1)
    elif a[0] == "video" and len(a) > 1:
        video(a[1])
    elif a[0] == "channel":
        channel(a[1] if len(a) > 1 else "28")
    elif a[0] == "reschedule" and len(a) > 2:
        reschedule(a[1], a[2], a[3] if len(a) > 3 else None)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
