"""Storyboard board backend: scene_state.characters, GET/PUT /board, ui.json (ui_prefs), duplicate, jobs from the board."""

import json
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filmmaker.web import server, ui_prefs  # noqa: E402

FAKE = Path(__file__).parent / "fake_gflow.py"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
EMPTY = {"cards": {}, "videos": [], "view": None}


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
    c.ws = tmp_path / "ws"
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


def story(**extra):
    sb = {
        "title": "Bảng",
        "characters": [
            {"name": "Lan", "description": "a girl in a red coat", "image": "assets/characters/lan.png"},
            {"name": "Minh", "description": "a tall boy with glasses"},
        ],
        "scenes": [
            {"id": "s01", "mode": "keyframe", "prompt": "Lan walks in the rain"},
            {"id": "s02", "mode": "t2v", "prompt": "an empty street", "characters": ["minh", "Lan"]},
            {"id": "s03", "mode": "t2v", "prompt": "Lan and Minh laugh", "characters": []},
        ],
    }
    sb.update(extra)
    return sb


def make(c, **extra) -> str:
    pid = c.post("/api/projects", json={"title": "Bảng"}).json()["id"]
    img = c.ws / pid / "assets" / "characters" / "lan.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\x89PNG fake")
    r = c.put(f"/api/projects/{pid}", json={"storyboard": story(**extra)})
    assert r.status_code == 200 and r.json()["errors"] == [], r.text
    return pid


# ---- AC15: scene_state.characters ----

def test_scene_state_characters_auto_and_explicit(client):
    st = client.get(f"/api/projects/{make(client)}").json()["scene_state"]
    assert st["s01"]["characters"] == ["Lan"] and st["s01"]["characters_explicit"] is False
    assert st["s01"]["refs"] == ["Lan"]
    # explicit list: storyboard order, names as in the characters list, includes characters with no image
    assert st["s02"]["characters"] == ["Lan", "Minh"] and st["s02"]["characters_explicit"] is True
    assert st["s02"]["refs"] == ["Lan"]
    assert st["s03"]["characters"] == [] and st["s03"]["characters_explicit"] is True and st["s03"]["refs"] == []


def test_scene_state_characters_with_refs_off(client):
    st = client.get(f"/api/projects/{make(client, use_character_refs=False)}").json()["scene_state"]
    assert st["s01"]["characters"] == ["Lan"] and st["s01"]["refs"] == []
    assert st["s02"]["characters"] == ["Lan", "Minh"] and st["s02"]["refs"] == []


def test_detail_errors_contain_unknown_character(client):
    pid = make(client)
    sb = story()
    sb["scenes"][0]["characters"] = ["Bao"]
    d = client.put(f"/api/projects/{pid}", json={"storyboard": sb}).json()
    assert any("unknown character 'Bao'" in e for e in d["errors"])
    assert d["scene_state"] == {}  # invalid storyboard: no scene state (client draws from raw data)


# ---- AC13 (web): characters lists survive write_raw ----

def test_put_project_keeps_characters_lists(client):
    pid = make(client)
    raw = yaml.safe_load((client.ws / pid / "storyboard.yaml").read_text(encoding="utf-8"))
    scenes = {s["id"]: s for s in raw["scenes"]}
    assert "characters" not in scenes["s01"]
    assert scenes["s02"]["characters"] == ["minh", "Lan"]
    assert scenes["s03"]["characters"] == []
    assert client.get(f"/api/projects/{pid}").json()["storyboard"]["scenes"][2]["characters"] == []


# ---- AC16: GET /board ----

def test_get_board_default_no_file(client):
    pid = make(client)
    r = client.get(f"/api/projects/{pid}/board")
    assert r.status_code == 200 and r.json() == EMPTY
    assert not (client.ws / pid / "ui.json").exists()


def test_board_404(client):
    assert client.get("/api/projects/nope/board").status_code == 404
    assert client.put("/api/projects/nope/board", json=EMPTY).status_code == 404
    pid = make(client)
    assert client.put(f"/api/projects/{pid}/board", json=EMPTY, headers={"X-Token": "bad"}).status_code == 403


# ---- AC17: PUT /board ----

BOARD = {"cards": {"char:Lan": {"x": 10, "y": 20.5}, "kf:s01": {"x": -5, "y": 0}, "vid:s01": {"x": 400, "y": 0}},
         "videos": ["s01"], "view": {"x": 0, "y": -10, "zoom": 1.25}}


