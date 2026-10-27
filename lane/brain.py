"""The AI writer/editor — an open-source vision model running on the same machine (llama.cpp + Qwen3.5).

No account, no API key, nothing that can ever be billed: the model files are downloaded once from Hugging Face
(public, Apache-2.0) and cached; each run starts a private llama.cpp server on a free local port and stops it
when the run ends. Answers are grammar-constrained JSON, so they always parse.

Facts come from the official hamdan.ae article for each event (Arabic); the model writes English narration
from them and is told never to add facts that are not in the article, the title or the pictures.
"""
from __future__ import annotations

import atexit
import base64
import json
import logging
import os
import platform
import random
import re
import socket
import subprocess
import tarfile
import time
import zipfile
from pathlib import Path

import requests

from .config import ROOT
from .media import FFMPEG, run

log = logging.getLogger("lane.brain")

MODELS_DIR = Path(os.environ.get("LANE_MODELS_DIR", ROOT / "models"))
MOODS = ["epic", "modern", "arabian", "elegant"]

SYSTEM = """You write for "The Luxury Lane", a YouTube channel for fans of His Highness Sheikh Hamdan bin Mohammed bin Rashid Al Maktoum: Prince Hamdan, Crown Prince of Dubai, known to millions as "Fazza".
Fans do not watch for government news. They watch for HIM: the man, his character, his family, his passions
(horses, poetry, adventure, fitness), his warmth with people, his presence.

VOICE
- An admiring storyteller talking to fans. Emotional, intimate, cinematic. Make the viewer feel close to him.
- Turn every official moment into a human moment about Fazza: what he did, how he behaved, what it shows about him.
- Hooks create curiosity about HIM. Short, vivid sentences. No policy jargon, no budgets, no lists of officials.
- Call him "Prince Hamdan", "Fazza", "Sheikh Hamdan" or "the Crown Prince". Respectful always: no gossip,
  no rumours, no wealth estimates, no politics.

TRUTH (non-negotiable)
- Use only: the official article, the event title, what is visible in the pictures, and the verified facts given.
- Never invent numbers, names, places, dates, quotes or private details. Keep numbers exactly as given.
- Keep proper names exactly as written in the source; if unsure of an English spelling, describe instead.
- Titles may create curiosity but the video must truly deliver what the title promises.
- The article may be in Arabic: translate faithfully."""

SHORT_SCREEN = """Official event: "{title}" ({date}).
Article summary (official, may be Arabic): \"\"\"{brief}\"\"\"
The images are {n} moments from this event's footage and photos.

Score (0-10) how strong a vertical Short (under a minute) this material makes for Fazza's fans:
- 9-10: Sheikh Hamdan clearly visible in a striking, emotional or spectacular moment (sport, horses, falcons,
  travel, landmarks, warm moments with people, grand venues).
- 7-8: Sheikh Hamdan visible at an impressive project, visit or ceremony with strong visuals.
- 5-6: formal meeting or office scene with little visual variety.
- 0-4: condolences, mourning, funerals, mostly text or graphics, very low quality.
mood = music that fits: epic (grand, heroic), modern (sleek, tech, fast), arabian (heritage, desert, horses,
falcons, poetry), elegant (warm, people, calm luxury)."""

SHORT_PLAN = """Official moment: "{title}" ({date}).
Official article (may be Arabic): \"\"\"{article}\"\"\"
Verified facts you may weave in (optional, at most one, word for word true): {facts}

Each image below is labelled "Item N". Prince Hamdan is on screen in every item.
{labels}

Write a {target:.0f}-second Short for Fazza's fans. The script must be {lo}-{hi} words.
- Line one is a scroll-stopping hook about HIM, under 8 words (curiosity or emotion). Never open with
  "Did you know", "In this video", "Meet", "This is", "Here's" or a question.
- Tell ONE human moment about him. End with a line that makes fans proud or loops back to the hook.
- shots: 3-6 items in story order; the first shows him most clearly and closest.
- layout: "fill" (vertical crop on him) unless the item is a wide group shot, then "frame".
- hook_text: 2-5 words on screen for the first 2 seconds, e.g. "FAZZA DID THIS", "WATCH HIS REACTION".
- title: the style of the most-viewed fan Shorts. Begins with "Prince Hamdan" or "Fazza", then a CONCRETE thing
  from this clip (an action, a place, a person, a number) plus emotion or curiosity. Max 60 characters, one emoji,
  no hashtags, TRUE to the clip. Good: "Prince Hamdan Stopped Everything For These Students 🤍",
  "Fazza Inside Dubai's New Air Taxi Station 🚁", "Prince Hamdan Meets The Boring Company Boss 🔥".
  Bad (too vague): "Fazza Builds Trust", "Prince Hamdan Brings Warmth".
- score: 0-10 how much fans will love this (him clearly visible, emotion, action, warmth = high;
  him sitting in a meeting = low).
- mood: epic (grand), modern (sleek), arabian (heritage, desert, horses, poetry), elegant (warm, family, people).
- description: two short sentences. tags: 8-12 search tags. emphasis: 2-5 words from the script.

Hooks and titles used recently: do NOT reuse their wording or structure:
{recent}"""

