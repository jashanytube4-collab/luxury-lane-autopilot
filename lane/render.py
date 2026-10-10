"""The Shorts edit: shot timeline (video scenes and photos), camera moves, transitions, colour grade, captions,
final encode.

Frames are composed in Python (OpenCV, sub-pixel smooth zooms/pans), piped straight into FFmpeg, which burns
in the libass captions and encodes H.264 + AAC at 1080x1920.
"""
from __future__ import annotations

import logging
import math
import random
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .config import FONTS_DIR
from .media import FFMPEG, iter_frames, probe

log = logging.getLogger("lane.render")

W, H = 1080, 1920
FILL_MARGIN = 1.10      # decoded width headroom for pans in fill layout
FRAME_ZOOM = 1.32       # how much a wide source is enlarged inside the framed layout
PHOTO_MAX_SECONDS = 4.6


class RenderError(Exception):
    pass


@dataclass
class Segment:
    kind: str             # video | photo
    src: Path
    src_w: int
    src_h: int
    src_start: float
    out_frames: int
    speed: float          # >1 means slow motion (output is longer than the source span)
    focus_x: float
    move: str             # camera move: push | pull | drift_l | drift_r


@dataclass
class Plan:
    layout: str
    fps: int
    duration: float
    segments: list[Segment]
    transitions: list[str]          # transitions[i] is the cut INTO segment i+1
    cut_times: list[float] = field(default_factory=list)


# ---- planning -------------------------------------------------------------
def _snap(start: float, end: float, cuts: list[float], fps: float) -> tuple[float, float]:
    """Keep shots from starting or ending a few frames before/after a hard cut in the source."""
    guard = 2.0 / fps
    for c in cuts:
        if start - guard <= c <= start + 0.6 and end - (c + guard) >= 1.2:
            start = c + guard
        if end - 0.6 <= c <= end + guard and (c - guard) - start >= 1.2:
            end = c - guard
    return start, end


def make_plan(shots: list[dict], *, layout: str, duration: float, video: Path | None, cuts: list[float],
              fps: int, seed: int) -> Plan:
    """shots: [{"kind": "video", "start", "end", "focus_x"} | {"kind": "photo", "path", "seconds", "focus_x"}]."""
    rng = random.Random(seed)
    vinfo = probe(video) if video else None
    src_duration = vinfo["duration"] if vinfo else 0.0
    spans = []  # [kind, a, b, focus, src, w, h]
    for s in shots:
        if s.get("kind", "video") == "video":
            if not vinfo:
                continue
            a, b = _snap(s["start"], min(s["end"], src_duration - 0.05), cuts, fps)
            if b - a >= 0.8:
                hard = min(s.get("hard_end", src_duration - 0.05), src_duration - 0.05)
                spans.append(["video", a, b, s.get("focus_x", 0.5), video, vinfo["width"], vinfo["height"], hard])
        else:
            img = cv2.imread(str(s["path"]), cv2.IMREAD_REDUCED_COLOR_2)
            if img is None:
                continue
            h, w = img.shape[:2]
            spans.append(["photo", 0.0, float(s.get("seconds", 3.2)), s.get("focus_x", 0.5), Path(s["path"]), w, h,
                          None])
    if not spans:
        raise RenderError("no usable shots")

    need = duration
    have = sum(b - a for _, a, b, *_ in spans)
    speeds = [1.0] * len(spans)
    if have > need:  # trim every shot proportionally (keep the starts — the best moment is usually there)
        k = need / have
        for sp in spans:
            sp[2] = sp[1] + (sp[2] - sp[1]) * k
    else:
        extra = need - have
        # 1) extend video shots into unused footage that follows them
        starts = sorted(sp[1] for sp in spans if sp[0] == "video")
        for sp in spans:
            if extra <= 0 or sp[0] != "video":
                continue
            later = [x for x in starts if x > sp[1]]
            room = min((min(later) if later else src_duration - 0.05), sp[7]) - sp[2]
            grow = max(0.0, min(room, extra))
            sp[2] += grow
            extra -= grow
        # 2) let photos breathe a little longer
        for sp in spans:
            if extra <= 0 or sp[0] != "photo":
                continue
            grow = min(PHOTO_MAX_SECONDS - (sp[2] - sp[1]), extra)
            if grow > 0:
                sp[2] += grow
                extra -= grow
        # 3) gentle slow motion on video for whatever is still missing (cap 1.35x)
        if extra > 0.05:
            vid = sum(sp[2] - sp[1] for sp in spans if sp[0] == "video")
            if vid <= 0:
                raise RenderError(f"not enough material for a {need:.1f}s Short")
            slow = min(1.35, (vid + extra) / vid)
            if vid * slow < vid + extra - 0.05:
                raise RenderError(f"not enough footage for a {need:.1f}s Short")
            speeds = [slow if sp[0] == "video" else 1.0 for sp in spans]

    total_frames = int(round(duration * fps))
    lengths = [(sp[2] - sp[1]) * s for sp, s in zip(spans, speeds)]
    scale = total_frames / (sum(lengths) * fps)
    frames, acc, prev = [], 0.0, 0
    for L in lengths:
        acc += L * fps * scale
        frames.append(int(round(acc)) - prev)
        prev = int(round(acc))

    moves = ["push", "pull", "drift_l", "drift_r"]
    segs, last_move = [], None
    for (kind, a, _, f, src, w, h, _hard), sp, n in zip(spans, speeds, frames):
        move = rng.choice([m for m in moves if m != last_move])
        last_move = move
        segs.append(Segment(kind=kind, src=src, src_w=w, src_h=h, src_start=a, out_frames=max(1, n), speed=sp,
                            focus_x=f, move=move))
    segs[0].move = "push"

    transitions, last = [], None
    for _ in range(len(segs) - 1):
        t = rng.choices(["punch", "whip", "flash"], weights=[5, 3, 2])[0]
        if t == last and t != "punch":
            t = "punch"
        transitions.append(t)
        last = t
    cut_times, f0 = [], 0
    for s in segs[:-1]:
        f0 += s.out_frames
        cut_times.append(f0 / fps)
    return Plan(layout=layout, fps=fps, duration=duration, segments=segs, transitions=transitions, cut_times=cut_times)


