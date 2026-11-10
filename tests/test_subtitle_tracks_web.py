"""Web side of the three subtitle tracks: API fields, translation saved to the YAML, and the UI helpers (node)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filmmaker import planner  # noqa: E402
from filmmaker.web import server  # noqa: E402

from .test_web import FAKE, run  # noqa: E402

APP = Path(server.__file__).parent / "static" / "app.js"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "calls.log"))
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"pause_min": 0, "pause_max": 0, "retries": 0}))
    monkeypatch.setattr(server, "SETTINGS_PATH", settings)
    c = TestClient(server.create_app(tmp_path / "ws", token="t0k"))
    c.headers["X-Token"] = "t0k"
    c.ws = tmp_path / "ws"
    return c


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_stitch_translates_and_writes_three_tracks(client, monkeypatch):
    monkeypatch.setattr(planner, "_translate_items",
                        lambda lines, **kw: {k: {"id": k, "vi": v, "th": "ไทย " + k, "en": "EN " + k}
                                             for k, v in lines.items()})
    pid = client.post("/api/projects", json={"title": "Ly", "source": "template"}).json()["id"]
    # pretend a planner backend exists (no real Claude in tests)
    monkeypatch.setattr(planner, "claude_code_binary", lambda: "/bin/true")
    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": True, "logged_in": True, "method": "x"})
    assert run(client, pid, action="video")["status"] == "done"
    j = run(client, pid, action="stitch", burn_subtitles=True, burn_language="th")
    assert j["status"] == "done", j["lines"]
    d = client.get(f"/api/projects/{pid}").json()
    assert set(d["srt_urls"]) == {"vi", "th", "en"} and all(d["srt_urls"].values())
    assert "ไทย s01" in client.get(d["srt_urls"]["th"]).text
    raw = yaml.safe_load((client.ws / pid / "storyboard.yaml").read_text(encoding="utf-8"))
    s01 = next(s for s in raw["scenes"] if s["id"] == "s01")
    assert s01["subtitles"] == {"th": "ไทย s01", "en": "EN s01"}


def test_final_settings_keep_burn_language(client):
    pid = client.post("/api/projects", json={"title": "Ly", "source": "template"}).json()["id"]
    r = client.put(f"/api/projects/{pid}/final-settings", json={"burn_subtitles": True, "burn_language": "en"})
    assert r.json()["burn_language"] == "en"
    assert client.get(f"/api/projects/{pid}").json()["final_prefs"]["burn_language"] == "en"


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_ui_helpers_under_node():
    import re

    src = APP.read_text(encoding="utf-8")
    start = src.index("const SUB_TRACKS")
    helpers = src[start:src.index("function subtitleFields", start)]
    parse = re.search(r"^function parseSrt\(.*?^}\n", src, re.S | re.M).group(0)
    js = helpers + parse + r"""
const sb = { scenes: [{ id: "s01", narration: "Xin chào", subtitles: { th: "สวัสดี" } }, { id: "s02", narration: "Hello", subtitles: {} }] };
const out = {};
out.primary = primarySubCode(sb);
out.lines = ["vi", "th", "en"].map((c) => subLine(sb, sb.scenes[0], c));
setSubLine(sb, sb.scenes[0], "en", "Hi");
setSubLine(sb, sb.scenes[0], "vi", "Chào");
out.after = sb.scenes[0];
out.thai = primarySubCode({ subtitle_language: "Thai", scenes: [] });
out.srt = parseSrt("1\n00:00:01,200 --> 00:00:03,500\nHai\ndòng\n\n2\n00:01:00,000 --> 00:01:02,000\nB\n");
console.log(JSON.stringify(out));
"""
    res = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["primary"] == "vi" and out["thai"] == "th"
    assert out["lines"] == ["Xin chào", "สวัสดี", "Xin chào"]
    assert out["after"] == {"id": "s01", "narration": "Chào", "subtitles": {"th": "สวัสดี", "en": "Hi", "vi": "Chào"}}
    assert out["srt"] == [{"start": 1.2, "end": 3.5, "text": "Hai\ndòng"}, {"start": 60, "end": 62, "text": "B"}]
