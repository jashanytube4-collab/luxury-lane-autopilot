"""Turns official events into finished videos centred on Prince Hamdan (Fazza).

Every Short and every episode is cut from scenes and photos where face recognition confirms he is on screen, with
the vertical crop centred on him. Episodes follow fan topics (father, horseman, poet, humble moments...) instead of
government news, because that is what Prince Hamdan's fans actually watch.

Usage bookkeeping (state/catalog.json, keyed by event):
    long         date of the episode that used it (each event is in at most one episode)
    shot_ranges  footage ranges [a, b] already used by Shorts (a Short never reuses footage another Short used)
    shorts_made  Shorts made from this event (max MAX_SHORTS_PER_EVENT, each a different moment)
    last_short   date of its last Short (events rest COOLDOWN_DAYS between Shorts)
    no_hamdan    True when he is not clearly visible in its footage/photos (never used again)
"""
from __future__ import annotations

import hashlib
import logging
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from .brain import Brain, BrainError, image_b64
from .config import ASSETS_DIR
from .faceid import FaceID, Sighting, scene_presence
from .hamdan import Hamdan, SourceError
from .longform import EpisodeResult, Visuals, _scene_list, produce_episode
from .media import FFMPEG, probe, run, scene_cuts
from .short import produce

log = logging.getLogger("lane.studio")

MAX_SHORTS_PER_EVENT = 3     # one story is never told more than three times, each with different footage
COOLDOWN_DAYS = 7
TOPIC_REST_DAYS = 9


def _slug(key: str) -> str:
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def _overlaps(a: float, b: float, ranges: list) -> bool:
    return any(a < y and x < b for x, y in ranges)


def fact_bank() -> dict:
    return yaml.safe_load((ASSETS_DIR / "fazza_facts.yaml").read_text(encoding="utf-8"))


