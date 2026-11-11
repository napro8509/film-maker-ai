"""“Hỏi AI về phim”: the context built from the film's files, the saved conversation and the web endpoints."""
import json
import time

import pytest
import yaml

from filmmaker import film_chat, planner
from filmmaker.storyboard import Storyboard

SB = {"title": "Cơn Mưa", "model": "veo-lite", "aspect": "9:16",
      "characters": [{"name": "Mai", "description": "young woman, teal jacket"}],
      "scenes": [{"id": "s01", "mode": "t2v", "prompt": "Mai runs in the rain", "duration": 8,
                  "narration": "Một năm sau"}]}


@pytest.fixture
def film(tmp_path):
    d = tmp_path / "ws" / "film"
    out = d / "output" / "storyboard"
    (out / "reviews").mkdir(parents=True)
    (d / "storyboard.yaml").write_text(yaml.safe_dump(SB, allow_unicode=True), encoding="utf-8")
    (d / "ui.json").write_text(json.dumps({"idea": "cô giao hàng gặp ông bán vé số",
                                           "thumbnail_titles": [{"text": "Cơn Mưa"}]}), encoding="utf-8")
    (out / "state.json").write_text(json.dumps({"scenes": {"s01": {"status": "done", "fingerprint": "abc",
                                                                    "media_id": "m1", "rendered_as": "t2v"}}}))
    (out / "edit.json").write_text(json.dumps({"clips": [{"id": "s01", "trim_end": 0.5}]}))
    (out / "final.srt").write_text("1\n00:00:01,000 --> 00:00:03,000\nMột năm sau\n", encoding="utf-8")
    (out / "reviews" / "film.json").write_text(json.dumps({"score": 8, "verdict": "PASS",
                                                           "issues": ["áo mưa không rõ ở s01"]}, ensure_ascii=False))
    (out / "reviews" / "report.md").write_text("# Báo cáo\n- review xong", encoding="utf-8")
    return d, out


def test_gather_includes_everything_made_for_the_film(film):
    d, out = film
    sections = dict(film_chat.gather(d, out))
    assert sections["Ý tưởng ban đầu"] == "cô giao hàng gặp ông bán vé số"
    assert "Mai runs in the rain" in sections["Storyboard (storyboard.yaml)"]
    assert "Mai runs in the rain" in sections["Prompt gửi Flow (đã gộp phong cách, nhân vật, chỉ đạo)"]
    state = sections["Hồ sơ render từng cảnh (state.json)"]
    assert "rendered_as" in state and "fingerprint" not in state and "media_id" not in state
    assert "trim_end" in sections["Kế hoạch dựng phim (edit.json)"]
    assert "Một năm sau" in sections["Phụ đề (final.srt)"]
    assert "áo mưa không rõ" in sections["Review của agent: film"]
    assert "review xong" in sections["Báo cáo tổng hợp của agent (reviews/report.md)"]
    assert "Cơn Mưa" in sections["Tiêu đề bìa đã gợi ý"]


def test_gather_skips_missing_files_and_caps_size(film, monkeypatch):
    d, out = film
    (out / "edit.json").unlink()
    monkeypatch.setattr(film_chat, "MAX_CONTEXT", 500)
    sections = film_chat.gather(d, out)
    assert "Kế hoạch dựng phim (edit.json)" not in dict(sections)
    assert sum(len(t) for _, t in sections) < 600


def test_prompt_carries_history_and_new_question(film):
    d, out = film
    history = [{"role": "user", "content": "phim dài bao lâu?"}, {"role": "assistant", "content": "8 giây"}]
    text = film_chat.build_prompt(film_chat.gather(d, out), history, "viết caption TikTok")
    assert "phim dài bao lâu?" in text and "Trợ lý: 8 giây" in text
    assert text.rstrip().endswith("viết caption TikTok")
    assert "cô giao hàng gặp ông bán vé số" in text


