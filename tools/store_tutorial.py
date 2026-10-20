"""'How to order from our store' screen-recording videos (one long 16:9, one vertical Short), made by driving the
public storefront in a clean hidden browser (no login, so no admin buttons), with a voice-over and captions.

    python -m tools.store_tutorial long|short

Nothing is typed into checkout and no order is placed. Output: work/store_video/<kind>.mp4 (private, not uploaded).
"""
from __future__ import annotations

import base64
import random
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lane.media import FFMPEG  # noqa: E402
from lane.voice import synthesize  # noqa: E402
from tools.post_helper import CHROME  # noqa: E402
from tools.studio_tool import UA  # noqa: E402

STORE = "https://fazza-shop.fourthwall.com"
HOODIE = STORE + "/products/the-sky-belongs-to-those-who-dare-hoodie"
OUT = ROOT / "work" / "store_video"
FONT_DIR = ROOT / "assets" / "fonts"

CURSOR_JS = """
(() => {
  const c = document.createElement('div');
  c.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;width:30px;height:30px;margin:-15px 0 0 -15px;' +
    'border-radius:50%;background:rgba(232,184,74,.55);border:3px solid #fff;box-shadow:0 0 18px rgba(232,184,74,.9);' +
    'transition:transform .12s;left:-50px;top:-50px';
  document.addEventListener('DOMContentLoaded', () => document.documentElement.appendChild(c));
  addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; });
  addEventListener('mousedown', () => c.style.transform = 'scale(.6)');
  addEventListener('mouseup', () => c.style.transform = 'scale(1)');
})();
"""

LONG = [
    ("Winter is coming. And if you want something warm with the falcon on it, here is exactly how to order from our "
     "official store, step by step, in under two minutes.", "home"),
    ("First, find the store. Open our channel, tap the Store tab, and it opens this page. Everything is designed by us, "
     "made to order, and shipped worldwide, including India.", "home_scroll"),
    ("Tap Browse all. Here is everything: tees, sweatshirts, mugs, posters, and for the cold months, our hoodies. "
     "Let's pick one together.", "browse"),
    ("This is The Sky Belongs to Those Who Dare hoodie. A heavyweight fleece hoodie, printed for you after you order. "
     "The price is shown in your own currency.", "hoodie"),
    ("Now choose your size. The sizing is unisex and it can run a little tight, so open the size guide, and if you are "
     "between two sizes, go one size up.", "size"),
    ("Pick the quantity, and tap Add to Cart.", "add"),
    ("A small window confirms it is in your cart. Tap Checkout.", "checkout"),
    ("On the next page you add your delivery address, choose shipping, and pay securely. One important thing: every "
     "item is made to order, so sizing returns are not accepted. Double check your size before you pay. If there is "
     "a printing mistake, they replace it or refund you.", "checkout_hold"),
    ("Not a hoodie person? Sweatshirts, the mug and the tumbler are perfect for cold mornings too. They are all "
     "in the same store.", "winter_picks"),
    ("If this helped, subscribe, and tell us in the comments which design you want next. See you in the next video.",
     "end"),
]

SHORT = [
    ("Winter is coming. Here is how to get your Fazza hoodie in thirty seconds.", "home"),
    ("Open our Store tab, tap Browse all, and pick your hoodie.", "hoodie"),
    ("Choose your size. It runs a little tight, so if you are between two, size up.", "size"),
    ("Add to cart, tap Checkout, enter your address, and pay. It ships worldwide, including India.", "add_checkout"),
    ("Subscribe for more, and tell us which design you want next.", "end"),
]


class Rec:
    def __init__(self, pg, mobile: bool):
        self.pg, self.mobile = pg, mobile

    def wait(self, s: float) -> None:
        self.pg.wait_for_timeout(int(max(0.0, s) * 1000))

    def move_to(self, loc, steps: int = 28) -> None:
        loc.scroll_into_view_if_needed()
        b = loc.bounding_box()
        if b:
            self.pg.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, steps=steps)

    def click(self, loc) -> None:
        self.move_to(loc)
        self.wait(0.35)
        loc.click()

    def scroll(self, px: int, secs: float) -> None:
        n = max(1, int(secs * 6))
        for _ in range(n):
            self.pg.mouse.wheel(0, px / n)
            self.wait(secs / n)


