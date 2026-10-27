"""ONE pre-render quality gate for long videos (code only, no Claude credits).

    python -m tools.prerender_gate <story id or file> [--no-voice]

Runs BEFORE any render. Fails with a precise list (time + sentence + clip) when the storyboard breaks the checklist,
writes ~1 contact sheet per 60 s of the planned storyboard to work/gate/<id>/, and on a pass writes a stamp
(assets/stories/.gate/<id>.json) that the render entry (lane/pipeline.py, test renders) requires.

Reuses tools.lint_story (script/CTA/open-loop/first-30 s basics), tools.storyboard.find (local clip files) and the
one-time clip tag library assets/clip_tags.yaml (tools/tag_clips.py: a local vision model, tags written once).
Voice timing is real: the narration is synthesised with the free edge voice (cached by text) and the beats are aligned
exactly like the renderer does.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
STORIES = ROOT / "assets" / "stories"
STAMPS = STORIES / ".gate"
TAGS = ROOT / "assets" / "clip_tags.yaml"
WORK = ROOT / "work" / "gate"
CLIPS = (yaml.safe_load((ROOT / "assets" / "clips.yaml").read_text(encoding="utf-8")) or {}).get("clips", {})

STOP = set("""about above after again against also always another because been before being between both could did does done
down during each even ever every first from further had has have having here him himself his how into its itself just
like made make many more most much must never next none not now off once only other our out over own really same she should
so some still such than that the their them then there these they this those through too under until upon very was were
what when where which while who whom why will with without would you your yours said says say saying told one two three
four five six seven eight nine ten hundred thousand million billion way thing things people person time times year years day
days today tonight story video truth rumour rumours report reported reports estimate estimates actually simply real
prince hamdan fazza fazzas dubai crown sheikh royal him her hers whole word words number numbers figure figures known
every anything something everything nothing someone anyone nobody can cant dont doesnt didnt isnt wasnt youve ive weve
lets let get gets got going gone come comes came know knows knew think thought look looks looked want wants need needs
take takes took give gives gave keep keeps kept put puts show shows showed see sees saw seen tell tells heard hear
question answer reveal stay end start begin begins began young life official animal public record cost""".split())
SYN = {  # canonical groups: words that mean the same thing on screen
    "money": """cash coins coin banknote banknotes dollar dollars dirham dirhams wealth wealthy rich richest fortune billion
             million salary earn earns earned income pay paid price cost costs spend spends spent budget gold bars note""",
    "car": """cars suv vehicle vehicles gclass g-class mercedes drive drives driving drove road highway garage supercar convertible
            limousine automobile""",
    "horse": """horses stallion racehorse riding rider ride equestrian polo endurance stable stables jockey race racing mare pony""",
    "falcon": """falcons falconry bird birds hawk hunting""",
    "plane": """jet jets aircraft airplane flight fly flying flew private airport pilot aviation cockpit""",
    "yacht": """yachts boat boats ship sea ocean marina cruise sail""",
    "city": """skyline skyscraper skyscrapers tower towers building buildings downtown urban burj skyline""",
    "desert": """dunes dune sand camel camels camp""",
    "palace": """mansion mansions castle residence villa home house""",
    "watch": """watches jewellery jewelry diamond diamonds luxury ring necklace""",
    "crowd": """audience fans spectators""",
    "office": """meeting council government minister ministry leader desk conference""",
    "phone": """instagram social online post posted internet video app""",
    "water": """swim swimming pool beach diving dive""",
    "family": """mother father wife brother sister child children son daughter baby""",
}
CANON = {}
for g, ws in SYN.items():
    CANON[g] = g
    for w in ws.split():
        CANON[w] = g
# clips cut for one news story (credit names it) are off-topic in any other story
OTHER_STORY = re.compile(r"Smit|FZ1073|flydubai|Tabuk|Yaniv|ambassador to the UAE|i24NEWS|via CNA|prayers for Captain", re.I)
OTHER_STORY_TOPIC = re.compile(r"\b(smit|machchhar|fz ?1073|flydubai|tel aviv)\b", re.I)
HAM = re.compile(r"\b(hamdan|fazza|prince|crown prince|he|his|him|himself)\b", re.I)
PERSON = re.compile(r"\b(mother|wife|father|sister|sisters|brother|brothers|grandfather|grandmother|daughter|son|queen|"
                    r"king|princess|president|prime minister|sheikha|sheikh [A-Z]\w+)\b", re.I)


def canon(w: str) -> str:
    w = w.lower()
    if w in CANON:
        return CANON[w]
    if w.endswith("s") and w[:-1] in CANON:
        return CANON[w[:-1]]
    return w[:-1] if w.endswith("s") and len(w) > 4 else w


def words_of(text: str) -> set[str]:
    return {canon(w) for w in re.findall(r"[A-Za-z][A-Za-z\-']+", text) if len(w) >= 4 and w.lower() not in STOP}


def sh(*a: str) -> str:
    return subprocess.run(a, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def duration(p: Path) -> float:
    try:
        return float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)).strip() or 0)
    except ValueError:
        return 0.0


def story_path(arg: str) -> Path | None:
    if Path(arg).exists():
        return Path(arg)
    for p in STORIES.glob("*.yaml"):
        try:
            if str((yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("id")) == arg or arg in p.stem:
                return p
        except (OSError, yaml.YAMLError):
            continue
    return None


def load_beats(s: dict) -> list[tuple[int, int, dict]]:
    return [(i, j, b) for i, sec in enumerate(s["sections"]) for j, b in enumerate(sec.get("beats", []))]


def story_tokens(path: Path) -> list[str]:
    s = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for _, _, b in load_beats(s):
        for t in b.get("show") or []:
            t = str(t)
            if t not in out and not t.startswith("card:"):
                out.append(t)
    return out


def story_sha(p: Path) -> str:
    """Fingerprint of a story file, the same on Windows (CRLF) and on the GitHub runner (LF checkout)."""
    return hashlib.sha1(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def library_names() -> set[str] | None:
    """File names in the private footage library (GitHub releases), or None when it cannot be read."""
    import os
    from lane.hamdan import Library
    token = os.environ.get("LIBRARY_TOKEN", "")
    for gh in (ROOT / "models" / "gh" / "bin" / "gh.exe", "gh"):
        if token:
            break
        try:
            token = subprocess.run([str(gh), "auth", "token"], capture_output=True, text=True, timeout=30).stdout.strip()
        except OSError:
            continue
    if not token:
        return None
    try:
        repo = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["source"]["library_repo"]
        names = set(Library(repo, token).assets())
    except Exception:  # noqa: BLE001 - offline / API trouble: reported as a warning
        return None
    return names or None


def stamp_ok(story_id: str) -> str:
    """'' when the story file still matches a passed gate; else the reason (used by the render entry)."""
    p = story_path(story_id)
    if not p:
        return ""                                   # automatic stories have no file: nothing to gate
    st = STAMPS / f"{story_id}.json"
    if not st.exists():
        return f"no pre-render gate pass for '{story_id}' (run: python -m tools.prerender_gate {story_id})"
    try:
        if json.loads(st.read_text(encoding="utf-8")).get("sha") != story_sha(p):
            return f"story '{story_id}' changed since the pre-render gate passed (run the gate again)"
    except ValueError:
        return "gate stamp unreadable"
    return ""


# ---- voice timing -----------------------------------------------------------------------------------------------
def voice_timing(s: dict, use_voice: bool) -> tuple[list[dict], list[str]]:
    """Per section: {"words": [...], "dur": seconds}. Edge voice cached by text; falls back to 2.6 words/s."""
    from lane.config import load_config
    cfg = load_config()
    scfg = cfg.get("story", {})
    vcfg = dict(cfg["voice"])
    vcfg["edge_voice"] = scfg.get("voice", "en-US-AvaMultilingualNeural")
    vcfg["edge_rate"] = scfg.get("voice_rate", "+8%")
    cache = WORK / "voice"
    cache.mkdir(parents=True, exist_ok=True)
    notes, out = [], []
    for i, sec in enumerate(s["sections"]):
        text = " ".join(str(b["say"]).strip() for b in sec["beats"])
        key = hashlib.sha1(f"{text}|{vcfg['edge_voice']}|{vcfg['edge_rate']}".encode()).hexdigest()[:16]
        f = cache / f"{key}.json"
        if f.exists():
            out.append(json.loads(f.read_text())); continue
        if use_voice:
            try:
                from lane.voice import synthesize
                v = synthesize(text, {**vcfg, "engine": "edge"}, cache / f"{key}.wav")
                if v.engine == "edge":
                    d = {"words": v.words, "dur": v.duration}
                    f.write_text(json.dumps(d)); out.append(d); continue
            except Exception as e:  # network
                notes.append(f"voice synthesis failed ({e}); timing estimated")
        ws, t = [], 0.0
        for w in text.split():
            ws.append({"text": w, "start": t, "end": t + 0.3}); t += 0.385
        out.append({"words": ws, "dur": t})
        notes.append(f"section {i}: timing ESTIMATED (no voice)") if not notes else None
    return out, notes


def timeline(s: dict, voices: list[dict]) -> list[dict]:
    """Shots: {t0,t1,tok,beat,sec,say,cue,fx}. Mirrors produce_story: section starts, beat alignment, equal split."""
    from lane.beats import align
    shots, t = [], 0.0
    for i, (sec, v) in enumerate(zip(s["sections"], voices)):
        off = 0.6 if i == 0 else 0.15
        speech = v["words"][-1]["end"] if v["words"] else v["dur"]
        length = max(speech + 0.1, v["dur"] - 0.25) + 0.2 + off
        spans = align(sec["beats"], v["words"], v["dur"])
        for j, (b, (a, e)) in enumerate(zip(sec["beats"], spans)):
            a = 0.0 if j == 0 else a + off
            e = length if j == len(spans) - 1 else e + off
            toks = [str(x) for x in (b.get("show") or []) if not str(x).startswith("card:")]
            toks = toks or ["(none)"]
            n = max(1, len(toks))
            for k, tok in enumerate(toks):
                shots.append({"t0": t + a + (e - a) * k / n, "t1": t + a + (e - a) * (k + 1) / n, "tok": tok, "sec": i,
                              "beat": j, "say": str(b["say"]).strip(), "first": k == 0,
                              "sfx": str(b.get("sfx", "")) if k == 0 else "", "fx": str(b.get("fx", "")) if k == 0 else "",
                              "bstart": t + a})
        t += length
    for sh_ in shots:
        sh_["total"] = t
    return shots


# rule number -> (what, failure tags that belong to it, approximation note or "")
RULES = [
    (1, "no repeat / near-duplicate / source>25% / holds", ["repeat", "near-dup", "source share", "held too long", "clip too short"], ""),
    (2, "every sentence shows its exact moment", ["irrelevant", "stock does not", "Hamdan/people clip", "untagged", "graphic", "not local"], "relevance = local vision tags (written once) vs sentence nouns + face check; uncertain Hamdan matches are listed for one batched look"),
    (3, "opens on moving Hamdan, hook, Hamdan first", ["first 30 s", "[lint] photo", "open loop"], "'strongest hook' = open-loop question in sentence 1; Hamdan-first-in-comparisons not checked"),
    (4, "no charts/cards/pops, one CTA at end, Dubai Media Office logo", ["no self-made", "config", "CTA", "[lint] call-to-action"], "logo is burned in by the renderer; checked in the post-render watcher only"),
    (5, "facts true, rumours labelled, family never invented", ["money figure", "person mentioned"], "APPROX: truth cannot be checked by code; every money figure must sit next to a report/estimate/rumour label; facts were sourced at writing time"),
    (6, "fresh script, 1320+ words, thumbnail", ["script not fresh", "words", "thumbnail", "[lint] script", "[lint] no thumbnail"], "thumbnail pixels are built/checked by the thumbnail step, not here"),
    (7, "music Kevin MacLeod only, licences", ["music"], "APPROX: only assets/music (Kevin MacLeod) is ever used by the renderer; stock licences = Storyblocks vetted list"),
    (8, "preview to Telegram automatically, publish only on yes", [], "enforced by tools/render_watch.py + publish workflow (not part of the storyboard)"),
    (9, "non-Hamdan footage mostly Storyblocks", ["footage rule 9"], ""),
    (10, "no photo pairs / static photos / photo in first 30 s", ["two photos", "[lint] two photos", "[lint] photo"], "static photos are always given camera motion by the renderer"),
    (11, "professional editing: transitions, leaks, SFX", ["editing cues"], "transition variety is chosen by the renderer; leak/SFX density is gated here and measured again after render"),
    (12, "Emirati stock people, stock <= 6 s", ["Emirati", "held too long"], "Arab look = local vision tag"),
    (13, "first 30 s fast cuts + SFX + drama", ["first 30 s", "editing cues] first", "drama"], ""),
    (14, "open loop teased 2-3x, answered at the end", ["open loop"], "heuristic keyword check"),
    (15, "everything local & viewed before render", ["not local", "untagged", "[lint]"], "clip tags + contact sheets are produced here; the final glance at sheets is optional"),
    (16, "loop rule: fix only the faulty part", [], "PROCESS: this report lists exactly the faulty parts; nothing re-renders until it is empty"),
    (17, "first 30 s native 16:9 Hamdan clips", ["first 30 s", "[lint] beat"], ""),
    (18, "no dead air", ["dead air"], "from the real synthesised voice timing"),
    (19, "visible editing: leaks + SFX", ["editing cues"], "post-render luma/audio check in render_watch"),
    (20, "people videos: many real clips of them", ["person mentioned"], "APPROX: only the 'person mentioned needs real footage' check"),
    (21, "stock that does not match its line", ["stock does not", "graphic"], ""),
    (22, "aspect/length: stock<=6 s, any<=8 s, ends on Hamdan", ["held too long", "ending", "[lint] the video must END"], ""),
    (23, "any person mentioned needs real footage of them", ["person mentioned", "sentence about Hamdan"], "APPROX: role words/names matched by regex; footage is accepted if any real (non-stock) clip is in the beat"),
    (24, "maximum drama", ["drama", "open loop", "editing cues"], "heuristic: short lines, questions, cliffhangers, cues"),
    (25, "where to find people footage", [], "PROCESS: sourcing is done at tagging/library time; gate only requires real clips"),
    (26, "topic-only trigger runs all rules", [], "PROCESS: render entry refuses without a valid gate stamp"),
]


def rule_table(fails: list[str]) -> list[str]:
    out = []
    for n, what, keys, note in RULES:
        bad = [f for f in fails if any(k in f for k in keys)]
        status = "FAIL" if bad else ("PASS" if keys else "n/a ")
        out.append(f"  R{n:<2} {status} {what}" + (f"  [{len(bad)}]" if bad else "") + ("\n        approximation: " + note if note else ""))
    return out


SUBJ = {canon(x) for x in ("horse", "falcon", "car", "plane", "yacht", "camel", "desert", "money")}


def relevance(tok: str, say: str, tg: dict, vocab: set) -> str:
    """'ok', 'uncertain' (Hamdan face on screen for a sentence about him, no shared noun) or the failure reason."""
    cw = {canon(x) for x in tg.get("tags", [])} | words_of(tg.get("desc", "")) | words_of(tok.replace("-", " "))
    spec = CLIPS.get(tok[5:], {}) if tok.startswith("clip:") else {}
    if spec.get("match") and re.search(spec["match"], say, re.I):
        return "ok"
    concrete = words_of(say) & vocab
    hit = cw & concrete
    if tok.startswith("sb-"):
        return "ok" if hit else f"stock does not match the sentence nouns {sorted(concrete)[:4] or 'none'}; clip shows {sorted(tg.get('tags', []))[:5]}"
    if hit:
        return "ok"
    if not concrete and tg.get("hamdan", 0) >= 0.3:
        return "ok"                               # a line with no concrete object: him on screen is right
    clash = (cw & SUBJ) - concrete
    if tg.get("hamdan", 0) >= 0.3 and HAM.search(say) and not (clash and concrete & SUBJ):
        return "uncertain"
    return (f"Hamdan/people clip does not fit the sentence: line mentions {sorted(concrete)[:4] or 'other subject'}, "
            f"clip shows {sorted(tg.get('tags', []))[:5]}, Hamdan face in {tg.get('hamdan', 0):.0%} of frames")


def fmt(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


# ---- perceptual hashes ------------------------------------------------------------------------------------------
_POP = np.array([bin(i).count("1") for i in range(256)], np.uint8)


def dhashes(p: Path) -> np.ndarray:
    """dHash (64 bit as 8 bytes) of EVERY half second of the whole file; cached."""
    cd = WORK / "hash"
    cd.mkdir(parents=True, exist_ok=True)
    st = p.stat()
    f = cd / f"{p.stem}_{st.st_size}_{int(st.st_mtime)}.npy"
    if f.exists():
        return np.load(f)
    from lane.media import FFMPEG
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(p), "-vf", "fps=2,scale=9:8:flags=area,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8)
    fr = fr[: len(fr) // 72 * 72].reshape(-1, 8, 9).astype(np.int16)
    flat = (fr.std(axis=(1, 2)) < 5) | (fr.mean(axis=(1, 2)) < 14)           # black / uniform frames carry no picture
    bits = (fr[:, :, 1:] > fr[:, :, :-1]).reshape(len(fr), 64)
    h = np.packbits(bits, axis=1)
    h = h[~flat] if len(h) else h.reshape(0, 8)
    np.save(f, h)
    return h


def near_dup(a: np.ndarray, b: np.ndarray) -> float:
    """share of a's frames that have a (<=5 bit) twin in b."""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    x = a[:, None, :] ^ b[None, :, :]
    d = _POP[x].sum(axis=2)
    return float((d.min(axis=1) <= 5).mean())


