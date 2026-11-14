"""Review agents for the autopilot: storyboard, keyframes, clips and the finished film.

Each review is one Claude call that answers in a fixed JSON shape (pydantic models below) and,
where it makes sense, gives the corrected prompt text so the autopilot can fix the scene itself.
Reviews of pictures send small JPEGs (keyframes, three frames of each clip, a contact sheet of the film).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import ai
from .storyboard import NO_DIALOGUE, Camera, Storyboard

Verdict = Literal["PASS", "REVISE"]


# --- answer shapes ---------------------------------------------------------------


class DialogueFix(BaseModel):
    character: str
    line: str
    delivery: str | None = None


class NewCharacter(BaseModel):
    name: str = Field(description="Short unique name in capitals, e.g. FARMER.")
    description: str = Field(description="Fixed look in English, like the other characters: age, face, hair, outfit.")


class SceneFix(BaseModel):
    id: str
    issues: list[str] = Field(default_factory=list, description="Problems found, short, in Vietnamese.")
    prompt: str | None = Field(default=None, description="Corrected action prompt (English), only when it must change.")
    image_prompt: str | None = Field(default=None, description="Corrected opening-frame prompt (English), only when it must change.")
    content_direction: str | None = Field(default=None, description="Corrected content direction (English), only when it must change.")
    narration: str | None = Field(default=None, description="Corrected subtitle line, only when it must change.")
    dialogue: list[DialogueFix] | None = Field(default=None, description="Full corrected dialogue of the scene, only when it must change.")
    camera: Camera | None = Field(default=None, description="Full new camera direction (replaces the old one), only "
                                  "when the shot itself changes (a new framing or composition).")


class StoryboardReview(BaseModel):
    verdict: Verdict
    score: int = Field(description="1-10, overall quality of the storyboard as a short film for social video.")
    summary: str = Field(description="2-4 sentences in Vietnamese.")
    scenes: list[SceneFix] = Field(default_factory=list, description="Only the scenes that need a change.")
    characters: list[NewCharacter] = Field(default_factory=list, description="Characters to ADD, only when a scene's "
                                           "line belongs to someone not in the character list.")


class KeyframeVerdict(BaseModel):
    id: str
    verdict: Verdict
    score: int = Field(description="1-10")
    issues: list[str] = Field(default_factory=list, description="Short, in Vietnamese.")
    image_prompt: str | None = Field(default=None, description="When REVISE: a corrected opening-frame prompt (English) "
                                     "that fixes the problems. Name characters exactly; no text in the picture.")


class KeyframeReview(BaseModel):
    summary: str
    scenes: list[KeyframeVerdict]


class ClipVerdict(BaseModel):
    id: str
    verdict: Literal["PASS", "MINOR", "RERENDER"] = Field(
        description="PASS = good; MINOR = small flaws, keep it; RERENDER = clearly broken, worth paying to render again.")
    score: int = Field(description="1-10")
    issues: list[str] = Field(default_factory=list, description="Short, in Vietnamese.")
    prompt: str | None = Field(default=None, description="When RERENDER: a corrected action prompt (English) that avoids the problem.")


class ClipReview(BaseModel):
    summary: str
    scenes: list[ClipVerdict]


class FilmReview(BaseModel):
    verdict: Verdict
    score: int = Field(description="1-10, the finished film as a whole.")
    summary: str = Field(description="3-5 sentences in Vietnamese.")
    strengths: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list, description="Concrete next steps, e.g. re-render s04 because ...")
    thumbnail: str = Field(default="", description="One sentence on the cover picture and its title.")


# --- prompts ---------------------------------------------------------------------

COMMON = """You review short AI-made films (Google Flow: Nano Banana images, Veo video clips of 4-8 seconds).
Write `summary` and `issues` in Vietnamese (short, concrete). Write every prompt text you return in English.
Character names in prompts must match the storyboard's character names exactly."""

