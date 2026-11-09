"""filmmaker.manual_clip: ffprobe check of a hand-downloaded clip, cache, submitted_at stamps (resend-credit-guard T1)."""
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from filmmaker import manual_clip
from tests.model_helpers import make_mp4, set_scene, stamp, truncate_half, write_project

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="needs ffmpeg + ffprobe")


# --- stamps -------------------------------------------------------------------

def test_stamp_now_is_utc_rounded_down_to_the_second():
    before = int(datetime.now(timezone.utc).timestamp())
    s = manual_clip.stamp_now()
    after = datetime.now(timezone.utc).timestamp()
    assert len(s) == 20 and s.endswith("Z") and "." not in s
    assert before <= manual_clip.parse_stamp(s) <= after


@pytest.mark.parametrize("value,expected", [
    ("2026-11-09T08:15:42Z", datetime(2026, 11, 9, 8, 15, 42, tzinfo=timezone.utc).timestamp()),
    ("2026-11-09T08:15:42+00:00", datetime(2026, 11, 9, 8, 15, 42, tzinfo=timezone.utc).timestamp()),
    ("2026-11-09T10:15:42+02:00", datetime(2026, 11, 9, 8, 15, 42, tzinfo=timezone.utc).timestamp()),
    (None, None), ("", None), ("garbage", None), (12345, None),
])
def test_parse_stamp(value, expected):
    assert manual_clip.parse_stamp(value) == expected


def test_adopt_line_is_the_spec_sentence():
    assert manual_clip.adopt_line("s02", Path("out/clips/s02.mp4")) == (
        "[s02] dùng clip bạn đã tải về (out/clips/s02.mp4) thay vì gửi lại; xoá file này nếu muốn render lại")


# --- check_file / cache ---------------------------------------------------------

def test_check_file_order_and_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(manual_clip, "probe_clip", lambda p: calls.append(p) or True)
    monkeypatch.setattr(manual_clip, "ffprobe_available", lambda: True)
    clip = tmp_path / "s01.mp4"
    assert manual_clip.check_file(clip) == "missing"
    clip.write_bytes(b"\0" * (manual_clip.MIN_BYTES - 1))
    assert manual_clip.check_file(clip) == "invalid" and calls == []  # too small: ffprobe not even run
    clip.write_bytes(b"\0" * manual_clip.MIN_BYTES)
    assert manual_clip.check_file(clip) == "valid"
    assert manual_clip.check_file(clip) == "valid" and len(calls) == 1  # cached
    st = clip.stat()
    os.utime(clip, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert manual_clip.check_file(clip) == "valid" and len(calls) == 2  # new mtime: checked again
    manual_clip.clear_cache()
    assert manual_clip.check_file(clip) == "valid" and len(calls) == 3


def test_check_file_without_ffprobe_is_unchecked(tmp_path, no_ffprobe, monkeypatch):
    monkeypatch.setattr(manual_clip, "probe_clip", lambda p: pytest.fail("ffprobe must not run"))
    clip = tmp_path / "s01.mp4"
    clip.write_bytes(b"\0" * 20_000)
    assert manual_clip.ffprobe_available() is False
    assert manual_clip.check_file(clip) == "unchecked"
    assert manual_clip.check_file(tmp_path / "none.mp4") == "missing"


def _fake_run(stdout: str = "", exc: Exception | None = None, seen: dict | None = None):
    def run(cmd, **kw):
        if seen is not None:
            seen.update(kw, cmd=cmd)
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
    return run


def _probe_json(fmt="4.0", stream="4.0", pts=(0.0, 1.0, 3.9), streams=True) -> str:
    st = [{"index": 0, **({"duration": stream} if stream is not None else {})}] if streams else []
    return json.dumps({"streams": st, "packets": [{"pts_time": str(p)} for p in pts], "format": {"duration": fmt}})


@pytest.mark.parametrize("stdout,ok", [
    (_probe_json(), True),
    (_probe_json(pts=(0.0, 3.5)), True),                     # within TAIL_SLACK of the end
    (_probe_json(pts=(0.0, 1.9)), False),                    # packets stop early: cut-off download
    (_probe_json(pts=(3.9, 0.0, 1.0)), True),                # max, not the last packet (B-frames)
    (_probe_json(pts=()), False),
    (_probe_json(streams=False), False),                     # no video stream
    (_probe_json(fmt="N/A"), False),
    (_probe_json(fmt="0"), False),
    (_probe_json(fmt="6.0", stream=None, pts=(0.0, 3.9)), False),  # no stream duration: compare with format
    (_probe_json(fmt="6.0", stream="4.0", pts=(0.0, 3.9)), True),  # longer audio does not matter
    ("not json", False),
    (_probe_json(pts=(0.0, "N/A", 3.9)), True),              # a packet without pts is skipped, not fatal
    (_probe_json(pts=("N/A", "N/A")), False),                # but no packet with a pts: invalid
])
def test_probe_clip_rules(tmp_path, monkeypatch, stdout, ok):
    seen = {}
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout, seen=seen))
    assert manual_clip.probe_clip(tmp_path / "x.mp4") is ok
    assert seen["timeout"] == manual_clip.PROBE_TIMEOUT and seen["check"] is True
    assert "packet=pts_time" in " ".join(seen["cmd"]) and "format=duration" in " ".join(seen["cmd"])


