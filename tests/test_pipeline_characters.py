"""Pipeline argv with `Scene.characters` (storyboard-board AC11, AC12): the `--ref` images sent to gflow
follow the scene's character list (storyboard order, every character with a picture), for image i2i,
video r2v and the fallback_t2v branch of keyframe scenes.

Here the Flow chat driver is OFF (`ui_enabled=False`), so references go through the gflow CLI, which caps
them at the model's `--ref` limit (nano2 image = 10, Veo video = 3). When the chat driver is available the
pipeline attaches every Flow character by name with no cap instead — see test_flow_char_refs_ui.py."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from filmmaker.gflow import Gflow
from filmmaker.pipeline import Pipeline, RunOptions
from filmmaker.storyboard import load_storyboard

FAKE = Path(__file__).parent / "fake_gflow.py"

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

HOA_PNG = b"hoa-portrait"
HOA_MID = "media-hoa-0001"

# Lan, Hoa, Tuan, Bao have images; Minh has none. Hoa's portrait is already in the Flow project (media id).
HEADER = """
title: Characters run
aspect: "9:16"
model: veo-lite
image_model: nano2
style: "anime"
{extra}
characters:
  - name: Lan
    description: "red scarf"
    image: lan.png
  - name: Minh
    description: "round glasses"
  - name: Hoa
    description: "white ao dai"
    image: hoa.png
  - name: Tuan
    description: "yellow raincoat"
    image: tuan.png
  - name: Bao
    description: "black cat"
    image: bao.png
scenes:
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(log))
    for var in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_NO_AUDIO", "FLOW_UI_CMD"):
        monkeypatch.delenv(var, raising=False)
    for name in ("lan", "tuan", "bao"):
        (tmp_path / f"{name}.png").write_bytes(f"{name}-portrait".encode())
    (tmp_path / "hoa.png").write_bytes(HOA_PNG)
    (tmp_path / "flow").mkdir()
    (tmp_path / "flow" / "hoa.jpg").write_bytes(HOA_PNG)
    cat = tmp_path / "catalog.jsonl"
    cat.write_text(json.dumps({"media_id": HOA_MID, "project_id": "proj-123",
                               "local_path": str(tmp_path / "flow" / "hoa.jpg"), "prompt": "portrait"}))
    monkeypatch.setenv("FAKE_GFLOW_CATALOG", str(cat))

    def run(scenes: str, *, extra: str = "") -> list[list[str]]:
        p = tmp_path / "story.yaml"
        p.write_text(HEADER.format(extra=extra) + scenes, encoding="utf-8")
        pipe = Pipeline(load_storyboard(p), tmp_path / "out", Gflow(log=lambda *_: None, ui_enabled=False),
                        log=lambda *_: None, sleep=lambda *_: None)
        report = pipe.run(RunOptions(pause=(0, 0)))
        assert report.ok, report
        calls = [json.loads(l) for l in log.read_text().splitlines()]
        root = str(tmp_path.resolve())
        return [[a.replace(root, "<tmp>").replace(str(tmp_path), "<tmp>") for a in c] for c in calls]

    return run


def call_for(calls: list[list[str]], kind: tuple[str, str], out_name: str) -> list[str] | None:
    """The `gflow <kind...>` call writing `out_name` (e.g. "k1.png"), or None."""
    found = [c for c in calls if tuple(c[:2]) == kind and Path(c[c.index("-o") + 1]).name == out_name]
    assert len(found) <= 1, found
    return found[0] if found else None


def refs(call: list[str]) -> list[str]:
    return [call[i + 1] for i, a in enumerate(call) if a == "--ref"]


def test_i2i_refs_follow_scene_characters(env):
    calls = env("""
  - id: k1
    mode: keyframe
    image_prompt: "Lan at a cafe table"
    prompt: "Lan laughs"
    characters: [Bao, Hoa]
  - id: k2
    mode: keyframe
    image_prompt: "a crowd in the rain"
    prompt: "everyone runs"
    characters: [bao, Minh, Tuan, HOA, Lan]
  - id: k3
    mode: keyframe
    image_prompt: "Lan and Hoa at the market"
    prompt: "they walk"
    characters: []
  - id: k4
    mode: keyframe
    image_prompt: "Lan on the beach"
    prompt: "Lan waves"
  - id: k5
    mode: keyframe
    image_prompt: "Minh reads"
    prompt: "Minh reads"
    characters: [Minh]
""")
    # Listed characters override detection; storyboard order; Hoa's portrait goes as its Flow media id.
    k1 = call_for(calls, ("image", "i2i"), "k1.png")
    assert refs(k1) == [HOA_MID, "<tmp>/bao.png"]
    assert "Lan:" not in k1[2] and "Hoa: white ao dai" in k1[2] and "Bao: black cat" in k1[2]
    # Five names, four with images: all four in storyboard order (no 3-image cap).
    assert refs(call_for(calls, ("image", "i2i"), "k2.png")) == ["<tmp>/lan.png", HOA_MID, "<tmp>/tuan.png", "<tmp>/bao.png"]
    # Empty list: no references at all, plain text-to-image.
    assert call_for(calls, ("image", "i2i"), "k3.png") is None
    assert call_for(calls, ("image", "t2i"), "k3.png") is not None
    # No field: names detected in the text, as before.
    assert refs(call_for(calls, ("image", "i2i"), "k4.png")) == ["<tmp>/lan.png"]
    # Only characters without images: t2i.
    assert call_for(calls, ("image", "i2i"), "k5.png") is None
    assert call_for(calls, ("image", "t2i"), "k5.png") is not None
    assert all(c[:2] != ["video", "r2v"] for c in calls)


