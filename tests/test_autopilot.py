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
    # s03 failed QC and the gate left it out: a third of the story is missing, so the film review is REVISE
    assert res["film_score"] == 4 and res["film_verdict"] == "REVISE"
    assert res["qc_excluded"] == ["s03"] and any("Phim chỉ có 2/3 cảnh" in l for l in lines)
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


def test_review_can_add_the_real_speaker_as_a_new_character():
    # s27 of "18 ngày": the farmer's line was given to WIT (an officer, not in the rice field). The reviewer saw
    # it but could not name a speaker outside the list, so the fix was dropped and nothing said so.
    raw = {"characters": [{"name": "WIT", "description": "naval officer"}],
           "scenes": [{"id": "s27", "prompt": "The old farmer nods.", "characters": ["WIT"],
                       "dialogue": [{"character": "WIT", "line": "Cứ để ngập ruộng tôi."}]}]}
    rev = reviewer.StoryboardReview(verdict="REVISE", score=7, summary="", scenes=[
        reviewer.SceneFix(id="s27", issues=["thoại của ông nông dân"],
                          dialogue=[reviewer.DialogueFix(character="FARMER", line="Cứ để ngập ruộng tôi.")])],
        characters=[reviewer.NewCharacter(name="FARMER", description="70-year-old Thai farmer, straw hat"),
                    reviewer.NewCharacter(name="wit", description="duplicate, ignored")])
    assert reviewer.add_characters(raw, rev.characters) == ["FARMER"]
    assert reviewer.apply_scene_fixes(raw, rev.scenes) == ["s27"]
    s27 = raw["scenes"][0]
    assert s27["dialogue"] == [{"character": "FARMER", "line": "Cứ để ngập ruộng tôi."}]
    assert s27["characters"] == ["WIT", "FARMER"]
    assert "ADD that person" in reviewer.STORYBOARD_SYSTEM


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


def test_prominent_people_refusal_is_rewritten_then_sent_once(env, monkeypatch):
    # "18 ngày" (a true story): Flow refused s29/s37/s38 with "might violate our policies about generating
    # prominent people", and the scene was sent again unchanged. Now Claude rewrites it first.
    from filmmaker.pipeline import State
    sb_path, calls, _ = env
    _pilot(sb_path, review=False)[0].run()
    out = sb_path.parent / "output" / "storyboard"
    State(out / "state.json").update(
        "s02", status="submitting", submitted_at="2026-11-11T00:00:00+00:00",
        error="Flow (giao diện chat): Flow báo tạo video lỗi: warning\nFailed\nThis prompt might violate our "
              "policies about generating prominent people. Please try a different prompt or send feedback.")
    (out / "clips" / "s02.mp4").unlink()
    got = []
    monkeypatch.setattr(reviewer.ai, "ask", lambda system, user, schema, **kw:
                        got.append((system, user)) or schema(id="s02", prompt="A diver nods and steps forward",
                                                            content_direction="Quiet courage"))
    n = len([c for c in calls() if c[0] == "video"])
    p, lines = _pilot(sb_path, review=False)
    p._stage_videos({})
    assert yaml.safe_load(sb_path.read_text())["scenes"][1]["prompt"] == "A diver nods and steps forward"
    assert any("người nổi tiếng" in l for l in lines)
    assert "prominent people" in got[0][1] and "true story" in got[0][0]
    assert len([c for c in calls() if c[0] == "video"]) == n + 1 and p.data["resent"] == ["s02"]
    assert "prominent people" in reviewer.STORYBOARD_SYSTEM


def test_harmful_content_refusal_is_rewritten_too(monkeypatch):
    # s45/s47: a sedated boy carried on a stretcher was refused as "harmful content".
    from filmmaker.storyboard import Storyboard
    sb = Storyboard.model_validate({"title": "x", "model": "veo-lite", "scenes": [
        {"id": "s45", "mode": "t2v", "prompt": "a diver carries a sleeping boy", "duration": 8}]})
    got = []
    monkeypatch.setattr(reviewer.ai, "ask", lambda system, user, schema, **kw: got.append((system, user)) or schema(id="s45"))
    reviewer.soften_scene(sb, "s45", "Failed\nThis prompt might violate our policies about generating harmful content.")
    assert "harmful content" in got[0][1] and "unconscious" in got[0][0]


