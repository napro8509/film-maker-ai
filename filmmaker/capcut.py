"""CapCut export: writes the editor agent's plan as a CapCut project (a "draft"), ready to open in CapCut.

The same edit plan (edit.json) that ffmpeg renders is laid out on a CapCut timeline instead:
trimmed clips on the main track, CapCut's own transitions between them, fade in / out, each clip's
volume, the music on its own track (looped, faded) and the subtitles as editable text. The user opens
the project in CapCut, tunes what they like and exports from there.

The project format is the plain JSON CapCut desktop writes itself (checked against CapCut 9.7.1 on
macOS); the empty-project skeleton in templates/capcut_draft.json was saved by that version.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .assemble import AssembleError, _need, probe_duration
from .editor import ClipInfo, EditPlan, Transition
from .storyboard import Storyboard

Log = Callable[[str], None]

US = 1_000_000  # CapCut counts time in microseconds
MARKER = ".filmmaker.json"  # in every project folder this module wrote
MEDIA_DIR = "filmmaker_media"


@dataclass(frozen=True)
class CapcutTransition:
    name: str  # CapCut's English name
    label: str  # Vietnamese label for the UI
    effect_id: str
    hint: str  # when to use it (for the editor agent)


# Free CapCut transitions (id -> effect). Effect ids are CapCut's own resource ids.
CAPCUT_TRANSITIONS: dict[str, CapcutTransition] = {
    "dissolve": CapcutTransition("Dissolve", "Hoà tan", "6724845717472416269", "soft link, same mood"),
    "fade_black": CapcutTransition("Black Fade", "Mờ qua đen", "6724239388189921806", "jump in time, new chapter"),
    "fade_white": CapcutTransition("White Flash", "Loé trắng", "6724845376098013708", "flash: memory, shock, dream"),
    "color_dissolve": CapcutTransition("Color Dissolve", "Hoà tan màu", "6724846004274729480", "warm, emotional link"),
    "flare_dissolve": CapcutTransition("Flare Dissolve", "Hoà tan loé sáng", "7481553072678784311",
                                       "slow, dreamy link (1.5-2 s)"),
    "blur": CapcutTransition("Blur", "Nhoè", "6916426617455645186", "soft change of place or time"),
    "blur_horizontal": CapcutTransition("Horizontal Blur", "Nhoè ngang", "7451535587812594193",
                                        "fast move to another place"),
    "bloom": CapcutTransition("Bloom", "Toả sáng", "6916426858368078338", "hopeful or happy turn"),
    "glare": CapcutTransition("Glare", "Loé nắng", "6726707814028284423", "bright, energetic change"),
    "memory": CapcutTransition("Memory", "Hồi ức", "6748220149284737550", "into or out of a flashback"),
    "blink": CapcutTransition("Blink", "Chớp mắt", "6864867302936941064", "waking up, point of view, passing out"),
    "old_film": CapcutTransition("Old Film", "Phim cũ", "7131932238618300929", "the past, nostalgia"),
    "ink": CapcutTransition("Ink", "Loang mực", "6789847231873683976", "poetic, period or Asian drama"),
    "smoke": CapcutTransition("Smoke", "Khói", "7451535589498688016", "mystery, something hidden (1-1.5 s)"),
    "push_in": CapcutTransition("Pull In", "Đẩy gần", "6724226861666144779", "zoom into a detail or a reveal"),
    "pull_out": CapcutTransition("Pull Out", "Kéo xa", "6724226338418332167", "leaving a scene, wider view"),
    "left": CapcutTransition("Left", "Đẩy sang trái", "6724227717195108867", "change of place, light tone"),
    "right": CapcutTransition("Right", "Đẩy sang phải", "6724227599616184836", "change of place, light tone"),
    "up": CapcutTransition("Up", "Đẩy lên", "6724227090872275463", "rising energy"),
    "down": CapcutTransition("Down", "Đẩy xuống", "6724227330190873100", "falling mood"),
    "slide_left": CapcutTransition("Slide Left", "Trượt sang trái", "6726711499676455435", "energetic, comic"),
    "slide_right": CapcutTransition("Slide Right", "Trượt sang phải", "6726711296063967748", "energetic, comic"),
    "wipe_left": CapcutTransition("Wipe Left", "Gạt sang trái", "6724849999336706573", "meanwhile, parallel action"),
    "wipe_right": CapcutTransition("Wipe Right", "Gạt sang phải", "6724849898857959950", "meanwhile, parallel action"),
    "gradient_wipe": CapcutTransition("Gradient Wipe", "Gạt mờ dần", "6923134021744464386", "gentle passing of time"),
    "circle": CapcutTransition("Circle Mask", "Mở tròn", "6725767129519362573", "playful opening or ending"),
    "open": CapcutTransition("Open", "Mở màn", "6750893890712113677", "start of a new part"),
    "page_turn": CapcutTransition("Page Turn", "Lật trang", "6747979085894390279", "story-book, next chapter"),
    "blinds": CapcutTransition("Blinds", "Rèm lá", "6789847331060584974", "stylised change of place"),
    "cube": CapcutTransition("Cube", "Khối xoay", "7429600601161338117", "stylised, modern"),
    "shutter": CapcutTransition("Shutter", "Màn trập", "6885172292339372546", "a photo moment, a snap"),
    "swirl": CapcutTransition("Swirl", "Xoáy", "6851810799510360583", "dizziness, dream, magic"),
    "glitch": CapcutTransition("Glitch", "Nhiễu sóng", "6725771847444468236", "tech, tension, something wrong"),
    "shake": CapcutTransition("Shake", "Rung", "7252670515926536705", "impact, anger, action"),
    "particles": CapcutTransition("Particles", "Hạt sáng", "6873386344463208961", "magic, romance"),
}

# The ffmpeg transition of a plan -> its closest CapCut transition (None = straight cut).
FROM_FFMPEG: dict[str, str | None] = {
    "cut": None, "crossfade": "dissolve", "fadeblack": "fade_black", "fadewhite": "fade_white",
    "dissolve": "color_dissolve", "smoothleft": "left", "smoothright": "right", "slideleft": "slide_left",
    "slideright": "slide_right", "wipeleft": "wipe_left", "circleopen": "circle", "circleclose": "circle",
    "zoomin": "push_in", "hblur": "blur_horizontal", "radial": "swirl",
}

MOTION_SCALE = 1.08  # how far a push in / pull out goes
SOUND_MAX = 12.0  # seconds of one sound effect on the timeline
FADE_IN = ("Fade In", "6798320778182922760")  # video "in" animation
FADE_OUT = ("Fade Out", "6798320902548230669")  # video "out" animation


_CATALOG: dict[str, dict[str, dict[str, Any]]] | None = None


def catalog() -> dict[str, dict[str, dict[str, Any]]]:
    """CapCut's free resources by kind and name: filters, effects, text_in, text_out (ids are CapCut's own)."""
    global _CATALOG
    if _CATALOG is None:
        raw = json.loads((Path(__file__).parent / "templates" / "capcut_catalog.json").read_text(encoding="utf-8"))
        _CATALOG = {kind: {item["name"]: item for item in items} for kind, items in raw.items()}
    return _CATALOG


def pick(kind: str, name: str | None) -> str | None:
    """The catalogue's exact name for what the agent wrote (case and spaces forgiven), or None."""
    if not name:
        return None
    items = catalog()[kind]
    if name in items:
        return name
    key = "".join(name.split()).lower()
    return next((n for n in items if "".join(n.split()).lower() == key), None)


