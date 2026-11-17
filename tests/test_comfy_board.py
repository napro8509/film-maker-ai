"""Tests for ComfyUI workflow graph & board flow integration (Task #20).

Covers:
1. `filmmaker.comfy` URL/settings validation, LiteGraph v0.4 `workflow.json` & `/prompt` `workflow_api.json`
   export and round-trip import (nodes, typed links, continuity chaining, scene groups, API templates).
2. End-to-end FastAPI endpoints (`/api/comfy/status`, `/api/comfy/interrupt`, `/api/projects/{pid}/comfy/workflow`,
   `/api/projects/{pid}/comfy/template`, `/api/projects/{pid}/comfy/run`, `PUT /board` with `cfg:`/`loc:`/`out:` keys)
   against a real in-process mock ComfyUI HTTP server.
3. Frontend `board.js` ComfyUI DAG model (`boardModel`, `bContinuityPrevSid`, `bCurve`, `bDropProblem`) executed under Node.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filmmaker import comfy  # noqa: E402
from filmmaker.gflow import Gflow  # noqa: E402
from filmmaker.pipeline import Pipeline  # noqa: E402
from filmmaker.storyboard import Storyboard, load_storyboard  # noqa: E402
from filmmaker.web import server  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BOARD_JS = ROOT / "filmmaker" / "web" / "static" / "board.js"
APP_JS = ROOT / "filmmaker" / "web" / "static" / "app.js"
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def _make_tiny_png() -> bytes:
    """Valid 64x64 solid-color PNG (via ffmpeg if present, else minimal 1x1 PNG)."""
    if shutil.which("ffmpeg"):
        proc = subprocess.run(
            ["ffmpeg", "-f", "lavfi", "-i", "color=c=#2d5a88:s=64x64:d=0.1", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
            capture_output=True,
            timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.startswith(b"\x89PNG"):
            return proc.stdout
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
        b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
    )


PNG_BYTES = _make_tiny_png()


@pytest.fixture
def fake_comfy_server():
    """Real HTTP server implementing ComfyUI's REST API endpoints on 127.0.0.1."""
    state: dict = {
        "prompts": [],
        "uploads": [],
        "interrupted": 0,
        "checkpoints": ["juggernautXL_v9.safetensors", "flux1-dev-fp8.safetensors"],
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _json(self, code: int, data: object) -> None:
            raw = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            sp = urlsplit(self.path)
            if sp.path == "/system_stats":
                return self._json(200, {
                    "system": {"comfyui_version": "0.3.14", "pytorch_version": "2.5.1"},
                    "devices": [{"name": "Apple M3 Max", "type": "mps", "vram_total": 38654705664, "vram_free": 25769803776}],
                })
            if sp.path == "/queue":
                return self._json(200, {"queue_running": [], "queue_pending": []})
            if sp.path == "/object_info/CheckpointLoaderSimple":
                return self._json(200, {
                    "CheckpointLoaderSimple": {
                        "input": {"required": {"ckpt_name": [state["checkpoints"]]}}
                    }
                })
            if sp.path.startswith("/object_info/"):
                return self._json(200, {})
            if sp.path.startswith("/history/"):
                pid = sp.path.split("/history/", 1)[1]
                return self._json(200, {
                    pid: {
                        "status": {"status_str": "success", "completed": True, "messages": []},
                        "outputs": {
                            "9": {
                                "images": [{"filename": f"{pid}.png", "subfolder": "", "type": "output"}]
                            }
                        },
                    }
                })
            if sp.path == "/view":
                qs = parse_qs(sp.query)
                assert qs.get("filename")
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(PNG_BYTES)))
                self.end_headers()
                self.wfile.write(PNG_BYTES)
                return
            self._json(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            if self.path == "/upload/image":
                state["uploads"].append(len(body))
                return self._json(200, {"name": "ref_uploaded.png", "subfolder": "filmmaker", "type": "input"})
            if self.path == "/prompt":
                payload = json.loads(body.decode("utf-8"))
                prompt_id = f"prompt-{len(state['prompts']) + 1}"
                state["prompts"].append(payload)
                return self._json(200, {"prompt_id": prompt_id, "number": len(state["prompts"]), "node_errors": {}})
            if self.path == "/interrupt":
                state["interrupted"] += 1
                return self._json(200, {"ok": True})
            self._json(404, {"error": "not found"})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield {"url": f"http://127.0.0.1:{port}", "state": state}
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"pause_min": 0, "pause_max": 0, "retries": 0}), encoding="utf-8")
    monkeypatch.setattr(server, "SETTINGS_PATH", settings_file)
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(settings_file))
    monkeypatch.delenv("COMFYUI_URL", raising=False)
    app = server.create_app(tmp_path / "ws", token="t0k")
    c = TestClient(app)
    c.headers["X-Token"] = "t0k"
    c.ws = tmp_path / "ws"
    return c


