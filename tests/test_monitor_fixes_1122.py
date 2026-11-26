"""Fixes from the monitored 22/11/2026 run of "ผ้าทอผืนสุดท้าย" (docs/monitor-findings-2026-11-22.md, items 1-5)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from filmmaker import autopilot as ap_mod
from filmmaker import flow_ui, reviewer
from filmmaker.autopilot import AutoError, AutoSettings, Autopilot, _refusal_reason, _Softener
from filmmaker.gflow import EXIT_UI_DRIFT, Gflow, GflowError
from filmmaker.i2v_prompt import Speaker, VideoShot
from filmmaker.pipeline import RunReport

# Flow's real answers in that run.
GLITCH_BEFORE_OFFER = "error\n\nSomething went wrong. Please try again.\n\nTry again"
GLITCH_AFTER_OFFER = ("I'll generate 1 video in 9:16 aspect ratio using Veo 3.1 - Lite as requested, with reference "
                      "image guidance and character entities Jaa and Kham.\n\nwarning\nFailed\nSomething went wrong. "
                      "Please try again.")
AUDIO_FAIL = ("I'll generate 1 video in 9:16 aspect ratio using Veo 3.1 - Lite.\n\nwarning\nFailed\nAudio generation "
              "failed. Please try a different prompt or send feedback.\nYour video generation has started!")


# --- item 2: Flow's own hiccup is not a refusal ---------------------------------------


def test_glitch_text_is_told_apart_from_content_and_audio_failures():
    assert flow_ui.is_glitch(GLITCH_BEFORE_OFFER) and flow_ui.is_glitch(GLITCH_AFTER_OFFER)
    assert not flow_ui.is_glitch(AUDIO_FAIL)
    assert not flow_ui.is_glitch("Something went wrong: this request might violate our policies")
    assert not flow_ui.is_glitch("Video generation failed")


def test_glitch_is_not_rewritten_but_audio_failure_is():
    assert _refusal_reason("Flow lỗi tạm thời sau khi nhận yêu cầu: …", flow_ui.EXIT_FLOW_GLITCH) is None
    # saved by older runs as "agent declined" (exit 41): still a glitch, so a rerun does not rewrite it
    assert _refusal_reason("Flow (giao diện chat): Flow không đề nghị tạo video. Trả lời của Flow: "
                           + GLITCH_BEFORE_OFFER, 41) is None
    assert _refusal_reason("Flow báo tạo video lỗi: " + AUDIO_FAIL, 42)
    assert _refusal_reason("Muse: That s07 video couldn't be generated.", 42)


def _sb_file(tmp_path: Path, scenes: list[dict]) -> Path:
    p = tmp_path / "storyboard.yaml"
    p.write_text(yaml.safe_dump({"title": "Pha", "model": "veo-lite", "scenes": scenes}, allow_unicode=True),
                 encoding="utf-8")
    return p


def _ap(tmp_path: Path, scenes: list[dict], lines: list[str], slept: list[float] | None = None) -> Autopilot:
    out = tmp_path / "output" / "storyboard"
    out.mkdir(parents=True, exist_ok=True)
    return Autopilot(_sb_file(tmp_path, scenes), out, Gflow(log=lambda *_: None), settings=AutoSettings(pause=(0, 0)),
                     log=lines.append, sleep=(slept.append if slept is not None else (lambda *_: None)))


def test_glitch_is_sent_again_unchanged_twice(tmp_path):
    lines: list[str] = []
    slept: list[float] = []
    ap = _ap(tmp_path, [{"id": "s01", "prompt": "rain", "duration": 8}], lines, slept)
    soft = _Softener(ap)
    try:
        scene = ap.sb().scenes[0]
        glitch = GflowError("Flow lỗi tạm thời", exit_code=flow_ui.EXIT_FLOW_GLITCH)
        soft.on_failed(scene, glitch)
        assert soft.requeue(False) == []  # not due yet: the run goes on with the next scenes first
        back = soft.requeue(True)  # nothing else to send: wait out the gap, then the same scene again
        assert [s.id for s in back] == ["s01"] and slept and slept[0] > 0
        assert soft.jobs == {}  # no AI rewrite was started
        soft.on_failed(scene, glitch)
        assert [s.id for s in soft.requeue(True)] == ["s01"]
        soft.on_failed(scene, glitch)  # third time: left for the end-of-pass retry
        assert soft.requeue(True) == []
    finally:
        soft.close()
    assert sum("gửi lại đúng prompt cũ" in x for x in lines) == 2
    assert any("vẫn lỗi tạm thời" in x for x in lines)


# --- item 1: gflow's broken `image t2i` no longer stops the film ------------------------


def test_text_to_image_moves_to_the_flow_chat_page_when_gflow_t2i_is_broken(tmp_path, monkeypatch):
    lines: list[str] = []
    g = Gflow(log=lines.append)
    calls: list[str] = []

    def broken(*args):
        calls.append("cli")
        raise GflowError("settings trigger did not become visible", exit_code=EXIT_UI_DRIFT)

    def chat(prompt, refs, **kw):
        calls.append("chat")
        assert refs == []
        return kw["out"]

    monkeypatch.setattr(g, "run_json", broken)
    monkeypatch.setattr(g, "ui_available", lambda: True)
    monkeypatch.setattr(g, "ui_image_to_image", chat)
    a = g.text_to_image("a veranda", out=tmp_path / "a.png", aspect="9:16", model="nano2", project="p1")
    b = g.text_to_image("hands on cloth", out=tmp_path / "b.png", aspect="9:16", model="nano2", project="p1")
    assert (a, b) == (tmp_path / "a.png", tmp_path / "b.png")
    assert calls == ["cli", "chat", "chat"]  # the broken path is tried once per run
    assert any("giao diện chat" in x for x in lines)


def test_text_to_image_drift_without_chat_page_is_still_reported(tmp_path, monkeypatch):
    g = Gflow(log=lambda *_: None)
    monkeypatch.setattr(g, "run_json", lambda *a: (_ for _ in ()).throw(GflowError("x", exit_code=EXIT_UI_DRIFT)))
    monkeypatch.setattr(g, "ui_available", lambda: False)
    with pytest.raises(GflowError) as exc:
        g.text_to_image("a veranda", out=tmp_path / "a.png", aspect="9:16", model="nano2", project="p1")
    assert exc.value.exit_code == EXIT_UI_DRIFT


def test_chat_image_prompt_without_references():
    text = flow_ui.build_image_prompt("a teak veranda", refs=0, aspect="9:16", model=None)
    assert text.startswith("Make exactly 1 image") and "character references" not in text
    assert "character references" in flow_ui.build_image_prompt("x", refs=2, aspect="9:16", model=None)


# --- item 3: a silent scene's sound cue first ------------------------------------------


SILENT = {"id": "s04", "mode": "keyframe", "prompt": "the old woman draws the shawl over the sleeping nurse",
          "image_prompt": "veranda", "duration": 8,
          "video_shot": VideoShot(camera="Slow dolly-in", action="the old woman draws the shawl over the nurse",
                                  audio="quiet veranda room tone, gentle slow breathing").model_dump(exclude_none=True)}


def test_silent_scene_audio_failure_only_quiets_the_sound_first(tmp_path, monkeypatch):
    lines: list[str] = []
    ap = _ap(tmp_path, [SILENT], lines)
    monkeypatch.setattr(reviewer.ai, "ask", lambda *a, **k: pytest.fail("no AI rewrite for the first try"))
    assert ap._soften_one(ap.sb(), "s04", "Flow báo tạo video lỗi: " + AUDIO_FAIL)
    s04 = ap.sb().scenes[0]
    assert s04.video_shot.audio == reviewer.QUIET_AUDIO
    assert s04.video_shot.action == SILENT["video_shot"]["action"] and s04.image_prompt == "veranda"
    assert "s04" not in (ap.data.get("softened") or {})  # not one of the MAX_SOFTEN rewrites
    assert ap.data["quieted"]["s04"] == [SILENT["video_shot"]["action"]]


def test_second_silent_audio_failure_asks_the_ai_about_the_sound_cue(tmp_path, monkeypatch):
    ap = _ap(tmp_path, [SILENT], [])
    ap.data["softened"] = {"s04": [{"prompt": "old"}]}
    ap.data["quieted"] = {"s04": [SILENT["video_shot"]["action"]]}  # the sound fix was tried for this action
    asked: list[str] = []

    def fake(system, user, schema, **kw):
        asked.append(user)
        return reviewer.SoftenFix(id="s04", video_shot=VideoShot(camera="Static", action="she smooths the shawl",
                                                                 audio="room tone"))

    monkeypatch.setattr(reviewer.ai, "ask", fake)
    assert ap._soften_one(ap.sb(), "s04", AUDIO_FAIL)
    assert "NO ONE SPEAKS" in asked[0]


def test_is_silent_and_quiet_fix():
    from filmmaker.storyboard import Storyboard

    sb = Storyboard.model_validate({"title": "t", "scenes": [SILENT, {
        "id": "s05", "prompt": "talk", "duration": 8,
        "video_shot": VideoShot(camera="c", action="a", speaker=Speaker(visual_tag="the nurse", line="สวัสดี")
                                ).model_dump(exclude_none=True)}]})
    assert reviewer.is_silent(sb.scenes[0]) and not reviewer.is_silent(sb.scenes[1])
    fix = reviewer.quiet_audio_fix(sb.scenes[0])
    assert fix.video_shot.audio == reviewer.QUIET_AUDIO and fix.video_shot.camera == "Slow dolly-in"
    quiet = sb.scenes[0].model_copy(update={"video_shot": fix.video_shot})
    assert reviewer.quiet_audio_fix(quiet) is None


# --- item 4: the approved story and words survive a rewrite ------------------------------


SPOKEN = {"id": "s07", "mode": "keyframe", "prompt": "the old woman speaks", "image_prompt": "dusk", "duration": 8,
          "video_shot": VideoShot(camera="Static", action="the old woman lifts her gaze and speaks",
                                  speaker=Speaker(visual_tag="the old woman", voice_style="frail, slow",
                                                  line="นกน้อย... ภู่เจ้ามาหาอุ๊ยแหมเน้อ")).model_dump(exclude_none=True)}
SCRIPT = """# ผ้าทอผืนสุดท้าย

