"""Golden snapshot for dialogue-language AC16: a storyboard WITHOUT `dialogue_language` must send Flow exactly
the same prompts (video and image) and keep exactly the same `scene_fingerprint` as before the feature.

GOLDEN was recorded on commit 0718918 (before the feature), from `git archive 0718918 filmmaker`, with
`python -I capture.py <archive>` (capture.py asserted `filmmaker.__file__` was inside the archive).
Recorded on 0718918, do not regenerate: never recompute these values from the current code. If a test here
fails, the change altered what old storyboards send to Flow or marked finished scenes as changed.

Status while the feature is being built:
- green from the start: image / thumbnail / portrait prompts via `for_flow`, `for_flow(video_prompt)`,
  fingerprints, `save_storyboard` bytes, API `detail().scene_state[*].video_prompt`;
- red until T1 adds `Storyboard.for_flow_video`: `test_video_prompts_via_for_flow_video_match_golden`.
"""
import json
import sys
from pathlib import Path

import pytest

from filmmaker.storyboard import load_storyboard, save_storyboard

FAKE = Path(__file__).parent / "fake_gflow.py"

# Fixed reference-image bytes: fingerprints hash them.
LAN_PNG = b"golden-dlang-lan-v1"
HOA_PNG = b"golden-dlang-hoa-v1"

# t2v, i2v (fixed absolute paths, with and without end_image), keyframe with and without image_prompt, camera,
# explicit scene `characters`, prompts ending in "!", a prompt that already contains NO_TEXT, quoted dialogue,
# per-scene model, thumbnail prompt, a character with its own image_prompt. No dialogue_language.
SAMPLE = r'''title: Golden dialogue sample
aspect: "16:9"
model: veo-lite
image_model: nano2
style: "cinematic, warm film grain."
thumbnail:
  prompt: "Lan and Hoa hug under a red umbrella"
  text: "Mưa tháng Sáu"
characters:
  - name: Lan
    description: "young woman, short black hair, red scarf"
    image: lan.png
  - name: Minh
    description: "tall young man, round glasses, grey hoodie."
    image_prompt: "Minh standing by a window, full body"
  - name: Hoa
    description: "elderly woman, white ao dai"
    image: hoa.png
scenes:
  - id: k1
    mode: keyframe
    image_prompt: "Lan and Minh at a cafe table"
    prompt: "Lan says \"I missed you\" while Minh pours tea"
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
    prompt: "minh whispers to himself on the beach at sunset!"
  - id: k3
    mode: keyframe
    image_prompt: "Hoa sits on a bench"
    prompt: "Hoa sings softly"
    characters: [Hoa, Lan]
    duration: 6
  - id: t1
    mode: t2v
    prompt: "Lan shouts across the street to Hoa"
    camera:
      shot: wide shot
      movement: handheld camera
  - id: t2
    mode: t2v
    prompt: "Empty street at night, neon reflections. No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; any dialogue is spoken audio only."
    duration: 8
    model: veo-fast
  - id: i1
    mode: i2v
    image: /golden/fixed/start.png
    end_image: /golden/fixed/end.png
    prompt: "Lan turns around and smiles"
    camera:
      movement: orbit
      composition: rule of thirds
  - id: i2
    mode: i2v
    image: /golden/fixed/still.png
    prompt: "Minh nods"
'''

VARIANTS = {"no_text_true": "", "no_text_false": "no_text: false\n"}