def _act(r: Rec, name: str, dur: float) -> None:
    pg, t0 = r.pg, time.time()
    try:
        if name == "home":
            pg.goto(STORE, wait_until="domcontentloaded")
            r.wait(1.5)
            pg.mouse.move(700, 300, steps=30)
        elif name == "home_scroll":
            r.scroll(700, min(5, dur * 0.7))
        elif name == "browse":
            pg.evaluate("window.scrollTo(0, 0)")
            r.click(pg.get_by_role("link", name="Browse all").first)
            pg.wait_for_load_state("domcontentloaded")
            r.wait(1.0)
            r.scroll(2600, max(2.0, dur - 3.5))
        elif name == "hoodie":
            pg.goto(HOODIE, wait_until="domcontentloaded")
            r.wait(1.5)
            r.scroll(260, 2.0)
        elif name == "size":
            sz = pg.locator("input[type=radio][value='L'], label:has-text('Size guide')").first
            r.scroll(200, 1.5)
            guide = pg.get_by_role("button", name="Size guide").first
            if guide.count():
                r.move_to(guide)
            lab = pg.locator("label[for*='_SIZE'], label[for*='-L'], label:text-is('L')")
            for i in range(lab.count()):
                if (lab.nth(i).inner_text() or "").strip() == "L":
                    r.click(lab.nth(i))
                    break
        elif name == "add":
            sel = pg.locator("select").first
            if sel.count():
                r.move_to(sel)
            r.click(pg.get_by_role("button", name="Add to Cart").first)
        elif name == "add_checkout":
            lab = pg.locator("label:text-is('L')")
            if lab.count():
                r.click(lab.first)
            r.click(pg.get_by_role("button", name="Add to Cart").first)
            r.wait(1.6)
            r.click(pg.get_by_role("button", name="Checkout").first)
        elif name == "checkout":
            r.click(pg.get_by_role("button", name="Checkout").first)
        elif name == "checkout_hold":
            pg.wait_for_load_state("domcontentloaded")
            r.wait(2.0)
            r.scroll(500, max(2.0, dur - 3.5))
        elif name == "winter_picks":
            pg.goto(STORE + "/collections/all", wait_until="domcontentloaded")
            r.wait(1.5)
            r.scroll(900, max(2.0, dur - 3.0))
        elif name == "end":
            pg.goto(STORE, wait_until="domcontentloaded")
            pg.mouse.move(640, 360, steps=20)
    except Exception as e:  # noqa: BLE001 — a missed click must not lose the recording
        print("action", name, "problem:", str(e)[:120])
    r.wait(dur - (time.time() - t0))


def _ass(chunks: list[tuple[float, float, str]], vertical: bool) -> str:
    w, h = (1080, 1920) if vertical else (1920, 1080)
    size, margin = (74, 330) if vertical else (54, 70)
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            f"MarginR, MarginV, Encoding\nStyle: Sub,Montserrat ExtraBold,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,"
            f"&H80000000,0,0,0,0,100,100,0,0,1,5,2,2,60,60,{margin},1\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

    def ts(x: float) -> str:
        return f"{int(x // 3600)}:{int(x % 3600 // 60):02d}:{x % 60:05.2f}"
    return head + "".join(f"Dialogue: 0,{ts(a)},{ts(b)},Sub,,0,0,0,,{t}\n" for a, b, t in chunks)