class Studio:
    def __init__(self, cfg: dict, brain: Brain, hamdan: Hamdan, work: Path, cookies: Path | None = None) -> None:
        self.cfg, self.brain, self.hamdan, self.work, self.cookies = cfg, brain, hamdan, work, cookies
        self.video_failures = 0
        self.video_successes = 0
        self.face = FaceID()
        self.bank = fact_bank()

    # ---- material --------------------------------------------------------------------------------------
    def video(self, key: str, ev: dict) -> Path | None:
        if not ev.get("video"):
            return None
        dest = self.work / "cache" / f"{_slug(key)}.mp4"
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
            except Exception as e:  # noqa: BLE001 — one missing photo is fine
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

    def his_scenes(self, video: Path, exclude: list | None = None) -> list[tuple[float, float, Sighting]]:
        """Scenes of `video` where Prince Hamdan is clearly on screen, closest shots first."""
        timeline = self.face.scan(video)
        out = []
        for a, b in _scene_list(video):
            if exclude and _overlaps(a, b, exclude):
                continue
            s = scene_presence(timeline, a, b)
            if s.present:
                out.append((a, b, s))
        out.sort(key=lambda x: -x[2].size)
        return out

    def his_photos(self, photos: list[Path]) -> list[tuple[Path, Sighting]]:
        found = [(p, self.face.photo(p)) for p in photos]
        found = [(p, s) for p, s in found if s.present]
        found.sort(key=lambda x: -x[1].size)
        return found

    def facts(self, groups: list[str] | None, n: int, rng: random.Random) -> list[str]:
        bank = self.bank["facts"]
        pool = [f for g in (groups or list(bank)) for f in bank.get(g, [])]
        rng.shuffle(pool)
        return pool[:n]

    # ---- Shorts -----------------------------------------------------------------------------------------
    def _short_target(self, seed: int) -> float:
        s = self.cfg["short"]
        return random.Random(seed).uniform(s["min_seconds"] + 1, s["max_seconds"] - 2)

    def short_candidates(self, events: dict, usage: dict, day: str | None = None,
                         cooldown_days: int = COOLDOWN_DAYS) -> list[str]:
        """Events that can still give a Short: newest first, footage before photo stories, each event resting a week
        between Shorts so a day never shows the same story twice."""
        now = datetime.now(timezone.utc).date().isoformat()
        day = day or now
        rest_until = _days_ago(day, cooldown_days - 1)
        vids, pics = [], []
        for k, ev in events.items():
            u = usage.get(k, {})
            if u.get("rejected") or u.get("no_hamdan") or u.get("fails", 0) >= 2:
                continue
            if u.get("last_short") and u["last_short"] >= rest_until:
                continue
            if u.get("shorts_made", 0) >= MAX_SHORTS_PER_EVENT:
                continue
            if ev.get("video") and not u.get("video_exhausted"):
                vids.append(k)
            elif len(ev.get("photos", [])) >= 3 and not u.get("photo_short"):
                pics.append(k)
        key = lambda k: (events[k].get("date") or "0000", k)  # noqa: E731
        vids.sort(key=key, reverse=True)
        pics.sort(key=key, reverse=True)
        fresh = [k for k in vids + pics if (events[k].get("date") or "") >= _days_ago(now, 14)]
        rest = [k for k in vids + pics if k not in fresh]
        return fresh + rest

    def make_short(self, key: str, ev: dict, usage: dict, recent: list[str], seed: int) -> dict | None:
        """Returns {"path", "plan", "unit", "ranges", "music", ...} or None if this event gave nothing usable."""
        u = usage.setdefault(key, {})
        rng = random.Random(seed)
        work = self.work / f"short_{_slug(key)}_{seed % 10000}"
        video = self.video(key, ev)
        items, cuts, portrait = [], [], False
        if video:
            info = probe(video)
            portrait = info["height"] > info["width"]
            scenes = self.his_scenes(video, exclude=u.get("shot_ranges", []))
            if len(scenes) < 2 or sum(b - a for a, b, _ in scenes) < 4:
                u["video_exhausted"] = True
                video = None
            else:
                cuts = scene_cuts(video, info["fps"])
                for a, b, s in sorted(scenes[:6], key=lambda x: x[0]):
                    items.append({"kind": "video", "start": a, "end": b, "focus": s.cx,
                                  "b64": image_b64(video, (a + b) / 2)})
        photos = self.his_photos(self.photos(key, ev, limit=6))
        if not items and (u.get("photo_short") or len(photos) < 3):
            u["no_hamdan" if not photos else "video_exhausted"] = True
            return None
        for p, s in photos[: (2 if items else 5)]:
            items.append({"kind": "photo", "path": str(p), "focus": s.cx, "b64": image_b64(p)})
        if len(items) < 3:
            u["video_exhausted"] = True
            return None
        brief, text = self.article_text(ev)
        plan = self.brain.plan_short(items, title=ev["title"], date=ev.get("date", ""), brief=brief, article=text,
                                     target=self._short_target(seed), recent=recent, height_gt_width=portrait,
                                     facts=self.facts(None, 3, rng))
        if not plan["suitable"]:
            if not any(it["kind"] == "video" for it in items):
                u["rejected"] = {"score": plan["score"], "reason": plan.get("reject_reason", "")}
            else:
                u["video_exhausted"] = True
            return None
        self._fill_with_him(plan, items, video, work, self.cfg["short"]["max_seconds"])
        out = work / "short.mp4"
        res = produce(video if any(s["kind"] == "video" for s in plan["shots"]) else None, plan, self.cfg, work, out,
                      seed, shorten=self.brain.shorten, cuts=cuts)
        ranges = [[s["start"], s["end"]] for s in plan["shots"] if s["kind"] == "video"]
        unit = f"{key}|{'v' if ranges else 'p'}{len(u.get('shot_ranges', []))}"
        return {"path": res.path, "plan": plan, "unit": unit, "ranges": ranges, "music": res.music, "work": work,
                "photo_only": not ranges}

    def _fill_with_him(self, plan: dict, items: list[dict], video: Path | None, work: Path, need: float) -> None:
        """Make sure there is enough footage OF HIM for the whole Short: shots never run past the end of his scene,
        so any shortfall is filled with stills of him (frames from his own scenes, or his photos) with camera moves."""
        have = sum(min(s.get("hard_end", s["end"]), s["start"] + 4.5) - s["start"] if s["kind"] == "video" else 4.4
                   for s in plan["shots"])
        extra = [it for it in items if it["kind"] == "photo" and all(it["path"] != s.get("path") for s in plan["shots"])]
        grabs = [it for it in items if it["kind"] == "video"]
        k = 0
        while have < need + 1.0 and (extra or (video and k < len(grabs))):
            if extra:
                it = extra.pop(0)
                plan["shots"].append({"kind": "photo", "path": it["path"], "seconds": 3.4, "focus_x": it["focus"]})
            else:
                it = grabs[k]
                frame = work / f"still_{k}.jpg"
                work.mkdir(parents=True, exist_ok=True)
                run([FFMPEG, "-y", "-v", "error", "-ss", f"{(it['start'] + it['end']) / 2:.2f}", "-i", str(video),
                     "-frames:v", "1", "-q:v", "2", str(frame)])
                plan["shots"].insert(min(len(plan["shots"]), 2 * k + 1),
                                     {"kind": "photo", "path": str(frame), "seconds": 3.4, "focus_x": it["focus"]})
                k += 1
            have += 4.4

    # ---- long-form ---------------------------------------------------------------------------------------
    def choose_topic(self, events: dict, usage: dict, topics_used: dict, day: date
                     ) -> tuple[dict, list[str]]:
        """Today's fan topic and the events for it (unused in any episode, newest first)."""
        topics = self.bank["topics"]
        start = day.toordinal() % len(topics)
        recent = {t for t, d in topics_used.items() if d >= (day - timedelta(days=TOPIC_REST_DAYS)).isoformat()}
        order = [topics[(start + i) % len(topics)] for i in range(len(topics))]
        order = [t for t in order if t["id"] not in recent] + [t for t in order if t["id"] in recent]
        pool = {k: e for k, e in events.items()
                if not usage.get(k, {}).get("long") and not usage.get(k, {}).get("no_hamdan") and e.get("news_id")
                and (e.get("video") or len(e.get("photos", [])) >= 3)}
        for topic in order:
            kws = [k.lower() for k in topic.get("keywords", [])]
            cands = [k for k, e in pool.items() if not kws or any(w in f" {e['title'].lower()} " for w in kws)]
            cands.sort(key=lambda k: (bool(pool[k].get("video")), pool[k].get("date", "")), reverse=True)
            if len(cands) >= 6:
                return topic, cands[:16]
        raise BrainError("not enough unused events for an episode")

    def make_episode(self, topic: dict, candidates: list[str], events: dict, usage: dict, seed: int,
                     chapters_wanted: int = 6) -> tuple[EpisodeResult, list[str]]:
        rng = random.Random(seed)
        work = self.work / f"episode_{seed % 100000}"
        lf = self.cfg.get("longform", {})
        target_s = float(lf.get("target_minutes", 9.5)) * 60
        words = int(max(120, (target_s - 80) / chapters_wanted - 5) * 2.45)
        # 1) gather the material where he is on screen; keep the events with the most of him
        scored = []
        for k in candidates:
            ev = events[k]
            video = self.video(k, ev)
            scenes = [(a, b) for a, b, _ in self.his_scenes(video)] if video else []
            photos = self.his_photos(self.photos(k, ev, limit=6))
            seconds = sum(b - a for a, b in scenes)
            if not photos and seconds < 8:
                usage.setdefault(k, {})["no_hamdan"] = True
                continue
            scored.append((seconds + 4 * len(photos), k, video, sorted(scenes), photos))
            if len(scored) >= chapters_wanted + 3:
                break
        scored.sort(key=lambda x: -x[0])
        chosen = sorted(scored[:chapters_wanted], key=lambda x: events[x[1]].get("date", ""))
        if len(chosen) < 4:
            raise BrainError(f"too few events with Prince Hamdan on screen for '{topic['id']}'")
        # 2) words: one chapter per event, written from the official article + verified facts
        chapters, hero, hero_size = [], None, 0.0
        for i, (_, k, video, scenes, photos) in enumerate(chosen):
            ev = events[k]
            brief, text = self.article_text(ev)
            ch = self.brain.write_chapter(i + 1, title=ev["title"], date=_pretty_date(ev.get("date", "")),
                                          article=text or brief or ev["title"], words=words, angle=topic["angle"],
                                          facts=self.facts(topic.get("facts"), 4, rng))
            still = [p for p, _ in photos] or self.photos(k, ev, limit=4)
            if not still and scenes:   # footage-only event: a still of him from the footage for the title card
                a, b = scenes[0]
                frame = self.work / "cache" / f"{_slug(k)}_still.jpg"
                run([FFMPEG, "-y", "-v", "error", "-ss", f"{(a + b) / 2:.2f}", "-i", str(video), "-frames:v", "1",
                     "-q:v", "2", str(frame)])
                still = [frame]
            if not still:
                continue
            if photos and photos[0][1].size > hero_size:
                hero, hero_size = photos[0][0], photos[0][1].size
            chapters.append({"event_title": ev["title"], "date": _pretty_date(ev.get("date", "")).upper(),
                             "mood": _mood(topic["id"]), **ch,
                             "visuals": Visuals(video if scenes else None, scenes, still)})
        if len(chapters) < 4:
            raise BrainError("too few chapters with visuals")
        package = self.brain.write_episode(topic["angle"], chapters, topic.get("title_hint", ""))
        result = produce_episode(theme=topic["angle"], chapters_in=chapters, package=package, cfg=self.cfg, work=work,
                                 seed=seed, hero=hero)
        return result, [k for _, k, *_ in chosen]


def _mood(topic_id: str) -> str:
    return {"father": "elegant", "humble": "elegant", "poet": "arabian", "horseman": "arabian",
            "fitness": "modern", "future": "modern", "builder": "epic", "defence": "epic",
            "world": "epic"}.get(topic_id, "epic")


def _days_ago(today_iso: str, n: int) -> str:
    return (date.fromisoformat(today_iso) - timedelta(days=n)).isoformat()


def _pretty_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d %B %Y")
    except ValueError:
        return iso
