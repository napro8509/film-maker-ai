"""Shared test setup: a fixed "today" for the model metadata and no access to the real settings folder."""
import atexit
import json
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

# Safety net, set BEFORE anything imports filmmaker: `server.SETTINGS_PATH = settings_path()` is computed when the module
# is imported (during collection, before any fixture), and the real ~/.config/filmmaker/settings.json may have
# auto_backup on (rclone to the real Google Drive). Point it at a temp file with auto_backup false, so even code that
# runs outside a test's monkeypatch (collection, a job thread still alive after its test) never sees the real settings.
_SAFE_SETTINGS = Path(tempfile.mkdtemp(prefix="fm-test-settings-")) / "settings.json"
_SAFE_SETTINGS.write_text(json.dumps({"auto_backup": False}), encoding="utf-8")
os.environ["FILMMAKER_SETTINGS"] = str(_SAFE_SETTINGS)
os.environ.pop("FILMMAKER_MODELS", None)

# Same idea for the binaries that spend credits or touch the real Drive: unless the caller already set them, gflow and
# rclone point at a command that always fails, so a test that forgets its fake never runs the real ones. Tests that
# set GFLOW_BIN / RCLONE_BIN themselves (monkeypatch.setenv) are unaffected. The ".py" name keeps flow_ui from treating
# it as gflow-cli's Python (gflow._gflow_python).
_BLOCKED_BIN = _SAFE_SETTINGS.parent / "blocked_bin.py"
_BLOCKED_BIN.write_text(f"#!{sys.executable}\nimport sys\nsys.stderr.write('tests: real gflow/rclone are blocked "
                        "(set GFLOW_BIN / RCLONE_BIN to a fake)\\n')\nsys.exit(97)\n", encoding="utf-8")
_BLOCKED_BIN.chmod(0o755)
_REAL_RCLONE = shutil.which("rclone")  # for `local_rclone` only
for _name in ("GFLOW_BIN", "RCLONE_BIN"):
    os.environ.setdefault(_name, str(_BLOCKED_BIN))
os.environ["FILMMAKER_OFFLINE"] = "1"  # the editor never searches the web for sounds in tests
# CapCut: never write into the real CapCut projects folder (a missing folder = "CapCut not installed").
os.environ["FILMMAKER_CAPCUT_DRAFTS"] = str(_SAFE_SETTINGS.parent / "no-capcut")
atexit.register(shutil.rmtree, _SAFE_SETTINGS.parent, ignore_errors=True)

import pytest  # noqa: E402

from filmmaker import manual_clip, model_meta  # noqa: E402


@pytest.fixture(autouse=True)
def _never_open_capcut(monkeypatch):
    from filmmaker import capcut

    monkeypatch.setattr(capcut, "open_capcut", lambda: False)


@pytest.fixture(autouse=True)
def _model_meta_isolation(monkeypatch, tmp_path):
    # Before the 2026-12-05 retirement, so older tests that still use veo-fast are never blocked by the real clock.
    # The real function stays reachable as model_meta._real_today.
    monkeypatch.setattr(model_meta, "today", lambda: date(2026, 11, 9))
    # Never read ~/.config/filmmaker (settings.json, models.yaml) of the machine running the tests.
    settings = tmp_path / "settings.json"
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(settings))
    # server.SETTINGS_PATH was fixed at import time; move it to the same file as the env above. Tests that patch it
    # themselves (make_client & co.) still override this afterwards.
    srv = sys.modules.get("filmmaker.web.server")
    if srv is not None:
        monkeypatch.setattr(srv, "SETTINGS_PATH", settings)
    monkeypatch.delenv("FILMMAKER_MODELS", raising=False)
    manual_clip.clear_cache()  # ffprobe verdicts are cached per (path, size, mtime) in the process


@pytest.fixture
def local_rclone(monkeypatch):
    """The real rclone, for tests whose remote is a tmp folder (or a remote name that does not exist): it never
    reaches a real Drive. Every other test sees the blocked RCLONE_BIN set above."""
    if _REAL_RCLONE is None:
        pytest.skip("needs rclone")
    monkeypatch.setenv("RCLONE_BIN", _REAL_RCLONE)


@pytest.fixture
def fake_clip_check(monkeypatch):
    """Hand-downloaded clips are checked without ffprobe: a file is valid unless it starts with b"BROKEN"."""
    manual_clip.clear_cache()
    monkeypatch.setattr(manual_clip, "probe_clip", lambda p: not Path(p).read_bytes().startswith(b"BROKEN"))
    monkeypatch.setattr(manual_clip, "ffprobe_available", lambda: True)
    yield
    manual_clip.clear_cache()


@pytest.fixture
def no_ffprobe(monkeypatch):
    """No ffprobe on this machine. Only "ffprobe" is hidden: gflow must still be found."""
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: None if n == "ffprobe" else real(n, *a, **k))
    manual_clip.clear_cache()
    yield
    manual_clip.clear_cache()


@pytest.fixture(autouse=True)
def _no_real_subtitle_translation(monkeypatch):
    # Stitch / edit translate missing vi / th / en subtitle lines with Claude; tests never reach the real one
    # (a test that wants translations patches `planner._translate_items` itself).
    from filmmaker import planner

    def blocked(*_a, **_k):
        raise RuntimeError("tests: real Claude translation is blocked")

    monkeypatch.setattr(planner, "_translate_items", blocked)
