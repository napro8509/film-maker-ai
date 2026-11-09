"""Golden snapshot (T1 of storyboard-board): prompts, references, fingerprints and gflow argv of storyboards
WITHOUT `Scene.characters`, recorded on the code before that field existed (AC1, AC11).

The GOLDEN_* constants are fixed values: never recompute them from the code. If one of these tests fails,
the change altered what old storyboards send to Flow (or marks finished scenes as changed).
"""
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from filmmaker.gflow import Gflow
from filmmaker.pipeline import Pipeline, RunOptions
from filmmaker.storyboard import load_storyboard

FAKE = Path(__file__).parent / "fake_gflow.py"
TEMPLATE = Path(__file__).parent.parent / "filmmaker" / "templates" / "storyboard.yaml"

# Fixed reference-image bytes: the fingerprints hash them.
LAN_PNG = b"golden-lan-portrait-v1"
HOA_PNG = b"golden-hoa-portrait-v1"
TUAN_PNG = b"golden-tuan-portrait-v1"
BAO_PNG = b"golden-bao-portrait-v1"
I2V_IMAGE = "/golden/fixed/start.png"  # absolute and fixed: the i2v source path is part of the fingerprint

# Lan, Hoa, Tuan, Bao have images; Minh has none. Every mode, image_prompt empty and set, camera or not,
# names in different case, a name only in image_prompt, more than MAX_REFS characters in one scene.
SAMPLE = f"""
title: Golden sample
aspect: "9:16"
model: veo-lite
image_model: nano2
style: "anime, soft pastel colors."
characters:
  - name: Lan
    description: "short black hair, red scarf"
    image: lan.png
  - name: Minh
    description: "tall young man, round glasses, grey hoodie."
  - name: Hoa
    description: "elderly woman, white ao dai"
    image: hoa.png
  - name: Tuan
    description: "boy of ten, yellow raincoat"
    image: tuan.png
  - name: Bao
    description: "a black cat with green eyes"
    image: bao.png
scenes:
  - id: k1
    mode: keyframe
    image_prompt: "Lan and Minh at a cafe table"
    prompt: "Lan laughs while Minh pours tea"
    camera:
      shot: medium shot
      angle: eye-level angle
      movement: slow dolly-in
      lens: 50mm lens
      focus: shallow depth of field
      lighting: warm window light
      speed: real-time speed
      notes: keep both faces in frame
    narration: "Chiều hôm ấy"
  - id: k2
    mode: keyframe
    prompt: "minh walks alone on the beach at sunset."
  - id: k3
    mode: keyframe
    image_prompt: "Hoa sits on a bench, Bao on her lap"
    prompt: "she strokes the cat slowly"
    duration: 6
  - id: k4
    mode: keyframe
    image_prompt: "   "
    prompt: "Lan, Hoa, Tuan and Bao run through the rain"
    model: veo-fast
  - id: t1
    mode: t2v
    prompt: "Tuan jumps into a puddle, LAN watches"
    camera:
      shot: wide shot
      movement: handheld camera
  - id: t2
    mode: t2v
    prompt: "Empty street at night, neon reflections"
    duration: 8
  - id: t3
    mode: t2v
    prompt: "Minh reads a letter"
  - id: i1
    mode: i2v
    image: {I2V_IMAGE}
    end_image: /golden/fixed/end.png
    prompt: "Lan turns around and smiles"
    camera:
      movement: orbit
      composition: rule of thirds
"""


def _norm(value, tmp_path: Path):
    s = json.dumps(value, ensure_ascii=False)
    for p in sorted({str(tmp_path.resolve()), str(tmp_path)}, key=len, reverse=True):
        s = s.replace(p, "<tmp>")
    return json.loads(s)


def _write_images(folder: Path):
    for name, data in [("lan.png", LAN_PNG), ("hoa.png", HOA_PNG), ("tuan.png", TUAN_PNG), ("bao.png", BAO_PNG)]:
        (folder / name).write_bytes(data)


def _sample(tmp_path: Path) -> Path:
    _write_images(tmp_path)
    p = tmp_path / "story.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


def _template(tmp_path: Path) -> Path:
    """The project template, as shipped, plus a reference image for Lan."""
    _write_images(tmp_path)
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    data["characters"][0]["image"] = "lan.png"
    p = tmp_path / "template.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def snapshot(sb_path: Path, tmp_path: Path) -> dict:
    sb = load_storyboard(sb_path)
    out = {}
    for s in sb.scenes:
        out[s.id] = {
            "image_prompt": sb.image_prompt(s),
            "video_prompt": sb.video_prompt(s),
            "text_only_prompt": sb.text_only_prompt(s),
            "scene_refs": [[n, str(p)] for n, p in sb.scene_refs(s)],
            "fingerprint": sb.scene_fingerprint(s),
        }
    return _norm(out, tmp_path)


