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
import random
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone

from .audio import credit_line
from .brain import AIUnavailable, Brain
from .config import WORK_DIR, load_config, load_secrets
from .hamdan import Hamdan, Library, SourceError, load_events, save_events
from .report import RunReport, setup_logging
from .schedule import day_slots, long_slot, target_days, top_up
from .state import State
from .studio import Studio
from .youtube import AuthError, QuotaError, YouTube, key_tag

log = logging.getLogger("lane.pipeline")

RUN_BUDGET_MIN = 320       # stop starting new videos after this many minutes (GitHub's job limit is 360)
SOURCE_CREDIT = "Footage and photos: official media of H.H. Sheikh Hamdan bin Mohammed (hamdan.ae)."


def _fmt_ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


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
        self.made = 0

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

    def reconcile(self) -> None:
        """Anything already on the channel counts as used, even if a crashed run never saved it."""
        if not self.yt:
            return
        tags = self.yt.recent_tags()
        posted = self.state.cursors.setdefault("posted_units", [])
        new = [t for t in tags if t not in posted]
        if new:
            posted.extend(new)
            self.report.note(f"Reconciled {len(new)} upload(s) already on the channel")

    def _recent_lines(self) -> list[str]:
        return self.state.cursors.get("recent_hooks", [])[-40:]

    # ---- one upload ------------------------------------------------------------------------------------
    def _upload(self, path, *, title, description, tags, publish_at, thumbnail=None) -> str | None:
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
        when = publish_at if self.cfg["youtube"].get("privacy", "scheduled") == "scheduled" else None
        vid = self.yt.upload(path, title=title, description=description, tags=tags,
                             category_id=self.cfg["youtube"]["category_id"], publish_at=when)
        if thumbnail:
            self.yt.set_thumbnail(vid, thumbnail)
        return vid

    # ---- long-form ------------------------------------------------------------------------------------
    def fill_long(self, day, slot: dict, plan: dict) -> bool:
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

    # ---- Shorts -----------------------------------------------------------------------------------------
    def fill_short(self, day, slot: dict, plan: dict) -> bool:
        at = datetime.fromisoformat(slot["at"])
        for key in self.studio.short_candidates(self.events, self.usage, day.isoformat()):
            if self.over_budget():
                self.report.note("Run time budget reached; remaining slots are filled by the next run")
                return False
            ev = self.events[key]
            seed = random.randint(1, 10**9)
            try:
                item = self.studio.make_short(key, ev, self.usage, self._recent_lines(), seed)
            except AIUnavailable:
                raise
            except Exception as e:  # noqa: BLE001 — one bad event never stops the day
                u = self.usage.setdefault(key, {})
                u["fails"] = u.get("fails", 0) + 1
                self.report.note(f"Skipped {ev['title'][:60]}: {str(e)[:150]}")
                self.state.save()
                continue
            self.state.save()
            if item is None:
                continue
            p = item["plan"]
            hashtags = " ".join(self.cfg["youtube"].get("base_hashtags", []))
            credit = credit_line(item["music"])
            desc = (f"{p['description']}\n\n{SOURCE_CREDIT}\nNarration and edit: {self.cfg['channel']['name']}.\n"
                    + (f"{credit}\n" if credit else "") + f"\n{hashtags}")
            try:
                vid = self._upload(item["path"], title=p["title"], description=desc,
                                   tags=_tags(self.cfg["youtube"].get("base_tags", []), p.get("tags", []), item["unit"]),
                                   publish_at=at)
            except QuotaError as e:
                self.report.problem(f"YouTube upload quota reached ({e})")
                return False
            except Exception as e:  # noqa: BLE001
                self.report.problem(f"Upload failed: {e}")
                return True
            u = self.usage.setdefault(key, {})
            if not self.args.dry_run:
                if item["photo_only"]:
                    u["photo_short"] = True
                u.setdefault("shot_ranges", []).extend(item["ranges"])
                u["last_short"] = max(u.get("last_short", ""), day.isoformat())
                u["shorts_made"] = u.get("shorts_made", 0) + 1
                hooks = self.state.cursors.setdefault("recent_hooks", [])
                hooks.append(f"{p.get('hook_text', '')} | {p['title']}")
                del hooks[:-60]
            slot.update(status="scheduled", video_id=vid, title=p["title"], event=key)
            self.state.save_day(day.isoformat(), plan)
            self.state.checkpoint(f"short {vid} for {slot['at']}")
            self.made += 1
            self.report.note(f"Short {at.strftime('%b %d %H:%M')} — {p['title']}")
            shutil.rmtree(item["work"], ignore_errors=True)
            return True
        self.report.problem("No usable material left for Shorts today")
        return False

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
        shorts_now = [datetime.fromisoformat(s["at"]) for s in plan["slots"] if s["type"] == "short"]
        if len(shorts_now) < int(sc["shorts_per_day"]):
            extra = top_up(day, sc, shorts_now, random.Random(), datetime.now(timezone.utc) + lead)
            if extra:
                plan["slots"] = sorted(plan["slots"] + [{"at": t.isoformat(), "type": "short", "status": "open"}
                                                        for t in extra], key=lambda s: s["at"])
                self.state.save_day(date_s, plan)
                self.report.note(f"{date_s}: added {len(extra)} Short slot(s) to match shorts_per_day")
        # the episode first: it is the day's most important upload
        order = sorted(plan["slots"], key=lambda s: (s["type"] != "long", s["at"]))
        for slot in order:
            if slot["status"] != "open":
                continue
            if datetime.fromisoformat(slot["at"]) < datetime.now(timezone.utc) + lead:
                slot["status"] = "missed"
                self.state.save_day(date_s, plan)
                continue
            if self.over_budget() or (self.args.max is not None and self.made >= self.args.max):
                return False
            ok = self.fill_long(day, slot, plan) if slot["type"] == "long" else self.fill_short(day, slot, plan)
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