def test_scenes_flow_never_accepted_get_a_last_try_with_a_new_keyframe(env, monkeypatch):
    # 18 ngày: s10/s14/s26/s42 got "Something went wrong" on every try with the same picture. After the usual
    # retry, such scenes get a fresh (free) keyframe and one last send.
    sb_path, _, _ = env
    p, lines = _pilot(sb_path, review=False)
    runs: list[dict] = []
    left = iter([["s01", "s02", "s03"], ["s01", "s03"], ["s01", "s03"]])
    monkeypatch.setattr(p, "_video_run", lambda **kw: runs.append(kw))
    monkeypatch.setattr(p, "_unsent_failures", lambda: next(left))
    monkeypatch.setattr(p, "_soften_blocked", lambda: None)
    monkeypatch.setattr(p, "_refusal", lambda pipe, s: None)  # not a content refusal (e.g. a dropped page)
    monkeypatch.setattr(p, "pipe", lambda *a: type("P", (), {"sb": p.sb(), "is_done": lambda self, s: True,
                                                              "state": None})())
    try:
        p._stage_videos({})
    except Exception:  # what follows the retries is not under test here
        pass
    assert runs[1] == {"only": {"s01", "s02", "s03"}, "confirm": set()}
    modes = {s.id: s.mode for s in p.sb().scenes}
    assert runs[2]["only"] == {"s01", "s03"}
    assert runs[2]["force_keyframes"] == {sid for sid in ("s01", "s03") if modes[sid] == "keyframe"}
    assert any("thử lần cuối" in l for l in lines)


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


def test_a_failed_keyframe_batch_is_split_and_never_skips_the_rest(env, monkeypatch):
    # Claude Code gave up on a batch (error_max_structured_output_retries) and the whole keyframe review was
    # skipped: s08 went to Veo with the speaker turned away. Now the batch is split, and only a picture that
    # still cannot be reviewed is left out, by name.
    sb_path, calls, asked = env
    real = reviewer.ai.ask
    tries: list[list[str]] = []

    def flaky(system, user, schema, *, images=(), **kw):
        if schema is reviewer.KeyframeReview:
            ids = [label.split()[1] for label, _ in images if label.endswith("keyframe")]
            tries.append(ids)
            if len(ids) > 1 or ids == ["s02"]:
                raise RuntimeError("Claude Code không trả lời được: error_max_structured_output_retries")
            return schema(summary="", scenes=[reviewer.KeyframeVerdict(id=i, verdict="PASS", score=9) for i in ids])
        return real(system, user, schema, images=images, **kw)

    monkeypatch.setattr(reviewer.ai, "ask", flaky)
    p, lines = _pilot(sb_path)
    p.run()
    st = json.loads((sb_path.parent / "output" / "storyboard" / "autopilot.json").read_text())
    stage = st["stages"]["review_keyframes"]
    assert stage["status"] == "done" and stage["scores"] == {"s01": 9} and stage["unreviewed"] == ["s02"]
    assert "chưa review được s02" in stage["note"]
    assert tries == [["s01", "s02"], ["s01"], ["s02"], ["s02"]]  # split, then one retry for the stubborn one
    assert any("chia nhỏ" in l for l in lines) and any("hãy tự xem" in l for l in lines)
    assert not any("max_structured_output_retries" in l for l in lines)  # said in plain words
    assert any("sai định dạng" in l for l in lines)


def test_keyframes_are_reviewed_in_even_groups():
    from filmmaker.autopilot import _even_chunks
    assert [len(c) for c in _even_chunks(list(range(9)), 4)] == [3, 3, 3]
    assert [len(c) for c in _even_chunks(list(range(9)), 5)] == [5, 4]
    assert [len(c) for c in _even_chunks(list(range(3)), 5)] == [3]
    assert _even_chunks([], 5) == []
    assert sum(_even_chunks(list(range(11)), 3), []) == list(range(11))


