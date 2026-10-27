"""Rocky on Telegram: the owner speaks (voice note) or types, Rocky answers with a voice note and the same text.

    .venv\\Scripts\\pythonw.exe -m tools.rocky_telegram        (started hidden at sign-in; see "Rocky Voice.bat")

- Only the owner's Telegram account is answered (locked on the first /start or message; rocky/telegram_owner.json).
  Everyone else is ignored. The bot token lives only in rocky/telegram_token.txt (never committed, never printed).
- Speech to text: Whisper "medium" on this laptop (free, Hindi + English). Rocky's brain: Claude Code (`claude -p`)
  with rocky/ROCKY.md, in one continuing conversation (send /new to start a fresh one). Voice: Microsoft neural
  voices via edge-tts (Hindi: hi-IN-MadhurNeural, English: en-IN-PrabhatNeural), sent as a Telegram voice note.
- The laptop only makes outgoing requests (long polling); nothing is opened to the internet.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lane.media import FFMPEG  # noqa: E402

HOME = ROOT / "rocky"
TOKEN_FILE = HOME / "telegram_token.txt"
OWNER_FILE = HOME / "telegram_owner.json"
SESSION_FILE = HOME / "telegram_session.json"
WORK = HOME / "telegram_work"
LOG_FILE = HOME / "logs" / "telegram.log"
CLAUDE = Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin" / "claude.exe"
VOICE_HI, VOICE_EN = "hi-IN-MadhurNeural", "en-IN-PrabhatNeural"
WHISPER_HINT = ("Rocky, Fazza, Prince Hamdan, Sheikh Hamdan, The Luxury Lane, YouTube, Shorts, long video, "
                "Captain Smit, James, brand deal, thumbnail, members, Instagram")

VOICE_RULES = """
## You are on Telegram voice right now
The owner is talking to you through Telegram voice notes. Each message was turned into text by speech recognition
(it can mishear names: "fuzzer/fajja" = Fazza, "smith" = Smit). Your reply is read aloud by a voice and also shown
as text, so write it to be SPOKEN:
- No markdown, no tables, no bullet symbols, no emojis, no links, no file paths, no code.
- Short sentences. Normally under about 80 words; give more only when the owner asks for detail.
- Language: owner spoke Hindi -> answer in Hindi written in Devanagari; Hinglish -> Hinglish in Latin letters;
  English -> English. Numbers the way people say them.
- You cannot ask questions with buttons here. If you need a decision or a yes before acting, say so clearly
  ("Should I go ahead? Say yes or no.") and stop; the owner answers in the next voice note.
