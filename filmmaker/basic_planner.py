"""Planner for the basic formats (Phim điện ảnh, Phim bộ, Phim ngắn, Clip ngắn): text -> image -> video.

Unlike the drama planner (planner.SYSTEM) there is no content direction, camera direction, dialogue rows or
locations: every scene is one still picture (`image_prompt`) and one motion from it (`prompt`), plus an optional
`narration` line. Claude is called through the same backends as the drama planner.
"""

from __future__ import annotations

import json
import os
import re

from pydantic import BaseModel, ConfigDict, Field

from . import durations as durations_mod
from . import formats, i2v_prompt, model_meta, planner
from .i2v_prompt import Listener, Speaker, VideoShot
from .storyboard import NO_DIALOGUE, NO_SUBTITLES, Storyboard, Thumbnail, clean_language

SYSTEM = """You write the shot list of a film made with AI in two steps per scene: first a still picture is
generated from `image_prompt` (text -> image, with the characters' reference pictures), then that picture is
animated into ONE short clip (image -> video) from the video fields. The tool joins the video fields with fixed
templates, so write each field as a plain phrase. Rules:
- English everywhere except spoken words / `narration` (in the language the message gives).
- `locations` = every place of the film, described ONCE with a fixed, specific look: layout, main fixtures and
  furniture, colours and materials, windows and light sources (English, no people). Set every scene's `location`
  to the exact name of its place. The look is added to each picture automatically and the previous picture at
  the same place is reused, so the room stays the same from scene to scene.
- `image_prompt` = the opening frame as a still picture: who or what is in it, where in the place, time of day,
  where each person stands (left / right / foreground, on what). Do not repeat the whole room description. Name every character in the frame by exact name (their look is added automatically). No
  aspect ratio, duration or resolution words (no "vertical 9:16 frame", no "8 seconds", no "4K").
- Never ask the image model for look-alikes: no "identical twins", "identical", "same face", "exact copy". Twins or
  siblings are "sisters who look alike" told apart by hair (two pigtails vs one ponytail) and clothing colour;
  image models merge identical people into one deformed body.
- VIDEO FIELDS DESCRIBE ONLY MOTION. The picture already holds the faces, clothes and place: never describe them
  again, never repeat the image prompt.
- Never use a character's name in the video fields: the video model only sees the picture. Identify people by
  [position + visual tag]: "On the left, the little girl in the yellow dress". With exactly one person in the frame
  "The subject", "he" or "she" is fine; with two or more people never use names or a bare he / she.
- `camera` = shot size, depth of field, ONE continuous camera move and how it ends, e.g. "Static locked-off medium
  shot, shallow depth of field" or "Slow dolly-in from medium shot to close-up".
- `action` = the [position + visual tag] of who moves + one or two physical action beats in the present tense,
  observable behaviour only ("widens her eyes", "presses lips together", "steps back"), never abstract feelings
  ("feels jealous"). Mouths stay closed unless that person is the speaker. Never 3+ actions in a row.
- ONE ACTIVE PERSON per clip: only one person does the main action; everyone else reacts lightly in the same
  sentence ("while the girl on the right holds the plate still and watches"). Never two people moving the same
  object, and avoid fiddly hands on small objects (grabbing, sliding, passing, tugging a plate or cup): prefer face,
  head and body movement. Hands and small objects deform and duplicate in video models.
- `environment` = usually EMPTY: the starting picture already holds the place and the light, and extra words take
  attention away from the main movement. Fill it only when something in the surroundings must move for the
  story (rain starts, a door swings open), in a few words, never the light or "matching the starting frame".
- `audio` = the real sounds that are heard: ambient room tone and sound effects tied to the movement, e.g. "quiet
  kitchen room tone, soft chair creak". Say silence with real sounds; never write what is NOT heard ("no music",
  "no dialogue", "silence without voices"): negatives make the video model add murmurs or music.
- `speaker` ONLY when the sound guide allows spoken lines and someone on screen speaks in this scene: `position`,
  `visual_tag`, `voice_style` (tone, pace), `line` = ONE short sentence of at most 12 words with no quotation marks
  and no apostrophes (write "do not", not "don't"), `action` = one small delivery gesture ("leans forward
  slightly"). Only one speaker per scene; a back-and-forth goes over several scenes.
- EYELINE LOCK for every scene with a `speaker` (video models otherwise turn the speaker to stare into the lens
  when the mouth opens): every position and gaze is in 2D FRAME coordinates. `position` of the speaker and of each
  listener = "frame left", "frame right" or "frame center"; `head_angle` = "three-quarter profile" or "side
  profile" (never facing the viewpoint straight on); `gaze` = "frame right" / "frame left" / "off-screen left" /
  "off-screen right" or the visual tag of the listener in the frame; `gaze_level` = "at eye level", "slightly
  upward" or "slightly downward". A speaker alone in the frame looks toward the opposite edge, at the partner
  off-screen (on frame left -> gaze "frame right"; on frame right -> gaze "frame left"). The tool locks the viewpoint
  (locked-off 50mm two-shot / 85mm close-up), adds the gaze anchor right before the speech verb and closes the
  mouth after the line.
- 180-degree axis across scenes: in a shot / reverse-shot pair, if the first speaker is on frame left looking
  toward frame right, the one who answers in the next scene is on frame right looking toward frame left.
- NEVER in the video fields, not even negated: camera, lens, viewer, audience, fourth wall, cinematic, "looks at
  the camera", "his left" / "her right" (body-relative sides are ambiguous: use frame left / frame right). Write
  gaze or focus instead of eyes for where someone looks.
- In a scene with a `speaker`, the `image_prompt` shows the speaker already in three-quarter or side profile
  turned toward the partner's side of the frame, and EVERYONE with neutral closed lips (no open-mouthed surprise,
  laughing or gasping); `audio` has no voice sounds from others (no gasps, giggles, laughter): the video model
  gives the line to whichever face it sees talking.
- `listeners` = every other person visible while someone speaks, each with `position` (frame left / right /
  center), `visual_tag` and a small `silent_action` ("listening attentively with a gentle closed-mouth smile");
  the tool adds that they face the speaker and stay silent with lips closed.
- `narration`: only when the sound guide asks for it, in the message's language, at most 12 words. With spoken
  dialogue, `narration` is the speaker's `line` (it becomes the subtitle).
- Never ask for visible text: no captions, signs with words or title cards anywhere.
- For 9:16 keep faces and the key action in the upper-middle of the frame (say so in `image_prompt` only as
  "upper-middle of the frame", without the ratio).
- Define recurring people once in `characters` with a fixed, specific look (face, hair, signature clothing), each
  with a distinct silhouette. Each description is about that one person only (never compare to another character).
- Children: never an exact age ("age 8", "8-year-old"): write "a young girl", "a little boy", "a schoolgirl".
  Google's child-safety filter blocks pictures of children described with a precise age or body details; keep
  children in everyday activities, fully and casually dressed, described by hair, clothes and expression.
- `style` = one short sentence for the shared look (palette, lighting, mood); the visual style named in the
  message is added automatically, do not repeat it.
- Follow the format, sound and genres in the message: they decide pacing, the number of beats and who speaks.
- Google Flow refuses some content and the credits are lost: no gore, injuries, weapons up close, insults, or
  children in distress. Show hard beats mildly (a look, a turned back, a quiet moment).
- Scene ids: s01, s02, ...
- `thumbnail`: `text` = a short catchy title (max ~6 words) in the message's language; `prompt` = an English
  description of an eye-catching cover picture with the main characters by exact name (no text in it)."""