def test_a_failed_group_size_is_not_tried_again(monkeypatch):
    # 9 keyframes: the first group of 5 fails, so the other 4 waiting are not sent as a group that big again.
    from filmmaker import autopilot
    p = autopilot.Autopilot.__new__(autopilot.Autopilot)
    p._kf_batch = autopilot.REVIEW_BATCH
    lines: list[str] = []
    p.log, p.write_review, p.ai_kw = lines.append, lambda *a: None, lambda: {}
    sizes: list[int] = []

    def ask(sb, chunk, **kw):
        sizes.append(len(chunk))
        if len(chunk) > 3:
            raise RuntimeError("Claude Code không trả lời được: error_max_structured_output_retries")
        return reviewer.KeyframeReview(summary="", scenes=[
            reviewer.KeyframeVerdict(id=sid, verdict="PASS", score=8) for sid, _ in chunk])

    monkeypatch.setattr(reviewer, "review_keyframes", ask)
    items = [(f"s{i:02d}", None) for i in range(1, 10)]
    unreviewed: set[str] = set()
    got = p._review_keyframe_batches(None, items, 1, unreviewed)
    assert sorted(v.id for v in got) == [sid for sid, _ in items] and not unreviewed
    assert sizes == [5, 3, 3, 3]  # one failure, then the 9 in even groups of 3
    assert sum("chia nhỏ" in l for l in lines) == 1


def test_keyframe_reviewer_checks_who_faces_the_camera():
    assert "Who speaks" in reviewer.KEYFRAME_SYSTEM and "facing the camera" in reviewer.KEYFRAME_SYSTEM
    # a listener who is the biggest face or has an open mouth is mouthing the line in the video
    assert "largest / closest face" in reviewer.KEYFRAME_SYSTEM and "open-mouthed grin" in reviewer.KEYFRAME_SYSTEM


MUSE_ERR = ("Muse (giao diện chat): Muse báo không tạo được video này (Failed to create…). "
            "Muse: That s03 video couldn't be generated")


def _muse_refused(sb_path, sid="s03", error=MUSE_ERR):
    from filmmaker.pipeline import State
    _pilot(sb_path, review=False)[0].run()
    out = sb_path.parent / "output" / "storyboard"
    State(out / "state.json").update(sid, status="failed", error=error, exit_code=42, submitted_at=None)
    (out / "clips" / f"{sid}.mp4").unlink()
    return out


def test_muse_refusal_is_rewritten_and_succeeds(env, monkeypatch):
    sb_path, calls, _ = env
    _muse_refused(sb_path)
    seen = []
    monkeypatch.setattr(reviewer, "soften_scene", lambda sb, sid, err, earlier=None, **kw: (
        seen.append(earlier), reviewer.SceneFix(id=sid, prompt="He smiles warmly and nods"))[1])
    n = len([c for c in calls() if c[0] == "video"])
    p, lines = _pilot(sb_path, review=False)
    p._soften_blocked()
    assert yaml.safe_load(sb_path.read_text())["scenes"][2]["prompt"] == "He smiles warmly and nods"
    assert len([c for c in calls() if c[0] == "video"]) == n + 1
    assert seen == [None]  # first rewrite: no refused rewrites to show yet
    assert any("lần 1/2" in l for l in lines) and not any("bỏ qua" in l for l in lines)
    assert "s03" not in p.data["softened"] or p.data["softened"]["s03"]  # kept until the next pass clears it


def test_muse_refusal_rewritten_at_most_twice_then_skipped(env, monkeypatch):
    from filmmaker.pipeline import State
    sb_path, calls, _ = env
    out = _muse_refused(sb_path)
    prompts = iter(["Calm version one", "Re-staged version two", "never used"])
    seen = []
    monkeypatch.setattr(reviewer, "soften_scene", lambda sb, sid, err, earlier=None, **kw: (
        seen.append(earlier), reviewer.SceneFix(id=sid, prompt=next(prompts)))[1])
    p, lines = _pilot(sb_path, review=False)
    sent: list[set] = []

    def still_refused(**kw):  # Muse refuses every version
        sent.append(kw["only"])
        State(out / "state.json").update("s03", status="failed", error=MUSE_ERR, exit_code=42)

    monkeypatch.setattr(p, "_video_run", still_refused)
    p._soften_blocked()
    assert sent == [{"s03"}, {"s03"}]  # two rewritten resends, no third
    # the second rewrite sees both refused versions: the original and the first rewrite
    assert seen[0] is None and [e["prompt"] for e in seen[1]] == ["Rain on the street", "Calm version one"]
    assert yaml.safe_load(sb_path.read_text())["scenes"][2]["prompt"] == "Re-staged version two"
    assert any("lần 2/2" in l for l in lines)
    assert any("bỏ qua" in l and "s03" in l for l in lines)
    p._soften_blocked()  # a later pass gives up straight away
    assert len(sent) == 2


