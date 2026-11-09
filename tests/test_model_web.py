"""Model retirement, server side (AC6a, AC8–AC19, AC22, AC23): fake gflow, TestClient, injected "today"."""
import json
import shutil
import sys
import time
from pathlib import Path

import pytest
import yaml

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filmmaker import model_meta, planner  # noqa: E402
from filmmaker.pipeline import Pipeline  # noqa: E402
from filmmaker.storyboard import Storyboard  # noqa: E402
from filmmaker.web import server  # noqa: E402
from tests.model_helpers import (  # noqa: E402
    four_groups, set_scene, set_today, ui_out_dir, write_override, write_project,
)

FAKE = Path(__file__).parent / "fake_gflow.py"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Like the fixture in test_web.py (not imported: that file is being edited by another feature)."""
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "calls.log"))
    for k in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_MODELS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": False, "logged_in": False})

    def make(**settings):
        path = tmp_path / "settings.json"  # same file conftest points FILMMAKER_SETTINGS at
        path.write_text(json.dumps({"pause_min": 0, "pause_max": 0, "retries": 0, **settings}))
        monkeypatch.setattr(server, "SETTINGS_PATH", path)
        c = TestClient(server.create_app(tmp_path / "ws", token="t0k"))
        c.headers["X-Token"] = "t0k"
        c.ws = tmp_path / "ws"
        c.override = tmp_path / "models.yaml"  # <settings dir>/models.yaml (AC6)
        log = tmp_path / "calls.log"
        c.calls = lambda: [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
        return c

    return make


@pytest.fixture
def client(make_client):
    return make_client()


def wait(c, job, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def video_by_alias(res):
    return {m["alias"]: m for m in res["video"]}


def model_calls(c):
    return [x for x in c.calls() if x[:1] == ["models"]]


def video_calls(c):
    return [x for x in c.calls() if x[:1] == ["video"]]


# --- /api/models ---------------------------------------------------------------

def test_api_models_info_status_today(client, tmp_path, monkeypatch):
    # gflow also lists an alias the metadata does not know.
    data = {"video": {"models": [{"aliases": ["veo-lite"], "max_duration": 8}, {"aliases": ["veo-fast"], "max_duration": 8},
                                 {"aliases": ["brand-new"], "max_duration": 12}]},
            "image": {"models": [{"aliases": ["nano2"], "label": "Nano Banana 2 (default)"}]}}
    f = tmp_path / "models.json"
    f.write_text(json.dumps(data))
    monkeypatch.setenv("FAKE_GFLOW_MODELS", str(f))
    r = client.get("/api/models")
    assert r.status_code == 200
    res = r.json()
    assert res["source"] == "gflow" and res["today"] == "2026-11-09"
    assert res["plan"] == "ultra" and res["default_video_model"] == "veo-lite" and res["metadata_error"] is None
    v = video_by_alias(res)
    assert v["veo-lite"]["max_duration"] == 8
    assert v["veo-lite"]["info"]["durations"] == [4, 6, 8] and v["veo-lite"]["info"]["credits"] == 5  # AC39, AC3
    assert v["veo-lite"]["status"] == "active" and v["veo-lite"]["retires_label"] is None
    assert v["veo-fast"]["status"] == "retiring" and v["veo-fast"]["retires_label"] == "05/12"
    assert v["veo-fast"]["info"]["retires"] == "2026-12-05" and v["veo-fast"]["info"]["credits"] == 10
    assert v["brand-new"] == {"alias": "brand-new", "max_duration": 12, "info": None, "status": "active",
                              "retires_label": None}
    assert res["image"] == [{"alias": "nano2", "label": "Nano Banana 2"}]


def test_api_models_status_after_retirement(client, monkeypatch):
    set_today(monkeypatch, 2026, 12, 5)
    v = video_by_alias(client.get("/api/models").json())
    assert v["veo-fast"]["status"] == "retired" and v["omni-flash"]["status"] == "retired"
    assert v["veo-lite"]["status"] == "active"


def test_api_models_aliases_only_from_gflow(client, tmp_path, monkeypatch):
    data = {"video": {"models": [{"aliases": [a], "max_duration": 8} for a in ("veo-fast", "veo-lite")]},
            "image": {"models": [{"aliases": ["nano2"], "label": "Nano Banana 2"}]}}
    f = tmp_path / "models.json"
    f.write_text(json.dumps(data))
    monkeypatch.setenv("FAKE_GFLOW_MODELS", str(f))
    res = client.get("/api/models").json()
    assert [m["alias"] for m in res["video"]] == ["veo-fast", "veo-lite"]  # no veo-quality although it is in metadata


def test_api_models_reads_override(client):
    write_override(client.override, "plan: pro\nmodels:\n  veo-lite:\n    price: {unit: clip, ultra: 7, default: 12}\n")
    res = client.get("/api/models").json()
    v = video_by_alias(res)
    assert res["plan"] == "pro" and v["veo-lite"]["info"]["credits"] == 12  # no "pro" price: default
    assert v["veo-lite"]["info"]["durations"] == [4, 6, 8]  # untouched fields come from the package (AC7)


def test_api_models_override_from_env(client, tmp_path, monkeypatch):
    other = write_override(tmp_path / "elsewhere.yaml", "models:\n  veo-lite:\n    retires: 2026-11-01\n")
    monkeypatch.setenv("FILMMAKER_MODELS", str(other))
    assert video_by_alias(client.get("/api/models").json())["veo-lite"]["status"] == "retired"


def test_metadata_merged_after_cache(client):
    first = video_by_alias(client.get("/api/models").json())
    assert first["veo-lite"]["status"] == "active"
    write_override(client.override, "models:\n  veo-lite:\n    retires: 2026-11-01\n")
    second = video_by_alias(client.get("/api/models").json())
    assert second["veo-lite"]["status"] == "retired" and second["veo-lite"]["retires_label"] == "01/11"
    client.override.unlink()
    assert video_by_alias(client.get("/api/models").json())["veo-lite"]["status"] == "active"
    assert len(model_calls(client)) == 1


def test_fallback_not_cached(client, monkeypatch):
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "models:2:false:1")
    res = client.get("/api/models").json()
    assert res["source"] == "fallback"
    assert [m["alias"] for m in res["video"]] == ["veo-fast", "veo-quality", "veo-lite", "veo-lite-lp",
                                                   "omni-flash", "omni-1.2-flash"]
    assert [m["max_duration"] for m in res["video"]] == [8, 8, 8, 8, 10, 15]
    assert [m["alias"] for m in res["image"]] == ["nano2", "nano-pro", "imagen4", "nano2-lite"]
    assert video_by_alias(res)["veo-fast"]["status"] == "retiring"  # warnings still work without gflow
    res2 = client.get("/api/models").json()
    assert res2["source"] == "gflow" and [m["alias"] for m in res2["image"]] == ["nano2", "nano-pro"]
    assert len(model_calls(client)) == 2
    client.get("/api/models")
    assert len(model_calls(client)) == 2  # the gflow answer is cached


def test_fallback_when_gflow_missing(client, monkeypatch):
    monkeypatch.setenv("GFLOW_BIN", "/nonexistent/gflow-binary")
    res = client.get("/api/models").json()
    assert res["source"] == "fallback" and len(res["video"]) == 6
    assert not hasattr(server, "FALLBACK_MODELS")


def test_api_models_reports_metadata_error(client):
    write_override(client.override, "models: [unclosed\n")
    r = client.get("/api/models")
    assert r.status_code == 200
    res = r.json()
    assert str(client.override) in res["metadata_error"]
    assert video_by_alias(res)["veo-fast"]["status"] == "retiring"  # package values still used


def test_broken_package_metadata(client, tmp_path, monkeypatch):
    broken = tmp_path / "broken-package.yaml"
    broken.write_text("models: [oops\n")
    monkeypatch.setattr(model_meta, "PACKAGE_FILE", broken)
    write_override(client.override, "models:\n  veo-lite:\n    retires: 2026-11-01\n")  # ignored: package broken
    res = client.get("/api/models").json()
    assert res["source"] == "gflow" and len(res["video"]) == 6
    assert all(m["info"] is None and m["status"] == "active" for m in res["video"])
    assert "broken-package.yaml" in res["metadata_error"]

    # gflow failing at the same time: the hard-coded minimal list.
    c2 = TestClient(server.create_app(tmp_path / "ws2", token="t0k"))
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "models:2:false")
    res = c2.get("/api/models").json()
    assert res["source"] == "fallback"
    assert res["video"] == [{"alias": "veo-lite", "max_duration": 8, "info": None, "status": "active",
                             "retires_label": None}]
    assert res["image"] == [{"alias": "nano2", "label": "Nano Banana 2"}]
    assert "broken-package.yaml" in res["metadata_error"]

    # Still no 500 elsewhere, new projects still on veo-lite, nothing is blocked.
    set_today(monkeypatch, 2026, 12, 20)
    pid = client.post("/api/projects", json={"title": "x"}).json()["id"]
    assert yaml.safe_load((client.ws / pid / "storyboard.yaml").read_text())["model"] == "veo-lite"
    write_project(client.ws, "old", "veo-fast", [{"id": "s01"}])
    d = client.get("/api/projects/old").json()
    assert d["model_warning"] is None and d["scene_state"]["s01"]["model_status"] == "active"
    assert client.post("/api/projects/old/estimate", json={"action": "video"}).json()["retired"] == []


def test_wrong_type_override_no_500_no_crash(client):
    write_override(client.override, "models:\n  veo-fast: 2026-12-05\n  veo-lite:\n    price: 5\n")
    four_groups(client.ws, "film")
    r = client.get("/api/models")
    assert r.status_code == 200
    res = r.json()
    assert "models.veo-fast" in res["metadata_error"] and "models.veo-lite.price" in res["metadata_error"]
    v = video_by_alias(res)
    assert v["veo-fast"]["status"] == "retiring" and v["veo-lite"]["info"]["credits"] == 5  # package values
    d = client.get("/api/projects/film")
    assert d.status_code == 200 and d.json()["model_warning"]["models"][0]["alias"] == "veo-fast"
    e = client.post("/api/projects/film/estimate", json={"action": "video"})
    assert e.status_code == 200 and e.json()["retired"] == []


# --- new projects / opening old ones ------------------------------------------------

def test_new_projects_default_veo_lite(client):
    for source in ("blank", "template"):
        pid = client.post("/api/projects", json={"title": f"Phim {source}", "source": source}).json()["id"]
        data = yaml.safe_load((client.ws / pid / "storyboard.yaml").read_text(encoding="utf-8"))
        assert data["model"] == "veo-lite", source
        assert client.get(f"/api/projects/{pid}").json()["errors"] == []


def test_new_project_uses_metadata_default(client):
    write_override(client.override, "default_video_model: omni-1.2-flash\n")
    pid = client.post("/api/projects", json={"title": "x"}).json()["id"]
    assert yaml.safe_load((client.ws / pid / "storyboard.yaml").read_text())["model"] == "omni-1.2-flash"


def test_open_old_project_keeps_bytes(client, monkeypatch):
    p = write_project(client.ws, "old", "veo-fast", [{"id": "s01"}, {"id": "s02", "model": "veo-quality"}])
    before = p.read_bytes()
    for day in ((2026, 11, 9), (2026, 12, 20)):
        set_today(monkeypatch, *day)
        assert client.get("/api/projects/old").status_code == 200
        client.post("/api/projects/old/estimate", json={"action": "video"})
    assert p.read_bytes() == before


def test_import_keeps_old_model(client):
    text = yaml.safe_dump({"title": "Cũ", "model": "veo-fast", "scenes": [{"id": "s01", "mode": "t2v", "prompt": "x"}]})
    pid = client.post("/api/projects/import", files={"file": ("old.yaml", text, "text/yaml")}).json()["id"]
    assert yaml.safe_load((client.ws / pid / "storyboard.yaml").read_text())["model"] == "veo-fast"


@pytest.mark.parametrize("case", ["old_models", "empty_model"])
def test_planner_output_normalized_web(make_client, monkeypatch, case):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")  # plan_auto writes os.environ; monkeypatch cleans it up
    c = make_client(planner="api", anthropic_api_key="x")

    def fake_plan(idea, **kw):
        sb = {"title": "AI film", "scenes": [{"id": "s01", "mode": "t2v", "prompt": "a", "model": "veo-quality"},
                                             {"id": "s02", "mode": "t2v", "prompt": "b", "model": "omni-1.2-flash"}]}
        if case == "old_models":
            sb["model"] = "veo-fast"
        return Storyboard.model_validate(sb)

    monkeypatch.setattr(planner, "plan", fake_plan)
    r = c.post("/api/projects/plan", json={"idea": "một ý tưởng"})
    assert r.status_code == 200, r.text
    j = wait(c, r.json())
    assert j["status"] == "done", j["lines"]
    data = yaml.safe_load((c.ws / j["result"]["project"] / "storyboard.yaml").read_text(encoding="utf-8"))
    assert data["model"] == "veo-lite"
    s01, s02 = data["scenes"]
    assert "model" not in s01 and s02["model"] == "omni-1.2-flash"


def test_server_has_no_retiring_defaults():
    src = Path(server.__file__).read_text(encoding="utf-8")
    assert "veo-fast" not in src and "veo-quality" not in src


# --- detail(): model_status, model_warning, model_note ---------------------------------

def test_model_warning_before_and_after(client, monkeypatch):
    four_groups(client.ws, "film")  # s01 done, s02 interrupted, s03 changed, s04 todo
    d = client.get("/api/projects/film").json()
    st = d["scene_state"]
    assert st["s03"]["status"] == "changed"
    assert {sid: st[sid]["model_status"] for sid in st} == dict.fromkeys(("s01", "s02", "s03", "s04"), "retiring")
    assert st["s01"]["model_retires_label"] == "05/12"
    w = d["model_warning"]
    assert w["models"] == [{"alias": "veo-fast", "status": "retiring", "retires_label": "05/12",
                            "todo": 2, "done": 1, "interrupted": 1}]
    assert w["lines"] == ["Model veo-fast ngừng ngày 05/12: 2 cảnh chưa có clip, 1 cảnh đã có clip, 1 cảnh bị ngắt."]
    assert w["text"] == w["lines"][0]
    assert d["models_used"] == {"veo-fast": {"status": "retiring", "retires_label": "05/12", "known": True}}
    assert "backup" in d  # backup feature field kept

    set_today(monkeypatch, 2026, 12, 5)
    d = client.get("/api/projects/film").json()
    assert d["scene_state"]["s01"]["model_status"] == "retired"
    assert d["model_warning"]["lines"] == [
        "Model veo-fast đã ngừng từ 05/12: 2 cảnh chưa có clip, 1 cảnh đã có clip, 1 cảnh bị ngắt."]


def test_model_warning_without_interrupted_and_none(client):
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01"}, {"id": "s02"}])
    set_scene(p, "s01", "done")
    assert client.get("/api/projects/film").json()["model_warning"]["lines"] == [
        "Model veo-fast ngừng ngày 05/12: 1 cảnh chưa có clip, 1 cảnh đã có clip."]
    write_project(client.ws, "fresh", "veo-lite", [{"id": "s01"}, {"id": "s02", "model": "omni-1.2-flash"}])
    d = client.get("/api/projects/fresh").json()
    assert d["model_warning"] is None and d["scene_state"]["s01"]["model_status"] == "active"
    assert d["scene_state"]["s01"]["model_retires_label"] is None


def test_model_warning_two_models_one_line_each(client):
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01", "model": "omni-flash"}, {"id": "s02"},
                                                      {"id": "s03", "model": "veo-lite"}])
    set_scene(p, "s02", "done")
    d = client.get("/api/projects/film").json()
    assert d["model_warning"]["lines"] == [
        "Model omni-flash ngừng ngày 05/12: 1 cảnh chưa có clip, 0 cảnh đã có clip.",
        "Model veo-fast ngừng ngày 05/12: 0 cảnh chưa có clip, 1 cảnh đã có clip."]
    assert d["scene_state"]["s03"]["model_status"] == "active"
    assert set(d["models_used"]) == {"omni-flash", "veo-fast", "veo-lite"}


def test_storyboard_with_errors_has_empty_model_fields(client):
    d = client.ws / "bad"
    (d / "assets").mkdir(parents=True)
    (d / "storyboard.yaml").write_text("title: x\nmodel: veo-fast\nscenes: []\n")
    r = client.get("/api/projects/bad")
    assert r.status_code == 200
    j = r.json()
    assert j["errors"] and j["model_warning"] is None and j["model_note"] is None and j["models_used"] == {}


def test_model_note_only_for_todo_null_model(client):
    p = write_project(client.ws, "auto", None, [{"id": "s01"}, {"id": "s02"}])
    note = client.get("/api/projects/auto").json()["model_note"]
    assert note.startswith("Flow tự chọn model;") and note.endswith("cho các cảnh chưa có clip.")
    set_scene(p, "s01", "done")
    set_scene(p, "s02", "interrupted")
    assert client.get("/api/projects/auto").json()["model_note"] is None  # no scene without a clip uses null
    write_project(client.ws, "pinned", None, [{"id": "s01", "model": "veo-lite"}])
    assert client.get("/api/projects/pinned").json()["model_note"] is None
    write_project(client.ws, "set", "veo-lite", [{"id": "s01"}])
    assert client.get("/api/projects/set").json()["model_note"] is None


# --- /estimate ---------------------------------------------------------------

def test_estimate_retired_and_manual_clip(client, monkeypatch):
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01"}, {"id": "s02"}])
    set_scene(p, "s01", "interrupted", manual_clip=True)
    set_scene(p, "s02", "interrupted")
    body = {"action": "video", "retry_unknown": True}
    assert client.post("/api/projects/film/estimate", json=body).json()["retired"] == []  # before 05/12
    set_today(monkeypatch, 2026, 12, 6)
    est = client.post("/api/projects/film/estimate", json=body).json()
    assert est["retired"] == [{"id": "s02", "model": "veo-fast", "retires_label": "05/12"}]
    assert [r["id"] for r in est["renders"]] == ["s02"]


def test_estimate_retired_follows_only_and_force(client, monkeypatch):
    set_today(monkeypatch, 2026, 12, 6)
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01"}, {"id": "s02"}, {"id": "s03", "model": "veo-lite"}])
    set_scene(p, "s01", "done")
    est = client.post("/api/projects/film/estimate", json={"action": "video"}).json()
    assert [r["id"] for r in est["retired"]] == ["s02"]  # s01 done (not sent), s03 on an active model
    est = client.post("/api/projects/film/estimate", json={"action": "video", "only": ["s03"]}).json()
    assert est["retired"] == []
    est = client.post("/api/projects/film/estimate", json={"action": "video", "only": ["s01"], "force": ["s01"]}).json()
    assert [r["id"] for r in est["retired"]] == ["s01"]


def test_estimate_excludes_interrupted_with_manual_clip(client):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}, {"id": "s03"}])
    set_scene(p, "s01", "interrupted", manual_clip=True)
    set_scene(p, "s02", "interrupted")
    est = client.post("/api/projects/film/estimate", json={"action": "video"}).json()
    assert est["blocked"] == ["s02"]
    assert [r["id"] for r in est["renders"]] == ["s03"]  # s01 will be adopted, not rendered


def test_video_plan_has_no_side_effects(client):
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01"}, {"id": "s02"}])
    pipe = set_scene(p, "s01", "interrupted", manual_clip=True)
    state = ui_out_dir(p) / "state.json"
    before = state.read_bytes()
    pipe.video_plan(None, set(), True)
    client.post("/api/projects/film/estimate", json={"action": "video", "retry_unknown": True})
    client.get("/api/projects/film")
    assert state.read_bytes() == before


# --- /run ----------------------------------------------------------------------

def test_run_409_when_retired_no_job_no_gflow_call(make_client, monkeypatch):
    c = make_client(auto_backup=True)
    set_today(monkeypatch, 2026, 12, 6)
    p = write_project(c.ws, "film", "veo-fast", [{"id": "s01"}, {"id": "s02", "model": "veo-lite"}])
    for action in ("video", "make"):
        r = c.post("/api/projects/film/run", json={"action": action})
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["message"] == "Không thể render: 1 cảnh dùng model đã ngừng"
        assert detail["retired"] == [{"id": "s01", "model": "veo-fast", "retires_label": "05/12"}]
    assert c.get("/api/jobs/current").json() is None
    assert c.calls() == []
    assert not (ui_out_dir(p) / "clips").exists()


def test_run_unknown_scene_still_400_first(client, monkeypatch):
    set_today(monkeypatch, 2026, 12, 6)
    write_project(client.ws, "film", "veo-fast", [{"id": "s01"}])
    assert client.post("/api/projects/film/run", json={"action": "video", "only": ["zz"]}).status_code == 400


@needs_ffmpeg
def test_run_other_actions_not_blocked(client, monkeypatch):
    set_today(monkeypatch, 2026, 12, 6)
    write_project(client.ws, "film", "veo-fast", [{"id": "s01"}])
    for action in ("dry", "keyframes", "stitch", "thumbnail"):
        r = client.post("/api/projects/film/run", json={"action": action})
        assert r.status_code == 200, (action, r.text)
        j = wait(client, r.json())
        assert not any("KHÔNG GỬI" in line for line in j["lines"]), action
    assert video_calls(client) == []


@needs_ffmpeg
def test_run_not_blocked_by_scenes_not_sent(client, monkeypatch):
    p = write_project(client.ws, "film", "veo-fast",
                      [{"id": "s01"}, {"id": "s02"}, {"id": "s03", "model": "veo-lite"}, {"id": "s04"}])
    set_scene(p, "s01", "done")                        # done: not sent
    set_scene(p, "s02", "interrupted", manual_clip=True)  # adopted: not sent
    set_today(monkeypatch, 2026, 12, 6)
    r = client.post("/api/projects/film/run",
                    json={"action": "video", "only": ["s01", "s02", "s03"], "retry_unknown": True})  # s04 outside only
    assert r.status_code == 200, r.text
    j = wait(client, r.json())
    assert j["status"] == "done", j["lines"]
    assert j["result"]["done"] == ["s03"] and set(j["result"]["skipped"]) == {"s01", "s02"}
    assert len(video_calls(client)) == 1
    assert client.get("/api/projects/film").json()["scene_state"]["s02"]["rendered_as"] == "manual"


@needs_ffmpeg
def test_web_job_refuses_after_midnight(client, monkeypatch):
    set_today(monkeypatch, 2026, 12, 4)
    write_project(client.ws, "film", "veo-fast", [{"id": "s01"}, {"id": "s02"}])
    original = Pipeline._render_scene

    def render_then_midnight(self, s, project, opts):
        out = original(self, s, project, opts)
        set_today(monkeypatch, 2026, 12, 5)  # the clock passes 0:00 of the retirement day mid-job
        return out

    monkeypatch.setattr(Pipeline, "_render_scene", render_then_midnight)
    r = client.post("/api/projects/film/run", json={"action": "video"})
    assert r.status_code == 200, r.text  # accepted on 04/12
    j = wait(client, r.json())
    assert j["status"] == "failed" and j["result"]["error"] == "1 cảnh lỗi"
    assert j["result"]["done"] == ["s01"] and "đã ngừng" in j["result"]["failed"]["s02"]
    assert any("[s02] KHÔNG GỬI:" in line for line in j["lines"])
    assert len(video_calls(client)) == 1
    d = client.get("/api/projects/film").json()
    assert d["scene_state"]["s02"]["status"] == "todo"  # state untouched


@needs_ffmpeg
def test_dry_run_job_is_not_refused(client, monkeypatch):
    set_today(monkeypatch, 2026, 12, 6)
    write_project(client.ws, "film", "veo-fast", [{"id": "s01"}])
    j = wait(client, client.post("/api/projects/film/run", json={"action": "dry"}).json())
    assert j["status"] == "done", j["lines"]
    assert not any("KHÔNG GỬI" in line for line in j["lines"])
    assert j["result"]["done"] == ["s01"]


# --- after review: /api/models never 500, jobs keep a model change ----------------------------

def test_api_models_non_finite_price_no_500(client):
    write_override(client.override, "models:\n  veo-lite:\n    price: {default: .inf, ultra: .nan}\n"
                                     "  veo-fast:\n    price: {default: -3}\n")
    r = client.get("/api/models")
    assert r.status_code == 200, r.text
    res = r.json()
    v = video_by_alias(res)
    assert v["veo-lite"]["info"]["credits"] == 5 and v["veo-fast"]["info"]["credits"] == 10  # package prices
    assert "models.veo-lite.price" in res["metadata_error"] and "models.veo-fast.price" in res["metadata_error"]


def test_override_retiring_default_not_used_for_new_projects(client):
    write_override(client.override, "default_video_model: veo-fast\n")
    res = client.get("/api/models").json()
    assert res["default_video_model"] == "veo-lite" and "default_video_model" in res["metadata_error"]
    pid = client.post("/api/projects", json={"title": "x"}).json()["id"]
    assert yaml.safe_load((client.ws / pid / "storyboard.yaml").read_text())["model"] == "veo-lite"


def _change_model_while_job_waits(c, pid, start_job, reached):
    """Hold the project lock while the job runs; once it reached its storyboard write, change the model
    (as model-change would, under the lock), then let the job finish. The change must survive."""
    p = c.ws / pid / "storyboard.yaml"
    with c.app.state.sb_lock(pid):
        job = start_job()
        end = time.time() + 30
        while not reached() and time.time() < end:
            time.sleep(0.05)
        time.sleep(0.5)  # the job is now waiting for the lock (or, before the fix, had read the old file)
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        data["model"] = "omni-1.2-flash"
        p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    j = wait(c, job)
    assert j["status"] == "done", j["lines"]
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def test_portraits_job_keeps_model_change(client):
    pid = client.post("/api/projects", json={"title": "Nhân vật", "source": "template"}).json()["id"]
    name = client.get(f"/api/projects/{pid}").json()["storyboard"]["characters"][0]["name"]
    chars = client.ws / pid / "assets" / "characters"
    data = _change_model_while_job_waits(
        client, pid, lambda: client.post(f"/api/projects/{pid}/characters/portraits", json={}).json(),
        lambda: chars.is_dir() and any(chars.iterdir()))
    assert data["model"] == "omni-1.2-flash"
    assert data["characters"][0]["name"] == name and data["characters"][0]["image"].startswith("assets/characters/")


def test_thumbnail_job_keeps_model_change(client):
    pid = client.post("/api/projects", json={"title": "Tựa", "source": "template"}).json()["id"]
    p = client.ws / pid / "storyboard.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    data.setdefault("thumbnail", {}).pop("text", None)
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (client.ws / pid / "ui.json").write_text(json.dumps({"thumbnail_titles": [{"text": "Tựa gợi ý", "vi": ""}]}),
                                             encoding="utf-8")
    data = _change_model_while_job_waits(
        client, pid, lambda: client.post(f"/api/projects/{pid}/run", json={"action": "thumbnail"}).json(),
        lambda: True)
    assert data["model"] == "omni-1.2-flash" and data["thumbnail"]["text"] == "Tựa gợi ý"
