"""Web server side of dialogue-language (T3): POST /plan languages, PUT checks, detail() video_prompt and
clips_rendered. Fake gflow + fake planner only: no Claude, Flow or Veo."""

import json
import sys
import time
from pathlib import Path

import pytest
import yaml

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filmmaker import planner  # noqa: E402
from filmmaker.storyboard import DIALOGUE_RULE, NO_DIALOGUE_RULE, Storyboard  # noqa: E402
from filmmaker.web import server  # noqa: E402

FAKE = Path(__file__).parent / "fake_gflow.py"
VI_RULE = DIALOGUE_RULE.format(lang="Vietnamese")



def _said_once(text, rule):
    """The film's language sentence, or for a scene without lines the "nobody speaks" one, exactly once."""
    from filmmaker.storyboard import SILENT_SCENE_RULE

    return text.count(rule) + text.count(SILENT_SCENE_RULE) == 1

@pytest.fixture
def client(tmp_path, monkeypatch):
    """Copied from tests/test_web.py (no shared fixture), plus a fake planner reachable via the "api" backend."""
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "calls.log"))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")  # plan_auto writes os.environ; monkeypatch cleans it up
    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": False, "logged_in": False})
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"pause_min": 0, "pause_max": 0, "retries": 0,
                                    "planner": "api", "anthropic_api_key": "x"}))
    monkeypatch.setattr(server, "SETTINGS_PATH", settings)
    app = server.create_app(tmp_path / "ws", token="t0k")
    c = TestClient(app)
    c.headers["X-Token"] = "t0k"
    c.ws = tmp_path / "ws"
    c.calls = lambda: [json.loads(l) for l in (tmp_path / "calls.log").read_text().splitlines()] \
        if (tmp_path / "calls.log").exists() else []

    c.plan_calls = []

    def fake_plan(idea, **kw):  # stands in for Claude; returns the language keys to prove they are ignored
        c.plan_calls.append(kw)
        return Storyboard.model_validate({"title": "Phim AI", "scenes": [{"id": "s01", "mode": "t2v", "prompt": "a"}],
                                          "dialogue_language": "English", "subtitle_language": "Thai"})

    monkeypatch.setattr(planner, "plan", fake_plan)
    return c


