"""Scan the public posts of professional Instagram accounts through Meta's official Business Discovery API.

    python -m tools.ig_scan hhshkmohd faz3 dubaimediaoffice [--pages 20] [--out work/igscan]

Writes <out>/<user>.json (every post: id, caption, type, permalink, date) and downloads the media of posts whose
caption matches the family keywords into <out>/<user>/ (links expire fast, so they are copied at once).
Needs IG_TOKEN and IG_USER_ID in the environment (GitHub secrets). Nothing is posted anywhere."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lane.instagram import Instagram  # noqa: E402

KEYS = re.compile(r"mother|mama|mum|\bmom\b|\bhind\b|wife|shaikha|sheikha|daughter|grand|family|childhood|"
                  r"أم|والدة|ماما|هند|زوج|شيخة|حرم|بنت|عائلة|طفولة|جدة", re.I)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("users", nargs="+")
    ap.add_argument("--pages", type=int, default=20)
    ap.add_argument("--out", default="work/igscan")
    a = ap.parse_args()
    ig = Instagram(os.environ["IG_TOKEN"], os.environ["IG_USER_ID"])
    out = Path(a.out)
    for user in a.users:
        try:
            posts = ig.discover(user, pages=a.pages, per_page=50)
        except Exception as e:  # noqa: BLE001
            print(f"{user}: FAILED {str(e)[:200]}")
            continue
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{user}.json").write_text(json.dumps(posts, ensure_ascii=False, indent=1), encoding="utf-8")
        hits = [p for p in posts if KEYS.search(p.get("caption") or "")]
        print(f"{user}: {len(posts)} posts, {len(hits)} match the family keywords")
        d = out / user
        d.mkdir(exist_ok=True)
        for p in hits[:80]:
            url = p.get("media_url")
            if not url:
                continue
            ext = ".mp4" if p.get("media_type") == "VIDEO" else ".jpg"
            try:
                r = requests.get(url, timeout=120)
                if r.ok:
                    (d / f"{p['id']}{ext}").write_bytes(r.content)
            except requests.RequestException:
                pass


if __name__ == "__main__":
    main()
