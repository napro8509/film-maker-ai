"""QA (T5) for dialogue-language: the gaps left by the T0-T3 tests.

- AC11/AC12: the flow_ui path (gflow refuses i2v -> Flow chat page) and `filmmaker run --dry-run`.
- AC14: portrait and cover images made by the pipeline carry no spoken-language rule.
- AC17: adding / changing / removing `dialogue_language` after a full run re-sends nothing.
- AC10: what `suggest_titles` really sends to Claude (fake `claude` binary).
Fake gflow / fake flow_ui / fake claude only: no Claude, Flow or Veo.
"""
import json
import shlex
import shutil
import stat
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from filmmaker import planner
from filmmaker.cli import main
from filmmaker.gflow import Gflow
from filmmaker.pipeline import Pipeline, RunOptions
from filmmaker.storyboard import DIALOGUE_RULE, NO_DIALOGUE_RULE, NO_TEXT, Storyboard, load_storyboard

FAKE = Path(__file__).parent / "fake_gflow.py"
FAKE_UI = Path(__file__).parent / "fake_flow_ui.py"
VI = DIALOGUE_RULE.format(lang="Vietnamese")
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

STORY = """
title: Test film
aspect: "16:9"
style: "cinematic"
dialogue_language: Vietnamese
characters:
  - name: Lan
    description: "short black hair, red scarf"
    image: lan.png
  - name: Minh
    description: "tall, glasses"
thumbnail:
  text: "Quán cà phê"
scenes:
  - id: s01
    mode: keyframe
    image_prompt: "Lan at a cafe"
    prompt: "slow push-in"
  - id: s02
    mode: t2v
    prompt: "Lan walks in the rain"
  - id: s03
    mode: i2v
    image: start.png
    prompt: "Minh waits. All spoken dialogue is in Vietnamese; characters speak only Vietnamese. He sighs."
  - id: s04
    mode: i2v
    image: start.png
    prompt: "pan left"
"""


def _story(lang: str | None) -> str:
    if lang is None:
        return STORY.replace("dialogue_language: Vietnamese\n", "")
    return STORY.replace("dialogue_language: Vietnamese", f"dialogue_language: {lang}")


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "start.png").write_bytes(b"png")
    (tmp_path / "lan.png").write_bytes(b"lan")
    log, ui_log = tmp_path / "calls.log", tmp_path / "ui.log"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(log))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)
    monkeypatch.setenv("FLOW_UI_CMD", f"{sys.executable} {FAKE_UI}")
    monkeypatch.setenv("FAKE_FLOW_UI_LOG", str(ui_log))
    monkeypatch.delenv("FAKE_FLOW_UI_FAIL", raising=False)

    def read(p):
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    return tmp_path, (lambda: read(log)), (lambda: read(ui_log))


def _pipe(tmp_path, story: str) -> Pipeline:
    path = tmp_path / "story.yaml"
    path.write_text(story)
    return Pipeline(load_storyboard(path), tmp_path / "out", Gflow(log=lambda *_: None),
                    log=lambda *_: None, sleep=lambda *_: None)


def _prompt(argv: list[str]) -> str:
    return argv[argv.index("--prompt") + 1]


def _no_rule_in_images(calls):
    for c in calls():
        if c[0] == "image":
            text = " ".join(c)
            assert VI not in text and NO_DIALOGUE_RULE not in text and "spoken dialogue" not in text.lower(), c


# --- AC11 / AC12: flow_ui path ---------------------------------------------------------------------------

@needs_ffmpeg
def test_flow_ui_prompts_get_rule_once(env, monkeypatch):
    tmp_path, calls, ui = env
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")  # gflow refuses i2v -> Flow chat page
    report = _pipe(tmp_path, STORY).run(RunOptions(pause=(0, 0)))
    assert report.ok, report
    ui_calls = ui()
    assert len(ui_calls) == 3 and all(c[0] == "i2v" for c in ui_calls)  # s01 keyframe, s03, s04
    for c in ui_calls:
        p = _prompt(c)
        assert p.count(VI) == 1, p  # s03 already had the sentence pasted: still once
        assert p.index(VI) < p.index(NO_TEXT), p
    assert any("Minh waits" in _prompt(c) for c in ui_calls)
    # the t2v scene still went through gflow, with the rule
    videos = [c for c in calls() if c[0] == "video"]
    assert videos and all(" ".join(c).count(VI) == 1 for c in videos)
    _no_rule_in_images(calls)


