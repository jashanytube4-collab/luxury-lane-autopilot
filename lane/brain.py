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

SYSTEM = """You are the head writer and editor of "The Luxury Lane", a premium YouTube channel loved by fans of \
His Highness Sheikh Hamdan bin Mohammed bin Rashid Al Maktoum — Crown Prince of Dubai, known to millions as \
"Fazza". The audience admires him: his leadership, his vision for Dubai, his horsemanship, poetry and humility.

VOICE
- Warm, confident, cinematic documentary narration. Short, vivid sentences. Present tense for scenes.
- Make the viewer feel they are there. Connect every moment to what it means for Dubai and its people.
- Never cheesy, never gossip, never political opinion, never wealth estimates. Respectful at all times.
- Refer to him as "Sheikh Hamdan", "the Crown Prince of Dubai" or "Fazza".

TRUTH (non-negotiable)
- Use only facts from the official article, the event title or what is visible in the pictures.
- Never invent numbers, names, places, dates or quotes. Keep numbers exactly as the article states them.
- Keep proper names exactly as written in the source (use the English title's spelling when it has one);
  if unsure how a foreign name is spelled in English, describe it instead of guessing.
- The article may be in Arabic: translate faithfully."""

SHORT_SCREEN = """Official event: "{title}" ({date}).
Article summary (official, may be Arabic): \"\"\"{brief}\"\"\"
The images are {n} moments from this event's footage and photos.

Score (0-10) how strong a 15-20 second Short this material makes for Fazza's fans:
- 9-10: Sheikh Hamdan clearly visible in a striking, emotional or spectacular moment (sport, horses, falcons,
  travel, landmarks, warm moments with people, grand venues).
- 7-8: Sheikh Hamdan visible at an impressive project, visit or ceremony with strong visuals.
- 5-6: formal meeting or office scene with little visual variety.
- 0-4: condolences, mourning, funerals, mostly text or graphics, very low quality.
mood = music that fits: epic (grand, heroic), modern (sleek, tech, fast), arabian (heritage, desert, horses,
falcons, poetry), elegant (warm, people, calm luxury)."""

SHORT_PLAN = """Official event: "{title}" ({date}).
Official article (may be Arabic): \"\"\"{article}\"\"\"

Each image below is labelled "Item N". Video items are scenes of moving footage; photo items are stills.
{labels}

Write a {target:.0f}-second Short. The script must be {lo}-{hi} words.
- The first 6 words must stop the scroll: a bold, specific statement the pictures pay off. Never open with
  "Did you know", "In this video", "Meet", "This is", "Here's" or a question.
- One idea. Build to a payoff; end on a line that lands or loops back to the opening.
- shots: 3-6 items in story order; the first is the most arresting. Prefer video items; photos add variety.
- focus_x: where the main subject is horizontally (0 left, 0.5 centre, 1 right).
- layout: "fill" when a vertical crop keeps the subject; "frame" for wide group scenes.
- hook_text: 2-5 words on screen for the first 2 seconds (not the same words as the script opening).
- title: max 70 characters, curiosity-driven but true, Title Case, at most one emoji, no hashtags.
- description: two short sentences of context. tags: 8-12 search tags. emphasis: 2-5 words from the script.

Hooks and titles used recently — do NOT reuse their wording or structure:
{recent}"""

CHAPTER_PROMPT = """Write chapter {num} of a documentary episode about Sheikh Hamdan.
Official event: "{title}" ({date}).
Official article (Arabic — translate faithfully, use only these facts):
\"\"\"{article}\"\"\"

Return:
- chapter_title: 2-5 evocative words of your own that capture this event (not a generic phrase).
- place_line: short location/date line for the lower third, e.g. "DUBAI · 04 OCTOBER 2026" (use the date above).
- narration: {lo}-{hi} words of spoken narration for this chapter. Open with an image or a moment, explain what
  happened and why it matters for Dubai and its people, close with a line that carries the viewer forward.
  Plain spoken English, no lists, no headings, no stage directions."""

EPISODE_PROMPT = """You are packaging today's documentary episode for The Luxury Lane.
Theme: {theme}
Chapters:
{chapters}

Return:
- episode_title: 3-7 words, cinematic (shown on screen in the opening).
- hook: 55-75 words of opening narration over a fast montage. Start with a striking line, promise what the
  viewer will see, end with a line that pulls them in. No "welcome to", no "in this video".
- outro: 30-45 words closing narration that ties the chapters together and invites viewers to subscribe for
  more stories of Sheikh Hamdan and Dubai (one short, sincere sentence for the invitation).
- youtube_title: max 80 characters, compelling and true, Title Case, at most one emoji.
- description: 3-4 sentences summarising the episode for search (no hashtags).
- tags: 12-15 search tags.
- thumbnail_text: 2-4 punchy words for the thumbnail, uppercase."""


def _short_screen_schema() -> dict:
    return {"type": "object", "properties": {
        "what_happens": {"type": "string"}, "score": {"type": "integer", "minimum": 0, "maximum": 10},
        "reject_reason": {"type": "string"}, "mood": {"type": "string", "enum": MOODS}},
        "required": ["what_happens", "score", "reject_reason", "mood"]}


