"""flow-render-fixes, server side of bad durations (AC7, AC8, AC12, AC14–AC16, AC20): fake gflow, TestClient, no Flow.

Every client points server.SETTINGS_PATH at a temp settings.json with auto_backup false (`make_client`), and every
job a test starts is waited for before the test ends.
"""
import json
import threading
import time

import pytest
import yaml

pytest.importorskip("fastapi")

from filmmaker import durations  # noqa: E402
from filmmaker.web import server  # noqa: E402
from tests.model_helpers import set_scene, set_today, stamp, ui_out_dir, write_override, write_project  # noqa: E402
from tests.test_model_web import make_client, video_calls, wait  # noqa: E402,F401
from tests.test_resend_web import fake_running_job  # noqa: E402


@pytest.fixture
def c(make_client):
    return make_client(auto_backup=False)


def est(c, **body):
    r = c.post("/api/projects/film/estimate", json={"action": "video", **body})
    assert r.status_code == 200, r.text
    return r.json()


def ids(rows):
    return [r["id"] for r in rows]


def ac16_project(c):
    """s01 5 s todo (roundable), s02 7 s interrupted (blocked); all on veo-lite (4, 6, 8)."""
    p = write_project(c.ws, "film", "veo-lite", [{"id": "s01", "duration": 5}, {"id": "s02", "duration": 7}])
    set_scene(p, "s02", "interrupted", submitted_at=stamp(-60))
    return p


def ac12_project(c):
    """s01 5, s02 7 todo (roundable); s03 6 valid; s04 9 interrupted (skipped); s05 5 done (neither); s06 no duration."""
    p = write_project(c.ws, "film", "veo-lite", [
        {"id": "s01", "duration": 5}, {"id": "s02", "duration": 7}, {"id": "s03", "duration": 6},
        {"id": "s04", "duration": 9}, {"id": "s05", "duration": 5}, {"id": "s06"}])
    set_scene(p, "s04", "interrupted", submitted_at=stamp(-60))
    set_scene(p, "s05", "done")
    return p


def preview(c, pid="film"):
    r = c.post(f"/api/projects/{pid}/durations/preview")
    assert r.status_code == 200, r.text
    return r.json()


def round_(c, direction="up", base=None, pid="film"):
    return c.post(f"/api/projects/{pid}/durations/round", json={"direction": direction, "base": base})


# --- detail(): scene_state[id].bad_duration (AC20) -------------------------------------

def test_detail_bad_duration_flag(c):
    p = write_project(c.ws, "film", "veo-lite", [
        {"id": "s01", "duration": 5}, {"id": "s02", "duration": 5}, {"id": "s03", "duration": 6},
        {"id": "s04"}, {"id": "s05", "duration": 7}, {"id": "s06", "duration": 9},
        {"id": "s07", "duration": 5, "model": "veo-fast"}, {"id": "s08", "duration": 7}])
    set_scene(p, "s02", "done")
    set_scene(p, "s05", "interrupted", submitted_at=stamp(-60))
    set_scene(p, "s06", "changed")
    set_scene(p, "s08", "failed")
    st = c.get("/api/projects/film").json()["scene_state"]
    assert st["s01"]["bad_duration"] == {"duration": 5, "model": "veo-lite", "allowed": [4, 6, 8]}
    assert st["s02"]["bad_duration"] is None  # done: the clip exists
    assert st["s03"]["bad_duration"] is None and st["s04"]["bad_duration"] is None
    assert st["s05"]["bad_duration"]["duration"] == 7  # interrupted is flagged too
    assert st["s06"]["bad_duration"]["duration"] == 9  # changed (old fingerprint) too
    assert st["s07"]["bad_duration"] is None  # veo-fast declares no durations
    assert st["s08"]["bad_duration"]["duration"] == 7


# --- /estimate (AC7) -------------------------------------------------------------------

