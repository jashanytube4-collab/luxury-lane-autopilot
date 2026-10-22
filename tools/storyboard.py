"""Pre-render storyboard: python -m tools.storyboard <story file> -> work/storyboard/<id>_N.jpg
One row per beat: the spoken sentence + 3 frames of EVERY clip it will show. Look at it BEFORE rendering."""
from __future__ import annotations
import io, re, subprocess, sys, textwrap
from pathlib import Path
import yaml
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
W, H, ROWS = 200, 112, 9


def find(tok: str) -> Path | None:
    if tok.startswith("sb-"):
        for d in ("raw", "raw2", "raw3", "raw4", "raw5"):
            p = ROOT / "work" / "women" / d / f"{tok[3:]}.mp4"
            if p.exists():
                return p
    if tok.startswith("clip:"):
        for n in (f"{tok[5:]}.mp4", f"clip-{tok[5:]}.mp4"):
            p = ROOT / "work" / "clips" / n
            if p.exists():
                return p
    return None


def frames(p: Path) -> list[Image.Image]:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    d = float(r.stdout.strip() or 4)
    out = []
    for f in (0.2, 0.5, 0.8):
        b = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{d * f:.2f}", "-i", str(p), "-frames:v", "1", "-vf", f"scale={W}:{H}", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
        out.append(Image.open(io.BytesIO(b)).convert("RGB") if b else Image.new("RGB", (W, H), "gray"))
    return out


def main(path: str) -> None:
    s = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    beats = [b for sec in s["sections"] for b in sec["beats"]]
    out = ROOT / "work" / "storyboard"
    out.mkdir(parents=True, exist_ok=True)
    for sh in range(0, len(beats), ROWS):
        chunk = beats[sh:sh + ROWS]
        im = Image.new("RGB", (1500, ROWS * (H + 6)), "black")
        dr = ImageDraw.Draw(im)
        for r, b in enumerate(chunk):
            y = r * (H + 6)
            dr.text((4, y + 2), f"#{sh + r + 1}", fill="yellow")
            dr.text((4, y + 16), "\n".join(textwrap.wrap(b["say"], 34)[:6]), fill="white")
            x = 270
            for tok in b.get("show") or []:
                tok = str(tok)
                p = find(tok)
                if p:
                    for fr in frames(p):
                        im.paste(fr, (x, y)); x += W + 2
                    dr.text((x - 3 * (W + 2) + 2, y + 2), tok, fill="cyan")
                    x += 8
                else:
                    dr.rectangle((x, y, x + W, y + H), outline="gray")
                    dr.text((x + 4, y + 40), ("HAMDAN official: " if tok.startswith("key:") else "UNRESOLVED: ") + tok[:60], fill="orange")
                    x += W + 10
        im.save(out / f"{Path(path).stem}_{sh // ROWS + 1}.jpg", quality=80)
    print("sheets:", (len(beats) + ROWS - 1) // ROWS)


if __name__ == "__main__":
    main(sys.argv[1])
