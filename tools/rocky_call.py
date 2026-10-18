"""Rocky Call: talk to Rocky like a phone call, hands-free, from the owner's phone.

    .venv\\Scripts\\pythonw.exe -m tools.rocky_call        (started hidden at sign-in)

The phone opens https://<this laptop>.<tailnet>.ts.net (Tailscale Serve: only the owner's own Tailscale devices can
reach it; nothing is open to the internet). The page listens with the phone's own speech recognition (Hindi/English),
sends each sentence here, and plays Rocky's answer sentence by sentence as soon as it is ready, then listens again.

Brain: one continuing Claude Code conversation (stream-json) with rocky/ROCKY.md + call rules, so there is no
start-up wait on each turn. Voice: Microsoft neural voices via edge-tts.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = ROOT / "rocky"
SESSION_FILE = HOME / "call_session.json"
SETTINGS_FILE = HOME / "call_settings.json"
LOG_FILE = HOME / "logs" / "call.log"
PAGE = Path(__file__).with_name("rocky_call.html")
CLAUDE = Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin" / "claude.exe"
TAILSCALE = Path(r"C:\Program Files\Tailscale\tailscale.exe")
PORT = 8765
VOICES = {   # name shown on the phone -> (Hindi voice, English voice)
    "Madhur (Hindi, male)": ("hi-IN-MadhurNeural", "en-IN-PrabhatNeural"),
    "Andrew (natural, male)": ("en-US-AndrewMultilingualNeural", "en-US-AndrewMultilingualNeural"),
    "Swara (Hindi, female)": ("hi-IN-SwaraNeural", "en-IN-NeerjaNeural"),
}
DEFAULT_VOICE = "Madhur (Hindi, male)"
MODEL = os.environ.get("ROCKY_CALL_MODEL", "sonnet")     # fast enough for a conversation, still very capable

CALL_RULES = """
## You are on a live voice call right now (Rocky Call)
The owner is talking to you out loud from the phone, hands-free. Each message is the phone's speech recognition of
what was said (it can mishear names: "fuzzer/fajja/faza" = Fazza, "smith" = Smit, "rocky" may be missing). Every
word you write is spoken aloud immediately, sentence by sentence. So:
- Talk like a warm, sharp manager on a phone call: natural, friendly, confident. One to three short sentences per
  answer unless the owner asks for details or a report. Never read out lists; say the two or three things that matter.
- No markdown, no lists, no emojis, no links, no file paths, no code, no brackets. Numbers the way people say them.
- Language: if the owner speaks Hindi or Hinglish, answer in natural spoken Hindi/Hinglish written in DEVANAGARI
  script (English words like video, Shorts, views, thumbnail may stay in English letters). If the owner speaks
  English, answer in English.
- Before a slow step (checking Studio, GitHub, emails, rendering), first say one short line like
  "एक सेकंड, मैं Studio चेक करता हूँ" so the owner is not left in silence; then do it, then give the answer.
- You cannot show buttons. For anything that needs a yes (publishing, sending, rescheduling), ask clearly
  ("क्या मैं आगे बढ़ूँ? हाँ या ना बोलिए") and wait for the next message.
