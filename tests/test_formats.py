"""Film formats (Phim mới): short drama keeps the full flow; other formats use the basic text -> image -> video."""

import json

import pytest
from pydantic import ValidationError

from filmmaker import basic_planner, formats, planner
from filmmaker.storyboard import NO_DIALOGUE, NO_SUBTITLES, NO_TEXT, Storyboard

SCENE = {"id": "s01", "mode": "keyframe", "image_prompt": "Lan sits by the window", "prompt": "Lan stands up",
         "content_direction": "she is anxious", "camera": {"shot": "close-up", "movement": "slow dolly-in"},
         "dialogue": [{"character": "Lan", "line": "Anh về rồi."}], "location": "Kitchen"}


def _sb(**kw):
    return Storyboard.model_validate({
        "title": "t", "style": "warm light", "characters": [{"name": "Lan", "description": "a young woman"}],
        "locations": [{"name": "Kitchen", "description": "small tiled kitchen"}], "dialogue_language": "Vietnamese",
        "scenes": [dict(SCENE)], **kw})


def test_basic_format_leaves_out_the_drama_direction():
    sb = _sb(format="clip", audio="voiceover")
    s = sb.scenes[0]
    img = sb.for_flow_keyframe(sb.image_prompt(s), s)
    vid = sb.for_flow_video(sb.video_prompt(s), s)
    for text in (img, vid):
        for drama in ("close-up", "dolly", "anxious", "Anh về", "Staging", "Who is who"):
            assert drama not in text
    assert "tiled kitchen" not in vid  # the place is in the picture, the video prompt only moves
    assert img.startswith("Lan sits by the window. Setting: small tiled kitchen. Lan: a young woman. Style: warm light.")
    assert vid.startswith("Lan stands up. Voice-over: one calm narrator's voice speaks in Vietnamese")
    assert vid.endswith("No on-screen text, letters or watermarks.") and NO_TEXT not in vid


def test_drama_is_unchanged_without_a_format_or_with_short_drama():
    old, new = _sb(), _sb(format=formats.DRAMA)
    s_old, s_new = old.scenes[0], new.scenes[0]
    assert not old.basic and not new.basic
    assert old.for_flow_video(old.video_prompt(s_old), s_old) == new.for_flow_video(new.video_prompt(s_new), s_new)
    vid = old.for_flow_video(old.video_prompt(s_old), s_old)
    assert "dolly" in vid and "Anh về rồi" in vid and "anxious" in vid


def test_silent_basic_film_gets_the_short_no_speech_rule():
    sb = _sb(format="short_film", audio="silent", dialogue_language="none")
    s = sb.scenes[0]
    assert sb.for_flow_video(sb.video_prompt(s), s).startswith("Lan stands up. No dialogue, no music.")  # hand-written scene


def test_unknown_choices_are_rejected_and_genres_cleaned():
    with pytest.raises(ValidationError):
        _sb(format="opera")
    with pytest.raises(ValidationError):
        _sb(format="clip", audio="karaoke")
    assert _sb(format="clip", genres=["horror", "bogus", "horror", "comedy"]).genres == ["horror", "comedy"]


SHOT = {"camera": "Static locked-off medium shot, shallow depth of field",
        "action": "In the center, the ginger kitten looks left and right", "environment": "a cart rolls past far behind",
        "audio": "quiet supermarket hum, soft bell jingle"}


def _fake_claude(monkeypatch, calls, scenes=2, scene=None, fix=None):
    def draft(user, timeout=900, *, system=None, schema_model=None):
        calls.append((user, system, schema_model))
        if "Some scenes break the rules" in user:
            return fix
        return {"title": "Mèo lạc", "style": "soft pastel", "characters": [{"name": "Mi", "description": "a ginger cat"}],
                "scenes": [{"id": "x", "image_prompt": f"Vertical 9:16 frame. Mi on a street {i}", "narration": f"Câu {i}",
                            **(scene or SHOT)} for i in range(scenes)]}
    monkeypatch.setattr(planner, "_draft_claude_code", draft)
    monkeypatch.setattr(planner, "claude_code_binary", lambda: "/usr/bin/claude")


def test_plan_basic_writes_structured_i2v_shots(monkeypatch):
    calls = []
    _fake_claude(monkeypatch, calls)
    sb, used = basic_planner.plan_basic("mèo lạc", fmt="clip", audio="onscreen_text", visual="anime",
                                        genres=["comedy"], scenes=2, aspect="9:16", language="Vietnamese")
    user, system, schema = calls[0]
    assert len(calls) == 1  # nothing to repair
    assert used == "claude_code" and schema is basic_planner.BasicDraft and system == basic_planner.SYSTEM
    assert "Format: Clip ngắn" in user and "Sound: Chữ trên màn hình" in user and "Genres: comedy" in user
    assert [s.id for s in sb.scenes] == ["s01", "s02"] and {s.mode for s in sb.scenes} == {"keyframe"}
    assert sb.format == "clip" and sb.audio == "onscreen_text" and sb.visual_style == "anime" and sb.basic
    assert sb.style.startswith("Japanese anime style") and "soft pastel" in sb.style
    assert sb.scenes[0].narration == "Câu 0" and sb.subtitle_language == "Vietnamese"
    assert sb.dialogue_language == NO_DIALOGUE and sb.aspect == "9:16"
    s = sb.scenes[0]
    assert "9:16" not in s.image_prompt and s.image_prompt.startswith("Mi on a street")  # rule 7
    vid = sb.for_flow_video(sb.video_prompt(s), s)
    assert vid == ("Static locked-off medium shot, shallow depth of field. In the center, the ginger kitten looks left "
                   "and right, while a cart rolls past far behind. Audio: Quiet supermarket hum, soft bell jingle. "
                   "Single continuous shot, no scene cuts.")
    assert "No dialogue" not in vid and "no music" not in vid  # review 2: no negatives
    assert NO_TEXT not in vid  # rule 6: no long negative


