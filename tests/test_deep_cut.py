from pathlib import Path

from lane.faceid import Sighting
from lane.studio import Studio


def _studio(deep):
    st = Studio.__new__(Studio)
    st.cfg = {"short": {"deep_cut": deep}}
    st._scenes = {}
    return st


def test_event_cap_normal_and_deep():
    assert _studio(None).event_cap({"video_len": 900}) == 3
    assert _studio(True).event_cap({"video_len": 900}) == 15
    assert _studio(True).event_cap({"video_len": 100}) == 3
    assert _studio(True).event_cap({}) == 3


def test_used_ranges_are_never_reused():
    st = _studio(True)
    v = Path("x.mp4")
    seen = Sighting(True, 0.2, 0.5, 0.9)
    st._scenes[v] = [(0, 5, seen), (5.5, 9, seen), (20, 30, seen)]
    left = st.his_scenes(v, exclude=[[0, 5]])
    assert [(a, b) for a, b, _ in left] == [(20, 30)]      # 5.5-9 sits inside the 1 s safety pad of the used range
