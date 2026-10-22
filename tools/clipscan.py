"""Full-length clip review: python -m tools.clipscan <story file> -> work/clipscan/<id>_N.jpg
Every unique clip the story uses, ONE FRAME PER SECOND of the whole clip, labelled with the beats that use it."""
from __future__ import annotations
import io, subprocess, sys
from pathlib import Path
import yaml
from PIL import Image, ImageDraw
from tools.storyboard import find

ROOT = Path(__file__).resolve().parent.parent
W, H, COLS, ROWS = 160, 90, 10, 12


def dur(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def main(path: str) -> None:
    s = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    beats = [b for sec in s["sections"] for b in sec["beats"]]
    use: dict[str, list[int]] = {}
    for i, b in enumerate(beats, 1):
        for t in b.get("show") or []:
            use.setdefault(str(t), []).append(i)
    out = ROOT / "work" / "clipscan"
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.jpg"):
        f.unlink()
    rows, missing = [], []
    for tok, bs in use.items():
        p = find(tok)
        if not p:
            missing.append(tok); continue
        d = dur(p)
        n = max(1, int(d))
        b = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-vf", f"fps=1,scale={W}:{H}", "-frames:v", str(n), "-f", "image2pipe", "-vcodec", "bmp", "-"], capture_output=True).stdout
        frames, off = [], 0
        while off < len(b) and b[off:off + 2] == b"BM":
            size = int.from_bytes(b[off + 2:off + 6], "little")
            frames.append(Image.open(io.BytesIO(b[off:off + size])).convert("RGB")); off += size
        rows.append((tok, bs, d, frames))
    sheet, y, k = None, 0, 0
    def flush():
        nonlocal sheet, k
        if sheet is not None:
            k += 1; sheet.save(out / f"scan_{k:02d}.jpg", quality=80); sheet = None
    for tok, bs, d, frames in rows:
        for c in range(0, max(1, len(frames)), COLS):
            if sheet is None:
                sheet = Image.new("RGB", (COLS * (W + 2), ROWS * (H + 14)), "black"); y = 0
            dr = ImageDraw.Draw(sheet)
            dr.text((2, y), f"{tok} {d:.0f}s beats {bs[:6]}" if c == 0 else f"{tok} (cont.)", fill="yellow")
            for j, fr in enumerate(frames[c:c + COLS]):
                sheet.paste(fr, (j * (W + 2), y + 12))
            y += H + 14
            if y + H + 14 > sheet.size[1]:
                flush()
    flush()
    print(f"{len(rows)} clips scanned, {k} sheets in {out}; missing locally: {missing}")


if __name__ == "__main__":
    main(sys.argv[1])
