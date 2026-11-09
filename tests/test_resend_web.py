"""resend-credit-guard, server side (AC12, AC13, AC15–AC25): fake gflow, TestClient, no Flow."""
import json
import os
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from filmmaker.pipeline import Pipeline  # noqa: E402
from filmmaker.web import server  # noqa: E402
from tests.model_helpers import set_scene, set_today, stamp, ui_out_dir, write_project  # noqa: E402
from tests.test_model_web import client, make_client, needs_ffmpeg, video_calls, wait  # noqa: E402,F401

SERVER = Path(server.__file__)


def sent_ids(c) -> list[str]:
    """Scene ids of the `video` commands the fake gflow received (from `-o .../clips/<id>.mp4`)."""
    return [Path(x[x.index("-o") + 1]).stem for x in video_calls(c)]


def est(c, **body):
    r = c.post("/api/projects/film/estimate", json={"action": "video", **body})
    assert r.status_code == 200, r.text
    return r.json()


def run(c, **body):
    return c.post("/api/projects/film/run", json={"action": "video", **body})


def fake_running_job(c, project: str = "film") -> None:
    c.app.state.jobs.current = server.Job(id="busy", kind="video", project=project, title="Render video")


def state_of(p, sid):
    return json.loads((ui_out_dir(p) / "state.json").read_text())["scenes"][sid]


# --- /estimate (AC15, AC16) -----------------------------------------------------------

def test_estimate_blocks_forced_interrupted(client):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}])
    set_scene(p, "s02", "interrupted", submitted_at=stamp(-60))
    e = est(client, only=["s02"], force=["s02"])
    assert e["blocked"] == ["s02"] and e["renders"] == []
    e = est(client, only=["s02"], force=["s02"], confirm_resend=["s02"])
    assert e["blocked"] == [] and [r["id"] for r in e["renders"]] == ["s02"]


def test_estimate_adopt_and_unverified_lists(client, fake_clip_check):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}, {"id": "s03"}, {"id": "s04"}])
    pipe = set_scene(p, "s01", "interrupted", manual_clip=True, submitted_at=stamp(-60))  # fresh clip: adopted
    set_scene(p, "s02", "interrupted", manual_clip=True)  # legacy interrupted scene with a clip
    set_scene(p, "s03", "interrupted", submitted_at=stamp(-60))  # no clip
    e = est(client)
    assert e["adopt"] == ["s01"]
    clip = pipe.clip_path(pipe.sb.scenes[0])
    assert e["adopt_lines"] == [f"[s01] dùng clip bạn đã tải về ({clip}) thay vì gửi lại; "
                                "xoá file này nếu muốn render lại"]
    assert e["blocked"] == ["s02", "s03"] and e["unverified_clip"] == ["s02"]
    assert [r["id"] for r in e["renders"]] == ["s04"]
    assert est(client, force=["s01"])["adopt"] == ["s01"]  # AC3: adopted even when forced


# --- /run: 428 needs_confirm (AC18, AC19, AC20) ---------------------------------------

def test_run_428_needs_confirm_no_job(make_client):
    c = make_client(auto_backup=True)
    p = write_project(c.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}])
    set_scene(p, "s02", "interrupted", submitted_at=stamp(-60))
    for action in ("video", "make"):
        r = run(c, action=action, only=["s02"], force=["s02"])
        assert r.status_code == 428, r.text
        d = r.json()["detail"]
        assert d["reason"] == "needs_confirm" and d["scenes"] == ["s02"]
        assert "s02" in d["message"] and "có thể đã trừ credit" in d["message"]
    assert c.get("/api/jobs/current").json() is None
    assert c.calls() == []


def test_retry_unknown_no_longer_unblocks(client):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s02"}])
    set_scene(p, "s02", "interrupted", submitted_at=stamp(-60))
    r = run(client, force=["s02"], retry_unknown=True)
    assert r.status_code == 428 and r.json()["detail"]["scenes"] == ["s02"]
    assert est(client, retry_unknown=True)["blocked"] == ["s02"]
    assert client.calls() == []


