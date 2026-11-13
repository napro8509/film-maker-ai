"""Phân tích video: measurements, fetch, rea-video, Claude (fake), new film in the same style, web API, CLI.
No network: yt-dlp, rea-video and claude are fake scripts; ffmpeg only works on a tiny video made locally."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from filmmaker import editor, planner
from filmmaker import video_analysis as va
from filmmaker.storyboard import Storyboard, load_storyboard, save_storyboard

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")


# --- canned data --------------------------------------------------------------


def ai_answer(n_shots: int = 3) -> dict:
    return {
        "title_vi": "Video mẫu", "summary_vi": "Một phim ngắn.", "genre_vi": "drama dọc", "hook_vi": "Cận mặt",
        "why_it_works_vi": "Bất ngờ.",
        "shots": [{"n": i, "size": "close-up", "angle": "eye-level", "movement": "push in", "subject": f"shot {i}",
                   "transition_in": "start" if i == 1 else "cut", "text_on_screen": "", "purpose": "mở"}
                  for i in range(1, n_shots + 1)],
        "rhythm_vi": "Nhanh.", "transitions": [{"type": "cut", "count": 2, "where_vi": "khắp nơi"}],
        "camera_vi": "Cầm tay.",
        "look": {"palette_vi": "ấm", "grade_vi": "teal-orange", "lighting_vi": "đèn vàng", "texture_vi": "hạt",
                 "style_prompt": "warm cinematic drama, teal-orange grade"},
        "sound": {"music_vi": "piano buồn", "voice_vi": "thoại", "sfx_vi": "whoosh",
                  "moments": [{"at": 1.0, "what_vi": "nhạc lên"}], "music_query": "sad piano cinematic"},
        "text": {"subtitles_vi": "chữ trắng viền đen", "titles_vi": ""},
        "characters": [{"name": "the wife", "look": "30s woman", "role_vi": "vợ"}],
        "settings": [{"name": "living room", "look": "teak furniture"}],
        "beats": [{"start": 0, "end": 1, "label": "hook", "what_vi": "mở"},
                  {"start": 1, "end": 3, "label": "twist", "what_vi": "lộ bí mật"}],
        "edit_style": "- Cut every 1-2 s.\n- Use fade to black before the twist.",
        "film_recipe_vi": "6 cảnh.", "idea_suggestion_vi": "Một người cha giấu bí mật.",
    }


def write_exe(path: Path, code: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{code}", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def tiny_video(tmp_path_factory) -> Path:
    """3 s: black / white / blue, 1 s each (two hard cuts); a tone that gets much louder in the last second."""
    if not HAS_FFMPEG:
        pytest.skip("needs ffmpeg")
    d = tmp_path_factory.mktemp("vid")
    out = d / "tiny.mp4"
    graph = ("[0:v][1:v][2:v]concat=n=3:v=1:a=0[v];"
             "[3:a]volume='if(lt(t,2),0.02,0.9)':eval=frame[a]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=black:s=160x284:d=1:r=24",
                    "-f", "lavfi", "-i", "color=white:s=160x284:d=1:r=24",
                    "-f", "lavfi", "-i", "color=0x2040c0:s=160x284:d=1:r=24",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-pix_fmt", "yuv420p", "-shortest",
                    str(out)], check=True, capture_output=True)
    return out


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """A `claude` that records its call and returns ai_answer() as structured output."""
    log = tmp_path / "claude_calls.jsonl"
    script = write_exe(tmp_path / "claude", f"""
