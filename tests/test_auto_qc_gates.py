"""Tests for Task #22: ViMax Pre-Render QC + OpenMontage Post-Render QC + Final Assembly Gate."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from filmmaker import ai, autopilot, qc, reviewer
from filmmaker.cli import main
from filmmaker.gflow import Gflow
from filmmaker.pipeline import DONE, Pipeline, RunOptions, State
from filmmaker.storyboard import Storyboard, load_storyboard, save_storyboard

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg/ffprobe")


def _make_png(path: Path, *, black: bool = False, color: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    src = "color=c=black:size=320x180" if black else (
        f"color=c={color}:size=320x180" if color else "testsrc=size=320x180:rate=1"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src, "-frames:v", "1", str(path)],
        check=True,
    )
    return path


def _make_clip(
    path: Path,
    *,
    duration: float = 2.0,
    black: bool = False,
    silent_audio: bool = False,
    clipped_audio: bool = False,
    no_audio: bool = False,
    black_intro: float = 0.0,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if black_intro > 0:
        rest = max(0.2, duration - black_intro)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black:size=320x180:rate=10:duration={black_intro}",
            "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=10:duration={rest}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-map", "2:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ]
        subprocess.run(cmd, check=True)
        return path

    vsrc = f"color=c=black:size=320x180:rate=10:duration={duration}" if black else f"testsrc=size=320x180:rate=10:duration={duration}"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", vsrc]
    if not no_audio:
        asrc = f"anullsrc=r=44100:cl=mono:d={duration}" if silent_audio else f"sine=frequency=440:duration={duration}"
        cmd += ["-f", "lavfi", "-i", asrc]
        if clipped_audio:
            cmd += ["-af", "volume=35dB"]
        cmd += ["-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)
    return path


# --- 1. ViMax Pre-Render Automated QC Gate -----------------------------------


@needs_ffmpeg
def test_vimax_parallel_keyframe_candidates_and_vlm_scoring(tmp_path, monkeypatch):
    sb_file = tmp_path / "storyboard.yaml"
    sb_file.write_text(
        yaml.safe_dump(
            {
                "title": "ViMax Demo",
                "aspect": "16:9",
                "model": "veo-fast",
                "scenes": [
                    {
                        "id": "s01",
                        "mode": "keyframe",
                        "image_prompt": "Chef plating pho in warm kitchen light",
                        "prompt": "Chef garnishes the bowl gently",
                        "duration": 4,
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    sb = load_storyboard(sb_file)
    out = tmp_path / "output" / "storyboard"

    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0
    img_counter = 0
    video_inputs: list[bytes] = []

    class ParallelFakeGflow(Gflow):
        def __init__(self):
            super().__init__(dry_run=False, log=lambda *_: None)

        def create_project(self, title: str) -> str:
            return "proj-vimax"

        def text_to_image(self, prompt, *, out, **kw):
            nonlocal in_flight, max_in_flight, img_counter
            with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
                img_counter += 1
                idx = img_counter
            try:
                time.sleep(0.08)
                p = Path(out)
                if "c1" in p.name or idx == 1:
                    _make_png(p, black=True)
                elif "c2" in p.name or idx == 2:
                    _make_png(p, color="blue")
                else:
                    _make_png(p, color="red")
                return p
            finally:
                with lock:
                    in_flight -= 1

        def image_to_video(self, image, prompt, *, out, **kw):
            video_inputs.append(Path(image).read_bytes())
            _make_clip(Path(out), duration=1.5)
            return Path(out), "vid-1"

    vlm_calls: list[list[str]] = []

    def fake_ask(system, user, schema, *, images=(), **kw):
        assert schema is qc.KeyframeSelection
        vlm_calls.append([lbl for lbl, _ in images])
        return qc.KeyframeSelection(
            best_index=2,
            verdict="PASS",
            score=9,
            reason="Ứng viên #2 đúng bố cục, đủ sáng và không lỗi tay.",
            candidates=[
                qc.KeyframeCandidateVerdict(index=1, verdict="FAIL", score=1, issues=["khung hình đen"]),
                qc.KeyframeCandidateVerdict(index=2, verdict="PASS", score=9, issues=[]),
                qc.KeyframeCandidateVerdict(index=3, verdict="MINOR", score=7, issues=["hơi lệch góc"]),
            ],
        )

    monkeypatch.setattr(ai, "ask", fake_ask)

    pipe = Pipeline(sb, out, ParallelFakeGflow(), sleep=lambda *_: None)
    report = pipe.run(RunOptions(keyframe_candidates=3, pre_qc_gate=True, pause=(0, 0), ai_kw={"backend": "api"}))

    assert report.done == ["s01"] and not report.failed
    assert max_in_flight >= 2, "keyframe candidates must be generated concurrently"
    assert len(vlm_calls) == 1
    cdir = out / "keyframes" / "candidates"
    assert sorted(p.name for p in cdir.glob("s01_c*.png")) == ["s01_c1.png", "s01_c2.png", "s01_c3.png"]

    chosen_kf = out / "keyframes" / "s01.png"
    assert chosen_kf.read_bytes() == (cdir / "s01_c2.png").read_bytes()
    assert len(video_inputs) == 1 and video_inputs[0] == (cdir / "s01_c2.png").read_bytes()

    saved_kf = qc.load_keyframe_qc(out)
    assert saved_kf["s01"]["best_index"] == 2
    assert saved_kf["s01"]["verdict"] == "PASS"
    assert saved_kf["s01"]["vlm_used"] is True


@needs_ffmpeg
def test_vimax_all_candidates_fail_triggers_one_free_remediation_round(tmp_path, monkeypatch):
    sb_file = tmp_path / "storyboard.yaml"
    sb_file.write_text(
        yaml.safe_dump(
            {
                "title": "Remediate",
                "scenes": [{"id": "s01", "mode": "keyframe", "image_prompt": "Blurry dark room", "prompt": "Slow pan", "duration": 4}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    sb = load_storyboard(sb_file)
    out = tmp_path / "output" / "storyboard"
    prompts_sent: list[str] = []

    class RemediateGflow(Gflow):
        def __init__(self):
            super().__init__(dry_run=False, log=lambda *_: None)

        def create_project(self, title: str) -> str:
            return "proj-rem"

        def text_to_image(self, prompt, *, out, **kw):
            prompts_sent.append(prompt)
            return _make_png(Path(out), color="green")

        def image_to_video(self, image, prompt, *, out, **kw):
            _make_clip(Path(out), duration=1.5)
            return Path(out), "vid-rem"

    rounds = 0

    def fake_ask(system, user, schema, *, images=(), **kw):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            return qc.KeyframeSelection(
                best_index=1,
                verdict="FAIL",
                score=3,
                reason="Cả 2 ứng viên đều sai bố cục",
                image_prompt="Warm sunlit living room, sharp 35mm photograph",
                candidates=[
                    qc.KeyframeCandidateVerdict(index=1, verdict="FAIL", score=3, issues=["tối, sai bố cục"]),
                    qc.KeyframeCandidateVerdict(index=2, verdict="FAIL", score=2, issues=["nhòe"]),
                ],
            )
        return qc.KeyframeSelection(
            best_index=2,
            verdict="PASS",
            score=9,
            reason="Prompt mới rõ nét",
            candidates=[
                qc.KeyframeCandidateVerdict(index=1, verdict="MINOR", score=7, issues=[]),
                qc.KeyframeCandidateVerdict(index=2, verdict="PASS", score=9, issues=[]),
            ],
        )

    monkeypatch.setattr(ai, "ask", fake_ask)

    pipe = Pipeline(sb, out, RemediateGflow(), sleep=lambda *_: None)
    report = pipe.run(RunOptions(keyframe_candidates=2, pre_qc_gate=True, strict_pre_qc=True, pause=(0, 0), ai_kw={"backend": "api"}))

    assert report.done == ["s01"]
    assert rounds == 2
    assert len(prompts_sent) == 4  # 2 initial parallel candidates + 2 remediated parallel candidates
    assert any("Warm sunlit living room" in p for p in prompts_sent[2:])


@needs_ffmpeg
def test_strict_pre_qc_gate_blocks_paid_video_api_when_keyframe_still_fails(tmp_path):
    sb_file = tmp_path / "storyboard.yaml"
    sb_file.write_text(
        yaml.safe_dump(
            {
                "title": "Strict Gate",
                "qc_gate": True,
                "scenes": [{"id": "s01", "mode": "keyframe", "image_prompt": "Pitch black", "prompt": "Move", "duration": 4}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    sb = load_storyboard(sb_file)
    out = tmp_path / "output" / "storyboard"
    video_calls = 0

    class BlackKeyframeGflow(Gflow):
        def __init__(self):
            super().__init__(dry_run=False, log=lambda *_: None)

        def create_project(self, title: str) -> str:
            return "proj-gate"

        def text_to_image(self, prompt, *, out, **kw):
            return _make_png(Path(out), black=True)

        def image_to_video(self, image, prompt, *, out, **kw):
            nonlocal video_calls
            video_calls += 1
            _make_clip(Path(out))
            return Path(out), "vid-gate"

    pipe = Pipeline(sb, out, BlackKeyframeGflow(), sleep=lambda *_: None)
    report = pipe.run(RunOptions(keyframe_candidates=2, pre_qc_gate=True, strict_pre_qc=bool(sb.qc_gate), pause=(0, 0)))

    assert video_calls == 0, "paid video API must NEVER be called when strict Pre-QC fails"
    assert "s01" in report.failed and "Pre-QC" in report.failed["s01"]
    st = State(out / "state.json").scene("s01")
    assert st["status"] == "failed"  # not 'submitting', so 0 credits spent


# --- 2. OpenMontage Post-Render Automated QC Gate ----------------------------


@needs_ffmpeg
def test_openmontage_ffprobe_and_four_frame_black_detection(tmp_path):
    sb = Storyboard.model_validate({"title": "OM", "aspect": "16:9", "scenes": [{"id": "s01", "prompt": "walk", "duration": 2}]})
    work = tmp_path / "qc_frames"

    # 1. Corrupted / unreadable file -> ffprobe FAIL
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"not-a-valid-mp4-container")
    probe_bad = qc.ffprobe_clip(corrupt)
    assert probe_bad["valid"] is False
    om_corrupt = qc.openmontage_check(corrupt, work, sb=sb, s=sb.scenes[0])
    assert om_corrupt["verdict"] == "FAIL" and om_corrupt["score"] <= 2
    assert any("ffprobe" in x for x in om_corrupt["issues"])

    # 2. Black-frame clip -> 4 frames extracted, black frames caught -> FAIL
    black_clip = _make_clip(tmp_path / "black.mp4", duration=2.0, black=True)
    om_black = qc.openmontage_check(black_clip, work, sb=sb, s=sb.scenes[0])
    assert len(om_black["frame_files"]) == 4
    assert all((work / fn).is_file() for fn in om_black["frame_files"])
    assert om_black["black_frame_indices"] == [1, 2, 3, 4]
    assert om_black["verdict"] == "FAIL"
    assert any("khung hình đen" in x for x in om_black["issues"])

    # 3. Clean bright clip with short 0.3s fade-in -> 4 frames extracted, PASS
    good_clip = _make_clip(tmp_path / "good.mp4", duration=2.5, black_intro=0.3)
    om_good = qc.openmontage_check(good_clip, work, sb=sb, s=sb.scenes[0])
    assert len(om_good["frame_files"]) == 4
    assert om_good["black_frame_indices"] == []
    assert om_good["verdict"] == "PASS" and om_good["score"] >= 8


@needs_ffmpeg
def test_openmontage_ocr_text_errors_and_audio_dead_air_and_clipping(tmp_path, monkeypatch):
    # Fake tesseract CLI to test OCR text error gate deterministically even on machines without tesseract installed
    fake_tess = tmp_path / "fake_tesseract.py"
    fake_tess.write_text(
        f"#!{sys.executable}\nimport sys\nprint('SUBTITLE WATERMARK 2026')\n",
        encoding="utf-8",
    )
    fake_tess.chmod(0o755)
    monkeypatch.setattr(qc, "tesseract_binary", lambda: str(fake_tess))

    clip = _make_clip(tmp_path / "clean.mp4", duration=2.0)
    sb_no_text = Storyboard.model_validate({"title": "NoText", "format": "clip", "audio": "silent",
                                            "scenes": [{"id": "s01", "prompt": "cat sleeping", "duration": 2}]})
    om_ocr_fail = qc.openmontage_check(clip, tmp_path / "w1", sb=sb_no_text, s=sb_no_text.scenes[0])
    assert om_ocr_fail["verdict"] == "FAIL"
    assert len(om_ocr_fail["ocr_texts"]) == 4
    assert any("chữ" in x for x in om_ocr_fail["issues"])

    # When format explicitly uses onscreen_text, OCR text is expected and NOT flagged as an error
    sb_with_text = Storyboard.model_validate({"title": "WithText", "format": "clip", "audio": "onscreen_text",
                                              "scenes": [{"id": "s01", "prompt": "title card", "duration": 2}]})
    om_ocr_ok = qc.openmontage_check(clip, tmp_path / "w2", sb=sb_with_text, s=sb_with_text.scenes[0])
    assert om_ocr_ok["verdict"] == "PASS"

    # Disable fake OCR to isolate audio level gates
    monkeypatch.setattr(qc, "tesseract_binary", lambda: None)

    # Dialogue scene with dead-air silent audio -> FAIL
    sb_dlg = Storyboard.model_validate({
        "title": "Dlg",
        "scenes": [{"id": "s01", "prompt": "Lan speaks", "duration": 2,
                    "dialogue": [{"character": "Lan", "line": "Xin chào bạn"}]}],
    })
    dead_clip = _make_clip(tmp_path / "dead.mp4", duration=2.0, silent_audio=True)
    om_dead = qc.openmontage_check(dead_clip, tmp_path / "w3", sb=sb_dlg, s=sb_dlg.scenes[0])
    assert om_dead["audio"]["dead_air"] is True
    assert om_dead["verdict"] == "FAIL"
    assert any("mất tiếng" in x or "câm" in x for x in om_dead["issues"])

    # Clipped 0 dBFS audio -> MINOR warning
    clipped = _make_clip(tmp_path / "clipped.mp4", duration=2.0, clipped_audio=True)
    sb_amb = Storyboard.model_validate({"title": "Amb", "scenes": [{"id": "s01", "prompt": "ocean waves", "duration": 2}]})
    om_clip = qc.openmontage_check(clipped, tmp_path / "w4", sb=sb_amb, s=sb_amb.scenes[0])
    assert om_clip["audio"]["clipping"] is True
    assert om_clip["verdict"] == "MINOR"
    assert any("0 dBFS" in x for x in om_clip["issues"])


@needs_ffmpeg
def test_combined_openmontage_and_vlm_check_scene_enforces_hard_gates(tmp_path, monkeypatch):
    monkeypatch.setattr(qc, "tesseract_binary", lambda: None)
    monkeypatch.setattr(qc, "whisper_binary", lambda: None)

    sb = Storyboard.model_validate({
        "title": "Combined",
        "scenes": [{"id": "s01", "mode": "keyframe", "image_prompt": "Sunny park", "prompt": "Dog runs", "duration": 2}],
    })
    kf = _make_png(tmp_path / "s01.png")
    clip = _make_clip(tmp_path / "s01.mp4", duration=2.0)

    seen_images: list[str] = []

    def fake_vlm(system, user, schema, *, images=(), **kw):
        seen_images.extend(lbl for lbl, _ in images)
        # VLM spots garbled text on frame #2 even if it gave MINOR score 7
        return qc.QCAnswer(
            verdict="MINOR",
            score=7,
            issues=[],
            picture="Có dòng chữ lạ ở góc dưới",
            sound="Tốt",
            text_errors=True,
        )

    monkeypatch.setattr(ai, "ask", fake_vlm)
    res = qc.check_scene(sb, sb.scenes[0], clip, kf, tmp_path / "qc_frames", backend="api")

    # 1 keyframe + 4 OpenMontage frames + 1 6-frame strip = 6 images sent to VLM
    assert seen_images[0].startswith("scene s01 keyframe")
    assert sum("OpenMontage QC frame" in lbl for lbl in seen_images) == 4
    assert "6 frames" in seen_images[-1]
    assert res["verdict"] == "FAIL" and res["score"] <= 4
    assert any("lỗi chữ" in x or "phụ đề" in x for x in res["issues"])
    assert len(res["openmontage"]["frame_files"]) == 4


# --- 3. Final Assembly Gate, CLI, Autopilot (Basic Format), and Web UI API ---


@needs_ffmpeg
def test_cli_qc_local_only_and_stitch_excludes_failed_clip(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    out = proj / "output" / "storyboard"
    clips_dir = out / "clips"
    c1 = _make_clip(clips_dir / "s01.mp4", duration=1.5, black=False)
    c2 = _make_clip(clips_dir / "s02.mp4", duration=2.0, black=True)  # bad black-frame clip!
    c3 = _make_clip(clips_dir / "s03.mp4", duration=1.5, black=False)

    sb_path = proj / "storyboard.yaml"
    sb_path.write_text(
        yaml.safe_dump(
            {
                "title": "CLI Gate",
                "aspect": "16:9",
                "scenes": [
                    {"id": "s01", "mode": "t2v", "prompt": "bright morning", "duration": 2},
                    {"id": "s02", "mode": "t2v", "prompt": "midday walk", "duration": 2},
                    {"id": "s03", "mode": "t2v", "prompt": "sunset sky", "duration": 2},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    sb = load_storyboard(sb_path)
    pipe = Pipeline(sb, out, Gflow(dry_run=True))
    for sc, cp in zip(sb.scenes, (c1, c2, c3)):
        pipe.state.update(sc.id, status=DONE, video=str(cp), fingerprint=sb.scene_fingerprint(sc))

    runner = CliRunner()
    r_qc = runner.invoke(main, ["qc", str(sb_path), "--out", str(out), "--local-only"])
    # CLI `qc` exits with code 1 when any clip is flagged FAIL (automated QC gate exit code)
    assert r_qc.exit_code == 1, r_qc.output
    qc_data = qc.load(out)
    assert qc_data["s01"]["verdict"] == "PASS"
    assert qc_data["s02"]["verdict"] == "FAIL"
    assert qc_data["s03"]["verdict"] == "PASS"

    # Stitch automatically gates out s02 (total duration ~3.0s instead of ~5.0s)
    final_gated = out / "final_gated.mp4"
    r_st = runner.invoke(main, ["stitch", str(sb_path), "--out", str(out), "-o", str(final_gated)])
    assert r_st.exit_code == 0, r_st.output
    assert "Cổng QC loại bỏ 1 clip lỗi" in r_st.output and "s02" in r_st.output
    assert qc.ffprobe_clip(final_gated)["duration"] < 3.8

    # Override with --include-qc-failed keeps all 3 clips (~5.0s)
    final_all = out / "final_all.mp4"
    r_all = runner.invoke(main, ["stitch", str(sb_path), "--out", str(out), "--include-qc-failed", "-o", str(final_all)])
    assert r_all.exit_code == 0, r_all.output
    assert qc.ffprobe_clip(final_all)["duration"] > 4.2


@needs_ffmpeg
def test_autopilot_basic_format_runs_vimax_and_openmontage_and_gates_bad_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(qc, "tesseract_binary", lambda: None)
    monkeypatch.setattr(qc, "whisper_binary", lambda: None)

    proj = tmp_path / "basic_film"
    proj.mkdir()
    sb_path = proj / "storyboard.yaml"
    sb_path.write_text(
        yaml.safe_dump(
            {
                "title": "Clip Mới",
                "format": "clip",
                "audio": "silent",
                "aspect": "16:9",
                "model": "veo-lite",
                "thumbnail": {"prompt": "Cute orange cat", "text": "Mèo cam"},
                "scenes": [
                    {"id": "s01", "mode": "keyframe", "image_prompt": "Orange cat stretching", "prompt": "Cat yawns", "duration": 4},
                    {"id": "s02", "mode": "keyframe", "image_prompt": "Orange cat jumping", "prompt": "Cat jumps", "duration": 4},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    class AutoFakeGflow(Gflow):
        def __init__(self):
            super().__init__(dry_run=False, log=lambda *_: None)

        def create_project(self, title: str) -> str:
            return "proj-auto-basic"

        def text_to_image(self, prompt, *, out, **kw):
            return _make_png(Path(out), color="orange")

        def image_to_image(self, prompt, refs, *, out, **kw):
            return _make_png(Path(out), color="orange")

        def image_to_video(self, image, prompt, *, out, **kw):
            # Scene s02 renders as an all-black broken clip!
            is_s02 = Path(out).stem == "s02"
            _make_clip(Path(out), duration=2.0, black=is_s02)
            return Path(out), f"vid-{Path(out).stem}"

    monkeypatch.setattr(reviewer.ai, "pick_backend", lambda *a, **k: "api")

    def fake_ask(system, user, schema, *, images=(), **kw):
        if schema is qc.KeyframeSelection:
            return qc.KeyframeSelection(
                best_index=2,
                verdict="PASS",
                score=9,
                reason="Ứng viên #2 nét nhất",
                candidates=[
                    qc.KeyframeCandidateVerdict(index=1, verdict="PASS", score=8, issues=[]),
                    qc.KeyframeCandidateVerdict(index=2, verdict="PASS", score=9, issues=[]),
                ],
            )
        if schema is qc.QCAnswer:
            sid = user.split("QC scene ")[1].split()[0]
            return qc.QCAnswer(
                verdict="FAIL" if sid == "s02" else "PASS",
                score=2 if sid == "s02" else 9,
                issues=["khung hình đen"] if sid == "s02" else [],
            )
        if schema is reviewer.FilmReview:
            return reviewer.FilmReview(verdict="PASS", score=9, summary="Mượt, đã loại clip hỏng")
        raise AssertionError(f"Unexpected schema: {schema}")

    monkeypatch.setattr(ai, "ask", fake_ask)

    out = proj / "output" / "storyboard"
    logs: list[str] = []
    pilot = autopilot.Autopilot(
        sb_path,
        out,
        AutoFakeGflow(),
        settings=autopilot.AutoSettings(review=True, burn_subtitles=False, pause=(0, 0), api_key="sk-test"),
        log=logs.append,
        sleep=lambda *_: None,
    )
    res = pilot.run()

    assert Path(res["final"]).is_file()
    assert res["qc_failed"] == ["s02"]
    # Verify ViMax ran 2 parallel candidates for each keyframe scene
    assert (out / "keyframes" / "candidates" / "s01_c2.png").is_file()
    assert (out / "keyframes" / "candidates" / "s02_c2.png").is_file()
    # Verify final edit plan excluded FAIL clip s02!
    edit_plan = json.loads((out / "edit.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in edit_plan["clips"]] == ["s01"]


@needs_ffmpeg
def test_web_endpoints_settings_local_qc_keyframe_select_and_assembly_gate(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from filmmaker.web import server

    monkeypatch.setattr(qc, "tesseract_binary", lambda: None)
    monkeypatch.setattr(qc, "whisper_binary", lambda: None)

    ws = tmp_path / "ws"
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(server, "SETTINGS_PATH", settings_path)
    c = TestClient(server.create_app(ws, token="t0k"))
    c.headers["X-Token"] = "t0k"

    # 1. Settings round-trip for ViMax & OpenMontage QC gates
    s0 = c.get("/api/settings").json()
    assert s0["pre_qc"] is True and s0["keyframe_candidates"] == 2 and s0["qc_gate_assembly"] is True
    s1 = c.put("/api/settings", json={"pre_qc": True, "keyframe_candidates": 3, "qc_gate_assembly": True}).json()
    assert s1["keyframe_candidates"] == 3

    # 2. Create project with 2 scenes (s01 clean, s02 black clip) & 2 keyframe candidates for s01
    pid = c.post("/api/projects", json={"title": "Web QC Gate"}).json()["id"]
    pdir = ws / pid
    out = pdir / "output" / "storyboard"
    sb = Storyboard.model_validate({
        "title": "Web QC Gate",
        "format": "clip",
        "audio": "silent",
        "aspect": "16:9",
        "scenes": [
            {"id": "s01", "mode": "keyframe", "image_prompt": "Sunrise", "prompt": "Clouds drift", "duration": 2},
            {"id": "s02", "mode": "keyframe", "image_prompt": "Noon", "prompt": "Birds fly", "duration": 2},
        ],
    })
    save_storyboard(sb, pdir / "storyboard.yaml")

    cdir = out / "keyframes" / "candidates"
    cand1 = _make_png(cdir / "s01_c1.png", color="blue")
    cand2 = _make_png(cdir / "s01_c2.png", color="yellow")
    kf1 = out / "keyframes" / "s01.png"
    kf2 = _make_png(out / "keyframes" / "s02.png", color="green")
    shutil.copy2(cand1, kf1)

    clip1 = _make_clip(out / "clips" / "s01.mp4", duration=1.5, black=False)
    clip2 = _make_clip(out / "clips" / "s02.mp4", duration=1.5, black=True)

    pipe = Pipeline(sb, out, Gflow(dry_run=True))
    pipe.state.update(
        "s01",
        status=DONE,
        video=str(clip1),
        fingerprint=sb.scene_fingerprint(sb.scenes[0]),
        keyframe=str(kf1),
        keyframe_prompt=sb.stored_image_prompt(sb.scenes[0]),
        keyframe_refs=sb.refs_signature(sb.scenes[0]),
    )
    pipe.state.update(
        "s02",
        status=DONE,
        video=str(clip2),
        fingerprint=sb.scene_fingerprint(sb.scenes[1]),
        keyframe=str(kf2),
        keyframe_prompt=sb.stored_image_prompt(sb.scenes[1]),
        keyframe_refs=sb.refs_signature(sb.scenes[1]),
    )

    def wait_job(job_resp):
        for _ in range(1200):
            j = c.get(f"/api/jobs/{job_resp['id']}").json()
            if j["status"] != "running":
                return j
            time.sleep(0.05)
        raise AssertionError("job timed out")

    # 3. Pre-QC keyframes via /qc (keyframes=True, local_only=True) + switch candidate via /select-keyframe
    j_kf = wait_job(c.post(f"/api/projects/{pid}/qc", json={"only": ["s01"], "force": True, "keyframes": True, "local_only": True}).json())
    assert j_kf["status"] == "done", j_kf["lines"]
    d_after_kf = c.get(f"/api/projects/{pid}").json()
    assert len(d_after_kf["scene_state"]["s01"]["keyframe_qc"]["candidates"]) == 2

    d_sw = c.post(f"/api/projects/{pid}/scenes/s01/select-keyframe", json={"index": 2}).json()
    assert d_sw["scene_state"]["s01"]["keyframe_qc"]["best_index"] == 2
    assert kf1.read_bytes() == cand2.read_bytes()

    # 4. Post-QC via /qc (local_only=True — works even without Claude/Gemini API key!)
    j_om = wait_job(c.post(f"/api/projects/{pid}/qc", json={"force": True, "local_only": True}).json())
    assert j_om["status"] == "done", j_om["lines"]
    assert j_om["result"]["qc"] == {"s01": "PASS", "s02": "FAIL"}
    d_om = c.get(f"/api/projects/{pid}").json()
    om_view = d_om["scene_state"]["s02"]["qc"]["openmontage"]
    assert len(om_view["frame_urls"]) == 4
    assert c.get(om_view["frame_urls"][0]).status_code == 200

    # 5. Web stitch excludes FAIL clip s02 automatically!
    j_st = wait_job(c.post(f"/api/projects/{pid}/run", json={"action": "stitch", "skip_qc_failed": True}).json())
    assert j_st["status"] == "done", j_st["lines"]
    assert any("Cổng QC loại bỏ 1 clip lỗi" in ln and "s02" in ln for ln in j_st["lines"])
    assert qc.ffprobe_clip(out / "final.mp4")["duration"] < 2.2
