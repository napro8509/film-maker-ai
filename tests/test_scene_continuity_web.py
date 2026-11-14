"""scene-continuity T4: project payload (scene_state / locations_state), location upload / assets / import, the
locations/images job, and continuity_outdated (AC10, AC13–AC15, AC28, AC30, AC31). Fake gflow, temp settings."""
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
    for var in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_CATALOG", "FLOW_UI_CMD"):
        monkeypatch.delenv(var, raising=False)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"pause_min": 0, "pause_max": 0, "retries": 0, "auto_backup": False}))
    monkeypatch.setattr(server, "SETTINGS_PATH", settings)
    app = server.create_app(tmp_path / "ws", token="t0k")
    c = TestClient(app)
    c.headers["X-Token"] = "t0k"
    c.ws = tmp_path / "ws"

    def calls():
        log = tmp_path / "calls.log"
        lines = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return [json.loads(l) for l in lines]

    c.calls = calls
    return c


def wait(c, job, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def run(c, pid, **body):
    r = c.post(f"/api/projects/{pid}/run", json=body)
    assert r.status_code == 200, r.text
    j = wait(c, r.json())
    assert j["status"] == "done", j["lines"]
    return j


ROOM = "long dark teak table, brass ceiling lamp"


def storyboard(**over) -> dict:
    sb = {"title": "Họp gia đình", "aspect": "9:16", "model": "veo-lite", "image_model": "nano2",
          "characters": [{"name": n, "description": f"{n} look", "image": f"assets/characters/{n.lower()}.png"}
                         for n in ("Minh", "Lan", "Tuan")],
          "locations": [{"name": "Phòng khách", "description": ROOM, "image": "assets/locations/room.png"}],
          "scenes": [{"id": sid, "mode": "keyframe", "image_prompt": f"Minh, Lan and Tuan at the table ({sid})",
                      "prompt": "they argue", "location": "Phòng khách"} for sid in ("s01", "s02", "s03")]}
    sb.update(over)
    return sb


def project(c, sb: dict | None = None, *, room: bool = True) -> str:
    pid = c.post("/api/projects", json={"title": "Họp gia đình"}).json()["id"]
    d = c.ws / pid / "assets"
    (d / "characters").mkdir(parents=True, exist_ok=True)
    for n in ("minh", "lan", "tuan"):
        (d / "characters" / f"{n}.png").write_bytes(f"{n}-pic".encode())
    if room:
        (d / "locations").mkdir(parents=True, exist_ok=True)
        (d / "locations" / "room.png").write_bytes(b"room-pic")
    put(c, pid, sb or storyboard())
    return pid


def put(c, pid, sb: dict) -> dict:
    r = c.put(f"/api/projects/{pid}", json={"storyboard": sb})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["errors"] == [], d["errors"]
    return d


def get(c, pid) -> dict:
    return c.get(f"/api/projects/{pid}").json()


def kf(c, pid, sid: str) -> Path:
    return c.ws / pid / "output" / "storyboard" / "keyframes" / f"{sid}.png"


# --- AC31 / AC10: payload ---------------------------------------------------------------------------------------

def test_payload_fields(client):
    pid = project(client)
    d = get(client, pid)
    s01, s02 = d["scene_state"]["s01"], d["scene_state"]["s02"]
    assert s01["refs"] == ["Minh", "Lan", "Tuan"]  # character names only, no cap
    assert s01["ref_labels"] == ["bối cảnh Phòng khách", "Minh", "Lan", "Tuan"]
    assert s01["location"] == "Phòng khách"
    assert s01["keyframe_from"] is None and s01["continuity_source"] is None and s01["continuity_outdated"] is False
    assert "Setting: long dark teak table, brass ceiling lamp." in s01["image_prompt"]
    assert d["locations_state"] == [{
        "name": "Phòng khách", "image_url": d["locations_state"][0]["image_url"], "image_missing": False,
        "image_prompt": d["locations_state"][0]["image_prompt"]}]
    assert d["locations_state"][0]["image_url"]
    assert d["locations_state"][0]["image_prompt"].startswith("Location reference image: long dark teak table")
    run(client, pid, action="keyframes")
    d = get(client, pid)
    s02 = d["scene_state"]["s02"]
    assert s02["ref_labels"] == ["keyframe s01", "Minh", "Lan", "Tuan"] and s02["refs"] == ["Minh", "Lan", "Tuan"]
    assert s02["keyframe_from"] == "s01" and s02["continuity_source"] == "s01"
    assert s02["continuity_outdated"] is False
    assert not any(c[0] == "video" for c in client.calls())  # AC29


def test_payload_without_location_unchanged(client):
    sb = storyboard()
    sb.pop("locations")
    for s in sb["scenes"]:
        s.pop("location")
    pid = project(client, sb)
    st = get(client, pid)["scene_state"]["s01"]
    assert st["refs"] == ["Minh", "Lan", "Tuan"] and st["ref_labels"] == ["Minh", "Lan", "Tuan"]
    assert st["location"] is None and st["continuity_source"] is None
    assert get(client, pid)["locations_state"] == []


def test_i2v_payload(client):
    sb = storyboard()
    sb["scenes"].append({"id": "s04", "mode": "i2v", "image": "assets/locations/room.png", "prompt": "Lan smiles",
                         "location": "Phòng khách"})
    pid = project(client, sb)
    st = get(client, pid)["scene_state"]["s04"]
    assert st["location"] is None and st["continuity_source"] is None
    assert st["refs"] == [] and st["ref_labels"] == []
    assert "Setting:" not in st["video_prompt"]


def test_locations_state_missing_image(client):
    pid = project(client, room=False)
    loc = get(client, pid)["locations_state"][0]
    assert loc["image_url"] is None and loc["image_missing"] is True


# --- AC30: continuity_outdated -----------------------------------------------------------------------------------

def outdated(c, pid, sid="s03") -> tuple[bool, str | None]:
    st = get(c, pid)["scene_state"][sid]
    return st["continuity_outdated"], st["continuity_source"]


def test_continuity_outdated_a(client):
    pid = project(client)
    run(client, pid, action="keyframes")
    assert outdated(client, pid) == (False, "s02")
    kf(client, pid, "s02").write_bytes(b"a regenerated s02")  # the fake always draws the same picture
    assert outdated(client, pid) == (True, "s02")
    assert get(client, pid)["scene_state"]["s03"]["status"] == "todo"


def test_continuity_outdated_b(client):
    pid = project(client)
    run(client, pid, action="keyframes", only=["s01", "s03"])
    assert get(client, pid)["scene_state"]["s03"]["keyframe_from"] is None
    assert outdated(client, pid) == (False, None)
    run(client, pid, action="keyframes", only=["s02"])
    assert outdated(client, pid) == (True, "s02")


def test_continuity_outdated_c(client):
    pid = project(client)
    run(client, pid, action="keyframes")
    kf(client, pid, "s02").write_bytes(b"a regenerated s02")
    put(client, pid, storyboard(continuity=False))
    assert outdated(client, pid) == (False, None)


def test_continuity_outdated_d(client):
    pid = project(client)
    run(client, pid, action="keyframes")
    kf(client, pid, "s02").write_bytes(b"a regenerated s02")
    assert outdated(client, pid)[0] is True
    run(client, pid, action="keyframes", only=["s03"], force_keyframes=["s03"])
    assert outdated(client, pid) == (False, "s02")


def test_continuity_outdated_e(client):
    pid = project(client)
    run(client, pid, action="keyframes")
    sb = storyboard()
    sb["scenes"] = [sb["scenes"][1], sb["scenes"][0], sb["scenes"][2]]  # s02, s01, s03
    put(client, pid, sb)
    assert outdated(client, pid) == (True, "s01")


def test_continuity_outdated_f(client):
    pid = project(client)
    run(client, pid, action="keyframes")
    kf(client, pid, "s02").write_bytes(b"a regenerated s02")
    sb = storyboard()
    sb["scenes"][1]["image_prompt"] = "something else"  # keyframe s02 is stale now
    put(client, pid, sb)
    assert outdated(client, pid) == (False, None)


# --- AC13 / AC28: the locations/images job ------------------------------------------------------------------------

def test_location_job_updates_yaml(client):
    sb = storyboard(locations=[{"name": "Phòng khách", "description": ROOM},
                               {"name": "Bếp", "description": "small kitchen"}])
    pid = project(client, sb, room=False)
    r = client.post(f"/api/projects/{pid}/locations/images", json={})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "locations"
    j = wait(client, r.json())
    assert j["status"] == "done", j["lines"]
    assert j["result"]["images"] == {"Phòng khách": "assets/locations/phong-khach.png", "Bếp": "assets/locations/bep.png"}
    assert j["result"]["failed"] == {}
    d = get(client, pid)
    assert [loc["image"] for loc in d["storyboard"]["locations"]] == list(j["result"]["images"].values())
    assert all(loc["image_url"] and not loc["image_missing"] for loc in d["locations_state"])
    assert all(c[:2] == ["image", "t2i"] for c in client.calls() if c[0] != "project")
    # nothing left to do; names that do not exist; force a named one
    assert client.post(f"/api/projects/{pid}/locations/images", json={}).status_code == 400
    r = client.post(f"/api/projects/{pid}/locations/images", json={"names": ["Nhà kho"]})
    assert r.status_code == 400 and "Không có bối cảnh: Nhà kho" in r.text
    j = wait(client, client.post(f"/api/projects/{pid}/locations/images",
                                 json={"names": ["Bếp"], "force": True}).json())
    assert j["status"] == "done" and list(j["result"]["images"]) == ["Bếp"]


def test_location_job_progress_keys(client):
    pid = project(client, storyboard(locations=[{"name": "Phòng khách", "description": ROOM}]), room=False)
    j = wait(client, client.post(f"/api/projects/{pid}/locations/images", json={}).json())
    assert j["status"] == "done", j["lines"]
    assert "loc:Phòng khách" in json.dumps(j.get("progress") or {}, ensure_ascii=False)


def test_location_job_errors(client):
    pid = project(client, storyboard(locations=[{"name": "Phòng khách", "description": " "}]), room=False)
    r = client.post(f"/api/projects/{pid}/locations/images", json={})
    assert r.status_code == 400 and "Bối cảnh #1 cần có name và description (hoặc image_prompt)" in r.text
    bad = storyboard()
    bad["scenes"][0]["location"] = "Nowhere"
    client.put(f"/api/projects/{pid}", json={"storyboard": bad})
    r = client.post(f"/api/projects/{pid}/locations/images", json={})
    assert r.status_code == 400 and "Storyboard còn lỗi" in r.text


def test_location_job_marks_changed(client):
    """AC28: the first picture of a location used by done scenes turns exactly those scenes "changed"."""
    sb = storyboard(locations=[{"name": "Phòng khách", "description": ROOM}])
    sb["scenes"][2].pop("location")
    pid = project(client, sb, room=False)
    run(client, pid, action="video")
    assert {k: v["status"] for k, v in get(client, pid)["scene_state"].items()} == {
        "s01": "done", "s02": "done", "s03": "done"}
    j = wait(client, client.post(f"/api/projects/{pid}/locations/images", json={}).json())
    assert j["status"] == "done", j["lines"]
    assert any("sẽ chuyển “Đã đổi” sau khi tạo ảnh bối cảnh: s01, s02" in l for l in j["lines"])
    assert {k: v["status"] for k, v in get(client, pid)["scene_state"].items()} == {
        "s01": "changed", "s02": "changed", "s03": "done"}


# --- AC13 / AC14 / AC15: upload, assets, import, duplicate -----------------------------------------------------------

def test_upload_location_kind(client):
    pid = project(client, room=False)
    r = client.post(f"/api/projects/{pid}/upload", files={"file": ("Phòng Khách.webp", b"x")}, data={"kind": "location"})
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "assets/locations/phong-khach.webp"
    images = [i["path"] for i in client.get(f"/api/projects/{pid}/assets").json()["images"]]
    assert "assets/locations/phong-khach.webp" in images and "assets/characters/minh.png" in images
    r = client.post(f"/api/projects/{pid}/upload", files={"file": ("a.mp3", b"x")}, data={"kind": "location"})
    assert r.status_code == 400


def test_import_copies_location_images(client, tmp_path):
    import yaml

    src = tmp_path / "elsewhere" / "room big.png"
    src.parent.mkdir()
    src.write_bytes(b"room")
    text = yaml.safe_dump({"title": "Nhập bối cảnh", "locations": [
        {"name": "Phòng khách", "description": ROOM, "image": str(src)},
        {"name": "Bếp", "description": "k", "image": "/does/not/exist.png"}],
        "scenes": [{"id": "a1", "mode": "t2v", "prompt": "hi", "location": "Phòng khách"}]}, allow_unicode=True)
    pid = client.post("/api/projects/import", files={"file": ("x.yaml", text.encode())}).json()["id"]
    d = get(client, pid)
    assert d["storyboard"]["locations"][0]["image"] == "assets/locations/room big.png"
    assert d["storyboard"]["locations"][1]["image"] == "/does/not/exist.png"
    assert (client.ws / pid / "assets" / "locations" / "room big.png").read_bytes() == b"room"
    assert d["locations_state"][0]["image_url"]


def test_duplicate_keeps_location_assets(client):
    pid = project(client)
    dup = client.post(f"/api/projects/{pid}/duplicate").json()["id"]
    assert (client.ws / dup / "assets" / "locations" / "room.png").is_file()
    d = get(client, dup)
    assert d["locations_state"][0]["image_url"] and not d["locations_state"][0]["image_missing"]
    assert d["scene_state"]["s01"]["ref_labels"][0] == "bối cảnh Phòng khách"


def test_import_same_file_names_and_non_images(client, tmp_path):
    """Two different pictures with the same file name are both kept (suffix); the same picture twice is copied
    once; files that are not images (scene, character or location) are never copied into the project."""
    import yaml

    a, b = tmp_path / "a" / "room.png", tmp_path / "b" / "room.png"
    for f, data in ((a, b"room A"), (b, b"room B")):
        f.parent.mkdir()
        f.write_bytes(data)
    secret = tmp_path / "id_rsa"
    secret.write_bytes(b"PRIVATE KEY")
    text = yaml.safe_dump({"title": "Trùng tên", "characters": [
        {"name": "Minh", "description": "m", "image": str(secret)}], "locations": [
        {"name": "A", "description": "a", "image": str(a)}, {"name": "B", "description": "b", "image": str(b)},
        {"name": "C", "description": "c", "image": str(a)}, {"name": "D", "description": "d", "image": str(secret)}],
        "scenes": [{"id": "a1", "mode": "i2v", "image": str(secret), "prompt": "hi"}]}, allow_unicode=True)
    pid = client.post("/api/projects/import", files={"file": ("x.yaml", text.encode())}).json()["id"]
    d = get(client, pid)["storyboard"]
    assert [loc["image"] for loc in d["locations"]] == [
        "assets/locations/room.png", "assets/locations/room-2.png", "assets/locations/room.png", str(secret)]
    base = client.ws / pid / "assets"
    assert (base / "locations" / "room.png").read_bytes() == b"room A"
    assert (base / "locations" / "room-2.png").read_bytes() == b"room B"
    assert d["characters"][0]["image"] == str(secret) and d["scenes"][0]["image"] == str(secret)
    assert not any(f.name == "id_rsa" for f in base.rglob("*")) if base.exists() else True
