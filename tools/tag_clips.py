"""One-time local tagging of footage clips (no Claude credits): python -m tools.tag_clips <story file> [--all]
A local open-source vision model (the same Qwen used by lane/brain.py) looks at 3 frames of every clip the story
uses and writes assets/clip_tags.yaml: what is visible (keywords), people / Arab look / Hamdan flags.
A clip already in the file is never looked at again; tags are reused forever by tools.prerender_gate."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TAGS = ROOT / "assets" / "clip_tags.yaml"

PROMPT = ("These 3 images are frames from ONE short video clip. Describe what is really visible. Answer JSON only: "
          '{"desc": "max 8 words", "tags": ["10-14 lowercase single-word nouns/places/objects visible, '
          'e.g. horse, falcon, car, desert, skyline, money, plane, yacht, crowd, office, jewellery"], '
          '"people": true/false (any person clearly visible), '
          '"arab_people": true/false/null (null if no people; true only if they clearly look Gulf Arab: kandura, ghutra, abaya, hijab), '
          '"text_graphic": true/false (cartoon, chart, big text overlay or green screen)}')
SCHEMA = {"type": "object", "properties": {
    "desc": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
    "people": {"type": "boolean"}, "arab_people": {"type": ["boolean", "null"]}, "text_graphic": {"type": "boolean"}},
    "required": ["desc", "tags", "people", "arab_people", "text_graphic"]}


def load() -> dict:
    return (yaml.safe_load(TAGS.read_text(encoding="utf-8")) or {}) if TAGS.exists() else {}


def save(d: dict) -> None:
    TAGS.write_text(yaml.safe_dump(d, sort_keys=True, allow_unicode=True, width=200), encoding="utf-8")


def main() -> None:
    from lane.brain import LocalModel, image_b64, _img
    from lane.config import load_config
    from tools.prerender_gate import story_tokens, duration
    from tools.storyboard import find
    path = Path(sys.argv[1])
    if str(path) == "--pool":      # every vetted stock clip + every library clip that exists locally
        from tools.prerender_gate import CLIPS
        vet = yaml.safe_load((ROOT / "assets" / "stock_vetted.yaml").read_text(encoding="utf-8"))
        toks = [f"sb-{i}" for i in vet["approved"]] + [f"clip:{c}" for c in CLIPS]
    else:
        toks = story_tokens(path)
    tags = load()
    todo = [t for t in toks if t not in tags and find(t)]
    print(f"{len(toks)} clips, {len(todo)} to tag", flush=True)
    if not todo:
        return
    m = LocalModel(load_config()["ai"])
    for n, tok in enumerate(todo, 1):
        p = find(tok)
        d = duration(p)
        parts = [{"type": "text", "text": PROMPT}] + [_img(image_b64(p, d * f, 336)) for f in (0.15, 0.5, 0.85)]
        try:
            out = m.chat(parts, schema=SCHEMA, max_tokens=170, temperature=0.1)
            j = json.loads(out[out.find("{"): out.rfind("}") + 1])
        except Exception as e:  # keep going; the gate reports untagged clips
            print("FAILED", tok, e, flush=True)
            continue
        tags[tok] = {"desc": str(j.get("desc", ""))[:160], "tags": sorted({str(x).lower().strip() for x in j.get("tags", [])}),
                     "people": bool(j.get("people")), "arab_people": j.get("arab_people"),
                     "text_graphic": bool(j.get("text_graphic"))}
        if tok.startswith("clip:"):
            from tools.prerender_gate import face_pass
            face_pass({tok: p}, tags)      # Hamdan face share, written once
        save(tags)
        print(f"[{n}/{len(todo)}] {tok}: {tags[tok]['desc']}", flush=True)
    m.stop()


if __name__ == "__main__":
    main()