@needs_ffmpeg
def test_confirm_one_scene_does_not_unblock_other(client):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s02"}, {"id": "s03"}])
    set_scene(p, "s02", "interrupted", submitted_at=stamp(-60))
    set_scene(p, "s03", "interrupted", submitted_at=stamp(-60))
    r = run(client, force=["s02", "s03"], confirm_resend=["s02"])
    assert r.status_code == 428 and r.json()["detail"]["scenes"] == ["s03"]
    assert client.calls() == []
    r = run(client, confirm_resend=["s02"])
    assert r.status_code == 200, r.text
    j = wait(client, r.json())
    assert sent_ids(client) == ["s02"]
    assert j["result"]["done"] == ["s02"]
    msg = j["result"]["failed"]["s03"]
    assert "bị ngắt" in msg and "tick" in msg and "--retry-unknown" not in msg
    assert any("[s02] có thể đã bị trừ credit ở lần trước, đang gửi lại" in line for line in j["lines"])


@pytest.mark.parametrize("body", [{"confirm_resend": []},
                                  {"only": ["s02"], "force": ["s02"], "confirm_resend": []}])
def test_only_adopt_runs_without_confirm(client, fake_clip_check, body):
    """The UI calls /run with confirm_resend=[] when there is only a clip to adopt: no 428, nothing sent."""
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}])
    set_scene(p, "s01", "done")
    set_scene(p, "s02", "interrupted", manual_clip=True, submitted_at=stamp(-60))
    e = est(client, **body)
    assert e["renders"] == [] and e["blocked"] == [] and e["adopt"] == ["s02"]
    r = run(client, **body)
    assert r.status_code == 200, r.text
    wait(client, r.json())
    assert video_calls(client) == []
    st = state_of(p, "s02")
    assert st["status"] == "done" and st["rendered_as"] == "manual"


# --- the job checks again by itself (AC21) ---------------------------------------------

def test_web_job_rechecks_when_clip_vanishes(client, monkeypatch, fake_clip_check):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s02"}])
    pipe = set_scene(p, "s02", "interrupted", manual_clip=True, submitted_at=stamp(-60))
    clip = pipe.clip_path(pipe.sb.scenes[0])
    original = Pipeline._adopt_manual_clip

    def vanish_then_adopt(self, s, **kw):
        clip.unlink(missing_ok=True)
        return original(self, s, **kw)

    monkeypatch.setattr(Pipeline, "_adopt_manual_clip", vanish_then_adopt)
    r = run(client, force=["s02"])
    assert r.status_code == 200, r.text  # s02 is "adopt" when /run checks
    j = wait(client, r.json())
    assert video_calls(client) == []
    assert "bị ngắt" in j["result"]["failed"]["s02"]
    assert state_of(p, "s02")["status"] == "submitting"


# --- ids (AC22) -------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["only", "force", "confirm_resend"])
def test_unknown_ids_400_both_routes(client, field):
    write_project(client.ws, "film", "veo-lite", [{"id": "s01"}])
    for route in ("estimate", "run"):
        r = client.post(f"/api/projects/film/{route}", json={"action": "video", field: ["zz"]})
        assert r.status_code == 400 and "Không có cảnh: zz" in r.text, (route, r.text)
    assert client.calls() == []


@needs_ffmpeg
def test_confirm_outside_only_ignored(client, monkeypatch):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02", "model": "veo-fast"}])
    set_scene(p, "s02", "interrupted", submitted_at=stamp(-60))
    set_today(monkeypatch, 2026, 12, 6)  # veo-fast (s02) is retired
    body = {"only": ["s01"], "confirm_resend": ["s02"]}
    e = est(client, **body)
    assert [r["id"] for r in e["renders"]] == ["s01"] and e["retired"] == [] and e["blocked"] == []
    r = run(client, **body)
    assert r.status_code == 200, r.text
    wait(client, r.json())
    assert sent_ids(client) == ["s01"]


def test_confirm_of_scene_not_interrupted_ignored(client):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}])
    set_scene(p, "s01", "done")
    e = est(client, confirm_resend=["s01"])
    assert [r["id"] for r in e["renders"]] == ["s02"] and e["done"] == ["s01"]


# --- other actions (AC23) ---------------------------------------------------------------

@pytest.mark.parametrize("action", ["dry", "keyframes", "stitch", "thumbnail", "edit"])
def test_other_actions_never_428(client, action):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}])
    set_scene(p, "s01", "interrupted", submitted_at=stamp(-60))
    r = run(client, action=action, force=["s01"])
    assert r.status_code != 428, r.text
    if r.status_code == 200:
        wait(client, r.json())
    assert video_calls(client) == []


