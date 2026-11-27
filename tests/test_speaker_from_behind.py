"""Người nói được dàn cảnh quay lưng: prompt không được bảo nhân vật quay đầu (sẽ lộ mặt)."""
from filmmaker import i2v_prompt
from filmmaker.i2v_prompt import Speaker, VideoShot


def compose(**speaker):
    sp = Speaker(position="frame center", line="พี่น้อยคะ", **speaker)
    shot = VideoShot(camera="Static shot", action="she sits", speaker=sp)
    return i2v_prompt._dialogue(shot, sp, language="Thai", env="", sound="rain")


def test_a_speaker_seen_from_behind_keeps_facing_away():
    out = compose(visual_tag="the woman in the navy top seen from behind", head_angle="seen from behind",
                  gaze="off-screen left")
    assert "keeps facing away" in out and "turns head" not in out and "พี่น้อยคะ" in out


def test_other_speakers_are_unchanged():
    out = compose(visual_tag="the woman in the navy top", head_angle="three-quarter profile", gaze="off-screen left")
    assert "turns head toward frame left" in out and "keeps facing away" not in out
