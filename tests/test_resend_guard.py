"""Pipeline rules for interrupted ("submitting") scenes: submitted_at, adopting a hand-downloaded clip,
web confirmation, warnings (resend-credit-guard T1). Fake gflow / Flow chat driver only."""
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from filmmaker import manual_clip
from filmmaker.cli import main
from filmmaker.gflow import Gflow, GflowError
from filmmaker.pipeline import DONE, FAILED, SUBMITTING, Pipeline, RunOptions
from filmmaker.storyboard import load_storyboard
from tests.model_helpers import stamp

FAKE = Path(__file__).parent / "fake_gflow.py"
FAKE_UI = Path(__file__).parent / "fake_flow_ui.py"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")  # the fake gflow / Flow UI make clips with it

STORY = """
title: Guard film
aspect: "16:9"
model: veo-fast
scenes:
  - id: s01
    mode: keyframe
    image_prompt: "a cafe"
    prompt: "slow push-in"
  - id: s02
    mode: t2v
    prompt: "rain"
  - id: s03
    mode: i2v
    image: start.png
    prompt: "pan left"
"""
BILLED = "Submit was already clicked, so Flow may have made and billed this generation."
WARN = "có thể đã bị trừ credit ở lần trước, đang gửi lại"
NO_FFPROBE = "không có ffprobe nên không kiểm được clip tải tay"


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "start.png").write_bytes(b"png")
    sb_path = tmp_path / "story.yaml"
    sb_path.write_text(STORY)
    log = tmp_path / "calls.log"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(log))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)
    monkeypatch.delenv("FAKE_GFLOW_HINT", raising=False)

    class E:
        path = sb_path
        out = tmp_path / "out"

        @staticmethod
        def calls():
            return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

        @staticmethod
        def videos(sid):
            return [c for c in E.calls() if c[0] == "video" and f"{sid}.mp4" in " ".join(c)]

    return E


def make_pipe(env, out=None, lines=None, **gkw):
    log = lines.append if lines is not None else (lambda *_: None)
    return Pipeline(load_storyboard(env.path), out or env.out, Gflow(log=lambda *_: None, **gkw), log=log,
                    sleep=lambda *_: None)


def scene(pipe, sid):
    return next(s for s in pipe.sb.scenes if s.id == sid)


def interrupted(pipe, sid, *, submitted_at=None, clip=None, clip_offset=5.0):
    """Put `sid` in "submitting"; `clip` bytes go to clips/<id>.mp4 with mtime submitted_at + clip_offset."""
    s = scene(pipe, sid)
    fields = {"submitted_at": submitted_at} if submitted_at is not None else {}
    pipe.state.update(sid, status=SUBMITTING, fingerprint=pipe.sb.scene_fingerprint(s), **fields)
    if clip is not None:
        p = pipe.clip_path(s)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(clip)
        t = manual_clip.parse_stamp(submitted_at)
        if t is not None:
            os.utime(p, (t + clip_offset, t + clip_offset))
    return s


GOOD = b"\0" * 20_000
BROKEN = b"BROKEN" + b"\0" * 20_000


def floor_now() -> float:
    return float(int(datetime.now(timezone.utc).timestamp()))


def state(env, sid):
    return json.loads((env.out / "state.json").read_text())["scenes"][sid]


# --- submitted_at (AC1, AC2, AC36) ----------------------------------------------

@needs_ffmpeg
@pytest.mark.parametrize("sid,fail", [("s02", "video t2v"), ("s01", "video i2v")], ids=["t2v", "keyframe-i2v"])
def test_submitted_at_written_when_billed(env, monkeypatch, sid, fail):
    monkeypatch.setenv("FAKE_GFLOW_FAIL", f"{fail}:9:false")
    monkeypatch.setenv("FAKE_GFLOW_HINT", BILLED)
    before = floor_now()
    rep = make_pipe(env).run(RunOptions(only={sid}, pause=(0, 0)))
    after = datetime.now(timezone.utc).timestamp()
    st = state(env, sid)
    assert sid in rep.failed and st["status"] == SUBMITTING
    assert before <= manual_clip.parse_stamp(st["submitted_at"]) <= after


def test_submitted_at_written_text_only_fallback(env, monkeypatch):
    env.path.write_text(STORY.replace("model: veo-fast", "model: veo-fast\nfallback_t2v: true"))
    pipe = make_pipe(env, ui_enabled=False)
    pipe.state.data["i2v_unsupported"] = True
    pipe.state.save()
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video t2v:9:false")
    monkeypatch.setenv("FAKE_GFLOW_HINT", BILLED)
    before = floor_now()
    make_pipe(env, ui_enabled=False).run(RunOptions(only={"s01"}, pause=(0, 0)))
    st = state(env, "s01")
    assert st["status"] == SUBMITTING and manual_clip.parse_stamp(st["submitted_at"]) >= before
    assert any(c[:2] == ["video", "t2v"] and "s01.mp4" in " ".join(c) for c in env.calls())


