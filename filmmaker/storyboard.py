"""Storyboard file format (YAML) and helpers to build the final prompts."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Literal, NamedTuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import formats
from . import i2v_prompt
from .ai import DEFAULT_AI_MODEL, clean_ai_model
from .i2v_prompt import VideoShot

Aspect = Literal["16:9", "9:16"]
Mode = Literal["t2v", "i2v", "keyframe"]


MAX_REFS = 3  # reference images sent with one generation (Flow accepts only a few)
# Added to every prompt sent to Flow: models like to burn dialogue/titles into the picture.
NO_TEXT = ("No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; "
           "any dialogue is spoken audio only.")
# Spoken language forced into every *video* prompt (never into image prompts or the fingerprints).
LANG_MAX = 40
NO_DIALOGUE = "none"  # stored value for "Không thoại"
NO_SUBTITLES = "none"  # subtitle_language for "Không phụ đề": the film has no subtitles at all
# Says the language of the lines, never that "characters speak": "characters speak only Thai" read as everyone
# talking, and listeners mouthed or echoed the speaker's line.
DIALOGUE_RULE = ("Spoken language: {lang}. Each line is said in {lang} only by the character it belongs to; "
                 "nobody else repeats it, mouths it or talks along.")
# The sentence used before; prompts that still carry it get the new one instead.
OLD_DIALOGUE_RULE = re.compile(r"All spoken dialogue is in [^;.]+; characters speak only [^.]+\.?")
NO_DIALOGUE_RULE = "No spoken dialogue: characters do not talk; only ambient sound, sound effects and music."
# A scene without lines in a film with dialogue. Sending the language rule there ("characters speak only Thai")
# made Veo invent lines in half of such scenes (a mother saying "it's ok, I'm here" in a scene meant to be silent).
SILENT_SCENE_RULE = ("Nobody speaks in this scene: no dialogue, no words, no voice-over; the characters stay silent "
                     "(breathing, sighs, crying, laughing and wordless humming are fine); background voices, if any, "
                     "are an indistinct murmur; "
                     "only ambient sound and sound effects.")


def clean_language(value: object) -> str | None:
    """A language name as stored: None / "" / blanks -> None, inner whitespace collapsed.
    ValueError when it is not text, has a line break, a control / format character (NUL, ESC, bidi
    override, zero-width...) or is longer than LANG_MAX."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("tên ngôn ngữ phải là chữ")
    if "\n" in value or "\r" in value:
        raise ValueError("tên ngôn ngữ không được xuống dòng")
    v = " ".join(value.split())
    if not v:
        return None
    if any(unicodedata.category(ch).startswith("C") for ch in v):
        raise ValueError("tên ngôn ngữ có ký tự không hợp lệ")
    if len(v) > LANG_MAX:
        raise ValueError(f"tên ngôn ngữ dài quá {LANG_MAX} ký tự")
    return v


# Every film always gets these three subtitle tracks: (code, English name, ISO 639-2 for the MP4 track).
SUBTITLE_LANGS = (("vi", "Vietnamese", "vie"), ("th", "Thai", "tha"), ("en", "English", "eng"))
SUBTITLE_CODES = tuple(c for c, _, _ in SUBTITLE_LANGS)


def subtitle_code(language: str | None) -> str | None:
    """"Vietnamese" / "vi" / "tiếng việt" -> "vi"; None when it is not one of the three track languages."""
    v = " ".join(str(language or "").split()).casefold()
    if not v:
        return None
    for code, name, iso in SUBTITLE_LANGS:
        if v in (code, name.casefold(), iso):
            return code
    aliases = {"tiếng việt": "vi", "tieng viet": "vi", "tiếng thái": "th", "tieng thai": "th", "ไทย": "th",
               "tiếng anh": "en", "tieng anh": "en"}
    return aliases.get(v)


def clean_dialogue_language(value: object) -> str | None:
    """Like clean_language; "none" in any case means no spoken dialogue and is stored as "none"."""
    v = clean_language(value)
    return NO_DIALOGUE if v is not None and v.casefold() == NO_DIALOGUE else v


_SHA_CACHE: dict[tuple[str, int, int, int], str] = {}
_SHA_CACHE_MAX = 512


def file_sha(path: str | Path) -> str | None:
    """sha256 of a file, cached by (path, mtime, ctime, size) so the project page does not re-read every reference
    image on each poll. None when the file cannot be read."""
    try:
        st = Path(path).stat()
        key = (str(path), st.st_mtime_ns, st.st_ctime_ns, st.st_size)
        if key in _SHA_CACHE:
            return _SHA_CACHE[key]
        sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None
    if len(_SHA_CACHE) >= _SHA_CACHE_MAX:
        _SHA_CACHE.clear()
    _SHA_CACHE[key] = sha
    return sha


