"""QA additions for model-retirement: gaps in the original tests (AC5 with the CLI, AC17 for app.js)
and edge cases the spec does not spell out (unknown project, broken storyboard, empty input, re-runs)."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filmmaker import model_change, planner  # noqa: E402
from filmmaker.web import server  # noqa: E402
from tests.model_helpers import four_groups, set_scene, set_today, write_override, write_project  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FAKE = Path(__file__).parent / "fake_gflow.py"
RETIRING = ("veo-fast", "veo-quality")


# --- AC5: the CLI must not pull in the web stack -----------------------------------------

def test_meta_and_cli_do_not_import_fastapi():
    code = ("import sys, filmmaker.model_meta, filmmaker.cli, filmmaker.retirement, filmmaker.model_change; "
            "assert 'fastapi' not in sys.modules, 'fastapi imported'")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# --- AC17: no retiring defaults left in app.js -------------------------------------------

def test_app_js_has_no_retiring_defaults():
    js = (ROOT / "filmmaker/web/static/app.js").read_text(encoding="utf-8")
    for m in RETIRING:
        assert m not in js, m
    assert "veo-fast rẻ" not in js and "chuyển sang veo-quality" not in js


# --- edge cases ------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "calls.log"))
    for k in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_MODELS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": False, "logged_in": False})
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"pause_min": 0, "pause_max": 0, "retries": 0}))
    monkeypatch.setattr(server, "SETTINGS_PATH", path)
    c = TestClient(server.create_app(tmp_path / "ws", token="t0k"))
    c.headers["X-Token"] = "t0k"
    c.ws = tmp_path / "ws"
    c.override = tmp_path / "models.yaml"
    log = tmp_path / "calls.log"
    c.calls = lambda: [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return c


def test_model_change_unknown_project_is_404(client):
    for url in ("/api/projects/nope/model-change/preview", "/api/projects/nope/model-change"):
        assert client.post(url, json={"target": "veo-lite", "base": "x"}).status_code == 404


def test_model_change_on_broken_storyboard_is_400(client):
    d = client.ws / "bad"
    (d / "assets").mkdir(parents=True)
    p = d / "storyboard.yaml"
    p.write_text("title: x\nmodel: veo-fast\nscenes: []\n")
    before = p.read_bytes()
    assert client.post("/api/projects/bad/model-change/preview", json={"target": "veo-lite"}).status_code == 400
    r = client.post("/api/projects/bad/model-change", json={"target": "veo-lite", "base": model_change.content_hash(p)})
    assert r.status_code == 400 and p.read_bytes() == before


def test_model_change_missing_target_is_422(client):
    four_groups(client.ws)
    assert client.post("/api/projects/film/model-change/preview", json={}).status_code == 422


def test_apply_twice_with_include_second_is_noop(client, monkeypatch):
    """Spec AC36 only covers no `include`; with `include` the second run must not churn either."""
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01"}, {"id": "s02"}])
    set_scene(p, "s01", "done")
    body = {"target": "veo-lite", "include": ["s01"]}
    base = client.post("/api/projects/film/model-change/preview", json=body).json()["base"]
    assert client.post("/api/projects/film/model-change", json={**body, "base": base}).json()["changed"] is True
    after = p.read_bytes()
    pv = client.post("/api/projects/film/model-change/preview", json=body).json()
    assert pv["change"] == [] and pv["has_changes"] is False
    r = client.post("/api/projects/film/model-change", json={**body, "base": pv["base"]}).json()
    assert r["changed"] is False and p.read_bytes() == after


def test_apply_keeps_other_fields(client):
    """Only model/duration are touched; titles, prompts, narration and characters survive."""
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01", "narration": "Xin chào", "duration": 6}])
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    raw["style"] = "cinematic"
    raw["characters"] = [{"name": "An", "description": "cô gái"}]
    p.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    base = client.post("/api/projects/film/model-change/preview", json={"target": "veo-lite"}).json()["base"]
    assert client.post("/api/projects/film/model-change", json={"target": "veo-lite", "base": base}).status_code == 200
    after = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert after["model"] == "veo-lite" and after["style"] == "cinematic"
    assert after["characters"] == raw["characters"]
    assert after["scenes"][0]["narration"] == "Xin chào" and after["scenes"][0]["duration"] == 6


def test_estimate_after_unretire_override(client, monkeypatch):
    """Postponing the date in the override ("retires:" empty) lifts the block at once (spec: Google lùi ngày)."""
    set_today(monkeypatch, 2026, 12, 6)
    write_project(client.ws, "film", "veo-fast", [{"id": "s01"}])
    assert client.post("/api/projects/film/run", json={"action": "video"}).status_code == 409
    write_override(client.override, "models:\n  veo-fast:\n    retires:\n")
    assert client.post("/api/projects/film/estimate", json={"action": "video"}).json()["retired"] == []
    d = client.get("/api/projects/film").json()
    assert d["model_warning"] is None and d["scene_state"]["s01"]["model_status"] == "active"


def test_retired_scene_with_unknown_alias_not_blocked(client, monkeypatch):
    """An alias missing from the metadata is active: never warned, never blocked."""
    set_today(monkeypatch, 2026, 12, 6)
    write_project(client.ws, "film", "brand-new-model", [{"id": "s01"}])
    d = client.get("/api/projects/film").json()
    assert d["model_warning"] is None
    assert d["models_used"] == {"brand-new-model": {"status": "active", "retires_label": None, "known": False}}
    assert client.post("/api/projects/film/estimate", json={"action": "video"}).json()["retired"] == []


def test_banner_counts_on_exact_retirement_day(client, monkeypatch):
    """Boundary: 04/12 still 'ngừng ngày', 05/12 already 'đã ngừng từ'."""
    write_project(client.ws, "film", "veo-fast", [{"id": "s01"}])
    set_today(monkeypatch, 2026, 12, 4)
    assert client.get("/api/projects/film").json()["model_warning"]["lines"][0].startswith("Model veo-fast ngừng ngày 05/12")
    assert client.post("/api/projects/film/estimate", json={"action": "video"}).json()["retired"] == []
    set_today(monkeypatch, 2026, 12, 5)
    assert client.get("/api/projects/film").json()["model_warning"]["lines"][0].startswith("Model veo-fast đã ngừng từ 05/12")
    assert len(client.post("/api/projects/film/estimate", json={"action": "video"}).json()["retired"]) == 1


def test_readme_documents_check_storyboard():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert re.search(r"filmmaker check \S+\.yaml", readme)
    assert "FILMMAKER_MODELS" in readme