@pytest.mark.parametrize("which", ["sample", "template"])
def test_prompts_refs_fingerprints_match_golden(which, tmp_path):
    path = _sample(tmp_path) if which == "sample" else _template(tmp_path)
    assert snapshot(path, tmp_path) == GOLDEN_PROMPTS[which]


def test_refs_off_and_missing_images_match_golden(tmp_path):
    """use_character_refs=false and reference images missing on disk: no refs, fingerprints without refs."""
    path = _sample(tmp_path)
    off = tmp_path / "off.yaml"
    off.write_text(SAMPLE.replace("image_model: nano2", "image_model: nano2\nuse_character_refs: false"))
    snap_off = snapshot(off, tmp_path)
    for f in ("lan.png", "hoa.png", "tuan.png", "bao.png"):
        (tmp_path / f).unlink()
    snap_missing = snapshot(path, tmp_path)
    assert snap_off == snap_missing == GOLDEN_NO_REFS


# --- AC11: gflow argv of the pipeline ------------------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


@pytest.fixture
def gflow_env(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(log))
    for var in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_CATALOG", "FAKE_GFLOW_NO_AUDIO", "FLOW_UI_CMD"):
        monkeypatch.delenv(var, raising=False)
    # Hoa's portrait is already in the Flow project: i2i must send its media id instead of the file.
    (tmp_path / "flow").mkdir()
    (tmp_path / "flow" / "hoa.jpg").write_bytes(HOA_PNG)
    cat = tmp_path / "catalog.jsonl"
    cat.write_text(json.dumps({"media_id": "media-hoa-0001", "project_id": "proj-123",
                               "local_path": str(tmp_path / "flow" / "hoa.jpg"), "prompt": "portrait"}))
    monkeypatch.setenv("FAKE_GFLOW_CATALOG", str(cat))

    def calls():
        lines = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return _norm([json.loads(l) for l in lines], tmp_path)

    return calls


def _pipe(sb_path: Path, out: Path) -> Pipeline:
    return Pipeline(load_storyboard(sb_path), out, Gflow(log=lambda *_: None, ui_enabled=False),
                    log=lambda *_: None, sleep=lambda *_: None)


def _sample_for_run(tmp_path: Path, *, i2v: bool = True) -> Path:
    """The sample with the i2v frames as real files in tmp (or without the i2v scene)."""
    p = _sample(tmp_path)
    data = yaml.safe_load(p.read_text())
    if i2v:
        (tmp_path / "start.png").write_bytes(b"start")
        (tmp_path / "end.png").write_bytes(b"end")
        for s in data["scenes"]:
            if s["mode"] == "i2v":
                s["image"], s["end_image"] = "start.png", "end.png"
    else:
        data["scenes"] = [s for s in data["scenes"] if s["mode"] != "i2v"]
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    return p


@needs_ffmpeg
def test_gflow_argv_legacy_matches_golden(gflow_env, tmp_path):
    """Keyframes (i2i with file / media-id refs, t2i), i2v, r2v and plain t2v."""
    p = _sample_for_run(tmp_path)
    report = _pipe(p, tmp_path / "out").run(RunOptions(pause=(0, 0)))
    assert report.ok
    assert gflow_env() == GOLDEN_ARGV["normal"]


@needs_ffmpeg
def test_gflow_argv_fallback_t2v_matches_golden(gflow_env, tmp_path, monkeypatch):
    """i2v refused before submit (exit 36) with fallback_t2v: keyframe scenes go through text_only_prompt + r2v."""
    p = _sample_for_run(tmp_path, i2v=False)
    p.write_text(p.read_text().replace("image_model: nano2", "image_model: nano2\nfallback_t2v: true"))
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    report = _pipe(p, tmp_path / "out").run(RunOptions(pause=(0, 0)))
    assert report.ok
    assert gflow_env() == GOLDEN_ARGV["fallback_t2v"]


@needs_ffmpeg
def test_gflow_argv_template_matches_golden(gflow_env, tmp_path):
    report = _pipe(_template(tmp_path), tmp_path / "out").run(RunOptions(pause=(0, 0)))
    assert report.ok
    assert gflow_env() == GOLDEN_ARGV["template"]