OPEN_MOUTH = r"(?i)\bopen[- ]mouth(?:ed|s)?\b|\bmouths? (?:wide )?open\b|\bgasp(?:s|ing)?\b|\blaugh(?:s|ing)\b|\bshout(?:s|ing)\b"
VOICE_SOUNDS = r"(?i)\b(?:gasps?|giggles?|giggling|laugh(?:s|ter|ing)?|cheers?|chatter|murmurs?|screams?|squeals?)\b"

FIX_INTRO = """Some scenes break the rules. Rewrite ONLY the scenes listed below (same ids, same story beat, same
image_prompt unless it is the problem) so that every problem is fixed, and return them in `scenes`. Keep title,
style, characters and thumbnail as they are."""


class BasicCharacter(BaseModel):
    name: str
    description: str = Field(description="Fixed look (English): face, hair, age, outfit.")


class BasicLocation(BaseModel):
    name: str = Field(description="Short unique name of the place.")
    description: str = Field(description="Fixed look (English): layout, fixtures, colours, materials, light. No people.")


class BasicScene(BaseModel):
    id: str = Field(description="s01, s02, ...")
    location: str | None = Field(default=None, description="Exact name of the place (one of `locations`).")
    image_prompt: str = Field(description="The opening frame as a still picture (English, characters by name).")
    camera: str = Field(description="Shot size, depth of field, ONE continuous move and its ending framing.")
    action: str = Field(description="[position + visual tag] + 1-2 physical beats, present tense, no names.")
    environment: str = Field(default="", description="Secondary motion + lighting continuity.")
    audio: str = Field(default="", description="Ambient room tone + SFX tied to the movement. No music.")
    speaker: Speaker | None = Field(default=None, description="Only when someone on screen speaks.")
    listeners: list[Listener] | None = Field(default=None, description="Everyone else visible while someone speaks.")
    narration: str | None = Field(default=None, description="Subtitle / caption / narrator line, at most 12 words.")