def _sample_storyboard() -> dict:
    return {
        "title": "Cà phê phố cổ",
        "aspect": "16:9",
        "model": "veo-lite",
        "image_model": "nano2",
        "style": "cinematic warm morning sunlight, 35mm film grain",
        "characters": [
            {"name": "Lan", "description": "young Vietnamese woman, white ao dai"},
            {"name": "Minh", "description": "young man with round glasses"},
        ],
        "locations": [
            {"name": "Quán cà phê", "description": "Hanoi old quarter sidewalk cafe, wooden stools"},
        ],
        "scenes": [
            {
                "id": "s01",
                "mode": "keyframe",
                "location": "Quán cà phê",
                "characters": ["Lan"],
                "image_prompt": "Lan sits by the wooden table holding a phin coffee",
                "prompt": "Lan smiles warmly as steam rises",
                "duration": 6,
            },
            {
                "id": "s02",
                "mode": "keyframe",
                "location": "Quán cà phê",
                "characters": ["Lan", "Minh"],
                "image_prompt": "Minh joins Lan at the table",
                "prompt": "Minh sets down two cups of iced tea",
                "duration": 8,
            },
            {
                "id": "s03",
                "mode": "t2v",
                "location": "Quán cà phê",
                "characters": [],
                "prompt": "Raindrops begin falling on the street outside",
                "duration": 4,
            },
        ],
    }


def _wait_job(c: TestClient, job: dict, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.08)
    raise AssertionError("ComfyUI job did not complete in time")


# --- 1. URL & Workflow Compiler Unit Tests ------------------------------------


def test_clean_comfyui_url_and_engine():
    assert comfy.clean_comfyui_url(None) == "http://127.0.0.1:8188"
    assert comfy.clean_comfyui_url("  ") == "http://127.0.0.1:8188"
    assert comfy.clean_comfyui_url("127.0.0.1:8188/") == "http://127.0.0.1:8188"
    assert comfy.clean_comfyui_url("https://comfy.local:9000/sub/") == "https://comfy.local:9000/sub"
    for bad in ("ftp://127.0.0.1:8188", "http://user:pass@127.0.0.1:8188", "http://127.0.0.1:8188?x=1", "http://bad\nhost"):
        with pytest.raises(ValueError):
            comfy.clean_comfyui_url(bad)
    assert comfy.clean_board_engine("comfyui") == "comfyui"
    assert comfy.clean_board_engine("flow") == "flow"
    with pytest.raises(ValueError):
        comfy.clean_board_engine("unknown")


def test_build_and_roundtrip_comfy_workflow():
    raw = _sample_storyboard()
    sb = Storyboard.model_validate(raw)
    board = {
        "cards": {"cfg:film": {"x": 24, "y": 32}, "char:Lan": {"x": 24, "y": 280}, "loc:Quán cà phê": {"x": 24, "y": 600}},
        "videos": ["s01", "s02"],
        "view": {"x": -40, "y": 12, "zoom": 0.85},
    }
    wf = comfy.build_comfy_workflow(sb, board)
    assert wf["version"] == 0.4
    types = [n["type"] for n in wf["nodes"]]
    assert "FilmMakerConfig" in types
    assert types.count("FilmMakerCharacter") == 2
    assert types.count("FilmMakerLocation") == 1
    assert types.count("FilmMakerKeyframe") == 2
    assert types.count("FilmMakerVideoSampler") == 3
    assert "FilmMakerSaveFilm" in types
    assert len(wf["groups"]) == 3

    # Continuity wire: s01 and s02 share location "Quán cà phê", so kf:s01 -> kf:s02 prev_image link exists!
    kf_nodes = {n["properties"]["fm_key"]: n for n in wf["nodes"] if n["type"] == "FilmMakerKeyframe"}
    prev_slot = next(inp for inp in kf_nodes["kf:s02"]["inputs"] if inp["name"] == "prev_image")
    assert prev_slot["link"] is not None

    # Modify positions and prompts inside the ComfyUI workflow JSON, then import back
    kf_nodes["kf:s01"]["pos"] = [420, 128]
    kf_nodes["kf:s01"]["widgets_values"][1] = "Updated opening shot of Lan at dawn"
    imported = comfy.import_comfy_workflow(wf, raw, current_board=board)
    assert imported["kind"] == "ui_workflow"
    assert imported["board"]["cards"]["kf:s01"] == {"x": 420.0, "y": 128.0}
    assert imported["board"]["view"] == {"x": -40.0, "y": 12.0, "zoom": 0.85}
    assert imported["storyboard"]["scenes"][0]["image_prompt"] == "Updated opening shot of Lan at dawn"
    assert imported["storyboard"]["scenes"][1]["characters"] == ["Lan", "Minh"]
    assert imported["storyboard"]["scenes"][1]["location"] == "Quán cà phê"