# ===== GOLDEN VALUES (recorded on the code before Scene.characters; do not regenerate) =====
GOLDEN_PROMPTS = {'sample': {'k1': {'image_prompt': 'Lan and Minh at a cafe table. Shot: medium shot, eye-level angle. Lens: 50mm '
                                   'lens. Focus: shallow depth of field. Lighting: warm window light. Direction: '
                                   'keep both faces in frame. Lan: short black hair, red scarf. Minh: tall young '
                                   'man, round glasses, grey hoodie.. Style: anime, soft pastel colors.',
                   'video_prompt': 'Lan laughs while Minh pours tea. Camera movement: slow dolly-in. Speed: '
                                   'real-time speed. Focus: shallow depth of field. Direction: keep both faces in '
                                   'frame.',
                   'text_only_prompt': 'Lan and Minh at a cafe table. Lan laughs while Minh pours tea. Shot: medium '
                                       'shot, eye-level angle. Camera movement: slow dolly-in. Speed: real-time '
                                       'speed. Lens: 50mm lens. Focus: shallow depth of field. Lighting: warm window '
                                       'light. Direction: keep both faces in frame. Lan: short black hair, red '
                                       'scarf. Minh: tall young man, round glasses, grey hoodie.. Style: anime, soft '
                                       'pastel colors.',
                   'scene_refs': [['Lan', '<tmp>/lan.png']],
                   'fingerprint': '6b48bfa970fe0617'},
            'k2': {'image_prompt': 'minh walks alone on the beach at sunset. Minh: tall young man, round glasses, '
                                   'grey hoodie.. Style: anime, soft pastel colors.',
                   'video_prompt': 'minh walks alone on the beach at sunset.',
                   'text_only_prompt': 'minh walks alone on the beach at sunset. Minh: tall young man, round '
                                       'glasses, grey hoodie.. Style: anime, soft pastel colors.',
                   'scene_refs': [],
                   'fingerprint': 'cfc399843ce99137'},
            'k3': {'image_prompt': 'Hoa sits on a bench, Bao on her lap. Hoa: elderly woman, white ao dai. Bao: a '
                                   'black cat with green eyes. Style: anime, soft pastel colors.',
                   'video_prompt': 'she strokes the cat slowly',
                   'text_only_prompt': 'Hoa sits on a bench, Bao on her lap. she strokes the cat slowly. Hoa: '
                                       'elderly woman, white ao dai. Bao: a black cat with green eyes. Style: anime, '
                                       'soft pastel colors.',
                   'scene_refs': [['Hoa', '<tmp>/hoa.png'], ['Bao', '<tmp>/bao.png']],
                   'fingerprint': 'b016f98382cf8881'},
            'k4': {'image_prompt': '. Style: anime, soft pastel colors.',
                   'video_prompt': 'Lan, Hoa, Tuan and Bao run through the rain',
                   'text_only_prompt': 'Lan, Hoa, Tuan and Bao run through the rain. Lan: short black hair, red '
                                       'scarf. Hoa: elderly woman, white ao dai. Tuan: boy of ten, yellow raincoat. '
                                       'Bao: a black cat with green eyes. Style: anime, soft pastel colors.',
                   'scene_refs': [['Lan', '<tmp>/lan.png'], ['Hoa', '<tmp>/hoa.png'], ['Tuan', '<tmp>/tuan.png']],
                   'fingerprint': '024800d89d730d85'},
            't1': {'image_prompt': 'Tuan jumps into a puddle, LAN watches. Shot: wide shot. Lan: short black hair, '
                                   'red scarf. Tuan: boy of ten, yellow raincoat. Style: anime, soft pastel colors.',
                   'video_prompt': 'Tuan jumps into a puddle, LAN watches. Shot: wide shot. Camera movement: '
                                   'handheld camera. Lan: short black hair, red scarf. Tuan: boy of ten, yellow '
                                   'raincoat. Style: anime, soft pastel colors.',
                   'text_only_prompt': 'Tuan jumps into a puddle, LAN watches. Shot: wide shot. Camera movement: '
                                       'handheld camera. Lan: short black hair, red scarf. Tuan: boy of ten, yellow '
                                       'raincoat. Style: anime, soft pastel colors.',
                   'scene_refs': [['Lan', '<tmp>/lan.png'], ['Tuan', '<tmp>/tuan.png']],
                   'fingerprint': '7641901cf21c78f1'},
            't2': {'image_prompt': 'Empty street at night, neon reflections. Style: anime, soft pastel colors.',
                   'video_prompt': 'Empty street at night, neon reflections. Style: anime, soft pastel colors.',
                   'text_only_prompt': 'Empty street at night, neon reflections. Style: anime, soft pastel colors.',
                   'scene_refs': [],
                   'fingerprint': '2ecd04a5fcff0890'},
            't3': {'image_prompt': 'Minh reads a letter. Minh: tall young man, round glasses, grey hoodie.. Style: '
                                   'anime, soft pastel colors.',
                   'video_prompt': 'Minh reads a letter. Minh: tall young man, round glasses, grey hoodie.. Style: '
                                   'anime, soft pastel colors.',
                   'text_only_prompt': 'Minh reads a letter. Minh: tall young man, round glasses, grey hoodie.. '
                                       'Style: anime, soft pastel colors.',
                   'scene_refs': [],
                   'fingerprint': 'f4b1e022d9872822'},
            'i1': {'image_prompt': 'Lan turns around and smiles. Composition: rule of thirds. Lan: short black hair, '
                                   'red scarf. Style: anime, soft pastel colors.',
                   'video_prompt': 'Lan turns around and smiles. Camera movement: orbit.',
                   'text_only_prompt': 'Lan turns around and smiles. Camera movement: orbit. Composition: rule of '
                                       'thirds. Lan: short black hair, red scarf. Style: anime, soft pastel colors.',
                   'scene_refs': [],
                   'fingerprint': '4e715f66ff5e490b'}},
 'template': {'s01': {'image_prompt': 'A small Hanoi street cafe at dawn, empty plastic stools, steam rising from a '
                                      'kettle. Shot: establishing shot, high angle. Lens: 24mm wide-angle lens. '
                                      'Lighting: soft blue dawn light. Style: cinematic, warm morning light, shallow '
                                      'depth of field, 35mm film grain.',
                      'video_prompt': 'The first motorbikes pass by the cafe, ambient street sounds. Camera '
                                      'movement: slow dolly-in.',
                      'text_only_prompt': 'A small Hanoi street cafe at dawn, empty plastic stools, steam rising '
                                          'from a kettle. The first motorbikes pass by the cafe, ambient street '
                                          'sounds. Shot: establishing shot, high angle. Camera movement: slow '
                                          'dolly-in. Lens: 24mm wide-angle lens. Lighting: soft blue dawn light. '
                                          'Style: cinematic, warm morning light, shallow depth of field, 35mm film '
                                          'grain.',
                      'scene_refs': [],
                      'fingerprint': '68215192315d7c26'},
              's02': {'image_prompt': 'Lan sitting at a low table, holding a glass of Vietnamese drip coffee, coffee '
                                      'dripping in the foreground. Shot: medium close-up, eye-level angle. Lens: '
                                      '85mm portrait lens. Focus: rack focus from foreground to background. '
                                      'Composition: rule of thirds. Lan: a Vietnamese woman in her late 20s, '
                                      'shoulder-length black hair, cream linen shirt, small silver earrings. Style: '
                                      'cinematic, warm morning light, shallow depth of field, 35mm film grain.',
                      'video_prompt': 'Lan lifts the glass and smiles softly. Camera movement: handheld camera, '
                                      'subtle shake. Focus: rack focus from foreground to background.',
                      'text_only_prompt': 'Lan sitting at a low table, holding a glass of Vietnamese drip coffee, '
                                          'coffee dripping in the foreground. Lan lifts the glass and smiles softly. '
                                          'Shot: medium close-up, eye-level angle. Camera movement: handheld camera, '
                                          'subtle shake. Lens: 85mm portrait lens. Focus: rack focus from foreground '
                                          'to background. Composition: rule of thirds. Lan: a Vietnamese woman in '
                                          'her late 20s, shoulder-length black hair, cream linen shirt, small silver '
                                          'earrings. Style: cinematic, warm morning light, shallow depth of field, '
                                          '35mm film grain.',
                      'scene_refs': [['Lan', '<tmp>/lan.png']],
                      'fingerprint': 'ed9fbb33c7ceedf5'},
              's03': {'image_prompt': "Condensed milk swirling into dark coffee. Shot: extreme close-up, bird's-eye "
                                      'view, top-down. Lens: macro lens. Style: cinematic, warm morning light, '
                                      'shallow depth of field, 35mm film grain.',
                      'video_prompt': "Condensed milk swirling into dark coffee. Shot: extreme close-up, bird's-eye "
                                      'view, top-down. Camera movement: static locked-off camera. Speed: slow '
                                      'motion. Lens: macro lens. Style: cinematic, warm morning light, shallow depth '
                                      'of field, 35mm film grain.',
                      'text_only_prompt': 'Condensed milk swirling into dark coffee. Shot: extreme close-up, '
                                          "bird's-eye view, top-down. Camera movement: static locked-off camera. "
                                          'Speed: slow motion. Lens: macro lens. Style: cinematic, warm morning '
                                          'light, shallow depth of field, 35mm film grain.',
                      'scene_refs': [],
                      'fingerprint': '2b7bc34444a1147d'}}}