def test_dry_preview_blocks_unconfirmed_forced_interrupted(client):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}])
    set_scene(p, "s01", "interrupted", submitted_at=stamp(-60))
    r = run(client, action="dry", force=["s01"])
    assert r.status_code == 200, r.text
    j = wait(client, r.json())
    assert "bị ngắt" in j["result"]["failed"]["s01"]
    r = run(client, action="dry", force=["s01"], confirm_resend=["s01"])
    j = wait(client, r.json())
    assert "s01" not in j["result"]["failed"]
    assert client.calls() == [] or video_calls(client) == []


# --- order of the checks, retired (AC17, AC24) -----------------------------------------

def test_retired_forced_interrupted_409_before_428(client, monkeypatch):
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01"}])
    set_scene(p, "s01", "interrupted", submitted_at=stamp(-60))
    set_today(monkeypatch, 2026, 12, 6)
    assert [r["id"] for r in est(client, force=["s01"])["retired"]] == ["s01"]
    r = run(client, force=["s01"])
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["reason"] == "model_retired" and [x["id"] for x in d["retired"]] == ["s01"]
    assert est(client)["retired"] == []  # not forced, not confirmed: not sent, so not blocking
    assert client.calls() == []


def test_retired_list_in_storyboard_order(client, monkeypatch):
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01"}, {"id": "s02"}, {"id": "s03"}])
    set_scene(p, "s02", "interrupted", submitted_at=stamp(-60))
    set_today(monkeypatch, 2026, 12, 6)
    assert [r["id"] for r in est(client, force=["s02"])["retired"]] == ["s01", "s02", "s03"]
    assert [r["id"] for r in run(client, force=["s02"]).json()["detail"]["retired"]] == ["s01", "s02", "s03"]


def test_confirmed_body_while_job_running_409_job_running(client):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}])
    set_scene(p, "s01", "interrupted", submitted_at=stamp(-60))
    fake_running_job(client, project="other")
    assert run(client, force=["s01"]).status_code == 428  # 428 before job_running
    r = run(client, force=["s01"], confirm_resend=["s01"])
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "job_running"
    assert "Đang có việc chạy" in r.json()["detail"]["message"]
    assert client.calls() == []


# --- every 409 has a reason (AC25) ------------------------------------------------------

def test_no_string_409_in_server():
    text = SERVER.read_text(encoding="utf-8")
    assert not re.search(r"HTTPException\(409,\s*f?[\"']", text)


def test_409_reasons(client):
    write_project(client.ws, "film", "veo-fast", [{"id": "s01"}])
    r = client.post("/api/projects/film/model-change", json={"target": "veo-lite", "base": "x"})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "stale_base"
    assert r.json()["detail"]["message"] == "Storyboard đã thay đổi từ lúc xem trước. Hãy xem trước lại."
    (client.ws / "phim-cu").mkdir()
    r = client.post("/api/backups/restore", json={"name": "phim-cu"})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "project_exists"
    assert "Đã có dự án “phim-cu” trên máy" in r.json()["detail"]["message"]
    fake_running_job(client)
    r = client.delete("/api/projects/film")
    assert r.status_code == 409 and r.json()["detail"] == {"message": "Dự án đang chạy, hãy dừng trước.",
                                                           "reason": "job_running"}
    r = run(client, action="stitch")
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "job_running"


# --- GET detail: manual_clip (AC13) -----------------------------------------------------

def test_detail_manual_clip_values(client, fake_clip_check):
    ids = [f"s0{i}" for i in range(1, 9)]
    p = write_project(client.ws, "film", "veo-lite", [{"id": i} for i in ids])
    set_scene(p, "s01", "done", manual_clip=True)  # not submitting -> None
    set_scene(p, "s02", "interrupted", submitted_at=stamp(-60))  # no clip -> None
    set_scene(p, "s03", "interrupted", manual_clip=True, submitted_at=stamp(-60))  # ok
    set_scene(p, "s04", "interrupted", manual_clip=True)  # legacy -> unverified
    pipe = set_scene(p, "s05", "interrupted", manual_clip=True, submitted_at=stamp(-60))
    set_scene(p, "s06", "interrupted", manual_clip=True, submitted_at=stamp(-60), clip_bytes=b"BROKEN" * 4000)
    set_scene(p, "s07", "interrupted", manual_clip=True, clip_bytes=b"BROKEN" * 4000)  # legacy + broken
    pipe7 = set_scene(p, "s08", "interrupted", manual_clip=True, submitted_at=stamp(-60),
                      clip_bytes=b"BROKEN" * 4000)
    for sid, pp in (("s05", pipe), ("s08", pipe7)):
        clip = pp.clip_path(next(s for s in pp.sb.scenes if s.id == sid))
        t = clip.stat().st_mtime - 120  # older than submitted_at
        os.utime(clip, (t, t))
    ss = client.get("/api/projects/film").json()["scene_state"]
    assert {i: ss[i]["manual_clip"] for i in ids} == {
        "s01": None, "s02": None, "s03": "ok", "s04": "unverified", "s05": "stale",
        "s06": "invalid", "s07": "invalid", "s08": "invalid"}