def test_long_line_and_names_are_sent_back_for_one_fix(monkeypatch):
    calls, logs = [], []
    bad = {**SHOT, "action": "Mi turns to the girl",
           "speaker": {"position": "On the right", "visual_tag": "the girl in yellow", "voice_style": "excited",
                       "line": "I really really cannot believe that you found me here in this huge store today",
                       "action": "laughs as she speaks"},
           "listeners": [{"position": "On the left", "visual_tag": "the ginger kitten"}]}
    good = {**bad, "action": "On the left, the ginger kitten turns to the girl",
            "speaker": {**bad["speaker"], "line": "You found me, little one"}}
    _fake_claude(monkeypatch, calls, scenes=1, scene=bad, fix={"title": "t", "scenes": [{"id": "s01", "image_prompt": "Mi", **good}]})
    sb, _ = basic_planner.plan_basic("x", fmt="short_film", audio="dialogue", scenes=1, language="English",
                                     log=logs.append)
    assert len(calls) == 2 and "more than 12 words" in calls[1][0] and "name(s) Mi" in calls[1][0]
    s = sb.scenes[0]
    assert s.video_shot.speaker.line == "You found me, little one" and s.narration == "You found me, little one"
    vid = sb.for_flow_video(sb.video_prompt(s), s)
    # eyeline guide: locked-off viewpoint, frame position + head angle + gaze anchor right before the speech verb
    assert vid.startswith("Characters face and look only at each other throughout the scene. Locked-off 50mm medium "
                          "two-shot, stationary viewpoint. Positioned on frame right in three-quarter profile, the girl "
                          "in yellow laughs, then turns head toward frame left and locks gaze onto the ginger kitten on "
                          "frame left at eye level, speaking in English in an excited voice: You found me, little one. "
                          "(no subtitles, no captions, no on-screen text) After finishing the line, the girl in yellow "
                          "closes mouth while maintaining that exact eyeline.")
    assert ("On frame left, the ginger kitten faces frame right, remaining completely silent with lips closed, "
            "nodding slightly.") in vid
    assert vid.endswith("Audio: Quiet supermarket hum, soft bell jingle. Single continuous shot, no scene cuts.")
    assert sb.dialogue_warnings() == {}
    assert any("Sửa 1 cảnh" in l for l in logs)


def test_speaker_is_dropped_when_the_sound_has_no_dialogue(monkeypatch):
    scene = {**SHOT, "speaker": {"visual_tag": "the girl", "line": "Hi"}}
    _fake_claude(monkeypatch, [], scenes=1, scene=scene)
    sb, _ = basic_planner.plan_basic("x", fmt="clip", audio="voiceover", scenes=1, language="Vietnamese")
    s = sb.scenes[0]
    assert s.video_shot.speaker is None
    vid = sb.video_prompt(s)
    assert vid.startswith("A narrator voice-over speaks in Vietnamese in a calm, warm storytelling voice: Câu 0.")
    assert "Everyone in the frame keeps lips closed." in vid


def test_plan_basic_without_text_drops_narration_and_subtitles(monkeypatch):
    _fake_claude(monkeypatch, [])
    sb, _ = basic_planner.plan_basic("x", fmt="feature", audio="music", visual="other", custom_style="clay stop-motion",
                                     scenes=2, language="Vietnamese")
    assert all(s.narration is None for s in sb.scenes)
    assert sb.subtitle_language == NO_SUBTITLES and sb.style.startswith("clay stop-motion")


def test_plan_basic_refuses_the_drama_format():
    with pytest.raises(ValueError):
        basic_planner.plan_basic("x", fmt=formats.DRAMA)


def test_public_choices_for_the_ui():
    pub = formats.public()
    keys = [f["key"] for f in pub["formats"]]
    assert keys == ["feature", "series", "short_film", "clip", "short_drama"]  # the deprecated drama comes last
    assert [f["basic"] for f in pub["formats"]] == [True, True, True, True, False]
    assert [f["deprecated"] for f in pub["formats"]] == [False, False, False, False, True]
    assert pub["default"] == "clip" and pub["deprecated_note"]
    assert {a["value"]: a["enabled"] for a in pub["aspects"]} == {"9:16": True, "16:9": True, "1:1": False}
    assert len(pub["audio"]) == 5 and len(pub["visuals"]) == 5 and len(pub["genres"]) == 12


# --- the I2V prompt guide, in code ------------------------------------------------------------------------

def test_clean_text_applies_the_checklist():
    from filmmaker.i2v_prompt import clean_line, clean_text
    assert clean_text('Vertical 9:16 frame. She says "I don\'t know" in 8 seconds, it\'s 4K.') == "She says I do not know, it is."
    assert clean_line("Let's go, we can't wait.") == "Let us go, we cannot wait"