# Recorded on 0718918, do not regenerate.
GOLDEN = {'no_text_true': {'scenes': {'k1': {'video': 'Lan says "I missed you" while Minh pours tea. Camera movement: slow '
                                             'dolly-in. Speed: real-time speed. Focus: shallow depth of field. '
                                             'Direction: keep both faces in frame. No captions, no subtitles, no '
                                             'on-screen text or lettering, no title cards, no watermarks, no logos; '
                                             'any dialogue is spoken audio only.',
                                    'text_only': 'Lan and Minh at a cafe table. Lan says "I missed you" while Minh '
                                                 'pours tea. Shot: medium shot, eye-level angle. Camera movement: '
                                                 'slow dolly-in. Speed: real-time speed. Lens: 50mm lens. Focus: '
                                                 'shallow depth of field. Lighting: warm window light. Direction: '
                                                 'keep both faces in frame. Lan: young woman, short black hair, red '
                                                 'scarf. Minh: tall young man, round glasses, grey hoodie.. Style: '
                                                 'cinematic, warm film grain. No captions, no subtitles, no '
                                                 'on-screen text or lettering, no title cards, no watermarks, no '
                                                 'logos; any dialogue is spoken audio only.',
                                    'image': 'Lan and Minh at a cafe table. Shot: medium shot, eye-level angle. '
                                             'Lens: 50mm lens. Focus: shallow depth of field. Lighting: warm window '
                                             'light. Direction: keep both faces in frame. Lan: young woman, short '
                                             'black hair, red scarf. Minh: tall young man, round glasses, grey '
                                             'hoodie.. Style: cinematic, warm film grain. No captions, no subtitles, '
                                             'no on-screen text or lettering, no title cards, no watermarks, no '
                                             'logos; any dialogue is spoken audio only.',
                                    'fingerprint': 'd6fcf519166dd28a'},
                             'k2': {'video': 'minh whispers to himself on the beach at sunset! No captions, no '
                                             'subtitles, no on-screen text or lettering, no title cards, no '
                                             'watermarks, no logos; any dialogue is spoken audio only.',
                                    'text_only': 'minh whispers to himself on the beach at sunset!. Minh: tall young '
                                                 'man, round glasses, grey hoodie.. Style: cinematic, warm film '
                                                 'grain. No captions, no subtitles, no on-screen text or lettering, '
                                                 'no title cards, no watermarks, no logos; any dialogue is spoken '
                                                 'audio only.',
                                    'image': 'minh whispers to himself on the beach at sunset!. Minh: tall young '
                                             'man, round glasses, grey hoodie.. Style: cinematic, warm film grain. '
                                             'No captions, no subtitles, no on-screen text or lettering, no title '
                                             'cards, no watermarks, no logos; any dialogue is spoken audio only.',
                                    'fingerprint': '8564b253207aca4a'},
                             'k3': {'video': 'Hoa sings softly. No captions, no subtitles, no on-screen text or '
                                             'lettering, no title cards, no watermarks, no logos; any dialogue is '
                                             'spoken audio only.',
                                    'text_only': 'Hoa sits on a bench. Hoa sings softly. Lan: young woman, short '
                                                 'black hair, red scarf. Hoa: elderly woman, white ao dai. Style: '
                                                 'cinematic, warm film grain. No captions, no subtitles, no '
                                                 'on-screen text or lettering, no title cards, no watermarks, no '
                                                 'logos; any dialogue is spoken audio only.',
                                    'image': 'Hoa sits on a bench. Lan: young woman, short black hair, red scarf. '
                                             'Hoa: elderly woman, white ao dai. Style: cinematic, warm film grain. '
                                             'No captions, no subtitles, no on-screen text or lettering, no title '
                                             'cards, no watermarks, no logos; any dialogue is spoken audio only.',
                                    'fingerprint': '9f90d31ba4502eb0'},
                             't1': {'video': 'Lan shouts across the street to Hoa. Shot: wide shot. Camera movement: '
                                             'handheld camera. Lan: young woman, short black hair, red scarf. Hoa: '
                                             'elderly woman, white ao dai. Style: cinematic, warm film grain. No '
                                             'captions, no subtitles, no on-screen text or lettering, no title '
                                             'cards, no watermarks, no logos; any dialogue is spoken audio only.',
                                    'text_only': 'Lan shouts across the street to Hoa. Shot: wide shot. Camera '
                                                 'movement: handheld camera. Lan: young woman, short black hair, red '
                                                 'scarf. Hoa: elderly woman, white ao dai. Style: cinematic, warm '
                                                 'film grain. No captions, no subtitles, no on-screen text or '
                                                 'lettering, no title cards, no watermarks, no logos; any dialogue '
                                                 'is spoken audio only.',
                                    'image': None,
                                    'fingerprint': '7232d88f9f9a17e0'},
                             't2': {'video': 'Empty street at night, neon reflections. No captions, no subtitles, no '
                                             'on-screen text or lettering, no title cards, no watermarks, no logos; '
                                             'any dialogue is spoken audio only. Style: cinematic, warm film grain.',
                                    'text_only': 'Empty street at night, neon reflections. No captions, no '
                                                 'subtitles, no on-screen text or lettering, no title cards, no '
                                                 'watermarks, no logos; any dialogue is spoken audio only. Style: '
                                                 'cinematic, warm film grain.',
                                    'image': None,
                                    'fingerprint': 'a7ad8a8545204dd2'},
                             'i1': {'video': 'Lan turns around and smiles. Camera movement: orbit. No captions, no '
                                             'subtitles, no on-screen text or lettering, no title cards, no '
                                             'watermarks, no logos; any dialogue is spoken audio only.',
                                    'text_only': 'Lan turns around and smiles. Camera movement: orbit. Composition: '
                                                 'rule of thirds. Lan: young woman, short black hair, red scarf. '
                                                 'Style: cinematic, warm film grain. No captions, no subtitles, no '
                                                 'on-screen text or lettering, no title cards, no watermarks, no '
                                                 'logos; any dialogue is spoken audio only.',
                                    'image': None,
                                    'fingerprint': '68ef0fb9abce8bbe'},
                             'i2': {'video': 'Minh nods. No captions, no subtitles, no on-screen text or lettering, '
                                             'no title cards, no watermarks, no logos; any dialogue is spoken audio '
                                             'only.',
                                    'text_only': 'Minh nods. Minh: tall young man, round glasses, grey hoodie.. '
                                                 'Style: cinematic, warm film grain. No captions, no subtitles, no '
                                                 'on-screen text or lettering, no title cards, no watermarks, no '
                                                 'logos; any dialogue is spoken audio only.',
                                    'image': None,
                                    'fingerprint': '4158efa3f164824c'}},
                  'thumbnail': 'Lan and Hoa hug under a red umbrella. Lan: young woman, short black hair, red scarf. '
                               'Hoa: elderly woman, white ao dai. Style: cinematic, warm film grain. No captions, no '
                               'subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; '
                               'any dialogue is spoken audio only.',
                  'portraits': {'Lan': 'Character reference image of Lan: young woman, short black hair, red scarf. '
                                       'Full body, standing, facing the camera, face clearly visible, neutral '
                                       'expression, plain light grey studio background, soft even lighting, sharp '
                                       'detail. Style: cinematic, warm film grain. No captions, no subtitles, no '
                                       'on-screen text or lettering, no title cards, no watermarks, no logos; any '
                                       'dialogue is spoken audio only.',
                                'Minh': 'Minh standing by a window, full body. Style: cinematic, warm film grain. No '
                                        'captions, no subtitles, no on-screen text or lettering, no title cards, no '
                                        'watermarks, no logos; any dialogue is spoken audio only.',
                                'Hoa': 'Character reference image of Hoa: elderly woman, white ao dai. Full body, '
                                       'standing, facing the camera, face clearly visible, neutral expression, plain '
                                       'light grey studio background, soft even lighting, sharp detail. Style: '
                                       'cinematic, warm film grain. No captions, no subtitles, no on-screen text or '
                                       'lettering, no title cards, no watermarks, no logos; any dialogue is spoken '
                                       'audio only.'},
                  'saved_yaml': 'title: Golden dialogue sample\n'
                                "aspect: '16:9'\n"
                                'model: veo-lite\n'
                                'image_model: nano2\n'
                                'style: cinematic, warm film grain.\n'
                                'characters:\n'
                                '- name: Lan\n'
                                '  description: young woman, short black hair, red scarf\n'
                                '  image: <tmp>/lan.png\n'
                                '- name: Minh\n'
                                '  description: tall young man, round glasses, grey hoodie.\n'
                                '  image_prompt: Minh standing by a window, full body\n'
                                '- name: Hoa\n'
                                '  description: elderly woman, white ao dai\n'
                                '  image: <tmp>/hoa.png\n'
                                'thumbnail:\n'
                                '  prompt: Lan and Hoa hug under a red umbrella\n'
                                '  text: Mưa tháng Sáu\n'
                                'use_character_refs: true\n'
                                'no_text: true\n'
                                'fallback_t2v: false\n'
                                'scenes:\n'
                                '- id: k1\n'
                                '  mode: keyframe\n'
                                '  prompt: Lan says "I missed you" while Minh pours tea\n'
                                '  image_prompt: Lan and Minh at a cafe table\n'
                                '  narration: Chiều hôm ấy\n'
                                '  camera:\n'
                                '    shot: medium shot\n'
                                '    angle: eye-level angle\n'
                                '    movement: slow dolly-in\n'
                                '    lens: 50mm lens\n'
                                '    focus: shallow depth of field\n'
                                '    lighting: warm window light\n'
                                '    speed: real-time speed\n'
                                '    notes: keep both faces in frame\n'
                                '- id: k2\n'
                                '  mode: keyframe\n'
                                '  prompt: minh whispers to himself on the beach at sunset!\n'
                                '- id: k3\n'
                                '  mode: keyframe\n'
                                '  prompt: Hoa sings softly\n'
                                '  image_prompt: Hoa sits on a bench\n'
                                '  duration: 6\n'
                                '  characters:\n'
                                '  - Hoa\n'
                                '  - Lan\n'
                                '- id: t1\n'
                                '  mode: t2v\n'
                                '  prompt: Lan shouts across the street to Hoa\n'
                                '  camera:\n'
                                '    shot: wide shot\n'
                                '    movement: handheld camera\n'
                                '- id: t2\n'
                                '  mode: t2v\n'
                                '  prompt: Empty street at night, neon reflections. No captions, no subtitles, no '
                                'on-screen text or lettering,\n'
                                '    no title cards, no watermarks, no logos; any dialogue is spoken audio only.\n'
                                '  duration: 8\n'
                                '  model: veo-fast\n'
                                '- id: i1\n'
                                '  mode: i2v\n'
                                '  prompt: Lan turns around and smiles\n'
                                '  image: /golden/fixed/start.png\n'
                                '  end_image: /golden/fixed/end.png\n'
                                '  camera:\n'
                                '    movement: orbit\n'
                                '    composition: rule of thirds\n'
                                '- id: i2\n'
                                '  mode: i2v\n'
                                '  prompt: Minh nods\n'
                                '  image: /golden/fixed/still.png\n'},
 'no_text_false': {'scenes': {'k1': {'video': 'Lan says "I missed you" while Minh pours tea. Camera movement: slow '
                                              'dolly-in. Speed: real-time speed. Focus: shallow depth of field. '
                                              'Direction: keep both faces in frame.',
                                     'text_only': 'Lan and Minh at a cafe table. Lan says "I missed you" while Minh '
                                                  'pours tea. Shot: medium shot, eye-level angle. Camera movement: '
                                                  'slow dolly-in. Speed: real-time speed. Lens: 50mm lens. Focus: '
                                                  'shallow depth of field. Lighting: warm window light. Direction: '
                                                  'keep both faces in frame. Lan: young woman, short black hair, red '
                                                  'scarf. Minh: tall young man, round glasses, grey hoodie.. Style: '
                                                  'cinematic, warm film grain.',
                                     'image': 'Lan and Minh at a cafe table. Shot: medium shot, eye-level angle. '
                                              'Lens: 50mm lens. Focus: shallow depth of field. Lighting: warm window '
                                              'light. Direction: keep both faces in frame. Lan: young woman, short '
                                              'black hair, red scarf. Minh: tall young man, round glasses, grey '
                                              'hoodie.. Style: cinematic, warm film grain.',
                                     'fingerprint': 'd6fcf519166dd28a'},
                              'k2': {'video': 'minh whispers to himself on the beach at sunset!',
                                     'text_only': 'minh whispers to himself on the beach at sunset!. Minh: tall '
                                                  'young man, round glasses, grey hoodie.. Style: cinematic, warm '
                                                  'film grain.',
                                     'image': 'minh whispers to himself on the beach at sunset!. Minh: tall young '
                                              'man, round glasses, grey hoodie.. Style: cinematic, warm film grain.',
                                     'fingerprint': '8564b253207aca4a'},
                              'k3': {'video': 'Hoa sings softly',
                                     'text_only': 'Hoa sits on a bench. Hoa sings softly. Lan: young woman, short '
                                                  'black hair, red scarf. Hoa: elderly woman, white ao dai. Style: '
                                                  'cinematic, warm film grain.',
                                     'image': 'Hoa sits on a bench. Lan: young woman, short black hair, red scarf. '
                                              'Hoa: elderly woman, white ao dai. Style: cinematic, warm film grain.',
                                     'fingerprint': '9f90d31ba4502eb0'},
                              't1': {'video': 'Lan shouts across the street to Hoa. Shot: wide shot. Camera '
                                              'movement: handheld camera. Lan: young woman, short black hair, red '
                                              'scarf. Hoa: elderly woman, white ao dai. Style: cinematic, warm film '
                                              'grain.',
                                     'text_only': 'Lan shouts across the street to Hoa. Shot: wide shot. Camera '
                                                  'movement: handheld camera. Lan: young woman, short black hair, '
                                                  'red scarf. Hoa: elderly woman, white ao dai. Style: cinematic, '
                                                  'warm film grain.',
                                     'image': None,
                                     'fingerprint': '7232d88f9f9a17e0'},
                              't2': {'video': 'Empty street at night, neon reflections. No captions, no subtitles, '
                                              'no on-screen text or lettering, no title cards, no watermarks, no '
                                              'logos; any dialogue is spoken audio only. Style: cinematic, warm film '
                                              'grain.',
                                     'text_only': 'Empty street at night, neon reflections. No captions, no '
                                                  'subtitles, no on-screen text or lettering, no title cards, no '
                                                  'watermarks, no logos; any dialogue is spoken audio only. Style: '
                                                  'cinematic, warm film grain.',
                                     'image': None,
                                     'fingerprint': 'a7ad8a8545204dd2'},
                              'i1': {'video': 'Lan turns around and smiles. Camera movement: orbit.',
                                     'text_only': 'Lan turns around and smiles. Camera movement: orbit. Composition: '
                                                  'rule of thirds. Lan: young woman, short black hair, red scarf. '
                                                  'Style: cinematic, warm film grain.',
                                     'image': None,
                                     'fingerprint': '68ef0fb9abce8bbe'},
                              'i2': {'video': 'Minh nods',
                                     'text_only': 'Minh nods. Minh: tall young man, round glasses, grey hoodie.. '
                                                  'Style: cinematic, warm film grain.',
                                     'image': None,
                                     'fingerprint': '4158efa3f164824c'}},
                   'thumbnail': 'Lan and Hoa hug under a red umbrella. Lan: young woman, short black hair, red '
                                'scarf. Hoa: elderly woman, white ao dai. Style: cinematic, warm film grain.',
                   'portraits': {'Lan': 'Character reference image of Lan: young woman, short black hair, red scarf. '
                                        'Full body, standing, facing the camera, face clearly visible, neutral '
                                        'expression, plain light grey studio background, soft even lighting, sharp '
                                        'detail. Style: cinematic, warm film grain.',
                                 'Minh': 'Minh standing by a window, full body. Style: cinematic, warm film grain.',
                                 'Hoa': 'Character reference image of Hoa: elderly woman, white ao dai. Full body, '
                                        'standing, facing the camera, face clearly visible, neutral expression, '
                                        'plain light grey studio background, soft even lighting, sharp detail. '
                                        'Style: cinematic, warm film grain.'},
                   'saved_yaml': 'title: Golden dialogue sample\n'
                                 "aspect: '16:9'\n"
                                 'model: veo-lite\n'
                                 'image_model: nano2\n'
                                 'style: cinematic, warm film grain.\n'
                                 'characters:\n'
                                 '- name: Lan\n'
                                 '  description: young woman, short black hair, red scarf\n'
                                 '  image: <tmp>/lan.png\n'
                                 '- name: Minh\n'
                                 '  description: tall young man, round glasses, grey hoodie.\n'
                                 '  image_prompt: Minh standing by a window, full body\n'
                                 '- name: Hoa\n'
                                 '  description: elderly woman, white ao dai\n'
                                 '  image: <tmp>/hoa.png\n'
                                 'thumbnail:\n'
                                 '  prompt: Lan and Hoa hug under a red umbrella\n'
                                 '  text: Mưa tháng Sáu\n'
                                 'use_character_refs: true\n'
                                 'no_text: false\n'
                                 'fallback_t2v: false\n'
                                 'scenes:\n'
                                 '- id: k1\n'
                                 '  mode: keyframe\n'
                                 '  prompt: Lan says "I missed you" while Minh pours tea\n'
                                 '  image_prompt: Lan and Minh at a cafe table\n'
                                 '  narration: Chiều hôm ấy\n'
                                 '  camera:\n'
                                 '    shot: medium shot\n'
                                 '    angle: eye-level angle\n'
                                 '    movement: slow dolly-in\n'
                                 '    lens: 50mm lens\n'
                                 '    focus: shallow depth of field\n'
                                 '    lighting: warm window light\n'
                                 '    speed: real-time speed\n'
                                 '    notes: keep both faces in frame\n'
                                 '- id: k2\n'
                                 '  mode: keyframe\n'
                                 '  prompt: minh whispers to himself on the beach at sunset!\n'
                                 '- id: k3\n'
                                 '  mode: keyframe\n'
                                 '  prompt: Hoa sings softly\n'
                                 '  image_prompt: Hoa sits on a bench\n'
                                 '  duration: 6\n'
                                 '  characters:\n'
                                 '  - Hoa\n'
                                 '  - Lan\n'
                                 '- id: t1\n'
                                 '  mode: t2v\n'
                                 '  prompt: Lan shouts across the street to Hoa\n'
                                 '  camera:\n'
                                 '    shot: wide shot\n'
                                 '    movement: handheld camera\n'
                                 '- id: t2\n'
                                 '  mode: t2v\n'
                                 '  prompt: Empty street at night, neon reflections. No captions, no subtitles, no '
                                 'on-screen text or lettering,\n'
                                 '    no title cards, no watermarks, no logos; any dialogue is spoken audio only.\n'
                                 '  duration: 8\n'
                                 '  model: veo-fast\n'
                                 '- id: i1\n'
                                 '  mode: i2v\n'
                                 '  prompt: Lan turns around and smiles\n'
                                 '  image: /golden/fixed/start.png\n'
                                 '  end_image: /golden/fixed/end.png\n'
                                 '  camera:\n'
                                 '    movement: orbit\n'
                                 '    composition: rule of thirds\n'
                                 '- id: i2\n'
                                 '  mode: i2v\n'
                                 '  prompt: Minh nods\n'
                                 '  image: /golden/fixed/still.png\n'}}


