"""The per-film image / video prompt rules (the "Quy tắc prompt" popup): rules.IMAGE_RULES / VIDEO_RULES,
Storyboard.prompt_rules and where the prompt helpers read them."""

import pytest

from filmmaker import i2v_prompt, rules
from filmmaker.storyboard import (I2V_SILENT_RULE, NO_TEXT, SILENT_SCENE_RULE, Storyboard)


def _drama(**kw) -> Storyboard:
    return Storyboard.model_validate({
        "title": "T", "dialogue_language": "Thai",
        "characters": [{"name": "Lan", "description": "A woman in a red coat."},
                       {"name": "Minh", "description": "A man in a grey suit."}],
        "scenes": [
            {"id": "s01", "mode": "keyframe", "image_prompt": "Lan and Minh at a table", "prompt": "Lan looks up",
             "camera": {"shot": "medium shot", "movement": "slow dolly-in"},
             "dialogue": [{"character": "Lan", "line": "Anh về rồi à?"}]},
            {"id": "s02", "mode": "keyframe", "image_prompt": "Minh alone", "prompt": "Minh sits down"},
        ], **kw})


def _basic(**kw) -> Storyboard:
    return Storyboard.model_validate({
        "title": "T", "format": "clip", "audio": "dialogue", "dialogue_language": "Thai", "aspect": "9:16",
        "characters": [{"name": "Fon", "description": "A young woman, long black hair, white blouse."}],
        "scenes": [
            {"id": "s01", "mode": "keyframe", "image_prompt": "Fon at the table, vertical 9:16, 8 seconds",
             "prompt": "x", "narration": "Pai nai ma",
             "video_shot": {"camera": "static medium shot", "action": "", "audio": "",
                            "speaker": {"position": "frame left", "visual_tag": "the woman in the white blouse",
                                        "line": "Pai nai ma"},
                            "listeners": [{"position": "frame right", "visual_tag": "the man in the grey shirt",
                                           "silent_action": "grinning"}]}},
            {"id": "s02", "mode": "keyframe", "image_prompt": "Fon by the window", "prompt": "x",
             "video_shot": {"camera": "static wide shot", "action": "On the left, the woman looks out"}},
        ], **kw})


# --- stored form -----------------------------------------------------------------------------------------------

def test_clean_keeps_only_values_that_differ_from_the_default():
    assert rules.clean_prompt_rules(None) is None
    assert rules.clean_prompt_rules({"image": {"child_safe": True, "staging": "auto"}}) is None
    assert rules.clean_prompt_rules({"image": {"staging": "camera"}, "video": {"closing": False}}) == \
        {"image": {"staging": "camera"}, "video": {"closing": False}}


@pytest.mark.parametrize("bad", [
    {"audio": {}}, {"image": {"nope": True}}, {"image": {"child_safe": "yes"}}, {"video": {"facing": "left"}}, [1],
])
def test_clean_rejects_unknown_rules_and_values(bad):
    with pytest.raises(ValueError):
        rules.clean_prompt_rules(bad)


def test_every_choice_has_auto_default_and_ids_are_unique():
    for kind, rs in rules.RULE_SETS.items():
        ids = [r.id for r in rs]
        assert len(ids) == len(set(ids)), kind
        for r in rs:
            assert set(r.flows) <= set(rules.FLOWS)
            if isinstance(r, rules.Choice):
                assert r.default == "auto" and r.options[0][0] == "auto"


def test_saved_storyboard_keeps_the_rules(tmp_path):
    from filmmaker.storyboard import load_storyboard, save_storyboard
    sb = _drama(prompt_rules={"video": {"facing": "profile"}})
    save_storyboard(sb, tmp_path / "s.yaml")
    assert load_storyboard(tmp_path / "s.yaml").prompt_rules == {"video": {"facing": "profile"}}


# --- auto resolves to what each flow did before ------------------------------------------------------------------

def test_auto_follows_the_film_format():
    d, b = _drama(), _basic()
    assert (d.rule("image", "no_text"), b.rule("image", "no_text")) == ("long", "short")
    assert (d.rule("video", "facing"), b.rule("video", "facing")) == ("camera", "profile")
    assert (d.rule("video", "silence"), b.rule("video", "silence")) == ("laugh", "breath")


def test_old_no_text_checkbox_reads_as_off():
    sb = _drama(no_text=False)
    assert sb.rule("image", "no_text") == "off" and NO_TEXT not in sb.for_flow("A cat.")


# --- image rules -------------------------------------------------------------------------------------------------

def test_image_no_text_choices():
    sb = _drama(prompt_rules={"image": {"no_text": "short"}})
    out = sb.for_flow("A cat.")
    assert i2v_prompt.NO_TEXT_IMAGE in out and NO_TEXT not in out
    sb = _drama(prompt_rules={"image": {"no_text": "off"}})
    assert sb.for_flow("A cat.") == "A cat."


def test_drama_keyframe_staging_choices():
    s = _drama().scenes[0]
    assert "turned toward the camera" in _drama().for_flow_keyframe("x", s)
    prof = _drama(prompt_rules={"image": {"staging": "profile"}}).for_flow_keyframe("x", s)
    assert "three-quarter profile turned toward Minh" in prof and "toward the camera" not in prof
    assert "Staging" not in _drama(prompt_rules={"image": {"staging": "off"}}).for_flow_keyframe("x", s)


