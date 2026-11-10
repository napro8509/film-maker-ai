"""Per-scene content direction and dialogue: prompts, no repeated lines, planner safety net, warnings."""

import pytest

from filmmaker import planner
from filmmaker.storyboard import DIALOGUE_TURNS, Storyboard

CHARS = [{"name": "Lan", "description": "young woman"}, {"name": "Minh", "description": "young man"},
         {"name": "Hoa", "description": "their mother"}]


def make(scenes, **kw):
    return Storyboard.model_validate({"title": "t", "characters": CHARS, "scenes": scenes, **kw})


TALK = [{"character": "Lan", "line": "Anh đi đâu cả đêm qua?", "delivery": "hurt, trembling"},
        {"character": "Minh", "line": "Không phải việc của em", "delivery": "cold"}]


def test_old_scene_prompts_and_fingerprint_unchanged():
    scene = {"id": "s01", "mode": "keyframe", "image_prompt": "Lan at a table", "prompt": "Lan stands up"}
    a = make([scene])
    b = make([{**scene, "dialogue": [], "content_direction": "  "}])
    assert b.scenes[0].dialogue is None and b.scenes[0].content_direction is None
    assert a.video_prompt(a.scenes[0]) == "Lan stands up"
    assert a.scene_fingerprint(a.scenes[0]) == b.scene_fingerprint(b.scenes[0])
    assert "dialogue" not in a.scenes[0].model_dump(exclude_none=True)


def test_dialogue_in_video_prompt_in_order_without_quotes():
    sb = make([{"id": "s01", "mode": "keyframe", "image_prompt": "Lan, Minh and Hoa at a table",
                "prompt": "Lan stands up", "content_direction": "Lan confronts her brother.", "dialogue": TALK}],
              dialogue_language="Vietnamese")
    v = sb.video_prompt(sb.scenes[0])
    assert v.startswith("Lan stands up. Content direction: Lan confronts her brother.")
    assert ("Lan (hurt, trembling) says: Anh đi đâu cả đêm qua? "
            "Then Minh (cold) says: Không phải việc của em.") in v
    assert v.index("Lan (hurt") < v.index("Then Minh")
    assert DIALOGUE_TURNS in v
    assert "Hoa stays silent." in v
    assert '"' not in v
    # the still gets the acting direction but never the spoken words
    img = sb.image_prompt(sb.scenes[0])
    assert "Content direction: Lan confronts her brother." in img and "says:" not in img


def test_editing_a_line_rerenders_only_that_scene():
    base = [{"id": "s01", "mode": "t2v", "prompt": "Lan waits", "dialogue": TALK},
            {"id": "s02", "mode": "t2v", "prompt": "Minh leaves"}]
    a = make(base)
    b = make([{**base[0], "dialogue": [TALK[0], {**TALK[1], "line": "Em đừng hỏi nữa"}]}, base[1]])
    assert a.scene_fingerprint(a.scenes[0]) != b.scene_fingerprint(b.scenes[0])
    assert a.scene_fingerprint(a.scenes[1]) == b.scene_fingerprint(b.scenes[1])


def test_speaker_gets_description_even_if_not_named_in_prompt():
    sb = make([{"id": "s01", "mode": "t2v", "prompt": "A quiet kitchen at night",
                "dialogue": [{"character": "Hoa", "line": "Con ăn chưa?"}]}])
    v = sb.video_prompt(sb.scenes[0])
    assert "Hoa: their mother." in v
    assert [c.name for c in sb.auto_characters(sb.scenes[0])] == ["Hoa"]


def test_no_dialogue_film_leaves_lines_out_and_warns():
    sb = make([{"id": "s01", "mode": "t2v", "prompt": "Lan waits", "dialogue": TALK[:1]}], dialogue_language="none")
    assert "says:" not in sb.for_flow_video(sb.video_prompt(sb.scenes[0]))
    assert any("Không thoại" in m for m in sb.dialogue_warnings()["s01"])


def test_warnings_for_repeated_lines_and_unknown_speakers():
    sb = make([
        {"id": "s01", "mode": "t2v", "prompt": "x", "dialogue": [
            {"character": "Lan", "line": "Về đi!"}, {"character": "Minh", "line": "về đi"}]},
        {"id": "s02", "mode": "t2v", "prompt": "y", "dialogue": [
            {"character": "Lan", "line": "Anh đi đâu cả đêm qua"}, {"character": "Tuan", "line": "Chào"},
            {"character": "Hoa", "line": "Anh đi đâu cả đêm qua?"}]},
    ])
    w = sb.dialogue_warnings()
    assert w["s01"] == ["Minh nói lặp câu “về đi” (đã có ở cảnh s01, Lan)"]
    assert "“Tuan” không có trong danh sách nhân vật" in w["s02"]
    assert any(m.startswith("Hoa nói lặp câu") for m in w["s02"])
    clean = make([{"id": "s01", "mode": "t2v", "prompt": "x", "dialogue": TALK}])
    assert clean.dialogue_warnings() == {}


def test_speaker_not_picked_into_scene_warns():
    sb = make([{"id": "s01", "mode": "t2v", "prompt": "x", "characters": ["Lan"], "dialogue": TALK}])
    assert sb.dialogue_warnings()["s01"] == ["Minh có thoại nhưng chưa được chọn vào cảnh"]


def test_too_many_lines_rejected():
    with pytest.raises(ValueError):
        make([{"id": "s01", "mode": "t2v", "prompt": "x",
               "dialogue": [{"character": "Lan", "line": f"câu {i}"} for i in range(13)]}])


def test_planner_drops_repeated_lines_across_scenes():
    sb = planner._to_storyboard({"title": "t", "characters": CHARS, "scenes": [
        {"id": "s01", "prompt": "a", "dialogue": [{"character": "Lan", "line": "Mẹ ơi!"},
                                                    {"character": "Minh", "line": "mẹ ơi"}]},
        {"id": "s02", "prompt": "b", "dialogue": [{"character": "Hoa", "line": "Mẹ ơi"}]},
        {"id": "s03", "prompt": "c", "dialogue": [{"character": "Hoa", "line": "Con về rồi à"}]},
    ]})
    assert [d.line for d in sb.scenes[0].dialogue] == ["Mẹ ơi!"]
    assert sb.scenes[1].dialogue is None
    assert [d.line for d in sb.scenes[2].dialogue] == ["Con về rồi à"]


def test_planner_schema_and_prompt_ask_for_direction_and_unique_lines():
    from filmmaker.storyboard import StoryboardDraft
    props = StoryboardDraft.model_json_schema()["$defs"]["SceneDraft"]["properties"]
    assert "content_direction" in props and "dialogue" in props
    assert "Never repeat dialogue" in planner.SYSTEM


def test_repeated_line_in_same_scene_is_not_sent():
    sb = make([{"id": "s01", "mode": "t2v", "prompt": "x", "dialogue": [
        {"character": "Lan", "line": "Về đi!"}, {"character": "Minh", "line": "về đi"},
        {"character": "Minh", "line": "Không"}]}])
    v = sb.video_prompt(sb.scenes[0])
    assert v.count("says:") == 2 and "Minh says: về đi" not in v and "Then Minh says: Không." in v
