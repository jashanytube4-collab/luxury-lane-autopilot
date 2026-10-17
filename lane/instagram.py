"""Instagram through Meta's official Graph API (no scraping, no logging in as anyone).

* Business Discovery: our professional account (@thelaneluxury) reads the public posts of other professional
  accounts — here Prince Hamdan's own @faz3. New videos are copied at once into the private footage library
  (Instagram's links expire after a few days) and become material for moment Shorts.
* Content publishing (optional): our Shorts can also be posted as Reels on @thelaneluxury.

Setup once with tools/connect_instagram.py; GitHub then needs the IG_TOKEN and IG_USER_ID secrets. The token is a
Facebook Page token made from a long-lived user token, which does not expire.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

import requests

log = logging.getLogger("lane.instagram")

GRAPH = "https://graph.facebook.com/v21.0"


class InstagramError(Exception):
    pass


class Instagram:
    def __init__(self, token: str, user_id: str) -> None:
        self.token, self.user_id = token, user_id

    def _get(self, path: str, **params) -> dict:
        r = requests.get(f"{GRAPH}/{path}", params={"access_token": self.token, **params}, timeout=60)
        d = r.json() if r.headers.get("content-type", "").startswith(("application/json", "text/javascript")) else {}
        if r.status_code != 200 or "error" in d:
            raise InstagramError(str(d.get("error", {}).get("message", r.text[:200])))
        return d

    def discover(self, username: str, pages: int = 2, per_page: int = 50) -> list[dict]:
        """Newest public posts of another professional account: [{id, media_type, media_url, permalink, ...}]."""
        out, after = [], ""
        for _ in range(pages):
            cursor = f".after({after})" if after else ""
            fields = (f"business_discovery.username({username}){{media.limit({per_page}){cursor}"
                      "{id,caption,media_type,media_product_type,media_url,permalink,timestamp,like_count,"
                      "comments_count}}")
            d = self._get(self.user_id, fields=fields)
            media = d.get("business_discovery", {}).get("media", {})
            out += media.get("data", [])
            after = media.get("paging", {}).get("cursors", {}).get("after", "")
            if not after:
                break
        return out

    def publish_reel(self, video_url: str, caption: str) -> str:
        """Post a Reel from a public video URL; returns the Instagram media id."""
        r = requests.post(f"{GRAPH}/{self.user_id}/media", timeout=60, data={
            "access_token": self.token, "media_type": "REELS", "video_url": video_url, "caption": caption[:2150],
            "share_to_feed": "true"}).json()
        if "id" not in r:
            raise InstagramError(str(r.get("error", r))[:300])
        cid = r["id"]
        for _ in range(60):                       # Instagram processes the video first (usually under 2 minutes)
            time.sleep(10)
            st = self._get(cid, fields="status_code").get("status_code")
            if st == "FINISHED":
                break
            if st in ("ERROR", "EXPIRED"):
                raise InstagramError(f"Instagram could not process the video ({st})")
        r = requests.post(f"{GRAPH}/{self.user_id}/media_publish", timeout=60,
                          data={"access_token": self.token, "creation_id": cid}).json()
        if "id" not in r:
            raise InstagramError(str(r.get("error", r))[:300])
        return r["id"]


def _title(caption: str) -> str:
    first = next((ln.strip() for ln in (caption or "").splitlines() if ln.strip()), "")
    first = re.sub(r"#\w+|@\w+", "", first).strip()
    return (first or "Moment from Fazza's Instagram")[:140]


SKIPPED: set = set()        # videos checked this run that do not show him (not re-downloaded within a run)
HAMDAN = re.compile(r"hamdan|fazza|حمدان|فزاع|crown prince of dubai|ولي عهد دبي", re.I)


def sync(events: dict, ig: Instagram, library, usernames: list, work: Path, limit: int = 40,
         check=None) -> int:
    """Copy new videos of `usernames` into the footage library and add them as events. Returns how many were added.
    An account given as {"name": ..., "only_hamdan": true} keeps only posts whose caption names him.
    `check(path, caption)` -> dict | None looks at each downloaded video: None drops it (he is not in it),
    a dict ({"about", "tags"}) is stored with the event so story sentences can be matched to it."""
    added = 0
    skipped = SKIPPED
    for acc in usernames:
        username = acc["name"] if isinstance(acc, dict) else acc
        only_hamdan = bool(acc.get("only_hamdan")) if isinstance(acc, dict) else False
        pages = int(acc.get("pages", 4)) if isinstance(acc, dict) else 4
        try:
            media = ig.discover(username, pages=pages)
        except InstagramError as e:
            log.warning("Instagram @%s unavailable: %s", username, e)
            continue
        for m in media:
            if added >= limit:
                return added
            key = f"ig:{username}:{m['id']}"
            if key in events or key in skipped or m.get("media_type") != "VIDEO" or not m.get("media_url"):
                continue
            if only_hamdan and not HAMDAN.search(m.get("caption") or ""):
                continue
            name = f"ig_{m['id']}"
            path = work / f"{name}.mp4"
            try:
                with requests.get(m["media_url"], stream=True, timeout=120) as r:
                    r.raise_for_status()
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                seen = check(path, m.get("caption") or "", only_hamdan) if check else {}
                if seen is None:                     # not a real moment of him: remembered, never fetched again
                    skipped.add(key)
                    events[key] = {"title": "", "date": (m.get("timestamp") or "")[:10], "photos": [],
                                   "source": "instagram", "skip": True}
                    continue
                if not library.upload(f"{name}.mp4", path):
                    continue
            except (requests.RequestException, OSError) as e:
                log.warning("Instagram video %s skipped: %s", m["id"], e)
                continue
            finally:
                path.unlink(missing_ok=True)
            events[key] = {"title": _title(m.get("caption", "")), "date": (m.get("timestamp") or "")[:10],
                           "video": {"kind": "ig", "ref": name}, "photos": [], "news_id": None,
                           "source": "instagram", "permalink": m.get("permalink", ""),
                           "caption": (m.get("caption") or "")[:1500], "likes": m.get("like_count", 0),
                           "about": seen.get("about", ""), "tags": seen.get("tags", [])}
            added += 1
    return added
