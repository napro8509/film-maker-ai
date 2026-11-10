"""flow-render-fixes AC26/AC27: a Flow chat render that timed out (9) or failed (42) after approving is never
sent again by itself, and a clip downloaded by hand afterwards is adopted. Fake gflow + fake flow_ui only:
no Flow, no browser, no credits. The browser-profile lookup of Gflow is patched out (no `gflow auth status`)."""
import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from filmmaker import manual_clip
from filmmaker.cli import main
from filmmaker.gflow import Gflow
from filmmaker.pipeline import SUBMITTING
from tests.model_helpers import ui_out_dir, write_project

FAKE = Path(__file__).parent / "fake_gflow.py"
FAKE_UI = Path(__file__).parent / "fake_flow_ui.py"
CLIP = b"\0" * 20_000  # a valid hand-downloaded clip with fake_clip_check


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A web-layout project <ws>/film (one i2v scene s01, veo-lite 6 s) whose state already says gflow can't do
    image-to-video, so the render goes straight to the (fake) Flow chat driver."""
    ws = tmp_path / "ws"
    p = write_project(ws, "film", "veo-lite", [{"id": "s01", "mode": "i2v", "image": "start.png", "duration": 6}])
    (p.parent / "start.png").write_bytes(b"png")
    out = ui_out_dir(p)
    out.mkdir(parents=True)
    (out / "state.json").write_text(json.dumps({"i2v_unsupported": True, "scenes": {}}))
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "calls.log"))
    for k in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_HINT", "FAKE_FLOW_UI_PROGRESS", "FAKE_FLOW_UI_MEDIA"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FLOW_UI_CMD", f"{sys.executable} {FAKE_UI}")
    monkeypatch.setenv("FAKE_FLOW_UI_LOG", str(tmp_path / "ui.log"))
    monkeypatch.setattr(Gflow, "ui_profile_dir", lambda self: tmp_path / "profile")

    class E:
        path = p
        workspace = ws
        state = out / "state.json"
        clip = out / "clips" / "s01.mp4"

        @staticmethod
        def ui_calls() -> list[list[str]]:
            log = tmp_path / "ui.log"
            return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

        @staticmethod
        def i2v_calls() -> list[list[str]]:
            return [a for a in E.ui_calls() if a[:1] == ["i2v"]]

        @staticmethod
        def scene() -> dict:
            return json.loads(E.state.read_text())["scenes"]["s01"]

    return E


def cli_run(env, *args):
    return CliRunner().invoke(main, ["run", str(env.path), "--pause", "0", "--no-backup", *args])


def first_run(env, monkeypatch, fail):
    monkeypatch.setenv("FAKE_FLOW_UI_FAIL", fail)
    cli_run(env)
    monkeypatch.delenv("FAKE_FLOW_UI_FAIL")  # the next call would succeed: only the guard may stop it
    st = env.scene()
    assert st["status"] == SUBMITTING and manual_clip.parse_stamp(st.get("submitted_at")) is not None, st
    assert len(env.i2v_calls()) == 1
    return st


@pytest.mark.parametrize("fail", ["9:true", "42:true"])
def test_no_auto_resend_cli(env, monkeypatch, fail):
    st1 = first_run(env, monkeypatch, fail)
    argv = env.i2v_calls()[0]
    assert argv[argv.index("--duration") + 1] == "6" and argv[argv.index("--model") + 1] == "Veo 3.1 Lite"
    res = cli_run(env)
    assert "[s01] BỎ QUA" in res.output, res.output
    assert len(env.i2v_calls()) == 1  # still the one call of the first run
    assert env.scene()["status"] == SUBMITTING
    assert env.scene()["submitted_at"] == st1["submitted_at"]


@pytest.mark.parametrize("fail", ["9:true", "42:true"])
def test_no_auto_resend_web(env, monkeypatch, fail):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from filmmaker import planner
    from filmmaker.web import server
    from tests.test_model_web import wait

    settings = Path(os.environ["FILMMAKER_SETTINGS"])  # conftest: tmp_path/settings.json, also server.SETTINGS_PATH
    assert server.SETTINGS_PATH == settings
    settings.write_text(json.dumps({"pause_min": 0, "pause_max": 0, "retries": 0, "auto_backup": False}))
    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": False, "logged_in": False})
    c = TestClient(server.create_app(env.workspace, token="t0k"))
    c.headers["X-Token"] = "t0k"

    # run 1 through the web job
    monkeypatch.setenv("FAKE_FLOW_UI_FAIL", fail)
    r = c.post("/api/projects/film/run", json={"action": "video"})
    assert r.status_code == 200, r.text
    wait(c, r.json())
    monkeypatch.delenv("FAKE_FLOW_UI_FAIL")
    st = env.scene()
    assert st["status"] == SUBMITTING and st.get("submitted_at"), st
    assert len(env.i2v_calls()) == 1

    # run 2, no confirm_resend: blocked, nothing sent
    e = c.post("/api/projects/film/estimate", json={"action": "video"}).json()
    assert e["blocked"] == ["s01"] and e["renders"] == []
    r = c.post("/api/projects/film/run", json={"action": "video"})
    assert r.status_code == 200, r.text  # whole film: s01 is just skipped
    j = wait(c, r.json())
    assert any("[s01] BỎ QUA" in line for line in j["lines"]), j["lines"]
    r = c.post("/api/projects/film/run", json={"action": "video", "only": ["s01"], "force": ["s01"]})
    assert r.status_code == 428 and r.json()["detail"]["reason"] == "needs_confirm"
    assert c.get("/api/jobs/current").json()["status"] != "running"
    # and the CLI agrees
    assert "[s01] BỎ QUA" in cli_run(env).output
    assert len(env.i2v_calls()) == 1
    assert env.scene()["submitted_at"] == st["submitted_at"]


@pytest.mark.parametrize("fail", ["9:true", "42:true"])
def test_manual_clip_adopted_after_timeout(env, monkeypatch, fake_clip_check, fail):
    st = first_run(env, monkeypatch, fail)
    env.clip.parent.mkdir(parents=True, exist_ok=True)
    env.clip.write_bytes(CLIP)
    t = manual_clip.parse_stamp(st["submitted_at"]) + 5  # downloaded after the submission
    os.utime(env.clip, (t, t))
    res = cli_run(env)
    assert res.exit_code == 0, res.output
    assert manual_clip.adopt_line("s01", env.clip) in res.output
    after = env.scene()
    assert after["status"] == "done" and after["rendered_as"] == "manual" and after["video"] == str(env.clip)
    assert len(env.i2v_calls()) == 1  # no new flow_ui call