def test_detail_manual_clip_unchecked_without_ffprobe(client, no_ffprobe):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}])
    set_scene(p, "s01", "interrupted", manual_clip=True, submitted_at=stamp(-60))
    set_scene(p, "s02", "interrupted", manual_clip=True)
    ss = client.get("/api/projects/film").json()["scene_state"]
    assert ss["s01"]["manual_clip"] == "unchecked" and ss["s02"]["manual_clip"] == "unchecked"


def test_detail_does_not_probe_while_job_runs_for_project(client, fake_clip_check, monkeypatch):
    """A job of this project may be writing clips/<id>.mp4: GET must not ffprobe it on every poll."""
    from filmmaker import manual_clip
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}])
    set_scene(p, "s01", "interrupted", manual_clip=True, submitted_at=stamp(-60))
    probed: list[Path] = []
    monkeypatch.setattr(manual_clip, "probe_clip", lambda path: probed.append(path) or True)
    fake_running_job(client)
    assert client.get("/api/projects/film").json()["scene_state"]["s01"]["manual_clip"] is None
    assert probed == []
    fake_running_job(client, project="other")  # another project's job: probed as usual
    assert client.get("/api/projects/film").json()["scene_state"]["s01"]["manual_clip"] == "ok"
    assert len(probed) == 1


# --- POST /adopt-clip (AC12) ------------------------------------------------------------

def adopt(c, sid, pid="film"):
    return c.post(f"/api/projects/{pid}/scenes/{sid}/adopt-clip")


def test_adopt_clip_route_ok(client, fake_clip_check):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}])
    pipe = set_scene(p, "s02", "interrupted", manual_clip=True)
    r = adopt(client, "s02")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["scene_state"]["s02"]["status"] == "done" and d["scene_state"]["s02"]["manual_clip"] is None
    st = state_of(p, "s02")
    assert st["status"] == "done" and st["rendered_as"] == "manual" and st.get("submitted_at") is None
    assert st["video"] == str(pipe.clip_path(pipe.sb.scenes[1]))
    assert client.calls() == []
    assert client.get("/api/jobs/current").json() is None


def test_adopt_clip_route_bad_stamp_is_legacy(client, fake_clip_check):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}])
    set_scene(p, "s01", "interrupted", manual_clip=True, submitted_at="garbage")
    assert client.get("/api/projects/film").json()["scene_state"]["s01"]["manual_clip"] == "unverified"
    assert adopt(client, "s01").status_code == 200


def test_adopt_clip_route_not_adoptable(client, fake_clip_check):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}])
    set_scene(p, "s01", "done", manual_clip=True)
    set_scene(p, "s02", "interrupted", manual_clip=True, submitted_at=stamp(-60))  # has a stamp: auto-adopted
    for sid in ("s01", "s02"):
        r = adopt(client, sid)
        assert r.status_code == 409 and r.json()["detail"]["reason"] == "not_adoptable", r.text
    assert state_of(p, "s02")["status"] == "submitting"


def test_adopt_clip_route_clip_invalid(client, fake_clip_check):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}])
    set_scene(p, "s01", "interrupted")  # legacy, no clip
    set_scene(p, "s02", "interrupted", manual_clip=True, clip_bytes=b"BROKEN" * 4000)
    for sid in ("s01", "s02"):
        r = adopt(client, sid)
        assert r.status_code == 409 and r.json()["detail"]["reason"] == "clip_invalid", r.text
        assert state_of(p, sid)["status"] == "submitting"


def test_adopt_clip_route_without_ffprobe(client, no_ffprobe):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}])
    set_scene(p, "s01", "interrupted", manual_clip=True)
    r = adopt(client, "s01")
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "clip_invalid" and "Chưa cài ffprobe" in r.json()["detail"]["message"]