def test_ask_saves_the_conversation(film, monkeypatch):
    d, out = film
    seen = {}

    def fake_ask(system, user, **kw):
        seen.update(user=user, images=kw["images"], backend=kw["backend"])
        return "Dùng **#xuhuong**\n\n**GỢI Ý TIẾP: Thêm tiêu đề? |  | a | b | c**"

    monkeypatch.setattr(film_chat.ai, "ask_text", fake_ask)
    msgs = film_chat.ask(d, out, "  caption?  ", with_pictures=False, backend="api")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "caption?" and msgs[1]["content"] == "Dùng **#xuhuong**"
    assert msgs[1]["follow_ups"] == ["Thêm tiêu đề?", "a", "b"]
    assert seen["images"] == [] and seen["backend"] == "api" and "Mai runs" in seen["user"]
    assert film_chat.load(out) == msgs
    film_chat.ask(d, out, "tiếp", with_pictures=False)
    assert "caption?" in seen["user"] and len(film_chat.load(out)) == 4
    film_chat.clear(out)
    assert film_chat.load(out) == []


def test_ask_errors_save_nothing(film, monkeypatch):
    d, out = film
    with pytest.raises(ValueError):
        film_chat.ask(d, out, "   ")

    def boom(*a, **k):
        raise RuntimeError("Claude Code không trả lời được")

    monkeypatch.setattr(film_chat.ai, "ask_text", boom)
    with pytest.raises(RuntimeError):
        film_chat.ask(d, out, "hỏi", with_pictures=False)
    assert film_chat.load(out) == []


def test_split_follow_ups():
    assert film_chat.split_follow_ups("Trả lời\nGỢI Ý TIẾP: a | b") == ("Trả lời", ["a", "b"])
    assert film_chat.split_follow_ups("Không có gợi ý") == ("Không có gợi ý", [])


def test_claude_code_chat_returns_plain_result(monkeypatch, tmp_path):
    import subprocess

    from filmmaker import ai

    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"is_error": False, "result": " # Đáp án "}), "")

    monkeypatch.setattr(planner, "claude_code_binary", lambda: "claude")
    monkeypatch.setattr(planner, "_subscription_env", lambda: {})
    monkeypatch.setattr(ai.subprocess, "run", fake_run)
    assert ai.ask_text("sys", "hỏi", backend="claude_code") == "# Đáp án"
    assert "--json-schema" not in seen["cmd"]
    assert json.loads(seen["cmd"][seen["cmd"].index("--settings") + 1]) == {"disableAllHooks": True}


def test_pictures_list_portraits_and_cover(film):
    d, out = film
    (d / "assets").mkdir()
    (d / "assets" / "mai.png").write_bytes(b"x")
    raw = yaml.safe_load((d / "storyboard.yaml").read_text())
    raw["characters"][0]["image"] = "assets/mai.png"
    (d / "storyboard.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    (out / "thumbnail.jpg").write_bytes(b"x")
    labels = [label for label, _ in film_chat.pictures(d, out)]
    assert labels == ["Ảnh tham chiếu nhân vật Mai", "Ảnh bìa (thumbnail.jpg)"]  # no final.mp4: no contact sheet


# --- web ---------------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi")


@pytest.fixture
def web(film, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from filmmaker.web import server

    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": False, "logged_in": False})
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"anthropic_api_key": "sk-test", "planner": "api"}))
    monkeypatch.setattr(server, "SETTINGS_PATH", settings)
    c = TestClient(server.create_app(tmp_path / "ws", token="t0k"))
    c.headers["X-Token"] = "t0k"
    c.ws = tmp_path / "ws"
    return c


def test_web_ask_round_trip(web, monkeypatch):
    monkeypatch.setattr(film_chat.ai, "ask_text", lambda system, user, **kw: "Làm lại s01")
    r = web.get("/api/projects/film/ask")
    assert r.status_code == 200
    body = r.json()
    assert body["messages"] == [] and body["has_final"] is False
    assert "Ý tưởng ban đầu" in [c["title"] for c in body["context"]]
    r = web.post("/api/projects/film/ask", json={"question": "Cảnh nào làm lại?", "pictures": False})
    assert r.status_code == 200, r.text
    assert [m["content"] for m in r.json()["messages"]] == ["Cảnh nào làm lại?", "Làm lại s01"]
    assert web.post("/api/projects/film/ask", json={"question": ""}).status_code == 400
    assert web.delete("/api/projects/film/ask").json()["messages"] == []


