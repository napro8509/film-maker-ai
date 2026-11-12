"""Spoken-language rule: cleaning, Storyboard fields, for_flow_video and the 4 video calls of the pipeline."""
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from filmmaker.gflow import Gflow
from filmmaker.pipeline import Pipeline, RunOptions
from filmmaker.storyboard import (DIALOGUE_RULE, NO_DIALOGUE_RULE, NO_TEXT, Storyboard, StoryboardDraft,
                                  _dump_dict, clean_dialogue_language, clean_language, load_storyboard,
                                  save_storyboard)

FAKE = Path(__file__).parent / "fake_gflow.py"
VI = DIALOGUE_RULE.format(lang="Vietnamese")



def _said_once(text, rule):
    """The film's language sentence, or for a scene without lines the "nobody speaks" one, exactly once."""
    from filmmaker.storyboard import SILENT_SCENE_RULE

    return text.count(rule) + text.count(SILENT_SCENE_RULE) == 1

def _sb(**kw) -> Storyboard:
    data = {"title": "T", "scenes": [{"id": "s01", "mode": "t2v", "prompt": "Lan walks"}]}
    data.update(kw)
    return Storyboard.model_validate(data)


# --- clean_language / clean_dialogue_language -------------------------------

@pytest.mark.parametrize("raw", [None, "", "   ", "\t "])
def test_blank_language_is_unset(raw):
    assert clean_language(raw) is None
    assert clean_dialogue_language(raw) is None


def test_language_whitespace_is_collapsed():
    assert clean_language("  Brazilian   Portuguese ") == "Brazilian Portuguese"


@pytest.mark.parametrize("raw", ["Viet\nnamese", "Viet\rnamese", "x" * 41, "  " + "x" * 41, 5, True])
def test_bad_language_raises(raw):
    with pytest.raises(ValueError):
        clean_language(raw)
    with pytest.raises(ValueError):
        clean_dialogue_language(raw)


def test_language_length_limit_is_after_collapsing():
    assert clean_language("x" * 40) == "x" * 40
    assert clean_language("  " + "x" * 40 + "  ") == "x" * 40


@pytest.mark.parametrize("raw", ["none", "None", "NONE", " nOnE "])
def test_none_any_case_means_no_dialogue(raw):
    assert clean_dialogue_language(raw) == "none"
    assert clean_language(raw) == raw.strip()  # only the dialogue field knows "none"


# --- Storyboard fields --------------------------------------------------------

def test_fields_are_validated_on_storyboard_only():
    sb = _sb(dialogue_language="  Vietnamese ", subtitle_language=" English ")
    assert (sb.dialogue_language, sb.subtitle_language) == ("Vietnamese", "English")
    assert _sb(dialogue_language="NONE").dialogue_language == "none"
    assert _sb(dialogue_language="", subtitle_language="  ").dialogue_language is None
    assert _sb(dialogue_language=None).dialogue_language is None
    for bad in ("a\nb", "x" * 41):
        with pytest.raises(ValidationError):
            _sb(dialogue_language=bad)
        with pytest.raises(ValidationError):
            _sb(subtitle_language=bad)
    schema = json.dumps(StoryboardDraft.model_json_schema())
    assert "dialogue_language" not in schema and "subtitle_language" not in schema


# --- for_flow_video -----------------------------------------------------------

def test_no_language_keeps_for_flow():
    sb = _sb()
    for p in ("Lan walks", "Lan walks.", "", f"x. {NO_TEXT}"):
        assert sb.dialogue_rule() == ""
        assert sb.for_flow_video(p) == sb.for_flow(p)


def test_rule_before_no_text():
    sb = _sb(dialogue_language="Vietnamese")
    out = sb.for_flow_video("Lan walks")
    assert out == f"Lan walks. {VI} {NO_TEXT}"
    assert _said_once(out, VI) and out.index(VI) < out.index(NO_TEXT)
    # the user pasted NO_TEXT into the prompt: the rule still comes before it
    out = sb.for_flow_video(f"Lan walks. {NO_TEXT} Rain.")
    assert _said_once(out, VI) and out.count(NO_TEXT) == 1 and out.index(VI) < out.index(NO_TEXT)
    assert sb.for_flow_video("") == f"{VI} {NO_TEXT}"


def test_rule_kept_when_no_text_is_off():
    sb = _sb(dialogue_language="Vietnamese", no_text=False)
    assert sb.for_flow_video("Lan walks!") == f"Lan walks! {VI}"
    assert NO_TEXT not in sb.for_flow_video("Lan walks")


@pytest.mark.parametrize("pasted", [
    VI,                                   # exact sentence at the end
    f"{VI} She smiles at the camera.",    # pasted in the middle
    f"{VI.rstrip('.')} and she smiles.",  # pasted without the final dot, text after it
])
def test_pasted_rule_is_not_repeated(pasted):
    sb = _sb(dialogue_language="Vietnamese")
    out = sb.for_flow_video(f"Lan walks. {pasted}")
    assert out.count(VI.rstrip(".")) == 1
    assert out.endswith(NO_TEXT)


@pytest.mark.parametrize("lang", ["none", "NONE", "None"])
def test_no_dialogue_rule(lang):
    sb = _sb(dialogue_language=lang)
    out = sb.for_flow_video("Lan walks")
    assert out.count(NO_DIALOGUE_RULE) == 1 and "Spoken language:" not in out
    out = sb.for_flow_video(f"Lan walks. {NO_DIALOGUE_RULE} Rain falls.")
    assert out.count(NO_DIALOGUE_RULE) == 1


