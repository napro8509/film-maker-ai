"""AC9: the per-scene `refuse` layer (durations.refuse_bad_duration) inside `Pipeline.run`.

`Pipeline.run` is called directly, on a storyboard that already has a `project_id` so `ensure_project()` never
runs `gflow project create`. Gflow is NOT dry (fake gflow + fake flow_ui), so a missing check would show up as
image/video calls in the fake logs. Nothing reaches Flow.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from filmmaker import durations as dur
from filmmaker.gflow import Gflow
from filmmaker.pipeline import SUBMITTING, Pipeline, RunOptions
from filmmaker.storyboard import load_storyboard
from tests.duration_helpers import veo_lite_project
from tests.model_helpers import set_scene, ui_out_dir

FAKE = Path(__file__).parent / "fake_gflow.py"
FAKE_UI = Path(__file__).parent / "fake_flow_ui.py"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")  # the fakes make clips with it


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings = tmp_path / "cfg" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"auto_backup": False}))
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(settings))
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "gflow.log"))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)
    monkeypatch.setenv("FLOW_UI_CMD", f"{sys.executable} {FAKE_UI}")
    monkeypatch.setenv("FAKE_FLOW_UI_LOG", str(tmp_path / "ui.log"))
    monkeypatch.delenv("FAKE_FLOW_UI_FAIL", raising=False)

    class E:
        root = tmp_path

        @staticmethod
        def calls() -> list[list[str]]:
            out = []
            for p in (tmp_path / "gflow.log", tmp_path / "ui.log"):
                if p.exists():
                    out += [json.loads(line) if line.startswith("[") else line.split()
                            for line in p.read_text().splitlines() if line.strip()]
            return out

    return E


def storyboard(env, scenes) -> Path:
    """veo-lite project that already has a Flow project id (no `project create`)."""
    p = veo_lite_project(env.root / "ws", "film", scenes)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    raw["project_id"] = "proj-existing"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def run(sb_path: Path, **opts):
    lines: list[str] = []
    sb = load_storyboard(sb_path)
    pipe = Pipeline(sb, ui_out_dir(sb_path), Gflow(log=lambda *_: None), log=lines.append, sleep=lambda *_: None)
    report = pipe.run(RunOptions(pause=(0, 0), refuse=dur.refuse_bad_duration(sb), **opts))
    return pipe, report, lines


def render_calls(env) -> list[list[str]]:
    words = {"image", "video", "i2v", "t2v", "i2i", "keyframe"}
    return [c for c in env.calls() if words & set(c)]


BAD = [
    {"id": "s01", "mode": "keyframe", "image_prompt": "a cafe", "duration": 5},
    {"id": "s02", "mode": "t2v", "duration": 7},
    {"id": "s03", "mode": "i2v", "image": "start.png", "duration": 9},
]


def test_pipeline_refuse_bad_duration(env):
    sb = storyboard(env, BAD)
    (sb.parent / "start.png").write_bytes(b"png")
    pipe, report, lines = run(sb)
    assert set(report.failed) == {"s01", "s02", "s03"}
    assert report.failed["s01"].startswith("độ dài 5 giây không hợp lệ với veo-lite (chỉ nhận 4, 6, 8)")
    assert "round-durations" in report.failed["s02"]
    assert report.done == [] and report.aborted is None
    assert any("[s01] KHÔNG GỬI: độ dài 5 giây" in line for line in lines)
    assert render_calls(env) == [], env.calls()
    assert not any(c[:2] == ["project", "create"] for c in env.calls())
    assert not any((pipe.out / "keyframes").iterdir())
    assert not any(pipe.state.scene(s).get("status") == SUBMITTING for s in ("s01", "s02", "s03"))
    assert all(pipe.state.scene(s) == {} or "submitted_at" not in pipe.state.scene(s) for s in ("s01", "s02", "s03"))


def test_pipeline_refuse_keeps_interrupted_state(env):
    """A forced interrupted scene with a bad duration: refused, and its state (submitted_at…) is not touched."""
    sb = storyboard(env, [{"id": "s01", "mode": "t2v", "duration": 7}])
    set_scene(sb, "s01", "interrupted", submitted_at="2026-11-01T00:00:00Z")
    before = (ui_out_dir(sb) / "state.json").read_bytes()
    _, report, _ = run(sb, force={"s01"})
    assert "s01" in report.failed and "độ dài 7 giây" in report.failed["s01"]
    assert render_calls(env) == []
    assert (ui_out_dir(sb) / "state.json").read_bytes() == before


@needs_ffmpeg
def test_pipeline_refuse_only_bad_scenes(env):
    """Valid scenes in the same run are still rendered; only the bad one is refused."""
    sb = storyboard(env, [{"id": "s01", "mode": "t2v", "duration": 5}, {"id": "s02", "mode": "t2v", "duration": 6},
                          {"id": "s03", "mode": "t2v"}])
    pipe, report, _ = run(sb)
    assert list(report.failed) == ["s01"]
    assert sorted(report.done) == ["s02", "s03"]
    sent = " ".join(" ".join(c) for c in render_calls(env))
    assert "s02" in sent and "s03" in sent and "s01" not in sent
    assert pipe.state.scene("s01").get("status") != SUBMITTING


def test_refuse_reads_current_metadata(env, monkeypatch, tmp_path):
    """Like refuse_retired, each scene re-reads models.yaml: an override that accepts 5 s lets s01 through."""
    sb_path = storyboard(env, [{"id": "s01", "mode": "t2v", "duration": 5}])
    sb = load_storyboard(sb_path)
    refuse = dur.refuse_bad_duration(sb)
    assert refuse(sb.scenes[0])
    override = tmp_path / "models.yaml"
    override.write_text("models:\n  veo-lite:\n    durations: [5, 8]\n", encoding="utf-8")
    monkeypatch.setenv("FILMMAKER_MODELS", str(override))
    assert refuse(sb.scenes[0]) is None


@pytest.mark.parametrize("web", [True, False])
def test_blocked_message_interrupted_bad_duration_says_fix_duration_first(env, web):
    """QA 1: an interrupted scene with a bad duration cannot be resent as is (the UI tick box is off for it), so the
    BỎ QUA message says to check Flow / download the clip, or fix `duration:` by hand first."""
    sb = storyboard(env, [{"id": "s01", "mode": "t2v", "duration": 7}, {"id": "s02", "mode": "t2v", "duration": 6}])
    set_scene(sb, "s01", "interrupted", submitted_at="2026-11-01T00:00:00Z")
    set_scene(sb, "s02", "interrupted", submitted_at="2026-11-01T00:00:00Z")
    _, report, lines = run(sb, confirm=set() if web else None)
    bad, ok = report.failed["s01"], report.failed["s02"]
    assert "Nếu video đã có trên Flow, tải về và lưu thành" in bad
    assert ("nếu chưa có, sửa tay `duration:` (7 giây, veo-lite chỉ nhận 4, 6, 8; bị ngắt nên app không tự làm tròn) "
            "rồi ") in bad
    assert "sửa tay `duration:`" not in ok  # a valid duration keeps the old message
    tick = "tick ô xác nhận trong hộp Render"
    assert (tick in ok) is web and (tick in bad) is web  # the tick comes only after the fix
    assert bad.index("sửa tay") < (bad.index(tick) if web else bad.index("--retry-unknown"))
    assert render_calls(env) == []