def test_quota_error_is_not_rewritten(env, monkeypatch):
    sb_path, calls, _ = env
    _muse_refused(sb_path, error="Muse không tạo video, trả lời: Daily limit reached, try again later")
    monkeypatch.setattr(reviewer, "soften_scene", lambda *a, **k: pytest.fail("must not rewrite"))
    p, lines = _pilot(sb_path, review=False)
    p._soften_blocked()


def test_soften_scene_prompt_mentions_muse(env, monkeypatch):
    sb = load_storyboard(env[0])
    got = {}
    monkeypatch.setattr(reviewer.ai, "ask", lambda system, user, schema, **kw: got.update(user=user, system=system)
                        or reviewer.SceneFix(id="s01"))
    reviewer.soften_scene(sb, sb.scenes[0].id, MUSE_ERR)
    assert "keeping the dialogue word for word" in got["user"] and "Muse" in got["system"]
    # a second refusal re-stages the shot but still keeps the approved line (it once became "Dad is proud of you")
    reviewer.soften_scene(sb, sb.scenes[0].id, MUSE_ERR, earlier=[{"prompt": "x"}])
    assert "keeping the dialogue word for word" in got["user"] and "rephrase the dialogue" not in got["user"]


def test_refused_scene_skips_unchanged_retries_and_goes_to_rewrite(env, monkeypatch):
    # thai-tham-ruong-cua-bo s07/s09: Muse refused the same prompt with a new keyframe too. A content refusal
    # is not sent again unchanged (no plain retry, no new keyframe): the next send has a rewritten prompt.
    sb_path, calls, _ = env
    _muse_refused(sb_path)
    p, lines = _pilot(sb_path, review=False)
    runs: list[dict] = []
    softened = []
    monkeypatch.setattr(p, "_video_run", lambda **kw: runs.append(kw))
    monkeypatch.setattr(p, "_soften_blocked", lambda: softened.append(True))
    try:
        p._stage_videos({})
    except Exception:  # what follows is not under test here
        pass
    others = {s.id for s in p.sb().scenes} - {"s03"}
    assert runs == [{"only": others, "confirm": set()}]  # s03 is never sent with its refused prompt
    assert softened == [True]  # rewritten in the background while the others render; leftovers checked after
    assert any("chạy nền" in l for l in lines)
    assert not any("thử lần cuối" in l or "Thử lại cảnh lỗi" in l for l in lines)


def test_refused_scene_is_rewritten_in_background_while_next_scenes_render(env, monkeypatch):
    # The run does not wait for the AI rewrite: s03 is sent while s02's new prompt is being written, then the
    # run goes back to s02 with the new prompt.
    import time as _time
    sb_path, calls, _ = env
    sb_path.write_text(STORY.replace("duration: 5", "duration: 6"))
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "Lan sips coffee:42:false:1")
    lines: list[str] = []

    def soften(sb, sid, err, earlier=None, **kw):
        for _ in range(200):  # the rewrite "takes long": finishes only once the next scene was sent
            if any(l.startswith("[s03] t2v") for l in lines):
                break
            _time.sleep(0.05)
        return reviewer.SceneFix(id=sid, prompt="Lan smiles at the window")

    monkeypatch.setattr(reviewer, "soften_scene", soften)
    p = Autopilot(sb_path, sb_path.parent / "output" / "storyboard", Gflow(log=lambda *_: None),
                  settings=AutoSettings(pause=(0, 0), review=False), log=lines.append, sleep=lambda *_: None)
    p._stage_videos({})

    def at(prefix):
        return next(i for i, l in enumerate(lines) if l.startswith(prefix))

    assert len([c for c in calls() if c[0] == "video"]) == 4  # s01, s02 (refused), s03, s02 (new prompt)
    assert at("[s02] LỖI") < at("[s03] t2v") < at("[s02] prompt mới đã sẵn sàng") < at("[s02] OK")
    assert any("chạy nền" in l for l in lines)
    assert yaml.safe_load(sb_path.read_text())["scenes"][1]["prompt"] == "Lan smiles at the window"
    assert p.data["resent"] == ["s02"] and p.stage("videos")["status"] == "done"
    assert p._softener is None  # worker pool closed


