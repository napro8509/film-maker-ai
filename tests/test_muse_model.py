"""`model: muse` renders through muse_ui.py (Meta Muse), never through gflow / the Flow chat page."""
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from filmmaker import durations as dur
from filmmaker import model_meta
from filmmaker.gflow import Gflow, GflowError, is_muse
from filmmaker.pipeline import Pipeline, RunOptions
from filmmaker.storyboard import load_storyboard
from tests.duration_helpers import veo_lite_project
from tests.model_helpers import ui_out_dir

FAKE = Path(__file__).parent / "fake_gflow.py"
FAKE_UI = Path(__file__).parent / "fake_flow_ui.py"  # answers `i2v ... -o X` like muse_ui does
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


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
    monkeypatch.setenv("MUSE_UI_CMD", f"{sys.executable} {FAKE_UI}")
    monkeypatch.setenv("FAKE_FLOW_UI_LOG", str(tmp_path / "muse.log"))
    monkeypatch.delenv("FAKE_FLOW_UI_FAIL", raising=False)

    class E:
        root = tmp_path

        @staticmethod
        def muse_calls() -> list[list[str]]:
            p = tmp_path / "muse.log"
            return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

        @staticmethod
        def gflow_video_calls() -> list[list[str]]:
            p = tmp_path / "gflow.log"
            rows = [x.split() if not x.startswith("[") else json.loads(x)
                    for x in (p.read_text().splitlines() if p.exists() else []) if x.strip()]
            return [r for r in rows if "video" in r]

    return E


def test_is_muse():
    assert is_muse("muse") and is_muse(" Muse ")
    assert not is_muse("veo-lite") and not is_muse(None)


@needs_ffmpeg
@pytest.mark.parametrize("call", ["t2v", "i2v", "r2v", "ui"])
def test_every_video_method_goes_to_muse(env, tmp_path, call):
    img = tmp_path / "start.png"
    ref = tmp_path / "anna.png"
    img.write_bytes(b"png")
    ref.write_bytes(b"png")
    g = Gflow(log=lambda *_: None)
    out = tmp_path / "clips" / "s01.mp4"
    kw = dict(aspect="9:16", model="muse", duration=10, project="p1", out=out)
    if call == "t2v":
        path, media = g.text_to_video("a cat", **kw)
    elif call == "i2v":
        path, media = g.image_to_video(img, "a cat", **kw)
    elif call == "r2v":
        path, media = g.reference_to_video("a cat", [ref], **kw)
    else:
        path, media = g.ui_image_to_video(img, "a cat", refs=[ref, "character:Bo"], **kw)
    assert path == out and out.is_file() and media is None
    (argv,) = env.muse_calls()
    assert argv[0] == "i2v" and argv[argv.index("--prompt") + 1] == "a cat"
    assert argv[argv.index("--duration") + 1] == "10" and "--project" not in argv
    assert ("--image" in argv) == (call in ("i2v", "ui"))
    refs = [argv[i + 1] for i, a in enumerate(argv) if a == "--ref"]
    assert refs == ([str(ref)] if call in ("r2v", "ui") else [])  # a Flow character by name cannot go to Muse
    assert env.gflow_video_calls() == []


def test_dry_run_sends_nothing(env, tmp_path):
    g = Gflow(dry_run=True, log=lambda *_: None)
    path, _ = g.text_to_video("x", aspect="9:16", model="muse", duration=10, project=None, out=tmp_path / "o.mp4")
    assert path == tmp_path / "o.mp4" and env.muse_calls() == []


def test_muse_failure_is_reported_as_muse(env, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FLOW_UI_FAIL", "8:false")
    with pytest.raises(GflowError) as e:
        Gflow(log=lambda *_: None).text_to_video("x", aspect="9:16", model="muse", duration=10, project=None,
                                                 out=tmp_path / "o.mp4")
    assert e.value.exit_code == 8 and str(e.value).startswith("Muse (giao diện chat)")


def test_models_yaml_knows_muse():
    meta = model_meta.load()
    assert meta.durations("muse") == [10]


@needs_ffmpeg
def test_pipeline_renders_a_muse_scene(env):
    p = veo_lite_project(env.root / "ws", "film", [{"id": "s01", "mode": "t2v", "duration": 10, "model": "muse"}])
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    raw["project_id"] = "proj-existing"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    sb = load_storyboard(p)
    pipe = Pipeline(sb, ui_out_dir(p), Gflow(log=lambda *_: None), log=lambda *_: None, sleep=lambda *_: None)
    report = pipe.run(RunOptions(pause=(0, 0), refuse=dur.refuse_bad_duration(sb)))
    assert report.done == ["s01"], report.failed
    assert len(env.muse_calls()) == 1 and env.gflow_video_calls() == []
    assert Path(pipe.state.scene("s01")["video"]).is_file()


@needs_ffmpeg
def test_muse_only_run_creates_no_flow_project(env):
    p = veo_lite_project(env.root / "ws", "film", [{"id": "s01", "mode": "i2v", "image": "start.png",
                                                     "duration": 10, "model": "muse"}])
    (p.parent / "start.png").write_bytes(b"png")
    sb = load_storyboard(p)
    pipe = Pipeline(sb, ui_out_dir(p), Gflow(log=lambda *_: None), log=lambda *_: None, sleep=lambda *_: None)
    report = pipe.run(RunOptions(pause=(0, 0), refuse=dur.refuse_bad_duration(sb)))
    assert report.done == ["s01"], report.failed
    log = (env.root / "gflow.log").read_text() if (env.root / "gflow.log").exists() else ""
    assert "project" not in log  # no `gflow project create`
    (argv,) = env.muse_calls()
    assert argv[argv.index("--image") + 1].endswith("start.png")