- Long jobs (renders, uploads): start them, say what you started and when it should be done.
"""

log = logging.getLogger("rocky.telegram")


class Bot:
    def __init__(self, token: str) -> None:
        self.base = f"https://api.telegram.org/bot{token}"
        self.file_base = f"https://api.telegram.org/file/bot{token}"
        self.s = requests.Session()

    def call(self, method: str, http_timeout: int = 60, files=None, **params):
        params = {k: v for k, v in params.items() if v is not None}
        r = self.s.post(f"{self.base}/{method}", data=params if files else None, json=None if files else params,
                        files=files, timeout=http_timeout)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method}: {data.get('description')}")
        return data["result"]

    def download(self, file_id: str, dest: Path) -> Path:
        info = self.call("getFile", file_id=file_id)
        with self.s.get(f"{self.file_base}/{info['file_path']}", timeout=120) as r:
            r.raise_for_status()
            dest.write_bytes(r.content)
        return dest

    def text(self, chat: int, text: str) -> None:
        for i in range(0, len(text), 3900):
            self.call("sendMessage", chat_id=chat, text=text[i:i + 3900])

    def voice(self, chat: int, ogg: Path) -> None:
        with open(ogg, "rb") as f:
            self.call("sendVoice", http_timeout=120, files={"voice": ("rocky.ogg", f, "audio/ogg")}, chat_id=chat)

    def action(self, chat: int, what: str) -> None:
        try:
            self.call("sendChatAction", http_timeout=15, chat_id=chat, action=what)
        except Exception:  # noqa: BLE001 — only a "typing..." hint
            pass


# ---- ears, brain, voice --------------------------------------------------------------------------------
_whisper = None
_whisper_lock = threading.Lock()


def hear(path: Path) -> str:
    global _whisper
    with _whisper_lock:
        if _whisper is None:
            from faster_whisper import WhisperModel
            _whisper = WhisperModel("medium", device="cpu", compute_type="int8",
                                    cpu_threads=max(2, (os.cpu_count() or 4) - 2))
    segs, info = _whisper.transcribe(str(path), beam_size=3, vad_filter=True, initial_prompt=WHISPER_HINT,
                                     language=None)
    text = " ".join(s.text.strip() for s in segs).strip()
    if info.language not in ("hi", "en") or info.language_probability < 0.6:
        # the owner speaks Hindi, English or a mix: choose between those two only and listen again
        probs = dict(info.all_language_probs or [])
        lang = "hi" if probs.get("hi", 0) + probs.get("ur", 0) >= probs.get("en", 0) else "en"
        segs, info = _whisper.transcribe(str(path), beam_size=5, vad_filter=True, initial_prompt=WHISPER_HINT,
                                         language=lang)
        text = " ".join(s.text.strip() for s in segs).strip()
    log.info("heard (%s): %s", info.language, text)
    return text


def _prompt_file() -> Path:
    p = WORK / "rocky_voice_prompt.md"
    p.write_text((HOME / "ROCKY.md").read_text(encoding="utf-8") + "\n" + VOICE_RULES, encoding="utf-8")
    return p


def think(message: str) -> str:
    """One turn of Rocky's continuing Telegram conversation."""
    sess = json.loads(SESSION_FILE.read_text(encoding="utf-8")).get("id") if SESSION_FILE.exists() else None
    cmd = [str(CLAUDE), "-p", "--output-format", "json", "--permission-mode", "auto",
           "--append-system-prompt-file", str(_prompt_file())]
    if sess:
        cmd += ["--resume", sess]
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    p = subprocess.run(cmd, input=message, capture_output=True, text=True, encoding="utf-8", cwd=ROOT, env=env,
                       timeout=40 * 60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = (p.stdout or "").strip()
    try:
        data = json.loads(out.splitlines()[-1]) if out else {}
    except json.JSONDecodeError:
        data = {}
    if data.get("session_id"):
        SESSION_FILE.write_text(json.dumps({"id": data["session_id"]}), encoding="utf-8")
    reply = (data.get("result") or "").strip()
    if not reply:
        err = (p.stderr or out or "no answer").strip()
        log.error("claude failed (%s): %s", p.returncode, err[-500:])
        if sess and "No conversation found" in err:          # the saved conversation is gone: start a new one
            SESSION_FILE.unlink(missing_ok=True)
            return think(message)
        if "limit" in err.lower():
            return "Sorry, the Claude usage limit is reached right now. " + err[-160:]
        return "Sorry, something went wrong on the laptop and I could not answer. Please try again in a minute."
    return reply


def speakable(text: str) -> str:
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    t = re.sub(r"https?://\S+", " link ", t)
    t = re.sub(r"[*_#`|>]+", " ", t)
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.M)
    t = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", t)
    return re.sub(r"[ \t]+", " ", t).strip()[:2500]


def speak(text: str, dest: Path) -> Path | None:
    import edge_tts
    words = speakable(text)
    if not words:
        return None
    hindi = sum(1 for ch in words if "ऀ" <= ch <= "ॿ") > 0.2 * max(1, sum(ch.isalpha() for ch in words))
    mp3 = dest.with_suffix(".mp3")
    for attempt in range(3):
        try:
            asyncio.run(edge_tts.Communicate(words, VOICE_HI if hindi else VOICE_EN).save(str(mp3)))
            break
        except Exception as e:  # noqa: BLE001
            log.warning("voice attempt %d failed: %s", attempt + 1, e)
            time.sleep(3)
    else:
        return None
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(mp3), "-c:a", "libopus", "-b:a", "40k", "-ar", "48000",
                    "-ac", "1", str(dest)], check=False, timeout=120,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    mp3.unlink(missing_ok=True)
    return dest if dest.exists() else None


