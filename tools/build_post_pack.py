"""Builds a year of ready-made YouTube Posts (Posts tab), 5 a day, into a Desktop folder with one page per month.

    python tools/build_post_pack.py [start-date] [days]

YouTube has no API for Posts, so they are scheduled by hand in YouTube Studio (Create > Post): each month's
index.html shows every post's picture, date, suggested time and caption with a Copy button.

Daily mix: 3 official-moment posts (a different official event and photo each), 1 quote card (a real quote of
Prince Hamdan with his name, or an unattributed fan line), 1 engagement post (question or "did you know").
Every photo is used once and is face-checked to show him.
"""
from __future__ import annotations

import html
import json
import random
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lane import quotes  # noqa: E402
from lane.config import FONTS_DIR, WORK_DIR, load_config  # noqa: E402
from lane.faceid import FaceID  # noqa: E402
from lane.hamdan import Hamdan, load_events  # noqa: E402
from lane.studio import Studio, fact_bank  # noqa: E402

OUT = Path(__import__("os").environ.get("PACK_OUT", str(Path.home() / "Desktop" / "Fazza Posts (YouTube)")))
SIZE = 1080
YELLOW, WHITE, GOLD = (255, 214, 0), (255, 255, 255), (247, 201, 72)
TAGS = "#Fazza #SheikhHamdan #PrinceHamdan #Dubai #faz3"
CTAS = ["Subscribe to The Luxury Lane for more Fazza 🤍", "Follow for daily moments of Fazza 🤍",
        "Subscribe for more of Prince Hamdan 👑", "More Fazza every day on The Luxury Lane 🇦🇪",
        "Turn on notifications for more Fazza ✨"]
QUESTIONS = [
    "Which Fazza moment is your all-time favourite?", "Where would you love to meet Prince Hamdan?",
    "One word to describe Fazza?", "Horses, falcons or poetry: which side of Fazza do you love most?",
    "What is the best lesson we can learn from Prince Hamdan?", "Which country should Fazza visit next?",
    "What would you ask Prince Hamdan if you met him?", "Rate this picture of Fazza from 1 to 10!",
    "Which city are you watching from? Let's see how far the Fazza family reaches!",
    "Morning person or night owl: which do you think Fazza is?", "What is your favourite Fazza poem?",
    "Which Fazza video should we make next?", "Do you think Fazza will be a great ruler of Dubai one day?",
    "What do you admire most: his humility, his discipline or his kindness?",
    "Since when have you been a Fazza fan?", "Tag a friend who loves Fazza as much as you do!",
    "Which Dubai landmark is your favourite?", "Skydiving, diving or endurance riding: which would you try?",
    "What makes a true leader in your eyes?", "Say hello to Fazza from your country in the comments!",
    "Which Fazza adventure would you join?", "Who inspires you the most in life?",
    "Desert or city: where do you think Fazza feels most at home?", "What is one dream you are chasing this year?",
    "Which quality of Fazza would you like to have?", "Write a short message for Prince Hamdan below!",
    "Best Fazza look: traditional kandura or sports gear?", "Have you ever visited Dubai?",
    "What would you name your horse if you were Fazza?", "Which language do you speak? Say Fazza's name in it!",
]


DRY = re.compile(r"bylaw|resolution|\blaw\b|decree|amend|issues|approv|budget|committee|framework|regulat|policy|"
                 r"council|session|meeting of|memorandum|agreement|licens", re.I)
WARM = ["Prince Hamdan, working for Dubai every single day.", "Another day of service for the Crown Prince of Dubai.",
        "Fazza at work: calm, focused and dedicated.", "Leading Dubai with vision and discipline.",
        "The Crown Prince of Dubai, hard at work for his people.", "Behind every big plan for Dubai, there is Fazza.",
        "Prince Hamdan, building the future of Dubai.", "Focused, humble and always working for the nation."]


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS_DIR / name), size)


def square_crop(img: np.ndarray, row) -> np.ndarray:
    h, w = img.shape[:2]
    side = min(h, w)
    if row is not None:
        cx, cy = row[0] + row[2] / 2, row[1] + row[3] * 0.9
    else:
        cx, cy = w / 2, h / 2
    x0 = int(np.clip(cx - side / 2, 0, w - side))
    y0 = int(np.clip(cy - side * 0.42, 0, h - side))
    crop = img[y0:y0 + side, x0:x0 + side]
    return cv2.resize(crop, (SIZE, SIZE), interpolation=cv2.INTER_AREA)