def test_estimate_bad_duration_keys(c):
    ac16_project(c)
    e = est(c)
    assert ids(e["bad_duration"]) == ["s01"] and ids(e["blocked_bad_duration"]) == ["s02"]
    assert e["bad_duration"][0] == {"id": "s01", "model": "veo-lite", "duration": 5, "allowed": [4, 6, 8],
                                    "up": 6, "down": 4, "roundable": True, "reason": None}
    assert e["blocked_bad_duration"][0]["roundable"] is False
    assert e["blocked_bad_duration"][0]["reason"] == "interrupted"
    e = est(c, only=["s02"], force=["s02"])
    assert e["bad_duration"] == [] and ids(e["blocked_bad_duration"]) == ["s02"]
    e = est(c, only=["s02"], force=["s02"], confirm_resend=["s02"])
    assert ids(e["bad_duration"]) == ["s02"] and e["bad_duration"][0]["roundable"] is False
    assert e["blocked_bad_duration"] == []


def test_estimate_no_bad_duration(c):
    write_project(c.ws, "film", "veo-lite", [{"id": "s01", "duration": 6}, {"id": "s02"}])
    e = est(c)
    assert e["bad_duration"] == [] and e["blocked_bad_duration"] == []


# --- /run (AC8) ------------------------------------------------------------------------

def test_run_409_order(c, monkeypatch):
    p = write_project(c.ws, "film", "veo-lite", [
        {"id": "s01", "duration": 5}, {"id": "s02", "duration": 7}, {"id": "s03", "model": "veo-fast"}])
    set_scene(p, "s02", "interrupted", submitted_at=stamp(-60))
    state = ui_out_dir(p) / "state.json"
    before = state.read_bytes()
    for action in ("video", "make"):
        # bad duration before 428: s02 forced but not ticked, s01 has a bad duration
        r = c.post("/api/projects/film/run", json={"action": action, "force": ["s02"]})
        assert r.status_code == 409, r.text
        d = r.json()["detail"]
        assert d["reason"] == "bad_duration" and ids(d["bad_duration"]) == ["s01"]
        assert d["message"] == "Không thể render: 1 cảnh có độ dài model không nhận (s01)"
        # ticked interrupted scene with a bad duration: refused too, not roundable
        r = c.post("/api/projects/film/run", json={"action": action, "only": ["s02"], "confirm_resend": ["s02"]})
        assert r.status_code == 409, r.text
        d = r.json()["detail"]
        assert d["reason"] == "bad_duration" and d["bad_duration"][0]["id"] == "s02"
        assert d["bad_duration"][0]["roundable"] is False
    # a retired model wins over the duration
    set_today(monkeypatch, 2026, 12, 6)
    r = c.post("/api/projects/film/run", json={"action": "video"})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "model_retired"
    assert c.get("/api/jobs/current").json() is None
    assert c.calls() == [] and state.read_bytes() == before
    assert not (ui_out_dir(p) / "clips").exists()


def test_run_other_actions_not_blocked(c):
    write_project(c.ws, "film", "veo-lite", [{"id": "s01", "duration": 5}])
    for action in ("dry", "keyframes"):
        r = c.post("/api/projects/film/run", json={"action": action})
        assert r.status_code == 200, (action, r.text)
        wait(c, r.json())
    assert video_calls(c) == []


def test_do_run_refuses_bad_duration(c, monkeypatch):
    """The job checks every scene again (a storyboard changed after /run): nothing is sent for it."""
    p = write_project(c.ws, "film", "veo-lite", [{"id": "s01", "duration": 5}])
    monkeypatch.setattr(durations, "scan", lambda *a, **k: [])  # let /run through
    r = c.post("/api/projects/film/run", json={"action": "video"})
    assert r.status_code == 200, r.text
    j = wait(c, r.json())
    assert any("độ dài 5 giây không hợp lệ với veo-lite" in line for line in j["lines"]), j["lines"]
    assert video_calls(c) == []
    sc = json.loads((ui_out_dir(p) / "state.json").read_text()).get("scenes", {}).get("s01", {})
    assert sc.get("status") != "submitting"


# --- durations/preview + durations/round (AC12, AC14, AC15) ----------------------------

