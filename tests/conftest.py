"""Shared test setup: a fixed "today" for the model metadata and no access to the real settings folder."""
from datetime import date

import pytest

from filmmaker import model_meta


@pytest.fixture(autouse=True)
def _model_meta_isolation(monkeypatch, tmp_path):
    # Before the 2026-12-05 retirement, so older tests that still use veo-fast are never blocked by the real clock.
    # The real function stays reachable as model_meta._real_today.
    monkeypatch.setattr(model_meta, "today", lambda: date(2026, 11, 9))
    # Never read ~/.config/filmmaker (settings.json, models.yaml) of the machine running the tests.
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("FILMMAKER_MODELS", raising=False)
