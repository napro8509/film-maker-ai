"""Who-is-who for dialogue in keyframe scenes: Veo sees a picture, not names, and gave the son's line to the
mother (the only face toward the camera). The cues are added only when sending, never to the fingerprints."""
from filmmaker.gflow import Gflow
from filmmaker.pipeline import Pipeline
from filmmaker.storyboard import NO_TEXT, Storyboard

SB = {"title": "Mother", "model": "veo-lite", "aspect": "9:16", "dialogue_language": "Thai",
      "characters": [{"name": "Malee", "description": "A gentle Thai mother, black hair in a low bun, pink floral blouse."},
                     {"name": "TonTeen", "description": "A Thai teenage boy around 16, gray school shirt, white earbuds."}],
      "scenes": [{"id": "s08", "mode": "keyframe", "image_prompt": "TonTeen and Malee at a school gate",
                  "prompt": "TonTeen steps away from Malee.", "duration": 8,
                  "dialogue": [{"character": "TonTeen", "line": "แม่ไม่ต้องมาส่งผมแล้ว", "delivery": "low"}]},
                 {"id": "s09", "mode": "keyframe", "image_prompt": "Malee alone", "prompt": "Malee waits.", "duration": 6},
                 {"id": "s10", "mode": "t2v", "prompt": "TonTeen talks to Malee.", "duration": 6,
                  "dialogue": [{"character": "TonTeen", "line": "ครับ"}]}]}


def _sb(**kw):
    return Storyboard.model_validate({**SB, **kw})


def test_cues_say_who_speaks_and_who_stays_silent():
    sb = _sb()
    s = sb.scenes[0]
    cues = sb.speaker_cues(s)
    assert "Malee is a gentle Thai mother" in cues and "pink floral blouse" in cues
    assert "TonTeen is a Thai teenage boy" in cues
    assert "Only TonTeen speaks" in cues and "TonTeen turns the face toward the camera" in cues
    assert "Malee keeps the mouth closed" in cues
    sent = sb.for_flow_video(sb.video_prompt(s), s)
    assert cues in sent and sent.index(cues) < sent.index("All spoken dialogue is in Thai") < sent.index(NO_TEXT)
    assert sb.for_flow_video(sent, s) == sent  # sending the sent prompt again adds nothing


def test_no_cues_without_lines_for_t2v_or_a_silent_film():
    sb = _sb()
    assert sb.speaker_cues(sb.scenes[1]) == ""  # nobody speaks
    assert sb.speaker_cues(sb.scenes[2]) == ""  # text-to-video already describes the characters
    quiet = _sb(dialogue_language="none")
    assert quiet.speaker_cues(quiet.scenes[0]) == ""


def test_cues_never_change_the_fingerprint(tmp_path):
    # Rendered clips of every film must not turn "changed" (and be rendered again) because of this.
    sb = _sb()
    before = {s.id: sb.scene_fingerprint(s) for s in sb.scenes}
    assert "Who is who" not in sb.video_prompt(sb.scenes[0])
    assert {s.id: sb.scene_fingerprint(s) for s in sb.scenes} == before


def test_pipeline_sends_the_cues(tmp_path, monkeypatch):
    sb = _sb()
    sent = []
    g = Gflow(dry_run=True, log=lambda *_: None)
    monkeypatch.setattr(g, "image_to_video", lambda img, prompt, **kw: (sent.append(prompt), (tmp_path / "v.mp4", None))[1])
    pipe = Pipeline(sb, tmp_path / "out", g)
    kf = tmp_path / "kf.png"
    kf.write_bytes(b"png")
    monkeypatch.setattr(pipe, "_keyframe", lambda *a, **k: kf)
    from filmmaker.pipeline import RunOptions

    try:
        pipe._render_scene(sb.scenes[0], "proj", RunOptions())
    except Exception:  # what happens after sending (checking the clip) is not under test here
        pass
    assert sent and "Only TonTeen speaks" in sent[0]


def test_a_scene_without_lines_is_told_nobody_speaks():
    # The language rule alone ("characters speak only Thai") made Veo invent lines in silent scenes.
    from filmmaker.storyboard import DIALOGUE_RULE, SILENT_SCENE_RULE

    sb = _sb()
    quiet, talking = sb.scenes[1], sb.scenes[0]
    sent = sb.for_flow_video(sb.video_prompt(quiet), quiet)
    assert SILENT_SCENE_RULE in sent and "speak only Thai" not in sent
    assert sb.for_flow_video(sent, quiet) == sent
    assert DIALOGUE_RULE.format(lang="Thai") in sb.for_flow_video(sb.video_prompt(talking), talking)
    before = sb.scene_fingerprint(quiet)
    assert "Nobody speaks" not in sb.video_prompt(quiet) and sb.scene_fingerprint(quiet) == before
    nolang = _sb(dialogue_language=None)  # no film language set: nothing is added, as before
    assert "Nobody speaks" not in nolang.for_flow_video(nolang.video_prompt(nolang.scenes[1]), nolang.scenes[1])


def test_speech_typed_in_the_prompt_is_not_silenced():
    sb = _sb()
    for prompt in ("Malee: con ăn đi.", "Malee says hello.", "They talk quietly.", "TonTeen (soft): ครับ"):
        assert sb._speech_in_prompt(prompt), prompt
    for prompt in ("TonTeen steps away from Malee.", "Ambient noise: rain. Camera movement: handheld."):
        assert not sb._speech_in_prompt(prompt), prompt