def test_preview_round_fixture_ac12(c):
    p = ac12_project(c)
    state = ui_out_dir(p) / "state.json"
    before_state = state.read_bytes()
    raw_before = yaml.safe_load(p.read_text())
    pv = preview(c)
    assert [(r["id"], r["duration"], r["up"], r["down"]) for r in pv["scenes"]] == [("s01", 5, 6, 4), ("s02", 7, 8, 6)]
    assert pv["skipped"] == ["s04"] and pv["base"]
    assert p.read_text() and yaml.safe_load(p.read_text()) == raw_before  # preview only reads
    r = round_(c, "up", pv["base"])
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["changed"] is True
    assert res["scenes"] == [{"id": "s01", "from": 5, "to": 6}, {"id": "s02", "from": 7, "to": 8}]
    assert res["skipped"] == ["s04"]
    assert res["project"]["scene_state"]["s01"]["bad_duration"] is None
    raw_after = yaml.safe_load(p.read_text())
    diffs = [(i, k) for i, (a, b) in enumerate(zip(raw_before["scenes"], raw_after["scenes"]))
             for k in set(a) | set(b) if a.get(k) != b.get(k)]
    assert diffs == [(0, "duration"), (1, "duration")]
    assert {k: v for k, v in raw_after.items() if k != "scenes"} == {k: v for k, v in raw_before.items() if k != "scenes"}
    assert raw_after["scenes"][3]["duration"] == 9 and raw_after["scenes"][4]["duration"] == 5
    assert state.read_bytes() == before_state
    assert c.calls() == []


def test_round_down(c):
    p = ac12_project(c)
    r = round_(c, "down", preview(c)["base"])
    assert r.status_code == 200, r.text
    assert [s["duration"] for s in yaml.safe_load(p.read_text())["scenes"][:2]] == [4, 6]


def test_round_stale_base(c):
    p = ac12_project(c)
    base = preview(c)["base"]
    raw = yaml.safe_load(p.read_text())
    raw["title"] = "đổi ngoài editor"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    edited = p.read_bytes()
    r = round_(c, "up", base)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "stale_base"
    assert p.read_bytes() == edited


def test_round_stale_when_group_changes(c):
    p = ac12_project(c)
    base = preview(c)["base"]
    set_scene(p, "s01", "done")  # rendered between preview and round
    before = p.read_bytes()
    r = round_(c, "up", base)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "stale_base"
    assert p.read_bytes() == before


def test_round_stale_when_models_yaml_changes(c):
    p = ac12_project(c)
    base = preview(c)["base"]
    write_override(c.override, "models:\n  veo-lite:\n    durations: [4, 8]\n")
    before = p.read_bytes()
    r = round_(c, "up", base)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "stale_base"
    assert p.read_bytes() == before
    assert ids(preview(c)["scenes"]) == ["s01", "s02", "s03"]  # 6 is no longer allowed


def test_round_400_direction_and_base(c):
    p = ac12_project(c)
    base = preview(c)["base"]
    before = p.read_bytes()
    for body in ({"base": base}, {"direction": "sideways", "base": base}, {"direction": "up"},
                 {"direction": "up", "base": ""}):
        r = c.post("/api/projects/film/durations/round", json=body)
        assert r.status_code == 400, (body, r.text)
    assert p.read_bytes() == before


def test_round_job_running(c):
    p = ac12_project(c)
    base = preview(c)["base"]
    fake_running_job(c)
    before = p.read_bytes()
    r = round_(c, "up", base)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "job_running"
    assert p.read_bytes() == before
    c.app.state.jobs.current = None


def test_round_nothing(c):
    p = write_project(c.ws, "film", "veo-lite", [{"id": "s01", "duration": 6}, {"id": "s02", "duration": 7}])
    set_scene(p, "s02", "interrupted", submitted_at=stamp(-60))
    pv = preview(c)
    assert pv["scenes"] == [] and pv["skipped"] == ["s02"]
    before, mtime = p.read_bytes(), p.stat().st_mtime_ns
    r = round_(c, "up", pv["base"])
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["changed"] is False and res["scenes"] == [] and res["skipped"] == ["s02"] and res["project"]
    assert p.read_bytes() == before and p.stat().st_mtime_ns == mtime


