"""Model change preview/apply (AC27–AC39): fake gflow, TestClient, injected "today"."""
import json
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filmmaker import model_change, model_meta, planner  # noqa: E402
from filmmaker.model_change import ChangeError, plan_change, round_options  # noqa: E402
from filmmaker.storyboard import load_storyboard  # noqa: E402
from filmmaker.web import server  # noqa: E402
from tests.model_helpers import (  # noqa: E402
    four_groups, pipeline, set_scene, set_today, ui_out_dir, write_override, write_project,
)

FAKE = Path(__file__).parent / "fake_gflow.py"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "calls.log"))
    for k in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_MODELS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": False, "logged_in": False})
    path = tmp_path / "settings.json"  # same file conftest points FILMMAKER_SETTINGS at
    path.write_text(json.dumps({"pause_min": 0, "pause_max": 0, "retries": 0}))
    monkeypatch.setattr(server, "SETTINGS_PATH", path)
    c = TestClient(server.create_app(tmp_path / "ws", token="t0k"))
    c.headers["X-Token"] = "t0k"
    c.ws = tmp_path / "ws"
    c.override = tmp_path / "models.yaml"
    log = tmp_path / "calls.log"
    c.calls = lambda: [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return c


def preview(c, pid="film", **body):
    r = c.post(f"/api/projects/{pid}/model-change/preview", json={"target": "veo-lite", **body})
    assert r.status_code == 200, r.text
    return r.json()


def apply(c, pid="film", **body):
    base = preview(c, pid, **{k: v for k, v in body.items() if k != "base"})["base"]
    r = c.post(f"/api/projects/{pid}/model-change", json={"target": "veo-lite", "base": base, **body})
    assert r.status_code == 200, r.text
    return r.json()


def raw(sb_path):
    return yaml.safe_load(sb_path.read_text(encoding="utf-8"))


def scene_models(sb_path):
    return {s["id"]: s.get("model") for s in raw(sb_path)["scenes"]}


def fingerprints(sb_path, ids):
    sb = load_storyboard(sb_path)  # read back from disk
    return {s.id: sb.scene_fingerprint(s) for s in sb.scenes if s.id in ids}


# --- AC27: preview writes nothing, bad input is 400 ------------------------------

def test_preview_writes_nothing_and_rejects_bad_target(client):
    p = four_groups(client.ws)
    before = p.read_bytes()
    res = preview(client)
    assert set(res) >= {"mode", "target", "change", "redo", "interrupted", "keep", "pin", "bad_duration",
                        "storyboard_model", "credits", "credits_approx", "has_changes", "base"}
    assert res["base"] == model_change.content_hash(p) and res["mode"] == "project"
    assert p.read_bytes() == before
    for body in ({"target": "veo-fast"}, {"target": "nope"}, {"target": "veo-lite", "scenes": ["zz"]},
                 {"target": "veo-lite", "include": ["zz"]}, {"target": "veo-lite", "rounding": "sideways"},
                 {"target": "veo-lite", "scenes": ["s01"], "include": ["s02"]}):
        r = client.post("/api/projects/film/model-change/preview", json=body)
        assert r.status_code == 400, body
        r = client.post("/api/projects/film/model-change", json={**body, "base": res["base"]})
        assert r.status_code == 400, body
    assert p.read_bytes() == before


def test_retired_target_rejected(client, monkeypatch):
    four_groups(client.ws)
    write_override(client.override, "models:\n  veo-lite:\n    retires: 2026-11-01\n")
    r = client.post("/api/projects/film/model-change/preview", json={"target": "veo-lite"})
    assert r.status_code == 400


# --- AC28–AC31: project mode -------------------------------------------------------

def test_project_mode_selects_only_todo_old(client):
    p = write_project(client.ws, "film", "veo-fast", [
        {"id": "s01"}, {"id": "s02"}, {"id": "s03"}, {"id": "s04", "model": "omni-1.2-flash"},
        {"id": "s05", "model": "veo-quality"}])
    set_scene(p, "s01", "done")
    set_scene(p, "s02", "interrupted")
    res = preview(client)
    assert res["change"] == ["s03", "s05"]
    assert res["keep"] == ["s01", "s02"] and res["pin"] == ["s01", "s02"]
    assert res["redo"] == [] and res["interrupted"] == []
    assert res["storyboard_model"] == {"from": "veo-fast", "to": "veo-lite"}
    apply(client)
    assert scene_models(p)["s04"] == "omni-1.2-flash"  # pinned active model kept


def test_project_mode_null_model_untouched(client):
    p = write_project(client.ws, "film", None, [{"id": "s01"}, {"id": "s02", "model": "veo-fast"}])
    res = preview(client)
    assert res["change"] == ["s02"] and res["storyboard_model"] is None and res["pin"] == []
    apply(client)
    assert scene_models(p) == {"s01": None, "s02": "veo-lite"}
    assert "model" not in raw(p)


def test_scene_model_cleared_or_set(client):
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01"}, {"id": "s02", "model": "veo-quality"}])
    apply(client)
    assert raw(p)["model"] == "veo-lite"
    assert scene_models(p) == {"s01": None, "s02": None}
    # storyboard model active and different from target: scenes get scene.model = target
    p2 = write_project(client.ws, "other", "omni-1.2-flash", [{"id": "s01", "model": "veo-fast"}])
    apply(client, "other")
    assert raw(p2)["model"] == "omni-1.2-flash" and scene_models(p2) == {"s01": "veo-lite"}


def test_pin_preserves_fingerprint_with_model(client):
    p = four_groups(client.ws, model="veo-fast")
    before = fingerprints(p, {"s01", "s02"})
    res = apply(client)
    assert res["changed"] is True and res["preview"]["pin"] == ["s01", "s02"]
    assert scene_models(p) == {"s01": "veo-fast", "s02": "veo-fast", "s03": None, "s04": None}
    assert fingerprints(p, {"s01", "s02"}) == before
    pipe = pipeline(p)
    s01 = next(s for s in pipe.sb.scenes if s.id == "s01")
    assert pipe.is_done(s01) and pipe.scene_group(pipe.sb.scenes[1]) == "interrupted"
    assert res["project"]["scene_state"]["s01"]["status"] == "done"


def test_null_storyboard_model_preserves_fingerprint(client):
    p = write_project(client.ws, "film", None, [{"id": "s01"}, {"id": "s02"}, {"id": "s03", "model": "veo-fast"}])
    set_scene(p, "s01", "done")
    set_scene(p, "s02", "interrupted")
    before = fingerprints(p, {"s01", "s02"})
    res = apply(client)
    assert res["preview"]["pin"] == [] and res["preview"]["storyboard_model"] is None
    assert scene_models(p) == {"s01": None, "s02": None, "s03": "veo-lite"} and "model" not in raw(p)
    assert fingerprints(p, {"s01", "s02"}) == before
    pipe = pipeline(p)
    assert pipe.is_done(pipe.sb.scenes[0])


def test_empty_string_model_counts_as_none(client):
    p = write_project(client.ws, "film", "", [{"id": "s01", "model": ""}, {"id": "s02", "model": "veo-fast"}])
    set_scene(p, "s01", "done")
    res = preview(client)
    assert res["change"] == ["s02"] and res["pin"] == [] and res["storyboard_model"] is None


# --- AC32: single-scene mode -------------------------------------------------------

def test_single_scene_mode(client):
    p = write_project(client.ws, "film", "veo-fast", [
        {"id": "s01"}, {"id": "s02"}, {"id": "s03"}, {"id": "s04", "model": "veo-lite", "duration": 10}])
    set_scene(p, "s01", "done")
    set_scene(p, "s02", "interrupted")
    res = preview(client, scenes=["s01", "s02", "s04"])
    assert res["mode"] == "scene" and res["change"] == ["s01", "s02"]
    assert res["redo"] == ["s01"] and res["interrupted"] == ["s02"]
    assert res["pin"] == [] and res["keep"] == [] and res["storyboard_model"] is None and res["bad_duration"] == []
    apply(client, scenes=["s01", "s02"])
    assert raw(p)["model"] == "veo-fast"
    assert scene_models(p) == {"s01": "veo-lite", "s02": "veo-lite", "s03": None, "s04": "veo-lite"}
    # scene already on the target, duration not allowed by it: nothing to do, file untouched (review #9)
    before = p.read_bytes()
    res = preview(client, scenes=["s04"])
    assert res["change"] == [] and res["bad_duration"] == [] and res["has_changes"] is False
    r = client.post("/api/projects/film/model-change", json={"target": "veo-lite", "scenes": ["s04"], "base": res["base"]})
    assert r.status_code == 200 and r.json()["changed"] is False and r.json()["message"] == "Không có gì thay đổi"
    assert p.read_bytes() == before


def test_single_scene_mode_clears_when_storyboard_is_target(client):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01", "model": "veo-fast"}])
    apply(client, scenes=["s01"])
    assert scene_models(p) == {"s01": None}


# --- AC33: rounding ------------------------------------------------------------------

@pytest.mark.parametrize("d,up,down", [(2, 4, 4), (3, 4, 4), (5, 6, 4), (7, 8, 6), (10, 8, 8), (12, 8, 8), (15, 8, 8)])
def test_round_options_table(d, up, down):
    assert round_options(d, [4, 6, 8]) == (up, down)


def test_rounding_in_plan(tmp_path):
    p = write_project(tmp_path, "film", "veo-fast", [
        {"id": "s01", "duration": 5}, {"id": "s02"}, {"id": "s03", "duration": 6}, {"id": "s04", "duration": 10}])
    meta = model_meta.load()
    plan = plan_change(pipeline(p), meta, "veo-lite")
    assert plan.bad_duration == [{"id": "s01", "duration": 5, "up": 6, "down": 4},
                                 {"id": "s04", "duration": 10, "up": 8, "down": 8}]  # empty duration is fine
    assert all("duration" not in e for e in plan.edits.values())
    plan = plan_change(pipeline(p), meta, "veo-lite", rounding="down")
    assert plan.edits["s01"]["duration"] == 4 and plan.edits["s04"]["duration"] == 8
    assert "duration" not in plan.edits["s02"] and "duration" not in plan.edits["s03"]
    with pytest.raises(ChangeError):
        plan_change(pipeline(p), meta, "veo-lite", scenes=["s01"], include=["s02"])


# --- AC34–AC36: apply ----------------------------------------------------------------

def test_apply_requires_rounding_and_matching_base(client):
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01", "duration": 5}, {"id": "s02"}])
    base = preview(client)["base"]
    url = "/api/projects/film/model-change"
    r = client.post(url, json={"target": "veo-lite", "base": base})
    assert r.status_code == 400  # bad_duration without rounding
    r = client.post(url, json={"target": "veo-lite"})
    assert r.status_code == 400  # no base
    p.write_text(p.read_text(encoding="utf-8") + "\n# edited by hand\n", encoding="utf-8")
    before = p.read_bytes()
    r = client.post(url, json={"target": "veo-lite", "base": base, "rounding": "up"})
    assert r.status_code == 409 and p.read_bytes() == before
    res = preview(client, rounding="up")
    r = client.post(url, json={"target": "veo-lite", "base": res["base"], "rounding": "up"})
    assert r.status_code == 200 and r.json()["changed"] is True
    assert r.json()["preview"]["change"] == res["change"]
    data = raw(p)
    assert data["model"] == "veo-lite" and data["scenes"][0]["duration"] == 6 and "model" not in data["scenes"][0]


def test_apply_no_job_no_gflow(client):
    p = four_groups(client.ws)
    apply(client)
    assert client.calls() == []
    assert client.get("/api/jobs/current").json() is None
    assert not (ui_out_dir(p) / "clips").exists()


def test_apply_twice_is_noop(client):
    p = four_groups(client.ws)
    apply(client)
    after_first = p.read_bytes()
    res = preview(client)
    assert res["change"] == [] and res["pin"] == [] and res["storyboard_model"] is None
    assert res["has_changes"] is False
    second = apply(client)
    assert second["changed"] is False
    assert p.read_bytes() == after_first


# --- AC37: escaping the block loop -----------------------------------------------------

@pytest.mark.parametrize("case", ["force_done", "retry_interrupted", "todo"])
def test_escape_block_loop_three_cases(client, monkeypatch, case):
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01"}, {"id": "s02", "model": "veo-lite"}])
    set_scene(p, "s02", "done")
    if case == "force_done":
        set_scene(p, "s01", "done")
        body = {"action": "video", "force": ["s01"]}
    elif case == "retry_interrupted":
        set_scene(p, "s01", "interrupted")
        body = {"action": "video", "retry_unknown": True}
    else:
        body = {"action": "video"}
    set_today(monkeypatch, 2026, 12, 6)
    retired = client.post("/api/projects/film/estimate", json=body).json()["retired"]
    assert [r["id"] for r in retired] == ["s01"]
    assert client.post("/api/projects/film/run", json=body).status_code == 409
    apply(client, include=[r["id"] for r in retired])
    assert client.post("/api/projects/film/estimate", json=body).json()["retired"] == []
    assert client.calls() == []


# --- AC38 -------------------------------------------------------------------------------

def test_all_done_still_switches_storyboard_model(client):
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01"}, {"id": "s02"}])
    set_scene(p, "s01", "done")
    set_scene(p, "s02", "done")
    before = fingerprints(p, {"s01", "s02"})
    res = preview(client)
    assert res["change"] == [] and res["storyboard_model"] == {"from": "veo-fast", "to": "veo-lite"}
    assert res["has_changes"] is True and res["credits"] == 0
    assert apply(client)["changed"] is True
    assert raw(p)["model"] == "veo-lite" and scene_models(p) == {"s01": "veo-fast", "s02": "veo-fast"}
    assert fingerprints(p, {"s01", "s02"}) == before


def test_apply_nothing_to_change(client):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02", "model": "omni-1.2-flash"}])
    before = p.read_bytes()
    res = apply(client)
    assert res == {"changed": False, "message": "Không có gì thay đổi", "project": res["project"]}
    assert p.read_bytes() == before


# --- AC39: credits -------------------------------------------------------------------

def test_credits_clip_second_and_unknown(client):
    write_project(client.ws, "film", "veo-fast", [{"id": "s01", "duration": 10}, {"id": "s02"}, {"id": "s03", "duration": 7}])
    res = preview(client)
    assert res["credits"] == 15 and res["credits_approx"] is False  # veo-lite, ultra: 5 per clip
    res = preview(client, target="veo-lite-lp")
    assert res["credits"] is None  # no price in the metadata
    res = preview(client, target="omni-1.2-flash")  # 3 per second, no duration list
    assert res["credits"] == 3 * (10 + 8 + 7) and res["credits_approx"] is True and res["bad_duration"] == []
    write_override(client.override, "models:\n  omni-1.2-flash:\n    retires:\n    durations: [5, 10]\n")
    res = preview(client, target="omni-1.2-flash", rounding="up")
    assert [b["id"] for b in res["bad_duration"]] == ["s03"]
    assert res["credits"] == 3 * (10 + 8 + 10) and res["credits_approx"] is True


# --- review #11: per-project lock -------------------------------------------------------

def test_writes_wait_for_project_lock(client):
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01"}])
    lock = client.app.state.sb_lock("film")
    assert lock is client.app.state.sb_lock("film")
    with lock, lock:  # re-entrant
        pass
    data = raw(p)
    data["title"] = "Đổi tên"
    done = threading.Event()

    def save():
        client.put("/api/projects/film", json={"storyboard": data})
        done.set()

    with lock:
        t = threading.Thread(target=save)
        t.start()
        time.sleep(0.3)
        assert not done.is_set() and raw(p)["title"] == "film"
    t.join(10)
    assert done.is_set() and raw(p)["title"] == "Đổi tên"


# --- after review: running job, pid checked before the lock -----------------------------

def test_apply_409_while_job_running_preview_still_allowed(client):
    p = four_groups(client.ws)
    four_groups(client.ws, "other")
    base = preview(client)["base"]
    before = p.read_bytes()
    jobs = client.app.state.jobs
    jobs.current = server.Job(id="j1", kind="video", project="film", title="Render video")  # status "running"
    r = client.post("/api/projects/film/model-change", json={"target": "veo-lite", "base": base})
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "job_running" and r.json()["detail"]["message"]
    assert p.read_bytes() == before
    assert preview(client)["base"] == base  # preview is still allowed
    assert apply(client, "other")["changed"] is True  # a job on another project does not block
    jobs.current.status = "done"
    assert apply(client)["changed"] is True


def test_model_change_checks_pid_before_lock(client):
    sb_lock = client.app.state.sb_lock
    locks = dict(zip(sb_lock.__code__.co_freevars, (c.cell_contents for c in sb_lock.__closure__)))["sb_locks"]
    for pid in ("BAD..pid", "missing"):
        for path in ("model-change/preview", "model-change"):
            r = client.post(f"/api/projects/{pid}/{path}", json={"target": "veo-lite", "base": "x"})
            assert r.status_code == 404, (pid, path)
    assert "BAD..pid" not in locks and "missing" not in locks
