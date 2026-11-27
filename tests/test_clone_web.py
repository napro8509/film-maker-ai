from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filmmaker import clone as cl  # noqa: E402
from filmmaker import planner  # noqa: E402
from filmmaker.web import server  # noqa: E402

PLAN = {"setting": "kitchen", "camera": "static", "first_frame": "Mai sits.", "sounds": "",
        "beats": [{"start": 0.0, "end": 1.0, "action": "Mai smiles."}]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": False, "logged_in": False})
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"auto_backup": False, "planner": "api", "anthropic_api_key": "x"}))
    monkeypatch.setattr(server, "SETTINGS_PATH", settings)
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(settings))
    c = TestClient(server.create_app(tmp_path / "ws", token="t0k"))
    c.headers["X-Token"] = "t0k"
    c.ws = tmp_path / "ws"
    return c


def _wait(c, job, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] not in ("running", "queued"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _fake_init(calls):
    def init(source, folder, *, durations, model, mode, backend, log, **_):
        calls.append({"source": source, "durations": tuple(durations), "model": model, "mode": mode})
        (folder / "segments").mkdir(parents=True)
        (folder / "source").mkdir()
        (folder / "source" / "source.mp4").write_bytes(b"mp4")
        (folder / "segments" / "seg01.jpg").write_bytes(b"jpg")
        data = {"source": source, "title": "Bữa sáng", "file": "source/source.mp4", "duration": 15.23,
                "aspect": "9:16", "mode": mode, "model": model, "flow_project": None, "extra": "",
                "cast": [{"name": "Mẹ", "role_vi": "mẹ", "replaces": "the woman", "new_look": "blue dress", "ref": ""}],
                "segments": cl.plan_segments(15.23, [], durations)}
        cl.save(folder, data)
        return data
    return init


def test_clone_init_lists_and_serves_files(client, monkeypatch):
    c = client
    assert c.post("/api/clones", json={"url": "/etc/passwd"}).status_code == 400
    assert c.post("/api/clones", json={"url": "https://x.com/v", "mode": "edit", "model": "veo-lite"}).status_code == 400
    calls = []
    monkeypatch.setattr(cl, "init", _fake_init(calls))
    j = _wait(c, c.post("/api/clones", json={"url": "https://www.facebook.com/a/videos/1"}).json())
    assert j["status"] == "done", j["lines"]
    kid = j["result"]["clone"]
    # regen defaults to Veo Lite clips (4/6/8 s): Omni blocks scenes with small children
    assert calls[0]["mode"] == "regen" and calls[0]["model"] == "veo-lite" and calls[0]["durations"] == (4, 6, 8)
    assert [x["id"] for x in c.get("/api/clones").json()] == [kid]
    assert c.get("/api/projects").json() == []  # .clones is not a film project
    d = c.get(f"/api/clones/{kid}").json()
    assert [s["id"] for s in d["segments"]] == ["seg01", "seg02"]
    assert d["segments"][1]["chained"] and not d["segments"][0]["chained"]
    assert d["video_url"].startswith(f"/api/clones/{kid}/file/source/source.mp4")
    assert c.get(d["segments"][0]["thumb_url"]).content == b"jpg"
    assert c.get(f"/api/clones/{kid}/file/%2e%2e/%2e%2e/settings.json").status_code == 404
    assert c.get("/api/clones/..%2f..%2fx").status_code == 404
    assert c.delete(f"/api/clones/{kid}").json() == {"ok": True}
    assert c.get("/api/clones").json() == []


def _make(c, monkeypatch):
    monkeypatch.setattr(cl, "init", _fake_init([]))
    return _wait(c, c.post("/api/clones", json={"url": "https://x.com/v"}).json())["result"]["clone"]


def test_clone_save_cast_and_plan(client, monkeypatch):
    c = client
    kid = _make(c, monkeypatch)
    folder = c.ws / ".clones" / kid
    data = cl.load(folder)
    data["cast"][0]["ref"] = "character:Mẹ"
    data["segments"][0]["plan"] = PLAN
    cl.save(folder, data)
    (folder / "keyframes").mkdir()
    (folder / "keyframes" / "seg01.png").write_bytes(b"png")
    # same look: the Flow character is kept
    r = c.put(f"/api/clones/{kid}", json={"cast": [{"name": "Mẹ", "role_vi": "mẹ", "replaces": "the woman",
                                                    "new_look": "blue dress"}]})
    assert r.json()["cast"][0]["ref"] == "character:Mẹ"
    # a new look drops it so “Tạo nhân vật” makes a new one
    r = c.put(f"/api/clones/{kid}", json={"cast": [{"name": "Mai", "old_name": "Mẹ", "replaces": "the woman",
                                                    "new_look": "green dress"}]})
    assert r.json()["cast"][0]["ref"] == "" and r.json()["cast"][0]["name"] == "Mai"
    assert c.put(f"/api/clones/{kid}", json={"cast": [{"name": ""}]}).status_code == 400
    # editing actions keeps the start frame; changing the first frame moves it aside
    beats = [{"start": 0, "end": 1, "action": "Mai laughs."}, {"start": 1, "end": 2, "action": "  "}]
    r = c.put(f"/api/clones/{kid}", json={"segments": [{"id": "seg01", "plan": {"beats": beats}}]})
    assert r.json()["segments"][0]["plan"]["beats"] == [{"start": 0.0, "end": 1.0, "action": "Mai laughs."}]
    assert (folder / "keyframes" / "seg01.png").is_file()
    c.put(f"/api/clones/{kid}", json={"segments": [{"id": "seg01", "plan": {"first_frame": "Mai stands."}}]})
    assert not (folder / "keyframes" / "seg01.png").exists() and (folder / "keyframes" / "seg01.v1.png").is_file()
    bad = c.put(f"/api/clones/{kid}", json={"segments": [{"id": "seg01", "plan": {"beats": [{"start": "x"}]}}]})
    assert bad.status_code == 400


def test_clone_run_redo_and_edit_mode_needs_rights(client, monkeypatch):
    c = client
    kid = _make(c, monkeypatch)
    folder = c.ws / ".clones" / kid
    data = cl.load(folder)
    data["cast"][0]["ref"] = "character:Mẹ"
    for s in data["segments"]:
        s["plan"] = PLAN
    cl.save(folder, data)
    calls = []

    def fake_render(f, g, *, only, agree_rights, log, **_):
        d = cl.load(f)
        for s in d["segments"]:
            if s["id"] in only:
                calls.append(s["id"])
                (f / "renders").mkdir(exist_ok=True)
                (f / "renders" / f"{s['id']}.mp4").write_bytes(b"mp4")
                (f / "keyframes").mkdir(exist_ok=True)
                (f / "keyframes" / f"{s['id']}.png").write_bytes(b"png")
                s.update(status="done", render=f"renders/{s['id']}.mp4")
        cl.save(f, d)
        return d

    monkeypatch.setattr(cl, "render", fake_render)
    j = _wait(c, c.post(f"/api/clones/{kid}/run", json={}).json())
    assert j["status"] == "done", j["lines"]
    assert calls == ["seg01", "seg02"]
    # redo seg01: its old take is kept, and seg02 (which starts from seg01's last frame) must be re-joined
    d = c.post(f"/api/clones/{kid}/redo", json={"segment": "seg01"}).json()
    assert [s["status"] for s in d["segments"]] == ["todo", "todo"]
    assert (folder / "renders" / "seg01.v1.mp4").is_file() and (folder / "renders" / "seg02.v1.mp4").is_file()
    assert (folder / "keyframes" / "seg01.png").is_file() and not (folder / "keyframes" / "seg02.png").exists()
    assert "seg01" in d["segments"][1]["note"]
    assert c.post(f"/api/clones/{kid}/redo", json={"segment": "seg09"}).status_code == 400
    data = cl.load(folder)
    data["mode"] = "edit"
    cl.save(folder, data)
    assert c.post(f"/api/clones/{kid}/run", json={}).status_code == 400


def test_clone_upload_video_file(client, monkeypatch):
    c = client
    seen = []

    def init(source, folder, **kw):
        seen.append(Path(source).read_bytes())  # the uploaded file is there while init reads it
        return _fake_init([])(source, folder, **kw)

    monkeypatch.setattr(cl, "init", init)
    bad = c.post("/api/clones/upload", files={"file": ("x.txt", b"hi", "text/plain")})
    assert bad.status_code == 400
    assert c.post("/api/clones/upload", data={"mode": "edit", "model": "veo-lite"},
                  files={"file": ("a.mp4", b"mp4", "video/mp4")}).status_code == 400
    assert c.post("/api/clones/upload", files={"file": ("a.mp4", b"", "video/mp4")}).status_code == 400
    assert not list((c.ws / ".clones").iterdir())  # refused uploads leave nothing behind
    r = c.post("/api/clones/upload", data={"mode": "regen", "model": "omni-1.2-flash"},
               files={"file": ("Bữa sáng nhà Mai.MOV", b"video-bytes", "video/quicktime")})
    j = _wait(c, r.json())
    assert j["status"] == "done", j["lines"]
    kid = j["result"]["clone"]
    folder = c.ws / ".clones" / kid
    assert seen == [b"video-bytes"]
    assert not list(folder.glob("upload.*"))  # the temporary copy is gone
    d = c.get(f"/api/clones/{kid}").json()
    assert d["title"] == "Bữa sáng nhà Mai" and d["source"] == "" and d["model"] == "omni-1.2-flash"


def test_clone_upload_failed_init_cleans_up(client, monkeypatch):
    c = client

    def boom(source, folder, **_):
        raise RuntimeError("ffprobe failed")

    monkeypatch.setattr(cl, "init", boom)
    j = _wait(c, c.post("/api/clones/upload", files={"file": ("a.mp4", b"x", "video/mp4")}).json())
    assert j["status"] == "failed"
    assert not list((c.ws / ".clones").iterdir())


def test_clone_cast_picture_upload(client, monkeypatch):
    c = client
    kid = _make(c, monkeypatch)
    folder = c.ws / ".clones" / kid
    url = f"/api/clones/{kid}/cast/picture"
    assert c.post(url, data={"name": "Mẹ"}, files={"file": ("a.gif", b"g", "image/gif")}).status_code == 400
    assert c.post(url, data={"name": "Ai"}, files={"file": ("a.png", b"p", "image/png")}).status_code == 404
    d = c.post(url, data={"name": "Mẹ"}, files={"file": ("me.png", b"png1", "image/png")}).json()
    m = d["cast"][0]
    assert m["uploaded"] and m["ref"] == m["picture"] and m["ref"].startswith("cast/")
    assert c.get(m["picture_url"]).content == b"png1"
    assert cl.segment_refs(folder, cl.load(folder)) == [str(folder / m["ref"])]
    # a new picture replaces the old one (another extension too)
    d = c.post(url, data={"name": "Mẹ"}, files={"file": ("me.jpg", b"jpg2", "image/jpeg")}).json()
    assert [p.name for p in (folder / "cast").iterdir()] == [Path(d["cast"][0]["ref"]).name]
    # editing the look keeps your own picture (only a Flow character made from the look is dropped)
    r = c.put(f"/api/clones/{kid}", json={"cast": [{"name": "Mẹ", "replaces": "the woman", "new_look": "red"}]})
    assert r.json()["cast"][0]["ref"] == d["cast"][0]["ref"]
    # “Tạo nhân vật” then has nothing to make for her
    assert cl.make_cast(folder, gf=None, log=lambda *_: None)[0]["ref"] == d["cast"][0]["ref"]
    d = c.delete(url, params={"name": "Mẹ"}).json()
    assert d["cast"][0]["ref"] == "" and not d["cast"][0].get("uploaded")