### s06. Hiên
- **Thoại:** không có.

### s07. Hiên, hoàng hôn cùng ngày
- **Hành động chính:** Kham nói câu đầu tiên trong cả phim. Jaa sững lại, mắt ngấn nước.
- **Thoại:** Kham: "นกน้อย... ภู่เจ้ามาหาอุ๊ยแหมเน้อ"

### s08. Hiên, sáng sớm hôm sau
- **Thoại:** không có.
"""


def test_script_scene_is_read_from_the_approved_script(tmp_path):
    ap = _ap(tmp_path, [SPOKEN], [])
    (tmp_path / "script.final.md").write_text(SCRIPT, encoding="utf-8")
    sec = ap._script_scene("s07")
    assert sec.startswith("### s07.") and "câu đầu tiên" in sec and "s08" not in sec
    assert ap._script_scene("s99") is None


def test_rewrite_gets_the_script_and_a_reworded_line_is_flagged(tmp_path, monkeypatch):
    lines: list[str] = []
    ap = _ap(tmp_path, [SPOKEN], lines)
    (tmp_path / "script.final.md").write_text(SCRIPT, encoding="utf-8")
    ap.data["softened"] = {"s07": [{"prompt": "old"}]}  # second audio failure: the words may change now
    asked: list[str] = []

    def fake(system, user, schema, **kw):
        asked.append(user)
        return reviewer.SoftenFix(id="s07", video_shot=VideoShot(
            camera="Static", action="she smiles and speaks",
            speaker=Speaker(visual_tag="the old woman", voice_style="soft, calm", line="นกน้อย... ดีใจที่หนูมาหาอุ๊ยนะ")))

    monkeypatch.setattr(reviewer.ai, "ask", fake)
    assert ap._soften_one(ap.sb(), "s07", AUDIO_FAIL)
    assert "The approved script for this scene" in asked[0] and "câu đầu tiên" in asked[0]
    assert ap.data["dialogue_changed"]["s07"] == {"from": ["นกน้อย... ภู่เจ้ามาหาอุ๊ยแหมเน้อ"],
                                                  "to": ["นกน้อย... ดีใจที่หนูมาหาอุ๊ยนะ"]}
    assert any("ĐỔI THOẠI ĐÃ DUYỆT" in x for x in lines)


def test_first_audio_rewrite_keeps_the_words(tmp_path, monkeypatch):
    ap = _ap(tmp_path, [SPOKEN], [])
    monkeypatch.setattr(reviewer.ai, "ask", lambda *a, **k: reviewer.SoftenFix(id="s07", video_shot=VideoShot(
        camera="Static", action="she speaks",
        speaker=Speaker(visual_tag="the old woman", voice_style="soft, calm", line="อย่างอื่น"))))
    assert ap._soften_one(ap.sb(), "s07", AUDIO_FAIL)
    sp = ap.sb().scenes[0].video_shot.speaker
    assert sp.line == "นกน้อย... ภู่เจ้ามาหาอุ๊ยแหมเน้อ" and sp.voice_style == "soft, calm"
    assert "dialogue_changed" not in ap.data


def test_rewrite_that_loses_the_story_point_is_not_used(tmp_path, monkeypatch):
    lines: list[str] = []
    ap = _ap(tmp_path, [SPOKEN], lines)
    monkeypatch.setattr(reviewer.ai, "ask", lambda *a, **k: reviewer.SoftenFix(
        id="s07", prompt="she sleeps", story_point_kept=False))
    assert not ap._soften_one(ap.sb(), "s07", "Flow xoá video: deleted")
    assert ap.sb().scenes[0].prompt == "the old woman speaks"
    assert any("giữ ý chính" in x for x in lines)


# --- item 5: a stopped render still gives a film ----------------------------------------


def _fake_pipe(n: int, done: int):
    scenes = [SimpleNamespace(id=f"s{i:02d}") for i in range(1, n + 1)]
    made = {s.id for s in scenes[:done]}
    return SimpleNamespace(sb=SimpleNamespace(scenes=scenes), is_done=lambda s: s.id in made,
                           run=lambda opts: RunReport(aborted="dừng toàn bộ vì lỗi nghiêm trọng ở s10 (exit 23)"))


@pytest.mark.parametrize("done, assembles", [(8, True), (7, True), (6, False)])
def test_render_stopped_early_assembles_with_enough_clips(tmp_path, monkeypatch, done, assembles):
    lines: list[str] = []
    ap = _ap(tmp_path, [{"id": "s01", "prompt": "rain", "duration": 8}], lines)
    fake = _fake_pipe(10, done)
    monkeypatch.setattr(ap, "pipe", lambda *a, **k: fake)
    monkeypatch.setattr(ap, "opts", lambda **kw: kw)
    if assembles:
        ap._video_run()
        assert ap.render_aborted and any(f"đã có {done}/10 clip" in x for x in lines)
        assert ap._video_run().done == []  # later passes do not hit the same error again
    else:
        with pytest.raises(AutoError):
            ap._video_run()
    assert ap_mod.MIN_ASSEMBLE_SHARE == 0.7


def test_chat_image_command_accepts_no_reference(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(flow_ui, "run_i2i", lambda args: seen.append(args.ref) or {"status": "ok"})
    assert flow_ui.main(["i2i", "--project", "p1", "--prompt", "a veranda", "--aspect", "9:16", "-o", "x.png",
                         "--profile-dir", "none"]) == 0
    assert seen == [None]


# --- 22/11 follow-up: s04 was out of rewrites before its sound was ever quieted ------------------------


def _refused_state(ap: Autopilot, error: str, *, edited: bool = False, code: int = 42) -> None:
    import json

    pipe = ap.pipe()
    s = pipe.sb.scenes[0]
    fp = "edited-since" if edited else pipe.sb.scene_fingerprint(s)
    (ap.out / "state.json").write_text(json.dumps({"project_id": "p", "scenes": {s.id: {
        "status": "submitting", "error": error, "exit_code": code, "fingerprint": fp}}}), encoding="utf-8")


def test_scene_out_of_rewrites_still_gets_the_sound_fix(tmp_path, monkeypatch):
    lines: list[str] = []
    ap = _ap(tmp_path, [SILENT], lines)
    ap.data["softened"] = {"s04": [{"prompt": "a"}, {"prompt": "b"}]}  # both rewrites used on the action
    scene = ap.sb().scenes[0]
    assert not ap._out_of_rewrites(scene, AUDIO_FAIL)
    monkeypatch.setattr(reviewer.ai, "ask", lambda *a, **k: pytest.fail("the sound fix needs no AI"))
    soft = _Softener(ap)
    try:
        assert soft.start(ap.sb(), "s04", AUDIO_FAIL)
        assert [s.id for s in soft.requeue(True)] == ["s04"]
    finally:
        soft.close()
    assert ap.sb().scenes[0].video_shot.audio == reviewer.QUIET_AUDIO
    assert len(ap.data["softened"]["s04"]) == 2
    assert ap._out_of_rewrites(ap.sb().scenes[0], AUDIO_FAIL)  # now there is nothing free left


def test_render_video_changes_a_refused_scene_first(tmp_path, monkeypatch):
    lines: list[str] = []
    ap = _ap(tmp_path, [SILENT], lines)
    ap.data["softened"] = {"s04": [{"prompt": "a"}, {"prompt": "b"}]}
    _refused_state(ap, "Flow báo tạo video lỗi: " + AUDIO_FAIL)
    monkeypatch.setattr(reviewer.ai, "ask", lambda *a, **k: pytest.fail("the sound fix needs no AI"))
    assert ap.rewrite_refused({"s04"}) == ["s04"]
    assert ap.sb().scenes[0].video_shot.audio == reviewer.QUIET_AUDIO
    assert any("đổi prompt trước khi render lại" in x for x in lines)
    # the quieted version is refused too: now really out of tries, the current version is sent as it is
    _refused_state(ap, "Flow báo tạo video lỗi: " + AUDIO_FAIL)
    assert ap.rewrite_refused({"s04"}) == []
    assert any("đã viết lại 2 lần" in x for x in lines)


def test_render_video_rewrites_with_ai_while_rewrites_are_left(tmp_path, monkeypatch):
    ap = _ap(tmp_path, [SPOKEN], [])
    _refused_state(ap, "Flow xoá video: deleted")
    monkeypatch.setattr(reviewer.ai, "ask", lambda *a, **k: reviewer.SoftenFix(id="s07", prompt="she nods"))
    assert ap.rewrite_refused(None) == ["s07"]
    assert ap.sb().scenes[0].prompt == "she nods" and len(ap.data["softened"]["s07"]) == 1


def test_render_video_leaves_a_scene_edited_by_hand_alone(tmp_path, monkeypatch):
    lines: list[str] = []
    ap = _ap(tmp_path, [SILENT], lines)
    _refused_state(ap, "Flow báo tạo video lỗi: " + AUDIO_FAIL, edited=True)
    monkeypatch.setattr(reviewer.ai, "ask", lambda *a, **k: pytest.fail("no rewrite of a hand edit"))
    assert ap.rewrite_refused(None) == []
    assert ap.sb().scenes[0].video_shot.audio == SILENT["video_shot"]["audio"]
    assert any("đã được sửa" in x for x in lines)


def test_render_video_ignores_scenes_that_were_not_refused(tmp_path, monkeypatch):
    ap = _ap(tmp_path, [SILENT], [])
    _refused_state(ap, "Hết thời gian chờ", code=9)
    monkeypatch.setattr(reviewer.ai, "ask", lambda *a, **k: pytest.fail("a timeout is not a refusal"))
    assert ap.rewrite_refused(None) == []


# --- s05: the second half of one line came out of the listener's mouth ------------------------------


S05_SHOT = VideoShot(
    camera="Static medium two-shot", action="",
    speaker=Speaker(position="frame right", visual_tag="the standing young nurse in white with a steel cup",
                    voice_style="calm, soft, clear", line="ทอเสร็จแล้วเหรอคะแม่อุ๊ย ความลับเหรอคะ", gaze="frame left"),
    listeners=[{"position": "frame left", "visual_tag": "the grey-haired woman on the loom bench",
                "silent_action": "resting a hand on the folded cloth"}],
    audio="quiet veranda room tone")


def test_line_parts():
    from filmmaker.i2v_prompt import line_parts

    assert line_parts("ทอเสร็จแล้วเหรอคะแม่อุ๊ย ความลับเหรอคะ") == 2  # Thai: a space ends a phrase
    assert line_parts("I am fine. Thanks!") == 2
    assert line_parts("Con về rồi đây mẹ") == 1  # spaces between words, one sentence
    assert line_parts("") == 1 and line_parts("Thanks!") == 1


def test_two_part_line_tells_veo_one_person_says_all_of_it():
    from filmmaker import i2v_prompt as ip

    p = ip.assemble(S05_SHOT, audio_mode="dialogue", language="Thai")
    rule = ip.ONE_SPEAKER_RULE.format(who="The standing young nurse in white with a steel cup")
    assert rule in p and p.index(rule) < p.index("Audio:")
    one = S05_SHOT.model_copy(update={"speaker": S05_SHOT.speaker.model_copy(update={"line": "ทอเสร็จแล้วเหรอคะ"})})
    assert "says every part of the line" not in ip.assemble(one, audio_mode="dialogue", language="Thai")
    alone = S05_SHOT.model_copy(update={"listeners": None})
    assert "says every part of the line" not in ip.assemble(alone, audio_mode="dialogue", language="Thai")
    assert any("2 câu/vế" in w for w in ip.warnings(S05_SHOT, audio_mode="dialogue", narration=None, names=[]))


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="needs ffmpeg")
def test_qc_speaking_frames_cover_the_whole_line(tmp_path):
    from filmmaker import qc
    from tests.test_auto_qc_gates import _make_clip

    clip = _make_clip(tmp_path / "s05.mp4", duration=8.0)
    segs = [{"start": 0.0, "end": 2.9, "text": "ทอเสร็จแล้วเหรอคะแม่อุ๊ย"},
            {"start": 4.0, "end": 7.9, "text": "ความลับเหรอคะ"}]
    _, first = qc.speaking_frames(clip, segs, tmp_path / "a.jpg")
    _, whole = qc.speaking_frames(clip, segs, tmp_path / "b.jpg", whole=True)
    assert max(first) < 3.0  # the old strip: the first half only
    assert max(whole) > 6.0 and min(whole) < 1.0
