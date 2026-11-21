"""YouTube END SCREEN for Luxury Lane long videos, in the hidden Studio browser (owner's order 2026-11-01).

    python -m tools.end_screen <id> [<id> ...]   add the end screen to these videos (only if they have none)
    python -m tools.end_screen --all             every long video in the Studio list that has no end screen yet
    python -m tools.end_screen --new             long videos on the first Studio page not yet done (used right after every upload)
    python -m tools.end_screen --check <id>      only say whether a video already has an end screen

The end screen = template "2 videos" (one 'Best for viewer', one 'Most recent upload') shown in the LAST 10 SECONDS only (owner 2026-11-22, every channel; older end screens keep their length);
the story keeps playing behind it. Only the end screen is touched; every result is verified by reopening the editor.
Never deletes anything and never changes an existing end screen.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools import studio_tool as st  # noqa: E402

SECONDS = 10                     # length of the end screen (owner 2026-11-22: last 10 seconds, all channels; YouTube minimum is 5)
SEEN = ROOT / "work" / "end_screen_seen.json"
TIME_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})$")   # Studio editor shows m:ss:ff


def _start_for(end: str, seconds: int = SECONDS) -> str:
    m, s, f = (int(x) for x in TIME_RE.match(end.strip()).groups())
    total = m * 60 + s - seconds
    if total < 0:
        total = 0
    return f"{total // 60}:{total % 60:02d}:{f:02d}"


def _open_editor(page, vid: str) -> None:
    st._go(page, f"https://studio.youtube.com/video/{vid}/editor", 9000)
    page.get_by_text("End screen", exact=True).first.wait_for(timeout=60000)


def has_end_screen(page, vid: str) -> bool:
    """True when the editor already holds end screen elements (the 'End screen' row then offers Edit, not a +)."""
    _open_editor(page, vid)
    page.get_by_text("End screen", exact=True).first.click()
    page.wait_for_timeout(2000)
    body = page.locator("body").inner_text()
    return "Apply template" not in body


def _times(page) -> list[str]:
    return [(v or "").strip() for v in page.evaluate(
        "() => [...document.querySelectorAll('ytve-formatted-input input')].map(e => e.value)")]


def _set_start(page, new_start: str) -> None:
    box = page.locator("ytve-formatted-input input").first      # start box of the selected element
    box.click()
    box.fill(new_start)
    box.press("Enter")
    box.press("Tab")


def add(page, vid: str) -> str:
    """Returns 'added', 'already has end screen' or raises."""
    _open_editor(page, vid)
    page.get_by_text("End screen", exact=True).first.click()
    page.wait_for_timeout(1500)
    if page.get_by_text("Apply template", exact=True).count() == 0:
        return "already has end screen"
    page.get_by_text("Apply template", exact=True).first.click()
    page.wait_for_timeout(3000)
    page.locator('[role=gridcell][aria-label="2 videos"]').first.click()
    page.wait_for_timeout(3500)
    # the template puts both elements in the last 20 s: shorten each to the last SECONDS seconds
    for label in ("Video: Best for viewer", "Video: Most recent upload"):
        page.locator(f'ytve-endscreen-marker[aria-label="{label}"]').first.click()
        page.wait_for_timeout(1200)
        times = [t for t in _times(page) if TIME_RE.match(t)]
        if len(times) < 2:
            raise RuntimeError(f"could not read start/end times for {label}: {times}")
        _set_start(page, _start_for(times[1]))
        page.wait_for_timeout(1000)
        after = [t for t in _times(page) if TIME_RE.match(t)]
        if after[:1] != [_start_for(times[1])]:
            raise RuntimeError(f"start time not accepted for {label}: {after}")
    page.screenshot(path=str(st.SHOTS / f"es_{vid}_before_save.png"))
    page.get_by_role("button", name="Save").last.click()
    page.wait_for_timeout(6000)
    return "added"


def verify(page, vid: str) -> bool:
    """Reopen the editor and look: the End screen option must no longer offer a template, and both elements exist."""
    _open_editor(page, vid)
    page.get_by_text("End screen", exact=True).first.click()
    page.wait_for_timeout(2500)
    body = page.locator("body").inner_text()
    page.screenshot(path=str(st.SHOTS / f"es_{vid}_after_save.png"))
    return "Apply template" not in body and page.locator("ytve-endscreen-marker").count() >= 2


def long_ids(pages: int = 3) -> list[str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        st.videos("long", pages)
    out: list[str] = []
    for line in buf.getvalue().splitlines():
        parts = [x.strip() for x in line.split("|")]
        if parts and re.fullmatch(r"[\w-]{11}", parts[0]) and parts[0] not in out:
            out.append(parts[0])
    return out


def _done() -> set[str]:
    return set(json.loads(SEEN.read_text())) if SEEN.exists() else set()


def _mark_done(vid: str) -> None:
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(_done() | {vid})))


def run(ids: list[str]) -> dict[str, str]:
    from playwright.sync_api import sync_playwright
    result: dict[str, str] = {}
    with sync_playwright() as p:
        ctx, page = st._open(p)
        page.set_default_timeout(60000)
        try:
            for vid in ids:
                try:
                    res = add(page, vid)
                    if res == "added":
                        res = "added, verified" if verify(page, vid) else "SAVED BUT NOT VERIFIED"
                    result[vid] = res
                except Exception as e:  # noqa: BLE001 - one broken video must not stop the rest
                    result[vid] = "FAILED: " + str(e).splitlines()[0][:150]
                print(vid, "->", result[vid], flush=True)
                if result[vid].startswith(("added, verified", "already")):
                    _mark_done(vid)
        finally:
            ctx.close()
    return result


def main() -> None:
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    if a[0] == "--check":
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            ctx, page = st._open(p)
            page.set_default_timeout(60000)
            try:
                for vid in a[1:]:
                    print(vid, "has end screen" if has_end_screen(page, vid) else "no end screen")
            finally:
                ctx.close()
        return
    if a[0] == "--new":                     # long videos on the first Studio page that are not yet done
        new = [v for v in long_ids(1) if v not in _done()]
        print("long videos without a finished end screen:", new or "none")
        if new:
            run(new)
        return
    ids = long_ids(3) if a[0] == "--all" else a
    run(ids)


if __name__ == "__main__":
    main()
