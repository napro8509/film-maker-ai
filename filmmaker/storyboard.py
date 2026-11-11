"""Storyboard file format (YAML) and helpers to build the final prompts."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Literal

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
SUBTITLE_LANGS_MAX = 6  # subtitle languages per film
NO_DIALOGUE = "none"  # stored value for "Không thoại"
DIALOGUE_RULE = "All spoken dialogue is in {lang}; characters speak only {lang}."
NO_DIALOGUE_RULE = "No spoken dialogue: characters do not talk; only ambient sound, sound effects and music."
# A scene without lines in a film with dialogue. Sending the language rule there ("characters speak only Thai")
# made Veo invent lines in half of such scenes (a mother saying "it's ok, I'm here" in a scene meant to be silent).
SILENT_SCENE_RULE = ("Nobody speaks in this scene: no dialogue, no words, no voice-over; the characters stay silent "
                     "(breathing, sighs and crying are fine); background voices, if any, are an indistinct murmur; "
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


def clean_dialogue_language(value: object) -> str | None:
    """Like clean_language; "none" in any case means no spoken dialogue and is stored as "none"."""
    v = clean_language(value)
    return NO_DIALOGUE if v is not None and v.casefold() == NO_DIALOGUE else v


class Character(BaseModel):
    name: str
    description: str = Field(description="Fixed look: face, hair, outfit... Repeated in every scene that mentions the name.")
    image: str | None = Field(default=None, description="Reference image of the character (path). Sent with every "
                              "keyframe / video of a scene that mentions the name so the face stays the same.")
    image_prompt: str | None = Field(default=None, description="Prompt for generating the reference image "
                                     "(defaults to a full-body portrait built from the description).")


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
    content_direction: str | None = Field(default=None, description="Content direction (English): what this scene "
                                          "must make the audience understand and feel, the emotional beat, and how "
                                          "each character acts and reacts. Added to the prompts automatically.")
    dialogue: list[DialogueLine] | None = Field(default=None, description="Spoken lines in the order they are "
                                                "said, one speaker per line. Each line is said once by one "
                                                "character only: no character repeats or echoes another's line, "
                                                "and no line repeats an earlier scene. Empty when nobody speaks.")

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
    subtitles: dict[str, str] | None = Field(
        default=None, description="`narration` translated into the film's other subtitle languages "
                                  "({language: text}). Only for the film's subtitles: never sent to Flow.")

    @field_validator("subtitles", mode="before")
    @classmethod
    def _clean_subtitles(cls, v: object) -> dict[str, str] | None:
        if not v:
            return None
        if not isinstance(v, dict):
            raise ValueError("subtitles phải là {ngôn ngữ: câu phụ đề}")
        out: dict[str, str] = {}
        for k, text in v.items():
            lang = clean_language(k)
            if lang and isinstance(text, str) and text.strip():
                out[lang] = text.strip()
        return out or None

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
    subtitle_languages: list[str] | None = Field(
        default=None, description="Every language the film has subtitles in; the first is `subtitle_language` "
                                  "(the language `narration` is written in), the others come from scenes' `subtitles`.")

    @field_validator("subtitle_languages", mode="before")
    @classmethod
    def _subtitle_languages(cls, v: object) -> list[str] | None:
        if v is None:
            return None
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            raise ValueError("subtitle_languages phải là danh sách tên ngôn ngữ")
        out: list[str] = []
        for x in v:
            lang = clean_language(x)
            if lang and lang.casefold() != NO_DIALOGUE and lang.casefold() not in {o.casefold() for o in out}:
                out.append(lang)
        if len(out) > SUBTITLE_LANGS_MAX:
            raise ValueError(f"tối đa {SUBTITLE_LANGS_MAX} ngôn ngữ phụ đề")
        return out or None

    @model_validator(mode="after")
    def _primary_subtitle_language(self) -> "Storyboard":
        """subtitle_language is always the first of subtitle_languages (the narration's language)."""
        if self.subtitle_languages:
            if self.subtitle_language and self.subtitle_language.casefold() != self.subtitle_languages[0].casefold():
                rest = [x for x in self.subtitle_languages if x.casefold() != self.subtitle_language.casefold()]
                self.subtitle_languages = [self.subtitle_language, *rest][:SUBTITLE_LANGS_MAX]
            elif not self.subtitle_language:
                self.subtitle_language = self.subtitle_languages[0]
        return self

    def subtitle_langs(self) -> list[str]:
        """Every subtitle language of the film, the narration's language first ([] when none is known)."""
        if self.subtitle_languages:
            return list(self.subtitle_languages)
        return [self.subtitle_language] if self.subtitle_language else []

    def subtitle_text(self, scene: "Scene", lang: str | None = None) -> str | None:
        """The scene's subtitle in `lang` (None = the narration's language): narration, or its translation."""
        langs = self.subtitle_langs()
        if not scene.narration or not scene.narration.strip():
            return None
        if lang is None or not langs or lang.casefold() == langs[0].casefold():
            return scene.narration.strip()
        for k, v in (scene.subtitles or {}).items():
            if k.casefold() == lang.casefold():
                return v
        return None

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

    # --- prompt building -------------------------------------------------

    def _characters_in(self, text: str) -> list[Character]:
        return [c for c in self.characters if re.search(rf"\b{re.escape(c.name)}\b", text, re.IGNORECASE)]

    def _compose(self, text: str, direction: str = "", chars: list[Character] | None = None) -> str:
        """`chars` = characters whose description is added; None = those named in `text`."""
        parts = [text.strip().rstrip(".") + "."]
        if direction:
            parts.append(direction)
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

    def speaker_cues(self, scene: Scene) -> str:
        """Who is who for the dialogue of a keyframe / image-to-video scene ("" when there is none).
        There the prompt only names the characters, and Veo, which sees a picture and no names, gave a line to
        whoever faced the camera (the mother said her son's line). So: what each one looks like, the speaker
        faces the camera with moving lips, the others keep their mouths closed."""
        if scene.mode not in ("keyframe", "i2v") or not self.speaks():
            return ""
        speakers: list[str] = []
        for d in scene.spoken_lines():
            if d.character.strip() and d.character.strip().casefold() not in {x.casefold() for x in speakers}:
                speakers.append(d.character.strip())
        if not speakers:
            return ""
        by_name = {c.name.strip().casefold(): c for c in self.characters if c.name.strip()}
        cast = [c.name.strip() for c in self.scene_characters(scene)]
        for n in speakers:
            if n.casefold() not in {x.casefold() for x in cast}:
                cast.append(n)
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
        return " ".join(parts)

    def _speech_in_prompt(self, prompt: str) -> bool:
        """Speech written straight into the prompt ("Minh: …", "Lan says hello", "they talk"): such a scene does
        have words even with no dialogue lines, so it is not told that nobody speaks."""
        if re.search(r"\b(?:say|says|said|saying|ask|asks|asked|whisper|whispers|whispered|shout|shouts|shouted|"
                     r"reply|replies|replied|speak|speaks|spoke|speaking|talk|talks|talking|sing|sings|singing|"
                     r"call|calls|calling out|chat|chats|chatting|tell|tells|told)\b", prompt, re.IGNORECASE):
            return True
        names = [re.escape(c.name.strip()) for c in self.characters if c.name.strip()]
        return bool(names) and bool(re.search(rf"\b(?:{'|'.join(names)})\b(?:\s*\([^)]*\))?\s*:", prompt))

    def for_flow_video(self, prompt: str, scene: Scene | None = None) -> str:
        """A video prompt as actually sent to Flow: who-is-who for the dialogue (`speaker_cues`, when the scene is
        given), the spoken-language sentence, then `for_flow` (NO_TEXT).
        None of these are part of the fingerprints, so adding them re-renders nothing."""
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

    def scene_refs(self, scene: Scene) -> list[tuple[str, Path]]:
        """(name, image) of characters in this scene that have a reference image on disk."""
        if not self.use_character_refs or scene.mode == "i2v":
            return []
        refs = [(c.name, Path(c.image)) for c in self.scene_characters(scene)
                if c.image and Path(c.image).is_file()]
        return refs[:MAX_REFS]

    def refs_signature(self, scene: Scene) -> list[str]:
        """Changes when a reference image is added, removed or regenerated."""
        return [f"{n}:{hashlib.sha256(p.read_bytes()).hexdigest()[:12]}" for n, p in self.scene_refs(scene)]

    @staticmethod
    def direction(scene: Scene, fields: tuple[str, ...] | None = None) -> str:
        """The scene's camera direction as prompt text ("" when it has none)."""
        return scene.camera.describe(fields) if scene.camera and not scene.camera.is_empty() else ""

    @staticmethod
    def _join(*parts: str) -> str:
        return " ".join(p for p in parts if p)

    def video_prompt(self, scene: Scene) -> str:
        perf = self.performance(scene)
        if scene.mode == "t2v":
            return self._compose(scene.prompt, self._join(self.direction(scene), perf), self._prompt_characters(scene))
        # In keyframe/i2v mode the look and framing are already in the image; keep the prompt on the motion.
        motion = self.direction(scene, MOTION_FIELDS + ("focus",))
        action = scene.prompt.strip().rstrip(".")
        if not motion and not perf:
            return scene.prompt.strip()
        return self._join(f"{action}." if action else "", motion, perf)

    def text_only_prompt(self, scene: Scene) -> str:
        """Text-to-video prompt for a keyframe scene when image-to-video is unavailable:
        the opening-frame description plus the motion, with characters and style."""
        direction = self._join(self.direction(scene), self.performance(scene))
        if scene.image_prompt and scene.image_prompt.strip():
            return self._compose(f"{scene.image_prompt.strip().rstrip('.')}. {scene.prompt.strip()}",
                                 direction, self._prompt_characters(scene))
        return self._compose(scene.prompt, direction, self._prompt_characters(scene))

    def image_prompt(self, scene: Scene) -> str:
        # A still has no motion: only the framing, lens, focus, composition and lighting apply.
        # The content direction sets the faces and body language of the opening frame; dialogue is audio only.
        content = f"Content direction: {scene.content_direction.rstrip('.')}." if scene.content_direction else ""
        return self._compose(scene.image_prompt or scene.prompt,
                             self._join(self.direction(scene, FRAME_FIELDS), content), self._prompt_characters(scene))

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
    for c in sb.characters:
        if c.image and not Path(c.image).is_absolute():
            c.image = str((path.parent / c.image).resolve())
    return sb


_LANG_KEYS = ("dialogue_language", "subtitle_language", "subtitle_languages")


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
