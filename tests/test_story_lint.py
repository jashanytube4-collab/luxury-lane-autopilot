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


def test_rule38_flags_years_and_dates_but_not_ages_or_plan_names():
    from tools.lint_story import old_content_signals
    bad = {"title": "Fazza in 2026", "tags": ["Fazza 2020"], "sections": [{"beats": [
        {"say": "In May two thousand and nineteen he flew."},
        {"say": "He joined in twenty twenty-two."},
        {"say": "That was five years ago."},
        {"say": "On the thirtieth of September, a jet took off."}]}]}
    assert len(old_content_signals(bad)) >= 6
    ok = {"title": "One Tiny Bird", "sections": [{"say": "At twenty-five he was named heir. Vision twenty-thirty "
                                                          "changed Riyadh. People may never go there."}]}
    assert old_content_signals(ok) == []