def test_resend_rewrites_submitted_at(env, monkeypatch):
    interrupted(make_pipe(env), "s02", submitted_at=stamp(-100))
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video t2v:9:false")
    monkeypatch.setenv("FAKE_GFLOW_HINT", BILLED)
    before = floor_now()
    make_pipe(env).run(RunOptions(only={"s02"}, force={"s02"}, pause=(0, 0)))
    assert manual_clip.parse_stamp(state(env, "s02")["submitted_at"]) >= before


@needs_ffmpeg
def test_no_submitted_at_after_success_and_after_plain_failure(env, monkeypatch):
    pipe = make_pipe(env)
    interrupted(pipe, "s02", submitted_at=stamp(-100))
    interrupted(pipe, "s01", submitted_at=stamp(-100))
    make_pipe(env).run(RunOptions(only={"s02"}, force={"s02"}, pause=(0, 0)))
    assert state(env, "s02")["status"] == DONE and state(env, "s02").get("submitted_at") is None
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:2:false")  # not billed
    make_pipe(env).run(RunOptions(only={"s01"}, force={"s01"}, pause=(0, 0)))
    assert state(env, "s01")["status"] == FAILED and state(env, "s01").get("submitted_at") is None


def _billed_keyframe(self, *a, **kw):
    raise GflowError("keyframe: billed", exit_code=9, payload={"error": {"spent": True}})


def test_leftover_submitted_at_on_done_scene(env, monkeypatch, fake_clip_check):
    """A DONE scene with an old submitted_at is re-rendered and billed before the video call: the old
    clips/s01.mp4 must not be adopted later."""
    pipe = make_pipe(env)
    s = scene(pipe, "s01")
    clip = pipe.clip_path(s)
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(GOOD)
    now = datetime.now(timezone.utc).timestamp()
    os.utime(clip, (now - 10, now - 10))
    pipe.state.update("s01", status=DONE, video=str(clip), fingerprint=pipe.sb.scene_fingerprint(s),
                      submitted_at=stamp(-100))
    monkeypatch.setattr(Pipeline, "_keyframe", _billed_keyframe)
    before = floor_now()
    make_pipe(env).run(RunOptions(only={"s01"}, force={"s01"}, pause=(0, 0)))
    st = state(env, "s01")
    assert st["status"] == SUBMITTING and manual_clip.parse_stamp(st["submitted_at"]) >= before
    assert env.videos("s01") == []
    p2 = make_pipe(env)
    assert [k for _, k in p2.video_plan({"s01"}, set(), False)] == ["blocked"]
    assert p2.manual_clip_state(scene(p2, "s01")) == "stale"
    rep = p2.run(RunOptions(only={"s01"}, pause=(0, 0)))
    assert "s01" in rep.failed and state(env, "s01")["status"] == SUBMITTING


def test_equal_second_counts_as_current_attempt(env, monkeypatch):
    fixed = "2026-11-09T08:00:00Z"
    monkeypatch.setattr(manual_clip, "stamp_now", lambda: fixed)
    monkeypatch.setattr(Pipeline, "_keyframe", _billed_keyframe)
    pipe = make_pipe(env)
    pipe.state.update("s01", status=DONE, submitted_at="2026-11-09T08:00:00+00:00")  # same second, other spelling
    make_pipe(env).run(RunOptions(only={"s01"}, force={"s01"}, pause=(0, 0)))
    assert state(env, "s01")["submitted_at"] == "2026-11-09T08:00:00+00:00"  # kept: written by this attempt
    pipe = make_pipe(env)
    pipe.state.update("s01", status=DONE, submitted_at="2026-11-09T07:59:59Z")
    make_pipe(env).run(RunOptions(only={"s01"}, force={"s01"}, pause=(0, 0)))
    assert state(env, "s01")["submitted_at"] == fixed


# --- adopting a hand-downloaded clip (AC3, AC5, AC6, AC9, AC13) -------------------

def test_force_with_fresh_manual_clip_adopts(env, fake_clip_check):
    pipe = make_pipe(env)
    s = interrupted(pipe, "s02", submitted_at=stamp(-60), clip=GOOD)
    assert [k for _, k in pipe.video_plan({"s02"}, {"s02"}, False)] == ["adopt"]
    assert pipe.has_manual_clip(s)
    lines = []
    rep = make_pipe(env, lines=lines).run(RunOptions(only={"s02"}, force={"s02"}, pause=(0, 0)))
    assert rep.skipped == ["s02"] and env.videos("s02") == []
    st = state(env, "s02")
    assert st["status"] == DONE and st["rendered_as"] == "manual" and st.get("submitted_at") is None
    assert manual_clip.adopt_line("s02", pipe.clip_path(s)) in lines


def test_dry_run_does_not_adopt(env, fake_clip_check):
    pipe = make_pipe(env)
    interrupted(pipe, "s02", submitted_at=stamp(-60), clip=GOOD)
    before = (env.out / "state.json").read_bytes()
    lines = []
    Pipeline(load_storyboard(env.path), env.out, Gflow(dry_run=True, log=lambda *_: None), log=lines.append).run(
        RunOptions(only={"s02"}))
    assert (env.out / "state.json").read_bytes() == before
    assert any("(dry-run) sẽ dùng clip" in l for l in lines)