def test_put_board_keeps_other_keys_and_yaml_bytes(client):
    pid = make(client)
    d = client.ws / pid
    (d / "ui.json").write_text(json.dumps({"final": {"volume": 0.5}, "thumbnail_titles": [{"title": "T"}]}))
    yaml_before = (d / "storyboard.yaml").read_bytes()
    r = client.put(f"/api/projects/{pid}/board", json={**BOARD, "extra": 1})
    assert r.status_code == 200 and r.json() == BOARD  # extra keys dropped
    prefs = json.loads((d / "ui.json").read_text())
    assert prefs["board"] == BOARD and prefs["final"] == {"volume": 0.5} and prefs["thumbnail_titles"] == [{"title": "T"}]
    assert (d / "storyboard.yaml").read_bytes() == yaml_before
    assert client.get("/api/jobs/current").json() is None
    assert client.get(f"/api/projects/{pid}/board").json() == BOARD
    assert not (d / "ui.json.tmp").exists()


def test_board_and_final_settings_interleaved(client):
    pid = make(client)
    for i in range(3):
        assert client.put(f"/api/projects/{pid}/board", json={**BOARD, "videos": [f"s0{i}"]}).status_code == 200
        r = client.put(f"/api/projects/{pid}/final-settings", json={"volume": 0.1 * i})
        assert r.status_code == 200 and r.json()["volume"] == pytest.approx(0.1 * i)
    prefs = json.loads((client.ws / pid / "ui.json").read_text())
    assert prefs["board"]["videos"] == ["s02"] and prefs["final"]["volume"] == pytest.approx(0.2)
    d = client.get(f"/api/projects/{pid}").json()
    assert d["final_prefs"]["volume"] == pytest.approx(0.2)


