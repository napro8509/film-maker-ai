"""Storyboard file format (YAML) and helpers to build the final prompts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

Aspect = Literal["16:9", "9:16"]
Mode = Literal["t2v", "i2v", "keyframe"]


MAX_REFS = 3  # reference images sent with one generation (Flow accepts only a few)
# Added to every prompt sent to Flow: models like to burn dialogue/titles into the picture.
NO_TEXT = ("No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; "
           "any dialogue is spoken audio only.")


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


class Scene(BaseModel):
    id: str = Field(description="Short unique id, e.g. s01")
    mode: Mode = Field(
        default="keyframe",
        description="t2v = text-to-video; i2v = animate a local image; keyframe = generate a still with Imagen first, then animate it (best consistency).",
    )
    prompt: str = Field(description="What happens in the clip: action + camera motion.")
    image_prompt: str | None = Field(default=None, description="Still image prompt for mode=keyframe (defaults to prompt).")
    image: str | None = Field(default=None, description="Start frame path for mode=i2v.")
    end_image: str | None = Field(default=None, description="Optional end frame path for mode=i2v.")
    duration: int | None = Field(default=None, description="Seconds; omit for Flow's default.")
    model: str | None = Field(
        default=None,
        description="Optional video model alias for this scene only; normally leave empty to use the storyboard model.",
    )
    narration: str | None = Field(default=None, description="Optional line shown as a subtitle in the final film.")

    @model_validator(mode="after")
    def _check(self) -> "Scene":
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.id):
            raise ValueError(f"scene id '{self.id}' may only contain letters, digits, - and _")
        if self.mode == "i2v" and not self.image:
            raise ValueError(f"scene {self.id}: mode=i2v needs 'image'")
        return self


class Storyboard(BaseModel):
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
    scenes: list[Scene]

    @model_validator(mode="after")
    def _unique_ids(self) -> "Storyboard":
        ids = [s.id for s in self.scenes]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate scene ids: {sorted(dupes)}")
        if not self.scenes:
            raise ValueError("storyboard has no scenes")
        return self

    # --- prompt building -------------------------------------------------

    def _characters_in(self, text: str) -> list[Character]:
        return [c for c in self.characters if re.search(rf"\b{re.escape(c.name)}\b", text, re.IGNORECASE)]

    def _compose(self, text: str) -> str:
        parts = [text.strip().rstrip(".") + "."]
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

    def scene_refs(self, scene: Scene) -> list[tuple[str, Path]]:
        """(name, image) of characters in this scene that have a reference image on disk."""
        if not self.use_character_refs or scene.mode == "i2v":
            return []
        refs = [(c.name, Path(c.image)) for c in self._characters_in(self.scene_text(scene))
                if c.image and Path(c.image).is_file()]
        return refs[:MAX_REFS]

    def refs_signature(self, scene: Scene) -> list[str]:
        """Changes when a reference image is added, removed or regenerated."""
        return [f"{n}:{hashlib.sha256(p.read_bytes()).hexdigest()[:12]}" for n, p in self.scene_refs(scene)]

    def video_prompt(self, scene: Scene) -> str:
        # In keyframe/i2v mode the look is already in the image; keep the motion prompt focused.
        if scene.mode == "t2v":
            return self._compose(scene.prompt)
        return scene.prompt.strip()

    def text_only_prompt(self, scene: Scene) -> str:
        """Text-to-video prompt for a keyframe scene when image-to-video is unavailable:
        the opening-frame description plus the motion, with characters and style."""
        if scene.image_prompt and scene.image_prompt.strip():
            return self._compose(f"{scene.image_prompt.strip().rstrip('.')}. {scene.prompt.strip()}")
        return self._compose(scene.prompt)

    def image_prompt(self, scene: Scene) -> str:
        return self._compose(scene.image_prompt or scene.prompt)

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


def save_storyboard(sb: Storyboard, path: str | Path) -> None:
    data = sb.model_dump(exclude_none=True)
    Path(path).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
