"""Turn Watch Page ads ON for long videos (Studio > video > Earn), in the hidden Studio browser. No Claude needed.

    python -m tools.ads_on <id> --rate-none just this (new) video: the normal use, right after an upload
    python -m tools.ads_on --new            only long videos that appeared since the last run (first run = baseline, changes nothing)
    python -m tools.ads_on --all            every long video that shows "Ads off" (only if the owner asks)
    python -m tools.ads_on --check          only list what is off, change nothing
    --rate-none                             answer YouTube's content rating with 'None of the above' (owner's declaration:
                                            use only for videos where none of the listed items really apply)

Owner's rule (2026-10-27): ads must always be ON for every long video; this tool only ever switches Off -> On, never On -> Off,
and re-reads the page after saving to confirm. Videos that show neither On nor Off (not eligible, claims) are left alone.
"""
from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools import studio_tool as st  # noqa: E402


def videos_with_ads_off(pages: int = 2) -> list[str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        st.videos("long", pages)
    out = []
    for line in buf.getvalue().splitlines():
        parts = [x.strip() for x in line.split("|")]
        if parts and re.fullmatch(r"[\w-]{11}", parts[0]) and "Ads off" in line and parts[0] not in out:
            out.append(parts[0])
    return out


def _state(page) -> str:
    """'on', 'off' or 'other' for the Watch Page ads dropdown on the Earn page."""
    txt = page.locator("body").inner_text()
    m = re.search(r"Learn more about ad placement\s*\n\s*(\S[^\n]*)", txt)
    val = (m.group(1).strip() if m else "").lower()
    return val if val in ("on", "off") else "other"


def turn_on(page, vid: str, rate_none: bool = False) -> str:
    st._go(page, f"https://studio.youtube.com/video/{vid}/monetization/ads")
    before = _state(page)
    if before == "on":
        return "already on"
    if before != "off":
        return "left alone (ads setting is not a plain On/Off: not eligible or has claims)"
    page.get_by_text("Off", exact=True).first.click()
    page.wait_for_timeout(1200)
    page.get_by_text("On", exact=True).first.click()
    page.wait_for_timeout(800)
    page.get_by_role("button", name="Next").last.click()
    page.wait_for_timeout(2500)
    if "None of the above" in page.locator("body").inner_text():
        # YouTube's content self-rating: a declaration that cannot be changed after submitting
        if not rate_none:
            page.get_by_role("button", name="Cancel").last.click()
            return "NEEDS THE CONTENT RATING (an owner declaration, not submitted): run again with --rate-none only if none of the listed items apply"
        page.get_by_text("None of the above", exact=True).first.click()
        page.wait_for_timeout(600)
        page.get_by_role("button", name="Submit").last.click()
        page.wait_for_timeout(3000)
    page.get_by_role("button", name="Save", exact=True).first.click()
    page.wait_for_timeout(6000)
    st._go(page, f"https://studio.youtube.com/video/{vid}/monetization/ads", wait=6000)
    after = _state(page)
    return "TURNED ON (confirmed)" if after == "on" else f"FAILED: page still shows '{after}'"


SEEN = ROOT / "work" / "ads_seen.json"


def long_ids(pages: int = 2) -> list[str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        st.videos("long", pages)
    out = []
    for line in buf.getvalue().splitlines():
        first = line.split("|")[0].strip()
        if re.fullmatch(r"[\w-]{11}", first) and first not in out:
            out.append(first)
    return out


def new_videos_mode() -> int:
    """Once per NEW long video: the first run only notes the videos that exist (nothing is changed); later runs turn ads
    ON (+ rating 'None of the above', the owner's standing rule) for each video that was not there before."""
    import json
    now = long_ids()
    if not now:
        print("could not read the video list; nothing changed")
        return 1
    if not SEEN.exists():
        SEEN.write_text(json.dumps(now), encoding="utf-8")
        print(f"baseline saved: {len(now)} existing long videos are left alone")
        return 0
    seen = set(json.loads(SEEN.read_text(encoding="utf-8")))
    new = [v for v in now if v not in seen]
    if not new:
        print("no new long video")
        return 0
    rc = 0
    for vid in new:
        r = main([vid, "--rate-none"])
        if r == 0:
            seen.add(vid)
        rc = rc or r
    SEEN.write_text(json.dumps(sorted(seen)), encoding="utf-8")
    return rc


def main(argv: list[str]) -> int:
    from playwright.sync_api import sync_playwright
    if "--new" in argv:
        return new_videos_mode()
    check = "--check" in argv
    ids = [a for a in argv if not a.startswith("--")]
    if not ids and "--all" not in argv and not check:
        print("give the new video's id (older videos are left alone on purpose); --all scans every long video")
        return 2
    ids = ids or videos_with_ads_off()
    if not ids:
        print("every long video already has ads on")
        return 0
    if check:
        print("ads are OFF on:", ", ".join(ids))
        return 0
    bad = 0
    with sync_playwright() as p:
        ctx, page = st._open(p)
        try:
            for vid in ids:
                try:
                    res = turn_on(page, vid, "--rate-none" in argv)
                except Exception as e:  # noqa: BLE001
                    res = f"FAILED: {e}"
                bad += res.startswith(("FAILED", "NEEDS"))
                print(f"{vid}: {res}")
        finally:
            ctx.close()
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
