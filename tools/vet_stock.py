"""Vet Storyblocks stock clips BEFORE they can be used in a video.

    python -m tools.vet_stock sheets [dir]      contact sheets (labelled with clip ids) in work/vet/ for a human/Rocky to LOOK at
    python -m tools.vet_stock auto [dir]        automatic flags (green screen, too short, too dark)
    python -m tools.vet_stock approve ID...     mark clips as looked-at and fine (Emirati/Gulf look, relevant, clean)
    python -m tools.vet_stock reject ID... -r why

The result is assets/stock_vetted.yaml; lane/story.py refuses any sb-<id> that is not approved there.
"""
from __future__ import annotations
import subprocess, sys
from pathlib import Path
import numpy as np
import yaml
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
VET = ROOT / "assets" / "stock_vetted.yaml"
RAW = ROOT / "work" / "women" / "raw"


def load() -> dict:
    d = yaml.safe_load(VET.read_text(encoding="utf-8")) if VET.exists() else {}
    d = d or {}
    d.setdefault("approved", [])
    d.setdefault("rejected", {})
    return d


def save(d: dict) -> None:
    d["approved"] = sorted({str(x) for x in d["approved"]})
    d["rejected"] = {str(k): v for k, v in sorted(d["rejected"].items())}
    VET.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _dur(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _frame(p: Path, t: float, w: int = 320) -> Image.Image:
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(p), "-frames:v", "1", "-vf", f"scale={w}:-1",
                        "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
    import io
    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


def green_share(img: Image.Image) -> float:
    a = np.asarray(img.resize((160, 90))).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return float(((g > r + 35) & (g > b + 35) & (g > 110)).mean())


def auto_flags(p: Path) -> list[str]:
    d = _dur(p)
    flags = []
    if d < 2.5:
        flags.append(f"too short ({d:.1f}s)")
    gs = max(green_share(_frame(p, d * f)) for f in (0.2, 0.5, 0.8)) if d else 0
    if gs > 0.18:
        flags.append(f"green screen ({gs * 100:.0f}% green)")
    lum = np.asarray(_frame(p, d * 0.5).convert("L")).mean() if d else 0
    if lum < 25:
        flags.append("too dark")
    return flags


def clips(dir_: Path) -> list[Path]:
    return sorted(dir_.glob("*.mp4"))


def sheets(dir_: Path, per: int = 12) -> None:
    VET.parent.mkdir(exist_ok=True)
    out = ROOT / "work" / "vet"
    out.mkdir(parents=True, exist_ok=True)
    v = load()
    todo = [c for c in clips(dir_) if c.stem not in v["approved"] and c.stem not in v["rejected"]]
    for n in range(0, len(todo), per):
        group = todo[n:n + per]
        W, H = 320, 180
        sheet = Image.new("RGB", (W * 3 * 1, (H + 18) * ((len(group) + 0) )), "black")
        sheet = Image.new("RGB", (W * 3, (H + 20) * len(group)), "black")
        dr = ImageDraw.Draw(sheet)
        for i, c in enumerate(group):
            d = _dur(c)
            for j, f in enumerate((0.15, 0.5, 0.85)):
                im = _frame(c, d * f, W).resize((W, H))
                sheet.paste(im, (j * W, i * (H + 20) + 20))
            dr.text((4, i * (H + 20) + 4), f"{c.stem}  {d:.0f}s  {' '.join(auto_flags(c))}", fill="yellow")
        sheet.save(out / f"sheet_{n // per + 1}.jpg", quality=80)
        print("wrote", out / f"sheet_{n // per + 1}.jpg", [c.stem for c in group])


def main() -> None:
    a = sys.argv[1:]
    cmd = a[0] if a else "sheets"
    if cmd == "sheets":
        sheets(Path(a[1]) if len(a) > 1 else RAW, per=6)
    elif cmd == "auto":
        for c in clips(Path(a[1]) if len(a) > 1 else RAW):
            f = auto_flags(c)
            print(c.stem, "FLAG: " + "; ".join(f) if f else "ok")
    elif cmd in ("approve", "reject"):
        v = load()
        why = ""
        ids = [x for x in a[1:] if x != "-r"]
        if "-r" in a:
            i = a.index("-r")
            why = " ".join(a[i + 1:])
            ids = [x for x in a[1:i]]
        for x in ids:
            x = x.removeprefix("sb-")
            if cmd == "approve":
                v["approved"].append(x)
                v["rejected"].pop(x, None)
            else:
                v["rejected"][x] = why or "rejected"
                v["approved"] = [y for y in v["approved"] if y != x]
        save(v)
        print(len(v["approved"]), "approved,", len(v["rejected"]), "rejected")


if __name__ == "__main__":
    main()
