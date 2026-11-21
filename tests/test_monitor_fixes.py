"""Fixes from the monitored 21/11/2026 run of "เดือนของเต้" (docs/monitor-findings-2026-11-21.md, items 2-6)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from filmmaker import basic_planner, planner, qc, reviewer, rules
from filmmaker.autopilot import AutoSettings, Autopilot
from filmmaker.gflow import Gflow
from filmmaker.i2v_prompt import Speaker, VideoShot, base_look
from filmmaker.storyboard import Storyboard

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg/ffprobe")

# The four real Muse refusals of that run.
MUSE_S07 = ("Muse: That s07 video couldn't be generated. I can try a different version instead — Sak standing beside "
            "Tae with his hand at his side, speaking the apology without the hand on Tae's shoulder — if you'd like "
            "that one.")
MUSE_S08 = ("Muse: That s08 video couldn't be generated. I can try a different version of the handover instead — Sak "
            "standing a step back from Mee, holding the rope out at arm's length while he speaks the line — if you "
            "want me to make that one.")
MUSE_S09 = ("Muse: That s09 video couldn't be generated. I can make a different version of the moment instead — Mee "
            "standing beside Duean speaking to Tae from a little distance, rather than handing the rope over — if "
            "you'd like me to try that.")


# --- item 3: no adult touches a child ---------------------------------------------


def test_child_contact_rule_reaches_every_prompt_that_writes_scenes():
    for text in (planner.SYSTEM, basic_planner.SYSTEM, reviewer.STORYBOARD_SYSTEM, reviewer.SOFTEN_SYSTEM):
        assert rules.CHILD_CONTACT_RULE in text
    assert "CHILD_CONTACT_RULE" in {r.name for r in rules.RULES}


# --- item 4: rewrites change the action, keep the words ---------------------------


def test_muse_suggestion_is_read_from_real_refusals():
    assert reviewer.muse_suggestion(MUSE_S09) == ("Mee standing beside Duean speaking to Tae from a little distance, "
                                                  "rather than handing the rope over")
    assert reviewer.muse_suggestion(MUSE_S08).startswith("Sak standing a step back from Mee")
    assert "without the hand on Tae's shoulder" in reviewer.muse_suggestion(MUSE_S07)
    assert reviewer.muse_suggestion("Muse báo không tạo được video này (Failed to create…).") is None


def _shot_sb() -> Storyboard:
    return Storyboard.model_validate({
        "title": "Duean", "format": "clip", "audio": "dialogue", "dialogue_language": "Thai",
        "characters": [{"name": "Mee", "description": "An old man"}, {"name": "Tae", "description": "A boy"}],
        "scenes": [{
            "id": "s09", "mode": "keyframe", "prompt": "old man lays the rope into the boy's hands",
            "image_prompt": "Night, lamp light", "duration": "10",
            "narration": "ลุงซื้อแล้ว แต่ลุงเลี้ยงบ่ไหวดอก",
            "dialogue": [{"character": "Mee", "line": "ลุงซื้อแล้ว แต่ลุงเลี้ยงบ่ไหวดอก"}],
            "video_shot": VideoShot(camera="Static medium shot", action="old man lays the rope into the boy's hands",
                                    speaker=Speaker(visual_tag="the old man", line="ลุงซื้อแล้ว แต่ลุงเลี้ยงบ่ไหวดอก")
                                    ).model_dump(exclude_none=True),
        }],
    })


def _changed_fix() -> reviewer.SoftenFix:
    return reviewer.SoftenFix(
        id="s09", prompt="old man stands beside the buffalo", narration="เดือนขออยู่กับหลานเนาะ",
        dialogue=[reviewer.DialogueFix(character="Mee", line="เดือนขออยู่กับหลานเนาะ")],
        video_shot=VideoShot(camera="Static wide shot", action="old man stands beside the buffalo",
                             speaker=Speaker(visual_tag="the old man", line="เดือนขออยู่กับหลานเนาะ")))


def test_soften_after_muse_refusal_keeps_dialogue_and_passes_muse_idea(monkeypatch):
    sb = _shot_sb()
    asked: list[str] = []

    def fake_ask(system, user, schema, **kw):
        asked.append(user)
        return _changed_fix()

    monkeypatch.setattr(reviewer.ai, "ask", fake_ask)
    fix = reviewer.soften_scene(sb, "s09", MUSE_S09)
    assert fix.dialogue is None and fix.narration is None  # the approved words are not touched
    assert fix.video_shot.speaker.line == "ลุงซื้อแล้ว แต่ลุงเลี้ยงบ่ไหวดอก"
    assert fix.video_shot.action == "old man stands beside the buffalo"  # the staging does change
    assert "Muse offered this version itself" in asked[0] and "rather than handing the rope over" in asked[0]
    assert "changing the action or staging" in asked[0]

    raw = yaml.safe_load(yaml.safe_dump(sb.model_dump(mode="json", exclude_none=True), allow_unicode=True))
    assert reviewer.apply_scene_fixes(raw, [fix]) == ["s09"]
    s09 = raw["scenes"][0]
    assert s09["narration"] == "ลุงซื้อแล้ว แต่ลุงเลี้ยงบ่ไหวดอก"
    assert s09["dialogue"][0]["line"] == "ลุงซื้อแล้ว แต่ลุงเลี้ยงบ่ไหวดอก"
    assert s09["video_shot"]["speaker"]["line"] == "ลุงซื้อแล้ว แต่ลุงเลี้ยงบ่ไหวดอก"


def test_soften_after_audio_failure_keeps_words_first_then_may_change_them(monkeypatch):
    sb = _shot_sb()
    asked: list[str] = []
    monkeypatch.setattr(reviewer.ai, "ask", lambda s, user, *a, **k: asked.append(user) or _changed_fix())
    first = reviewer.soften_scene(sb, "s09", "Flow: Audio generation failed")
    assert first.dialogue is None and first.video_shot.speaker.line == "ลุงซื้อแล้ว แต่ลุงเลี้ยงบ่ไหวดอก"
    assert "keep every spoken word" in asked[0]
    second = reviewer.soften_scene(sb, "s09", "Flow: Audio generation failed", earlier=[{"prompt": "x"}])
    assert second.dialogue is not None and second.video_shot.speaker.line == "เดือนขออยู่กับหลานเนาะ"
    assert "dialect" in asked[1]


# --- item 5: an agent-approved keyframe is not remade at render time ---------------


def test_agent_review_overrides_pre_qc_fail(tmp_path):
    kf = tmp_path / "keyframes" / "s01.png"
    kf.parent.mkdir(parents=True)
    kf.write_bytes(b"png")
    qc.save_keyframe_qc(tmp_path, "s01", {"verdict": "FAIL", "score": 3, "sig": qc.clip_sig(kf),
                                          "v": qc.QC_VERSION, "issues": ["mặt mờ"], "candidates": []})
    qc.accept_keyframe(tmp_path, "s01", kf, "PASS", 8, "agent review")
    rec = qc.current_keyframe_qc(tmp_path, "s01", kf)
    assert rec["verdict"] == "PASS" and rec["score"] == 8 and rec["accepted_by"] == "agent review"


def test_accept_keyframe_leaves_a_pre_qc_pass_alone(tmp_path):
    kf = tmp_path / "keyframes" / "s02.png"
    kf.parent.mkdir(parents=True)
    kf.write_bytes(b"png")
    qc.save_keyframe_qc(tmp_path, "s02", {"verdict": "PASS", "score": 9, "sig": qc.clip_sig(kf), "v": qc.QC_VERSION})
    qc.accept_keyframe(tmp_path, "s02", kf, "MINOR", 5, "agent review: hết vòng sửa, dùng tạm")
    assert qc.current_keyframe_qc(tmp_path, "s02", kf)["verdict"] == "PASS"


# --- item 6: the portrait shows the first look only --------------------------------


def test_base_look_drops_later_scene_costume():
    tae = ("A young Isan boy, thin, sun-browned skin, short cropped hair. Faded blue cotton t-shirt, brown shorts, "
           "barefoot. From the dusk scene onward a red-and-white checked cloth rests on his shoulder; in the final "
           "scene that cloth is tied around his waist. Bright open expression.")
    assert base_look(tae) == ("A young Isan boy, thin, sun-browned skin, short cropped hair. Faded blue cotton "
                              "t-shirt, brown shorts, barefoot. Bright open expression.")
    mee = ("An elderly village man, short white hair. Deep indigo shirt, a green-checked cloth over his shoulder, "
           "always leaning on a bamboo cane. Kind lined face.")
    assert base_look(mee) == mee
    assert base_look("Later wears a grey shirt.") == "Later wears a grey shirt."  # never an empty description


def test_portrait_prompt_uses_base_look():
    sb = Storyboard.model_validate({
        "title": "T", "characters": [{"name": "Phat", "description": "A man in a blue jacket. Later wears a plain gray t-shirt."}],
        "scenes": [{"id": "s01", "prompt": "Phat walks", "duration": 8}],
    })
    prompt = sb.portrait_prompt(sb.characters[0])
    assert "blue jacket" in prompt and "gray t-shirt" not in prompt


def test_outfit_rule_reaches_planners():
    assert rules.OUTFIT_RULE in planner.SYSTEM and rules.OUTFIT_RULE in basic_planner.SYSTEM


# --- item 2: the film review knows which scenes are not in the film ----------------


@needs_ffmpeg
def test_film_review_is_told_what_is_missing_and_revises(tmp_path, monkeypatch):
    sb_path = tmp_path / "storyboard.yaml"
    sb_path.write_text(yaml.safe_dump({
        "title": "Duean", "scenes": [{"id": f"s0{i}", "prompt": "rice field", "duration": 8} for i in range(1, 5)],
    }), encoding="utf-8")
    out = tmp_path / "output" / "storyboard"
    out.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=2",
                    "-pix_fmt", "yuv420p", str(out / "final.mp4")], check=True)
    seen: list[str] = []

    def fake_review(sb, sheet, thumb, extra="", **kw):
        seen.append(extra)
        return reviewer.FilmReview(verdict="PASS", score=8, summary="Câu chuyện dễ theo dõi.")

    monkeypatch.setattr(reviewer, "review_film", fake_review)
    lines: list[str] = []
    p = Autopilot(sb_path, out, Gflow(log=lambda *_: None), settings=AutoSettings(pause=(0, 0)), log=lines.append,
                  sleep=lambda *_: None)
    qc._save(out, "s02", {"verdict": "FAIL", "issues": ["Sai người nói."]})
    p.qc_excluded = ["s02"]
    result = {"missing": ["s04"]}
    p._stage_review_film(result)
    assert "ONLY these scenes: s01, s03 (2/4)" in seen[0]
    assert "s02: left out by the QC gate (Sai người nói.)" in seen[0]
    assert "s04: no clip" in seen[0]
    assert result["film_verdict"] == "REVISE" and result["film_score"] == 4
