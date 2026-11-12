"""Long films are planned in parts, each part seeing what was already written."""

import re

import pytest

from filmmaker import planner


def _fake_draft(calls, *, short_by=0):
    """Stands in for one Claude call: returns the scenes the message asks for (ids deliberately wrong)."""
    def draft(user):
        calls.append(user)
        m = re.search(r"write ONLY scenes s(\d+)-s(\d+)", user)
        a, b = (int(m.group(1)), int(m.group(2))) if m else (1, int(re.search(r"Number of scenes: (\d+)", user).group(1)))
        n = b - a + 1 - (short_by if len(calls) == 1 else 0)
        scenes = [{"id": "s01", "prompt": f"beat {i}", "dialogue": [{"character": "Krit", "line": f"line {i}"}]}
                  for i in range(a, a + n)]
        chars = [{"name": "Krit", "description": "a man"}]
        if len(calls) == 2:
            chars.append({"name": "Mark", "description": "a diver"})
            chars.append({"name": "krit", "description": "duplicate, must be ignored"})
        return {"title": f"Title {len(calls)}", "style": "s", "characters": chars, "scenes": scenes}
    return draft


def test_small_film_is_one_call():
    calls = []
    out = planner._draft_in_parts(_fake_draft(calls), "Idea: x\nNumber of scenes: 6", 6)
    assert len(calls) == 1 and len(out["scenes"]) == 6


def test_55_scenes_in_four_parts_merged_in_order():
    calls, logs = [], []
    out = planner._draft_in_parts(_fake_draft(calls), "Idea: x\nNumber of scenes: 55", 55, logs.append)
    assert len(calls) == 4
    assert [s["id"] for s in out["scenes"]] == [f"s{i:02d}" for i in range(1, 56)]
    assert [s["prompt"] for s in out["scenes"]] == [f"beat {i}" for i in range(1, 56)]
    assert out["title"] == "Title 1"  # part 1 decides the title
    assert [c["name"] for c in out["characters"]] == ["Krit", "Mark"]
    # later parts see the earlier scenes and their lines, and the last part is told to end the story
    assert "s16-s30 of 55" in calls[1] and "line 15" in calls[1] and "Character Krit" in calls[1]
    assert "LAST part" in calls[3] and "LAST part" not in calls[2]
    assert any("Phần 1/4" in l for l in logs)


def test_short_part_is_continued_not_lost():
    calls = []
    out = planner._draft_in_parts(_fake_draft(calls, short_by=3), "Idea: x\nNumber of scenes: 20", 20)
    assert len(out["scenes"]) == 20 and "write ONLY scenes s13-s20" in calls[1]


def test_stuck_planner_raises():
    def empty(user):
        return {"title": "t", "scenes": []}
    with pytest.raises(RuntimeError, match="0/40"):
        planner._draft_in_parts(empty, "Idea: x", 40)


def test_plan_with_claude_code_long_film(monkeypatch):
    calls = []
    monkeypatch.setattr(planner, "_draft_claude_code", lambda user, timeout=900: _fake_draft(calls)(user))
    sb = planner.plan_with_claude_code("x", scenes=55, aspect="16:9", durations=None)
    assert len(sb.scenes) == 55 and len(calls) == 4


def test_parts_keep_locations_and_add_only_new_ones():
    calls = []

    def draft(user):
        calls.append(user)
        m = re.search(r"write ONLY scenes s(\d+)-s(\d+)", user)
        a, b = int(m.group(1)), int(m.group(2))
        locs = [{"name": "Cave", "description": "wet rock"}]
        if len(calls) == 2:
            locs += [{"name": "cave", "description": "dup"}, {"name": "Camp", "description": "tents"}]
        return {"title": "t", "locations": locs,
                "scenes": [{"id": "x", "prompt": f"beat {i}", "location": "Cave"} for i in range(a, b + 1)]}

    out = planner._draft_in_parts(draft, "Idea: x", 20)
    assert [loc["name"] for loc in out["locations"]] == ["Cave", "Camp"]
    assert "Location Cave: wet rock" in calls[1] and "s01 [at Cave]: beat 1" in calls[1]
    assert "EVERY location" in calls[0]