def make(kind: str) -> Path:
    from playwright.sync_api import sync_playwright
    vertical = kind == "short"
    script = SHORT if vertical else LONG
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    vcfg = {**cfg["voice"], "edge_voice": cfg.get("story", {}).get("voice", cfg["voice"]["edge_voice"]),
            "edge_rate": "-2%"}
    work = OUT / kind
    work.mkdir(parents=True, exist_ok=True)
    voices = [synthesize(text, vcfg, work / f"v{i:02d}.wav") for i, (text, _) in enumerate(script)]
    durs = [v.duration + 0.9 for v in voices]
    vsize = {"width": 1080, "height": 1920} if vertical else {"width": 1280, "height": 720}
    vp = {"width": 540, "height": 960} if vertical else {"width": 1280, "height": 720}
    for f in list(work.glob("fr_*.jpg")) + [work / "frames.txt"]:
        f.unlink(missing_ok=True)
    starts: list[float] = []
    frames: list[tuple[float, bytes]] = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=str(CHROME), headless=True)
        ctx = b.new_context(user_agent=UA, viewport=vp, is_mobile=vertical, has_touch=False,
                            device_scale_factor=vsize["width"] / vp["width"])
        ctx.add_init_script(CURSOR_JS)
        pg = ctx.new_page()
        pg.set_default_timeout(15000)
        cdp = ctx.new_cdp_session(pg)
        t_page = time.time()

        def on_frame(ev: dict) -> None:           # the browser's own screencast: frames with their time
            frames.append((float(ev["metadata"]["timestamp"]) - t_page, base64.b64decode(ev["data"])))
            cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        cdp.on("Page.screencastFrame", on_frame)
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": vsize["width"],
                                          "maxHeight": vsize["height"], "everyNthFrame": 1})
        r = Rec(pg, vertical)
        for (text, name), d in zip(script, durs):
            starts.append(time.time() - t_page)
            _act(r, name, d)
        r.wait(1.0)
        cdp.send("Page.stopScreencast")
        ctx.close()
        b.close()
    lines = []
    for i, (t, jpg) in enumerate(frames):
        (work / f"fr_{i:05d}.jpg").write_bytes(jpg)
        nxt = frames[i + 1][0] if i + 1 < len(frames) else t + 0.5
        lines.append(f"file 'fr_{i:05d}.jpg'" + chr(10) + f"duration {max(0.02, nxt - t):.3f}")
    lines.append(f"file 'fr_{len(frames) - 1:05d}.jpg'")
    (work / "frames.txt").write_text(chr(10).join(lines), encoding="utf-8")
    video = work / "screen.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "frames.txt"),
                    "-vf", f"fps=30,scale={vsize['width']}:{vsize['height']}", "-c:v", "libx264", "-crf", "17",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    for f in work.glob("fr_*.jpg"):
        f.unlink()
    chunks = []
    for s, v, (text, _) in zip(starts, voices, script):
        words = text.split()
        n = 4 if vertical else 8
        pieces = [" ".join(words[i:i + n]) for i in range(0, len(words), n)]
        per = v.duration / len(pieces)
        for k, pc in enumerate(pieces):
            chunks.append((s + 0.15 + k * per, s + 0.15 + (k + 1) * per, pc.replace("{", "").replace("}", "")))
    ass = work / "captions.ass"
    ass.write_text(_ass(chunks, vertical), encoding="utf-8")
    music = random.Random(7).choice(sorted((ROOT / "assets" / "music").glob("*.mp3")))
    total = starts[-1] + durs[-1] + 1.0
    inputs = ["-i", str(video)]
    for v in voices:
        inputs += ["-i", str(v.wav)]
    inputs += ["-stream_loop", "-1", "-i", str(music)]
    n = len(voices)
    delays = ";".join(f"[{i + 1}:a]adelay={int((starts[i] + 0.15) * 1000)}|{int((starts[i] + 0.15) * 1000)}[d{i}]"
                      for i in range(n))
    mix = "".join(f"[d{i}]" for i in range(n))
    fc = (f"{delays};{mix}amix=inputs={n}:normalize=0,volume=1.6[vo];[{n + 1}:a]volume=0.10[m];"
          f"[m][vo]amix=inputs=2:duration=first:normalize=0[a]")
    out = OUT / f"{kind}.mp4"
    sub = str(ass).replace("\\", "/").replace(":", "\\:")
    fonts = str(FONT_DIR).replace("\\", "/").replace(":", "\\:")
    cmd = [FFMPEG, "-v", "error", "-y", *inputs, "-filter_complex", fc + f";[0:v]fps=30,scale={vsize['width']}:{vsize['height']},"
           f"subtitles='{sub}':fontsdir='{fonts}'[v]", "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}",
           "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
           str(out)]
    subprocess.run(cmd, check=True)
    print("made", out, f"{total:.0f}s")
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    make(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("long", "short") else "long")
