"""A clip from Flow must start on the picture we attached; one that starts on another picture is set aside."""
import shutil
import subprocess

import pytest

from filmmaker import flow_ui

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def _ff(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture
def media(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    _ff("-f", "lavfi", "-i", "testsrc2=size=640x360", "-frames:v", "1", str(a))
    _ff("-f", "lavfi", "-i", "mandelbrot=size=640x360", "-frames:v", "1", str(b))
    clip = tmp_path / "s07.mp4"  # 2 s video that starts on picture a
    _ff("-loop", "1", "-i", str(a), "-t", "2", "-vf", "scale=1280:720,format=yuv420p", str(clip))
    return a, b, clip


@needs_ffmpeg
def test_frame_match_tells_right_from_wrong_picture(media):
    a, b, clip = media
    assert flow_ui.frame_match(a, clip) > 0.95
    assert flow_ui.frame_match(b, clip) < flow_ui.START_MATCH_MIN


@needs_ffmpeg
def test_right_clip_is_kept(media):
    a, _, clip = media
    flow_ui.check_start_frame(a, clip)
    assert clip.is_file()


@needs_ffmpeg
def test_wrong_clip_is_set_aside_and_fails_as_spent(media):
    _, b, clip = media
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui.check_start_frame(b, clip)
    assert ei.value.code == flow_ui.EXIT_GENERATION_FAILED and ei.value.spent is True
    assert not clip.exists() and (clip.parent / "s07.mismatch.mp4").is_file()
    assert "nhầm ảnh" in str(ei.value)


def test_unreadable_media_is_not_judged(tmp_path):
    (tmp_path / "x.png").write_bytes(b"not a picture")
    (tmp_path / "s01.mp4").write_bytes(b"not a video")
    assert flow_ui.frame_match(tmp_path / "x.png", tmp_path / "s01.mp4") is None
    flow_ui.check_start_frame(tmp_path / "x.png", tmp_path / "s01.mp4")  # unknown: keep the clip
    assert (tmp_path / "s01.mp4").is_file()


def test_upload_names_are_unique():
    from pathlib import Path

    names = {flow_ui.upload_name(Path("/k/s07.png")) for _ in range(50)}
    assert len(names) == 50 and all(n.startswith("fm-s07-") and n.endswith(".png") for n in names)
