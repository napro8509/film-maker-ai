"""The /api/translate endpoint behind the per-scene "Dịch sang tiếng Việt" button (no AI, no Flow credits)."""
import pytest

pytest.importorskip("fastapi")

from filmmaker import translate as tr  # noqa: E402
from tests.test_model_web import make_client  # noqa: E402,F401


def test_translate_endpoint(make_client, monkeypatch):
    c = make_client()
    # Never hit the network in tests: fake the online machine-translation call.
    monkeypatch.setattr(tr, "_google_translate", lambda text, s, t, to: "VI:" + text)
    r = c.post("/api/translate", json={"texts": ["A woman smiles", "The sea at night"], "target": "vi"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["engine"] == "google"
    assert [x["text"] for x in d["translations"]] == ["VI:A woman smiles", "VI:The sea at night"]


def test_translate_endpoint_offline_fallback(make_client, monkeypatch):
    c = make_client()
    monkeypatch.setattr(tr, "_google_translate", lambda *a, **k: None)  # offline
    r = c.post("/api/translate", json={"texts": ["The woman walks"]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["engine"] == "glossary"
    assert "người phụ nữ" in d["translations"][0]["text"]


def test_translate_endpoint_empty(make_client):
    c = make_client()
    r = c.post("/api/translate", json={"texts": ["", "  "]})
    assert r.status_code == 200, r.text
    assert r.json() == {"translations": [], "engine": "none"}


def test_translate_endpoint_needs_token(make_client):
    c = make_client()
    r = c.post("/api/translate", json={"texts": ["hi"]}, headers={"X-Token": "wrong"})
    assert r.status_code == 403