class BasicDraft(BaseModel):
    model_config = ConfigDict(title="Storyboard")

    title: str
    style: str = ""
    characters: list[BasicCharacter] = Field(default_factory=list)
    locations: list[BasicLocation] = Field(default_factory=list)
    thumbnail: Thumbnail = Field(default_factory=Thumbnail)
    scenes: list[BasicScene]


def user_message(idea: str, *, fmt: str, audio: str, visual: str, style: str, genres: list[str], scenes: int,
                 aspect: str, language: str | None) -> str:
    f = formats.FORMATS[fmt]
    a = formats.AUDIO[audio]
    lines = [f"Idea: {idea}", f"Number of scenes: {scenes}", f"Aspect ratio: {aspect}",
             f"Format: {f['label']} - {f['guide']}", f"Sound: {a['label']} - {a['guide']}"]
    if genres:
        lines.append("Genres: " + ", ".join(formats.GENRES[g][1] for g in genres))
    if style:
        lines.append(f"Visual style (added automatically): {style}")
    if language and (formats.uses_language(audio) or formats.has_text(audio)):
        lines.append(f"Language of spoken words and `narration`: {language}")
    elif language:
        lines.append(f"Language of the title: {language}")
    return "\n".join(lines)


def _to_storyboard(draft: dict, *, fmt: str, audio: str, visual: str, style: str, genres: list[str],
                   aspect: str, language: str | None) -> Storyboard:
    text = formats.has_text(audio)
    own = " ".join(str(draft.get("style") or "").split())
    data = {
        "title": str(draft.get("title") or "Phim mới").strip() or "Phim mới",
        "aspect": aspect,
        "style": ". ".join(x.rstrip(".") for x in (style, own) if x),
        "characters": [c for c in draft.get("characters") or []
                       if isinstance(c, dict) and str(c.get("name") or "").strip()],
        "thumbnail": draft.get("thumbnail") or {},
        "scenes": [],
        "format": fmt, "audio": audio, "visual_style": visual, "genres": genres or None,
        "dialogue_language": language if formats.uses_language(audio) and language else NO_DIALOGUE,
        "subtitle_language": language if text and language else NO_SUBTITLES,
    }
    for i, sc in enumerate([s for s in draft.get("scenes") or [] if isinstance(s, dict)], 1):
        shot = _shot(sc, audio)
        narration = " ".join(str(sc.get("narration") or "").split()) if text else ""
        if not narration and audio == "dialogue" and shot.speaker:
            narration = i2v_prompt.clean_line(shot.speaker.line)
        summary = i2v_prompt.clean_text(shot.action or (shot.speaker.action if shot.speaker else "")) or "."
        data["scenes"].append({"id": f"s{i:02d}", "mode": "keyframe",
                               **({"location": sc["location"]} if isinstance(sc.get("location"), str) else {}),
                               "image_prompt": i2v_prompt.strip_technical(sc.get("image_prompt")),
                               "prompt": summary, "video_shot": shot.model_dump(exclude_none=True),
                               **({"narration": narration} if narration else {})})
    data["locations"] = [loc for loc in draft.get("locations") or [] if isinstance(loc, dict)] or None
    planner._clean_locations(data)  # unknown / duplicate names never fail the plan
    if not data.get("locations"):
        data.pop("locations", None)
        for sc in data["scenes"]:
            sc.pop("location", None)
    return Storyboard.model_validate(data)


