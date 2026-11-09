import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from filmmaker.gflow import Gflow
from filmmaker.pipeline import Pipeline, RunOptions
from filmmaker.storyboard import NO_TEXT, load_storyboard
from filmmaker.thumbnail import find_font, render_cover, wrap

FAKE = Path(__file__).parent / "fake_gflow.py"
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

STORY = """
title: Chiếc vòng cổ
aspect: "16:9"
style: "cinematic"
characters:
  - name: Mali
    description: "young Thai woman, mint blouse"
    image: mali.png
  - name: Ratana
    description: "stern older woman, burgundy silk"
thumbnail:
  text: "Chiếc vòng cổ của mẹ"
scenes:
  - id: s01
    mode: t2v
    prompt: "Mali cries"
"""


def _size(path: Path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return s["width"], s["height"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1",
                    str(tmp_path / "mali.png")], check=True)
    sb_path = tmp_path / "story.yaml"
    sb_path.write_text(STORY)
    log = tmp_path / "calls.log"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(log))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)
    calls = lambda: [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return sb_path, calls


def pipe(sb_path, out):
    return Pipeline(load_storyboard(sb_path), out, Gflow(log=lambda *_: None), log=lambda *_: None, sleep=lambda *_: None)


def test_wrap_keeps_lines_short_and_count_bounded():
    assert wrap("Chiếc vòng cổ của mẹ", 18) == ["Chiếc vòng cổ của", "mẹ"]
    assert wrap("short", 18) == ["short"]
    lines = wrap("สร้อยของแม่ที่หายไปในคืนฝนตกหนัก", 12)
    assert all(len(l) <= 12 for l in lines[:-1]) and "".join(lines).replace(" ", "") == "สร้อยของแม่ที่หายไปในคืนฝนตกหนัก"
    assert len(wrap("a b c d e f g h i j k l m n o p q r s t u v w x y z " * 3, 10)) == 3


def test_render_cover_crops_and_prints_title(tmp_path):
    src = tmp_path / "raw.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=1600x1000", "-frames:v", "1",
                    str(src)], check=True)
    if not find_font("Chiếc vòng cổ"):
        pytest.skip("no font")
    out = render_cover(src, tmp_path / "t.jpg", aspect="16:9", text="Chiếc vòng cổ của mẹ", log=lambda *_: None)
    assert _size(out) == (1280, 720)
    plain = render_cover(src, tmp_path / "p.jpg", aspect="9:16", text=None, log=lambda *_: None)
    assert _size(plain) == (720, 1280)
    assert out.read_bytes() != render_cover(src, tmp_path / "n.jpg", aspect="16:9", text="", log=lambda *_: None).read_bytes()


def test_make_thumbnail_uses_character_reference_and_is_reused(env, tmp_path):
    sb_path, calls = env
    out = tmp_path / "out"
    p = pipe(sb_path, out)
    final = p.make_thumbnail(RunOptions(pause=(0, 0)))
    assert final.is_file() and _size(final) == (1280, 720)
    kinds = [c[:2] for c in calls()]
    assert kinds == [["project", "create"], ["image", "i2i"]]  # Mali has an image; Ratana has none
    i2i = calls()[1]
    assert i2i[i2i.index("--ref") + 1] == str(tmp_path / "mali.png")
    assert NO_TEXT in i2i[2] and "Mali" in i2i[2] and "Chiếc vòng" not in i2i[2]  # the title is never drawn by AI

    # New title only: no new picture.
    n = len(calls())
    sb_path.write_text(STORY.replace("Chiếc vòng cổ của mẹ", "Ai đã lấy chiếc vòng?"))
    pipe(sb_path, out).make_thumbnail(RunOptions())
    assert len(calls()) == n
    # Asked for a new picture, or the picture prompt changed: Flow again.
    pipe(sb_path, out).make_thumbnail(RunOptions(), remake=True)
    sb_path.write_text(STORY.replace('text: "Chiếc vòng cổ của mẹ"', 'text: "x"\n  prompt: "Ratana glaring"'))
    p = pipe(sb_path, out)
    assert not p.has_thumbnail_image()
    p.make_thumbnail(RunOptions())
    assert [c[:2] for c in calls()[n:]] == [["image", "i2i"], ["image", "t2i"]]  # Ratana has no reference image


def test_default_thumbnail_prompt_names_characters(env):
    sb = load_storyboard(env[0])
    tp = sb.thumbnail_prompt()
    assert "Mali" in tp and "Ratana" in tp and "mint blouse" in tp and "cinematic" in tp


def test_thai_wrap_keeps_leading_vowels_and_marks_with_their_letter():
    assert wrap("ความลับของกำไลที่ไม่มีใครรู้", 18) == ["ความลับของกำไลที่", "ไม่มีใครรู้"]
    for line in wrap("ที่ไม่มีใครรู้เลยแม้แต่คนเดียว", 7):
        assert line[-1] not in "เแโใไ" and not line[0] in "่้๊๋ิีึืุู"


def test_newer_ffmpeg_without_text_shaping_switch_still_shapes(monkeypatch):
    from filmmaker import thumbnail

    class R:
        def __init__(self, out): self.stdout = out

    def fake_run(cmd, **kw):
        return R("fontfile textfile") if "-h" in cmd else R("configuration: --enable-libharfbuzz --enable-libfreetype")

    monkeypatch.setattr(thumbnail.subprocess, "run", fake_run)
    assert thumbnail._drawtext_caps("ffmpeg") == (True, True, False)