def test_r2v_refs_follow_scene_characters(env):
    calls = env("""
  - id: t1
    mode: t2v
    prompt: "Lan watches the sea"
    characters: [Bao]
  - id: t2
    mode: t2v
    prompt: "Lan and Tuan play"
    characters: []
  - id: t3
    mode: t2v
    prompt: "the family dinner"
    characters: [Bao, Tuan, Hoa, Lan]
  - id: t4
    mode: t2v
    prompt: "Tuan jumps, Lan watches"
""")
    t1 = call_for(calls, ("video", "r2v"), "t1.mp4")
    assert refs(t1) == ["<tmp>/bao.png"]
    assert "Lan:" not in t1[2] and "Bao: black cat" in t1[2]
    assert call_for(calls, ("video", "r2v"), "t2.mp4") is None
    assert call_for(calls, ("video", "t2v"), "t2.mp4") is not None
    # r2v sends files (no media-id lookup); four characters but the CLI (chat driver off) caps veo-lite at 3,
    # so only the first three in storyboard order are sent (gflow would reject a fourth with exit 2).
    assert refs(call_for(calls, ("video", "r2v"), "t3.mp4")) == ["<tmp>/lan.png", "<tmp>/hoa.png", "<tmp>/tuan.png"]
    assert refs(call_for(calls, ("video", "r2v"), "t4.mp4")) == ["<tmp>/lan.png", "<tmp>/tuan.png"]


def test_fallback_t2v_refs_follow_scene_characters(env, monkeypatch):
    """i2v refused before submit (not-ported, exit 36) with fallback_t2v: keyframe scenes render as r2v/t2v."""
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    calls = env("""
  - id: k1
    mode: keyframe
    image_prompt: "Lan at a cafe table"
    prompt: "Lan laughs"
    characters: [Hoa, Bao]
  - id: k2
    mode: keyframe
    image_prompt: "Lan alone"
    prompt: "Lan walks"
    characters: []
  - id: k3
    mode: keyframe
    image_prompt: "a crowd"
    prompt: "everyone runs"
    characters: [Bao, Tuan, Hoa, Lan]
  - id: k4
    mode: keyframe
    image_prompt: "Tuan on the beach"
    prompt: "Tuan waves"
""", extra="fallback_t2v: true")
    # The first scene made its keyframe (i2i, media id for Hoa) before i2v was refused.
    assert refs(call_for(calls, ("image", "i2i"), "k1.png")) == [HOA_MID, "<tmp>/bao.png"]
    assert call_for(calls, ("video", "i2v"), "k1.mp4") is not None
    k1 = call_for(calls, ("video", "r2v"), "k1.mp4")
    assert refs(k1) == ["<tmp>/hoa.png", "<tmp>/bao.png"]
    assert "Lan:" not in k1[2] and "Hoa: white ao dai" in k1[2]
    assert call_for(calls, ("video", "r2v"), "k2.mp4") is None
    assert call_for(calls, ("video", "t2v"), "k2.mp4") is not None
    # Four characters, Veo cap 3 on the CLI (chat driver off): first three in storyboard order.
    assert refs(call_for(calls, ("video", "r2v"), "k3.mp4")) == ["<tmp>/lan.png", "<tmp>/hoa.png", "<tmp>/tuan.png"]
    assert refs(call_for(calls, ("video", "r2v"), "k4.mp4")) == ["<tmp>/tuan.png"]
    # Later keyframe scenes skip the keyframe and i2v entirely.
    assert all(Path(c[c.index("-o") + 1]).stem == "k1" for c in calls if c[:2] in (["image", "i2i"], ["video", "i2v"]))
