"""Freely licensed photos from Wikimedia Commons (for story episodes about the family: father, grandfather, Dubai
history...). Every file used is credited in the description with its author and licence, as the licences require."""
from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger("lane.commons")

API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "TheLuxuryLane/1.0 (https://github.com/jashanytube4-collab/luxury-lane-autopilot) "
                         "python-requests"}
WIDTH = 1280          # one of the standard thumbnail sizes Wikimedia asks bots to use
OK_LICENCES = ("cc0", "public domain", "pd", "cc by", "cc-by", "attribution", "gfdl", "ogl", "godl")


@dataclass
class Picture:
    path: Path
    title: str
    credit: str       # "Author / Licence" for the description


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def search(query: str, limit: int = 12, min_width: int = 700) -> list[dict]:
    """[{"title", "url", "credit"}] for photos free to reuse. `query` is either "cat:<Commons category>" (every photo
    filed under exactly that person/place — preferred, it is precise) or plain search words."""
    if query.startswith("cat:"):
        gen = {"generator": "categorymembers", "gcmtitle": "Category:" + query[4:].strip(), "gcmtype": "file",
               "gcmlimit": 50}
    else:
        gen = {"generator": "search", "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
               "gsrlimit": min(50, limit * 3)}
    try:
        r = requests.get(API, params={"action": "query", "format": "json", "prop": "imageinfo",
                                      "iiprop": "url|size|extmetadata|mime", "iiurlwidth": WIDTH, **gen},
                         headers=HEADERS, timeout=40)
        r.raise_for_status()
        pages = sorted(r.json().get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    except (requests.RequestException, ValueError) as e:
        log.warning("Commons search %r failed: %s", query, e)
        return []
    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png") or info.get("width", 0) < min_width:
            continue
        meta = info.get("extmetadata", {})
        lic = _plain(meta.get("LicenseShortName", {}).get("value", ""))
        if not any(k in lic.lower() for k in OK_LICENCES):
            continue
        artist = _plain(meta.get("Artist", {}).get("value", "")) or "Unknown author"
        out.append({"title": p["title"], "url": info.get("thumburl") or info["url"],
                    "credit": f"\"{p['title'][5:]}\" by {artist[:80]}, {lic} (Wikimedia Commons)"})
        if len(out) >= limit:
            break
    return out


def fetch(query: str, dest: Path, limit: int = 8) -> list[Picture]:
    """Download up to `limit` photos for `query` into `dest` (cached by URL)."""
    dest.mkdir(parents=True, exist_ok=True)
    pics = []
    for hit in search(query, limit):
        path = dest / (hashlib.md5(hit["url"].encode()).hexdigest()[:14] + ".jpg")
        if not path.exists() and not _download(hit["url"], path):
            continue
        pics.append(Picture(path, hit["title"], hit["credit"]))
    return pics


def _download(url: str, path: Path) -> bool:
    """Polite download: a pause between files and a back-off when Wikimedia asks us to slow down."""
    for wait in (2, 8, 30):
        time.sleep(1.2)
        try:
            r = requests.get(url, headers=HEADERS, timeout=60)
            if r.status_code == 429:
                time.sleep(wait)
                continue
            r.raise_for_status()
            path.write_bytes(r.content)
            return True
        except requests.RequestException as e:
            log.warning("Commons download failed: %s", e)
            return False
    log.warning("Commons kept rate-limiting; skipped %s", url[:80])
    return False
