"""“Viết lại prompt”: a scene Flow refused on policy grounds is rewritten by Claude (faked here), nothing sent to Flow."""
import json

import pytest
import yaml

pytest.importorskip("fastapi")

from filmmaker import reviewer  # noqa: E402
from tests.model_helpers import ui_out_dir, write_project  # noqa: E402
from tests.test_model_web import make_client, video_calls  # noqa: E402,F401

ERR = ("Flow báo tạo video lỗi: warning Failed This prompt might violate our policies about generating "
       "prominent people. Please try a different prompt or send feedback.")


def set_error(p, sid, err):
    st = ui_out_dir(p) / "state.json"
    st.parent.mkdir(parents=True, exist_ok=True)
    st.write_text(json.dumps({"scenes": {sid: {"status": "failed", "error": err}}}))


def test_rewrite_prompt_applies_fix(make_client, monkeypatch):
    c = make_client(anthropic_api_key="sk-test")
    p = write_project(c.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}])
    set_error(p, "s02", ERR)
    seen = {}

    def fake(sb, sid, error, **kw):
        seen.update(sid=sid, error=error, kw=kw)
        return reviewer.SceneFix(id="x", issues=["tên người thật"], prompt="Two ordinary fishermen smile at the sea.")

    monkeypatch.setattr(reviewer, "soften_scene", fake)
    r = c.post("/api/projects/film/scenes/s02/rewrite-prompt")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["rewrite"]["changed"] and d["rewrite"]["scene"] == "s02"
    assert seen["sid"] == "s02" and "prominent people" in seen["error"] and seen["kw"]["backend"] == "api"
    raw = yaml.safe_load(p.read_text())
    assert next(s for s in raw["scenes"] if s["id"] == "s02")["prompt"] == "Two ordinary fishermen smile at the sea."
    assert next(s for s in d["storyboard"]["scenes"] if s["id"] == "s02")["prompt"].startswith("Two ordinary")
    assert video_calls(c) == []  # nothing sent to Flow


def test_rewrite_prompt_needs_an_error(make_client, monkeypatch):
    c = make_client(anthropic_api_key="sk-test")
    write_project(c.ws, "film", "veo-lite", [{"id": "s01"}])
    monkeypatch.setattr(reviewer, "soften_scene", lambda *a, **k: pytest.fail("must not call Claude"))
    assert c.post("/api/projects/film/scenes/s01/rewrite-prompt").status_code == 400
    assert c.post("/api/projects/film/scenes/nope/rewrite-prompt").status_code == 404


def test_rewrite_prompt_needs_claude(make_client):
    c = make_client()
    p = write_project(c.ws, "film", "veo-lite", [{"id": "s01"}])
    set_error(p, "s01", ERR)
    r = c.post("/api/projects/film/scenes/s01/rewrite-prompt")
    assert r.status_code == 400 and "Claude" in r.json()["detail"]


def set_failed_at(p, sid, err, when):
    st = ui_out_dir(p) / "state.json"
    st.parent.mkdir(parents=True, exist_ok=True)
    st.write_text(json.dumps({"scenes": {sid: {"status": "submitting", "error": err, "submitted_at": when}}}))


def test_refused_rewrite_is_sent_back_and_restages(make_client, monkeypatch):
    c = make_client(anthropic_api_key="sk-test")
    p = write_project(c.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}])
    set_error(p, "s02", ERR)
    calls = []

    def fake(sb, sid, error, earlier=None, **kw):
        calls.append(earlier)
        return reviewer.SceneFix(id=sid, prompt=f"version {len(calls)}", image_prompt=f"frame {len(calls)}")

    monkeypatch.setattr(reviewer, "soften_scene", fake)
    d = c.post("/api/projects/film/scenes/s02/rewrite-prompt").json()
    assert calls == [None] and not d["rewrite"]["restaged"] and d["rewrite"]["attempt"] == 1
    assert d["scene_state"]["s02"]["rewrites"] == 1
    # rendered again after the rewrite and refused again: the refused version goes back to Claude
    set_failed_at(p, "s02", ERR, "2999-01-01T00:00:00Z")
    d = c.post("/api/projects/film/scenes/s02/rewrite-prompt").json()
    assert [e["prompt"] for e in calls[1]] == ["version 1"]
    assert d["rewrite"]["restaged"] and d["rewrite"]["attempt"] == 2
    scene = next(s for s in yaml.safe_load(p.read_text())["scenes"] if s["id"] == "s02")
    assert scene["prompt"] == "version 2" and scene["continue_previous"] is False