def _norm(value, tmp_path: Path):
    s = json.dumps(value, ensure_ascii=False)
    for p in sorted({str(tmp_path.resolve()), str(tmp_path)}, key=len, reverse=True):
        s = s.replace(p, "<tmp>")
    return json.loads(s)


def _load(variant: str, tmp_path: Path):
    (tmp_path / "lan.png").write_bytes(LAN_PNG)
    (tmp_path / "hoa.png").write_bytes(HOA_PNG)
    p = tmp_path / "story.yaml"
    p.write_text(SAMPLE + VARIANTS[variant], encoding="utf-8")
    return load_storyboard(p)


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_sample_has_no_language_fields(variant, tmp_path):
    sb = _load(variant, tmp_path)
    assert getattr(sb, "dialogue_language", None) is None
    assert getattr(sb, "subtitle_language", None) is None
    assert [s.id for s in sb.scenes] == list(GOLDEN[variant]["scenes"])


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_video_prompts_via_for_flow_video_match_golden(variant, tmp_path):
    """Red until T1: the pipeline sends videos through for_flow_video; without dialogue_language it must equal the old prompt."""
    sb = _load(variant, tmp_path)
    got = {s.id: {"video": sb.for_flow_video(sb.video_prompt(s)), "text_only": sb.for_flow_video(sb.text_only_prompt(s))}
           for s in sb.scenes}
    want = {sid: {"video": v["video"], "text_only": v["text_only"]} for sid, v in GOLDEN[variant]["scenes"].items()}
    assert _norm(got, tmp_path) == want


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_video_prompts_via_for_flow_match_golden(variant, tmp_path):
    sb = _load(variant, tmp_path)
    got = {s.id: {"video": sb.for_flow(sb.video_prompt(s)), "text_only": sb.for_flow(sb.text_only_prompt(s))}
           for s in sb.scenes}
    want = {sid: {"video": v["video"], "text_only": v["text_only"]} for sid, v in GOLDEN[variant]["scenes"].items()}
    assert _norm(got, tmp_path) == want


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_image_prompts_match_golden(variant, tmp_path):
    sb = _load(variant, tmp_path)
    got = {s.id: sb.for_flow(sb.image_prompt(s)) if s.mode == "keyframe" else None for s in sb.scenes}
    assert _norm(got, tmp_path) == {sid: v["image"] for sid, v in GOLDEN[variant]["scenes"].items()}
    assert sb.for_flow(sb.thumbnail_prompt()) == GOLDEN[variant]["thumbnail"]
    assert {c.name: sb.for_flow(sb.portrait_prompt(c)) for c in sb.characters} == GOLDEN[variant]["portraits"]


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_fingerprints_match_golden(variant, tmp_path):
    sb = _load(variant, tmp_path)
    assert {s.id: sb.scene_fingerprint(s) for s in sb.scenes} == \
        {sid: v["fingerprint"] for sid, v in GOLDEN[variant]["scenes"].items()}


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_save_storyboard_bytes_match_golden(variant, tmp_path):
    sb = _load(variant, tmp_path)
    save_storyboard(sb, tmp_path / "saved.yaml")
    assert _norm((tmp_path / "saved.yaml").read_text(encoding="utf-8"), tmp_path) == GOLDEN[variant]["saved_yaml"]


