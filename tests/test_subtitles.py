"""Subtitles in several languages: the storyboard fields, Claude translation, one .srt per language, the
language burned into the picture, the web endpoints and the CLI --lang list."""
import json
import shutil
import subprocess
import time

import pytest
import yaml

from filmmaker import subtitles
from filmmaker.assemble import assemble, burn_path, write_subtitles
from filmmaker.storyboard import Storyboard, load_storyboard, save_storyboard

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

SB = {"title": "Mưa", "model": "veo-lite", "aspect": "16:9", "subtitle_language": "Vietnamese",
      "subtitle_languages": ["Vietnamese", "English", "Thai"],
      "scenes": [{"id": "s01", "mode": "t2v", "prompt": "rain", "duration": 4, "narration": "Trời mưa",
                  "subtitles": {"English": "It rains"}},
                 {"id": "s02", "mode": "t2v", "prompt": "sun", "duration": 4, "narration": "Một năm sau"},
                 {"id": "s03", "mode": "t2v", "prompt": "end", "duration": 4}]}


def sb_():
    return Storyboard.model_validate(json.loads(json.dumps(SB)))


# --- storyboard -------------------------------------------------------------------

def test_languages_are_cleaned_and_narration_language_is_first():
    sb = Storyboard.model_validate({**SB, "subtitle_language": "English",
                                    "subtitle_languages": ["Vietnamese", " english ", "Thai", "vietnamese", "none"]})
    assert sb.subtitle_languages == ["English", "Vietnamese", "Thai"] and sb.subtitle_language == "English"
    only = Storyboard.model_validate({**SB, "subtitle_language": None, "subtitle_languages": ["Thai", "English"]})
    assert only.subtitle_language == "Thai"
    with pytest.raises(ValueError):
        Storyboard.model_validate({**SB, "subtitle_languages": [f"L{i}" for i in range(7)]})


def test_subtitle_text_per_language():
    sb = sb_()
    s1, s2, s3 = sb.scenes
    assert sb.subtitle_text(s1) == "Trời mưa" and sb.subtitle_text(s1, "english") == "It rains"
    assert sb.subtitle_text(s1, "Thai") is None and sb.subtitle_text(s3, "English") is None
    assert sb.subtitle_langs() == ["Vietnamese", "English", "Thai"]


def test_translations_never_change_the_clip_fingerprint():
    a, b = sb_(), sb_()
    b.scenes[1].subtitles = {"English": "A year later"}
    b.subtitle_languages = ["Vietnamese"]
    assert a.scene_fingerprint(a.scenes[1]) == b.scene_fingerprint(b.scenes[1])


def test_saved_yaml_keeps_language_keys_after_aspect(tmp_path):
    p = tmp_path / "sb.yaml"
    save_storyboard(sb_(), p)
    keys = list(yaml.safe_load(p.read_text()).keys())
    assert keys[keys.index("aspect") + 1: keys.index("aspect") + 3] == ["subtitle_language", "subtitle_languages"]
    assert load_storyboard(p).scenes[0].subtitles == {"English": "It rains"}


# --- translation --------------------------------------------------------------------

def test_missing_lists_untranslated_lines():
    assert subtitles.missing(sb_()) == {"English": ["s02"], "Thai": ["s01", "s02"]}
    assert subtitles.missing(sb_(), ["Thai"]) == {"Thai": ["s01", "s02"]}


def test_translate_asks_only_for_missing_lines_and_keeps_the_rest(monkeypatch):
    seen = {}

    def ask(system, user, schema, **kw):
        seen["user"] = user
        return schema(lines=[{"id": "s02", "language": "english", "text": " A  year later "},
                             {"id": "s01", "language": "Thai", "text": "ฝนตก"},
                             {"id": "s02", "language": "Thai", "text": "หนึ่งปีต่อมา"},
                             {"id": "s01", "language": "English", "text": "should not replace"},
                             {"id": "s09", "language": "Thai", "text": "unknown scene"}])

    monkeypatch.setattr(subtitles.ai, "ask", ask)
    done = subtitles.translate(sb_())
    assert done == {"s02": {"English": "A year later", "Thai": "หนึ่งปีต่อมา"}, "s01": {"Thai": "ฝนตก"}}
    assert "English: s02" in seen["user"] and "Thai: s01, s02" in seen["user"] and "s03" not in seen["user"]
    raw = json.loads(json.dumps(SB))
    assert subtitles.apply(raw, done) == ["s01", "s02"]
    assert raw["scenes"][0]["subtitles"] == {"English": "It rains", "Thai": "ฝนตก"}


