"""Planner through Claude Code, with a fake `claude` binary."""

import json
import os
import stat
import sys

from filmmaker import planner

FAKE = """#!{py}
import json, os, sys
if sys.argv[1:3] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True, "authMethod": "claude.ai"}})); sys.exit(0)
assert "-p" in sys.argv and "--json-schema" in sys.argv
open(os.environ["FAKE_CLAUDE_ENV"], "w").write(json.dumps(sorted(os.environ)))
schema = sys.argv[sys.argv.index("--json-schema") + 1]
if os.environ.get("FAKE_CLAUDE_SCHEMA"):
    open(os.environ["FAKE_CLAUDE_SCHEMA"], "w").write(schema)
if '"titles"' in schema:
    out = {{"titles": [{{"text": "“ความลับของกำไล”.", "vi": "Bí mật chiếc vòng"}}, {{"text": "ความลับของกำไล", "vi": "x"}},
                       {{"text": "  Mẹ chồng   ác độc ", "vi": "Mẹ chồng ác độc"}}, {{"text": "", "vi": ""}}]}}
else:
    out = {{"title": "Mèo", "scenes": [{{"id": "s01", "prompt": "a cat walks"}}]}}
    if os.environ.get("FAKE_CLAUDE_SCENE_CHARS"):
        out["characters"] = [{{"name": "Lan", "description": "a girl"}}]
        out["scenes"][0]["characters"] = ["Bao"]  # unknown name: must be dropped, not fail validation
print(json.dumps({{"type": "result", "is_error": False, "structured_output": out}}))
"""


def _fake(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text(FAKE.format(py=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CLAUDE_BIN", str(exe))
    monkeypatch.setenv("FAKE_CLAUDE_ENV", str(tmp_path / "env.json"))
    return tmp_path / "env.json"


def test_claude_code_backend_uses_subscription_not_api_key(tmp_path, monkeypatch):
    env_dump = _fake(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-passed")
    sb, used = planner.plan_auto("con mèo", backend="auto", scenes=1, aspect="9:16")
    assert used == "claude_code" and sb.title == "Mèo" and sb.aspect == "9:16"
    assert "ANTHROPIC_API_KEY" not in json.loads(env_dump.read_text())
    assert planner.claude_code_status() == {"available": True, "logged_in": True, "method": "claude.ai"}


def test_no_claude_and_no_key_gives_clear_error(monkeypatch):
    monkeypatch.setenv("CLAUDE_BIN", "/nonexistent/claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    try:
        planner.plan_auto("x", backend="auto")
    except RuntimeError as exc:
        assert "ANTHROPIC_API_KEY" in str(exc)
    else:
        raise AssertionError("expected an error")


def test_suggest_titles_via_claude_code_are_cleaned(tmp_path, monkeypatch):
    from filmmaker.storyboard import Storyboard

    _fake(tmp_path, monkeypatch)
    sb = Storyboard.model_validate({"title": "Chiếc vòng", "scenes": [{"id": "s01", "prompt": "x", "narration": "Mẹ ơi"}]})
    assert planner.suggest_titles(sb, backend="claude_code") == [
        {"text": "ความลับของกำไล", "vi": "Bí mật chiếc vòng"}, {"text": "Mẹ chồng ác độc", "vi": ""}]
    assert planner._clean_titles(["Cũ"], 5) == [{"text": "Cũ", "vi": ""}]  # old saved format
    assert "Mẹ ơi" in planner._story_summary(sb)


def _scene_def(schema: dict) -> dict:
    defs = schema.get("$defs", {})
    found = [d for k, d in defs.items() if k == "SceneDraft" or d.get("title") == "Scene"]
    assert found, f"no Scene def in schema: {sorted(defs)}"
    return found[0]


def test_claude_code_schema_has_no_scene_characters_and_strips_them(tmp_path, monkeypatch):
    _fake(tmp_path, monkeypatch)
    schema_dump = tmp_path / "schema.json"
    monkeypatch.setenv("FAKE_CLAUDE_SCHEMA", str(schema_dump))
    monkeypatch.setenv("FAKE_CLAUDE_SCENE_CHARS", "1")
    sb = planner.plan_with_claude_code("con mèo", scenes=1, aspect="9:16")
    scene = _scene_def(json.loads(schema_dump.read_text()))
    assert "prompt" in scene["properties"] and "characters" not in scene["properties"]
    assert [c.name for c in sb.characters] == ["Lan"]  # storyboard characters are kept
    assert all(s.characters is None for s in sb.scenes)
    assert sb.aspect == "9:16"


def test_api_plan_uses_draft_model_and_strips(monkeypatch):
    import types

    from filmmaker.storyboard import StoryboardDraft

    raw = {"title": "Mèo", "characters": [{"name": "Lan", "description": "a girl"}],
           "scenes": [{"id": "s01", "prompt": "a cat walks", "characters": ["Bao"]},
                      {"id": "s02", "prompt": "Lan smiles", "characters": []}]}
    calls = []

    class Parsed:  # what the SDK hands back; may carry keys the draft model does not know
        def model_dump(self, **kw):
            return json.loads(json.dumps(raw))

    class Messages:
        def parse(self, **kw):
            calls.append(kw)
            return types.SimpleNamespace(stop_reason="end_turn", parsed_output=Parsed())

    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda: types.SimpleNamespace(messages=Messages())
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    sb = planner.plan("con mèo", scenes=2, aspect="9:16")
    fmt = calls[0]["output_format"]
    assert fmt is StoryboardDraft
    scene = _scene_def(fmt.model_json_schema())
    assert "prompt" in scene["properties"] and "characters" not in scene["properties"]
    assert fmt.model_validate(raw).scenes[0].model_dump().get("characters") is None  # SDK side ignores the key
    assert all(s.characters is None for s in sb.scenes)
    assert [c.name for c in sb.characters] == ["Lan"] and sb.aspect == "9:16"
