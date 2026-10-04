"""Sound: synthesized whooshes/impacts, music bed (your tracks, or a generated ambient pad), voice polish,
ducking under the narrator, and loudness normalization to YouTube's -14 LUFS."""
from __future__ import annotations

import json
import random
import subprocess
import wave
from pathlib import Path

import numpy as np

from .config import MUSIC_DIR
from .media import FFMPEG, run

SR = 48000
VOICE_CHAIN = (
    "highpass=f=75,"
    "equalizer=f=200:t=q:w=1.0:g=1.5,"
    "equalizer=f=3200:t=q:w=1.2:g=2.5,"
    "equalizer=f=7500:t=q:w=2.0:g=-1.5,"
    "acompressor=threshold=-21dB:ratio=3:attack=4:release=80:makeup=2"
)


# ---- io ------------------------------------------------------------------
def load(path: Path, start: float = 0.0, duration: float | None = None) -> np.ndarray:
    cmd = [FFMPEG, "-v", "error"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    return np.frombuffer(run(cmd).stdout, np.float32).copy()


def save(arr: np.ndarray, path: Path) -> None:
    pcm = (np.clip(arr, -1, 1) * 32767).astype(np.int16)
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())


def db(x: float) -> float:
    return 10 ** (x / 20)


# ---- synthesis -----------------------------------------------------------
def _bandpass_sweep(noise: np.ndarray, f0: float, f1: float, q: float) -> np.ndarray:
    out = np.zeros_like(noise)
    n = len(noise)
    x1 = x2 = y1 = y2 = 0.0
    block = 64
    for s in range(0, n, block):
        f = f0 * (f1 / f0) ** (s / n)
        w0 = 2 * np.pi * f / SR
        alpha = np.sin(w0) / (2 * q)
        b0, b2 = alpha, -alpha
        a0, a1, a2 = 1 + alpha, -2 * np.cos(w0), 1 - alpha
        b0, b2, a1, a2 = b0 / a0, b2 / a0, a1 / a0, a2 / a0
        for i in range(s, min(n, s + block)):
            x0 = noise[i]
            y0 = b0 * x0 + b2 * x2 - a1 * y1 - a2 * y2
            out[i] = y0
            x2, x1, y2, y1 = x1, x0, y1, y0
    return out


def whoosh(seed: int, length: float = 0.5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(SR * length)
    noise = rng.standard_normal(n).astype(np.float64)
    lo, hi = rng.uniform(350, 600), rng.uniform(3500, 6000)
    sig = _bandpass_sweep(noise, lo, hi, q=1.4)
    t = np.linspace(0, 1, n)
    peak = 0.62
    env = np.where(t < peak, (t / peak) ** 2.2, np.exp(-(t - peak) * 9))
    sig = sig * env
    return (sig / (np.abs(sig).max() + 1e-9) * 0.55).astype(np.float32)


def impact(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(SR * 1.3)
    t = np.arange(n) / SR
    freq = 38 + 80 * np.exp(-t * 9)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    boom = np.sin(phase) * np.exp(-t * 3.2)
    click = rng.standard_normal(n) * np.exp(-t * 60)
    click = np.convolve(click, np.ones(12) / 12, mode="same")
    sig = boom * 0.9 + click * 0.35
    return (sig / (np.abs(sig).max() + 1e-9) * 0.7).astype(np.float32)


def _reverb(sig: np.ndarray, seconds: float = 2.6, wet: float = 0.35, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(SR * seconds)
    ir = rng.standard_normal(n) * np.exp(-np.linspace(0, 7, n))
    ir = np.convolve(ir, np.ones(8) / 8, mode="same")  # darken the tail
    ir /= np.sqrt(np.sum(ir ** 2))
    size = 1 << int(np.ceil(np.log2(len(sig) + n)))
    tail = np.fft.irfft(np.fft.rfft(sig, size) * np.fft.rfft(ir, size), size)[: len(sig)]
    return (1 - wet) * sig + wet * tail * 0.6


def ambient_pad(duration: float, seed: int) -> np.ndarray:
    """Fallback music: a slow, warm minor-key pad with a soft heartbeat pulse. Used only when assets/music is empty."""
    rng = np.random.default_rng(seed)
    progressions = [
        [[50, 57, 60, 64, 65], [46, 53, 58, 62, 69], [43, 50, 55, 58, 65], [45, 52, 57, 61, 64]],  # Dm9 Bbmaj7 Gm7 A
        [[45, 52, 57, 60, 64], [41, 48, 53, 57, 64], [48, 55, 60, 64, 67], [43, 50, 55, 59, 62]],  # Am F C G
        [[52, 59, 64, 67, 71], [48, 55, 60, 64, 67], [43, 50, 55, 59, 62], [50, 57, 62, 66, 69]],  # Em C G D
    ]
    prog = progressions[rng.integers(len(progressions))]
    bpm = float(rng.choice([84, 88, 92]))
    chord_len = 60 / bpm * 8
    n = int(SR * (duration + 1.0))
    t = np.arange(n) / SR
    out = np.zeros(n)
    for ci in range(int(np.ceil(duration / chord_len)) + 1):
        chord = prog[ci % len(prog)]
        start = ci * chord_len
        env = np.clip((t - start) / 0.9, 0, 1) * np.clip((start + chord_len + 0.9 - t) / 0.9, 0, 1)
        if not env.any():
            continue
        for k, note in enumerate(chord):
            f = 440 * 2 ** ((note - 69) / 12)
            amp = 0.11 if k else 0.16
            for cents in (-5, 0, 6):
                ff = f * 2 ** (cents / 1200)
                out += amp * env * (np.sin(2 * np.pi * ff * t + rng.uniform(0, 6.28)) + 0.18 * np.sin(4 * np.pi * ff * t))
        sub = 440 * 2 ** ((chord[0] - 12 - 69) / 12)
        out += 0.22 * env * np.sin(2 * np.pi * sub * t)
    beat = 60 / bpm
    pulse = 0.78 + 0.22 * np.clip(((t % beat) / beat) * 2.5, 0, 1)
    out *= pulse
    kick_n = int(SR * 0.35)
    kt = np.arange(kick_n) / SR
    kick = np.sin(2 * np.pi * np.cumsum(45 + 90 * np.exp(-kt * 30)) / SR) * np.exp(-kt * 9)
    for b in np.arange(0, duration + 1, beat * 2):
        i = int(b * SR)
        seg = out[i : i + kick_n]
        seg += 0.35 * kick[: len(seg)]
    out = _reverb(out)
    return (out / (np.abs(out).max() + 1e-9) * 0.8).astype(np.float32)[: int(SR * duration)]


def music_credits() -> dict:
    path = MUSIC_DIR / "credits.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def credit_line(track: str) -> str:
    """Attribution required by the track's licence (empty for the generated pad / unlisted tracks)."""
    c = music_credits().get(track)
    if not c:
        return ""
    return (f'Music: "{c["title"]}" {c["artist"]} ({c["source"].replace("https://", "")}), '
            f'licensed under {c["license"]} {c["license_url"]}')


def music_bed(duration: float, seed: int, mood: str = "") -> tuple[np.ndarray, str]:
    """A random section of a random track — matching the clip's mood when tracks are tagged in credits.json."""
    rng = random.Random(seed)
    tracks = sorted(p for p in MUSIC_DIR.glob("*") if p.suffix.lower() in {".mp3", ".wav", ".m4a", ".ogg", ".flac"})
    moods = music_credits()
    matching = [p for p in tracks if moods.get(p.name, {}).get("mood") == mood]
    tracks = matching or tracks
    if tracks:
        track = rng.choice(tracks)
        try:
            total = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                               "default=nw=1:nk=1", str(track)]).stdout.strip() or 0)
            start = rng.uniform(0, max(0.0, total - duration - 2)) if total > duration + 4 else 0.0
            sig = load(track, start, duration + 0.5)
            if len(sig) >= int(SR * duration * 0.9):
                return sig, track.name
        except subprocess.CalledProcessError:
            pass
    return ambient_pad(duration, seed), "generated-pad"