STORYBOARD_SYSTEM = COMMON + """
You are the storyboard reviewer. Check the whole storyboard BEFORE any picture is made:
- Story: s01 shows conflict or emotion in its first 2 seconds (REVISE a calm establishing opening), clear progression, an emotional payoff at the end; each scene moves the story.
- Each scene is ONE continuous shot doable in its duration: one main action, no cuts, no time jumps, no montage inside a clip.
- Continuity between scenes: same outfits, places, time of day, props, unless the story changes them on purpose.
- Prompts are concrete (who, does what, where, lighting) and avoid what Veo does badly: readable text, signs,
  screens with words, many people with precise hand actions, fast complex choreography, morphing objects.
- `image_prompt` is a still: positions, faces and setting, no motion words. `prompt` is the motion from that frame.
- Dialogue fits the clip (about 2-3 short lines in 8 seconds), nobody repeats a line, lines move the story.
  Lip-sync works best with ONE speaker per scene whose face is visible: fix scenes where two characters both
  speak by keeping one speaker (move the reply to the next scene's dialogue if it matters).
- Every line is given to the person who says it in the picture. When it belongs to someone who is not in the
  character list (a farmer, a nurse, a passer-by), ADD that person in `characters` (short NAME, look in English
  like the others) and return the scene's full corrected `dialogue` with that name. A line given to a character
  who is not in the scene is never left as it is: Veo is told to show that person speaking.
- No visible text, captions or written words asked for anywhere.
- Content Google Flow refuses or fails on (credits are lost: "Audio generation failed", or the clip is deleted):
  insults or slurs (above all about disability), threats, shouting, fights, bullying up close, injuries, pain,
  blood, weapons, and above all children in distress (hurt, crying hard, mocked, fighting). Rewrite such scenes
  mildly, keeping the story beat: dialogue without insults, calm/soft `delivery` instead of fierce or raised,
  action and image_prompt without pain or violence (feelings through faces and small gestures).
- Children in `characters`, `prompt` and `image_prompt` (Google's child-safety filter blocks many pictures of minors and the credits are lost): never an
  exact age ("4 years old", "age 8", "10 months old") nor body details (chubby, round or rosy cheeks, plump): write
  "a toddler girl", "a schoolgirl", "a baby boy" and describe them by hair, clothes and what they do. Keep children
  fully and casually dressed in everyday activities, usually in medium or wide shots together with an adult; never
  a child in a bath, swimwear, undressing, in bed up close, asleep or limp, alone and frightened, crying hard, or an
  adult kissing, feeding or holding a child's face up close. Fear or shock in a child is played as surprise or
  playful worry. Give close-ups and the main spoken lines to adults or teens when the story allows.
- Real, identifiable people (Flow refuses "prominent people"): in a film based on a true event, prompts and
  content directions must not point to the real persons — no real names, ranks, units or famous roles ("the
  retired Navy SEAL", "the Australian doctor"), no "true story". Describe characters by look and action only.
Fix only what clearly needs fixing; keep everything else exactly. For a scene you change, return only the
fields that change (full new text for each), plus the issues. verdict PASS when it can be produced as is
(small fixes still allowed), REVISE when the story or several scenes have real problems."""