def grade(img: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    lab[..., 0] = cv2.createCLAHE(1.6, (8, 8)).apply(lab[..., 0])
    out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR).astype(np.float32)
    out[..., 2] += 6
    out[..., 0] -= 4
    return np.clip(out, 0, 255).astype(np.uint8)


def wrap(text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if cur and f.getbbox(t)[2] > width:
            lines.append(cur)
            cur = w
        else:
            cur = t
    return lines + ([cur] if cur else [])


def card(base: np.ndarray, title: str, body: str = "", by: str = "", band: str = "bottom") -> Image.Image:
    """Photo with a dark gradient and text (quote cards and engagement posts)."""
    img = Image.fromarray(cv2.cvtColor(base, cv2.COLOR_BGR2RGB)).convert("RGBA")
    grad = Image.new("L", (1, SIZE))
    for y in range(SIZE):
        t = y / SIZE if band == "bottom" else 1 - y / SIZE
        grad.putpixel((0, y), int(235 * max(0.0, (t - 0.35) / 0.65) ** 1.3))
    shade = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 255))
    shade.putalpha(grad.resize((SIZE, SIZE)))
    img = Image.alpha_composite(img, shade)
    d = ImageDraw.Draw(img)
    d.text((SIZE // 2, 46), "THE LUXURY LANE", font=font("Cinzel-Bold.ttf", 30), fill=(255, 255, 255, 170),
           anchor="mt")
    if title:
        ft = font("Anton-Regular.ttf", 92)
        y = 120 if band == "top" else SIZE - 560
        d.text((70, y), title, font=ft, fill=YELLOW, stroke_width=5, stroke_fill=(0, 0, 0))
    if body:
        size = 58 if len(body) < 70 else 50 if len(body) < 110 else 44
        fb = font("Montserrat-ExtraBold.ttf", size)
        lines = wrap(body, fb, SIZE - 140)
        lh = int(size * 1.28)
        y = SIZE - 110 - lh * len(lines) - (60 if by else 0) if band == "bottom" else 250
        for line in lines:
            d.text((70, y), line, font=fb, fill=WHITE, stroke_width=3, stroke_fill=(0, 0, 0))
            y += lh
        if by:
            d.text((70, y + 18), "— " + by.upper(), font=font("Cinzel-Bold.ttf", 34), fill=GOLD)
    return img.convert("RGB")


def nice_title(t: str) -> str:
    t = re.sub(r"^H\.?H\.? ", "", t.strip())
    t = re.sub(r"\bHamdan bin Mohammed\b", "Prince Hamdan", t, count=1)
    t = t.replace("Dubai Crown Prince", "Prince Hamdan")
    return t.rstrip(".…") + "."


def main() -> None:
    start = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 10, 14)
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 365
    rng = random.Random(2026)
    cfg = load_config()
    events = load_events()
    studio = Studio(cfg, None, Hamdan(None), WORK_DIR / "postpack")
    face = studio.face
    cursors: dict = {}
    print("collecting real quotes...", flush=True)
    quotes.harvest(cursors, pages=80)
    real = [q for q in cursors.get("quotes", [])]
    fans = quotes.fan_lines()
    facts = [str(f) for g in fact_bank()["facts"].values() for f in g if isinstance(f, str)]
    print(f"{len(real)} real quotes, {len(fans)} fan lines, {len(facts)} facts", flush=True)

    keys = [k for k, e in events.items() if e.get("photos") and e.get("title")]
    rng.shuffle(keys)
    need_events = days * 3
    photos: list[tuple[str, np.ndarray]] = []        # (event key, square photo) for every post
    used_events: list[str] = []
    spare: list[tuple[str, np.ndarray]] = []          # second photos of events, for quote/engagement posts
    for n, k in enumerate(keys):
        if len(used_events) >= need_events and len(spare) >= days * 2:
            break
        try:
            paths = studio.photos(k, events[k], limit=3)
        except Exception:  # noqa: BLE001
            continue
        good = []
        for p in paths:
            img = cv2.imread(str(p), cv2.IMREAD_COLOR)
            if img is None or min(img.shape[:2]) < 700:
                continue
            row = face.locate(img)
            if row is None or row[3] / img.shape[0] < 0.05:
                continue
            good.append(grade(square_crop(img, row)))
        for p in paths:
            p.unlink(missing_ok=True)                   # keep the disk small: only the finished square is kept
        if not good:
            continue
        if len(used_events) < need_events:
            used_events.append(k)
            photos.append((k, good[0]))
            spare += [(k, g) for g in good[1:]]
        else:
            spare += [(k, g) for g in good]
        if n % 50 == 0:
            print(f"  {n} events checked, {len(used_events)} moment photos, {len(spare)} spare", flush=True)
    rng.shuffle(spare)
    print(f"photos ready: {len(photos)} moments, {len(spare)} spare", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    month_pages: dict[str, list[dict]] = {}
    q_cycle = real + fans
    rng.shuffle(q_cycle)
    qi = ei = 0
    for day_i in range(days):
        day = start + timedelta(days=day_i)
        times = sorted(rng.sample(range(8 * 60, 23 * 60, 15), 5))
        while any(b - a < 100 for a, b in zip(times, times[1:])):
            times = sorted(rng.sample(range(8 * 60, 23 * 60, 15), 5))
        kinds = ["moment", "moment", "moment", "quote", "engage"]
        rng.shuffle(kinds)
        for slot, (kind, minute) in enumerate(zip(kinds, times)):
            if kind == "moment" and photos:
                k, ph = photos.pop()
                ev = events[k]
                when = datetime.fromisoformat(ev["date"][:10]).strftime("%B %Y") if ev.get("date") else ""
                headline = rng.choice(WARM) if DRY.search(ev["title"]) else nice_title(ev["title"])
                caption = (f"{headline}\n" + (f"📍 Official moment, {when}\n" if when else "")
                           + f"\n{rng.choice(CTAS)}\n\n{TAGS}")
                pic = Image.fromarray(cv2.cvtColor(ph, cv2.COLOR_BGR2RGB))
            else:
                src = spare.pop() if spare else photos.pop()
                if kind == "quote" or not spare:
                    item = q_cycle[qi % len(q_cycle)]
                    qi += 1
                    if isinstance(item, dict):
                        pic = card(src[1], "", item["text"], "Sheikh Hamdan bin Mohammed")
                        caption = (f"“{item['text']}”\n— Sheikh Hamdan bin Mohammed (Fazza)\n\n"
                                   f"{rng.choice(CTAS)}\n\n{TAGS} #FazzaQuotes")
                    else:
                        pic = card(src[1], "", item)
                        caption = f"{item} 🤍\n\n{rng.choice(CTAS)}\n\n{TAGS}"
                else:
                    if ei % 3 == 2 and facts:
                        fact = facts[(ei // 3) % len(facts)]
                        pic = card(src[1], "DID YOU KNOW?", fact, band="bottom")
                        caption = f"Did you know? {fact}\n\n{rng.choice(CTAS)}\n\n{TAGS}"
                    else:
                        q = QUESTIONS[(ei - ei // 3) % len(QUESTIONS)]
                        pic = card(src[1], "YOUR TURN", q, band="bottom")
                        caption = f"{q} 👇\nTell us in the comments!\n\n{rng.choice(CTAS)}\n\n{TAGS}"
                    ei += 1
            month = day.strftime("%Y-%m %B")
            folder = OUT / month
            folder.mkdir(exist_ok=True)
            hh, mm = divmod(minute, 60)
            name = f"{day.isoformat()}_{hh:02d}{mm:02d}_{slot + 1}.jpg"
            pic.save(folder / name, quality=90)
            (folder / name.replace(".jpg", ".txt")).write_text(caption, encoding="utf-8")
            india = (datetime.combine(day, datetime.min.time()) + timedelta(minutes=minute + 90)).strftime("%H:%M")
            month_pages.setdefault(month, []).append({"img": name, "day": day.strftime("%a %d %b %Y"),
                                                      "dubai": f"{hh:02d}:{mm:02d}", "india": india,
                                                      "caption": caption})
        if day_i % 30 == 0:
            print(f"  posts written up to {day}", flush=True)
    for month, posts in month_pages.items():
        write_page(OUT / month / "index.html", month, posts)
    write_home(OUT / "START HERE.html", sorted(month_pages))
    print(f"done: {sum(len(p) for p in month_pages.values())} posts in {OUT}", flush=True)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
:root{{--bg:#f6f4ef;--card:#fff;--ink:#1b1b1b;--muted:#6b6b6b;--gold:#b8860b;--line:#e5e1d8}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141414;--card:#1f1f1f;--ink:#f1f1f1;--muted:#a3a3a3;--gold:#f7c948;--line:#333}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,Arial,sans-serif}}
header{{padding:20px 16px;max-width:1100px;margin:auto}} h1{{margin:0 0 6px;font-size:22px}} p{{color:var(--muted);margin:0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;padding:0 16px 40px;max-width:1100px;margin:auto}}
.post{{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}}
.post img{{width:100%;display:block}} .meta{{padding:10px 12px 0;font-weight:600}} .meta span{{color:var(--gold)}}
textarea{{box-sizing:border-box;width:calc(100% - 24px);margin:8px 12px;height:110px;border:1px solid var(--line);border-radius:8px;
background:transparent;color:var(--ink);font:13px/1.4 system-ui,Arial;padding:8px;resize:vertical}}
.row{{display:flex;gap:8px;padding:0 12px 12px}} button,a.btn{{flex:1;border:0;border-radius:8px;padding:9px;font-weight:600;cursor:pointer;
background:var(--gold);color:#111;text-align:center;text-decoration:none;font-size:14px}} a.btn{{background:var(--line);color:var(--ink)}}
.done{{opacity:.45}}</style></head><body><header><h1>{title}</h1>
<p>In YouTube Studio: <b>Create &rarr; Post</b> &rarr; add the picture &rarr; paste the caption &rarr; <b>Schedule</b> for the date and time shown.
Times are shown for Dubai and for India. Tick "Done" to grey out posts you have scheduled (remembered in this browser).</p></header>
<div class="grid">{cards}</div><script>
document.querySelectorAll('.copy').forEach(b=>b.onclick=()=>{{const t=b.closest('.post').querySelector('textarea');
navigator.clipboard.writeText(t.value).then(()=>{{b.textContent='Copied ✓';setTimeout(()=>b.textContent='Copy caption',1500)}})}});
document.querySelectorAll('.tick').forEach(b=>{{const k='done:'+b.dataset.k;let d=false;try{{d=localStorage.getItem(k)==='1'}}catch(e){{}}
const p=b.closest('.post');if(d){{p.classList.add('done');b.textContent='Done ✓'}}
b.onclick=()=>{{const on=!p.classList.contains('done');p.classList.toggle('done',on);b.textContent=on?'Done ✓':'Mark done';
try{{localStorage.setItem(k,on?'1':'0')}}catch(e){{}}}}}});</script></body></html>"""


def write_page(path: Path, month: str, posts: list[dict]) -> None:
    cards = "".join(
        f'<div class="post"><img src="{p["img"]}" loading="lazy" alt="">'
        f'<div class="meta">{p["day"]} · <span>{p["dubai"]} Dubai</span> · {p["india"]} India</div>'
        f'<textarea readonly>{html.escape(p["caption"])}</textarea>'
        f'<div class="row"><button class="copy">Copy caption</button>'
        f'<a class="btn" href="{p["img"]}" download>Save picture</a>'
        f'<button class="tick" data-k="{p["img"]}">Mark done</button></div></div>' for p in posts)
    path.write_text(PAGE.format(title=f"Fazza Posts — {month[8:]} {month[:4]}", cards=cards), encoding="utf-8")


def write_home(path: Path, months: list[str]) -> None:
    links = "".join(f'<div class="post"><div class="meta"><a href="{html.escape(m)}/index.html">{m[8:]} {m[:4]}</a>'
                    f'</div><p style="padding:0 12px 12px">5 posts a day</p></div>' for m in months)
    path.write_text(PAGE.format(title="Fazza Posts — one year", cards=links), encoding="utf-8")


if __name__ == "__main__":
    main()