def test_edited_shot_gets_warnings_for_what_code_cannot_fix():
    sb = Storyboard.model_validate({
        "title": "t", "format": "clip", "audio": "dialogue", "dialogue_language": "Vietnamese",
        "characters": [{"name": "Lan", "description": "a woman"}],
        "scenes": [{"id": "s01", "mode": "keyframe", "prompt": "x", "video_shot": {
            "camera": "", "action": "Lan smiles, then waves, then sits, then stands",
            "speaker": {"visual_tag": "the woman", "line": " ".join(["chữ"] * 13)},
            "listeners": [{"visual_tag": "the man"}]}}]})
    w = " | ".join(sb.dialogue_warnings()["s01"])
    for part in ("Thiếu máy quay", "13 từ", "cần vị trí", "quá nhiều nhịp", "tên riêng (Lan)"):
        assert part in w


def test_line_keeps_its_own_punctuation_and_names_in_camera_are_caught():
    from filmmaker.i2v_prompt import Speaker, VideoShot, assemble, warnings
    shot = VideoShot(camera="Slow dolly-in toward Grandma", speaker=Speaker(visual_tag="she", line="Của em mà!"))
    assert "speaking in Vietnamese: Của em mà! (no" in assemble(shot, audio_mode="dialogue", language="Vietnamese")
    assert any("Grandma" in w for w in warnings(shot, audio_mode="dialogue", narration=None, names=["Grandma"]))


def test_basic_image_prompt_has_a_short_no_text_ending_and_no_double_stop():
    from filmmaker.i2v_prompt import NO_TEXT_IMAGE
    sb = Storyboard.model_validate({"title": "t", "format": "clip", "audio": "silent",
                                    "characters": [{"name": "Lan", "description": "a young woman in red."}],
                                    "scenes": [{"id": "s01", "mode": "keyframe", "image_prompt": "Lan sits", "prompt": "x"}]})
    s = sb.scenes[0]
    img = sb.for_flow_keyframe(sb.image_prompt(s), s)
    assert img == f"Lan sits. Lan: a young woman in red. {NO_TEXT_IMAGE}"
    assert NO_TEXT not in img and ".." not in img and "dialogue" not in img
    drama = _sb()  # unchanged for the drama (its fingerprints must not move)
    assert drama.for_flow_keyframe(drama.image_prompt(drama.scenes[0]), drama.scenes[0]).endswith(NO_TEXT)


def test_review_two_people_acting_identical_twins_light_and_negatives_are_repaired(monkeypatch):
    calls = []
    bad = {**SHOT, "image_prompt": "Two identical twin girls at a table",
           "action": "On the left, the girl in yellow reaches for the plate. On the right, the girl in blue slides the plate away",
           "environment": "warm light matching the starting frame", "audio": "kitchen tone, no music"}
    good = {**SHOT, "image_prompt": "Two sisters who look alike at a table", "environment": "",
            "action": "On the left, the girl in yellow reaches for the plate while the girl in blue watches"}
    _fake_claude(monkeypatch, calls, scenes=1, scene=bad,
                 fix={"title": "t", "scenes": [{"id": "s01", **good}]})
    sb, _ = basic_planner.plan_basic("x", fmt="clip", audio="silent", scenes=1, language="Vietnamese")
    fix = calls[1][0]
    for problem in ("ONE active person", "identical look-alikes", "repeats the light", "lists what is not heard"):
        assert problem in fix
    assert sb.dialogue_warnings() == {}


def test_review_fixes_applied_by_code_and_warnings():
    from filmmaker.i2v_prompt import VideoShot, assemble, soften_identical, warnings
    assert soften_identical("two identical twin girls; Lan, identical twin to Mai, same round face") == \
        "two girls who look alike; Lan, sister of Mai, a similar round face"
    shot = VideoShot(camera="Static medium shot", action="On the left, A waves. On the right, B claps",
                     environment="curtains sway under warm light matching the starting frame", audio="")
    vid = assemble(shot, audio_mode="silent", language=None)
    assert vid.endswith("while curtains sway. Audio: Quiet ambient room tone. Single continuous shot, no scene cuts.")
    assert any("1 người chủ động" in w for w in warnings(shot, audio_mode="silent", narration=None, names=[]))


def test_dialogue_scene_with_open_mouths_or_voice_sounds_is_repaired(monkeypatch):
    calls = []
    talk = {**SHOT, "speaker": {"position": "In the center", "visual_tag": "the grandmother", "line": "Bà có bánh đây"},
            "listeners": [{"position": "On the left", "visual_tag": "the girl"}]}
    bad = {**talk, "image_prompt": "Two girls with open mouths of surprise", "audio": "kitchen tone, children gasp"}
    good = {**talk, "image_prompt": "Two girls with closed-mouth smiles", "audio": "kitchen tone, plate clink"}
    _fake_claude(monkeypatch, calls, scenes=1, scene=bad, fix={"title": "t", "scenes": [{"id": "s01", **good}]})
    sb, _ = basic_planner.plan_basic("x", fmt="clip", audio="dialogue", scenes=1, language="Vietnamese")
    assert "open mouth" in calls[1][0] and "voice sounds" in calls[1][0]
    assert sb.scenes[0].image_prompt == "Two girls with closed-mouth smiles" and sb.dialogue_warnings() == {}