# ---- API: what the web UI shows as "prompt sent to Flow" -------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from filmmaker.web import server
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "calls.log"))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"pause_min": 0, "pause_max": 0, "retries": 0}))
    monkeypatch.setattr(server, "SETTINGS_PATH", settings)
    app = server.create_app(tmp_path / "ws", token="t0k")
    c = TestClient(app)
    c.headers["X-Token"] = "t0k"
    return c


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_api_detail_video_prompt_matches_golden(variant, client):
    text = SAMPLE + VARIANTS[variant]
    pid = client.post("/api/projects/import", files={"file": ("x.yaml", text.encode("utf-8"))}).json()["id"]
    d = client.get(f"/api/projects/{pid}").json()
    assert d["errors"] == []
    assert {sid: st["video_prompt"] for sid, st in d["scene_state"].items()} == \
        {sid: v["video"] for sid, v in GOLDEN[variant]["scenes"].items()}
    assert {sid: st["image_prompt"] for sid, st in d["scene_state"].items()} == \
        {sid: v["image"] for sid, v in GOLDEN[variant]["scenes"].items()}
    assert d["thumbnail"]["prompt"] == GOLDEN[variant]["thumbnail"]
    assert "dialogue_language" not in d["storyboard"] and "subtitle_language" not in d["storyboard"]