CHAPTER_PROMPT = """Write chapter {num} of a documentary for Fazza's fans. Episode theme: "{angle}".
This chapter tells one real moment: "{title}" ({date}).
Official article (Arabic: translate faithfully, use only its facts):
\"\"\"{article}\"\"\"
Verified facts about Prince Hamdan you may weave in (only these, word for word true):
{facts}

Return:
- chapter_title: 2-5 evocative words of your own about HIM in this moment (not a generic phrase).
- place_line: short location/date line for the lower third, e.g. "DUBAI · 04 OCTOBER 2026" (use the date above).
- narration: {lo}-{hi} words. Open on Prince Hamdan in the moment, show his character through what he does,
  connect it to the episode theme (use one verified fact if it fits), end with a line that carries viewers on.
  Focus on HIM, not on officials or budgets. Plain spoken English, no lists, no headings, no stage directions."""

EPISODE_PROMPT = """You are packaging today's documentary episode for The Luxury Lane, for Fazza's fans.
Theme: {theme}
Title idea: {title_hint}
Chapters:
{chapters}

Return:
- episode_title: 3-7 words, cinematic (shown on screen in the opening).
- hook: 60-80 words over a fast montage. Open with the most intriguing thing about Prince Hamdan in this episode,
  promise what fans will see, end with "stay until the end" or similar. No "welcome to", no "in this video".
- outro: 30-45 words that ties the chapters together and invites fans to subscribe for more of Prince Hamdan.
- youtube_title: max 70 characters, in the proven style of the biggest Prince Hamdan videos, for example
  "Inside Prince Hamdan's ...", "The Untold Story of ...", "Prince Hamdan's Most ... Moments",
  "Why Millions Love Fazza ...". It must be TRUE to this episode and contain "Prince Hamdan" or "Fazza".
  At most one emoji.
- description: 3-4 sentences, naturally using Prince Hamdan, Fazza, Sheikh Hamdan, Crown Prince of Dubai.
- tags: 12-15 search tags.
- thumbnail_text: 2-4 huge words for the thumbnail, uppercase, that make a fan NEED to click, in the style of the
  most-clicked Fazza thumbnails: a curiosity gap or a strong emotion, never a summary. Examples: "HIS REAL LIFE",
  "NOBODY SAW THIS", "THE SIDE HE HIDES", "WHY HE DID THIS", "HIS BIGGEST PASSION", "WHO TAUGHT HIM?".
  It must stay TRUE to this episode (no invented drama, no wives, no rumours) and must not repeat the title.
- thumbnail_tag: 1-2 words for a small red badge, uppercase, true to the episode, e.g. "RARE FOOTAGE",
  "UNSEEN", "EXCLUSIVE", "HIS STORY"."""


def _short_screen_schema() -> dict:
    return {"type": "object", "properties": {
        "what_happens": {"type": "string"}, "score": {"type": "integer", "minimum": 0, "maximum": 10},
        "reject_reason": {"type": "string"}, "mood": {"type": "string", "enum": MOODS}},
        "required": ["what_happens", "score", "reject_reason", "mood"]}


def _short_plan_schema(n: int) -> dict:
    return {"type": "object", "properties": {
        "shots": {"type": "array", "minItems": min(3, n), "maxItems": min(6, n), "items": {"type": "object", "properties": {
            "item": {"type": "integer", "minimum": 1, "maximum": n},
            "focus_x": {"type": "number", "minimum": 0, "maximum": 1}}, "required": ["item", "focus_x"]}},
        "layout": {"type": "string", "enum": ["fill", "frame"]},
        "score": {"type": "integer", "minimum": 0, "maximum": 10},
        "mood": {"type": "string", "enum": MOODS},
        "hook_text": {"type": "string"}, "script": {"type": "string"},
        "emphasis": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "title": {"type": "string"}, "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 12}},
        "required": ["shots", "layout", "score", "mood", "hook_text", "script", "emphasis", "title", "description",
                     "tags"]}


CHAPTER_SCHEMA = {"type": "object", "properties": {
    "chapter_title": {"type": "string"}, "place_line": {"type": "string"}, "narration": {"type": "string"}},
    "required": ["chapter_title", "place_line", "narration"]}

EPISODE_SCHEMA = {"type": "object", "properties": {
    "episode_title": {"type": "string"}, "hook": {"type": "string"}, "outro": {"type": "string"},
    "youtube_title": {"type": "string"}, "description": {"type": "string"},
    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 15}, "thumbnail_text": {"type": "string"},
    "thumbnail_tag": {"type": "string"}},
    "required": ["episode_title", "hook", "outro", "youtube_title", "description", "tags", "thumbnail_text",
                 "thumbnail_tag"]}


