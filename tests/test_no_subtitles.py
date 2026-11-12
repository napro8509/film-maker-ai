"""“Không phụ đề”: a film made with subtitle_language "none" has no subtitles anywhere."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from filmmaker import planner
from filmmaker.assemble import assemble, subtitle_paths, write_subtitle_tracks
from filmmaker.storyboard import NO_SUBTITLES, Storyboard

ROOT = Path(__file__).resolve().parents[1]


def _sb(**kw) -> Storyboard:
    data = {"title": "Mèo", "subtitle_language": "none", "scenes": [
        {"id": "s01", "prompt": "a cat walks", "narration": "Xin chào",
         "subtitles": {"vi": "Xin chào", "th": "สวัสดี", "en": "Hello"}},
        {"id": "s02", "prompt": "a cat sleeps", "narration": "Ngủ ngon"},
    ]}
    data.update(kw)
    return Storyboard.model_validate(data)


def test_none_in_any_case_means_no_subtitles():
    assert _sb(subtitle_language=" NONE ").subtitle_language == NO_SUBTITLES
    sb = _sb()
    assert not sb.has_subtitles
    assert all(sb.subtitle_line(s, c) is None for s in sb.scenes for c in ("vi", "th", "en"))
    assert sb.missing_subtitles() == {}  # nothing to translate when the film is assembled
    assert _sb(subtitle_language="Vietnamese").has_subtitles and _sb(subtitle_language=None).has_subtitles


def test_no_tracks_written(tmp_path):
    out = tmp_path / "final.mp4"
    tracks = write_subtitle_tracks(_sb(), [("s01", 0.2, 3.8), ("s02", 4.2, 7.8)], out, log=lambda *_: None)
    assert tracks == {}
    assert not any(p.exists() for p in subtitle_paths(out).values()) and not out.with_suffix(".srt").exists()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_assemble_has_no_subtitle_stream_even_if_burn_is_on(tmp_path):
    clips = []
    for sid in ("s01", "s02"):
        p = tmp_path / f"{sid}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=1.5",
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "1.5", "-shortest", str(p)], check=True)
        clips.append((sid, p))
    out = assemble(_sb(), clips, tmp_path / "final.mp4", burn_subtitles=True, log=lambda *_: None)
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries",
                                       "stream=index", "-of", "json", str(out)],
                                      capture_output=True, text=True, check=True).stdout)
    assert probe.get("streams", []) == []


def test_planner_message_and_rule():
    base = "Idea: ý\nNumber of scenes: 3\nAspect ratio: 16:9"
    line = "\nSubtitles: none (the film has no subtitles: leave `narration` and `subtitles` empty in every scene)"
    assert planner._user_message("ý", 3, "16:9", "none", "Vietnamese") == (
        base + "\nSpoken dialogue language: Vietnamese (every character who speaks speaks only Vietnamese)" + line)
    assert planner._user_message("ý", 3, "16:9", "None") == base + line  # CLI without --dialogue-lang
    assert 'If the message says "Subtitles: none"' in planner.SYSTEM


def test_plan_auto_clears_lines_claude_wrote_anyway(monkeypatch):
    sent = {}

    def fake(idea, **kw):
        sent.update(kw)
        return _sb(subtitle_language=None)

    monkeypatch.setattr(planner, "plan_with_claude_code", fake)
    sb, _ = planner.plan_auto("con mèo", backend="claude_code", scenes=2, language_hint="none",
                              dialogue_language="Vietnamese")
    assert sent["language_hint"] == "none"
    assert sb.subtitle_language == "none" and not sb.has_subtitles
    assert all(s.narration is None and s.subtitles is None for s in sb.scenes)
    # saved and loaded again it stays a film without subtitles
    assert not Storyboard.model_validate(sb.model_dump(exclude_none=True)).has_subtitles


def test_ui_offers_no_subtitles():
    src = (ROOT / "filmmaker/web/static/app.js").read_text(encoding="utf-8")
    assert '["none", "Không phụ đề"]' in src
    assert src.count('langPicker(SUBTITLE_LANGS, subSaved || "Vietnamese")') == 2  # Phim mới + film from analysis
    assert "Phim này không có phụ đề." in src and "noSubs(S.cur.storyboard) ? null" in src