# ---- main loop -------------------------------------------------------------------------------------------
def handle(bot: Bot, chat: int, msg: dict) -> None:
    stop = threading.Event()

    def busy():
        while not stop.wait(4.5):
            bot.action(chat, "typing")
    threading.Thread(target=busy, daemon=True).start()
    try:
        if msg.get("voice") or msg.get("audio"):
            media = msg.get("voice") or msg.get("audio")
            ogg = bot.download(media["file_id"], WORK / f"in_{msg['message_id']}.ogg")
            text = hear(ogg)
            ogg.unlink(missing_ok=True)
            if not text:
                bot.text(chat, "I could not hear anything in that voice note. Please try again.")
                return
            bot.text(chat, f"🎙 {text}")
        else:
            text = (msg.get("text") or "").strip()
        if not text:
            return
        if text.lower() in ("/new", "/reset"):
            SESSION_FILE.unlink(missing_ok=True)
            bot.text(chat, "Okay, fresh conversation. I still remember everything in my notebook.")
            return
        if text.lower() == "/start":
            text = "Hi Rocky, I just connected on Telegram. Say hello in one short sentence."
        reply = think(text)
        bot.text(chat, reply)
        bot.action(chat, "record_voice")
        ogg = speak(reply, WORK / f"out_{msg['message_id']}.ogg")
        if ogg:
            bot.voice(chat, ogg)
            ogg.unlink(missing_ok=True)
    finally:
        stop.set()


def main() -> None:
    HOME.joinpath("logs").mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        encoding="utf-8")
    lock = open(WORK / "running.lock", "w")
    try:
        import msvcrt
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except (ImportError, OSError):
        if os.name == "nt":
            log.info("Rocky on Telegram is already running; this copy exits")
            return
    token = TOKEN_FILE.read_text(encoding="utf-8-sig").strip()
    bot = Bot(token)
    me = bot.call("getMe")
    owner = json.loads(OWNER_FILE.read_text(encoding="utf-8")) if OWNER_FILE.exists() else None
    log.info("Rocky on Telegram started as @%s; owner %s", me["username"], owner and owner.get("name"))
    offset = None
    threading.Thread(target=lambda: hear_warmup(), daemon=True).start()
    threading.Thread(target=ads_watch, daemon=True).start()
    while True:
        try:
            updates = bot.call("getUpdates", http_timeout=70, offset=offset, timeout=50)
        except Exception as e:  # noqa: BLE001 — network drops, laptop sleep: just try again
            log.warning("polling: %s", e)
            time.sleep(10)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message")
            if not msg or msg.get("chat", {}).get("type") != "private":
                continue
            user = msg.get("from", {})
            if owner is None:            # the first person to talk to the new bot is the owner, locked for good
                owner = {"id": user["id"], "name": user.get("first_name", ""), "username": user.get("username", "")}
                OWNER_FILE.write_text(json.dumps(owner), encoding="utf-8")
                log.info("owner locked: %s (@%s)", owner["name"], owner["username"])
            if user.get("id") != owner["id"]:
                log.warning("ignored a message from someone else: %s", user.get("id"))
                continue
            try:
                handle(bot, msg["chat"]["id"], msg)
            except Exception as e:  # noqa: BLE001 — one bad message never stops Rocky
                log.exception("message failed")
                try:
                    bot.text(msg["chat"]["id"], f"Sorry, that failed on the laptop: {str(e)[:200]}")
                except Exception:  # noqa: BLE001
                    pass


ADS_HOURS = (13, 19, 23)      # laptop clock: 3 short checks a day for NEW long videos (ads ON, owner's standing rule)


def ads_watch() -> None:
    """Three times a day (not all day): turn ads ON for any new long video. No Claude; the tool does nothing if there is none."""
    import datetime as dt
    import subprocess
    done = None
    while True:
        time.sleep(300)
        now = dt.datetime.now()
        slot = (now.date(), now.hour)
        if now.hour in ADS_HOURS and slot != done:
            done = slot
            try:
                py = Path(sys.executable).with_name("python.exe")
                r = subprocess.run([str(py if py.exists() else sys.executable), "-m", "tools.ads_on", "--new"],
                                   cwd=str(ROOT), capture_output=True, text=True, timeout=900)
                log.info("ads_on --new: %s %s", r.returncode, (r.stdout or r.stderr)[-300:].strip())
            except Exception as e:  # noqa: BLE001
                log.warning("ads_on --new failed: %s", e)


def hear_warmup() -> None:
    """Load Whisper in the background at start, so the first voice note is not slow."""
    try:
        import tempfile
        import wave
        f = Path(tempfile.gettempdir()) / "rocky_silence.wav"
        with wave.open(str(f), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\0\0" * 16000)
        hear(f)
    except Exception as e:  # noqa: BLE001
        log.warning("whisper warm-up: %s", e)


if __name__ == "__main__":
    main()
