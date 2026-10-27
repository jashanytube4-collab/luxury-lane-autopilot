"""Automatic re-casting of a story's pictures from the tagged clip library (no Claude credits).

    python -m tools.recast <story> split      split long sentences (>14 words) into punchy beats (shows are shared out)
    python -m tools.recast <story> cast       choose for every beat fresh, relevant, non-repeating clips (the gate's own rules)
    python -m tools.recast <story> cues       add SFX / light-leak cues so the editing-cue density passes

A token that already passes (relevant, unused, not a duplicate) is kept; only failing parts change.
Beats it cannot fill are reported (the sentence then needs other wording or new footage). Writes the story file in place."""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools import prerender_gate as G  # noqa: E402
from tools.storyboard import find  # noqa: E402


def load(path: Path):
    head = "".join(l for l in path.read_text(encoding="utf-8").splitlines(True) if l.startswith("#"))
    return head, yaml.safe_load(path.read_text(encoding="utf-8"))


def save(path: Path, head: str, s: dict) -> None:
    path.write_text(head + yaml.safe_dump(s, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")


def split(s: dict) -> int:
    """Every beat except the very first becomes one beat per sentence; sentences over 14 words are cut at a comma/conjunction."""
    n = 0
    first = True
    for sec in s["sections"]:
        out = []
        for b in sec["beats"]:
            if first:
                first = False
                out.append(b)
                continue
            say = str(b["say"]).strip()
            parts = [x.strip() for x in re.split(r"(?<=[.?!])\s+(?=[A-Z0-9])", say) if x.strip()]
            final = []
            for p_ in parts:
                w = p_.split()
                if len(w) > 20:
                    cuts = [m.end() for m in re.finditer(r"[,;:] | (?=(?:and|but|so|because|while|which|yet) )", p_)]
                    cuts = [c for c in cuts if len(p_[:c].split()) >= 4 and len(p_[c:].split()) >= 4]
                    if cuts:
                        c = min(cuts, key=lambda x: abs(x - len(p_) / 2))
                        a_, b_ = p_[:c].strip().rstrip(",;:"), p_[c:].strip()
                        if not a_.endswith((".", "?", "!")):
                            a_ += "."
                        final += [a_, b_[0].upper() + b_[1:]]
                        continue
                final.append(p_)
            if len(final) == 1:
                out.append(b)
                continue
            shows = list(b.get("show") or [])
            tw = sum(len(x.split()) for x in final)
            idx = 0
            for k, x in enumerate(final):
                cnt = round(len(shows) * len(x.split()) / tw) if k < len(final) - 1 else len(shows) - idx
                nb = {"say": x, "show": shows[idx: idx + max(0, cnt)]}
                idx += max(0, cnt)
                if k == 0:
                    nb.update({kk: v for kk, v in b.items() if kk in ("fx", "sfx")})
                out.append(nb)
            n += len(final) - 1
        sec["beats"] = out
    return n


def beat_spans(s: dict):
    voices, _ = G.voice_timing(s, True)
    shots = G.timeline(s, voices)
    sp: dict = {}
    for x in shots:
        k = (x["sec"], x["beat"])
        a, b = sp.get(k, (x["t0"], x["t1"]))
        sp[k] = (min(a, x["t0"]), max(b, x["t1"]))
    return sp, (shots[-1]["total"] if shots else 0)


def cast(s: dict) -> list[str]:
    tags = yaml.safe_load(G.TAGS.read_text(encoding="utf-8"))
    vocab = set()
    for t in tags.values():
        vocab |= {G.canon(x) for x in t.get("tags", [])}
    vocab -= G.STOP
    spans, total = beat_spans(s)
    files = {t: find(t) for t in tags if find(t)}
    G.face_pass(files, tags)
    hashes = {t: G.dhashes(p) for t, p in files.items()}
    dur = {t: G.duration(p) for t, p in files.items()}
    asp: dict = {}

    def wide(t):
        if t not in asp:
            asp[t] = (G.aspect_ok(files[t]) == "")
        return asp[t]

    def event(t):
        spec = G.CLIPS.get(t[5:], {}) if t.startswith("clip:") else {}
        return "event:" + str(spec.get("yt") or spec.get("ig") or spec.get("credit") or t)

    used: list[str] = []
    ev_sec: dict = {}
    cap = 0.22 * total
    notes: list[str] = []
    prev_tok, prev_hold = None, 0.0

    sigs: dict = {}
    when: dict = {}                     # token -> start time of the beat that took it

    def sig(t):
        if t not in sigs:
            sigs[t] = G.colour_sig(files[t])
        return sigs[t]

    def dupe(t, t0=0.0):
        for u in used:
            if u not in hashes:
                continue
            if max(G.near_dup(hashes[t], hashes[u]), G.near_dup(hashes[u], hashes[t])) >= 0.34:
                return True
            src = G.source_of(t)
            shoot = t.startswith("sb-") and u.startswith("sb-") and abs(int(t[3:]) - int(u[3:])) < 5000
            near = t.startswith("sb-") and u.startswith("sb-") and abs(t0 - when.get(u, -99)) < 14
            if ((src and src == G.source_of(u)) or shoot) and abs(t0 - when.get(u, -99)) > 15 and G.colour_dist(sig(t), sig(u)) < 40:
                return True             # another cut of the same video / Storyblocks shoot reads as a repeat
            if near and G.colour_dist(sig(t), sig(u)) < 32:
                return True             # the same kind of stock picture seconds apart
        return False

    def usable(t, say, slot, early, need_real, t0=0.0):
        if t in used or t not in files or tags[t].get("text_graphic"):
            return None
        if t in owner and owner[t] > here[0]:
            return None
        if dur[t] + 0.3 < slot:
            return None
        sb = t.startswith("sb-")
        if sb and (early or need_real):
            return None
        if sb and tags[t].get("people") and tags[t].get("arab_people") is not True:
            return None
        if tags[t].get("not_hamdan") and not re.search(r"\b(president|mohamed bin zayed|sheikh mohamed)\b", say, re.I):
            return None
        cred = str(G.CLIPS.get(t[5:], {}).get("credit", "")) if t.startswith("clip:") else ""
        if G.OTHER_STORY.search(cred) and not G.OTHER_STORY_TOPIC.search(say):
            return None
        if early and (not wide(t) or tags[t].get("hamdan", 0) < 0.3):
            return None
        if ev_sec.get(event(t), 0) + slot > cap and not sb:
            return None
        v = G.relevance(t, say, tags[t], vocab)
        if v not in ("ok", "uncertain"):
            return None
        if dupe(t, t0):
            return None
        conc = G.words_of(say) & vocab
        cw = {G.canon(x) for x in tags[t].get("tags", [])}
        return 10 * len(cw & conc) + (3 if v == "ok" else 0) + 4 * tags[t].get("hamdan", 0) + (1 if 2.5 <= dur[t] <= 8 else 0)

    beats = [(i, j, b) for i, sec in enumerate(s["sections"]) for j, b in enumerate(sec["beats"])]
    last = (len(s["sections"]) - 1, len(s["sections"][-1]["beats"]) - 1)
    # an exact-moment clip (clips.yaml match:) belongs to the sentence it matches: never filler for an earlier line
    owner: dict = {}
    for i, j, b in beats:
        for t, spec in G.CLIPS.items():
            if spec.get("match") and f"clip:{t}" not in owner and re.search(spec["match"], str(b["say"]), re.I):
                owner[f"clip:{t}"] = (i, j)
    here = [(0, 0)]
    for i, j, b in beats:
        say = str(b["say"])
        here[0] = (i, j)
        t0, t1 = spans[(i, j)]
        d = t1 - t0
        early = t0 < 30
        n = max(1, round(d / 2.0)) if early else max(1, math.ceil(d / 4.2))
        slot = d / n
        named = bool(re.search(r"\b(hamdan|fazza|prince)\b", say, re.I))
        person = bool(G.PERSON.search(say))
        chosen: list[str] = []
        for tok in [str(x) for x in (b.get("show") or []) if not str(x).startswith("card:")]:
            spec = G.CLIPS.get(tok[5:], {}) if tok.startswith("clip:") else {}
            exact = bool(spec.get("match")) and re.search(spec["match"], say, re.I) and tok not in used
            if exact and tok not in chosen:     # the hand-matched exact moment of this sentence is always kept
                chosen.append(tok)
                used.append(tok)
                when[tok] = t0
                continue
            if len(chosen) < n and tok not in chosen and usable(tok, say, slot, early, False, t0) is not None:
                chosen.append(tok)
                used.append(tok)
                when[tok] = t0
        while len(chosen) < n:
            need_real = (named or person) and not any(not c.startswith("sb-") for c in chosen)
            if (i, j) == last and len(chosen) == n - 1:
                need_real = True
            scored = [(usable(t, say, slot, early, need_real, t0), t) for t in tags]
            scored = [x for x in scored if x[0] is not None]
            if not scored:
                break
            best = max(scored)
            chosen.append(best[1])
            used.append(best[1])
            when[best[1]] = t0
        if not chosen and prev_tok and d < 2.6 and dur.get(prev_tok, 0) >= prev_hold + d + 0.3:
            chosen = [prev_tok]                 # a very short line keeps the picture of the line before (one continuous shot)
            prev_hold += d
            b["show"] = chosen
            continue
        if not chosen:
            notes.append(f"NO CLIP for beat {i}.{j} @{G.fmt(t0)}: \"{say[:90]}\"")
            chosen = [str(x) for x in (b.get("show") or [])][:1]
        elif len(chosen) < n and early:
            notes.append(f"short of clips @{G.fmt(t0)} ({len(chosen)}/{n}): \"{say[:70]}\"")
        chosen.sort(key=lambda c: (c.startswith("sb-") and (named or person)))
        for c in chosen:
            ev_sec[event(c)] = ev_sec.get(event(c), 0) + slot
        b["show"] = chosen
        if chosen:
            prev_tok, prev_hold = chosen[-1], slot
    return notes


def cues(s: dict) -> int:
    spans, total = beat_spans(s)
    order = sorted(spans.items(), key=lambda kv: kv[1][0])
    sec_start: dict = {}
    for (i, j), (a, _) in order:
        sec_start.setdefault(i, a)
    sfx = [0.0] + [a - 0.25 for i, a in sec_start.items() if i]
    fx = [a for i, a in sec_start.items() if i]

    def beat(k):
        return s["sections"][k[0]]["beats"][k[1]]

    for (i, j), (a, _) in order:
        b = beat((i, j))
        for c in str(b.get("sfx", "")).split("+"):
            if c:
                sfx.append(a - (0.3 if c == "whoosh" else 2.4 if c == "riser" else 0))
        if b.get("fx") in ("leak", "flash"):
            fx.append(a)
    added = [0]

    def fill(lst, limit, key, vals, start, end):
        n = 0
        while n < 200:
            ts = sorted(t for t in lst if start <= t <= end)
            cur, gap = start, None
            for t in ts + [end]:
                if t - cur > limit:
                    gap = cur
                    break
                cur = t
            if gap is None:
                return
            want = gap + limit * 0.7
            cand = [(abs(a - want), k) for k, (a, _) in order if gap + 3 < a < end and not beat(k).get(key)]
            if not cand:
                return
            _, k = min(cand)
            beat(k)[key] = vals[n % len(vals)]
            lst.append(spans[k][0] - (0.3 if key == "sfx" else 0))
            n += 1
            added[0] += 1

    fill(sfx, 9, "sfx", ["whoosh", "impact", "whoosh"], 0, 30)
    fill(sfx, 22, "sfx", ["whoosh", "impact", "riser+impact", "whoosh"], 0, total)
    fill(fx, 34, "fx", ["flash", "leak"], 0, total)
    return added[0]


def main() -> None:
    path = G.story_path(sys.argv[1])
    mode = sys.argv[2]
    head, s = load(path)
    if mode == "split":
        print("split", split(s), "beats")
    elif mode == "cast":
        for n in cast(s):
            print(n)
    elif mode == "cues":
        print("cues added:", cues(s))
    save(path, head, s)


if __name__ == "__main__":
    main()
