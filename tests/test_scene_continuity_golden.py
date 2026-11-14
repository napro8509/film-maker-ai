"""Golden snapshot (T0 of scene-continuity, AC1/AC2): prompts, references, fingerprints, gflow argv, log lines and
state keys of a storyboard WITHOUT `locations` (the structure of the-inheritance-war), recorded on the code before
locations / keyframe chaining existed.

The GOLDEN_* constants are fixed values: never recompute them from the code. If one of these tests fails, the
change altered what old storyboards send to Flow, what they log, or marks finished scenes as changed.
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
FIXTURE = Path(__file__).parent / "fixtures" / "scene_continuity" / "inheritance.yaml"
CHAR_BYTES = {n: f"golden-continuity-{n}-v1".encode() for n in ("minh", "lan", "tuan", "hoa", "mother")}

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def _norm(value, tmp_path: Path):
    """Replace paths that differ per machine (tmp dir, Python, this checkout) with placeholders."""
    s = json.dumps(value, ensure_ascii=False)
    s = s.replace(str(FAKE.resolve()), "<fake_gflow>").replace(str(FAKE), "<fake_gflow>")
    s = s.replace(sys.executable, "<python>")
    for p in sorted({str(tmp_path.resolve()), str(tmp_path)}, key=len, reverse=True):
        s = s.replace(p, "<tmp>")
    return json.loads(s)


def make_project(tmp_path: Path, *, extra: dict | None = None) -> Path:
    """The fixture in tmp with fixed character pictures; `extra` is merged into the top-level YAML keys."""
    chars = tmp_path / "assets" / "characters"
    chars.mkdir(parents=True, exist_ok=True)
    for n, data in CHAR_BYTES.items():
        (chars / f"{n}.png").write_bytes(data)
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    if extra:
        data.update(extra)
    p = tmp_path / "storyboard.yaml"
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
            "refs_signature": sb.refs_signature(s),
            "fingerprint": sb.scene_fingerprint(s),
        }
    return _norm(out, tmp_path)


def test_old_storyboard_matches_baseline(tmp_path):
    assert snapshot(make_project(tmp_path), tmp_path) == GOLDEN_SNAPSHOT


def _keyframes_run(sb_path: Path, tmp_path: Path, monkeypatch, *, dry: bool = False) -> dict:
    log = tmp_path / "calls.log"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(log))
    for var in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_CATALOG", "FAKE_GFLOW_NO_AUDIO", "FLOW_UI_CMD"):
        monkeypatch.delenv(var, raising=False)
    lines: list[str] = []
    pipe = Pipeline(load_storyboard(sb_path), tmp_path / "out",
                    Gflow(log=lines.append, ui_enabled=False, dry_run=dry),
                    log=lines.append, sleep=lambda *_: None)
    report = pipe.run(RunOptions(pause=(0, 0), keyframes_only=True))
    assert report.ok, report
    calls = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    state = json.loads((tmp_path / "out" / "state.json").read_text()) if (tmp_path / "out" / "state.json").exists() else {}
    keys = {sid: sorted(st) for sid, st in state.get("scenes", {}).items()}
    sb = load_storyboard(sb_path)
    has_kf = {s.id: pipe.has_keyframe(s) for s in sb.scenes}
    return _norm({"calls": calls, "log": lines, "state_keys": keys, "has_keyframe": has_kf}, tmp_path)


@needs_ffmpeg
def test_old_storyboard_keyframes_run_matches_baseline(tmp_path, monkeypatch):
    """gflow argv, every log line, the state keys written and has_keyframe after `run --keyframes-only`."""
    assert _keyframes_run(make_project(tmp_path), tmp_path, monkeypatch) == GOLDEN_KEYFRAMES_RUN


def test_old_storyboard_dry_run_log_matches_baseline(tmp_path, monkeypatch):
    """`run --dry-run` of the whole film (keyframes, i2v, t2v with references): the log lines."""
    monkeypatch.setenv("GFLOW_BIN", "gflow")  # never run in dry-run; only shown in the "$ ..." lines
    p = make_project(tmp_path)
    lines: list[str] = []
    pipe = Pipeline(load_storyboard(p), tmp_path / "out", Gflow(log=lines.append, ui_enabled=False, dry_run=True),
                    log=lines.append, sleep=lambda *_: None)
    report = pipe.run(RunOptions(pause=(0, 0)))
    assert report.ok, report
    assert not (tmp_path / "out" / "state.json").exists()
    assert _norm(lines, tmp_path) == GOLDEN_DRY_LOG


def test_locations_without_assignment_keep_fingerprints(tmp_path):
    """AC2: `locations` (with a picture) and `continuity` in the YAML, but no scene has `location`: the same
    snapshot, and scenes that were done (clip + keyframe) on the old storyboard stay done with their keyframes."""
    old = load_storyboard(make_project(tmp_path))
    out = tmp_path / "out"
    state = {"project_id": "proj-123", "scenes": {}}
    for s in old.scenes:
        clip, kf = out / "clips" / f"{s.id}.mp4", out / "keyframes" / f"{s.id}.png"
        for f in (clip, kf):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x")
        state["scenes"][s.id] = {"status": "done", "video": str(clip), "fingerprint": old.scene_fingerprint(s),
                                 "keyframe": str(kf), "keyframe_prompt": old.image_prompt(s),
                                 "keyframe_refs": old.refs_signature(s)}
    (out / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "assets" / "locations").mkdir(parents=True)
    (tmp_path / "assets" / "locations" / "room.png").write_bytes(b"room")
    p = make_project(tmp_path, extra={"continuity": True, "locations": [
        {"name": "Phòng khách nhà cũ", "description": "long dark teak table, brass lamp",
         "image": "assets/locations/room.png"}]})
    assert snapshot(p, tmp_path) == GOLDEN_SNAPSHOT
    sb = load_storyboard(p)
    pipe = Pipeline(sb, out, Gflow(log=lambda *_: None, ui_enabled=False, dry_run=True), log=lambda *_: None)
    assert {s.id: pipe.scene_status(s) for s in sb.scenes} == {s.id: "done" for s in sb.scenes}
    keyframe_ids = [s.id for s in sb.scenes if s.mode == "keyframe"]
    assert [s.id for s in sb.scenes if pipe.has_keyframe(s) and s.mode == "keyframe"] == keyframe_ids


# ===== GOLDEN VALUES (recorded on the code before locations / keyframe chaining; do not regenerate) =====
# Re-recorded once when the character-reference cap was removed: scenes with more than 3 pictured characters
# now send every one of them, so scene_refs / refs_signature / fingerprint of s02, s11, s14, s15, s20, x3 grew
# (the prompts are untouched). This is the only sanctioned regeneration; keep these fixed from here on.
GOLDEN_SNAPSHOT = {'s01': {'image_prompt': 'Ancestral altar in a traditional Vietnamese family house, framed black-and-white portrait of '
                         'an elderly man surrounded by incense smoke, candles and white chrysanthemums, dim warm '
                         'light, 50mm lens. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm '
                         'tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, '
                         'high-contrast amber and teal color grade, fast-paced tense dramatic energy.',
         'video_prompt': "Slow push-in toward the father's portrait on the altar as incense smoke curls upward; "
                         'distant funeral gong and soft crying.',
         'text_only_prompt': 'Ancestral altar in a traditional Vietnamese family house, framed black-and-white '
                             'portrait of an elderly man surrounded by incense smoke, candles and white '
                             "chrysanthemums, dim warm light, 50mm lens. Slow push-in toward the father's portrait on "
                             'the altar as incense smoke curls upward; distant funeral gong and soft crying. Style: '
                             'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light '
                             'with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
                             'color grade, fast-paced tense dramatic energy.',
         'scene_refs': [],
         'refs_signature': [],
         'fingerprint': '6e2b7e9859b3756d'},
 's02': {'image_prompt': 'Wooden living room of an old Vietnamese house at night, Minh and Lan sit on one side of a '
                         'carved wooden table, Tuan and Hoa on the other, Mother sits at the head clutching prayer '
                         'beads, a thick envelope lies in the center, tense lighting from a single hanging lamp, 24mm '
                         'lens. Minh: Vietnamese man, 45, elder brother, stocky build, short neat black hair greying '
                         'at the temples, thick eyebrows, stern square face, wears a black long-sleeve dress shirt '
                         "with a white mourning band on his left arm. Lan: Vietnamese woman, 42, Minh's wife, slim, "
                         'sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy '
                         'blouse and black trousers, red lipstick, calculating eyes. Tuan: Vietnamese man, 38, younger '
                         'brother, lean and tall, messy medium-length black hair, light stubble, hot-tempered '
                         'expression, white mourning headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, '
                         "35, Tuan's wife, petite, long straight black hair worn loose, round face, large jade bangle "
                         'on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Mother: '
                         'Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep wrinkles, gentle sad '
                         'eyes, white traditional mourning ao ba ba, holds wooden prayer beads. Style: Cinematic '
                         'Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep '
                         'shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, '
                         'fast-paced tense dramatic energy.',
         'video_prompt': "Slow overhead crane down onto the table as Mother's trembling hand slides the envelope to "
                         'the center; clock ticking loudly.',
         'text_only_prompt': 'Wooden living room of an old Vietnamese house at night, Minh and Lan sit on one side of '
                             'a carved wooden table, Tuan and Hoa on the other, Mother sits at the head clutching '
                             'prayer beads, a thick envelope lies in the center, tense lighting from a single hanging '
                             "lamp, 24mm lens. Slow overhead crane down onto the table as Mother's trembling hand "
                             'slides the envelope to the center; clock ticking loudly. Minh: Vietnamese man, 45, elder '
                             'brother, stocky build, short neat black hair greying at the temples, thick eyebrows, '
                             'stern square face, wears a black long-sleeve dress shirt with a white mourning band on '
                             "his left arm. Lan: Vietnamese woman, 42, Minh's wife, slim, sharp cheekbones, black hair "
                             'in a tight low bun, thin gold necklace, fitted dark burgundy blouse and black trousers, '
                             'red lipstick, calculating eyes. Tuan: Vietnamese man, 38, younger brother, lean and '
                             'tall, messy medium-length black hair, light stubble, hot-tempered expression, white '
                             "mourning headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, 35, Tuan's "
                             'wife, petite, long straight black hair worn loose, round face, large jade bangle on '
                             'right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Mother: '
                             'Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep wrinkles, gentle '
                             'sad eyes, white traditional mourning ao ba ba, holds wooden prayer beads. Style: '
                             'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light '
                             'with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
                             'color grade, fast-paced tense dramatic energy.',
         'scene_refs': [['Minh', '<tmp>/assets/characters/minh.png'],
                        ['Lan', '<tmp>/assets/characters/lan.png'],
                        ['Tuan', '<tmp>/assets/characters/tuan.png'],
                        ['Hoa', '<tmp>/assets/characters/hoa.png'],
                        ['Mother', '<tmp>/assets/characters/mother.png']],
         'refs_signature': ['Minh:e0129c7986c9',
                            'Lan:c6448b57ea9a',
                            'Tuan:4562b732a8e5',
                            'Hoa:e8ec20958fc9',
                            'Mother:260713d1a03a'],
         'fingerprint': '36f6b7ca631d5031'},
 's03': {'image_prompt': 'Close-up of Minh opening a thick envelope at a wooden table, reading a document, lamp light '
                         'on his stern face, 85mm lens. Shot: close-up. Focus: rack focus from foreground to '
                         'background. Lighting: single hanging lamp. Minh: Vietnamese man, 45, elder brother, stocky '
                         'build, short neat black hair greying at the temples, thick eyebrows, stern square face, '
                         'wears a black long-sleeve dress shirt with a white mourning band on his left arm. Style: '
                         'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with '
                         'deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color '
                         'grade, fast-paced tense dramatic energy.',
         'video_prompt': 'Minh tears open the envelope and reads; camera quickly racks focus from the paper to his '
                         'face as a faint satisfied smirk appears; paper rustling. Camera movement: slow push-in. '
                         'Focus: rack focus from foreground to background.',
         'text_only_prompt': 'Close-up of Minh opening a thick envelope at a wooden table, reading a document, lamp '
                             'light on his stern face, 85mm lens. Minh tears open the envelope and reads; camera '
                             'quickly racks focus from the paper to his face as a faint satisfied smirk appears; paper '
                             'rustling. Shot: close-up. Camera movement: slow push-in. Focus: rack focus from '
                             'foreground to background. Lighting: single hanging lamp. Minh: Vietnamese man, 45, elder '
                             'brother, stocky build, short neat black hair greying at the temples, thick eyebrows, '
                             'stern square face, wears a black long-sleeve dress shirt with a white mourning band on '
                             'his left arm. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm '
                             'tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, '
                             'high-contrast amber and teal color grade, fast-paced tense dramatic energy.',
         'scene_refs': [['Minh', '<tmp>/assets/characters/minh.png']],
         'refs_signature': ['Minh:e0129c7986c9'],
         'fingerprint': 'b485f2cec8e0385b'},
 's04': {'image_prompt': 'Close-up of Hoa at a wooden table, eyes wide in disbelief, jade bangle on her wrist resting '
                         "on the table, warm side light, 85mm lens. Hoa: Vietnamese woman, 35, Tuan's wife, petite, "
                         'long straight black hair worn loose, round face, large jade bangle on right wrist, cream '
                         'silk blouse and black pencil skirt, fiery expressive eyes. Style: Cinematic Vietnamese '
                         'family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow '
                         'depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
                         'dramatic energy.',
         'video_prompt': 'Hoa slams her palm on the table, jade bangle clacking, and leans forward; fast whip pan to '
                         'Tuan beside her clenching his jaw. She shouts angrily in Vietnamese, voice only.',
         'text_only_prompt': 'Close-up of Hoa at a wooden table, eyes wide in disbelief, jade bangle on her wrist '
                             'resting on the table, warm side light, 85mm lens. Hoa slams her palm on the table, jade '
                             'bangle clacking, and leans forward; fast whip pan to Tuan beside her clenching his jaw. '
                             'She shouts angrily in Vietnamese, voice only. Tuan: Vietnamese man, 38, younger brother, '
                             'lean and tall, messy medium-length black hair, light stubble, hot-tempered expression, '
                             'white mourning headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, 35, '
                             "Tuan's wife, petite, long straight black hair worn loose, round face, large jade bangle "
                             'on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Style: '
                             'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light '
                             'with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
                             'color grade, fast-paced tense dramatic energy.',
         'scene_refs': [['Tuan', '<tmp>/assets/characters/tuan.png'], ['Hoa', '<tmp>/assets/characters/hoa.png']],
         'refs_signature': ['Tuan:4562b732a8e5', 'Hoa:e8ec20958fc9'],
         'fingerprint': '28894717193f5a15'},
 's05': {'image_prompt': 'Medium shot of Lan sitting upright with arms folded, cold smile, red lipstick, gold necklace '
                         'catching the light, dark living room background, 50mm lens. Lan: Vietnamese woman, 42, '
                         "Minh's wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, "
                         'fitted dark burgundy blouse and black trousers, red lipstick, calculating eyes. Style: '
                         'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with '
                         'deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color '
                         'grade, fast-paced tense dramatic energy.',
         'video_prompt': 'Lan slowly tilts her head and smiles coldly, speaking calmly and mockingly in Vietnamese, '
                         'voice only; slow dolly-in on her face.',
         'text_only_prompt': 'Medium shot of Lan sitting upright with arms folded, cold smile, red lipstick, gold '
                             'necklace catching the light, dark living room background, 50mm lens. Lan slowly tilts '
                             'her head and smiles coldly, speaking calmly and mockingly in Vietnamese, voice only; '
                             "slow dolly-in on her face. Lan: Vietnamese woman, 42, Minh's wife, slim, sharp "
                             'cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy '
                             'blouse and black trousers, red lipstick, calculating eyes. Style: Cinematic Vietnamese '
                             'family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, '
                             'shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, '
                             'fast-paced tense dramatic energy.',
         'scene_refs': [['Lan', '<tmp>/assets/characters/lan.png']],
         'refs_signature': ['Lan:c6448b57ea9a'],
         'fingerprint': '563d38bbf37e1d90'},
 's06': {'image_prompt': 'Tuan standing abruptly from a wooden chair in a dim living room, chair tipping backward, '
                         'furious face, mourning headband, 35mm lens. Tuan: Vietnamese man, 38, younger brother, lean '
                         'and tall, messy medium-length black hair, light stubble, hot-tempered expression, white '
                         'mourning headband, plain grey polo shirt and jeans. Style: Cinematic Vietnamese family '
                         'drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow depth '
                         'of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
                         'dramatic energy.',
         'video_prompt': 'Tuan leaps up, his chair crashes to the floor, and he points at Minh, yelling in Vietnamese, '
                         'voice only; handheld camera shakes with the movement.',
         'text_only_prompt': 'Tuan standing abruptly from a wooden chair in a dim living room, chair tipping backward, '
                             'furious face, mourning headband, 35mm lens. Tuan leaps up, his chair crashes to the '
                             'floor, and he points at Minh, yelling in Vietnamese, voice only; handheld camera shakes '
                             'with the movement. Minh: Vietnamese man, 45, elder brother, stocky build, short neat '
                             'black hair greying at the temples, thick eyebrows, stern square face, wears a black '
                             'long-sleeve dress shirt with a white mourning band on his left arm. Tuan: Vietnamese '
                             'man, 38, younger brother, lean and tall, messy medium-length black hair, light stubble, '
                             'hot-tempered expression, white mourning headband, plain grey polo shirt and jeans. '
                             'Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior '
                             'light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber '
                             'and teal color grade, fast-paced tense dramatic energy.',
         'scene_refs': [['Minh', '<tmp>/assets/characters/minh.png'], ['Tuan', '<tmp>/assets/characters/tuan.png']],
         'refs_signature': ['Minh:e0129c7986c9', 'Tuan:4562b732a8e5'],
         'fingerprint': '32925e81ad283196'},
 's07': {'image_prompt': "Minh rising from his seat facing camera, veins on his neck, dim lamp light, his brother's "
                         'shoulder blurred in the foreground, 50mm lens. Minh: Vietnamese man, 45, elder brother, '
                         'stocky build, short neat black hair greying at the temples, thick eyebrows, stern square '
                         'face, wears a black long-sleeve dress shirt with a white mourning band on his left arm. '
                         'Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior '
                         'light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and '
                         'teal color grade, fast-paced tense dramatic energy.',
         'video_prompt': "Minh rises and steps into Tuan's face, the two brothers chest to chest; fast orbiting "
                         'handheld shot around them, heavy breathing, rising tension score.',
         'text_only_prompt': 'Minh rising from his seat facing camera, veins on his neck, dim lamp light, his '
                             "brother's shoulder blurred in the foreground, 50mm lens. Minh rises and steps into "
                             "Tuan's face, the two brothers chest to chest; fast orbiting handheld shot around them, "
                             'heavy breathing, rising tension score. Minh: Vietnamese man, 45, elder brother, stocky '
                             'build, short neat black hair greying at the temples, thick eyebrows, stern square face, '
                             'wears a black long-sleeve dress shirt with a white mourning band on his left arm. Tuan: '
                             'Vietnamese man, 38, younger brother, lean and tall, messy medium-length black hair, '
                             'light stubble, hot-tempered expression, white mourning headband, plain grey polo shirt '
                             'and jeans. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm '
                             'tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, '
                             'high-contrast amber and teal color grade, fast-paced tense dramatic energy.',
         'scene_refs': [['Minh', '<tmp>/assets/characters/minh.png'], ['Tuan', '<tmp>/assets/characters/tuan.png']],
         'refs_signature': ['Minh:e0129c7986c9', 'Tuan:4562b732a8e5'],
         'fingerprint': '46c1888e3a44aeff'},
 's08': {'image_prompt': 'Mother seated at the head of the table, frail hands gripping prayer beads, eyes filled with '
                         'tears, soft backlight from the altar, 85mm lens. Mother: Vietnamese grandmother, 72, frail '
                         'and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning '
                         'ao ba ba, holds wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 '
                         'framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm film '
                         'grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy.',
         'video_prompt': 'Mother reaches out with a trembling hand, softly pleading in Vietnamese, voice only, while '
                         'the shouting continues off-screen; slow push-in on her tearful eyes.',
         'text_only_prompt': 'Mother seated at the head of the table, frail hands gripping prayer beads, eyes filled '
                             'with tears, soft backlight from the altar, 85mm lens. Mother reaches out with a '
                             'trembling hand, softly pleading in Vietnamese, voice only, while the shouting continues '
                             'off-screen; slow push-in on her tearful eyes. Mother: Vietnamese grandmother, 72, frail '
                             'and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional '
                             'mourning ao ba ba, holds wooden prayer beads. Style: Cinematic Vietnamese family drama, '
                             'vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow depth of '
                             'field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
                             'dramatic energy.',
         'scene_refs': [['Mother', '<tmp>/assets/characters/mother.png']],
         'refs_signature': ['Mother:260713d1a03a'],
         'fingerprint': '0bd8ba10c1ebde85'},
 's09': {'image_prompt': 'Hoa and Lan standing face to face beside the wooden table, Hoa with loose hair glaring up, '
                         "Lan smirking down, harsh overhead lamp, 35mm lens. Lan: Vietnamese woman, 42, Minh's wife, "
                         'slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark '
                         'burgundy blouse and black trousers, red lipstick, calculating eyes. Hoa: Vietnamese woman, '
                         "35, Tuan's wife, petite, long straight black hair worn loose, round face, large jade bangle "
                         'on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Style: '
                         'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with '
                         'deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color '
                         'grade, fast-paced tense dramatic energy.',
         'video_prompt': 'Hoa and Lan step toward each other, trading furious words in Vietnamese, voice only; rapid '
                         'handheld push-in between their faces.',
         'text_only_prompt': 'Hoa and Lan standing face to face beside the wooden table, Hoa with loose hair glaring '
                             'up, Lan smirking down, harsh overhead lamp, 35mm lens. Hoa and Lan step toward each '
                             'other, trading furious words in Vietnamese, voice only; rapid handheld push-in between '
                             "their faces. Lan: Vietnamese woman, 42, Minh's wife, slim, sharp cheekbones, black hair "
                             'in a tight low bun, thin gold necklace, fitted dark burgundy blouse and black trousers, '
                             "red lipstick, calculating eyes. Hoa: Vietnamese woman, 35, Tuan's wife, petite, long "
                             'straight black hair worn loose, round face, large jade bangle on right wrist, cream silk '
                             'blouse and black pencil skirt, fiery expressive eyes. Style: Cinematic Vietnamese family '
                             'drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow '
                             'depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced '
                             'tense dramatic energy.',
         'scene_refs': [['Lan', '<tmp>/assets/characters/lan.png'], ['Hoa', '<tmp>/assets/characters/hoa.png']],
         'refs_signature': ['Lan:c6448b57ea9a', 'Hoa:e8ec20958fc9'],
         'fingerprint': 'b33da1a8afa23d34'},
 's10': {'image_prompt': 'Lan in a burgundy blouse with her hand raised mid-swing, Hoa flinching, dim living room, '
                         "motion blur, 50mm lens. Lan: Vietnamese woman, 42, Minh's wife, slim, sharp cheekbones, "
                         'black hair in a tight low bun, thin gold necklace, fitted dark burgundy blouse and black '
                         "trousers, red lipstick, calculating eyes. Hoa: Vietnamese woman, 35, Tuan's wife, petite, "
                         'long straight black hair worn loose, round face, large jade bangle on right wrist, cream '
                         'silk blouse and black pencil skirt, fiery expressive eyes. Style: Cinematic Vietnamese '
                         'family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow '
                         'depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
                         'dramatic energy.',
         'video_prompt': "Lan slaps Hoa hard across the face, Hoa's hair whips sideways; sharp slap sound, camera "
                         'snap-zooms in, then silence.',
         'text_only_prompt': 'Lan in a burgundy blouse with her hand raised mid-swing, Hoa flinching, dim living room, '
                             "motion blur, 50mm lens. Lan slaps Hoa hard across the face, Hoa's hair whips sideways; "
                             "sharp slap sound, camera snap-zooms in, then silence. Lan: Vietnamese woman, 42, Minh's "
                             'wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted '
                             'dark burgundy blouse and black trousers, red lipstick, calculating eyes. Hoa: Vietnamese '
                             "woman, 35, Tuan's wife, petite, long straight black hair worn loose, round face, large "
                             'jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery expressive '
                             'eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten '
                             'interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast '
                             'amber and teal color grade, fast-paced tense dramatic energy.',
         'scene_refs': [['Lan', '<tmp>/assets/characters/lan.png'], ['Hoa', '<tmp>/assets/characters/hoa.png']],
         'refs_signature': ['Lan:c6448b57ea9a', 'Hoa:e8ec20958fc9'],
         'fingerprint': '882681892b1c168f'},
 's11': {'image_prompt': 'Hoa holding her cheek, eyes burning, Tuan behind her lunging forward, Minh grabbing his arm, '
                         'chaotic lamp-lit room, 24mm lens. Minh: Vietnamese man, 45, elder brother, stocky build, '
                         'short neat black hair greying at the temples, thick eyebrows, stern square face, wears a '
                         'black long-sleeve dress shirt with a white mourning band on his left arm. Tuan: Vietnamese '
                         'man, 38, younger brother, lean and tall, messy medium-length black hair, light stubble, '
                         'hot-tempered expression, white mourning headband, plain grey polo shirt and jeans. Hoa: '
                         "Vietnamese woman, 35, Tuan's wife, petite, long straight black hair worn loose, round face, "
                         'large jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery expressive '
                         'eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten '
                         'interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast '
                         'amber and teal color grade, fast-paced tense dramatic energy.',
         'video_prompt': 'Tuan lunges at Lan while Minh grabs and shoves him back; chaotic handheld tracking shot as a '
                         'teacup shatters on the floor.',
         'text_only_prompt': 'Hoa holding her cheek, eyes burning, Tuan behind her lunging forward, Minh grabbing his '
                             'arm, chaotic lamp-lit room, 24mm lens. Tuan lunges at Lan while Minh grabs and shoves '
                             'him back; chaotic handheld tracking shot as a teacup shatters on the floor. Minh: '
                             'Vietnamese man, 45, elder brother, stocky build, short neat black hair greying at the '
                             'temples, thick eyebrows, stern square face, wears a black long-sleeve dress shirt with a '
                             "white mourning band on his left arm. Lan: Vietnamese woman, 42, Minh's wife, slim, sharp "
                             'cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy '
                             'blouse and black trousers, red lipstick, calculating eyes. Tuan: Vietnamese man, 38, '
                             'younger brother, lean and tall, messy medium-length black hair, light stubble, '
                             'hot-tempered expression, white mourning headband, plain grey polo shirt and jeans. Hoa: '
                             "Vietnamese woman, 35, Tuan's wife, petite, long straight black hair worn loose, round "
                             'face, large jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery '
                             'expressive eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm '
                             'tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, '
                             'high-contrast amber and teal color grade, fast-paced tense dramatic energy.',
         'scene_refs': [['Minh', '<tmp>/assets/characters/minh.png'],
                        ['Lan', '<tmp>/assets/characters/lan.png'],
                        ['Tuan', '<tmp>/assets/characters/tuan.png'],
                        ['Hoa', '<tmp>/assets/characters/hoa.png']],
         'refs_signature': ['Minh:e0129c7986c9', 'Lan:c6448b57ea9a', 'Tuan:4562b732a8e5', 'Hoa:e8ec20958fc9'],
         'fingerprint': '00801efb439b96db'},
 's12': {'image_prompt': "Close-up of Tuan's hands gripping the will document, Minh's hands pulling the other side, "
                         'paper stretched tight, dramatic side light, 85mm lens. Minh: Vietnamese man, 45, elder '
                         'brother, stocky build, short neat black hair greying at the temples, thick eyebrows, stern '
                         'square face, wears a black long-sleeve dress shirt with a white mourning band on his left '
                         'arm. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy medium-length black '
                         'hair, light stubble, hot-tempered expression, white mourning headband, plain grey polo shirt '
                         'and jeans. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten '
                         'interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast '
                         'amber and teal color grade, fast-paced tense dramatic energy.',
         'video_prompt': 'Tuan and Minh tug at the will until it rips in half; extreme slow motion of the paper '
                         'tearing, with a deep bass hit.',
         'text_only_prompt': "Close-up of Tuan's hands gripping the will document, Minh's hands pulling the other "
                             'side, paper stretched tight, dramatic side light, 85mm lens. Tuan and Minh tug at the '
                             'will until it rips in half; extreme slow motion of the paper tearing, with a deep bass '
                             'hit. Minh: Vietnamese man, 45, elder brother, stocky build, short neat black hair '
                             'greying at the temples, thick eyebrows, stern square face, wears a black long-sleeve '
                             'dress shirt with a white mourning band on his left arm. Tuan: Vietnamese man, 38, '
                             'younger brother, lean and tall, messy medium-length black hair, light stubble, '
                             'hot-tempered expression, white mourning headband, plain grey polo shirt and jeans. '
                             'Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior '
                             'light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber '
                             'and teal color grade, fast-paced tense dramatic energy.',
         'scene_refs': [['Minh', '<tmp>/assets/characters/minh.png'], ['Tuan', '<tmp>/assets/characters/tuan.png']],
         'refs_signature': ['Minh:e0129c7986c9', 'Tuan:4562b732a8e5'],
         'fingerprint': 'fcce64b85aaf532e'},
 's13': {'image_prompt': 'Mother clutching her chest, prayer beads slipping from her fingers, swaying in her chair, '
                         'altar candles behind her, 50mm lens. Mother: Vietnamese grandmother, 72, frail and small, '
                         'silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, '
                         'holds wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, '
                         'warm tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, '
                         'high-contrast amber and teal color grade, fast-paced tense dramatic energy.',
         'video_prompt': 'Mother gasps, clutches her chest and collapses sideways from her chair, prayer beads '
                         'scattering across the tiles; camera drops with her to floor level, sound fades to a '
                         'high-pitched ring.',
         'text_only_prompt': 'Mother clutching her chest, prayer beads slipping from her fingers, swaying in her '
                             'chair, altar candles behind her, 50mm lens. Mother gasps, clutches her chest and '
                             'collapses sideways from her chair, prayer beads scattering across the tiles; camera '
                             'drops with her to floor level, sound fades to a high-pitched ring. Mother: Vietnamese '
                             'grandmother, 72, frail and small, silver hair in a bun, deep wrinkles, gentle sad eyes, '
                             'white traditional mourning ao ba ba, holds wooden prayer beads. Style: Cinematic '
                             'Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep '
                             'shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color '
                             'grade, fast-paced tense dramatic energy.',
         'scene_refs': [['Mother', '<tmp>/assets/characters/mother.png']],
         'refs_signature': ['Mother:260713d1a03a'],
         'fingerprint': 'b568b2a4e17aff16'},
 's14': {'image_prompt': 'Minh and Tuan kneeling on a tiled floor holding their unconscious mother, Lan and Hoa frozen '
                         'behind them in shock, scattered beads, dim light, 24mm lens. Minh: Vietnamese man, 45, elder '
                         'brother, stocky build, short neat black hair greying at the temples, thick eyebrows, stern '
                         'square face, wears a black long-sleeve dress shirt with a white mourning band on his left '
                         "arm. Lan: Vietnamese woman, 42, Minh's wife, slim, sharp cheekbones, black hair in a tight "
                         'low bun, thin gold necklace, fitted dark burgundy blouse and black trousers, red lipstick, '
                         'calculating eyes. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy '
                         'medium-length black hair, light stubble, hot-tempered expression, white mourning headband, '
                         "plain grey polo shirt and jeans. Hoa: Vietnamese woman, 35, Tuan's wife, petite, long "
                         'straight black hair worn loose, round face, large jade bangle on right wrist, cream silk '
                         'blouse and black pencil skirt, fiery expressive eyes. Mother: Vietnamese grandmother, 72, '
                         'frail and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional '
                         'mourning ao ba ba, holds wooden prayer beads. Style: Cinematic Vietnamese family drama, '
                         'vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow depth of '
                         'field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense dramatic '
                         'energy.',
         'video_prompt': 'Minh and Tuan kneel and cradle Mother, shouting her name in Vietnamese, voice only, while '
                         'Lan and Hoa stand frozen; fast circling handheld shot.',
         'text_only_prompt': 'Minh and Tuan kneeling on a tiled floor holding their unconscious mother, Lan and Hoa '
                             'frozen behind them in shock, scattered beads, dim light, 24mm lens. Minh and Tuan kneel '
                             'and cradle Mother, shouting her name in Vietnamese, voice only, while Lan and Hoa stand '
                             'frozen; fast circling handheld shot. Minh: Vietnamese man, 45, elder brother, stocky '
                             'build, short neat black hair greying at the temples, thick eyebrows, stern square face, '
                             'wears a black long-sleeve dress shirt with a white mourning band on his left arm. Lan: '
                             "Vietnamese woman, 42, Minh's wife, slim, sharp cheekbones, black hair in a tight low "
                             'bun, thin gold necklace, fitted dark burgundy blouse and black trousers, red lipstick, '
                             'calculating eyes. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy '
                             'medium-length black hair, light stubble, hot-tempered expression, white mourning '
                             "headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, 35, Tuan's wife, "
                             'petite, long straight black hair worn loose, round face, large jade bangle on right '
                             'wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Mother: '
                             'Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep wrinkles, gentle '
                             'sad eyes, white traditional mourning ao ba ba, holds wooden prayer beads. Style: '
                             'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light '
                             'with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
                             'color grade, fast-paced tense dramatic energy.',
         'scene_refs': [['Minh', '<tmp>/assets/characters/minh.png'],
                        ['Lan', '<tmp>/assets/characters/lan.png'],
                        ['Tuan', '<tmp>/assets/characters/tuan.png'],
                        ['Hoa', '<tmp>/assets/characters/hoa.png'],
                        ['Mother', '<tmp>/assets/characters/mother.png']],
         'refs_signature': ['Minh:e0129c7986c9',
                            'Lan:c6448b57ea9a',
                            'Tuan:4562b732a8e5',
                            'Hoa:e8ec20958fc9',
                            'Mother:260713d1a03a'],
         'fingerprint': '59747c92e3c567c6'},
 's15': {'image_prompt': 'Hospital corridor at night, cold fluorescent light, Minh, Lan, Tuan and Hoa sitting apart on '
                         'plastic chairs, heads down, long empty hallway, 35mm lens. Minh: Vietnamese man, 45, elder '
                         'brother, stocky build, short neat black hair greying at the temples, thick eyebrows, stern '
                         'square face, wears a black long-sleeve dress shirt with a white mourning band on his left '
                         "arm. Lan: Vietnamese woman, 42, Minh's wife, slim, sharp cheekbones, black hair in a tight "
                         'low bun, thin gold necklace, fitted dark burgundy blouse and black trousers, red lipstick, '
                         'calculating eyes. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy '
                         'medium-length black hair, light stubble, hot-tempered expression, white mourning headband, '
                         "plain grey polo shirt and jeans. Hoa: Vietnamese woman, 35, Tuan's wife, petite, long "
                         'straight black hair worn loose, round face, large jade bangle on right wrist, cream silk '
                         'blouse and black pencil skirt, fiery expressive eyes. Style: Cinematic Vietnamese family '
                         'drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow depth '
                         'of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
                         'dramatic energy.',
         'video_prompt': 'Slow lateral dolly along the row of chairs past Minh, Lan, Tuan and Hoa sitting in silence; '
                         'distant heart monitor beeping.',
         'text_only_prompt': 'Hospital corridor at night, cold fluorescent light, Minh, Lan, Tuan and Hoa sitting '
                             'apart on plastic chairs, heads down, long empty hallway, 35mm lens. Slow lateral dolly '
                             'along the row of chairs past Minh, Lan, Tuan and Hoa sitting in silence; distant heart '
                             'monitor beeping. Minh: Vietnamese man, 45, elder brother, stocky build, short neat black '
                             'hair greying at the temples, thick eyebrows, stern square face, wears a black '
                             'long-sleeve dress shirt with a white mourning band on his left arm. Lan: Vietnamese '
                             "woman, 42, Minh's wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold "
                             'necklace, fitted dark burgundy blouse and black trousers, red lipstick, calculating '
                             'eyes. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy medium-length '
                             'black hair, light stubble, hot-tempered expression, white mourning headband, plain grey '
                             "polo shirt and jeans. Hoa: Vietnamese woman, 35, Tuan's wife, petite, long straight "
                             'black hair worn loose, round face, large jade bangle on right wrist, cream silk blouse '
                             'and black pencil skirt, fiery expressive eyes. Style: Cinematic Vietnamese family drama, '
                             'vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow depth of '
                             'field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
                             'dramatic energy.',
         'scene_refs': [['Minh', '<tmp>/assets/characters/minh.png'],
                        ['Lan', '<tmp>/assets/characters/lan.png'],
                        ['Tuan', '<tmp>/assets/characters/tuan.png'],
                        ['Hoa', '<tmp>/assets/characters/hoa.png']],
         'refs_signature': ['Minh:e0129c7986c9', 'Lan:c6448b57ea9a', 'Tuan:4562b732a8e5', 'Hoa:e8ec20958fc9'],
         'fingerprint': '0fc78d20e3e8e94a'},
 's16': {'image_prompt': 'Hospital room, Mother lying awake in bed with oxygen tube, weak smile, holding a small old '
                         'sealed envelope, soft morning window light, 50mm lens. Mother: Vietnamese grandmother, 72, '
                         'frail and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional '
                         'mourning ao ba ba, holds wooden prayer beads. Style: Cinematic Vietnamese family drama, '
                         'vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow depth of '
                         'field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense dramatic '
                         'energy.',
         'video_prompt': 'Mother lifts a small worn envelope from under her pillow and holds it out toward camera; '
                         'slow push-in on the envelope in her trembling hand.',
         'text_only_prompt': 'Hospital room, Mother lying awake in bed with oxygen tube, weak smile, holding a small '
                             'old sealed envelope, soft morning window light, 50mm lens. Mother lifts a small worn '
                             'envelope from under her pillow and holds it out toward camera; slow push-in on the '
                             'envelope in her trembling hand. Mother: Vietnamese grandmother, 72, frail and small, '
                             'silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba '
                             'ba, holds wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 '
                             'framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm '
                             'film grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy.',
         'scene_refs': [['Mother', '<tmp>/assets/characters/mother.png']],
         'refs_signature': ['Mother:260713d1a03a'],
         'fingerprint': 'db8fb0b5ceb6e9da'},
 's17': {'image_prompt': 'Close-up of Tuan reading a handwritten letter in a hospital room, Minh reading over his '
                         'shoulder, morning light, eyes glistening, 85mm lens. Minh: Vietnamese man, 45, elder '
                         'brother, stocky build, short neat black hair greying at the temples, thick eyebrows, stern '
                         'square face, wears a black long-sleeve dress shirt with a white mourning band on his left '
                         'arm. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy medium-length black '
                         'hair, light stubble, hot-tempered expression, white mourning headband, plain grey polo shirt '
                         'and jeans. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten '
                         'interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast '
                         'amber and teal color grade, fast-paced tense dramatic energy.',
         'video_prompt': 'Tuan reads the letter and his hands begin to shake as Minh leans in behind him; slow rack '
                         'focus from the paper to their faces as tears form; soft piano begins.',
         'text_only_prompt': 'Close-up of Tuan reading a handwritten letter in a hospital room, Minh reading over his '
                             'shoulder, morning light, eyes glistening, 85mm lens. Tuan reads the letter and his hands '
                             'begin to shake as Minh leans in behind him; slow rack focus from the paper to their '
                             'faces as tears form; soft piano begins. Minh: Vietnamese man, 45, elder brother, stocky '
                             'build, short neat black hair greying at the temples, thick eyebrows, stern square face, '
                             'wears a black long-sleeve dress shirt with a white mourning band on his left arm. Tuan: '
                             'Vietnamese man, 38, younger brother, lean and tall, messy medium-length black hair, '
                             'light stubble, hot-tempered expression, white mourning headband, plain grey polo shirt '
                             'and jeans. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm '
                             'tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, '
                             'high-contrast amber and teal color grade, fast-paced tense dramatic energy.',
         'scene_refs': [['Minh', '<tmp>/assets/characters/minh.png'], ['Tuan', '<tmp>/assets/characters/tuan.png']],
         'refs_signature': ['Minh:e0129c7986c9', 'Tuan:4562b732a8e5'],
         'fingerprint': 'ef76dd7297baf882'},
 's18': {'image_prompt': 'Lan and Hoa standing near a hospital window, both ashamed, Hoa with a red mark on her cheek, '
                         "Lan looking down, soft daylight, 50mm lens. Lan: Vietnamese woman, 42, Minh's wife, slim, "
                         'sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy '
                         "blouse and black trousers, red lipstick, calculating eyes. Hoa: Vietnamese woman, 35, Tuan's "
                         'wife, petite, long straight black hair worn loose, round face, large jade bangle on right '
                         'wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Style: Cinematic '
                         'Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep '
                         'shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, '
                         'fast-paced tense dramatic energy.',
         'video_prompt': 'Lan slowly turns to Hoa and gently touches her bruised cheek, whispering an apology in '
                         "Vietnamese, voice only; Hoa's eyes fill with tears; slow arc shot around them.",
         'text_only_prompt': 'Lan and Hoa standing near a hospital window, both ashamed, Hoa with a red mark on her '
                             'cheek, Lan looking down, soft daylight, 50mm lens. Lan slowly turns to Hoa and gently '
                             "touches her bruised cheek, whispering an apology in Vietnamese, voice only; Hoa's eyes "
                             "fill with tears; slow arc shot around them. Lan: Vietnamese woman, 42, Minh's wife, "
                             'slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark '
                             'burgundy blouse and black trousers, red lipstick, calculating eyes. Hoa: Vietnamese '
                             "woman, 35, Tuan's wife, petite, long straight black hair worn loose, round face, large "
                             'jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery expressive '
                             'eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten '
                             'interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast '
                             'amber and teal color grade, fast-paced tense dramatic energy.',
         'scene_refs': [['Lan', '<tmp>/assets/characters/lan.png'], ['Hoa', '<tmp>/assets/characters/hoa.png']],
         'refs_signature': ['Lan:c6448b57ea9a', 'Hoa:e8ec20958fc9'],
         'fingerprint': '95403ca62e6d9e92'},
 's19': {'image_prompt': 'Minh and Tuan standing at the foot of a hospital bed, Mother smiling weakly between them, '
                         'morning sun through blinds, 35mm lens. Minh: Vietnamese man, 45, elder brother, stocky '
                         'build, short neat black hair greying at the temples, thick eyebrows, stern square face, '
                         'wears a black long-sleeve dress shirt with a white mourning band on his left arm. Tuan: '
                         'Vietnamese man, 38, younger brother, lean and tall, messy medium-length black hair, light '
                         'stubble, hot-tempered expression, white mourning headband, plain grey polo shirt and jeans. '
                         'Mother: Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep wrinkles, '
                         'gentle sad eyes, white traditional mourning ao ba ba, holds wooden prayer beads. Style: '
                         'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with '
                         'deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color '
                         'grade, fast-paced tense dramatic energy.',
         'video_prompt': 'Minh pulls Tuan into a tight embrace, both brothers crying, as Mother reaches out to hold '
                         'their hands; slow dolly-in, warm piano swells.',
         'text_only_prompt': 'Minh and Tuan standing at the foot of a hospital bed, Mother smiling weakly between '
                             'them, morning sun through blinds, 35mm lens. Minh pulls Tuan into a tight embrace, both '
                             'brothers crying, as Mother reaches out to hold their hands; slow dolly-in, warm piano '
                             'swells. Minh: Vietnamese man, 45, elder brother, stocky build, short neat black hair '
                             'greying at the temples, thick eyebrows, stern square face, wears a black long-sleeve '
                             'dress shirt with a white mourning band on his left arm. Tuan: Vietnamese man, 38, '
                             'younger brother, lean and tall, messy medium-length black hair, light stubble, '
                             'hot-tempered expression, white mourning headband, plain grey polo shirt and jeans. '
                             'Mother: Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep '
                             'wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds wooden prayer '
                             'beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten '
                             'interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast '
                             'amber and teal color grade, fast-paced tense dramatic energy.',
         'scene_refs': [['Minh', '<tmp>/assets/characters/minh.png'],
                        ['Tuan', '<tmp>/assets/characters/tuan.png'],
                        ['Mother', '<tmp>/assets/characters/mother.png']],
         'refs_signature': ['Minh:e0129c7986c9', 'Tuan:4562b732a8e5', 'Mother:260713d1a03a'],
         'fingerprint': '4ec6316e8d095410'},
 's20': {'image_prompt': "Ancestral altar in the family house in daylight, father's framed portrait, fresh incense, "
                         'Minh, Lan, Tuan, Hoa and Mother standing together bowing, warm golden light, 24mm lens. '
                         'Minh: Vietnamese man, 45, elder brother, stocky build, short neat black hair greying at the '
                         'temples, thick eyebrows, stern square face, wears a black long-sleeve dress shirt with a '
                         "white mourning band on his left arm. Lan: Vietnamese woman, 42, Minh's wife, slim, sharp "
                         'cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy blouse '
                         'and black trousers, red lipstick, calculating eyes. Tuan: Vietnamese man, 38, younger '
                         'brother, lean and tall, messy medium-length black hair, light stubble, hot-tempered '
                         'expression, white mourning headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, '
                         "35, Tuan's wife, petite, long straight black hair worn loose, round face, large jade bangle "
                         'on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Mother: '
                         'Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep wrinkles, gentle sad '
                         'eyes, white traditional mourning ao ba ba, holds wooden prayer beads. Style: Cinematic '
                         'Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep '
                         'shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, '
                         'fast-paced tense dramatic energy.',
         'video_prompt': "Slow crane up from the family bowing together before the altar to the father's portrait as "
                         'incense smoke rises in golden light; gentle temple bell.',
         'text_only_prompt': "Ancestral altar in the family house in daylight, father's framed portrait, fresh "
                             'incense, Minh, Lan, Tuan, Hoa and Mother standing together bowing, warm golden light, '
                             '24mm lens. Slow crane up from the family bowing together before the altar to the '
                             "father's portrait as incense smoke rises in golden light; gentle temple bell. Minh: "
                             'Vietnamese man, 45, elder brother, stocky build, short neat black hair greying at the '
                             'temples, thick eyebrows, stern square face, wears a black long-sleeve dress shirt with a '
                             "white mourning band on his left arm. Lan: Vietnamese woman, 42, Minh's wife, slim, sharp "
                             'cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy '
                             'blouse and black trousers, red lipstick, calculating eyes. Tuan: Vietnamese man, 38, '
                             'younger brother, lean and tall, messy medium-length black hair, light stubble, '
                             'hot-tempered expression, white mourning headband, plain grey polo shirt and jeans. Hoa: '
                             "Vietnamese woman, 35, Tuan's wife, petite, long straight black hair worn loose, round "
                             'face, large jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery '
                             'expressive eyes. Mother: Vietnamese grandmother, 72, frail and small, silver hair in a '
                             'bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds wooden '
                             'prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm '
                             'tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, '
                             'high-contrast amber and teal color grade, fast-paced tense dramatic energy.',
         'scene_refs': [['Minh', '<tmp>/assets/characters/minh.png'],
                        ['Lan', '<tmp>/assets/characters/lan.png'],
                        ['Tuan', '<tmp>/assets/characters/tuan.png'],
                        ['Hoa', '<tmp>/assets/characters/hoa.png'],
                        ['Mother', '<tmp>/assets/characters/mother.png']],
         'refs_signature': ['Minh:e0129c7986c9',
                            'Lan:c6448b57ea9a',
                            'Tuan:4562b732a8e5',
                            'Hoa:e8ec20958fc9',
                            'Mother:260713d1a03a'],
         'fingerprint': '60a5e2426cfd1d85'},
 'x1': {'image_prompt': 'Rain on the window of the old house at night, Tuan and Hoa leave through the gate. Shot: wide '
                        'shot. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy medium-length black '
                        'hair, light stubble, hot-tempered expression, white mourning headband, plain grey polo shirt '
                        "and jeans. Hoa: Vietnamese woman, 35, Tuan's wife, petite, long straight black hair worn "
                        'loose, round face, large jade bangle on right wrist, cream silk blouse and black pencil '
                        'skirt, fiery expressive eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 '
                        'framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm film '
                        'grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy.',
        'video_prompt': 'Rain on the window of the old house at night, Tuan and Hoa leave through the gate. Shot: wide '
                        'shot. Camera movement: handheld camera, subtle shake. Tuan: Vietnamese man, 38, younger '
                        'brother, lean and tall, messy medium-length black hair, light stubble, hot-tempered '
                        'expression, white mourning headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, '
                        "35, Tuan's wife, petite, long straight black hair worn loose, round face, large jade bangle "
                        'on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Style: '
                        'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with '
                        'deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color '
                        'grade, fast-paced tense dramatic energy.',
        'text_only_prompt': 'Rain on the window of the old house at night, Tuan and Hoa leave through the gate. Shot: '
                            'wide shot. Camera movement: handheld camera, subtle shake. Tuan: Vietnamese man, 38, '
                            'younger brother, lean and tall, messy medium-length black hair, light stubble, '
                            'hot-tempered expression, white mourning headband, plain grey polo shirt and jeans. Hoa: '
                            "Vietnamese woman, 35, Tuan's wife, petite, long straight black hair worn loose, round "
                            'face, large jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery '
                            'expressive eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm '
                            'tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, '
                            'high-contrast amber and teal color grade, fast-paced tense dramatic energy.',
        'scene_refs': [['Tuan', '<tmp>/assets/characters/tuan.png'], ['Hoa', '<tmp>/assets/characters/hoa.png']],
        'refs_signature': ['Tuan:4562b732a8e5', 'Hoa:e8ec20958fc9'],
        'fingerprint': 'd6249ce6643c8ff5'},
 'x2': {'image_prompt': 'Mother turns her head toward the altar and smiles. Mother: Vietnamese grandmother, 72, frail '
                        'and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning '
                        'ao ba ba, holds wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 '
                        'framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm film '
                        'grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy.',
        'video_prompt': 'Mother turns her head toward the altar and smiles. Camera movement: slow dolly-in.',
        'text_only_prompt': 'Mother turns her head toward the altar and smiles. Camera movement: slow dolly-in. '
                            'Mother: Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep wrinkles, '
                            'gentle sad eyes, white traditional mourning ao ba ba, holds wooden prayer beads. Style: '
                            'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light '
                            'with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
                            'color grade, fast-paced tense dramatic energy.',
        'scene_refs': [],
        'refs_signature': [],
        'fingerprint': '7a73294c6c179998'},
 'x3': {'image_prompt': "The whole family sits around the wooden table, lamp light. Lan: Vietnamese woman, 42, Minh's "
                        'wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark '
                        'burgundy blouse and black trousers, red lipstick, calculating eyes. Tuan: Vietnamese man, 38, '
                        'younger brother, lean and tall, messy medium-length black hair, light stubble, hot-tempered '
                        'expression, white mourning headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, '
                        "35, Tuan's wife, petite, long straight black hair worn loose, round face, large jade bangle "
                        'on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Mother: '
                        'Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep wrinkles, gentle sad '
                        'eyes, white traditional mourning ao ba ba, holds wooden prayer beads. Style: Cinematic '
                        'Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep '
                        'shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, '
                        'fast-paced tense dramatic energy.',
        'video_prompt': 'Nobody speaks; the clock ticks',
        'text_only_prompt': 'The whole family sits around the wooden table, lamp light. Nobody speaks; the clock '
                            "ticks. Lan: Vietnamese woman, 42, Minh's wife, slim, sharp cheekbones, black hair in a "
                            'tight low bun, thin gold necklace, fitted dark burgundy blouse and black trousers, red '
                            'lipstick, calculating eyes. Tuan: Vietnamese man, 38, younger brother, lean and tall, '
                            'messy medium-length black hair, light stubble, hot-tempered expression, white mourning '
                            "headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, 35, Tuan's wife, "
                            'petite, long straight black hair worn loose, round face, large jade bangle on right '
                            'wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Mother: '
                            'Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep wrinkles, gentle '
                            'sad eyes, white traditional mourning ao ba ba, holds wooden prayer beads. Style: '
                            'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light '
                            'with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
                            'color grade, fast-paced tense dramatic energy.',
        'scene_refs': [['Lan', '<tmp>/assets/characters/lan.png'],
                       ['Tuan', '<tmp>/assets/characters/tuan.png'],
                       ['Hoa', '<tmp>/assets/characters/hoa.png'],
                       ['Mother', '<tmp>/assets/characters/mother.png']],
        'refs_signature': ['Lan:c6448b57ea9a', 'Tuan:4562b732a8e5', 'Hoa:e8ec20958fc9', 'Mother:260713d1a03a'],
        'fingerprint': 'c9825e1086232f18'}}
GOLDEN_KEYFRAMES_RUN = {'calls': [['project', 'create', '--name', 'The Inheritance War', '--json'],
           ['image',
            't2i',
            'Ancestral altar in a traditional Vietnamese family house, framed black-and-white portrait of an elderly '
            'man surrounded by incense smoke, candles and white chrysanthemums, dim warm light, 50mm lens. Style: '
            'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, '
            'shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
            'dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no '
            'watermarks, no logos; any dialogue is spoken audio only.',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s01.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Wooden living room of an old Vietnamese house at night, Minh and Lan sit on one side of a carved wooden '
            'table, Tuan and Hoa on the other, Mother sits at the head clutching prayer beads, a thick envelope lies '
            'in the center, tense lighting from a single hanging lamp, 24mm lens. Minh: Vietnamese man, 45, elder '
            'brother, stocky build, short neat black hair greying at the temples, thick eyebrows, stern square face, '
            'wears a black long-sleeve dress shirt with a white mourning band on his left arm. Lan: Vietnamese woman, '
            "42, Minh's wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark "
            'burgundy blouse and black trousers, red lipstick, calculating eyes. Tuan: Vietnamese man, 38, younger '
            'brother, lean and tall, messy medium-length black hair, light stubble, hot-tempered expression, white '
            "mourning headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, 35, Tuan's wife, petite, long "
            'straight black hair worn loose, round face, large jade bangle on right wrist, cream silk blouse and black '
            'pencil skirt, fiery expressive eyes. Mother: Vietnamese grandmother, 72, frail and small, silver hair in '
            'a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds wooden prayer beads. '
            'Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep '
            'shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced '
            'tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no '
            'watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/minh.png',
            '--ref',
            '<tmp>/assets/characters/lan.png',
            '--ref',
            '<tmp>/assets/characters/tuan.png',
            '--ref',
            '<tmp>/assets/characters/hoa.png',
            '--ref',
            '<tmp>/assets/characters/mother.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s02.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Close-up of Minh opening a thick envelope at a wooden table, reading a document, lamp light on his stern '
            'face, 85mm lens. Shot: close-up. Focus: rack focus from foreground to background. Lighting: single '
            'hanging lamp. Minh: Vietnamese man, 45, elder brother, stocky build, short neat black hair greying at the '
            'temples, thick eyebrows, stern square face, wears a black long-sleeve dress shirt with a white mourning '
            'band on his left arm. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten '
            'interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
            'color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, '
            'no title cards, no watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/minh.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s03.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Close-up of Hoa at a wooden table, eyes wide in disbelief, jade bangle on her wrist resting on the table, '
            "warm side light, 85mm lens. Hoa: Vietnamese woman, 35, Tuan's wife, petite, long straight black hair worn "
            'loose, round face, large jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery '
            'expressive eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior '
            'light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color '
            'grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no '
            'title cards, no watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/tuan.png',
            '--ref',
            '<tmp>/assets/characters/hoa.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s04.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Medium shot of Lan sitting upright with arms folded, cold smile, red lipstick, gold necklace catching the '
            "light, dark living room background, 50mm lens. Lan: Vietnamese woman, 42, Minh's wife, slim, sharp "
            'cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy blouse and black '
            'trousers, red lipstick, calculating eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 '
            'framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, '
            'high-contrast amber and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no '
            'on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/lan.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s05.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Tuan standing abruptly from a wooden chair in a dim living room, chair tipping backward, furious face, '
            'mourning headband, 35mm lens. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy '
            'medium-length black hair, light stubble, hot-tempered expression, white mourning headband, plain grey '
            'polo shirt and jeans. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten '
            'interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
            'color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, '
            'no title cards, no watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/minh.png',
            '--ref',
            '<tmp>/assets/characters/tuan.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s06.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            "Minh rising from his seat facing camera, veins on his neck, dim lamp light, his brother's shoulder "
            'blurred in the foreground, 50mm lens. Minh: Vietnamese man, 45, elder brother, stocky build, short neat '
            'black hair greying at the temples, thick eyebrows, stern square face, wears a black long-sleeve dress '
            'shirt with a white mourning band on his left arm. Style: Cinematic Vietnamese family drama, vertical 9:16 '
            'framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, '
            'high-contrast amber and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no '
            'on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/minh.png',
            '--ref',
            '<tmp>/assets/characters/tuan.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s07.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Mother seated at the head of the table, frail hands gripping prayer beads, eyes filled with tears, soft '
            'backlight from the altar, 85mm lens. Mother: Vietnamese grandmother, 72, frail and small, silver hair in '
            'a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds wooden prayer beads. '
            'Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep '
            'shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced '
            'tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no '
            'watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/mother.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s08.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Hoa and Lan standing face to face beside the wooden table, Hoa with loose hair glaring up, Lan smirking '
            "down, harsh overhead lamp, 35mm lens. Lan: Vietnamese woman, 42, Minh's wife, slim, sharp cheekbones, "
            'black hair in a tight low bun, thin gold necklace, fitted dark burgundy blouse and black trousers, red '
            "lipstick, calculating eyes. Hoa: Vietnamese woman, 35, Tuan's wife, petite, long straight black hair worn "
            'loose, round face, large jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery '
            'expressive eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior '
            'light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color '
            'grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no '
            'title cards, no watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/lan.png',
            '--ref',
            '<tmp>/assets/characters/hoa.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s09.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Lan in a burgundy blouse with her hand raised mid-swing, Hoa flinching, dim living room, motion blur, '
            "50mm lens. Lan: Vietnamese woman, 42, Minh's wife, slim, sharp cheekbones, black hair in a tight low bun, "
            'thin gold necklace, fitted dark burgundy blouse and black trousers, red lipstick, calculating eyes. Hoa: '
            "Vietnamese woman, 35, Tuan's wife, petite, long straight black hair worn loose, round face, large jade "
            'bangle on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Style: Cinematic '
            'Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow '
            'depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense dramatic '
            'energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no '
            'logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/lan.png',
            '--ref',
            '<tmp>/assets/characters/hoa.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s10.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Hoa holding her cheek, eyes burning, Tuan behind her lunging forward, Minh grabbing his arm, chaotic '
            'lamp-lit room, 24mm lens. Minh: Vietnamese man, 45, elder brother, stocky build, short neat black hair '
            'greying at the temples, thick eyebrows, stern square face, wears a black long-sleeve dress shirt with a '
            'white mourning band on his left arm. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy '
            'medium-length black hair, light stubble, hot-tempered expression, white mourning headband, plain grey '
            "polo shirt and jeans. Hoa: Vietnamese woman, 35, Tuan's wife, petite, long straight black hair worn "
            'loose, round face, large jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery '
            'expressive eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior '
            'light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color '
            'grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no '
            'title cards, no watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/minh.png',
            '--ref',
            '<tmp>/assets/characters/lan.png',
            '--ref',
            '<tmp>/assets/characters/tuan.png',
            '--ref',
            '<tmp>/assets/characters/hoa.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s11.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            "Close-up of Tuan's hands gripping the will document, Minh's hands pulling the other side, paper stretched "
            'tight, dramatic side light, 85mm lens. Minh: Vietnamese man, 45, elder brother, stocky build, short neat '
            'black hair greying at the temples, thick eyebrows, stern square face, wears a black long-sleeve dress '
            'shirt with a white mourning band on his left arm. Tuan: Vietnamese man, 38, younger brother, lean and '
            'tall, messy medium-length black hair, light stubble, hot-tempered expression, white mourning headband, '
            'plain grey polo shirt and jeans. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm '
            'tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber '
            'and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or '
            'lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/minh.png',
            '--ref',
            '<tmp>/assets/characters/tuan.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s12.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Mother clutching her chest, prayer beads slipping from her fingers, swaying in her chair, altar candles '
            'behind her, 50mm lens. Mother: Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep '
            'wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds wooden prayer beads. Style: '
            'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, '
            'shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
            'dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no '
            'watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/mother.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s13.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Minh and Tuan kneeling on a tiled floor holding their unconscious mother, Lan and Hoa frozen behind them '
            'in shock, scattered beads, dim light, 24mm lens. Minh: Vietnamese man, 45, elder brother, stocky build, '
            'short neat black hair greying at the temples, thick eyebrows, stern square face, wears a black '
            "long-sleeve dress shirt with a white mourning band on his left arm. Lan: Vietnamese woman, 42, Minh's "
            'wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy '
            'blouse and black trousers, red lipstick, calculating eyes. Tuan: Vietnamese man, 38, younger brother, '
            'lean and tall, messy medium-length black hair, light stubble, hot-tempered expression, white mourning '
            "headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, 35, Tuan's wife, petite, long straight "
            'black hair worn loose, round face, large jade bangle on right wrist, cream silk blouse and black pencil '
            'skirt, fiery expressive eyes. Mother: Vietnamese grandmother, 72, frail and small, silver hair in a bun, '
            'deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds wooden prayer beads. Style: '
            'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, '
            'shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
            'dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no '
            'watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/minh.png',
            '--ref',
            '<tmp>/assets/characters/lan.png',
            '--ref',
            '<tmp>/assets/characters/tuan.png',
            '--ref',
            '<tmp>/assets/characters/hoa.png',
            '--ref',
            '<tmp>/assets/characters/mother.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s14.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Hospital corridor at night, cold fluorescent light, Minh, Lan, Tuan and Hoa sitting apart on plastic '
            'chairs, heads down, long empty hallway, 35mm lens. Minh: Vietnamese man, 45, elder brother, stocky build, '
            'short neat black hair greying at the temples, thick eyebrows, stern square face, wears a black '
            "long-sleeve dress shirt with a white mourning band on his left arm. Lan: Vietnamese woman, 42, Minh's "
            'wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy '
            'blouse and black trousers, red lipstick, calculating eyes. Tuan: Vietnamese man, 38, younger brother, '
            'lean and tall, messy medium-length black hair, light stubble, hot-tempered expression, white mourning '
            "headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, 35, Tuan's wife, petite, long straight "
            'black hair worn loose, round face, large jade bangle on right wrist, cream silk blouse and black pencil '
            'skirt, fiery expressive eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm '
            'tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber '
            'and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or '
            'lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/minh.png',
            '--ref',
            '<tmp>/assets/characters/lan.png',
            '--ref',
            '<tmp>/assets/characters/tuan.png',
            '--ref',
            '<tmp>/assets/characters/hoa.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s15.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Hospital room, Mother lying awake in bed with oxygen tube, weak smile, holding a small old sealed '
            'envelope, soft morning window light, 50mm lens. Mother: Vietnamese grandmother, 72, frail and small, '
            'silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds wooden '
            'prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior '
            'light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color '
            'grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no '
            'title cards, no watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/mother.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s16.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Close-up of Tuan reading a handwritten letter in a hospital room, Minh reading over his shoulder, morning '
            'light, eyes glistening, 85mm lens. Minh: Vietnamese man, 45, elder brother, stocky build, short neat '
            'black hair greying at the temples, thick eyebrows, stern square face, wears a black long-sleeve dress '
            'shirt with a white mourning band on his left arm. Tuan: Vietnamese man, 38, younger brother, lean and '
            'tall, messy medium-length black hair, light stubble, hot-tempered expression, white mourning headband, '
            'plain grey polo shirt and jeans. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm '
            'tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber '
            'and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or '
            'lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/minh.png',
            '--ref',
            '<tmp>/assets/characters/tuan.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s17.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Lan and Hoa standing near a hospital window, both ashamed, Hoa with a red mark on her cheek, Lan looking '
            "down, soft daylight, 50mm lens. Lan: Vietnamese woman, 42, Minh's wife, slim, sharp cheekbones, black "
            'hair in a tight low bun, thin gold necklace, fitted dark burgundy blouse and black trousers, red '
            "lipstick, calculating eyes. Hoa: Vietnamese woman, 35, Tuan's wife, petite, long straight black hair worn "
            'loose, round face, large jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery '
            'expressive eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior '
            'light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color '
            'grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no '
            'title cards, no watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/lan.png',
            '--ref',
            '<tmp>/assets/characters/hoa.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s18.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            'Minh and Tuan standing at the foot of a hospital bed, Mother smiling weakly between them, morning sun '
            'through blinds, 35mm lens. Minh: Vietnamese man, 45, elder brother, stocky build, short neat black hair '
            'greying at the temples, thick eyebrows, stern square face, wears a black long-sleeve dress shirt with a '
            'white mourning band on his left arm. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy '
            'medium-length black hair, light stubble, hot-tempered expression, white mourning headband, plain grey '
            'polo shirt and jeans. Mother: Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep '
            'wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds wooden prayer beads. Style: '
            'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, '
            'shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
            'dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no '
            'watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/minh.png',
            '--ref',
            '<tmp>/assets/characters/tuan.png',
            '--ref',
            '<tmp>/assets/characters/mother.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s19.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            "Ancestral altar in the family house in daylight, father's framed portrait, fresh incense, Minh, Lan, "
            'Tuan, Hoa and Mother standing together bowing, warm golden light, 24mm lens. Minh: Vietnamese man, 45, '
            'elder brother, stocky build, short neat black hair greying at the temples, thick eyebrows, stern square '
            'face, wears a black long-sleeve dress shirt with a white mourning band on his left arm. Lan: Vietnamese '
            "woman, 42, Minh's wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted "
            'dark burgundy blouse and black trousers, red lipstick, calculating eyes. Tuan: Vietnamese man, 38, '
            'younger brother, lean and tall, messy medium-length black hair, light stubble, hot-tempered expression, '
            "white mourning headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, 35, Tuan's wife, petite, "
            'long straight black hair worn loose, round face, large jade bangle on right wrist, cream silk blouse and '
            'black pencil skirt, fiery expressive eyes. Mother: Vietnamese grandmother, 72, frail and small, silver '
            'hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds wooden prayer '
            'beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with '
            'deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, '
            'fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title '
            'cards, no watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/minh.png',
            '--ref',
            '<tmp>/assets/characters/lan.png',
            '--ref',
            '<tmp>/assets/characters/tuan.png',
            '--ref',
            '<tmp>/assets/characters/hoa.png',
            '--ref',
            '<tmp>/assets/characters/mother.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/s20.png',
            '--project',
            'proj-123',
            '--json'],
           ['image',
            'i2i',
            "The whole family sits around the wooden table, lamp light. Lan: Vietnamese woman, 42, Minh's wife, slim, "
            'sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy blouse and '
            'black trousers, red lipstick, calculating eyes. Tuan: Vietnamese man, 38, younger brother, lean and tall, '
            'messy medium-length black hair, light stubble, hot-tempered expression, white mourning headband, plain '
            "grey polo shirt and jeans. Hoa: Vietnamese woman, 35, Tuan's wife, petite, long straight black hair worn "
            'loose, round face, large jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery '
            'expressive eyes. Mother: Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep '
            'wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds wooden prayer beads. Style: '
            'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, '
            'shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
            'dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no '
            'watermarks, no logos; any dialogue is spoken audio only.',
            '--ref',
            '<tmp>/assets/characters/lan.png',
            '--ref',
            '<tmp>/assets/characters/tuan.png',
            '--ref',
            '<tmp>/assets/characters/hoa.png',
            '--ref',
            '<tmp>/assets/characters/mother.png',
            '--aspect',
            '9:16',
            '--model',
            'nano2',
            '-o',
            '<tmp>/out/keyframes/x3.png',
            '--project',
            'proj-123',
            '--json']],
 'log': ['Tạo project Flow mới: The Inheritance War',
         "  $ <python> <fake_gflow> project create --name 'The Inheritance War' --json",
         '[s01] keyframe: Ancestral altar in a traditional Vietnamese family house, framed black-and-white',
         "  $ <python> <fake_gflow> image t2i 'Ancestral altar in a traditional Vietnamese family house, framed "
         'black-and-white portrait of an elderly man surrounded by incense smoke, candles and white chrysanthemums, '
         'dim warm light, 50mm lens. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten '
         'interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
         'color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no '
         "title cards, no watermarks, no logos; any dialogue is spoken audio only.' --aspect 9:16 --model nano2 -o "
         '<tmp>/out/keyframes/s01.png --project proj-123 --json',
         '[s01] OK -> <tmp>/out/keyframes/s01.png',
         '[s02] keyframe: Wooden living room of an old Vietnamese house at night, Minh and Lan sit on one ',
         '  ảnh tham chiếu nhân vật: Minh, Lan, Tuan, Hoa, Mother',
         "  $ <python> <fake_gflow> image i2i 'Wooden living room of an old Vietnamese house at night, Minh and Lan "
         'sit on one side of a carved wooden table, Tuan and Hoa on the other, Mother sits at the head clutching '
         'prayer beads, a thick envelope lies in the center, tense lighting from a single hanging lamp, 24mm lens. '
         'Minh: Vietnamese man, 45, elder brother, stocky build, short neat black hair greying at the temples, thick '
         'eyebrows, stern square face, wears a black long-sleeve dress shirt with a white mourning band on his left '
         'arm. Lan: Vietnamese woman, 42, Minh\'"\'"\'s wife, slim, sharp cheekbones, black hair in a tight low bun, '
         'thin gold necklace, fitted dark burgundy blouse and black trousers, red lipstick, calculating eyes. Tuan: '
         'Vietnamese man, 38, younger brother, lean and tall, messy medium-length black hair, light stubble, '
         'hot-tempered expression, white mourning headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, '
         '35, Tuan\'"\'"\'s wife, petite, long straight black hair worn loose, round face, large jade bangle on right '
         'wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Mother: Vietnamese grandmother, 72, '
         'frail and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, '
         'holds wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten '
         'interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
         'color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no '
         "title cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref "
         '<tmp>/assets/characters/minh.png --ref <tmp>/assets/characters/lan.png --ref '
         '<tmp>/assets/characters/tuan.png --ref <tmp>/assets/characters/hoa.png --ref '
         '<tmp>/assets/characters/mother.png --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s02.png --project '
         'proj-123 --json',
         '[s02] OK -> <tmp>/out/keyframes/s02.png',
         '[s03] keyframe: Close-up of Minh opening a thick envelope at a wooden table, reading a document,',
         '  ảnh tham chiếu nhân vật: Minh',
         "  $ <python> <fake_gflow> image i2i 'Close-up of Minh opening a thick envelope at a wooden table, reading a "
         'document, lamp light on his stern face, 85mm lens. Shot: close-up. Focus: rack focus from foreground to '
         'background. Lighting: single hanging lamp. Minh: Vietnamese man, 45, elder brother, stocky build, short neat '
         'black hair greying at the temples, thick eyebrows, stern square face, wears a black long-sleeve dress shirt '
         'with a white mourning band on his left arm. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, '
         'warm tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber '
         'and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or '
         "lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref "
         '<tmp>/assets/characters/minh.png --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s03.png --project '
         'proj-123 --json',
         '[s03] OK -> <tmp>/out/keyframes/s03.png',
         '[s04] keyframe: Close-up of Hoa at a wooden table, eyes wide in disbelief, jade bangle on her wr',
         '  ảnh tham chiếu nhân vật: Tuan, Hoa',
         "  $ <python> <fake_gflow> image i2i 'Close-up of Hoa at a wooden table, eyes wide in disbelief, jade bangle "
         'on her wrist resting on the table, warm side light, 85mm lens. Hoa: Vietnamese woman, 35, Tuan\'"\'"\'s '
         'wife, petite, long straight black hair worn loose, round face, large jade bangle on right wrist, cream silk '
         'blouse and black pencil skirt, fiery expressive eyes. Style: Cinematic Vietnamese family drama, vertical '
         '9:16 framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, '
         'high-contrast amber and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no '
         "on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' "
         '--ref <tmp>/assets/characters/tuan.png --ref <tmp>/assets/characters/hoa.png --aspect 9:16 --model nano2 -o '
         '<tmp>/out/keyframes/s04.png --project proj-123 --json',
         '[s04] OK -> <tmp>/out/keyframes/s04.png',
         '[s05] keyframe: Medium shot of Lan sitting upright with arms folded, cold smile, red lipstick, g',
         '  ảnh tham chiếu nhân vật: Lan',
         "  $ <python> <fake_gflow> image i2i 'Medium shot of Lan sitting upright with arms folded, cold smile, red "
         'lipstick, gold necklace catching the light, dark living room background, 50mm lens. Lan: Vietnamese woman, '
         '42, Minh\'"\'"\'s wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted '
         'dark burgundy blouse and black trousers, red lipstick, calculating eyes. Style: Cinematic Vietnamese family '
         'drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm '
         'film grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy. No captions, no '
         'subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken '
         "audio only.' --ref <tmp>/assets/characters/lan.png --aspect 9:16 --model nano2 -o "
         '<tmp>/out/keyframes/s05.png --project proj-123 --json',
         '[s05] OK -> <tmp>/out/keyframes/s05.png',
         '[s06] keyframe: Tuan standing abruptly from a wooden chair in a dim living room, chair tipping b',
         '  ảnh tham chiếu nhân vật: Minh, Tuan',
         "  $ <python> <fake_gflow> image i2i 'Tuan standing abruptly from a wooden chair in a dim living room, chair "
         'tipping backward, furious face, mourning headband, 35mm lens. Tuan: Vietnamese man, 38, younger brother, '
         'lean and tall, messy medium-length black hair, light stubble, hot-tempered expression, white mourning '
         'headband, plain grey polo shirt and jeans. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, '
         'warm tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber '
         'and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or '
         "lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref "
         '<tmp>/assets/characters/minh.png --ref <tmp>/assets/characters/tuan.png --aspect 9:16 --model nano2 -o '
         '<tmp>/out/keyframes/s06.png --project proj-123 --json',
         '[s06] OK -> <tmp>/out/keyframes/s06.png',
         '[s07] keyframe: Minh rising from his seat facing camera, veins on his neck, dim lamp light, his ',
         '  ảnh tham chiếu nhân vật: Minh, Tuan',
         "  $ <python> <fake_gflow> image i2i 'Minh rising from his seat facing camera, veins on his neck, dim lamp "
         'light, his brother\'"\'"\'s shoulder blurred in the foreground, 50mm lens. Minh: Vietnamese man, 45, elder '
         'brother, stocky build, short neat black hair greying at the temples, thick eyebrows, stern square face, '
         'wears a black long-sleeve dress shirt with a white mourning band on his left arm. Style: Cinematic '
         'Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow '
         'depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy. '
         'No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any '
         "dialogue is spoken audio only.' --ref <tmp>/assets/characters/minh.png --ref "
         '<tmp>/assets/characters/tuan.png --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s07.png --project '
         'proj-123 --json',
         '[s07] OK -> <tmp>/out/keyframes/s07.png',
         '[s08] keyframe: Mother seated at the head of the table, frail hands gripping prayer beads, eyes ',
         '  ảnh tham chiếu nhân vật: Mother',
         "  $ <python> <fake_gflow> image i2i 'Mother seated at the head of the table, frail hands gripping prayer "
         'beads, eyes filled with tears, soft backlight from the altar, 85mm lens. Mother: Vietnamese grandmother, 72, '
         'frail and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, '
         'holds wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten '
         'interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
         'color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no '
         "title cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref "
         '<tmp>/assets/characters/mother.png --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s08.png --project '
         'proj-123 --json',
         '[s08] OK -> <tmp>/out/keyframes/s08.png',
         '[s09] keyframe: Hoa and Lan standing face to face beside the wooden table, Hoa with loose hair g',
         '  ảnh tham chiếu nhân vật: Lan, Hoa',
         "  $ <python> <fake_gflow> image i2i 'Hoa and Lan standing face to face beside the wooden table, Hoa with "
         'loose hair glaring up, Lan smirking down, harsh overhead lamp, 35mm lens. Lan: Vietnamese woman, 42, '
         'Minh\'"\'"\'s wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark '
         'burgundy blouse and black trousers, red lipstick, calculating eyes. Hoa: Vietnamese woman, 35, Tuan\'"\'"\'s '
         'wife, petite, long straight black hair worn loose, round face, large jade bangle on right wrist, cream silk '
         'blouse and black pencil skirt, fiery expressive eyes. Style: Cinematic Vietnamese family drama, vertical '
         '9:16 framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, '
         'high-contrast amber and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no '
         "on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' "
         '--ref <tmp>/assets/characters/lan.png --ref <tmp>/assets/characters/hoa.png --aspect 9:16 --model nano2 -o '
         '<tmp>/out/keyframes/s09.png --project proj-123 --json',
         '[s09] OK -> <tmp>/out/keyframes/s09.png',
         '[s10] keyframe: Lan in a burgundy blouse with her hand raised mid-swing, Hoa flinching, dim livi',
         '  ảnh tham chiếu nhân vật: Lan, Hoa',
         "  $ <python> <fake_gflow> image i2i 'Lan in a burgundy blouse with her hand raised mid-swing, Hoa flinching, "
         'dim living room, motion blur, 50mm lens. Lan: Vietnamese woman, 42, Minh\'"\'"\'s wife, slim, sharp '
         'cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy blouse and black '
         'trousers, red lipstick, calculating eyes. Hoa: Vietnamese woman, 35, Tuan\'"\'"\'s wife, petite, long '
         'straight black hair worn loose, round face, large jade bangle on right wrist, cream silk blouse and black '
         'pencil skirt, fiery expressive eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm '
         'tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and '
         'teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or '
         "lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref "
         '<tmp>/assets/characters/lan.png --ref <tmp>/assets/characters/hoa.png --aspect 9:16 --model nano2 -o '
         '<tmp>/out/keyframes/s10.png --project proj-123 --json',
         '[s10] OK -> <tmp>/out/keyframes/s10.png',
         '[s11] keyframe: Hoa holding her cheek, eyes burning, Tuan behind her lunging forward, Minh grabb',
         '  ảnh tham chiếu nhân vật: Minh, Lan, Tuan, Hoa',
         "  $ <python> <fake_gflow> image i2i 'Hoa holding her cheek, eyes burning, Tuan behind her lunging forward, "
         'Minh grabbing his arm, chaotic lamp-lit room, 24mm lens. Minh: Vietnamese man, 45, elder brother, stocky '
         'build, short neat black hair greying at the temples, thick eyebrows, stern square face, wears a black '
         'long-sleeve dress shirt with a white mourning band on his left arm. Tuan: Vietnamese man, 38, younger '
         'brother, lean and tall, messy medium-length black hair, light stubble, hot-tempered expression, white '
         'mourning headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, 35, Tuan\'"\'"\'s wife, petite, '
         'long straight black hair worn loose, round face, large jade bangle on right wrist, cream silk blouse and '
         'black pencil skirt, fiery expressive eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, '
         'warm tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber '
         'and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or '
         "lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref "
         '<tmp>/assets/characters/minh.png --ref <tmp>/assets/characters/lan.png --ref '
         '<tmp>/assets/characters/tuan.png --ref <tmp>/assets/characters/hoa.png --aspect 9:16 --model nano2 -o '
         '<tmp>/out/keyframes/s11.png --project proj-123 --json',
         '[s11] OK -> <tmp>/out/keyframes/s11.png',
         "[s12] keyframe: Close-up of Tuan's hands gripping the will document, Minh's hands pulling the ot",
         '  ảnh tham chiếu nhân vật: Minh, Tuan',
         '  $ <python> <fake_gflow> image i2i \'Close-up of Tuan\'"\'"\'s hands gripping the will document, '
         'Minh\'"\'"\'s hands pulling the other side, paper stretched tight, dramatic side light, 85mm lens. Minh: '
         'Vietnamese man, 45, elder brother, stocky build, short neat black hair greying at the temples, thick '
         'eyebrows, stern square face, wears a black long-sleeve dress shirt with a white mourning band on his left '
         'arm. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy medium-length black hair, light '
         'stubble, hot-tempered expression, white mourning headband, plain grey polo shirt and jeans. Style: Cinematic '
         'Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow '
         'depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy. '
         'No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any '
         "dialogue is spoken audio only.' --ref <tmp>/assets/characters/minh.png --ref "
         '<tmp>/assets/characters/tuan.png --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s12.png --project '
         'proj-123 --json',
         '[s12] OK -> <tmp>/out/keyframes/s12.png',
         '[s13] keyframe: Mother clutching her chest, prayer beads slipping from her fingers, swaying in h',
         '  ảnh tham chiếu nhân vật: Mother',
         "  $ <python> <fake_gflow> image i2i 'Mother clutching her chest, prayer beads slipping from her fingers, "
         'swaying in her chair, altar candles behind her, 50mm lens. Mother: Vietnamese grandmother, 72, frail and '
         'small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds '
         'wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior '
         'light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, '
         'fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, '
         "no watermarks, no logos; any dialogue is spoken audio only.' --ref <tmp>/assets/characters/mother.png "
         '--aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s13.png --project proj-123 --json',
         '[s13] OK -> <tmp>/out/keyframes/s13.png',
         '[s14] keyframe: Minh and Tuan kneeling on a tiled floor holding their unconscious mother, Lan an',
         '  ảnh tham chiếu nhân vật: Minh, Lan, Tuan, Hoa, Mother',
         "  $ <python> <fake_gflow> image i2i 'Minh and Tuan kneeling on a tiled floor holding their unconscious "
         'mother, Lan and Hoa frozen behind them in shock, scattered beads, dim light, 24mm lens. Minh: Vietnamese '
         'man, 45, elder brother, stocky build, short neat black hair greying at the temples, thick eyebrows, stern '
         'square face, wears a black long-sleeve dress shirt with a white mourning band on his left arm. Lan: '
         'Vietnamese woman, 42, Minh\'"\'"\'s wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold '
         'necklace, fitted dark burgundy blouse and black trousers, red lipstick, calculating eyes. Tuan: Vietnamese '
         'man, 38, younger brother, lean and tall, messy medium-length black hair, light stubble, hot-tempered '
         'expression, white mourning headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, 35, '
         'Tuan\'"\'"\'s wife, petite, long straight black hair worn loose, round face, large jade bangle on right '
         'wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Mother: Vietnamese grandmother, 72, '
         'frail and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, '
         'holds wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten '
         'interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
         'color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no '
         "title cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref "
         '<tmp>/assets/characters/minh.png --ref <tmp>/assets/characters/lan.png --ref '
         '<tmp>/assets/characters/tuan.png --ref <tmp>/assets/characters/hoa.png --ref '
         '<tmp>/assets/characters/mother.png --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s14.png --project '
         'proj-123 --json',
         '[s14] OK -> <tmp>/out/keyframes/s14.png',
         '[s15] keyframe: Hospital corridor at night, cold fluorescent light, Minh, Lan, Tuan and Hoa sitt',
         '  ảnh tham chiếu nhân vật: Minh, Lan, Tuan, Hoa',
         "  $ <python> <fake_gflow> image i2i 'Hospital corridor at night, cold fluorescent light, Minh, Lan, Tuan and "
         'Hoa sitting apart on plastic chairs, heads down, long empty hallway, 35mm lens. Minh: Vietnamese man, 45, '
         'elder brother, stocky build, short neat black hair greying at the temples, thick eyebrows, stern square '
         'face, wears a black long-sleeve dress shirt with a white mourning band on his left arm. Lan: Vietnamese '
         'woman, 42, Minh\'"\'"\'s wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, '
         'fitted dark burgundy blouse and black trousers, red lipstick, calculating eyes. Tuan: Vietnamese man, 38, '
         'younger brother, lean and tall, messy medium-length black hair, light stubble, hot-tempered expression, '
         'white mourning headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, 35, Tuan\'"\'"\'s wife, '
         'petite, long straight black hair worn loose, round face, large jade bangle on right wrist, cream silk blouse '
         'and black pencil skirt, fiery expressive eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 '
         'framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, '
         'high-contrast amber and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no '
         "on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' "
         '--ref <tmp>/assets/characters/minh.png --ref <tmp>/assets/characters/lan.png --ref '
         '<tmp>/assets/characters/tuan.png --ref <tmp>/assets/characters/hoa.png --aspect 9:16 --model nano2 -o '
         '<tmp>/out/keyframes/s15.png --project proj-123 --json',
         '[s15] OK -> <tmp>/out/keyframes/s15.png',
         '[s16] keyframe: Hospital room, Mother lying awake in bed with oxygen tube, weak smile, holding a',
         '  ảnh tham chiếu nhân vật: Mother',
         "  $ <python> <fake_gflow> image i2i 'Hospital room, Mother lying awake in bed with oxygen tube, weak smile, "
         'holding a small old sealed envelope, soft morning window light, 50mm lens. Mother: Vietnamese grandmother, '
         '72, frail and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba '
         'ba, holds wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm '
         'tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and '
         'teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or '
         "lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref "
         '<tmp>/assets/characters/mother.png --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s16.png --project '
         'proj-123 --json',
         '[s16] OK -> <tmp>/out/keyframes/s16.png',
         '[s17] keyframe: Close-up of Tuan reading a handwritten letter in a hospital room, Minh reading o',
         '  ảnh tham chiếu nhân vật: Minh, Tuan',
         "  $ <python> <fake_gflow> image i2i 'Close-up of Tuan reading a handwritten letter in a hospital room, Minh "
         'reading over his shoulder, morning light, eyes glistening, 85mm lens. Minh: Vietnamese man, 45, elder '
         'brother, stocky build, short neat black hair greying at the temples, thick eyebrows, stern square face, '
         'wears a black long-sleeve dress shirt with a white mourning band on his left arm. Tuan: Vietnamese man, 38, '
         'younger brother, lean and tall, messy medium-length black hair, light stubble, hot-tempered expression, '
         'white mourning headband, plain grey polo shirt and jeans. Style: Cinematic Vietnamese family drama, vertical '
         '9:16 framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, '
         'high-contrast amber and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no '
         "on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' "
         '--ref <tmp>/assets/characters/minh.png --ref <tmp>/assets/characters/tuan.png --aspect 9:16 --model nano2 -o '
         '<tmp>/out/keyframes/s17.png --project proj-123 --json',
         '[s17] OK -> <tmp>/out/keyframes/s17.png',
         '[s18] keyframe: Lan and Hoa standing near a hospital window, both ashamed, Hoa with a red mark o',
         '  ảnh tham chiếu nhân vật: Lan, Hoa',
         "  $ <python> <fake_gflow> image i2i 'Lan and Hoa standing near a hospital window, both ashamed, Hoa with a "
         'red mark on her cheek, Lan looking down, soft daylight, 50mm lens. Lan: Vietnamese woman, 42, Minh\'"\'"\'s '
         'wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy blouse '
         'and black trousers, red lipstick, calculating eyes. Hoa: Vietnamese woman, 35, Tuan\'"\'"\'s wife, petite, '
         'long straight black hair worn loose, round face, large jade bangle on right wrist, cream silk blouse and '
         'black pencil skirt, fiery expressive eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, '
         'warm tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber '
         'and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or '
         "lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref "
         '<tmp>/assets/characters/lan.png --ref <tmp>/assets/characters/hoa.png --aspect 9:16 --model nano2 -o '
         '<tmp>/out/keyframes/s18.png --project proj-123 --json',
         '[s18] OK -> <tmp>/out/keyframes/s18.png',
         '[s19] keyframe: Minh and Tuan standing at the foot of a hospital bed, Mother smiling weakly betw',
         '  ảnh tham chiếu nhân vật: Minh, Tuan, Mother',
         "  $ <python> <fake_gflow> image i2i 'Minh and Tuan standing at the foot of a hospital bed, Mother smiling "
         'weakly between them, morning sun through blinds, 35mm lens. Minh: Vietnamese man, 45, elder brother, stocky '
         'build, short neat black hair greying at the temples, thick eyebrows, stern square face, wears a black '
         'long-sleeve dress shirt with a white mourning band on his left arm. Tuan: Vietnamese man, 38, younger '
         'brother, lean and tall, messy medium-length black hair, light stubble, hot-tempered expression, white '
         'mourning headband, plain grey polo shirt and jeans. Mother: Vietnamese grandmother, 72, frail and small, '
         'silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds wooden '
         'prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light '
         'with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, '
         'fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, '
         "no watermarks, no logos; any dialogue is spoken audio only.' --ref <tmp>/assets/characters/minh.png --ref "
         '<tmp>/assets/characters/tuan.png --ref <tmp>/assets/characters/mother.png --aspect 9:16 --model nano2 -o '
         '<tmp>/out/keyframes/s19.png --project proj-123 --json',
         '[s19] OK -> <tmp>/out/keyframes/s19.png',
         "[s20] keyframe: Ancestral altar in the family house in daylight, father's framed portrait, fresh",
         '  ảnh tham chiếu nhân vật: Minh, Lan, Tuan, Hoa, Mother',
         '  $ <python> <fake_gflow> image i2i \'Ancestral altar in the family house in daylight, father\'"\'"\'s '
         'framed portrait, fresh incense, Minh, Lan, Tuan, Hoa and Mother standing together bowing, warm golden light, '
         '24mm lens. Minh: Vietnamese man, 45, elder brother, stocky build, short neat black hair greying at the '
         'temples, thick eyebrows, stern square face, wears a black long-sleeve dress shirt with a white mourning band '
         'on his left arm. Lan: Vietnamese woman, 42, Minh\'"\'"\'s wife, slim, sharp cheekbones, black hair in a '
         'tight low bun, thin gold necklace, fitted dark burgundy blouse and black trousers, red lipstick, calculating '
         'eyes. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy medium-length black hair, light '
         'stubble, hot-tempered expression, white mourning headband, plain grey polo shirt and jeans. Hoa: Vietnamese '
         'woman, 35, Tuan\'"\'"\'s wife, petite, long straight black hair worn loose, round face, large jade bangle on '
         'right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Mother: Vietnamese '
         'grandmother, 72, frail and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional '
         'mourning ao ba ba, holds wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 '
         'framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, '
         'high-contrast amber and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no '
         "on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' "
         '--ref <tmp>/assets/characters/minh.png --ref <tmp>/assets/characters/lan.png --ref '
         '<tmp>/assets/characters/tuan.png --ref <tmp>/assets/characters/hoa.png --ref '
         '<tmp>/assets/characters/mother.png --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s20.png --project '
         'proj-123 --json',
         '[s20] OK -> <tmp>/out/keyframes/s20.png',
         '[x3] keyframe: The whole family sits around the wooden table, lamp light',
         '  ảnh tham chiếu nhân vật: Lan, Tuan, Hoa, Mother',
         "  $ <python> <fake_gflow> image i2i 'The whole family sits around the wooden table, lamp light. Lan: "
         'Vietnamese woman, 42, Minh\'"\'"\'s wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold '
         'necklace, fitted dark burgundy blouse and black trousers, red lipstick, calculating eyes. Tuan: Vietnamese '
         'man, 38, younger brother, lean and tall, messy medium-length black hair, light stubble, hot-tempered '
         'expression, white mourning headband, plain grey polo shirt and jeans. Hoa: Vietnamese woman, 35, '
         'Tuan\'"\'"\'s wife, petite, long straight black hair worn loose, round face, large jade bangle on right '
         'wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Mother: Vietnamese grandmother, 72, '
         'frail and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, '
         'holds wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten '
         'interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
         'color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no '
         "title cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref "
         '<tmp>/assets/characters/lan.png --ref <tmp>/assets/characters/tuan.png --ref <tmp>/assets/characters/hoa.png '
         '--ref <tmp>/assets/characters/mother.png --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/x3.png --project '
         'proj-123 --json',
         '[x3] OK -> <tmp>/out/keyframes/x3.png'],
 'state_keys': {'s01': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's02': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's03': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's04': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's05': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's06': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's07': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's08': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's09': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's10': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's11': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's12': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's13': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's14': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's15': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's16': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's17': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's18': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's19': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                's20': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at'],
                'x3': ['keyframe', 'keyframe_prompt', 'keyframe_refs', 'updated_at']},
 'has_keyframe': {'s01': True,
                  's02': True,
                  's03': True,
                  's04': True,
                  's05': True,
                  's06': True,
                  's07': True,
                  's08': True,
                  's09': True,
                  's10': True,
                  's11': True,
                  's12': True,
                  's13': True,
                  's14': True,
                  's15': True,
                  's16': True,
                  's17': True,
                  's18': True,
                  's19': True,
                  's20': True,
                  'x1': False,
                  'x2': False,
                  'x3': True}}
GOLDEN_DRY_LOG = ['Tạo project Flow mới: The Inheritance War',
 "  $ gflow project create --name 'The Inheritance War' --json",
 'Project: DRY-RUN-PROJECT',
 "[s01] keyframe: Slow push-in toward the father's portrait on the altar as incense smoke curls up",
 "  $ gflow image t2i 'Ancestral altar in a traditional Vietnamese family house, framed black-and-white portrait of an "
 'elderly man surrounded by incense smoke, candles and white chrysanthemums, dim warm light, 50mm lens. Style: '
 'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow '
 'depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy. No '
 'captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is '
 "spoken audio only.' --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s01.png --project DRY-RUN-PROJECT --json",
 '  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s01.png \'Slow push-in toward the father\'"\'"\'s portrait '
 'on the altar as incense smoke curls upward; distant funeral gong and soft crying. No captions, no subtitles, no '
 "on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --aspect "
 '9:16 -o <tmp>/out/clips/s01.mp4 --model veo-lite --project DRY-RUN-PROJECT --json',
 '[s01] (dry-run) sẽ lưu -> <tmp>/out/clips/s01.mp4',
 "[s02] keyframe: Slow overhead crane down onto the table as Mother's trembling hand slides the en",
 '  ảnh tham chiếu nhân vật: Minh, Lan, Tuan, Hoa, Mother',
 "  $ gflow image i2i 'Wooden living room of an old Vietnamese house at night, Minh and Lan sit on one side of a "
 'carved wooden table, Tuan and Hoa on the other, Mother sits at the head clutching prayer beads, a thick envelope '
 'lies in the center, tense lighting from a single hanging lamp, 24mm lens. Minh: Vietnamese man, 45, elder brother, '
 'stocky build, short neat black hair greying at the temples, thick eyebrows, stern square face, wears a black '
 'long-sleeve dress shirt with a white mourning band on his left arm. Lan: Vietnamese woman, 42, Minh\'"\'"\'s wife, '
 'slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy blouse and black '
 'trousers, red lipstick, calculating eyes. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy '
 'medium-length black hair, light stubble, hot-tempered expression, white mourning headband, plain grey polo shirt and '
 'jeans. Hoa: Vietnamese woman, 35, Tuan\'"\'"\'s wife, petite, long straight black hair worn loose, round face, large '
 'jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Mother: Vietnamese '
 'grandmother, 72, frail and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning '
 'ao ba ba, holds wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten '
 'interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, '
 'fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no '
 "watermarks, no logos; any dialogue is spoken audio only.' --ref <tmp>/assets/characters/minh.png --ref "
 '<tmp>/assets/characters/lan.png --ref <tmp>/assets/characters/tuan.png --ref <tmp>/assets/characters/hoa.png --ref '
 '<tmp>/assets/characters/mother.png --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s02.png --project '
 'DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s02.png 'Slow overhead crane down onto the table as "
 'Mother\'"\'"\'s trembling hand slides the envelope to the center; clock ticking loudly. No captions, no subtitles, '
 "no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' "
 '--aspect 9:16 -o <tmp>/out/clips/s02.mp4 --model veo-lite --project DRY-RUN-PROJECT --json',
 '[s02] (dry-run) sẽ lưu -> <tmp>/out/clips/s02.mp4',
 '[s03] keyframe: Minh tears open the envelope and reads; camera quickly racks focus from the pape',
 '  ảnh tham chiếu nhân vật: Minh',
 "  $ gflow image i2i 'Close-up of Minh opening a thick envelope at a wooden table, reading a document, lamp light on "
 'his stern face, 85mm lens. Shot: close-up. Focus: rack focus from foreground to background. Lighting: single hanging '
 'lamp. Minh: Vietnamese man, 45, elder brother, stocky build, short neat black hair greying at the temples, thick '
 'eyebrows, stern square face, wears a black long-sleeve dress shirt with a white mourning band on his left arm. '
 'Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, '
 'shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy. '
 'No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is '
 "spoken audio only.' --ref <tmp>/assets/characters/minh.png --aspect 9:16 --model nano2 -o "
 '<tmp>/out/keyframes/s03.png --project DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s03.png 'Minh tears open the envelope and reads; camera "
 'quickly racks focus from the paper to his face as a faint satisfied smirk appears; paper rustling. Camera movement: '
 'slow push-in. Focus: rack focus from foreground to background. No captions, no subtitles, no on-screen text or '
 "lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --aspect 9:16 -o "
 '<tmp>/out/clips/s03.mp4 --model veo-lite --project DRY-RUN-PROJECT --json',
 '[s03] (dry-run) sẽ lưu -> <tmp>/out/clips/s03.mp4',
 '[s04] keyframe: Hoa slams her palm on the table, jade bangle clacking, and leans forward; fast w',
 '  ảnh tham chiếu nhân vật: Tuan, Hoa',
 "  $ gflow image i2i 'Close-up of Hoa at a wooden table, eyes wide in disbelief, jade bangle on her wrist resting on "
 'the table, warm side light, 85mm lens. Hoa: Vietnamese woman, 35, Tuan\'"\'"\'s wife, petite, long straight black '
 'hair worn loose, round face, large jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery '
 'expressive eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with '
 'deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
 'dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; '
 "any dialogue is spoken audio only.' --ref <tmp>/assets/characters/tuan.png --ref <tmp>/assets/characters/hoa.png "
 '--aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s04.png --project DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s04.png 'Hoa slams her palm on the table, jade bangle "
 'clacking, and leans forward; fast whip pan to Tuan beside her clenching his jaw. She shouts angrily in Vietnamese, '
 'voice only. No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any '
 "dialogue is spoken audio only.' --aspect 9:16 -o <tmp>/out/clips/s04.mp4 --model veo-lite --project DRY-RUN-PROJECT "
 '--json',
 '[s04] (dry-run) sẽ lưu -> <tmp>/out/clips/s04.mp4',
 '[s05] keyframe: Lan slowly tilts her head and smiles coldly, speaking calmly and mockingly in Vi',
 '  ảnh tham chiếu nhân vật: Lan',
 "  $ gflow image i2i 'Medium shot of Lan sitting upright with arms folded, cold smile, red lipstick, gold necklace "
 'catching the light, dark living room background, 50mm lens. Lan: Vietnamese woman, 42, Minh\'"\'"\'s wife, slim, '
 'sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy blouse and black trousers, '
 'red lipstick, calculating eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten '
 'interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, '
 'fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no '
 "watermarks, no logos; any dialogue is spoken audio only.' --ref <tmp>/assets/characters/lan.png --aspect 9:16 "
 '--model nano2 -o <tmp>/out/keyframes/s05.png --project DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s05.png 'Lan slowly tilts her head and smiles coldly, "
 'speaking calmly and mockingly in Vietnamese, voice only; slow dolly-in on her face. No captions, no subtitles, no '
 "on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --aspect "
 '9:16 -o <tmp>/out/clips/s05.mp4 --model veo-lite --project DRY-RUN-PROJECT --json',
 '[s05] (dry-run) sẽ lưu -> <tmp>/out/clips/s05.mp4',
 '[s06] keyframe: Tuan leaps up, his chair crashes to the floor, and he points at Minh, yelling in',
 '  ảnh tham chiếu nhân vật: Minh, Tuan',
 "  $ gflow image i2i 'Tuan standing abruptly from a wooden chair in a dim living room, chair tipping backward, "
 'furious face, mourning headband, 35mm lens. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy '
 'medium-length black hair, light stubble, hot-tempered expression, white mourning headband, plain grey polo shirt and '
 'jeans. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep '
 'shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
 'dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; '
 "any dialogue is spoken audio only.' --ref <tmp>/assets/characters/minh.png --ref <tmp>/assets/characters/tuan.png "
 '--aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s06.png --project DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s06.png 'Tuan leaps up, his chair crashes to the floor, and "
 'he points at Minh, yelling in Vietnamese, voice only; handheld camera shakes with the movement. No captions, no '
 'subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio '
 "only.' --aspect 9:16 -o <tmp>/out/clips/s06.mp4 --model veo-lite --project DRY-RUN-PROJECT --json",
 '[s06] (dry-run) sẽ lưu -> <tmp>/out/clips/s06.mp4',
 "[s07] keyframe: Minh rises and steps into Tuan's face, the two brothers chest to chest; fast orb",
 '  ảnh tham chiếu nhân vật: Minh, Tuan',
 "  $ gflow image i2i 'Minh rising from his seat facing camera, veins on his neck, dim lamp light, his "
 'brother\'"\'"\'s shoulder blurred in the foreground, 50mm lens. Minh: Vietnamese man, 45, elder brother, stocky '
 'build, short neat black hair greying at the temples, thick eyebrows, stern square face, wears a black long-sleeve '
 'dress shirt with a white mourning band on his left arm. Style: Cinematic Vietnamese family drama, vertical 9:16 '
 'framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast '
 'amber and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or '
 "lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref "
 '<tmp>/assets/characters/minh.png --ref <tmp>/assets/characters/tuan.png --aspect 9:16 --model nano2 -o '
 '<tmp>/out/keyframes/s07.png --project DRY-RUN-PROJECT --json',
 '  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s07.png \'Minh rises and steps into Tuan\'"\'"\'s face, the '
 'two brothers chest to chest; fast orbiting handheld shot around them, heavy breathing, rising tension score. No '
 'captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is '
 "spoken audio only.' --aspect 9:16 -o <tmp>/out/clips/s07.mp4 --model veo-lite --project DRY-RUN-PROJECT --json",
 '[s07] (dry-run) sẽ lưu -> <tmp>/out/clips/s07.mp4',
 '[s08] keyframe: Mother reaches out with a trembling hand, softly pleading in Vietnamese, voice o',
 '  ảnh tham chiếu nhân vật: Mother',
 "  $ gflow image i2i 'Mother seated at the head of the table, frail hands gripping prayer beads, eyes filled with "
 'tears, soft backlight from the altar, 85mm lens. Mother: Vietnamese grandmother, 72, frail and small, silver hair in '
 'a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds wooden prayer beads. Style: '
 'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow '
 'depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy. No '
 'captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is '
 "spoken audio only.' --ref <tmp>/assets/characters/mother.png --aspect 9:16 --model nano2 -o "
 '<tmp>/out/keyframes/s08.png --project DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s08.png 'Mother reaches out with a trembling hand, softly "
 'pleading in Vietnamese, voice only, while the shouting continues off-screen; slow push-in on her tearful eyes. No '
 'captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is '
 "spoken audio only.' --aspect 9:16 -o <tmp>/out/clips/s08.mp4 --model veo-lite --project DRY-RUN-PROJECT --json",
 '[s08] (dry-run) sẽ lưu -> <tmp>/out/clips/s08.mp4',
 '[s09] keyframe: Hoa and Lan step toward each other, trading furious words in Vietnamese, voice o',
 '  ảnh tham chiếu nhân vật: Lan, Hoa',
 "  $ gflow image i2i 'Hoa and Lan standing face to face beside the wooden table, Hoa with loose hair glaring up, Lan "
 'smirking down, harsh overhead lamp, 35mm lens. Lan: Vietnamese woman, 42, Minh\'"\'"\'s wife, slim, sharp '
 'cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy blouse and black trousers, red '
 'lipstick, calculating eyes. Hoa: Vietnamese woman, 35, Tuan\'"\'"\'s wife, petite, long straight black hair worn '
 'loose, round face, large jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery expressive '
 'eyes. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep '
 'shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
 'dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; '
 "any dialogue is spoken audio only.' --ref <tmp>/assets/characters/lan.png --ref <tmp>/assets/characters/hoa.png "
 '--aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s09.png --project DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s09.png 'Hoa and Lan step toward each other, trading furious "
 'words in Vietnamese, voice only; rapid handheld push-in between their faces. No captions, no subtitles, no on-screen '
 "text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --aspect 9:16 -o "
 '<tmp>/out/clips/s09.mp4 --model veo-lite --project DRY-RUN-PROJECT --json',
 '[s09] (dry-run) sẽ lưu -> <tmp>/out/clips/s09.mp4',
 "[s10] keyframe: Lan slaps Hoa hard across the face, Hoa's hair whips sideways; sharp slap sound,",
 '  ảnh tham chiếu nhân vật: Lan, Hoa',
 "  $ gflow image i2i 'Lan in a burgundy blouse with her hand raised mid-swing, Hoa flinching, dim living room, motion "
 'blur, 50mm lens. Lan: Vietnamese woman, 42, Minh\'"\'"\'s wife, slim, sharp cheekbones, black hair in a tight low '
 'bun, thin gold necklace, fitted dark burgundy blouse and black trousers, red lipstick, calculating eyes. Hoa: '
 'Vietnamese woman, 35, Tuan\'"\'"\'s wife, petite, long straight black hair worn loose, round face, large jade bangle '
 'on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Style: Cinematic Vietnamese family '
 'drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm film '
 'grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no '
 "on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref "
 '<tmp>/assets/characters/lan.png --ref <tmp>/assets/characters/hoa.png --aspect 9:16 --model nano2 -o '
 '<tmp>/out/keyframes/s10.png --project DRY-RUN-PROJECT --json',
 '  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s10.png \'Lan slaps Hoa hard across the face, Hoa\'"\'"\'s '
 'hair whips sideways; sharp slap sound, camera snap-zooms in, then silence. No captions, no subtitles, no on-screen '
 "text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --aspect 9:16 -o "
 '<tmp>/out/clips/s10.mp4 --model veo-lite --project DRY-RUN-PROJECT --json',
 '[s10] (dry-run) sẽ lưu -> <tmp>/out/clips/s10.mp4',
 '[s11] keyframe: Tuan lunges at Lan while Minh grabs and shoves him back; chaotic handheld tracki',
 '  ảnh tham chiếu nhân vật: Minh, Lan, Tuan, Hoa',
 "  $ gflow image i2i 'Hoa holding her cheek, eyes burning, Tuan behind her lunging forward, Minh grabbing his arm, "
 'chaotic lamp-lit room, 24mm lens. Minh: Vietnamese man, 45, elder brother, stocky build, short neat black hair '
 'greying at the temples, thick eyebrows, stern square face, wears a black long-sleeve dress shirt with a white '
 'mourning band on his left arm. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy medium-length black '
 'hair, light stubble, hot-tempered expression, white mourning headband, plain grey polo shirt and jeans. Hoa: '
 'Vietnamese woman, 35, Tuan\'"\'"\'s wife, petite, long straight black hair worn loose, round face, large jade bangle '
 'on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Style: Cinematic Vietnamese family '
 'drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm film '
 'grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no '
 "on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref "
 '<tmp>/assets/characters/minh.png --ref <tmp>/assets/characters/lan.png --ref <tmp>/assets/characters/tuan.png --ref '
 '<tmp>/assets/characters/hoa.png --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s11.png --project DRY-RUN-PROJECT '
 '--json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s11.png 'Tuan lunges at Lan while Minh grabs and shoves him "
 'back; chaotic handheld tracking shot as a teacup shatters on the floor. No captions, no subtitles, no on-screen text '
 "or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --aspect 9:16 -o "
 '<tmp>/out/clips/s11.mp4 --model veo-lite --project DRY-RUN-PROJECT --json',
 '[s11] (dry-run) sẽ lưu -> <tmp>/out/clips/s11.mp4',
 '[s12] keyframe: Tuan and Minh tug at the will until it rips in half; extreme slow motion of the ',
 '  ảnh tham chiếu nhân vật: Minh, Tuan',
 '  $ gflow image i2i \'Close-up of Tuan\'"\'"\'s hands gripping the will document, Minh\'"\'"\'s hands pulling the '
 'other side, paper stretched tight, dramatic side light, 85mm lens. Minh: Vietnamese man, 45, elder brother, stocky '
 'build, short neat black hair greying at the temples, thick eyebrows, stern square face, wears a black long-sleeve '
 'dress shirt with a white mourning band on his left arm. Tuan: Vietnamese man, 38, younger brother, lean and tall, '
 'messy medium-length black hair, light stubble, hot-tempered expression, white mourning headband, plain grey polo '
 'shirt and jeans. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with '
 'deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
 'dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; '
 "any dialogue is spoken audio only.' --ref <tmp>/assets/characters/minh.png --ref <tmp>/assets/characters/tuan.png "
 '--aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s12.png --project DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s12.png 'Tuan and Minh tug at the will until it rips in "
 'half; extreme slow motion of the paper tearing, with a deep bass hit. No captions, no subtitles, no on-screen text '
 "or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --aspect 9:16 -o "
 '<tmp>/out/clips/s12.mp4 --model veo-lite --project DRY-RUN-PROJECT --json',
 '[s12] (dry-run) sẽ lưu -> <tmp>/out/clips/s12.mp4',
 '[s13] keyframe: Mother gasps, clutches her chest and collapses sideways from her chair, prayer b',
 '  ảnh tham chiếu nhân vật: Mother',
 "  $ gflow image i2i 'Mother clutching her chest, prayer beads slipping from her fingers, swaying in her chair, altar "
 'candles behind her, 50mm lens. Mother: Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep '
 'wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds wooden prayer beads. Style: Cinematic '
 'Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow depth of '
 'field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy. No captions, no '
 'subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio '
 "only.' --ref <tmp>/assets/characters/mother.png --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s13.png --project "
 'DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s13.png 'Mother gasps, clutches her chest and collapses "
 'sideways from her chair, prayer beads scattering across the tiles; camera drops with her to floor level, sound fades '
 'to a high-pitched ring. No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no '
 "logos; any dialogue is spoken audio only.' --aspect 9:16 -o <tmp>/out/clips/s13.mp4 --model veo-lite --project "
 'DRY-RUN-PROJECT --json',
 '[s13] (dry-run) sẽ lưu -> <tmp>/out/clips/s13.mp4',
 '[s14] keyframe: Minh and Tuan kneel and cradle Mother, shouting her name in Vietnamese, voice on',
 '  ảnh tham chiếu nhân vật: Minh, Lan, Tuan, Hoa, Mother',
 "  $ gflow image i2i 'Minh and Tuan kneeling on a tiled floor holding their unconscious mother, Lan and Hoa frozen "
 'behind them in shock, scattered beads, dim light, 24mm lens. Minh: Vietnamese man, 45, elder brother, stocky build, '
 'short neat black hair greying at the temples, thick eyebrows, stern square face, wears a black long-sleeve dress '
 'shirt with a white mourning band on his left arm. Lan: Vietnamese woman, 42, Minh\'"\'"\'s wife, slim, sharp '
 'cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy blouse and black trousers, red '
 'lipstick, calculating eyes. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy medium-length black '
 'hair, light stubble, hot-tempered expression, white mourning headband, plain grey polo shirt and jeans. Hoa: '
 'Vietnamese woman, 35, Tuan\'"\'"\'s wife, petite, long straight black hair worn loose, round face, large jade bangle '
 'on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Mother: Vietnamese grandmother, 72, '
 'frail and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds '
 'wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light '
 'with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced '
 'tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no '
 "logos; any dialogue is spoken audio only.' --ref <tmp>/assets/characters/minh.png --ref "
 '<tmp>/assets/characters/lan.png --ref <tmp>/assets/characters/tuan.png --ref <tmp>/assets/characters/hoa.png --ref '
 '<tmp>/assets/characters/mother.png --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s14.png --project '
 'DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s14.png 'Minh and Tuan kneel and cradle Mother, shouting her "
 'name in Vietnamese, voice only, while Lan and Hoa stand frozen; fast circling handheld shot. No captions, no '
 'subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio '
 "only.' --aspect 9:16 -o <tmp>/out/clips/s14.mp4 --model veo-lite --project DRY-RUN-PROJECT --json",
 '[s14] (dry-run) sẽ lưu -> <tmp>/out/clips/s14.mp4',
 '[s15] keyframe: Slow lateral dolly along the row of chairs past Minh, Lan, Tuan and Hoa sitting ',
 '  ảnh tham chiếu nhân vật: Minh, Lan, Tuan, Hoa',
 "  $ gflow image i2i 'Hospital corridor at night, cold fluorescent light, Minh, Lan, Tuan and Hoa sitting apart on "
 'plastic chairs, heads down, long empty hallway, 35mm lens. Minh: Vietnamese man, 45, elder brother, stocky build, '
 'short neat black hair greying at the temples, thick eyebrows, stern square face, wears a black long-sleeve dress '
 'shirt with a white mourning band on his left arm. Lan: Vietnamese woman, 42, Minh\'"\'"\'s wife, slim, sharp '
 'cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy blouse and black trousers, red '
 'lipstick, calculating eyes. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy medium-length black '
 'hair, light stubble, hot-tempered expression, white mourning headband, plain grey polo shirt and jeans. Hoa: '
 'Vietnamese woman, 35, Tuan\'"\'"\'s wife, petite, long straight black hair worn loose, round face, large jade bangle '
 'on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Style: Cinematic Vietnamese family '
 'drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow depth of field, 35mm film '
 'grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy. No captions, no subtitles, no '
 "on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref "
 '<tmp>/assets/characters/minh.png --ref <tmp>/assets/characters/lan.png --ref <tmp>/assets/characters/tuan.png --ref '
 '<tmp>/assets/characters/hoa.png --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s15.png --project DRY-RUN-PROJECT '
 '--json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s15.png 'Slow lateral dolly along the row of chairs past "
 'Minh, Lan, Tuan and Hoa sitting in silence; distant heart monitor beeping. No captions, no subtitles, no on-screen '
 "text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --aspect 9:16 -o "
 '<tmp>/out/clips/s15.mp4 --model veo-lite --project DRY-RUN-PROJECT --json',
 '[s15] (dry-run) sẽ lưu -> <tmp>/out/clips/s15.mp4',
 '[s16] keyframe: Mother lifts a small worn envelope from under her pillow and holds it out toward',
 '  ảnh tham chiếu nhân vật: Mother',
 "  $ gflow image i2i 'Hospital room, Mother lying awake in bed with oxygen tube, weak smile, holding a small old "
 'sealed envelope, soft morning window light, 50mm lens. Mother: Vietnamese grandmother, 72, frail and small, silver '
 'hair in a bun, deep wrinkles, gentle sad eyes, white traditional mourning ao ba ba, holds wooden prayer beads. '
 'Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, '
 'shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy. '
 'No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is '
 "spoken audio only.' --ref <tmp>/assets/characters/mother.png --aspect 9:16 --model nano2 -o "
 '<tmp>/out/keyframes/s16.png --project DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s16.png 'Mother lifts a small worn envelope from under her "
 'pillow and holds it out toward camera; slow push-in on the envelope in her trembling hand. No captions, no '
 'subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio '
 "only.' --aspect 9:16 -o <tmp>/out/clips/s16.mp4 --model veo-lite --project DRY-RUN-PROJECT --json",
 '[s16] (dry-run) sẽ lưu -> <tmp>/out/clips/s16.mp4',
 '[s17] keyframe: Tuan reads the letter and his hands begin to shake as Minh leans in behind him; ',
 '  ảnh tham chiếu nhân vật: Minh, Tuan',
 "  $ gflow image i2i 'Close-up of Tuan reading a handwritten letter in a hospital room, Minh reading over his "
 'shoulder, morning light, eyes glistening, 85mm lens. Minh: Vietnamese man, 45, elder brother, stocky build, short '
 'neat black hair greying at the temples, thick eyebrows, stern square face, wears a black long-sleeve dress shirt '
 'with a white mourning band on his left arm. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy '
 'medium-length black hair, light stubble, hot-tempered expression, white mourning headband, plain grey polo shirt and '
 'jeans. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep '
 'shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense '
 'dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; '
 "any dialogue is spoken audio only.' --ref <tmp>/assets/characters/minh.png --ref <tmp>/assets/characters/tuan.png "
 '--aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s17.png --project DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s17.png 'Tuan reads the letter and his hands begin to shake "
 'as Minh leans in behind him; slow rack focus from the paper to their faces as tears form; soft piano begins. No '
 'captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is '
 "spoken audio only.' --aspect 9:16 -o <tmp>/out/clips/s17.mp4 --model veo-lite --project DRY-RUN-PROJECT --json",
 '[s17] (dry-run) sẽ lưu -> <tmp>/out/clips/s17.mp4',
 '[s18] keyframe: Lan slowly turns to Hoa and gently touches her bruised cheek, whispering an apol',
 '  ảnh tham chiếu nhân vật: Lan, Hoa',
 "  $ gflow image i2i 'Lan and Hoa standing near a hospital window, both ashamed, Hoa with a red mark on her cheek, "
 'Lan looking down, soft daylight, 50mm lens. Lan: Vietnamese woman, 42, Minh\'"\'"\'s wife, slim, sharp cheekbones, '
 'black hair in a tight low bun, thin gold necklace, fitted dark burgundy blouse and black trousers, red lipstick, '
 'calculating eyes. Hoa: Vietnamese woman, 35, Tuan\'"\'"\'s wife, petite, long straight black hair worn loose, round '
 'face, large jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Style: '
 'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow '
 'depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy. No '
 'captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is '
 "spoken audio only.' --ref <tmp>/assets/characters/lan.png --ref <tmp>/assets/characters/hoa.png --aspect 9:16 "
 '--model nano2 -o <tmp>/out/keyframes/s18.png --project DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s18.png 'Lan slowly turns to Hoa and gently touches her "
 'bruised cheek, whispering an apology in Vietnamese, voice only; Hoa\'"\'"\'s eyes fill with tears; slow arc shot '
 'around them. No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any '
 "dialogue is spoken audio only.' --aspect 9:16 -o <tmp>/out/clips/s18.mp4 --model veo-lite --project DRY-RUN-PROJECT "
 '--json',
 '[s18] (dry-run) sẽ lưu -> <tmp>/out/clips/s18.mp4',
 '[s19] keyframe: Minh pulls Tuan into a tight embrace, both brothers crying, as Mother reaches ou',
 '  ảnh tham chiếu nhân vật: Minh, Tuan, Mother',
 "  $ gflow image i2i 'Minh and Tuan standing at the foot of a hospital bed, Mother smiling weakly between them, "
 'morning sun through blinds, 35mm lens. Minh: Vietnamese man, 45, elder brother, stocky build, short neat black hair '
 'greying at the temples, thick eyebrows, stern square face, wears a black long-sleeve dress shirt with a white '
 'mourning band on his left arm. Tuan: Vietnamese man, 38, younger brother, lean and tall, messy medium-length black '
 'hair, light stubble, hot-tempered expression, white mourning headband, plain grey polo shirt and jeans. Mother: '
 'Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional '
 'mourning ao ba ba, holds wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm '
 'tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
 'color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title '
 "cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref <tmp>/assets/characters/minh.png --ref "
 '<tmp>/assets/characters/tuan.png --ref <tmp>/assets/characters/mother.png --aspect 9:16 --model nano2 -o '
 '<tmp>/out/keyframes/s19.png --project DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s19.png 'Minh pulls Tuan into a tight embrace, both brothers "
 'crying, as Mother reaches out to hold their hands; slow dolly-in, warm piano swells. No captions, no subtitles, no '
 "on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only.' --aspect "
 '9:16 -o <tmp>/out/clips/s19.mp4 --model veo-lite --project DRY-RUN-PROJECT --json',
 '[s19] (dry-run) sẽ lưu -> <tmp>/out/clips/s19.mp4',
 "[s20] keyframe: Slow crane up from the family bowing together before the altar to the father's p",
 '  ảnh tham chiếu nhân vật: Minh, Lan, Tuan, Hoa, Mother',
 '  $ gflow image i2i \'Ancestral altar in the family house in daylight, father\'"\'"\'s framed portrait, fresh '
 'incense, Minh, Lan, Tuan, Hoa and Mother standing together bowing, warm golden light, 24mm lens. Minh: Vietnamese '
 'man, 45, elder brother, stocky build, short neat black hair greying at the temples, thick eyebrows, stern square '
 'face, wears a black long-sleeve dress shirt with a white mourning band on his left arm. Lan: Vietnamese woman, 42, '
 'Minh\'"\'"\'s wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy '
 'blouse and black trousers, red lipstick, calculating eyes. Tuan: Vietnamese man, 38, younger brother, lean and tall, '
 'messy medium-length black hair, light stubble, hot-tempered expression, white mourning headband, plain grey polo '
 'shirt and jeans. Hoa: Vietnamese woman, 35, Tuan\'"\'"\'s wife, petite, long straight black hair worn loose, round '
 'face, large jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Mother: '
 'Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional '
 'mourning ao ba ba, holds wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm '
 'tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
 'color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title '
 "cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref <tmp>/assets/characters/minh.png --ref "
 '<tmp>/assets/characters/lan.png --ref <tmp>/assets/characters/tuan.png --ref <tmp>/assets/characters/hoa.png --ref '
 '<tmp>/assets/characters/mother.png --aspect 9:16 --model nano2 -o <tmp>/out/keyframes/s20.png --project '
 'DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/s20.png 'Slow crane up from the family bowing together "
 'before the altar to the father\'"\'"\'s portrait as incense smoke rises in golden light; gentle temple bell. No '
 'captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is '
 "spoken audio only.' --aspect 9:16 -o <tmp>/out/clips/s20.mp4 --model veo-lite --project DRY-RUN-PROJECT --json",
 '[s20] (dry-run) sẽ lưu -> <tmp>/out/clips/s20.mp4',
 '[x1] t2v: Rain on the window of the old house at night, Tuan and Hoa leave through the gat',
 '  ảnh tham chiếu nhân vật: Tuan, Hoa',
 "  $ gflow video r2v 'Rain on the window of the old house at night, Tuan and Hoa leave through the gate. Shot: wide "
 'shot. Camera movement: handheld camera, subtle shake. Tuan: Vietnamese man, 38, younger brother, lean and tall, '
 'messy medium-length black hair, light stubble, hot-tempered expression, white mourning headband, plain grey polo '
 'shirt and jeans. Hoa: Vietnamese woman, 35, Tuan\'"\'"\'s wife, petite, long straight black hair worn loose, round '
 'face, large jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Style: '
 'Cinematic Vietnamese family drama, vertical 9:16 framing, warm tungsten interior light with deep shadows, shallow '
 'depth of field, 35mm film grain, high-contrast amber and teal color grade, fast-paced tense dramatic energy. No '
 'captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is '
 "spoken audio only.' --ref <tmp>/assets/characters/tuan.png --ref <tmp>/assets/characters/hoa.png --aspect 9:16 -o "
 '<tmp>/out/clips/x1.mp4 --model veo-lite --duration 8 --project DRY-RUN-PROJECT --json',
 '[x1] (dry-run) sẽ lưu -> <tmp>/out/clips/x1.mp4',
 '[x2] i2v: Mother turns her head toward the altar and smiles',
 "  $ gflow video i2v --initial-frame /golden/fixed/start.png 'Mother turns her head toward the altar and smiles. "
 'Camera movement: slow dolly-in. No captions, no subtitles, no on-screen text or lettering, no title cards, no '
 "watermarks, no logos; any dialogue is spoken audio only.' --aspect 9:16 -o <tmp>/out/clips/x2.mp4 --model veo-lite "
 '--project DRY-RUN-PROJECT --json',
 '[x2] (dry-run) sẽ lưu -> <tmp>/out/clips/x2.mp4',
 '[x3] keyframe: Nobody speaks; the clock ticks',
 '  ảnh tham chiếu nhân vật: Lan, Tuan, Hoa, Mother',
 "  $ gflow image i2i 'The whole family sits around the wooden table, lamp light. Lan: Vietnamese woman, 42, "
 'Minh\'"\'"\'s wife, slim, sharp cheekbones, black hair in a tight low bun, thin gold necklace, fitted dark burgundy '
 'blouse and black trousers, red lipstick, calculating eyes. Tuan: Vietnamese man, 38, younger brother, lean and tall, '
 'messy medium-length black hair, light stubble, hot-tempered expression, white mourning headband, plain grey polo '
 'shirt and jeans. Hoa: Vietnamese woman, 35, Tuan\'"\'"\'s wife, petite, long straight black hair worn loose, round '
 'face, large jade bangle on right wrist, cream silk blouse and black pencil skirt, fiery expressive eyes. Mother: '
 'Vietnamese grandmother, 72, frail and small, silver hair in a bun, deep wrinkles, gentle sad eyes, white traditional '
 'mourning ao ba ba, holds wooden prayer beads. Style: Cinematic Vietnamese family drama, vertical 9:16 framing, warm '
 'tungsten interior light with deep shadows, shallow depth of field, 35mm film grain, high-contrast amber and teal '
 'color grade, fast-paced tense dramatic energy. No captions, no subtitles, no on-screen text or lettering, no title '
 "cards, no watermarks, no logos; any dialogue is spoken audio only.' --ref <tmp>/assets/characters/lan.png --ref "
 '<tmp>/assets/characters/tuan.png --ref <tmp>/assets/characters/hoa.png --ref <tmp>/assets/characters/mother.png '
 '--aspect 9:16 --model nano2 -o <tmp>/out/keyframes/x3.png --project DRY-RUN-PROJECT --json',
 "  $ gflow video i2v --initial-frame <tmp>/out/keyframes/x3.png 'Nobody speaks; the clock ticks. No captions, no "
 'subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio '
 "only.' --aspect 9:16 -o <tmp>/out/clips/x3.mp4 --model veo-lite --project DRY-RUN-PROJECT --json",
 '[x3] (dry-run) sẽ lưu -> <tmp>/out/clips/x3.mp4']
