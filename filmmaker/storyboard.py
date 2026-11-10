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

Aspect = Literal["16:9", "9:16"]
Mode = Literal["t2v", "i2v", "keyframe"]


MAX_REFS = 3  # reference images sent with one generation (Flow accepts only a few)
# Added to every prompt sent to Flow: models like to burn dialogue/titles into the picture.
NO_TEXT = ("No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; "
           "any dialogue is spoken audio only.")
# Spoken language forced into every *video* prompt (never into image prompts or the fingerprints).
LANG_MAX = 40
NO_DIALOGUE = "none"  # stored value for "Không thoại"
DIALOGUE_RULE = "All spoken dialogue is in {lang}; characters speak only {lang}."
NO_DIALOGUE_RULE = "No spoken dialogue: characters do not talk; only ambient sound, sound effects and music."


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

    @field_validator("location", mode="before")
    @classmethod
    def _blank_location(cls, v: object) -> object:
        return (v.strip() or None) if isinstance(v, str) else v

    @model_validator(mode="after")
    def _check(self) -> "SceneDraft":
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.id):
            raise ValueError(f"scene id '{self.id}' may only contain letters, digits, - and _")
        if self.mode == "i2v" and not self.image:
            raise ValueError(f"scene {self.id}: mode=i2v needs 'image'")
        return self


class Scene(SceneDraft):
    characters: list[str] | None = Field(
        default=None, description="Names of characters in this scene (overrides name detection).")

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
    subtitle_language: str | None = Field(default=None, description="Language of `narration` chosen when planning. "
                                          "Reference only: never sent to Flow.")

    @field_validator("dialogue_language", mode="before")
    @classmethod
    def _dialogue_language(cls, v: object) -> str | None:
        return clean_dialogue_language(v)

    @field_validator("subtitle_language", mode="before")
    @classmethod
    def _subtitle_language(cls, v: object) -> str | None:
        return clean_language(v)

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
        if chars:
            parts.append(" ".join(f"{c.name}: {c.description.strip()}." for c in chars))
        if self.style.strip():
            parts.append(f"Style: {self.style.strip().rstrip('.')}.")
        return " ".join(parts)

    def for_flow(self, prompt: str) -> str:
        """The prompt as actually sent to Flow. Not part of the fingerprints, so turning this on or
        off does not mark finished scenes for re-rendering."""
        if not self.no_text or NO_TEXT in prompt:
            return prompt
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

    def for_flow_video(self, prompt: str) -> str:
        """A video prompt as actually sent to Flow: the spoken-language sentence, then `for_flow` (NO_TEXT).
        Like NO_TEXT it is not part of the fingerprints, so changing the language re-renders nothing."""
        rule = self.dialogue_rule()
        if not rule or rule.rstrip(".") in prompt:  # already there (pasted by the user, maybe without the dot)
            return self.for_flow(prompt)
        if NO_TEXT in prompt:  # keep the rule before NO_TEXT
            return self.for_flow(prompt.replace(NO_TEXT, f"{rule} {NO_TEXT}", 1))
        base = prompt.rstrip()
        if base and base[-1] not in ".!?":
            base += "."
        return self.for_flow(f"{base} {rule}" if base else rule)

    def portrait_prompt(self, c: Character) -> str:
        if c.image_prompt and c.image_prompt.strip():
            base = c.image_prompt.strip().rstrip(".") + "."
        else:
            base = (f"Character reference image of {c.name}: {c.description.strip().rstrip('.')}. "
                    "Full body, standing, facing the camera, face clearly visible, neutral expression, "
                    "plain light grey studio background, soft even lighting, sharp detail.")
        if self.style.strip():
            base += f" Style: {self.style.strip().rstrip('.')}."
        return base

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
        return [(c.name, Path(c.image)) for c in self._characters_in(self.thumbnail_prompt())
                if c.image and Path(c.image).is_file()][:MAX_REFS]

    def scene_text(self, scene: Scene) -> str:
        """Text whose character names decide which references a scene gets."""
        return f"{scene.image_prompt or ''} {scene.prompt}" if scene.mode == "keyframe" else scene.prompt

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
        return None if scene.characters is None else self.scene_characters(scene)

    def char_ref_limit(self, scene: Scene) -> int:
        """How many character pictures a scene may send: slot 1 is kept for the location (or the previous
        scene's keyframe) when the scene has one, even while that slot is empty."""
        return MAX_REFS - 1 if self.scene_location(scene) is not None else MAX_REFS

    def _char_ref_candidates(self, scene: Scene) -> list[tuple[str, Path]]:
        if not self.use_character_refs or scene.mode == "i2v":
            return []
        return [(c.name, Path(c.image)) for c in self.scene_characters(scene)
                if c.image and Path(c.image).is_file()]

    def scene_char_refs(self, scene: Scene) -> list[tuple[str, Path]]:
        """(name, image) of characters in this scene that have a reference image on disk and are sent."""
        return self._char_ref_candidates(scene)[:self.char_ref_limit(scene)]

    def dropped_char_refs(self, scene: Scene) -> list[str]:
        """Characters with a picture that is not sent because there is no slot left."""
        return [n for n, _ in self._char_ref_candidates(scene)[self.char_ref_limit(scene):]]

    def scene_ref_items(self, scene: Scene, prev: tuple[str, Path] | None = None) -> list[RefItem]:
        """Every reference image of the scene in sending order. With a location: slot 1 is the previous
        scene's keyframe (`prev` = (id, path), keyframe scenes only) or else the location's picture (or nothing),
        then at most 2 characters. Without one: only the characters, as before."""
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

    def video_prompt(self, scene: Scene) -> str:
        if scene.mode == "t2v":
            return self._compose(scene.prompt, self.direction(scene), self._prompt_characters(scene),
                                 setting=self._setting(scene))
        # In keyframe/i2v mode the look and framing are already in the image; keep the prompt on the motion.
        motion = self.direction(scene, MOTION_FIELDS + ("focus",))
        action = scene.prompt.strip().rstrip(".")
        if not motion:
            return scene.prompt.strip()
        return f"{action}. {motion}" if action else motion

    def text_only_prompt(self, scene: Scene) -> str:
        """Text-to-video prompt for a keyframe scene when image-to-video is unavailable:
        the opening-frame description plus the motion, with characters and style."""
        if scene.image_prompt and scene.image_prompt.strip():
            return self._compose(f"{scene.image_prompt.strip().rstrip('.')}. {scene.prompt.strip()}",
                                 self.direction(scene), self._prompt_characters(scene), setting=self._setting(scene))
        return self._compose(scene.prompt, self.direction(scene), self._prompt_characters(scene),
                             setting=self._setting(scene))

    def image_prompt(self, scene: Scene) -> str:
        # A still has no motion: only the framing, lens, focus, composition and lighting apply.
        return self._compose(scene.image_prompt or scene.prompt, self.direction(scene, FRAME_FIELDS),
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
        so every track has a line wherever the scene has narration. The primary track is `narration` itself."""
        narration = scene.narration.strip() if scene.narration and scene.narration.strip() else None
        if code == self.primary_subtitle_code() and narration:
            return narration
        own = getattr(scene.subtitles, code, None) if scene.subtitles else None
        return own or narration

    def missing_subtitles(self) -> dict[str, list[str]]:
        """{scene id: [track codes without their own line]} for scenes with narration."""
        out: dict[str, list[str]] = {}
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
        subs = sc.get("subtitles") if isinstance(sc.get("subtitles"), dict) else {}
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
