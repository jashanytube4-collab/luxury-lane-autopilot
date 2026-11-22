"""Local video QA, no Claude: python -m tools.factory.qa <video.mp4>
Writes contact sheets (one 4x3 sheet per ~20 s, next to the video: qa_sheet_N.jpg) and prints flags:
BLANK (almost no content), LOWCONTRAST, EDGE (bright pixels touching left/right edge = text overflow), DUPE (frame unchanged for >2.5 s)."""
import sys
from pathlib import Path
import cv2
import numpy as np


def check(video, step=0.5):
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    flags, frames, prev, same_since = [], [], None, None
    t = 0.0
    while t * fps < n - 1:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(fr, (480, 270)), cv2.COLOR_BGR2GRAY).astype(np.float32)
        body = g[:200]                                          # above subtitle zone
        if t > 0.6 and (cv2.Canny(body.astype(np.uint8), 60, 150) > 0).mean() < 0.004:
            flags.append((t, "BLANK"))
        if t > 0.6 and np.percentile(body, 99) - np.percentile(body, 50) < 55:
            flags.append((t, "LOWCONTRAST"))
        if (g[:200, :6] > 150).mean() > 0.05 or (g[:200, -6:] > 150).mean() > 0.05:
            flags.append((t, "EDGE"))
        if prev is not None and np.abs(g - prev).mean() < 0.15:
            same_since = same_since if same_since is not None else t
            if t - same_since > 2.5:
                flags.append((t, "DUPE")); same_since = t
        else:
            same_since = None
        prev = g
        frames.append((t, cv2.resize(fr, (480, 270))))
        t += step
    cap.release()
    return flags, frames


def sheets(frames, video, per=12, span=20):
    out = []
    pick = []
    for t, f in frames:
        pick.append((t, f))
    groups = {}
    for t, f in pick:
        groups.setdefault(int(t // span), []).append((t, f))
    for k, g in sorted(groups.items()):
        idx = np.linspace(0, len(g) - 1, min(per, len(g))).astype(int)
        tiles = []
        for i in idx:
            im = g[i][1].copy()
            cv2.putText(im, f"{g[i][0]:.1f}s", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            tiles.append(im)
        while len(tiles) < per:
            tiles.append(np.zeros_like(tiles[0]))
        rows = [np.hstack(tiles[r * 4:(r + 1) * 4]) for r in range(3)]
        p = Path(video).with_name(f"qa_sheet_{k}.jpg")
        cv2.imwrite(str(p), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 82])
        out.append(p)
    return out


if __name__ == "__main__":
    v = sys.argv[1]
    flags, frames = check(v)
    for p in sheets(frames, v):
        print("sheet", p)
    print("FLAGS:", ", ".join(f"{t:.1f}s {k}" for t, k in flags) or "none")