class Subtitles(BaseModel):
    """The scene's subtitle line in each of the three track languages."""
    vi: str | None = Field(default=None, description="Vietnamese subtitle line.")
    th: str | None = Field(default=None, description="Thai subtitle line.")
    en: str | None = Field(default=None, description="English subtitle line.")

    @field_validator("vi", "th", "en", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return (" ".join(v.split()) or None) if isinstance(v, str) else v

    @model_validator(mode="before")
    @classmethod
    def _language_names(cls, v: object) -> object:
        return subtitle_codes_dict(v) if isinstance(v, dict) else v


def subtitle_codes_dict(subs: dict) -> dict:
    """Older storyboards keyed scene subtitles by language name ({"Thai": ...}); turn those keys into track
    codes ({"th": ...}). A code already present wins; languages other than the three tracks are dropped."""
    out: dict = {}
    for k, v in subs.items():
        code = k if k in SUBTITLE_CODES else subtitle_code(k)
        if code and (code not in out or (k == code and v)):
            out[code] = v
    return out


class Character(BaseModel):
    name: str
    description: str = Field(description="Fixed look: face, hair, outfit... Repeated in every scene that mentions the name.")
    image: str | None = Field(default=None, description="Reference image of the character (path). Sent with every "
                              "keyframe / video of a scene that mentions the name so the face stays the same.")
    image_prompt: str | None = Field(default=None, description="Prompt for generating the reference image "
                                     "(defaults to a full-body portrait built from the description).")


class Location(BaseModel):
    """A place that appears in several scenes, described once so it looks the same everywhere."""
    name: str = Field(description="Short unique name of the place; scenes refer to it in `location`.")
    description: str = Field(default="", description="Fixed look of the place (English): layout, main furniture, "
                             "props, colors and the fixed light sources. Added to every scene set there.")
    image: str | None = Field(default=None, description="Reference image of the empty place (path). Sent with the "
                              "keyframes / videos of scenes set there so the room stays the same.")
    image_prompt: str | None = Field(default=None, description="Prompt for generating the reference image "
                                     "(defaults to an empty wide view built from the description).")


class RefItem(NamedTuple):
    """One reference image of a generation, in sending order. `label` is the text logged for it:
    "bối cảnh <name>", "keyframe <scene id>" or the character's name."""
    kind: Literal["location", "keyframe", "character"]
    label: str
    path: Path


class Thumbnail(BaseModel):
    prompt: str | None = Field(default=None, description="Cover picture for the video (English, no text). "
                               "Name the characters to include. Defaults to the main characters in a dramatic moment.")
    text: str | None = Field(default=None, description="Title printed on the cover (any language, short).")


# Camera direction vocabulary: (English phrase sent to Flow, Vietnamese label for the UI).
# Any other text is accepted too; these are just the usual film terms.
CAMERA_PRESETS: dict[str, list[tuple[str, str]]] = {
    "shot": [
        ("extreme wide shot", "Toàn cảnh rất rộng (EWS)"),
        ("wide shot", "Toàn cảnh (WS)"),
        ("full shot", "Toàn thân (FS)"),
        ("medium wide shot", "Trung toàn (MWS)"),
        ("medium shot", "Trung cảnh (MS)"),
        ("medium close-up", "Cận trung (MCU)"),
        ("close-up", "Cận cảnh (CU)"),
        ("extreme close-up", "Đặc tả (ECU)"),
        ("over-the-shoulder shot", "Qua vai (OTS)"),
        ("two shot", "Hai người trong khung"),
        ("point-of-view shot", "Góc nhìn nhân vật (POV)"),
        ("insert shot", "Cận vật (insert)"),
        ("establishing shot", "Cảnh giới thiệu bối cảnh"),
    ],
    "angle": [
        ("eye-level angle", "Ngang tầm mắt"),
        ("low angle", "Góc thấp (hất lên)"),
        ("high angle", "Góc cao (chúc xuống)"),
        ("bird's-eye view, top-down", "Từ trên xuống (bird's-eye)"),
        ("worm's-eye view", "Sát đất nhìn lên (worm's-eye)"),
        ("dutch angle, tilted horizon", "Nghiêng khung (dutch)"),
        ("overhead shot", "Trên đầu (overhead)"),
        ("profile view", "Nhìn ngang (profile)"),
        ("aerial drone view", "Flycam trên cao"),
    ],
    "movement": [
        ("static locked-off camera", "Máy đứng yên"),
        ("slow dolly-in", "Dolly tiến chậm"),
        ("slow dolly-out", "Dolly lùi chậm"),
        ("slow push-in", "Đẩy vào nhẹ (push-in)"),
        ("pull-back reveal", "Lùi ra để lộ bối cảnh"),
        ("tracking shot following the subject", "Bám theo chủ thể (tracking)"),
        ("pan left to right", "Lia ngang (pan)"),
        ("tilt up", "Ngửa máy lên (tilt up)"),
        ("tilt down", "Chúc máy xuống (tilt down)"),
        ("orbit around the subject", "Xoay vòng quanh chủ thể (orbit)"),
        ("crane up", "Cẩu lên (crane up)"),
        ("crane down", "Cẩu xuống (crane down)"),
        ("handheld camera, subtle shake", "Cầm tay, rung nhẹ"),
        ("smooth steadicam", "Steadicam mượt"),
        ("slow zoom in", "Zoom vào chậm"),
        ("dolly zoom (vertigo effect)", "Dolly zoom (hiệu ứng vertigo)"),
        ("whip pan", "Lia nhanh (whip pan)"),
        ("drone flyover", "Flycam bay qua"),
    ],
    "lens": [
        ("16mm ultra-wide lens", "16mm siêu rộng"),
        ("24mm wide-angle lens", "24mm góc rộng"),
        ("35mm lens", "35mm (tự nhiên, kể chuyện)"),
        ("50mm lens", "50mm (gần mắt người)"),
        ("85mm portrait lens", "85mm (chân dung)"),
        ("135mm telephoto lens, compressed background", "135mm tele (nén hậu cảnh)"),
        ("macro lens", "Macro (siêu cận)"),
        ("anamorphic lens, oval bokeh, horizontal flares", "Anamorphic (điện ảnh)"),
        ("fisheye lens", "Mắt cá (fisheye)"),
    ],
    "focus": [
        ("shallow depth of field, soft bokeh background", "Xoá phông (DOF mỏng)"),
        ("deep focus, everything sharp", "Nét sâu, nét toàn khung"),
        ("rack focus from foreground to background", "Chuyển nét trước → sau"),
        ("rack focus from background to foreground", "Chuyển nét sau → trước"),
        ("soft focus, dreamy", "Nét mềm, mơ màng"),
    ],
    "composition": [
        ("rule of thirds", "Bố cục 1/3"),
        ("centered symmetrical composition", "Đối xứng chính giữa"),
        ("foreground framing through a doorway or objects", "Khung trong khung (tiền cảnh)"),
        ("leading lines toward the subject", "Đường dẫn hướng"),
        ("negative space around the subject", "Nhiều khoảng trống"),
        ("silhouette against the light", "Bóng đổ ngược sáng (silhouette)"),
        ("reflection in a mirror or water", "Qua phản chiếu (gương, nước)"),
    ],
    "lighting": [
        ("soft natural window light", "Ánh sáng cửa sổ tự nhiên"),
        ("golden hour backlight", "Ngược sáng giờ vàng"),
        ("low-key lighting, deep shadows", "Low-key, nhiều bóng tối"),
        ("high-key lighting, bright and airy", "High-key, sáng trong"),
        ("hard side light, strong contrast", "Sáng gắt một bên, tương phản"),
        ("rim light outlining the subject", "Viền sáng (rim light)"),
        ("neon practical lights, colored glow", "Đèn neon, ánh màu"),
        ("moonlight, cool blue tones", "Ánh trăng, tông xanh lạnh"),
        ("candlelight, warm flicker", "Ánh nến ấm, chập chờn"),
    ],
    "speed": [
        ("real-time speed", "Tốc độ thật"),
        ("slow motion", "Quay chậm (slow motion)"),
        ("time-lapse", "Tua nhanh (time-lapse)"),
        ("speed ramp", "Đổi tốc độ (speed ramp)"),
    ],
}

# Which camera fields describe the still frame and which describe what happens over time.
FRAME_FIELDS = ("shot", "angle", "lens", "focus", "composition", "lighting")
MOTION_FIELDS = ("movement", "speed")
_CAMERA_LABELS = {
    "shot": "Shot", "angle": "Angle", "movement": "Camera movement", "speed": "Speed", "lens": "Lens",
    "focus": "Focus", "composition": "Composition", "lighting": "Lighting", "notes": "Direction",
}


class Camera(BaseModel):
    """Visual direction for one scene: how the camera frames and moves. Every field is optional English text."""
    shot: str | None = Field(default=None, description="Shot size, e.g. wide shot, medium shot, close-up, "
                             "extreme close-up, over-the-shoulder shot, point-of-view shot.")
    angle: str | None = Field(default=None, description="Camera angle, e.g. eye-level angle, low angle, high angle, "
                              "bird's-eye view, dutch angle.")
    movement: str | None = Field(default=None, description="ONE camera move, e.g. static locked-off camera, slow "
                                 "dolly-in, tracking shot following the subject, pan, tilt up, orbit, crane up, handheld.")
    lens: str | None = Field(default=None, description="Lens / focal length, e.g. 24mm wide-angle lens, 85mm portrait "
                             "lens, macro lens, anamorphic lens.")
    focus: str | None = Field(default=None, description="Depth of field / focus, e.g. shallow depth of field, deep "
                              "focus, rack focus from foreground to background.")
    composition: str | None = Field(default=None, description="Framing, e.g. rule of thirds, centered symmetrical "
                                    "composition, foreground framing, leading lines, silhouette.")
    lighting: str | None = Field(default=None, description="Lighting for this shot, e.g. golden hour backlight, "
                                 "low-key lighting, rim light, soft window light.")
    speed: str | None = Field(default=None, description="Playback speed, e.g. slow motion, time-lapse, real-time speed.")
    notes: str | None = Field(default=None, description="Any other direction for the shot (English), e.g. "
                              "the camera stays behind her shoulder the whole time.")

    @model_validator(mode="before")
    @classmethod
    def _blank_to_none(cls, data):
        if isinstance(data, dict):
            return {k: (v.strip() or None) if isinstance(v, str) else v for k, v in data.items()}
        return data

    def is_empty(self) -> bool:
        return not any(getattr(self, f) for f in _CAMERA_LABELS)

    def describe(self, fields: tuple[str, ...] | None = None) -> str:
        """Labelled sentences such as "Shot: close-up, low angle. Camera movement: slow dolly-in.".
        `fields` picks a subset (the free-form notes are always included)."""
        wanted = [f for f in _CAMERA_LABELS if f != "notes" and (fields is None or f in fields)]
        vals = {f: getattr(self, f).strip().rstrip(".") for f in wanted if getattr(self, f)}
        parts: list[str] = []
        framing = ", ".join(vals.pop(f) for f in ("shot", "angle") if f in vals)
        if framing:
            parts.append(f"Shot: {framing}.")
        parts += [f"{_CAMERA_LABELS[f]}: {v}." for f, v in vals.items()]
        if self.notes:
            parts.append(f"Direction: {self.notes.strip().rstrip('.')}.")
        return " ".join(parts)


DIALOGUE_MAX = 12  # lines per scene (a clip is only a few seconds)
SPEAKER_LOOK_WORDS = 40  # words of a character description used to tell who is who
BACKGROUND_NO_WORDS = ("Nobody else says any words: people in the background only cheer, laugh or murmur "
                       "without words, and the line is never shared, echoed or finished by anyone else.")
DIALOGUE_TURNS = ("Each line is spoken exactly once, only by the character named before it; no character repeats, "
                  "echoes or says another character's line, and they never speak at the same time.")


def _line_key(text: str) -> str:
    """Compare lines ignoring case, spacing and punctuation ("Về đi!" == "về đi")."""
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text.casefold()).split())