def test_translate_with_one_language_does_nothing(monkeypatch):
    monkeypatch.setattr(subtitles.ai, "ask", lambda *a, **k: pytest.fail("no call"))
    sb = Storyboard.model_validate({**SB, "subtitle_languages": None})
    assert subtitles.translate(sb) == {} and subtitles.missing(sb) == {}


def test_ensure_without_claude_only_warns():
    lines = []
    assert subtitles.ensure(sb_(), lambda fn: pytest.fail("no write"), backend=None, log=lines.append) is False
    assert any("Chưa dịch được phụ đề" in l for l in lines)


def test_lang_codes():
    assert [subtitles.lang_code(x) for x in ("Vietnamese", "english", "Thai", "Klingon (TNG)")] == \
        ["vi", "en", "th", "klingon-tng"]


# --- assembling --------------------------------------------------------------------

def test_one_srt_per_language_and_burn_choice(tmp_path):
    sb = sb_()
    out = tmp_path / "final.mp4"
    (tmp_path / "final.ja.srt").write_text("old")  # a language no longer in the film
    by_id = {s.id: s for s in sb.scenes}

    def entries(lang):
        return [(i * 4.0, i * 4.0 + 3, t) for i, s in enumerate(sb.scenes) if (t := sb.subtitle_text(by_id[s.id], lang))]

    lines = []
    files = write_subtitles(sb, out, entries, log=lines.append)
    assert set(files) == {"", "Vietnamese", "English"}  # Thai has no translation yet: no file
    assert "Trời mưa" in (tmp_path / "final.srt").read_text() and (tmp_path / "final.vi.srt").is_file()
    en = (tmp_path / "final.en.srt").read_text()
    assert "It rains" in en and "Một năm sau" not in en
    assert not (tmp_path / "final.ja.srt").exists() and not (tmp_path / "final.th.srt").exists()
    assert any("English còn thiếu 1 câu" in l for l in lines)
    assert burn_path(files, "english") == tmp_path / "final.en.srt"
    assert burn_path(files, None) == tmp_path / "final.srt" and burn_path(files, "Thai") == tmp_path / "final.srt"


@needs_ffmpeg
def test_stitch_burns_the_chosen_language(tmp_path, monkeypatch):
    clips = []
    for sid in ("s01", "s02"):
        p = tmp_path / f"{sid}.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x180:d=1:r=24",
                        "-pix_fmt", "yuv420p", str(p)], check=True)
        clips.append((sid, p))
    seen = []
    real = subprocess.run

    def spy(cmd, **kw):
        if cmd and str(cmd[0]).endswith("ffmpeg") and "-filter_complex" in cmd:
            seen.append(cmd[cmd.index("-filter_complex") + 1])
        return real(cmd, **kw)

    monkeypatch.setattr("filmmaker.assemble.subprocess.run", spy)
    out = tmp_path / "final.mp4"
    assemble(sb_(), clips, out, burn_subtitles=True, burn_lang="English", log=lambda *_: None)
    assert out.is_file() and "final.en.srt" in seen[-1]


# --- web ---------------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi")


@pytest.fixture
def web(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from filmmaker import planner
    from filmmaker.web import server

    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": False, "logged_in": False})
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"anthropic_api_key": "sk-test", "planner": "api"}))
    monkeypatch.setattr(server, "SETTINGS_PATH", settings)
    d = tmp_path / "ws" / "film"
    d.mkdir(parents=True)
    (d / "storyboard.yaml").write_text(yaml.safe_dump(SB, allow_unicode=True), encoding="utf-8")
    c = TestClient(server.create_app(tmp_path / "ws", token="t0k"))
    c.headers["X-Token"] = "t0k"
    c.ws = tmp_path / "ws"
    return c