def transition_catalog() -> list[dict[str, str]]:
    return [{"id": k, "label": v.label} for k, v in CAPCUT_TRANSITIONS.items()]


def transition_for(t: Transition) -> str | None:
    """CapCut transition id of one planned transition: the agent's own CapCut choice, else the closest match."""
    if t.type == "cut" or t.duration <= 0:
        return None
    if t.capcut in CAPCUT_TRANSITIONS:
        return t.capcut
    return FROM_FFMPEG.get(t.type, "dissolve")


def agent_guide() -> str:
    """Extra instructions for the editor agent when the film is finished in CapCut."""
    rows = "\n".join(f'- "{k}": {v.name} - {v.hint}' for k, v in CAPCUT_TRANSITIONS.items())
    return ("\n\nThis film is finished in CapCut, which has many more transitions. For every transition that is "
            "not a cut, ALSO set `capcut` to one id from this list (keep `type` as the closest basic transition, "
            "it is the fallback). Match the effect to what happens in the story at that cut; still keep straight "
            "cuts as the backbone and use at most 3-4 different CapCut transitions in one film.\n" + rows)


def finishing_guide(has_music: bool, has_sheets: bool) -> str:
    """Instructions for the finishing layer: the agent works as editor, colourist and sound designer."""
    cat = catalog()
    names = {k: " | ".join(cat[k]) for k in ("filters", "effects", "text_in")}
    look = ("You are given contact sheets of the real footage (one row per scene: its start, middle and end frame). "
            "Read EVERY sheet file before you decide anything; judge light, colour, faces, movement and continuity "
            "from what you see, not only from the script.\n" if has_sheets else "")
    return f"""

FINISHING PASS. You finish this film the way a feature-film editor, colourist and sound designer would: every
scene deliberate, nothing decorative. Restraint is what makes a film look like cinema; an effect that calls
attention to itself is a mistake. {look}
Whole film:
- `look` + `look_strength`: ONE colour filter from FILTERS for the whole film, so shots made separately feel like
  one film. Pick by genre and by the footage (warm family drama, cold thriller, faded memory...). Strength
  0.35-0.65. Null if nothing fits.
- `music_query`: {"the user already chose the music; leave null." if has_music else
  "no music was given: write a SHORT search phrase for a music library, 2-3 English words, mood + instrument "
  "('sad piano', 'tense strings', 'warm acoustic guitar'). Longer phrases find nothing."}
- `title` + `title_in`: an opening title only if it suits the film (the film title, short). `title_in` from
  TEXT ANIMATIONS, elegant ones (fade, typewriter, soft blur).
- `subtitle_in`: a very soft entrance for subtitles from TEXT ANIMATIONS (a plain fade-in), or null.
Every clip:
- `music` (0-1): the music level under this scene. Spoken dialogue: 0.25-0.4 so every word is clear. Silent or
  purely visual scenes: 0.8-1. A shock, a slap, a held breath: 0 can be stronger than any music.
- `motion`: "push_in" on a still or slow emotional shot (a face, a letter, an altar), "pull_out" to leave a scene
  or reveal the room, "none" when the camera or the action already moves. At most about a third of the clips.
- `effect`: from EFFECTS, on 0-3 clips in the whole film and only with a story reason (grain or a vignette on a
  memory, a light leak on a hopeful ending). Never playful, glittery, sticker-like or music-video effects in a drama.
- `filter`: only when one clip must look different from the film's look (flashback, dream, black-and-white memory).
- `sounds` (0-2 per clip, at most 10 in the film): sound effects the clip's own audio lacks. The clips already
  carry their dialogue and room sound, so add only what lifts the scene: an accent on a hit, a low riser before a
  reveal, a heartbeat, room tone or weather under a clip with no audio. `query` is 2-3 generic English words naming the
  sound itself as a sound library would ("glass shatter", "cinematic riser", "heartbeat", "face slap"); never
  names or the film's title. `at` is seconds
  from the start of the trimmed clip. Ambience 0.2-0.4, accents 0.5-0.9.
- `note`: one short Vietnamese line saying why (only when you set something).
Transitions: `sound` only on a transition that moves (push, slide, blur): "soft whoosh". None on cuts and dissolves.
Copy every name EXACTLY as written in these lists (many are Chinese; do not translate them).
FILTERS: {names["filters"]}
EFFECTS: {names["effects"]}
TEXT ANIMATIONS: {names["text_in"]}"""