@needs_ffmpeg
def test_force_with_leftover_old_clip(env, monkeypatch, fake_clip_check):
    pipe = make_pipe(env)
    interrupted(pipe, "s02", submitted_at=stamp(-60), clip=GOOD, clip_offset=-2)
    s = scene(pipe, "s02")
    assert pipe.manual_clip_state(s) == "stale" and not pipe.has_manual_clip(s)
    plan = lambda **kw: [k for _, k in pipe.video_plan({"s02"}, **kw)]
    assert plan(force=set(), retry_unknown=False) == ["blocked"]
    assert plan(force=set(), retry_unknown=False, confirm=set()) == ["blocked"]     # web, not confirmed
    assert plan(force={"s02"}, retry_unknown=True, confirm=set()) == ["blocked"]   # web ignores force/retry
    assert plan(force={"s02"}, retry_unknown=False) == ["send"]                    # CLI --force
    assert plan(force=set(), retry_unknown=True) == ["send"]                       # CLI --retry-unknown
    assert plan(force=set(), retry_unknown=False, confirm={"s02"}) == ["send"]     # web, confirmed
    rep = make_pipe(env).run(RunOptions(only={"s02"}, confirm=set(), force={"s02"}, pause=(0, 0)))
    assert "s02" in rep.failed and env.videos("s02") == []
    lines = []
    rep = make_pipe(env, lines=lines).run(RunOptions(only={"s02"}, confirm={"s02"}, pause=(0, 0)))
    assert rep.done == ["s02"] and len(env.videos("s02")) == 1
    assert f"[s02] {WARN}" in lines


@pytest.mark.parametrize("good_fp", [True, False])
def test_done_scene_forced_never_adopts(env, fake_clip_check, good_fp):
    pipe = make_pipe(env)
    s = scene(pipe, "s02")
    clip = pipe.clip_path(s)
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(GOOD)
    pipe.state.update("s02", status=DONE, video=str(clip),
                      fingerprint=pipe.sb.scene_fingerprint(s) if good_fp else "old", submitted_at=stamp(-60))
    assert pipe.manual_clip_state(s) is None
    assert [k for _, k in pipe.video_plan({"s02"}, {"s02"}, False)] == ["send"]
    make_pipe(env).run(RunOptions(only={"s02"}, force={"s02"}, pause=(0, 0)))
    assert len(env.videos("s02")) == 1


@pytest.mark.parametrize("status", [FAILED, None])
def test_failed_or_todo_with_clip_not_adopted(env, fake_clip_check, status):
    pipe = make_pipe(env)
    s = scene(pipe, "s02")
    pipe.clip_path(s).parent.mkdir(parents=True, exist_ok=True)
    pipe.clip_path(s).write_bytes(GOOD)
    if status:
        pipe.state.update("s02", status=status, submitted_at=stamp(-60))
    assert pipe.manual_clip_state(s) is None
    assert [k for _, k in pipe.video_plan({"s02"}, set(), False)] == ["send"]


def test_manual_clip_state_values(env, fake_clip_check):
    pipe = make_pipe(env)
    s1 = interrupted(pipe, "s01", submitted_at=stamp(-60))                      # no file
    s2 = interrupted(pipe, "s02", submitted_at=stamp(-60), clip=GOOD)           # ok
    s3 = interrupted(pipe, "s03", clip=GOOD)                                    # legacy
    assert pipe.manual_clip_state(s1) is None
    assert pipe.manual_clip_state(s2) == "ok"
    assert pipe.manual_clip_state(s3) == "unverified" and pipe.is_legacy_interrupted(s3)
    interrupted(pipe, "s02", submitted_at=stamp(-60), clip=GOOD, clip_offset=-3)
    assert pipe.manual_clip_state(s2) == "stale"
    interrupted(pipe, "s02", submitted_at=stamp(-60), clip=BROKEN, clip_offset=-3)
    assert pipe.manual_clip_state(s2) == "invalid"                              # stale + broken
    interrupted(pipe, "s03", clip=BROKEN)
    assert pipe.manual_clip_state(s3) == "invalid"                              # legacy + broken
    interrupted(pipe, "s03", submitted_at="garbage", clip=GOOD)
    assert pipe.is_legacy_interrupted(s3) and pipe.manual_clip_state(s3) == "unverified"


def test_manual_clip_state_unchecked_without_ffprobe(env, no_ffprobe):
    pipe = make_pipe(env)
    s = interrupted(pipe, "s02", submitted_at=stamp(-60), clip=GOOD)
    assert pipe.manual_clip_state(s) == "unchecked" and not pipe.has_manual_clip(s)


# --- web confirmation inside the run (AC20, AC21, AC29) ---------------------------