def test_probe_clip_reads_only_a_local_mp4(tmp_path, monkeypatch):
    """Hardening: the clip comes from outside, so no playlist/concat demuxer and no protocol but file."""
    seen = {}
    monkeypatch.setattr(subprocess, "run", _fake_run(_probe_json(), seen=seen))
    clip = tmp_path / "-s01.mp4"
    assert manual_clip.probe_clip(clip) is True
    cmd = seen["cmd"]
    assert cmd[cmd.index("-protocol_whitelist") + 1] == "file" and cmd[cmd.index("-f") + 1] == "mov"
    assert cmd[-1] == "file:" + str(clip)
    assert cmd.index("-f") < len(cmd) - 1  # options come before the input


@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired(["ffprobe"], 60),
                                 subprocess.CalledProcessError(1, ["ffprobe"]), OSError("boom")])
def test_probe_clip_errors_are_invalid(tmp_path, monkeypatch, exc):
    monkeypatch.setattr(subprocess, "run", _fake_run(exc=exc))
    assert manual_clip.probe_clip(tmp_path / "x.mp4") is False


# --- real ffprobe (AC7) -----------------------------------------------------------

@needs_ffmpeg
def test_probe_real_concat_playlist_is_invalid(tmp_path):
    """A text playlist named .mp4 that points at a good clip is not read through it (forced mov demuxer)."""
    make_mp4(tmp_path / "full.mp4", faststart=True)
    fake = tmp_path / "s01.mp4"
    fake.write_text("ffconcat version 1.0\nfile full.mp4\n")
    assert manual_clip.probe_clip(fake) is False


@needs_ffmpeg
@pytest.mark.parametrize("faststart", [False, True], ids=["moov-at-end", "faststart"])
def test_probe_real_mp4_full_valid_truncated_invalid(tmp_path, faststart):
    full = make_mp4(tmp_path / "full.mp4", faststart=faststart)
    assert full.stat().st_size >= manual_clip.MIN_BYTES
    assert manual_clip.probe_clip(full) is True
    assert manual_clip.check_file(full) == "valid"
    cut = make_mp4(tmp_path / "cut.mp4", faststart=faststart)
    truncate_half(cut)
    assert cut.stat().st_size >= manual_clip.MIN_BYTES  # so it is ffprobe that says no, not the size rule
    assert manual_clip.probe_clip(cut) is False
    assert manual_clip.check_file(cut) == "invalid"


@needs_ffmpeg
@pytest.mark.parametrize("faststart", [False, True], ids=["moov-at-end", "faststart"])
def test_real_truncated_clip_not_adopted_full_clip_adopted(tmp_path, faststart):
    """AC7 through the pipeline, real ffprobe: a cut-off download is never "adopt" nor DONE; the full file is."""
    full = make_mp4(tmp_path / "full.mp4", faststart=faststart).read_bytes()
    cut = make_mp4(tmp_path / "cut.mp4", faststart=faststart)
    truncate_half(cut)
    p = write_project(tmp_path / "ws", "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}])
    set_scene(p, "s01", "interrupted", manual_clip=True, submitted_at=stamp(-60), clip_bytes=full)
    pipe = set_scene(p, "s02", "interrupted", manual_clip=True, submitted_at=stamp(-60), clip_bytes=cut.read_bytes())
    s1, s2 = pipe.sb.scenes
    assert pipe.clip_path(s2).stat().st_size >= manual_clip.MIN_BYTES
    assert [(s.id, k) for s, k in pipe.video_plan(None, set(), False)] == [("s01", "adopt"), ("s02", "blocked")]
    assert [k for _, k in pipe.video_plan({"s02"}, {"s02"}, False, confirm=set())] == ["blocked"]
    assert pipe.manual_clip_state(s2) == "invalid" and not pipe.has_manual_clip(s2)
    assert pipe._adopt_manual_clip(s2, dry=False) is False
    assert pipe._adopt_manual_clip(s1, dry=False) is True
    assert pipe.state.scene("s01")["status"] == "done" and pipe.state.scene("s01")["rendered_as"] == "manual"
    assert pipe.state.scene("s02")["status"] == "submitting"