# ---- look ---------------------------------------------------------------------
def grade_lut() -> np.ndarray:
    x = np.arange(256) / 255.0
    s = x * x * (3 - 2 * x)
    base = 0.62 * x + 0.38 * s                     # gentle S-curve
    base = 0.022 + base * 0.968                    # slightly lifted, filmic blacks
    shadows, highs = (1 - x) ** 2.2, x ** 2.0
    b = base + 0.030 * shadows - 0.040 * highs     # teal shadows, warm highlights
    g = base + 0.012 * shadows + 0.010 * highs
    r = base - 0.012 * shadows + 0.040 * highs
    lut = np.stack([b, g, r], axis=-1).reshape(256, 1, 3)
    return np.clip(lut * 255, 0, 255).astype(np.uint8)


def shade_mask(w: int, h: int, layout: str) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
    m = 1.0 - 0.32 * np.clip((d - 0.35) / 0.65, 0, 1) ** 1.6       # vignette
    if layout == "fill":
        top = np.clip(yy / 560.0, 0, 1)
        m *= 0.50 + 0.50 * top ** 0.8                                   # room for the hook title + brand mark
        bot = np.clip((yy - 1150) / (h - 1150), 0, 1)
        m *= 1.0 - 0.42 * bot ** 1.3                                    # caption contrast + Shorts UI
    elif layout == "wide":
        bot = np.clip((yy - h * 0.70) / (h * 0.30), 0, 1)
        m *= 1.0 - 0.38 * bot ** 1.4                                    # subtitle + lower-third contrast
    return np.repeat((m * 255).astype(np.uint8)[:, :, None], 3, axis=2)


