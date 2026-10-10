"""Build Sheikh Hamdan's reference face from official photos: the face present in the most photos is his.

    python tools/build_face_ref.py [n_photos]

Writes assets/face/hamdan.npy and work/face_check.jpg (the matched faces, for a visual check).
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lane.faceid import REFERENCE, FaceID  # noqa: E402
from lane.hamdan import Hamdan  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 160
    events = json.loads((ROOT / "state" / "events.json").read_text(encoding="utf-8"))
    urls = []
    for ev in sorted(events.values(), key=lambda e: e.get("date", ""), reverse=True):
        if ev["title"].lower().startswith(("hamdan bin mohammed", "sheikh hamdan")):
            urls += ev.get("photos", [])[:2]
        if len(urls) >= n:
            break
    h, fid = Hamdan(), FaceID()
    feats, crops = [], []
    for i, url in enumerate(urls):
        dest = ROOT / "work" / "faceref" / f"{i}.jpg"
        try:
            h.download_photo(url, dest)
        except Exception as e:  # noqa: BLE001
            print("skip", url, e)
            continue
        img = cv2.imread(str(dest))
        for row, feat in fid.faces(img):
            x, y, w, hh = [int(v) for v in row[:4]]
            crop = img[max(0, y):y + hh, max(0, x):x + w]
            if crop.size:
                feats.append(feat)
                crops.append(cv2.resize(crop, (96, 96)))
    F = np.array(feats)
    sims = F @ F.T
    votes = (sims > 0.42).sum(1)
    centre = int(np.argmax(votes))
    members = np.where(sims[centre] > 0.42)[0]
    ref = F[members].mean(0)
    ref /= np.linalg.norm(ref)
    members = np.where(F @ ref > 0.45)[0]            # one refinement pass
    ref = F[members].mean(0)
    ref /= np.linalg.norm(ref)
    np.save(REFERENCE, ref)
    print(f"{len(urls)} photos, {len(F)} faces; reference built from {len(members)} matching faces")
    sample = [crops[i] for i in members[:48]]
    rows = [np.hstack(sample[i:i + 12] + [np.zeros_like(sample[0])] * (12 - len(sample[i:i + 12])))
            for i in range(0, len(sample), 12)]
    cv2.imwrite(str(ROOT / "work" / "face_check.jpg"), np.vstack(rows))


if __name__ == "__main__":
    main()
