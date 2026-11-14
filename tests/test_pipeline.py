import json
import shutil
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from filmmaker.cli import main
from filmmaker.gflow import Gflow, _extract_json
from filmmaker.pipeline import Pipeline, RunOptions
from filmmaker.storyboard import load_storyboard

FAKE = Path(__file__).parent / "fake_gflow.py"
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

STORY = """
title: Test film
aspect: "16:9"
model: veo-fast
style: "cinematic"
characters:
  - name: Lan
    description: "short black hair, red scarf"
scenes:
  - id: s01
    mode: keyframe
    image_prompt: "Lan at a cafe"
    prompt: "slow push-in"
    narration: "Xin chào"
  - id: s02
    mode: t2v
    prompt: "Lan walks in the rain"
  - id: s03
    mode: i2v
    image: start.png
    prompt: "pan left"
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "start.png").write_bytes(b"png")
    sb_path = tmp_path / "story.yaml"
    sb_path.write_text(STORY)
    log = tmp_path / "calls.log"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(log))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)

    def calls():
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    return sb_path, calls


def make_pipe(sb_path, out):
    return Pipeline(load_storyboard(sb_path), out, Gflow(log=lambda *_: None), log=lambda *_: None, sleep=lambda *_: None)


def test_extract_json_ignores_noise():
    assert _extract_json('banner {not json\n{"a": 1}\nlog {"status": "ok"}') == {"status": "ok"}


def test_prompts_include_character_and_style(env):
    sb = load_storyboard(env[0])
    t2v = sb.video_prompt(sb.scenes[1])
    assert "red scarf" in t2v and "cinematic" in t2v
    assert "red scarf" in sb.image_prompt(sb.scenes[0])
    assert sb.video_prompt(sb.scenes[0]) == "slow push-in"  # look comes from the keyframe


def test_full_run_then_resume_skips_everything(env, tmp_path):
    sb_path, calls = env
    out = tmp_path / "out"
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert report.ok and report.done == ["s01", "s02", "s03"]
    cmds = [c[:2] for c in calls()]
    assert cmds == [["project", "create"], ["image", "t2i"], ["video", "i2v"], ["video", "t2v"], ["video", "i2v"]]
    assert all("--project" in c and "proj-123" in c for c in calls()[1:])

    n = len(calls())
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert report.skipped == ["s01", "s02", "s03"] and len(calls()) == n


def test_edit_scene_regenerates_only_that_scene(env, tmp_path):
    sb_path, calls = env
    out = tmp_path / "out"
    make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    sb_path.write_text(STORY.replace("Lan walks in the rain", "Lan runs in the rain"))
    n = len(calls())
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert report.done == ["s02"]
    assert [c[:2] for c in calls()[n:]] == [["video", "t2v"]]


def test_retryable_error_is_retried(env, tmp_path, monkeypatch):
    sb_path, calls = env
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "s02.mp4:10:true:1")
    report = make_pipe(sb_path, tmp_path / "out").run(RunOptions(pause=(0, 0)))
    assert report.ok
    assert sum(1 for c in calls() if c[:2] == ["video", "t2v"]) == 2


def test_no_credits_aborts_run(env, tmp_path, monkeypatch):
    sb_path, calls = env
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "s02.mp4:37:false")
    report = make_pipe(sb_path, tmp_path / "out").run(RunOptions(pause=(0, 0)))
    assert report.done == ["s01"] and "s02" in report.failed and report.aborted
    assert not any("s03.mp4" in " ".join(c) for c in calls())


def test_interrupted_render_is_not_resubmitted(env, tmp_path):
    sb_path, calls = env
    out = tmp_path / "out"
    pipe = make_pipe(sb_path, out)
    pipe.state.data["project_id"] = "proj-123"
    pipe.state.update("s02", status="submitting")
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert "s02" in report.failed and set(report.done) == {"s01", "s03"}
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0), retry_unknown=True))
    assert report.done == ["s02"]


def test_cli_make_produces_final_film(env, tmp_path, monkeypatch):
    sb_path, calls = env
    monkeypatch.setenv("FAKE_GFLOW_NO_AUDIO", "s03.mp4")  # one clip without audio
    res = CliRunner().invoke(main, ["make", str(sb_path), "--pause", "0", "--burn-subtitles"])
    assert res.exit_code == 0, res.output
    final = sb_path.parent / "output" / "story" / "final.mp4"
    assert final.is_file()
    assert "Xin chào" in final.with_suffix(".srt").read_text()
    res = CliRunner().invoke(main, ["status", str(sb_path)])
    assert res.output.count("✔ xong") == 3


def test_cli_dry_run_spends_nothing(env, tmp_path):
    sb_path, calls = env
    res = CliRunner().invoke(main, ["run", str(sb_path), "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "gflow" in res.output or "fake_gflow" in res.output
    assert calls() == []


def test_i2v_not_ported_stops_by_default(env, tmp_path, monkeypatch):
    sb_path, calls = env
    # Agent-only flow.google.com accounts refuse i2v before submit (exit 36): no credits spent.
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    out = tmp_path / "out"
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert report.done == [] and "s01" in report.failed and report.aborted
    assert "fake video i2v" not in report.failed["s01"] and "Chữ → Video" in report.failed["s01"]
    assert not any(c[:2] == ["video", "t2v"] for c in calls())  # nothing paid was sent
    pipe = make_pipe(sb_path, out)
    assert pipe.i2v_unsupported and "fake video i2v" in pipe.state.data["i2v_error"]
    assert pipe.state.scene("s01").get("status") != "submitting"  # refused before submit: safe to retry
    # Still not switching on its own next time: it tries i2v again (refused for free) and stops.
    n = len(calls())
    pipe.run(RunOptions(pause=(0, 0)))
    assert [c[:2] for c in calls()[n:]] == [["video", "i2v"]]


def test_i2v_not_ported_falls_back_when_enabled(env, tmp_path, monkeypatch):
    sb_path, calls = env
    sb_path.write_text(STORY.replace('style: "cinematic"', 'style: "cinematic"\nfallback_t2v: true'))
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    out = tmp_path / "out"
    report = make_pipe(sb_path, out).run(RunOptions())
    assert report.done == ["s01", "s02"]  # keyframe scene rendered as t2v
    assert "s03" in report.failed and "Chữ → Video" in report.failed["s03"]  # user's own image cannot work
    assert report.aborted is None
    t2v = [c for c in calls() if c[:2] == ["video", "t2v"]]
    assert any("Lan at a cafe" in c[2] and "slow push-in" in c[2] and "red scarf" in c[2] for c in t2v)
    pipe = make_pipe(sb_path, out)
    assert pipe.i2v_unsupported and pipe.state.scene("s01")["rendered_as"] == "t2v"
    # Next time the flag is remembered: no keyframe image, straight to t2v.
    n = len(calls())
    pipe.run(RunOptions(only={"s01"}, force={"s01"}))
    assert [c[:2] for c in calls()[n:]] == [["video", "t2v"]]


REF_STORY = STORY.replace(
    'description: "short black hair, red scarf"',
    'description: "short black hair, red scarf"\n    image: lan.png')


def _ref_env(env, tmp_path):
    sb_path, calls = env
    (tmp_path / "lan.png").write_bytes(b"lan-v1")
    sb_path.write_text(REF_STORY)
    return sb_path, calls


def test_character_image_is_sent_as_reference(env, tmp_path):
    sb_path, calls = _ref_env(env, tmp_path)
    out = tmp_path / "out"
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert report.ok and report.done == ["s01", "s02", "s03"]
    kinds = [c[:2] for c in calls()]
    assert kinds == [["project", "create"], ["image", "i2i"], ["video", "i2v"], ["video", "r2v"], ["video", "i2v"]]
    ref = str(tmp_path / "lan.png")
    i2i, r2v = calls()[1], calls()[3]
    assert i2i[i2i.index("--ref") + 1] == ref and "red scarf" in i2i[2]
    assert r2v[r2v.index("--ref") + 1] == ref and "-o" in r2v
    assert "--ref" not in calls()[4]  # the user's own image (i2v) gets no references
    assert make_pipe(sb_path, out).state.scene("s02")["rendered_as"] == "r2v"

    # Nothing changed: resume does nothing.
    n = len(calls())
    assert make_pipe(sb_path, out).run(RunOptions(pause=(0, 0))).skipped == ["s01", "s02", "s03"]
    assert len(calls()) == n

    # A new reference image makes the scenes with Lan stale (not s03).
    (tmp_path / "lan.png").write_bytes(b"lan-v2")
    pipe = make_pipe(sb_path, out)
    assert [pipe.scene_status(s) for s in pipe.sb.scenes] == ["changed", "changed", "done"]
    assert not pipe.has_keyframe(pipe.sb.scenes[0])


def test_character_image_already_on_flow_is_reused_not_uploaded_again(env, tmp_path, monkeypatch):
    sb_path, calls = _ref_env(env, tmp_path)
    # gflow's catalog: the portrait was generated in this Flow project (same picture, saved as .jpg).
    (tmp_path / "lan.jpg").write_bytes(b"lan-v1")
    (tmp_path / "other.jpg").write_bytes(b"lan-v1")
    cat = tmp_path / "catalog.jsonl"
    cat.write_text("\n".join(json.dumps(r) for r in [
        {"media_id": "other-project-id", "project_id": "elsewhere", "local_path": str(tmp_path / "other.jpg")},
        {"media_id": "1ab3d9ff-4163-46b2-b368-c003fa2d36df", "project_id": "proj-123",
         "local_path": str(tmp_path / "lan.jpg")},
    ]))
    monkeypatch.setenv("FAKE_GFLOW_CATALOG", str(cat))
    report = make_pipe(sb_path, tmp_path / "out").run(RunOptions(pause=(0, 0), keyframes_only=True))
    assert report.done == ["s01"]
    i2i = calls()[1]
    assert i2i[:2] == ["image", "i2i"] and i2i[i2i.index("--ref") + 1] == "1ab3d9ff-4163-46b2-b368-c003fa2d36df"

    # A portrait Flow doesn't have yet is still sent as a file.
    (tmp_path / "lan.png").write_bytes(b"lan-v2")
    make_pipe(sb_path, tmp_path / "out").run(RunOptions(pause=(0, 0), keyframes_only=True))
    i2i = calls()[-1]
    assert i2i[i2i.index("--ref") + 1] == str(tmp_path / "lan.png")


def test_media_id_ref_not_ported_is_resent_as_local_file(env, tmp_path, monkeypatch):
    sb_path, calls = _ref_env(env, tmp_path)
    (tmp_path / "lan.jpg").write_bytes(b"lan-v1")
    cat = tmp_path / "catalog.jsonl"
    cat.write_text(json.dumps({"media_id": "1ab3d9ff-4163-46b2-b368-c003fa2d36df", "project_id": "proj-123",
                               "local_path": str(tmp_path / "lan.jpg")}))
    monkeypatch.setenv("FAKE_GFLOW_CATALOG", str(cat))
    # flow.google.com: a media-id reference is not ported (exit 36), a local file works.
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "1ab3d9ff:36:false")
    report = make_pipe(sb_path, tmp_path / "out").run(RunOptions(pause=(0, 0), keyframes_only=True))
    assert report.done == ["s01"]
    kinds = [c[:2] for c in calls()][1:]
    assert kinds == [["image", "i2i"], ["image", "i2i"]]  # no text-only fallback
    i2i = calls()[-1]
    assert i2i[i2i.index("--ref") + 1] == str(tmp_path / "lan.png")


def test_storyboards_without_reference_images_keep_fingerprints(env):
    sb = load_storyboard(env[0])
    assert all(sb.scene_refs(s) == [] for s in sb.scenes)
    sb.use_character_refs = False
    before = [sb.scene_fingerprint(s) for s in sb.scenes]
    sb.use_character_refs = True
    assert [sb.scene_fingerprint(s) for s in sb.scenes] == before


def test_reference_toggle_and_missing_file(env, tmp_path):
    sb_path, calls = _ref_env(env, tmp_path)
    sb = load_storyboard(sb_path)
    assert [n for n, _ in sb.scene_refs(sb.scenes[1])] == ["Lan"]
    sb.use_character_refs = False
    assert sb.scene_refs(sb.scenes[1]) == []
    (tmp_path / "lan.png").unlink()
    assert load_storyboard(sb_path).scene_refs(sb.scenes[1]) == []


def test_r2v_not_ported_falls_back_to_t2v(env, tmp_path, monkeypatch):
    sb_path, calls = _ref_env(env, tmp_path)
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video r2v:36:false")
    out = tmp_path / "out"
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert report.ok and report.done == ["s01", "s02", "s03"]
    assert [c[:2] for c in calls()].count(["video", "t2v"]) == 1
    pipe = make_pipe(sb_path, out)
    assert pipe.state.data["r2v_unsupported"] and pipe.state.scene("s02")["rendered_as"] == "t2v"


def test_i2i_failure_falls_back_to_text_image(env, tmp_path, monkeypatch):
    sb_path, calls = _ref_env(env, tmp_path)
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "image i2i:2:false")
    report = make_pipe(sb_path, tmp_path / "out").run(RunOptions(pause=(0, 0), keyframes_only=True))
    assert report.done == ["s01"]
    assert [c[:2] for c in calls()][1:] == [["image", "i2i"], ["image", "t2i"]]


def test_keyframe_fallback_to_text_video_uses_references(env, tmp_path, monkeypatch):
    sb_path, calls = _ref_env(env, tmp_path)
    sb_path.write_text(REF_STORY.replace('style: "cinematic"', 'style: "cinematic"\nfallback_t2v: true'))
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    out = tmp_path / "out"
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert report.done == ["s01", "s02"]
    assert make_pipe(sb_path, out).state.scene("s01")["rendered_as"] == "r2v"


def test_generate_portraits(env, tmp_path):
    sb_path, calls = env
    pipe = make_pipe(sb_path, tmp_path / "out")
    dest = tmp_path / "assets" / "characters" / "lan.png"
    report = pipe.generate_portraits([(pipe.sb.characters[0], dest)], RunOptions(pause=(0, 0)))
    assert report.done == ["Lan"] and dest.is_file()
    t2i = calls()[1]
    assert t2i[:2] == ["image", "t2i"] and "red scarf" in t2i[2] and "cinematic" in t2i[2]


FAKE_UI = Path(__file__).parent / "fake_flow_ui.py"


@pytest.fixture
def ui(env, tmp_path, monkeypatch):
    log = tmp_path / "ui.log"
    monkeypatch.setenv("FLOW_UI_CMD", f"{sys.executable} {FAKE_UI}")
    monkeypatch.setenv("FAKE_FLOW_UI_LOG", str(log))
    monkeypatch.delenv("FAKE_FLOW_UI_FAIL", raising=False)
    return lambda: [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


def test_i2v_refused_by_gflow_goes_through_flow_chat_page(env, ui, tmp_path, monkeypatch):
    sb_path, calls = env
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    out = tmp_path / "out"
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert report.ok and report.done == ["s01", "s02", "s03"]
    assert not any(c[:2] == ["video", "t2v"] and "s01.mp4" in " ".join(c) for c in calls())  # no t2v fallback
    ui_calls = ui()
    assert len(ui_calls) == 2 and all(c[0] == "i2v" for c in ui_calls)
    first = ui_calls[0]
    assert first[first.index("--image") + 1].endswith("keyframes/s01.png")
    assert first[first.index("--prompt") + 1].startswith("slow push-in. No captions, no subtitles")
    assert first[first.index("--project") + 1] == "proj-123"
    assert first[first.index("--model") + 1] == "Veo 3.1 Fast"
    assert first[first.index("--window") + 1] == "hidden"  # browser kept out of sight by default
    assert ui_calls[1][ui_calls[1].index("--image") + 1].endswith("start.png")
    pipe = make_pipe(sb_path, out)
    assert pipe.state.scene("s01")["rendered_as"] == "flow-ui" and pipe.state.scene("s03")["status"] == "done"
    # Remembered: next time straight to the chat page, gflow i2v is not asked again.
    n = len(calls())
    pipe.run(RunOptions(only={"s01"}, force={"s01"}, pause=(0, 0)))
    assert [c[:2] for c in calls()[n:]] == [] and len(ui()) == 3


def test_i2i_refused_by_gflow_goes_through_flow_chat_page(env, ui, tmp_path, monkeypatch):
    sb_path, calls = _ref_env(env, tmp_path)
    # Agent-only Flow composer: gflow refuses i2i before submitting, even with local files (#799).
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "image i2i:36:false")
    out = tmp_path / "out"
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0), keyframes_only=True))
    assert report.done == ["s01"]
    assert ["image", "t2i"] not in [c[:2] for c in calls()]  # no text-only fallback
    (first,) = ui()
    assert first[0] == "i2i" and first[first.index("--ref") + 1] == str(tmp_path / "lan.png")
    assert first[first.index("--project") + 1] == "proj-123" and "red scarf" in first[first.index("--prompt") + 1]
    pipe = make_pipe(sb_path, out)
    assert pipe.state.data["i2i_ui"] and pipe.keyframe_path(pipe.sb.scenes[0]).stat().st_size > 0
    # Remembered: next time straight to the chat page, gflow i2i is not asked again.
    n = len(calls())
    pipe.run(RunOptions(pause=(0, 0), keyframes_only=True, force_keyframes={"s01"}))
    assert ["image", "i2i"] not in [c[:2] for c in calls()[n:]] and len(ui()) == 2


def test_i2i_chat_page_failure_falls_back_to_text_image(env, ui, tmp_path, monkeypatch):
    sb_path, calls = _ref_env(env, tmp_path)
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "image i2i:36:false")
    monkeypatch.setenv("FAKE_FLOW_UI_FAIL", "23:false")
    report = make_pipe(sb_path, tmp_path / "out").run(RunOptions(pause=(0, 0), keyframes_only=True))
    assert report.done == ["s01"] and len(ui()) == 1
    assert [c[:2] for c in calls()][-1] == ["image", "t2i"]


def test_flow_chat_failure_after_approve_is_not_resent(env, ui, tmp_path, monkeypatch):
    sb_path, calls = env
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    monkeypatch.setenv("FAKE_FLOW_UI_FAIL", "9:true")  # timed out after Approve: may be billed
    out = tmp_path / "out"
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0), only={"s01"}))
    assert "s01" in report.failed
    pipe = make_pipe(sb_path, out)
    assert pipe.state.scene("s01")["status"] == "submitting"
    monkeypatch.delenv("FAKE_FLOW_UI_FAIL")
    n = len(ui())
    report = pipe.run(RunOptions(pause=(0, 0), only={"s01"}))
    assert "s01" in report.failed and len(ui()) == n  # blocked until the user confirms


def test_flow_chat_ui_drift_stops_the_run(env, ui, tmp_path, monkeypatch):
    sb_path, calls = env
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    monkeypatch.setenv("FAKE_FLOW_UI_FAIL", "23:false")
    out = tmp_path / "out"
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert report.aborted and report.done == [] and "fake ui 23" in report.failed["s01"]
    assert make_pipe(sb_path, out).state.scene("s01")["status"] == "failed"  # nothing was billed: retry is safe


def test_flow_chat_disabled_keeps_stopping(env, ui, tmp_path, monkeypatch):
    sb_path, calls = env
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    pipe = make_pipe(sb_path, tmp_path / "out")
    pipe.g.ui_enabled = False
    report = pipe.run(RunOptions(pause=(0, 0)))
    assert report.aborted and ui() == []


def test_flow_ui_prompt_and_bad_input():
    from filmmaker import flow_ui

    p = flow_ui.build_prompt("she smiles", refs=1, aspect="9:16", model="Veo 3.1 Fast", duration=8)
    assert "exactly 1 video" in p and "begin the video on that picture" in p and "9:16" in p and "Veo 3.1 Fast" in p
    assert "8 seconds" in p and p.endswith("Motion and action: she smiles") and "character references" in p
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = flow_ui.main(["i2v", "--project", "p", "--image", "/nope.png", "--prompt", "x", "-o", "/tmp/x.mp4",
                             "--profile-dir", "/tmp"])
    res = json.loads(buf.getvalue())
    assert code == 2 and res["status"] == "fail" and res["error"]["spent"] is False


def test_gflow_python_is_found_from_the_launcher(tmp_path, monkeypatch):
    from filmmaker import gflow as gmod

    venv = tmp_path / "tools" / "gflow-cli" / "bin"
    venv.mkdir(parents=True)
    py = venv / "python"
    py.write_text("")
    launcher = venv / "gflow"
    launcher.write_text(f"#!{py}\nimport sys\n")
    launcher.chmod(0o755)
    monkeypatch.setenv("PATH", f"{venv}:{__import__('os').environ['PATH']}")
    assert gmod._gflow_python("gflow") == str(py)
    prof = tmp_path / "home" / "Library" / "Application Support" / "gflow-cli" / "profile_me"
    prof.mkdir(parents=True)
    monkeypatch.setattr(gmod.Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("FLOW_UI_PROFILE", raising=False)
    assert gmod.Gflow(binary="gflow").ui_profile_dir() == prof


def test_flow_chat_clip_of_another_scene_is_refused(env, ui, tmp_path, monkeypatch):
    sb_path, calls = env
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    monkeypatch.setenv("FAKE_FLOW_UI_MEDIA", "same-uuid")  # driver returns the same Flow video twice
    out = tmp_path / "out"
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert report.done == ["s01", "s02"] and "trùng với cảnh s01" in report.failed["s03"]
    assert make_pipe(sb_path, out).state.scene("s03")["status"] == "submitting"  # check Flow before resending


def test_no_text_rule_is_sent_but_does_not_change_fingerprints(env, tmp_path):
    from filmmaker.storyboard import NO_TEXT

    sb_path, calls = env
    out = tmp_path / "out"
    make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    sent = [c for c in calls() if c[0] in ("image", "video")]
    assert sent and all(any(NO_TEXT in a for a in c) for c in sent)  # every image / video prompt carries the rule
    sb = load_storyboard(sb_path)
    assert NO_TEXT not in sb.video_prompt(sb.scenes[1])
    sb_path.write_text(STORY.replace('style: "cinematic"', 'style: "cinematic"\nno_text: false'))
    pipe = make_pipe(sb_path, out)
    assert [pipe.scene_status(s) for s in pipe.sb.scenes] == ["done", "done", "done"]  # nothing to redo
    assert pipe.sb.for_flow("x") == "x"


def test_interrupted_render_adopts_clip_downloaded_by_hand(env, tmp_path, fake_clip_check):
    from filmmaker import manual_clip

    sb_path, calls = env
    out = tmp_path / "out"
    pipe = make_pipe(sb_path, out)
    pipe.state.data["project_id"] = "proj-123"
    pipe.state.update("s02", status="submitting", submitted_at=manual_clip.stamp_now())  # clip written after it
    clip = pipe.clip_path(pipe.sb.scenes[1])
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"\x00" * 20_000)  # the user saved the clip from Flow here
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0)))
    assert "s02" in report.skipped and "s02" not in report.failed
    assert not any("s02.mp4" in " ".join(c) for c in calls())  # nothing resubmitted
    assert make_pipe(sb_path, out).is_done(pipe.sb.scenes[1])


def test_cli_characters_makes_portraits_and_saves_paths(env):
    sb_path, calls = env
    res = CliRunner().invoke(main, ["characters", str(sb_path), "--pause", "0"])
    assert res.exit_code == 0, res.output
    img = sb_path.parent / "assets" / "characters" / "lan.png"
    assert img.is_file()
    assert load_storyboard(sb_path).characters[0].image == str(img.resolve())
    assert sum(c[:2] == ["image", "t2i"] for c in calls()) == 1
    # Second run: everyone already has a picture, nothing is sent.
    res = CliRunner().invoke(main, ["characters", str(sb_path), "--pause", "0"])
    assert res.exit_code == 0 and "đã có ảnh" in res.output
    assert sum(c[:2] == ["image", "t2i"] for c in calls()) == 1


def test_cli_force_video_keeps_approved_keyframe(env):
    sb_path, calls = env
    assert CliRunner().invoke(main, ["run", str(sb_path), "--pause", "0"]).exit_code == 0
    images = sum(c[0] == "image" for c in calls())
    res = CliRunner().invoke(main, ["run", str(sb_path), "--pause", "0", "--force", "s01"])
    assert res.exit_code == 0, res.output
    assert sum(c[0] == "image" for c in calls()) == images  # keyframe reused
    res = CliRunner().invoke(main, ["run", str(sb_path), "--pause", "0", "--force", "s01", "--keyframes-only"])
    assert res.exit_code == 0, res.output
    assert sum(c[0] == "image" for c in calls()) == images + 1


def test_poll_timeout_after_submit_is_not_resent(env, monkeypatch):
    """gflow exit 9 'Submit was already clicked ... may have billed': the next run must not resend it."""
    sb_path, calls = env
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "s02.mp4:9:false")
    monkeypatch.setenv("FAKE_GFLOW_HINT", "Submit was already clicked, so Flow may have made and billed this generation.")
    res = CliRunner().invoke(main, ["run", str(sb_path), "--pause", "0"])
    assert res.exit_code == 1
    monkeypatch.delenv("FAKE_GFLOW_FAIL")
    sent = sum(c[0] == "video" and "s02.mp4" in " ".join(c) for c in calls())
    res = CliRunner().invoke(main, ["run", str(sb_path), "--pause", "0"])
    assert "bị ngắt" in res.output
    assert sum(c[0] == "video" and "s02.mp4" in " ".join(c) for c in calls()) == sent


# --- Flow characters (left panel Characters -> New character) ---------------------------------------------------

def _flow_char_portrait(sb_path, tmp_path, out):
    """Lan's picture made as a Flow character, saved over the storyboard's lan.png."""
    pipe = make_pipe(sb_path, out)
    report = pipe.generate_portraits([(pipe.sb.characters[0], tmp_path / "lan.png")], RunOptions(pause=(0, 0)))
    assert report.done == ["Lan"]
    return pipe


def test_portrait_is_made_as_a_flow_character(env, ui, tmp_path):
    sb_path, calls = _ref_env(env, tmp_path)
    out = tmp_path / "out"
    pipe = _flow_char_portrait(sb_path, tmp_path, out)
    assert ["image", "t2i"] not in [c[:2] for c in calls()]  # no plain prompt picture
    (char,) = ui()
    assert char[0] == "char" and char[char.index("--name") + 1] == "Lan"
    assert char[char.index("--project") + 1] == "proj-123" and "red scarf" in char[char.index("--prompt") + 1]
    assert "--retire-id" not in char
    fc = make_pipe(sb_path, out).state.data["flow_characters"]["Lan"]
    assert fc["entity_id"] == "ent-lan" and fc["flow_name"] == "Lan" and fc["project"] == "proj-123"
    assert fc["sha"] == pipe._sha(tmp_path / "lan.png") and (tmp_path / "lan.png").stat().st_size > 100


def test_keyframe_attaches_the_flow_character_by_name(env, ui, tmp_path, monkeypatch):
    sb_path, calls = _ref_env(env, tmp_path)
    out = tmp_path / "out"
    _flow_char_portrait(sb_path, tmp_path, out)
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "image i2i:36:false")  # agent-only composer: keyframes via the chat page
    report = make_pipe(sb_path, out).run(RunOptions(pause=(0, 0), keyframes_only=True))
    assert report.done == ["s01"]
    i2i = ui()[-1]
    assert i2i[0] == "i2i" and i2i[i2i.index("--ref") + 1] == "character:Lan"  # not an upload of lan.png


def test_changed_picture_is_uploaded_not_the_old_flow_character(env, ui, tmp_path, monkeypatch):
    sb_path, calls = _ref_env(env, tmp_path)
    out = tmp_path / "out"
    _flow_char_portrait(sb_path, tmp_path, out)
    (tmp_path / "lan.png").write_bytes(b"a picture the user put there by hand")
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "image i2i:36:false")
    make_pipe(sb_path, out).run(RunOptions(pause=(0, 0), keyframes_only=True))
    i2i = ui()[-1]
    assert i2i[i2i.index("--ref") + 1] == str(tmp_path / "lan.png")


def test_remade_flow_character_renames_the_old_one(env, ui, tmp_path):
    sb_path, calls = _ref_env(env, tmp_path)
    out = tmp_path / "out"
    _flow_char_portrait(sb_path, tmp_path, out)
    _flow_char_portrait(sb_path, tmp_path, out)
    second = ui()[-1]
    assert second[second.index("--retire-id") + 1] == "ent-lan"
    assert second[second.index("--retire-name") + 1].startswith("Lan (cũ ")
    assert second[second.index("--name") + 1] == "Lan"


def test_flow_character_failure_falls_back_to_a_prompt_picture(env, ui, tmp_path, monkeypatch):
    sb_path, calls = _ref_env(env, tmp_path)
    monkeypatch.setenv("FAKE_FLOW_UI_FAIL", "23:false")  # the Characters page changed
    pipe = make_pipe(sb_path, tmp_path / "out")
    chars = [(c, tmp_path / f"c{i}.png") for i, c in enumerate(pipe.sb.characters)]
    report = pipe.generate_portraits(chars, RunOptions(pause=(0, 0)))
    assert report.done == [c.name for c in pipe.sb.characters]
    assert [c[:2] for c in calls()].count(["image", "t2i"]) == len(chars)
    assert len(ui()) == 1  # tried once, then the rest of the run went straight to t2i
    assert "flow_characters" not in pipe.state.data


def test_flow_characters_can_be_turned_off(env, ui, tmp_path):
    sb_path, calls = _ref_env(env, tmp_path)
    pipe = Pipeline(load_storyboard(sb_path), tmp_path / "out", Gflow(log=lambda *_: None, flow_characters=False),
                    log=lambda *_: None, sleep=lambda *_: None)
    pipe.generate_portraits([(pipe.sb.characters[0], tmp_path / "x.png")], RunOptions(pause=(0, 0)))
    assert ui() == [] and ["image", "t2i"] in [c[:2] for c in calls()]


def test_flow_ui_split_refs():
    from filmmaker.flow_ui import character_ref, split_refs

    assert split_refs([Path("/a/b.png"), character_ref("Bà Mai"), "c.png"]) == [
        ("file", "/a/b.png"), ("character", "Bà Mai"), ("file", "c.png")]
