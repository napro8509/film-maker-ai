"""Subtitles in several languages: Claude translates each scene's `narration` into the film's other subtitle
languages (stored in `scene.subtitles`), and the film is assembled with one .srt per language.

The narration's language is the first of `storyboard.subtitle_languages`. Translations never touch Flow,
so adding a language or fixing a line never costs credits.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from pydantic import BaseModel, Field

from . import ai
from .storyboard import Storyboard

# ISO 639-1 codes for file names (final.en.srt); other languages get a short slug of their name.
CODES = {"vietnamese": "vi", "english": "en", "thai": "th", "chinese": "zh", "japanese": "ja", "korean": "ko",
         "indonesian": "id", "malay": "ms", "filipino": "fil", "tagalog": "tl", "khmer": "km", "lao": "lo",
         "burmese": "my", "hindi": "hi", "spanish": "es", "portuguese": "pt", "french": "fr", "german": "de",
         "italian": "it", "russian": "ru", "arabic": "ar", "turkish": "tr"}


def lang_code(lang: str) -> str:
    key = lang.strip().casefold()
    if key in CODES:
        return CODES[key]
    slug = re.sub(r"[^a-z0-9]+", "-", key).strip("-")
    return slug[:20] or "sub"


def missing(sb: Storyboard, langs: list[str] | None = None) -> dict[str, list[str]]:
    """{language: [scene ids whose narration has no subtitle in it]} for the film's other languages."""
    all_langs = sb.subtitle_langs()
    wanted = [x for x in (langs or all_langs[1:]) if all_langs and x.casefold() != all_langs[0].casefold()]
    out: dict[str, list[str]] = {}
    for lang in wanted:
        ids = [s.id for s in sb.scenes if s.narration and s.narration.strip() and not sb.subtitle_text(s, lang)]
        if ids:
            out[lang] = ids
    return out


class Line(BaseModel):
    id: str = Field(description="Scene id.")
    language: str = Field(description="Target language, exactly as given.")
    text: str = Field(description="The subtitle in that language.")


class Translation(BaseModel):
    lines: list[Line]


SYSTEM = """You translate the subtitles of a short film. Each line is shown on screen during one 4-8 second
scene, so keep it as short and natural as the original (a subtitle, not an explanation), keep the meaning and
emotion, keep names as they are (in the script the language normally uses), and never add quotes or notes.
Write each target language natively in its own script (Thai in Thai script, Vietnamese with full diacritics)."""


def translate(sb: Storyboard, *, langs: list[str] | None = None, only_missing: bool = True,
              backend: str = "auto", api_key: str | None = None) -> dict[str, dict[str, str]]:
    """Translate narration into the film's other subtitle languages with one Claude call.
    Returns {scene id: {language: text}} (only what was translated). RuntimeError when Claude cannot answer."""
    all_langs = sb.subtitle_langs()
    if len(all_langs) < 2:
        return {}
    src = all_langs[0]
    targets = [x for x in (langs or all_langs[1:]) if x.casefold() != src.casefold()]
    todo: dict[str, list[str]] = (missing(sb, targets) if only_missing else
                                  {lang: [s.id for s in sb.scenes if s.narration and s.narration.strip()]
                                   for lang in targets})
    todo = {k: v for k, v in todo.items() if v}
    if not todo:
        return {}
    by_id = {s.id: s for s in sb.scenes}
    ids = sorted({i for v in todo.values() for i in v}, key=[s.id for s in sb.scenes].index)
    rows = "\n".join(f"- {i}: {by_id[i].narration.strip()}" for i in ids)
    jobs = "\n".join(f"- {lang}: {', '.join(v)}" for lang, v in todo.items())
    user = (f"Film: {sb.title}\nSubtitles are written in {src}:\n{rows}\n\n"
            f"Translate these scenes into these languages (one line per scene and language):\n{jobs}")
    res = ai.ask(SYSTEM, user, Translation, backend=backend, api_key=api_key, timeout=600, effort="low")
    wanted = {(lang.casefold(), i): lang for lang, v in todo.items() for i in v}
    out: dict[str, dict[str, str]] = {}
    for line in res.lines:
        lang = wanted.get((line.language.strip().casefold(), line.id.strip()))
        text = " ".join(line.text.split())
        if lang and text:
            out.setdefault(line.id.strip(), {})[lang] = text
    return out


def apply(raw: dict[str, Any], translated: dict[str, dict[str, str]]) -> list[str]:
    """Write translations into the raw storyboard dict (as saved). Returns the scene ids changed."""
    changed = []
    for scene in raw.get("scenes") or []:
        if not isinstance(scene, dict) or scene.get("id") not in translated:
            continue
        subs = dict(scene.get("subtitles") or {})
        before = dict(subs)
        for lang, text in translated[scene["id"]].items():
            for k in [k for k in subs if k.casefold() == lang.casefold()]:
                del subs[k]
            subs[lang] = text
        if subs != before:
            scene["subtitles"] = subs
            changed.append(scene["id"])
    return changed


def ensure(sb: Storyboard, edit_raw: Callable[[Callable[[dict], bool]], bool], *, backend: str | None,
           api_key: str | None = None, log: Callable[[str], None] = print) -> bool:
    """Before assembling: translate what is missing (best effort). True when the storyboard changed."""
    gaps = missing(sb)
    if not gaps:
        return False
    if not backend:
        log("⚠ Chưa dịch được phụ đề " + ", ".join(gaps) + " (chưa dùng được Claude): các cảnh đó sẽ thiếu phụ đề ở "
            "ngôn ngữ này.")
        return False
    log("Claude đang dịch phụ đề sang " + ", ".join(gaps) + "…")
    try:
        done = translate(sb, backend=backend, api_key=api_key)
    except (RuntimeError, ValueError) as exc:
        log(f"⚠ Không dịch được phụ đề ({exc}); các cảnh chưa dịch sẽ thiếu phụ đề ở ngôn ngữ đó.")
        return False
    if not done:
        return False
    return edit_raw(lambda raw: bool(apply(raw, done)))