# --- where CapCut keeps its projects ---------------------------------------------


def drafts_dir(override: str | Path | None = None) -> Path:
    """CapCut's projects folder: the given one, $FILMMAKER_CAPCUT_DRAFTS, or CapCut's default for this OS."""
    custom = override or os.environ.get("FILMMAKER_CAPCUT_DRAFTS")
    if custom:
        return Path(custom).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"


def draft_file_name() -> str:
    return "draft_info.json" if sys.platform == "darwin" else "draft_content.json"


def installed(override: str | Path | None = None) -> bool:
    return drafts_dir(override).is_dir()


# --- timeline ----------------------------------------------------------------------


def timeline(plan: EditPlan, infos: dict[str, ClipInfo]) -> tuple[list[tuple[str, float, float]], float]:
    """[(scene id, start in film, length)] and the film's length on the CapCut timeline.

    Unlike the ffmpeg render, CapCut keeps the clips back to back: a transition plays across the cut
    and does not shorten the film."""
    rows, t = [], 0.0
    for c in plan.clips:
        length = infos[c.id].duration - c.trim_start - c.trim_end
        rows.append((c.id, t, length))
        t += length
    return rows, t


def subtitle_entries(sb: Storyboard, plan: EditPlan, infos: dict[str, ClipInfo],
                     code: str | None = None) -> list[tuple[float, float, str]]:
    """Timed subtitle lines of one track on the CapCut timeline (kept clear of the transitions)."""
    code = code or sb.primary_subtitle_code()
    scenes = {s.id: s for s in sb.scenes}
    rows, _ = timeline(plan, infos)
    d_in = [0.0] + [t.duration if transition_for(t) else 0.0 for t in plan.transitions]
    d_out = [t.duration if transition_for(t) else 0.0 for t in plan.transitions] + [0.0]
    out = []
    for i, (sid, start, length) in enumerate(rows):
        a = start + max(0.2, d_in[i] / 2)
        b = start + length - max(0.2, d_out[i] / 2)
        line = sb.subtitle_line(scenes[sid], code) if sid in scenes else None
        if line and b - a > 0.3:
            out.append((round(a, 3), round(b, 3), line))
    return out


# --- the project JSON ----------------------------------------------------------------


@dataclass
class Media:
    path: str  # where CapCut reads the file
    duration: float
    width: int = 0
    height: int = 0
    has_audio: bool = True


def _id() -> str:
    return str(uuid.uuid4()).upper()


def _us(seconds: float) -> int:
    return int(round(seconds * US))


def _template() -> dict[str, Any]:
    path = Path(__file__).parent / "templates" / "capcut_draft.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _segment(material_id: str, start: int, duration: int, *, source_start: int | None, volume: float,
             refs: list[str], render_index: int, clip: dict | None) -> dict[str, Any]:
    return {
        "enable_adjust": True, "enable_color_correct_adjust": False, "enable_color_curves": True,
        "enable_color_match_adjust": False, "enable_color_wheels": True, "enable_lut": True,
        "enable_smart_color_adjust": False, "last_nonzero_volume": 1.0, "reverse": False, "track_attribute": 0,
        "track_render_index": 0, "visible": True, "id": _id(), "material_id": material_id,
        "target_timerange": {"start": start, "duration": duration},
        "source_timerange": None if source_start is None else {"start": source_start, "duration": duration},
        "common_keyframes": [], "keyframe_refs": [], "speed": 1.0, "volume": volume,
        "extra_material_refs": refs, "render_index": render_index, "clip": clip,
        "uniform_scale": {"on": True, "value": 1.0} if clip else None,
        "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000} if clip and source_start is not None else None,
    }


def _plain_segment(material_id: str, start: int, duration: int, render_index: int) -> dict[str, Any]:
    """A piece on an effect / filter track: it only says what plays when."""
    return {"enable_adjust": True, "enable_color_correct_adjust": False, "enable_color_curves": True,
            "enable_color_match_adjust": False, "enable_color_wheels": True, "enable_lut": True,
            "enable_smart_color_adjust": False, "last_nonzero_volume": 1.0, "reverse": False, "track_attribute": 0,
            "track_render_index": 0, "visible": True, "id": _id(), "material_id": material_id,
            "target_timerange": {"start": start, "duration": duration}, "common_keyframes": [],
            "keyframe_refs": [], "render_index": render_index}