def test_ui_prefs_update_concurrent_threads(tmp_path):
    def put(i):
        ui_prefs.update(tmp_path, lambda p: p.__setitem__(f"k{i}", i))

    threads = [threading.Thread(target=put, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert ui_prefs.read(tmp_path) == {f"k{i}": i for i in range(20)}


# ---- AC18: validation ----

@pytest.mark.parametrize("body, needle", [
    ({"cards": {"scene:s01": {"x": 1, "y": 1}}}, "Khoá card không hợp lệ"),
    ({"cards": {"kf:": {"x": 1, "y": 1}}}, "Khoá card không hợp lệ"),
    ('{"cards": {"kf:s01": {"x": NaN, "y": 1}}}', "phải là số"),
    ('{"cards": {"kf:s01": {"x": Infinity, "y": 1}}}', "phải là số"),
    ({"cards": {"kf:s01": {"x": True, "y": 1}}}, "phải là số"),
    ({"cards": {"kf:s01": {"x": "1", "y": 1}}}, "phải là số"),
    ({"cards": {"kf:s01": {"x": 1}}}, "phải là số"),
    ({"cards": [], "videos": []}, "card"),
    ({"videos": [1]}, "video"),
    ({"view": {"x": 0, "y": 0, "zoom": 0.1}}, "25%–200%"),
    ({"view": {"x": 0, "y": 0, "zoom": 3}}, "25%–200%"),
    ({"cards": {f"kf:s{i}": {"x": i, "y": 0} for i in range(1001)}}, "tối đa 1000"),
    ([], "đối tượng"),
    ("{not json", "không phải JSON hợp lệ"),
])
def test_put_board_validation(client, body, needle):
    pid = make(client)
    if isinstance(body, str):
        r = client.put(f"/api/projects/{pid}/board", content=body, headers={"Content-Type": "application/json"})
    else:
        r = client.put(f"/api/projects/{pid}/board", json=body)
    assert r.status_code == 400, r.text
    assert needle in r.json()["detail"]
    assert not (client.ws / pid / "ui.json").exists()


def test_put_board_limits_ok_and_too_large(client):
    pid = make(client)
    ok = {"cards": {f"kf:s{i}": {"x": i, "y": 0} for i in range(1000)}, "videos": [],
          "view": {"x": 0, "y": 0, "zoom": 0.25}}
    assert client.put(f"/api/projects/{pid}/board", json=ok).status_code == 200
    big = json.dumps({**EMPTY, "pad": "x" * 300_000})
    r = client.put(f"/api/projects/{pid}/board", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and "256 KB" in r.json()["detail"]
    assert len(client.get(f"/api/projects/{pid}/board").json()["cards"]) == 1000


def test_validate_board_normalises():
    out = ui_prefs.validate_board({"cards": {"vid:a b": {"x": 1, "y": 2, "w": 3}}, "view": {"x": 1, "y": 2, "zoom": 2,
                                                                                          "q": 1}, "z": 0})
    assert out == {"cards": {"vid:a b": {"x": 1, "y": 2}}, "videos": [], "view": {"x": 1, "y": 2, "zoom": 2}}
    assert ui_prefs.board_of({"board": {"cards": "bad"}}) == EMPTY
    b = ui_prefs.board_of({})
    b["cards"]["x"] = 1
    assert ui_prefs.EMPTY_BOARD == EMPTY  # a copy is returned


# ---- AC19: broken ui.json ----

@pytest.mark.parametrize("broken", ["{oops", "[]", '"text"'])
def test_broken_ui_json_get_empty_put_quarantines(client, broken):
    pid = make(client)
    d = client.ws / pid
    (d / "ui.json").write_text(broken)
    assert client.get(f"/api/projects/{pid}/board").json() == EMPTY
    assert client.get(f"/api/projects/{pid}").status_code == 200
    assert (d / "ui.json").read_text() == broken  # reading never writes
    assert client.put(f"/api/projects/{pid}/board", json=BOARD).status_code == 200
    moved = list(d.glob("ui.json.broken-*"))
    assert len(moved) == 1 and moved[0].read_text() == broken
    assert json.loads((d / "ui.json").read_text()) == {"board": BOARD}


def test_final_settings_quarantines_broken_file(client):
    pid = make(client)
    d = client.ws / pid
    for _ in range(2):  # same second: the second copy gets a -2 suffix
        (d / "ui.json").write_text("{oops")
        assert client.put(f"/api/projects/{pid}/final-settings", json={"volume": 0.3}).status_code == 200
    assert len(list(d.glob("ui.json.broken-*"))) == 2
    assert json.loads((d / "ui.json").read_text())["final"]["volume"] == 0.3


def test_suggested_titles_go_through_ui_prefs(client, monkeypatch):
    import filmmaker.planner as planner

    pid = make(client)
    d = client.ws / pid
    (d / "ui.json").write_text(json.dumps({"board": BOARD}))
    monkeypatch.setattr(server, "load_settings", lambda: {"anthropic_api_key": "sk-x"})
    monkeypatch.setattr(planner, "suggest_titles", lambda sb, **k: [{"title": "Mưa", "angle": "a"}])
    r = client.post(f"/api/projects/{pid}/thumbnail/titles")
    assert r.status_code == 200, r.text
    prefs = json.loads((d / "ui.json").read_text(encoding="utf-8"))
    assert prefs["board"] == BOARD and prefs["thumbnail_titles"]
    assert "Mưa" in (d / "ui.json").read_text(encoding="utf-8")  # ensure_ascii=False


# ---- AC20: duplicate ----

def test_duplicate_copies_board(client):
    pid = make(client)
    assert client.put(f"/api/projects/{pid}/board", json=BOARD).status_code == 200
    (client.ws / pid / "ui.json").write_text(json.dumps({"board": BOARD, "final": {"volume": 0.9}}))
    dup = client.post(f"/api/projects/{pid}/duplicate").json()["id"]
    assert client.get(f"/api/projects/{dup}/board").json() == BOARD
    assert "final" not in json.loads((client.ws / dup / "ui.json").read_text())
    # no board in the source: the copy gets no ui.json at all
    (client.ws / pid / "ui.json").unlink()
    dup2 = client.post(f"/api/projects/{pid}/duplicate").json()["id"]
    assert not (client.ws / dup2 / "ui.json").exists()


# ---- AC36, AC37, AC40: the board's buttons hit the existing endpoints ----

@needs_ffmpeg
def test_keyframes_from_board_no_video_calls(client):
    pid = make(client)
    r = client.post(f"/api/projects/{pid}/run", json={"action": "keyframes", "only": ["s01"]})
    assert r.status_code == 200, r.text
    j = wait(client, r.json())
    assert j["status"] == "done", j["lines"]
    kinds = [c[0] for c in client.calls()]
    assert "video" not in kinds and "image" in kinds
    assert client.get(f"/api/projects/{pid}").json()["scene_state"]["s01"]["keyframe_url"]


def test_estimate_creates_no_job(client):
    pid = make(client)
    r = client.post(f"/api/projects/{pid}/estimate", json={"action": "video", "only": ["s02"]})
    assert r.status_code == 200, r.text
    assert [x["id"] for x in r.json()["renders"]] == ["s02"]
    assert client.get("/api/jobs/current").json() is None and client.calls() == []


@needs_ffmpeg
def test_keyframe_failure_in_job_result(client, monkeypatch):
    # A keyframe-only run does not write scene state: its error is in the job result, keyed by scene id.
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "image:1:false")
    pid = make(client)
    j = wait(client, client.post(f"/api/projects/{pid}/run", json={"action": "keyframes", "only": ["s01"]}).json())
    assert j["status"] == "failed" and "fake" in j["result"]["failed"]["s01"], j
    assert "video" not in [c[0] for c in client.calls()]


@needs_ffmpeg
def test_video_failure_sets_scene_error(client, monkeypatch):
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video:1:false")
    pid = make(client)
    j = wait(client, client.post(f"/api/projects/{pid}/run", json={"action": "video", "only": ["s02"]}).json())
    assert j["status"] == "failed" and "s02" in j["result"]["failed"], j
    st = client.get(f"/api/projects/{pid}").json()["scene_state"]["s02"]
    assert st["status"] == "failed" and "fake" in st["error"]
    n = len(client.calls())
    time.sleep(0.3)
    assert len(client.calls()) == n  # no automatic resend


@needs_ffmpeg
def test_portrait_failure_in_job_result(client, monkeypatch):
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "image:1:false")
    pid = make(client)
    r = client.post(f"/api/projects/{pid}/characters/portraits", json={"names": ["Minh"]})
    assert r.status_code == 200, r.text
    j = wait(client, r.json())
    assert "Minh" in j["title"]
    assert "Minh" in j["result"]["failed"] and j["result"]["failed"]["Minh"]


# ---- review round 1: hardening ----

def test_put_board_huge_int_is_400_not_500(client):
    pid = make(client)
    body = '{"cards": {"char:a": {"x": 1' + "0" * 400 + ', "y": 0}}}'
    r = client.put(f"/api/projects/{pid}/board", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and "phải là số" in r.json()["detail"]
    view = '{"view": {"x": 0, "y": 0, "zoom": 1' + "0" * 400 + "}}"
    assert client.put(f"/api/projects/{pid}/board", content=view).status_code == 400
    assert not (client.ws / pid / "ui.json").exists()


def test_put_board_chunked_too_large_stops_early(client):
    pid = make(client)
    sent = []

    def gen():
        for _ in range(100):  # 100 x 64 KB = 6.4 MB, no Content-Length
            sent.append(1)
            yield b" " * 65536

    r = client.put(f"/api/projects/{pid}/board", content=gen(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and "256 KB" in r.json()["detail"]
    assert not (client.ws / pid / "ui.json").exists()

    def small():
        yield json.dumps(BOARD)[:10].encode()
        yield json.dumps(BOARD)[10:].encode()

    r = client.put(f"/api/projects/{pid}/board", content=small())
    assert r.status_code == 200 and r.json() == BOARD


def test_update_does_not_follow_tmp_symlink(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    d = tmp_path / "proj"
    d.mkdir()
    (d / "ui.json.tmp").symlink_to(outside)
    ui_prefs.update(d, lambda p: p.__setitem__("board", EMPTY))
    assert outside.read_text() == "secret"
    assert json.loads((d / "ui.json").read_text()) == {"board": EMPTY}
    assert not (d / "ui.json").is_symlink()
    assert sorted(p.name for p in d.iterdir()) == ["ui.json", "ui.json.tmp"]  # no temp file left behind


def test_update_read_oserror_does_not_quarantine(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "ui.json").write_text('{"final": {"volume": 0.5}}')
    real = Path.read_text

    def flaky(self, *a, **k):
        if self.name == "ui.json":
            raise PermissionError("EACCES")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", flaky)
    with pytest.raises(OSError):
        ui_prefs.update(d, lambda p: p.__setitem__("board", EMPTY))
    assert ui_prefs.read(d) == {}  # read() still never raises
    monkeypatch.setattr(Path, "read_text", real)
    assert not list(d.glob("ui.json.broken-*"))
    assert json.loads((d / "ui.json").read_text()) == {"final": {"volume": 0.5}}  # untouched