def test_softener_requeue_waits_only_while_rewrites_are_pending(env, monkeypatch):
    sb_path, _, _ = env
    p, _ = _pilot(sb_path, review=False)
    soft = autopilot._Softener(p)
    assert soft.requeue(True) == []  # nothing pending: never blocks
    monkeypatch.setattr(reviewer, "soften_scene", lambda sb, sid, err, **kw: reviewer.SceneFix(id=sid, prompt="calm"))
    assert soft.start(p.sb(), "s03", "Muse: couldn't be generated")
    assert not soft.start(p.sb(), "s03", "again")  # already rewriting
    got = soft.requeue(True)
    assert [s.id for s in got] == ["s03"] and got[0].prompt.startswith("calm")
    p.data["softened"]["s03"] = [{}] * autopilot.MAX_SOFTEN
    assert not soft.start(p.sb(), "s03", "x")  # out of rewrites
    soft.close()


def _basic_raw() -> dict:
    return {"characters": [{"name": "Prasert", "description": "stocky man"}],
            "scenes": [{"id": "s07", "mode": "keyframe", "prompt": "old", "narration": "old line",
                        "video_shot": {"camera": "Static medium two-shot",
                                       "action": "the man gives a cryptic slight smile",
                                       "speaker": {"position": "frame left", "visual_tag": "the stocky man",
                                                   "line": "old line"}}}]}


def test_soften_rewrites_the_video_shot_that_is_actually_sent():
    # thai-tham-ruong-cua-bo s07: basic films send `video_shot`, not `prompt`. Three rewrites changed only
    # `prompt` and dialogue, so Muse got the very same refused prompt each time.
    from filmmaker.i2v_prompt import VideoShot
    raw = _basic_raw()
    shot = VideoShot.model_validate({"camera": "Static medium shot", "action": "the man smiles warmly",
                                     "speaker": {"position": "frame left", "visual_tag": "the stocky man",
                                                 "line": "new line"}})
    fix = reviewer.SoftenFix(id="s07", prompt="the man smiles warmly", video_shot=shot)
    assert reviewer.apply_scene_fixes(raw, [fix]) == ["s07"]
    vs = raw["scenes"][0]["video_shot"]
    assert vs["action"] == "the man smiles warmly" and vs["speaker"]["line"] == "new line"


def test_new_dialogue_reaches_the_video_shot_speaker_without_a_new_shot():
    raw = _basic_raw()
    fix = reviewer.SoftenFix(id="s07", dialogue=[reviewer.DialogueFix(character="Prasert", line="new line")])
    reviewer.apply_scene_fixes(raw, [fix])
    assert raw["scenes"][0]["video_shot"]["speaker"]["line"] == "new line"


def test_soften_scene_asks_for_the_video_shot(tmp_path, monkeypatch):
    raw = _basic_raw()
    raw.update(title="t", format="short_film", audio="dialogue", dialogue_language="Thai")
    raw["scenes"][0]["image_prompt"] = "two men in a rice field"
    p = tmp_path / "storyboard.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    sb = load_storyboard(p)
    got = {}
    monkeypatch.setattr(reviewer.ai, "ask", lambda system, user, schema, **kw: got.update(user=user, schema=schema)
                        or schema(id="s07"))
    reviewer.soften_scene(sb, "s07", MUSE_ERR, earlier=[{"prompt": "x", "video_shot": {"action": "earlier act"}}])
    assert got["schema"] is reviewer.SoftenFix
    assert "cryptic slight smile" in got["user"] and "video_shot" in got["user"] and "earlier act" in got["user"]
