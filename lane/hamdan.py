"""Official source: hamdan.ae (website of H.H. Sheikh Hamdan bin Mohammed) — media gallery + news articles.

An "event" is one official story: its title and date, its video (a YouTube upload by the official channel, or an
mp4 hosted on hamdan.ae), its photos (full-resolution, asset.hamdan.ae) and its article text (Arabic, official).
No account or key is needed; the same public API the website itself uses is called politely and sparingly.
"""
from __future__ import annotations

import html
import json
import logging
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

import requests

from .config import STATE_DIR

log = logging.getLogger("lane.hamdan")

BASE = "https://hamdan.ae"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
EVENTS_FILE = STATE_DIR / "events.json"
ARTICLES_DIR = STATE_DIR / "articles"


class SourceError(Exception):
    pass


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _parse_date(s: str) -> str:
    for fmt in ("%d %B, %Y", "%d %B %Y", "%d %b, %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except (ValueError, AttributeError):
            continue
    return ""


def _clean_html(v) -> str:
    if not isinstance(v, str):
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", v))).strip()


def _youtube_id(url: str) -> str | None:
    m = re.search(r"(?:embed/|watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


class Library:
    """Private footage library: a GitHub release holding copies of the official YouTube videos (<id>.mp4).
    GitHub's servers are blocked by YouTube, but can always read from GitHub."""

    def __init__(self, repo: str, token: str) -> None:
        self.repo, self.token = repo, token
        self._assets: dict[str, int] | None = None

    def _api(self, url: str, **kw) -> requests.Response:
        h = {"Authorization": f"Bearer {self.token}", "X-GitHub-Api-Version": "2022-11-28", **kw.pop("headers", {})}
        return requests.get(url, headers=h, timeout=60, **kw)

    def assets(self) -> dict[str, int]:
        if self._assets is None:
            self._assets = {}
            rel = self._api(f"https://api.github.com/repos/{self.repo}/releases/tags/footage")
            if rel.status_code == 200:
                rid = rel.json()["id"]
                for page in range(1, 30):
                    r = self._api(f"https://api.github.com/repos/{self.repo}/releases/{rid}/assets",
                                  params={"per_page": 100, "page": page})
                    batch = r.json() if r.status_code == 200 else []
                    self._assets.update({a["name"]: a["id"] for a in batch})
                    if len(batch) < 100:
                        break
            log.info("footage library: %d videos", len(self._assets))
        return self._assets

    def fetch(self, name: str, dest: Path) -> bool:
        aid = self.assets().get(name)
        if not aid:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._api(f"https://api.github.com/repos/{self.repo}/releases/assets/{aid}",
                       headers={"Accept": "application/octet-stream"}, stream=True) as r:
            if r.status_code != 200:
                return False
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        return dest.stat().st_size > 200_000


class Hamdan:
    def __init__(self, library: Library | None = None) -> None:
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self._warm = False
        self.library = library

    def _post(self, path: str, body: dict, referer: str = "/en/media-gallery"):
        if not self._warm:
            self.s.get(BASE + referer, timeout=30)
            self._warm = True
        hdr = {"Content-Type": "application/json", "Referer": BASE + referer, "Origin": BASE}
        last = None
        for attempt in range(3):
            try:
                r = self.s.post(BASE + path, data=json.dumps(body), headers=hdr, timeout=60)
                r.raise_for_status()
                return r.json()
            except (requests.RequestException, ValueError) as e:
                last = e
                time.sleep(5 * (attempt + 1))
        raise SourceError(f"hamdan.ae {path} failed: {last}")

    # ---- catalogue ---------------------------------------------------------------------------------------
    def _gallery_page(self, kind: str, page: int, size: int = 50) -> list[dict]:
        body = {"pageCount": str(size), "pageIndex": str(page), "lang": "en", "mediaType": kind, "title": "",
                "listname": "MediaGallery"}
        return self._post("/api/MediaGallery/AllMediaItems", body).get("CommonReturnObject") or []

    def sync(self, events: dict[str, dict], full: bool = False) -> int:
        """Merge the gallery into `events` (keyed by normalised title). Returns how many events are new.
        Walks pages newest-first and stops after a page with nothing new, unless `full`."""
        added = 0
        for kind in ("Video", "Image"):
            for page in range(1, 80):
                items = self._gallery_page(kind, page)
                if not items:
                    break
                fresh = 0
                for it in items:
                    key = _norm_title(it.get("Title"))
                    if not key:
                        continue
                    ev = events.get(key)
                    if ev is None:
                        ev = events[key] = {"title": it.get("Title", "").strip(), "date": _parse_date(it.get("Date", "")),
                                            "video": None, "photos": [], "news_id": None}
                        added += 1
                    before = json.dumps(ev, sort_keys=True)
                    for vm in it.get("viewMedia") or []:
                        url = (vm.get("Url") or "").strip()
                        if not url:
                            continue
                        if kind == "Video":
                            yid = _youtube_id(url)
                            if yid:
                                ev["video"] = {"kind": "youtube", "ref": yid}
                            elif url.lower().endswith(".mp4"):
                                ev["video"] = {"kind": "mp4", "ref": url.replace("//CPDAssets", "/CPDAssets")}
                        else:
                            url = url.replace("//CPDAssets", "/CPDAssets")
                            if url not in ev["photos"]:
                                ev["photos"].append(url)
                            m = re.search(r"/News/\d{4}/(\d+)/", url)
                            if m and not ev.get("news_id"):
                                ev["news_id"] = m.group(1)
                    if json.dumps(ev, sort_keys=True) != before:
                        fresh += 1
                if not fresh and not full:
                    break
                time.sleep(0.7)
        return added

    # ---- article ----------------------------------------------------------------------------------------
    def article(self, news_id: str) -> dict:
        """Official article (Arabic) — cached in state/articles so each one is fetched once."""
        cache = ARTICLES_DIR / f"{news_id}.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        d = self._post("/api/Master/GetItemDetails", {"listName": "News", "title": str(news_id)},
                       referer=f"/en/latest-news/{news_id}")
        art = {"id": str(news_id), "title_ar": _clean_html(d.get("Title")),
               "brief": _clean_html(d.get("briefDescription")), "text": _clean_html(d.get("textContent"))[:6000],
               "photos": [m.get("Url") for m in d.get("viewMedia") or [] if m.get("Url")]}
        ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(art, ensure_ascii=False, indent=1), encoding="utf-8")
        return art

    # ---- downloads --------------------------------------------------------------------------------------
    def download_photo(self, url: str, dest: Path) -> Path:
        if dest.exists() and dest.stat().st_size > 20_000:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = self.s.get(url, timeout=90)
        r.raise_for_status()
        if len(r.content) < 20_000:
            raise SourceError(f"photo too small: {url}")
        dest.write_bytes(r.content)
        return dest

    def download_video(self, video: dict, dest: Path, cookies: Path | None = None) -> Path:
        """Best quality up to 1080p, picture only (the original sound is never used)."""
        if dest.exists() and dest.stat().st_size > 200_000:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        if video["kind"] == "mp4":
            with self.s.get(video["ref"], stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            return dest
        if self.library and self.library.fetch(f"{video['ref']}.mp4", dest):
            return dest
        cmd = ["yt-dlp", "--no-warnings", "--no-playlist", "-q", "--js-runtimes", "node", "--retries", "3",
               "-f", "bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080]/b[height<=1080]/b",
               "--merge-output-format", "mp4", "--remux-video", "mp4", "-o", str(dest),
               f"https://www.youtube.com/watch?v={video['ref']}"]
        if cookies and cookies.exists():
            cmd[1:1] = ["--cookies", str(cookies)]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if p.returncode != 0 or not dest.exists():
            msg = (p.stderr or p.stdout or "").strip().splitlines()
            raise SourceError(f"video download failed: {msg[-1] if msg else p.returncode}")
        return dest


def load_events() -> dict[str, dict]:
    return json.loads(EVENTS_FILE.read_text(encoding="utf-8")) if EVENTS_FILE.exists() else {}


def save_events(events: dict[str, dict]) -> None:
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = EVENTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(events, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(EVENTS_FILE)