def _clip(y: float = 0.0, scale: float = 1.0) -> dict[str, Any]:
    return {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False}, "rotation": 0.0,
            "scale": {"x": scale, "y": scale}, "transform": {"x": 0.0, "y": y}}


def _track(kind: str, segments: list[dict], name: str = "") -> dict[str, Any]:
    return {"attribute": 0, "flag": 0, "id": _id(), "is_default_name": not name, "name": name,
            "segments": segments, "type": kind}


def _speed(materials: dict) -> str:
    sid = _id()
    materials["speeds"].append({"curve_speed": None, "id": sid, "mode": 0, "speed": 1.0, "type": "speed"})
    return sid


def _audio_fade(materials: dict, fade_in: float, fade_out: float) -> str:
    fid = _id()
    materials["audio_fades"].append({"id": fid, "fade_in_duration": _us(fade_in), "fade_out_duration": _us(fade_out),
                                     "fade_type": 0, "type": "audio_fade"})
    return fid


def _animation(kind: str, effect: tuple[str, str], start: int, duration: int) -> dict[str, Any]:
    return {"anim_adjust_params": None, "platform": "all", "panel": "video", "material_type": "video",
            "name": effect[0], "id": effect[1], "type": kind, "resource_id": effect[1], "start": start,
            "duration": duration}


def _keyframes(prop: str, points: list[tuple[int, float]]) -> dict[str, Any]:
    """One animated property of a segment; points = [(microseconds from the segment's start, value)]."""
    return {"id": _id(), "material_id": "", "property_type": prop, "keyframe_list": [
        {"curveType": "Line", "graphID": "", "left_control": {"x": 0.0, "y": 0.0},
         "right_control": {"x": 0.0, "y": 0.0}, "id": _id(), "time_offset": t, "values": [v]} for t, v in points]}


def _params(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"default_value": d, "max_value": hi, "min_value": lo, "name": n, "parameterIndex": i, "portIndex": 0,
             "value": d} for i, (n, d, lo, hi) in enumerate(item.get("params") or [])]


def _filter_material(name: str, strength: float) -> dict[str, Any]:
    item = catalog()["filters"][name]
    return {"adjust_params": [], "algorithm_artifact_path": "", "apply_target_type": 0, "bloom_params": None,
            "category_id": "", "category_name": "",
            "color_match_info": {"source_feature_path": "", "target_feature_path": "", "target_image_path": ""},
            "effect_id": item["id"], "enable_skin_tone_correction": False, "exclusion_group": [],
            "face_adjust_params": [], "formula_id": "", "id": _id(), "intensity_key": "",
            "multi_language_current": "", "name": name, "panel_id": "", "platform": "all",
            "resource_id": item["id"], "source_platform": 1, "sub_type": "none", "time_range": None,
            "type": "filter", "value": strength, "version": ""}


def _effect_material(name: str) -> dict[str, Any]:
    item = catalog()["effects"][name]
    return {"adjust_params": _params(item), "apply_target_type": 2, "apply_time_range": None, "category_id": "",
            "category_name": "", "common_keyframes": [], "disable_effect_faces": [], "effect_id": item["id"],
            "formula_id": "", "id": _id(), "name": name, "platform": "all", "render_index": 11000,
            "resource_id": item["id"], "source_platform": 0, "time_range": None, "track_render_index": 0,
            "type": "video_effect", "value": 1.0, "version": ""}


def _text_animation(mats: dict, kind: str, name: str | None, *, start: int = 0, length: int | None = None) -> str | None:
    """Entrance ("in") or exit ("out") animation for a text; returns the id to reference, or None."""
    item = catalog()["text_in" if kind == "in" else "text_out"].get(name or "")
    if not item:
        return None
    aid = _id()
    mats["material_animations"].append({"id": aid, "type": "sticker_animation", "multi_language_current": "none",
                                        "animations": [{
        "anim_adjust_params": None, "platform": "all", "panel": "", "material_type": "sticker", "name": name,
        "id": item["id"], "type": kind, "resource_id": item["id"], "start": start,
        "duration": length if length is not None else _us(item["duration"])}]})
    return aid


def music_envelope(plan: EditPlan, infos: dict[str, ClipInfo], ramp: float = 0.35) -> list[tuple[float, float]]:
    """[(film time, music level 0-1)]: the music dips under the scenes the editor marked (dialogue) and
    comes back between them. Empty when the level never changes."""
    rows, total = timeline(plan, infos)
    levels = [c.music for c in plan.clips]
    if not levels or all(abs(v - 1.0) < 0.01 for v in levels):
        return []
    points = [(0.0, levels[0])]
    for i in range(1, len(rows)):
        if abs(levels[i] - levels[i - 1]) >= 0.01:
            cut = rows[i][1]
            points += [(max(points[-1][0], cut - ramp), levels[i - 1]), (min(total, cut + ramp), levels[i])]
    points.append((total, levels[-1]))
    return points


def _level_at(points: list[tuple[float, float]], t: float) -> float:
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t0 <= t <= t1:
            return v0 if t1 <= t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return points[-1][1]


