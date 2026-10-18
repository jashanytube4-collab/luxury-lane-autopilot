from lane.brain import STORY_PART, STORY_STYLE, _quality


def test_prompt_formats_with_style():
    out = STORY_PART.format(num=1, title="T", role="r", facts="- f", background="b", last="l", words=175, style=STORY_STYLE)
    assert "STYLE (match it exactly)" in out and "flydubai" in out


def test_quality_flags_filler_and_generic_openings():
    assert _quality("On 30 September, a jet took off. He stayed by the bed all night.") == []
    bad = _quality("In a remarkable journey, he showed a true legacy of leadership across many different fields of life and work today.")
    assert "filler words" in bad and "generic opening" in bad
