"""Script library ("Kho kịch bản"): listing approved scripts and making a new film from one (web + module).
No AI is called: the planners are stubbed."""
import json
import sys
import time
from pathlib import Path

import pytest
import yaml

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filmmaker import basic_planner, planner, script_library  # noqa: E402
from filmmaker.storyboard import Storyboard  # noqa: E402
from filmmaker.web import server  # noqa: E402

FAKE = Path(__file__).parent / "fake_gflow.py"

SCRIPT = """# ปิ่นโตของแม่
*(Hộp cơm của mẹ)*

**Logline:** Một bà mẹ bán cơm hộp nghe con giới thiệu mình là người giúp việc.

## Cảnh
### s01. Sạp cơm
Malee: แม่...
### s02. Cửa nhà
Krit: แม่บ้าน
"""


def make_film(ws: Path, name: str, *, script: str = SCRIPT, final: bool = True, verdict: str = "PASS",
              storyboard: bool = False) -> Path:
    d = ws / name
    (d / "reviews").mkdir(parents=True)
    (d / ("script.final.md" if final else "script.md")).write_text(script, encoding="utf-8")
    (d / "brief.md").write_text("# Brief\n- Khung hình: 9:16\n- Ngôn ngữ thoại: Tiếng Thái (nhân vật người Thái)\n",
                                encoding="utf-8")
    (d / "reviews" / "script-review.md").write_text(f"TỔNG ĐIỂM: 7.1/10\n...\nTỔNG ĐIỂM: 8.4/10\nVERDICT: {verdict}\n",
                                                    encoding="utf-8")
    if storyboard:
        (d / "storyboard.yaml").write_text("title: x\nscenes: []\n", encoding="utf-8")
    return d


# --- module --------------------------------------------------------------------------------------------------

def test_list_reads_title_score_language_and_skips_archives(tmp_path):
    ws = tmp_path / "ws"
    make_film(ws, "thai-hop-com")
    make_film(ws, "draft-one", final=False, verdict="REVISE")
    make_film(ws, "_kich-ban-thai")  # archive folders are not listed
    (ws / "no-script").mkdir()
    rows = script_library.list_scripts(ws)
    assert [r["id"] for r in rows] == ["thai-hop-com", "draft-one"]  # PASS first
    r = rows[0]
    assert (r["title"], r["subtitle"], r["file"]) == ("ปิ่นโตของแม่", "Hộp cơm của mẹ", "script.final.md")
    assert r["score"] == 8.4 and r["verdict"] == "PASS" and r["scenes"] == 2
    assert r["dialogue_language"] == "Thai" and r["aspect"] == "9:16" and not r["has_film"]
    assert r["logline"].startswith("Một bà mẹ bán cơm hộp")


@pytest.mark.parametrize("bad", ["../etc", "_kich-ban-thai", ".hidden", "a/b", "", "no-such"])
def test_folder_of_rejects_bad_ids(tmp_path, bad):
    ws = tmp_path / "ws"
    make_film(ws, "_kich-ban-thai")
    with pytest.raises(ValueError):
        script_library.folder_of(ws, bad)


def test_idea_keeps_script_and_note():
    idea = script_library.idea_from_script(SCRIPT, "giữ trang phục Thái")
    assert idea.startswith(script_library.SCRIPT_IDEA_HEAD)
    assert "Ghi chú thêm: giữ trang phục Thái" in idea and idea.endswith("Krit: แม่บ้าน")


@pytest.mark.parametrize("text,name", [("Tiếng Thái (nhân vật người Thái)", "Thai"), ("Tiếng Việt", "Vietnamese"),
                                       ("English", "English"), ("Klingon", None)])
def test_language_name(text, name):
    assert script_library.language_name(text) == name


# --- web -----------------------------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": False, "logged_in": False})
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"pause_min": 0, "pause_max": 0, "retries": 0, "auto_backup": False,
                                    "planner": "api", "anthropic_api_key": "x"}))
    monkeypatch.setattr(server, "SETTINGS_PATH", settings)
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(settings))
    ws = tmp_path / "ws"
    ws.mkdir()
    app = server.create_app(ws, token="t0k")
    c = TestClient(app)
    c.headers["X-Token"] = "t0k"
    c.ws = ws
    c.ideas = []

    def fake_basic(idea, **kw):
        c.ideas.append(idea)
        return Storyboard.model_validate({"title": "Pinto", "format": kw.get("fmt"), "audio": kw.get("audio"),
                                          "scenes": [{"id": "s01", "mode": "keyframe", "prompt": "a"}]}), None

    monkeypatch.setattr(basic_planner, "plan_basic", fake_basic)
    return c


def _wait(c, job, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error", "stopped"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_api_lists_and_reads_scripts(client):
    make_film(client.ws, "thai-hop-com")
    rows = client.get("/api/scripts").json()
    assert [r["id"] for r in rows] == ["thai-hop-com"]
    one = client.get("/api/scripts/thai-hop-com").json()
    assert one["title"] == "ปิ่นโตของแม่" and "Krit: แม่บ้าน" in one["text"]
    assert client.get("/api/scripts/..%2Fetc").status_code == 404
    assert client.get("/api/scripts/nope").status_code == 404


BODY = {"script": "thai-hop-com", "idea": "", "scenes": 2, "aspect": "9:16", "language": "Thai",
        "dialogue_language": "Thai", "format": "clip", "audio": "dialogue", "visual_style": "realistic", "engine": "muse"}


def test_plan_from_script_goes_into_the_script_folder(client):
    d = make_film(client.ws, "thai-hop-com")
    j = _wait(client, client.post("/api/projects/plan", json={**BODY, "idea": "giữ trang phục Thái"}).json())
    assert j["status"] == "done", j.get("lines")
    assert j["result"]["project"] == "thai-hop-com"
    assert client.ideas[-1].startswith(script_library.SCRIPT_IDEA_HEAD) and "Krit: แม่บ้าน" in client.ideas[-1]
    assert "Ghi chú thêm: giữ trang phục Thái" in client.ideas[-1]
    data = yaml.safe_load((d / "storyboard.yaml").read_text(encoding="utf-8"))
    assert data["model"] == "muse"
    assert (d / "brief.md").is_file() and (d / "script.final.md").is_file()  # nothing of the script folder lost
    # the script now has a film: a second film from it gets its own folder
    j2 = _wait(client, client.post("/api/projects/plan", json=BODY).json())
    assert j2["status"] == "done" and j2["result"]["project"] != "thai-hop-com"
    assert client.get("/api/scripts").json()[0]["has_film"] is True


def test_plan_with_unknown_script_is_400(client):
    r = client.post("/api/projects/plan", json={**BODY, "script": "../x"})
    assert r.status_code == 400
    r = client.post("/api/projects/autopilot", json={**BODY, "script": "nope"})
    assert r.status_code == 400