@needs_ffmpeg
@pytest.mark.parametrize("lang", ["none", "NONE"])
def test_flow_ui_no_dialogue(env, monkeypatch, lang):
    tmp_path, calls, ui = env
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    report = _pipe(tmp_path, _story(lang)).run(RunOptions(pause=(0, 0)))
    assert report.ok, report
    ui_calls = ui()
    assert len(ui_calls) == 3
    for c in ui_calls:
        p = _prompt(c)
        assert p.count(NO_DIALOGUE_RULE) == 1, p
        if "Minh waits" not in p:  # s03 keeps the user's own pasted sentence
            assert "All spoken dialogue is in" not in p, p


# --- AC11: `filmmaker run --dry-run` ---------------------------------------------------------------------

def _dry_run_videos(tmp_path, story: str) -> list[list[str]]:
    path = tmp_path / "story.yaml"
    path.write_text(story)
    res = CliRunner().invoke(main, ["run", str(path), "--dry-run"])
    assert res.exit_code == 0, res.output
    cmds = [shlex.split(l.strip()[2:]) for l in res.output.splitlines() if l.strip().startswith("$ ")]
    return cmds


def test_dry_run_shows_rule_once_in_every_video(env):
    tmp_path, calls, _ = env
    cmds = _dry_run_videos(tmp_path, STORY)
    videos = [c for c in cmds if "video" in c]
    kinds = {c[c.index("video") + 1] for c in videos}
    assert {"i2v", "r2v"} <= kinds, kinds  # s01 keyframe / s03 / s04 = i2v, s02 = r2v (Lan has an image)
    assert len(videos) == 4
    for c in videos:
        assert " ".join(c).count(VI) == 1, c
    for c in cmds:
        if "image" in c:
            assert VI not in " ".join(c), c
    assert calls() == []  # nothing really sent


def test_dry_run_without_language_has_no_rule(env):
    tmp_path, calls, _ = env
    cmds = _dry_run_videos(tmp_path, _story(None).replace(
        " All spoken dialogue is in Vietnamese; characters speak only Vietnamese.", ""))
    assert cmds and not any("spoken dialogue" in " ".join(c).lower() for c in cmds)


# --- AC14: portraits and cover ---------------------------------------------------------------------------

@needs_ffmpeg
@pytest.mark.parametrize("lang", ["Vietnamese", "none"])
def test_portrait_and_cover_images_have_no_rule(env, lang):
    tmp_path, calls, _ = env
    pipe = _pipe(tmp_path, _story(lang))
    minh = pipe.sb.characters[1]
    rep = pipe.generate_portraits([(minh, tmp_path / "assets" / "minh.png")], RunOptions(pause=(0, 0)))
    assert rep.done == ["Minh"]
    pipe.make_thumbnail(RunOptions(pause=(0, 0)))
    images = [c for c in calls() if c[0] == "image"]
    assert len(images) == 2  # portrait t2i + cover (i2i with Lan's picture)
    _no_rule_in_images(calls)
    # byte-for-byte the same prompts as a storyboard without the field
    plain = load_storyboard_text(tmp_path, _story(None))
    sb = pipe.sb
    assert sb.for_flow(sb.portrait_prompt(minh)) == plain.for_flow(plain.portrait_prompt(plain.characters[1]))
    assert sb.for_flow(sb.thumbnail_prompt()) == plain.for_flow(plain.thumbnail_prompt())


def load_storyboard_text(tmp_path, text: str) -> Storyboard:
    p = tmp_path / "plain.yaml"
    p.write_text(text)
    return load_storyboard(p)


