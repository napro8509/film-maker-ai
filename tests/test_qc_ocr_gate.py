"""OCR is a hint, never a FAIL by itself; the assembly gate never guts a film over judgement FAILs.

Regression for the 21/11/2026 run of "เดือนของเต้": whole-frame Tesseract read wood grain and checked cloth as
text, which failed good keyframes and then dropped 4 of 9 clips from final.mp4.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from filmmaker import ai, qc
from filmmaker.storyboard import Storyboard

from tests.test_auto_qc_gates import _fake_tesseract, _make_clip, _make_png

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg/ffprobe")


def _sb(n: int = 1) -> Storyboard:
    return Storyboard.model_validate({
        "title": "OCR", "audio": "silent",
        "scenes": [{"id": f"s{i:02d}", "mode": "keyframe", "image_prompt": "rice field", "prompt": "boy walks",
                    "duration": 2} for i in range(1, n + 1)],
    })


# --- ocr_frame ------------------------------------------------------------------


@needs_ffmpeg
def test_ocr_frame_ignores_low_confidence_texture_words(tmp_path, monkeypatch):
    img = _make_png(tmp_path / "f.png")
    monkeypatch.setattr(qc, "tesseract_binary",
                        lambda: str(_fake_tesseract(tmp_path, [("iil", 41), ("Milt", 55), ("emay", 62), ("Sas", 70)])))
    assert qc.ocr_frame(img) == ""


@needs_ffmpeg
def test_ocr_frame_reports_confident_caption_words(tmp_path, monkeypatch):
    img = _make_png(tmp_path / "f.png")
    monkeypatch.setattr(qc, "tesseract_binary",
                        lambda: str(_fake_tesseract(tmp_path, [("Tonight", 95), ("will", 87), ("come", 96), ("|", 95)])))
    text = qc.ocr_frame(img)
    assert "Tonight" in text and "come" in text and "|" not in text


@needs_ffmpeg
def test_ocr_frame_needs_two_words_or_one_long_word(tmp_path, monkeypatch):
    img = _make_png(tmp_path / "f.png")
    monkeypatch.setattr(qc, "tesseract_binary", lambda: str(_fake_tesseract(tmp_path, [("dau", 95)])))
    assert qc.ocr_frame(img) == ""
    monkeypatch.setattr(qc, "tesseract_binary", lambda: str(_fake_tesseract(tmp_path, [("Subscribe", 92)])))
    assert qc.ocr_frame(img) == "Subscribe"


# --- keyframe pre-QC ------------------------------------------------------------


@needs_ffmpeg
def test_keyframe_ocr_hint_without_vlm_is_minor_not_fail(tmp_path, monkeypatch):
    sb = _sb()
    c1 = _make_png(tmp_path / "s01_c1.png")
    monkeypatch.setattr(qc, "ocr_frame", lambda p: "Hi iil Milt emay")
    res = qc.select_best_keyframe(sb, sb.scenes[0], [c1], allow_vlm=False)
    assert res["verdict"] == "MINOR"
    assert any(x.startswith(qc.OCR_HINT_PREFIX) for x in res["issues"])


@needs_ffmpeg
def test_keyframe_vlm_dismisses_ocr_hint(tmp_path, monkeypatch):
    sb = _sb()
    c1, c2 = _make_png(tmp_path / "s01_c1.png"), _make_png(tmp_path / "s01_c2.png", color="green")
    monkeypatch.setattr(qc, "ocr_frame", lambda p: "Hi iil Milt emay")
    seen: list[str] = []

    def fake_vlm(system, user, schema, *, images=(), **kw):
        seen.append(user)
        return qc.KeyframeSelection(
            best_index=1, verdict="PASS", score=8, reason="Cả hai sạch, không có chữ.",
            candidates=[qc.KeyframeCandidateVerdict(index=1, verdict="PASS", score=8, issues=[]),
                        qc.KeyframeCandidateVerdict(index=2, verdict="PASS", score=7, issues=[])])

    monkeypatch.setattr(ai, "ask", fake_vlm)
    res = qc.select_best_keyframe(sb, sb.scenes[0], [c1, c2])
    assert res["verdict"] == "PASS" and res["best_index"] == 1 and res["score"] == 8
    assert not any(x.startswith(qc.OCR_HINT_PREFIX) for x in res["issues"])
    assert "local_ocr_hint=Hi iil Milt emay" in seen[0] and "noisy" in seen[0]
    assert res["v"] == qc.QC_VERSION


@needs_ffmpeg
def test_keyframe_black_still_fails_even_if_vlm_passes(tmp_path, monkeypatch):
    sb = _sb()
    c1 = _make_png(tmp_path / "s01_c1.png", black=True)
    monkeypatch.setattr(ai, "ask", lambda *a, **k: qc.KeyframeSelection(
        best_index=1, verdict="PASS", score=9,
        candidates=[qc.KeyframeCandidateVerdict(index=1, verdict="PASS", score=9)]))
    res = qc.select_best_keyframe(sb, sb.scenes[0], [c1])
    assert res["verdict"] == "FAIL"


def test_old_ocr_keyframe_fail_is_scored_again(tmp_path):
    kf = tmp_path / "keyframes" / "s01.png"
    kf.parent.mkdir(parents=True)
    kf.write_bytes(b"png")
    old = {"verdict": "FAIL", "score": 3, "sig": qc.clip_sig(kf),
           "issues": ["Ảnh keyframe dính chữ trên hình (Hi |) iil | Milt)."]}
    qc.save_keyframe_qc(tmp_path, "s01", old)
    assert qc.current_keyframe_qc(tmp_path, "s01", kf) is None
    other = {**old, "issues": ["Mặt người nói quay đi."]}
    qc.save_keyframe_qc(tmp_path, "s01", other)
    assert qc.current_keyframe_qc(tmp_path, "s01", kf) is not None  # a real (non-OCR) old FAIL is kept
    qc.save_keyframe_qc(tmp_path, "s01", {**old, "v": qc.QC_VERSION})
    assert qc.current_keyframe_qc(tmp_path, "s01", kf) is not None


# --- clip QC --------------------------------------------------------------------


@needs_ffmpeg
def test_clip_vlm_sees_no_text_so_ocr_hint_does_not_fail(tmp_path, monkeypatch):
    sb = _sb()
    clip = _make_clip(tmp_path / "s01.mp4", duration=2.0)
    kf = _make_png(tmp_path / "s01.png")
    monkeypatch.setattr(qc, "whisper_binary", lambda: None)
    monkeypatch.setattr(qc, "ocr_frame", lambda p: "I Hs ti sah SLL")
    users: list[str] = []

    def fake_vlm(system, user, schema, *, images=(), **kw):
        users.append(user)
        return qc.QCAnswer(verdict="PASS", score=8, issues=[], picture="Sạch", sound="Tốt", text_errors=False)

    monkeypatch.setattr(ai, "ask", fake_vlm)
    res = qc.check_scene(sb, sb.scenes[0], clip, kf, tmp_path / "w", backend="api")
    assert res["verdict"] == "PASS" and res["score"] == 8
    assert not any(x.startswith(qc.OCR_HINT_PREFIX) for x in res["issues"])
    assert res["openmontage"]["text_errors"] is False and res["openmontage"]["ocr_texts"]
    assert "noisy Tesseract read" in users[0]


@needs_ffmpeg
def test_clip_vlm_confirms_text_fails(tmp_path, monkeypatch):
    sb = _sb()
    clip = _make_clip(tmp_path / "s01.mp4", duration=2.0)
    monkeypatch.setattr(qc, "whisper_binary", lambda: None)
    monkeypatch.setattr(qc, "ocr_frame", lambda p: "Tonight will come")
    monkeypatch.setattr(ai, "ask", lambda *a, **k: qc.QCAnswer(
        verdict="MINOR", score=7, issues=[], picture="Phụ đề cháy ở dưới", sound="Tốt", text_errors=True))
    res = qc.check_scene(sb, sb.scenes[0], clip, None, tmp_path / "w", backend="api")
    assert res["verdict"] == "FAIL" and res["openmontage"]["text_errors"] is True


def test_old_ocr_clip_fail_is_checked_again(tmp_path):
    clip = tmp_path / "s02.mp4"
    clip.write_bytes(b"mp4")
    rec = {"verdict": "FAIL", "score": 4, "v": 2, "sig": qc.clip_sig(clip), "dialogue_checked": True,
           "issues": ["OpenMontage phát hiện chữ/phụ đề dính trên khung hình (frame #4 (8.8s): I Hs ti . sah SLL |)."]}
    qc._save(tmp_path, "s02", rec)
    assert qc.current(tmp_path, "s02", clip) is None
    qc._save(tmp_path, "s02", {**rec, "issues": ["Sai người nói."]})
    assert qc.current(tmp_path, "s02", clip) is not None  # v2 non-OCR verdicts stay valid


# --- assembly gate --------------------------------------------------------------


def _gate_film(tmp_path: Path, n: int, fails: dict[str, dict]) -> tuple[Storyboard, list[tuple[str, Path]]]:
    sb = _sb(n)
    clips = []
    for s in sb.scenes:
        p = tmp_path / "clips" / f"{s.id}.mp4"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(s.id.encode())
        clips.append((s.id, p))
        om = {"passed": True, "ffprobe": {"valid": True}, "black_flagged": False, "text_errors": False}
        rec = {"verdict": "PASS", "score": 8, "v": qc.QC_VERSION, "sig": qc.clip_sig(p), "issues": [],
               "openmontage": om}
        if s.id in fails:
            rec = {**rec, "verdict": "FAIL", "score": 4, "openmontage": {**om, **fails[s.id]}}
        qc._save(tmp_path, s.id, rec)
    return sb, clips


def test_gate_keeps_judgement_fails_when_dropping_would_gut_the_film(tmp_path):
    sb, clips = _gate_film(tmp_path, 9, {sid: {} for sid in ("s02", "s03", "s06", "s09")})
    logs: list[str] = []
    kept, rejected = qc.filter_clips_for_assembly(sb, tmp_path, clips, run_local_qc=False, log=logs.append)
    assert [sid for sid, _ in kept] == [f"s{i:02d}" for i in range(1, 10)]
    assert rejected == []
    assert any("GIỮ LẠI" in x and "s02" in x for x in logs)


def test_gate_drops_a_few_judgement_fails(tmp_path):
    sb, clips = _gate_film(tmp_path, 9, {"s04": {}, "s07": {}})  # 2 <= limit 2 for 9 clips
    kept, rejected = qc.filter_clips_for_assembly(sb, tmp_path, clips, run_local_qc=False, log=lambda m: None)
    assert rejected == ["s04", "s07"] and len(kept) == 7


def test_gate_always_drops_technical_fails_even_past_the_limit(tmp_path):
    sb, clips = _gate_film(tmp_path, 4, {
        "s01": {"black_flagged": True, "passed": False},
        "s02": {"ffprobe": {"valid": False}, "passed": False},
        "s03": {},  # judgement FAIL: kept, the film already lost 2 of 4
    })
    logs: list[str] = []
    kept, rejected = qc.filter_clips_for_assembly(sb, tmp_path, clips, run_local_qc=False, log=logs.append)
    assert rejected == ["s01", "s02"]
    assert [sid for sid, _ in kept] == ["s03", "s04"]


def test_gate_limit_math():
    assert qc.gate_drop_limit(1) == 1
    assert qc.gate_drop_limit(4) == 1
    assert qc.gate_drop_limit(9) == 2
    assert qc.gate_drop_limit(10) == 2
    assert qc.gate_drop_limit(20) == 5
