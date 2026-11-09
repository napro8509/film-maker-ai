"""Web UI backend, driven end to end with the fake gflow (no credits, no browser)."""

import json
import shutil
import sys
import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filmmaker.web import server  # noqa: E402

FAKE = Path(__file__).parent / "fake_gflow.py"
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "calls.log"))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"pause_min": 0, "pause_max": 0, "retries": 0}))
    monkeypatch.setattr(server, "SETTINGS_PATH", settings)
    app = server.create_app(tmp_path / "ws", token="t0k")
    c = TestClient(app)
    c.headers["X-Token"] = "t0k"
    c.calls = lambda: [json.loads(l) for l in (tmp_path / "calls.log").read_text().splitlines()] \
        if (tmp_path / "calls.log").exists() else []
    return c


def wait(c, job, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def run(c, pid, **body):
    r = c.post(f"/api/projects/{pid}/run", json=body)
    assert r.status_code == 200, r.text
    return wait(c, r.json())


def test_token_and_host_are_required(client):
    assert client.post("/api/projects", json={"title": "x"}, headers={"X-Token": "wrong"}).status_code == 403
    assert client.get("/api/projects", headers={"Host": "evil.example"}).status_code == 403
    assert "t0k" in client.get("/").text


def test_full_flow_keyframes_then_video_then_stitch(client):
    pid = client.post("/api/projects", json={"title": "Ly cà phê", "source": "template"}).json()["id"]
    assert pid == "ly-ca-phe"
    d = client.get(f"/api/projects/{pid}").json()
    assert d["errors"] == [] and len(d["storyboard"]["scenes"]) == 3

    # Dry run spends nothing.
    j = run(client, pid, action="dry")
    assert j["status"] == "done" and client.calls() == []

    # Keyframes only: images, no video calls.
    j = run(client, pid, action="keyframes")
    assert j["status"] == "done", j["lines"]
    kinds = [c[:2] for c in client.calls()]
    assert ["video", "t2v"] not in kinds and kinds.count(["image", "t2i"]) == 2
    d = client.get(f"/api/projects/{pid}").json()
    assert d["scene_state"]["s01"]["keyframe_url"] and d["scene_state"]["s01"]["status"] == "todo"
    assert client.get(d["scene_state"]["s01"]["keyframe_url"]).status_code == 200

    # The estimate lists the 3 paid renders; keyframes already exist.
    est = client.post(f"/api/projects/{pid}/estimate", json={"action": "video"}).json()
    assert [r["id"] for r in est["renders"]] == ["s01", "s02", "s03"] and est["new_keyframes"] == []

    n_before = len(client.calls())
    j = run(client, pid, action="video")
    assert j["status"] == "done", j["lines"]
    new = client.calls()[n_before:]
    assert [c[:2] for c in new].count(["image", "t2i"]) == 0  # approved keyframes were reused
    d = client.get(f"/api/projects/{pid}").json()
    assert all(s["status"] == "done" for s in d["scene_state"].values()) and d["done"] == 3

    # Re-render one scene but keep its keyframe.
    n_before = len(client.calls())
    j = run(client, pid, action="video", only=["s01"], force=["s01"])
    new = client.calls()[n_before:]
    assert [c[:2] for c in new] == [["video", "i2v"]]

    j = run(client, pid, action="stitch", burn_subtitles=True)
    assert j["status"] == "done", j["lines"]
    d = client.get(f"/api/projects/{pid}").json()
    assert d["final_url"] and d["srt_url"] and d["has_final"]


def test_edit_marks_scene_changed_and_validation_errors(client):
    pid = client.post("/api/projects", json={"title": "A", "source": "template"}).json()["id"]
    run(client, pid, action="video")
    d = client.get(f"/api/projects/{pid}").json()
    sb = d["storyboard"]
    sb["scenes"][1]["prompt"] += " at sunset"
    d = client.put(f"/api/projects/{pid}", json={"storyboard": sb}).json()
    assert d["scene_state"]["s02"]["status"] == "changed" and d["scene_state"]["s01"]["status"] == "done"
    est = client.post(f"/api/projects/{pid}/estimate", json={"action": "video"}).json()
    assert [r["id"] for r in est["renders"]] == ["s02"]

    sb["scenes"].append({"id": "s04", "mode": "i2v", "prompt": "pan"})
    d = client.put(f"/api/projects/{pid}", json={"storyboard": sb}).json()
    assert any("image" in e for e in d["errors"])  # saved anyway, but flagged
    assert client.post(f"/api/projects/{pid}/run", json={"action": "video"}).status_code == 400


def test_upload_image_for_i2v_and_music(client, tmp_path):
    pid = client.post("/api/projects", json={"title": "B"}).json()["id"]
    png = Path(__file__).parent / "fake_gflow.py"  # any bytes; extension is what is checked
    r = client.post(f"/api/projects/{pid}/upload", files={"file": ("Ảnh 1.png", png.read_bytes())}, data={"kind": "image"})
    assert r.status_code == 200 and r.json()["path"] == "assets/anh-1.png"
    bad = client.post(f"/api/projects/{pid}/upload", files={"file": ("x.exe", b"x")}, data={"kind": "image"})
    assert bad.status_code == 400
    assets = client.get(f"/api/projects/{pid}/assets").json()
    assert assets["images"][0]["path"] == "assets/anh-1.png"
    # Path traversal is refused.
    assert client.get(f"/api/projects/{pid}/file/../../settings.json").status_code == 404


def test_stop_and_one_job_at_a_time(client, monkeypatch):
    monkeypatch.setattr(server, "load_settings", lambda: {"pause_min": 2, "pause_max": 2, "retries": 0, "profile": ""})
    pid = client.post("/api/projects", json={"title": "C", "source": "template"}).json()["id"]
    j = client.post(f"/api/projects/{pid}/run", json={"action": "video"}).json()
    assert client.post(f"/api/projects/{pid}/run", json={"action": "dry"}).status_code == 409
    time.sleep(0.5)
    client.post(f"/api/jobs/{j['id']}/stop")
    j = wait(client, j)
    assert j["status"] == "stopped"
    d = client.get(f"/api/projects/{pid}").json()
    assert d["done"] < 3


def test_import_duplicate_delete(client):
    yaml_text = "title: Nhập thử\nscenes:\n  - id: a1\n    mode: t2v\n    prompt: hello\n"
    pid = client.post("/api/projects/import", files={"file": ("x.yaml", yaml_text.encode())}).json()["id"]
    assert pid == "nhap-thu"
    dup = client.post(f"/api/projects/{pid}/duplicate").json()["id"]
    ids = {p["id"] for p in client.get("/api/projects").json()}
    assert {pid, dup} <= ids
    assert client.delete(f"/api/projects/{dup}").json()["ok"]
    assert dup not in {p["id"] for p in client.get("/api/projects").json()}


def test_settings_hide_api_key(client):
    s = client.put("/api/settings", json={"anthropic_api_key": "sk-ant-secret1234", "pause_min": 5, "pause_max": 1}).json()
    assert s["anthropic_api_key"] == "" and s["anthropic_key_set"] and s["anthropic_key_hint"] == "…1234"
    assert s["pause_max"] == 5  # max is never below min


def test_plan_via_claude_code(client, tmp_path, monkeypatch):
    from tests.test_planner import _fake

    _fake(tmp_path, monkeypatch)
    env = client.get("/api/env?refresh=true").json()
    assert env["planner"] == "claude_code" and env["claude_code"]["logged_in"]
    j = wait(client, client.post("/api/projects/plan", json={"idea": "mèo", "scenes": 1}).json())
    assert j["status"] == "done", j["lines"]
    assert client.get(f"/api/projects/{j['result']['project']}").json()["title"] == "Mèo"


def test_character_portraits_and_references(client):
    pid = client.post("/api/projects", json={"title": "Nhân vật", "source": "template"}).json()["id"]
    d = client.get(f"/api/projects/{pid}").json()
    sb = d["storyboard"]
    assert sb["characters"], "template should have a character"
    name = sb["characters"][0]["name"]
    assert d["characters_state"][0]["image_url"] is None and d["characters_state"][0]["portrait_prompt"]

    j = wait(client, client.post(f"/api/projects/{pid}/characters/portraits", json={}).json())
    assert j["status"] == "done", j["lines"]
    rel = j["result"]["images"][name]
    assert rel.startswith("assets/characters/")
    d = client.get(f"/api/projects/{pid}").json()
    assert d["storyboard"]["characters"][0]["image"] == rel
    assert d["characters_state"][0]["image_url"]
    assert any(p["path"] == rel for p in client.get(f"/api/projects/{pid}/assets").json()["images"])
    with_refs = [sid for sid, st in d["scene_state"].items() if st["refs"]]
    assert with_refs, "scenes naming the character should use its image"

    # Everyone has an image now: "all missing" has nothing to do; a named remake still works.
    assert client.post(f"/api/projects/{pid}/characters/portraits", json={}).status_code == 400
    assert client.post(f"/api/projects/{pid}/characters/portraits", json={"names": ["Nobody"]}).status_code == 400
    j = wait(client, client.post(f"/api/projects/{pid}/characters/portraits", json={"names": [name], "force": True}).json())
    assert j["status"] == "done"

    n = len(client.calls())
    j = run(client, pid, action="keyframes")
    assert j["status"] == "done", j["lines"]
    assert ["image", "i2i"] in [c[:2] for c in client.calls()[n:]]


def test_upload_character_image(client):
    pid = client.post("/api/projects", json={"title": "D"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/upload", files={"file": ("Lan.png", b"x")}, data={"kind": "character"})
    assert r.status_code == 200 and r.json()["path"] == "assets/characters/lan.png"


def test_i2v_refusal_is_reported_and_resettable(client, monkeypatch):
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    pid = client.post("/api/projects", json={"title": "E", "source": "template"}).json()["id"]
    j = run(client, pid, action="video")
    assert j["status"] == "failed"
    assert ["video", "t2v"] not in [c[:2] for c in client.calls()]
    d = client.get(f"/api/projects/{pid}").json()
    assert d["i2v_unsupported"] and "fake video i2v" in d["i2v_error"]
    d = client.post(f"/api/projects/{pid}/reset-i2v").json()
    assert not d["i2v_unsupported"] and d["i2v_error"] is None
    assert "gflow_version" in client.get("/api/env").json()


def test_thumbnail_action_and_make_adds_one(client):
    pid = client.post("/api/projects", json={"title": "Bìa", "source": "template"}).json()["id"]
    d = client.get(f"/api/projects/{pid}").json()
    assert d["thumbnail"]["url"] is None and d["thumbnail"]["picture_ready"] is False and d["thumbnail"]["prompt"]
    j = run(client, pid, action="thumbnail")
    assert j["status"] == "done", j["lines"]
    d = client.get(f"/api/projects/{pid}").json()
    assert d["thumbnail"]["url"] and d["thumbnail"]["picture_ready"]
    assert client.get(d["thumbnail"]["url"]).status_code == 200
    n = len(client.calls())
    assert run(client, pid, action="thumbnail")["status"] == "done"
    assert len(client.calls()) == n  # title re-printed, picture reused

    pid2 = client.post("/api/projects", json={"title": "Bìa 2", "source": "template"}).json()["id"]
    j = run(client, pid2, action="make")
    assert j["status"] == "done", j["lines"]
    assert client.get(f"/api/projects/{pid2}").json()["thumbnail"]["url"]


def test_thumbnail_titles_are_suggested_and_used_when_empty(client, tmp_path, monkeypatch):
    from tests.test_planner import _fake

    _fake(tmp_path, monkeypatch)
    client.get("/api/env?refresh=true")
    pid = client.post("/api/projects", json={"title": "Tựa", "source": "template"}).json()["id"]
    sb = client.get(f"/api/projects/{pid}").json()["storyboard"]
    sb["thumbnail"].pop("text", None)
    client.put(f"/api/projects/{pid}", json={"storyboard": sb})
    r = client.post(f"/api/projects/{pid}/thumbnail/titles")
    assert r.status_code == 200 and r.json()["titles"][0] == {"text": "ความลับของกำไล", "vi": "Bí mật chiếc vòng"}
    assert client.get(f"/api/projects/{pid}").json()["thumbnail"]["suggestions"] == r.json()["titles"]
    j = run(client, pid, action="thumbnail")
    assert j["status"] == "done", j["lines"] and j["result"]["title"] == "ความลับของกำไล"
    assert client.get(f"/api/projects/{pid}").json()["storyboard"]["thumbnail"]["text"] == "ความลับของกำไล"


@pytest.mark.skipif(not shutil.which("rclone"), reason="needs rclone")
def test_backup_auto_backup_and_restore(client, tmp_path):
    remote = str(tmp_path / "drive" / "FilmMaker")
    st = client.put("/api/settings", json={"backup_remote": remote, "auto_backup": True}).json()
    assert st["backup_remote"] == remote and st["auto_backup"] is True
    assert client.put("/api/settings", json={"backup_remote": "--config=/x"}).status_code == 400
    info = client.get("/api/backup").json()
    assert info["available"] and info["configured"] and info["auto"]

    pid = client.post("/api/projects", json={"title": "Ly cà phê", "source": "template"}).json()["id"]
    assert client.get("/api/backups").json()["items"] == []

    j = run(client, pid, action="dry")  # previews never back up
    assert not any("☁" in l for l in j["lines"])

    j = run(client, pid, action="video")
    assert j["status"] == "done", j["lines"]
    assert any("Đã backup xong" in l for l in j["lines"])
    cloud = Path(remote) / pid
    assert sorted(p.name for p in (cloud / "output/storyboard/clips").iterdir()) == ["s01.mp4", "s02.mp4", "s03.mp4"]
    d = client.get(f"/api/projects/{pid}").json()
    assert d["backup"]["remote"].endswith(pid)

    j = wait(client, client.post(f"/api/projects/{pid}/backup").json())
    assert j["status"] == "done", j["lines"]
    assert client.get("/api/backups").json()["items"] == [{"name": pid, "local": True}]

    # A project that is still on this machine is not overwritten.
    assert client.post("/api/backups/restore", json={"name": pid}).status_code == 409
    assert client.post("/api/backups/restore", json={"name": "../x"}).status_code == 400

    # Delete it (to .trash), then bring it back from Drive: all scenes are still done.
    client.delete(f"/api/projects/{pid}")
    j = wait(client, client.post("/api/backups/restore", json={"name": pid}).json())
    assert j["status"] == "done", j["lines"]
    d = client.get(f"/api/projects/{pid}").json()
    assert d["done"] == 3 and all(s["status"] == "done" for s in d["scene_state"].values())
    n = len(client.calls())
    j = run(client, pid, action="video")
    assert len(client.calls()) == n  # nothing re-rendered


@pytest.mark.skipif(not shutil.which("rclone"), reason="needs rclone")
def test_failed_auto_backup_does_not_fail_the_render(client, tmp_path):
    client.put("/api/settings", json={"backup_remote": "nosuchremote:FilmMaker", "auto_backup": True})
    pid = client.post("/api/projects", json={"title": "Ly cà phê", "source": "template"}).json()["id"]
    j = run(client, pid, action="video")
    assert j["status"] == "done", j["lines"]
    assert any("Backup lỗi" in l and "rclone config" in l for l in j["lines"])
    assert client.get(f"/api/projects/{pid}").json()["done"] == 3
