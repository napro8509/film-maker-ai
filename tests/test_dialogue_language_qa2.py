"""QA round 2 for dialogue-language: gaps not covered by the other test files.
Fake gflow + fake planner only: no Claude, Flow or Veo."""

import json
import sys
import time
from pathlib import Path

import pytest
import yaml

from filmmaker.storyboard import DIALOGUE_RULE, NO_DIALOGUE_RULE, NO_TEXT, Storyboard, load_storyboard

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filmmaker import planner  # noqa: E402
from filmmaker.web import server  # noqa: E402

FAKE = Path(__file__).parent / "fake_gflow.py"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Copied from tests/test_web.py (no shared fixture) + a fake planner on the "api" backend."""
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "calls.log"))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": False, "logged_in": False})
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"pause_min": 0, "pause_max": 0, "retries": 0, "auto_backup": False,
                                    "planner": "api", "anthropic_api_key": "x"}))
    monkeypatch.setattr(server, "SETTINGS_PATH", settings)
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(settings))
    app = server.create_app(tmp_path / "ws", token="t0k")
    c = TestClient(app)
    c.headers["X-Token"] = "t0k"
    c.ws = tmp_path / "ws"
    c.log = tmp_path / "calls.log"
    c.plan_calls = []

    def fake_plan(idea, **kw):
        c.plan_calls.append(kw)
        return Storyboard.model_validate({"title": "Phim QA", "scenes": [{"id": "s01", "mode": "t2v", "prompt": "a"}]})

    monkeypatch.setattr(planner, "plan", fake_plan)
    return c


def _wait(c, job, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _project_with_done_scenes(c, **extra):
    """3 scenes; s01 (t2v) and s02 (manual) are DONE with matching fingerprints and real files."""
    d = c.ws / "film"
    d.mkdir(parents=True)
    data = {"title": "Film", **extra, "scenes": [
        {"id": "s01", "mode": "t2v", "prompt": "Lan says hello"},
        {"id": "s02", "mode": "t2v", "prompt": "Minh waves"},
        {"id": "s03", "mode": "t2v", "prompt": "rain"},
    ]}
    (d / "storyboard.yaml").write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    sb = load_storyboard(d / "storyboard.yaml")
    out = d / "output" / "storyboard"
    (out / "clips").mkdir(parents=True)
    scenes = {}
    for sid, how in (("s01", "t2v"), ("s02", "manual")):
        clip = out / "clips" / f"{sid}.mp4"
        clip.write_bytes(b"x")
        s = next(x for x in sb.scenes if x.id == sid)
        scenes[sid] = {"status": "done", "fingerprint": sb.scene_fingerprint(s), "video": str(clip), "rendered_as": how}
    (out / "state.json").write_text(json.dumps({"project_id": "p", "scenes": scenes}), encoding="utf-8")
    return d


# --- AC17 / AC18 through the web API: changing the language marks nothing as changed ----------------------

@pytest.mark.parametrize("start", [None, "Vietnamese"])
def test_put_language_keeps_done_scenes_done(client, start):
    extra = {"dialogue_language": start} if start else {}
    _project_with_done_scenes(client, **extra)
    before = client.get("/api/projects/film").json()
    assert {k: v["status"] for k, v in before["scene_state"].items()} == {"s01": "done", "s02": "done", "s03": "todo"}
    assert before["clips_rendered"] == 2
    jobs_before = client.get("/api/jobs").json() if client.get("/api/jobs").status_code == 200 else None
    for value in ("English", "none", None, "Thai"):
        sb = dict(before["storyboard"])
        if value is None:
            sb.pop("dialogue_language", None)
        else:
            sb["dialogue_language"] = value
        r = client.put("/api/projects/film", json={"storyboard": sb})
        assert r.status_code == 200, r.text
        d = r.json()
        assert {k: v["status"] for k, v in d["scene_state"].items()} == {"s01": "done", "s02": "done", "s03": "todo"}
        assert d["clips_rendered"] == 2
        vp = d["scene_state"]["s03"]["video_prompt"]
        if value is None:
            assert "spoken dialogue" not in vp.lower()
        elif value == "none":
            assert vp.count(NO_DIALOGUE_RULE) == 1
        else:
            assert vp.count(DIALOGUE_RULE.format(lang=value)) == 1
    # no gflow call at all (not even a video) and no job started
    calls = [json.loads(l) for l in client.log.read_text().splitlines()] if client.log.exists() else []
    assert not [c for c in calls if c and c[0] in ("video", "image")]
    if jobs_before is not None:
        assert client.get("/api/jobs").json() == jobs_before


def test_put_language_whitespace_is_collapsed_in_prompt(client):
    _project_with_done_scenes(client)
    sb = client.get("/api/projects/film").json()["storyboard"]
    sb["dialogue_language"] = "  Brazilian   Portuguese "
    d = client.put("/api/projects/film", json={"storyboard": sb}).json()
    assert d["scene_state"]["s03"]["video_prompt"].count(DIALOGUE_RULE.format(lang="Brazilian Portuguese")) == 1


# --- AC15 on the fallback path: keyframe shown as text-only when i2v is unsupported -------------------------

def test_detail_fallback_text_prompt_has_rule(client):
    d = client.ws / "fb"
    d.mkdir(parents=True)
    (d / "storyboard.yaml").write_text(yaml.safe_dump({
        "title": "FB", "dialogue_language": "Thai", "fallback_t2v": True,
        "scenes": [{"id": "k1", "mode": "keyframe", "image_prompt": "Lan at a cafe", "prompt": "Lan smiles"}]},
        sort_keys=False), encoding="utf-8")
    out = d / "output" / "storyboard"
    out.mkdir(parents=True)
    (out / "state.json").write_text(json.dumps({"i2v_unsupported": True, "scenes": {}}), encoding="utf-8")
    st = client.get("/api/projects/fb").json()["scene_state"]["k1"]
    assert "Lan at a cafe" in st["video_prompt"]  # the text-only prompt
    assert st["video_prompt"].count(DIALOGUE_RULE.format(lang="Thai")) == 1
    assert "spoken dialogue" not in st["image_prompt"].lower()


# --- POST /plan: "None" typed in any case means no dialogue; old clients still get the default language ------

@pytest.mark.parametrize("typed", ["None", "NONE", " none "])
def test_plan_none_typed_any_case(client, typed):
    r = client.post("/api/projects/plan", json={"idea": "ý", "dialogue_language": typed, "language": "English"})
    assert r.status_code == 200, r.text
    j = _wait(client, r.json())
    assert j["status"] == "done", j["lines"]
    text = (client.ws / j["result"]["project"] / "storyboard.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["dialogue_language"] == "none" and data["subtitle_language"] == "English"
    assert any("nhân vật nói: không thoại; phụ đề: English" in line for line in j["lines"])
    assert client.plan_calls[-1]["dialogue_language"] == "none"


def test_plan_twice_makes_two_projects_with_own_languages(client):
    pids = []
    for dl, lang in (("Vietnamese", "English"), ("Thai", "Thai")):
        j = _wait(client, client.post("/api/projects/plan", json={"idea": "ý", "dialogue_language": dl,
                                                                   "language": lang}).json())
        assert j["status"] == "done"
        pids.append(j["result"]["project"])
    assert len(set(pids)) == 2
    got = [yaml.safe_load((client.ws / p / "storyboard.yaml").read_text())["dialogue_language"] for p in pids]
    assert got == ["Vietnamese", "Thai"]


# --- for_flow_video is idempotent (a prompt that already went through it is not changed again) --------------

@pytest.mark.parametrize("lang", ["Vietnamese", "none", None])
@pytest.mark.parametrize("no_text", [True, False])
@pytest.mark.parametrize("prompt", ["a cat walks", "a cat walks!", "", f"a cat. {NO_TEXT}"])
def test_for_flow_video_idempotent(lang, no_text, prompt):
    sb = Storyboard.model_validate({"title": "t", "no_text": no_text, "dialogue_language": lang,
                                    "scenes": [{"id": "s01", "mode": "t2v", "prompt": "x"}]})
    once = sb.for_flow_video(prompt)
    assert sb.for_flow_video(once) == once
    rule = sb.dialogue_rule()
    if rule:
        assert once.count(rule) == 1
        if NO_TEXT in once:
            assert once.index(rule) < once.index(NO_TEXT)