class Look:
    def __init__(self, layout: str, seed: int, size: tuple[int, int] = (W, H)) -> None:
        w, h = size
        self.lut = grade_lut()
        self.mask = shade_mask(w, h, layout)
        rng = np.random.default_rng(seed)
        self.grain = []
        for _ in range(4):
            n = rng.normal(0, 3.2, (h // 2, w // 2)).astype(np.float32)
            n = cv2.resize(n, (w, h), interpolation=cv2.INTER_LINEAR)
            pos = np.clip(n, 0, 255).astype(np.uint8)
            neg = np.clip(-n, 0, 255).astype(np.uint8)
            self.grain.append((cv2.merge([pos] * 3), cv2.merge([neg] * 3)))

    def apply(self, img: np.ndarray, i: int) -> np.ndarray:
        img = cv2.LUT(img, self.lut)
        gray = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        img = cv2.addWeighted(img, 1.13, gray, -0.13, 0)                 # +13% saturation
        img = cv2.multiply(img, self.mask, scale=1 / 255)
        pos, neg = self.grain[i % len(self.grain)]
        return cv2.subtract(cv2.add(img, pos), neg)


# ---- camera ---------------------------------------------------------------------
def ease(t: float) -> float:
    return t * t * (3 - 2 * t)


def camera(move: str, p: float, strength: float = 1.0) -> tuple[float, float]:
    """(zoom, horizontal drift as fraction of the window) for progress p in [0,1]."""
    e = ease(p)
    if move == "push":
        return 1.0 + 0.085 * strength * e, 0.0
    if move == "pull":
        return 1.0 + 0.085 * strength * (1 - e), 0.0
    if move == "drift_l":
        return 1.0 + 0.06 * strength, (0.035 - 0.07 * e) * strength
    return 1.0 + 0.06 * strength, (-0.035 + 0.07 * e) * strength


def window(src: np.ndarray, out_w: int, out_h: int, zoom: float, cx: float, cy: float,
           shift_px: float = 0.0) -> np.ndarray:
    """Crop a out_w x out_h view centred at (cx, cy) with sub-pixel zoom. shift_px (whip pans) is applied
    after the view is clamped inside the frame, so the motion is never swallowed by the clamp."""
    sh, sw = src.shape[:2]
    base = max(out_w / sw, out_h / sh)          # scale that makes src cover the window at zoom 1
    z = base * zoom
    half_w, half_h = out_w / (2 * z), out_h / (2 * z)
    cx = min(max(cx, half_w), sw - half_w) + shift_px / z
    cy = min(max(cy, half_h), sh - half_h)
    m = np.array([[1 / z, 0, cx - half_w], [0, 1 / z, cy - half_h]], np.float32)
    return cv2.warpAffine(src, m, (out_w, out_h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REFLECT)


def motion_blur(img: np.ndarray, k: int) -> np.ndarray:
    return cv2.blur(img, (k, 1)) if k > 2 else img


# ---- frame sources --------------------------------------------------------------------
def _work_size(layout: str, src_w: int, src_h: int) -> tuple[str, int, int, int, int]:
    """(mode, scaled_w, scaled_h, crop_w, crop_h) of the working frame for one shot."""
    if layout == "fill":
        if src_w / src_h >= 9 / 16:
            sw = int(round(src_w * 1920 / src_h / 2)) * 2
            return "fill_h", sw, 1920, min(sw, int(W * FILL_MARGIN) // 2 * 2), 1920
        sh = int(round(src_h * W / src_w / 2)) * 2
        return "fill_w", W, sh, W, 1920
    fw = int(round(W * FRAME_ZOOM / 2)) * 2
    fh = int(round(fw * src_h / src_w / 2)) * 2
    return "frame", fw, fh, fw, min(fh, 1500)


def _decode_filter(layout: str, src_w: int, src_h: int, focus_x: float) -> tuple[str, int, int]:
    mode, sw, sh, cw, ch = _work_size(layout, src_w, src_h)
    fx = f"{focus_x:.3f}"
    if mode == "fill_h":
        return f"scale={sw}:{sh}:flags=lanczos,crop={cw}:{ch}:'max(0,min(iw-ow,{fx}*iw-ow/2))':0,", cw, ch
    if mode == "fill_w":
        return f"scale={sw}:{sh}:flags=lanczos,crop={cw}:{ch}:0:'(ih-oh)/2',", cw, ch
    return f"scale={sw}:-2:flags=lanczos,crop={cw}:'min(ih,{ch})':0:'(ih-oh)/2',", cw, ch


def prepare_photo(path: Path, layout: str, focus_x: float) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise RenderError(f"cannot read photo {path}")
    h, w = img.shape[:2]
    mode, sw, sh, cw, ch = _work_size(layout, w, h)
    img = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA if sw < w else cv2.INTER_CUBIC)
    if mode == "fill_h":
        x0 = int(max(0, min(sw - cw, focus_x * sw - cw / 2)))
        return np.ascontiguousarray(img[:, x0:x0 + cw])
    y0 = max(0, (sh - ch) // 2)
    return np.ascontiguousarray(img[y0:y0 + ch, :cw])


def band_height(layout: str, src_w: int, src_h: int) -> int:
    """Height of the picture band on screen (full height in fill layout)."""
    return H if layout == "fill" else _work_size(layout, src_w, src_h)[4]


def _shot_frames(seg: Segment, layout: str, fps: int):
    if seg.kind == "photo":
        img = prepare_photo(seg.src, layout, seg.focus_x)
        for _ in range(seg.out_frames):
            yield img
        return
    vf, dw, dh = _decode_filter(layout, seg.src_w, seg.src_h, seg.focus_x)
    vf_prefix = f"setpts={seg.speed:.4f}*(PTS-STARTPTS),fps={fps},"
    frames = iter_frames(seg.src, dw, dh, start=seg.src_start, duration=seg.out_frames / fps + 0.2,
                         vf_prefix=vf_prefix + vf)
    last, count = None, 0
    for f in frames:
        if count >= seg.out_frames:
            break
        last = f
        count += 1
        yield f
    if last is None:
        raise RenderError(f"could not decode shot at {seg.src_start:.2f}s")
    while count < seg.out_frames:  # source ran out a frame or two early: hold the last frame
        count += 1
        yield last


# ---- compose ----------------------------------------------------------------------------
def _compose_fill(src: np.ndarray, zoom: float, drift: float, shift: float) -> np.ndarray:
    sh, sw = src.shape[:2]
    return window(src, W, H, zoom, sw / 2 + drift * W, sh / 2, shift * W)


def blurred_backdrop(src: np.ndarray, out_w: int, out_h: int, focus_x: float, zoom: float,
                     darken: float = 0.42) -> np.ndarray:
    sh, sw = src.shape[:2]
    aspect = out_w / out_h
    bw = max(16, min(sw, int(sh * aspect)))
    x0 = max(0, min(sw - bw, int(sw * focus_x - bw / 2)))
    small_w, small_h = (54, 96) if aspect < 1 else (96, 54)
    small = cv2.resize(src[:, x0:x0 + bw], (small_w, small_h), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 3.2)
    bg = window(small, out_w, out_h, zoom, small_w / 2, small_h / 2)
    return cv2.convertScaleAbs(bg, alpha=darken, beta=0)


def paste_feathered(bg: np.ndarray, fg: np.ndarray, x0: int, y0: int, feather: int = 28) -> np.ndarray:
    """Paste fg onto bg with soft top/bottom (and left/right, when fg is narrower) edges."""
    fh, fw = fg.shape[:2]
    out = bg
    region = out[y0:y0 + fh, x0:x0 + fw].astype(np.float32)
    alpha = np.ones((fh, fw), np.float32)
    ramp = np.linspace(0, 1, feather, dtype=np.float32)
    if fh < out.shape[0]:
        alpha[:feather] *= ramp[:, None]
        alpha[-feather:] *= ramp[::-1][:, None]
    if fw < out.shape[1]:
        alpha[:, :feather] *= ramp[None, :]
        alpha[:, -feather:] *= ramp[::-1][None, :]
    a = alpha[:, :, None]
    out[y0:y0 + fh, x0:x0 + fw] = (fg.astype(np.float32) * a + region * (1 - a)).astype(np.uint8)
    return out


def _compose_frame(src: np.ndarray, focus_x: float, zoom: float, drift: float, shift: float,
                   bg_zoom: float) -> np.ndarray:
    sh, sw = src.shape[:2]
    bg = blurred_backdrop(src, W, H, focus_x, bg_zoom)
    fg = window(src, W, sh, zoom, sw * focus_x + drift * W, sh / 2, shift * W)
    return paste_feathered(bg, fg, 0, (H - sh) // 2)


def start_encoder(work: Path, ass: Path, audio: Path, out: Path, size: tuple[int, int], fps: int,
                  duration: float, crf: int = 18) -> tuple[subprocess.Popen, object]:
    fonts = work / "fonts"
    if fonts.exists():
        shutil.rmtree(fonts)
    shutil.copytree(FONTS_DIR, fonts)
    if ass.resolve() != (work / "captions.ass").resolve():
        shutil.copy(ass, work / "captions.ass")
    cmd = [FFMPEG, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{size[0]}x{size[1]}", "-r", str(fps), "-i", "-",
           "-i", str(audio.resolve()),
           "-filter_complex", "[0:v]ass=captions.ass:fontsdir=fonts,format=yuv420p[v]",
           "-map", "[v]", "-map", "1:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-profile:v", "high", "-tune", "film",
           "-g", str(fps * 2), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", "-t", f"{duration:.3f}", str(out.resolve())]
    errlog = open(work / "ffmpeg_encode.log", "wb")
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errlog, cwd=work), errlog


def finish_encoder(enc: subprocess.Popen, errlog, work: Path) -> None:
    enc.stdin.close()
    code = enc.wait()
    errlog.close()
    if code != 0:
        raise RenderError(f"ffmpeg failed: {(work / 'ffmpeg_encode.log').read_text(errors='ignore')[-800:]}")


def render_video(plan: Plan, ass: Path, audio: Path, out: Path, work: Path, seed: int) -> None:
    enc, errlog = start_encoder(work, ass, audio, out, (W, H), plan.fps, plan.duration)
    look = Look(plan.layout, seed)
    fps = plan.fps
    n_seg = len(plan.segments)
    gi = 0
    try:
        for si, seg in enumerate(plan.segments):
            t_in = plan.transitions[si - 1] if si > 0 else "open"
            t_out = plan.transitions[si] if si < n_seg - 1 else None
            strength = 1.4 if seg.kind == "photo" else 1.0   # photos get a livelier camera
            for fi, src_frame in enumerate(_shot_frames(seg, plan.layout, fps)):
                p = fi / max(1, seg.out_frames - 1)
                zoom, drift = camera(seg.move, p, strength)
                shift, blur, flash = 0.0, 0, 0.0
                if t_in == "open" and fi < 12:
                    zoom *= 1 + 0.22 * (1 - ease(fi / 12)) ** 2
                elif t_in == "punch" and fi < 7:
                    zoom *= 1 + 0.14 * (1 - fi / 7) ** 3
                    flash = max(flash, [0.42, 0.18, 0.05][fi] if fi < 3 else 0)
                elif t_in == "whip" and fi < 5:
                    q = 1 - fi / 5
                    shift, blur = 0.30 * q * q, int(70 * q)
                elif t_in == "flash" and fi < 6:
                    flash = max(flash, 0.75 * (1 - fi / 6) ** 1.5)
                left = seg.out_frames - 1 - fi
                if t_out == "whip" and left < 5:
                    q = 1 - left / 5
                    shift, blur = -0.30 * q * q, int(70 * q)
                elif t_out == "flash" and left < 3:
                    flash = max(flash, 0.75 * (1 - left / 3))

                if plan.layout == "fill":
                    img = _compose_fill(src_frame, zoom, drift, shift)
                else:
                    bg_zoom = 1.06 + 0.04 * (gi / max(1, plan.duration * fps))
                    img = _compose_frame(src_frame, seg.focus_x, zoom, drift, shift, bg_zoom)
                img = motion_blur(img, blur)
                img = look.apply(img, gi)
                if flash > 0:
                    img = cv2.addWeighted(img, 1 - flash, np.full_like(img, 255), flash, 0)
                enc.stdin.write(np.ascontiguousarray(img).tobytes())
                gi += 1
    except (BrokenPipeError, OSError) as e:
        enc.kill()
        errlog.close()
        raise RenderError(f"encoder stopped: {(work / 'ffmpeg_encode.log').read_text(errors='ignore')[-800:]} {e}")
    finish_encoder(enc, errlog, work)
