"""Schedules the ready-made YouTube Posts from the Desktop pack ("Fazza Posts (YouTube)") in YouTube's own scheduler.

    python -m tools.post_helper            # the next 7 days of posts
    python -m tools.post_helper --days 14
    python -m tools.post_helper --test     # just the next single post (to check it in Studio first)

It opens its own Chrome window (signed in once by the owner, profile in work/studio_profile), and for each post does
what the owner would do by hand on the channel's Posts tab: attach the picture, paste the caption, choose
Post ▼ → Schedule post, set the date and time, press Schedule. Posts that were scheduled are remembered in
work/posts_scheduled.json and never scheduled twice. YouTube publishes them itself at those times, laptop on or off.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PACK = Path(r"D:\Jashan\Desktop") / "Fazza Posts (YouTube)"   # the owner's Desktop (on drive D)
PROFILE = ROOT / "work" / "studio_profile"
DONE_FILE = ROOT / "work" / "posts_scheduled.json"
CHROME = next((p for p in (Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                           Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")) if p.exists()), None)
POSTS_URL = "https://www.youtube.com/channel/UCkpQ81nmE2JAjW3qcpn-zkQ/posts"
INDIA_MINUS_DUBAI = timedelta(minutes=90)        # the pack lists Dubai times; YouTube shows this laptop's time (India)


def pack_posts() -> list[tuple[datetime, Path]]:
    """(local India time, picture) of every post in the pack, in order."""
    out = []
    for img in PACK.glob("*/*.jpg"):
        m = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})_\d\.jpg$", img.name)
        if m:
            dubai = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:{m.group(3)}")
            out.append((dubai + INDIA_MINUS_DUBAI, img))
    return sorted(out)


def load_done() -> set[str]:
    try:
        return set(json.loads(DONE_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def save_done(done: set[str]) -> None:
    DONE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DONE_FILE.write_text(json.dumps(sorted(done), indent=0), encoding="utf-8")


def _time_label(t: datetime) -> re.Pattern:
    h = t.hour % 12 or 12
    return re.compile(rf"(?<![0-9]){h}:{t.minute:02d}\s*{'AM' if t.hour < 12 else 'PM'}")


def schedule_one(page, when: datetime, img: Path, caption: str, dry: bool = False) -> None:
    page.goto(POSTS_URL, wait_until="domcontentloaded")
    box = page.locator("ytd-backstage-post-dialog-renderer").first
    box.locator("#image-button button").first.wait_for(timeout=30000)
    box.locator("#image-button button").first.click()
    page.locator("input[type=file]").last.set_input_files(str(img))
    box.get_by_role("button", name="Edit preview").first.wait_for(timeout=60000)   # the picture has uploaded

    editor = box.locator("#contenteditable-root:visible").first
    editor.click()
    for i, line in enumerate(caption.strip().split("\n")):
        if i:
            page.keyboard.press("Shift+Enter")
        if line:
            page.keyboard.insert_text(line)

    box.get_by_role("button", name="Action menu").first.click()
    page.locator("ytd-menu-service-item-renderer:visible", has_text="Schedule post").first.click()

    page.locator("#date-picker:visible").first.click()
    field = page.locator("input#textbox:visible").first
    field.fill(when.strftime("%b %d, %Y").replace(" 0", " "))
    field.press("Enter")
    page.locator("#time-picker:visible").first.click()
    page.locator("tp-yt-paper-item:visible").filter(has_text=_time_label(when)).first.click()

    shown_date = page.locator("#date-picker:visible").first.inner_text().strip()
    shown_time = page.locator("#time-picker:visible").first.inner_text().strip().replace("\u202f", " ")
    want_date = when.strftime("%b %d, %Y").replace(" 0", " ")
    if shown_date != want_date or not _time_label(when).match(shown_time):
        raise RuntimeError(f"date/time did not stick: shows {shown_date} {shown_time}, wanted {want_date} {when:%H:%M}")

    typed = editor.inner_text().strip()
    first_line = caption.strip().splitlines()[0][:20]
    if first_line not in typed:
        raise RuntimeError(f"caption did not go in (editor shows: {typed[:60]!r})")
    if dry:                                       # rehearsal: everything filled in, nothing scheduled
        print("caption in editor:", " / ".join(typed.splitlines())[:120])
        page.screenshot(path=str(ROOT / "work" / "post_helper_dry.png"))
        return
    box.locator("#submit-button button").first.click()
    box.get_by_role("button", name="Edit preview").first.wait_for(state="detached", timeout=60000)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--test", action="store_true", help="schedule only the next single post")
    ap.add_argument("--dry", action="store_true", help="fill in the next post but do not press Schedule")
    args = ap.parse_args()
    if CHROME is None:
        sys.exit("Google Chrome is not installed")
    done = load_done()
    now = datetime.now()
    todo = [(t, img) for t, img in pack_posts()
            if img.name not in done and now + timedelta(minutes=30) < t < now + timedelta(days=args.days)]
    if args.test or args.dry:
        todo = todo[:1]
    print(f"{len(todo)} posts to schedule")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE), executable_path=str(CHROME), headless=False,
                                                   viewport={"width": 1400, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(30000)
        for k, (when, img) in enumerate(todo, 1):
            caption = img.with_suffix(".txt").read_text(encoding="utf-8")
            try:
                schedule_one(page, when, img, caption, dry=args.dry)
                if args.dry:
                    print(f"rehearsal filled in for {when:%a %d %b %H:%M} (India time) — not scheduled")
                    break
            except (PWTimeout, RuntimeError) as e:
                print(f"[{k}/{len(todo)}] {when:%a %d %b %H:%M} — FAILED, stopping: {str(e)[:200]}")
                page.screenshot(path=str(ROOT / "work" / "post_helper_error.png"))
                break
            done.add(img.name)
            save_done(done)
            print(f"[{k}/{len(todo)}] scheduled for {when:%a %d %b %H:%M} (India time) — {img.name}")
            time.sleep(3)
        ctx.close()


if __name__ == "__main__":
    main()