@needs_ffmpeg
def test_web_run_sends_only_confirmed(env):
    pipe = make_pipe(env)
    interrupted(pipe, "s02", submitted_at=stamp(-60))
    interrupted(pipe, "s01", submitted_at=stamp(-60))
    rep = make_pipe(env).run(RunOptions(only={"s01", "s02"}, confirm={"s02"}, retry_unknown=True, force={"s01"},
                                        pause=(0, 0)))
    assert rep.done == ["s02"] and env.videos("s01") == []
    msg = rep.failed["s01"]
    assert "bị ngắt" in msg and "tick" in msg and "--retry-unknown" not in msg


@needs_ffmpeg
def test_job_rechecks_when_clip_vanishes(env, monkeypatch, fake_clip_check):
    pipe = make_pipe(env)
    s = interrupted(pipe, "s02", submitted_at=stamp(-60), clip=GOOD)
    real = Pipeline._adopt_manual_clip

    def vanish(self, sc, **kw):
        self.clip_path(sc).unlink()
        return real(self, sc, **kw)

    monkeypatch.setattr(Pipeline, "_adopt_manual_clip", vanish)
    rep = make_pipe(env).run(RunOptions(only={"s02"}, force={"s02"}, confirm=set(), pause=(0, 0)))
    assert env.videos("s02") == [] and "bị ngắt" in rep.failed["s02"]
    # confirmed on the web, or CLI --force: sent (with the warning)
    pipe.clip_path(s).write_bytes(GOOD)
    os.utime(pipe.clip_path(s), (datetime.now(timezone.utc).timestamp(),) * 2)
    lines = []
    rep = make_pipe(env, lines=lines).run(RunOptions(only={"s02"}, force={"s02"}, pause=(0, 0)))
    assert rep.done == ["s02"] and len(env.videos("s02")) == 1 and f"[s02] {WARN}" in lines


# --- blocked messages (AC10, AC29) --------------------------------------------------

def test_blocked_message_stale_has_touch_and_path(env, fake_clip_check):
    pipe = make_pipe(env)
    s = interrupted(pipe, "s02", submitted_at=stamp(-60), clip=GOOD, clip_offset=-5)
    msg = pipe._blocked_message(s, RunOptions())
    assert "clip cũ hơn lần gửi" in msg and f"touch {pipe.clip_path(s)}" in msg
    assert "--retry-unknown hoặc --force s02" in msg


def test_blocked_message_invalid(env, fake_clip_check):
    pipe = make_pipe(env)
    s = interrupted(pipe, "s02", submitted_at=stamp(-60), clip=BROKEN)
    assert "không đọc được trọn clip, có thể tải dở — tải lại từ Flow" in pipe._blocked_message(s, RunOptions())


def test_legacy_interrupted_not_auto_adopted(env, fake_clip_check):
    pipe = make_pipe(env)
    interrupted(pipe, "s02", clip=GOOD)
    assert [k for _, k in pipe.video_plan({"s02"}, set(), True)] == ["blocked"]  # not even --retry-unknown
    assert [k for _, k in pipe.video_plan({"s02"}, {"s02"}, False)] == ["send"]   # --force: explicit
    rep = make_pipe(env).run(RunOptions(only={"s02"}, retry_unknown=True, pause=(0, 0)))
    assert "--adopt s02" in rep.failed["s02"] and env.videos("s02") == []
    assert state(env, "s02")["status"] == SUBMITTING
    web = make_pipe(env)._blocked_message(scene(pipe, "s02"), RunOptions(confirm=set()))
    assert "Dùng clip này" in web and "--adopt" not in web


# --- adopt_legacy_clip --------------------------------------------------------------

def test_adopt_legacy_clip_writes_even_with_dry_gflow(env, fake_clip_check):
    pipe = Pipeline(load_storyboard(env.path), env.out, Gflow(dry_run=True, log=lambda *_: None), log=lambda *_: None)
    s = interrupted(pipe, "s02", clip=GOOD)
    reason, msg = pipe.adopt_legacy_clip(s)
    assert reason is None and msg == manual_clip.adopt_line("s02", pipe.clip_path(s))
    st = state(env, "s02")
    assert st["status"] == DONE and st["rendered_as"] == "manual" and st.get("submitted_at") is None
    assert st["video"] == str(pipe.clip_path(s)) and env.calls() == []


def test_adopt_legacy_clip_refusals(env, fake_clip_check):
    pipe = make_pipe(env)
    s1, s2 = scene(pipe, "s01"), scene(pipe, "s02")
    assert pipe.adopt_legacy_clip(s1) == ("not_adoptable", "cảnh không ở trạng thái bị ngắt")
    interrupted(pipe, "s02", submitted_at=stamp(-60), clip=GOOD)
    assert pipe.adopt_legacy_clip(s2)[0] == "not_adoptable" and "mốc gửi" in pipe.adopt_legacy_clip(s2)[1]
    interrupted(pipe, "s01")
    assert pipe.adopt_legacy_clip(s1) == ("clip_invalid", f"không thấy {pipe.clip_path(s1)}")
    interrupted(pipe, "s01", clip=BROKEN)
    assert pipe.adopt_legacy_clip(s1) == ("clip_invalid", "không đọc được trọn clip, có thể tải dở — tải lại từ Flow")
    assert state(env, "s01")["status"] == SUBMITTING


