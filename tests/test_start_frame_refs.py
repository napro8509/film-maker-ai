"""A clip that starts from a picture (keyframe / i2v) gets at most 2 characters attached, the ones who speak or
act first: Flow takes 3 images in all on Veo Lite, and the start picture is one of them."""
from __future__ import annotations

from filmmaker import flow_ui
from filmmaker.storyboard import START_FRAME_CHARS, Storyboard


def _sb(tmp_path, scene: dict, names=("Mimi", "Lan", "Toan")) -> Storyboard:
    chars = []
    for n in names:
        img = tmp_path / f"{n}.png"
        img.write_bytes(b"x")
        chars.append({"name": n, "description": f"{n} look", "image": str(img)})
    return Storyboard.model_validate({"title": "T", "characters": chars,
                                      "scenes": [{"id": "s01", "mode": "keyframe", **scene}]})


def _picked(sb: Storyboard) -> list[str]:
    return [n for n, _ in sb.start_frame_char_refs(sb.scenes[0])]


def test_the_two_who_act_come_before_the_onlooker(tmp_path):
    sb = _sb(tmp_path, {
        "prompt": "The little girl waves both hands while the foam-headed man nods toward her.",
        "image_prompt": "Mimi sits in the foreground center, hands lifted. In front and lower, Toan leans in "
                        "playfully. Behind them, Lan stands near the bathtub watching with a warm smile."})
    assert START_FRAME_CHARS == 2
    assert _picked(sb) == ["Mimi", "Toan"]  # storyboard order would have been Mimi, Lan
    assert [n for n, _ in sb.scene_ui_char_refs(sb.scenes[0])] == ["Mimi", "Lan", "Toan"]  # unchanged elsewhere


def test_an_onlooker_named_first_still_comes_last(tmp_path):
    sb = _sb(tmp_path, {"prompt": "x", "image_prompt": "In the background Lan looks on. Toan lifts Mimi up."})
    assert _picked(sb) == ["Toan", "Mimi"]


def test_speakers_come_first_in_the_order_spoken(tmp_path):
    sb = _sb(tmp_path, {"prompt": "Mimi plays with the foam while Lan and Toan talk.",
                        "dialogue": [{"character": "Toan", "line": "Con ngoan quá"},
                                     {"character": "Lan", "line": "Ừ, giống anh"}]})
    assert _picked(sb) == ["Toan", "Lan"]


def test_two_or_fewer_characters_are_all_sent_in_storyboard_order(tmp_path):
    sb = _sb(tmp_path, {"prompt": "Lan hugs Mimi."}, names=("Mimi", "Lan"))
    assert _picked(sb) == ["Mimi", "Lan"]


def test_characters_the_text_never_names_fill_the_last_places(tmp_path):
    sb = _sb(tmp_path, {"prompt": "Toan laughs.", "characters": ["Mimi", "Lan", "Toan"]})
    assert _picked(sb) == ["Toan", "Mimi"]


def test_not_part_of_the_fingerprint_or_reference_signature(tmp_path):
    scene = {"prompt": "x", "image_prompt": "Mimi claps. Toan claps. Behind, Lan stands watching."}
    sb = _sb(tmp_path, scene)
    s = sb.scenes[0]
    assert len(sb.refs_signature(s)) == 3 and [n for n, _ in sb.scene_char_refs(s)] == ["Mimi", "Lan", "Toan"]


def test_chat_prompt_asks_for_reference_to_video_when_characters_ride_along():
    """Asked for "the exact first frame" plus character references, Flow stopped and asked which to drop."""
    p = flow_ui.build_prompt("she waves", refs=2, aspect="9:16", model="Veo 3.1 Lite", duration=8)
    assert "Reference-to-Video" in p and "all 3 attached images" in p and "exact first frame" not in p
    assert "The other 2 attached image(s) are character references" in p
    alone = flow_ui.build_prompt("she waves", refs=0, aspect="9:16", model="Veo 3.1 Lite", duration=8)
    assert "exact first frame" in alone and "Reference-to-Video" not in alone
