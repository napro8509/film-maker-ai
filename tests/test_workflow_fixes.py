"""Fixes after making a Thai drama on Muse: model/script options for plan, adult portraits, keyframe chain limit,
Muse retries and hints, Muse content warnings, dialogue-field notes. Nothing here calls Flow, Muse or an AI."""
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from filmmaker import cli
from filmmaker.gflow import GflowError, Gflow, MUSE_FAILED, MUSE_DECLINED, muse_error, muse_fail_dir
from filmmaker.pipeline import MAX_KEYFRAME_CHAIN, Pipeline
from filmmaker.storyboard import Storyboard, load_storyboard


# --- 1. plan --video-model / --image-model / --script ------------------------------------------------------

def test_plan_rejects_unknown_video_model(tmp_path):
    r = CliRunner().invoke(cli.main, ["plan", "an idea", "-o", str(tmp_path / "sb.yaml"), "--video-model", "bogus"])
    assert r.exit_code == 2 and "bogus" in r.output


def test_plan_needs_idea_or_script(tmp_path):
    r = CliRunner().invoke(cli.main, ["plan", "-o", str(tmp_path / "sb.yaml")])
    assert r.exit_code != 0 and "--script" in r.output


def test_plan_from_script_sets_models(tmp_path, monkeypatch):
    script = tmp_path / "script.md"
    script.write_text("# Film\n\n### s01\nDao: แม่...?\n", encoding="utf-8")
    seen = {}

    def fake_plan(idea, **kw):
        seen["idea"] = idea
        return Storyboard.model_validate({"title": "t", "scenes": [{"id": "s01", "prompt": "x"}]})

    monkeypatch.setattr(cli, "_plan_new", fake_plan)
    out = tmp_path / "sb.yaml"
    r = CliRunner().invoke(cli.main, ["plan", "-o", str(out), "--script", str(script),
                                      "--video-model", "Muse", "--image-model", "nano2"])
    assert r.exit_code == 0, r.output
    assert seen["idea"].startswith(cli.SCRIPT_IDEA_HEAD) and "Dao: แม่...?" in seen["idea"]
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert data["model"] == "muse" and data["image_model"] == "nano2"


def test_idea_from_script_keeps_extra_note(tmp_path):
    s = tmp_path / "s.md"
    s.write_text("body", encoding="utf-8")
    assert cli._idea_from("idea", None) == "idea"
    text = cli._idea_from("aspect 9:16", s)
    assert "Ghi chú thêm: aspect 9:16" in text and text.endswith("body")


# --- 2. adult reference pictures are not the kitchen scene ---------------------------------------------------

def test_adult_portrait_is_a_reference_picture_child_keeps_kitchen():
    sb = Storyboard.model_validate({
        "title": "t", "format": "clip", "audio": "dialogue",
        "characters": [{"name": "Dao", "description": "A 25-year-old Thai mango seller, indigo shirt"},
                       {"name": "Lan", "description": "Vietnamese girl, age 8, yellow t-shirt"}],
        "scenes": [{"id": "s01", "mode": "keyframe", "image_prompt": "x", "prompt": "x"}]})
    adult, child = (sb.portrait_prompt(c) for c in sb.characters)
    assert "kitchen" not in adult and adult.startswith("Character reference image of Dao")
    assert "kitchen table" in child


# --- 3. keyframes are edited from the previous one at most MAX_KEYFRAME_CHAIN times in a row ----------------

def _chain_pipe(tmp_path, n: int) -> Pipeline:
    data = {"title": "Chain", "aspect": "9:16",
            "characters": [{"name": "Minh", "description": "a man"}],
            "locations": [{"name": "Room", "description": "a room"}],
            "scenes": [{"id": f"s{i:02d}", "mode": "keyframe", "image_prompt": "Minh sits", "prompt": "x",
                        "location": "Room"} for i in range(1, n + 1)]}
    p = tmp_path / "sb.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return Pipeline(load_storyboard(p), tmp_path / "out", Gflow(log=lambda *_: None, ui_enabled=False, dry_run=True),
                    log=lambda *_: None, sleep=lambda *_: None)