def test_for_flow_and_fingerprint_ignore_language():
    plain = _sb()
    for lang in ("Vietnamese", "none"):
        sb = _sb(dialogue_language=lang)
        s, s0 = sb.scenes[0], plain.scenes[0]
        assert sb.for_flow(sb.image_prompt(s)) == plain.for_flow(plain.image_prompt(s0))
        assert sb.for_flow("Lan walks") == plain.for_flow("Lan walks")
        assert sb.scene_fingerprint(s) == plain.scene_fingerprint(s0)


# --- saving -------------------------------------------------------------------

def test_dump_puts_languages_after_aspect(tmp_path):
    sb = _sb(dialogue_language="Vietnamese", subtitle_language="English")
    keys = list(_dump_dict(sb))
    assert keys[keys.index("aspect") + 1: keys.index("aspect") + 3] == ["dialogue_language", "subtitle_language"]
    save_storyboard(sb, tmp_path / "s.yaml")
    back = load_storyboard(tmp_path / "s.yaml")
    assert (back.dialogue_language, back.subtitle_language) == ("Vietnamese", "English")
    only = list(_dump_dict(_sb(dialogue_language="none")))
    assert only[only.index("aspect") + 1] == "dialogue_language" and "subtitle_language" not in only


def test_dump_without_languages_is_unchanged(tmp_path):
    sb = _sb()
    assert _dump_dict(sb) == sb.model_dump(exclude_none=True)
    assert list(_dump_dict(sb)) == list(sb.model_dump(exclude_none=True))
    save_storyboard(sb, tmp_path / "s.yaml")
    text = (tmp_path / "s.yaml").read_text()
    assert "dialogue_language" not in text and "subtitle_language" not in text
    assert text == yaml.safe_dump(sb.model_dump(exclude_none=True), allow_unicode=True, sort_keys=False, width=100)


# --- pipeline: every video call gets the rule, images do not -------------------

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
scenes:
  - id: s01
    mode: keyframe
    image_prompt: "Lan at a cafe"
    prompt: "slow push-in"
  - id: s02
    mode: t2v
    prompt: "Lan walks in the rain"
  - id: s03
    mode: t2v
    prompt: "Minh waits. All spoken dialogue is in Vietnamese; characters speak only Vietnamese. He sighs."
  - id: s04
    mode: i2v
    image: start.png
    prompt: "pan left"
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "start.png").write_bytes(b"png")
    (tmp_path / "lan.png").write_bytes(b"lan")
    log = tmp_path / "calls.log"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(log))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)

    def calls():
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    return tmp_path, calls


def _run(tmp_path, story):
    path = tmp_path / "story.yaml"
    path.write_text(story)
    pipe = Pipeline(load_storyboard(path), tmp_path / "out", Gflow(log=lambda *_: None),
                    log=lambda *_: None, sleep=lambda *_: None)
    return pipe.run(RunOptions(pause=(0, 0)))


def _check(calls, rule, kinds):
    videos = [c for c in calls() if c[0] == "video"]
    assert sorted({c[1] for c in videos}) == sorted(kinds)
    for c in videos:
        assert _said_once(" ".join(c), rule), c
    for c in calls():
        if c[0] == "image":
            assert "spoken dialogue" not in " ".join(c).lower(), c


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_pipeline_video_prompts_get_rule_once(env):
    tmp_path, calls = env
    report = _run(tmp_path, STORY)
    assert report.done == ["s01", "s02", "s03", "s04"], report
    _check(calls, VI, {"i2v", "r2v", "t2v"})


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_pipeline_no_dialogue_rule(env):
    tmp_path, calls = env
    report = _run(tmp_path, STORY.replace("dialogue_language: Vietnamese", "dialogue_language: NONE"))
    assert report.ok, report
    _check(calls, NO_DIALOGUE_RULE, {"i2v", "r2v", "t2v"})
    for c in calls():  # s03 (Minh) has the Vietnamese sentence pasted by hand: the user's own text stays
        if c[0] == "video" and "Minh" not in " ".join(c):
            assert "Spoken language:" not in " ".join(c), c


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_pipeline_fallback_t2v_gets_rule(env, monkeypatch):
    tmp_path, calls = env
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    story = STORY.replace('style: "cinematic"', 'style: "cinematic"\nfallback_t2v: true')
    _run(tmp_path, story)
    # the keyframe scene is re-sent as text (r2v here, since Lan has a reference image)
    text = [c for c in calls() if c[0] == "video" and c[1] in ("t2v", "r2v")]
    assert any("Lan at a cafe" in " ".join(c) for c in text)
    for c in calls():
        if c[0] == "video":
            assert _said_once(" ".join(c), VI), c


# --- review fix: control / format characters ----------------------------------

@pytest.mark.parametrize("raw", ["Viet\x00nam", "Viet\x1bnam", "Viet‮nam", "Viet​nam", "﻿Thai",
                                 "Thai\x7f", "Thai"])
def test_control_and_format_chars_rejected(raw):
    for clean in (clean_language, clean_dialogue_language):
        with pytest.raises(ValueError, match="ký tự không hợp lệ"):
            clean(raw)
    with pytest.raises(ValidationError):
        _sb(dialogue_language=raw)
    with pytest.raises(ValidationError):
        _sb(subtitle_language=raw)


@pytest.mark.parametrize("raw", ["Viet nam", "Viet\x0bnam", "Viet\x85nam", "Tiếng Việt", "日本語"])
def test_whitespace_and_unicode_letters_still_ok(raw):
    assert clean_language(raw) in ("Viet nam", "Tiếng Việt", "日本語")
