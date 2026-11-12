"""Planner and allowed clip durations (AC18, AC19), with a fake `claude` binary and a fake `anthropic` module."""

import json
import stat
import sys
import types

import pytest

from filmmaker import planner
from filmmaker.storyboard import Storyboard, StoryboardDraft

LINE = "\nAllowed clip durations (seconds): 4, 6, 8"

FAKE = """#!{py}
import json, os, sys
dump = {{"p": sys.argv[sys.argv.index("-p") + 1]}}
open(os.environ["FAKE_CLAUDE_DUMP"], "w").write(json.dumps(dump))
out = {{"title": "T", "scenes": [{{"id": "s01", "prompt": "x", "duration": 5}}]}}
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


# --- AC18: message ------------------------------------------------------------------

def test_user_message_durations_line():
    base = "Idea: ý\nNumber of scenes: 6\nAspect ratio: 16:9"
    # no dialogue_language branch
    msg = planner._user_message("ý", 6, "16:9", "Vietnamese", durations=[4, 6, 8])
    assert msg == base + LINE + "\nNarration language: Vietnamese"
    assert "Aspect ratio: 16:9" + LINE + "\n" in msg and msg.endswith("\nNarration language: Vietnamese")
    assert planner._user_message("ý", 6, "16:9", None, durations=[4, 6, 8]) == base + LINE
    # dialogue_language branch
    msg = planner._user_message("ý", 6, "16:9", "English", "Thai", [4, 6, 8])
    assert "Aspect ratio: 16:9" + LINE + "\n" in msg
    assert msg.endswith("\nSubtitle (narration) language: English")
    # None / empty: unchanged
    for d in (None, []):
        assert planner._user_message("ý", 6, "16:9", "Vietnamese", durations=d) == (
            planner._user_message("ý", 6, "16:9", "Vietnamese"))
        assert planner._user_message("ý", 6, "16:9", "English", "Thai", d) == (
            planner._user_message("ý", 6, "16:9", "English", "Thai"))


def test_default_durations_is_default_model():
    assert planner.default_durations() == [4, 6, 8]  # veo-lite in the package models.yaml


def test_claude_code_message_has_durations(dump):
    planner.plan_with_claude_code("ý", scenes=1, language_hint="Vietnamese")
    p = json.loads(dump.read_text())["p"]
    assert "Aspect ratio: 16:9" + LINE + "\n" in p and p.endswith("\nNarration language: Vietnamese")


def test_claude_code_explicit_durations(dump):
    planner.plan_with_claude_code("ý", scenes=1, durations=None)
    assert "Allowed clip durations" not in json.loads(dump.read_text())["p"]
    planner.plan_with_claude_code("ý", scenes=1, durations=[6, 8])
    assert "\nAllowed clip durations (seconds): 6, 8" in json.loads(dump.read_text())["p"]


def test_claude_code_default_without_model_durations(dump, monkeypatch):
    monkeypatch.setattr(planner, "default_durations", lambda: None)
    planner.plan_with_claude_code("ý", scenes=1)
    assert "Allowed clip durations" not in json.loads(dump.read_text())["p"]


@pytest.fixture
def fake_anthropic(monkeypatch):
    sent = []

    class Messages:
        def parse(self, **kw):
            sent.append(kw)
            draft = StoryboardDraft.model_validate({"title": "T", "scenes": [{"id": "s01", "prompt": "x"}]})
            return types.SimpleNamespace(stop_reason="end_turn", parsed_output=draft)

    class Anthropic:
        def __init__(self, *a, **kw):
            self.messages = Messages()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Anthropic))
    return sent


def test_api_backend_message_has_durations(fake_anthropic):
    planner.plan("ý", scenes=2, language_hint="English", dialogue_language="Thai")
    content = fake_anthropic[0]["messages"][0]["content"]
    assert "Aspect ratio: 16:9" + LINE + "\n" in content
    assert content.endswith("\nSubtitle (narration) language: English")
    planner.plan("ý", scenes=2, durations=None)
    assert fake_anthropic[1]["messages"][0]["content"] == "Idea: ý\nNumber of scenes: 2\nAspect ratio: 16:9"


def test_plan_auto_sends_durations_via_backend(dump):
    sb, _ = planner.plan_auto("ý", backend="claude_code", scenes=1, language_hint="Vietnamese")
    assert LINE in json.loads(dump.read_text())["p"]
    assert sb.scenes[0].duration == 6  # fake Claude returned 5


# --- AC19: rounding up after normalize_new_storyboard -------------------------------

def test_plan_auto_rounds_up(monkeypatch):
    seen = []

    def fake_plan(idea, **kw):
        seen.append(kw)
        return Storyboard.model_validate({"title": "T", "scenes": [
            {"id": "s01", "prompt": "x", "duration": 5},
            {"id": "s02", "prompt": "x", "duration": 7},
            {"id": "s03", "prompt": "x"},
            {"id": "s04", "prompt": "x", "duration": 6},
            {"id": "s05", "prompt": "x", "duration": 3, "model": "veo-lite-lp"},  # Claude picked another model
            {"id": "s06", "prompt": "x", "duration": 9},
            {"id": "s07", "prompt": "x", "duration": 5, "model": "omni-1.2-flash"},  # no durations declared
        ]})

    monkeypatch.setattr(planner, "plan", fake_plan)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    sb, used = planner.plan_auto("x", backend="api")
    assert used == "api" and sb.model == "veo-lite"
    assert [s.duration for s in sb.scenes] == [6, 8, None, 6, 4, 8, 5]
    assert seen == [{}]  # plan_auto does not pass durations: plan() fetches them itself


def test_plan_auto_rounds_on_model_claude_picked(monkeypatch):
    monkeypatch.setattr(planner, "plan", lambda idea, **kw: Storyboard.model_validate(
        {"title": "T", "model": "veo-lite-lp", "scenes": [{"id": "s01", "prompt": "x", "duration": 7}]}))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    sb, _ = planner.plan_auto("x", backend="api")
    assert sb.model == "veo-lite-lp" and sb.scenes[0].duration == 8


# --- AC18: the old planner tests still pass, untouched ---------------------------------------------