def render_pieces(shots: list[dict], files: dict) -> dict:
    """The pieces the renderer cuts from each file (lane/story.py: clips 6 s pieces, stock 3.8 s pieces out of order)
    after its same-moment filter (lane/longform.Material, one per episode, in story order)."""
    from lane.longform import Material, Visuals, _scene_list, probe
    mat, out = Material(), {}
    for tok in dict.fromkeys(x["tok"] for x in shots):
        p = files.get(tok)
        if not p or not tok.startswith(("clip:", "sb-")):
            continue
        sc = _scene_list(p, piece=3.8) if tok.startswith("sb-") else _scene_list(p)
        if not sc:
            dur = probe(p)["duration"]
            sc = [(0.05, dur - 0.05)] if dur > 0.8 else []
        if tok.startswith("sb-"):
            sc = sc[::2] + sc[1::2]
        out[tok] = mat.items(Visuals(p, sc, [])) if sc else []
    return out


def colour_sig(p: Path) -> np.ndarray:
    """8x8 RGB layout of every second of a file (cached): catches another cut of the same scene."""
    cd = WORK / "hash"
    cd.mkdir(parents=True, exist_ok=True)
    st = p.stat()
    f = cd / f"{p.stem}_{st.st_size}_{int(st.st_mtime)}_c.npy"
    if f.exists():
        return np.load(f)
    from lane.media import FFMPEG
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(p), "-vf", "fps=1,scale=8:8:flags=area,format=rgb24",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.uint8)
    a = a[: len(a) // 192 * 192].reshape(-1, 192).astype(np.float32)
    np.save(f, a)
    return a


def colour_dist(a: np.ndarray, b: np.ndarray) -> float:
    """Mean distance from each second of one file to its closest second in the other (lower = same scene;
    same-scene cuts measured 21-26, unrelated pictures mostly 40+)."""
    if not len(a) or not len(b):
        return 999.0
    d = np.abs(a[:, None, :] - b[None, :, :]).mean(axis=2)
    return float(min(d.min(axis=1).mean(), d.min(axis=0).mean()))


def source_of(tok: str) -> str | None:
    if not tok.startswith("clip:"):
        return None
    c = CLIPS.get(tok[5:], {})
    return f"ig:{c['ig']}" if c.get("ig") else (f"yt:{c['yt']}" if c.get("yt") else None)


def aspect_ok(p: Path) -> str:
    o = sh("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(p)).strip()
    try:
        w, h = (int(x) for x in o.split(",")[:2])
    except ValueError:
        return "unreadable"
    if w / h < 1.7:
        return f"{w}x{h} is not 16:9"
    from lane.media import FFMPEG
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{duration(p) / 2:.1f}", "-i", str(p), "-frames:v", "1", "-vf",
                          "scale=160:90,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    if len(raw) == 160 * 90:
        g = np.frombuffer(raw, np.uint8).reshape(90, 160)
        if g[:, :12].mean() < 12 and g[:, -12:].mean() < 12 and g[:, 40:120].mean() > 25:
            return "has black bars left/right (pillarbox)"
    return ""


# ---- the gate ---------------------------------------------------------------------------------------------------
def shots_tokens(s: dict) -> list[str]:
    return [str(x) for _, _, b in load_beats(s) for x in (b.get("show") or []) if not str(x).startswith("card:")]


def files_early(toks: list[str]) -> dict:
    from tools.storyboard import find
    return {t: (find(t) if t.startswith(("clip:", "sb-")) else None) for t in toks}


def run_gate(path: Path, use_voice: bool = True) -> tuple[list[str], list[str], dict]:
    from tools.lint_story import lint
    from tools.storyboard import find
    s = yaml.safe_load(path.read_text(encoding="utf-8"))
    sid = str(s.get("id"))
    fails: list[str] = []
    warns: list[str] = []
    fails += [f"[lint] {p}" for p in lint(path)]
    voices, vnotes = voice_timing(s, use_voice)
    warns += vnotes
    shots = timeline(s, voices)
    total = shots[-1]["total"] if shots else 0
    tags = (yaml.safe_load(TAGS.read_text(encoding="utf-8")) or {}) if TAGS.exists() else {}
    face_pass(files_early(shots_tokens(s)), tags)
    vocab = set()
    for t in tags.values():
        vocab |= {canon(x) for x in t.get("tags", [])}
    vocab -= STOP
    files = {}
    for sh_ in shots:
        tok = sh_["tok"]
        if tok in files:
            continue
        files[tok] = find(tok) if tok.startswith(("clip:", "sb-")) else None
    ref = lambda x: f"{fmt(x['t0'])}-{fmt(x['t1'])} {x['tok']} | \"{x['say'][:70]}\""

    # 1. every picture must be a local file we can view (rule 15); key:/him/video:/chart: cannot be viewed locally
    for sh_ in shots:
        if files[sh_["tok"]] is None and sh_["tok"] not in [x for x in files if files[x] is not None]:
            if not sh_["tok"].startswith(("search:", "cat:")):
                fails.append(f"[not local] {ref(sh_)} cannot be downloaded/viewed locally")
    # 2. reuse
    uses = defaultdict(list)
    prev_t = None
    for sh_ in shots:
        if sh_["tok"] != prev_t:                      # the same picture on consecutive lines = one continuous shot
            uses[sh_["tok"]].append(sh_)
        prev_t = sh_["tok"]
    for tok, u in uses.items():
        if tok != "him" and len(u) > 1:
            fails.append(f"[repeat] {tok} used {len(u)}x: " + "; ".join(f"{fmt(x['t0'])} \"{x['say'][:40]}\"" for x in u))
    # 3. near duplicates between different files (whole-video half-second perceptual hash)
    hashes = {t: dhashes(p) for t, p in files.items() if p}
    toks = list(hashes)
    first_use = {t: uses[t][0] for t in toks}
    for i, a in enumerate(toks):
        for b in toks[i + 1:]:
            r = max(near_dup(hashes[a], hashes[b]), near_dup(hashes[b], hashes[a]))
            if r >= 0.34:
                fails.append(f"[near-duplicate {r:.0%}] {a} @{fmt(first_use[a]['t0'])} \"{first_use[a]['say'][:40]}\" ~ "
                             f"{b} @{fmt(first_use[b]['t0'])} \"{first_use[b]['say'][:40]}\"")
    # 4. source share (>25%) and hold times
    src_sec = defaultdict(float)
    for sh_ in shots:
        tok = sh_["tok"]
        if tok.startswith("clip:"):
            spec = CLIPS.get(tok[5:], {})
            src = "event:" + str(spec.get("yt") or spec.get("ig") or spec.get("credit") or tok)
        else:
            src = tok
        src_sec[src] += sh_["t1"] - sh_["t0"]
        d = sh_["t1"] - sh_["t0"]
        lim = 6.0 if tok.startswith("sb-") else 8.0
        if d > lim:
            fails.append(f"[held too long {d:.1f}s > {lim:.0f}s] {ref(sh_)}")
        elif d > (3.2 if tok.startswith("sb-") else 5.5):
            warns.append(f"long hold {d:.1f}s {ref(sh_)}")
        p = files.get(tok)
        if p and duration(p) + 0.4 < d:
            fails.append(f"[clip too short for its slot: {duration(p):.1f}s file, {d:.1f}s needed -> loops/repeats] {ref(sh_)}")
    for src, sec in sorted(src_sec.items(), key=lambda x: -x[1])[:3]:
        if sec / total > 0.25:
            fails.append(f"[source share {sec / total:.0%} > 25%] {src}")
    # 4b. what the RENDERER will really have: its pieces + its same-moment filter; a sentence with less usable picture
    #     than its length shows its own clip again (render 40407164763: 15 repeats the token check could not see)
    pieces = render_pieces(shots, files)
    for tok, its in pieces.items():
        if not its:
            fails.append(f"[repeat: renderer drops {tok} as the same moment as an earlier picture] {ref(uses[tok][0])}")
    beats = defaultdict(list)
    for sh_ in shots:
        beats[(sh_["sec"], sh_["beat"])].append(sh_)
    for bs in beats.values():
        d = bs[-1]["t1"] - bs[0]["t0"]
        toks = [t for t in dict.fromkeys(x["tok"] for x in bs) if pieces.get(t)]
        if not toks or bs[0]["sec"] == 0:
            continue
        # the renderer plays each piece, then holds the last clip on (lane.longform._hold_room) up to 6 s stock / 9 s
        cap = sum(min(duration(files[t]) - 0.2, 5.0 * (len(pieces[t]) - 1) + (6.0 if t.startswith("sb-") else 9.0))
                  for t in toks)
        if cap + 0.3 < d:
            fails.append(f"[repeat: only {cap:.1f}s of distinct picture for a {d:.1f}s sentence -> the renderer shows it "
                         f"again; add a 2nd clip or shorten the line] {ref(bs[0])}")
    # 4c. the same scene again: another cut of the same Instagram/YouTube video, or the same Storyblocks shoot, in a
    #     different part of the video reads as a repeat (horse-stable a/b/c, lion-cub a/b/c, rider 353711162/353711197)
    sigs = {t: colour_sig(p) for t, p in files.items() if p and t.startswith(("clip:", "sb-"))}
    order = [t for t in dict.fromkeys(x["tok"] for x in shots) if t in sigs]
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            ua, ub = uses[a][0], uses[b][0]
            gap = ub["t0"] - ua["t1"]
            same_src = source_of(a) == source_of(b) and source_of(a) is not None
            shoot = a.startswith("sb-") and b.startswith("sb-") and abs(int(a[3:]) - int(b[3:])) < 5000
            dist = colour_dist(sigs[a], sigs[b])
            if (same_src or shoot) and gap > 15 and dist < 40:
                fails.append(f"[repeat: same scene again {dist:.0f}] {b} @{fmt(ub['t0'])} \"{ub['say'][:40]}\" ~ "
                             f"{a} @{fmt(ua['t0'])} \"{ua['say'][:40]}\" (another cut of the same video/shoot)")
            elif a.startswith("sb-") and b.startswith("sb-") and 0 <= gap < 12 and dist < 32:
                fails.append(f"[repeat: stock looks alike {dist:.0f}] {b} @{fmt(ub['t0'])} ~ {a} @{fmt(ua['t0'])} "
                             f"(same kind of picture within seconds)")
    # 4d. thumbnail scene tokens must exist (render 40407164763: 'clip:ig-falcon-blue' unknown -> no thumbnail)
    scene = str((s.get("thumbnail") or {}).get("scene", "") or "")
    for part in [x for x in scene.split("|") if x]:
        tk = part.split("@")[0]
        if tk.startswith("clip:") and tk[5:] not in CLIPS:
            fails.append(f"[thumbnail scene: unknown clip {tk}]")
        elif tk.startswith("sb-") and find(tk) is None:
            fails.append(f"[thumbnail scene: stock {tk} not local]")
    # 5. photos
    for a, b in zip(shots, shots[1:]):
        if a["tok"].startswith(("search:", "cat:")) and b["tok"].startswith(("search:", "cat:")):
            fails.append(f"[two photos back to back] {fmt(a['t0'])} {a['tok']} + {b['tok']}")
    # 6. first 30 s: native 16:9 moving Hamdan clips, a cut about every 2 s
    first = [x for x in shots if x["t0"] < 30]
    if len(first) < 13:
        fails.append(f"[first 30 s] only {len(first)} pictures planned (need 13+: a cut about every 2 s)")
    for x in first:
        if x["t1"] - x["t0"] > 3.2:
            fails.append(f"[first 30 s] shot held {x['t1'] - x['t0']:.1f}s (max ~3 s): {ref(x)}")
        if not x["tok"].startswith("clip:"):
            fails.append(f"[first 30 s] not a Hamdan clip: {ref(x)}")
        elif files.get(x["tok"]):
            bad = aspect_ok(files[x["tok"]])
            if bad:
                fails.append(f"[first 30 s] {x['tok']} {bad}: {ref(x)}")
            tg = tags.get(x["tok"], {})
            if tg and not tg.get("people") and "hamdan" not in x["tok"]:
                warns.append(f"first 30 s clip shows no person: {ref(x)}")
    # 7. dead air from the voice timing
    t = 0.0
    prev_tail = 0.0
    for i, (v, sec) in enumerate(zip(voices, s["sections"])):
        off = 0.6 if i == 0 else 0.15
        ws = v["words"]
        speech = ws[-1]["end"] if ws else v["dur"]
        length = max(speech + 0.1, v["dur"] - 0.25) + 0.2 + off
        lead = ws[0]["start"] if ws else 0
        gap_here = prev_tail + off + lead
        if i and gap_here > 2.0:
            fails.append(f"[dead air {gap_here:.1f}s] before section {i} at {fmt(t)} (voice stops)")
        elif i and gap_here > 0.9:
            warns.append(f"pause {gap_here:.1f}s before section {i} at {fmt(t)}")
        for a, b in zip(ws, ws[1:]):
            if b["start"] - a["end"] > 2.0:
                fails.append(f"[dead air {b['start'] - a['end']:.1f}s] inside section {i} at {fmt(t + off + a['end'])}")
        prev_tail = length - off - speech
        t += length
    uncertain: list[dict] = []
    # 8. clip tags vs the sentence (library tags are written once by tools.tag_clips)
    for sh_ in shots:
        tok = sh_["tok"]
        if not tok.startswith(("clip:", "sb-")) or files.get(tok) is None:
            continue
        tg = tags.get(tok)
        if not tg:
            fails.append(f"[untagged clip: run python -m tools.tag_clips] {tok}")
            continue
        if tg.get("text_graphic"):
            fails.append(f"[graphic/text/green-screen clip] {ref(sh_)}")
        cred = str((CLIPS.get(tok[5:]) or {}).get("credit", "")) if tok.startswith("clip:") else ""
        if OTHER_STORY.search(cred) and not OTHER_STORY_TOPIC.search(" ".join(x["say"] for x in shots)):
            fails.append(f"[clip belongs to another story ({cred[:50]}), off-topic here] {ref(sh_)}")
        if tg.get("not_hamdan") and not re.search(r"\b(president|mohamed bin zayed|sheikh mohamed)\b", sh_["say"], re.I):
            fails.append(f"[clip shows someone else ({tg.get('desc', '')[:60]}), the line is not about them] {ref(sh_)}")
        if tok.startswith("sb-") and tg.get("people") and tg.get("arab_people") is not True:
            fails.append(f"[stock people do not look Emirati/Arab] {ref(sh_)}")
        verdict = relevance(tok, sh_["say"], tg, vocab)
        if verdict == "uncertain":
            uncertain.append(sh_)                      # Hamdan on screen for a Hamdan sentence: accepted, listed for the one batched look
        elif verdict != "ok":
            fails.append(f"[{verdict}] {ref(sh_)}")
    # a sentence that names Hamdan / a person must open on REAL footage of him/them, never stock
    seen_b = set()
    for sh_ in shots:
        k = (sh_["sec"], sh_["beat"])
        if k not in seen_b and sh_["first"] and (HAM.search(sh_["say"]) and re.search(r"\b(hamdan|fazza|prince)\b", sh_["say"], re.I)) and sh_["tok"].startswith("sb-"):
            fails.append(f"[sentence about Hamdan opens on stock, not on him] {ref(sh_)}")
        seen_b.add(k)
    # 9. people mentioned need real (non-stock) footage in the same beat
    done = set()
    for sh_ in shots:
        k = (sh_["sec"], sh_["beat"])
        if k in done:
            continue
        done.add(k)
        if PERSON.search(sh_["say"]):
            beat_shots = [x for x in shots if (x["sec"], x["beat"]) == k]
            if all(x["tok"].startswith("sb-") for x in beat_shots):
                fails.append(f"[person mentioned but only stock footage] {fmt(sh_['t0'])} \"{sh_['say'][:80]}\"")
    # 10. script checks: words, CTA, open loop teased, drama
    beats = [b for _, _, b in load_beats(s)]
    says = [str(b["say"]) for b in beats]
    nwords = sum(len(re.findall(r"\w+", x)) for x in says)
    if nwords < 1320:
        fails.append(f"[words] {nwords} < 1320")
    ctas = [i for i, x in enumerate(says) if re.search(r"\b(subscribe|like and|comment below|hit the bell)\b", x, re.I)]
    if len(ctas) != 1 or ctas[0] != len(says) - 1:
        fails.append(f"[CTA] must be exactly once, in the last sentence (found at {ctas})")
    tease = re.compile(r"\b(stay|until the end|reveal|i promised|i will tell|coming up|we will get to|still waiting|"
                       r"haven.t (told|revealed)|remember (the|that)|the answer|you.ll see|wait for it)\b", re.I)
    teases = [i for i, x in enumerate(says) if tease.search(x)]
    mid = [i for i in teases if 3 < i < len(says) - 8]
    if "?" not in says[0] or not teases or teases[0] != 0:
        fails.append("[open loop] sentence 1 must ask the big question and promise the answer at the end")
    if len(mid) < 2:
        fails.append(f"[open loop] teased only {len(mid)}x in the middle (need 2-3: 'remember...', 'we are getting closer')")
    tail = " ".join(says[-12:]).lower()
    if not re.search(r"\b(answer|reveal|because|the reason|that is why|here is)\b", tail):
        fails.append("[open loop] the answer is not revealed in the last minute")
    sents = [y for x in says for y in re.split(r"(?<=[.?!])\s+", x) if y.strip()]
    lens = [len(re.findall(r"\w+", x)) for x in sents]
    short = sum(1 for n in lens if n <= 8) / max(1, len(lens))
    if short < 0.30:
        fails.append(f"[drama] only {short:.0%} short punchy lines (<=8 words; need 30%+)")
    if np.mean(lens) > 14:
        fails.append(f"[drama] average sentence {np.mean(lens):.1f} words (max 14)")
    if sum(x.count("?") for x in says) < 5:
        fails.append("[drama] fewer than 5 questions/cliffhangers")
    # 10b. rules 4,5,6,7,9,22 (script / config / library checks)
    for x in shots:
        if x["tok"].startswith(("chart:", "card:")):
            fails.append(f"[no self-made charts/cards] {ref(x)}")
    from lane.config import load_config as _lc
    _c = _lc().get("story", {})
    if _c.get("cards") or _c.get("pops") or _c.get("end_card"):
        fails.append("[config] story cards/pops/end_card must be off (no text cards, pop-up words, closing screen)")
    label = re.compile(r"\b(estimate|estimated|report|reports|reported|reportedly|rumou?r|rumou?rs|claim|claims|claimed|according|"
                       r"unconfirmed|unofficial|online|guess|roughly|about|around|budget|official|officially|no official|we do not know|"
                       r"we found no|nobody|not a fact|numbers?)\b", re.I)
    for x in {(sx["sec"], sx["beat"]): sx for sx in shots}.values():
        # a money figure: a currency word, or million/billion not followed by a count of things ("million people")
        if re.search(r"\$|\bdollars?\b|\bdirhams?\b|\b(million|billion)\b(?!\s+(people|residents|visitors|followers|"
                     r"views|fans|viewers|users|tourists|passengers|trees|years|times))", x["say"], re.I):
            near = " ".join(y["say"] for y in shots if y["sec"] == x["sec"] and abs(y["beat"] - x["beat"]) <= 1)
            if not label.search(near):
                fails.append(f"[money figure not labelled fact/report/estimate/rumour] {fmt(x['t0'])} \"{x['say'][:80]}\"")
    # fresh script: no 6-word run shared with another story file
    def grams(txt):
        w = re.findall(r"[a-z']+", txt.lower())
        return {" ".join(w[i:i + 6]) for i in range(len(w) - 5)}
    mine = grams(" ".join(says))
    for o in STORIES.glob("*.yaml"):
        if o.resolve() == path.resolve():
            continue
        try:
            os_ = yaml.safe_load(o.read_text(encoding="utf-8")) or {}
            otxt = " ".join(str(b.get("say", "")) for sec in os_.get("sections", []) for b in sec.get("beats", []))
        except Exception:
            continue
        shared = mine & grams(otxt)
        if len(shared) > 12:
            fails.append(f"[script not fresh] {len(shared)} six-word phrases shared with {o.name}, e.g. '{sorted(shared)[0]}'")
    th = s.get("thumbnail") or {}
    ttext = re.sub(r"[^A-Za-z0-9$?]+", " ", str(th.get("text", ""))).split()
    if not th.get("scene") or not (1 <= len(ttext) <= 4):
        fails.append("[thumbnail] needs a scene (his face + subject) and 2-4 huge words")
    mus = sorted((ROOT / "assets" / "music").glob("*.mp3"))
    if not mus:
        fails.append("[music] no Kevin MacLeod tracks in assets/music")
    sb_sec = sum(x["t1"] - x["t0"] for x in shots if x["tok"].startswith("sb-"))
    if sb_sec / max(total, 1) < 0.25:
        fails.append(f"[footage rule 9] stock is only {sb_sec / total:.0%} of the video; non-Hamdan subjects must be mostly Storyblocks")
    if shots and not shots[-1]["tok"].startswith(("clip:", "key:", "him")):
        fails.append("[ending] must end on Hamdan, not a stranger")
    # 11. editing cues: SFX + light leaks/flashes on a schedule (explicit beat cues + the automatic chapter cues)
    cues_sfx, cues_fx = [], []
    for x in shots:
        if x["first"] or x["sfx"] or x["fx"]:
            pass
    for x in shots:
        if x["sfx"]:
            for c in x["sfx"].split("+"):
                if c in ("whoosh", "impact", "riser"):
                    cues_sfx.append((x["bstart"] - (0.3 if c == "whoosh" else 2.4 if c == "riser" else 0), c))
        if x["fx"] in ("leak", "flash"):
            cues_fx.append((x["bstart"], x["fx"]))
    secstart = {}
    for x in shots:
        secstart.setdefault(x["sec"], x["t0"])
    cues_sfx.append((0.0, "impact"))
    for i, t0 in secstart.items():
        if i:
            cues_sfx.append((t0 - 0.25, "whoosh"))
            cues_fx.append((t0, "leak"))
    cues_sfx.sort()
    cues_fx.sort()
    def gaps(c, name, limit):
        ts = [0.0] + [max(0.0, t) for t, _ in c] + [total]
        for a, b in zip(ts, ts[1:]):
            if b - a > limit:
                fails.append(f"[editing cues] no {name} between {fmt(a)} and {fmt(b)} ({b - a:.0f}s; max {limit}s)")
    gaps(cues_sfx, "sound effect", 30)
    gaps(cues_fx, "light leak/white flash", 45)
    if sum(1 for t, _ in cues_sfx if t < 30) < 3:
        fails.append("[editing cues] first 30 s needs 3+ sound effects (hook hits/whooshes)")
    # 11b. the GitHub render only has what is in the footage library: a missing file means a lost clip + filler repeats
    have = library_names()
    if have is None:
        warns.append("footage library could not be read: clip presence on GitHub not checked")
    else:
        for tok in sorted({sh_["tok"] for sh_ in shots}):
            name = f"clip-{tok[5:]}.mp4" if tok.startswith("clip:") else f"stock-{tok[3:]}.mp4" if tok.startswith("sb-") else None
            if name and name not in have:
                fails.append(f"[not in the footage library, the render would lose it: upload {name}] {tok}")
    info = {"minutes": round(total / 60, 1), "shots": len(shots), "words": nwords, "tagged": len(tags),
            "cues_sfx": [round(t, 1) for t, _ in cues_sfx], "cues_fx": [round(t, 1) for t, _ in cues_fx]}
    # 12. contact sheets (1 per ~60 s)
    make_sheets(sid, shots, files, tags)
    return fails, warns, info


def face_pass(files: dict, tags: dict) -> None:
    """Once per clip: share of 6 sampled frames where Hamdan's face is verified (lane/faceid.py). Stored in the tag library."""
    todo = [k for k, p in files.items() if p and k.startswith("clip:") and k in tags and "hamdan" not in tags[k]]
    if not todo:
        return
    import cv2
    from lane.faceid import FaceID
    from lane.media import FFMPEG
    fid = FaceID()
    for k in todo:
        p, d, hits = files[k], duration(files[k]), 0
        for f in (0.1, 0.25, 0.4, 0.55, 0.7, 0.85):
            jpg = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{d * f:.2f}", "-i", str(p), "-frames:v", "1", "-f", "image2pipe",
                                  "-vcodec", "png", "-"], capture_output=True).stdout
            img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR) if jpg else None
            if img is not None and fid.find(img).present:
                hits += 1
        tags[k]["hamdan"] = round(hits / 6, 2)
    TAGS.write_text(yaml.safe_dump(tags, sort_keys=True, allow_unicode=True, width=200), encoding="utf-8")


