"""Gemini Pro through the Antigravity CLI (`agy -p ... --output-format json`)."""

import json
import os
import stat

from pydantic import BaseModel

from filmmaker import ai


class _Out(BaseModel):
    ok: bool
    n: int


def _fake_agy(tmp_path, monkeypatch):
    log = tmp_path / "args.json"
    script = tmp_path / "agy"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(log)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "print('warning: something harmless')\n"
        "if '--json-schema' in sys.argv:\n"
        "    print(json.dumps({'status': 'SUCCESS', 'response': '{}', 'structured_output': {'ok': True, 'n': 2}}))\n"
        "else:\n"
        "    print(json.dumps({'status': 'SUCCESS', 'response': 'Xin chào từ agy\\n'}))\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("AGY_BIN", str(script))
    monkeypatch.setenv("AGY_LOGGED_IN", "1")
    return log


def test_agy_picked_in_auto_and_explicit(tmp_path, monkeypatch):
    _fake_agy(tmp_path, monkeypatch)
    assert ai.agy_status()["logged_in"] is True
    assert ai.pick_backend("auto", ai_model="gemini-pro") == "gemini_agy"
    assert ai.pick_backend("gemini_agy", ai_model="gemini-pro") == "gemini_agy"
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert ai.pick_backend("auto", ai_model="gemini-pro") == "gemini_api"  # a key still wins in auto


def test_agy_structured_and_text(tmp_path, monkeypatch):
    log = _fake_agy(tmp_path, monkeypatch)
    out = ai.ask("sys", "hỏi", _Out, backend="gemini_agy", ai_model="gemini-pro")
    assert out == _Out(ok=True, n=2)
    args = json.loads(log.read_text())
    assert args[0] == "-p" and "sys" in args[1] and "--json-schema" in args and "--model" not in args
    monkeypatch.setenv("FILMMAKER_AGY_MODEL", "gemini-4-carbon-high")
    assert ai.ask_text("sys", "hỏi", backend="gemini_agy", ai_model="gemini-pro") == "Xin chào từ agy"
    args = json.loads(log.read_text())
    assert args[args.index("--model") + 1] == "gemini-4-carbon-high"
    assert ai.draft_json("sys", "hỏi", _Out, backend="gemini_agy") == {"ok": True, "n": 2}


def test_agy_failure_message(tmp_path, monkeypatch):
    script = tmp_path / "agy"
    script.write_text("#!/bin/sh\necho 'please sign in first' >&2\nexit 3\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("AGY_BIN", str(script))
    try:
        ai.ask_text("s", "u", backend="gemini_agy", ai_model="gemini-pro")
    except RuntimeError as exc:
        assert "agy" in str(exc) and "đăng nhập" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
