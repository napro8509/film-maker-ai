"""The "Phim mới" form's format choices through the web API."""

import pytest

pytest.importorskip("fastapi")

from filmmaker import basic_planner, planner  # noqa: E402
from tests.test_web import client, wait  # noqa: E402,F401


def _claude(monkeypatch):
    monkeypatch.setattr(planner, "claude_code_binary", lambda: "/usr/bin/claude")
    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": True, "logged_in": True, "method": "claude.ai"})


def test_env_lists_the_formats(client):
    f = client.get("/api/env").json()["formats"]
    assert f["drama"] == "short_drama" and len(f["formats"]) == 5


def test_basic_format_plans_with_the_basic_planner(client, monkeypatch):
    _claude(monkeypatch)
    seen = {}

    def fake(idea, **kw):
        seen.update(kw, idea=idea)
        return {"title": "Mèo", "scenes": [{"id": "s", "image_prompt": "a cat", "prompt": "it jumps"}]}
    monkeypatch.setattr(planner, "_draft_claude_code", lambda user, timeout=900, **kw: fake(user, **kw))
    r = client.post("/api/projects/plan", json={"idea": "mèo", "scenes": 1, "aspect": "9:16", "language": "Vietnamese",
                                                 "format": "clip", "audio": "silent", "visual_style": "2d",
                                                 "genres": ["comedy"]})
    j = wait(client, r.json())
    assert j["status"] == "done", j["lines"]
    assert seen["schema_model"] is basic_planner.BasicDraft and "Format: Clip ngắn" in seen["idea"]
    sb = client.get(f"/api/projects/{j['result']['project']}").json()["storyboard"]
    assert sb["format"] == "clip" and sb["audio"] == "silent" and sb["genres"] == ["comedy"]
    assert sb["scenes"][0]["mode"] == "keyframe"


@pytest.mark.parametrize("body, msg", [
    ({"format": "opera"}, "Định dạng"),
    ({"format": "clip", "aspect": "1:1"}, "16:9"),
    ({"format": "clip", "audio": "karaoke"}, "Âm thanh"),
    ({"format": "clip", "visual_style": "other"}, "phong cách"),
])
def test_bad_choices_are_400(client, monkeypatch, body, msg):
    _claude(monkeypatch)
    r = client.post("/api/projects/plan", json={"idea": "x", **body})
    assert r.status_code == 400 and msg in r.json()["detail"]