class DialogueLine(BaseModel):
    """One spoken line in a scene."""
    character: str = Field(description="Exact name of the character who speaks (one of `characters`).")
    line: str = Field(description="The exact words spoken, in the spoken dialogue language. Short: a clip is "
                      "only a few seconds. Never repeat a line another character already said.")
    delivery: str | None = Field(default=None, description="How it is said, in English: emotion, tone, volume, "
                                 "pace, e.g. softly, holding back tears / shouting angrily / whispering.")

    @model_validator(mode="before")
    @classmethod
    def _strip(cls, data):
        if isinstance(data, dict):
            return {k: (" ".join(v.split()) or None) if isinstance(v, str) else v for k, v in data.items()}
        return data


class SceneDraft(BaseModel):
    """A scene without `characters`: the schema the planner sends to Claude (still titled "Scene")."""
    model_config = ConfigDict(title="Scene")

    id: str = Field(description="Short unique id, e.g. s01")
    mode: Mode = Field(
        default="keyframe",
        description="t2v = text-to-video; i2v = animate a local image; keyframe = generate a still with Imagen first, then animate it (best consistency).",
    )
    prompt: str = Field(description="What happens in the clip: the action (put camera work in `camera`).")
    image_prompt: str | None = Field(default=None, description="Still image prompt for mode=keyframe (defaults to prompt).")
    image: str | None = Field(default=None, description="Start frame path for mode=i2v.")
    end_image: str | None = Field(default=None, description="Optional end frame path for mode=i2v.")
    duration: int | None = Field(default=None, description="Seconds; omit for Flow's default.")
    model: str | None = Field(
        default=None,
        description="Optional video model alias for this scene only; normally leave empty to use the storyboard model.",
    )
    narration: str | None = Field(default=None, description="Optional line shown as a subtitle in the final film.")
    camera: Camera | None = Field(default=None, description="Visual direction: shot size, angle, camera movement, "
                                  "lens, focus, composition, lighting, speed. Added to the prompts automatically.")
    subtitles: Subtitles | None = Field(default=None, description="The `narration` line in Vietnamese, Thai and "
                                        "English: the film always has these three subtitle tracks.")
    location: str | None = Field(default=None, description="Exact name of the place (one of `locations`) where "
                                 "this scene happens. Ignored for mode=i2v.")
    continue_previous: bool | None = Field(default=None, description="false after a time jump: do not continue "
                                           "from the previous scene's frame. Omit otherwise.")
    content_direction: str | None = Field(default=None, description="Content direction (English): what this scene "
                                          "must make the audience understand and feel, the emotional beat, and how "
                                          "each character acts and reacts. Added to the prompts automatically.")
    dialogue: list[DialogueLine] | None = Field(default=None, description="Spoken lines in the order they are "
                                                "said, one speaker per line. Each line is said once by one "
                                                "character only: no character repeats or echoes another's line, "
                                                "and no line repeats an earlier scene. Empty when nobody speaks.")

    @field_validator("location", mode="before")
    @classmethod
    def _blank_location(cls, v: object) -> object:
        return (v.strip() or None) if isinstance(v, str) else v

    @field_validator("content_direction", mode="before")
    @classmethod
    def _clean_content(cls, v: object) -> object:
        return (v.strip() or None) if isinstance(v, str) else v

    @field_validator("dialogue", mode="before")
    @classmethod
    def _clean_dialogue(cls, v: object) -> object:
        """Drop blank rows (the editor adds empty ones); [] -> None so old storyboards stay identical."""
        if not isinstance(v, list):
            return v
        rows = [r for r in v if not (isinstance(r, dict) and not str(r.get("line") or "").strip()
                                     and not str(r.get("character") or "").strip())]
        return rows or None

    @model_validator(mode="after")
    def _check(self) -> "SceneDraft":
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.id):
            raise ValueError(f"scene id '{self.id}' may only contain letters, digits, - and _")
        if self.mode == "i2v" and not self.image:
            raise ValueError(f"scene {self.id}: mode=i2v needs 'image'")
        if self.dialogue and len(self.dialogue) > DIALOGUE_MAX:
            raise ValueError(f"scene {self.id}: at most {DIALOGUE_MAX} dialogue lines")
        return self

    def spoken_lines(self) -> list[DialogueLine]:
        """Dialogue rows that have both a speaker and words."""
        return [d for d in self.dialogue or [] if d.character and d.line]


