"""A scene without a line in a film with dialogue must tell Veo that nobody speaks: the structured I2V prompt said
nothing about speech and Veo made people talk, in English, in Thai films."""
from filmmaker.storyboard import I2V_SILENT_RULE, Storyboard

SPEAKER = {"position": "frame left", "visual_tag": "the man in the grey polo", "gaze": "the girl",
           "line": "แม่เป็นเจ้าหญิงนะลูก", "voice_style": "tender"}


def _sb(audio="dialogue", lang="Thai"):
    return Storyboard.model_validate({
        "title": "t", "format": "clip", "audio": audio, "dialogue_language": lang,
        "scenes": [{"id": "s01", "mode": "keyframe", "prompt": "x", "video_shot": {
                        "camera": "Medium shot.", "action": "The man points at the moon.", "audio": "Night room tone."}},
                   {"id": "s02", "mode": "keyframe", "prompt": "y", "video_shot": {
                        "camera": "Two-shot.", "action": "", "audio": "Night room tone.", "speaker": SPEAKER}}]})


def _sent(sb, i):
    s = sb.scenes[i]
    return sb.for_flow_video(sb.video_prompt(s), s)


def test_wordless_scene_says_everyone_stays_silent_before_the_audio_line():
    p = _sent(_sb(), 0)
    assert I2V_SILENT_RULE in p
    assert p.index(I2V_SILENT_RULE) < p.index("Audio:") < p.index("Single continuous shot")
    assert p.count(I2V_SILENT_RULE) == 1


def test_scene_with_a_line_is_unchanged():
    sb = _sb()
    s = sb.scenes[1]
    assert sb.for_flow_video(sb.video_prompt(s), s) == sb.video_prompt(s)
    assert I2V_SILENT_RULE not in _sent(sb, 1) and "speaking in Thai" in _sent(sb, 1)


def test_films_without_dialogue_are_unchanged():
    for sb in (_sb(audio="silent"), _sb(lang="none")):
        assert I2V_SILENT_RULE not in _sent(sb, 0)


def test_rule_is_not_part_of_the_fingerprint():
    sb = _sb()
    assert I2V_SILENT_RULE not in sb.video_prompt(sb.scenes[0])  # fingerprint input: old clips stay done


def test_applied_twice_is_idempotent():
    sb = _sb()
    once = _sent(sb, 0)
    assert sb.for_flow_video(once, sb.scenes[0]) == once


def test_silent_sentence_asks_for_no_crying_and_replaces_the_old_one():
    """22/11/2026: the old sentence asked for "soft crying" next to a plain-ambience Audio line, and Flow answered
    "Audio generation failed" to the same silent scene six times."""
    from filmmaker.rules import OLD_I2V_SILENT_RULE

    p = _sent(_sb(), 0)
    assert "crying" not in p and "sighs" not in p
    sb = _sb()
    s = sb.scenes[0]
    old = sb.video_prompt(s).replace("Audio:", OLD_I2V_SILENT_RULE + " Audio:", 1)
    out = sb.for_flow_video(old, s)
    assert OLD_I2V_SILENT_RULE not in out and out.count(I2V_SILENT_RULE) == 1
