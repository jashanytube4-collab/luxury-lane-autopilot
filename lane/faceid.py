"""Finds Sheikh Hamdan in photos and footage (OpenCV YuNet detector + SFace recogniser, free, CPU).

The reference face (assets/face/hamdan.npy) is built once by tools/build_face_ref.py: across many official photos the
one face that appears in almost all of them is his. Fans come for him, so Shorts and episodes are cut from the scenes
where he is clearly on screen, and vertical crops are centred on him.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import ASSETS_DIR
from .media import iter_frames, probe

log = logging.getLogger("lane.faceid")

FACE_DIR = ASSETS_DIR / "face"
DETECTOR = FACE_DIR / "face_detection_yunet_2023mar.onnx"
RECOGNISER = FACE_DIR / "face_recognition_sface_2021dec.onnx"
REFERENCE = FACE_DIR / "hamdan.npy"
MATCH = 0.68          # cosine similarity for Sheikh Hamdan (measured: him 0.75-0.80, lookalike brother 0.63-0.65)


@dataclass
class Sighting:
    present: bool
    size: float        # face height as a fraction of the frame height (bigger = closer shot)
    cx: float          # horizontal centre of his face, 0..1
    sim: float


NONE = Sighting(False, 0.0, 0.5, 0.0)


class FaceID:
    def __init__(self) -> None:
        self.det = cv2.FaceDetectorYN.create(str(DETECTOR), "", (320, 320), 0.75, 0.3, 50)
        self.rec = cv2.FaceRecognizerSF.create(str(RECOGNISER), "")
        self.ref = np.load(REFERENCE) if REFERENCE.exists() else None

    @property
    def ready(self) -> bool:
        return self.ref is not None

    def faces(self, img: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
        """[(face row from the detector, 128-d unit feature)] for every face in a BGR image."""
        h, w = img.shape[:2]
        scale = 960 / max(h, w) if max(h, w) > 960 else 1.0
        small = cv2.resize(img, (int(w * scale), int(h * scale))) if scale < 1 else img
        self.det.setInputSize((small.shape[1], small.shape[0]))
        _, found = self.det.detect(small)
        out = []
        for f in found if found is not None else []:
            if f[2] < 24 or f[3] < 24:
                continue
            feat = self.rec.feature(self.rec.alignCrop(small, f)).flatten()
            feat = feat / (np.linalg.norm(feat) + 1e-9)
            row = f.copy()
            row[:4] /= scale
            out.append((row, feat))
        return out

    def find(self, img: np.ndarray) -> Sighting:
        if self.ref is None:
            return NONE
        best = NONE
        h, w = img.shape[:2]
        for row, feat in self.faces(img):
            sim = float(np.dot(feat, self.ref))
            if sim >= MATCH and sim > best.sim:
                x, y, fw, fh = row[:4]
                best = Sighting(True, float(fh / h), float((x + fw / 2) / w), sim)
        return best

    def photo(self, path: Path) -> Sighting:
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        return self.find(img) if img is not None else NONE

    def scan(self, video: Path, every: float = 0.5) -> list[tuple[float, Sighting]]:
        """His presence through a video, sampled every `every` seconds."""
        info = probe(video)
        w = 640
        h = int(round(w * info["height"] / info["width"] / 2)) * 2
        out, t = [], 0.0
        for frame in iter_frames(video, w, h, vf_prefix=f"fps={1 / every},"):
            out.append((t, self.find(frame)))
            t += every
        return out


def scene_presence(timeline: list[tuple[float, Sighting]], a: float, b: float) -> Sighting:
    """Summary of how much of the scene [a, b] shows him: present = on screen in at least 60% of samples."""
    pts = [s for t, s in timeline if a <= t <= b]
    if not pts:
        return NONE
    seen = [s for s in pts if s.present]
    if len(seen) < 0.6 * len(pts):
        return Sighting(False, 0.0, 0.5, len(seen) / len(pts))
    return Sighting(True, float(np.mean([s.size for s in seen])), float(np.median([s.cx for s in seen])),
                    len(seen) / len(pts))
