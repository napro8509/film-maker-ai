"""Autopilot end to end with the fake gflow; the Claude reviews are replaced by canned answers."""
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from filmmaker import autopilot, flow_caps, reviewer
from filmmaker.autopilot import Autopilot, AutoSettings
from filmmaker.gflow import Gflow
from filmmaker.pipeline import Pipeline, RunOptions
from filmmaker.storyboard import load_storyboard

FAKE = Path(__file__).parent / "fake_gflow.py"
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

STORY = """
title: Auto film
aspect: "9:16"
model: veo-lite
style: "cinematic"
characters:
  - name: Lan
    description: "short black hair, red scarf"
thumbnail:
  prompt: "Lan smiling at dawn"
  text: "Ly cà phê đầu tiên"
scenes:
  - id: s01
    mode: keyframe
    image_prompt: "Lan at a cafe"
    prompt: "Lan smiles"
    duration: 5
    narration: "Xin chào"
  - id: s02
    mode: keyframe
    image_prompt: "Lan at the window"
    prompt: "Lan sips coffee"
    duration: 8
  - id: s03
    mode: t2v
    prompt: "Rain on the street"
    duration: 4
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    sb_path = tmp_path / "proj" / "storyboard.yaml"
    sb_path.parent.mkdir()
    sb_path.write_text(STORY)
    log = tmp_path / "calls.log"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(log))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)
    asked: list[str] = []

    def fake_ask(system, user, schema, *, images=(), **kw):
        asked.append(schema.__name__)
        if schema is reviewer.StoryboardReview:
            return schema(verdict="PASS", score=8, summary="ổn",
                          scenes=[reviewer.SceneFix(id="s02", issues=["mơ hồ"], prompt="Lan sips hot coffee slowly")])
        if schema is reviewer.KeyframeReview:
            ids = [label.split()[1] for label, _ in images if label.endswith("keyframe")]
            first = len([a for a in asked if a == "KeyframeReview"]) == 1
            return schema(summary="", scenes=[
                reviewer.KeyframeVerdict(id=i, verdict="REVISE" if (first and i == "s01") else "PASS", score=6,
                                         issues=["sai mặt"] if first and i == "s01" else [],
                                         image_prompt="Lan at a cafe, face to camera") for i in ids])
        if schema is reviewer.ClipReview:
            ids = [label.split()[1] for label, _ in images if " clip" in label]
            return schema(summary="", scenes=[
                reviewer.ClipVerdict(id=i, verdict="RERENDER" if i == "s03" else "PASS", score=5,
                                     prompt="Gentle rain on an empty street") for i in ids])
        if schema is reviewer.FilmReview:
            return schema(verdict="PASS", score=8, summary="tốt")
        if schema.__name__ == "QCAnswer":
            sid = user.split("QC scene ")[1].split()[0]
            return schema(verdict="FAIL" if sid == "s03" else "PASS", score=4 if sid == "s03" else 9,
                          issues=["sai cảnh"] if sid == "s03" else [], prompt="Gentle rain on an empty street")
        raise AssertionError(schema)

    monkeypatch.setattr(reviewer.ai, "ask", fake_ask)
    monkeypatch.setattr(reviewer.ai, "pick_backend", lambda *a, **k: None)  # editor: rules, no Claude

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return sb_path, calls, asked


def _pilot(sb_path, **kw):
    lines: list[str] = []
    p = Autopilot(sb_path, sb_path.parent / "output" / "storyboard", Gflow(log=lambda *_: None),
                  settings=AutoSettings(pause=(0, 0), **kw), log=lines.append, sleep=lambda *_: None)
    return p, lines


def test_autopilot_makes_the_whole_film(env):
    sb_path, calls, asked = env
    p, lines = _pilot(sb_path)
    res = p.run()
    out = sb_path.parent / "output" / "storyboard"
    assert Path(res["final"]).is_file() and (out / "final.mp4").is_file()
    assert Path(res["thumbnail"]).is_file()
    assert res["film_score"] == 8
    raw = yaml.safe_load(sb_path.read_text())
    s01, s02, s03 = raw["scenes"]
    assert s01["duration"] == 6  # 5 is not accepted by veo-lite: rounded up before anything was sent
    assert s02["prompt"] == "Lan sips hot coffee slowly"  # storyboard review fix
    assert s01["image_prompt"] == "Lan at a cafe, face to camera"  # keyframe review fix, remade for free
    assert s03["prompt"] != "Gentle rain on an empty street"  # QC only flags: no paid re-render by itself
    assert raw["characters"][0]["image"].startswith("assets/characters/")
    st = json.loads((out / "autopilot.json").read_text())
    assert st["rerendered"] == [] and st["stages"]["review_clips"]["failed"] == ["s03"]
    assert res["qc_failed"] == ["s03"] and any("AI QC đánh FAIL: s03" in l for l in lines)
    qc = json.loads((out / "qc.json").read_text())
    assert qc["s03"]["verdict"] == "FAIL" and qc["s01"]["verdict"] == "PASS" and qc["s01"]["sig"]
    assert all(st["stages"][k]["status"] in ("done",) for k, _ in autopilot.STAGES)
    assert (out / "reviews" / "report.md").is_file()
    videos = [c for c in calls() if c[0] == "video"]
    assert len(videos) == 3  # 3 scenes, no re-render
    assert asked.count("KeyframeReview") == 2  # the remade keyframe was checked again


def test_autopilot_resumes_without_paying_again(env):
    sb_path, calls, asked = env
    _pilot(sb_path)[0].run()
    n_video = len([c for c in calls() if c[0] == "video"])
    n_ask = len(asked)
    res = _pilot(sb_path)[0].run()
    assert Path(res["final"]).is_file()
    assert len([c for c in calls() if c[0] == "video"]) == n_video
    assert len(asked) == n_ask  # every review was already done for these exact files


def test_autopilot_stops_on_request(env):
    sb_path, calls, _ = env
    stop = {"now": False}
    p, lines = _pilot(sb_path)
    p.should_stop = lambda: stop["now"]
    orig = p._stage_keyframes

    def keyframes_then_stop(result):
        orig(result)
        stop["now"] = True
    p._stage_keyframes = keyframes_then_stop
    res = p.run()
    assert res.get("stopped")
    assert not [c for c in calls() if c[0] == "video"]


def test_autopilot_without_reviews(env):
    sb_path, calls, asked = env
    res = _pilot(sb_path, review=False)[0].run()
    assert Path(res["final"]).is_file()
    assert asked == []


def test_account_caps_carry_over_to_new_projects(tmp_path, monkeypatch):
    g = Gflow(binary="gflow-x", log=lambda *_: None)
    flow_caps.remember(g.binary, g.profile, i2i_ui=True, i2v_unsupported=True)
    sb_path = tmp_path / "s.yaml"
    sb_path.write_text(STORY)
    pipe = Pipeline(load_storyboard(sb_path), tmp_path / "out", g, log=lambda *_: None)
    assert pipe.state.data["i2i_ui"] and pipe.i2v_unsupported
    other = Pipeline(load_storyboard(sb_path), tmp_path / "out2", Gflow(binary="other", log=lambda *_: None),
                     log=lambda *_: None)
    assert "i2i_ui" not in other.state.data
    dry = Pipeline(load_storyboard(sb_path), tmp_path / "out3", Gflow(binary="gflow-x", dry_run=True), log=lambda *_: None)
    assert "i2i_ui" not in dry.state.data


def test_image_pause_is_shorter_than_video_pause(tmp_path):
    sb_path = tmp_path / "s.yaml"
    sb_path.write_text(STORY)
    pipe = Pipeline(load_storyboard(sb_path), tmp_path / "out", Gflow(dry_run=True), log=lambda *_: None)
    vals = [pipe._image_pause(RunOptions(pause=(8, 20))) for _ in range(50)]
    assert all(2 <= v <= 5 for v in vals)
    assert pipe._image_pause(RunOptions(image_pause=(1, 1))) == 1


def test_apply_scene_fixes_only_touches_given_fields():
    raw = {"characters": [{"name": "Lan"}], "scenes": [{"id": "s01", "prompt": "a", "narration": "x"}]}
    fixes = [reviewer.SceneFix(id="s01", prompt="b", dialogue=[reviewer.DialogueFix(character="Lan", line="Chào"),
                                                                reviewer.DialogueFix(character="Ghost", line="Boo")]),
             reviewer.SceneFix(id="nope", prompt="z")]
    assert reviewer.apply_scene_fixes(raw, fixes) == ["s01"]
    assert raw["scenes"][0] == {"id": "s01", "prompt": "b", "narration": "x",
                                "dialogue": [{"character": "Lan", "line": "Chào"}]}


def test_frame_strip_and_contact_sheet(tmp_path):
    import subprocess
    v = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", "3",
                    str(v)], check=True)
    strip = reviewer.frame_strip(v, tmp_path / "strip.jpg")
    sheet = reviewer.contact_sheet(v, tmp_path / "sheet.jpg", cols=2, rows=2)
    assert strip and strip.stat().st_size > 1000
    assert sheet and sheet.stat().st_size > 1000
    assert reviewer.frame_strip(tmp_path / "missing.mp4", tmp_path / "x.jpg") is None


# --- web API ----------------------------------------------------------------------

from tests.model_helpers import write_project  # noqa: E402
from tests.test_model_web import make_client, wait  # noqa: E402,F401


@pytest.fixture
def web(make_client, monkeypatch):
    from filmmaker import planner

    monkeypatch.setattr(reviewer.ai, "ask", lambda system, user, schema, **kw: (
        schema(verdict="PASS", score=9, summary="tốt") if schema in (reviewer.StoryboardReview, reviewer.FilmReview)
        else schema(summary="", scenes=[])))
    monkeypatch.setattr(reviewer.ai, "pick_backend", lambda *a, **k: None)
    monkeypatch.setattr(planner, "suggest_titles", lambda *a, **k: [{"text": "Tiêu đề", "vi": ""}])
    return make_client()


def test_web_autopilot_on_existing_project(web):
    write_project(web.ws, "film", "veo-lite", [{"id": "s01"}, {"id": "s02"}])
    r = web.post("/api/projects/film/autopilot", json={"review": True, "burn_subtitles": False})
    assert r.status_code == 200, r.text
    job = wait(web, r.json(), timeout=120)
    assert job["status"] == "done", job["lines"][-15:]
    assert job["kind"] == "auto" and job["stage"]["key"] == "review_film"
    d = web.get("/api/projects/film").json()
    assert d["final_url"] and d["thumbnail"]["url"]
    keys = [s["key"] for s in d["autopilot"]["stages"]]
    assert keys[0] == "storyboard" and keys[-1] == "review_film"
    assert d["autopilot"]["film_review"]["score"] == 9


def test_web_autopilot_new_film_plans_then_opens_the_project(web, make_client, monkeypatch):
    from filmmaker import planner
    from filmmaker.storyboard import Storyboard

    def fake_plan(idea, **kw):
        assert kw["scenes"] == 2 and kw["backend"] == "api"
        return Storyboard.model_validate({"title": "Phim tự động", "model": "veo-lite", "scenes": [
            {"id": "s01", "mode": "t2v", "prompt": "rain", "duration": 4},
            {"id": "s02", "mode": "t2v", "prompt": "sun", "duration": 4}]}), "api"
    monkeypatch.setattr(planner, "plan_auto", fake_plan)
    c = make_client(anthropic_api_key="sk-test", planner="api")
    r = c.post("/api/projects/autopilot", json={"idea": "mưa nắng", "scenes": 2, "aspect": "9:16",
                                                "language": "Vietnamese", "dialogue_language": "Vietnamese"})
    assert r.status_code == 200, r.text
    job = wait(c, r.json(), timeout=120)
    assert job["status"] == "done", job["lines"][-15:]
    assert job["project"] and (c.ws / job["project"] / "output" / "storyboard" / "final.mp4").is_file()
    assert job["title"] == "Tạo tự động: Phim tự động"


def test_web_autopilot_refuses_bad_storyboard(web):
    p = write_project(web.ws, "film", "veo-lite", [{"id": "s01"}])
    p.write_text("title: x\nscenes: []\n")
    assert web.post("/api/projects/film/autopilot", json={}).status_code == 400


def test_portrait_retry_saves_the_right_character(env, monkeypatch):
    sb_path, calls, _ = env
    raw = yaml.safe_load(sb_path.read_text())
    raw["characters"].append({"name": "Minh", "description": "tall man, glasses"})
    sb_path.write_text(yaml.safe_dump(raw, allow_unicode=True))
    # Lan's portrait fails once; on the retry Minh is not in the list any more: Lan must still be saved as Lan.
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "short black hair:25:false:1")
    p, lines = _pilot(sb_path)
    p._stage_characters({})
    chars = {c["name"]: c.get("image") for c in yaml.safe_load(sb_path.read_text())["characters"]}
    assert chars["Lan"] == "assets/characters/lan.png" and chars["Minh"] == "assets/characters/minh.png"


def test_no_new_portrait_for_a_character_in_a_finished_clip(env):
    sb_path, calls, _ = env
    _pilot(sb_path, review=False)[0].run()
    raw = yaml.safe_load(sb_path.read_text())
    raw["characters"][0].pop("image")
    sb_path.write_text(yaml.safe_dump(raw, allow_unicode=True))
    n = len(calls())
    p, lines = _pilot(sb_path, review=False)
    p._stage_characters({})
    assert len(calls()) == n and any("không tạo ảnh mới" in l for l in lines)


def test_scene_deleted_by_flow_is_rewritten_then_sent_once(env, monkeypatch):
    from filmmaker.flow_ui import BLOCKED_MARK
    from filmmaker.pipeline import State
    sb_path, calls, _ = env
    _pilot(sb_path, review=False)[0].run()
    out = sb_path.parent / "output" / "storyboard"
    st = State(out / "state.json")
    st.update("s03", status="submitting", error=f"Flow (giao diện chat): {BLOCKED_MARK}: Flow đã tạo rồi tự xoá video",
              submitted_at="2026-11-11T00:00:00+00:00")
    (out / "clips" / "s03.mp4").unlink()
    monkeypatch.setattr(reviewer, "soften_scene",
                        lambda sb, sid, err, **kw: reviewer.SceneFix(id=sid, prompt="Soft rain, an empty street"))
    n = len([c for c in calls() if c[0] == "video"])
    p, lines = _pilot(sb_path, review=False)
    p._stage_videos({})
    assert yaml.safe_load(sb_path.read_text())["scenes"][2]["prompt"] == "Soft rain, an empty street"
    assert len([c for c in calls() if c[0] == "video"]) == n + 1
    assert p.data["resent"] == ["s03"]
    p._soften_blocked()  # already resent: never a second time
    assert len([c for c in calls() if c[0] == "video"]) == n + 1


def test_audio_failure_is_rewritten_with_gentler_dialogue_then_sent_once(env, monkeypatch):
    from filmmaker.pipeline import State
    sb_path, calls, _ = env
    _pilot(sb_path, review=False)[0].run()
    out = sb_path.parent / "output" / "storyboard"
    st = State(out / "state.json")
    st.update("s02", status="submitting", submitted_at="2026-11-11T00:00:00+00:00",
              error="Flow (giao diện chat): Flow báo tạo video lỗi: warning\nFailed\nAudio generation failed. "
                    "Please try a different prompt or send feedback.")
    (out / "clips" / "s02.mp4").unlink()
    name = yaml.safe_load(sb_path.read_text())["characters"][0]["name"]
    seen = {}

    def soften(sb, sid, err, **kw):
        seen["err"] = err
        return reviewer.SceneFix(id=sid, prompt="She smiles softly at the window",
                                 dialogue=[reviewer.DialogueFix(character=name, line="Mình ổn mà", delivery="calm, soft")])

    monkeypatch.setattr(reviewer, "soften_scene", soften)
    n = len([c for c in calls() if c[0] == "video"])
    p, lines = _pilot(sb_path, review=False)
    p._stage_videos({})
    scene = yaml.safe_load(sb_path.read_text())["scenes"][1]
    assert scene["prompt"] == "She smiles softly at the window"
    assert scene["dialogue"] == [{"character": name, "line": "Mình ổn mà", "delivery": "calm, soft"}]
    assert "Audio generation failed" in seen["err"]
    assert any("không tạo được âm thanh" in l for l in lines) and any("thoại mới" in l for l in lines)
    assert len([c for c in calls() if c[0] == "video"]) == n + 1  # one rewritten resend, not the old scene again
    assert p.data["resent"] == ["s02"]


def test_soften_prompt_mentions_audio_only_for_audio_failures(monkeypatch):
    from filmmaker.storyboard import Storyboard
    sb = Storyboard.model_validate({"title": "x", "model": "veo-lite", "scenes": [
        {"id": "s01", "mode": "t2v", "prompt": "rain", "duration": 4}]})
    got = []
    monkeypatch.setattr(reviewer.ai, "ask", lambda system, user, schema, **kw: got.append((system, user)) or schema(id="s01"))
    reviewer.soften_scene(sb, "s01", "Failed\nAudio generation failed. Please try a different prompt")
    reviewer.soften_scene(sb, "s01", "Flow xoá video: deleted")
    assert "Audio generation failed" in got[0][1] and "rewrite the dialogue" in got[0][1]
    assert "rewrite the dialogue" not in got[1][1]
    assert "children" in got[0][0] and "Audio generation failed" in got[0][0]


def test_flow_deleted_video_is_seen_after_the_offer():
    from filmmaker.flow_ui import classify_replies
    bubble = ("Show thinking\nI must not violate anything. Would you like me to kick off this 1 video generation, "
              "costing 5 credits?\ncheck\nApprove\nclose\nReject\nwarning\n\nVideo has been deleted\n\nYour video "
              "generation has started!")
    assert classify_replies([bubble]) == ("deleted", False)
    assert classify_replies([bubble.replace("Video has been deleted", "")]) == (None, False)