def test_api_prompt_export_and_custom_template_import():
    sb = Storyboard.model_validate(_sample_storyboard())
    api_wf = comfy.build_project_api_prompt(sb, checkpoint="flux1-dev.safetensors", steps=24, cfg=6.5)
    assert comfy.is_api_prompt_format(api_wf)
    assert api_wf["1"]["class_type"] == "CheckpointLoaderSimple"
    assert api_wf["1"]["inputs"]["ckpt_name"] == "flux1-dev.safetensors"

    custom_tmpl = {
        "10": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}", "clip": ["1", 1]}},
        "11": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}", "steps": "{{steps}}", "cfg": "{{cfg}}"}},
    }
    parsed = comfy.import_comfy_workflow(custom_tmpl, _sample_storyboard())
    assert parsed["kind"] == "api_template"
    built = comfy.build_single_image_api_prompt(
        "cinematic portrait of Lan",
        steps=28,
        cfg=5.5,
        seed=12345,
        template=parsed["template"],
    )
    assert built["10"]["inputs"]["text"] == "cinematic portrait of Lan"
    assert built["11"]["inputs"] == {"seed": 12345, "steps": 28, "cfg": 5.5}


# --- 2. Web Server API & End-to-End Fake ComfyUI Execution --------------------


def test_board_accepts_new_comfy_node_keys(client):
    pid = client.post("/api/projects", json={"title": "Comfy Flow"}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"storyboard": _sample_storyboard()})
    layout = {
        "cards": {
            "cfg:film": {"x": 40, "y": 40},
            "char:Lan": {"x": 40, "y": 300},
            "loc:Quán cà phê": {"x": 40, "y": 620},
            "kf:s01": {"x": 380, "y": 56},
            "vid:s01": {"x": 760, "y": 56},
            "out:final": {"x": 1140, "y": 220},
        },
        "videos": ["s01"],
        "view": {"x": 16, "y": 24, "zoom": 0.8},
    }
    r = client.put(f"/api/projects/{pid}/board", json=layout)
    assert r.status_code == 200 and r.json() == layout
    assert client.get(f"/api/projects/{pid}/board").json() == layout


def test_comfy_settings_and_workflow_endpoints(client, fake_comfy_server):
    # Save ComfyUI settings
    r = client.put("/api/settings", json={
        "comfyui_url": fake_comfy_server["url"],
        "comfyui_checkpoint": "juggernautXL_v9.safetensors",
        "comfyui_steps": 25,
        "comfyui_cfg": 6.5,
        "board_engine": "comfyui",
    })
    assert r.status_code == 200
    s = r.json()
    assert s["comfyui_url"] == fake_comfy_server["url"]
    assert s["board_engine"] == "comfyui"
    assert client.get("/api/env").json()["comfyui"]["engine"] == "comfyui"

    # Bad URL is rejected with 400
    assert client.put("/api/settings", json={"comfyui_url": "ftp://bad"}).status_code == 400

    # Live ComfyUI status check
    st = client.get("/api/comfy/status").json()
    assert st["online"] is True
    assert st["comfyui_version"] == "0.3.14"
    assert "juggernautXL_v9.safetensors" in st["checkpoints"]

    # Interrupt endpoint
    assert client.post("/api/comfy/interrupt", json={}).status_code == 200
    assert fake_comfy_server["state"]["interrupted"] == 1

    # Export UI workflow & API workflow, then re-import
    pid = client.post("/api/projects", json={"title": "Comfy Export"}).json()["id"]
    client.put(f"/api/projects/{pid}", json={"storyboard": _sample_storyboard()})
    wf_ui = client.get(f"/api/projects/{pid}/comfy/workflow?format=ui").json()
    assert wf_ui["version"] == 0.4 and len(wf_ui["nodes"]) >= 8
    wf_api = client.get(f"/api/projects/{pid}/comfy/workflow?format=api").json()
    assert comfy.is_api_prompt_format(wf_api)

    # Import UI workflow back
    imp = client.post(f"/api/projects/{pid}/comfy/workflow", json={"workflow": wf_ui, "apply_storyboard": True})
    assert imp.status_code == 200 and imp.json()["kind"] == "ui_workflow"

    # Import API prompt template and then delete it
    imp_t = client.post(f"/api/projects/{pid}/comfy/workflow", json={"workflow": wf_api})
    assert imp_t.status_code == 200 and imp_t.json()["kind"] == "api_template"
    assert imp_t.json()["project"]["has_comfy_template"] is True
    del_t = client.delete(f"/api/projects/{pid}/comfy/template")
    assert del_t.status_code == 200 and del_t.json()["project"]["has_comfy_template"] is False