def test_listener_grin_becomes_a_closed_lip_smile():
    from filmmaker.i2v_prompt import Listener, Speaker, VideoShot, assemble
    shot = VideoShot(camera="Static", speaker=Speaker(visual_tag="the woman", line="Ăn đi"),
                     listeners=[Listener(position="On the left", visual_tag="the girl", silent_action="grinning happily")])
    vid = assemble(shot, audio_mode="dialogue", language=None)
    assert "On frame left, the girl faces frame right, smiling, remaining completely silent with lips closed" in vid
    assert "grin" not in vid


def test_child_safety_wording_for_basic_pictures():
    from filmmaker.i2v_prompt import child_safe
    assert child_safe("An 8-year-old Vietnamese girl") == "A young Vietnamese schoolgirl"
    assert child_safe("Vietnamese girl, age 8, round cheeks, red shirt") == "Vietnamese schoolgirl, red shirt"
    assert child_safe("grandmother, aged 70") == "grandmother, aged 70"
    assert child_safe("A little Vietnamese girl, two pigtails") == "A Vietnamese schoolgirl, two pigtails"
    assert child_safe("a young boy in blue") == "a schoolboy in blue"
    assert child_safe("girl, age 8, red shirt, lively expression") == "schoolgirl, red shirt"
    sb = Storyboard.model_validate({"title": "t", "format": "clip", "audio": "silent",
                                    "characters": [{"name": "Lan", "description": "Vietnamese girl, age 8, yellow t-shirt"}],
                                    "scenes": [{"id": "s01", "mode": "keyframe", "image_prompt": "Lan sits", "prompt": "x"}]})
    portrait = sb.portrait_prompt(sb.characters[0])
    assert "age 8" not in portrait and "Full body" not in portrait and "studio" not in portrait
    assert portrait.startswith("A cheerful Vietnamese schoolgirl, yellow t-shirt, smiling as she helps set the kitchen table in a bright, everyday home. Medium shot")
    assert "age 8" not in sb.image_prompt(sb.scenes[0])
    drama = Storyboard.model_validate({**sb.model_dump(exclude_none=True), "format": None, "audio": None})
    # the drama too: a child's reference picture is never the studio full-body pose
    drama_portrait = drama.portrait_prompt(drama.characters[0])
    assert "Full body" not in drama_portrait and drama_portrait.startswith("A cheerful Vietnamese schoolgirl")


def test_policy_block_is_reported_plainly(monkeypatch):
    from filmmaker import gflow as gf
    err = {"status": "fail", "error": {"detail": "agent-only composer: no image. Last reply: error\n\nThis generation "
                                                 "might violate our policies. Please try a different prompt."}}
    monkeypatch.setattr(gf, "_stream", lambda cmd, timeout, on_line, env: (25, json.dumps(err), False))
    g = gf.Gflow(binary="gflow")
    with pytest.raises(gf.GflowError, match="chính sách nội dung"):
        g.run_json("image", "t2i", "x")


def test_vietnamese_without_diacritics_is_sent_back_once(monkeypatch):
    sb = Storyboard.model_validate({
        "title": "t", "dialogue_language": "Vietnamese", "subtitle_language": "Vietnamese",
        "characters": [{"name": "Minh", "description": "a man"}],
        "scenes": [{"id": "s01", "mode": "keyframe", "prompt": "x", "narration": "Vua ve toi nha anh thay",
                    "dialogue": [{"character": "Minh", "line": "Ca nha lam gi tren do vay?"}]},
                   {"id": "s02", "mode": "keyframe", "prompt": "y", "narration": "Anh hung nha minh — ca nha"}]})
    assert set(planner.vietnamese_without_marks(sb)) == {"s01.line.0", "s01.narration", "s02.narration"}
    seen = {}

    def fake(user, timeout=900, *, system=None, schema_model=None):
        seen["user"] = user
        return {"items": [{"key": "s01.line.0", "text": "Cả nhà làm gì trên đó vậy?"},
                          {"key": "s01.narration", "text": "Vừa về tới nhà anh thấy"}]}
    monkeypatch.setattr(planner, "_draft_claude_code", fake)
    out = planner.fix_vietnamese_diacritics(sb, backend="claude_code")
    assert out.scenes[0].dialogue[0].line == "Cả nhà làm gì trên đó vậy?"
    assert out.scenes[0].narration == "Vừa về tới nhà anh thấy" and "Ca nha lam gi" in seen["user"]
    assert planner.vietnamese_without_marks(out) == {}


def test_image_prompt_keeps_possessives_so_the_character_is_found():
    sb = Storyboard.model_validate({"title": "t", "format": "clip", "audio": "silent",
                                    "characters": [{"name": "Dad", "description": "a man in grey"}],
                                    "scenes": [{"id": "s01", "mode": "keyframe", "prompt": "x",
                                                "image_prompt": "Vertical 9:16 frame. Close-up of Dad's face"}]})
    s = sb.scenes[0]
    assert sb.image_prompt(s).startswith("Close-up of Dad's face. Dad: a man in grey.")
    assert [c.name for c in sb.scene_characters(s)] == ["Dad"]


