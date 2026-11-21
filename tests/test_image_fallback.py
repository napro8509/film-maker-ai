"""A picture the film's image AI cannot make after its tries is made on the other one (Muse <-> Flow);
every other picture and the video keep the AI chosen for the film."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from filmmaker import ai, qc
from filmmaker.gflow import Gflow, GflowError, other_image_model
from filmmaker.pipeline import Pipeline, RunOptions, State
from filmmaker.storyboard import load_storyboard

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def _png(path: Path, color: str = "green") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:size=64x64",
                    "-frames:v", "1", str(path)], check=True)
    return path


def _clip(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _sb(tmp_path: Path, **top) -> Path:
    p = tmp_path / "storyboard.yaml"
    p.write_text(yaml.safe_dump({"title": "Fallback", **top, "scenes": [
        {"id": "s01", "mode": "keyframe", "image_prompt": "A farmer on a veranda", "prompt": "He nods",
         "duration": 4},
        {"id": "s02", "mode": "keyframe", "image_prompt": "Rice field at dusk", "prompt": "Wind", "duration": 4},
    ]}, allow_unicode=True), encoding="utf-8")
    return p


class FakeGflow(Gflow):
    def __init__(self, fail_models: set[str] = frozenset()):
        super().__init__(dry_run=False, log=lambda *_: None)
        self.images: list[tuple[str, str]] = []  # (model, project)
        self.videos: list[str | None] = []
        self.fail_models = fail_models
        self.projects = 0

    def create_project(self, title: str) -> str:
        self.projects += 1
        return "proj-flow"

    def text_to_image(self, prompt, *, out, model=None, project=None, **kw):
        self.images.append((model, project))
        if model in self.fail_models:
            raise GflowError(f"{model} refused the picture", exit_code=42)
        return _png(Path(out))

    def image_to_video(self, image, prompt, *, out, model=None, **kw):
        self.videos.append(model)
        return _clip(Path(out)), "vid"

    def muse_video(self, image, prompt, *, out, **kw):  # the film's video AI (Muse) stays Muse
        self.videos.append("muse")
        return _clip(Path(out))


def test_other_image_model():
    assert other_image_model("muse") == "nano2"
    assert other_image_model("nano2") == "muse"
    assert other_image_model(None) == "muse"


@needs_ffmpeg
def test_two_failed_pre_qc_rounds_make_only_that_keyframe_on_the_other_ai(tmp_path, monkeypatch):
    sb = load_storyboard(_sb(tmp_path))  # pictures on Flow (nano2)
    out = tmp_path / "out"
    calls = {"n": 0}

    def fake_ask(system, user, schema, *, images=(), **kw):
        calls["n"] += 1
        bad = "A farmer" in user or "farmer" in user.lower()
        first_two_of_s01 = bad and calls["n"] <= 2
        verdict, score = ("FAIL", 3) if first_two_of_s01 else ("PASS", 9)
        return qc.KeyframeSelection(best_index=1, verdict=verdict, score=score, reason="",
                                    image_prompt="A calm farmer on a veranda" if verdict == "FAIL" else None,
                                    candidates=[qc.KeyframeCandidateVerdict(index=i, verdict=verdict, score=score,
                                                                            issues=[]) for i in (1, 2)])

    monkeypatch.setattr(ai, "ask", fake_ask)
    g = FakeGflow()
    lines: list[str] = []
    pipe = Pipeline(sb, out, g, sleep=lambda *_: None, log=lines.append)
    report = pipe.run(RunOptions(keyframe_candidates=2, pre_qc_gate=True, pause=(0, 0), ai_kw={"backend": "api"},
                                 only={"s01"}))
    assert report.done == ["s01"]
    models = [m for m, _ in g.images]
    assert models == ["nano2"] * 4 + ["muse"] * 2  # 2 rounds on Flow, then this picture on Muse
    assert any("⇄" in l and "Muse" in l for l in lines)
    assert qc.load_keyframe_qc(out)["s01"]["image_model"] == "muse"
    assert all(m != "muse" for m in g.videos)  # the video keeps the film's AI

    # the next scene starts again on the film's own picture AI
    g.images.clear()
    pipe.run(RunOptions(keyframe_candidates=2, pre_qc_gate=True, pause=(0, 0), ai_kw={"backend": "api"},
                        only={"s02"}))
    assert {m for m, _ in g.images} == {"nano2"}


@needs_ffmpeg
def test_muse_picture_error_is_made_on_flow_with_a_flow_project(tmp_path):
    sb = load_storyboard(_sb(tmp_path, model="muse", image_model="muse"))  # a film made on Muse only
    out = tmp_path / "out"
    g = FakeGflow(fail_models={"muse"})
    lines: list[str] = []
    pipe = Pipeline(sb, out, g, sleep=lambda *_: None, log=lines.append)
    pipe.run(RunOptions(keyframes_only=True, pause=(0, 0)))
    assert (out / "keyframes" / "s01.png").is_file() and (out / "keyframes" / "s02.png").is_file()
    flow = [(m, p) for m, p in g.images if m == "nano2"]
    assert flow and all(p == "proj-flow" for _, p in flow)
    assert g.projects == 1
    assert sum("⇄" in l for l in lines) == 2


@needs_ffmpeg
def test_character_picture_falls_back_to_the_other_ai(tmp_path):
    p = tmp_path / "storyboard.yaml"
    p.write_text(yaml.safe_dump({"title": "Chars", "model": "muse", "image_model": "muse",
                                 "characters": [{"name": "Mai", "description": "young woman"}],
                                 "scenes": [{"id": "s01", "mode": "t2v", "prompt": "Mai walks", "duration": 4}]},
                                allow_unicode=True), encoding="utf-8")
    sb = load_storyboard(p)
    g = FakeGflow(fail_models={"muse"})
    pipe = Pipeline(sb, tmp_path / "out", g, sleep=lambda *_: None, log=lambda *_: None)
    dest = tmp_path / "mai.png"
    report = pipe.generate_portraits([(sb.characters[0], dest)], RunOptions(pause=(0, 0)))
    assert report.done == ["Mai"] and dest.is_file()
    assert [m for m, _ in g.images][-1] == "nano2"


@needs_ffmpeg
def test_fatal_error_never_switches(tmp_path):
    sb = load_storyboard(_sb(tmp_path))
    g = FakeGflow()

    def boom(prompt, *, out, model=None, project=None, **kw):
        g.images.append((model, project))
        raise GflowError("not signed in", exit_code=next(iter(__import__("filmmaker.gflow").gflow.FATAL_EXIT_CODES)))

    g.text_to_image = boom
    pipe = Pipeline(sb, tmp_path / "out", g, sleep=lambda *_: None, log=lambda *_: None)
    pipe.run(RunOptions(keyframes_only=True, pause=(0, 0)))
    assert {m for m, _ in g.images} == {"nano2"}
    assert State(tmp_path / "out" / "state.json").scene("s01").get("keyframe") is None