def test_basic_image_staging_and_toggles():
    sb = _basic()
    s = sb.scenes[0]
    assert "three-quarter profile" in sb.image_prompt(s) and "9:16" not in sb.image_prompt(s)
    cam = _basic(prompt_rules={"image": {"staging": "camera", "strip_technical": False, "looks": False}})
    p = cam.image_prompt(s)
    assert "turned toward the viewpoint" in p and "9:16" in p and "Fon:" not in p


def test_drama_image_camera_and_content_direction_toggles():
    sb = _drama(prompt_rules={"image": {"camera": False}})
    assert "medium shot" not in sb.image_prompt(sb.scenes[0])
    assert "medium shot" in _drama().image_prompt(_drama().scenes[0])


# --- video rules -------------------------------------------------------------------------------------------------

def test_drama_facing_profile_changes_the_speaker_cue():
    s = _drama().scenes[0]
    assert "turns the face toward the camera" in _drama().for_flow_video("x", s)
    out = _drama(prompt_rules={"video": {"facing": "profile"}}).for_flow_video("x", s)
    assert "three-quarter profile" in out and "toward the camera" not in out


def test_drama_silence_choices_for_a_scene_without_lines():
    s = _drama().scenes[1]
    assert SILENT_SCENE_RULE in _drama().for_flow_video("Minh sits down.", s)
    out = _drama(prompt_rules={"video": {"silence": "breath"}}).for_flow_video("Minh sits down.", s)
    assert I2V_SILENT_RULE in out and SILENT_SCENE_RULE not in out
    out = _drama(prompt_rules={"video": {"silence": "off"}}).for_flow_video("Minh sits down.", s)
    assert SILENT_SCENE_RULE not in out and I2V_SILENT_RULE not in out and "Spoken language" not in out


def test_drama_dialogue_toggles():
    sb = _drama(prompt_rules={"video": {"who_is_who": False, "background_no_words": False, "dialogue_turns": False,
                                        "language": False}})
    s = sb.scenes[0]
    out = sb.for_flow_video(sb.video_prompt(s), s)
    assert "Who is who" not in out and "Nobody else says" not in out and "exactly once" not in out
    assert "Spoken language" not in out and "Lan says: Anh về rồi à?" in out


def test_basic_dialogue_shot_toggles():
    s = _basic().scenes[0]
    base = _basic().video_prompt(s)
    assert "Locked-off" in base and i2v_prompt.CLOSING in base and "smiling" in base and "grinning" not in base
    sb = _basic(prompt_rules={"video": {"locked_camera": False, "closing": False, "closed_smile": False,
                                        "lip_lock": False, "listener_silent": False, "no_text": "off"}})
    out = sb.video_prompt(s)
    assert "Locked-off" not in out and i2v_prompt.CLOSING not in out and "grinning" in out
    assert "After finishing the line" not in out and "remaining completely silent" not in out
    assert i2v_prompt.NO_TEXT_SHORT not in out
    # still recognised as a structured prompt: no sound sentence tacked on the end
    assert sb.for_flow_video(out, s) == out


def test_basic_facing_camera():
    sb = _basic(prompt_rules={"video": {"facing": "camera"}})
    out = sb.video_prompt(sb.scenes[0])
    assert "faces the viewpoint with face and mouth clearly visible" in out and "three-quarter" not in out


def test_basic_silence_choices():
    s = _basic().scenes[1]
    assert I2V_SILENT_RULE in _basic().for_flow_video(_basic().video_prompt(s), s)
    sb = _basic(prompt_rules={"video": {"silence": "off"}})
    assert I2V_SILENT_RULE not in sb.for_flow_video(sb.video_prompt(s), s)
    sb = _basic(prompt_rules={"video": {"silence": "laugh"}})
    assert SILENT_SCENE_RULE in sb.for_flow_video(sb.video_prompt(s), s)


# --- nothing re-renders --------------------------------------------------------------------------------------------

def test_fingerprints_and_stored_keyframe_prompt_ignore_the_rules():
    for make, rs in ((_drama, {"image": {"staging": "off", "camera": False}, "video": {"camera": False,
                                                                                         "dialogue_turns": False}}),
                     (_basic, {"image": {"looks": False, "staging": "camera"}, "video": {"closing": False,
                                                                                          "facing": "camera"}})):
        a, b = make(), make(prompt_rules=rs)
        for s in a.scenes:
            assert a.scene_fingerprint(s) == b.scene_fingerprint(s)
            assert a.stored_image_prompt(s) == b.stored_image_prompt(s)
        assert any(a.image_prompt(s) != b.image_prompt(s) or a.video_prompt(s) != b.video_prompt(s) for s in a.scenes)


# --- web API -------------------------------------------------------------------------------------------------------

def test_schema_and_preview_api(client):
    sch = client.get("/api/prompt-rules").json()
    assert {r["id"] for r in sch["image"]} >= {"no_text", "staging", "child_safe"}
    assert next(r for r in sch["video"] if r["id"] == "facing")["type"] == "choice"
    pid = client.post("/api/projects", json={"title": "A", "source": "template"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/prompt-rules/preview", json={"prompt_rules": {"image": {"no_text": "off"}}})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["scene"] and j["video"]
    assert client.post(f"/api/projects/{pid}/prompt-rules/preview",
                       json={"prompt_rules": {"image": {"nope": 1}}}).status_code == 400
    assert client.post(f"/api/projects/{pid}/prompt-rules/preview", json={"scene_id": "zz"}).status_code == 404


from tests.test_web import client  # noqa: E402,F401