def test_plan_basic_keeps_locations_and_adds_the_setting_to_pictures_only(monkeypatch):
    scene = {**SHOT, "location": "Bathroom"}
    calls = []

    def draft(user, timeout=900, *, system=None, schema_model=None):
        calls.append(user)
        return {"title": "t", "characters": [{"name": "Mi", "description": "a ginger cat"}],
                "locations": [{"name": "Bathroom", "description": "small bathroom, mint tiles, white tub on the left"},
                              {"name": "bathroom", "description": "duplicate"}],
                "scenes": [{"id": "x", "image_prompt": "Mi on the tub", **scene},
                           {"id": "y", "image_prompt": "Mi again", **{**SHOT, "location": "Nowhere"}}]}
    monkeypatch.setattr(planner, "_draft_claude_code", draft)
    monkeypatch.setattr(planner, "claude_code_binary", lambda: "/usr/bin/claude")
    sb, _ = basic_planner.plan_basic("x", fmt="clip", audio="silent", scenes=2, language="Vietnamese")
    assert [l.name for l in sb.locations] == ["Bathroom"]
    assert sb.scenes[0].location == "Bathroom" and sb.scenes[1].location is None  # unknown name dropped
    s = sb.scenes[0]
    assert "Setting: small bathroom, mint tiles, white tub on the left." in sb.image_prompt(s)
    assert "mint tiles" not in sb.video_prompt(s)  # rule 1: the video prompt only moves


def test_autopilot_makes_location_pictures_for_basic_films_only(tmp_path):
    from filmmaker import autopilot as ap
    from filmmaker.storyboard import save_storyboard
    made = []

    class FakePipe:
        class state:
            @staticmethod
            def scene(_sid):
                return {}

        def generate_location_images(self, items, opts, on_done=None):
            for i, (loc, dest) in enumerate(items):
                made.append((loc.name, dest.name))
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"png")
                on_done(i, loc, dest)

            class R:
                failed = {}
            return R()

    for fmt, expect in (("clip", [("Bathroom", "bathroom.png")]), (None, [])):
        made.clear()
        d = tmp_path / str(fmt)
        d.mkdir()
        sb = Storyboard.model_validate({"title": "t", "format": fmt, "audio": "silent" if fmt else None,
                                        "locations": [{"name": "Bathroom", "description": "mint tiles"}],
                                        "scenes": [{"id": "s01", "mode": "keyframe", "prompt": "x", "location": "Bathroom"}]})
        save_storyboard(sb, d / "storyboard.yaml")
        pilot = ap.Autopilot.__new__(ap.Autopilot)
        pilot.sb_path, pilot.log = d / "storyboard.yaml", (lambda *_: None)
        pilot.pipe = lambda sb=None: FakePipe()
        pilot.opts = lambda **kw: None
        pilot.check_stop = lambda: None

        def edit_raw(fn, path=d / "storyboard.yaml"):
            import yaml
            raw = yaml.safe_load(path.read_text())
            if fn(raw):
                path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
            return True
        pilot.edit_raw = edit_raw
        pilot._location_images()
        assert made == expect
        if fmt:
            assert "assets/locations/bathroom.png" in (d / "storyboard.yaml").read_text()


# --- eyeline guide (Veo Dialogue & Eyeline Lock) ---------------------------------------------------------------

def test_single_speaker_is_locked_and_looks_at_the_opposite_frame_edge():
    from filmmaker.i2v_prompt import Speaker, VideoShot, assemble
    shot = VideoShot(camera="Slow cinematic push-in, close-up, 35mm lens",
                     speaker=Speaker(position="frame right", visual_tag="the man in the grey t-shirt", voice_style="panicked",
                                     line="Trời ơi con gì ghê quá vậy", action="throws his head back as he speaks"))
    vid = assemble(shot, audio_mode="dialogue", language="Vietnamese")
    assert vid.startswith("Locked-off 85mm close-up, stationary viewpoint. Positioned on frame right in three-quarter "
                          "profile, the man in the grey t-shirt throws his head back, then turns head toward frame left "
                          "and holds steady focus toward the left edge of the frame at eye level, speaking in Vietnamese "
                          "in a panicked voice: Trời ơi con gì ghê quá vậy.")
    assert "closes mouth while maintaining that exact eyeline" in vid
    for word in ("camera", "lens", "cinematic", "push-in", "viewer"):
        assert word not in vid.lower()


def test_gaze_anchor_sits_right_before_the_speech_verb_and_off_screen_targets():
    from filmmaker.i2v_prompt import Speaker, VideoShot, assemble
    shot = VideoShot(camera="medium", speaker=Speaker(position="frame left", visual_tag="the woman", line="Đi thôi",
                                                       gaze="off-screen right", gaze_level="slightly downward"))
    vid = assemble(shot, audio_mode="dialogue", language=None)
    assert "holds steady focus toward off-screen right slightly downward, speaking: Đi thôi." in vid


def test_over_the_shoulder_has_exactly_two_people():
    from filmmaker.i2v_prompt import Listener, Speaker, VideoShot, assemble
    shot = VideoShot(camera="Over-the-shoulder shot", speaker=Speaker(position="frame right", visual_tag="the girl in yellow",
                                                                      line="Chị ơi"),
                     listeners=[Listener(position="frame left", visual_tag="the woman in blue")])
    vid = assemble(shot, audio_mode="dialogue", language=None)
    assert vid.startswith("Static over-the-shoulder medium close-up, unmoving composition. Softly out-of-focus in "
                          "foreground left is the shoulder of the woman in blue seen from behind; exactly two people "
                          "present in the scene.")
    assert "locks gaze onto the position of the woman in blue at foreground left, speaking: Chị ơi." in vid


