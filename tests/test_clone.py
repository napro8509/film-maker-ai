from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from filmmaker import clone as cl
from filmmaker.flow_ui import build_v2v_prompt


def test_segments_split_at_cuts_and_long_shots():
    segs = cl.plan_segments(15.23, [3.9])
    assert [(s["start"], s["end"]) for s in segs] == [(0.0, 3.9), (3.9, 9.565), (9.565, 15.23)]
    assert [s["duration"] for s in segs] == [4, 6, 6]
    assert all(s["status"] == "todo" for s in segs)
    assert [s["id"] for s in segs] == ["seg01", "seg02", "seg03"]


def test_segments_merge_short_piece_into_neighbour():
    segs = cl.plan_segments(9.0, [1.0, 5.0])
    assert [(s["start"], s["end"]) for s in segs] == [(0.0, 5.0), (5.0, 9.0)]


def test_segments_never_longer_than_the_model_allows():
    segs = cl.plan_segments(31.0, [], (4, 6, 8))
    assert len(segs) == 4
    assert all(s["length"] <= 8 for s in segs)
    assert segs[-1]["end"] == 31.0


def test_short_clip_asks_for_shortest_length():
    assert cl.plan_segments(2.5, [])[0]["duration"] == 4


def test_fit_filter():
    assert cl.fit_filter(4.0, 4.0) == ("setpts=PTS-STARTPTS", 1.0)
    vf, r = cl.fit_filter(4.0, 3.9)  # slightly long: sped up a hair
    assert r == pytest.approx(0.975) and vf.startswith("setpts=0.975")
    assert cl.fit_filter(10.0, 3.9)[1] == 1.0  # much longer: trimmed, not sped up 2.5x
    assert cl.fit_filter(2.0, 5.0)[1] == pytest.approx(1.25)  # much shorter: slowed at most 25 %


def test_prompt_and_refs(tmp_path):
    data = {"cast": [{"name": "Mai", "replaces": "the woman in the white dress.", "new_look": "blue linen dress",
                      "ref": "character:Mai"},
                     {"name": "Khoa", "replaces": "the man with glasses", "new_look": "", "ref": "cast/Khoa.png"}],
            "extra": "Keep the red boots."}
    text = cl.segment_prompt(data, {"extra": "The man kisses the girl."})
    assert "Replace the woman in the white dress with Mai (blue linen dress)." in text
    assert "Replace the man with glasses with Khoa." in text
    assert text.endswith("Keep the red boots. The man kisses the girl.")
    assert cl.segment_refs(tmp_path, data) == ["character:Mai", str(tmp_path / "cast/Khoa.png")]


def test_v2v_prompt_says_video_first_and_keep_everything():
    p = build_v2v_prompt("Replace A with B.", refs=2, aspect="9:16", model="Omni Flash", duration=6)
    assert p.startswith("Edit the attached video (the first attachment)")
    assert "2 attached image(s) are the NEW characters" in p
    assert "frame by frame" in p and "aspect ratio 9:16, model Omni Flash, 6 seconds" in p
    assert p.endswith("Changes: Replace A with B.")


class FakeFlow:
    dry_run = False

    def __init__(self, fail: set[str] = frozenset()):
        self.calls, self.fail = [], fail

    def create_project(self, title):
        return "proj-1"

    def ui_video_to_video(self, video, prompt, *, refs, aspect, model, duration, project, out, agree_rights=False):
        self.calls.append((Path(video).name, refs, duration))
        if Path(video).stem in self.fail:
            raise RuntimeError("Flow từ chối")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return out, "m1"


def _state(tmp_path, **kw):
    data = {"file": "source/source.mp4", "aspect": "9:16", "model": "omni-flash", "flow_project": None,
            "cast": [{"name": "Mai", "replaces": "the woman", "ref": "character:Mai"}],
            "segments": cl.plan_segments(15.23, [3.9])}
    data.update(kw)
    cl.save(tmp_path, data)
    return data


