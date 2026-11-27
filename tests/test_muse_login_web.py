"""F18: the app knows whether Muse is signed in, refuses a Muse film at once when it is not, and signs in itself."""
import json
import time

from fastapi.testclient import TestClient

from filmmaker.web.server import create_app


def client(tmp_path, monkeypatch, signed_in: bool):
    monkeypatch.delenv("MUSE_UI_CMD", raising=False)
    prof = tmp_path / "muse-profile"
    prof.mkdir()
    if signed_in:
        (prof / "muse-state.json").write_text(json.dumps({"cookies": [
            {"name": "hatch_sess", "value": "v", "domain": ".muse.ai", "path": "/", "expires": time.time() + 86400}]}))
    monkeypatch.setenv("FILMMAKER_MUSE_PROFILE", str(prof))
    ws = tmp_path / "ws"
    (ws / "film").mkdir(parents=True)
    (ws / "film" / "storyboard.yaml").write_text(
        "title: Phim\nmodel: muse\nimage_model: muse\nscenes:\n- id: s01\n  mode: t2v\n  duration: 10\n  prompt: a cat walks\n")
    app = create_app(ws)
    return TestClient(app, headers={"x-token": app.state.token})


def test_signed_out_muse_film_is_refused_with_a_sign_in_reason(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch, signed_in=False)
    assert c.get("/api/muse/status").json()["signed_in"] is False
    r = c.post("/api/projects/film/autopilot", json={})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "muse_login"


def test_signed_in_status(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch, signed_in=True)
    st = c.get("/api/muse/status").json()
    assert st["signed_in"] is True and st["days_left"] <= 1