def _shot(sc: dict, audio: str) -> VideoShot:
    """The scene's video fields as a VideoShot; a speaker / listeners only where the sound allows speech."""
    speaks = audio == "dialogue"
    return VideoShot.model_validate({
        "camera": sc.get("camera") or "", "action": sc.get("action") or "",
        "environment": sc.get("environment") or "", "audio": sc.get("audio") or "",
        "speaker": sc.get("speaker") if speaks else None,
        "listeners": sc.get("listeners") if speaks and sc.get("speaker") else None,
    })


def problems(draft: dict, audio: str) -> dict[str, list[str]]:
    """{scene id: rule problems} for the scenes Claude must rewrite (English, for the fix request)."""
    names = [str(c.get("name") or "") for c in draft.get("characters") or [] if isinstance(c, dict)]
    out: dict[str, list[str]] = {}
    for sc in draft.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        shot, bad = _shot(sc, audio), []
        if audio == "dialogue" and shot.speaker and i2v_prompt.word_count(i2v_prompt.clean_line(shot.speaker.line)) > i2v_prompt.MAX_WORDS:
            bad.append(f"the spoken line has more than {i2v_prompt.MAX_WORDS} words: shorten it")
        narration = i2v_prompt.clean_line(sc.get("narration"))
        if formats.has_text(audio) and narration and i2v_prompt.word_count(narration) > i2v_prompt.MAX_WORDS:
            bad.append(f"`narration` has more than {i2v_prompt.MAX_WORDS} words: shorten it")
        blob = " ".join(str(x or "") for x in (shot.camera, shot.action, shot.environment, shot.speaker.visual_tag if shot.speaker else "",
                                                 shot.speaker.action if shot.speaker else "",
                                                 *[f"{x.visual_tag} {x.silent_action}" for x in shot.listeners or []]))
        used = [n for n in names if n.strip() and re.search(rf"\b{re.escape(n.strip())}\b", blob, re.IGNORECASE)]
        if used:
            bad.append(f"the video fields use the name(s) {', '.join(used)}: use position + visual tag instead")
        talking = audio == "dialogue" and shot.speaker and i2v_prompt.clean_line(shot.speaker.line)
        if not talking and i2v_prompt.active_people(shot.action) >= 2:
            bad.append("two or more people act at once in `action`: keep ONE active person, the others only react "
                       "lightly, and no two people handling the same object")
        if re.search(r"(?i)\b(?:identical|exact copy|same face)\b", f"{sc.get('image_prompt') or ''}"):
            bad.append("`image_prompt` asks for identical look-alikes: describe them as people who look alike, told "
                       "apart by hair and clothing")
        if shot.environment.strip() and re.search(r"(?i)matching the|\blight(?:ing)?\b", shot.environment):
            bad.append("`environment` repeats the light / setting of the picture: leave it empty unless something "
                       "in the surroundings must move")
        if talking and re.search(OPEN_MOUTH, f"{sc.get('image_prompt') or ''}"):
            bad.append("`image_prompt` of a dialogue scene shows someone with an open mouth: everyone but the speaker "
                       "has the mouth closed")
        if talking and re.search(VOICE_SOUNDS, shot.audio):
            bad.append("`audio` of a dialogue scene has voice sounds from others (gasp, giggle, laugh): keep only "
                       "room tone and object sounds")
        if re.search(r"(?i)\b(?:no|without)\s+(?:music|dialogue|speech|voices?|talking)\b", shot.audio):
            bad.append("`audio` lists what is not heard: name only the real sounds")
        if talking:
            sp = shot.speaker
            words = " ".join(str(x or "") for x in (sp.action, sp.gaze, sp.visual_tag, *[f"{x.visual_tag} {x.silent_action}" for x in shot.listeners or []]))
            if i2v_prompt.BODY_RELATIVE.search(words):
                bad.append("a direction is body-relative (his left / her right): use frame left / frame right")
            if re.search(r"(?i)\b(?:camera|lens|viewer|audience|fourth wall|cinematic)\b", words + " " + shot.action):
                bad.append("forbidden word in the dialogue fields (camera, lens, viewer, audience, cinematic)")
            if not i2v_prompt.frame_side(sp.position) or any(not i2v_prompt.frame_side(x.position) for x in shot.listeners or []):
                bad.append("every `position` in a dialogue scene must be frame left / frame right / frame center")
        if not shot.camera.strip() or not (shot.action.strip() or (shot.speaker and shot.speaker.action.strip())):
            bad.append("`camera` or `action` is missing")
        if bad:
            out[str(sc.get("id"))] = bad
    if audio == "dialogue":
        shots = [(str(sc.get("id")), _shot(sc, audio)) for sc in draft.get("scenes") or [] if isinstance(sc, dict)]
        for sid, why in i2v_prompt.axis_breaks(shots).items():
            out.setdefault(sid, []).append(f"180-degree axis broken: {why}")
    return out