def test_forbidden_tokens_are_removed_even_when_negated():
    from filmmaker.i2v_prompt import veo_safe
    out = veo_safe("Slow dolly-in, the camera tilts down. She does not look at the camera. Cinematic shot of a man, "
                   "35mm lens, facing the viewer.")
    assert out == "Slow dolly-in, the view tilts down. She stays in three-quarter profile. Shot of a man, 35mm, facing."
    for word in ("camera", "lens", "viewer", "cinematic"):
        assert word not in out.lower()


def test_first_frame_of_a_dialogue_scene_is_staged_in_profile_with_closed_lips():
    sb = Storyboard.model_validate({
        "title": "t", "format": "clip", "audio": "dialogue", "dialogue_language": "Vietnamese",
        "characters": [{"name": "Mom", "description": "a woman in peach"}],
        "scenes": [{"id": "s01", "mode": "keyframe", "prompt": "x", "image_prompt": "Mom sits on the tub",
                    "video_shot": {"camera": "two-shot", "speaker": {"position": "frame left", "visual_tag": "the woman in peach",
                                                                     "line": "Anh hùng nhà mình"},
                                   "listeners": [{"position": "frame right", "visual_tag": "the man in grey"}]}}]})
    img = sb.image_prompt(sb.scenes[0])
    assert ("Staging for the dialogue: the woman in peach positioned on frame left in three-quarter profile, head "
            "turned toward frame right, gaze held there at eye level; everyone in the frame with neutral closed lips.") in img


def test_180_degree_axis_between_shot_and_reverse_shot():
    from filmmaker.i2v_prompt import Speaker, VideoShot, axis_breaks
    a = VideoShot(camera="close-up", speaker=Speaker(position="frame left", visual_tag="the man", line="Gì vậy"))
    b_bad = VideoShot(camera="close-up", speaker=Speaker(position="frame left", visual_tag="the woman", line="Con gián"))
    b_ok = VideoShot(camera="close-up", speaker=Speaker(position="frame right", visual_tag="the woman", line="Con gián"))
    assert "s02" in axis_breaks([("s01", a), ("s02", b_bad)])
    assert axis_breaks([("s01", a), ("s02", b_ok)]) == {}


def test_planner_sends_body_relative_directions_and_axis_breaks_back(monkeypatch):
    calls = []
    talk = {**SHOT, "speaker": {"position": "frame left", "visual_tag": "the man", "line": "Gì vậy",
                                "action": "turns to his left"}}
    good = {**SHOT, "speaker": {"position": "frame left", "visual_tag": "the man", "line": "Gì vậy"}}
    _fake_claude(monkeypatch, calls, scenes=1, scene=talk, fix={"title": "t", "scenes": [{"id": "s01", "image_prompt": "x", **good}]})
    sb, _ = basic_planner.plan_basic("x", fmt="clip", audio="dialogue", scenes=1, language="Vietnamese")
    assert "body-relative" in calls[1][0]
    assert sb.scenes[0].video_shot.speaker.action == ""


def test_in_frame_partner_gets_the_gaze_lock_and_listener_locks_do_not_contradict():
    from filmmaker.i2v_prompt import Listener, Speaker, VideoShot, assemble
    shot = VideoShot(camera="two-shot",
                     speaker=Speaker(position="frame left", visual_tag="the woman", line="Anh làm vỡ", gaze="frame right",
                                     action="points toward the right"),
                     listeners=[Listener(position="frame right", visual_tag="the man",
                                         silent_action="shaking his head slightly with lips closed")])
    vid = assemble(shot, audio_mode="dialogue", language=None)
    assert "points toward frame right, then turns head toward frame right and locks gaze onto the man on frame right" in vid
    assert ("On frame right, the man faces frame left, shaking his head slightly, remaining completely silent with lips "
            "closed.") in vid
    assert vid.count("lips closed") == 1 and "nodding" not in vid


def test_only_one_while_per_sentence():
    from filmmaker.i2v_prompt import VideoShot, assemble
    shot = VideoShot(camera="wide", action="the cat jumps down, while the two people flinch",
                     environment="a bowl topples and shatters")
    assert assemble(shot, audio_mode="silent", language=None).count("while") == 1


def test_listeners_face_a_speaker_in_the_center():
    from filmmaker.i2v_prompt import Listener, Speaker, VideoShot, assemble
    shot = VideoShot(camera="group", speaker=Speaker(position="frame center", visual_tag="the schoolgirl", line="Con mèo"),
                     listeners=[Listener(position="frame left", visual_tag="the woman"),
                                Listener(position="frame right", visual_tag="the man")])
    vid = assemble(shot, audio_mode="dialogue", language=None)
    assert "On frame left, the woman faces frame right" in vid and "On frame right, the man faces frame left" in vid