# --- AC17: language changes never re-render --------------------------------------------------------------

@needs_ffmpeg
def test_changing_language_after_run_resends_nothing(env):
    tmp_path, calls, _ = env
    first = _pipe(tmp_path, _story(None))
    assert first.run(RunOptions(pause=(0, 0))).done == ["s01", "s02", "s03", "s04"]
    prints = {s.id: first.state.scene(s.id)["fingerprint"] for s in first.sb.scenes}
    for lang in ("Vietnamese", "Thai", "NONE", None):  # add, change, change to none, remove
        pipe = _pipe(tmp_path, _story(lang))
        assert pipe.sb.dialogue_language == (lang.lower() if lang == "NONE" else lang)
        for s in pipe.sb.scenes:
            assert pipe.scene_status(s) == "done", (lang, s.id)
            assert pipe.sb.scene_fingerprint(s) == prints[s.id], (lang, s.id)
        n = len(calls())
        report = pipe.run(RunOptions(pause=(0, 0)))
        assert report.ok and report.done == [], report
        assert [c for c in calls()[n:] if c[0] in ("video", "image")] == [], lang


# --- AC10: suggest_titles really sends the "Spoken language" line ---------------------------------------

FAKE_CLAUDE = """#!{py}
import json, os, sys
if sys.argv[1:3] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True, "authMethod": "claude.ai"}})); sys.exit(0)
dump = {{"p": sys.argv[sys.argv.index("-p") + 1], "system": sys.argv[sys.argv.index("--system-prompt") + 1]}}
open(os.environ["FAKE_CLAUDE_DUMP"], "w").write(json.dumps(dump))
out = {{"titles": [{{"text": "Mưa", "vi": ""}}]}}
print(json.dumps({{"type": "result", "is_error": False, "structured_output": out}}))
"""


@pytest.fixture
def claude(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text(FAKE_CLAUDE.format(py=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CLAUDE_BIN", str(exe))
    dump = tmp_path / "dump.json"
    monkeypatch.setenv("FAKE_CLAUDE_DUMP", str(dump))
    return lambda: json.loads(dump.read_text())


def _title_sb(**kw) -> Storyboard:
    data = {"title": "Mèo", "characters": [{"name": "Lan", "description": "a girl"}],
            "scenes": [{"id": "s01", "prompt": "Lan says softly in Thai, voice only", "narration": "Xin chào"}]}
    data.update(kw)
    return Storyboard.model_validate(data)


OLD_USER = ("Film title: Mèo\nCharacters: Lan (a girl)\nNarration lines (subtitle language): Xin chào\n"
            "Scenes: s01: Lan says softly in Thai, voice only\n\nWrite 5 title options.")


@pytest.mark.parametrize("kw, line", [
    ({"dialogue_language": "Vietnamese"}, "Spoken language: Vietnamese"),
    ({"dialogue_language": "NONE", "subtitle_language": "English"}, "Spoken language: none; write titles in English"),
    ({"dialogue_language": "none"},
     "Spoken language: none; write titles in the language of the narration lines"),
])
def test_suggest_titles_sends_spoken_language_line(claude, kw, line):
    assert planner.suggest_titles(_title_sb(**kw), backend="claude_code") == [{"text": "Mưa", "vi": ""}]
    sent = claude()
    lines = sent["p"].split("\n")
    assert lines[0] == "Film title: Mèo" and lines[1] == line
    assert sent["p"].replace(line + "\n", "", 1) == OLD_USER  # the rest is the old summary
    assert sent["system"] == planner.TITLE_SYSTEM and "overrides" in sent["system"]


def test_suggest_titles_without_language_is_unchanged(claude):
    planner.suggest_titles(_title_sb(), backend="claude_code")
    assert claude()["p"] == OLD_USER  # recorded from the code before the feature (0718918)
    # subtitle_language alone (hand-written YAML) adds nothing either
    planner.suggest_titles(_title_sb(subtitle_language="English"), backend="claude_code")
    assert claude()["p"] == OLD_USER
