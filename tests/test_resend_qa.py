"""QA additions for resend-credit-guard: end-to-end chains the per-AC tests check only in pieces.
Fake gflow, TestClient, CliRunner; never Flow."""
import json
import os
import time

import pytest
from click.testing import CliRunner

pytest.importorskip("fastapi")

from filmmaker import manual_clip  # noqa: E402
from filmmaker.cli import main  # noqa: E402
from filmmaker.gflow import GflowError  # noqa: E402
from filmmaker.pipeline import DONE, Pipeline  # noqa: E402
from tests.model_helpers import (make_mp4, pipeline, set_scene, stamp, truncate_half, ui_out_dir,  # noqa: E402
                                 write_project)
from tests.test_model_web import client, make_client, needs_ffmpeg, video_calls, wait  # noqa: E402,F401


def _state(p, sid):
    return json.loads((ui_out_dir(p) / "state.json").read_text())["scenes"][sid]


def _billed_keyframe(self, *a, **kw):
    raise GflowError("keyframe: billed", exit_code=9, payload={"error": {"spent": True}})


@needs_ffmpeg
def test_qa_ac36_web_rerender_billed_before_video_then_get_stale_and_cli_does_not_adopt(
        client, monkeypatch, fake_clip_check):
    """AC36 chain through the real entry points: web "Render lại" of a DONE keyframe scene with a leftover
    submitted_at, billed in the keyframe step -> GET says "stale" -> CLI `run` neither adopts nor sends."""
    p = write_project(client.ws, "film", "veo-lite",
                      [{"id": "s01", "mode": "keyframe", "image_prompt": "a cafe", "prompt": "push in"}])
    pipe = pipeline(p)
    s = pipe.sb.scenes[0]
    clip = pipe.clip_path(s)
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"\0" * 20_000)
    now = time.time()
    os.utime(clip, (now - 10, now - 10))
    pipe.state.update("s01", status=DONE, video=str(clip), fingerprint=pipe.sb.scene_fingerprint(s),
                      submitted_at=stamp(-100))
    monkeypatch.setattr(Pipeline, "_keyframe", _billed_keyframe)
    before = int(time.time())

    r = client.post("/api/projects/film/run", json={"action": "video", "only": ["s01"], "force": ["s01"]})
    assert r.status_code == 200, r.text  # DONE scene: no 428
    wait(client, r.json())
    st = _state(p, "s01")
    assert st["status"] == "submitting" and manual_clip.parse_stamp(st["submitted_at"]) >= before
    assert video_calls(client) == []

    assert client.get("/api/projects/film").json()["scene_state"]["s01"]["manual_clip"] == "stale"
    est = client.post("/api/projects/film/estimate", json={"action": "video"}).json()
    assert est["adopt"] == [] and est["blocked"] == ["s01"]

    res = CliRunner().invoke(main, ["run", str(p), "--pause", "0-0", "--only", "s01"])
    assert res.exit_code == 1, res.output
    assert "dùng clip bạn đã tải về" not in res.output and "touch" in res.output
    assert _state(p, "s01")["status"] == "submitting"
    assert video_calls(client) == []


def test_qa_adopt_clip_route_twice(client, fake_clip_check):
    """Pressing [Dùng clip này] twice (two tabs): the second call is a clean 409, the state stays DONE."""
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}])
    set_scene(p, "s01", "interrupted", manual_clip=True)
    assert client.post("/api/projects/film/scenes/s01/adopt-clip").status_code == 200
    r = client.post("/api/projects/film/scenes/s01/adopt-clip")
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "not_adoptable"
    assert _state(p, "s01")["status"] == DONE and client.calls() == []


@needs_ffmpeg
def test_qa_touch_remedy_for_stale_clip_makes_it_adopted_on_web(client, fake_clip_check):
    """The remedy the messages give for a stale clip (`touch clips/<id>.mp4`) really works: next web run
    adopts it without any confirmation and sends nothing."""
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}])
    pipe = set_scene(p, "s01", "interrupted", manual_clip=True, submitted_at=stamp(-60))
    clip = pipe.clip_path(pipe.sb.scenes[0])
    old = manual_clip.parse_stamp(_state(p, "s01")["submitted_at"]) - 5
    os.utime(clip, (old, old))
    assert client.get("/api/projects/film").json()["scene_state"]["s01"]["manual_clip"] == "stale"
    os.utime(clip, None)  # touch
    assert client.get("/api/projects/film").json()["scene_state"]["s01"]["manual_clip"] == "ok"
    r = client.post("/api/projects/film/run", json={"action": "video", "only": ["s01"], "force": ["s01"]})
    assert r.status_code == 200, r.text
    j = wait(client, r.json())
    assert j["result"]["skipped"] == ["s01"] and video_calls(client) == []
    assert _state(p, "s01")["rendered_as"] == "manual"


@needs_ffmpeg
@pytest.mark.parametrize("faststart", [False, True], ids=["moov-at-end", "faststart"])
def test_qa_adopt_clip_route_with_real_ffprobe(client, tmp_path, faststart):
    """No fake clip check: the server route uses the real ffprobe. A truncated download is refused
    (clip_invalid), the full file is taken."""
    p = write_project(client.ws, "film", "veo-lite", [{"id": "s01"}])
    pipe = set_scene(p, "s01", "interrupted")  # legacy: no submitted_at
    clip = pipe.clip_path(pipe.sb.scenes[0])
    clip.parent.mkdir(parents=True, exist_ok=True)
    full = make_mp4(tmp_path / "full.mp4", faststart=faststart)
    clip.write_bytes(full.read_bytes())
    truncate_half(clip)
    assert clip.stat().st_size >= manual_clip.MIN_BYTES
    assert client.get("/api/projects/film").json()["scene_state"]["s01"]["manual_clip"] == "invalid"
    r = client.post("/api/projects/film/scenes/s01/adopt-clip")
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "clip_invalid", r.text
    clip.write_bytes(full.read_bytes())
    assert client.get("/api/projects/film").json()["scene_state"]["s01"]["manual_clip"] == "unverified"
    r = client.post("/api/projects/film/scenes/s01/adopt-clip")
    assert r.status_code == 200, r.text
    assert _state(p, "s01")["rendered_as"] == "manual" and client.calls() == []
