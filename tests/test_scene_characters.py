"""Scene.characters: an explicit list of characters per scene overriding name detection (storyboard-board T2)."""
import pytest
import yaml
from pydantic import ValidationError

from filmmaker.storyboard import (MAX_REFS, Scene, SceneDraft, Storyboard, StoryboardDraft, load_storyboard,
                                  save_storyboard)


def make(tmp_path, scenes, chars=None, **extra) -> Storyboard:
    """Storyboard where A, B, C, D have images on disk and E has none."""
    if chars is None:
        chars = []
        for n in "ABCDE":
            c = {"name": f"Char{n}", "description": f"look of {n}"}
            if n != "E":
                img = tmp_path / f"{n}.png"
                img.write_bytes(n.encode())
                c["image"] = str(img)
            chars.append(c)
    return Storyboard.model_validate({"title": "t", "characters": chars, "scenes": scenes, **extra})


def names(refs):
    return [n for n, _ in refs]


def test_explicit_list_overrides_detection(tmp_path):
    sb = make(tmp_path, [
        {"id": "k", "mode": "keyframe", "image_prompt": "CharA and CharB at a table", "prompt": "CharA talks to CharB",
         "characters": ["CharA"]},
        {"id": "t", "mode": "t2v", "prompt": "CharA meets CharB", "characters": ["CharA"]},
    ])
    k, t = sb.scenes
    assert names(sb.scene_refs(k)) == ["CharA"]
    for p in (sb.image_prompt(k), sb.text_only_prompt(k)):
        assert "CharA: look of A." in p and "look of B" not in p
    assert names(sb.scene_refs(t)) == ["CharA"]
    assert "CharA: look of A." in sb.video_prompt(t) and "look of B" not in sb.video_prompt(t)
    assert [c.name for c in sb.auto_characters(k)] == ["CharA", "CharB"]
    assert [c.name for c in sb.scene_characters(k)] == ["CharA"]


def test_list_adds_unmentioned(tmp_path):
    sb = make(tmp_path, [{"id": "k", "prompt": "an empty street at dawn", "characters": ["CharB"]}])
    k = sb.scenes[0]
    assert names(sb.scene_refs(k)) == ["CharB"]
    assert "CharB: look of B." in sb.image_prompt(k)


def test_empty_list_means_none(tmp_path):
    sb = make(tmp_path, [{"id": "k", "image_prompt": "CharA sits", "prompt": "CharA and CharB walk",
                          "characters": []},
                         {"id": "t", "mode": "t2v", "prompt": "CharA walks", "characters": []}])
    k, t = sb.scenes
    assert sb.scene_refs(k) == [] and sb.scene_characters(k) == []
    assert "look of" not in sb.image_prompt(k) and "look of" not in sb.text_only_prompt(k)
    assert "look of" not in sb.video_prompt(t)


def test_thumbnail_unaffected_by_scene_characters(tmp_path):
    scenes = [{"id": "k", "prompt": "CharA and CharB walk"}]
    before = make(tmp_path, scenes, thumbnail={"prompt": "CharA and CharC on a poster"})
    after = make(tmp_path, [{**scenes[0], "characters": ["CharD"]}], thumbnail={"prompt": "CharA and CharC on a poster"})
    assert after.thumbnail_prompt() == before.thumbnail_prompt()
    assert after.thumbnail_refs() == before.thumbnail_refs()
    assert names(after.thumbnail_refs()) == ["CharA", "CharC"]


def test_four_names_three_images_in_storyboard_order(tmp_path):
    sb = make(tmp_path, [{"id": "k", "prompt": "a crowd", "characters": ["CharD", "CharE", "CharC", "CharB", "CharA"]}])
    k = sb.scenes[0]
    p = sb.image_prompt(k)
    for n in "ABCDE":
        assert f"Char{n}: look of {n}." in p
    assert p.index("CharA:") < p.index("CharB:") < p.index("CharE:")  # storyboard order, not list order
    assert names(sb.scene_refs(k)) == ["CharA", "CharB", "CharC"]
    assert len(sb.scene_refs(k)) == MAX_REFS


def test_i2v_and_refs_off(tmp_path):
    img = tmp_path / "start.png"
    img.write_bytes(b"x")
    sb = make(tmp_path, [{"id": "i", "mode": "i2v", "image": str(img), "prompt": "she turns", "characters": ["CharA"]}])
    assert sb.scene_refs(sb.scenes[0]) == []
    off = make(tmp_path, [{"id": "k", "prompt": "a street", "characters": ["CharA"]}], use_character_refs=False)
    assert off.scene_refs(off.scenes[0]) == []
    assert "CharA: look of A." in off.image_prompt(off.scenes[0])


def test_fingerprint_same_when_list_equals_auto_set(tmp_path):
    base = {"id": "k", "prompt": "CharB and CharA walk in the rain"}  # image_prompt empty: one text for every prompt
    old = make(tmp_path, [base])
    auto = [c.name for c in old.auto_characters(old.scenes[0])]
    assert auto == ["CharA", "CharB"]
    new = make(tmp_path, [{**base, "characters": auto}])
    assert new.scene_fingerprint(new.scenes[0]) == old.scene_fingerprint(old.scenes[0])
    t_old = make(tmp_path, [{"id": "t", "mode": "t2v", "prompt": "CharC dances"}])
    t_new = make(tmp_path, [{"id": "t", "mode": "t2v", "prompt": "CharC dances", "characters": ["CharC"]}])
    assert t_new.scene_fingerprint(t_new.scenes[0]) == t_old.scene_fingerprint(t_old.scenes[0])