def test_rewrite_not_rendered_yet_is_not_counted_as_refused(make_client, monkeypatch):
    c = make_client(anthropic_api_key="sk-test")
    p = write_project(c.ws, "film", "veo-lite", [{"id": "s01"}])
    set_failed_at(p, "s01", ERR, "2000-01-01T00:00:00Z")
    calls = []
    monkeypatch.setattr(reviewer, "soften_scene", lambda sb, sid, error, earlier=None, **kw: (
        calls.append(earlier), reviewer.SceneFix(id=sid, prompt=f"v{len(calls)}"))[1])
    c.post("/api/projects/film/scenes/s01/rewrite-prompt")
    c.post("/api/projects/film/scenes/s01/rewrite-prompt")  # pressed twice without rendering in between
    assert calls == [None, None]


def test_restage_on_first_try_sends_the_failed_version(make_client, monkeypatch):
    c = make_client(anthropic_api_key="sk-test")
    p = write_project(c.ws, "film", "veo-lite", [{"id": "s01"}])
    set_error(p, "s01", ERR)
    before = next(s for s in yaml.safe_load(p.read_text())["scenes"] if s["id"] == "s01")["prompt"]
    calls = []
    monkeypatch.setattr(reviewer, "soften_scene", lambda sb, sid, error, earlier=None, **kw: (
        calls.append(earlier), reviewer.SceneFix(id=sid, prompt="close-up of one face"))[1])
    d = c.post("/api/projects/film/scenes/s01/rewrite-prompt", json={"restage": True}).json()
    assert calls[0][0]["prompt"] == before and d["rewrite"]["restaged"]


def test_soften_scene_prompt_lists_refused_attempts(monkeypatch):
    from tests.model_helpers import storyboard_dict
    from filmmaker.storyboard import Storyboard
    sb = Storyboard.model_validate(storyboard_dict("veo-lite", [{"id": "s01"}], title="t"))
    seen = {}
    monkeypatch.setattr(reviewer.ai, "ask", lambda system, user, model, **kw: seen.update(user=user) or model(id="s01"))
    reviewer.soften_scene(sb, "s01", ERR, earlier=[{"prompt": "divers rise from water", "image_prompt": "boys on ledge",
                                                   "dialogue": [{"character": "NAT", "line": "mười ba người"}]}])
    assert "already rewritten 1 time" in seen["user"] and "boys on ledge" in seen["user"]
    assert "NAT: mười ba người" in seen["user"] and "re-stage" in seen["user"]


def test_restage_replaces_or_drops_the_old_camera(make_client, monkeypatch):
    c = make_client(anthropic_api_key="sk-test")
    p = write_project(c.ws, "film", "veo-lite", [{"id": "s01", "camera": {"shot": "wide shot", "composition": "divers low",
                                                                       "movement": "handheld"}},
                                                 {"id": "s02", "camera": {"shot": "wide shot"}}])
    set_error(p, "s01", ERR)
    monkeypatch.setattr(reviewer, "soften_scene", lambda sb, sid, error, earlier=None, **kw: reviewer.SceneFix(
        id=sid, prompt="close-up", camera=reviewer.Camera(shot="extreme close-up", lens="85mm portrait lens")))
    c.post("/api/projects/film/scenes/s01/rewrite-prompt", json={"restage": True})
    scenes = {s["id"]: s for s in yaml.safe_load(p.read_text())["scenes"]}
    assert scenes["s01"]["camera"] == {"shot": "extreme close-up", "lens": "85mm portrait lens"}
    # Claude gave no camera: the old framing goes, the camera move stays
    set_error(p, "s02", ERR)
    raw = yaml.safe_load(p.read_text())
    raw["scenes"][1]["camera"] = {"shot": "wide shot", "movement": "slow dolly-in"}
    p.write_text(yaml.safe_dump(raw, allow_unicode=True))
    monkeypatch.setattr(reviewer, "soften_scene", lambda sb, sid, error, earlier=None, **kw: reviewer.SceneFix(id=sid, prompt="x"))
    c.post("/api/projects/film/scenes/s02/rewrite-prompt", json={"restage": True})
    scenes = {s["id"]: s for s in yaml.safe_load(p.read_text())["scenes"]}
    assert scenes["s02"]["camera"] == {"movement": "slow dolly-in"}