KEYFRAME_SYSTEM = COMMON + """
You are the keyframe reviewer: each picture is the opening frame of one scene, before paying for its video.
You also get the character reference portraits. For each scene check:
- The right characters are there (count and identity), faces/hair/clothes match their portraits.
- It matches the scene's opening-frame prompt and content direction (place, action set-up, emotion, framing).
- No text, captions, watermarks, logos or garbled lettering; no deformed hands/faces/limbs; no duplicated people.
- It works as a first frame for the action that follows (the subject is placed so the motion can happen).
- Clean and readable: REVISE cluttered, low-contrast frames or heavily overlapping/occluded people (they animate
  badly), and for 9:16 faces cut off by or sitting in the bottom 20% or at the right edge.
- Continuity with the other scenes (outfits, place, time of day).
- Who speaks: when the scene has dialogue, the video starts from THIS picture and the line is spoken in the first
  seconds, so Veo gives it to a face it can see talking. The character who speaks first must face the camera (front
  or three-quarter view, face and mouth clearly visible) and be the most prominent face. If the speaker is in
  profile, turned away, hidden or small while another character faces the camera, that other character will be
  made to say the line: REVISE, with an `image_prompt` that puts the speaker facing the camera and the others
  turned toward the speaker or listening with the mouth closed.
  Veo also gives the line to a listener who is bigger or closer than the speaker, or whose mouth is open
  (talking, shouting, laughing, an open-mouthed grin), even when the speaker faces the camera too. So REVISE
  when a character who does not speak is the largest / closest face, or has the mouth open: the speaker must be
  the main face and every listener (and the crowd behind) must have the mouth closed. A listener turned
  sideways does not make it fine. The `image_prompt` then puts the speaker in the foreground facing the camera,
  the listeners smaller or further back with closed-mouth expressions.
verdict REVISE only for problems worth a new picture (wrong/missing character, wrong face, text in the picture,
deformation, wrong setting or framing that breaks the scene, the speaker of a dialogue scene not facing the
camera or not the main face, a listener with an open mouth). Continuity breaks are ALWAYS REVISE, because the video
inherits them: an outfit, prop, weather or time of day that contradicts the story so far (a raincoat given away
earlier, rain a year later in a sunny reunion, the old work clothes after the character's life changed). If you
list such a problem in `issues`, the verdict must be REVISE, never PASS. Then give `image_prompt`: the full corrected
opening-frame prompt that fixes it (keep what was right). Minor taste issues are PASS."""

CLIP_SYSTEM = COMMON + """
You are the clip reviewer. Each picture is a strip of 3 frames (start, middle, end) from one rendered video clip.
Judge from the frames: does the clip show the scene's action, keep the characters' identity (compare with the
portraits), and avoid visual failures (morphing faces or bodies, extra limbs, people appearing/vanishing,
burned-in subtitles/text, frozen or black frames, wrong characters)?
verdict RERENDER only when the clip is clearly broken or shows the wrong thing (rendering again costs credits),
and then give `prompt`: a corrected action prompt that makes the problem less likely (simpler motion, clearer
action). Check clips with dialogue hardest for burned-in subtitles, especially in the bottom third: any readable
or gibberish caption = RERENDER. MINOR = small flaws a viewer would accept. PASS = good. You cannot hear audio: do not judge sound."""

FILM_SYSTEM = COMMON + """
You are the final reviewer of the finished film. You get a contact sheet of frames sampled across the whole film
(in order, left to right, top to bottom), the cover thumbnail, and the storyboard. Judge it as a short video for
TikTok / YouTube Shorts / Facebook: does it tell the story clearly, is the look consistent, are characters
recognisable from scene to scene, any visible failures, is the cover eye-catching with a readable title?
verdict PASS when it is ready to publish, REVISE when something should be fixed first; say exactly what."""


AUDIO_FAILED = "audio generation failed"  # Flow's words when it could not make a clip's sound
PROMINENT = "prominent people"  # Flow's words when a prompt looks like real, well-known people
HARMFUL = "harmful content"  # Flow's words when a scene looks like harm (often a child unconscious or in danger)