class Scene(SceneDraft):
    characters: list[str] | None = Field(
        default=None, description="Names of characters in this scene (overrides name detection).")
    # Basic formats only (not in the drama planner's schema): the parts of the image-to-video prompt, joined by
    # i2v_prompt.assemble with the house I2V templates. `prompt` keeps the action as a plain summary.
    video_shot: VideoShot | None = Field(default=None, description="Structured image-to-video prompt (basic formats).")
    screen_time: float | None = Field(default=None, ge=0.5, le=60, description="Seconds this scene stays on screen "
                                      "in the final cut (films made from a reference video): the clip is rendered "
                                      "at its usual length and the editor trims it to this. Omit to let the "
                                      "editor decide.")

    @field_validator("video_shot", mode="after")
    @classmethod
    def _empty_shot(cls, v: VideoShot | None) -> VideoShot | None:
        return None if v is None or v.is_empty() else v

    @field_validator("characters")
    @classmethod
    def _dedupe_characters(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        out: list[str] = []
        seen: set[str] = set()
        for name in v:
            name = name.strip()
            if name.casefold() not in seen:
                seen.add(name.casefold())
                out.append(name)
        return out


class StoryboardDraft(BaseModel):
    """A storyboard whose scenes have no `characters`: the planner's output schema (still titled "Storyboard")."""
    model_config = ConfigDict(title="Storyboard")

    title: str
    aspect: Aspect = "16:9"
    model: str | None = Field(default=None, description="Default gflow video model alias for all scenes, e.g. veo-lite. Empty = Flow picks.")
    image_model: str = "nano2"
    style: str = Field(default="", description="Visual style appended to every prompt.")
    project_id: str | None = Field(default=None, description="Reuse an existing Flow project.")
    characters: list[Character] = Field(default_factory=list)
    # Right after `characters`: the schema order is the order Claude writes in, so places are defined before
    # the scenes that use them (and `save_storyboard` writes them before the long scene list).
    locations: list[Location] | None = Field(default=None, description="Every place that appears in the film, "
                                             "described once; scenes refer to it by name in `location`.")
    continuity: bool | None = Field(default=None, description="false = never start a scene's keyframe from the "
                                    "previous scene's keyframe. Omit otherwise.")
    thumbnail: Thumbnail = Field(default_factory=Thumbnail)
    use_character_refs: bool = Field(default=True, description="Send character reference images with each generation.")
    no_text: bool = Field(default=True, description="Tell Flow not to put captions, subtitles or any text in "
                          "images and videos (subtitles come from `narration` when the film is assembled).")
    fallback_t2v: bool = Field(default=False, description="If the Flow account cannot do image-to-video, render "
                               "keyframe scenes as text-to-video instead of stopping.")
    scenes: list[SceneDraft]

    @model_validator(mode="after")
    def _unique_ids(self) -> "StoryboardDraft":
        ids = [s.id for s in self.scenes]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate scene ids: {sorted(dupes)}")
        if not self.scenes:
            raise ValueError("storyboard has no scenes")
        return self


class Storyboard(StoryboardDraft):
    scenes: list[Scene]  # same position as in StoryboardDraft
    # Only on Storyboard (not StoryboardDraft), so they are not in the schema sent to Claude.
    dialogue_language: str | None = Field(default=None, description="Language every character speaks in the videos "
                                          "(English name, e.g. Vietnamese), or 'none' for no spoken dialogue. "
                                          "Empty = Veo guesses.")
    subtitle_language: str | None = Field(default=None, description="Language of `narration` chosen when planning, "
                                          "or 'none' for a film without subtitles. Never sent to Flow.")
    edit_style: str | None = Field(default=None, description="How to cut this film, learnt from a reference video "
                                   "(Phân tích video): rhythm, transitions, look, music and sound. The editor agent "
                                   "follows it. Never sent to Flow.")
    # Chosen in "Phim mới". Empty `format` = short_drama (every project made before formats existed).
    format: str | None = Field(default=None, description="Film format: short_drama (full drama flow: content "
                               "direction, camera, dialogue, locations) or feature / series / short_film / clip "
                               "(basic flow: picture -> video). Never sent to Flow.")
    audio: str | None = Field(default=None, description="Basic formats: dialogue, silent, voiceover, onscreen_text "
                              "or music. Sets the sound sentence added to every video prompt.")
    visual_style: str | None = Field(default=None, description="Visual style picked when planning (realistic, 2d, "
                                     "3d, anime, other); its sentence is already in `style`.")
    genres: list[str] | None = Field(default=None, description="Genres picked when planning (family, romance...).")
    ai_model: str | None = Field(default=None, description="AI model used for storyboard, review, QC, editing and "
                                 "chat in this project: 'claude' (default) or 'gemini-pro'. Never sent to Flow.")

    @field_validator("ai_model", mode="before")
    @classmethod
    def _ai_model(cls, v: object) -> str | None:
        return clean_ai_model(v)

    @property
    def effective_ai_model(self) -> str:
        """AI model for this project ('claude' when unset)."""
        return self.ai_model or DEFAULT_AI_MODEL

    @field_validator("format", "audio", "visual_style", mode="before")
    @classmethod
    def _choice(cls, v: object, info) -> str | None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        allowed = {"format": formats.FORMATS, "audio": formats.AUDIO, "visual_style": formats.VISUALS}[info.field_name]
        if not isinstance(v, str) or v.strip() not in allowed:
            raise ValueError(f"must be one of: {', '.join(allowed)}")
        return v.strip()

    @field_validator("genres", mode="before")
    @classmethod
    def _genres(cls, v: object) -> list[str] | None:
        return formats.clean_genres(v) or None

    @property
    def basic(self) -> bool:
        """Basic text -> image -> video flow (I2V prompt guide): camera, content direction and dialogue rows are not used;
        locations are (described once, their look added to the picture, the previous keyframe reused)."""
        return formats.is_basic(self.format)

    def spoken_language(self) -> str | None:
        """Language of the words spoken in a basic film (None when nobody speaks / not set)."""
        return self.dialogue_language if self.dialogue_language and self.speaks() else None

    def audio_rule(self) -> str:
        """Basic formats: the sound sentence for every video prompt ("" for the drama)."""
        if not self.basic:
            return ""
        lang = self.dialogue_language if self.dialogue_language and self.speaks() else None
        return formats.audio_rule(self.audio, lang)

    @field_validator("dialogue_language", mode="before")
    @classmethod
    def _dialogue_language(cls, v: object) -> str | None:
        return clean_dialogue_language(v)

    @field_validator("subtitle_language", mode="before")
    @classmethod
    def _subtitle_language(cls, v: object) -> str | None:
        v = clean_language(v)
        return NO_SUBTITLES if v is not None and v.casefold() == NO_SUBTITLES else v

    @property
    def has_subtitles(self) -> bool:
        """False when the film was made without subtitles ("Không phụ đề"): no tracks, no .srt, nothing burnt in."""
        return self.subtitle_language != NO_SUBTITLES

    @model_validator(mode="after")
    def _dialogue_subtitles(self) -> "Storyboard":
        """Basic films with spoken dialogue: the subtitle IS the speaker's line, nothing else. A scene without a
        `speaker` has no subtitle: a free `narration` there read like dialogue but never reached the video prompt.
        Translations are kept while the line is unchanged and dropped (re-translated when editing) otherwise."""
        if not (self.basic and self.audio == "dialogue"):
            return self
        for s in self.scenes:
            sp = s.video_shot.speaker if s.video_shot else None
            line = " ".join(str(sp.line or "").split()) if sp else ""
            if line == " ".join(str(s.narration or "").split()):
                if not line:
                    s.narration, s.subtitles = None, None
                continue
            s.narration, s.subtitles = (line or None), None
        return self

    @model_validator(mode="after")
    def _check_scene_characters(self) -> "Storyboard":
        if all(s.characters is None for s in self.scenes):
            return self  # old storyboards: duplicate / empty names stay allowed
        bad: list[str] = []
        seen: set[str] = set()
        for c in self.characters:
            key = c.name.strip().casefold()
            if not key or key in seen:
                label = repr(c.name.strip()) if not key else repr(next(
                    o.name for o in self.characters if o.name.strip().casefold() == key))
                if label not in bad:
                    bad.append(label)
            seen.add(key)
        if bad:
            raise ValueError("character names must be unique and non-empty when scenes list characters: "
                             + ", ".join(bad))
        for s in self.scenes:
            for name in s.characters or []:
                if name.casefold() not in seen:
                    raise ValueError(f"scene {s.id}: unknown character '{name}' in characters")
        return self

    @model_validator(mode="after")
    def _check_locations(self) -> "Storyboard":
        users = [s for s in self.scenes if s.mode != "i2v" and s.location]
        if not users:
            return self  # no scene uses a location: blank / duplicate names stay allowed (still being edited)
        bad: list[str] = []
        seen: set[str] = set()
        for loc in self.locations or []:
            key = self._loc_key(loc.name)
            if not key or key in seen:
                if not key:
                    label = repr(loc.name.strip())
                else:  # the duplicated name, with the scenes that use it
                    label = repr(next(o.name for o in self.locations or [] if self._loc_key(o.name) == key))
                    ids = [x.id for x in users if self._loc_key(x.location) == key]
                    if ids:
                        label += f" (scenes {', '.join(ids)})"
                if label not in bad:
                    bad.append(label)
            seen.add(key)
        if bad:
            raise ValueError(f"location names must be unique and non-empty when scenes use a location "
                             f"(scenes {', '.join(x.id for x in users)} use one): " + ", ".join(bad))
        for s in users:
            if self._loc_key(s.location) not in seen:
                raise ValueError(f"scene {s.id}: unknown location '{s.location}'")
        return self

    # --- locations ---------------------------------------------------------

    @staticmethod
    def _loc_key(name: str | None) -> str:
        return (name or "").strip().casefold()

    def find_location(self, name: str | None) -> Location | None:
        key = self._loc_key(name)
        if not key:
            return None
        return next((loc for loc in self.locations or [] if self._loc_key(loc.name) == key), None)

    def scene_location(self, scene: SceneDraft) -> Location | None:
        """The location the scene is set in; None for i2v scenes (they ignore `location`) or without one.
        The only definition of a scene's "effective" location."""
        if scene.mode == "i2v" or not scene.location:
            return None
        return self.find_location(scene.location)

    @staticmethod
    def location_image(loc: Location) -> Path | None:
        return Path(loc.image) if loc.image and Path(loc.image).is_file() else None

    def location_prompt(self, loc: Location) -> str:
        if loc.image_prompt and loc.image_prompt.strip():
            base = loc.image_prompt.strip().rstrip(".") + "."
        else:
            base = (f"Location reference image: {loc.description.strip().rstrip('.')}. "
                    "Empty set with no people, wide establishing view at eye level, furniture, props and light "
                    "sources clearly visible, sharp detail.")
        if self.style.strip():
            base += f" Style: {self.style.strip().rstrip('.')}."
        return base

    def _setting(self, scene: Scene) -> str:
        loc = self.scene_location(scene)
        desc = loc.description.strip() if loc else ""
        return f"Setting: {desc.rstrip('.')}." if desc else ""

    # --- prompt building -------------------------------------------------

    def _characters_in(self, text: str) -> list[Character]:
        return [c for c in self.characters if re.search(rf"\b{re.escape(c.name)}\b", text, re.IGNORECASE)]

    def _compose(self, text: str, direction: str = "", chars: list[Character] | None = None,
                 setting: str = "") -> str:
        """`chars` = characters whose description is added; None = those named in `text`.
        `setting` (the location sentence) goes after the direction, before the characters."""
        parts = [text.strip().rstrip(".") + "."]
        if direction:
            parts.append(direction)
        if setting:
            parts.append(setting)
        if chars is None:
            chars = self._characters_in(text)
        if chars:  # basic formats: no "look.." double stop (the drama keeps its text: fingerprints)
            parts.append(" ".join(f"{c.name}: {c.description.strip().rstrip('.') if self.basic else c.description.strip()}."
                                  for c in chars))
        if self.style.strip():
            parts.append(f"Style: {self.style.strip().rstrip('.')}.")
        return " ".join(parts)

    def for_flow(self, prompt: str) -> str:
        """The prompt as actually sent to Flow. Not part of the fingerprints, so turning this on or
        off does not mark finished scenes for re-rendering. Children are always described in the wording Google's
        child-safety filter lets through (`i2v_prompt.child_safe`: no exact ages or body details)."""
        prompt = i2v_prompt.child_safe(prompt)
        if not self.no_text or NO_TEXT in prompt:
            return prompt
        if self.basic:  # I2V guide rule 6: one short closing sentence, not a long list of negatives
            if i2v_prompt.NO_TEXT_IMAGE in prompt:
                return prompt
            base = prompt.rstrip()
            if base and base[-1] not in ".!?":
                base += "."
            return f"{base} {i2v_prompt.NO_TEXT_IMAGE}" if base else i2v_prompt.NO_TEXT_IMAGE
        base = prompt.rstrip()
        if base and base[-1] not in ".!?":
            base += "."
        return f"{base} {NO_TEXT}" if base else NO_TEXT

    def dialogue_rule(self) -> str:
        """The sentence that fixes the spoken language ("" when `dialogue_language` is not set)."""
        lang = self.dialogue_language
        if not lang:
            return ""
        return NO_DIALOGUE_RULE if lang.casefold() == NO_DIALOGUE else DIALOGUE_RULE.format(lang=lang)

    def _dialogue_cast(self, scene: Scene) -> tuple[list[str], list[str]]:
        """(speakers in the order they first speak, everyone in the scene including the speakers) for a
        keyframe / image-to-video scene with dialogue; ([], []) otherwise."""
        if scene.mode not in ("keyframe", "i2v") or not self.speaks():
            return [], []
        speakers: list[str] = []
        for d in scene.spoken_lines():
            if d.character.strip() and d.character.strip().casefold() not in {x.casefold() for x in speakers}:
                speakers.append(d.character.strip())
        if not speakers:
            return [], []
        cast = [c.name.strip() for c in self.scene_characters(scene)]
        for n in speakers:
            if n.casefold() not in {x.casefold() for x in cast}:
                cast.append(n)
        return speakers, cast

    def speaker_cues(self, scene: Scene) -> str:
        """Who is who for the dialogue of a keyframe / image-to-video scene ("" when there is none).
        There the prompt only names the characters, and Veo, which sees a picture and no names, gave a line to
        whoever faced the camera (the mother said her son's line). So: what each one looks like, the speaker
        faces the camera with moving lips, the others keep their mouths closed."""
        speakers, cast = self._dialogue_cast(scene)
        if not speakers:
            return ""
        by_name = {c.name.strip().casefold(): c for c in self.characters if c.name.strip()}
        looks = []
        for n in cast:
            c = by_name.get(n.casefold())
            if c and c.description.strip():
                words = c.description.strip().rstrip(".").split()
                look = " ".join(words[:SPEAKER_LOOK_WORDS]).rstrip(",;")
                if words[0] in ("A", "An", "The"):
                    look = look[0].lower() + look[1:]
                looks.append(f"{n} is {look}")
        silent = [n for n in cast if n.casefold() not in {x.casefold() for x in speakers}]
        parts = [f"Who is who in the picture: {'; '.join(looks)}."] if looks else []
        who = " and ".join(speakers)
        parts.append(f"Only {who} speak{'s' if len(speakers) == 1 else ''}: while speaking, "
                     f"{'the speaker turns' if len(speakers) > 1 else who + ' turns'} the face toward the camera "
                     "and the lips visibly move with the words.")
        if silent:
            parts.append(f"{' and '.join(silent)} {'keeps' if len(silent) == 1 else 'keep'} the mouth closed the "
                         "whole time and only listen" + ("s." if len(silent) == 1 else "."))
        # Ambient "chatter" or a cheering crowd made Veo give part of the line to a listener (s05: the coach
        # mouthed the boy's "Thầy ơi…"): the background makes sounds, never words.
        parts.append(BACKGROUND_NO_WORDS)
        return " ".join(parts)

    def keyframe_staging(self, scene: Scene) -> str:
        """How the opening frame of a dialogue scene is staged ("" when it has none). Veo gives the line to the
        biggest face it sees talking, so the first speaker is the main face, toward the camera, and everyone
        else is smaller with the mouth closed. A listener in the foreground with an open-mouthed grin was made
        to say the speaker's line."""
        speakers, cast = self._dialogue_cast(scene)
        if not speakers:
            return ""
        first = speakers[0]
        others = [n for n in cast if n.casefold() != first.casefold()]
        text = (f"Staging for the dialogue that follows: {first} is the main subject, in the foreground with the "
                "largest, closest face, turned toward the camera (front or three-quarter view), face and mouth "
                "clearly visible.")
        if others:
            many = len(others) > 1
            text += (f" {' and '.join(others)} {'are' if many else 'is'} smaller in the frame or further back, "
                     f"turned toward {first}, mouth closed (a closed-mouth smile at most), listening.")
        text += " Anyone in the background is small, with the mouth closed."
        return text

    def for_flow_keyframe(self, prompt: str, scene: Scene | None = None) -> str:
        """A keyframe prompt as actually sent to Flow: the dialogue staging (`keyframe_staging`), then
        `for_flow`. Not part of the fingerprints, so adding it remakes nothing that is already made."""
        staging = self.keyframe_staging(scene) if scene is not None and not self.basic else ""
        if staging and "Staging for the dialogue" not in prompt:
            base = prompt.rstrip()
            if base and base[-1] not in ".!?":
                base += "."
            prompt = f"{base} {staging}" if base else staging
        return self.for_flow(prompt)

    def _speech_in_prompt(self, prompt: str) -> bool:
        """Speech written straight into the prompt ("Minh: …", "Lan says hello", "they talk"): such a scene does
        have words even with no dialogue lines, so it keeps the language rule. Singing is not talking: s10 of
        "18 ngày" ("boys softly singing") got the language rule and Veo made up four lines."""
        if re.search(r"\b(?:say|says|said|saying|ask|asks|asked|whisper|whispers|whispered|shout|shouts|shouted|"
                     r"reply|replies|replied|speak|speaks|spoke|speaking|talk|talks|talking|"
                     r"call|calls|calling out|chat|chats|chatting|tell|tells|told)\b", prompt, re.IGNORECASE):
            return True
        names = [re.escape(c.name.strip()) for c in self.characters if c.name.strip()]
        return bool(names) and bool(re.search(rf"\b(?:{'|'.join(names)})\b(?:\s*\([^)]*\))?\s*:", prompt))

    def for_flow_video(self, prompt: str, scene: Scene | None = None) -> str:
        """A video prompt as actually sent to Flow: who-is-who for the dialogue (`speaker_cues`, when the scene is
        given), the spoken-language sentence, then `for_flow` (NO_TEXT).
        None of these are part of the fingerprints, so adding them re-renders nothing."""
        if self.basic and i2v_prompt.CLOSING in prompt:  # a structured I2V prompt is complete as it is
            return prompt
        if self.basic:  # one sound sentence, nothing from the drama flow
            rule = self.audio_rule()
            if not rule or rule.rstrip(".") in prompt:
                return self.for_flow(prompt)
            base = prompt.rstrip()
            if base and base[-1] not in ".!?":
                base += "."
            return self.for_flow(f"{base} {rule}" if base else rule)
        cues = self.speaker_cues(scene) if scene is not None else ""
        if cues and "Who is who in the picture" not in prompt and "lips visibly move" not in prompt:
            if NO_TEXT in prompt:
                prompt = prompt.replace(NO_TEXT, f"{cues} {NO_TEXT}", 1)
            else:
                base = prompt.rstrip()
                if base and base[-1] not in ".!?":
                    base += "."
                prompt = f"{base} {cues}" if base else cues
        rule = self.dialogue_rule()
        prompt = OLD_DIALOGUE_RULE.sub(lambda _: rule, prompt)
        if (scene is not None and self.speaks() and rule and not scene.spoken_lines()
                and rule.rstrip(".") not in prompt
                and not self._speech_in_prompt(prompt.replace(SILENT_SCENE_RULE, ""))):
            rule = SILENT_SCENE_RULE
        if not rule or rule.rstrip(".") in prompt:  # already there (pasted by the user, maybe without the dot)
            return self.for_flow(prompt)
        if NO_TEXT in prompt:  # keep the rule before NO_TEXT
            return self.for_flow(prompt.replace(NO_TEXT, f"{rule} {NO_TEXT}", 1))
        base = prompt.rstrip()
        if base and base[-1] not in ".!?":
            base += "."
        return self.for_flow(f"{base} {rule}" if base else rule)

    def portrait_prompt(self, c: Character) -> str:
        if not self.basic and i2v_prompt.is_minor(c.description):
            # A child's reference picture: never the studio "full body, facing the camera" pose (Google's
            # child-safety filter blocks it), even when the planner wrote one; the look comes from the description.
            # Not part of any fingerprint: an existing portrait is not remade.
            return i2v_prompt.child_portrait(c.description, self.style)
        if self.basic and not (c.image_prompt and c.image_prompt.strip()):
            # A natural everyday setting, not "full body on a plain studio background": that framing of a child
            # is blocked by Google's child-safety filter (and no exact ages: child_safe).
            # A film still of an everyday moment, the person DOING something (tested on Flow with a child: the
            # studio "reference image" wording and a posed "smiling in a home" were both blocked; helping to set
            # the table passed).
            desc = i2v_prompt.child_safe(c.description.strip().rstrip("."))
            if not re.match(r"(?i)(?:a|an|the)\b", desc):
                desc = f"A cheerful {desc}"
            who = ("she" if re.search(r"(?i)\b(?:girl|schoolgirl|woman|lady|mother|grandmother|grandma|sister|she|her)\b", desc)
                   else "he" if re.search(r"(?i)\b(?:boy|schoolboy|man|father|grandfather|grandpa|brother|he|his)\b", desc)
                   else "they")
            base = (f"{desc}, smiling as {who} {'help' if who == 'they' else 'helps'} set the kitchen table in a "
                    "bright, everyday home. Medium shot, face clearly visible, softly blurred background, soft "
                    "natural light, photorealistic film still.")
            if self.style.strip():
                base += f" Style: {self.style.strip().rstrip('.')}."
            return i2v_prompt.child_safe(i2v_prompt.soften_identical(base))
        if c.image_prompt and c.image_prompt.strip():
            base = c.image_prompt.strip().rstrip(".") + "."
        else:
            base = (f"Character reference image of {c.name}: {c.description.strip().rstrip('.')}. "
                    "Full body, standing, facing the camera, face clearly visible, neutral expression, "
                    "plain light grey studio background, soft even lighting, sharp detail.")
        if self.style.strip():
            base += f" Style: {self.style.strip().rstrip('.')}."
        return i2v_prompt.child_safe(i2v_prompt.soften_identical(base)) if self.basic else base

    def thumbnail_prompt(self) -> str:
        """Cover picture prompt (the title is printed later, not drawn by the model)."""
        p = (self.thumbnail.prompt or "").strip()
        if not p:
            names = [c.name for c in self.characters if c.name.strip()][:MAX_REFS]
            who = (", ".join(names) + " in") if names else "The main characters in"
            p = (f"{who} the most dramatic, emotional moment of the story. Cinematic movie-poster composition, "
                 "expressive faces in close-up, strong rim light, high contrast, rich colors, "
                 "a clean darker area in the lower third")
        return self._compose(p)

    def thumbnail_refs(self) -> list[tuple[str, Path]]:
        if not self.use_character_refs:
            return []
        # No cap: on the Flow chat page every cover character is attached by name; the CLI path trims to the
        # image model's --ref cap at send time (Pipeline._cap_cli_refs), as for scene keyframes.
        return [(c.name, Path(c.image)) for c in self._characters_in(self.thumbnail_prompt())
                if c.image and Path(c.image).is_file()]

    def scene_text(self, scene: Scene) -> str:
        """Text whose character names decide which references a scene gets (speakers count too)."""
        text = f"{scene.image_prompt or ''} {scene.prompt}" if scene.mode == "keyframe" else scene.prompt
        speakers = [d.character for d in scene.spoken_lines()]
        return f"{text} {' '.join(speakers)}" if speakers else text

    def auto_characters(self, scene: Scene) -> list[Character]:
        """Characters found by name in the scene text (what an old storyboard gets)."""
        return self._characters_in(self.scene_text(scene))

    def scene_characters(self, scene: Scene) -> list[Character]:
        """Characters of a scene: its `characters` list if set (in storyboard order), else the detected ones."""
        if scene.characters is None:
            return self.auto_characters(scene)
        wanted = {n.casefold() for n in scene.characters}
        return [c for c in self.characters if c.name.strip().casefold() in wanted]

    def _prompt_characters(self, scene: Scene) -> list[Character] | None:
        """Characters to describe in the scene's prompts; None = detect names in each prompt text (old behaviour)."""
        if scene.characters is None:
            return self.auto_characters(scene) if scene.spoken_lines() else None
        return self.scene_characters(scene)

    def speaks(self) -> bool:
        """False when the film is set to "no spoken dialogue" (then scene dialogue is left out of the prompts)."""
        return (self.dialogue_language or "").casefold() != NO_DIALOGUE

    def performance(self, scene: Scene) -> str:
        """Content direction and dialogue as video prompt text ("" when the scene has neither).
        Lines use `Name says: words` without quotation marks (Veo burns quoted text into the picture)."""
        parts: list[str] = []
        if scene.content_direction:
            parts.append(f"Content direction: {scene.content_direction.strip().rstrip('.')}.")
        lines: list[DialogueLine] = []
        seen: set[str] = set()
        for d in scene.spoken_lines() if self.speaks() else []:
            key = _line_key(d.line)
            if key and key not in seen:  # a line repeated in the same scene is never sent (the editor warns)
                seen.add(key)
                lines.append(d)
        if lines:
            said = []
            for i, d in enumerate(lines):
                how = f" ({d.delivery.strip().rstrip('.')})" if d.delivery else ""
                words = d.line.strip()
                if words[-1] not in ".!?…":
                    words += "."
                said.append(f"{'Then ' if i else ''}{d.character}{how} says: {words}")
            order = "in this order" if len(lines) > 1 else "only this line"
            parts.append(f"Dialogue, spoken one character at a time, {order}: " + " ".join(said))
            parts.append(DIALOGUE_TURNS)
            speakers = {d.character.casefold() for d in lines}
            silent = [c.name for c in self.scene_characters(scene) if c.name.strip().casefold() not in speakers]
            if silent:
                parts.append(f"{' and '.join(silent)} {'stays' if len(silent) == 1 else 'stay'} silent.")
        return " ".join(parts)

    def dialogue_warnings(self) -> dict[str, list[str]]:
        """Problems with the dialogue, per scene id (Vietnamese, for the editor and `filmmaker check`):
        a line said twice (in the scene or in an earlier scene), unknown speaker, speaker not in the scene."""
        out: dict[str, list[str]] = {}
        if self.basic:  # the I2V prompt guide instead (word count, names, missing camera / action...)
            names = [c.name for c in self.characters]
            for sc in self.scenes:
                if sc.video_shot is not None:
                    w = i2v_prompt.warnings(sc.video_shot, audio_mode=self.audio, narration=sc.narration, names=names)
                    if w:
                        out[sc.id] = w
            if self.audio == "dialogue":
                for sid in i2v_prompt.axis_breaks([(sc.id, sc.video_shot) for sc in self.scenes
                                                   if sc.video_shot is not None]):
                    out.setdefault(sid, []).append("Sai trục 180°: người đáp ở cảnh này phải đứng phía khung hình đối "
                                                   "diện và nhìn ngược hướng với người nói ở cảnh trước.")
            return out
        known = {c.name.strip().casefold() for c in self.characters if c.name.strip()}
        said: dict[str, str] = {}  # line key -> "scene id / speaker" that said it first
        for s in self.scenes:
            msgs: list[str] = []
            in_scene = {c.name.strip().casefold() for c in self.scene_characters(s)} if s.characters is not None else None
            for d in s.spoken_lines():
                who = d.character.strip()
                if who.casefold() not in known:
                    msgs.append(f"“{who}” không có trong danh sách nhân vật")
                elif in_scene is not None and who.casefold() not in in_scene:
                    msgs.append(f"{who} có thoại nhưng chưa được chọn vào cảnh")
                key = _line_key(d.line)
                if not key:
                    continue
                if key in said:
                    msgs.append(f"{who} nói lặp câu “{d.line}” (đã có ở {said[key]})")
                else:
                    said[key] = f"cảnh {s.id}, {who}"
            if s.spoken_lines() and not self.speaks():
                msgs.append("phim đang đặt Không thoại nên lời thoại cảnh này bị bỏ qua")
            if msgs:
                out[s.id] = msgs
        return out

    def _char_ref_candidates(self, scene: Scene) -> list[tuple[str, Path]]:
        """(name, image) of characters with a reference picture on disk. For every mode."""
        if not self.use_character_refs:
            return []
        return [(c.name, Path(c.image)) for c in self.scene_characters(scene)
                if c.image and Path(c.image).is_file()]

    def scene_char_refs(self, scene: Scene) -> list[tuple[str, Path]]:
        """(name, image) of every character picked for this scene that is part of the scene's references
        (location-aware reference list, fingerprint, refs_signature). All of them are sent: characters are made
        as Flow characters and attached by name (the picked-on-Board flow), not uploaded one picture at a time,
        so a scene is no longer capped at a few reference images.

        An i2v scene already starts from a provided frame, so its reference list / fingerprint keep NOT counting
        the characters (that contract is unchanged). To still attach the picked characters when an i2v clip is
        rendered through the Flow chat page, use `scene_ui_char_refs`."""
        if scene.mode == "i2v":
            return []
        return self._char_ref_candidates(scene)

    def scene_ui_char_refs(self, scene: Scene) -> list[tuple[str, Path]]:
        """(name, image) of every character picked for this scene that has a reference picture — for EVERY mode,
        i2v included. Used only when a clip is rendered on the Flow chat composer (the characters are attached
        by name, with no 3-image cap). Never part of scene_refs / refs_signature / the scene fingerprint, so
        attaching characters to an i2v clip does not change the i2v reference contract."""
        return self._char_ref_candidates(scene)

    def scene_ref_items(self, scene: Scene, prev: tuple[str, Path] | None = None) -> list[RefItem]:
        """Every reference image of the scene in sending order. With a location: slot 1 is the previous
        scene's keyframe (`prev` = (id, path), keyframe scenes only) or else the location's picture (or nothing),
        then all the scene's characters. Without one: only the characters, as before."""
        chars = [RefItem("character", n, p) for n, p in self.scene_char_refs(scene)]
        loc = self.scene_location(scene)
        if loc is None:
            return chars
        if prev is not None and scene.mode == "keyframe":
            return [RefItem("keyframe", f"keyframe {prev[0]}", Path(prev[1])), *chars]
        img = self.location_image(loc)
        return ([RefItem("location", f"bối cảnh {loc.name}", img)] if img else []) + chars

    def scene_refs(self, scene: Scene) -> list[tuple[str, Path]]:
        """(label, image) sent with the scene without chaining: the location picture (if any) and the characters
        that have a reference image on disk. Never the previous keyframe."""
        return [(it.label, it.path) for it in self.scene_ref_items(scene)]

    def refs_signature(self, scene: Scene) -> list[str]:
        """Changes when a reference image is added, removed or regenerated. The location picture is
        "loc:<sha>" (no name, so renaming changes nothing); the previous keyframe is never part of it."""
        sig: list[str] = []
        loc = self.scene_location(scene)
        img = self.location_image(loc) if loc else None
        # file_sha: the same sha256 as before, cached (a vanished file reads as "" like an unreadable one used to fail)
        if img is not None:
            sig.append(f"loc:{(file_sha(img) or '')[:12]}")
        return sig + [f"{n}:{(file_sha(p) or '')[:12]}" for n, p in self.scene_char_refs(scene)]

    @staticmethod
    def direction(scene: Scene, fields: tuple[str, ...] | None = None) -> str:
        """The scene's camera direction as prompt text ("" when it has none)."""
        return scene.camera.describe(fields) if scene.camera and not scene.camera.is_empty() else ""

    @staticmethod
    def _join(*parts: str) -> str:
        return " ".join(p for p in parts if p)

    def video_prompt(self, scene: Scene) -> str:
        if self.basic:  # the motion only (the look is in the picture); t2v also gets characters and style
            if scene.video_shot is not None:
                return i2v_prompt.assemble(scene.video_shot, audio_mode=self.audio, language=self.spoken_language(),
                                           narration=scene.narration)
            if scene.mode == "t2v":
                return self._compose(scene.prompt, "", self._prompt_characters(scene))
            return scene.prompt.strip()
        perf = self.performance(scene)
        if scene.mode == "t2v":
            return self._compose(scene.prompt, self._join(self.direction(scene), perf), self._prompt_characters(scene),
                                 setting=self._setting(scene))
        # In keyframe/i2v mode the look and framing are already in the image; keep the prompt on the motion.
        motion = self.direction(scene, MOTION_FIELDS + ("focus",))
        action = scene.prompt.strip().rstrip(".")
        if not motion and not perf:
            return scene.prompt.strip()
        return self._join(f"{action}." if action else "", motion, perf)

    def text_only_prompt(self, scene: Scene) -> str:
        """Text-to-video prompt for a keyframe scene when image-to-video is unavailable:
        the opening-frame description plus the motion, with characters and style."""
        direction = "" if self.basic else self._join(self.direction(scene), self.performance(scene))
        setting = self._setting(scene)
        if scene.image_prompt and scene.image_prompt.strip():
            return self._compose(f"{scene.image_prompt.strip().rstrip('.')}. {scene.prompt.strip()}",
                                 direction, self._prompt_characters(scene), setting=setting)
        return self._compose(scene.prompt, direction, self._prompt_characters(scene), setting=setting)

    def image_prompt(self, scene: Scene) -> str:
        # A still has no motion: only the framing, lens, focus, composition and lighting apply.
        # The content direction sets the faces and body language of the opening frame; dialogue is audio only.
        if self.basic:  # text -> picture: the scene's still, its characters and the style (no aspect / seconds)
            text = scene.image_prompt or scene.prompt
            staging = (i2v_prompt.first_frame_staging(scene.video_shot)
                       if self.audio == "dialogue" and scene.video_shot is not None else "")
            return i2v_prompt.child_safe(i2v_prompt.soften_identical(
                self._compose(i2v_prompt.strip_technical(text), staging, self._prompt_characters(scene),
                              setting=self._setting(scene))))
        content = f"Content direction: {scene.content_direction.rstrip('.')}." if scene.content_direction else ""
        return self._compose(scene.image_prompt or scene.prompt,
                             self._join(self.direction(scene, FRAME_FIELDS), content),
                             self._prompt_characters(scene), setting=self._setting(scene))

    # --- subtitles (three tracks: vi / th / en) ---

    def primary_subtitle_code(self) -> str:
        """Track language `narration` is written in: the planning subtitle language, else the track whose
        line equals `narration`, else Vietnamese."""
        code = subtitle_code(self.subtitle_language)
        if code:
            return code
        for s in self.scenes:
            n = " ".join((s.narration or "").split())
            if n and s.subtitles:
                for c in SUBTITLE_CODES:
                    if getattr(s.subtitles, c) == n:
                        return c
        return "vi"

    def subtitle_line(self, scene: SceneDraft, code: str) -> str | None:
        """The scene's line for track `code`. A missing translation falls back to `narration`,
        so every track has a line wherever the scene has narration. The primary track is `narration` itself.
        Always None for a film without subtitles."""
        if not self.has_subtitles:
            return None
        narration = scene.narration.strip() if scene.narration and scene.narration.strip() else None
        if code == self.primary_subtitle_code() and narration:
            return narration
        own = getattr(scene.subtitles, code, None) if scene.subtitles else None
        return own or narration

    def missing_subtitles(self) -> dict[str, list[str]]:
        """{scene id: [track codes without their own line]} for scenes with narration."""
        out: dict[str, list[str]] = {}
        if not self.has_subtitles:
            return out
        primary = self.primary_subtitle_code()
        for s in self.scenes:
            if not (s.narration and s.narration.strip()):
                continue
            miss = [c for c in SUBTITLE_CODES
                    if c != primary and not (s.subtitles and getattr(s.subtitles, c))]
            if miss:
                out[s.id] = miss
        return out

    def scene_fingerprint(self, scene: Scene) -> str:
        """Changes whenever anything that affects the generated clip changes."""
        data = {
            "mode": scene.mode,
            "video": self.video_prompt(scene),
            "image": self.image_prompt(scene) if scene.mode == "keyframe" else None,
            "src": scene.image,
            "end": scene.end_image,
            "duration": scene.duration,
            "model": scene.model or self.model,
            "aspect": self.aspect,
        }
        refs = self.refs_signature(scene)
        if refs:  # only when used, so storyboards without reference images keep their fingerprints
            data["refs"] = refs
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def load_storyboard(path: str | Path) -> Storyboard:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    sb = Storyboard.model_validate(data)
    # Resolve relative image paths against the storyboard's folder.
    for s in sb.scenes:
        for attr in ("image", "end_image"):
            val = getattr(s, attr)
            if val and not Path(val).is_absolute():
                setattr(s, attr, str((path.parent / val).resolve()))
    for c in [*sb.characters, *(sb.locations or [])]:
        if c.image and not Path(c.image).is_absolute():
            c.image = str((path.parent / c.image).resolve())
    return sb


def merge_subtitles_raw(raw: dict, added: dict[str, dict[str, str]]) -> bool:
    """Write translated lines ({scene id: {code: line}}) into a raw storyboard dict (as read from YAML),
    keeping lines already there. True when something changed."""
    changed = False
    for sc in raw.get("scenes") or []:
        if not isinstance(sc, dict) or str(sc.get("id")) not in added:
            continue
        subs = subtitle_codes_dict(sc["subtitles"]) if isinstance(sc.get("subtitles"), dict) else {}
        for code, line in added[str(sc["id"])].items():
            if code in SUBTITLE_CODES and line and not subs.get(code):
                subs[code] = line
                changed = True
        if subs:
            sc["subtitles"] = {c: subs[c] for c in SUBTITLE_CODES if subs.get(c)}
    return changed


_LANG_KEYS = ("dialogue_language", "subtitle_language")


def _dump_dict(sb: Storyboard) -> dict:
    """`sb` as saved to YAML: the language fields (when set) come right after `aspect` so they are easy
    to find; without them the key order is exactly as before."""
    data = sb.model_dump(exclude_none=True)
    langs = {k: data.pop(k) for k in _LANG_KEYS if k in data}
    if not langs:
        return data
    out: dict = {}
    for k, v in data.items():
        out[k] = v
        if k == "aspect":
            out.update(langs)
    return out


def save_storyboard(sb: Storyboard, path: str | Path) -> None:
    data = _dump_dict(sb)
    Path(path).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
