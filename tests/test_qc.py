"""AI QC of rendered clips: measurements, the Claude call, storage tied to the clip file, CLI-free runs, web."""
import json
import shutil
import subprocess
import sys
import time

import pytest
import yaml

from filmmaker import qc
from filmmaker.gflow import Gflow
from filmmaker.pipeline import DONE, Pipeline, State
from filmmaker.storyboard import load_storyboard

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

SB = {"title": "Bạn", "model": "veo-lite", "aspect": "16:9", "dialogue_language": "Thai",
      "characters": [{"name": "Phet", "description": "boy, blue shirt"}],
      "scenes": [{"id": "s01", "mode": "keyframe", "image_prompt": "Phet on a bench", "prompt": "Phet rests",
                  "duration": 4, "dialogue": [{"character": "Phet", "line": "เราไม่เป็นไร", "delivery": "soft"}]},
                 {"id": "s02", "mode": "keyframe", "image_prompt": "Phet walks", "prompt": "Phet walks home",
                  "duration": 4}]}


def _ff(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture
def film(tmp_path):
    d = tmp_path / "ws" / "film"
    out = d / "output" / "storyboard"
    (out / "keyframes").mkdir(parents=True)
    (out / "clips").mkdir()
    (d / "storyboard.yaml").write_text(yaml.safe_dump(SB, allow_unicode=True), encoding="utf-8")
    return d, out


def _media(out, sid, *, same=True):
    """keyframe + 2 s clip (with a tone) that starts on it, or on another picture."""
    kf, other, clip = out / "keyframes" / f"{sid}.png", out / f"{sid}_other.png", out / "clips" / f"{sid}.mp4"
    _ff("-f", "lavfi", "-i", "testsrc2=size=640x360", "-frames:v", "1", str(kf))
    _ff("-f", "lavfi", "-i", "mandelbrot=size=640x360", "-frames:v", "1", str(other))
    _ff("-loop", "1", "-i", str(kf if same else other), "-f", "lavfi", "-i", "sine=frequency=440",
        "-t", "2", "-vf", "scale=1280:720,format=yuv420p", "-shortest", str(clip))
    return kf, clip


def _done(out, sid, clip):
    State(out / "state.json").update(sid, status=DONE, video=str(clip))


@pytest.fixture
def fake_claude(monkeypatch):
    seen = []

    def ask(system, user, schema, *, images=(), **kw):
        seen.append({"system": system, "user": user, "images": [label for label, _ in images]})
        return schema(verdict="PASS", score=9, picture="đúng cảnh", sound="đúng câu")

    monkeypatch.setattr(qc.ai, "ask", ask)
    return seen


@pytest.fixture
def no_whisper(monkeypatch):
    monkeypatch.setattr(qc, "whisper_model", lambda: None)


def test_expected_dialogue_text():
    from filmmaker.storyboard import Storyboard

    sb = Storyboard.model_validate(SB)
    assert "Phet: เราไม่เป็นไร (soft)" in qc.expected_dialogue(sb, sb.scenes[0])
    assert "in Thai" in qc.expected_dialogue(sb, sb.scenes[0])
    assert "No dialogue in this scene" in qc.expected_dialogue(sb, sb.scenes[1])
    sb.dialogue_language = "none"
    assert "nobody should speak" in qc.expected_dialogue(sb, sb.scenes[0])


@needs_ffmpeg
def test_audio_stats_reads_levels(film):
    _, out = film
    _, clip = _media(out, "s01")
    a = qc.audio_stats(clip)
    assert a["has_audio"] and a["mean_db"] < 0 and a["silent_share"] == 0.0 and a["duration"] > 1.5
    silent = out / "silent.mp4"
    _ff("-f", "lavfi", "-i", "color=c=black:s=320x240", "-t", "1", "-pix_fmt", "yuv420p", str(silent))
    assert qc.audio_stats(silent) == {"has_audio": False}


def test_transcribe_parses_whisper_json(tmp_path, monkeypatch):
    fake = tmp_path / "whisper-cli"
    fake.write_text(f"""#!{sys.executable}
import json, sys
a = sys.argv
base = a[a.index('-of') + 1]
json.dump({{"result": {{"language": "th"}}, "transcription": [
  {{"offsets": {{"from": 0, "to": 1500}}, "text": " เราไม่เป็นไร"}},
  {{"offsets": {{"from": 1500, "to": 2000}}, "text": " [Music]"}}]}}, open(base + '.json', 'w'))
""")
    fake.chmod(0o755)
    model = tmp_path / "m.bin"
    model.write_bytes(b"x")
    monkeypatch.setattr(qc, "whisper_binary", lambda: str(fake))
    monkeypatch.setattr(qc, "whisper_model", lambda: model)
    monkeypatch.setattr(qc.subprocess, "run", _ffmpeg_ok(qc.subprocess.run))
    r = qc.transcribe(tmp_path / "clip.mp4")
    assert r == {"language": "th", "text": "เราไม่เป็นไร", "segments": [{"start": 0.0, "end": 1.5, "text": "เราไม่เป็นไร"}]}


def _ffmpeg_ok(real):
    def run(cmd, **kw):
        if cmd[0] == "ffmpeg":  # the audio extraction: pretend it worked
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        return real(cmd, **kw)
    return run


def test_no_whisper_means_dialogue_not_checked(monkeypatch, tmp_path):
    monkeypatch.setattr(qc, "whisper_model", lambda: None)
    assert qc.transcribe(tmp_path / "x.mp4") is None


@needs_ffmpeg
def test_check_scene_sends_keyframe_frames_and_facts(film, fake_claude, no_whisper):
    d, out = film
    kf, clip = _media(out, "s01")
    sb = load_storyboard(d / "storyboard.yaml")
    r = qc.check_scene(sb, sb.scenes[0], clip, kf, out / "qcw")
    assert r["verdict"] == "PASS" and r["match"] > 0.95 and r["dialogue_checked"] is False
    call = fake_claude[0]
    assert call["images"][0].startswith("scene s01 keyframe") and "6 frames" in call["images"][-1]
    assert "Expected dialogue" in call["user"] and "เราไม่เป็นไร" in call["user"]
    assert "Transcript: not available" in call["user"] and "Start-frame match" in call["user"]


@needs_ffmpeg
def test_clip_of_another_picture_is_always_fail(film, fake_claude, no_whisper):
    d, out = film
    kf, clip = _media(out, "s01", same=False)
    sb = load_storyboard(d / "storyboard.yaml")
    r = qc.check_scene(sb, sb.scenes[0], clip, kf, out / "qcw")
    assert r["verdict"] == "FAIL" and r["match"] < qc.WRONG_START
    assert "nhầm ảnh" in r["issues"][0]


@needs_ffmpeg
def test_run_checks_new_clips_once_and_again_when_the_clip_changes(film, fake_claude, no_whisper):
    d, out = film
    _, clip = _media(out, "s01")
    _done(out, "s01", clip)
    pipe = Pipeline(load_storyboard(d / "storyboard.yaml"), out, Gflow(dry_run=True, log=lambda *_: None))
    lines = []
    assert set(qc.run(pipe, log=lines.append)) == {"s01"}  # s02 has no clip: skipped
    assert qc.current(out, "s01", clip)["verdict"] == "PASS"
    assert qc.run(pipe, log=lines.append) == {}  # already checked
    assert set(qc.run(pipe, force=True, log=lines.append)) == {"s01"}
    time.sleep(1.1)
    _media(out, "s01", same=True)  # a new render of the scene
    assert qc.current(out, "s01", clip) is None
    assert set(qc.run(pipe, log=lines.append)) == {"s01"}
    assert any("chưa có model" in l for l in lines) and any("QC PASS 9/10" in l for l in lines)


@needs_ffmpeg
def test_claude_error_skips_scene(film, monkeypatch, no_whisper):
    d, out = film
    _, clip = _media(out, "s01")
    _done(out, "s01", clip)

    def boom(*a, **k):
        raise RuntimeError("Claude bận")

    monkeypatch.setattr(qc.ai, "ask", boom)
    pipe = Pipeline(load_storyboard(d / "storyboard.yaml"), out, Gflow(dry_run=True, log=lambda *_: None))
    lines = []
    assert qc.run(pipe, log=lines.append) == {}
    assert any("không QC được (Claude bận)" in l for l in lines) and qc.load(out) == {}


# --- web ---------------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi")


@pytest.fixture
def web(film, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from filmmaker import planner
    from filmmaker.web import server

    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": False, "logged_in": False})
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"anthropic_api_key": "sk-test", "planner": "api"}))
    monkeypatch.setattr(server, "SETTINGS_PATH", settings)
    c = TestClient(server.create_app(tmp_path / "ws", token="t0k"))
    c.headers["X-Token"] = "t0k"
    return c


