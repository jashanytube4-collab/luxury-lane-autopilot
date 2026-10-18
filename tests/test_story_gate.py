from pathlib import Path

import pytest

from lane.longform import Visuals
from lane.story import Gathered, QualityGateError, Story, audit


def _story():
    return Story("t", "T", "", [], [], Path("t.yaml"), "", True, False)


def _got(n_sources, per_source):
    g = Gathered([[]], [], [])
    vis = []
    for s in range(n_sources):
        v = Visuals(Path(f"{s}.mp4"), [(0, 1)] * per_source, [])
        g.sources[id(v)] = f"source {s}"
        vis.append(v)
    g.visuals = [vis]
    return g


def test_gate_stops_thin_story():
    with pytest.raises(QualityGateError):
        audit(_story(), _got(5, 20), {})        # 100 pieces but only 5 sources


def test_gate_passes_varied_story():
    audit(_story(), _got(12, 10), {})           # 120 pieces, 12 sources, none over 25%