import json, os, sys
with open({str(log)!r}, "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd(), "files": sorted(os.listdir("."))}}) + "\\n")
print(json.dumps({{"is_error": False, "structured_output": json.loads(os.environ["FAKE_CLAUDE_ANSWER"])}}))
""")
    monkeypatch.setenv("CLAUDE_BIN", str(script))
    monkeypatch.setenv("FAKE_CLAUDE_ANSWER", json.dumps(ai_answer()))
    monkeypatch.setenv("REA_VIDEO_BIN", "")  # rea-video off unless a test sets a fake one
    return log


# --- parsers ------------------------------------------------------------------


def test_parse_showinfo_and_shots():
    err = "[Parsed_showinfo_2 @ 0x1] n:0 pts:24 pts_time:1.0 duration:1\n... pts_time:2.5 ...\npts_time:2.6\n"
    cuts = va.parse_showinfo(err)
    assert cuts == [1.0, 2.5, 2.6]
    shots = va.shots_from_cuts(cuts, 4.0)
    assert shots == [(0.0, 1.0), (1.0, 2.5), (2.5, 4.0)]  # 2.6 is too close to 2.5: merged
    assert va.shots_from_cuts([0.1, 3.95], 4.0) == [(0.0, 4.0)]  # cuts at the very ends are ignored
    assert va.shots_from_cuts([], 900.0)[-1][1] == va.MAX_SECONDS


def test_parse_blackdetect():
    err = "[blackdetect @ 0x1] black_start:0 black_end:0.5 black_duration:0.5\nblack_start:3.2 black_end:3.6 x"
    assert va.parse_blackdetect(err) == [(0.0, 0.5), (3.2, 3.6)]


def test_parse_ebur128_both_formats_and_per_second():
    new = ("[Parsed_ametadata_1 @ 0x1] frame:0 pts:0 pts_time:0.1\n[Parsed_ametadata_1 @ 0x1] lavfi.r128.M=-30.0\n"
           "[Parsed_ametadata_1 @ 0x1] frame:1 pts:1 pts_time:0.6\n[Parsed_ametadata_1 @ 0x1] lavfi.r128.M=-20.0\n"
           "[Parsed_ametadata_1 @ 0x1] frame:2 pts:2 pts_time:1.5\n[Parsed_ametadata_1 @ 0x1] lavfi.r128.M=-inf\n")
    pts = va.parse_ebur128(new)
    assert pts == [(0.1, -30.0), (0.6, -20.0), (1.5, -120.0)]
    assert va.per_second(pts, 3.0) == [-25.0, -70.0, -70.0]  # floor -70; an empty second repeats the last value
    old = "[Parsed_ebur128_0 @ 0x1] t: 0.0999  TARGET:-23 LUFS    M: -18.5 S:-20.0     I: -19.0 LUFS"
    assert va.parse_ebur128(old) == [(0.0999, -18.5)]


def test_sound_moments_and_rhythm():
    curve = [-30.0] * 5 + [-12.0] * 5 + [-65.0] * 4 + [-30.0] * 3
    kinds = [m["kind"] for m in va.sound_moments(curve)]
    assert "rise" in kinds and "silence" in kinds and "sound_in" in kinds
    st = va.rhythm_stats([(0, 1), (1, 1.5), (1.5, 4), (4, 8)])
    assert st["shots"] == 4 and st["average"] == 2.0 and st["shortest"] == 0.5 and st["longest"] == 4.0
    assert st["first_3s_shots"] == 3 and st["cuts_per_minute"] == 22.5
    assert len(st["average_by_quarter"]) == 4
    assert va.rhythm_stats([]) == {}


def test_clean_subs_vtt_rolling_captions():
    vtt = ("WEBVTT\nKind: captions\nLanguage: en\n\n00:00:01.000 --> 00:00:02.000\n<c>Hello</c> there\n\n"
           "00:00:02.000 --> 00:00:03.000\nHello there\n\n00:01:05.500 --> 00:01:07.000\nWhere were you?\n")
    assert va._clean_subs(vtt) == "[0:01] Hello there\n[1:05] Where were you?"


def test_parse_timeline_and_read_reel(tmp_path):
    (tmp_path / "reel").mkdir()
    (tmp_path / "REEL.md").write_text("---\nreel: 1\nshots: 2\ndescription: all live action\n---\n# Overview\n")
    (tmp_path / "reel" / "timeline.md").write_text(
        "---\nreel: 1\npart: timeline\n---\n```reel-timeline\n"
        "#01 0.0-1.38 | seen a man talks | cam static | made~ live_action(live_action) 90%\n"
        "#02 1.38-3.0 | seen phone screen | cam handheld | flag low_confidence\n```\n")
    (tmp_path / "reel" / "sound.md").write_text("---\nreel: 1\n---\nBGM 122 BPM")
    reel = va.read_reel(tmp_path)
    assert reel["card"]["shots"] == 2 and reel["card"]["description"] == "all live action"
    assert [(r["shot"], r["start"], r["end"]) for r in reel["timeline"]] == [(1, 0.0, 1.38), (2, 1.38, 3.0)]
    assert reel["timeline"][0]["cam"] == "static" and reel["timeline"][1]["flag"] == "low_confidence"
    assert "122 BPM" in reel["texts"]["reel/sound.md"]
    assert va.read_reel(tmp_path / "nothing") is None


def test_read_reel_long_timeline_is_parsed_whole(tmp_path):
    # A 4-minute film gives a timeline far longer than the text sent to Claude: every shot must still be read.
    (tmp_path / "reel").mkdir()
    (tmp_path / "REEL.md").write_text("---\nreel: 1\nshots: 300\n---\n")
    rows = "".join(f"#{n:02d} {n - 1}.0-{n}.0 | cam static | {'x' * 60}\n" for n in range(1, 301))
    (tmp_path / "reel" / "timeline.md").write_text("```reel-timeline\n" + rows + "```\n")
    reel = va.read_reel(tmp_path)
    assert len(reel["timeline"]) == 300 and reel["timeline"][-1]["end"] == 300.0
    assert len(reel["texts"]["reel/timeline.md"]) <= va.REA_TEXT_MAX
    assert len(va.parse_timeline("```reel-timeline\n#01 0.0-1.0 | cam static\n")) == 1  # no closing fence


def test_ratio_label():
    assert va.ratio_label(1920, 1080) == "16:9" and va.ratio_label(1080, 1920) == "9:16"
    assert va.ratio_label(1280, 536) == "2.39:1" and va.ratio_label(1000, 1000) == "1:1"
    assert va.ratio_label(1000, 500) == "2.00:1" and va.ratio_label(0, 0) == "?"


# --- fetch --------------------------------------------------------------------


def test_fetch_local_file_and_refusals(tmp_path):
    src = tmp_path / "clip.MP4"
    src.write_bytes(b"x")
    info = va.fetch(str(src), tmp_path / "a")
    assert info["file"] == "source.mp4" and (tmp_path / "a" / "source.mp4").read_bytes() == b"x"
    with pytest.raises(va.AnalysisError):
        va.fetch(str(src), tmp_path / "b", allow_local=False)  # the web UI only takes links
    with pytest.raises(va.AnalysisError):
        va.fetch(str(tmp_path / "missing.mp4"), tmp_path / "c")


def test_fetch_with_fake_ytdlp(tmp_path, monkeypatch):
    calls = tmp_path / "ytdlp.log"
    fake = write_exe(tmp_path / "yt-dlp", f"""
import json, sys
args = sys.argv[1:]
open({str(calls)!r}, "a").write(json.dumps(args) + "\\n")
out = args[args.index("-o") + 1]
if "--skip-download" in args:
    open(out.replace("%(ext)s", "en-orig.vtt"), "w").write("WEBVTT\\n\\n00:00:01.000 --> 00:00:02.000\\nauto line\\n")
    sys.exit(0)
open(out.replace("%(ext)s", "mp4"), "wb").write(b"video")
json.dump({{"title": "Clip", "description": "desc", "uploader": "someone", "webpage_url": "https://x.test/v",
           "automatic_captions": {{"en-orig": [], "fr": []}}, "tags": ["a"]}},
          open(out.replace("%(ext)s", "info.json"), "w"))
""")
    monkeypatch.setenv("YTDLP_BIN", str(fake))
    info = va.fetch("https://www.youtube.com/watch?v=abc", tmp_path / "a", log=lambda *_: None)
    assert info["file"] == "source.mp4" and info["title"] == "Clip" and info["uploader"] == "someone"
    assert info["subtitles"] == "[0:01] auto line"
    assert not list((tmp_path / "a").glob("*.info.json"))  # big json not kept
    runs = [json.loads(x) for x in calls.read_text().splitlines()]
    assert runs[0][-2:] == ["--", "https://www.youtube.com/watch?v=abc"]  # the link can never be read as an option
    assert runs[1][runs[1].index("--sub-langs") + 1] == "en-orig"


def test_fetch_ytdlp_failure_and_missing_ytdlp(tmp_path, monkeypatch):
    fake = write_exe(tmp_path / "yt-dlp", "import sys\nsys.stderr.write('ERROR: Unsupported URL\\n')\nsys.exit(1)\n")
    monkeypatch.setenv("YTDLP_BIN", str(fake))
    with pytest.raises(va.AnalysisError, match="Unsupported URL"):
        va.fetch("https://example.test/page", tmp_path / "a", log=lambda *_: None)
    monkeypatch.setenv("YTDLP_BIN", "")  # no yt-dlp: a page link cannot be downloaded directly

    def no_net(url, dest, should_stop):
        raise va.AnalysisError("link không phải file video (text/html)")

    monkeypatch.setattr(va, "_download", no_net)
    with pytest.raises(va.AnalysisError, match="yt-dlp"):
        va.fetch("https://example.test/page", tmp_path / "b", log=lambda *_: None)


def test_fetch_direct_mp4_link_without_ytdlp(tmp_path, monkeypatch):
    monkeypatch.setenv("YTDLP_BIN", "")
    got = {}

    def fake_download(url, dest, should_stop):
        got["url"] = url
        dest.write_bytes(b"v")
        return dest

    monkeypatch.setattr(va, "_download", fake_download)
    info = va.fetch("https://cdn.test/a/film.mp4?sig=1", tmp_path / "a", log=lambda *_: None)
    assert info["file"] == "source.mp4" and got["url"].startswith("https://cdn.test/")


# --- rea-video ----------------------------------------------------------------


def test_rea_binary_lookup(monkeypatch, tmp_path):
    monkeypatch.setenv("REA_VIDEO_BIN", "")
    assert va.rea_binary() is None
    monkeypatch.setenv("REA_VIDEO_BIN", "/x/rea-video")
    assert va.rea_binary() == "/x/rea-video"
    lines = []
    monkeypatch.setenv("REA_VIDEO_BIN", "")
    assert va.run_rea(tmp_path / "v.mp4", tmp_path / "rea", log=lines.append) is None
    assert "chưa cài" in lines[0]


def test_run_rea_fake_and_failure(tmp_path, monkeypatch):
    fake = write_exe(tmp_path / "rea-video", """
import os, sys
args = sys.argv[1:]
assert args[0] == "decompose" and args[args.index("--lang") + 1] == "en"
out = args[args.index("-o") + 1]
os.makedirs(os.path.join(out, "reel"), exist_ok=True)
open(os.path.join(out, "REEL.md"), "w").write("---\\nshots: 2\\ndescription: mixed\\n---\\nbody")
open(os.path.join(out, "reel", "timeline.md"), "w").write("```reel-timeline\\n#01 0.0-1.5 | cam static\\n#02 1.5-3.0 | cam handheld\\n```")
""")
    monkeypatch.setenv("REA_VIDEO_BIN", str(fake))
    reel = va.run_rea(tmp_path / "v.mp4", tmp_path / "rea", log=lambda *_: None)
    assert [r["end"] for r in reel["timeline"]] == [1.5, 3.0]
    bad = write_exe(tmp_path / "rea-bad", "import sys\nsys.stderr.write('boom\\n')\nsys.exit(3)\n")
    monkeypatch.setenv("REA_VIDEO_BIN", str(bad))
    lines = []
    assert va.run_rea(tmp_path / "v.mp4", tmp_path / "rea2", log=lines.append) is None
    assert any("exit 3" in x for x in lines)


# --- measuring a real (tiny) video --------------------------------------------


@needs_ffmpeg
def test_measure_tiny_video(tiny_video):
    m = va.measure(tiny_video, log=lambda *_: None)
    assert m["aspect"] == "9:16" and m["has_audio"] and abs(m["duration"] - 3.0) < 0.15
    assert len(m["shots"]) == 3
    assert [round(a) for a, _ in m["shots"]] == [0, 1, 2]
    assert m["loudness"][-1] - m["loudness"][0] > 15  # the tone gets ~33 dB louder


@needs_ffmpeg
def test_contact_sheets(tiny_video, tmp_path):
    sheets = va.contact_sheets(tiny_video, [(0, 1), (1, 2), (2, 3)], tmp_path / "f", aspect="9:16")
    assert sheets == [{"file": "sheet01.jpg", "shots": [1, 2, 3], "columns": 3}]
    assert (tmp_path / "f" / "sheet01.jpg").stat().st_size > 0
    many = [(i * 0.09, (i + 1) * 0.09) for i in range(33)]  # > 30 shots: one frame each, 16 per sheet
    sheets = va.contact_sheets(tiny_video, many, tmp_path / "g", aspect="9:16")
    assert [len(s["shots"]) for s in sheets] == [16, 16, 1] and all(s["columns"] == 4 for s in sheets)


# --- whole analysis with a fake Claude ----------------------------------------


@needs_ffmpeg
def test_analyze_end_to_end(tiny_video, tmp_path, fake_claude):
    lines = []
    data = va.analyze(str(tiny_video), tmp_path / "an", backend="claude_code", log=lines.append)
    saved = json.loads((tmp_path / "an" / "analysis.json").read_text())
    assert saved["ai"]["title_vi"] == "Video mẫu" and saved == json.loads(json.dumps(data))
    assert [s["n"] for s in saved["measured"]["shots"]] == [1, 2, 3]
    assert saved["measured"]["stats"]["shots"] == 3 and saved["rea"] == {"used": False}
    assert saved["sheets"][0]["file"] == "frames/sheet01.jpg"
    assert "Measured on the reference" in saved["edit_style"] and "fade to black" in saved["edit_style"]
    assert "sad piano cinematic" in saved["edit_style"]
    report = (tmp_path / "an" / "report.md").read_text()
    assert "# Video mẫu" in report and "| 3 |" in report and "lộ bí mật" in report
    call = json.loads(fake_claude.read_text().splitlines()[0])
    assert call["cwd"].endswith("frames") and "sheet01.jpg" in call["files"]  # Claude only sees the frames
    argv = call["argv"]
    assert argv[argv.index("--tools") + 1] == "Read" and "--json-schema" in argv
    prompt = argv[argv.index("-p") + 1]
    assert "#3 2." in prompt and "sheet01.jpg" in prompt and "Loudness per second" in prompt
    assert va.list_analyses(tmp_path)[0]["id"] == "an"


@needs_ffmpeg
def test_analyze_uses_rea_timeline(tiny_video, tmp_path, fake_claude, monkeypatch):
    fake = write_exe(tmp_path / "rea-video", """
import os, sys
out = sys.argv[sys.argv.index("-o") + 1]
os.makedirs(os.path.join(out, "reel"), exist_ok=True)
open(os.path.join(out, "REEL.md"), "w").write("---\\nshots: 2\\ndescription: 100% live action\\n---\\n")
open(os.path.join(out, "reel", "timeline.md"), "w").write("```reel-timeline\\n#01 0.0-1.0 | cam static\\n#02 1.0-3.0 | cam static\\n```")
open(os.path.join(out, "reel", "sound.md"), "w").write("BGM 96 BPM, sfx at 2.1s")
""")
    monkeypatch.setenv("REA_VIDEO_BIN", str(fake))
    data = va.analyze(str(tiny_video), tmp_path / "an", backend="claude_code", log=lambda *_: None)
    assert data["rea"]["used"] and data["measured"]["shot_source"] == "rea-video"
    assert [s["end"] for s in data["measured"]["shots"]] == [1.0, 3.0]
    prompt = json.loads(fake_claude.read_text().splitlines()[0])["argv"]
    assert "BGM 96 BPM" in prompt[prompt.index("-p") + 1]


@needs_ffmpeg
def test_analyze_claude_error(tiny_video, tmp_path, monkeypatch, fake_claude):
    script = write_exe(tmp_path / "claude2", 'import json\nprint(json.dumps({"is_error": True, "result": "Not logged in"}))\n')
    monkeypatch.setenv("CLAUDE_BIN", str(script))
    with pytest.raises(va.AnalysisError, match="Not logged in"):
        va.analyze(str(tiny_video), tmp_path / "an", backend="claude_code", log=lambda *_: None)
    assert not (tmp_path / "an" / "analysis.json").exists()


def test_stop_interrupts_a_long_command(tmp_path):
    slow = write_exe(tmp_path / "slow", "import time\ntime.sleep(30)\n")
    t = time.time()
    with pytest.raises(va.AnalysisStopped):
        va._run([str(slow)], timeout=60, should_stop=lambda: time.time() - t > 0.3)
    assert time.time() - t < 10


# --- new film in the same style -----------------------------------------------


def analysis_data(aspect="9:16") -> dict:
    shots = [(0, 1.0), (1.0, 2.5), (2.5, 4.0)]
    data = {"id": "x", "created": 1.0, "source": {"url": "https://x.test/v", "title": "Clip", "file": "source.mp4"},
            "measured": {"duration": 40.0, "width": 720, "height": 1280, "aspect": aspect, "fps": 30, "has_audio": True,
                         "shots": [{"n": i, "start": a, "end": b, "length": b - a} for i, (a, b) in enumerate(shots, 1)],
                         "stats": va.rhythm_stats(shots), "blacks": [], "loudness": [-20.0] * 4, "moments": []},
            "rea": {"used": False}, "sheets": [], "ai": ai_answer()}
    data["edit_style"] = va.edit_style_text(data)
    return data


def test_plan_from_analysis(monkeypatch):
    got = {}

    def fake_plan_auto(idea, **kw):
        got["idea"], got["kw"] = idea, kw
        return Storyboard.model_validate({"title": "Phim mới", "scenes": [{"id": "s01", "prompt": "a"}]}), "api"

    monkeypatch.setattr(planner, "plan_auto", fake_plan_auto)
    sb, used = va.plan_from_analysis(analysis_data(), "", language_hint="Vietnamese", dialogue_language="Thai")
    assert used == "api" and sb.style == "warm cinematic drama, teal-orange grade"
    assert "Measured on the reference (40s): 3 shots" in sb.edit_style and "Cut every 1-2 s" in sb.edit_style
    assert got["kw"]["aspect"] == "9:16" and got["kw"]["scenes"] == 7  # 2.5 + 1.5 s, then 36 s in 5 clip-sized scenes
    assert got["kw"]["dialogue_language"] == "Thai" and got["kw"]["language_hint"] == "Vietnamese"
    assert "Một người cha giấu bí mật" in got["idea"]  # no idea given: the suggested one
    assert "REFERENCE STYLE" in got["idea"] and "do NOT copy" in got["idea"] and "twist" in got["idea"]
    sb2, _ = va.plan_from_analysis(analysis_data("16:9"), "Chuyện cô giáo", scenes=4)
    assert got["idea"].startswith("Chuyện cô giáo") and got["kw"]["scenes"] == 4 and got["kw"]["aspect"] == "16:9"


def test_plan_from_analysis_remake_keeps_the_film_and_recasts_people(monkeypatch):
    got = {}

    def fake_plan_auto(idea, **kw):
        got["idea"], got["kw"] = idea, kw
        return Storyboard.model_validate({"title": "Làm lại", "model": "veo-lite", "scenes": [
            {"id": f"s{i:02d}", "prompt": "a"} for i in range(1, kw["scenes"] + 1)]}), "api"

    monkeypatch.setattr(planner, "plan_auto", fake_plan_auto)
    data = analysis_data()
    data["measured"]["duration"] = 8.0  # 0-2.5 (shots 1+2), 2.5-4 (shot 3), 4-8
    data["subtitles"] = "00:01 Anh về rồi!"
    sb, _ = va.plan_from_analysis(data, "nhân vật là người Việt", mode="same")
    idea = got["idea"]
    assert idea.startswith("REMAKE THE REFERENCE VIDEO") and "Change ONLY the people" in idea
    assert "REFERENCE STYLE" not in idea and "Extra wishes from the user: nhân vật là người Việt" in idea
    assert "s01, 2.5s" in idea and "shot #1, #2" in idea and "close-up, eye-level, push in: shot 1" in idea
    assert "Anh về rồi" in idea and "the wife (vợ)" in idea
    assert [s.screen_time for s in sb.scenes] == [2.5, 1.5, 4.0]
    assert sum(s.screen_time for s in sb.scenes) == 8.0
    assert all(s.duration is None or s.duration >= s.screen_time for s in sb.scenes)
    assert sb.edit_style.startswith("- Target length: about 8s")
    with pytest.raises(ValueError):
        va.plan_from_analysis(data, mode="copy")


def test_clip_too_short_for_its_screen_time_falls_back_to_flow_default():
    from filmmaker import model_meta
    sb = Storyboard.model_validate({"title": "x", "model": "veo-3-fast", "scenes": [
        {"id": "s01", "prompt": "a", "duration": 4, "screen_time": 4.2},
        {"id": "s02", "prompt": "b", "duration": 4, "screen_time": 2.0}]})
    meta = model_meta.load()
    if meta.durations("veo-3-fast"):
        pytest.skip("veo-3-fast lists its lengths here")
    va._clip_durations(sb, meta)
    assert [s.duration for s in sb.scenes] == [None, 4]


def test_style_mode_also_times_each_scene(monkeypatch):
    monkeypatch.setattr(planner, "plan_auto", lambda idea, **kw: (Storyboard.model_validate(
        {"title": "x", "scenes": [{"id": f"s{i:02d}", "prompt": "a"} for i in range(1, kw["scenes"] + 1)]}), "api"))
    data = analysis_data()
    data["measured"]["duration"] = 15.0
    sb, _ = va.plan_from_analysis(data, "Chuyện mới", scenes=7)
    assert len(sb.scenes) == 7 and abs(sum(s.screen_time for s in sb.scenes) - 15) < 0.05


def test_suggested_scenes_bounds():
    d = analysis_data()
    d["measured"]["duration"] = 8
    assert va.suggested_scenes(d) == 3  # 0-2.5 (1 s shot merged), 2.5-4, 4-8
    d["measured"]["duration"] = 600
    assert va.suggested_scenes(d) == 20


def _m(duration, shots):
    return {"duration": duration, "shots": [{"n": i, "start": a, "end": b} for i, (a, b) in enumerate(shots, 1)]}


def test_scene_budget_fits_a_short_reference():
    # a 15 s reel with 7 shots must not become 7 scenes of 4-8 s: the budget adds up to 15 s
    shots = [(0, 3.5), (3.5, 5.2), (5.2, 6.4), (6.4, 8.3), (8.3, 10.0), (10.0, 11.2), (11.2, 15.0)]
    b = va.scene_budget(_m(15, shots))
    assert abs(sum(x["length"] for x in b) - 15) < 0.05
    assert all(x["length"] >= va.MIN_SCENE_CUT for x in b)
    assert [k for x in b for k in x["shots"]] == list(range(1, 8))  # every shot used once, in order
    assert va.suggested_scenes({"measured": _m(15, shots)}) == len(b) <= 7
    forced = va.scene_budget(_m(15, shots), 7)
    assert [x["shots"] for x in forced] == [[k] for k in range(1, 8)]  # a chosen 7 = the reference's own 7 shots
    assert abs(sum(x["length"] for x in forced) - 15) < 0.05
    assert len(va.scene_budget(_m(15, shots), 9)) == 9
    text = va.recipe_timing_vi({"measured": _m(15, shots)})
    assert text.startswith(f"Khớp video gốc 15s: {len(b)} cảnh") and "Tổng ≈ 15.0s" in text


def test_scene_budget_splits_long_takes_and_fills_the_tail():
    b = va.scene_budget(_m(20, [(0, 20)]))  # one 20 s take = three Veo clips
    assert len(b) == 3 and all(x["length"] <= va.MAX_SCENE_CUT for x in b)
    b = va.scene_budget(_m(10, [(0, 2), (2, 4)]))  # cut detection stopped at 4 s
    assert b[-1]["end"] == 10 and b[-1]["shots"] == []
    assert va.scene_budget({"duration": 0, "shots": []}) == []


def test_brief_and_film_brief_carry_the_budget():
    m = {**_m(15, [(0, 5), (5, 10), (10, 15)]), "width": 720, "height": 1280, "aspect": "9:16", "fps": 30,
         "has_audio": True}
    m["shots"] = [(0, 5), (5, 10), (10, 15)]
    brief = va._brief({"title": "x"}, m, None)
    assert "SCENE BUDGET" in brief and "exactly 3 scenes" in brief and "scene 2: 5.0s" in brief
    data = analysis_data()
    data["measured"]["duration"] = 15.0
    fb = va.film_brief(data, 4)
    assert "about 15s in total" in fb and "4 scenes" in fb


def test_edit_style_round_trip_and_editor_brief(tmp_path):
    sb = Storyboard.model_validate({"title": "T", "edit_style": "- Cut every 1.5 s.", "scenes": [{"id": "s01", "prompt": "a"}]})
    save_storyboard(sb, tmp_path / "sb.yaml")
    assert yaml.safe_load((tmp_path / "sb.yaml").read_text())["edit_style"] == "- Cut every 1.5 s."
    sb = load_storyboard(tmp_path / "sb.yaml")
    info = editor.ClipInfo(id="s01", path="x", duration=6.0, has_audio=True)
    brief = editor._brief(sb, {"s01": info}, ["s01"], False)
    assert "Edit style to imitate" in brief and "Cut every 1.5 s." in brief
    assert "Edit style to imitate" in editor.SYSTEM
    plain = Storyboard.model_validate({"title": "T", "scenes": [{"id": "s01", "prompt": "a"}]})
    assert "Edit style" not in editor._brief(plain, {"s01": info}, ["s01"], False)
    assert "edit_style" not in yaml.safe_dump(plain.model_dump(exclude_none=True))


def test_copy_reference(tmp_path):
    a = tmp_path / "a"
    (a / "frames").mkdir(parents=True)
    (a / "analysis.json").write_text("{}")
    (a / "report.md").write_text("r")
    (a / "frames" / "sheet01.jpg").write_bytes(b"j")
    (a / "source.mp4").write_bytes(b"big")
    dest = va.copy_reference(a, tmp_path / "proj")
    assert sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()) == \
        ["analysis.json", "frames/sheet01.jpg", "report.md"]


def test_new_id():
    assert va.new_id("www.youtube.com").endswith("-www-youtube-com")
    assert va.new_id("").endswith("-video")


# --- web API ------------------------------------------------------------------


fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filmmaker.web import server  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": False, "logged_in": False})
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"auto_backup": False, "planner": "api", "anthropic_api_key": "x"}))
    monkeypatch.setattr(server, "SETTINGS_PATH", settings)
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(settings))
    c = TestClient(server.create_app(tmp_path / "ws", token="t0k"))
    c.headers["X-Token"] = "t0k"
    c.ws = tmp_path / "ws"
    return c


def _wait(c, job, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _fake_analyze(calls):
    def run(source, folder, **kw):
        calls.append((source, kw))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "frames").mkdir(exist_ok=True)
        (folder / "frames" / "sheet01.jpg").write_bytes(b"jpg")
        (folder / "source.mp4").write_bytes(b"mp4")
        data = analysis_data()
        data["id"] = folder.name
        data["sheets"] = [{"file": "frames/sheet01.jpg", "shots": [1, 2, 3], "columns": 3}]
        (folder / "analysis.json").write_text(json.dumps(data))
        kw["log"]("fake analysis done")
        return data
    return run


def test_web_analysis_flow(client, monkeypatch):
    c = client
    assert c.post("/api/analyses", json={"url": "/etc/passwd"}).status_code == 400  # links only
    assert c.post("/api/analyses", json={"url": "file:///etc/passwd"}).status_code == 400
    calls = []
    monkeypatch.setattr(va, "analyze", _fake_analyze(calls))
    j = _wait(c, c.post("/api/analyses", json={"url": "https://www.tiktok.com/@a/video/1", "use_rea": False}).json())
    assert j["status"] == "done" and j["kind"] == "analyze"
    aid = j["result"]["analysis"]
    assert calls[0][0] == "https://www.tiktok.com/@a/video/1"
    assert calls[0][1]["allow_local"] is False and calls[0][1]["use_rea"] is False and calls[0][1]["backend"] == "api"
    assert (c.ws / ".analyses" / aid / "analysis.json").is_file()
    assert c.get("/api/projects").json() == []  # .analyses is not a project
    items = c.get("/api/analyses").json()
    assert [a["id"] for a in items] == [aid] and items[0]["thumb"].endswith("frames/sheet01.jpg")
    d = c.get(f"/api/analyses/{aid}").json()
    assert d["ai"]["title_vi"] == "Video mẫu" and d["suggested_scenes"] == 7
    assert d["recipe_timing_vi"].startswith("Khớp video gốc 40s: 7 cảnh")
    assert d["video_url"] == f"/api/analyses/{aid}/file/source.mp4"
    assert c.get(d["sheets"][0]["url"]).content == b"jpg"
    assert c.get(f"/api/analyses/{aid}/file/../../settings.json").status_code == 404
    assert c.get(f"/api/analyses/{aid}/file/%2e%2e/%2e%2e/settings.json").status_code == 404
    assert c.get("/api/analyses/..%2f..").status_code == 404
    assert c.get("/api/analyses/nope").status_code == 404
    env = c.get("/api/env").json()["video_analysis"]
    assert set(env) == {"ytdlp", "rea_video"}

    got = {}

    def fake_plan_auto(idea, **kw):
        got["idea"], got["kw"] = idea, kw
        return Storyboard.model_validate({"title": "Phim theo mẫu", "scenes": [{"id": "s01", "prompt": "a"}]}), "api"

    monkeypatch.setattr(planner, "plan_auto", fake_plan_auto)
    j = _wait(c, c.post(f"/api/analyses/{aid}/film", json={"idea": "", "scenes": 5, "language": "Vietnamese",
                                                           "dialogue_language": "Thai"}).json())
    assert j["status"] == "done", j["lines"]
    pid = j["result"]["project"]
    sb = load_storyboard(c.ws / pid / "storyboard.yaml")
    assert "Cut every 1-2 s" in sb.edit_style and sb.style.startswith("warm cinematic")
    assert got["kw"]["scenes"] == 5 and got["kw"]["aspect"] == "9:16"
    assert (c.ws / pid / "reference" / "analysis.json").is_file()
    assert c.get(f"/api/projects/{pid}").json()["storyboard"]["edit_style"].startswith("- Target length") and "- Measured" in c.get(f"/api/projects/{pid}").json()["storyboard"]["edit_style"]
    assert c.post(f"/api/analyses/{aid}/film", json={"dialogue_language": "a\nb"}).status_code == 400
    assert c.post(f"/api/analyses/{aid}/film", json={"mode": "copy"}).status_code == 422
    j = _wait(c, c.post(f"/api/analyses/{aid}/film", json={"idea": "", "scenes": 3, "mode": "same"}).json())
    assert j["status"] == "done", j["lines"] and got["idea"].startswith("REMAKE THE REFERENCE VIDEO")
    assert got["idea"].startswith("REMAKE THE REFERENCE VIDEO") and got["kw"]["scenes"] == 3
    b = c.get(f"/api/analyses/{aid}/budget?n=4").json()
    assert b["scenes"] == 4 and abs(sum(b["lengths"]) - 40) < 0.1 and b["text"].startswith("Khớp video gốc 40s: 4 cảnh")
    assert c.get(f"/api/analyses/{aid}/budget").json()["scenes"] == 7

    assert c.delete(f"/api/analyses/{aid}").json() == {"ok": True}
    assert not (c.ws / ".analyses" / aid).exists()
    assert (c.ws / pid / "storyboard.yaml").is_file()  # the film made from it stays


def test_web_analysis_failure_cleans_up(client, monkeypatch):
    def boom(source, folder, **kw):
        folder.mkdir(parents=True)
        (folder / "source.mp4").write_bytes(b"x")
        raise va.AnalysisError("yt-dlp không tải được video: ERROR: private video")

    monkeypatch.setattr(va, "analyze", boom)
    j = _wait(client, client.post("/api/analyses", json={"url": "https://x.test/v"}).json())
    assert j["status"] == "failed" and "private video" in j["result"]["error"]
    assert list((client.ws / ".analyses").iterdir()) == []


def test_web_analysis_needs_claude(client, monkeypatch):
    settings = json.loads(server.SETTINGS_PATH.read_text())
    settings.update(planner="api", anthropic_api_key="")
    server.SETTINGS_PATH.write_text(json.dumps(settings))
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    r = client.post("/api/analyses", json={"url": "https://x.test/v"})
    assert r.status_code == 400 and "Claude" in r.json()["detail"]


# --- CLI ----------------------------------------------------------------------


def test_cli_analyze_and_film(tmp_path, monkeypatch):
    from filmmaker.cli import main

    calls = []
    monkeypatch.setattr(va, "analyze", _fake_analyze(calls))
    monkeypatch.setattr(planner, "plan_auto", lambda idea, **kw: (
        Storyboard.model_validate({"title": "CLI", "scenes": [{"id": "s01", "prompt": "a"}]}), "api"))
    out = tmp_path / "an"
    film = tmp_path / "proj" / "storyboard.yaml"
    film.parent.mkdir()
    r = CliRunner().invoke(main, ["analyze", "https://x.test/v.mp4", "-o", str(out), "--no-rea", "--film", str(film),
                                  "--idea", "Một ý tưởng", "--scenes", "4"])
    assert r.exit_code == 0, r.output
    assert calls[0][1]["use_rea"] is False and calls[0][1]["allow_local"] is True
    sb = load_storyboard(film)
    assert sb.title == "CLI" and "Cut every 1-2 s" in sb.edit_style
    assert (film.parent / "reference" / "analysis.json").is_file()


def test_cli_analyze_error(tmp_path, monkeypatch):
    from filmmaker.cli import main

    def boom(*a, **k):
        raise va.AnalysisError("không thấy file video: x")

    monkeypatch.setattr(va, "analyze", boom)
    r = CliRunner().invoke(main, ["analyze", "x", "-o", str(tmp_path / "a")])
    assert r.exit_code == 1 and "không thấy file video" in r.output
