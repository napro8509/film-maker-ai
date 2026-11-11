"""The film always has three subtitle tracks: Vietnamese, Thai and English."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from filmmaker import planner
from filmmaker.assemble import assemble, subtitle_paths, subtitle_track_args, write_subtitle_tracks
from filmmaker.storyboard import Storyboard, merge_subtitles_raw, subtitle_code


def _sb(**kw) -> Storyboard:
    data = {"title": "Mèo", "scenes": [
        {"id": "s01", "prompt": "a cat walks", "narration": "Xin chào",
         "subtitles": {"vi": "Xin chào", "th": "สวัสดี", "en": "Hello"}},
        {"id": "s02", "prompt": "a cat sleeps", "narration": "Ngủ ngon"},
        {"id": "s03", "prompt": "a cat eats"},
    ]}
    data.update(kw)
    return Storyboard.model_validate(data)


def test_subtitle_code():
    assert [subtitle_code(x) for x in ("Vietnamese", "vi", "Tiếng Việt", "THAI", "ไทย", "English", "eng")] == [
        "vi", "vi", "vi", "th", "th", "en", "en"]
    assert subtitle_code("French") is None and subtitle_code(None) is None


def test_lines_fall_back_to_narration():
    sb = _sb()
    s1, s2, s3 = sb.scenes
    assert [sb.subtitle_line(s1, c) for c in ("vi", "th", "en")] == ["Xin chào", "สวัสดี", "Hello"]
    assert [sb.subtitle_line(s2, c) for c in ("vi", "th", "en")] == ["Ngủ ngon"] * 3
    assert sb.subtitle_line(s3, "th") is None
    assert sb.missing_subtitles() == {"s02": ["th", "en"]}


def test_primary_track():
    assert _sb().primary_subtitle_code() == "vi"
    assert _sb(subtitle_language="Thai").primary_subtitle_code() == "th"
    sb = _sb(scenes=[{"id": "s01", "prompt": "x", "narration": "Hello", "subtitles": {"vi": "Chào", "en": "Hello"}}])
    assert sb.primary_subtitle_code() == "en"  # narration matches the English line
    # the primary track is narration itself, even if its own line is stale
    sb = _sb(scenes=[{"id": "s01", "prompt": "x", "narration": "Chào bạn", "subtitles": {"vi": "Chào"}}])
    assert sb.subtitle_line(sb.scenes[0], "vi") == "Chào bạn"


def test_merge_raw_keeps_existing_lines():
    raw = {"scenes": [{"id": "s01", "subtitles": {"th": "เดิม"}}, {"id": "s02"}]}
    assert merge_subtitles_raw(raw, {"s01": {"th": "ใหม่", "en": "Hi"}, "s02": {"en": "Night"}})
    assert raw["scenes"][0]["subtitles"] == {"th": "เดิม", "en": "Hi"}
    assert raw["scenes"][1]["subtitles"] == {"en": "Night"}
    assert not merge_subtitles_raw(raw, {"s02": {"en": "Other"}})


def test_translate_fills_missing(monkeypatch):
    sent = {}

    def fake(lines, **kw):
        sent.update(lines)
        return {"s02": {"id": "s02", "vi": "Ngủ ngon", "th": "ฝันดี", "en": " Good  night "}}

    monkeypatch.setattr(planner, "_translate_items", fake)
    sb = _sb()
    assert planner.translate_subtitles(sb) == {"s02": {"th": "ฝันดี", "en": "Good night"}}
    assert sent == {"s02": "Ngủ ngon"}
    assert sb.missing_subtitles() == {}
    assert planner.translate_subtitles(sb) == {}  # nothing left: no Claude call


def test_write_tracks(tmp_path):
    out = tmp_path / "final.mp4"
    tracks = write_subtitle_tracks(_sb(), [("s01", 0.2, 3.8), ("s02", 4.2, 7.8), ("s03", 8.2, 11.8)], out,
                                   log=lambda *_: None)
    assert list(tracks) == ["vi", "th", "en"]
    assert "สวัสดี" in tracks["th"].read_text(encoding="utf-8")
    assert "Hello" in tracks["en"].read_text(encoding="utf-8")
    assert out.with_suffix(".srt").read_text(encoding="utf-8") == tracks["vi"].read_text(encoding="utf-8")
    # no narration any more: the stale files go away
    write_subtitle_tracks(_sb(scenes=[{"id": "s01", "prompt": "x"}]), [("s01", 0.2, 3.8)], out, log=lambda *_: None)
    assert not any(p.exists() for p in subtitle_paths(out).values()) and not out.with_suffix(".srt").exists()


def test_track_args(tmp_path):
    ins, outs = subtitle_track_args({"vi": tmp_path / "a.srt", "th": tmp_path / "b.srt"}, 3)
    assert ins.count("-i") == 2
    assert outs[:6] == ["-map", "3:0", "-metadata:s:s:0", "language=vie", "-metadata:s:s:0", "title=Tiếng Việt"]
    assert "4:0" in outs and "language=tha" in outs and outs[-2:] == ["-c:s", "mov_text"]
    assert subtitle_track_args({}, 3) == ([], [])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_assemble_embeds_three_tracks(tmp_path):
    clips = []
    for sid in ("s01", "s02"):
        p = tmp_path / f"{sid}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=1.5",
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "1.5", "-shortest", str(p)], check=True)
        clips.append((sid, p))
    sb = _sb(scenes=_sb().model_dump(exclude_none=True)["scenes"][:2])
    out = assemble(sb, clips, tmp_path / "final.mp4", burn_subtitles=True, burn_language="th", log=lambda *_: None)
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries",
                                       "stream_tags=language", "-of", "json", str(out)],
                                      capture_output=True, text=True, check=True).stdout)
    assert [s["tags"]["language"] for s in probe["streams"]] == ["vie", "tha", "eng"]


def test_old_language_name_keys_become_track_codes(tmp_path):
    """Storyboards saved with subtitles keyed by language name ({"Thai": ...}) still load, as track codes."""
    from filmmaker.storyboard import load_storyboard, merge_subtitles_raw

    p = tmp_path / "storyboard.yaml"
    p.write_text(
        "title: t\nsubtitle_language: Vietnamese\nsubtitle_languages: [Vietnamese, Thai, Japanese]\n"
        "scenes:\n- id: s01\n  prompt: p\n  narration: Xin chào\n"
        "  subtitles: {Thai: สวัสดี, Japanese: こんにちは}\n", encoding="utf-8")
    sb = load_storyboard(p)
    sc = sb.scenes[0]
    assert sc.subtitles.th == "สวัสดี" and sc.subtitles.vi is None and sc.subtitles.en is None
    assert sb.primary_subtitle_code() == "vi"
    assert sb.missing_subtitles() == {"s01": ["en"]}
    raw = {"scenes": [{"id": "s01", "subtitles": {"Thai": "สวัสดี"}}]}
    assert merge_subtitles_raw(raw, {"s01": {"en": "Hello", "th": "other"}})
    assert raw["scenes"][0]["subtitles"] == {"th": "สวัสดี", "en": "Hello"}