@needs_ffmpeg
def test_comfy_run_pipeline_end_to_end(client, fake_comfy_server):
    client.put("/api/settings", json={
        "comfyui_url": fake_comfy_server["url"],
        "board_engine": "comfyui",
        "comfyui_steps": 12,
    })
    pid = client.post("/api/projects", json={"title": "Comfy Render"}).json()["id"]
    sb_data = _sample_storyboard()
    sb_data["scenes"] = sb_data["scenes"][:2]
    assert client.put(f"/api/projects/{pid}", json={"storyboard": sb_data}).json()["errors"] == []

    # Run full ComfyUI pipeline: portraits -> locations -> keyframes -> videos
    r = client.post(f"/api/projects/{pid}/comfy/run", json={"action": "pipeline"})
    assert r.status_code == 200, r.text
    job = _wait_job(client, r.json())
    assert job["status"] == "done", job["lines"]

    # Check storyboard saved character & location images, and Pipeline sees both scenes as DONE!
    proj = client.get(f"/api/projects/{pid}").json()
    chars = proj["storyboard"]["characters"]
    locs = proj["storyboard"]["locations"]
    assert all(c.get("image") for c in chars)
    assert all(l.get("image") for l in locs)
    assert proj["scene_state"]["s01"]["keyframe_url"]
    assert proj["scene_state"]["s01"]["clip_url"]
    assert proj["scene_state"]["s01"]["status"] == "done"
    assert proj["scene_state"]["s02"]["status"] == "done"
    assert fake_comfy_server["state"]["uploads"]  # reference images uploaded to ComfyUI

    sb_loaded = load_storyboard(client.ws / pid / "storyboard.yaml")
    pipe = Pipeline(sb_loaded, client.ws / pid / "output" / "storyboard", Gflow(dry_run=True, log=lambda *_: None))
    assert all(pipe.has_keyframe(s) for s in sb_loaded.scenes)
    assert all(pipe.is_done(s) for s in sb_loaded.scenes)


# --- 3. Frontend board.js Node Graph Model Tests ------------------------------