def test_qc_expects_the_i2v_speaker_line_and_the_narrator():
    from filmmaker import qc
    base = {"title": "t", "format": "clip", "dialogue_language": "Vietnamese",
            "scenes": [{"id": "s01", "mode": "keyframe", "prompt": "x", "narration": "Mèo lạc rồi",
                        "video_shot": {"camera": "two-shot", "speaker": {"position": "frame left",
                                       "visual_tag": "the woman in the beige cardigan", "voice_style": "sharp",
                                       "line": "Anh làm vỡ cái bát rồi còn gì nữa!"}}},
                       {"id": "s02", "mode": "keyframe", "prompt": "y", "video_shot": {"camera": "wide", "action": "x"}}]}
    sb = Storyboard.model_validate({**base, "audio": "dialogue"})
    exp = qc.expected_dialogue(sb, sb.scenes[0])
    assert exp.startswith("Expected dialogue, in Vietnamese") and "the woman in the beige cardigan: Anh làm vỡ" in exp
    assert qc.expected_dialogue(sb, sb.scenes[1]).startswith("No dialogue in this scene")
    vo = Storyboard.model_validate({**base, "audio": "voiceover"})
    assert "narrator voice-over" in qc.expected_dialogue(vo, vo.scenes[0]) and "Mèo lạc rồi" in qc.expected_dialogue(vo, vo.scenes[0])
    drama = Storyboard.model_validate({"title": "t", "dialogue_language": "Vietnamese", "characters": [{"name": "Lan", "description": "a"}],
                                       "scenes": [{"id": "s01", "mode": "keyframe", "prompt": "x",
                                                   "dialogue": [{"character": "Lan", "line": "Chào"}]}]})
    assert "- Lan: Chào" in qc.expected_dialogue(drama, drama.scenes[0])  # the drama is unchanged


def test_plural_listeners_and_chubby_children():
    from filmmaker.i2v_prompt import Listener, Speaker, VideoShot, assemble, child_safe
    shot = VideoShot(camera="group", speaker=Speaker(position="frame left", visual_tag="the man", line="Gì vậy"),
                     listeners=[Listener(position="frame right", visual_tag="the two children on the toilet lid")])
    assert "the two children on the toilet lid face frame left" in assemble(shot, audio_mode="dialogue", language=None)
    assert child_safe("A little boy with short dark hair and a round chubby face") == \
        "A schoolboy with short dark hair and a round face"


def test_is_minor_tells_children_from_adults():
    from filmmaker.i2v_prompt import is_minor
    assert is_minor("Vietnamese toddler girl, about 4 years old, pigtails")
    assert is_minor("Chubby Vietnamese baby boy, about 10 months old")
    assert is_minor("15-year-old Thai boy, buzz cut") and is_minor("A little girl in a yellow dress")
    assert not is_minor("25-year-old Thai man") and not is_minor("Vietnamese woman, around 28")
    assert not is_minor("Vietnamese man, father of two little girls") and not is_minor("A young woman, long hair")
    assert not is_minor("18-year-old student, hoodie")


def test_child_wording_for_ages_in_months_and_approximate_ages():
    from filmmaker.i2v_prompt import child_portrait, child_safe
    assert child_safe("Vietnamese toddler girl, about 4 years old, round cheeks, pink dress") == "Vietnamese toddler girl, pink dress"
    assert child_safe("Chubby baby boy, about 10 months old, white onesie") == "baby boy, white onesie"
    assert child_safe(child_safe("Vietnamese girl, age 8, round cheeks")) == "Vietnamese schoolgirl"
    assert child_safe("A woman, about 30 years old") == "A woman, about 30 years old"
    baby = child_portrait("Chubby Vietnamese baby boy, about 10 months old, white onesie", "warm light")
    assert baby.startswith("A cheerful Vietnamese baby boy, white onesie, sitting on a soft play mat")
    assert "months" not in baby and "Chubby" not in baby and baby.endswith("Style: warm light.")
    girl = child_portrait("A little Vietnamese girl in a yellow dress")
    assert girl.startswith("A cheerful Vietnamese schoolgirl in a yellow dress, smiling as she helps set")


def test_drama_sends_children_in_safe_wording_without_touching_fingerprints():
    sb = Storyboard.model_validate({
        "title": "t", "characters": [{"name": "Na", "description": "Vietnamese toddler girl, about 4 years old, round cheeks",
                                      "image_prompt": "Full-body portrait of a Vietnamese toddler girl about 4, plain studio background"},
                                     {"name": "Mai", "description": "Vietnamese woman, around 28"}],
        "scenes": [{"id": "s01", "mode": "keyframe", "image_prompt": "Na, 4 years old, hugs Mai", "prompt": "Na waves"}]})
    s = sb.scenes[0]
    before = sb.scene_fingerprint(s)
    assert "4 years old" in sb.image_prompt(s)  # the stored prompt (fingerprint) is unchanged
    sent = sb.for_flow_keyframe(sb.image_prompt(s), s)
    assert "4 years old" not in sent and "round cheeks" not in sent
    na = sb.portrait_prompt(sb.characters[0])
    assert "studio" not in na and "Full-body" not in na and na.startswith("A cheerful Vietnamese toddler girl")
    assert sb.portrait_prompt(sb.characters[1]).startswith("Character reference image of Mai")  # adults unchanged
    assert sb.scene_fingerprint(s) == before


# --- the old short drama flow is deprecated ------------------------------------------------------------------

def test_drama_flow_is_deprecated():
    assert formats.is_deprecated(None) and formats.is_deprecated("") and formats.is_deprecated(formats.DRAMA)
    assert not formats.is_deprecated("clip") and not formats.is_deprecated(formats.DEFAULT_FORMAT)
    assert formats.is_basic(formats.DEFAULT_FORMAT)