# ---- mix -----------------------------------------------------------------
def polish_voice(src: Path, dst: Path) -> None:
    run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-af", VOICE_CHAIN, "-ar", str(SR), "-ac", "1", str(dst)])


def _duck_gain(voice: np.ndarray, base_db: float, duck_db: float) -> np.ndarray:
    hop = SR // 100
    frames = len(voice) // hop + 1
    padded = np.pad(voice, (0, frames * hop - len(voice)))
    rms = np.sqrt(np.mean(padded.reshape(frames, hop) ** 2, axis=1))
    active = np.clip((20 * np.log10(rms + 1e-9) + 45) / 15, 0, 1)  # 0 = silence, 1 = speech
    g = np.empty(frames)
    cur = 0.0
    for i, a in enumerate(active):
        coef = 0.35 if a > cur else 0.06  # fast attack, slow release
        cur += (a - cur) * coef
        g[i] = cur
    gains = db(base_db) * db(duck_db) ** g
    return np.interp(np.arange(len(voice)) / hop, np.arange(frames), gains)


def mix(voice_wav: Path, duration: float, sfx_events: list[tuple[float, np.ndarray, float]], music: np.ndarray,
        music_cfg: dict, out: Path, work: Path) -> None:
    n = int(SR * duration)
    polished = work / "voice_polished.wav"
    polish_voice(voice_wav, polished)
    voice = load(polished)[:n]
    voice = np.pad(voice, (0, n - len(voice)))

    m = np.pad(music[:n], (0, max(0, n - len(music))))
    fade_in, fade_out = int(SR * 0.25), int(SR * 1.2)
    m[:fade_in] *= np.linspace(0, 1, fade_in)
    m[-fade_out:] *= np.linspace(1, 0, fade_out)
    m_rms = np.sqrt(np.mean(m ** 2)) + 1e-9
    m = m / m_rms * 0.1  # normalize the bed before applying the configured level
    m *= _duck_gain(voice, music_cfg.get("volume_db", -21) + 20, music_cfg.get("duck_db", -9))

    fx = np.zeros(n, np.float32)
    for at, sig, gain_db in sfx_events:
        i = int(at * SR)
        if 0 <= i < n:
            seg = sig[: n - i]
            fx[i : i + len(seg)] += seg * db(gain_db)

    total = voice + m + fx
    raw = work / "mix_raw.wav"
    save(total / max(1.0, float(np.abs(total).max())), raw)
    loudnorm(raw, out)


def loudnorm(src: Path, dst: Path, target: float = -14.0) -> None:
    first = subprocess.run([FFMPEG, "-hide_banner", "-i", str(src), "-af",
                            f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True, check=True).stderr
    j = json.loads(first[first.rfind("{") : first.rfind("}") + 1])
    af = (f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={j['input_i']}:measured_TP={j['input_tp']}:"
          f"measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-af", af, "-ar", str(SR), str(dst)])