def test_keyframe_chain_restarts_from_references(tmp_path):
    pipe = _chain_pipe(tmp_path, 7)
    planned = {s.id for s in pipe.sb.scenes}
    chained = {}
    for s in pipe.sb.scenes:
        src, why = pipe.continuity_check(s, planned)
        chained[s.id] = src.id if src else why
    assert MAX_KEYFRAME_CHAIN == 2
    assert chained["s02"] == "s01" and chained["s03"] == "s02"
    assert "đã nối 2 cảnh liền" in chained["s04"]
    assert chained["s05"] == "s04" and chained["s06"] == "s05"
    assert "đã nối" in chained["s07"]
    assert [pipe.chain_depth(s, planned) for s in pipe.sb.scenes] == [0, 1, 2, 0, 1, 2, 0]


# --- 4/5. Muse failures: retried when nothing was made, a hint and the screenshot folder -------------------

def test_muse_failed_is_retryable_declined_is_not(tmp_path):
    shots = muse_fail_dir(tmp_path / "clips" / "s04.mp4")
    assert shots == tmp_path / "muse_failed" / "s04"
    failed = muse_error(GflowError("Muse (giao diện chat): Failed to create", exit_code=MUSE_FAILED,
                                   payload={"error": {"spent": False}}), shots)
    assert failed.retryable and "model: veo-lite" in str(failed) and str(shots) in str(failed)
    declined = muse_error(GflowError("Muse không tạo video", exit_code=MUSE_DECLINED,
                                     payload={"error": {"spent": False}}), shots)
    assert not declined.retryable and "bình tĩnh" in str(declined)
    other = GflowError("login", exit_code=37)
    assert muse_error(other, shots) is other


def test_muse_warnings_only_for_muse_scenes_with_tears():
    sb = Storyboard.model_validate({
        "title": "t", "model": "muse",
        "scenes": [{"id": "s01", "mode": "keyframe", "prompt": "x", "image_prompt": "a single tear on her cheek"},
                   {"id": "s02", "mode": "keyframe", "prompt": "x", "image_prompt": "calm face, clear dry eyes"},
                   {"id": "s03", "mode": "keyframe", "prompt": "x", "image_prompt": "she is crying",
                    "model": "veo-lite"}]})
    assert list(sb.muse_warnings()) == ["s01"]


# --- 7. what a dialogue scene really sends -------------------------------------------------------------------

def _dialogue_sb(camera: str) -> Storyboard:
    return Storyboard.model_validate({
        "title": "t", "format": "clip", "audio": "dialogue", "dialogue_language": "Thai",
        "characters": [{"name": "Dao", "description": "a woman"}, {"name": "Prasert", "description": "a man"}],
        "scenes": [{"id": "s01", "mode": "keyframe", "prompt": "she hands over the wallet", "image_prompt": "x",
                    "narration": "คุณลุงคะ",
                    "video_shot": {"camera": camera, "action": "she hands over the wallet",
                                   "speaker": {"character": "Dao", "position": "frame left",
                                               "visual_tag": "the woman in the indigo shirt",
                                               "line": "คุณลุงคะ", "action": "lifts the wallet"},
                                   "listeners": [{"position": "frame right", "visual_tag": "the man in white",
                                                  "silent_action": "nods"}]}}]})


def test_dialogue_field_note_and_camera_size_warning():
    sb = _dialogue_sb("Locked-off medium two-shot, waist-up, faces large")
    assert "speaker.action" in sb.dialogue_fields_note()
    msgs = sb.dialogue_warnings().get("s01", [])
    assert any("waist-up" in m and "medium two-shot" in m for m in msgs)
    plain = _dialogue_sb("Locked-off medium two-shot")
    assert not any("cỡ cảnh" in m for m in plain.dialogue_warnings().get("s01", []))
