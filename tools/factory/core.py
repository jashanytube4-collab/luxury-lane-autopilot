"""Factory core: script pack -> voice (edge-tts, word timing) -> shots -> graded segments -> subtitles + music -> mp4. Local only.

Script pack (written by Claude, JSON): {channel, slug, title, description, tags, thumb:{text, query}, beats:[{text, v:[visual,...]}]}
visual = {"t":"photo","q":"search words"} | {"t":"big","big":"65 km","small":"width of the gap"} | {"t":"quote","text":"..","by":".."}
       | {"t":"list","title":"..","lines":[..]} | {"t":"card","text":"BIG WORDS"} | {"t":"clip","path":"local.mp4"}
A beat with several visuals shows them one after another; a beat with one photo query pulls extra distinct photos
so no shot is held longer than the channel pace (profile.pace_max seconds)."""
import asyncio
import hashlib
import json
import random
import re
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from . import assets

ROOT = Path(__file__).resolve().parents[2]
FF, FPS, W, H = "ffmpeg", 30, 1920, 1080
FONTS = ROOT / "assets" / "fonts"
GAP = 0.28


def run(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(" ".join(map(str, cmd))[:300] + "\n" + r.stderr[-1200:])
    return r.stdout


def dur(p):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).strip())


def profile(key):
    p = json.loads((ROOT / "factory" / "profiles" / f"{key}.json").read_text(encoding="utf-8"))
    p.setdefault("pace_max", 4.5)
    p.setdefault("accent", "#e8b04a")
    p.setdefault("grade", "eq=contrast=1.06:saturation=1.05,vignette=PI/5")
    p.setdefault("music", [])
    return p