"""

log = logging.getLogger("rocky.call")


# ---- brain: one long-running Claude conversation ----------------------------------------------------------
class Brain:
    def __init__(self) -> None:
        self.p: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def _start(self) -> None:
        prompt = HOME / "telegram_work"
        prompt.mkdir(parents=True, exist_ok=True)
        pf = prompt / "rocky_call_prompt.md"
        pf.write_text((HOME / "ROCKY.md").read_text(encoding="utf-8") + "\n" + CALL_RULES, encoding="utf-8")
        cmd = [str(CLAUDE), "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--model", MODEL, "--permission-mode", "auto",
               "--append-system-prompt-file", str(pf)]
        sess = json.loads(SESSION_FILE.read_text(encoding="utf-8")).get("id") if SESSION_FILE.exists() else None
        if sess:
            cmd += ["--resume", sess]
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf-8", cwd=ROOT, env=env, bufsize=1,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._drain_err, daemon=True).start()
        log.info("brain started (model %s, resume %s)", MODEL, bool(sess))

    def _drain_err(self) -> None:
        p = self.p
        for line in p.stderr:
            if line.strip():
                log.warning("claude: %s", line.strip()[:300])

    def reset(self) -> None:
        SESSION_FILE.unlink(missing_ok=True)
        if self.p and self.p.poll() is None:
            self.p.kill()
        self.p = None

    def turn(self, text: str, out: "queue.Queue[tuple[str, str]]") -> None:
        """Streams ("text", chunk) and ("tool", name) into `out`, then ("done", "")."""
        with self.lock:
            for attempt in range(2):
                if self.p is None or self.p.poll() is not None:
                    self._start()
                try:
                    msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
                    self.p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
                    self.p.stdin.flush()
                    for line in self.p.stdout:
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        kind = ev.get("type")
                        if kind == "system" and ev.get("session_id"):
                            SESSION_FILE.write_text(json.dumps({"id": ev["session_id"]}), encoding="utf-8")
                        elif kind == "stream_event":
                            e = ev.get("event", {})
                            d = e.get("delta", {})
                            if d.get("type") == "text_delta":
                                out.put(("text", d["text"]))
                            elif e.get("type") == "content_block_start" and \
                                    e.get("content_block", {}).get("type") == "tool_use":
                                out.put(("tool", e["content_block"].get("name", "")))
                                out.put(("text", "\n"))          # a new sentence after the tool
                        elif kind == "result":
                            if ev.get("session_id"):
                                SESSION_FILE.write_text(json.dumps({"id": ev["session_id"]}), encoding="utf-8")
                            if ev.get("is_error") and not ev.get("result"):
                                out.put(("text", "Sorry, abhi Claude se jawab nahi aa paya."))
                            out.put(("done", ""))
                            return
                    raise BrokenPipeError("claude stopped")
                except (BrokenPipeError, OSError) as e:
                    log.warning("brain restart (%s)", e)
                    self.p = None
                    if attempt == 1 and SESSION_FILE.exists():
                        SESSION_FILE.unlink(missing_ok=True)
            out.put(("text", "Sorry, laptop par kuch gadbad hui. Ek minute baad phir try kariye."))
            out.put(("done", ""))


BRAIN = Brain()


# ---- voice -------------------------------------------------------------------------------------------------
def clean(text: str) -> str:
    t = re.sub(r"https?://\S+", " ", text)
    t = re.sub(r"[*_#`|>\[\]]+", " ", t)
    t = re.sub(r"^\s*[-•\d]+[.)]?\s+", "", t, flags=re.M)
    t = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def tts(text: str, voice_name: str) -> str | None:
    import edge_tts
    words = clean(text)
    if not words:
        return None
    hi, en = VOICES.get(voice_name, VOICES[DEFAULT_VOICE])
    devanagari = sum(1 for ch in words if "\u0900" <= ch <= "\u097f")
    voice = hi if devanagari > 0.2 * max(1, sum(ch.isalpha() for ch in words)) else en

    async def run() -> bytes:
        buf = b""
        async for chunk in edge_tts.Communicate(words, voice, rate="+4%").stream():
            if chunk["type"] == "audio":
                buf += chunk["data"]
        return buf
    for attempt in range(3):
        try:
            data = asyncio.run(run())
            if data:
                return base64.b64encode(data).decode()
        except Exception as e:  # noqa: BLE001
            log.warning("voice attempt %d: %s", attempt + 1, e)
            time.sleep(1)
    return None


SENTENCE_END = re.compile(r"(?<=[.!?।])\s+|\n+")


# ---- web server --------------------------------------------------------------------------------------------
def _owner_login() -> str | None:
    try:
        st = json.loads(subprocess.run([str(TAILSCALE), "status", "--json"], capture_output=True, text=True,
                                       timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout)
        return st["User"][str(st["Self"]["UserID"])]["LoginName"]
    except Exception as e:  # noqa: BLE001
        log.warning("could not read the Tailscale owner: %s", e)
        return None


OWNER = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        pass

    def _allowed(self) -> bool:
        global OWNER
        if OWNER is None:                    # laptop signed in to Tailscale after Rocky Call started
            OWNER = _owner_login()
        who = self.headers.get("Tailscale-User-Login")
        if not who or (OWNER and who != OWNER):
            log.warning("refused a request from %r", who)
            self.send_error(403)
            return False
        return True

    def do_GET(self):
        if not self._allowed():
            return
        if self.path.startswith("/settings"):
            body = json.dumps({"voices": list(VOICES), "voice": _settings().get("voice", DEFAULT_VOICE)}).encode()
            ctype = "application/json"
        else:
            body = PAGE.read_bytes()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self._allowed():
            return
        n = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(n) or b"{}")
        if self.path.startswith("/settings"):
            s = _settings()
            if data.get("voice") in VOICES:
                s["voice"] = data["voice"]
            SETTINGS_FILE.write_text(json.dumps(s), encoding="utf-8")
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/reset"):
            BRAIN.reset()
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        text = (data.get("text") or "").strip()
        log.info("owner: %s", text)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def send(obj: dict) -> None:
            self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()

        voice = _settings().get("voice", DEFAULT_VOICE)
        out: queue.Queue = queue.Queue()
        threading.Thread(target=BRAIN.turn, args=(text, out), daemon=True).start()
        buf, said = "", []
        try:
            while True:
                kind, val = out.get(timeout=40 * 60)
                if kind == "tool":
                    send({"status": "working"})
                    continue
                if kind == "text":
                    buf += val
                    parts = SENTENCE_END.split(buf)
                    for sentence in parts[:-1]:
                        if clean(sentence):
                            said.append(sentence)
                            send({"say": clean(sentence), "audio": tts(sentence, voice)})
                    buf = parts[-1]
                    continue
                if clean(buf):
                    said.append(buf)
                    send({"say": clean(buf), "audio": tts(buf, voice)})
                send({"done": True})
                log.info("rocky: %s", " ".join(clean(s) for s in said)[:500])
                return
        except (BrokenPipeError, ConnectionResetError):
            log.info("phone hung up during the answer")


def _settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> None:
    global OWNER
    (HOME / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        encoding="utf-8")
    OWNER = _owner_login()
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)     # only Tailscale Serve on this laptop reaches it
    except OSError:
        log.info("Rocky Call is already running")
        return
    log.info("Rocky Call listening on 127.0.0.1:%d for %s", PORT, OWNER)
    srv.serve_forever()


if __name__ == "__main__":
    main()