def test_fingerprint_changes_when_prompts_mention_different_names(tmp_path):
    base = {"id": "k", "image_prompt": "CharA sits alone", "prompt": "CharB enters"}
    old = make(tmp_path, [base])
    new = make(tmp_path, [{**base, "characters": ["CharA", "CharB"]}])
    assert "look of B" not in old.image_prompt(old.scenes[0])
    assert "CharB: look of B." in new.image_prompt(new.scenes[0])
    assert new.scene_fingerprint(new.scenes[0]) != old.scene_fingerprint(old.scenes[0])


def test_case_insensitive_and_dedupe(tmp_path):
    sb = make(tmp_path, [{"id": "k", "prompt": "a room", "characters": [" chara ", "CHARA", "charb", "CharA"]}])
    k = sb.scenes[0]
    assert k.characters == ["chara", "charb"]
    assert [c.name for c in sb.scene_characters(k)] == ["CharA", "CharB"]
    assert Scene(id="x", prompt="p").characters is None


def test_unknown_name_error(tmp_path):
    (tmp_path / "storyboard.yaml").write_text(yaml.safe_dump({
        "title": "t", "characters": [{"name": "Lan", "description": "d"}],
        "scenes": [{"id": "s01", "prompt": "p", "characters": ["lan"]},
                   {"id": "s02", "prompt": "p", "characters": ["Lan", "Bao", "Tuan"]}]}), encoding="utf-8")
    with pytest.raises(ValidationError) as e:
        load_storyboard(tmp_path / "storyboard.yaml")
    assert "scene s02: unknown character 'Bao' in characters" in str(e.value)
    assert "Tuan" not in str(e.value)


@pytest.mark.parametrize("chars, bad", [
    ([{"name": "Lan", "description": "a"}, {"name": "lan", "description": "b"}], "'Lan'"),
    ([{"name": "Lan", "description": "a"}, {"name": "  ", "description": "b"}], "''"),
    ([{"name": "", "description": "a"}, {"name": "Minh", "description": "b"}, {"name": "MINH ", "description": "c"}],
     "'', 'Minh'"),
])
def test_duplicate_or_empty_names_only_checked_when_used(chars, bad):
    scenes = [{"id": "s01", "prompt": "Lan walks"}]
    sb = Storyboard.model_validate({"title": "t", "characters": chars, "scenes": scenes})  # old storyboard: loads
    assert sb.scenes[0].characters is None
    with pytest.raises(ValidationError) as e:
        Storyboard.model_validate({"title": "t", "characters": chars, "scenes": [{**scenes[0], "characters": []}]})
    assert f"character names must be unique and non-empty when scenes list characters: {bad}" in str(e.value)


def test_save_storyboard_keeps_empty_list_and_omits_none(tmp_path):
    sb = make(tmp_path, [{"id": "a", "prompt": "p", "characters": []},
                         {"id": "b", "prompt": "p", "characters": ["CharA"]},
                         {"id": "c", "prompt": "p"}])
    out = tmp_path / "out.yaml"
    save_storyboard(sb, out)
    scenes = yaml.safe_load(out.read_text(encoding="utf-8"))["scenes"]
    assert scenes[0]["characters"] == [] and scenes[1]["characters"] == ["CharA"]
    assert "characters" not in scenes[2]
    again = load_storyboard(out)
    assert [s.characters for s in again.scenes] == [[], ["CharA"], None]


def test_field_order_unchanged():
    old_sb = ["title", "aspect", "model", "image_model", "style", "project_id", "characters", "thumbnail",
              "use_character_refs", "no_text", "fallback_t2v", "scenes"]
    old_scene = ["id", "mode", "prompt", "image_prompt", "image", "end_image", "duration", "model", "narration",
                 "camera", "subtitles"]  # subtitles (vi / th / en tracks) appended after every old field
    # scene-continuity: locations / continuity right after characters (the schema order is the order Claude writes
    # in: places before the scenes using them), location / continue_previous appended after every old scene field;
    # all on the Draft classes so the planner can fill them. Old YAML is unchanged (unset = not written).
    i = old_sb.index("characters") + 1
    new_sb = old_sb[:i] + ["locations", "continuity"] + old_sb[i:]
    new_scene = ["location", "continue_previous"]
    # dialogue_language / subtitle_language exist only on Storyboard, after every old field (dialogue-language)
    # edit_style (video-analysis) also only on Storyboard, appended last
    assert list(Storyboard.model_fields) == new_sb + ["dialogue_language", "subtitle_language", "edit_style"]
    assert list(StoryboardDraft.model_fields) == new_sb
    assert list(Scene.model_fields)[:-1] == old_scene + new_scene == list(SceneDraft.model_fields)
    assert list(Scene.model_fields)[-1] == "characters"


def test_draft_schema_has_no_scene_characters():
    schema = StoryboardDraft.model_json_schema()
    assert schema["title"] == "Storyboard"
    scene = next(d for d in schema["$defs"].values() if d.get("title") == "Scene")
    assert "characters" not in scene["properties"]
    assert "characters" in schema["properties"]  # the storyboard's own character list stays
    draft = StoryboardDraft.model_validate({"title": "t", "scenes": [{"id": "s", "prompt": "p", "characters": ["X"]}]})
    assert not hasattr(draft.scenes[0], "characters")