SOFTEN_SYSTEM = COMMON + """
Google Flow generated the clip of this scene and then deleted it (its safety filter), or refused it.
Rewrite the scene so the same story beat gets through: keep the characters, place and emotion, but remove
what likely tripped the filter (touching or feeding children, a child in bed or sick shown up close, injuries,
blood, weapons, violence, nudity, medical procedures, brand names, real people). Prefer showing the emotion
through faces, looks and gentle gestures at a respectful distance. Return the scene id, the new `prompt` (the
motion from the opening frame) and, only if the opening frame itself is the problem, a new `image_prompt`.
Keep the dialogue unless a line is the problem (then give the full corrected dialogue).

When the error says "Audio generation failed", Flow could not make the SOUND (voice and ambience) of the
clip, which is also a safety check. It is triggered by what is said AND by what is shown, most often children
in distress. Then change all of these that apply:
- Dialogue: no insults, slurs or mocking words (above all about disability, body, race or family), no threats,
  no shouting. Give the full corrected dialogue: the same story point said plainly and gently.
- Delivery: calm, quiet, sincere or soft, never fierce, sneering, screaming, crying hard or "raised voice".
- Action and ambience: no fight, confrontation, bullying up close, pain, injury, limping, gritted teeth,
  sweat and tears together, or crowds jeering; keep the emotion (sad, worried, brave, kind) with faces, looks
  and small gestures. With children, keep it especially mild and warm.

When the error says the prompt "might violate our policies about generating prominent people", Flow thinks the
scene shows real, identifiable people (a film based on a true event). Keep the story beat and the characters'
names, but remove what points to the real persons: real names, ranks, units and famous roles ("retired Navy
SEAL", "the Australian doctor"), nationality + job combinations, exact dates, places and true-event details
that identify them, and words like "true story". Describe them as ordinary fictional people by look and action
only. Give a new `prompt` and a new `content_direction`, and a new `image_prompt` too if the opening frame
carries those details.

When the error says "harmful content", the scene reads as harm, most often a child who looks unconscious, limp,
sedated, trapped or in danger. Keep the story beat but show it safely: the child awake and calm or resting
peacefully, carried gently with the face visible and relaxed, no masks over a still face, no limp bodies, no
drowning or struggle in water; show danger through the setting and the adults' focus, not the child's body.

When the error is a plain "might violate our policies" and a child is in the scene, it is most often the
child-safety filter: keep the beat but frame it so the child is a natural part of an everyday moment with an
adult (medium or wide shot, fully dressed, awake and calm or playful), drop exact ages and body details, and
move close-ups or the line to the adult.

When earlier rewrites of the scene are listed and Flow refused them the same way, rewording was not enough:
Flow judges the opening picture and the whole moment, not single words. Change what is SHOWN, not just the
wording. Re-stage the shot so it no longer recreates a well-known real photo, news footage or famous moment:
another camera angle and framing (a close-up of one character's face or hands, the scene seen from behind or
from far away, a detail such as lamp light on water), fewer people in frame, and different poses. Drop the
famous details of the real event from the picture, the action and the dialogue (exact counts, dates, places,
well-known quotes): keep the feeling, say it another way. Always give a new `image_prompt` then, a new
`prompt` and `content_direction` that match it, and a new `camera` (the old shot size, lens and composition
belong to the refused framing and would contradict the new picture)."""


# --- storyboard text -------------------------------------------------------------


def storyboard_brief(sb: Storyboard) -> str:
    """The storyboard as compact text for the reviewers."""
    lines = [f"Title: {sb.title} ({sb.aspect})", f"Style: {sb.style}"]
    if sb.dialogue_language == NO_DIALOGUE:
        lines.append("Spoken dialogue: none")
    elif sb.dialogue_language:
        lines.append(f"Spoken dialogue language: {sb.dialogue_language}")
    lines.append("Characters:")
    lines += [f"- {c.name}: {c.description}" for c in sb.characters]
    lines.append("Scenes:")
    for s in sb.scenes:
        parts = [f"- {s.id} ({s.mode}, {s.duration or 'default'}s)"]
        if s.image_prompt:
            parts.append(f"  opening frame: {s.image_prompt}")
        parts.append(f"  action: {s.prompt}")
        if s.camera and not s.camera.is_empty():
            parts.append(f"  camera: {s.camera.describe()}")
        if s.content_direction:
            parts.append(f"  content direction: {s.content_direction}")
        for d in s.spoken_lines():
            parts.append(f"  {d.character}{f' ({d.delivery})' if d.delivery else ''}: {d.line}")
        if s.narration:
            parts.append(f"  subtitle: {s.narration}")
        lines += parts
    return "\n".join(lines)


def portraits(sb: Storyboard) -> list[tuple[str, Path]]:
    return [(f"portrait of {c.name}", Path(c.image)) for c in sb.characters if c.image and Path(c.image).is_file()]


# --- frames from videos ----------------------------------------------------------


def duration_of(path: Path) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=60).stdout
        return float(json.loads(out)["format"]["duration"])
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return 0.0


def frame_strip(video: Path, dest: Path, *, frames: int = 3, height: int = 360) -> Path | None:
    """`frames` frames spread over the clip, side by side in one JPEG. None if ffmpeg fails."""
    return _sheet(video, dest, cols=frames, rows=1, height=height)


