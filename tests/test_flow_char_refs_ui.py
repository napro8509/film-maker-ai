"""Task #17: when a scene's characters are Flow characters (made on Flow's Characters page) and the Flow chat
driver is available, keyframes and videos are made ON THE CHAT COMPOSER with every character attached BY NAME
(`--ref character:<name>`), with no 3-image cap — never uploaded one picture at a time through the gflow CLI.
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from filmmaker.gflow import Gflow
from filmmaker.pipeline import Pipeline, RunOptions
from filmmaker.storyboard import load_storyboard

TESTS = Path(__file__).parent
FAKE = TESTS / "fake_gflow.py"
FAKE_UI = TESTS / "fake_flow_ui.py"

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

HEADER = """
title: Flow characters on chat
aspect: "9:16"
model: veo-lite
image_model: nano2
style: "anime"
characters:
  - {name: Lan, description: "red scarf", image: lan.png}
  - {name: Hoa, description: "white ao dai", image: hoa.png}
  - {name: Tuan, description: "yellow raincoat", image: tuan.png}
  - {name: Bao, description: "black cat", image: bao.png}
  - {name: Minh, description: "round glasses"}
scenes:
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    gflow_log = tmp_path / "gflow.log"
    ui_log = tmp_path / "ui.log"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(gflow_log))
    monkeypatch.setenv("FLOW_UI_CMD", f"{sys.executable} {FAKE_UI}")
    monkeypatch.setenv("FAKE_FLOW_UI_LOG", str(ui_log))
    for var in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_CATALOG", "FAKE_FLOW_UI_FAIL", "FAKE_FLOW_UI_PROGRESS"):
        monkeypatch.delenv(var, raising=False)
    imgs = {"lan": b"lan-portrait", "hoa": b"hoa-portrait", "tuan": b"tuan-portrait", "bao": b"bao-portrait"}
    for name, data in imgs.items():
        (tmp_path / f"{name}.png").write_bytes(data)
    # Pre-mark Lan/Hoa/Tuan/Bao as Flow characters of the project (as `filmmaker characters` would have).
    flow_chars = {name.capitalize() if name != "bao" else "Bao": {
        "project": "proj-123", "flow_name": name.capitalize() if name != "bao" else "Bao",
        "entity_id": f"ent-{name}", "media_id": f"m-{name}",
        "sha": hashlib.sha256(data).hexdigest()} for name, data in imgs.items()}
    out = tmp_path / "out"
    out.mkdir()
    (out / "state.json").write_text(json.dumps(
        {"project_id": "proj-123", "flow_characters": flow_chars, "scenes": {}}), encoding="utf-8")

    def run(scenes: str, *, fail: str | None = None) -> tuple[list[list[str]], list[list[str]]]:
        if fail:
            monkeypatch.setenv("FAKE_GFLOW_FAIL", fail)
        p = tmp_path / "story.yaml"
        p.write_text(HEADER + scenes, encoding="utf-8")
        pipe = Pipeline(load_storyboard(p), out, Gflow(log=lambda *_: None, ui_enabled=True),
                        log=lambda *_: None, sleep=lambda *_: None)
        report = pipe.run(RunOptions(pause=(0, 0)))
        assert report.ok, report
        gcalls = [json.loads(l) for l in gflow_log.read_text().splitlines()] if gflow_log.exists() else []
        ucalls = [json.loads(l) for l in ui_log.read_text().splitlines()] if ui_log.exists() else []
        return gcalls, ucalls

    return run


def refs(call: list[str]) -> list[str]:
    return [call[i + 1] for i, a in enumerate(call) if a == "--ref"]


def ui_call(ucalls: list[list[str]], sub: str, out_stem: str) -> list[str] | None:
    found = [c for c in ucalls if c and c[0] == sub and Path(c[c.index("-o") + 1]).stem == out_stem]
    assert len(found) <= 1, found
    return found[0] if found else None


def test_keyframe_i2i_on_chat_attaches_all_flow_characters(env):
    """A keyframe with four Flow characters is drawn on the chat composer, each attached by name — no 3-image
    cap, and no `gflow image i2i` CLI upload."""
    gcalls, ucalls = env("""
  - id: k1
    mode: t2v
    prompt: "the whole family at dinner"
    characters: [Lan, Hoa, Tuan, Bao]
""")
    # The video (t2v with references) went to the chat composer as a reference-to-video: no start --image,
    # every character attached by name, no cap.
    i2v = ui_call(ucalls, "i2v", "k1")
    assert i2v is not None and "--image" not in i2v
    assert refs(i2v) == ["character:Lan", "character:Hoa", "character:Tuan", "character:Bao"]
    # Nothing uploaded through the gflow CLI image/video reference commands.
    assert all(c[:2] not in (["image", "i2i"], ["video", "r2v"]) for c in gcalls)


def test_keyframe_scene_draws_keyframe_on_chat(env):
    """A keyframe scene: the still is drawn on the chat composer (i2i) with the characters by name; the clip is
    then the normal CLI image-to-video from that keyframe."""
    gcalls, ucalls = env("""
  - id: s1
    mode: keyframe
    image_prompt: "the family around the table"
    prompt: "they gather"
    characters: [Lan, Hoa, Tuan, Bao, Minh]
""")
    i2i = ui_call(ucalls, "i2i", "s1")
    assert i2i is not None
    # Minh has no picture, so only the four with Flow characters are attached, each by name.
    assert refs(i2i) == ["character:Lan", "character:Hoa", "character:Tuan", "character:Bao"]
    assert all(c[:2] != ["image", "i2i"] for c in gcalls)  # never the CLI i2i


def test_i2v_fallback_passes_character_refs_to_chat(env):
    """An image-to-video scene whose gflow i2v is refused (exit 36) falls back to the chat composer, and there
    the scene's Flow characters are attached alongside the start frame."""
    (env.__self__ if False else None)  # noqa (keep linters calm about the fixture object)
    gcalls, ucalls = env("""
  - id: v1
    mode: i2v
    image: lan.png
    prompt: "Lan and Hoa turn and smile"
    characters: [Lan, Hoa, Tuan]
""", fail="video i2v:36:false")
    i2v = ui_call(ucalls, "i2v", "v1")
    assert i2v is not None and "--image" in i2v  # the start frame is still attached first
    # At most 2 next to the start frame (Flow takes 3 images on Veo Lite): the two the prompt names, not Tuan.
    assert refs(i2v) == ["character:Lan", "character:Hoa"]
