"""Film formats chosen in the "Phim mới" form: length / episodes, sound, visual style, aspect and genres.

`short_drama` is the original flow (content direction, camera, dialogue, locations...). Every other format uses
the basic flow: Claude writes a still picture + one motion per scene, then text -> image -> video. Pure data and
small helpers: no I/O, imported by the storyboard, the planner and the web server.
"""

from __future__ import annotations

DRAMA = "short_drama"
# The drama flow is deprecated: kept so old projects (empty `format`) still open and finish, but new films use
# the basic flow. DEFAULT_FORMAT is what a new film gets when no format is chosen.
DEFAULT_FORMAT = "clip"
DEPRECATED_NOTE = ("Luồng Short drama cũ đã ngừng dùng (deprecated): phim mới hãy chọn định dạng khác "
                   "(Clip ngắn, Phim ngắn, ...). Dự án cũ vẫn chạy tiếp được.")

# key -> label, help, default scene count, pacing guide for the planner (English)
FORMATS: dict[str, dict] = {
    "feature": {
        "label": "Phim điện ảnh", "desc": "60 phút trở lên, có mở – thân – kết đầy đủ", "scenes": 40,
        "guide": ("A feature film with a full three-act structure: setup that introduces the world and the hero, "
                  "a confrontation with rising stakes and a midpoint turn, then a climax and a resolution. "
                  "Give quieter scenes room to breathe between big moments."),
    },
    "series": {
        "label": "Phim bộ", "desc": "Nhiều tập dài, mỗi tập có cao trào riêng", "scenes": 30,
        "guide": ("One episode of a series. It has its own climax, keeps the series' main conflict open, and "
                  "ends on a hook that makes people want the next episode."),
    },
    "short_film": {
        "label": "Phim ngắn", "desc": "3–30 phút, một câu chuyện trọn vẹn", "scenes": 20,
        "guide": ("A complete short film: one clear story with a beginning, middle and end, a single main "
                  "character arc and a satisfying ending."),
    },
    DRAMA: {
        "label": "Short drama (cũ)", "desc": "Luồng cũ, đã ngừng dùng: chỉ để làm tiếp dự án cũ", "scenes": 10,
        "guide": "",  # the original drama planner, unchanged
    },
    "clip": {
        "label": "Clip ngắn", "desc": "15–90 giây, một tình huống, có twist cuối", "scenes": 6,
        "guide": ("A short clip about ONE situation: hook in the first two seconds, build quickly, and end on a "
                  "twist or punchline in the last scene."),
    },
}

# key -> label, help, planner guide (English), rule added to every video prompt (English)
AUDIO: dict[str, dict] = {
    "dialogue": {
        "label": "Có thoại", "desc": "Kịch bản có lời nhân vật, cần lồng tiếng hoặc khớp môi",
        "guide": ("Characters speak. A scene with words has ONE `speaker` (face visible, one line of at most 12 "
                  "words) and every other visible person in `listeners`; `narration` = exactly that line (the subtitle). "
                  "Scenes without words leave both `speaker` and `narration` empty: never a `narration` without "
                  "a `speaker`."),
        "rule": "Spoken language: {lang}. Only one person speaks; everyone else keeps lips closed.",
    },
    "silent": {
        "label": "Không thoại", "desc": "Kể bằng biểu cảm và hành động, nhạc nền giữ cảm xúc",
        "guide": ("Nobody speaks. Tell the story with faces, gestures and actions; `audio` has only ambient sound "
                  "and effects (music is added when editing). No `speaker`, leave `narration` empty."),
        "rule": "No dialogue, no music.",
    },
    "voiceover": {
        "label": "Thuyết minh", "desc": "Một giọng kể dẫn dắt, nhân vật không cần nói",
        "guide": ("One narrator tells the story; characters on screen do not talk (no `speaker`). Put the "
                  "narrator's sentence for the scene in `narration`, at most 12 words, no quotation marks."),
        "rule": ("Voice-over: one calm narrator's voice speaks in {lang}; the characters on screen do not talk "
                 "and their lips do not move."),
    },
    "onscreen_text": {
        "label": "Chữ trên màn hình", "desc": "Lời hiện thành chữ, xem tắt tiếng vẫn hiểu",
        "guide": ("The story must be understood with the sound off. Nobody speaks (no `speaker`); put a short "
                  "caption for the scene in `narration`, at most 12 words (added as text when editing, never "
                  "drawn by the video model)."),
        "rule": "No dialogue, no music.",
    },
    "music": {
        "label": "Nhạc kể chuyện", "desc": "Câu chuyện đi theo một bài hát",
        "guide": ("The story follows one song: scenes are cut like a music video, each scene matches a part of "
                  "the song (intro, verse, chorus, bridge, final chorus); the song is added when editing. Nobody "
                  "talks (no `speaker`), leave `narration` empty."),
        "rule": "No dialogue, no music.",
    },
}