def contact_sheet(video: Path, dest: Path, *, cols: int = 4, rows: int = 4, height: int = 240) -> Path | None:
    return _sheet(video, dest, cols=cols, rows=rows, height=height)


def _sheet(video: Path, dest: Path, *, cols: int, rows: int, height: int) -> Path | None:
    n = cols * rows
    dur = duration_of(video)
    if dur <= 0:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    # n frames at the middle of n equal slices (never the black first/last frame): from s to dur - s at a rate
    # that gives exactly n of them, so no tile of the sheet is left empty (an empty tile read as a "black frame")
    s = dur / n / 2
    rate = n / max(dur - 2 * s, 0.001)  # a little more than n frames: the tile takes the first n
    vf = f"fps={rate:.6f}:start_time={s:.3f},scale=-2:{height},tile={cols}x{rows}"
    try:
        proc = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), "-vf", vf, "-frames:v", "1",
                               "-q:v", "4", str(dest)], capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return dest if proc.returncode == 0 and dest.is_file() else None


# --- the reviews -----------------------------------------------------------------


def _ai_kw(sb: Storyboard, kw: dict) -> dict:
    if "ai_model" not in kw and sb.ai_model:
        return {"ai_model": sb.ai_model, **kw}
    return kw


def review_storyboard(sb: Storyboard, **kw) -> StoryboardReview:
    user = storyboard_brief(sb) + "\n\nReview this storyboard."
    return ai.ask(STORYBOARD_SYSTEM, user, StoryboardReview, **_ai_kw(sb, kw))


def review_keyframes(sb: Storyboard, keyframes: list[tuple[str, Path]], **kw) -> KeyframeReview:
    """`keyframes` = [(scene id, picture)]. Every scene listed gets a verdict."""
    ids = ", ".join(sid for sid, _ in keyframes)
    user = (storyboard_brief(sb) + f"\n\nReview the opening-frame picture of scenes {ids}. "
            "Return one entry per scene, in that order.")
    images = portraits(sb) + [(f"scene {sid} keyframe", p) for sid, p in keyframes]
    return ai.ask(KEYFRAME_SYSTEM, user, KeyframeReview, images=images, **_ai_kw(sb, kw))


def review_clips(sb: Storyboard, strips: list[tuple[str, Path]], **kw) -> ClipReview:
    ids = ", ".join(sid for sid, _ in strips)
    user = (storyboard_brief(sb) + f"\n\nReview the rendered clips of scenes {ids}. "
            "Return one entry per scene, in that order.")
    images = portraits(sb) + [(f"scene {sid} clip: start | middle | end", p) for sid, p in strips]
    return ai.ask(CLIP_SYSTEM, user, ClipReview, images=images, **_ai_kw(sb, kw))


def review_film(sb: Storyboard, sheet: Path | None, thumbnail: Path | None, extra: str = "", **kw) -> FilmReview:
    user = storyboard_brief(sb)
    if sb.thumbnail.text:
        user += f"\nCover title: {sb.thumbnail.text}"
    if extra:
        user += "\n" + extra
    user += "\n\nReview the finished film."
    images = []
    if sheet:
        images.append(("contact sheet of the whole film", sheet))
    if thumbnail:
        images.append(("cover thumbnail", thumbnail))
    return ai.ask(FILM_SYSTEM, user, FilmReview, images=images, **_ai_kw(sb, kw))


