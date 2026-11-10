"""QA extras for flow-render-fixes: edge cases the spec does not spell out (run twice, render one valid scene in a
film with bad scenes, models without `durations`, odd Flow bubbles). Fake gflow / fake flow_ui only, temp settings.
"""
import json
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from filmmaker import flow_ui
from filmmaker.cli import main
from tests.duration_helpers import veo_lite_project
from tests.model_helpers import set_scene, stamp, write_project

pytest.importorskip("fastapi")

from tests.test_model_web import make_client  # noqa: E402,F401

FAKE = Path(__file__).parent / "fake_gflow.py"


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    settings = tmp_path / "cfg" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"auto_backup": False}))
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(settings))
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "gflow.log"))
    return tmp_path


def cli(*args):
    return CliRunner().invoke(main, [str(a) for a in args])


@pytest.fixture
def c(make_client):
    return make_client(auto_backup=False)


# --- web: running the round twice -----------------------------------------------------

def test_web_round_twice_same_base_is_stale_and_second_pass_is_noop(c):
    p = write_project(c.ws, "film", "veo-lite", [{"id": "s01", "duration": 5}, {"id": "s02", "duration": 7}])
    base = c.post("/api/projects/film/durations/preview").json()["base"]
    r1 = c.post("/api/projects/film/durations/round", json={"direction": "up", "base": base})
    assert r1.status_code == 200 and r1.json()["changed"] is True
    after = p.read_bytes()
    # a double click / retry with the old base must not write again
    r2 = c.post("/api/projects/film/durations/round", json={"direction": "down", "base": base})
    assert r2.status_code == 409 and r2.json()["detail"]["reason"] == "stale_base"
    assert p.read_bytes() == after
    # fresh preview: nothing left; round with it changes nothing and leaves the bytes alone
    pv = c.post("/api/projects/film/durations/preview").json()
    assert pv["scenes"] == [] and pv["skipped"] == []
    r3 = c.post("/api/projects/film/durations/round", json={"direction": "up", "base": pv["base"]})
    assert r3.status_code == 200 and r3.json()["changed"] is False
    assert p.read_bytes() == after
    assert [s["duration"] for s in yaml.safe_load(after)["scenes"]] == [6, 8]


def test_web_estimate_one_valid_scene_not_blocked_by_others(c):
    """Rendering a valid scene alone (`only`) is not refused because other scenes of the film are bad."""
    write_project(c.ws, "film", "veo-lite", [{"id": "s01", "duration": 5}, {"id": "s02", "duration": 6}])
    r = c.post("/api/projects/film/estimate", json={"action": "video", "only": ["s02"]})
    assert r.status_code == 200, r.text
    assert r.json()["bad_duration"] == [] and r.json()["blocked_bad_duration"] == []
    # and the whole film is still flagged
    r = c.post("/api/projects/film/estimate", json={"action": "video"})
    assert [x["id"] for x in r.json()["bad_duration"]] == ["s01"]


def test_web_interrupted_changed_to_valid_is_resendable(c):
    """AC16 last steps: after the user fixes s02 by hand, it leaves blocked_bad_duration (the tick works again)."""
    p = write_project(c.ws, "film", "veo-lite", [{"id": "s01", "duration": 6}, {"id": "s02", "duration": 7}])
    set_scene(p, "s02", "interrupted", submitted_at=stamp(-60))
    body = {"action": "video", "only": ["s02"], "force": ["s02"]}
    assert [x["id"] for x in c.post("/api/projects/film/estimate", json=body).json()["blocked_bad_duration"]] == ["s02"]
    d = c.get("/api/projects/film").json()
    sb = d["storyboard"]
    sb["scenes"][1]["duration"] = 8
    put = c.put("/api/projects/film", json={"storyboard": sb})
    assert put.status_code == 200, put.text
    assert put.json()["scene_state"]["s02"]["bad_duration"] is None
    e = c.post("/api/projects/film/estimate", json=body).json()
    assert e["blocked"] == ["s02"] and e["blocked_bad_duration"] == []


# --- CLI ------------------------------------------------------------------------------

def test_cli_round_up_twice_is_idempotent(cli_env):
    sb = veo_lite_project(cli_env / "ws", "film", [{"id": "s01", "duration": 5}, {"id": "s02", "duration": 9},
                                                   {"id": "s03", "duration": 3}])
    r1 = cli("round-durations", sb, "--up")
    assert r1.exit_code == 0, r1.output
    assert "s02: 9 → 8 giây" in r1.output and "s03: 3 → 4 giây" in r1.output
    once = sb.read_bytes()
    mtime = sb.stat().st_mtime_ns
    r2 = cli("round-durations", sb, "--up")
    assert r2.exit_code == 0, r2.output
    assert "✔ Độ dài cảnh hợp lệ với model" in r2.output
    assert sb.read_bytes() == once and sb.stat().st_mtime_ns == mtime
    assert not (cli_env / "gflow.log").exists()


def test_cli_scene_model_without_durations_is_never_bad(cli_env):
    """A scene on omni-1.2-flash (no `durations` in the metadata) inside a veo-lite film: not listed, not changed."""
    sb = veo_lite_project(cli_env / "ws", "film", [{"id": "s01", "duration": 5, "model": "omni-1.2-flash"},
                                                   {"id": "s02", "duration": 6}])
    r = cli("round-durations", sb)
    assert r.exit_code == 0, r.output
    before = sb.read_bytes()
    r = cli("round-durations", sb, "--down")
    assert r.exit_code == 0 and sb.read_bytes() == before


# --- flow_ui: odd bubbles --------------------------------------------------------------

def test_classify_tolerates_empty_and_none_bubbles():
    assert flow_ui.classify_replies(["", None, "  "]) == (None, False)
    s10 = ("warning\nFailed\nAudio generation failed. Please try a different prompt or send feedback.\n\n"
           "Your video generation has been scheduled and is waiting in the queue due to high demand.")
    assert flow_ui.classify_replies([None, s10, ""]) == ("queued", True)
    # audio failure in an older bubble still sets the flag when a newer one says "failed"
    assert flow_ui.classify_replies(["Audio generation failed.", "Video generation failed."]) == ("failed", True)