class BrainError(Exception):
    """The answer for one item was unusable."""


class AIUnavailable(BrainError):
    """The local model could not be started — stop for now, nothing is wrong with the content."""


# ---- the local model server ---------------------------------------------------------------------------------
def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(4):
        try:
            with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "luxury-lane"}) as r:
                r.raise_for_status()
                with open(part, "wb") as f:
                    for chunk in r.iter_content(8 << 20):
                        f.write(chunk)
            os.replace(part, dest)
            return
        except requests.RequestException as e:
            log.warning("download %s failed (%s), retrying", url, e)
            time.sleep(10 * (attempt + 1))
    raise AIUnavailable(f"could not download {url}")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalModel:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.port = 0
        self.proc: subprocess.Popen | None = None
        atexit.register(self.stop)

    def _server_binary(self) -> Path:
        if os.environ.get("LLAMA_SERVER"):
            return Path(os.environ["LLAMA_SERVER"])
        ver = self.cfg["llama_cpp_version"]
        windows = platform.system() == "Windows"
        name = "llama-server.exe" if windows else "llama-server"
        bin_dir = MODELS_DIR / f"llama-{ver}"
        found = next(bin_dir.rglob(name), None) if bin_dir.exists() else None
        if found:
            return found
        asset = f"llama-{ver}-bin-win-cpu-x64.zip" if windows else f"llama-{ver}-bin-ubuntu-x64.tar.gz"
        archive = MODELS_DIR / asset
        _download(f"https://github.com/ggml-org/llama.cpp/releases/download/{ver}/{asset}", archive)
        if asset.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                z.extractall(bin_dir)
        else:
            with tarfile.open(archive) as t:
                t.extractall(bin_dir)
        archive.unlink()
        found = next(bin_dir.rglob(name), None)
        if not found:
            raise AIUnavailable(f"{name} not found in {asset}")
        for f in found.parent.iterdir():
            f.chmod(0o755)
        return found

    def _model_file(self, filename: str) -> Path:
        dest = MODELS_DIR / self.cfg["model_repo"].replace("/", "__") / filename
        if not dest.exists():
            log.info("Downloading AI model file %s (one time, then cached)", filename)
            _download(f"https://huggingface.co/{self.cfg['model_repo']}/resolve/main/{filename}", dest)
        return dest

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        server = self._server_binary()
        model, mmproj = self._model_file(self.cfg["model_file"]), self._model_file(self.cfg["mmproj_file"])
        threads = int(self.cfg.get("threads") or os.cpu_count() or 4)
        self.port = _free_port()
        cmd = [str(server), "-m", str(model), "--mmproj", str(mmproj), "--host", "127.0.0.1",
               "--port", str(self.port), "-c", str(self.cfg.get("context", 12288)), "-t", str(threads),
               "-np", "1", "--reasoning-budget", "0", "--image-max-tokens", str(self.cfg.get("image_tokens", 280))]
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        log_file = open(MODELS_DIR / "llama-server.log", "ab")
        env = dict(os.environ)
        if platform.system() != "Windows":
            env["LD_LIBRARY_PATH"] = f"{server.parent}:{env.get('LD_LIBRARY_PATH', '')}"
        self.proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, cwd=server.parent, env=env)
        deadline = time.time() + 900
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise AIUnavailable(f"AI server exited (code {self.proc.returncode}); see models/llama-server.log")
            try:
                if requests.get(f"http://127.0.0.1:{self.port}/health", timeout=5).status_code == 200:
                    log.info("Local AI ready (%s) on port %d", self.cfg["model_file"], self.port)
                    return
            except requests.RequestException:
                pass
            time.sleep(2)
        self.stop()
        raise AIUnavailable("AI server did not start within 15 minutes")

    def chat(self, parts: list[dict], *, schema: dict | None, max_tokens: int, temperature: float) -> str:
        self.start()
        body = {
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": parts}],
            "max_tokens": max_tokens, "temperature": temperature, "top_p": 0.9, "repeat_penalty": 1.05,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if schema:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
        last = None
        for _ in range(2):
            try:
                r = requests.post(f"http://127.0.0.1:{self.port}/v1/chat/completions", json=body, timeout=2400)
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
            except (requests.RequestException, KeyError, ValueError) as e:
                last = e
                if self.proc and self.proc.poll() is not None:  # crashed: restart once
                    self.proc = None
                    self.start()
        raise AIUnavailable(f"local AI request failed: {last}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


# ---- helpers ------------------------------------------------------------------------------------------------
def image_b64(path_or_video: Path, t: float | None = None, size: int = 448) -> str:
    """JPEG (base64) of a photo, or of the frame at `t` seconds of a video, longest side `size` px."""
    vf = f"scale='if(gt(iw,ih),{size},-2)':'if(gt(iw,ih),-2,{size})'"
    cmd = [FFMPEG, "-v", "error"]
    if t is not None:
        cmd += ["-ss", f"{t:.2f}"]
    cmd += ["-i", str(path_or_video), "-frames:v", "1", "-vf", vf, "-q:v", "4", "-f", "mjpeg", "-"]
    jpg = run(cmd).stdout
    if not jpg:
        raise BrainError(f"could not read an image from {path_or_video}")
    return base64.b64encode(jpg).decode()


def _img(b64: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def clean(s) -> str:
    """Single-spaced plain text: no markdown emphasis, no stray symbols the narrator would read out."""
    s = re.sub(r"[*_#`~]+", "", str(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def _words(s: str) -> int:
    return len(s.split())


class Brain:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.llm = LocalModel(cfg)

    def close(self) -> None:
        self.llm.stop()

    def _json(self, parts, schema, max_tokens, temperature) -> dict:
        text = self.llm.chat(parts, schema=schema, max_tokens=max_tokens, temperature=temperature)
        try:
            return json.loads(text[text.find("{"): text.rfind("}") + 1])
        except ValueError as e:
            raise BrainError(f"model returned invalid JSON: {text[:200]}") from e

    # ---- Shorts -----------------------------------------------------------------------------------------
    def plan_short(self, items: list[dict], *, title: str, date: str, brief: str, article: str, target: float,
                   recent: list[str], height_gt_width: bool, facts: list[str] | None = None) -> dict:
        """items: [{"kind": "video", "start", "end", "b64", "focus"} | {"kind": "photo", "path", "b64", "focus"}],
        all already checked to show Prince Hamdan. One AI call writes and scores the Short."""
        if len(items) < 3:
            raise BrainError("not enough scenes/photos of him for a Short")
        lo, hi = int(target * 2.3), int(target * 2.7)
        labels = "\n".join(
            f"Item {i + 1}: " + (f"video scene {it['start']:.1f}-{it['end']:.1f}s" if it["kind"] == "video" else "photo")
            for i, it in enumerate(items))
        parts = [{"type": "text", "text": SHORT_PLAN.format(
            title=title, date=date, article=article[:1800] or brief or "(none)", labels=labels, target=target, lo=lo,
            hi=hi, facts="; ".join(facts or []) or "(none)",
            recent="\n".join(f"- {r}" for r in recent[-30:]) or "- (none yet)")}]
        for i, it in enumerate(items):
            parts += [{"type": "text", "text": f"Item {i + 1}:"}, _img(it["b64"])]
        d = self._json(parts, _short_plan_schema(len(items)), 900, 0.85)
        score = int(d.get("score", 0))
        result = {"score": score, "suitable": score >= int(self.cfg.get("min_score", 6)),
                  "reject_reason": "" if score >= int(self.cfg.get("min_score", 6)) else "fans would not love it",
                  "mood": d.get("mood") if d.get("mood") in MOODS else "epic"}
        if not result["suitable"]:
            return result
        shots, used = [], set()
        for s in d.get("shots") or []:
            k = int(s.get("item", 0)) - 1
            if 0 <= k < len(items) and k not in used:
                used.add(k)
                it = items[k]
                fx = it.get("focus", min(1.0, max(0.0, float(s.get("focus_x", 0.5)))))
                if it["kind"] == "video":
                    shots.append({"kind": "video", "start": it["start"] + 0.1,
                                  "end": min(it["end"] - 0.05, it["start"] + 4.5), "focus_x": fx,
                                  "hard_end": it["end"] - 0.05})
                else:
                    shots.append({"kind": "photo", "path": it["path"], "seconds": 3.0, "focus_x": fx})
        if len(shots) < 3:
            raise BrainError("model picked too few usable items")
        script = clean(d.get("script"))
        if _words(script) > hi + 6:
            script = self.shorten(script, hi)
        title_out = clean(d.get("title")).replace("#", "")
        if not script or not title_out:
            raise BrainError("incomplete plan from model")
        result.update(
            shots=shots,
            layout=d.get("layout") if d.get("layout") in ("fill", "frame") else ("fill" if height_gt_width else "frame"),
            hook_text=clean(d.get("hook_text"))[:40], script=script,
            emphasis=[clean(w).lower() for w in d.get("emphasis") or [] if clean(w)][:5],
            title=title_out[:90], description=clean(d.get("description")),
            tags=[clean(t)[:60] for t in d.get("tags") or [] if clean(t)][:12])
        return result

    def voiceover(self, facts: list[str], article: str, title: str, words: int) -> str:
        """A short narrated commentary for a footage-only Short: only the given facts / article, nothing invented.
        Falls back to the first fact word for word (always true) if the model is unavailable."""
        facts = [f for f in facts if f]
        plain = facts[0] if facts else ""
        if not facts and not article:
            return ""
        prompt = (f"Write the spoken voice-over for a {words + 6}-second vertical Short about Prince Hamdan (Fazza) "
                  f"of Dubai, in plain spoken English, {words - 6}-{words} words, one flowing paragraph.\n"
                  f"Use ONLY the facts below (and the article, if given); add nothing, guess nothing, no numbers or "
                  f"dates that are not written here. First line is a hook about HIM under 8 words (never 'Did you "
                  f"know'). End with a line that makes fans proud.\n"
                  + (f"Moment on screen: {title}\nArticle: \"\"\"{article[:1500]}\"\"\"\n" if article else "")
                  + "Facts:\n" + "\n".join(f"- {f}" for f in facts) + "\nReply with the narration only.")
        try:
            out = clean(self.llm.chat([{"type": "text", "text": prompt}], schema=None, max_tokens=300,
                                      temperature=0.6)).strip('"')
        except Exception:  # noqa: BLE001 - fallback below is a verified fact
            return plain
        return out if (words * 0.6) <= _words(out) <= words + 10 else (plain if not article else "")

    def lengthen(self, script: str, words: int) -> str:
        """Owner rule: a narrated Short talks at least 8 s. Stretch a too-short narration to about `words` words."""
        prompt = (f"Rewrite this short narration so it is about {words} words. Keep the opening hook and every fact exactly "
                  f"as stated; add NO new facts, names, numbers or dates. Lengthen only with vivid, true-to-the-footage "
                  f"description of the moment, the feeling and what it shows about him, and end with a line that makes "
                  f"fans proud. Same voice. Reply with the narration only.\n\n"
                  f"\"\"\"{script}\"\"\"")
        try:
            out = clean(self.llm.chat([{"type": "text", "text": prompt}], schema=None, max_tokens=400,
                                      temperature=0.5)).strip('"')
        except Exception:  # noqa: BLE001 - the original script stays
            return script
        return out if words * 0.8 <= _words(out) <= words + 10 else script

    def shorten(self, script: str, words: int) -> str:
        prompt = (f"Rewrite this narration in at most {words} words. Keep the opening hook, keep every fact exactly "
                  f"as stated (add none), keep the ending, same voice. Reply with the narration only.\n\n"
                  f"\"\"\"{script}\"\"\"")
        out = clean(self.llm.chat([{"type": "text", "text": prompt}], schema=None, max_tokens=400,
                                  temperature=0.4)).strip('"')
        return out if 5 <= _words(out) <= words + 8 else " ".join(script.split()[:words])

    # ---- long-form --------------------------------------------------------------------------------------
    def write_chapter(self, num: int, *, title: str, date: str, article: str, words: int, angle: str = "",
                      facts: list[str] | None = None) -> dict:
        lo, hi = int(words * 0.85), int(words * 1.1)
        prompt = CHAPTER_PROMPT.format(num=num, title=title, date=date, article=article[:3500] or title, lo=lo, hi=hi,
                                       angle=angle or "Prince Hamdan's moments",
                                       facts="\n".join(f"- {f}" for f in facts or []) or "- (none)")
        for attempt in range(2):
            d = self._json([{"type": "text", "text": prompt}], CHAPTER_SCHEMA, 1100, 0.75)
            narration = clean(d.get("narration"))
            if _words(narration) >= lo * 0.7:
                break
        if _words(narration) > hi + 8:
            narration = self.shorten(narration, hi)
        if _words(narration) < 40:
            raise BrainError(f"chapter {num} narration too short")
        return {"chapter_title": clean(d.get("chapter_title"))[:40] or title[:40],
                "place_line": clean(d.get("place_line")).upper()[:60], "narration": narration}

    def describe_clip(self, frames: list[str], caption: str) -> dict:
        """What a video shows, from a few frames (base64 JPEG) and its caption (often Arabic):
        {"about": one English sentence, "tags": [lowercase words]} — used to match story sentences to clips."""
        parts = [{"type": "text", "text": CLIP_DESCRIBE.format(caption=clean(caption)[:900] or "(no caption)")}]
        for i, b64 in enumerate(frames[:4]):
            parts += [{"type": "text", "text": f"Frame {i + 1}:"}, _img(b64)]
        d = self._json(parts, CLIP_DESCRIBE_SCHEMA, 300, 0.2)
        tags = []
        for t in d.get("tags") or []:
            t = re.sub(r"[^a-z ]", "", clean(t).lower()).strip()
            if 2 < len(t) < 30 and t not in tags:
                tags.append(t)
        return {"about": clean(d.get("about"))[:200], "tags": tags[:12], "real": bool(d.get("real_moment", True))}

    def write_episode(self, theme: str, chapters: list[dict], title_hint: str = "") -> dict:
        lines = "\n".join(f"{i + 1}. {c['chapter_title']} — {c['event_title']} ({c['date']}): "
                          f"{' '.join(c['narration'].split()[:45])}..." for i, c in enumerate(chapters))
        d = self._json([{"type": "text", "text": EPISODE_PROMPT.format(theme=theme, chapters=lines,
                                                                        title_hint=title_hint or "(your choice)")}],
                       EPISODE_SCHEMA, 900, 0.8)
        out = {k: clean(d.get(k)) for k in ("episode_title", "hook", "outro", "youtube_title", "description",
                                            "thumbnail_text", "thumbnail_tag")}
        out["thumbnail_tag"] = " ".join(out["thumbnail_tag"].upper().split()[:2])[:16]
        out["tags"] = [clean(t)[:60] for t in d.get("tags") or [] if clean(t)][:15]
        if _words(out["hook"]) < 20 or not out["youtube_title"]:
            raise BrainError("episode package incomplete")
        out["youtube_title"] = out["youtube_title"].replace("#", "")[:95]
        return out


# ---- automatic story episodes (when the hand-written story bank is empty) -----------------------------------
STORY_FACTS = """Read these official sources about Prince Hamdan (Fazza) and list the facts a storyteller could use.
Topic: {label}

OFFICIAL SOURCES (may be in Arabic — translate faithfully):
{sources}

Return "facts": 14-22 short, separate, concrete facts (one idea each): who, what, where, when, what he said or did,
how people reacted. Copy names of people and places exactly as the sources spell them. Prefer human details
(what he did, said, saw, who he met) over policy. No opinions, nothing that is not in the sources."""

STORY_OUTLINE = """Plan a YouTube story episode for Fazza's fans, in the style of the most-watched royal story channels.
Topic: {label}

NUMBERED FACTS (the only facts you may use):
{facts}

Return:
- title: max 90 characters, a curiosity-driven question or reveal the story truly answers, containing "Fazza" or
  "Prince Hamdan" (styles that work: "What Prince Hamdan Did Next...", "Why Fazza...", "The Day Fazza...").
- thumbnail_text: 2-4 huge words, uppercase, curiosity gap. thumbnail_tag: 1-2 words for a red badge.
- questions: 3 short burning questions the episode answers (for the opening hook).
- beats: exactly 7 parts in story order. Each: "beat" = one sentence on what that part tells, and "facts" = the
  numbers of 2-3 facts it uses. EVERY fact is used by exactly one beat, so no part repeats another.
- description: 3 sentences for YouTube, naturally using Prince Hamdan, Fazza, Sheikh Hamdan, Crown Prince of Dubai.
- tags: 12 search tags. keywords: 3-5 English words naming places, people or things in this story."""

STORY_STYLE = """STYLE (match it exactly):
- Short spoken sentences, 8 to 16 words, one idea each. Plain words a 12-year-old understands.
- Open the part with a concrete moment, a name or a number. Never open with a general statement.
- Show, do not tell: say what he did, said, held or where he stood. Do not call him "humble", "a true leader" or "inspiring".
- End the part with a small open question or a turn that pulls the viewer into the next part.
- Never use these words: testament, legacy, journey, tapestry, beacon, remarkable, "it is worth noting", "in conclusion".
EXAMPLE OF THE VOICE (a different story):
"On the thirtieth of September, a flydubai jet took off with more than one hundred and seventy people on board. Within hours, the whole world was asking how close it had come to disaster. The danger came from the seat right next to the captain. What happened behind that locked door has shocked pilots all over the world."
"""

STORY_PART = """Write part {num} of 9 of a narrated YouTube story about Prince Hamdan (Fazza) for his fans.
Episode title: "{title}"
{role}

FACTS FOR THIS PART (use only these; copy names exactly):
{facts}

GENERAL BACKGROUND YOU MAY MENTION BRIEFLY:
{background}

The previous part ended with: "{last}"

{style}
Write about {words} words of spoken narration: warm, emotional, vivid, like a storyteller talking to a friend. Do not repeat anything from earlier parts. Never mention "sources", "articles" or "reports". Never invent
names, numbers, dates or quotes. Write numbers and years in digits. No headings, lists, stage directions or emojis."""

ROLES = {
    "hook": ("This is the OPENING HOOK. Start with the most intriguing moment, then ask these questions: {questions}. "
             "Say viewers should stay until the end to find out."),
    "beat": "This part tells: {beat}",
    "outro": ("This is the ENDING. Reflect, with emotion, on what this story shows about who Fazza is as a person. "
              "Do not list the facts again."),
}
THUMB_WORDS = ["WHAT HE DID NEXT", "NOBODY EXPECTED THIS", "THE REAL STORY", "WATCH WHAT HAPPENED", "HE DID THIS",
               "THE SIDE YOU NEVER SEE", "WHY HE DID IT"]
HOOK_END = " Let's begin."      # the one request to comment and subscribe comes at the very end, never twice
OUTRO_END = (" What do you think about this side of Fazza? Write it in the comments, we read every one. And if you "
             "enjoyed this story, like the video and subscribe to The Luxury Lane for more true stories about "
             "Prince Hamdan. See you next time.")

CLIP_DESCRIBE = """These frames come from one short video posted on Instagram about Prince Hamdan (Fazza), Crown Prince
of Dubai. Its caption: "{caption}"

Describe what the video shows, for an editor who must match it to a narrated sentence.
Return "about": one plain English sentence (what happens, where, with whom or with which animals or things), and
"tags": 6-12 single lowercase English words a narrator would use for this moment (people like son, daughter, twins,
father, soldiers, children; animals like falcon, camel, horse, lion; places and things like desert, sea, snow,
mountain, car, plane, helicopter, majlis, hospital; actions like riding, diving, skydiving, hiking, cycling, running,
hunting, praying, visiting). Only what is really visible or stated in the caption; no guesses about names.
"real_moment": true if this is real filmed footage of a moment; false if it is mainly text, a graphic, a poem or
greeting card, a slideshow of still photos, or an advertisement."""
CLIP_DESCRIBE_SCHEMA = {"type": "object", "properties": {
    "about": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
    "real_moment": {"type": "boolean"}},
    "required": ["about", "tags", "real_moment"]}
STORY_FACTS_SCHEMA = {"type": "object", "properties": {
    "facts": {"type": "array", "items": {"type": "string"}, "minItems": 8, "maxItems": 24}}, "required": ["facts"]}
STORY_OUTLINE_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"}, "thumbnail_text": {"type": "string"}, "thumbnail_tag": {"type": "string"},
    "questions": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
    "beats": {"type": "array", "minItems": 7, "maxItems": 7, "items": {"type": "object", "properties": {
        "beat": {"type": "string"}, "facts": {"type": "array", "items": {"type": "integer"}, "maxItems": 4}},
        "required": ["beat", "facts"]}},
    "description": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
    "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 6}},
    "required": ["title", "thumbnail_text", "thumbnail_tag", "questions", "beats", "description", "tags", "keywords"]}
STORY_PART_SCHEMA = {"type": "object", "properties": {"narration": {"type": "string"}}, "required": ["narration"]}

_NUMBER = re.compile(r"\b\d[\d,.]*\b")
_NAME = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}\b")
_META = re.compile(r"\b(source|sources|article|articles|report says|according to the official)\b", re.I)
_COMMON = set("""The This That These Those His Her Its Our Their He She It We They You What Why How When Where Who
Fazza Prince Hamdan Sheikh Highness Crown Dubai Emirates Emirati Arab United UAE Mohammed Rashid Maktoum Al Bin
Today Then But And Yet Now Here There Every Each One Two Three Even Still Just Only Remember Imagine Look Stay
Minister Deputy Defence President King Queen Sheikha God Allah Ramadan Eid Friday Saturday Sunday Monday Tuesday
Wednesday Thursday January February March April May June July August September October November December Instagram
Burj Khalifa Expo Luxury Lane Islam Muslim Gulf Middle East""".split())


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", clean(text)) if s]


def _novel(sentence: str, earlier: list[set]) -> bool:
    w = set(re.findall(r"[a-z]{4,}", sentence.lower()))
    return not w or all(len(w & e) / len(w) < 0.6 for e in earlier)


_FILLER = re.compile(r"testament|legacy|journey|tapestry|beacon|remarkable|worth noting|in conclusion|little did|"
                     r"not just .{1,40} but|true leader|truly inspiring", re.I)
_GENERIC_OPEN = re.compile(r"^(in|as|throughout|over the years|when it comes|it is|there is|today)\b", re.I)


def _quality(text: str) -> list[str]:
    """Rule-based check of one narrated part against the house style; returns what is wrong (empty = good)."""
    sents = _sentences(text)
    if not sents:
        return ["empty"]
    problems = []
    if sum(len(s.split()) for s in sents) / len(sents) > 19:
        problems.append("sentences too long")
    if _FILLER.search(text):
        problems.append("filler words")
    if _GENERIC_OPEN.search(sents[0]):
        problems.append("generic opening")
    return problems


def _guard(text: str, truth: str, earlier: list[set], used_names: set | None = None,
           allowed: str = "") -> str:
    """Keep only sentences whose numbers and names appear in the sources, that do not talk about 'sources', and that
    do not repeat an earlier sentence (a cheap but effective guard against a small model's habits)."""
    low_truth = truth.lower().replace(",", "")
    keep = []
    for s in _sentences(text):
        if _META.search(s):
            continue
        if any(n.strip(".,").replace(",", "") not in low_truth for n in _NUMBER.findall(s)):
            continue
        if any(n not in _COMMON and n.lower() not in low_truth for n in _NAME.findall(s)):
            continue
        if not _novel(s, earlier):
            continue
        names = {n for n in _NAME.findall(s) if n not in _COMMON}
        if used_names is not None and any(n in used_names and n.lower() not in allowed.lower() for n in names):
            continue          # e.g. "Fujairah" again in every part: a small model's favourite filler
        keep.append(s)
        if used_names is not None:
            used_names |= names
        earlier.append(set(re.findall(r"[a-z]{4,}", s.lower())))
    return " ".join(keep)


def write_story(brain: "Brain", label: str, sources: list[dict], background: list[str]) -> dict:
    """An automatic story episode: {"title", "thumbnail", "description", "tags", "keywords", "parts": [9 texts]}."""
    src = "\n\n".join(f"[{s['date']}] {s['title']}\n{s['text'][:2500]}" for s in sources)[:9000]
    facts = [clean(f) for f in brain._json([{"type": "text", "text": STORY_FACTS.format(label=label, sources=src)}],
                                           STORY_FACTS_SCHEMA, 1400, 0.3).get("facts", []) if clean(f)]
    if len(facts) < 8:
        raise BrainError("too few facts for a story")
    numbered = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(facts))
    plan = brain._json([{"type": "text", "text": STORY_OUTLINE.format(label=label, facts=numbered)}],
                       STORY_OUTLINE_SCHEMA, 1400, 0.7)
    title = clean(plan.get("title")).replace("#", "")[:95]
    beats = (plan.get("beats") or [])[:7]
    if not title or len(beats) < 7 or not re.search(r"fazza|hamdan", title, re.I):
        raise BrainError("story outline incomplete")
    truth = src + "\n" + "\n".join(facts) + "\n" + "\n".join(background)
    bg_pool = list(background[:4])
    roles = [(ROLES["hook"].format(questions=" ".join(clean(q) for q in plan.get("questions", []))), facts[:3])]
    for b in beats:
        picked = [facts[i - 1] for i in b.get("facts", []) if 1 <= i <= len(facts)] or facts[:2]
        roles.append((ROLES["beat"].format(beat=clean(b.get("beat"))), picked))
    roles.append((ROLES["outro"], facts[-2:]))
    parts, part_facts_out, earlier, used_names = [], [], [], set()
    for i, (role, part_facts) in enumerate(roles):
        bg = f"- {bg_pool.pop(0)}" if i in (2, 5) and bg_pool else "(none needed)"
        words = 120 if i in (0, len(roles) - 1) else 175
        text, best, feedback = "", None, ""
        for temp in (0.75, 0.6, 0.45):       # up to three tries: the best part that follows the house style wins
            d = brain._json([{"type": "text", "text": STORY_PART.format(
                num=i + 1, title=title, role=role + feedback, facts="\n".join(f"- {f}" for f in part_facts),
                background=bg, style=STORY_STYLE,
                last=(parts[-1].split(". ")[-1] if parts else "(this is the start)")[:300], words=words)}],
                STORY_PART_SCHEMA, 700, temp)
            trial, trial_names = list(earlier), set(used_names)
            text = _guard(d.get("narration", ""), truth, trial, trial_names, " ".join(part_facts) + " " + bg)
            problems = _quality(text) if _words(text) >= words * 0.65 else ["too short"]
            rank = (len(problems), -_words(text))
            if best is None or rank < best[0]:
                best = (rank, text, trial, trial_names)
            if not problems:
                break
            feedback = f"\nFix these problems from your last try: {', '.join(problems)}."
        _, text, trial, trial_names = best
        earlier, used_names = trial, trial_names
        if _words(text) < 50:
            raise BrainError(f"story part {i + 1} came out too short")
        if i == 0:
            text += HOOK_END
        if i == len(roles) - 1:
            text += OUTRO_END
        parts.append(text)
        part_facts_out.append(list(part_facts))
    if sum(_words(p) for p in parts) < 1250:
        raise BrainError("story too short for an 8-minute episode")
    return {"title": title, "description": clean(plan.get("description")),
            "thumbnail": {"text": random.choice(THUMB_WORDS),
                          "tag": " ".join(clean(plan.get("thumbnail_tag")).upper().split()[:2])[:16]},
            "tags": [clean(t)[:60] for t in plan.get("tags") or [] if clean(t)][:15],
            "keywords": [clean(k) for k in plan.get("keywords") or [] if clean(k)][:6], "parts": parts,
            "part_facts": part_facts_out}