def test_round_holds_jobs_lock(c):
    p = ac12_project(c)
    base = preview(c)["base"]
    out = {}
    jobs = c.app.state.jobs
    jobs.lock.acquire()
    try:
        t = threading.Thread(target=lambda: out.setdefault("r", round_(c, "up", base)))
        t.start()
        time.sleep(0.4)
        assert "r" not in out and yaml.safe_load(p.read_text())["scenes"][0]["duration"] == 5
    finally:
        jobs.lock.release()
    t.join(30)
    assert out["r"].status_code == 200, out["r"].text
    assert yaml.safe_load(p.read_text())["scenes"][0]["duration"] == 6


def _sb_locks(c) -> dict:
    cells = [x.cell_contents for x in c.app.state.sb_lock.__closure__]
    return next(x for x in cells if isinstance(x, dict))


def test_durations_unknown_pid_404(c):
    ac12_project(c)
    for pid in ("nope", "Bad_ID"):
        assert c.post(f"/api/projects/{pid}/durations/preview").status_code == 404
        assert round_(c, "up", "x", pid=pid).status_code == 404
        assert round_(c, "sideways", None, pid=pid).status_code == 404  # 404 before 400
    assert set(_sb_locks(c)) <= {"film"}


def test_preview_broken_storyboard_400(c):
    p = write_project(c.ws, "film", "veo-lite", [{"id": "s01", "duration": 5}])
    p.write_text("title: x\nscenes:\n  - {id: s01, mode: t2v}\n")  # no prompt
    assert c.post("/api/projects/film/durations/preview").status_code == 400
    assert round_(c, "up", "x").status_code == 400


def test_preview_yaml_syntax_error_400(c):
    """security 1: a YAML syntax error is a 400 with a message, not a 500."""
    p = write_project(c.ws, "film", "veo-lite", [{"id": "s01", "duration": 5}])
    p.write_text("title: x\nscenes: [\n  - {id: s01\n", encoding="utf-8")
    for r in (c.post("/api/projects/film/durations/preview"), round_(c, "up", "x")):
        assert r.status_code == 400, r.text
        assert r.json()["detail"].startswith("Storyboard còn lỗi: ")


# --- AC16: the whole flow on the server ---------------------------------------------------

def test_ac16_flow(c):
    p = ac16_project(c)
    state = ui_out_dir(p) / "state.json"
    before_state = state.read_bytes()
    e = est(c)
    assert ids(e["bad_duration"]) == ["s01"] and ids(e["blocked_bad_duration"]) == ["s02"]
    pv = preview(c)
    assert ids(pv["scenes"]) == ["s01"] and pv["skipped"] == ["s02"]
    r = round_(c, "up", pv["base"])
    assert r.status_code == 200 and r.json()["scenes"] == [{"id": "s01", "from": 5, "to": 6}]
    e = est(c)
    assert e["bad_duration"] == [] and ids(e["blocked_bad_duration"]) == ["s02"]
    raw = yaml.safe_load(p.read_text())
    assert raw["scenes"][1]["duration"] == 7
    assert state.read_bytes() == before_state
    assert c.get("/api/projects/film").json()["scene_state"]["s02"]["bad_duration"]["duration"] == 7
    raw["scenes"][1]["duration"] = 8
    r = c.put("/api/projects/film", json={"storyboard": raw})
    assert r.status_code == 200, r.text
    assert r.json()["scene_state"]["s02"]["bad_duration"] is None
    assert est(c)["blocked_bad_duration"] == []
    assert c.calls() == []


# --- settings safety net (AC32, design "Kế hoạch test" (a)/(b)) ----------------------------

