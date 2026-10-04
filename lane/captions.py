"""Animated subtitles (ASS, rendered by libass inside FFmpeg).

Style: 1-3 word chunks in heavy uppercase Montserrat; the spoken word lights up gold with a small pop,
key words pop harder. A Cinzel gold hook title opens the Short; a quiet brand mark sits at the top.
"""
from __future__ import annotations

import re
from pathlib import Path

W, H = 1080, 1920
GOLD = "&H0048C9F7&"      # #F7C948 in ASS BGR order
WHITE = "&H00FFFFFF&"

HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Montserrat Black,98,&H00FFFFFF,&H00FFFFFF,&H00000000,&H8C000000,0,0,0,0,100,100,1,0,1,6,4,5,90,90,0,1
Style: Hook,Cinzel,80,&H0048C9F7,&H0048C9F7,&H00140E08,&H90000000,-1,0,0,0,100,100,3,0,1,4,6,5,80,80,0,1
Style: Bar,Cinzel,10,&H0048C9F7,&H0048C9F7,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Mark,Cinzel,30,&H70FFFFFF,&H70FFFFFF,&HB0000000,&HB0000000,-1,0,0,0,100,100,7,0,1,1.5,0,8,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")")


def _norm(s: str) -> str:
    return re.sub(r"[^\w]", "", s.lower())


def _sentence_breaks(words: list[dict], script: str) -> set[int]:
    """Indexes of words followed by punctuation in the script (so chunks never straddle a sentence)."""
    tokens = script.split()
    breaks, j = set(), 0
    for i, w in enumerate(words):
        target = _norm(w["text"])
        for k in range(j, min(j + 4, len(tokens))):
            if _norm(tokens[k]) == target:
                if re.search(r"[.,!?;:—-]['\"”]?$", tokens[k]):
                    breaks.add(i)
                j = k + 1
                break
    return breaks


def chunk_words(words: list[dict], script: str, max_words: int = 3, max_chars: int = 16) -> list[list[int]]:
    breaks = _sentence_breaks(words, script)
    chunks, cur, chars = [], [], 0
    for i, w in enumerate(words):
        n = len(w["text"])
        if cur and (len(cur) >= max_words or chars + n + 1 > max_chars):
            chunks.append(cur)
            cur, chars = [], 0
        cur.append(i)
        chars += n + 1
        if i in breaks:
            chunks.append(cur)
            cur, chars = [], 0
    if cur:
        chunks.append(cur)
    return chunks


def _wrap_hook(text: str, max_line: int = 14) -> str:
    words, lines, cur = text.upper().split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_line:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return r"\N".join(lines[:3])


def build_ass(path: Path, *, words: list[dict], script: str, emphasis: list[str], hook: str,
              watermark: str, duration: float, caption_y: int, hook_y: int) -> None:
    emph = {_norm(e) for e in emphasis}
    ev: list[str] = []

    def add(start, end, style, text, layer=0):
        if end - start >= 0.02:
            ev.append(f"Dialogue: {layer},{_ts(start)},{_ts(end)},{style},,0,0,0,,{text}")

    if watermark:
        add(0, duration, "Mark", rf"{{\an8\pos({W // 2},118)}}{_esc(watermark.upper())}", layer=1)

    hook_end = 0.0
    if hook:
        hook_end = min(2.4, duration)
        hook_txt = _wrap_hook(_esc(hook))
        n_lines = hook_txt.count(r"\N") + 1
        # soft dark halo underneath, so gold stays readable over bright skies and white walls
        add(0, hook_end, "Hook",
            rf"{{\an5\pos({W // 2},{hook_y})\fad(80,260)\1a&HFF&\3c&H000000&\3a&H60&\bord18\blur16\shad0"
            rf"\fscx118\fscy118\t(0,260,0.6,\fscx100\fscy100)}}{hook_txt}", 2)
        add(0, hook_end, "Hook",
            rf"{{\an5\pos({W // 2},{hook_y})\fad(80,260)\fscx118\fscy118\t(0,260,0.6,\fscx100\fscy100)}}{hook_txt}", 3)
        bar_y = hook_y + n_lines * 48 + 22
        add(0.12, hook_end, "Bar",
            rf"{{\an5\pos({W // 2},{bar_y})\fad(0,260)\fscx0\t(0,380,0.5,\fscx100)\p1}}m -110 0 l 110 0 l 110 5 l -110 5{{\p0}}", 3)

    chunks = chunk_words(words, script)
    for ci, idxs in enumerate(chunks):
        c_start = words[idxs[0]]["start"]
        nxt = words[chunks[ci + 1][0]]["start"] if ci + 1 < len(chunks) else duration
        last_end = words[idxs[-1]]["end"]
        c_end = nxt if nxt - last_end < 0.6 else last_end + 0.3
        c_end = min(c_end, duration)
        for k, wi in enumerate(idxs):
            w_start = c_start if k == 0 else words[wi]["start"]
            w_end = words[idxs[k + 1]]["start"] if k + 1 < len(idxs) else c_end
            parts = []
            for j in idxs:
                t = _esc(words[j]["text"].upper())
                if j == wi:
                    big = _norm(words[j]["text"]) in emph
                    s0, s1 = (128, 116) if big else (114, 106)
                    glow = r"\3c&H0A2A4A&\bord7" if big else ""
                    parts.append(rf"{{\c{GOLD}{glow}\fscx{s0}\fscy{s0}\t(0,110,\fscx{s1}\fscy{s1})}}{t}"
                                 rf"{{\c{WHITE}\3c&H000000&\bord6\fscx100\fscy100}}")
                else:
                    parts.append(t)
            intro = r"\fad(40,0)\fscx82\fscy82\t(0,90,\fscx100\fscy100)" if k == 0 else ""
            # The first chunk waits for the hook title to clear a little, so they never fight for attention.
            add(max(w_start, 0.0), w_end, "Cap", rf"{{\an5\pos({W // 2},{caption_y}){intro}}}" + " ".join(parts), 2)

    path.write_text(HEADER + "\n".join(ev) + "\n", encoding="utf-8")
