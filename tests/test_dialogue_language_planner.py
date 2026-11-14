"""Spoken / subtitle language in the planner and in `filmmaker plan`, with a fake `claude` binary."""

import json
import stat
import sys

import pytest
import yaml
from click.testing import CliRunner

from filmmaker import planner
from filmmaker.cli import main
from filmmaker.storyboard import Storyboard

FAKE = """#!{py}
import json, os, sys
if sys.argv[1:3] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True, "authMethod": "claude.ai"}})); sys.exit(0)
dump = {{"p": sys.argv[sys.argv.index("-p") + 1], "system": sys.argv[sys.argv.index("--system-prompt") + 1]}}
open(os.environ["FAKE_CLAUDE_DUMP"], "w").write(json.dumps(dump))
out = {{"title": "Mèo", "scenes": [{{"id": "s01", "prompt": "a cat walks"}}],
        # Claude must not decide these: the request does
        "dialogue_language": "English", "subtitle_language": "French"}}
print(json.dumps({{"type": "result", "is_error": False, "structured_output": out}}))
"""


@pytest.fixture
def dump(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text(FAKE.format(py=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CLAUDE_BIN", str(exe))
    monkeypatch.setenv("FAKE_CLAUDE_DUMP", str(tmp_path / "dump.json"))
    return tmp_path / "dump.json"


# --- message to Claude (AC8, AC8b) ----------------------------------------------

def test_message_without_dialogue_is_unchanged():
    assert planner._user_message("ý", 6, "16:9", None) == "Idea: ý\nNumber of scenes: 6\nAspect ratio: 16:9"
    assert planner._user_message("ý", 6, "16:9", "Vietnamese") == (
        "Idea: ý\nNumber of scenes: 6\nAspect ratio: 16:9\nNarration language: Vietnamese")


def test_message_with_dialogue_language():
    base = "Idea: ý\nNumber of scenes: 3\nAspect ratio: 9:16"
    assert planner._user_message("ý", 3, "9:16", "English", "Vietnamese") == (
        base + "\nSpoken dialogue language: Vietnamese (every character who speaks speaks only Vietnamese)"
        "\nSubtitle (narration) language: English")
    assert planner._user_message("ý", 3, "9:16", None, "Thai") == (
        base + "\nSpoken dialogue language: Thai (every character who speaks speaks only Thai)")
    assert planner._user_message("ý", 3, "9:16", "English", "none") == (
        base + "\nSpoken dialogue: none (no character speaks; only ambient sound, sound effects and music)"
        "\nSubtitle (narration) language: English")


def test_system_prompt_rules():
    assert '"Subtitle (narration) language" or "Narration language"' in planner.SYSTEM
    assert 'if the message gives a "Spoken dialogue language"' in planner.SYSTEM
    assert 'If it says "Spoken dialogue: none", no character speaks' in planner.SYSTEM
    assert "written in the spoken dialogue language when the message gives one" in planner.SYSTEM
    assert "she says angrily in Thai, voice only" in planner.SYSTEM  # old behaviour without the new lines


def test_to_storyboard_drops_language_fields():
    sb = planner._to_storyboard({"title": "T", "scenes": [{"id": "s01", "prompt": "x"}],
                                 "dialogue_language": "x", "subtitle_language": "y"})
    assert sb.dialogue_language is None and sb.subtitle_language is None


# --- plan_auto (AC6, AC7, AC21) ---------------------------------------------------

def test_plan_auto_stores_languages_from_request(dump):
    sb, used = planner.plan_auto("con mèo", backend="claude_code", scenes=1, language_hint="English",
                                 dialogue_language="Vietnamese")
    assert used == "claude_code"
    assert (sb.dialogue_language, sb.subtitle_language) == ("Vietnamese", "English")  # not Claude's English/French
    sent = json.loads(dump.read_text())
    assert "Spoken dialogue language: Vietnamese" in sent["p"]
    assert "Subtitle (narration) language: English" in sent["p"]
    assert sent["system"] == planner.SYSTEM


def test_plan_auto_without_dialogue_sets_nothing(dump):
    sb, _ = planner.plan_auto("con mèo", backend="claude_code", scenes=1, language_hint="Vietnamese")
    assert sb.dialogue_language is None and sb.subtitle_language is None
    assert json.loads(dump.read_text())["p"].endswith("\nNarration language: Vietnamese")


def test_plan_auto_none_in_any_case(dump):
    sb, _ = planner.plan_auto("x", backend="claude_code", scenes=1, dialogue_language=" NONE ")
    assert sb.dialogue_language == "none" and sb.subtitle_language is None
    assert "Spoken dialogue: none" in json.loads(dump.read_text())["p"]


def test_plan_auto_bad_language_fails_before_claude(dump):
    with pytest.raises(ValueError):
        planner.plan_auto("x", backend="claude_code", dialogue_language="Viet\nnamese")
    assert not dump.exists()


def test_plan_auto_passes_dialogue_only_when_given(monkeypatch):
    seen = []

    def fake_plan(idea, **kw):
        seen.append(kw)
        return Storyboard.model_validate({"title": "T", "scenes": [{"id": "s01", "prompt": "x"}]})

    monkeypatch.setattr(planner, "plan", fake_plan)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    planner.plan_auto("x", backend="api", language_hint="English")
    sb, _ = planner.plan_auto("x", backend="api", language_hint="English", dialogue_language="Thai")
    assert seen == [{"language_hint": "English"}, {"language_hint": "English", "dialogue_language": "Thai"}]
    assert (sb.dialogue_language, sb.subtitle_language) == ("Thai", "English")


# --- titles (AC10) --------------------------------------------------------------

def _sb(**kw) -> Storyboard:
    data = {"title": "Mèo", "scenes": [{"id": "s01", "prompt": "a cat walks", "narration": "Xin chào"}]}
    data.update(kw)
    return Storyboard.model_validate(data)


def test_story_summary_spoken_language_line():
    old = _sb()
    lines = planner._story_summary(old).split("\n")
    assert lines[0] == "Film title: Mèo" and not any(x.startswith("Spoken language") for x in lines)
    assert planner._story_summary(_sb(dialogue_language="Vietnamese")).split("\n")[1] == "Spoken language: Vietnamese"
    assert planner._story_summary(_sb(dialogue_language="none", subtitle_language="English")).split("\n")[1] == (
        "Spoken language: none; write titles in English")
    assert planner._story_summary(_sb(dialogue_language="none")).split("\n")[1] == (
        "Spoken language: none; write titles in the language of the narration lines")
    # the rest of the summary is unchanged
    with_lang = planner._story_summary(_sb(dialogue_language="Thai")).split("\n")
    assert lines == [with_lang[0]] + with_lang[2:]


def test_title_system_prefers_spoken_language_line():
    assert '"Spoken language:" line' in planner.TITLE_SYSTEM and "overrides" in planner.TITLE_SYSTEM
    assert "Only if nothing\nsays what language is spoken" in planner.TITLE_SYSTEM


# --- CLI (AC15c, AC21, AC22) ------------------------------------------------------

def test_plan_help_mentions_dialogue_lang():
    out = CliRunner().invoke(main, ["plan", "--help"]).output
    assert "--dialogue-lang" in out and "--lang" in out


@pytest.mark.parametrize("bad", ["", "   ", "x" * 41, "Viet\nnamese"])
def test_cli_bad_dialogue_lang_exits_before_claude(dump, tmp_path, bad):
    out = tmp_path / "sb.yaml"
    r = CliRunner().invoke(main, ["plan", "ý", "-o", str(out), "--backend", "claude_code", "--dialogue-lang", bad])
    assert r.exit_code == 2, r.output
    assert "--dialogue-lang" in r.output
    assert not dump.exists() and not out.exists()


def test_cli_bad_lang_with_dialogue_lang_exits(dump, tmp_path):
    out = tmp_path / "sb.yaml"
    out.write_text("keep")
    r = CliRunner().invoke(main, ["plan", "ý", "-o", str(out), "--backend", "claude_code",
                                  "--dialogue-lang", "Thai", "--lang", "x" * 41])
    assert r.exit_code == 2, r.output
    assert not dump.exists() and out.read_text() == "keep"  # failed before the overwrite prompt


def _cli_plan(tmp_path, *args):
    out = tmp_path / "sb.yaml"
    r = CliRunner().invoke(main, ["plan", "ý", "-n", "1", "-o", str(out), "--backend", "claude_code", "--format", "short_drama", *args])
    assert r.exit_code == 0, r.output
    return yaml.safe_load(out.read_text())


def test_cli_saves_languages(dump, tmp_path):
    data = _cli_plan(tmp_path, "--dialogue-lang", "None", "--lang", " English ")
    assert data["dialogue_language"] == "none" and data["subtitle_language"] == "English"


def test_cli_lang_only_keeps_old_behaviour(dump, tmp_path):
    data = _cli_plan(tmp_path, "--lang", "English")
    assert "dialogue_language" not in data and "subtitle_language" not in data
    assert json.loads(dump.read_text())["p"].endswith("\nNarration language: English")


def test_cli_dialogue_lang_without_lang(dump, tmp_path):
    data = _cli_plan(tmp_path, "--dialogue-lang", "Thai")
    assert data["dialogue_language"] == "Thai" and "subtitle_language" not in data


# --- review fixes -----------------------------------------------------------------

@pytest.mark.parametrize("value", ["None", "NONE", " none "])
def test_message_none_any_case(value):
    msg = planner._user_message("ý", 3, "9:16", None, value)
    assert msg.endswith("\nSpoken dialogue: none (no character speaks; only ambient sound, sound effects and music)")


@pytest.mark.parametrize("opt", ["--dialogue-lang", "--lang"])
def test_cli_rejects_control_chars(dump, tmp_path, opt):
    out = tmp_path / "sb.yaml"
    args = ["--dialogue-lang", "Thai", "--lang", "English"]
    args[args.index(opt) + 1] = "Viet\x1bnam"
    r = CliRunner().invoke(main, ["plan", "ý", "-o", str(out), "--backend", "claude_code", *args])
    assert r.exit_code == 2, r.output
    assert opt in r.output and "ký tự không hợp lệ" in r.output
    assert not dump.exists() and not out.exists()


def test_cli_lang_uses_given_clean_function():
    from filmmaker.cli import _cli_lang
    from filmmaker.storyboard import clean_dialogue_language, clean_language
    assert _cli_lang("None", "--x", clean_dialogue_language) == "none"
    assert _cli_lang("None", "--dialogue-lang", clean_language) == "None"