def _repair(draft: dict, draft_fn, audio: str, base: str, log=None) -> dict:
    """One rewrite request for the scenes that break the rules (the guide's 'ask the LLM to shorten')."""
    bad = problems(draft, audio)
    if not bad:
        return draft
    if log:
        log(f"Sửa {len(bad)} cảnh chưa đúng quy tắc prompt I2V: {', '.join(bad)}…")
    listing = "\n".join(f"{sid}: {'; '.join(p)}\n  current: {json.dumps(next(s for s in draft['scenes'] if str(s.get('id')) == sid), ensure_ascii=False)}"
                        for sid, p in bad.items())
    try:
        fixed = draft_fn(f"{base}\n\n{FIX_INTRO}\n\nCharacters: "
                         f"{json.dumps(draft.get('characters') or [], ensure_ascii=False)}\n\n{listing}")
    except RuntimeError as exc:  # the first draft is still usable; the editor shows the warnings
        if log:
            log(f"⚠ Không sửa được: {exc}")
        return draft
    by_id = {str(s.get("id")): s for s in fixed.get("scenes") or [] if isinstance(s, dict)}
    draft = dict(draft)
    draft["scenes"] = [by_id.get(str(s.get("id")), s) if str(s.get("id")) in bad else s for s in draft["scenes"]]
    return draft


def plan_basic(idea: str, *, fmt: str, audio: str = "silent", visual: str = "realistic", custom_style: str = "",
               genres: list[str] | None = None, scenes: int = 6, aspect: str = "16:9", language: str | None = None,
               backend: str = "auto", api_key: str | None = None, log=None) -> tuple[Storyboard, str]:
    """Storyboard for a basic format. Returns (storyboard, backend used: claude_code | api)."""
    if not formats.is_basic(fmt):
        raise ValueError(f"định dạng {fmt!r} không dùng luồng cơ bản")
    if audio not in formats.AUDIO:
        raise ValueError(f"âm thanh {audio!r} không hợp lệ")
    if visual not in formats.VISUALS:
        raise ValueError(f"phong cách {visual!r} không hợp lệ")
    style = formats.visual_style(visual, custom_style if visual == "other" else "")
    genres = formats.clean_genres(genres or [])
    language = clean_language(language) if language else None
    if language and language.casefold() == NO_SUBTITLES:
        language = None
    base = user_message(idea, fmt=fmt, audio=audio, visual=visual, style=style, genres=genres, scenes=scenes,
                        aspect=aspect, language=language)
    if backend == "auto":
        backend = "claude_code" if planner.claude_code_binary() else "api"
    if backend == "claude_code":
        def draft_fn(user: str) -> dict:
            return planner._draft_claude_code(user, system=SYSTEM, schema_model=BasicDraft)
    else:
        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("chưa có ANTHROPIC_API_KEY (hoặc cài Claude Code để dùng gói Claude của bạn)")

        def draft_fn(user: str) -> dict:
            return planner._draft_api(user, system=SYSTEM, schema_model=BasicDraft)
    draft = planner._draft_in_parts(draft_fn, base, scenes, log)
    draft["scenes"] = [{**sc, "id": f"s{i:02d}"} for i, sc in
                       enumerate([x for x in draft.get("scenes") or [] if isinstance(x, dict)], 1)]
    draft = _repair(draft, draft_fn, audio, base, log)
    sb = _to_storyboard(draft, fmt=fmt, audio=audio, visual=visual, style=style, genres=genres, aspect=aspect,
                        language=language)
    sb = planner.fix_vietnamese_diacritics(sb, backend=backend, api_key=api_key, log=log)
    meta = model_meta.load()
    sb = model_meta.normalize_new_storyboard(sb, meta)
    return durations_mod.round_new_storyboard(sb, meta), backend
