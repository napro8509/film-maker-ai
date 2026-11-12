"""QA additions for the storyboard board (storyboard-board): gaps in the original tests and edge cases the spec
does not spell out (model change keeping `characters: []`, Unicode layout keys, whitespace / case in names,
renames done by hand in YAML, running things twice). Fake gflow only, no Flow, no credits."""
import json
import sys
from pathlib import Path

import pytest
import yaml

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filmmaker import planner  # noqa: E402
from filmmaker.storyboard import Storyboard  # noqa: E402
from filmmaker.web import server, ui_prefs  # noqa: E402
from tests.model_helpers import write_project  # noqa: E402

FAKE = Path(__file__).parent / "fake_gflow.py"
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "calls.log"))
    for k in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_MODELS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": False, "logged_in": False})
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"pause_min": 0, "pause_max": 0, "retries": 0}))
    monkeypatch.setattr(server, "SETTINGS_PATH", settings)
    c = TestClient(server.create_app(tmp_path / "ws", token="t0k"))
    c.headers["X-Token"] = "t0k"
    c.ws = tmp_path / "ws"
    log = tmp_path / "calls.log"
    c.calls = lambda: [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return c


def make(c, sb: dict) -> str:
    pid = c.post("/api/projects", json={"title": sb["title"]}).json()["id"]
    r = c.put(f"/api/projects/{pid}", json={"storyboard": sb})
    assert r.status_code == 200, r.text
    return pid


CHARS = [{"name": "Lan", "description": "a girl"}, {"name": "Bảo", "description": "an old man"}]


# --- AC13: model change (web) keeps characters lists, [] included -------------------------------------------------

def test_model_change_apply_keeps_scene_characters(client):
    p = write_project(client.ws, "film", "veo-fast", [{"id": "s01", "prompt": "Lan runs", "characters": []},
                                                     {"id": "s02", "prompt": "rain", "characters": ["Bảo"]},
                                                     {"id": "s03", "prompt": "Lan waits"}])
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    raw["characters"] = CHARS
    p.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    body = {"target": "veo-lite"}
    base = client.post("/api/projects/film/model-change/preview", json=body).json()["base"]
    r = client.post("/api/projects/film/model-change", json={**body, "base": base})
    assert r.status_code == 200 and r.json()["changed"] is True, r.text
    scenes = yaml.safe_load(p.read_text(encoding="utf-8"))["scenes"]
    assert scenes[0]["characters"] == [] and scenes[1]["characters"] == ["Bảo"]
    assert "characters" not in scenes[2]
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["model"] == "veo-lite"


# --- G6 / AC17: Unicode layout keys, idempotent saves, storyboard save never touches ui.json --------------------

def test_board_unicode_keys_round_trip_and_repeat(client):
    pid = make(client, {"title": "Bảng", "characters": CHARS, "scenes": [{"id": "s01", "prompt": "Bảo đi"}]})
    board = {"cards": {"char:Bảo": {"x": 1.5, "y": -2}, "char:#0": {"x": 0, "y": 0}, "kf:s01": {"x": 3, "y": 4}},
             "videos": ["s01"], "view": None}
    for _ in range(2):  # saving the same layout twice is harmless
        r = client.put(f"/api/projects/{pid}/board", json=board)
        assert r.status_code == 200 and r.json() == board
    assert client.get(f"/api/projects/{pid}/board").json() == board
    text = (client.ws / pid / "ui.json").read_text(encoding="utf-8")
    assert "char:Bảo" in text  # stored as Unicode, not \u escapes
    assert not list((client.ws / pid).glob("ui.json.tmp")) and not list((client.ws / pid).glob("ui.json.broken-*"))
    before = (client.ws / pid / "ui.json").read_bytes()
    sb = client.get(f"/api/projects/{pid}").json()["storyboard"]
    sb["scenes"][0]["characters"] = ["Bảo"]
    assert client.put(f"/api/projects/{pid}", json={"storyboard": sb}).json()["errors"] == []
    assert (client.ws / pid / "ui.json").read_bytes() == before  # a storyboard save leaves the layout alone


def test_board_keys_for_missing_things_are_kept_by_server(client):
    """The server does not know the storyboard when it checks a layout: stale keys are the client's to drop."""
    pid = make(client, {"title": "Bảng", "characters": CHARS, "scenes": [{"id": "s01", "prompt": "x"}]})
    board = {"cards": {"kf:gone": {"x": 0, "y": 0}}, "videos": ["gone"], "view": {"x": 0, "y": 0, "zoom": 1}}
    assert client.put(f"/api/projects/{pid}/board", json=board).json() == board


def test_board_view_zoom_limits_inclusive(client):
    pid = make(client, {"title": "Bảng", "scenes": [{"id": "s01", "prompt": "x"}]})
    for z in (0.25, 2):
        assert client.put(f"/api/projects/{pid}/board", json={"view": {"x": 0, "y": 0, "zoom": z}}).status_code == 200
    for z in (0.2499, 2.0001):
        assert client.put(f"/api/projects/{pid}/board", json={"view": {"x": 0, "y": 0, "zoom": z}}).status_code == 400


# --- G3 / AC8 / AC15: names with spaces / case in scenes[].characters -------------------------------------------

def test_scene_characters_trim_case_and_dedupe(tmp_path):
    sb = Storyboard.model_validate({"title": "t", "characters": CHARS, "scenes": [
        {"id": "s01", "prompt": "nobody here", "characters": ["  lan ", "LAN", "bảo"]}]})
    assert sb.scenes[0].characters == ["lan", "bảo"]  # first spelling kept, spaces dropped
    assert [c.name for c in sb.scene_characters(sb.scenes[0])] == ["Lan", "Bảo"]
    assert "Lan: a girl." in sb.image_prompt(sb.scenes[0]) and "Bảo: an old man." in sb.image_prompt(sb.scenes[0])


def test_scene_state_reports_canonical_names(client):
    pid = make(client, {"title": "Bảng", "characters": CHARS,
                        "scenes": [{"id": "s01", "prompt": "x", "characters": ["bảo", "LAN"]}]})
    st = client.get(f"/api/projects/{pid}").json()["scene_state"]["s01"]
    assert st["characters"] == ["Lan", "Bảo"] and st["characters_explicit"] is True
    # the YAML keeps what the user wrote
    raw = yaml.safe_load((client.ws / pid / "storyboard.yaml").read_text(encoding="utf-8"))
    assert raw["scenes"][0]["characters"] == ["bảo", "LAN"]


def test_name_with_spaces_counts_as_duplicate_when_listed():
    with pytest.raises(ValueError, match="must be unique and non-empty"):
        Storyboard.model_validate({"title": "t", "characters": [{"name": "Lan", "description": "a"},
                                                                {"name": " lan ", "description": "b"}],
                                   "scenes": [{"id": "s01", "prompt": "x", "characters": ["Lan"]}]})


# --- G3 last bullet: renaming in the YAML tab without fixing scenes[].characters is an error ------------------

def test_yaml_rename_without_scene_update_reports_error(client):
    pid = make(client, {"title": "Bảng", "characters": CHARS,
                        "scenes": [{"id": "s02", "prompt": "x", "characters": ["Bảo"]}]})
    text = (client.ws / pid / "storyboard.yaml").read_text(encoding="utf-8").replace("name: Bảo", "name: Bao")
    d = client.put(f"/api/projects/{pid}/yaml", json={"text": text}).json()
    assert any("scene s02: unknown character 'Bảo' in characters" in e for e in d["errors"]), d["errors"]
    # nothing can be run while the storyboard is invalid; estimate refuses too, no job, no gflow call
    r = client.post(f"/api/projects/{pid}/estimate", json={"action": "video", "only": ["s02"]})
    assert r.status_code == 400 and "unknown character" in r.json()["detail"]
    assert client.get("/api/jobs/current").json() is None and client.calls() == []


# --- G4: a planner storyboard with invented scene characters and a real one ---------------------------------------

def test_planner_keeps_storyboard_characters_and_drops_scene_ones():
    data = {"title": "t", "characters": [{"name": "Lan", "description": "a"}],
            "scenes": [{"id": "s01", "prompt": "Lan smiles", "characters": ["Nobody"]}, "not a dict"]}
    with pytest.raises(Exception):
        planner._to_storyboard(data)  # a non-dict scene still fails validation (not swallowed)
    data["scenes"].pop()
    sb = planner._to_storyboard(data)
    assert sb.scenes[0].characters is None and [c.name for c in sb.characters] == ["Lan"]
    assert data["scenes"][0]["characters"] == ["Nobody"]  # the input is not mutated


# --- ui_prefs: update() keeps a broken file aside once, then works normally --------------------------------------

def test_ui_prefs_update_after_quarantine_and_read_never_writes(tmp_path):
    (tmp_path / "ui.json").write_text("{oops")
    assert ui_prefs.read(tmp_path) == {}
    assert (tmp_path / "ui.json").read_text() == "{oops"
    ui_prefs.update(tmp_path, lambda p: p.__setitem__("a", 1))
    ui_prefs.update(tmp_path, lambda p: p.__setitem__("b", 2))
    assert json.loads((tmp_path / "ui.json").read_text()) == {"a": 1, "b": 2}
    assert len(list(tmp_path.glob("ui.json.broken-*"))) == 1


# --- AC41/AC42: board files are offline and the visible strings are Vietnamese -----------------------------------

def test_board_static_files_have_no_external_urls():
    static = ROOT / "filmmaker/web/static"
    for name in ("board.js", "board.css", "index.html"):
        text = (static / name).read_text(encoding="utf-8")
        for url in __import__("re").findall(r"https?://[^\s\"')]+", text):
            assert url.startswith("http://www.w3.org/2000/svg"), (name, url)
    html = (static / "index.html").read_text(encoding="utf-8")
    assert html.index("/static/board.js") < html.index("/static/app.js")  # board.js first (design)


# ---- QA round 2: CLI end-to-end with scene character lists (fake gflow, keyframes only, run twice) ----

CLI_SB = """
title: QA CLI
aspect: "16:9"
model: veo-lite
image_model: nano2
style: cinematic
characters:
  - name: Lan
    description: a young woman
    image: assets/characters/lan.png
  - name: Minh
    description: a young man
    image: assets/characters/minh.png
scenes:
  - id: s01
    mode: keyframe
    image_prompt: Lan and Minh in a cafe
    prompt: they talk
    characters: [minh]
  - id: s02
    mode: keyframe
    image_prompt: Lan and Minh walking
    prompt: they walk
  - id: s03
    mode: keyframe
    image_prompt: Lan in an empty room
    prompt: nothing
    characters: []
"""


def test_cli_keyframes_follow_scene_characters_and_rerun_is_noop(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from filmmaker.cli import main

    log = tmp_path / "calls.log"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(log))
    for k in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_MODELS"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "assets/characters").mkdir(parents=True)
    (tmp_path / "assets/characters/lan.png").write_bytes(b"lan")
    (tmp_path / "assets/characters/minh.png").write_bytes(b"minh")
    sb = tmp_path / "sb.yaml"
    sb.write_text(CLI_SB)
    before = sb.read_bytes()

    res = CliRunner().invoke(main, ["run", str(sb), "--keyframes-only", "--pause", "0", "--no-backup"])
    assert res.exit_code == 0, res.output
    calls = [json.loads(l) for l in log.read_text().splitlines()]
    images = {Path(c[c.index("-o") + 1]).stem: c for c in calls if c[:1] == ["image"]}
    assert not [c for c in calls if c[:1] == ["video"]]

    refs = lambda c: [Path(c[i + 1]).name for i, a in enumerate(c) if a == "--ref"]  # noqa: E731
    # s01: [minh] (lower case) -> only Minh's picture and description, Lan is mentioned but not sent.
    assert images["s01"][:2] == ["image", "i2i"] and refs(images["s01"]) == ["minh.png"]
    assert "Minh: a young man" in images["s01"][2] and "Lan: a young woman" not in images["s01"][2]
    # s02: no field -> name detection as before (both).
    assert refs(images["s02"]) == ["lan.png", "minh.png"]
    # s03: [] -> no reference at all (t2i), no description although Lan is named.
    assert images["s03"][:2] == ["image", "t2i"] and "Lan: a young woman" not in images["s03"][2]
    assert sb.read_bytes() == before  # the CLI never rewrites the list

    n = len(calls)
    res = CliRunner().invoke(main, ["run", str(sb), "--keyframes-only", "--pause", "0", "--no-backup"])
    assert res.exit_code == 0, res.output
    assert len(log.read_text().splitlines()) - n <= 1  # second run: nothing regenerated (at most a project lookup)
    assert not [l for l in log.read_text().splitlines()[n:] if '"image"' in l]


# ---- QA round 3: guards for the three round-2 frontend bugs (behaviour checked by hand in the browser) ----------

