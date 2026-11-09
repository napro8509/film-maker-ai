"""Pipeline.video_plan / scene groups / RunOptions.refuse and filmmaker.retirement (model-retirement T2)."""
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest

from filmmaker import manual_clip, model_meta, retirement
from filmmaker.gflow import Gflow
from filmmaker.pipeline import DONE, FAILED, SUBMITTING, Pipeline, RunOptions, send_list
from filmmaker.storyboard import load_storyboard
from tests.model_helpers import stamp

FAKE = Path(__file__).parent / "fake_gflow.py"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

STORY = """
title: Plan film
aspect: "16:9"
model: veo-fast
scenes:
  - id: s01
    mode: t2v
    prompt: "one"
  - id: s02
    mode: t2v
    prompt: "two"
  - id: s03
    mode: keyframe
    image_prompt: "a cafe"
    prompt: "three"
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    sb_path = tmp_path / "story.yaml"
    sb_path.write_text(STORY)
    log = tmp_path / "calls.log"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(log))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("FILMMAKER_MODELS", raising=False)
    monkeypatch.setattr(model_meta, "today", lambda: date(2026, 11, 9))

    def calls():
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    return sb_path, calls


def make_pipe(sb_path, out, sleep=lambda *_: None):
    return Pipeline(load_storyboard(sb_path), out, Gflow(log=lambda *_: None), log=lambda *_: None, sleep=sleep)


def set_today(monkeypatch, y, m, d):
    monkeypatch.setattr(model_meta, "today", lambda: date(y, m, d))


def put_state(pipe, sid, status, *, good_fp=True, clip_bytes=None, submitted_at=None):
    s = next(x for x in pipe.sb.scenes if x.id == sid)
    clip = pipe.clip_path(s)
    if clip_bytes is not None:
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.write_bytes(b"\0" * clip_bytes)
        t = manual_clip.parse_stamp(submitted_at)
        if t is not None:  # saved after the submission
            os.utime(clip, (t + 5, t + 5))
    if status is None:
        return s
    fields = {"status": status}
    if submitted_at is not None:
        fields["submitted_at"] = submitted_at
    if status == DONE:
        video = pipe.out / "done" / f"{sid}.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"\0" * 20_000)
        fields.update(video=str(video), fingerprint=pipe.sb.scene_fingerprint(s) if good_fp else "old")
    pipe.state.update(sid, **fields)
    return s


# --- video_plan ------------------------------------------------------------

@pytest.mark.parametrize("status,good_fp,clip,force,retry,kind", [
    (DONE, True, None, False, False, "done"),
    (DONE, True, None, True, False, "send"),
    (SUBMITTING, True, 20_000, False, False, "adopt"),
    (SUBMITTING, True, 20_000, True, False, "adopt"),  # a fresh clip is adopted even when forced
    (SUBMITTING, True, 20_000, False, True, "adopt"),
    (SUBMITTING, True, None, False, False, "blocked"),
    (SUBMITTING, True, None, False, True, "send"),
    (SUBMITTING, True, 9_999, False, False, "blocked"),
    (DONE, False, None, False, False, "send"),      # changed
    (FAILED, True, None, False, False, "send"),
    (None, True, None, False, False, "send"),       # todo
], ids=["done", "done+force", "adopt", "adopt+force", "adopt+retry", "blocked", "blocked+retry",
        "small-clip", "changed", "failed", "todo"])
def test_video_plan_kinds(env, tmp_path, fake_clip_check, status, good_fp, clip, force, retry, kind):
    sb_path, _ = env
    pipe = make_pipe(sb_path, tmp_path / "out")
    sent = stamp(-60) if status == SUBMITTING and clip else None
    put_state(pipe, "s01", status, good_fp=good_fp, clip_bytes=clip, submitted_at=sent)
    plan = pipe.video_plan({"s01"}, {"s01"} if force else set(), retry)
    assert [(s.id, k) for s, k in plan] == [("s01", kind)]


def test_video_plan_only_empty_or_none_means_all(env, tmp_path):
    sb_path, _ = env
    pipe = make_pipe(sb_path, tmp_path / "out")
    put_state(pipe, "s01", DONE)
    put_state(pipe, "s02", SUBMITTING)
    expected = [("s01", "done"), ("s02", "blocked"), ("s03", "send")]
    assert [(s.id, k) for s, k in pipe.video_plan(None, set(), False)] == expected
    assert [(s.id, k) for s, k in pipe.video_plan(set(), set(), False)] == expected
    assert [(s.id, k) for s, k in pipe.video_plan({"s02"}, set(), False)] == [("s02", "blocked")]
    assert [s.id for s in send_list(pipe, set(), set(), False)] == ["s03"]
    assert [s.id for s in send_list(pipe, None, set(), True)] == ["s02", "s03"]


def test_video_plan_has_no_side_effects(env, tmp_path, fake_clip_check):
    sb_path, _ = env
    pipe = make_pipe(sb_path, tmp_path / "out")
    put_state(pipe, "s01", SUBMITTING, clip_bytes=20_000, submitted_at=stamp(-60))
    before = (pipe.out / "state.json").read_bytes()
    assert [k for _, k in pipe.video_plan(None, set(), False)] == ["adopt", "send", "send"]
    assert (pipe.out / "state.json").read_bytes() == before
    assert pipe.state.scene("s01")["status"] == SUBMITTING


def test_scene_group_and_manual_clip(env, tmp_path, fake_clip_check):
    sb_path, _ = env
    pipe = make_pipe(sb_path, tmp_path / "out")
    s1 = put_state(pipe, "s01", DONE, good_fp=False)          # changed -> todo
    s2 = put_state(pipe, "s02", SUBMITTING, clip_bytes=10_000, submitted_at=stamp(-60))
    s3 = put_state(pipe, "s03", FAILED, clip_bytes=9_999)
    assert [pipe.scene_group(s) for s in (s1, s2, s3)] == ["todo", "interrupted", "todo"]
    assert pipe.has_manual_clip(s2) and not pipe.has_manual_clip(s3) and not pipe.has_manual_clip(s1)
    put_state(pipe, "s01", DONE)
    assert pipe.scene_group(s1) == "done"


def test_run_blocks_when_manual_clip_vanishes(env, tmp_path, monkeypatch, fake_clip_check):
    sb_path, calls = env
    pipe = make_pipe(sb_path, tmp_path / "out")
    put_state(pipe, "s01", SUBMITTING, clip_bytes=20_000, submitted_at=stamp(-60))
    monkeypatch.setattr(Pipeline, "_adopt_manual_clip", lambda self, s: False)
    rep = pipe.run(RunOptions(only={"s01"}))
    assert "s01" in rep.failed and "bị ngắt" in rep.failed["s01"]
    assert not any(c[0] == "video" for c in calls())


# --- Pipeline.run ------------------------------------------------------------

@needs_ffmpeg
def test_keyframes_only_does_not_adopt_manual_clip(env, tmp_path):
    sb_path, calls = env
    pipe = make_pipe(sb_path, tmp_path / "out")
    put_state(pipe, "s01", SUBMITTING, clip_bytes=20_000)
    rep = pipe.run(RunOptions(keyframes_only=True))
    assert rep.ok
    st = make_pipe(sb_path, tmp_path / "out").state.scene("s01")
    assert st["status"] == SUBMITTING and "rendered_as" not in st
    assert not any(c[0] == "video" for c in calls())


@needs_ffmpeg
def test_refuse_checked_before_each_scene(env, tmp_path, monkeypatch):
    sb_path, calls = env
    set_today(monkeypatch, 2026, 12, 4)
    orig = Pipeline._render_scene

    def render_then_midnight(self, s, project, opts):
        out = orig(self, s, project, opts)
        set_today(monkeypatch, 2026, 12, 5)
        return out

    monkeypatch.setattr(Pipeline, "_render_scene", render_then_midnight)
    pipe = make_pipe(sb_path, tmp_path / "out")
    backed_up = []
    rep = pipe.run(RunOptions(only={"s01", "s02"}, pause=(0, 0), after_scene=backed_up.append,
                              refuse=retirement.refuse_retired(pipe.sb)))
    assert rep.done == ["s01"]
    assert "đã ngừng" in rep.failed["s02"] and "veo-fast" in rep.failed["s02"]
    assert backed_up == ["s01"]
    assert sum(1 for c in calls() if c[0] == "video") == 1
    assert make_pipe(sb_path, tmp_path / "out").state.scene("s02") == {}


@needs_ffmpeg
def test_no_pause_after_refused_scene(env, tmp_path):
    sb_path, calls = env
    sleeps = []
    pipe = make_pipe(sb_path, tmp_path / "out", sleep=sleeps.append)
    rep = pipe.run(RunOptions(only={"s01", "s02"}, pause=(5, 5),
                              refuse=lambda s: "no" if s.id == "s01" else None))
    assert rep.done == ["s02"] and rep.failed == {"s01": "no"}
    assert sleeps == []
    assert sum(1 for c in calls() if c[0] == "video") == 1


@needs_ffmpeg
def test_default_refuse_keeps_pause_between_sends(env, tmp_path):
    sb_path, calls = env
    sleeps = []
    pipe = make_pipe(sb_path, tmp_path / "out", sleep=sleeps.append)
    rep = pipe.run(RunOptions(only={"s01", "s02"}, pause=(5, 5)))
    assert rep.done == ["s01", "s02"]
    assert sleeps == [5]
    assert sum(1 for c in calls() if c[0] == "video") == 2


# --- retirement ----------------------------------------------------------------

def test_refuse_retired_by_date(env, tmp_path, monkeypatch):
    sb_path, _ = env
    sb = load_storyboard(sb_path)
    refuse = retirement.refuse_retired(sb)
    assert refuse(sb.scenes[0]) is None
    set_today(monkeypatch, 2026, 12, 5)
    assert "model veo-fast đã ngừng từ 05/12" in refuse(sb.scenes[0])
    sb.scenes[1].model = "veo-lite"
    assert refuse(sb.scenes[1]) is None


def test_model_warning_note_and_used(env, tmp_path, monkeypatch):
    sb_path, _ = env
    pipe = make_pipe(sb_path, tmp_path / "out")
    put_state(pipe, "s01", DONE)
    put_state(pipe, "s02", SUBMITTING)
    meta = model_meta.load()
    w = retirement.model_warning(pipe, meta)
    assert w["models"] == [{"alias": "veo-fast", "status": "retiring", "retires_label": "05/12",
                            "todo": 1, "done": 1, "interrupted": 1}]
    assert w["text"] == "Model veo-fast ngừng ngày 05/12: 1 cảnh chưa có clip, 1 cảnh đã có clip, 1 cảnh bị ngắt."
    assert retirement.model_note(pipe) is None
    assert retirement.models_used(pipe, meta) == {"veo-fast": {"status": "retiring", "retires_label": "05/12",
                                                               "known": True}}
    assert retirement.retired_of(pipe, meta, pipe.sb.scenes) == []

    set_today(monkeypatch, 2026, 12, 6)
    meta = model_meta.load()
    assert retirement.model_warning(pipe, meta)["lines"][0].startswith("Model veo-fast đã ngừng từ 05/12:")
    assert [r["id"] for r in retirement.retired_of(pipe, meta, send_list(pipe, None, set(), False))] == ["s03"]
    groups = {r["id"]: r["group"] for r in retirement.old_scenes(pipe, meta, pipe.sb.scenes)}
    assert groups == {"s01": "done", "s02": "interrupted", "s03": "todo"}

    pipe.sb.model = None
    pipe.sb.scenes[0].model = "veo-lite"
    meta = model_meta.load()
    assert retirement.model_warning(pipe, meta) is None
    assert retirement.model_note(pipe) == retirement.MODEL_NOTE
    assert retirement.models_used(pipe, meta) == {"veo-lite": {"status": "active", "retires_label": None,
                                                               "known": True}}


def test_sentence_omits_zero_interrupted():
    assert (retirement.sentence("veo-fast", "retiring", "05/12", 0, 0, 0)
            == "Model veo-fast ngừng ngày 05/12: 0 cảnh chưa có clip, 0 cảnh đã có clip.")
