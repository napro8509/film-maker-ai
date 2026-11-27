"""Flow từ chối vì quá số ảnh tham chiếu: gửi lại chỉ kèm nhân vật chính, không đi viết lại prompt."""
from types import SimpleNamespace

import pytest

from filmmaker.autopilot import _refusal_reason
from filmmaker.gflow import GflowError
from filmmaker.pipeline import REF_LIMIT, Pipeline

REPLY = ("Flow (giao diện chat): Flow không đề nghị tạo video. Trả lời của Flow: I cannot process your request as "
         "specified. The model you requested, Veo 3.1 - Lite, supports a maximum of 3 image references in total.")


def fake_pipeline(tmp_path, errors):
    calls = []
    video = tmp_path / "s01.mp4"
    video.write_bytes(b"x")

    def ui_image_to_video(start, prompt, *, refs=None, **kw):
        calls.append(list(refs or []))
        if errors:
            raise GflowError(errors.pop(0), exit_code=41)
        return video, "m1"

    pipe = SimpleNamespace(
        _send_with_fewer_refs=lambda *a: Pipeline._send_with_fewer_refs(pipe, *a),
        g=SimpleNamespace(dry_run=True, ui_image_to_video=ui_image_to_video),
        sb=SimpleNamespace(for_flow_video=lambda p, s, refs: p,
                           start_frame_char_refs=lambda s, limit: [("Noi", tmp_path / "noi.png")][:limit]),
        log=lambda m: None, ui_refs=lambda refs, project: [f"character:{n}" for n, _ in refs])
    return pipe, calls


def render(pipe, tmp_path, refs):
    scene = SimpleNamespace(id="s01", end_image=None)
    return Pipeline._render_via_ui(pipe, scene, tmp_path / "start.png", "p", {"project": "pr"}, refs=refs)


def test_retries_with_only_the_main_character(tmp_path):
    pipe, calls = fake_pipeline(tmp_path, [REPLY])
    render(pipe, tmp_path, ["character:Fern", "character:Noi"])
    assert calls == [["character:Fern", "character:Noi"], ["character:Noi"]]


@pytest.mark.parametrize("error,refs", [("Flow không đề nghị tạo video: nội dung không phù hợp", ["a", "b"]),
                                        ("Flow lỗi khác", ["a"])])
def test_other_refusals_are_not_resent(tmp_path, error, refs):
    pipe, calls = fake_pipeline(tmp_path, [error])
    with pytest.raises(GflowError):
        render(pipe, tmp_path, refs)
    assert len(calls) == 1


def test_matches_the_real_reply():
    assert REF_LIMIT.search(REPLY)


UNSUPPORTED = ("Flow không đề nghị tạo video. Trả lời của Flow: I cannot complete your request using Veo 3.1 Lite "
               "because the model does not support character entity references.")


def test_unsupported_references_fall_back_to_the_start_picture_alone(tmp_path):
    pipe, calls = fake_pipeline(tmp_path, [UNSUPPORTED])
    render(pipe, tmp_path, ["character:Fern", "character:Noi"])
    assert calls == [["character:Fern", "character:Noi"], []]


def test_each_step_drops_more_then_gives_up(tmp_path):
    pipe, calls = fake_pipeline(tmp_path, [REPLY, REPLY, REPLY])
    with pytest.raises(GflowError):
        render(pipe, tmp_path, ["character:Fern", "character:Noi"])
    assert calls == [["character:Fern", "character:Noi"], ["character:Noi"], []]


@pytest.mark.parametrize("error", [REPLY, UNSUPPORTED])
def test_a_reference_complaint_is_not_a_content_refusal(error):
    assert _refusal_reason(error, 41) is None


MUSE_NO = ("Muse (giao diện chat): Muse báo không tạo được video này (Failed to create…). Muse: I couldn't generate "
           "that video as written.")


def test_muse_refusal_is_resent_without_character_pictures(tmp_path):
    pipe, calls = fake_pipeline(tmp_path, [MUSE_NO])
    render(pipe, tmp_path, ["noi.png", "fern.png"])
    assert calls == [["noi.png", "fern.png"], []]


def test_muse_refusal_without_pictures_goes_to_the_rewrite(tmp_path):
    pipe, calls = fake_pipeline(tmp_path, [MUSE_NO, MUSE_NO])
    with pytest.raises(GflowError):
        render(pipe, tmp_path, ["noi.png"])
    assert calls == [["noi.png"], []]