def test_settings_never_the_real_file(tmp_path):
    """conftest points FILMMAKER_SETTINGS at a temp file before filmmaker is imported, and moves
    server.SETTINGS_PATH onto this test's tmp_path/settings.json (same file as the env)."""
    import os
    from pathlib import Path

    from filmmaker import settings as user_settings
    from tests import conftest

    real = Path.home() / ".config" / "filmmaker" / "settings.json"
    assert server.SETTINGS_PATH == tmp_path / "settings.json"
    assert Path(os.environ["FILMMAKER_SETTINGS"]) == server.SETTINGS_PATH == user_settings.settings_path()
    assert server.SETTINGS_PATH != real and conftest._SAFE_SETTINGS != real
    assert json.loads(conftest._SAFE_SETTINGS.read_text())["auto_backup"] is False
    assert server.load_settings()["auto_backup"] is False


# --- AC12: exact fixture of the spec, Render on one scene still rounds the whole film -------

def ac12_spec_project(c):
    """s01 5, s02 7, s03 6 todo; s04 5 done; s05 7 interrupted (spec AC12)."""
    p = write_project(c.ws, "film", "veo-lite", [
        {"id": "s01", "duration": 5}, {"id": "s02", "duration": 7}, {"id": "s03", "duration": 6},
        {"id": "s04", "duration": 5}, {"id": "s05", "duration": 7}])
    set_scene(p, "s04", "done")
    set_scene(p, "s05", "interrupted", submitted_at=stamp(-60))
    return p


def test_ac12_render_one_scene_rounds_table_rows(c):
    p = ac12_spec_project(c)
    state = ui_out_dir(p) / "state.json"
    before_state = state.read_bytes()
    raw_before = yaml.safe_load(p.read_text())
    e = est(c, only=["s01"])
    assert ids(e["bad_duration"]) == ["s01"] and e["blocked_bad_duration"] == []
    pv = preview(c)
    table = ids(pv["scenes"])
    assert table == ["s01", "s02"] and pv["skipped"] == ["s05"]
    r = round_(c, "up", pv["base"])
    assert r.status_code == 200, r.text
    raw_after = yaml.safe_load(p.read_text())
    changed = [b["id"] for a, b in zip(raw_before["scenes"], raw_after["scenes"]) if a != b]
    assert changed == table  # the set of changed scenes is exactly the rows of the table
    assert [s["duration"] for s in raw_after["scenes"]] == [6, 8, 6, 5, 7]
    assert state.read_bytes() == before_state  # done / interrupted state untouched (AC15)
    assert est(c, only=["s01"])["bad_duration"] == []
    assert c.calls() == []


def test_ac12_stale_base_when_edited_outside_editor(c):
    p = ac12_spec_project(c)
    base = preview(c)["base"]
    raw = yaml.safe_load(p.read_text())
    raw["scenes"][2]["prompt"] = "sửa ở trình soạn khác"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    edited = p.read_bytes()
    r = round_(c, "up", base)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "stale_base"
    assert p.read_bytes() == edited
    pv = preview(c)  # the UI asks again: new base, same table
    assert pv["base"] != base and ids(pv["scenes"]) == ["s01", "s02"]


# --- AC10 / AC16: "Render lại" on a done scene with a bad duration --------------------------

def test_rerender_done_bad_duration_is_not_roundable(c):
    p = ac12_spec_project(c)
    e = est(c, only=["s04"], force=["s04"])
    assert ids(e["bad_duration"]) == ["s04"]
    row = e["bad_duration"][0]
    assert row["roundable"] is False and row["reason"] == "done" and row["allowed"] == [4, 6, 8]
    r = c.post("/api/projects/film/run", json={"action": "video", "only": ["s04"], "force": ["s04"]})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "bad_duration"
    assert r.json()["detail"]["bad_duration"][0]["reason"] == "done"
    # a done scene is never in the preview table nor in skipped, so rounding never touches it
    pv = preview(c)
    assert "s04" not in ids(pv["scenes"]) and "s04" not in pv["skipped"]
    round_(c, "down", pv["base"])
    assert yaml.safe_load(p.read_text())["scenes"][3]["duration"] == 5
    assert c.get("/api/jobs/current").json() is None and c.calls() == []


# --- AC13 on the server: after rounding s01, the interrupted s02 is only a warning -----------