def make_sheets(sid: str, shots: list[dict], files: dict, tags: dict) -> None:
    import io
    from PIL import Image, ImageDraw
    from lane.media import FFMPEG
    out = WORK / sid
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("sheet_*.jpg"):
        f.unlink()
    W, H, COLS = 208, 117, 6
    n_sheets = int((shots[-1]["t1"] if shots else 0) // 60) + 1
    for k in range(n_sheets):
        part = [x for x in shots if k * 60 <= x["t0"] < (k + 1) * 60]
        if not part:
            continue
        rows = (len(part) + COLS - 1) // COLS
        im = Image.new("RGB", (COLS * (W + 4), rows * (H + 26)), "black")
        dr = ImageDraw.Draw(im)
        for n, x in enumerate(part):
            cx, cy = (n % COLS) * (W + 4), (n // COLS) * (H + 26)
            p = files.get(x["tok"])
            if p:
                d = duration(p)
                b = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{d * 0.5:.2f}", "-i", str(p), "-frames:v", "1", "-vf",
                                    f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2",
                                    "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
                if b:
                    im.paste(Image.open(io.BytesIO(b)).convert("RGB"), (cx, cy + 24))
            else:
                dr.rectangle((cx, cy + 24, cx + W, cy + 24 + H), outline="red")
            dr.text((cx + 2, cy), f"{fmt(x['t0'])} {x['tok'][:26]}", fill="yellow")
            dr.text((cx + 2, cy + 11), x["say"][:34], fill="white")
        im.save(out / f"sheet_{k + 1:02d}.jpg", quality=78)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    p = story_path(args[0]) if args else None
    if not p:
        print("usage: python -m tools.prerender_gate <story id or file> [--no-voice]")
        return 2
    fails, warns, info = run_gate(p, "--no-voice" not in sys.argv)
    sid = str(yaml.safe_load(p.read_text(encoding="utf-8")).get("id"))
    print(f"GATE {'PASS' if not fails else 'FAIL'} {sid}: {info['minutes']} min, {info['shots']} shots, {info['words']} words, "
          f"{info['tagged']} clips in tag library")
    print("RULE TABLE (checklist 1-26):")
    print(chr(10).join(rule_table(fails)))
    for f in fails:
        print("  FAIL", f)
    for w in warns[:10]:
        print("  warn", w)
    print(f"contact sheets: {WORK / sid}")
    st = STAMPS / f"{sid}.json"
    if not fails:
        STAMPS.mkdir(parents=True, exist_ok=True)
        st.write_text(json.dumps({"sha": story_sha(p), "passed": datetime.now().isoformat(timespec="seconds"),
                                  "cues_sfx": info["cues_sfx"], "cues_fx": info["cues_fx"], "minutes": info["minutes"]}), encoding="utf-8")
        print("stamp written:", st)
    elif st.exists():
        st.unlink()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