def test_adopt_legacy_clip_reason_when_file_changes_between_checks(env, fake_clip_check, monkeypatch):
    pipe = make_pipe(env)
    s = interrupted(pipe, "s02", clip=GOOD)
    verdicts = iter(["valid", "missing", "missing"])  # first check sees it, then it is gone
    monkeypatch.setattr(manual_clip, "check_file", lambda p: next(verdicts))
    assert pipe.adopt_legacy_clip(s) == ("clip_invalid", f"không thấy {pipe.clip_path(s)}")
    monkeypatch.setattr(manual_clip, "check_file", lambda p: "valid")
    monkeypatch.setattr(pipe, "_adopt_manual_clip", lambda *a, **k: False)  # changed, then fine again
    assert pipe.adopt_legacy_clip(s) == ("clip_invalid", "clip vừa thay đổi, thử lại")
    assert state(env, "s02")["status"] == SUBMITTING


def test_adopt_legacy_clip_without_ffprobe(env, no_ffprobe):
    pipe = make_pipe(env)
    s = interrupted(pipe, "s02", clip=GOOD)
    reason, msg = pipe.adopt_legacy_clip(s)
    assert reason == "clip_invalid" and "Chưa cài ffprobe" in msg and state(env, "s02")["status"] == SUBMITTING


# --- no ffprobe (AC8) ----------------------------------------------------------------

def test_no_ffprobe_never_adopts_logs_once(env, no_ffprobe):
    assert Gflow().available()  # only ffprobe is hidden, the run does not stop early
    pipe = make_pipe(env)
    interrupted(pipe, "s02", submitted_at=stamp(-60), clip=GOOD)
    interrupted(pipe, "s01", submitted_at=stamp(-60), clip=GOOD)
    lines = []
    rep = make_pipe(env, lines=lines).run(RunOptions(only={"s01", "s02"}, pause=(0, 0)))
    assert sum(NO_FFPROBE in l for l in lines) == 1
    assert set(rep.failed) == {"s01", "s02"} and env.videos("s02") == [] and env.videos("s01") == []
    assert "chưa cài ffprobe" in rep.failed["s02"]
    lines = []
    Pipeline(load_storyboard(env.path), env.out, Gflow(dry_run=True, log=lambda *_: None), log=lines.append).run(
        RunOptions(only={"s02"}))
    assert sum(NO_FFPROBE in l for l in lines) == 1  # dry-run too
    lines = []
    make_pipe(env, lines=lines).run(RunOptions(only={"s03"}, pause=(0, 0)))  # no interrupted clip in scope
    assert not any(NO_FFPROBE in l for l in lines)


# --- CLI warning (AC4, AC27, AC28) -----------------------------------------------------

def _cli(env, *args):
    return CliRunner().invoke(main, ["run", str(env.path), "--pause", "0", *args])


def _cli_pipe(env):
    return Pipeline(load_storyboard(env.path), env.path.parent / "output" / env.path.stem,
                    Gflow(log=lambda *_: None), log=lambda *_: None)


@needs_ffmpeg
def test_cli_force_interrupted_prints_credit_warning_before_video(env):
    interrupted(_cli_pipe(env), "s02", submitted_at=stamp(-60))
    res = _cli(env, "--only", "s02", "--force", "s02")
    assert res.exit_code == 0, res.output
    lines = res.output.splitlines()
    warn = next(i for i, l in enumerate(lines) if f"[s02] {WARN}" in l)
    cmd = next(i for i, l in enumerate(lines) if l.lstrip().startswith("$ ") and " video " in l and "s02" in l)
    assert warn < cmd


@needs_ffmpeg
def test_cli_retry_unknown_prints_warning_force_non_submitting_does_not(env):
    interrupted(_cli_pipe(env), "s02", submitted_at=stamp(-60))
    res = _cli(env, "--only", "s02", "--retry-unknown")
    assert res.exit_code == 0 and f"[s02] {WARN}" in res.output
    res = _cli(env, "--only", "s02", "--force", "s02")  # now DONE: plain re-render
    assert res.exit_code == 0 and WARN not in res.output


def test_cli_force_adopts_fresh_clip_prints_line(env, fake_clip_check):
    pipe = _cli_pipe(env)
    s = interrupted(pipe, "s02", submitted_at=stamp(-60), clip=GOOD)
    res = _cli(env, "--only", "s02", "--force", "s02")
    assert res.exit_code == 0, res.output
    assert manual_clip.adopt_line("s02", pipe.clip_path(s)) in res.output
    assert env.videos("s02") == [] and WARN not in res.output


# --- Flow chat clip of another scene (AC6, design-review item 2) ------------------------

