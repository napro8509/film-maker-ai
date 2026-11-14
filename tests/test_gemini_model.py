"""Tests for Gemini Pro AI model integration alongside default Claude (Task #18).

Covers:
1. Model normalization (`clean_ai_model`, `effective_ai_model`, `ai_model_label`) and `Storyboard` YAML persistence
   (default `claude` omitted from YAML; `gemini-pro` persisted and reloaded).
2. `filmmaker/ai.py` Gemini Pro backends (`pick_backend`, `gemini_cli_status`, Gemini REST API with multimodal JPEG,
   `responseJsonSchema` + HTTP 400 fallback, Gemini 2.5 thinking-part filtering, and Gemini CLI `GEMINI_BIN`).
3. Planners & downstream AI tasks (`basic_planner`, `planner`, `reviewer`, `qc`, `film_chat`, `social`, `editor`,
   `video_analysis`, `autopilot`) routing to `gemini-pro` when configured while keeping `claude` as the default.
4. CLI (`--ai-model`) and Web API (`/api/env`, `/api/settings`, `/api/projects`, `/api/projects/plan`, project AI
   endpoints) + `app.js` UI helpers (`openNewProject` defaulting to `claude` and offering `gemini-pro`).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from filmmaker import (
    ai,
    autopilot,
    basic_planner,
    cli,
    editor,
    film_chat,
    planner,
    qc,
    reviewer,
    social,
    video_analysis,
)
from filmmaker.storyboard import Scene, Storyboard, StoryboardDraft, load_storyboard, save_storyboard

APP_JS = Path(__file__).resolve().parent.parent / "filmmaker" / "web" / "static" / "app.js"
REAL_TRANSLATE_ITEMS = planner._translate_items  # captured at import before conftest autouse fixture patches it
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


class _SampleOut(BaseModel):
    verdict: str
    score: int


# --- 1. Model normalization & Storyboard persistence --------------------------


def test_ai_model_normalization_and_default():
    assert ai.DEFAULT_AI_MODEL == "claude"
    assert ai.clean_ai_model(None) is None
    assert ai.clean_ai_model("") is None
    assert ai.clean_ai_model("   ") is None
    assert ai.clean_ai_model("claude") == "claude"
    assert ai.clean_ai_model("  CLAUDE  ") == "claude"
    for alias in ("gemini-pro", "GEMINI-PRO", "gemini", "gemini_pro", "gemini-2.5-pro", "geminipro"):
        assert ai.clean_ai_model(alias) == "gemini-pro"
    assert ai.effective_ai_model(None) == "claude"
    assert ai.effective_ai_model("") == "claude"
    assert ai.effective_ai_model("claude") == "claude"
    assert ai.effective_ai_model("gemini-pro") == "gemini-pro"
    assert ai.ai_model_label(None) == "Claude"
    assert ai.ai_model_label("claude") == "Claude"
    assert ai.ai_model_label("gemini-pro") == "Gemini Pro"
    with pytest.raises(ValueError, match="must be one of"):
        ai.clean_ai_model("gpt-99")


def test_storyboard_ai_model_persistence_and_default_omission(tmp_path):
    # Default storyboard: ai_model is None, effective_ai_model is 'claude', and YAML has NO 'ai_model' key
    sb_default = Storyboard(title="Mặc định Claude", scenes=[Scene(id="s01", prompt="A quiet street")])
    assert sb_default.ai_model is None
    assert sb_default.effective_ai_model == "claude"
    p_default = tmp_path / "default.yaml"
    save_storyboard(sb_default, p_default)
    raw_default = yaml.safe_load(p_default.read_text(encoding="utf-8"))
    assert "ai_model" not in raw_default
    reloaded_default = load_storyboard(p_default)
    assert reloaded_default.ai_model is None
    assert reloaded_default.effective_ai_model == "claude"

    # Explicit 'claude' keeps effective_ai_model == 'claude'; Gemini Pro persists 'ai_model: gemini-pro'
    sb_gemini = Storyboard(title="Phim Gemini", ai_model="gemini", scenes=[Scene(id="s01", prompt="A bright cafe")])
    assert sb_gemini.ai_model == "gemini-pro"
    assert sb_gemini.effective_ai_model == "gemini-pro"
    p_gemini = tmp_path / "gemini.yaml"
    save_storyboard(sb_gemini, p_gemini)
    raw_gemini = yaml.safe_load(p_gemini.read_text(encoding="utf-8"))
    assert raw_gemini["ai_model"] == "gemini-pro"
    reloaded_gemini = load_storyboard(p_gemini)
    assert reloaded_gemini.ai_model == "gemini-pro"
    assert reloaded_gemini.effective_ai_model == "gemini-pro"

    # StoryboardDraft (LLM JSON schema) intentionally excludes ai_model
    assert "ai_model" not in StoryboardDraft.model_json_schema()["properties"]

    # Invalid ai_model raises ValidationError
    with pytest.raises(ValidationError):
        Storyboard(title="Bad", ai_model="unknown-llm", scenes=[Scene(id="s01", prompt="x")])


# --- 2. Gemini Pro backends in filmmaker/ai.py --------------------------------


def test_pick_backend_claude_vs_gemini_pro(tmp_path, monkeypatch):
    # Default (ai_model=None or "claude") uses Claude backends, unaffected by Gemini key
    assert ai.pick_backend("auto", api_key=None, gemini_api_key="AIza-test") is None
    assert ai.pick_backend("auto", api_key="sk-ant-test", gemini_api_key="AIza-test") == "api"

    # Gemini Pro without key or CLI -> None
    assert ai.pick_backend("auto", api_key="sk-ant-test", ai_model="gemini-pro") is None
    assert "Gemini Pro" in ai.not_ready_message("gemini-pro")
    assert "Claude" in ai.not_ready_message(None)

    # Gemini Pro with API key (arg or env) -> gemini_api
    assert ai.pick_backend("auto", ai_model="gemini-pro", gemini_api_key="AIza-test") == "gemini_api"
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-from-google-env")
    assert ai.pick_backend("auto", ai_model="gemini-pro") == "gemini_api"
    monkeypatch.delenv("GOOGLE_API_KEY")

    # Gemini CLI status & selection with fake binary + oauth_creds.json
    fake_bin = tmp_path / "fake_gemini"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bin.chmod(0o755)
    monkeypatch.setenv("GEMINI_BIN", str(fake_bin))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path))
    assert ai.gemini_cli_status() == {"available": True, "logged_in": False, "method": None}
    assert ai.pick_backend("auto", ai_model="gemini-pro") is None
    assert ai.pick_backend("gemini_cli", ai_model="gemini-pro") == "gemini_cli"

    creds = tmp_path / ".gemini" / "oauth_creds.json"
    creds.parent.mkdir(parents=True)
    creds.write_text('{"access_token": "tok"}', encoding="utf-8")
    assert ai.gemini_cli_status() == {"available": True, "logged_in": True, "method": "google-oauth"}
    assert ai.pick_backend("auto", ai_model="gemini-pro") == "gemini_cli"
    # API key takes priority in 'auto' mode when both exist
    assert ai.pick_backend("auto", ai_model="gemini-pro", gemini_api_key="AIza-test") == "gemini_api"


def test_gemini_api_structured_and_text_and_multimodal(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "_gemini_http_post", ai._real_gemini_http_post)
    monkeypatch.setattr(ai, "shrink", lambda src, dest, size=ai.IMAGE_MAX: (dest.parent.mkdir(parents=True, exist_ok=True), shutil.copyfile(src, dest), dest)[2])

    img = tmp_path / "frame.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    captured: list[dict] = []

    class _FakeResp:
        def __init__(self, payload: dict):
            self._raw = json.dumps(payload).encode("utf-8")

        def read(self) -> bytes:
            return self._raw

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req: urllib.request.Request, timeout: int = 0):
        body = json.loads(req.data.decode("utf-8"))
        headers = {k.lower(): v for k, v in req.header_items()}
        captured.append({"url": req.full_url, "headers": headers, "body": body, "timeout": timeout})
        if body.get("generationConfig", {}).get("responseMimeType") == "application/json":
            # Include a Gemini 2.5 thinking part ('thought': True) to verify it is ignored
            return _FakeResp({
                "candidates": [{
                    "finishReason": "STOP",
                    "content": {
                        "parts": [
                            {"text": '{"verdict": "WRONG_THOUGHT", "score": 0}', "thought": True},
                            {"text": '{"verdict": "PASS", "score": 9}'},
                        ]
                    },
                }]
            })
        return _FakeResp({
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": "Reasoning...", "thought": True}, {"text": "Phim nhịp nhanh, ấm áp."}]},
            }]
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    out = ai.ask(
        "You are a film QC agent.",
        "Evaluate scene s01.",
        _SampleOut,
        images=[("Frame 1", img)],
        ai_model="gemini-pro",
        gemini_api_key="AIza-secret-key",
        timeout=120,
    )
    assert out == _SampleOut(verdict="PASS", score=9)
    assert len(captured) == 1
    req1 = captured[0]
    assert req1["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent"
    assert req1["headers"]["x-goog-api-key"] == "AIza-secret-key"
    assert req1["body"]["systemInstruction"] == {"parts": [{"text": "You are a film QC agent."}]}
    assert req1["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseJsonSchema" in req1["body"]["generationConfig"]
    parts = req1["body"]["contents"][0]["parts"]
    assert parts[0] == {"text": "Frame 1"}
    assert parts[1]["inlineData"]["mimeType"] == "image/jpeg"
    assert parts[2] == {"text": "Evaluate scene s01."}

    txt = ai.ask_text("System chat.", "Nhận xét phim?", ai_model="gemini-pro", gemini_api_key="AIza-secret-key")
    assert txt == "Phim nhịp nhanh, ấm áp."
    assert "responseMimeType" not in captured[1]["body"]["generationConfig"]


def test_gemini_api_schema_fallback_and_http_error(monkeypatch):
    monkeypatch.setattr(ai, "_gemini_http_post", ai._real_gemini_http_post)
    calls: list[dict] = []

    class _FakeResp:
        def __init__(self, payload: dict):
            self._raw = json.dumps(payload).encode("utf-8")

        def read(self):
            return self._raw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req: urllib.request.Request, timeout: int = 0):
        body = json.loads(req.data.decode("utf-8"))
        calls.append(body)
        if "responseJsonSchema" in body.get("generationConfig", {}):
            err_json = json.dumps({"error": {"message": "Unknown name 'responseJsonSchema' at generation_config"}})
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(err_json.encode("utf-8")))
        return _FakeResp({
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": '```json\n{"verdict": "MINOR", "score": 7}\n```'}]},
            }]
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    res = ai.draft_json("Sys", "User", _SampleOut, gemini_api_key="AIza-key")
    assert res == {"verdict": "MINOR", "score": 7}
    assert len(calls) == 2
    assert "responseJsonSchema" in calls[0]["generationConfig"]
    assert "responseJsonSchema" not in calls[1]["generationConfig"]
    assert calls[1]["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_cli_backend_structured_and_text(tmp_path, monkeypatch):
    log_file = tmp_path / "cli_calls.jsonl"
    fake_cli = tmp_path / "fake_gemini.py"
    fake_cli.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"with open({str(log_file)!r}, 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps({'argv': sys.argv[1:], 'has_anth': 'ANTHROPIC_API_KEY' in os.environ}) + '\\n')\n"
        "prompt = sys.argv[sys.argv.index('-p') + 1]\n"
        "if 'JSON Schema' in prompt:\n"
        "    print(json.dumps({'response': '```json\\n{\"verdict\": \"PASS\", \"score\": 10}\\n```'}))\n"
        "else:\n"
        "    print(json.dumps({'response': 'Trả lời từ Gemini CLI'}))\n",
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)
    monkeypatch.setenv("GEMINI_BIN", str(fake_cli))
    monkeypatch.setenv("GEMINI_CLI_LOGGED_IN", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")
    monkeypatch.setattr(ai, "shrink", lambda src, dest, size=ai.IMAGE_MAX: (dest.parent.mkdir(parents=True, exist_ok=True), shutil.copyfile(src, dest), dest)[2])

    img = tmp_path / "shot.jpg"
    img.write_bytes(b"jpegbytes")

    parsed = ai.ask("Sys", "Check image", _SampleOut, images=[("Shot 1", img)], ai_model="gemini-pro")
    assert parsed == _SampleOut(verdict="PASS", score=10)
    plain = ai.ask_text("Sys", "Hello", ai_model="gemini-pro")
    assert plain == "Trả lời từ Gemini CLI"

    records = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert "-m" in records[0]["argv"] and "gemini-2.5-pro" in records[0]["argv"]
    assert "-o" in records[0]["argv"] and "json" in records[0]["argv"]
    assert "--approval-mode" in records[0]["argv"] and "plan" in records[0]["argv"]
    assert records[0]["has_anth"] is False
    assert "--approval-mode" not in records[1]["argv"]


# --- 3. Planners & downstream AI modules routing ------------------------------


def test_plan_basic_default_claude_vs_gemini_pro(monkeypatch):
    gemini_calls = []

    def fake_draft_json(system, user, schema, **kw):
        gemini_calls.append({"system": system, "user": user, "kw": kw})
        return {
            "title": "Chú mèo nhỏ",
            "style": "3D Pixar animation, warm golden light",
            "characters": [{"name": "Miu", "description": "a tiny orange kitten with green eyes"}],
            "locations": [{"name": "Hẻm nhỏ", "description": "narrow Saigon alley with warm lanterns, empty"}],
            "scenes": [
                {
                    "id": "s01",
                    "location": "Hẻm nhỏ",
                    "characters": ["Miu"],
                    "image_prompt": "Wide shot of a tiny orange kitten with green eyes sitting in a lantern-lit alley.",
                    "camera": "Static locked-off wide shot",
                    "action": "In the center, the tiny orange kitten stands up and walks forward",
                    "environment": "lanterns sway gently",
                    "audio": "soft rain and distant traffic",
                }
            ],
        }

    monkeypatch.setattr(ai, "draft_json", fake_draft_json)
    diacritic_models = []
    monkeypatch.setattr(
        planner,
        "fix_vietnamese_diacritics",
        lambda sb, **kw: (diacritic_models.append(kw.get("ai_model")), sb)[1],
    )

    sb, used_backend = basic_planner.plan_basic(
        "Một chú mèo đi lạc",
        fmt="clip",
        audio="silent",
        visual="3d",
        scenes=1,
        aspect="9:16",
        language="Vietnamese",
        ai_model="gemini-pro",
        gemini_api_key="AIza-test",
    )
    assert used_backend == "gemini_api"
    assert sb.ai_model == "gemini-pro"
    assert sb.effective_ai_model == "gemini-pro"
    assert len(gemini_calls) == 1
    assert gemini_calls[0]["kw"]["gemini_api_key"] == "AIza-test"
    assert diacritic_models == ["gemini-pro"]


def test_planner_translate_and_titles_and_downstream_modules_route_to_gemini(tmp_path, monkeypatch):
    seen_ask: list[dict] = []
    seen_text: list[dict] = []

    def fake_ask(system, user, schema, **kw):
        seen_ask.append({"schema": schema.__name__, **kw})
        if schema is reviewer.StoryboardReview:
            return reviewer.StoryboardReview(verdict="PASS", score=9, summary="Tốt")
        if schema is reviewer.SceneFix:
            return reviewer.SceneFix(id="s01", prompt="Safe prompt")
        if schema is social.SocialPack:
            return social.SocialPack(
                posts=[social.Post(platform="facebook", language="Vietnamese", title="T", hook="H", caption="C", hashtags=["phim"])]
            )
        raise AssertionError(f"unexpected schema {schema}")

    def fake_ask_text(system, user, **kw):
        seen_text.append(kw)
        return "Câu trả lời từ Gemini Pro"

    monkeypatch.setattr(ai, "ask", fake_ask)
    monkeypatch.setattr(ai, "ask_text", fake_ask_text)

    sb_gem = Storyboard(
        title="Phim Gemini",
        ai_model="gemini-pro",
        scenes=[Scene(id="s01", prompt="A calm scene", narration="Xin chào")],
    )
    sb_claude = Storyboard(
        title="Phim Claude",
        scenes=[Scene(id="s01", prompt="A calm scene", narration="Xin chào")],
    )

    # Reviewer & soften_scene read sb.ai_model automatically
    reviewer.review_storyboard(sb_gem, gemini_api_key="AIza-1")
    reviewer.soften_scene(sb_gem, "s01", "policy error", gemini_api_key="AIza-1")
    reviewer.review_storyboard(sb_claude, api_key="sk-1")
    assert seen_ask[0]["ai_model"] == "gemini-pro" and seen_ask[0]["gemini_api_key"] == "AIza-1"
    assert seen_ask[1]["ai_model"] == "gemini-pro"
    assert "ai_model" not in seen_ask[2]  # default Claude preserves exact legacy call signature

    # Social & film_chat route via sb.ai_model on disk
    project = tmp_path / "proj"
    out = project / "output" / "storyboard"
    out.mkdir(parents=True, exist_ok=True)
    save_storyboard(sb_gem, project / "storyboard.yaml")
    social.generate(project, out, ["facebook"], ["Vietnamese"], gemini_api_key="AIza-2")
    assert seen_ask[-1]["ai_model"] == "gemini-pro" and seen_ask[-1]["gemini_api_key"] == "AIza-2"

    ans = film_chat.ask(project, out, "Phim nói về gì?", gemini_api_key="AIza-2")
    assert ans[-1]["role"] == "assistant" and ans[-1]["content"] == "Câu trả lời từ Gemini Pro"
    assert seen_text[-1]["ai_model"] == "gemini-pro" and seen_text[-1]["gemini_api_key"] == "AIza-2"

    # Subtitle translation & thumbnail titles route via sb.ai_model -> _draft_gemini / ai.draft_json
    draft_calls = []

    def fake_draft_json(system, user, schema, **kw):
        draft_calls.append(kw)
        if "lines" in (schema if isinstance(schema, dict) else schema.model_json_schema())["properties"]:
            return {"lines": [{"id": "s01", "vi": "Xin chào", "th": "สวัสดี", "en": "Hello"}]}
        return {"titles": [{"text": "Bí mật cuối cùng", "vi": ""}]}

    monkeypatch.setattr(ai, "draft_json", fake_draft_json)
    monkeypatch.setattr(planner, "_translate_items", REAL_TRANSLATE_ITEMS)
    added = planner.translate_subtitles(sb_gem, gemini_api_key="AIza-3")
    assert added == {"s01": {"th": "สวัสดี", "en": "Hello"}}
    titles = planner.suggest_titles(sb_gem, n=2, gemini_api_key="AIza-3")
    assert titles == [{"text": "Bí mật cuối cùng", "vi": ""}]
    assert len(draft_calls) == 2 and all(c["gemini_api_key"] == "AIza-3" for c in draft_calls)

    # Autopilot ai_kw honors sb.ai_model on disk vs AutoSettings.ai_model
    ap_gem = autopilot.Autopilot(
        project / "storyboard.yaml", out, None, settings=autopilot.AutoSettings(gemini_api_key="AIza-auto")
    )
    assert ap_gem.ai_kw() == {
        "backend": "auto",
        "api_key": None,
        "ai_model": "gemini-pro",
        "gemini_api_key": "AIza-auto",
    }
    proj_def = tmp_path / "proj_def"
    proj_def.mkdir(parents=True, exist_ok=True)
    save_storyboard(sb_claude, proj_def / "storyboard.yaml")
    ap_def = autopilot.Autopilot(
        proj_def / "storyboard.yaml", out, None, settings=autopilot.AutoSettings(api_key="sk-ant")
    )
    assert ap_def.ai_kw() == {"backend": "auto", "api_key": "sk-ant"}


# --- 4. CLI --ai-model --------------------------------------------------------


def test_cli_plan_default_claude_and_gemini_pro(tmp_path, monkeypatch):
    from click.testing import CliRunner

    calls = []

    def fake_plan_basic(idea, **kw):
        calls.append({"fn": "basic", "idea": idea, **kw})
        sb = Storyboard(title="T", scenes=[Scene(id="s01", prompt="A cat")])
        if ai.effective_ai_model(kw.get("ai_model")) == "gemini-pro":
            sb.ai_model = "gemini-pro"
        return sb, "gemini_api" if sb.ai_model == "gemini-pro" else "api"

    monkeypatch.setattr(basic_planner, "plan_basic", fake_plan_basic)
    runner = CliRunner()

    out_claude = tmp_path / "claude_sb.yaml"
    res1 = runner.invoke(cli.main, ["plan", "Ý tưởng 1", "-o", str(out_claude)])
    assert res1.exit_code == 0, res1.output
    assert ai.effective_ai_model(calls[-1].get("ai_model")) == "claude"
    assert "ai_model" not in yaml.safe_load(out_claude.read_text(encoding="utf-8"))

    out_gem = tmp_path / "gemini_sb.yaml"
    res2 = runner.invoke(cli.main, ["plan", "Ý tưởng 2", "--ai-model", "gemini-pro", "-o", str(out_gem)])
    assert res2.exit_code == 0, res2.output
    assert calls[-1]["ai_model"] == "gemini-pro"
    assert yaml.safe_load(out_gem.read_text(encoding="utf-8"))["ai_model"] == "gemini-pro"


# --- 5. Web API & Project lifecycle ------------------------------------------


def test_web_settings_env_and_project_creation_with_ai_model(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from tests.test_model_web import make_client as _mc, wait

    # Reuse the clean TestClient pattern with isolated settings & fake gflow
    fake_gflow = Path(__file__).parent / "fake_gflow.py"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {fake_gflow}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "gflow.log"))
    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": False, "logged_in": False})

    from fastapi.testclient import TestClient
    from filmmaker.web import server

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"pause_min": 0, "pause_max": 0, "retries": 0}), encoding="utf-8")
    monkeypatch.setattr(server, "SETTINGS_PATH", settings_file)
    c = TestClient(server.create_app(tmp_path / "ws", token="t0k"))
    c.headers["X-Token"] = "t0k"

    # 1. /api/env exposes default_ai_model == 'claude' and both AI models
    env0 = c.get("/api/env").json()
    assert env0["default_ai_model"] == "claude"
    assert [m["key"] for m in env0["ai_models"]] == ["claude", "gemini-pro"]
    assert env0["ai_models"][0]["default"] is True and env0["ai_models"][1]["default"] is False
    assert env0["gemini_backend"] is None

    # 2. Creating a project without ai_model defaults to 'claude' and omits 'ai_model' from storyboard.yaml
    r_def = c.post("/api/projects", json={"title": "Dự án mặc định", "source": "template"})
    assert r_def.status_code == 200, r_def.text
    pid_def = r_def.json()["id"]
    assert c.get(f"/api/projects/{pid_def}").json()["ai_model"] == "claude"
    sb_def_yaml = yaml.safe_load((tmp_path / "ws" / pid_def / "storyboard.yaml").read_text(encoding="utf-8"))
    assert "ai_model" not in sb_def_yaml

    # 3. Creating a project with ai_model='gemini-pro' saves ai_model: gemini-pro
    r_gem = c.post("/api/projects", json={"title": "Dự án Gemini", "source": "blank", "ai_model": "gemini-pro"})
    assert r_gem.status_code == 200, r_gem.text
    pid_gem = r_gem.json()["id"]
    projects_by_id = {p["id"]: p for p in c.get("/api/projects").json()}
    assert projects_by_id[pid_def]["ai_model"] == "claude"
    assert projects_by_id[pid_gem]["ai_model"] == "gemini-pro"
    sb_gem_yaml = yaml.safe_load((tmp_path / "ws" / pid_gem / "storyboard.yaml").read_text(encoding="utf-8"))
    assert sb_gem_yaml["ai_model"] == "gemini-pro"
    detail_gem = c.get(f"/api/projects/{pid_gem}").json()
    assert detail_gem["ai_model"] == "gemini-pro"
    assert detail_gem["storyboard"]["ai_model"] == "gemini-pro"

    # Invalid ai_model -> HTTP 400
    r_bad = c.post("/api/projects", json={"title": "Lỗi", "source": "blank", "ai_model": "invalid-llm"})
    assert r_bad.status_code == 400

    # 4. Planning with Gemini Pro before Gemini key is set -> HTTP 400 with Gemini message
    r_plan_unready = c.post("/api/projects/plan", json={"idea": "Mèo đi nắng", "scenes": 2, "ai_model": "gemini-pro"})
    assert r_plan_unready.status_code == 400
    assert "Gemini" in r_plan_unready.json()["detail"]

    # 5. Save Gemini API key via PUT /api/settings (masked on GET, activates gemini_backend)
    s_saved = c.put("/api/settings", json={"gemini_api_key": "AIzaSyTestKey123456", "gemini_backend": "auto"}).json()
    assert s_saved["gemini_key_set"] is True
    assert s_saved["gemini_api_key"] == ""
    assert "3456" in s_saved["gemini_key_hint"]
    env1 = c.get("/api/env").json()
    assert env1["gemini_key"] is True
    assert env1["gemini_backend"] == "gemini_api"

    # 6. Plan a new project using Gemini Pro (even with NO Claude key configured!)
    plan_calls = []

    def fake_plan_auto(idea, **kw):
        plan_calls.append(kw)
        return (
            Storyboard(
                title="Mèo Gemini",
                ai_model="gemini-pro",
                scenes=[Scene(id="s01", prompt="Orange kitten in sunlight", narration="Chú mèo nhỏ")],
            ),
            "gemini_api",
        )

    monkeypatch.setattr(planner, "plan_auto", fake_plan_auto)
    r_plan = c.post("/api/projects/plan", json={"idea": "Mèo đi nắng", "scenes": 1, "ai_model": "gemini-pro"})
    assert r_plan.status_code == 200, r_plan.text
    job = wait(c, r_plan.json())
    assert job["status"] == "done", job
    planned_pid = job["result"]["project"]
    assert plan_calls[-1]["ai_model"] == "gemini-pro"
    assert plan_calls[-1]["gemini_api_key"] == "AIzaSyTestKey123456"
    planned_yaml = yaml.safe_load((tmp_path / "ws" / planned_pid / "storyboard.yaml").read_text(encoding="utf-8"))
    assert planned_yaml["ai_model"] == "gemini-pro"

    # 7. Project AI endpoints (/thumbnail/titles, /ask) automatically use Gemini Pro for Gemini project,
    #    while rejecting Claude-default project when Claude is not configured!
    monkeypatch.setattr(
        planner,
        "suggest_titles",
        lambda sb, **kw: [{"text": f"Title via {kw.get('ai_model')}", "vi": ""}],
    )
    ask_calls = []

    def fake_ai_ask_text(system, user, **kw):
        ask_calls.append(kw)
        return f"Chat via {kw.get('ai_model')}"

    monkeypatch.setattr(ai, "ask_text", fake_ai_ask_text)

    r_titles_gem = c.post(f"/api/projects/{planned_pid}/thumbnail/titles", json={"n": 1})
    assert r_titles_gem.status_code == 200
    assert r_titles_gem.json()["titles"] == [{"text": "Title via gemini-pro", "vi": ""}]

    r_ask_gem = c.post(f"/api/projects/{planned_pid}/ask", json={"question": "Tóm tắt?"})
    assert r_ask_gem.status_code == 200
    assert ask_calls[-1]["ai_model"] == "gemini-pro"
    assert ask_calls[-1]["gemini_api_key"] == "AIzaSyTestKey123456"
    assert r_ask_gem.json()["messages"][-1]["content"] == "Chat via gemini-pro"

    # Default Claude project still requires Claude when Claude is unconfigured
    r_ask_def = c.post(f"/api/projects/{pid_def}/ask", json={"question": "Tóm tắt?"})
    assert r_ask_def.status_code == 400 and "Claude" in r_ask_def.json()["detail"]

    # 8. Clear Gemini key in settings
    s_cleared = c.put("/api/settings", json={"clear_gemini_key": True}).json()
    assert s_cleared["gemini_key_set"] is False
    assert c.get("/api/env").json()["gemini_backend"] is None


# --- 6. Frontend app.js AI model selector & default='claude' ------------------


@needs_node
def test_app_js_ai_model_helpers_and_new_project_default(tmp_path):
    harness = r"""
    const fs = require("fs");
    const src = fs.readFileSync(process.argv[2], "utf8");
    const fn = (n) => {
      const m = src.match(new RegExp("^(?:async )?function " + n + "\\(.*?^}\\n", "ms"));
      if (!m) throw new Error("missing function " + n);
      return m[0];
    };
    class El {
      constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.style = {}; this.value = ""; }
      setAttribute(k, v) { this.attrs[k] = String(v); }
      addEventListener(k, f) { this["on" + k] = f; }
      append(c) { this.children.push(c); }
    }
    const document = { createElement: (t) => new El(t), createTextNode: (t) => String(t) };
    const Node = El;
    const S = { env: { planner: "api", gemini_backend: null } };
    eval(fn("h"));
    eval(src.match(/^const AI_MODEL_OPTIONS = .*$/m)[0].replace("const ", "var "));
    eval(fn("cleanAiModel"));
    eval(fn("aiModelLabel"));
    eval(fn("aiModelReady"));
    eval(fn("aiModelSelect"));

    const selDefault = aiModelSelect(undefined, () => {});
    const selGemini = aiModelSelect("gemini-pro", () => {});
    const newProjSrc = fn("openNewProject");

    console.log(JSON.stringify({
      clean: [cleanAiModel(null), cleanAiModel(""), cleanAiModel("claude"), cleanAiModel("gemini-pro"), cleanAiModel("gemini")],
      labels: [aiModelLabel(null), aiModelLabel("claude"), aiModelLabel("gemini-pro")],
      readyClaudeOnly: [aiModelReady("claude"), aiModelReady("gemini-pro")],
      options: selDefault.children.map((o) => [o.attrs.value || o.value, o.children.join(""), "selected" in o.attrs || !!o.selected]),
      defaultSelected: selDefault.value,
      geminiSelected: selGemini.value,
      newProjInitDefaultClaude: /let aiModel = "claude";/.test(newProjSrc),
      newProjHasModelField: newProjSrc.includes('field("Model AI", aiModelSelect(aiModel'),
    }));
    """
    js = tmp_path / "ai_ui_test.js"
    js.write_text(harness, encoding="utf-8")
    proc = subprocess.run(["node", str(js), str(APP_JS)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["clean"] == ["claude", "claude", "claude", "gemini-pro", "gemini-pro"]
    assert out["labels"] == ["Claude", "Claude", "Gemini Pro"]
    assert out["readyClaudeOnly"] == [True, False]
    assert out["options"] == [
        ["claude", "Claude (mặc định)", True],
        ["gemini-pro", "Gemini Pro", False],
    ]
    assert out["defaultSelected"] == "claude"
    assert out["geminiSelected"] == "gemini-pro"
    assert out["newProjInitDefaultClaude"] is True
    assert out["newProjHasModelField"] is True