def test_web_ask_reports_claude_errors(web, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Claude không trả lời")

    monkeypatch.setattr(film_chat.ai, "ask_text", boom)
    r = web.post("/api/projects/film/ask", json={"question": "hỏi", "pictures": False})
    assert r.status_code == 502 and "Claude" in r.json()["detail"]


def test_web_ask_needs_token(web):
    web.headers.pop("X-Token")
    assert web.post("/api/projects/film/ask", json={"question": "hỏi"}).status_code == 403


def test_planning_remembers_the_idea(web, monkeypatch):
    def fake_plan(idea, **kw):
        return Storyboard.model_validate({"title": "Phim Mới", "model": "veo-lite", "scenes": [
            {"id": "s01", "mode": "t2v", "prompt": "rain", "duration": 4}]}), "api"

    monkeypatch.setattr(planner, "plan_auto", fake_plan)
    r = web.post("/api/projects/plan", json={"idea": "  ông bán vé số  ", "scenes": 1})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    for _ in range(100):
        j = web.get(f"/api/jobs/{jid}").json()
        if j["status"] != "running":
            break
        time.sleep(0.05)
    assert j["status"] == "done", j["lines"][-5:]
    pid = j["result"]["project"]
    assert json.loads((web.ws / pid / "ui.json").read_text())["idea"] == "ông bán vé số"


# --- posts for social networks ------------------------------------------------------

def _pack(schema, posts):
    return schema.model_validate({"posts": posts})


def _post(platform, language, **kw):
    return {"platform": platform, "language": language, "hook": "Xem đến cuối", "caption": "Câu chuyện",
            "hashtags": ["#phim ngan", "fyp"], **kw}


def test_social_posts_follow_the_choices(film, monkeypatch):
    from filmmaker import social

    d, out = film
    seen = {}

    def fake_ask(system, user, schema, **kw):
        seen["user"] = user
        return _pack(schema, [_post("tiktok", "Thai"), _post("tiktok", "Vietnamese"), _post("tiktok", "Vietnamese"),
                              _post("facebook", "Vietnamese", title="Tiêu đề"), _post("instagram", "English")])

    monkeypatch.setattr(social.ai, "ask", fake_ask)
    data = social.generate(d, out, ["facebook", "tiktok", "x"], ["Thai", "Vietnamese"], note="giọng hài", with_pictures=False)
    assert [(p["platform"], p["language"]) for p in data["posts"]] == [
        ("tiktok", "Vietnamese"), ("facebook", "Vietnamese"), ("tiktok", "Thai")]  # instagram/English not asked
    assert data["missing"] == ["Facebook / ไทย"]
    assert data["posts"][0]["hashtags"] == ["phimngan", "fyp"]
    assert "tiktok / Thai" in seen["user"] and "giọng hài" in seen["user"] and "Mai runs in the rain" in seen["user"]
    assert social.load(out)["posts"] == data["posts"]


def test_social_needs_a_choice(film):
    from filmmaker import social

    d, out = film
    with pytest.raises(ValueError):
        social.generate(d, out, [], ["Thai"])
    with pytest.raises(ValueError):
        social.generate(d, out, ["tiktok"], ["French"])


def test_web_social(web, monkeypatch):
    from filmmaker import social

    assert web.get("/api/projects/film/social").json()["result"] is None
    monkeypatch.setattr(social.ai, "ask", lambda system, user, schema, **kw: _pack(schema, [_post("instagram", "English")]))
    r = web.post("/api/projects/film/social", json={"platforms": ["instagram"], "languages": ["English"], "pictures": False})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["posts"][0]["platform"] == "instagram"
    assert web.post("/api/projects/film/social", json={"platforms": [], "languages": ["English"]}).status_code == 400