# ----------------------------------------------------------------------------- voice
def tts_beat(text, prof, out: Path):
    """Return (wav path, [(word, start, end)]) for one beat; cached."""
    import edge_tts
    h = hashlib.md5((prof["voice"] + prof["rate"] + prof["pitch"] + text).encode()).hexdigest()[:12]
    mp3, js = out / f"{h}.mp3", out / f"{h}.json"
    if not (mp3.exists() and js.exists()):
        async def go():
            words = []
            comm = edge_tts.Communicate(text, prof["voice"], rate=prof["rate"], pitch=prof["pitch"], boundary="WordBoundary")
            with open(mp3, "wb") as f:
                async for ch in comm.stream():
                    if ch["type"] == "audio":
                        f.write(ch["data"])
                    elif ch["type"] == "WordBoundary":
                        words.append((ch["text"], ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7))
            js.write_text(json.dumps(words), encoding="utf-8")
        for i in range(4):
            try:
                asyncio.run(go()); break
            except Exception as e:  # noqa: BLE001
                print("tts retry", e); asyncio.run(asyncio.sleep(3))
    return mp3, json.loads(js.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------- cards
def _font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def grad_bg(prof, seed=0):
    random.seed(seed)
    a = prof["accent"].lstrip("#")
    c = tuple(int(a[i:i + 2], 16) for i in (0, 2, 4))
    im = Image.new("RGB", (W, H), (10, 12, 16))
    px = ImageDraw.Draw(im)
    for y in range(H):
        k = y / H
        px.line((0, y, W, y), fill=(int(14 + c[0] * 0.07 * (1 - k)), int(16 + c[1] * 0.07 * (1 - k)), int(20 + c[2] * 0.09 * (1 - k))))
    return im


def card_image(v, prof, path: Path, seed=0):
    im = grad_bg(prof, seed)
    d = ImageDraw.Draw(im)
    acc = prof["accent"]
    t = v["t"]
    if t == "big":
        d.text((W // 2, 450), str(v["big"]), font=_font("Anton-Regular.ttf", 360), fill=acc, anchor="mm")
        d.text((W // 2, 700), str(v.get("small", "")).upper(), font=_font("Montserrat-ExtraBold.ttf", 54), fill="#e9edf3", anchor="mm")
    elif t == "quote":
        txt = _wrap(d, '"' + v["text"] + '"', _font("Cinzel-Bold.ttf", 76), 1500)
        d.multiline_text((W // 2, 470), txt, font=_font("Cinzel-Bold.ttf", 76), fill="#f2f2f2", anchor="mm", align="center", spacing=18)
        d.text((W // 2, 800), "â€” " + v.get("by", ""), font=_font("Montserrat-ExtraBold.ttf", 44), fill=acc, anchor="mm")
    elif t == "list":
        d.text((200, 190), v.get("title", "").upper(), font=_font("Anton-Regular.ttf", 100), fill=acc)
        for i, ln in enumerate(v["lines"][:6]):
            d.text((200, 360 + i * 120), f"{i + 1}", font=_font("Anton-Regular.ttf", 80), fill=acc)
            d.text((300, 370 + i * 120), ln, font=_font("Montserrat-ExtraBold.ttf", 62), fill="#e9edf3")
    else:  # card
        txt = _wrap(d, v.get("text", "").upper(), _font("Anton-Regular.ttf", 230), 1650)
        d.multiline_text((W // 2, H // 2), txt, font=_font("Anton-Regular.ttf", 230), fill=acc, anchor="mm", align="center", spacing=10)
    im.save(path)


def _wrap(d, text, font, maxw):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) > maxw and cur:
            lines.append(cur); cur = w
        else:
            cur = t
    lines.append(cur)
    return "\n".join(lines)


# ----------------------------------------------------------------------------- shots
def photo_pool(q, n, used: set):
    """n distinct photos for query q, trying shorter queries if the search is thin."""
    words = q.split()
    tries = [q] + [" ".join(words[:k]) for k in (3, 2, 1) if len(words) > k]
    got = []
    for t in tries:
        for it in assets.search_images(t, 8):
            if it["id"] not in used and it["id"] not in [g["id"] for g in got]:
                got.append(it)
        if len(got) >= n:
            break
    return got[:n]


def kenburns(img_path, d, out: Path, prof, idx):
    """Photo -> graded moving shot of d seconds (slow zoom/pan, direction varies by idx)."""
    n = max(2, int(round(d * FPS)))
    im = Image.open(img_path).convert("RGB")
    if im.width / im.height < 0.95:                     # true portrait only: sharp photo centred over its own blurred copy
        bg = im.resize((3840, int(3840 * im.height / im.width)), Image.LANCZOS)
        top = (bg.height - 2160) // 2
        bg = bg.crop((0, max(top, 0), 3840, max(top, 0) + 2160)).filter(ImageFilter.GaussianBlur(40))
        bg = Image.eval(bg, lambda v: int(v * 0.45))
        fg = im.resize((int(2000 * im.width / im.height), 2000), Image.LANCZOS)
        bg.paste(fg, ((3840 - fg.width) // 2, 80))
        im = bg
    else:
        s = max(3200 / im.width, 1800 / im.height, 1)
        k = min(s, 4200 / im.width)
        if k != 1:
            im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    tmp = out.with_suffix(".jpg")
    im.save(tmp, quality=93)
    z0, z1 = (1.0, 1.16) if idx % 2 == 0 else (1.16, 1.0)
    px = ["(iw-iw/zoom)/2", "0", "(iw-iw/zoom)"][idx % 3]
    py = ["(ih-ih/zoom)/2", "(ih-ih/zoom)/2", "0"][idx % 3]
    zexpr = f"{z0}+({z1}-{z0})*on/{n}"
    vf = (f"zoompan=z='{zexpr}':x='{px}':y='{py}':d={n}:s={W}x{H}:fps={FPS},{prof['grade']},"
          f"noise=alls=3:allf=t,format=yuv420p")
    run([FF, "-y", "-loglevel", "error", "-i", tmp, "-vf", vf, "-t", f"{d:.3f}", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "20", "-pix_fmt", "yuv420p", "-threads", "6", out])
    tmp.unlink(missing_ok=True)


def still(png, d, out: Path, prof):
    n = int(d * FPS)
    vf = (f"zoompan=z='1+0.05*on/{n}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d={n}:s={W}x{H}:fps={FPS},"
          f"noise=alls=4:allf=t,format=yuv420p")
    run([FF, "-y", "-loglevel", "error", "-i", png, "-vf", vf, "-t", f"{d:.3f}", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "20", "-pix_fmt", "yuv420p", "-threads", "6", out])


def plan_shots(beats, timing, prof, work: Path):
    """Return list of shots (path, dur, credit) covering every beat exactly; no photo reused in the video."""
    used, shots, credits = set(), [], []
    (work / "shots").mkdir(exist_ok=True)
    for bi, (b, tm) in enumerate(zip(beats, timing)):
        D = tm["dur"]
        vis = b.get("v") or [{"t": "photo", "q": b.get("q", "")}]
        # split the beat across its visuals; photo visuals expand so no shot exceeds pace_max
        slots = []
        for v in vis:
            slots.append(v)
        CARD_MAX = 3.2
        is_card = [v["t"] in ("big", "quote", "list", "card") for v in slots]
        fixed = sum(min(CARD_MAX, D / len(slots)) for c in is_card if c)
        rest = max(D - fixed, 0.5)
        n_other = max(sum(1 for c in is_card if not c), 1)
        dur_of = [min(CARD_MAX, D / len(slots)) if c else rest / n_other for c in is_card]
        if all(is_card):                                   # only cards: split the whole beat, never hold a card longer than needed
            dur_of = [D / len(slots)] * len(slots)
        out_slots = []
        for v, per in zip(slots, dur_of):
            if v["t"] == "photo" and per > prof["pace_max"]:
                k = int(per // prof["pace_max"]) + 1
                out_slots += [dict(v, _n=k, _per=per)] + [dict(v, _skip=True)] * (k - 1)
            else:
                out_slots.append(dict(v, _per=per))
        # materialise
        i = 0
        per = D / max(len(out_slots), 1)
        while i < len(out_slots):
            v = out_slots[i]
            if v["t"] == "photo":
                k = v.get("_n", 1)
                pool = photo_pool(v["q"], k, used)
                span = v.get("_per", per)
                sd = span / k
                for j in range(k):
                    sp = work / "shots" / f"b{bi:03d}_{i}_{j}.mp4"
                    if j < len(pool):
                        used.add(pool[j]["id"]); credits.append(pool[j]["credit"])
                        kenburns(pool[j]["path"], sd, sp, prof, bi + j)
                    else:                                    # not enough photos: a styled card, never a repeat
                        png = work / "shots" / f"b{bi:03d}_{i}_{j}.png"
                        card_image(dict(t="card", text=_keyword(b["text"])), prof, png, bi + j); still(png, sd, sp, prof)
                    shots.append((sp, sd))
                i += k
            else:
                sp = work / "shots" / f"b{bi:03d}_{i}.mp4"
                png = sp.with_suffix(".png")
                per = v.get("_per", per)
                if v["t"] == "clip":
                    run([FF, "-y", "-loglevel", "error", "-i", v["path"], "-t", f"{per:.3f}", "-vf",
                         f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},{prof['grade']},format=yuv420p", "-r", FPS,
                         "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", sp])
                else:
                    card_image(v, prof, png, bi); still(png, per, sp, prof)
                shots.append((sp, per)); i += 1
    return shots, sorted(set(credits))


def _keyword(text):
    ws = [w.strip(".,;:!?\"'").upper() for w in text.split() if len(w) > 4]
    return " ".join(ws[:2]) or "WATCH"


# ----------------------------------------------------------------------------- subtitles / audio
def ass_from_words(all_words, path: Path, prof):
    def t(x):
        return f"{int(x // 3600)}:{int(x % 3600 // 60):02d}:{x % 60:05.2f}"
    acc = prof["accent"].lstrip("#")
    bgr = acc[4:6] + acc[2:4] + acc[0:2]
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: Default,Montserrat ExtraBold,72,&H00FFFFFF,&H00{bgr},&H00101010,&H80000000,1,0,0,0,100,100,0,0,1,5,1,2,120,120,70,1\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    lines = []
    for beat in all_words:                      # all_words = list of per-beat word lists: never group across beats
        grp = []
        for w in beat:
            grp.append(w)
            if len(grp) >= 4:
                lines.append(grp); grp = []
        if grp:
            lines.append(grp)
    ev = []
    for g in lines:
        s, e = g[0][1], g[-1][2] + 0.05
        txt = " ".join(x[0] for x in g).upper()
        ev.append(f"Dialogue: 0,{t(s)},{t(e)},Default,,0,0,0,,{txt}")
    path.write_text(head + "\n".join(ev), encoding="utf-8")


def pick_music(prof):
    lib = sorted((ROOT / "assets" / "music").glob("*.mp3"))
    pref = [m for m in lib if any(k.lower() in m.stem.lower() for k in prof["music"])] or lib
    return pref[0] if pref else None


# ----------------------------------------------------------------------------- build
def build(pack_path, preview=0):
    pack = json.loads(Path(pack_path).read_text(encoding="utf-8"))
    prof = profile(pack["channel"])
    work = ROOT / "factory" / "out" / pack["channel"] / pack["slug"]
    (work / "tts").mkdir(parents=True, exist_ok=True)
    beats = pack["beats"][:preview] if preview else pack["beats"]
    # 1 voice
    wavs, timing, allw, t0 = [], [], [], 0.0
    gap = work / "tts" / "gap.wav"
    run([FF, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", GAP, gap])
    for i, b in enumerate(beats):
        mp3, words = tts_beat(b["text"], prof, work / "tts")
        w = work / "tts" / f"{i:03d}.wav"
        run([FF, "-y", "-loglevel", "error", "-i", mp3, "-ar", "24000", "-ac", "1", w])
        d = dur(w)
        timing.append(dict(start=t0, dur=d + GAP))
        allw.append([(x[0], t0 + x[1], t0 + x[2]) for x in words])
        wavs += [w, gap]; t0 += d + GAP
    lst = work / "tts" / "list.txt"
    lst.write_text("\n".join(f"file '{Path(p).as_posix()}'" for p in wavs))
    run([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", work / "voice.wav"])
    total = t0
    # 2 shots
    shots, credits = plan_shots(beats, timing, prof, work)
    sl = work / "shots" / "list.txt"
    sl.write_text("\n".join(f"file '{Path(p).as_posix()}'" for p, _ in shots))
    # 3 subtitles
    ass = work / "subs.ass"
    ass_from_words(allw, ass, prof)
    # 4 mix + render
    music = pick_music(prof)
    out = work / ("preview.mp4" if preview else "final.mp4")
    fonts = FONTS.as_posix().replace(":", r"\:")
    vf = f"subtitles='{ass.as_posix().replace(':', chr(92) + ':')}':fontsdir='{fonts}'"
    cmd = [FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", sl, "-i", work / "voice.wav"]
    if music:
        cmd += ["-stream_loop", "-1", "-i", music]
        af = ("[1:a]asplit=2[v][sc];[2:a]volume=0.55[m];[m][sc]sidechaincompress=threshold=0.03:ratio=9:attack=15:release=450[d];"
              "[v][d]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")
        cmd += ["-filter_complex", f"[0:v]{vf}[vv];{af}", "-map", "[vv]", "-map", "[a]"]
    else:
        cmd += ["-vf", vf, "-map", "0:v", "-map", "1:a"]
    cmd += ["-t", f"{total:.3f}", "-r", FPS, "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
            "-threads", "6", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    run(cmd)
    (work / "credits.txt").write_text("\n".join(credits), encoding="utf-8")
    print(f"ok {out} {total / 60:.2f} min, {len(shots)} shots, {len(credits)} credited photos")
    return out