GOLDEN_NO_REFS = {'k1': {'image_prompt': 'Lan and Minh at a cafe table. Shot: medium shot, eye-level angle. Lens: 50mm lens. Focus: '
                        'shallow depth of field. Lighting: warm window light. Direction: keep both faces in frame. '
                        'Lan: short black hair, red scarf. Minh: tall young man, round glasses, grey hoodie.. Style: '
                        'anime, soft pastel colors.',
        'video_prompt': 'Lan laughs while Minh pours tea. Camera movement: slow dolly-in. Speed: real-time speed. '
                        'Focus: shallow depth of field. Direction: keep both faces in frame.',
        'text_only_prompt': 'Lan and Minh at a cafe table. Lan laughs while Minh pours tea. Shot: medium shot, '
                            'eye-level angle. Camera movement: slow dolly-in. Speed: real-time speed. Lens: 50mm '
                            'lens. Focus: shallow depth of field. Lighting: warm window light. Direction: keep both '
                            'faces in frame. Lan: short black hair, red scarf. Minh: tall young man, round glasses, '
                            'grey hoodie.. Style: anime, soft pastel colors.',
        'scene_refs': [],
        'fingerprint': '8c93b5a1a893926a'},
 'k2': {'image_prompt': 'minh walks alone on the beach at sunset. Minh: tall young man, round glasses, grey hoodie.. '
                        'Style: anime, soft pastel colors.',
        'video_prompt': 'minh walks alone on the beach at sunset.',
        'text_only_prompt': 'minh walks alone on the beach at sunset. Minh: tall young man, round glasses, grey '
                            'hoodie.. Style: anime, soft pastel colors.',
        'scene_refs': [],
        'fingerprint': 'cfc399843ce99137'},
 'k3': {'image_prompt': 'Hoa sits on a bench, Bao on her lap. Hoa: elderly woman, white ao dai. Bao: a black cat '
                        'with green eyes. Style: anime, soft pastel colors.',
        'video_prompt': 'she strokes the cat slowly',
        'text_only_prompt': 'Hoa sits on a bench, Bao on her lap. she strokes the cat slowly. Hoa: elderly woman, '
                            'white ao dai. Bao: a black cat with green eyes. Style: anime, soft pastel colors.',
        'scene_refs': [],
        'fingerprint': 'b31ccdd5e6b549f0'},
 'k4': {'image_prompt': '. Style: anime, soft pastel colors.',
        'video_prompt': 'Lan, Hoa, Tuan and Bao run through the rain',
        'text_only_prompt': 'Lan, Hoa, Tuan and Bao run through the rain. Lan: short black hair, red scarf. Hoa: '
                            'elderly woman, white ao dai. Tuan: boy of ten, yellow raincoat. Bao: a black cat with '
                            'green eyes. Style: anime, soft pastel colors.',
        'scene_refs': [],
        'fingerprint': '58baf4591e7bf645'},
 't1': {'image_prompt': 'Tuan jumps into a puddle, LAN watches. Shot: wide shot. Lan: short black hair, red scarf. '
                        'Tuan: boy of ten, yellow raincoat. Style: anime, soft pastel colors.',
        'video_prompt': 'Tuan jumps into a puddle, LAN watches. Shot: wide shot. Camera movement: handheld camera. '
                        'Lan: short black hair, red scarf. Tuan: boy of ten, yellow raincoat. Style: anime, soft '
                        'pastel colors.',
        'text_only_prompt': 'Tuan jumps into a puddle, LAN watches. Shot: wide shot. Camera movement: handheld '
                            'camera. Lan: short black hair, red scarf. Tuan: boy of ten, yellow raincoat. Style: '
                            'anime, soft pastel colors.',
        'scene_refs': [],
        'fingerprint': '9ba89630a8ac3c3b'},
 't2': {'image_prompt': 'Empty street at night, neon reflections. Style: anime, soft pastel colors.',
        'video_prompt': 'Empty street at night, neon reflections. Style: anime, soft pastel colors.',
        'text_only_prompt': 'Empty street at night, neon reflections. Style: anime, soft pastel colors.',
        'scene_refs': [],
        'fingerprint': '2ecd04a5fcff0890'},
 't3': {'image_prompt': 'Minh reads a letter. Minh: tall young man, round glasses, grey hoodie.. Style: anime, soft '
                        'pastel colors.',
        'video_prompt': 'Minh reads a letter. Minh: tall young man, round glasses, grey hoodie.. Style: anime, soft '
                        'pastel colors.',
        'text_only_prompt': 'Minh reads a letter. Minh: tall young man, round glasses, grey hoodie.. Style: anime, '
                            'soft pastel colors.',
        'scene_refs': [],
        'fingerprint': 'f4b1e022d9872822'},
 'i1': {'image_prompt': 'Lan turns around and smiles. Composition: rule of thirds. Lan: short black hair, red scarf. '
                        'Style: anime, soft pastel colors.',
        'video_prompt': 'Lan turns around and smiles. Camera movement: orbit.',
        'text_only_prompt': 'Lan turns around and smiles. Camera movement: orbit. Composition: rule of thirds. Lan: '
                            'short black hair, red scarf. Style: anime, soft pastel colors.',
        'scene_refs': [],
        'fingerprint': '4e715f66ff5e490b'}}