def _fake_planners(monkeypatch, calls):
    from filmmaker import basic_planner as bp, planner

    def basic(idea, **kw):
        calls.append(("basic", kw))
        return Storyboard.model_validate({"title": "t", "format": kw["fmt"], "audio": kw["audio"],
                                          "scenes": [{"id": "s01", "prompt": "p"}]}), "api"

    def drama(idea, **kw):
        calls.append(("drama", kw))
        return Storyboard.model_validate({"title": "t", "scenes": [{"id": "s01", "prompt": "p"}]}), "api"

    monkeypatch.setattr(bp, "plan_basic", basic)
    monkeypatch.setattr(planner, "plan_auto", drama)


def test_cli_plan_uses_the_new_flow_by_default(monkeypatch, tmp_path):
    from click.testing import CliRunner
    from filmmaker.cli import main
    import yaml

    calls = []
    _fake_planners(monkeypatch, calls)
    out = tmp_path / "sb.yaml"
    r = CliRunner().invoke(main, ["plan", "ý", "-o", str(out), "--dialogue-lang", "Thai", "--aspect", "9:16"])
    assert r.exit_code == 0, r.output
    kind, kw = calls[0]
    assert kind == "basic" and kw["fmt"] == "clip" and kw["audio"] == "dialogue" and kw["language"] == "Thai"
    assert yaml.safe_load(out.read_text())["format"] == "clip"
    assert "deprecated" not in r.output


def test_cli_plan_old_drama_flow_warns_and_records_its_format(monkeypatch, tmp_path):
    from click.testing import CliRunner
    from filmmaker.cli import main
    import yaml

    calls = []
    _fake_planners(monkeypatch, calls)
    out = tmp_path / "sb.yaml"
    r = CliRunner().invoke(main, ["plan", "ý", "-o", str(out), "--format", "short_drama"])
    assert r.exit_code == 0, r.output
    assert calls[0][0] == "drama" and "deprecated" in r.output
    assert yaml.safe_load(out.read_text())["format"] == "short_drama"


# --- dialogue films: the subtitle is the spoken line, nothing else -------------------------------------------------

def _talk(line=None, narration=None, subtitles=None):
    shot = {"camera": "Static locked-off medium shot", "action": "the mother holds the spoon"}
    if line is not None:
        shot["speaker"] = {"position": "frame right", "visual_tag": "the woman in the cream cardigan", "line": line}
    sc = {"id": "s01", "mode": "keyframe", "image_prompt": "a mother and a toddler", "prompt": "x", "video_shot": shot}
    if narration is not None:
        sc["narration"] = narration
    if subtitles is not None:
        sc["subtitles"] = subtitles
    return Storyboard.model_validate({"title": "t", "style": "s", "format": "clip", "audio": "dialogue",
                                      "dialogue_language": "Vietnamese", "scenes": [sc]})


def test_dialogue_film_scene_without_speaker_has_no_subtitle():
    sb = _talk(narration="Há miệng nào, bé của mẹ ơi.", subtitles={"en": "Open wide"})
    s = sb.scenes[0]
    assert s.narration is None and s.subtitles is None
    assert "Há miệng" not in sb.video_prompt(s)


def test_dialogue_film_subtitle_follows_the_speaker_line():
    sb = _talk(line="Há miệng nào, bé của mẹ ơi.", narration="Một câu khác.", subtitles={"en": "Another line"})
    s = sb.scenes[0]
    assert s.narration == "Há miệng nào, bé của mẹ ơi." and s.subtitles is None  # stale translation dropped
    assert "Há miệng nào, bé của mẹ ơi." in sb.video_prompt(s)
    kept = _talk(line="Ăn nào", narration="Ăn nào", subtitles={"en": "Eat up"}).scenes[0]
    assert kept.subtitles.en == "Eat up"
    assert _talk(line="Ăn nào").scenes[0].narration == "Ăn nào"


def test_voiceover_narration_is_untouched():
    sb = Storyboard.model_validate({"title": "t", "style": "s", "format": "clip", "audio": "voiceover", "scenes": [
        {"id": "s01", "mode": "keyframe", "image_prompt": "a", "prompt": "x", "narration": "Ngày xưa."}]})
    assert sb.scenes[0].narration == "Ngày xưa."


def test_planner_flags_narration_without_speaker_in_dialogue_film():
    draft = {"characters": [], "scenes": [
        {"id": "s01", "camera": "Static shot", "action": "the toddler opens her mouth", "narration": "Há miệng nào"},
        {"id": "s02", "camera": "Static shot", "action": "the toddler squirms"}]}
    bad = basic_planner.problems(draft, "dialogue")
    assert any("no `speaker`" in m for m in bad.get("s01", []))
    assert "s02" not in bad
    assert "s01" not in basic_planner.problems(draft, "voiceover")


def test_center_partner_gaze_says_frame_center():
    from filmmaker import i2v_prompt
    shot = i2v_prompt.VideoShot.model_validate({
        "camera": "Static locked-off medium shot", "action": "a",
        "speaker": {"position": "frame right", "visual_tag": "the woman in the cream cardigan", "line": "Ăn nào",
                    "gaze": "the toddler girl in the pink romper"},
        "listeners": [{"position": "frame center", "visual_tag": "the toddler girl in the pink romper"}]})
    text = i2v_prompt.assemble(shot, audio_mode="dialogue", language="Vietnamese")
    assert "turns head toward frame left" in text and "pink romper on frame center" in text
