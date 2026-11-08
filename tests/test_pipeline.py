import json
import shutil
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from filmmaker.cli import main
from filmmaker.gflow import Gflow, _extract_json
from filmmaker.pipeline import Pipeline, RunOptions
from filmmaker.storyboard import load_storyboard

FAKE = Path(__file__).parent / "fake_gflow.py"
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

STORY = """
title: Test film
aspect: "16:9"
model: veo-fast
style: "cinematic"
characters:
  - name: Lan
    description: "short black hair, red scarf"
scenes:
  - id: s01
    mode: keyframe
    image_prompt: "Lan at a cafe"
    prompt: "slow push-in"
    narration: "Xin chào"
  - id: s02
    mode: t2v
    prompt: "Lan walks in the rain"
  - id: s03
    mode: i2v
    image: start.png
    prompt: "pan left"
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "start.png").write_bytes(b"png")
    sb_path = tmp_path / "story.yaml"
    sb_path.write_text(STORY)
    log = tmp_path / "calls.log"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(log))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)

    def calls():
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    return sb_path, calls


def make_pipe(sb_path, out):
    return Pipeline(load_storyboard(sb_path), out, Gflow(log=lambda *_: None), log=lambda *_: None, sleep=lambda *_: None)


def test_extract_json_ignores_noise():
    assert _extract_json('banner {not json\n{"a": 1}\nlog {"status": "ok"}') == {"status": "ok"}


def test_prompts_include_character_and_style(env):
    sb = load_storyboard(env[0])
    t2v = sb.video_prompt(sb.scenes[1])
    assert "red scarf" in t2v and "cinematic" in t2v
    assert "red scarf" in sb.image_prompt(sb.scenes[0])
    assert sb.video_prompt(sb.scenes[0]) == "slow push-in"  # look comes from the keyframe


def test_full_run_then_resume_skips_everything(env, tmp_path):
    sb_path, calls = env
    out = tmp_path / "out"
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert report.ok and report.done == ["s01", "s02", "s03"]
    cmds = [c[:2] for c in calls()]
    assert cmds == [["project", "create"], ["image", "t2i"], ["video", "i2v"], ["video", "t2v"], ["video", "i2v"]]
    assert all("--project" in c and "proj-123" in c for c in calls()[1:])

    n = len(calls())
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert report.skipped == ["s01", "s02", "s03"] and len(calls()) == n


def test_edit_scene_regenerates_only_that_scene(env, tmp_path):
    sb_path, calls = env
    out = tmp_path / "out"
    make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    sb_path.write_text(STORY.replace("Lan walks in the rain", "Lan runs in the rain"))
    n = len(calls())
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert report.done == ["s02"]
    assert [c[:2] for c in calls()[n:]] == [["video", "t2v"]]


def test_retryable_error_is_retried(env, tmp_path, monkeypatch):
    sb_path, calls = env
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "s02.mp4:10:true:1")
    report = make_pipe(sb_path, tmp_path / "out").run(RunOptions(pause=(0, 0)))
    assert report.ok
    assert sum(1 for c in calls() if c[:2] == ["video", "t2v"]) == 2


def test_no_credits_aborts_run(env, tmp_path, monkeypatch):
    sb_path, calls = env
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "s02.mp4:37:false")
    report = make_pipe(sb_path, tmp_path / "out").run(RunOptions(pause=(0, 0)))
    assert report.done == ["s01"] and "s02" in report.failed and report.aborted
    assert not any("s03.mp4" in " ".join(c) for c in calls())


def test_interrupted_render_is_not_resubmitted(env, tmp_path):
    sb_path, calls = env
    out = tmp_path / "out"
    pipe = make_pipe(sb_path, out)
    pipe.state.data["project_id"] = "proj-123"
    pipe.state.update("s02", status="submitting")
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert "s02" in report.failed and set(report.done) == {"s01", "s03"}
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0), retry_unknown=True))
    assert report.done == ["s02"]


def test_cli_make_produces_final_film(env, tmp_path, monkeypatch):
    sb_path, calls = env
    monkeypatch.setenv("FAKE_GFLOW_NO_AUDIO", "s03.mp4")  # one clip without audio
    res = CliRunner().invoke(main, ["make", str(sb_path), "--pause", "0", "--burn-subtitles"])
    assert res.exit_code == 0, res.output
    final = sb_path.parent / "output" / "story" / "final.mp4"
    assert final.is_file()
    assert "Xin chào" in final.with_suffix(".srt").read_text()
    res = CliRunner().invoke(main, ["status", str(sb_path)])
    assert res.output.count("✔ xong") == 3


def test_cli_dry_run_spends_nothing(env, tmp_path):
    sb_path, calls = env
    res = CliRunner().invoke(main, ["run", str(sb_path), "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "gflow" in res.output or "fake_gflow" in res.output
    assert calls() == []