# key -> label, style sentence added to every image / video prompt ("" = the user writes it)
VISUALS: dict[str, dict] = {
    "realistic": {"label": "Người thật / chân thực",
                  "style": "photorealistic live-action film, real people, cinematic lighting"},
    "2d": {"label": "Hoạt hình 2D", "style": "2D hand-drawn animation, clean line art, flat colors, cel shading"},
    "3d": {"label": "Hoạt hình 3D (kiểu Pixar)",
           "style": "3D animated feature film style, stylized characters with expressive faces, soft global illumination"},
    "anime": {"label": "Anime", "style": "Japanese anime style, detailed backgrounds, expressive eyes, vivid colors"},
    "other": {"label": "Phong cách khác", "style": ""},
}

OTHER_STYLE_HINT = "Ví dụ: tranh màu nước, đất sét stop-motion, tranh sơn dầu…"

# key -> Vietnamese label, English name for the planner
GENRES: dict[str, tuple[str, str]] = {
    "family": ("Gia đình", "family"), "romance": ("Tình cảm", "romance"), "comedy": ("Hài", "comedy"),
    "psychological": ("Tâm lý", "psychological drama"), "horror": ("Kinh dị", "horror"),
    "action": ("Hành động", "action"), "detective": ("Trinh thám", "detective / mystery"),
    "historical": ("Cổ trang", "historical period"), "fantasy": ("Giả tưởng", "fantasy"),
    "school": ("Học đường", "school life"), "office": ("Công sở", "office life"), "revenge": ("Trả thù", "revenge"),
}

# Veo renders 16:9 and 9:16 only; 1:1 is listed (disabled) so people know why it is missing.
ASPECTS = [("9:16", "Dọc 9:16", True), ("16:9", "Ngang 16:9", True), ("1:1", "Vuông 1:1 (Flow chưa hỗ trợ video vuông)", False)]


def is_basic(fmt: str | None) -> bool:
    """True for a format that uses the basic text -> image -> video flow (every known format but the drama)."""
    return bool(fmt) and fmt in FORMATS and fmt != DRAMA


def is_deprecated(fmt: str | None) -> bool:
    """True for the old drama flow: `short_drama`, or no format at all (projects made before formats existed)."""
    return not fmt or fmt == DRAMA


def clean_genres(values: object) -> list[str]:
    """Known genre keys, in the given order, without duplicates."""
    out: list[str] = []
    for v in values if isinstance(values, (list, tuple)) else []:
        if isinstance(v, str) and v in GENRES and v not in out:
            out.append(v)
    return out


def visual_style(visual: str | None, custom: str | None = None) -> str:
    """The style sentence for a visual style key; `custom` is used for "other" (and wins when given)."""
    custom = " ".join((custom or "").split())
    if custom:
        return custom
    return VISUALS.get(visual or "", {}).get("style", "")


def audio_rule(audio: str | None, lang: str | None) -> str:
    """The sentence added to every video prompt for this kind of sound ("" when unknown)."""
    a = AUDIO.get(audio or "")
    if not a:
        return ""
    return a["rule"].format(lang=lang or "the film's language")


def uses_language(audio: str | None) -> bool:
    """Sound options whose words are spoken (and need a spoken language)."""
    return audio in ("dialogue", "voiceover")


def has_text(audio: str | None) -> bool:
    """Sound options whose scenes carry a `narration` line (subtitle / caption)."""
    return audio in ("dialogue", "voiceover", "onscreen_text")


def public() -> dict:
    """The choices for the web UI."""
    return {
        "drama": DRAMA,
        "default": DEFAULT_FORMAT,
        "deprecated_note": DEPRECATED_NOTE,
        "formats": [{"key": k, "label": v["label"], "desc": v["desc"], "scenes": v["scenes"], "basic": k != DRAMA,
                     "deprecated": k == DRAMA}
                    for k, v in sorted(FORMATS.items(), key=lambda kv: kv[0] == DRAMA)],
        "audio": [{"key": k, "label": v["label"], "desc": v["desc"], "spoken": uses_language(k), "text": has_text(k)}
                  for k, v in AUDIO.items()],
        "visuals": [{"key": k, "label": v["label"]} for k, v in VISUALS.items()],
        "other_style_hint": OTHER_STYLE_HINT,
        "aspects": [{"value": v, "label": l, "enabled": ok} for v, l, ok in ASPECTS],
        "genres": [{"key": k, "label": v[0]} for k, v in GENRES.items()],
    }