def test_adopt_clip_route_job_running_and_404(client, fake_clip_check):
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}])
    set_scene(p, "s01", "interrupted", manual_clip=True)
    assert adopt(client, "zz").status_code == 404
    assert adopt(client, "s01", pid="nope").status_code == 404
    fake_running_job(client)
    r = adopt(client, "s01")
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "job_running"
    assert state_of(p, "s01")["status"] == "submitting"
    fake_running_job(client, project="other")  # a job of another project does not touch this state
    assert adopt(client, "s01").status_code == 200


def test_adopt_clip_route_probes_outside_jobs_lock(client, fake_clip_check, monkeypatch):
    """ffprobe (up to PROBE_TIMEOUT) runs before jobs.lock is taken; inside it the verdict comes from the cache."""
    from filmmaker import manual_clip
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}])
    set_scene(p, "s01", "interrupted", manual_clip=True)
    lock = client.app.state.jobs.lock
    held: list[bool] = []
    monkeypatch.setattr(manual_clip, "probe_clip", lambda path: held.append(lock.locked()) or True)
    assert adopt(client, "s01").status_code == 200
    assert held == [False]
    assert state_of(p, "s01")["status"] == "done"


# --- QA (T5): gaps found when mapping AC1–AC36 to tests ------------------------------------

def test_web_job_logs_adopt_line(client, fake_clip_check):
    """AC4 for the web job: the job log has the exact adopt line."""
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}])
    pipe = set_scene(p, "s01", "interrupted", manual_clip=True, submitted_at=stamp(-60))
    r = run(client, force=["s01"])
    assert r.status_code == 200, r.text
    j = wait(client, r.json())
    line = f"[s01] dùng clip bạn đã tải về ({pipe.clip_path(pipe.sb.scenes[0])}) thay vì gửi lại; " \
           "xoá file này nếu muốn render lại"
    assert any(line in l for l in j["lines"]), j["lines"]
    assert video_calls(client) == []


@needs_ffmpeg
def test_web_job_no_ffprobe_logs_once(client, no_ffprobe):
    """AC8 on the web: one line in the job log; nothing adopted; /estimate and GET do not start a job."""
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}, {"id": "s03"}])
    set_scene(p, "s01", "interrupted", manual_clip=True, submitted_at=stamp(-60))
    set_scene(p, "s02", "interrupted", manual_clip=True, submitted_at=stamp(-60))
    e = est(client)
    assert e["adopt"] == [] and e["blocked"] == ["s01", "s02"]
    client.get("/api/projects/film")
    assert client.get("/api/jobs/current").json() is None
    r = run(client, confirm_resend=[])
    assert r.status_code == 200, r.text
    j = wait(client, r.json())
    assert sum("không có ffprobe nên không kiểm được clip tải tay" in l for l in j["lines"]) == 1
    assert sent_ids(client) == ["s03"]
    assert state_of(p, "s01")["status"] == "submitting" and state_of(p, "s02")["status"] == "submitting"


@needs_ffmpeg
def test_web_run_never_adopts_legacy_clip(client, fake_clip_check):
    """AC10 on the web: a legacy interrupted scene with a valid clip is blocked, not adopted, not sent."""
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}])
    set_scene(p, "s01", "interrupted", manual_clip=True)
    r = run(client, retry_unknown=True)
    assert r.status_code == 200, r.text
    j = wait(client, r.json())
    assert sent_ids(client) == ["s02"]
    msg = j["result"]["failed"]["s01"]
    assert "bị ngắt" in msg and "Dùng clip này" in msg
    assert state_of(p, "s01")["status"] == "submitting"


def test_400_checked_before_409_model_retired(client, monkeypatch):
    """AC24: an unknown id is a 400 even when the body would also be a 409 model_retired / 428."""
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01"}])
    set_scene(p, "s01", "interrupted", submitted_at=stamp(-60))
    set_today(monkeypatch, 2026, 12, 6)
    r = run(client, force=["s01", "zz"])
    assert r.status_code == 400 and "Không có cảnh: zz" in r.text
    r = run(client, action="nope", force=["s01"])
    assert r.status_code == 400
    assert client.calls() == []


def test_detail_has_updated_at_for_skip_button(client, fake_clip_check):
    """AC35 relies on scene_state[id].updated_at (the [Bỏ qua] key) next to manual_clip."""
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}])
    set_scene(p, "s01", "interrupted", manual_clip=True)
    st = client.get("/api/projects/film").json()["scene_state"]["s01"]
    assert st["manual_clip"] == "unverified" and st["updated_at"]
