"""Daily run: sync official events from hamdan.ae -> make 1 long-form episode + 10 Shorts -> schedule them on
YouTube. Safe to run any number of times a day: it only fills slots that are still empty, and it saves (and in CI
commits) its state after every upload, so nothing is ever posted twice.

    python -m lane.pipeline                 # normal run (used by GitHub Actions)
    python -m lane.pipeline --dry-run -n 2  # make 2 videos into work/out without uploading
"""
from __future__ import annotations

import argparse
import base64
import logging
import os
import random
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from .audio import credit_line
from .brain import AIUnavailable, Brain
from .config import WORK_DIR, load_config, load_secrets
from .hamdan import Hamdan, Library, SourceError, load_events, save_events
from .instagram import Instagram, sync as sync_instagram
from .report import RunReport, setup_logging
from .posts import make_post
from .quotes import harvest as harvest_quotes
from .schedule import adopt_uploads, day_slots, long_slot, post_slots, target_days, top_up
from .state import State
from .story import QualityGateError, StoryMaker, auto_story, bank as story_bank, next_story, produce_story
from .trends import mark_used
from .studio import Studio
from .youtube import AuthError, QuotaError, YouTube, key_tag

log = logging.getLogger("lane.pipeline")

RUN_BUDGET_MIN = 320       # stop starting new videos after this many minutes (GitHub's job limit is 360)
REMIX = "<remix>"           # pseudo event key: old footage re-cut into short new pieces (Studio.make_montage remix=True)
MONTAGE = "<montage>"       # pseudo event key: the montage fallback after the real candidates (Studio.make_montage)
SOURCE_CREDIT = "Footage and photos: official media of H.H. Sheikh Hamdan bin Mohammed (hamdan.ae)."


def _fmt_ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


_FILLER = {"the", "a", "an", "of", "and", "in", "at", "to", "his", "prince", "hamdan", "sheikh", "fazza", "s", "with"}


def _similar(a: str, b: str) -> bool:
    """Two titles say the same thing (e.g. 'Prince Hamdan Brings Warmth' vs 'THE HUMAN SIDE | Fazza Brings Warmth')."""
    wa = set(re.findall(r"[a-z]+", a.lower())) - _FILLER
    wb = set(re.findall(r"[a-z]+", b.lower())) - _FILLER
    if not wa or not wb:
        return a.strip().lower() == b.strip().lower()
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.6


def _tags(base: list[str], extra: list[str], key: str) -> list[str]:
    tags, total = [], 0
    for t in [*extra, *base]:
        if t.lower() in (x.lower() for x in tags):
            continue
        if total + len(t) + 3 > 440:
            break
        tags.append(t)
        total += len(t) + 3
    tags.append(key_tag(key))
    return tags