def soften_scene(sb: Storyboard, sid: str, error: str, earlier: list[dict] | None = None, **kw) -> SceneFix:
    """`earlier`: previous rewrites of this scene that Flow refused too ({prompt, image_prompt, dialogue}),
    oldest first. With them Claude re-stages the shot instead of rewording it again."""
    user = (storyboard_brief(sb) + f"\n\nScene {sid} was blocked by Flow: {error[:500]}\n"
            f"Rewrite scene {sid} so it can be generated.")
    if earlier:
        user += (f"\nThe scene was already rewritten {len(earlier)} time(s) and Flow still refused it. "
                 "Those versions (all refused):")
        for i, e in enumerate(earlier[-4:], 1):
            user += f"\n  attempt {i}: opening frame: {str(e.get('image_prompt') or '-')[:600]}"
            user += f"\n             action: {str(e.get('prompt') or '-')[:600]}"
            if e.get("dialogue"):
                user += "\n             dialogue: " + " / ".join(
                    f"{d.get('character')}: {d.get('line')}" for d in e["dialogue"] if isinstance(d, dict))
        user += ("\nRewording again will fail the same way: re-stage the shot as the instructions say (new "
                 "framing and composition, fewer people, no famous details of the real event).")
    if PROMINENT in error.lower():
        user += ("\nFlow refused it as “prominent people”: rewrite the prompt and content direction so nothing "
                 "points to real, identifiable persons, as the instructions say.")
    if HARMFUL in error.lower():
        user += ("\nFlow refused it as “harmful content”: show the beat safely, as the instructions say (no child "
                 "who looks unconscious, limp or in danger).")
    if AUDIO_FAILED in error.lower():
        user += ("\nFlow said “Audio generation failed”: rewrite the dialogue, its delivery and the action as "
                 "the instructions say, keeping the story point.")
    return ai.ask(SOFTEN_SYSTEM, user, SceneFix, **_ai_kw(sb, kw))


# --- applying fixes to the storyboard (raw YAML dict) ----------------------------

SCENE_FIELDS = ("prompt", "image_prompt", "content_direction", "narration")


def add_characters(raw: dict, new: list[NewCharacter]) -> list[str]:
    """Add the reviewer's new characters (a speaker missing from the list). Returns the names added."""
    chars = raw.setdefault("characters", [])
    have = {str(c.get("name", "")).strip().casefold() for c in chars if isinstance(c, dict)}
    added: list[str] = []
    for c in new:
        name, desc = c.name.strip(), c.description.strip()
        if name and desc and name.casefold() not in have:
            chars.append({"name": name, "description": desc})
            have.add(name.casefold())
            added.append(name)
    return added


def apply_scene_fixes(raw: dict, fixes: list[SceneFix]) -> list[str]:
    """Write the corrected fields into the raw storyboard dict. Returns the ids of the scenes changed.
    A dialogue line by someone not in the character list is dropped (add them first with `add_characters`)."""
    by_id = {s.get("id"): s for s in raw.get("scenes") or [] if isinstance(s, dict)}
    names = {str(c.get("name", "")).strip().casefold() for c in raw.get("characters") or [] if isinstance(c, dict)}
    changed: list[str] = []
    for fix in fixes:
        scene = by_id.get(fix.id)
        if scene is None:
            continue
        touched = False
        for f in SCENE_FIELDS:
            val = getattr(fix, f)
            if isinstance(val, str) and val.strip() and val.strip() != str(scene.get(f) or "").strip():
                scene[f] = val.strip()
                touched = True
        if fix.camera is not None:
            cam = fix.camera.model_dump(exclude_none=True)
            if cam != (scene.get("camera") or {}):
                if cam:
                    scene["camera"] = cam
                else:
                    scene.pop("camera", None)
                touched = True
        if fix.dialogue is not None:
            rows = [d.model_dump(exclude_none=True) for d in fix.dialogue
                    if d.line.strip() and d.character.strip().casefold() in names]
            if rows != (scene.get("dialogue") or []):
                scene["dialogue"] = rows or None
                if not rows:
                    scene.pop("dialogue", None)
                touched = True
            cast = scene.get("characters")
            if isinstance(cast, list):  # an explicit cast must include whoever now speaks
                for r in rows:
                    if r["character"].strip().casefold() not in {str(n).strip().casefold() for n in cast}:
                        cast.append(r["character"].strip())
        if touched:
            changed.append(fix.id)
    return changed


def set_scene_field(raw: dict, sid: str, field: str, value: str) -> bool:
    for s in raw.get("scenes") or []:
        if isinstance(s, dict) and s.get("id") == sid:
            if str(s.get(field) or "").strip() == value.strip():
                return False
            s[field] = value.strip()
            return True
    return False