def _fake_translation(monkeypatch):
    def ask(system, user, schema, **kw):
        rows = []
        for part in user.split("one line per scene and language):\n")[1].splitlines():
            lang, ids = part[2:].split(": ")
            rows += [{"id": i, "language": lang, "text": f"{lang}-{i}"} for i in ids.split(", ")]
        return schema(lines=rows)

    monkeypatch.setattr(subtitles.ai, "ask", ask)


def test_web_translate_and_detail(web, monkeypatch):
    d = web.get("/api/projects/film").json()
    assert d["subtitle_missing"] == {"English": ["s02"], "Thai": ["s01", "s02"]}
    assert [f["code"] for f in d["subtitle_files"]] == ["vi", "en", "th"]
    _fake_translation(monkeypatch)
    r = web.post("/api/projects/film/subtitles/translate", json={})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == ["s01", "s02"] and r.json()["subtitle_missing"] == {}
    raw = yaml.safe_load((web.ws / "film" / "storyboard.yaml").read_text())
    assert raw["scenes"][0]["subtitles"] == {"English": "It rains", "Thai": "Thai-s01"}  # hand line kept
    r = web.post("/api/projects/film/subtitles/translate", json={"force": True, "langs": ["English"]})
    assert yaml.safe_load((web.ws / "film" / "storyboard.yaml").read_text())["scenes"][0]["subtitles"]["English"] == "English-s01"


def test_web_translate_needs_two_languages(web):
    p = web.ws / "film" / "storyboard.yaml"
    raw = yaml.safe_load(p.read_text())
    raw.pop("subtitle_languages")
    p.write_text(yaml.safe_dump(raw, allow_unicode=True))
    assert web.post("/api/projects/film/subtitles/translate", json={}).status_code == 400


def test_web_rejects_a_bad_subtitle_language_on_save(web):
    raw = yaml.safe_load((web.ws / "film" / "storyboard.yaml").read_text())
    raw["subtitle_languages"] = ["Vietnamese", "bad\nname"]
    assert web.put("/api/projects/film", json={"storyboard": raw}).status_code == 400


def test_web_plan_with_several_languages_translates_right_away(web, monkeypatch):
    from filmmaker import planner

    def fake_plan(idea, **kw):
        assert kw["language_hint"] == "English"
        return Storyboard.model_validate({"title": "Rain", "model": "veo-lite", "subtitle_language": "English",
                                          "scenes": [{"id": "s01", "mode": "t2v", "prompt": "rain", "duration": 4,
                                                      "narration": "It rains"}]}), "api"

    monkeypatch.setattr(planner, "plan_auto", fake_plan)
    _fake_translation(monkeypatch)
    r = web.post("/api/projects/plan", json={"idea": "mưa", "scenes": 1, "languages": ["English", "Vietnamese", "english"]})
    assert r.status_code == 200, r.text
    for _ in range(100):
        j = web.get(f"/api/jobs/{r.json()['id']}").json()
        if j["status"] != "running":
            break
        time.sleep(0.05)
    assert j["status"] == "done", j["lines"][-5:]
    raw = yaml.safe_load((web.ws / j["result"]["project"] / "storyboard.yaml").read_text())
    assert raw["subtitle_languages"] == ["English", "Vietnamese"]
    assert raw["scenes"][0]["subtitles"] == {"Vietnamese": "Vietnamese-s01"}
    assert web.post("/api/projects/plan", json={"idea": "x", "languages": ["none"]}).status_code == 400


# --- CLI ---------------------------------------------------------------------------

def test_cli_lang_list():
    import click

    from filmmaker.cli import _cli_langs

    assert _cli_langs("Vietnamese, English,thai,english", "--lang") == ["Vietnamese", "English", "thai"]
    with pytest.raises(click.BadParameter):
        _cli_langs("Vietnamese,,English", "--lang")