def _text_material(text: str, size: float) -> dict[str, Any]:
    content = {"styles": [{
        "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]}}},
        "range": [0, len(text.encode("utf-16-le")) // 2], "size": size, "bold": False, "italic": False,
        "underline": False,
        "strokes": [{"content": {"solid": {"alpha": 1.0, "color": [0.0, 0.0, 0.0]}}, "width": 0.06}],
    }], "text": text}
    return {"id": _id(), "content": json.dumps(content, ensure_ascii=False), "typesetting": 0, "alignment": 1,
            "letter_spacing": 0.0, "line_spacing": 0.02, "line_feed": 1, "line_max_width": 0.82,
            "force_apply_line_max_width": False, "check_flag": 15, "type": "subtitle", "global_alpha": 1.0}


def build_draft(sb: Storyboard, plan: EditPlan, infos: dict[str, ClipInfo], media: dict[str, Media], *,
                name: str, music: Media | None = None, music_volume: float = 0.25,
                subtitle_language: str | None = None, subtitles: bool = True,
                sounds: dict[str, Media] | None = None) -> dict[str, Any]:
    """The CapCut project document (draft_info.json) for this edit plan. `media` maps scene id -> its clip,
    `sounds` maps a sound's search phrase -> the downloaded file (phrases without a file are left out)."""
    sounds = sounds or {}
    doc = _template()["content"]
    mats = doc["materials"]
    width, height = (1920, 1080) if sb.aspect == "16:9" else (1080, 1920)
    rows, total = timeline(plan, infos)
    after = {t.after: t for t in plan.transitions}

    video_segments, cursor = [], 0
    for i, c in enumerate(plan.clips):
        m = media[c.id]
        duration = _us(rows[i][2])
        material_id = _id()
        mats["videos"].append({
            "audio_fade": None, "category_id": "", "category_name": "local", "check_flag": 63487,
            "crop": {"upper_left_x": 0.0, "upper_left_y": 0.0, "upper_right_x": 1.0, "upper_right_y": 0.0,
                     "lower_left_x": 0.0, "lower_left_y": 1.0, "lower_right_x": 1.0, "lower_right_y": 1.0},
            "crop_ratio": "free", "crop_scale": 1.0, "duration": _us(m.duration), "has_audio": m.has_audio,
            "height": m.height, "id": material_id, "local_material_id": material_id.lower(),
            "material_id": material_id, "material_name": Path(m.path).name, "media_path": "", "path": m.path,
            "type": "video", "width": m.width,
        })
        refs = [_speed(mats)]
        t = after.get(c.id)
        key = transition_for(t) if t and i < len(plan.clips) - 1 else None
        if key:
            eff, tid = CAPCUT_TRANSITIONS[key], _id()
            mats["transitions"].append({
                "category_id": "", "category_name": "", "duration": _us(t.duration), "effect_id": eff.effect_id,
                "id": tid, "is_overlap": True, "name": eff.name, "platform": "all", "resource_id": eff.effect_id,
                "type": "transition"})
            refs.append(tid)
        first, last = i == 0, i == len(plan.clips) - 1
        anims = []
        if first and plan.fade_in > 0:
            anims.append(_animation("in", FADE_IN, 0, min(_us(plan.fade_in), duration)))
        if last and plan.fade_out > 0:
            # CapCut times a clip's animations from where its picture first shows, which is half a transition
            # before the cut when a transition leads into the clip. Without this the fade would end early and
            # the picture would pop back for the last moments.
            before = plan.transitions[i - 1] if i else None
            lead = _us(before.duration) // 2 if before and transition_for(before) else 0
            d = min(_us(plan.fade_out), duration)
            anims.append(_animation("out", FADE_OUT, duration - d + lead, d))
        if anims:
            aid = _id()
            mats["material_animations"].append({"id": aid, "type": "sticker_animation",
                                                "multi_language_current": "none", "animations": anims})
            refs.append(aid)
        fi = min(plan.fade_in, 1.0) if first else 0.0
        fo = plan.fade_out if last else 0.0
        if m.has_audio and (fi > 0 or fo > 0):
            refs.append(_audio_fade(mats, fi, fo))
        seg = _segment(material_id, cursor, duration, source_start=_us(c.trim_start),
                       volume=float(c.volume), refs=refs, render_index=0, clip=_clip())
        if c.motion != "none":  # a slow push in / pull out, as a camera operator would
            a, b = (1.0, MOTION_SCALE) if c.motion == "push_in" else (MOTION_SCALE, 1.0)
            seg["common_keyframes"] = [_keyframes("KFTypeScaleX", [(0, a), (duration, b)])]
            seg["clip"] = _clip(scale=a)
        video_segments.append(seg)
        cursor += duration
    doc["tracks"].append(_track("video", video_segments))
    total_us = cursor
    starts = [_us(r[1]) for r in rows]

    if music and music.duration > 0.5:
        material_id = _id()
        mats["audios"].append({
            "app_id": 0, "category_id": "", "category_name": "local", "check_flag": 3,
            "copyright_limit_type": "none", "duration": _us(music.duration), "effect_id": "", "formula_id": "",
            "id": material_id, "local_material_id": material_id.lower(), "music_id": material_id,
            "name": Path(music.path).name, "path": music.path, "source_platform": 0, "type": "extract_music",
            "wave_points": []})
        envelope = music_envelope(plan, infos)
        spans, t = [], 0
        while t < total_us:  # the music loops until the film ends
            d = min(_us(music.duration), total_us - t)
            spans.append((t, d))
            t += d
        segments = []
        for k, (start, d) in enumerate(spans):
            refs = [_speed(mats)]
            fi = min(1.5, d / US / 2) if k == 0 else 0.0
            fo = min(max(plan.fade_out, 1.5), d / US / 2) if k == len(spans) - 1 else 0.0
            if fi or fo:
                refs.append(_audio_fade(mats, fi, fo))
            seg = _segment(material_id, start, d, source_start=0, volume=float(music_volume), refs=refs,
                           render_index=0, clip=None)
            if envelope:  # the music's own volume curve inside this piece
                inside = [(t, v) for t, v in envelope if start < _us(t) < start + d]
                pts = [(start / US, _level_at(envelope, start / US))] + inside + \
                      [((start + d) / US, _level_at(envelope, (start + d) / US))]
                seg["common_keyframes"] = [_keyframes(
                    "KFTypeVolume", [(_us(t) - start, round(float(music_volume) * v, 4)) for t, v in pts])]
            segments.append(seg)
        doc["tracks"].append(_track("audio", segments, "Nhạc nền"))

    # Sound effects: each on the first sound track that is free at that moment.
    placed: list[list[dict]] = []
    wanted = [(starts[i] + _us(snd.at), snd.query, snd.volume)
              for i, c in enumerate(plan.clips) for snd in c.sounds]
    wanted += [(starts[i + 1] - _us(0.25), t.sound, 0.6) for i, t in enumerate(plan.transitions)
               if t.sound and i + 1 < len(starts)]
    sound_ids: dict[str, str] = {}
    for start, query, volume in sorted(wanted):
        m = sounds.get(query)
        start = max(0, start)
        if not m or start >= total_us:
            continue
        if query not in sound_ids:
            sound_ids[query] = _id()
            mats["audios"].append({
                "app_id": 0, "category_id": "", "category_name": "local", "check_flag": 3,
                "copyright_limit_type": "none", "duration": _us(m.duration), "effect_id": "", "formula_id": "",
                "id": sound_ids[query], "local_material_id": sound_ids[query].lower(), "music_id": sound_ids[query],
                "name": Path(m.path).name, "path": m.path, "source_platform": 0, "type": "extract_music",
                "wave_points": []})
        d = min(_us(min(m.duration, SOUND_MAX)), total_us - start)
        refs = [_speed(mats), _audio_fade(mats, 0.0, min(0.4, d / US / 2))]
        seg = _segment(sound_ids[query], start, d, source_start=0, volume=float(volume), refs=refs, render_index=0,
                       clip=None)
        lane = next((ln for ln in placed if ln[-1]["target_timerange"]["start"] + ln[-1]["target_timerange"]["duration"]
                     <= start), None)
        if lane is None and len(placed) < 3:
            lane = []
            placed.append(lane)
        if lane is not None:
            lane.append(seg)
    for lane in placed:
        doc["tracks"].append(_track("audio", lane, "Tiếng động"))

    # Picture effects, one per clip that has one.
    segments = []
    for i, c in enumerate(plan.clips):
        if c.effect in catalog()["effects"]:
            mat = _effect_material(c.effect)
            mats["video_effects"].append(mat)
            segments.append(_plain_segment(mat["id"], starts[i], _us(rows[i][2]), 10000))
    if segments:
        doc["tracks"].append(_track("effect", segments))

    # Colour: the film's look, or the clip's own filter; neighbours with the same filter share one piece.
    runs: list[tuple[str, float, int, int]] = []
    for i, c in enumerate(plan.clips):
        name, strength = (c.filter, 0.8) if c.filter in catalog()["filters"] else (plan.look, plan.look_strength)
        if name not in catalog()["filters"]:
            continue
        end = starts[i] + _us(rows[i][2])
        if runs and runs[-1][0] == name and runs[-1][1] == strength and runs[-1][3] == starts[i]:
            runs[-1] = (name, strength, runs[-1][2], end)
        else:
            runs.append((name, strength, starts[i], end))
    segments = []
    for name, strength, a, b in runs:
        mat = _filter_material(name, strength)
        mats["effects"].append(mat)
        segments.append(_plain_segment(mat["id"], a, b - a, 11000))
    if segments:
        doc["tracks"].append(_track("filter", segments))

    if subtitles:
        size = 7.0 if sb.aspect == "16:9" else 9.0
        segments = []
        for a, b, line in subtitle_entries(sb, plan, infos, subtitle_language):
            mat = _text_material(line, size)
            mats["texts"].append(mat)
            anim = _text_animation(mats, "in", plan.subtitle_in, length=min(_us(0.4), (_us(b) - _us(a)) // 3))
            segments.append(_segment(mat["id"], _us(a), _us(b) - _us(a), source_start=None, volume=1.0,
                                     refs=[anim] if anim else [], render_index=15000, clip=_clip(y=-0.8)))
        if segments:
            doc["tracks"].append(_track("text", segments, "Phụ đề"))

    if plan.title and total_us > _us(4):
        mat = _text_material(plan.title, 14.0 if sb.aspect == "16:9" else 16.0)
        mats["texts"].append(mat)
        start, length = _us(max(0.3, plan.fade_in * 0.6)), _us(min(3.2, total_us / US / 3))
        refs = [a for a in (_text_animation(mats, "in", plan.title_in, length=min(_us(0.9), length // 3)),
                            _text_animation(mats, "out", "渐隐", start=length - _us(0.6), length=_us(0.6))) if a]
        doc["tracks"].append(_track("text", [_segment(mat["id"], start, length, source_start=None, volume=1.0,
                                                      refs=refs, render_index=15001, clip=_clip(y=0.3))],
                                    "Tiêu đề"))

    now = int(time.time() * US)
    doc.update({"canvas_config": {"background": None, "height": height, "ratio": "original", "width": width},
                "duration": total_us, "fps": 30.0, "id": _id(), "name": name, "create_time": now // US,
                "update_time": now // US})
    return doc


def build_meta(doc: dict[str, Any], folder: Path, media: list[Media], music: Media | None) -> dict[str, Any]:
    """draft_meta_info.json: what CapCut lists on its home screen and in the project's media bin."""
    meta = _template()["meta"]
    now = int(time.time())

    def item(m: Media, kind: str) -> dict[str, Any]:
        return {"ai_group_type": "", "create_time": now, "duration": _us(m.duration), "enter_from": 0,
                "extra_info": Path(m.path).name, "file_Path": m.path, "height": m.height, "id": str(uuid.uuid4()),
                "import_time": now, "import_time_ms": now * US, "item_source": 1, "material_color_tag": "",
                "md5": "", "metetype": kind, "roughcut_time_range": {"duration": _us(m.duration), "start": 0},
                "sub_time_range": {"duration": -1, "start": -1}, "type": 0, "width": m.width}

    values = [item(m, "video") for m in media] + ([item(music, "music")] if music else [])
    meta.update({
        "draft_fold_path": str(folder), "draft_id": doc["id"], "draft_name": folder.name,
        "draft_root_path": str(folder.parent), "tm_draft_create": now * US, "tm_draft_modified": now * US,
        "tm_duration": doc["duration"],
        "draft_materials": [{"type": t, "value": values if t == 0 else []} for t in (0, 1, 2, 3, 6)],
    })
    return meta


# --- writing the project ---------------------------------------------------------------


def _probe_size(path: Path) -> tuple[int, int]:
    try:
        out = subprocess.run(
            [_need("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
             "-of", "json", str(path)], capture_output=True, text=True, check=True, timeout=30).stdout
        s = json.loads(out)["streams"][0]
        return int(s["width"]), int(s["height"])
    except (subprocess.SubprocessError, ValueError, KeyError, IndexError, OSError):
        return 0, 0


def _safe_name(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title or "").strip(" .")
    return re.sub(r"\s+", " ", name)[:80] or "Film Maker"


def _stamp(folder: Path) -> dict[str, Any] | None:
    try:
        return json.loads((folder / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _touched(folder: Path) -> bool:
    """True when CapCut saved this project after we wrote it (the user worked on it there)."""
    stamp = _stamp(folder)
    try:
        return stamp is None or (folder / draft_file_name()).stat().st_mtime_ns != stamp.get("mtime_ns")
    except OSError:
        return False


def pick_folder(root: Path, name: str) -> tuple[Path, str | None]:
    """Project folder to write, and a note when the previous export was kept.

    Our own untouched export is replaced. A project CapCut has saved since (the user's edits), one that is
    open in CapCut, or a project that is not ours is never overwritten: the export gets the next free name."""
    note, n = None, 1
    while True:
        folder = root / (name if n == 1 else f"{name} ({n})")
        if not folder.exists():
            return folder, note
        ours = _stamp(folder) is not None
        if ours and not (folder / ".locked").exists() and not _touched(folder):
            return folder, note
        if ours and note is None:
            note = (f"“{folder.name}” đang mở hoặc đã được sửa trong CapCut nên được giữ nguyên; "
                    "bản dựng mới lưu thành dự án khác.")
        n += 1


def _place(src: Path, dst: Path) -> None:
    """Put the file inside the project (a hard link when possible: no extra disk space)."""
    dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def export_draft(sb: Storyboard, clips: list[tuple[str, Path]], plan: EditPlan, infos: dict[str, ClipInfo], *,
                 music: Path | None = None, music_volume: float = 0.25, subtitle_language: str | None = None,
                 subtitles: bool = True, name: str | None = None, root: str | Path | None = None,
                 library: Any = None, log: Log = print) -> Path:
    """Write the plan as a CapCut project and return its folder.

    library (resources.Library): where the sounds the plan asks for are found; without it the plan's
    sound effects and music search are left out."""
    base = drafts_dir(root)
    if not base.is_dir():
        raise AssembleError(f"không thấy thư mục dự án của CapCut ({base}). Cài CapCut bản máy tính và mở nó một lần, "
                            "hoặc chỉ thư mục bằng --capcut-dir / FILMMAKER_CAPCUT_DRAFTS.")
    paths = dict(clips)
    folder, note = pick_folder(base, _safe_name(name or sb.title))
    if note:
        log("⚠ " + note)
    if folder.exists():
        shutil.rmtree(folder)
    media_dir = folder / MEDIA_DIR
    media_dir.mkdir(parents=True)

    # The clips are placed inside the project: CapCut may not be allowed to read the film's own folder
    # (Desktop / Documents are protected on macOS), and the project keeps working if the film folder moves.
    media: dict[str, Media] = {}
    for c in plan.clips:
        src = paths[c.id]
        dst = media_dir / f"{c.id}{src.suffix.lower() or '.mp4'}"
        _place(src, dst)
        w, h = _probe_size(src)
        media[c.id] = Media(str(dst), infos[c.id].duration, w, h, infos[c.id].has_audio)
    used, sound_media = [], {}
    if library is not None:
        if not music and plan.music_query:  # no music chosen: the editor's own pick from the web
            asset = library.find(plan.music_query, "music", want=timeline(plan, infos)[1])
            if asset:
                music = library.path(asset)
                used.append(asset)
        queries = [snd.query for c in plan.clips for snd in c.sounds] + [t.sound for t in plan.transitions if t.sound]
        for n, query in enumerate(dict.fromkeys(queries)):
            asset = library.find(query, "sfx")
            if asset:
                src = library.path(asset)
                dst = media_dir / f"sfx{n + 1:02}{src.suffix.lower()}"
                _place(src, dst)
                sound_media[query] = Media(str(dst), asset.duration)
                used.append(asset)
        library.write_credits(used)
    music_media = None
    if music:
        dst = media_dir / f"music{music.suffix.lower()}"
        _place(music, dst)
        music_media = Media(str(dst), probe_duration(music))

    doc = build_draft(sb, plan, infos, media, name=folder.name, music=music_media, music_volume=music_volume,
                      subtitle_language=subtitle_language, subtitles=subtitles, sounds=sound_media)
    draft = folder / draft_file_name()
    draft.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    meta = build_meta(doc, folder, list(media.values()) , music_media)
    meta["draft_timeline_materials_size_"] = draft.stat().st_size
    (folder / "draft_meta_info.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (folder / MARKER).write_text(json.dumps({"mtime_ns": draft.stat().st_mtime_ns, "film": sb.title}),
                                 encoding="utf-8")

    cuts = [transition_for(t) for t in plan.transitions]
    kinds = sorted({CAPCUT_TRANSITIONS[k].label for k in cuts if k})
    log(f"Dự án CapCut “{folder.name}”: {len(plan.clips)} cảnh ({doc['duration'] / US:.1f}s), "
        f"{sum(1 for k in cuts if k)} chuyển cảnh{' (' + ', '.join(kinds) + ')' if kinds else ''}, "
        f"{sum(1 for k in cuts if not k)} cắt thẳng{', có nhạc nền' if music else ''}"
        f"{', có phụ đề' if any(t['type'] == 'text' and t['name'] == 'Phụ đề' for t in doc['tracks']) else ''}.")
    extras = []
    if plan.look:
        extras.append(f"màu phim “{plan.look}”")
    for label, n in (("hiệu ứng hình", sum(1 for c in plan.clips if c.effect)),
                     ("chuyển động máy", sum(1 for c in plan.clips if c.motion != "none")),
                     ("tiếng động", len(sound_media))):
        if n:
            extras.append(f"{n} {label}")
    if music_envelope(plan, infos) and music:
        extras.append("nhạc tự hạ dưới thoại")
    if plan.title:
        extras.append(f"tiêu đề “{plan.title}”")
    if extras:
        log("Lớp hoàn thiện: " + ", ".join(extras) + ".")
    if used:
        log(f"Nguồn nhạc / tiếng động (ghi nguồn khi đăng): {library.folder / 'CREDITS.txt'}")
    return folder


def export_film(sb: Storyboard, clips: list[tuple[str, Path]], out_dir: Path, *, music: Path | None = None,
                music_volume: float = 0.25, subtitles: bool = True, subtitle_language: str | None = None,
                name: str | None = None, root: str | Path | None = None, engine: str = "auto",
                replan: bool = False, backend: str = "auto", api_key: str | None = None,
                ai_available: bool = True, log: Log = print) -> tuple[Path, EditPlan]:
    """Editor agent's plan (made now, or the saved edit.json) -> CapCut project. Returns (folder, plan)."""
    from .editor import make_plan

    base = drafts_dir(root)
    if not base.is_dir():  # before the agent spends time on a plan
        raise AssembleError(f"không thấy thư mục dự án của CapCut ({base}). Cài CapCut bản máy tính và mở nó một lần, "
                            "hoặc chỉ thư mục bằng --capcut-dir / FILMMAKER_CAPCUT_DRAFTS.")
    from .resources import Library

    plan, infos = make_plan(sb, clips, out_dir, engine=engine, replan=replan, has_music=bool(music),
                            backend=backend, api_key=api_key, ai_available=ai_available, capcut=True, pro=True,
                            log=log)
    library = Library(out_dir / "edit_assets", log=log)
    folder = export_draft(sb, clips, plan, infos, music=music, music_volume=music_volume, subtitles=subtitles,
                          subtitle_language=subtitle_language, name=name, root=root, library=library, log=log)
    return folder, plan


def open_capcut() -> bool:
    """Bring CapCut up (the new project is on its home screen). False when it could not be started."""
    try:
        if sys.platform == "darwin":
            return subprocess.run(["open", "-a", "CapCut"], capture_output=True, timeout=20).returncode == 0
        if sys.platform == "win32":
            exe = Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "Apps" / "CapCut.exe"
            if exe.is_file():
                subprocess.Popen([str(exe)])
                return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


__all__ = ["CAPCUT_TRANSITIONS", "FROM_FFMPEG", "Media", "agent_guide", "build_draft", "build_meta", "drafts_dir",
           "export_draft", "export_film", "installed", "open_capcut", "pick_folder", "subtitle_entries", "timeline",
           "transition_catalog", "transition_for"]