def _short_plan_schema(n: int) -> dict:
    return {"type": "object", "properties": {
        "shots": {"type": "array", "minItems": 3, "maxItems": 6, "items": {"type": "object", "properties": {
            "item": {"type": "integer", "minimum": 1, "maximum": n},
            "focus_x": {"type": "number", "minimum": 0, "maximum": 1}}, "required": ["item", "focus_x"]}},
        "layout": {"type": "string", "enum": ["fill", "frame"]},
        "hook_text": {"type": "string"}, "script": {"type": "string"},
        "emphasis": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "title": {"type": "string"}, "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 12}},
        "required": ["shots", "layout", "hook_text", "script", "emphasis", "title", "description", "tags"]}


CHAPTER_SCHEMA = {"type": "object", "properties": {
    "chapter_title": {"type": "string"}, "place_line": {"type": "string"}, "narration": {"type": "string"}},
    "required": ["chapter_title", "place_line", "narration"]}

EPISODE_SCHEMA = {"type": "object", "properties": {
    "episode_title": {"type": "string"}, "hook": {"type": "string"}, "outro": {"type": "string"},
    "youtube_title": {"type": "string"}, "description": {"type": "string"},
    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 15}, "thumbnail_text": {"type": "string"}},
    "required": ["episode_title", "hook", "outro", "youtube_title", "description", "tags", "thumbnail_text"]}


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
                   recent: list[str], height_gt_width: bool) -> dict:
        """items: [{"kind": "video", "start", "end", "b64"} | {"kind": "photo", "path", "b64"}]."""
        if len(items) < 3:
            raise BrainError("not enough scenes/photos for a Short")
        pick = sorted({round(i * (len(items) - 1) / 3) for i in range(4)})
        parts = [{"type": "text", "text": SHORT_SCREEN.format(title=title, date=date, brief=brief[:700] or "(none)",
                                                              n=len(pick))}]
        parts += [_img(items[i]["b64"]) for i in pick]
        screen = self._json(parts, _short_screen_schema(), 300, 0.2)
        score = int(screen.get("score", 0))
        result = {"score": score, "suitable": score >= int(self.cfg.get("min_score", 7)),
                  "reject_reason": clean(screen.get("reject_reason")), "mood": screen.get("mood", "epic"),
                  "what_happens": clean(screen.get("what_happens"))}
        if not result["suitable"]:
            return result

        lo, hi = int(target * 2.3), int(target * 2.7)
        labels = "\n".join(
            f"Item {i + 1}: " + (f"video scene {it['start']:.1f}-{it['end']:.1f}s" if it["kind"] == "video" else "photo")
            for i, it in enumerate(items))
        parts = [{"type": "text", "text": SHORT_PLAN.format(
            title=title, date=date, article=article[:1800] or brief or "(none)", labels=labels, target=target, lo=lo,
            hi=hi, recent="\n".join(f"- {r}" for r in recent[-25:]) or "- (none yet)")}]
        for i, it in enumerate(items):
            parts += [{"type": "text", "text": f"Item {i + 1}:"}, _img(it["b64"])]
        d = self._json(parts, _short_plan_schema(len(items)), 900, 0.8)

        shots, used = [], set()
        for s in d.get("shots") or []:
            k = int(s.get("item", 0)) - 1
            if 0 <= k < len(items) and k not in used:
                used.add(k)
                it = items[k]
                fx = min(1.0, max(0.0, float(s.get("focus_x", 0.5))))
                if it["kind"] == "video":
                    shots.append({"kind": "video", "start": it["start"] + 0.1,
                                  "end": min(it["end"] - 0.05, it["start"] + 5.0), "focus_x": fx})
                else:
                    shots.append({"kind": "photo", "path": it["path"], "seconds": 3.2, "focus_x": fx})
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

    def shorten(self, script: str, words: int) -> str:
        prompt = (f"Rewrite this narration in at most {words} words. Keep the opening hook, keep every fact exactly "
                  f"as stated (add none), keep the ending, same voice. Reply with the narration only.\n\n"
                  f"\"\"\"{script}\"\"\"")
        out = clean(self.llm.chat([{"type": "text", "text": prompt}], schema=None, max_tokens=400,
                                  temperature=0.4)).strip('"')
        return out if 5 <= _words(out) <= words + 8 else " ".join(script.split()[:words])

    # ---- long-form --------------------------------------------------------------------------------------
    def write_chapter(self, num: int, *, title: str, date: str, article: str, words: int) -> dict:
        lo, hi = int(words * 0.85), int(words * 1.1)
        prompt = CHAPTER_PROMPT.format(num=num, title=title, date=date, article=article[:3500] or title, lo=lo, hi=hi)
        for attempt in range(2):
            d = self._json([{"type": "text", "text": prompt}], CHAPTER_SCHEMA, 1100, 0.75)
            narration = clean(d.get("narration"))
            if _words(narration) >= lo * 0.7:
                break
        if _words(narration) > hi + 20:
            narration = self.shorten(narration, hi)
        if _words(narration) < 40:
            raise BrainError(f"chapter {num} narration too short")
        return {"chapter_title": clean(d.get("chapter_title"))[:40] or title[:40],
                "place_line": clean(d.get("place_line")).upper()[:60], "narration": narration}

    def write_episode(self, theme: str, chapters: list[dict]) -> dict:
        lines = "\n".join(f"{i + 1}. {c['chapter_title']} — {c['event_title']} ({c['date']}): "
                          f"{' '.join(c['narration'].split()[:45])}..." for i, c in enumerate(chapters))
        d = self._json([{"type": "text", "text": EPISODE_PROMPT.format(theme=theme, chapters=lines)}],
                       EPISODE_SCHEMA, 900, 0.8)
        out = {k: clean(d.get(k)) for k in ("episode_title", "hook", "outro", "youtube_title", "description",
                                            "thumbnail_text")}
        out["tags"] = [clean(t)[:60] for t in d.get("tags") or [] if clean(t)][:15]
        if _words(out["hook"]) < 20 or not out["youtube_title"]:
            raise BrainError("episode package incomplete")
        out["youtube_title"] = out["youtube_title"].replace("#", "")[:95]
        return out
