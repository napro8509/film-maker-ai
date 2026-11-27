"""F19: "Dùng prompt gợi ý" puts only the action into the "Hành động" box, not a whole prompt."""
from filmmaker.qc import action_only


def test_camera_dialogue_sound_and_no_text_sentences_are_dropped():
    full = ("Locked-off 50mm medium shot, stationary viewpoint. The man in the white shirt turns toward the woman "
            "and nods slowly. He says: “ขอโทษนะ”. Audio: quiet rain and room tone. "
            "No captions, no subtitles, no on-screen text.")
    assert action_only(full) == "The man in the white shirt turns toward the woman and nods slowly."


def test_a_plain_action_is_kept_whole():
    a = "The woman lifts the cup, looks at the window, then smiles."
    assert action_only(a) == a
    assert action_only(None) is None


def test_never_returns_nothing():
    assert action_only("Static camera, 35mm lens.") == "Static camera, 35mm lens."
