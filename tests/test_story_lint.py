from pathlib import Path

import yaml

from tools import lint_story, vet_stock

ROOT = Path(__file__).resolve().parent.parent


def test_no_story_uses_unvetted_or_rejected_stock():
    for f in (ROOT / "assets" / "stories").glob("*.yaml"):
        bad = [p for p in lint_story.lint(f) if "REJECTED" in p or "never looked at" in p]
        assert not bad, (f.name, bad)


def test_vetted_lists_do_not_overlap():
    v = vet_stock.load()
    assert not {str(x) for x in v["approved"]} & {str(x) for x in v["rejected"]}
