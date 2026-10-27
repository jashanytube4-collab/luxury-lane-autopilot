"""A year of members-only YouTube Posts (1 a day): exclusive still frames from his official videos — moments that
are never posted as photos — with members-only captions. Runs on GitHub (members_pack.yml), where the private
footage library is fast to read; the result is downloaded to the Desktop as "Fazza Posts (Members)".

    python tools/build_members_pack.py [start-date] [days]
"""
from __future__ import annotations

import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lane.config import WORK_DIR, load_config, load_secrets  # noqa: E402
from lane.hamdan import Hamdan, Library, load_events  # noqa: E402
from lane.media import probe  # noqa: E402
from lane.studio import Studio  # noqa: E402
from tools.build_post_pack import QUESTIONS, grade, natural_times, nice_title, square_crop  # noqa: E402

OUT = Path(__import__("os").environ.get("PACK_OUT", str(WORK_DIR / "members_pack")))
PER_VIDEO = 2                     # frames taken from one video (different moments)

EXCLUSIVE = [
    "🔒 Fazza Fam exclusive", "🔒 Members only", "💛 Just for our members", "🔒 Fazza Fam first look",
    "🔒 Members' moment", "💛 For the Fazza Fam",
]
THANKS = [
    "Thank you for being a member of The Luxury Lane 💛", "You make this channel possible. Thank you 💛",
    "This one stays inside the Fazza Fam 🤍", "Members see it first. Thank you for your support 💛",
    "Only members can see this post 🔒", "Grateful for every one of you 💛",
]


def frames_of(video: Path, face, want: int) -> list:
    """Up to `want` square, graded frames where his face is big and clear, from different parts of the video."""
    dur = probe(video)["duration"]
    cap = cv2.VideoCapture(str(video))
    picks, last_t = [], -99.0
    for i in range(36):
        t = dur * (0.05 + 0.9 * i / 35)
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, img = cap.read()
        if not ok or min(img.shape[:2]) < 600:
            continue
        row = face.locate(img)
        if row is None or row[3] / img.shape[0] < 0.11 or t - last_t < dur / (want + 1):
            continue
        if cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var() < 60:
            continue                                   # motion blur
        picks.append((row[3], grade(square_crop(img, row))))
        last_t = t
        if len(picks) >= want:
            break
    cap.release()
    return [p for _, p in picks]


def badge(img):
    """A small gold 'FAZZA FAM · MEMBERS' badge in the corner: members see at a glance it is theirs."""
    import numpy as np
    from PIL import Image, ImageDraw
    from tools.build_post_pack import font
    im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f = font("Montserrat-ExtraBold.ttf", 28)
    text = "FAZZA FAM · MEMBERS"
    w = d.textlength(text, font=f)
    x0, y0 = 36, 36
    d.rounded_rectangle([x0, y0, x0 + w + 66, y0 + 58], radius=29, fill=(17, 17, 17, 215),
                        outline=(212, 175, 90, 255), width=3)
    d.ellipse([x0 + 22, y0 + 22, x0 + 36, y0 + 36], fill=(212, 175, 90, 255))
    d.text((x0 + 48, y0 + 13), text, font=f, fill=(212, 175, 90, 255))
    out = Image.alpha_composite(im, layer).convert("RGB")
    return cv2.cvtColor(np.asarray(out), cv2.COLOR_RGB2BGR)


def caption(kind: str, ev: dict, rng: random.Random, qi: int) -> str:
    when = datetime.fromisoformat(ev["date"][:10]).strftime("%B %Y") if ev.get("date") else ""
    head = rng.choice(EXCLUSIVE)
    if kind == "question":
        return (f"{head}\n\nMembers' corner: {QUESTIONS[qi % len(QUESTIONS)]} 👇\n\n"
                f"{rng.choice(THANKS)}\n\n#FazzaFam #Fazza #SheikhHamdan")
    return (f"{head}\n\nA frame from the official footage: {nice_title(ev['title']).rstrip('.')}"
            + (f" ({when})" if when else "") + ".\nNever posted as a photo — straight from the video, just for you."
            + f"\n\n{rng.choice(THANKS)}\n\n#FazzaFam #Fazza #SheikhHamdan")


def main() -> None:
    start = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 365
    rng = random.Random(4242)
    cfg, sec = load_config(), load_secrets()
    library = Library(cfg["source"]["library_repo"], sec.library_token) if sec.library_token else None
    events = load_events()
    studio = Studio(cfg, None, Hamdan(library), WORK_DIR / "memberspack")
    keys = [k for k, e in events.items() if e.get("video") and e.get("title")]
    rng.shuffle(keys)
    need = days * 1
    shots: list[tuple[str, object]] = []
    for n, k in enumerate(keys):
        if len(shots) >= need:
            break
        video = studio.video(k, events[k])
        if video is None:
            continue
        try:
            got = frames_of(video, studio.face, PER_VIDEO)
        except Exception as e:  # noqa: BLE001 — one bad file never stops the pack
            print(f"  skipped {k[:50]}: {e}", flush=True)
            got = []
        finally:
            video.unlink(missing_ok=True)
        shots += [(k, g) for g in got]
        if n % 25 == 0:
            print(f"  {n} videos checked, {len(shots)} frames", flush=True)
    rng.shuffle(shots)
    print(f"{len(shots)} exclusive frames for {need} posts", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    qi = 0
    for d_i in range(days):
        day = start + timedelta(days=d_i)
        folder = OUT / day.strftime("%Y-%m %B")
        folder.mkdir(exist_ok=True)
        for slot, minute in enumerate(natural_times(rng, 1)):
            i = d_i * 1 + slot
            if i >= len(shots):
                break
            k, img = shots[i]
            kind = "question" if (i % 3 == 2) else "frame"
            text = caption(kind, events[k], rng, qi)
            qi += kind == "question"
            hh, mm = divmod(minute, 60)
            name = f"{day.isoformat()}_{hh:02d}{mm:02d}_m{slot + 1}"
            cv2.imwrite(str(folder / f"{name}.jpg"), badge(img), [cv2.IMWRITE_JPEG_QUALITY, 90])
            (folder / f"{name}.txt").write_text(text, encoding="utf-8")
    print(f"done: members pack in {OUT}", flush=True)


if __name__ == "__main__":
    main()
