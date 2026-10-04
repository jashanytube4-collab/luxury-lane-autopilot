"""Automatic checks on every finished Short before it is allowed anywhere near YouTube."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from .media import FFMPEG, probe


def check(path: Path, min_s: float, max_s: float) -> list[str]:
    """Returns a list of problems (empty = passed)."""
    problems = []
    if not path.exists() or path.stat().st_size < 400_000:
        return ["output file missing or too small"]
    info = probe(path)
    if (info["width"], info["height"]) != (1080, 1920):
        problems.append(f"wrong resolution {info['width']}x{info['height']}")
    if not (min_s - 0.3 <= info["duration"] <= max_s + 0.3):
        problems.append(f"duration {info['duration']:.2f}s outside {min_s}-{max_s}s")
    if not info["has_audio"]:
        problems.append("no audio track")
    else:
        out = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", out)
        if not m or float(m.group(1)) < -30:
            problems.append(f"audio too quiet ({m.group(1) if m else '?'} dB)")
    # sample frames: catch black/frozen/corrupt video
    means, prev, frozen = [], None, 0
    for k in range(6):
        t = info["duration"] * (k + 0.5) / 6
        raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                              "-vf", "scale=108:192", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                             capture_output=True).stdout
        if len(raw) < 108 * 192:
            problems.append(f"could not decode frame at {t:.1f}s")
            continue
        f = np.frombuffer(raw[: 108 * 192], np.uint8).astype(np.int16)
        means.append(float(f.mean()))
        if prev is not None and float(np.abs(f - prev).mean()) < 0.5:
            frozen += 1
        prev = f
    if means and min(means) < 10:
        problems.append("black frames detected")
    if frozen >= 3:
        problems.append("video looks frozen")
    return problems


def check_long(path: Path, min_s: float, max_s: float) -> list[str]:
    """Checks for the long-form episode (dips to black between chapters are expected, so only flag many)."""
    problems = []
    if not path.exists() or path.stat().st_size < 20_000_000:
        return ["episode file missing or too small"]
    info = probe(path)
    if (info["width"], info["height"]) != (1920, 1080):
        problems.append(f"wrong resolution {info['width']}x{info['height']}")
    if not (min_s <= info["duration"] <= max_s):
        problems.append(f"duration {info['duration'] / 60:.1f} min outside {min_s / 60:.1f}-{max_s / 60:.1f} min")
    if not info["has_audio"]:
        problems.append("no audio track")
    dark = 0
    for k in range(14):
        t = info["duration"] * (k + 0.5) / 14
        raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                              "-vf", "scale=192:108", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                             capture_output=True).stdout
        if len(raw) < 192 * 108:
            problems.append(f"could not decode frame at {t:.0f}s")
            continue
        if float(np.frombuffer(raw[:192 * 108], np.uint8).mean()) < 10:
            dark += 1
    if dark > 3:
        problems.append(f"{dark} of 14 sampled frames are black")
    return problems