def wait(c, job, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def plan(c, **body):
    r = c.post("/api/projects/plan", json={"idea": "một ý tưởng", **body})
    assert r.status_code == 200, r.text
    j = wait(c, r.json())
    assert j["status"] == "done", j["lines"]
    path = c.ws / j["result"]["project"] / "storyboard.yaml"
    return j, path


def make_project(c, pid="film", **extra):
    d = c.ws / pid
    d.mkdir(parents=True, exist_ok=True)
    data = {"title": "Film", **extra, "scenes": [
        {"id": "s01", "mode": "t2v", "prompt": "Lan says hello"},
        {"id": "s02", "mode": "keyframe", "image_prompt": "Lan at a cafe", "prompt": "Lan waves"},
        {"id": "s03", "mode": "t2v", "prompt": "c"},
        {"id": "s04", "mode": "t2v", "prompt": "d"},
        {"id": "s05", "mode": "t2v", "prompt": "e"},
    ]}
    (d / "storyboard.yaml").write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return d


# --- AC5: 400 before Claude --------------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["dialogue_language", "language"])
@pytest.mark.parametrize("value", ["", "   ", "x" * 41, "Viet\nnamese", "Viet\rnamese"])
def test_plan_rejects_bad_language(client, field, value):
    body = {"idea": "một ý tưởng", "dialogue_language": "Vietnamese", "language": "English", field: value}
    r = client.post("/api/projects/plan", json=body)
    assert r.status_code == 400
    label = "Ngôn ngữ thoại" if field == "dialogue_language" else "Ngôn ngữ phụ đề"
    assert r.json()["detail"].startswith(label + ":")
    assert client.plan_calls == []
    assert not client.ws.exists() or not any(client.ws.glob("*/storyboard.yaml"))


def test_plan_400_messages(client):
    r = client.post("/api/projects/plan", json={"idea": "ý", "dialogue_language": " "})
    assert r.json()["detail"] == "Ngôn ngữ thoại: hãy nhập tên ngôn ngữ."
    r = client.post("/api/projects/plan", json={"idea": "ý", "language": "x" * 41})
    assert r.json()["detail"] == "Ngôn ngữ phụ đề: tên ngôn ngữ dài quá 40 ký tự."


def test_plan_language_40_chars_ok(client):
    _, path = plan(client, dialogue_language="x" * 40, language="y" * 40)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["dialogue_language"] == "x" * 40 and data["subtitle_language"] == "y" * 40


# --- AC6, AC7, AC9: saved fields and log -------------------------------------------------------------------

def test_plan_saves_languages_and_logs(client):
    j, path = plan(client, dialogue_language="  Vietnamese ", language="English")
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["dialogue_language"] == "Vietnamese" and data["subtitle_language"] == "English"
    assert list(data)[:4] == ["title", "aspect", "dialogue_language", "subtitle_language"]  # save_storyboard order
    assert client.plan_calls[0]["dialogue_language"] == "Vietnamese"
    assert client.plan_calls[0]["language_hint"] == "English"
    line = [l for l in j["lines"] if "nhân vật nói" in l]
    assert line and "nhân vật nói: Vietnamese" in line[0] and "phụ đề: English" in line[0]


def test_plan_none_dialogue(client):
    j, path = plan(client, dialogue_language="NONE", language="Thai")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["dialogue_language"] == "none" and data["subtitle_language"] == "Thai"
    assert any("nhân vật nói: không thoại; phụ đề: Thai" in l for l in j["lines"])


def test_plan_old_client_has_no_language_fields(client):
    j, path = plan(client, language="English")  # no dialogue_language; the fake still returns both keys
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "dialogue_language" not in data and "subtitle_language" not in data
    assert "dialogue_language" not in client.plan_calls[0]
    assert client.plan_calls[0]["language_hint"] == "English"
    assert any("nhân vật nói: chưa đặt (Veo tự đoán); phụ đề: English" in l for l in j["lines"])


# --- AC15b: PUT checks only the two keys -------------------------------------------------------------------

@pytest.mark.parametrize("key", ["dialogue_language", "subtitle_language"])
@pytest.mark.parametrize("value", ["Viet\nnamese", "x" * 41, 5])
def test_put_project_rejects_bad_language(client, key, value):
    d = make_project(client)
    before = (d / "storyboard.yaml").read_bytes()
    raw = yaml.safe_load(before)
    raw[key] = value
    r = client.put("/api/projects/film", json={"storyboard": raw})
    assert r.status_code == 400
    assert r.json()["detail"].startswith("Ngôn ngữ thoại:" if key == "dialogue_language" else "Ngôn ngữ phụ đề:")
    assert (d / "storyboard.yaml").read_bytes() == before


@pytest.mark.parametrize("key", ["dialogue_language", "subtitle_language"])
@pytest.mark.parametrize("value", ['"Viet\\nnamese"', "x" * 41, "12"])
def test_put_yaml_rejects_bad_language(client, key, value):
    d = make_project(client)
    before = (d / "storyboard.yaml").read_bytes()
    text = before.decode("utf-8") + f"{key}: {value}\n"
    r = client.put("/api/projects/film/yaml", json={"text": text})
    assert r.status_code == 400, r.text
    assert (d / "storyboard.yaml").read_bytes() == before


@pytest.mark.parametrize("value", ['""', "null", "None", "  Viet   namese "])
def test_put_yaml_accepts_blank_and_none(client, value):
    d = make_project(client)
    text = (d / "storyboard.yaml").read_text(encoding="utf-8") + f"dialogue_language: {value}\n"
    r = client.put("/api/projects/film/yaml", json={"text": text})
    assert r.status_code == 200, r.text
    assert r.json()["errors"] == []


def test_put_keeps_broken_drafts_for_other_fields(client):
    make_project(client)
    raw = {"title": "Film", "dialogue_language": "Thai", "scenes": [{"id": "s01", "mode": "nope"}]}
    r = client.put("/api/projects/film", json={"storyboard": raw})
    assert r.status_code == 200, r.text
    assert r.json()["errors"]  # draft saved, errors only reported
    r = client.put("/api/projects/film/yaml", json={"text": "title: X\ndialogue_language: Thai\nscenes: [{id: s01, mode: nope}]\n"})
    assert r.status_code == 200 and r.json()["errors"]


def test_put_language_creates_no_job_and_no_gflow_call(client):
    d = make_project(client)
    raw = yaml.safe_load((d / "storyboard.yaml").read_text(encoding="utf-8"))
    raw["dialogue_language"] = "Vietnamese"
    r = client.put("/api/projects/film", json={"storyboard": raw})
    assert r.status_code == 200
    assert yaml.safe_load((d / "storyboard.yaml").read_text(encoding="utf-8"))["dialogue_language"] == "Vietnamese"
    raw.pop("dialogue_language")  # "Chưa đặt": the key goes away
    client.put("/api/projects/film", json={"storyboard": raw})
    assert "dialogue_language" not in yaml.safe_load((d / "storyboard.yaml").read_text(encoding="utf-8"))
    assert client.get("/api/jobs/current").json() is None  # no job was started
    assert client.calls() == []  # gflow never called


# --- AC15: detail().video_prompt -----------------------------------------------------------------------------

def test_detail_video_prompt_has_rule(client):
    make_project(client, dialogue_language="Vietnamese")
    st = client.get("/api/projects/film").json()["scene_state"]
    for sid in ("s01", "s02"):
        assert _said_once(st[sid]["video_prompt"], VI_RULE)
    assert VI_RULE not in st["s02"]["image_prompt"]


def test_detail_video_prompt_no_dialogue(client):
    make_project(client, pid="mute", dialogue_language="none")
    st = client.get("/api/projects/mute").json()["scene_state"]
    assert st["s01"]["video_prompt"].count(NO_DIALOGUE_RULE) == 1
    assert "All spoken dialogue is in" not in st["s01"]["video_prompt"]


def test_detail_without_language_unchanged(client):
    make_project(client)
    d = client.get("/api/projects/film").json()
    sb = Storyboard.model_validate(d["storyboard"])
    assert d["scene_state"]["s01"]["video_prompt"] == sb.for_flow(sb.video_prompt(sb.scenes[0]))


# --- AC20: clips_rendered ------------------------------------------------------------------------------------

def test_clips_rendered_counts_done_scenes(client):
    d = make_project(client)
    out = d / "output" / "storyboard"
    (out / "clips").mkdir(parents=True)
    clip = out / "clips" / "s01.mp4"
    clip.write_bytes(b"x")
    state = {"project_id": None, "scenes": {
        "s01": {"status": "done", "video": str(clip), "fingerprint": "old"},  # file, changed
        "s02": {"status": "done", "video": str(out / "clips" / "gone.mp4"), "rendered_as": "manual"},  # no file
        "s03": {"status": "submitting"},
        "s04": {"status": "failed", "error": "x"},
    }}
    (out / "state.json").write_text(json.dumps(state), encoding="utf-8")
    j = client.get("/api/projects/film").json()
    assert j["clips_rendered"] == 2


def test_clips_rendered_zero(client):
    make_project(client)
    assert client.get("/api/projects/film").json()["clips_rendered"] == 0
    (client.ws / "film" / "storyboard.yaml").write_text("title: X\nscenes: [{id: s01, mode: nope}]\n", encoding="utf-8")
    j = client.get("/api/projects/film").json()
    assert j["errors"] and j["clips_rendered"] == 0


# --- review fixes: control / format characters, PUT with a bad value already on disk ------------------------

BAD_CHARS = ["Viet\x00nam", "Viet\x1bnam", "Viet‮nam", "Viet​nam", "﻿Thai"]


@pytest.mark.parametrize("field", ["dialogue_language", "language"])
@pytest.mark.parametrize("value", BAD_CHARS)
def test_plan_rejects_control_chars(client, field, value):
    body = {"idea": "một ý tưởng", "dialogue_language": "Vietnamese", "language": "English", field: value}
    r = client.post("/api/projects/plan", json=body)
    assert r.status_code == 400
    assert "ký tự không hợp lệ" in r.json()["detail"]
    assert client.plan_calls == []


@pytest.mark.parametrize("key", ["dialogue_language", "subtitle_language"])
@pytest.mark.parametrize("value", ['"Viet\\0nam"', '"Viet\\u202enam"', '"Viet\\u200bnam"'])
def test_put_yaml_rejects_control_chars(client, key, value):
    d = make_project(client)
    before = (d / "storyboard.yaml").read_bytes()
    r = client.put("/api/projects/film/yaml", json={"text": before.decode("utf-8") + f"{key}: {value}\n"})
    assert r.status_code == 400, r.text
    assert "ký tự không hợp lệ" in r.json()["detail"]
    assert (d / "storyboard.yaml").read_bytes() == before


@pytest.mark.parametrize("key", ["dialogue_language", "subtitle_language"])
def test_put_project_rejects_control_chars(client, key):
    d = make_project(client)
    before = (d / "storyboard.yaml").read_bytes()
    raw = yaml.safe_load(before)
    raw[key] = "Viet\x1bnam"
    r = client.put("/api/projects/film", json={"storyboard": raw})
    assert r.status_code == 400
    assert (d / "storyboard.yaml").read_bytes() == before


def test_put_project_ignores_unchanged_bad_language_on_disk(client):
    d = make_project(client)
    (d / "storyboard.yaml").write_text((d / "storyboard.yaml").read_text(encoding="utf-8")
                                       + "dialogue_language: no\n", encoding="utf-8")  # YAML bool, hand-written
    raw = yaml.safe_load((d / "storyboard.yaml").read_text(encoding="utf-8"))
    assert raw["dialogue_language"] is False
    raw["title"] = "Film 2"  # autosave of another field
    r = client.put("/api/projects/film", json={"storyboard": raw})
    assert r.status_code == 200, r.text
    assert r.json()["errors"]  # still reported by the model validation
    assert yaml.safe_load((d / "storyboard.yaml").read_text(encoding="utf-8"))["title"] == "Film 2"
    raw["dialogue_language"] = 5  # but a changed bad value is refused
    r = client.put("/api/projects/film", json={"storyboard": raw})
    assert r.status_code == 400
    raw["dialogue_language"] = "Thai"  # and a good one fixes it
    r = client.put("/api/projects/film", json={"storyboard": raw})
    assert r.status_code == 200 and r.json()["errors"] == []