@pytest.fixture
def ui(env, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_UI_CMD", f"{sys.executable} {FAKE_UI}")
    monkeypatch.setenv("FAKE_FLOW_UI_LOG", str(tmp_path / "ui.log"))
    monkeypatch.delenv("FAKE_FLOW_UI_FAIL", raising=False)
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    monkeypatch.setenv("FAKE_FLOW_UI_MEDIA", "same-uuid")  # the driver returns the same Flow video twice


@needs_ffmpeg
def test_flow_ui_duplicate_clip_moved_not_adopted_later(env, ui, fake_clip_check):
    rep = make_pipe(env).run(RunOptions(pause=(0, 0)))
    clips = env.out / "clips"
    assert "trùng với cảnh s01" in rep.failed["s03"] and ".dup-" in rep.failed["s03"]
    assert not (clips / "s03.mp4").exists() and (clips / "s03.dup-same-uuid.mp4").is_file()
    st = state(env, "s03")
    assert st["status"] == SUBMITTING and manual_clip.parse_stamp(st["submitted_at"]) is not None
    pipe = make_pipe(env)
    s3 = scene(pipe, "s03")
    assert pipe.manual_clip_state(s3) is None
    assert [k for _, k in pipe.video_plan({"s03"}, set(), False)] == ["blocked"]


@needs_ffmpeg
def test_flow_ui_duplicate_clip_deleted_when_it_cannot_be_moved(env, ui, fake_clip_check, monkeypatch):
    real = Path.replace

    def replace(self, target):
        if ".dup-" in str(target):  # only the move of the duplicate; State.save keeps working
            raise OSError("disk full")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", replace)
    rep = make_pipe(env).run(RunOptions(pause=(0, 0)))
    assert "đã xoá" in rep.failed["s03"] and not (env.out / "clips" / "s03.mp4").exists()
    assert state(env, "s03")["status"] == SUBMITTING


@needs_ffmpeg
@pytest.mark.parametrize("where", ["other-scene-clip", "outside-clips"])
def test_flow_ui_duplicate_never_moves_a_file_that_is_not_this_scenes(env, ui, fake_clip_check, monkeypatch,
                                                                      where):
    """The driver's local_path is another scene's clip, or a file outside clips/: it is left alone."""
    real = Gflow.ui_image_to_video
    outside = env.out.parent / "elsewhere.mp4"

    def ui_video(self, image, prompt, **kw):
        video, media = real(self, image, prompt, **kw)
        if Path(kw["out"]).stem != "s03":
            return video, media
        if where == "outside-clips":
            shutil.copy(video, outside)
            return outside, media
        return Path(kw["out"]).with_name("s01.mp4"), media

    monkeypatch.setattr(Gflow, "ui_image_to_video", ui_video)
    rep = make_pipe(env).run(RunOptions(pause=(0, 0)))
    assert "trùng với cảnh s01" in rep.failed["s03"] and "KHÔNG dời file" in rep.failed["s03"]
    assert (env.out / "clips" / "s01.mp4").is_file() and state(env, "s01")["status"] == DONE
    assert not list((env.out / "clips").glob("*.dup-*"))
    if where == "outside-clips":
        assert outside.is_file()


# --- CLI --adopt (AC10, AC11, AC29; T3) ---------------------------------------------------

def _cli_state(env, sid):
    return json.loads((env.path.parent / "output" / env.path.stem / "state.json").read_text())["scenes"].get(sid, {})


def test_cli_legacy_blocked_message_points_to_adopt_not_retry_unknown(env, fake_clip_check):
    interrupted(_cli_pipe(env), "s02", clip=GOOD)
    res = _cli(env, "--only", "s02", "--retry-unknown")
    assert res.exit_code == 1 and env.videos("s02") == []
    line = next(l for l in res.output.splitlines() if "✘ s02:" in l)
    assert "--adopt s02" in line and "--force s02" in line and "--retry-unknown" not in line


def test_cli_adopt_legacy_clip(env, fake_clip_check):
    pipe = _cli_pipe(env)
    s = interrupted(pipe, "s02", clip=GOOD)
    res = _cli(env, "--only", "s02", "--adopt", "s02")
    assert res.exit_code == 0, res.output
    assert manual_clip.adopt_line("s02", pipe.clip_path(s)) in res.output
    st = _cli_state(env, "s02")
    assert st["status"] == DONE and st["rendered_as"] == "manual" and st.get("submitted_at") is None
    assert env.calls() == [] or env.videos("s02") == []


def test_cli_make_accepts_adopt(env, fake_clip_check):
    interrupted(_cli_pipe(env), "s02", clip=GOOD)
    res = CliRunner().invoke(main, ["make", str(env.path), "--dry-run", "--only", "s02", "--adopt", "s02"])
    assert res.exit_code == 0, res.output
    assert "--adopt s02: (dry-run) không ghi gì" in res.output


def test_cli_adopt_invalid_exit1(env, fake_clip_check):
    interrupted(_cli_pipe(env), "s02", clip=BROKEN)
    res = _cli(env, "--only", "s02", "--adopt", "s02")
    assert res.exit_code == 1
    assert "--adopt s02: không dùng được clip (không đọc được trọn clip, có thể tải dở — tải lại từ Flow)" in res.output
    assert _cli_state(env, "s02")["status"] == SUBMITTING and env.videos("s02") == []


def test_cli_adopt_without_ffprobe_exit1(env, no_ffprobe):
    interrupted(_cli_pipe(env), "s02", clip=GOOD)
    res = _cli(env, "--only", "s02", "--adopt", "s02")
    assert res.exit_code == 1 and "Chưa cài ffprobe" in res.output
    assert _cli_state(env, "s02")["status"] == SUBMITTING and env.videos("s02") == []


@pytest.mark.parametrize("only", [True, False], ids=["only-s02", "all-scenes"])
def test_cli_adopt_broken_clip_with_retry_unknown_not_resent(env, fake_clip_check, only):
    """--adopt s02 --retry-unknown with a broken clip: --retry-unknown would resend an "invalid" clip's scene,
    but the failed --adopt keeps s02 out of this run (no second bill)."""
    interrupted(_cli_pipe(env), "s02", clip=BROKEN)
    args = (["--only", "s02"] if only else []) + ["--adopt", "s02", "--retry-unknown"]
    res = _cli(env, *args)
    assert res.exit_code == 1
    assert env.videos("s02") == [] and WARN not in res.output
    assert "✘ s02: --adopt: không dùng được clip" in res.output
    assert _cli_state(env, "s02")["status"] == SUBMITTING


def test_cli_adopt_without_ffprobe_with_retry_unknown_not_resent(env, no_ffprobe):
    interrupted(_cli_pipe(env), "s02", clip=GOOD)
    res = _cli(env, "--only", "s02", "--adopt", "s02", "--retry-unknown")
    assert res.exit_code == 1 and "Chưa cài ffprobe" in res.output
    assert env.videos("s02") == [] and _cli_state(env, "s02")["status"] == SUBMITTING


@needs_ffmpeg
def test_cli_adopt_not_legacy_skipped(env, fake_clip_check):
    pipe = _cli_pipe(env)
    interrupted(pipe, "s03", submitted_at=stamp(-60))
    res = _cli(env, "--only", "s02", "--adopt", "s01", "--adopt", "s03")
    assert res.exit_code == 0, res.output
    assert "--adopt s01: bỏ qua (cảnh không ở trạng thái bị ngắt)" in res.output
    assert "--adopt s03: bỏ qua (cảnh đã có mốc gửi, app tự nhận clip mới khi chạy)" in res.output
    assert _cli_state(env, "s01") == {} and _cli_state(env, "s03")["status"] == SUBMITTING


def test_cli_adopt_unknown_id(env):
    res = _cli(env, "--adopt", "s99")
    assert res.exit_code != 0 and "không có cảnh: s99" in res.output and env.calls() == []


def test_cli_adopt_dry_run_writes_nothing(env, fake_clip_check):
    interrupted(_cli_pipe(env), "s02", clip=GOOD)
    res = _cli(env, "--dry-run", "--only", "s02", "--adopt", "s02")
    assert res.exception is None or isinstance(res.exception, SystemExit), res.output  # still blocked in the preview
    assert "--adopt s02: (dry-run) không ghi gì" in res.output
    assert _cli_state(env, "s02")["status"] == SUBMITTING and env.videos("s02") == []


def test_cli_adopt_and_force_same_scene_refused(env, fake_clip_check):
    interrupted(_cli_pipe(env), "s02", clip=GOOD)
    res = _cli(env, "--only", "s02", "--adopt", "s02", "--force", "s02")
    assert res.exit_code != 0 and "--adopt và --force cho s02" in res.output
    assert env.calls() == [] and _cli_state(env, "s02")["status"] == SUBMITTING


# --- QA (T5): gaps found when mapping AC1–AC36 to tests ------------------------------------

@needs_ffmpeg
def test_rerender_done_billed_leaves_old_clip_not_adopted(env, monkeypatch, fake_clip_check):
    """AC5 end to end: DONE scene, "Render lại" (force), the video call is billed and fails, the old
    clips/s02.mp4 is still there. Later runs never adopt it; only an explicit resend sends again."""
    out = env.path.parent / "output" / env.path.stem  # same folder as the CLI
    pipe = make_pipe(env, out=out)
    s = scene(pipe, "s02")
    clip = pipe.clip_path(s)
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(GOOD)
    old = datetime.now(timezone.utc).timestamp() - 10
    os.utime(clip, (old, old))
    pipe.state.update("s02", status=DONE, video=str(clip), fingerprint=pipe.sb.scene_fingerprint(s))
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video t2v:9:false")
    monkeypatch.setenv("FAKE_GFLOW_HINT", BILLED)
    make_pipe(env, out=out).run(RunOptions(only={"s02"}, force={"s02"}, pause=(0, 0)))
    monkeypatch.delenv("FAKE_GFLOW_FAIL")
    st = _cli_state(env, "s02")
    assert st["status"] == SUBMITTING and manual_clip.parse_stamp(st["submitted_at"]) >= old + 2
    sent = len(env.videos("s02"))
    p2 = make_pipe(env, out=out)
    assert p2.manual_clip_state(s) == "stale"
    assert [k for _, k in p2.video_plan({"s02"}, set(), False)] == ["blocked"]
    rep = p2.run(RunOptions(only={"s02"}, force={"s02"}, confirm=set(), pause=(0, 0)))  # web, not confirmed
    assert "s02" in rep.failed and len(env.videos("s02")) == sent
    res = _cli(env, "--only", "s02")  # CLI without --force/--retry-unknown
    assert res.exit_code == 1 and len(env.videos("s02")) == sent and _cli_state(env, "s02")["status"] == SUBMITTING
    res = _cli(env, "--only", "s02", "--force", "s02")
    assert res.exit_code == 0, res.output
    assert f"[s02] {WARN}" in res.output and len(env.videos("s02")) == sent + 1
    st = _cli_state(env, "s02")
    assert st["status"] == DONE and st.get("rendered_as") != "manual"


@pytest.mark.parametrize("case", ["ok", "stale", "invalid", "legacy", "no-file", "done", "failed"])
def test_has_manual_clip_matches_adopt_rule(env, fake_clip_check, case):
    """AC9: has_manual_clip, video_plan "adopt" and _adopt_manual_clip agree on every case."""
    pipe = make_pipe(env)
    s = scene(pipe, "s02")
    if case in ("ok", "stale", "invalid"):
        interrupted(pipe, "s02", submitted_at=stamp(-60), clip=BROKEN if case == "invalid" else GOOD,
                    clip_offset=-5 if case == "stale" else 5)
    elif case == "legacy":
        interrupted(pipe, "s02", clip=GOOD)
    elif case == "no-file":
        interrupted(pipe, "s02", submitted_at=stamp(-60))
    else:
        pipe.clip_path(s).parent.mkdir(parents=True, exist_ok=True)
        pipe.clip_path(s).write_bytes(GOOD)
        pipe.state.update("s02", status=DONE if case == "done" else FAILED, submitted_at=stamp(-60))
    adopt = case == "ok"
    assert pipe.has_manual_clip(s) is adopt
    for force in (set(), {"s02"}):
        for confirm in (None, set(), {"s02"}):
            kind = pipe.video_plan({"s02"}, force, True, confirm=confirm)[0][1]
            assert (kind == "adopt") is adopt, (force, confirm, kind)
    before = pipe.state.scene("s02").get("status")
    assert pipe._adopt_manual_clip(s, dry=False) is adopt
    assert pipe.state.scene("s02").get("status") == (DONE if adopt else before)


def test_blocked_message_without_clip_names_the_clip_path(env):
    """AC29: even with no clip, the message says where to save the clip and how to resend."""
    pipe = make_pipe(env)
    s = interrupted(pipe, "s02", submitted_at=stamp(-60))
    cli_msg = pipe._blocked_message(s, RunOptions())
    assert "bị ngắt" in cli_msg and "có thể đã trừ credit" in cli_msg
    assert str(pipe.clip_path(s)) in cli_msg and "--retry-unknown hoặc --force s02" in cli_msg
    web_msg = pipe._blocked_message(s, RunOptions(confirm=set()))
    assert str(pipe.clip_path(s)) in web_msg and "tick" in web_msg and "--retry-unknown" not in web_msg


def test_cli_no_ffprobe_prints_one_line(env, no_ffprobe):
    """AC8 on the CLI: one line per run, only when an interrupted scene in scope has a clip."""
    pipe = _cli_pipe(env)
    interrupted(pipe, "s01", submitted_at=stamp(-60), clip=GOOD)
    interrupted(pipe, "s02", submitted_at=stamp(-60), clip=GOOD)
    res = _cli(env, "--only", "s01", "--only", "s02")
    assert res.exit_code == 1
    assert res.output.count(NO_FFPROBE) == 1
    assert env.videos("s01") == [] and env.videos("s02") == []
    res = _cli(env, "--only", "s03")
    assert NO_FFPROBE not in res.output


def test_cli_make_legacy_clip_not_adopted_even_with_retry_unknown(env, fake_clip_check):
    """AC10 for `make`: the legacy interrupted scene is not adopted; the message points to --adopt."""
    interrupted(_cli_pipe(env), "s02", clip=GOOD)
    res = CliRunner().invoke(main, ["make", str(env.path), "--pause", "0", "--only", "s02", "--retry-unknown"])
    assert res.exit_code != 0
    assert "--adopt s02" in res.output and env.videos("s02") == []
    assert _cli_state(env, "s02")["status"] == SUBMITTING


def test_cli_make_force_interrupted_prints_credit_warning(env):
    """AC27 for `make`: same warning line before the video command."""
    interrupted(_cli_pipe(env), "s02", submitted_at=stamp(-60))
    res = CliRunner().invoke(main, ["make", str(env.path), "--pause", "0", "--only", "s02", "--force", "s02",
                                    "--allow-missing"])
    lines = res.output.splitlines()
    warn = next(i for i, l in enumerate(lines) if f"[s02] {WARN}" in l)
    cmd = next(i for i, l in enumerate(lines) if l.lstrip().startswith("$ ") and " video " in l and "s02" in l)
    assert warn < cmd