def test_render_records_status_and_skips_done(tmp_path):
    _state(tmp_path)
    fake = FakeFlow(fail={"seg02"})
    data = cl.render(tmp_path, fake, log=lambda *_: None)
    assert [s["status"] for s in data["segments"]] == ["done", "failed", "done"]
    assert data["flow_project"] == "proj-1"
    assert fake.calls[0] == ("seg01.mp4", ["character:Mai"], 4)
    fake2 = FakeFlow()
    cl.render(tmp_path, fake2, log=lambda *_: None)
    assert [c[0] for c in fake2.calls] == ["seg02.mp4"]  # done ones are never paid for twice


def test_render_refuses_without_cast_ref(tmp_path):
    _state(tmp_path, cast=[{"name": "Mai", "replaces": "the woman", "ref": ""}])
    with pytest.raises(cl.CloneError, match="Mai"):
        cl.render(tmp_path, FakeFlow(), log=lambda *_: None)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_stitch_keeps_original_sound_and_length(tmp_path):
    src = tmp_path / "source" / "source.mp4"
    src.parent.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=180x320:rate=24:duration=7",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=7", "-shortest", "-pix_fmt", "yuv420p",
                    str(src)], check=True)
    segs = cl.plan_segments(7.0, [3.0])
    data = {"file": "source/source.mp4", "duration": 7.0, "width": 180, "height": 320, "fps": 24,
            "has_audio": True, "audio": "original", "segments": segs, "cast": []}
    for s in segs:
        cl.cut_piece(src, s["start"], s["end"], tmp_path / "segments" / f"{s['id']}.mp4")
    # a "render" of seg01 that came back 4 s long at another size
    r = tmp_path / "renders" / "seg01.mp4"
    r.parent.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:size=360x640:rate=30:duration=4",
                    "-pix_fmt", "yuv420p", str(r)], check=True)
    segs[0].update(status="done", render="renders/seg01.mp4")
    cl.save(tmp_path, data)
    with pytest.raises(cl.CloneError, match="seg02"):
        cl.stitch(tmp_path, log=lambda *_: None)
    out = cl.stitch(tmp_path, allow_missing=True, log=lambda *_: None)
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                      str(out)], capture_output=True, text=True).stdout)
    assert abs(float(info["format"]["duration"]) - 7.0) < 0.15
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (180, 320)
    assert any(s["codec_type"] == "audio" for s in info["streams"])


def test_regen_takes_whole_short_clip_in_one_take():
    segs = cl.plan_segments(15.23, [], cl.REGEN_DURATIONS)
    assert [(s["start"], s["end"], s["duration"]) for s in segs] == [(0.0, 15.23, 15)]
    assert len(cl.plan_segments(16.0, [], cl.REGEN_DURATIONS)) == 2


def test_regen_prompts_use_new_cast_and_timing():
    data = {"aspect": "9:16", "extra": "",
            "cast": [{"name": "Mai", "new_look": "blue linen dress.", "replaces": "the woman"}]}
    seg = {"plan": {"setting": "A bright kitchen.", "camera": "handheld medium shot", "sounds": "a giggle at 12 s",
                    "first_frame": "Mai walks in.",
                    "beats": [{"start": 0, "end": 1.5, "action": "Mai walks in smiling."},
                              {"start": 1.5, "end": 3, "action": "Mai sits down."}]}}
    still, motion = cl.regen_prompts(data, seg)
    assert still.startswith("Mai walks in. Setting: A bright kitchen.") and "Mai: blue linen dress." in still
    assert "[0.0-1.5s] Mai walks in smiling. [1.5-3.0s] Mai sits down." in motion
    assert motion.startswith("One continuous shot") and "a giggle at 12 s" in motion
    assert "No speech" in motion
    assert "walks in later): Mai: blue linen dress." in motion  # people absent from the first frame keep their look


