"""Hamdan clip miner (no Claude): finds the shots in already-downloaded official videos where Prince Hamdan's face is
verified on screen, skipping the seconds that existing clips already use.

    python -m tools.hamdan_miner <yt> [<yt> ...]            # list candidate shots (start, end, presence, face size)
    python -m tools.hamdan_miner <yt> --add <prefix> --match '<regex>' --credit '<text>' --desc '<words>' [--tags a,b]
                                                            # register them in assets/clips.yaml + clip_tags.yaml and cut

Source videos must already be in work/clipsrc/<yt>.mp4 (downloaded on this laptop from the official channel).
A candidate is a scene (between two camera cuts) of 2.2 s or more where his face is verified in >= 70% of samples;
long scenes are split into pieces of ~3.2 s, but only the FIRST piece of a long shot is kept (the rest are near-duplicates),
and far shots (face < 8% of the frame height) are skipped.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lane.faceid import FaceID, scene_presence  # noqa: E402
from lane.longform import _scene_list  # noqa: E402

PIECE = 3.2
MIN_FACE = 0.08     # face height share; smaller = a far wide shot where nobody can see it is him


def used_ranges(clips: dict, yt: str) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for v in clips.values() if v.get("yt") == yt for a, b in v.get("parts", [])]


def candidates(yt: str, clips: dict) -> list[tuple[float, float, float, float]]:
    src = ROOT / "work" / "clipsrc" / f"{yt}.mp4"
    if not src.exists():
        print(f"{yt}: no local source {src}")
        return []
    fid = FaceID()
    tl = fid.scan(src, every=0.5)
    taken = used_ranges(clips, yt)
    out, last_end = [], -1.0
    for a, b in _scene_list(src, piece=PIECE):
        same_shot = abs(a - last_end) < 0.05        # the next piece of one long shot: near-duplicate, keep only the first
        last_end = b
        if same_shot:
            continue
        if b - a < 2.2:
            continue
        if any(not (b <= x + 0.2 or a >= y - 0.2) for x, y in taken):
            continue
        s = scene_presence(tl, a, b)
        if s.present and s.sim >= 0.7 and s.size >= MIN_FACE:
            out.append((round(a, 2), round(b, 2), round(s.sim, 2), round(s.size, 2)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("yt", nargs="+")
    ap.add_argument("--add")
    ap.add_argument("--match", default="hamdan|fazza|crown prince")
    ap.add_argument("--credit", default="Dubai Media Office (official YouTube channel)")
    ap.add_argument("--desc", default="Prince Hamdan at an official event")
    ap.add_argument("--tags", default="hamdan,kandura,ghutra")
    ap.add_argument("--max", type=int, default=12)
    ap.add_argument("--note", default="mined by tools.hamdan_miner (face verified)")
    args = ap.parse_args()
    cy = ROOT / "assets" / "clips.yaml"
    clips = yaml.safe_load(cy.read_text(encoding="utf-8"))["clips"]
    found = []
    for yt in args.yt:
        for c in candidates(yt, clips):
            print(yt, *c, flush=True)
            found.append((yt, *c))
    if not args.add or not found:
        return
    found = found[: args.max]
    txt = cy.read_text(encoding="utf-8").rstrip("\n") + "\n"
    new = []
    for k, (yt, a, b, _sim, size) in enumerate(found, 1):
        cid = f"{args.add}-m{k}"
        while f"\n  {cid}:" in txt:
            k += 100
            cid = f"{args.add}-m{k}"
        shot = "close-up" if size >= 0.22 else "medium shot" if size >= 0.12 else "wide shot"
        desc = f"{args.desc} ({shot})"
        m = args.match.replace("'", "''")
        txt += (f"  {cid}:\n    match: '{m}'\n    yt: {yt}\n    parts:\n    - - {a}\n      - {b}\n"
                f"    note: {args.note}\n    credit: {args.credit}\n")
        new.append((cid, desc, size))
    cy.write_text(txt, encoding="utf-8")
    tp = ROOT / "assets" / "clip_tags.yaml"
    tags = yaml.safe_load(tp.read_text(encoding="utf-8"))
    base = [t.strip() for t in args.tags.split(",") if t.strip()]
    for cid, desc, size in new:
        tags.setdefault(f"clip:{cid}", {"desc": desc, "tags": base + (["face"] if size >= 0.22 else []), "people": True,
                                        "arab_people": True, "text_graphic": False})
    tp.write_text(yaml.safe_dump(tags, sort_keys=True, allow_unicode=True, width=200), encoding="utf-8")
    from tools.fetch_clips import cut
    allc = yaml.safe_load(cy.read_text(encoding="utf-8"))["clips"]
    for cid, _d, _s in new:
        print("cut", cid, cut(cid, allc[cid]), flush=True)


if __name__ == "__main__":
    main()
