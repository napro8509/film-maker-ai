"""Shared test setup: a fixed "today" for the model metadata and no access to the real settings folder."""
import shutil
from datetime import date
from pathlib import Path

import pytest

from filmmaker import manual_clip, model_meta


@pytest.fixture(autouse=True)
def _model_meta_isolation(monkeypatch, tmp_path):
    # Before the 2026-12-05 retirement, so older tests that still use veo-fast are never blocked by the real clock.
    # The real function stays reachable as model_meta._real_today.
    monkeypatch.setattr(model_meta, "today", lambda: date(2026, 11, 9))
    # Never read ~/.config/filmmaker (settings.json, models.yaml) of the machine running the tests.
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("FILMMAKER_MODELS", raising=False)
    manual_clip.clear_cache()  # ffprobe verdicts are cached per (path, size, mtime) in the process


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