GOLDEN_ARGV = {'normal': [['project', 'create', '--name', 'Golden sample', '--json'],
            ['image',
             'i2i',
             'Lan and Minh at a cafe table. Shot: medium shot, eye-level angle. Lens: 50mm lens. Focus: shallow '
             'depth of field. Lighting: warm window light. Direction: keep both faces in frame. Lan: short black '
             'hair, red scarf. Minh: tall young man, round glasses, grey hoodie.. Style: anime, soft pastel colors. '
             'No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; '
             'any dialogue is spoken audio only.',
             '--ref',
             '<tmp>/lan.png',
             '--aspect',
             '9:16',
             '--model',
             'nano2',
             '-o',
             '<tmp>/out/keyframes/k1.png',
             '--project',
             'proj-123',
             '--json'],
            ['video',
             'i2v',
             '--initial-frame',
             '<tmp>/out/keyframes/k1.png',
             'Lan laughs while Minh pours tea. Camera movement: slow dolly-in. Speed: real-time speed. Focus: '
             'shallow depth of field. Direction: keep both faces in frame. No captions, no subtitles, no on-screen '
             'text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.',
             '--aspect',
             '9:16',
             '-o',
             '<tmp>/out/clips/k1.mp4',
             '--model',
             'veo-lite',
             '--project',
             'proj-123',
             '--json'],
            ['image',
             't2i',
             'minh walks alone on the beach at sunset. Minh: tall young man, round glasses, grey hoodie.. Style: '
             'anime, soft pastel colors. No captions, no subtitles, no on-screen text or lettering, no title cards, '
             'no watermarks, no logos; any dialogue is spoken audio only.',
             '--aspect',
             '9:16',
             '--model',
             'nano2',
             '-o',
             '<tmp>/out/keyframes/k2.png',
             '--project',
             'proj-123',
             '--json'],
            ['video',
             'i2v',
             '--initial-frame',
             '<tmp>/out/keyframes/k2.png',
             'minh walks alone on the beach at sunset. No captions, no subtitles, no on-screen text or lettering, no '
             'title cards, no watermarks, no logos; any dialogue is spoken audio only.',
             '--aspect',
             '9:16',
             '-o',
             '<tmp>/out/clips/k2.mp4',
             '--model',
             'veo-lite',
             '--project',
             'proj-123',
             '--json'],
            ['image',
             'i2i',
             'Hoa sits on a bench, Bao on her lap. Hoa: elderly woman, white ao dai. Bao: a black cat with green '
             'eyes. Style: anime, soft pastel colors. No captions, no subtitles, no on-screen text or lettering, no '
             'title cards, no watermarks, no logos; any dialogue is spoken audio only.',
             '--ref',
             'media-hoa-0001',
             '--ref',
             '<tmp>/bao.png',
             '--aspect',
             '9:16',
             '--model',
             'nano2',
             '-o',
             '<tmp>/out/keyframes/k3.png',
             '--project',
             'proj-123',
             '--json'],
            ['video',
             'i2v',
             '--initial-frame',
             '<tmp>/out/keyframes/k3.png',
             'she strokes the cat slowly. No captions, no subtitles, no on-screen text or lettering, no title cards, '
             'no watermarks, no logos; any dialogue is spoken audio only.',
             '--aspect',
             '9:16',
             '-o',
             '<tmp>/out/clips/k3.mp4',
             '--model',
             'veo-lite',
             '--duration',
             '6',
             '--project',
             'proj-123',
             '--json'],
            ['image',
             'i2i',
             '. Style: anime, soft pastel colors. No captions, no subtitles, no on-screen text or lettering, no '
             'title cards, no watermarks, no logos; any dialogue is spoken audio only.',
             '--ref',
             '<tmp>/lan.png',
             '--ref',
             'media-hoa-0001',
             '--ref',
             '<tmp>/tuan.png',
             '--aspect',
             '9:16',
             '--model',
             'nano2',
             '-o',
             '<tmp>/out/keyframes/k4.png',
             '--project',
             'proj-123',
             '--json'],
            ['video',
             'i2v',
             '--initial-frame',
             '<tmp>/out/keyframes/k4.png',
             'Lan, Hoa, Tuan and Bao run through the rain. No captions, no subtitles, no on-screen text or '
             'lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.',
             '--aspect',
             '9:16',
             '-o',
             '<tmp>/out/clips/k4.mp4',
             '--model',
             'veo-fast',
             '--project',
             'proj-123',
             '--json'],
            ['video',
             'r2v',
             'Tuan jumps into a puddle, LAN watches. Shot: wide shot. Camera movement: handheld camera. Lan: short '
             'black hair, red scarf. Tuan: boy of ten, yellow raincoat. Style: anime, soft pastel colors. No '
             'captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any '
             'dialogue is spoken audio only.',
             '--ref',
             '<tmp>/lan.png',
             '--ref',
             '<tmp>/tuan.png',
             '--aspect',
             '9:16',
             '-o',
             '<tmp>/out/clips/t1.mp4',
             '--model',
             'veo-lite',
             '--project',
             'proj-123',
             '--json'],
            ['video',
             't2v',
             'Empty street at night, neon reflections. Style: anime, soft pastel colors. No captions, no subtitles, '
             'no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio '
             'only.',
             '--aspect',
             '9:16',
             '-o',
             '<tmp>/out/clips/t2.mp4',
             '--model',
             'veo-lite',
             '--duration',
             '8',
             '--project',
             'proj-123',
             '--json'],
            ['video',
             't2v',
             'Minh reads a letter. Minh: tall young man, round glasses, grey hoodie.. Style: anime, soft pastel '
             'colors. No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no '
             'logos; any dialogue is spoken audio only.',
             '--aspect',
             '9:16',
             '-o',
             '<tmp>/out/clips/t3.mp4',
             '--model',
             'veo-lite',
             '--project',
             'proj-123',
             '--json'],
            ['video',
             'i2v',
             '--initial-frame',
             '<tmp>/start.png',
             '--end-frame',
             '<tmp>/end.png',
             'Lan turns around and smiles. Camera movement: orbit. No captions, no subtitles, no on-screen text or '
             'lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.',
             '--aspect',
             '9:16',
             '-o',
             '<tmp>/out/clips/i1.mp4',
             '--model',
             'veo-lite',
             '--project',
             'proj-123',
             '--json']],
 'fallback_t2v': [['project', 'create', '--name', 'Golden sample', '--json'],
                  ['image',
                   'i2i',
                   'Lan and Minh at a cafe table. Shot: medium shot, eye-level angle. Lens: 50mm lens. Focus: '
                   'shallow depth of field. Lighting: warm window light. Direction: keep both faces in frame. Lan: '
                   'short black hair, red scarf. Minh: tall young man, round glasses, grey hoodie.. Style: anime, '
                   'soft pastel colors. No captions, no subtitles, no on-screen text or lettering, no title cards, '
                   'no watermarks, no logos; any dialogue is spoken audio only.',
                   '--ref',
                   '<tmp>/lan.png',
                   '--aspect',
                   '9:16',
                   '--model',
                   'nano2',
                   '-o',
                   '<tmp>/out/keyframes/k1.png',
                   '--project',
                   'proj-123',
                   '--json'],
                  ['video',
                   'i2v',
                   '--initial-frame',
                   '<tmp>/out/keyframes/k1.png',
                   'Lan laughs while Minh pours tea. Camera movement: slow dolly-in. Speed: real-time speed. Focus: '
                   'shallow depth of field. Direction: keep both faces in frame. No captions, no subtitles, no '
                   'on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken '
                   'audio only.',
                   '--aspect',
                   '9:16',
                   '-o',
                   '<tmp>/out/clips/k1.mp4',
                   '--model',
                   'veo-lite',
                   '--project',
                   'proj-123',
                   '--json'],
                  ['video',
                   'r2v',
                   'Lan and Minh at a cafe table. Lan laughs while Minh pours tea. Shot: medium shot, eye-level '
                   'angle. Camera movement: slow dolly-in. Speed: real-time speed. Lens: 50mm lens. Focus: shallow '
                   'depth of field. Lighting: warm window light. Direction: keep both faces in frame. Lan: short '
                   'black hair, red scarf. Minh: tall young man, round glasses, grey hoodie.. Style: anime, soft '
                   'pastel colors. No captions, no subtitles, no on-screen text or lettering, no title cards, no '
                   'watermarks, no logos; any dialogue is spoken audio only.',
                   '--ref',
                   '<tmp>/lan.png',
                   '--aspect',
                   '9:16',
                   '-o',
                   '<tmp>/out/clips/k1.mp4',
                   '--model',
                   'veo-lite',
                   '--project',
                   'proj-123',
                   '--json'],
                  ['video',
                   't2v',
                   'minh walks alone on the beach at sunset. Minh: tall young man, round glasses, grey hoodie.. '
                   'Style: anime, soft pastel colors. No captions, no subtitles, no on-screen text or lettering, no '
                   'title cards, no watermarks, no logos; any dialogue is spoken audio only.',
                   '--aspect',
                   '9:16',
                   '-o',
                   '<tmp>/out/clips/k2.mp4',
                   '--model',
                   'veo-lite',
                   '--project',
                   'proj-123',
                   '--json'],
                  ['video',
                   'r2v',
                   'Hoa sits on a bench, Bao on her lap. she strokes the cat slowly. Hoa: elderly woman, white ao '
                   'dai. Bao: a black cat with green eyes. Style: anime, soft pastel colors. No captions, no '
                   'subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue '
                   'is spoken audio only.',
                   '--ref',
                   '<tmp>/hoa.png',
                   '--ref',
                   '<tmp>/bao.png',
                   '--aspect',
                   '9:16',
                   '-o',
                   '<tmp>/out/clips/k3.mp4',
                   '--model',
                   'veo-lite',
                   '--duration',
                   '6',
                   '--project',
                   'proj-123',
                   '--json'],
                  ['video',
                   'r2v',
                   'Lan, Hoa, Tuan and Bao run through the rain. Lan: short black hair, red scarf. Hoa: elderly '
                   'woman, white ao dai. Tuan: boy of ten, yellow raincoat. Bao: a black cat with green eyes. Style: '
                   'anime, soft pastel colors. No captions, no subtitles, no on-screen text or lettering, no title '
                   'cards, no watermarks, no logos; any dialogue is spoken audio only.',
                   '--ref',
                   '<tmp>/lan.png',
                   '--ref',
                   '<tmp>/hoa.png',
                   '--ref',
                   '<tmp>/tuan.png',
                   '--aspect',
                   '9:16',
                   '-o',
                   '<tmp>/out/clips/k4.mp4',
                   '--model',
                   'veo-fast',
                   '--project',
                   'proj-123',
                   '--json'],
                  ['video',
                   'r2v',
                   'Tuan jumps into a puddle, LAN watches. Shot: wide shot. Camera movement: handheld camera. Lan: '
                   'short black hair, red scarf. Tuan: boy of ten, yellow raincoat. Style: anime, soft pastel '
                   'colors. No captions, no subtitles, no on-screen text or lettering, no title cards, no '
                   'watermarks, no logos; any dialogue is spoken audio only.',
                   '--ref',
                   '<tmp>/lan.png',
                   '--ref',
                   '<tmp>/tuan.png',
                   '--aspect',
                   '9:16',
                   '-o',
                   '<tmp>/out/clips/t1.mp4',
                   '--model',
                   'veo-lite',
                   '--project',
                   'proj-123',
                   '--json'],
                  ['video',
                   't2v',
                   'Empty street at night, neon reflections. Style: anime, soft pastel colors. No captions, no '
                   'subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue '
                   'is spoken audio only.',
                   '--aspect',
                   '9:16',
                   '-o',
                   '<tmp>/out/clips/t2.mp4',
                   '--model',
                   'veo-lite',
                   '--duration',
                   '8',
                   '--project',
                   'proj-123',
                   '--json'],
                  ['video',
                   't2v',
                   'Minh reads a letter. Minh: tall young man, round glasses, grey hoodie.. Style: anime, soft '
                   'pastel colors. No captions, no subtitles, no on-screen text or lettering, no title cards, no '
                   'watermarks, no logos; any dialogue is spoken audio only.',
                   '--aspect',
                   '9:16',
                   '-o',
                   '<tmp>/out/clips/t3.mp4',
                   '--model',
                   'veo-lite',
                   '--project',
                   'proj-123',
                   '--json']],
 'template': [['project', 'create', '--name', 'Ly cà phê đầu tiên', '--json'],
              ['image',
               't2i',
               'A small Hanoi street cafe at dawn, empty plastic stools, steam rising from a kettle. Shot: '
               'establishing shot, high angle. Lens: 24mm wide-angle lens. Lighting: soft blue dawn light. Style: '
               'cinematic, warm morning light, shallow depth of field, 35mm film grain. No captions, no subtitles, '
               'no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken '
               'audio only.',
               '--aspect',
               '16:9',
               '--model',
               'nano2',
               '-o',
               '<tmp>/out/keyframes/s01.png',
               '--project',
               'proj-123',
               '--json'],
              ['video',
               'i2v',
               '--initial-frame',
               '<tmp>/out/keyframes/s01.png',
               'The first motorbikes pass by the cafe, ambient street sounds. Camera movement: slow dolly-in. No '
               'captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any '
               'dialogue is spoken audio only.',
               '--aspect',
               '16:9',
               '-o',
               '<tmp>/out/clips/s01.mp4',
               '--model',
               'veo-lite',
               '--project',
               'proj-123',
               '--json'],
              ['image',
               'i2i',
               'Lan sitting at a low table, holding a glass of Vietnamese drip coffee, coffee dripping in the '
               'foreground. Shot: medium close-up, eye-level angle. Lens: 85mm portrait lens. Focus: rack focus from '
               'foreground to background. Composition: rule of thirds. Lan: a Vietnamese woman in her late 20s, '
               'shoulder-length black hair, cream linen shirt, small silver earrings. Style: cinematic, warm morning '
               'light, shallow depth of field, 35mm film grain. No captions, no subtitles, no on-screen text or '
               'lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.',
               '--ref',
               '<tmp>/lan.png',
               '--aspect',
               '16:9',
               '--model',
               'nano2',
               '-o',
               '<tmp>/out/keyframes/s02.png',
               '--project',
               'proj-123',
               '--json'],
              ['video',
               'i2v',
               '--initial-frame',
               '<tmp>/out/keyframes/s02.png',
               'Lan lifts the glass and smiles softly. Camera movement: handheld camera, subtle shake. Focus: rack '
               'focus from foreground to background. No captions, no subtitles, no on-screen text or lettering, no '
               'title cards, no watermarks, no logos; any dialogue is spoken audio only.',
               '--aspect',
               '16:9',
               '-o',
               '<tmp>/out/clips/s02.mp4',
               '--model',
               'veo-lite',
               '--duration',
               '8',
               '--project',
               'proj-123',
               '--json'],
              ['video',
               't2v',
               "Condensed milk swirling into dark coffee. Shot: extreme close-up, bird's-eye view, top-down. Camera "
               'movement: static locked-off camera. Speed: slow motion. Lens: macro lens. Style: cinematic, warm '
               'morning light, shallow depth of field, 35mm film grain. No captions, no subtitles, no on-screen text '
               'or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.',
               '--aspect',
               '16:9',
               '-o',
               '<tmp>/out/clips/s03.mp4',
               '--model',
               'veo-lite',
               '--project',
               'proj-123',
               '--json']]}