def test_render_regen_describes_once_then_keyframe_then_video(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cl, "describe_segment", lambda folder, data, seg: calls.append("describe") or {
        "setting": "kitchen", "camera": "static", "first_frame": "x", "beats": [], "sounds": ""})

    class Regen(FakeFlow):
        def ui_image_to_image(self, prompt, refs, *, out, aspect, model, project):
            calls.append(("still", tuple(refs), model))
            out.write_bytes(b"png")
            return out

        def ui_image_to_video(self, image, prompt, *, refs, aspect, model, duration, project, out):
            calls.append(("video", Path(image).name, refs, model, duration))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"mp4")
            return out, "m"

    _state(tmp_path, mode="regen", model="omni-1.2-flash", segments=cl.plan_segments(15.23, [], cl.REGEN_DURATIONS))
    data = cl.render(tmp_path, Regen(), log=lambda *_: None)
    assert calls == ["describe", ("still", ("character:Mai",), "nano2"),
                     ("video", "seg01.png", None, "omni-1.2-flash", 15)]
    assert data["segments"][0]["status"] == "done" and data["segments"][0]["plan"]["setting"] == "kitchen"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_regen_next_segment_starts_from_previous_last_frame(tmp_path, monkeypatch):
    calls = []

    class Regen(FakeFlow):
        def ui_image_to_image(self, prompt, refs, *, out, aspect, model, project):
            calls.append("still")
            out.write_bytes(b"png")
            return out

        def ui_image_to_video(self, image, prompt, *, refs, aspect, model, duration, project, out):
            calls.append(("video", Path(image).name))
            out.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=blue:size=90x160:rate=24:duration=1",
                            "-pix_fmt", "yuv420p", str(out)], check=True)
            return out, "m"

    plan = {"setting": "s", "camera": "c", "first_frame": "f", "beats": [], "sounds": ""}
    segs = cl.plan_segments(15.23, [], (4, 6, 8))
    for s in segs:
        s["plan"] = plan
    _state(tmp_path, mode="regen", model="veo-lite", segments=segs)
    cl.render(tmp_path, Regen(), log=lambda *_: None)
    assert calls == ["still", ("video", "seg01.png"), ("video", "seg02.png")]
    assert (tmp_path / "keyframes" / "seg02.png").stat().st_size > 100  # a real frame, not the fake still


def test_regen_next_segment_waits_when_previous_failed(tmp_path):
    class Regen(FakeFlow):
        def ui_image_to_image(self, prompt, refs, *, out, aspect, model, project):
            out.write_bytes(b"png")
            return out

        def ui_image_to_video(self, image, prompt, *, refs, aspect, model, duration, project, out):
            self.calls.append(Path(image).name)
            raise RuntimeError("Audio generation failed")

    plan = {"setting": "s", "camera": "c", "first_frame": "f", "beats": [], "sounds": ""}
    segs = cl.plan_segments(15.23, [], (4, 6, 8))
    for s in segs:
        s["plan"] = plan
    _state(tmp_path, mode="regen", model="veo-lite", segments=segs)
    fake = Regen()
    data = cl.render(tmp_path, fake, log=lambda *_: None)
    assert fake.calls == ["seg01.png"]  # seg02 is not paid for with a made-up start
    assert "seg01" in data["segments"][1]["note"] and not (tmp_path / "keyframes" / "seg02.png").exists()


def test_beat_times_stretch_for_a_fixed_longer_clip():
    """Muse always renders 10 s; a 7.615 s piece is sped up x1.31 at stitch, so beats are asked 1.31x later."""
    from filmmaker import clone as cl
    assert cl.beat_stretch({"duration": 8, "length": 7.615}) == 1.0  # Veo: a small gap stays as it was
    f = cl.beat_stretch({"duration": 10, "length": 7.615})
    assert abs(f - 10 / 7.615) < 1e-9
    seg = {"id": "seg01", "start": 0, "end": 7.615, "length": 7.615, "duration": 10,
           "plan": {"beats": [{"start": 3.0, "end": 3.5, "action": "Mai smiles"}]}}
    _, motion = cl.regen_prompts({"aspect": "9:16", "cast": []}, seg)
    assert "[3.9-4.6s] Mai smiles" in motion
    # after stitch speeds the 10 s render up to 7.615 s, that beat is back at 3.0-3.5 s
    _, ratio = cl.fit_filter(10.0, 7.615)
    assert abs(3.94 * ratio - 3.0) < 0.01


# --- Meta Muse: cast pictures, start frames and clips all on muse.ai; no Flow --------------------------------