NODE_BOARD_HARNESS = r"""
const fs = require("fs");
const bsrc = fs.readFileSync(process.argv[2], "utf8");
const asrc = fs.readFileSync(process.argv[3], "utf8");

function cutFn(src, name) {
  const m = src.match(new RegExp("^(?:async )?function " + name + "\\(.*?^}\\n", "ms"));
  if (!m) throw new Error("missing function " + name);
  return m[0];
}

const MODES = [["keyframe", "Ảnh → Video"], ["t2v", "Chữ → Video"], ["i2v", "Ảnh của bạn → Video"]];
const S = {
  cur: null,
  job: null,
  settings: { board_engine: "comfyui" },
  env: { comfyui: { engine: "comfyui" } },
  charSaved: new WeakMap(),
  locSaved: new WeakMap(),
};
const progOn = () => false;
const currentSceneInJob = () => null;
const charImageUrl = (c) => (c && c.image ? "u/" + c.image : null);
const locImageUrl = (l) => (l && l.image ? "u/" + l.image : null);
const localGet = () => null;

const code = [
  bsrc.slice(0, bsrc.indexOf("/* ---------- DAG Auto-Layout ---------- */")),
  cutFn(bsrc, "bCurve"),
  cutFn(bsrc, "bDropProblem"),
  cutFn(asrc, "nameKey"),
  cutFn(asrc, "charKey"),
  cutFn(asrc, "charNameProblems"),
  cutFn(asrc, "locKey"),
  cutFn(asrc, "blankLocation"),
  cutFn(asrc, "locNameProblems"),
  "globalThis.out = { B, boardModel, bCurve, bDropProblem };",
].join("\n");

eval(code);
const { B, boardModel, bCurve, bDropProblem } = globalThis.out;

S.cur = {
  id: "demo",
  storyboard: {
    title: "Demo",
    aspect: "16:9",
    characters: [
      { name: "Lan", description: "girl", image: "lan.png" },
      { name: "Minh", description: "boy" },
    ],
    locations: [
      { name: "Phòng khách", description: "living room", image: "room.png" },
    ],
    scenes: [
      { id: "s01", mode: "keyframe", location: "Phòng khách", characters: ["Lan"], prompt: "a" },
      { id: "s02", mode: "keyframe", location: "Phòng khách", characters: ["Lan", "Minh"], prompt: "b" },
      { id: "s03", mode: "i2v", image: "p.png", prompt: "c" },
    ],
  },
  scene_state: {
    s01: { refs: ["Lan"], status: "done", clip_url: "/clip/s01.mp4" },
    s02: { refs: ["Lan"], status: "todo", clip_url: null },
    s03: { refs: [], status: "todo", clip_url: null },
  },
};

const m1 = boardModel();
B.showModelLinks = true;
const mWithModel = boardModel();

B.wireStyle = "spline";
const cSpline = bCurve({ x: 0, y: 0 }, { x: 100, y: 50 });
B.wireStyle = "orthogonal";
const cOrtho = bCurve({ x: 0, y: 0 }, { x: 100, y: 50 });
B.wireStyle = "straight";
const cStraight = bCurve({ x: 0, y: 0 }, { x: 100, y: 50 });

const drops = {
  charOk: bDropProblem("char:#1", "kf:s01"),
  charDup: bDropProblem("char:#0", "kf:s01"),
  charI2v: bDropProblem("char:#0", "vid:s03"),
  locOk: bDropProblem("loc:#0", "kf:s01"),
  contDup: bDropProblem("kf:s01", "kf:s02"),
  contSkip: bDropProblem("kf:s01", "vid:s02"),
};
S.cur.storyboard.scenes[1].continue_previous = false;
drops.contCanEnable = bDropProblem("kf:s01", "kf:s02");

process.stdout.write(JSON.stringify({
  keys: m1.cards.map((c) => c.key),
  linkKinds: m1.links.map((l) => `${l.kind}:${l.from}->${l.to}`),
  modelLinkCount: mWithModel.links.filter((l) => l.kind === "model").length,
  curves: { cSpline, cOrtho, cStraight },
  drops,
}));
"""


@needs_node
def test_board_js_comfy_dag_and_wire_logic(tmp_path):
    chk = subprocess.run(["node", "--check", str(BOARD_JS)], capture_output=True, text=True)
    assert chk.returncode == 0, chk.stderr

    hfile = tmp_path / "board_harness.js"
    hfile.write_text(NODE_BOARD_HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(hfile), str(BOARD_JS), str(APP_JS)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)

    assert data["keys"] == [
        "cfg:film",
        "char:#0",
        "char:#1",
        "loc:#0",
        "kf:s01",
        "vid:s01",
        "kf:s02",
        "vid:s02",
        "vid:s03",
        "out:final",
    ]
    assert "char:char:#0->kf:s01" in data["linkKinds"]
    assert "loc:loc:#0->kf:s01" in data["linkKinds"]
    assert "cont:kf:s01->kf:s02" in data["linkKinds"]
    assert "kfvid:kf:s01->vid:s01" in data["linkKinds"]
    assert "vidout:vid:s01->out:final" in data["linkKinds"]
    assert data["modelLinkCount"] == 3
    assert data["curves"]["cSpline"].startswith("M0 0C")
    assert data["curves"]["cOrtho"] == "M0 0L50 0L50 50L100 50"
    assert data["curves"]["cStraight"] == "M0 0L100 50"

    assert data["drops"]["charOk"] == ""
    assert "đã nối" in data["drops"]["charDup"]
    assert "không dùng ảnh nhân vật" in data["drops"]["charI2v"]
    assert "đã gắn bối cảnh" in data["drops"]["locOk"]
    assert "đã nối tiếp" in data["drops"]["contDup"]
    assert data["drops"]["contCanEnable"] == ""