def _wait(c, job):
    for _ in range(200):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job still running")


@needs_ffmpeg
def test_web_qc_job_and_scene_state(web, film, fake_claude, no_whisper):
    _, out = film
    _, clip = _media(out, "s01")
    _done(out, "s01", clip)
    assert web.get("/api/projects/film").json()["scene_state"]["s01"]["qc"] is None
    r = web.post("/api/projects/film/qc", json={"only": ["s01"]})
    assert r.status_code == 200, r.text
    j = _wait(web, r.json())
    assert j["status"] == "done" and j["result"]["qc"] == {"s01": "PASS"}
    q = web.get("/api/projects/film").json()["scene_state"]["s01"]["qc"]
    assert q["verdict"] == "PASS" and q["score"] == 9 and "sig" not in q
    env = web.get("/api/env").json()
    assert set(env["qc"]) >= {"whisper", "model", "model_name"}


def test_web_settings_keep_auto_qc(web):
    assert web.get("/api/settings").json().get("auto_qc", True) is True
    web.put("/api/settings", json={"auto_qc": False})
    assert web.get("/api/settings").json()["auto_qc"] is False


def test_pipeline_stores_absolute_paths_for_a_relative_out(film, monkeypatch):
    # A CLI run with a relative --out stored relative clip paths: the app, running from another folder,
    # then saw every such scene as "changed" (clip missing).
    d, _ = film
    monkeypatch.chdir(d)
    pipe = Pipeline(load_storyboard(d / "storyboard.yaml"), __import__("pathlib").Path("output/storyboard"),
                    Gflow(dry_run=True, log=lambda *_: None))
    assert pipe.out.is_absolute() and pipe.out == (d / "output" / "storyboard").resolve()
    assert pipe.clip_path(pipe.sb.scenes[0]).is_absolute()