class MuseFlow(FakeFlow):
    """Records what a Muse clone asks for; create_project must never be called."""

    def create_project(self, title):
        raise AssertionError("a Muse clone needs no Flow project")

    def text_to_image(self, prompt, *, out, aspect, model, project):
        self.calls.append(("picture", model, project, aspect))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")
        return out

    def ui_image_to_image(self, prompt, refs, *, out, aspect, model, project):
        self.calls.append(("still", prompt, tuple(refs), model, project))
        out.write_bytes(b"png")
        return out

    def ui_image_to_video(self, image, prompt, *, refs, aspect, model, duration, project, out):
        self.calls.append(("video", Path(image).name, model, duration, project))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")
        return out, None


def test_muse_durations_and_cli_defaults():
    from filmmaker.cli import _mode_defaults
    assert cl.MODEL_DURATIONS["muse"] == (10,)
    assert _mode_defaults("edit", "muse", "4,6,8,10") == ("regen", "muse", "10")  # Muse cannot edit a video
    assert _mode_defaults("regen", "omni-flash", "4,6,8,10")[1] == cl.REGEN_MODEL
    segs = cl.plan_segments(15.23, [], (10,))
    assert [s["duration"] for s in segs] == [10, 10] and all(s["length"] <= 10 for s in segs)


def test_muse_init_refuses_edit_mode(tmp_path):
    with pytest.raises(cl.CloneError, match="regen"):
        cl.init("x.mp4", tmp_path, model="muse", mode="edit")


def test_muse_cast_is_a_muse_picture_used_as_the_reference(tmp_path):
    _state(tmp_path, mode="regen", model="muse", image_model="muse",
           cast=[{"name": "Mai", "replaces": "the woman", "new_look": "blue dress", "ref": ""}])
    gf = MuseFlow()
    cast = cl.make_cast(tmp_path, gf, log=lambda *_: None)
    assert gf.calls == [("picture", "muse", None, "9:16")]
    assert cast[0]["ref"] == cast[0]["picture"] == "cast/Mai.png"
    assert cl.segment_refs(tmp_path, cl.load(tmp_path)) == [str(tmp_path / "cast" / "Mai.png")]
    assert cl.load(tmp_path)["flow_project"] is None


def test_muse_uses_the_flow_characters_picture_when_switched_from_flow(tmp_path):
    data = _state(tmp_path, model="muse", cast=[{"name": "Mai", "ref": "character:Mai", "picture": "cast/Mai.png"}])
    assert cl.segment_refs(tmp_path, data) == [str(tmp_path / "cast" / "Mai.png")]
    flow = _state(tmp_path, model="veo-lite", cast=[{"name": "Mai", "ref": "character:Mai", "picture": "cast/Mai.png"}])
    assert cl.segment_refs(tmp_path, flow) == ["character:Mai"]


def test_muse_render_regen_still_and_clip_on_muse(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "describe_segment", lambda folder, data, seg: {
        "setting": "kitchen", "camera": "static", "first_frame": "Mai at the table",
        "beats": [{"start": 3.0, "end": 3.5, "action": "Mai smiles"}], "sounds": ""})
    (tmp_path / "cast").mkdir()
    (tmp_path / "cast" / "Mai.png").write_bytes(b"png")
    _state(tmp_path, mode="regen", model="muse", image_model="muse",
           cast=[{"name": "Mai", "replaces": "the woman", "ref": "cast/Mai.png"}],
           segments=cl.plan_segments(7.6, [], (10,)))
    gf = MuseFlow()
    data = cl.render(tmp_path, gf, log=lambda *_: None)
    still, video = gf.calls
    assert still[0] == "still" and still[2] == (str(tmp_path / "cast" / "Mai.png"),) and still[3] == "muse"
    assert still[1].startswith("Reference images, in the order attached: Mai") and still[4] == ""
    assert video == ("video", "seg01.png", "muse", 10, "")
    assert data["segments"][0]["status"] == "done" and data["flow_project"] is None


def test_muse_render_refuses_edit_mode(tmp_path):
    _state(tmp_path, mode="edit", model="muse")
    with pytest.raises(cl.CloneError, match="regen"):
        cl.render(tmp_path, MuseFlow(), log=lambda *_: None)
