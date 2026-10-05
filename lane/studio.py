"""Turns official events into finished videos: gathers footage/photos, asks the AI for the words, renders.

Usage bookkeeping (state/catalog.json, keyed by event):
    long        date of the episode that used it (each event is in at most one episode)
    shot_ranges footage ranges [a, b] already used by Shorts (a Short never reuses footage another Short used)
    photo_short True once a photo-only Short was made from it
    rejected    AI verdict when the material was too weak for a Short
"""
from __future__ import annotations

import hashlib
import logging
import random
from datetime import datetime, timezone
from pathlib import Path

from .brain import Brain, BrainError, image_b64
from .hamdan import Hamdan, SourceError
from .longform import THEME_MOOD, EpisodeResult, Visuals, _scene_list, produce_episode
from .media import probe, scene_cuts
from .short import produce

log = logging.getLogger("lane.studio")

MIN_FRESH_SECONDS = 20.0     # a video needs this much unused footage for another Short
MAX_SHORTS_PER_EVENT = 3     # one story never gets told more than three times, each with new footage


def _slug(key: str) -> str:
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def _overlaps(a: float, b: float, ranges: list) -> bool:
    return any(a < y and x < b for x, y in ranges)


class Studio:
    def __init__(self, cfg: dict, brain: Brain, hamdan: Hamdan, work: Path, cookies: Path | None = None) -> None:
        self.cfg, self.brain, self.hamdan, self.work, self.cookies = cfg, brain, hamdan, work, cookies
        self.video_failures = 0
        self.video_successes = 0

    # ---- material --------------------------------------------------------------------------------------
    def video(self, key: str, ev: dict) -> Path | None:
        if not ev.get("video"):
            return None
        dest = self.work / "cache" / f"{_slug(key)}.mp4"
        # circuit breaker: if YouTube is blocking this machine, stop trying for the rest of the run
        blocked = self.video_failures >= 3 and not self.video_successes
        if ev["video"]["kind"] == "youtube" and blocked and not dest.exists():
            return None
        try:
            path = self.hamdan.download_video(ev["video"], dest, self.cookies)
            self.video_successes += 1
            return path
        except (SourceError, OSError) as e:
            self.video_failures += 1
            log.warning("video for %r unavailable: %s", ev["title"][:60], e)
            return None

    def photos(self, key: str, ev: dict, limit: int = 8) -> list[Path]:
        out = []
        for i, url in enumerate(ev.get("photos", [])[:limit]):
            dest = self.work / "cache" / f"{_slug(key)}_{i}.jpg"
            try:
                out.append(self.hamdan.download_photo(url, dest))
            except (SourceError, OSError, Exception) as e:  # noqa: BLE001 — one missing photo is fine
                log.warning("photo skipped: %s", e)
        return out

    def article_text(self, ev: dict) -> tuple[str, str]:
        if not ev.get("news_id"):
            return "", ""
        try:
            art = self.hamdan.article(ev["news_id"])
            return art.get("brief", ""), art.get("text", "")
        except SourceError as e:
            log.warning("article %s unavailable: %s", ev["news_id"], e)
            return "", ""

    # ---- Shorts -----------------------------------------------------------------------------------------
    def _short_target(self, seed: int) -> float:
        s = self.cfg["short"]
        return random.Random(seed).uniform(s["min_seconds"] + 2, s["max_seconds"] - 3)

    def short_candidates(self, events: dict, usage: dict, day: str | None = None, cooldown_days: int = 7
                         ) -> list[str]:
        """Event keys that can still give a Short: newest first, footage before photo stories. An event used for a
        Short rests `cooldown_days` before it can give another, so a day never shows the same story twice."""
        now = datetime.now(timezone.utc).date().isoformat()
        day = day or now
        rest_until = _days_ago(day, cooldown_days - 1)
        vids, pics = [], []
        for k, ev in events.items():
            u = usage.get(k, {})
            if u.get("rejected") or u.get("fails", 0) >= 2:
                continue
            if u.get("last_short") and u["last_short"] >= rest_until:
                continue
            if u.get("shorts_made", 0) >= MAX_SHORTS_PER_EVENT:
                continue
            if ev.get("video") and not u.get("video_exhausted"):
                vids.append(k)
            elif len(ev.get("photos", [])) >= 4 and not u.get("photo_short"):
                pics.append(k)
        key = lambda k: (events[k].get("date") or "0000", k)  # noqa: E731
        vids.sort(key=key, reverse=True)
        pics.sort(key=key, reverse=True)
        # fresh events (last 14 days) jump the queue whatever their type
        fresh = [k for k in vids + pics if (events[k].get("date") or "") >= _days_ago(now, 14)]
        rest = [k for k in vids + pics if k not in fresh]
        return fresh + rest

    def make_short(self, key: str, ev: dict, usage: dict, recent: list[str], seed: int) -> dict | None:
        """Returns {"path", "plan", "unit", "ranges", "music"} or None if this event gave nothing usable."""
        u = usage.setdefault(key, {})
        work = self.work / f"short_{_slug(key)}_{seed % 10000}"
        video = self.video(key, ev)
        photos = self.photos(key, ev, limit=4)
        items, cuts = [], []
        if video:
            info = probe(video)
            cuts = scene_cuts(video, info["fps"])
            used = u.get("shot_ranges", [])
            scenes = [s for s in _scene_list(video) if not _overlaps(s[0], s[1], used)]
            if sum(b - a for a, b in scenes) < MIN_FRESH_SECONDS:
                u["video_exhausted"] = True
                video, scenes = None, []
            for a, b in scenes[:9]:
                items.append({"kind": "video", "start": a, "end": b, "b64": image_b64(video, (a + b) / 2)})
            portrait = info["height"] > info["width"]
        else:
            if u.get("photo_short") or len(photos) < 4:
                u["video_exhausted"] = True
                return None
            portrait = False
        for p in photos[: (3 if items else 6)]:
            items.append({"kind": "photo", "path": str(p), "b64": image_b64(p)})
        brief, text = self.article_text(ev)
        plan = self.brain.plan_short(items, title=ev["title"], date=ev.get("date", ""), brief=brief, article=text,
                                     target=self._short_target(seed), recent=recent,
                                     height_gt_width=portrait)
        if not plan["suitable"]:
            if not items or items[0]["kind"] == "photo" or u.get("video_exhausted"):
                u["rejected"] = {"score": plan["score"], "reason": plan.get("reject_reason", "")}
            else:
                u["video_exhausted"] = True     # this footage is weak; photo Shorts may still work later
            return None
        out = work / "short.mp4"
        res = produce(video if any(s["kind"] == "video" for s in plan["shots"]) else None, plan, self.cfg, work, out,
                      seed, shorten=self.brain.shorten, cuts=cuts)
        ranges = [[s["start"], s["end"]] for s in plan["shots"] if s["kind"] == "video"]
        unit = f"{key}|{'v' if ranges else 'p'}{len(u.get('shot_ranges', []))}"
        return {"path": res.path, "plan": plan, "unit": unit, "ranges": ranges, "music": res.music, "work": work,
                "photo_only": not ranges}

    # ---- long-form ---------------------------------------------------------------------------------------
    def make_episode(self, theme: str, keys: list[str], events: dict, seed: int) -> EpisodeResult:
        work = self.work / f"episode_{seed % 100000}"
        lf = self.cfg.get("longform", {})
        n = len(keys)
        target_s = float(lf.get("target_minutes", 9.5)) * 60
        words = int(max(120, (target_s - 80) / n - 5) * 2.45)
        chapters = []
        for i, k in enumerate(keys):
            ev = events[k]
            brief, text = self.article_text(ev)
            ch = self.brain.write_chapter(i + 1, title=ev["title"], date=_pretty_date(ev.get("date", "")),
                                          article=text or brief or ev["title"], words=words)
            video = self.video(k, ev)
            photos = self.photos(k, ev, limit=8)
            scenes = _scene_list(video) if video else []
            if not photos:
                log.warning("skipping chapter without photos: %s", ev["title"][:60])
                continue
            chapters.append({"event_title": ev["title"], "date": _pretty_date(ev.get("date", "")).upper(),
                             "mood": THEME_MOOD.get(theme, "epic"), **ch,
                             "visuals": Visuals(video if scenes else None, scenes, photos)})
        if len(chapters) < 4:
            raise BrainError("too few chapters with visuals")
        package = self.brain.write_episode(theme, chapters)
        return produce_episode(theme=theme, chapters_in=chapters, package=package, cfg=self.cfg, work=work,
                               seed=seed)


def _days_ago(today_iso: str, n: int) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(today_iso) - timedelta(days=n)).isoformat()


def _pretty_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d %B %Y")
    except ValueError:
        return iso