class Runner:
    def __init__(self, args) -> None:
        self.args = args
        self.cfg = load_config()
        self.secrets = load_secrets()
        self.report = RunReport()
        self.state = State()
        self.events: dict = load_events()
        self.started = time.monotonic()
        self.brain = Brain(self.cfg["ai"])
        lib_repo = self.cfg.get("source", {}).get("library_repo")
        library = Library(lib_repo, self.secrets.library_token) if lib_repo and self.secrets.library_token else None
        self.hamdan = Hamdan(library)
        cookies = None
        if self.secrets.yt_download_cookies:
            cookies = WORK_DIR / "yt_cookies.txt"
            WORK_DIR.mkdir(parents=True, exist_ok=True)
            cookies.write_bytes(base64.b64decode(self.secrets.yt_download_cookies))
        self.studio = Studio(self.cfg, self.brain, self.hamdan, WORK_DIR, cookies)
        self.yt: YouTube | None = None
        self.channel_uploads: list[dict] | None = None   # what is already on the channel (duplicate guard)
        self.made = 0
        self._dry: set[bool] = set()     # kinds of Short that found no fresh footage earlier in this run

    @property
    def usage(self) -> dict:
        return self.state.catalog

    def over_budget(self) -> bool:
        return time.monotonic() - self.started > RUN_BUDGET_MIN * 60

    # ---- setup ---------------------------------------------------------------------------------------
    def connect(self) -> None:
        if not self.args.dry_run:
            self.yt = YouTube(self.secrets.yt_client_id, self.secrets.yt_client_secret, self.secrets.yt_refresh_token)
            self.report.note(f"YouTube channel: {self.yt.channel_title}")

    def sync(self) -> None:
        try:
            added = self.hamdan.sync(self.events, full=not self.events)
            save_events(self.events)
            self.report.note(f"hamdan.ae: {added} new official events ({len(self.events)} in the library)")
        except SourceError as e:
            self.report.problem(f"Could not reach hamdan.ae ({e}); using the saved library")
        try:
            n = harvest_quotes(self.state.cursors)
            self.report.note(f"Quotes: {n} new real quotes of Prince Hamdan "
                             f"({len(self.state.cursors.get('quotes', []))} collected)")
        except Exception as e:  # noqa: BLE001 — quotes are optional; fan lines are always available
            self.report.problem(f"Quote collection failed: {str(e)[:150]}")
        accounts = self.cfg.get("source", {}).get("instagram") or []
        if accounts and self.secrets.ig_token and self.secrets.ig_user_id and self.hamdan.library:
            try:
                ig = Instagram(self.secrets.ig_token, self.secrets.ig_user_id)
                added = sync_instagram(self.events, ig, self.hamdan.library, accounts, WORK_DIR / "ig",
                                       limit=int(self.cfg.get("source", {}).get("instagram_per_run", 25)),
                                       check=self._check_clip)
                save_events(self.events)
                self.report.note(f"Instagram: {added} new videos from {', '.join('@' + (a['name'] if isinstance(a, dict) else a) for a in accounts)}")
            except Exception as e:  # noqa: BLE001 — Instagram is a bonus source, never a reason to stop
                msg = str(e)
                expired = bool(re.search(r"token|session|expired|OAuth|\(#?190\)|permission", msg, re.I))
                self.state.cursors["ig_status"] = {"ok": False, "expired": expired, "error": msg[:150],
                                                   "date": datetime.now(timezone.utc).date().isoformat()}
                self.report.problem(("Instagram login expired - reconnect it (tools/connect_instagram.py); "
                                     "Shorts continue from the official video library meanwhile. " if expired else "")
                                    + f"Instagram sync failed: {msg[:200]}")
                self.studio.cfg.setdefault("short", {})["deep_cut"] = True   # no new Instagram footage: cut deeper
            else:
                self.state.cursors["ig_status"] = {"ok": True, "date": datetime.now(timezone.utc).date().isoformat()}

    def _check_clip(self, path, caption: str, need_face: bool = False):
        """A new Instagram video. From news accounts it is kept only if face recognition finds him; on his own account
        he is often the one filming (his falcons, his children), so there only text graphics and greetings are
        dropped. The AI describes what it shows; without the AI, words of the caption serve as tags."""
        from .brain import image_b64
        from .longform import _scene_list
        scenes = [(a, b) for a, b, _ in self.studio.his_scenes(path)]
        if need_face and not scenes:
            return None
        scenes = scenes or _scene_list(path)
        fallback = {"about": "", "tags": sorted({w.lower() for w in re.findall(r"#?([A-Za-z]{4,})", caption)})[:10]}
        if not scenes:
            return fallback
        try:
            step = max(1, len(scenes) // 4)
            frames = [image_b64(path, (a + b) / 2) for a, b in scenes[::step][:4]]
            d = self.brain.describe_clip(frames, caption)
        except Exception as e:  # noqa: BLE001 — a clip without a description is still good footage
            log.warning("could not describe clip: %s", e)
            return fallback
        if not d.get("real", True):
            return None
        d["tags"] = d.get("tags") or fallback["tags"]
        return d

    def reconcile(self) -> None:
        """Anything already on the channel counts as used, even if a crashed run never saved it."""
        if not self.yt:
            return
        tags = None
        for wait in (0, 10, 30):             # a network blip here must never stop the day's uploads
            time.sleep(wait)
            try:
                tags = self.yt.recent_tags()
                break
            except Exception as e:  # noqa: BLE001
                log.warning("could not read recent uploads (%s); retrying", str(e)[:120])
        if tags is None:
            self.report.problem("Could not check the channel's recent uploads; continuing without it")
            return
        try:
            self.channel_uploads = self.yt.recent_uploads()
        except Exception as e:  # noqa: BLE001 — without it we just lose the duplicate guard for this run
            self.channel_uploads = None
            self.report.problem(f"Could not read the channel's upload times ({str(e)[:100]}); duplicate guard is off this run")
        posted = self.state.cursors.setdefault("posted_units", [])
        new = [t for t in tags if t not in posted]
        if new:
            posted.extend(new)
            self.report.note(f"Reconciled {len(new)} upload(s) already on the channel")

    def ensure_short_supply(self) -> None:
        """Shorts must never run dry. Counts the events that can give a Short today; when there are fewer than two
        days' worth: (1) stories rest fewer days (down to 1), then (2) footage whose Shorts went out at least
        `short.recycle_after_days` ago comes back for a fresh cut (new moments order, music and title)."""
        sc = self.cfg.setdefault("short", {})
        per_day = int(self.cfg["schedule"]["shorts_per_day"])
        need = 2 * per_day
        today = datetime.now(timezone.utc).date()

        def supply(rest: int) -> int:
            # footage that already gave a Short counts fully; untried footage about half (≈54% turn out to show him)
            n = 0.0
            for k in self.studio.short_candidates(self.events, self.usage, today.isoformat(), rest):
                if self.events[k].get("video"):
                    n += 1.0 if self.usage.get(k, {}).get("shorts_made", 0) >= 1 else 0.5
            return int(n)

        base = int(sc.get("cooldown_days", 3))
        rest, have = base, supply(base)
        while have < need and rest > 1:
            rest -= 1
            have = supply(rest)
        if rest != base:
            sc["cooldown_days"] = rest           # this run only; config.yaml keeps the normal value
            self.report.note(f"Footage for Shorts was low: stories rest {rest} day(s) instead of {base} this run")
        if have < need:
            cutoff = (today - timedelta(days=int(sc.get("recycle_after_days", 60)))).isoformat()
            back = 0
            for k, ev in self.events.items():
                u = self.usage.get(k)
                if not u or not ev.get("video") or u.get("rejected") or u.get("no_hamdan") or u.get("fails", 0) >= 2:
                    continue
                used_up = u.get("video_exhausted") or u.get("shorts_made", 0) >= self.studio.event_cap(u)
                if used_up and u.get("shorts_made", 0) >= 1 and u.get("last_short", "9999") <= cutoff:
                    u.pop("video_exhausted", None)
                    u["shot_ranges"], u["shorts_made"], u["recycled"] = [], 0, today.isoformat()
                    back += 1
            if back:
                self.state.save()
                have = supply(rest)
                self.report.note(f"{back} older footage event(s) came back for fresh Shorts "
                                 f"(their Shorts went out {sc.get('recycle_after_days', 60)}+ days ago)")
        if have < need and sc.get("deep_cut") is None:
            # Instagram (or any new source) is not keeping up: long official videos are cut deeper, one Short per
            # 45 s of video, always from time ranges no earlier Short used (state: shot_ranges), him clearly in frame
            sc["deep_cut"] = True                # this run only; config.yaml keeps the setting ("auto")
            more = supply(rest)
            if more > have:
                self.report.note(f"Deep cut on: long official videos now give more Shorts ({have} -> {more} events)")
                have = more
        days = have / max(1, per_day)
        self.state.cursors["shorts_supply"] = {"date": today.isoformat(), "events": have, "days": round(days, 1)}
        log.info("Shorts footage supply: %d events (about %.1f days at %d a day)", have, days, per_day)
        if have < need:
            self.report.problem(f"Footage for Shorts is running low: {have} events left (about {days:.1f} days). "
                                "More Instagram videos are collected every run; fewer Shorts per day would help.")

    def _recent_lines(self) -> list[str]:
        return self.state.cursors.get("recent_hooks", [])[-40:]

    # ---- one upload ------------------------------------------------------------------------------------
    def _upload(self, path, *, title, description, tags, publish_at, thumbnail=None, private=False) -> str | None:
        if self.args.dry_run:
            dest = WORK_DIR / "out" / f"{publish_at.strftime('%Y%m%d_%H%M')}_{path.name}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, dest)
            if thumbnail:
                shutil.copy(thumbnail, dest.with_suffix(".thumb.jpg"))
            dest.with_suffix(".txt").write_text(f"{title}\n\n{description}\n\nTAGS: {', '.join(tags)}\n",
                                                encoding="utf-8")
            self.report.note(f"[dry run] {dest.name} — {title}")
            return "dry-run"
        when = publish_at if self.cfg["youtube"].get("privacy", "scheduled") == "scheduled" and not private else None
        vid = self.yt.upload(path, title=title, description=description, tags=tags,
                             category_id=self.cfg["youtube"]["category_id"], publish_at=when)
        if thumbnail:
            self.yt.set_thumbnail(vid, thumbnail)
        return vid

    # ---- long-form ------------------------------------------------------------------------------------
    def alert_news(self, item) -> None:
        """Telegram message to the owner about a big fresh news story (no video is made automatically)."""
        text = (f"BIG NEWS ({item.score}/10): {item.label}\n"
                f"Reported by: {', '.join(item.publishers[:5])}\n"
                "Tell Rocky 'haan, is par video bana' to build a premium video.")
        self.report.note(f"News alert sent: {item.label[:90]} ({item.score}/10)")
        if not (self.secrets.telegram_bot_token and self.secrets.telegram_chat_id):
            return
        try:
            requests.post(f"https://api.telegram.org/bot{self.secrets.telegram_bot_token}/sendMessage",
                          json={"chat_id": self.secrets.telegram_chat_id, "text": text}, timeout=20)
        except requests.RequestException as e:
            log.warning("News alert failed: %s", e)

    def plan_news_long(self, day, plan: dict, sc: dict, ncfg: dict) -> None:
        """A big fresh news story (reporter score above news.min_score) gets an EXTRA long video today."""
        from . import news as news_mod
        item = news_mod.best(self.state.cursors, float(ncfg.get("min_score", 6)), day)
        if item is None:
            return
        if not ncfg.get("extra_long"):      # alert only: the owner decides, then Rocky builds the video by hand
            self.alert_news(item)
            news_mod.mark_done(self.state.cursors, item, day)
            self.state.save()
            return
        at = datetime.now(timezone.utc) + timedelta(minutes=int(ncfg.get("lead_minutes", 150)))
        at = at.astimezone(ZoneInfo(sc["timezone"])).replace(second=0, microsecond=0)
        if at.date() != day:
            self.report.note(f"Big news story '{item.label[:70]}' ({item.score}/10) found too late for an extra long video today")
            return
        slot = {"at": at.isoformat(), "type": "long", "status": "open",
                "news": {"key": item.key, "label": item.label, "score": item.score, "items": item.items}}
        plan["slots"] = sorted(plan["slots"] + [slot], key=lambda s: s["at"])
        news_mod.mark_done(self.state.cursors, item, day)
        self.state.save_day(day.isoformat(), plan)
        self.state.save()
        self.report.note(f"EXTRA long video planned for big news ({item.score}/10): {item.label[:90]}")

    def fill_news_long(self, day, slot: dict, plan: dict) -> bool:
        from . import news as news_mod
        try:
            item = news_mod.rebuild(slot["news"])
            found = auto_story(self.brain, self.studio, self.events, self.usage, self.state.cursors, day, self.cfg,
                               news=item) if item else None
        except AIUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 — the regular long video of the day is not affected
            self.report.problem(f"News story failed: {str(e)[:300]}")
            found = None
        if found is None:
            slot["status"] = "skipped"
            self.state.save_day(day.isoformat(), plan)
            return True
        story, _ = found
        return self.fill_story(day, slot, plan, story)

    def fill_long(self, day, slot: dict, plan: dict) -> bool:
        if slot.get("news"):
            return self.fill_news_long(day, slot, plan)
        held = (self.cfg.get("longform", {}).get("held") or {}).get(day.isoformat())
        if held and not self.args.dry_run:   # a long video the owner moved to this day: it is this day's long video
            slot.update(status="scheduled", video_id=held["video_id"], at=held["at"], title=held.get("title", ""))
            self.state.save_day(day.isoformat(), plan)
            self.state.checkpoint(f"long video {held['video_id']} held for {day.isoformat()}")
            return True
        forced = os.environ.get("TEST_STORY", "").strip() if self.args.dry_run else ""
        ready_only = bool(self.cfg.get("story", {}).get("ready_only", False))
        posted = set(self.state.cursors.get("posted_units", []))      # stories already on the channel count as done
        done = self.state.cursors.get("stories_done", []) + self.state.cursors.get("stories_blocked", []) + [s.id for s in story_bank()
                                                              if key_tag(f"story:{s.id}") in posted]
        story = (next((x for x in story_bank() if x.id == forced), None) if forced
                 else next_story(done, ready_only))
        if story is not None:
            return self.fill_story(day, slot, plan, story)
        if ready_only:      # quality first: no long video rather than one below the standard
            self.report.note("No approved story is ready, so no long video for this slot "
                             "(stories need exact clips for every sentence and ready: true)")
            return True
        if self.cfg.get("story", {}).get("auto", True):   # bank used up: a fresh topic found and written automatically
            try:
                found = auto_story(self.brain, self.studio, self.events, self.usage, self.state.cursors, day, self.cfg)
            except AIUnavailable:
                raise
            except Exception as e:  # noqa: BLE001 — fall back to the fan-topic episode below
                self.report.problem(f"Automatic story failed: {str(e)[:300]}")
                found = None
            if found is not None:
                story, topic = found
                self.report.note(f"Automatic story topic — {topic.why}")
                if self.fill_story(day, slot, plan, story) and slot.get("status") == "scheduled":
                    if not self.args.dry_run:
                        mark_used(self.state.cursors, topic, day)
                        self.state.save()
                    return True
        topics_used = self.state.cursors.setdefault("topics_used", {})
        try:
            topic, candidates = self.studio.choose_topic(self.events, self.usage, topics_used, day)
        except Exception as e:  # noqa: BLE001
            self.report.problem(f"No episode possible today: {e}")
            return True
        seed = random.randint(1, 10**9)
        try:
            ep, keys = self.studio.make_episode(topic, candidates, self.events, self.usage, seed,
                                                int(self.cfg.get("longform", {}).get("chapters", 6)))
        except AIUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 — try again next run with other events
            self.report.problem(f"Episode failed ({topic['id']}): {str(e)[:300]}")
            self.state.save()
            return True
        if not self.args.dry_run:
            topics_used[topic["id"]] = day.isoformat()
        at = datetime.fromisoformat(slot["at"])
        chapters = "\n".join(f"{_fmt_ts(t)} {name}" for t, name in [(0, "Intro"), *ep.chapters])
        credits = "\n".join(c for c in {credit_line(m) for m in ep.music} if c)
        desc = (f"{ep.description}\n\n{chapters}\n\n{SOURCE_CREDIT}\nNarration and edit: "
                f"{self.cfg['channel']['name']}.\n{credits}\n\n{' '.join(self.cfg['youtube'].get('base_hashtags', []))}")
        unit = f"long:{day.isoformat()}"
        try:
            vid = self._upload(ep.path, title=ep.title, description=desc,
                               tags=_tags(self.cfg["youtube"].get("base_tags", []), ep.tags, unit),
                               publish_at=at, thumbnail=ep.thumbnail)
        except QuotaError as e:
            self.report.problem(f"YouTube upload quota reached ({e})")
            return False
        except Exception as e:  # noqa: BLE001
            self.report.problem(f"Episode upload failed: {e}")
            return True
        if not self.args.dry_run:
            for k in keys:
                self.usage.setdefault(k, {})["long"] = day.isoformat()
        slot.update(status="scheduled", video_id=vid, title=ep.title, minutes=round(ep.duration / 60, 1))
        self.state.save_day(day.isoformat(), plan)
        self.state.checkpoint(f"episode {vid} for {slot['at']}")
        self.made += 1
        self.report.note(f"Episode {at.strftime('%b %d %H:%M')} — {ep.title} ({ep.duration / 60:.1f} min)")
        shutil.rmtree(ep.path.parent, ignore_errors=True)
        return True

    def fill_story(self, day, slot: dict, plan: dict, story) -> bool:
        """A story episode (assets/stories) in the style of the most-watched royal-family channels."""
        from tools.lint_story import lint_story_id     # pre-render gate (test renders): a failing story is never rendered
        bad = lint_story_id(story.id) if self.args.dry_run else []
        if self.args.dry_run:                          # pre-render quality gate (tools.prerender_gate): no stamp, no render
            from tools.prerender_gate import stamp_ok
            why = stamp_ok(story.id)
            if why:
                bad.append(why)
        if bad:
            self.report.problem(f"Story '{story.id}' blocked BEFORE render by the pre-render gate: " + "; ".join(bad[:8]))
            return True
        seed = random.randint(1, 10**9)
        work = WORK_DIR / f"story_{story.id}_{seed % 10000}"
        try:
            maker = StoryMaker(self.studio, self.events, self.usage, self.cfg)
            got = maker.gather(story, random.Random(seed), work)
            ep = produce_story(story, got, self.cfg, work, seed)
            if got.gate_warning:
                self.report.problem(got.gate_warning)
        except QualityGateError as e:      # not premium: never padded with repeats; skipped until more footage is added
            self.report.problem(f"Story held back by the quality gate: {str(e)[:400]}")
            if slot.get("news"):               # the extra news video is optional: skipped, never made below the bar
                slot["status"] = "skipped"
                self.state.save_day(day.isoformat(), plan)
            elif not self.args.dry_run:
                self.state.cursors.setdefault("stories_blocked", []).append(story.id)
            self.state.save()
            return True
        except Exception as e:  # noqa: BLE001 — the next run tries again
            self.report.problem(f"Story episode '{story.id}' failed: {str(e)[:300]}")
            self.state.save()
            return True
        at = datetime.fromisoformat(slot["at"])
        credits = "\n".join(c for c in {credit_line(m) for m in ep.music} if c)
        photo_credits = "\n".join(dict.fromkeys(got.credits))
        clip_credits = "\n".join(c for c in dict.fromkeys(got.clip_credits) if c)
        desc = (f"{ep.description}\n\n{SOURCE_CREDIT}\n"
                + (f"Clips:\n{clip_credits}\n" if clip_credits else "")
                + (f"Additional photos (Wikimedia Commons):\n{photo_credits}\n" if photo_credits else "")
                + f"Narration and edit: {self.cfg['channel']['name']}.\n{credits}\n\n"
                + " ".join(self.cfg['youtube'].get('base_hashtags', [])))[:4900]
        unit = f"story:{story.id}"
        try:
            vid = self._upload(ep.path, title=ep.title[:100], description=desc,
                               tags=_tags(self.cfg["youtube"].get("base_tags", []), ep.tags, unit),
                               publish_at=at, thumbnail=ep.thumbnail)
        except QuotaError as e:
            self.report.problem(f"YouTube upload quota reached ({e})")
            return False
        except Exception as e:  # noqa: BLE001
            self.report.problem(f"Story upload failed: {e}")
            return True
        if not self.args.dry_run:
            self.state.cursors.setdefault("stories_done", []).append(story.id)
            for k in got.event_keys:
                self.usage.setdefault(k, {})["long"] = day.isoformat()
        slot.update(status="scheduled", video_id=vid, title=ep.title, minutes=round(ep.duration / 60, 1))
        self.state.save_day(day.isoformat(), plan)
        self.state.checkpoint(f"story {vid} for {slot['at']}")
        self.made += 1
        self.report.note(f"Story {at.strftime('%b %d %H:%M')} — {ep.title} ({ep.duration / 60:.1f} min)")
        shutil.rmtree(work, ignore_errors=True)
        return True

    # ---- picture posts ------------------------------------------------------------------------------------
    def fill_post(self, day, slot: dict, plan: dict) -> bool:
        seed = random.randint(1, 10**9)
        work = WORK_DIR / f"post_{seed % 100000}"
        try:
            item = make_post(self.studio, self.events, self.usage, self.state.cursors, day, self.cfg, work, seed)
        except Exception as e:  # noqa: BLE001 — a missing picture post never stops the day
            self.report.note(f"Picture post skipped: {str(e)[:150]}")
            slot["status"] = "skipped"
            self.state.save_day(day.isoformat(), plan)
            return True
        if item is None:
            slot["status"] = "skipped"
            self.state.save_day(day.isoformat(), plan)
            return True
        at = datetime.fromisoformat(slot["at"])
        credit = credit_line(item["music"])
        desc = (f"{item['description']}\n\nPhotos: official media of H.H. Sheikh Hamdan bin Mohammed (hamdan.ae).\n"
                + (f"{credit}\n" if credit else "") + "\n" + " ".join(self.cfg["youtube"].get("base_hashtags", [])))
        try:
            vid = self._upload(item["path"], title=item["title"], description=desc,
                               tags=_tags(self.cfg["youtube"].get("base_tags", []), item["tags"], item["unit"]),
                               publish_at=at)
        except QuotaError as e:
            self.report.problem(f"YouTube upload quota reached ({e}); remaining picture posts skipped")
            return False
        except Exception as e:  # noqa: BLE001
            self.report.problem(f"Picture post upload failed: {e}")
            return True
        if not self.args.dry_run:
            for k, i in item["photos"]:
                self.usage.setdefault(k, {}).setdefault("post_photos", []).append(i)
        slot.update(status="scheduled", video_id=vid, title=item["title"])
        self.state.save_day(day.isoformat(), plan)
        self.state.checkpoint(f"post {vid} for {slot['at']}")
        self.made += 1
        self.report.note(f"Post {at.strftime('%b %d %H:%M')} — {item['title']}")
        shutil.rmtree(work, ignore_errors=True)
        return True

    # ---- Shorts -----------------------------------------------------------------------------------------
    def _recent_titles(self) -> list[str]:
        return self.state.cursors.get("recent_titles", [])[-300:]

    def fill_short(self, day, slot: dict, plan: dict) -> bool:
        first = random.random() < min(0.4, float(self.cfg["short"].get("moment_share", 0.25)))   # >=60% narrated
        # a moment Short when possible; a narrated one only if no footage of him is left for a moment today
        for moment in ([True, False] if first else [False]):
            if moment in self._dry:      # this run already found no fresh footage for this kind: don't burn time again
                continue
            done = self._fill_short(day, slot, plan, moment)
            if done is not None:
                return done
            self._dry.add(moment)
        # last resort so a day never runs empty: old footage re-cut as short new pieces from many videos
        done = self._fill_short(day, slot, plan, True, remix=True)
        if done is not None:
            return done
        self.report.problem("No usable material left for Shorts today")
        return False

    def _fill_short(self, day, slot: dict, plan: dict, moment: bool, members: bool = False,
                    remix: bool = False) -> bool | None:
        at = datetime.fromisoformat(slot["at"])
        tries, started = 0, time.time()
        keys = [] if remix else self.studio.short_candidates(self.events, self.usage, day.isoformat())[:12]
        if remix:
            keys = [REMIX, REMIX]
        elif moment and self.cfg["short"].get("deep_cut") is not False:
            keys = keys + [MONTAGE]          # fallback when no single event can give a moment (see Studio.make_montage)
        for key in keys:
            if time.time() - started > 20 * 60:     # rejected events are remembered, so the next slot goes on from here
                log.info("no Short after %d tries in 20 min for this slot; moving on", tries)
                return None
            if self.over_budget():
                self.report.note("Run time budget reached; remaining slots are filled by the next run")
                return False
            ev = self.events.get(key, {})
            if moment and key not in (MONTAGE, REMIX) and not ev.get("video"):
                continue
            seed = random.randint(1, 10**9)
            tries += 1
            t0 = time.time()
            try:
                if key in (MONTAGE, REMIX):
                    item = self.studio.make_montage(self.events, self.usage, self._recent_titles(), seed,
                                                    remix=key == REMIX)
                elif moment:
                    item = self.studio.make_moment(key, ev, self.usage, self._recent_titles(), seed)
                else:
                    item = self.studio.make_short(key, ev, self.usage, self._recent_lines(), seed)
            except AIUnavailable:
                raise
            except Exception as e:  # noqa: BLE001 — one bad event never stops the day
                if key in (MONTAGE, REMIX):
                    self.report.note(f"Montage fallback failed: {str(e)[:150]}")
                    continue
                u = self.usage.setdefault(key, {})
                u["fails"] = u.get("fails", 0) + 1
                self.report.note(f"Skipped {ev['title'][:60]}: {str(e)[:150]}")
                self.state.save()
                continue
            self.state.save()
            log.info("short try %d (%s): %s in %.0fs — %s", tries, "moment" if moment else "narrated",
                     "made" if item else "nothing", time.time() - t0, ev.get("title", "")[:60])
            if item is None:
                continue
            p = item["plan"]
            if not moment and any(_similar(p["title"], t) for t in self._recent_titles()[-120:]):
                self.report.note(f"Skipped a Short whose title repeats a recent one: {p['title']}")
                shutil.rmtree(item["work"], ignore_errors=True)
                continue
            hashtags = " ".join(self.cfg["youtube"].get("base_hashtags", []))
            credit = credit_line(item["music"])
            made_by = "Narration and edit" if (not moment or item.get("voiced")) else "Edit"
            if members:
                p["description"] = f"🔒 Fazza Fam exclusive — for members of The Luxury Lane 💛\n\n{p['description']}"
            desc = (f"{p['description']}\n\n{SOURCE_CREDIT}\n{made_by}: {self.cfg['channel']['name']}.\n"
                    + (f"{credit}\n" if credit else "") + f"\n{hashtags}")
            try:
                vid = self._upload(item["path"], title=p["title"], description=desc,
                                   tags=_tags(self.cfg["youtube"].get("base_tags", []), p.get("tags", []), item["unit"]),
                                   publish_at=at, private=members)
            except QuotaError as e:
                self.report.problem(f"YouTube upload quota reached ({e})")
                return False
            except Exception as e:  # noqa: BLE001
                self.report.problem(f"Upload failed: {e}")
                return True
            if members and not self.args.dry_run:
                self.state.cursors.setdefault("members_shorts", []).append(
                    {"id": vid, "day": day.isoformat(), "title": p["title"]})
            u = self.usage.setdefault(key, {}) if key not in (MONTAGE, REMIX) else {}
            if not self.args.dry_run:
                for k2, rngs in item.get("sources", {}).items():     # a montage: every source video remembers its range
                    u2 = self.usage.setdefault(k2, {})
                    u2.setdefault(item.get("range_key", "shot_ranges"), []).extend(rngs)
                    u2["last_short"] = max(u2.get("last_short", ""), day.isoformat())
                    u2["montage_used"] = u2.get("montage_used", 0) + 1
                if item["photo_only"]:
                    u["photo_short"] = True
                u.setdefault("shot_ranges", []).extend(item["ranges"])
                u["last_short"] = max(u.get("last_short", ""), day.isoformat())
                u["shorts_made"] = u.get("shorts_made", 0) + 1
                hooks = self.state.cursors.setdefault("recent_hooks", [])
                hooks.append(f"{p.get('hook_text', '')} | {p['title']}")
                del hooks[:-60]
                titles = self.state.cursors.setdefault("recent_titles", [])
                titles.append(p["title"])
                del titles[:-300]
            slot.update(status="scheduled", video_id=vid, title=p["title"], event=key if key not in (MONTAGE, REMIX) else "montage",
                        kind=("narrated" if not moment else "remix" if key == REMIX else "montage" if key == MONTAGE else "moment"),
                        voiced=bool(not moment or item.get("voiced")))
            self.state.save_day(day.isoformat(), plan)
            self.state.checkpoint(f"short {vid} for {slot['at']}")
            self.made += 1
            self.report.note(f"Short {at.strftime('%b %d %H:%M')} — {p['title']}")
            shutil.rmtree(item["work"], ignore_errors=True)
            return True
        return None

    # ---- days ------------------------------------------------------------------------------------------
    def fill_day(self, day) -> bool:
        sc = self.cfg["schedule"]
        date_s = day.isoformat()
        plan = self.state.load_day(date_s)
        if plan is None:
            rng = random.Random()
            shorts = day_slots(day, sc, rng)
            lt = long_slot(day, sc, shorts, rng)
            slots = [{"at": s.isoformat(), "type": "short", "status": "open"} for s in shorts]
            slots.append({"at": lt.isoformat(), "type": "long", "status": "open"})
            plan = {"date": date_s, "timezone": sc["timezone"], "slots": sorted(slots, key=lambda s: s["at"])}
            self.state.save_day(date_s, plan)
        lead = timedelta(minutes=sc.get("min_lead_minutes", 25))
        if sc.get("posts_per_day") and not any(s["type"] == "post" for s in plan["slots"]):
            taken = [datetime.fromisoformat(s["at"]) for s in plan["slots"]]
            extra = post_slots(day, sc, taken, random.Random(), datetime.now(timezone.utc) + lead)
            plan["slots"] = sorted(plan["slots"] + [{"at": t.isoformat(), "type": "post", "status": "open"}
                                                    for t in extra], key=lambda s: s["at"])
            self.state.save_day(date_s, plan)
            self.report.note(f"{date_s}: {len(extra)} picture posts planned")
        mcfg = self.cfg.get("members", {})
        n_members = int(mcfg.get("shorts_per_day", 0))
        if not n_members and day.weekday() == int(mcfg.get("weekday", 5)):
            n_members = int(mcfg.get("shorts_per_week", 0))
        if n_members and not any(s["type"] == "members" for s in plan["slots"]):
            # members-only Shorts: uploaded private; the Saturday helper on the laptop makes them members-only
            # (YouTube's upload API cannot set members-only, and Studio cannot schedule it)
            base = datetime.fromisoformat(f"{date_s}T21:00:00").replace(tzinfo=ZoneInfo(sc["timezone"]))
            plan["slots"] = sorted(plan["slots"] + [{"at": (base + timedelta(minutes=i)).isoformat(), "type": "members",
                                                     "status": "open"} for i in range(n_members)], key=lambda s: s["at"])
            self.state.save_day(date_s, plan)
        shorts_now = [datetime.fromisoformat(s["at"]) for s in plan["slots"] if s["type"] == "short"]
        if len(shorts_now) < int(sc["shorts_per_day"]):
            long_t = [datetime.fromisoformat(s["at"]) for s in plan["slots"] if s["type"] == "long"]
            extra = top_up(day, sc, shorts_now, random.Random(), datetime.now(timezone.utc) + lead, avoid=long_t)
            if extra:
                plan["slots"] = sorted(plan["slots"] + [{"at": t.isoformat(), "type": "short", "status": "open"}
                                                        for t in extra], key=lambda s: s["at"])
                self.state.save_day(date_s, plan)
                self.report.note(f"{date_s}: added {len(extra)} Short slot(s) to match shorts_per_day")
        ncfg = self.cfg.get("news", {})
        if ((ncfg.get("extra_long") or ncfg.get("alert")) and not self.args.dry_run and not any(s.get("news") for s in plan["slots"])
                and day == datetime.now(ZoneInfo(sc["timezone"])).date()):
            self.plan_news_long(day, plan, sc, ncfg)
        if self.channel_uploads is not None and not self.args.dry_run:
            tz = ZoneInfo(sc["timezone"])
            mine = [u for u in self.channel_uploads if u["at"].astimezone(tz).date() == day]
            # the guard decides afresh each run: future Short slots it skipped before get another look
            # (they are skipped again below if the day really is full or another upload is too close)
            for s in plan["slots"]:
                if (s["type"] == "short" and s["status"] == "skipped" and not s.get("video_id")
                        and datetime.fromisoformat(s["at"]) >= datetime.now(timezone.utc) + lead):
                    s["status"] = "open"
            notes = adopt_uploads(plan["slots"], mine, int(sc["shorts_per_day"]), 15, random.Random())
            if notes:
                self.state.save_day(date_s, plan)
                self.report.note(f"{date_s}: duplicate guard — {len(notes)} slot(s) adjusted ({notes[0]})")
        # the episode first: it is the day's most important upload
        rank = {"long": 0, "short": 1, "members": 2, "post": 3}   # picture posts last: never take a Short's place
        order = sorted(plan["slots"], key=lambda s: (rank.get(s["type"], 3), s["at"]))
        for slot in order:
            if slot["status"] != "open":
                continue
            if slot["type"] == "post" and not sc.get("posts_per_day"):   # picture Shorts switched off
                slot["status"] = "skipped"
                self.state.save_day(date_s, plan)
                continue
            if datetime.fromisoformat(slot["at"]) < datetime.now(timezone.utc) + lead:
                slot["status"] = "missed"
                self.state.save_day(date_s, plan)
                continue
            if self.over_budget() or (self.args.max is not None and self.made >= self.args.max):
                return False
            if slot["type"] == "long":
                ok = self.fill_long(day, slot, plan)
            elif slot["type"] == "post":
                ok = self.fill_post(day, slot, plan)
            elif slot["type"] == "members":
                ok = self._fill_short(day, slot, plan, True, members=True)
                ok = True if ok is None else ok
            else:
                ok = self.fill_short(day, slot, plan)
            if not ok:
                return False
        return True

    def run(self) -> int:
        try:
            self.connect()
        except AuthError as e:
            self.report.problem(str(e))
            self.report.publish(self.secrets.telegram_bot_token, self.secrets.telegram_chat_id)
            return 1
        self.sync()
        self.reconcile()
        if getattr(self.args, "force_episode", False):
            # test mode: build one episode for tomorrow's evening slot without touching the real plan
            day = target_days(datetime.now(timezone.utc), self.cfg["schedule"])[-1]
            slot = {"at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "type": "long",
                    "status": "open"}
            try:
                self.fill_long(day, slot, {"date": day.isoformat(), "slots": [slot]})
            finally:
                self.brain.close()
            self.report.publish(self.secrets.telegram_bot_token, self.secrets.telegram_chat_id)
            return 0
        try:
            self.ensure_short_supply()
        except Exception as e:  # noqa: BLE001 — a supply check must never stop the day's uploads
            log.warning("Shorts supply check failed: %s", e)
        try:
            for day in target_days(datetime.now(timezone.utc), self.cfg["schedule"]):
                if not self.fill_day(day):
                    break
        except AIUnavailable as e:
            self.report.problem(f"The AI writer could not start: {e}")
        finally:
            self.brain.close()
        if self.studio.video_failures:
            self.report.problem(f"{self.studio.video_failures} video download(s) failed — those days used photos "
                                "instead. If this keeps happening, add the optional YT_DOWNLOAD_COOKIES secret "
                                "(see SETUP.md).")
        self.state.checkpoint("run finished")
        self._health()
        self.report.publish(self.secrets.telegram_bot_token, self.secrets.telegram_chat_id)
        return 0

    def _health(self) -> None:
        left_shorts = len(self.studio.short_candidates(self.events, self.usage))
        left_long = sum(1 for k in self.events if not self.usage.get(k, {}).get("long"))
        self.report.note(f"Library: {left_shorts} events still available for Shorts, {left_long} for episodes")
        if self.args.dry_run:
            return
        today = target_days(datetime.now(timezone.utc), self.cfg["schedule"])[0]
        plan = self.state.load_day(today.isoformat()) or {"slots": []}
        filled = sum(1 for s in plan["slots"] if s["status"] == "scheduled")
        self.report.note(f"Today ({today}): {filled}/{len(plan['slots'])} uploads scheduled")
        open_left = sum(1 for s in plan["slots"] if s["status"] == "open")
        if open_left:
            self.report.problem(f"Today still has {open_left} empty slot(s)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="render into work/out without uploading")
    ap.add_argument("-n", "--max", type=int, default=None, help="make at most N videos this run")
    ap.add_argument("--force-episode", action="store_true", help="with --dry-run: build one test episode")
    args = ap.parse_args()
    setup_logging()
    sys.exit(Runner(args).run())


if __name__ == "__main__":
    main()
