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
sb = {{"title": "Mèo", "scenes": [{{"id": "s01", "prompt": "a cat walks"}}]}}
print(json.dumps({{"type": "result", "is_error": False, "structured_output": sb}}))
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
