"""The `  $ …` log line of the Flow chat-page calls (AC29–AC31): full options, short prompt, no --profile-dir."""
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from filmmaker import gflow as gflow_mod
from filmmaker.gflow import PROMPT_LOG_MAX, Gflow, short_prompt, ui_log_line

CMD_RE = re.compile(r"^\s+\$ ")  # app.js: lines matching this are shown as commands


@pytest.fixture
def ui_gflow(tmp_path, monkeypatch):
    """A dry-run Gflow whose flow_ui command/profile are fixed; any real subprocess makes the test fail."""
    monkeypatch.setenv("FLOW_UI_CMD", "flowpy flow_ui.py --extra")
    monkeypatch.setenv("FLOW_UI_PROFILE", str(tmp_path / "profile"))
    monkeypatch.setattr(Gflow, "ui_profile_dir", lambda self: tmp_path / "profile")

    def boom(*a, **k):
        raise AssertionError(f"subprocess must not run: {a!r}")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    lines: list[str] = []
    g = Gflow(dry_run=True, log=lines.append)
    return g, lines, tmp_path


def i2v(g, tmp_path, prompt="a cat walks", **kw):
    args = dict(refs=[tmp_path / "a.png", tmp_path / "b c.png"], aspect="16:9", model="veo-lite", duration=6,
                project="proj-1", out=tmp_path / "s01.mp4")
    args.update(kw)
    return g.ui_image_to_video(tmp_path / "s01 key.png", prompt, **args)


def test_short_prompt():
    assert short_prompt("") == ""
    assert short_prompt("  a\n\tb   c ") == "a b c"
    long = "x" * 500
    cut = short_prompt(long)
    assert len(cut) == PROMPT_LOG_MAX and cut.endswith("…") and cut[:-1] == "x" * (PROMPT_LOG_MAX - 1)
    assert short_prompt("y" * PROMPT_LOG_MAX) == "y" * PROMPT_LOG_MAX


def test_ui_log_line_empty_prompt_quoted():
    line = ui_log_line(["py", "f.py"], "i2v", ["--aspect", "9:16"], "")
    assert line == "  $ py f.py i2v --aspect 9:16 --prompt ''"


def test_ui_i2v_log_line(ui_gflow):
    g, lines, tmp = ui_gflow
    prompt = "Wide shot.\n" + "slow pan " * 60
    out, media = i2v(g, tmp, prompt=prompt)
    assert (out, media) == (tmp / "s01.mp4", None)  # dry run still logs, returns before running anything
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("  $ ") and CMD_RE.match(line)
    argv = shlex.split(line[4:])
    assert argv[:4] == ["flowpy", "flow_ui.py", "--extra", "i2v"]  # whole FLOW_UI_CMD, not cut to 2 tokens
    assert argv[argv.index("--image") + 1] == str(tmp / "s01 key.png")
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--ref"] == [str(tmp / "a.png"), str(tmp / "b c.png")]
    assert argv[argv.index("--aspect") + 1] == "16:9"
    assert argv[argv.index("--model") + 1] == "Veo 3.1 Lite"
    assert argv[argv.index("--duration") + 1] == "6"
    shown = argv[argv.index("--prompt") + 1]
    assert shown == short_prompt(prompt) and len(shown) <= PROMPT_LOG_MAX and shown.endswith("…")
    assert shown.startswith("Wide shot. slow pan")
    for hidden in ("--profile-dir", "--project", "-o", "--timeout", "proj-1", str(tmp / "profile")):
        assert hidden not in argv


def test_log_no_duration(ui_gflow):
    g, lines, tmp = ui_gflow
    i2v(g, tmp, duration=None, model=None, refs=None)
    argv = shlex.split(lines[0][4:])
    assert "--duration" not in argv and "--model" not in argv and "--ref" not in argv
    assert argv[argv.index("--prompt") + 1] == "a cat walks"


def test_log_quote_newline_one_line(ui_gflow):
    g, lines, tmp = ui_gflow
    prompt = "She says 'hi'\nand leaves.\n\nEnd"
    i2v(g, tmp, prompt=prompt)
    assert len(lines) == 1 and "\n" not in lines[0]
    assert CMD_RE.match(lines[0])
    argv = shlex.split(lines[0][4:])
    assert argv[argv.index("--prompt") + 1] == "She says 'hi' and leaves. End"


def test_ui_i2i_log_line(ui_gflow):
    g, lines, tmp = ui_gflow
    refs = [tmp / "c1.png", tmp / "c2.png"]
    out = g.ui_image_to_image("Portrait of\nAn  " + "z" * 400, refs, out=tmp / "k.png", aspect="9:16",
                              model="Nano Banana Pro", project="proj-1")
    assert out == tmp / "k.png"
    assert len(lines) == 1 and CMD_RE.match(lines[0])
    argv = shlex.split(lines[0][4:])
    assert argv[:4] == ["flowpy", "flow_ui.py", "--extra", "i2i"]
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--ref"] == [str(r) for r in refs]
    assert argv[argv.index("--aspect") + 1] == "9:16"
    assert argv[argv.index("--model") + 1] == "Nano Banana Pro"
    shown = argv[argv.index("--prompt") + 1]
    assert shown.startswith("Portrait of An z") and len(shown) == PROMPT_LOG_MAX and shown.endswith("…")
    for hidden in ("--profile-dir", "--project", "-o", "--timeout"):
        assert hidden not in argv


def test_profile_lookup_never_runs_gflow(tmp_path, monkeypatch):
    """Even without the class patch, FLOW_UI_PROFILE short-cuts the profile search (no `gflow auth status`)."""
    monkeypatch.setenv("FLOW_UI_PROFILE", str(tmp_path / "p"))
    monkeypatch.setattr(gflow_mod.Gflow, "auth_status",
                        lambda self: (_ for _ in ()).throw(AssertionError("auth status must not run")))
    assert Gflow(dry_run=True, log=lambda *_: None).ui_profile_dir() == Path(tmp_path / "p")