def test_ac13_estimate_after_rounding(c):
    ac16_project(c)
    round_(c, "up", preview(c)["base"])
    e = est(c)
    assert ids(e["renders"]) == ["s01"] and e["blocked"] == ["s02"]
    assert e["bad_duration"] == [] and ids(e["blocked_bad_duration"]) == ["s02"]
    b = e["blocked_bad_duration"][0]
    assert (b["duration"], b["model"], b["allowed"], b["roundable"]) == (7, "veo-lite", [4, 6, 8], False)
    # "Gửi lại" on s02: nothing to render, the warning stays, no bad_duration box
    e = est(c, only=["s02"], force=["s02"])
    assert e["renders"] == [] and e["bad_duration"] == [] and ids(e["blocked_bad_duration"]) == ["s02"]
    r = c.post("/api/projects/film/run", json={"action": "video", "only": ["s02"], "force": ["s02"]})
    assert r.status_code == 428 and r.json()["detail"]["reason"] == "needs_confirm"
    assert c.get("/api/jobs/current").json() is None and c.calls() == []


# --- AC8: no backup on 409, other actions not blocked -----------------------------------------

@pytest.fixture
def fake_rclone(tmp_path, monkeypatch):
    """RCLONE_BIN: a script that only logs its argv (never the real rclone, never Google Drive)."""
    import sys

    log = tmp_path / "rclone.log"
    script = tmp_path / "fake_rclone.py"
    script.write_text("import json, os, sys\n"
                      f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                      "print('rclone v1.0-fake')\n")
    wrapper = tmp_path / "rclone"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n")
    wrapper.chmod(0o755)
    monkeypatch.setenv("RCLONE_BIN", str(wrapper))
    return log


def test_run_409_bad_duration_no_backup(make_client, fake_rclone, tmp_path):
    c = make_client(auto_backup=True, backup_remote=str(tmp_path / "remote"))
    ac16_project(c)
    for body in ({"action": "video"}, {"action": "make"}, {"action": "video", "confirm_resend": ["s02"]}):
        r = c.post("/api/projects/film/run", json=body)
        assert r.status_code == 409 and r.json()["detail"]["reason"] == "bad_duration", (body, r.text)
    assert c.get("/api/jobs/current").json() is None
    assert c.calls() == [] and not fake_rclone.exists()


def test_run_stitch_not_blocked(c):
    write_project(c.ws, "film", "veo-lite", [{"id": "s01", "duration": 5}])
    r = c.post("/api/projects/film/run", json={"action": "stitch"})
    assert r.status_code == 200, r.text  # may fail inside the job (no clips), but is never refused for the duration
    j = wait(c, r.json())
    assert not any("độ dài" in line and "không hợp lệ" in line for line in j["lines"])
    assert video_calls(c) == []


# --- AC20: the flag follows the metadata (models.yaml override), not gflow's model list ---------

def test_detail_flag_follows_metadata_not_gflow_list(c, tmp_path, monkeypatch):
    write_project(c.ws, "film", "veo-lite", [{"id": "s01", "duration": 5}, {"id": "s02", "duration": 7}])
    f = tmp_path / "models.json"  # gflow lists no veo-lite at all
    f.write_text(json.dumps({"video": {"models": [{"aliases": ["veo-fast"], "max_duration": 8}]},
                             "image": {"models": []}}))
    monkeypatch.setenv("FAKE_GFLOW_MODELS", str(f))
    st = c.get("/api/projects/film").json()["scene_state"]
    assert st["s01"]["bad_duration"]["allowed"] == [4, 6, 8] and st["s02"]["bad_duration"]["duration"] == 7
    write_override(c.override, "models:\n  veo-lite:\n    durations: [4, 5, 6, 8]\n")
    st = c.get("/api/projects/film").json()["scene_state"]
    assert st["s01"]["bad_duration"] is None
    assert st["s02"]["bad_duration"]["allowed"] == [4, 5, 6, 8]
    assert est(c)["bad_duration"][0]["id"] == "s02"
