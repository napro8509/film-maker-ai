"""Posts for social networks (Facebook, TikTok, Instagram) about a finished film, in several languages.

One Claude call with the same material as “Hỏi AI” (film_chat.gather + the cover and a contact sheet) writes one
post per platform and language. The result is kept in `<out>/social.json` so the page shows it again later.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import ai, film_chat

SOCIAL_FILE = "social.json"
PLATFORMS: dict[str, str] = {"tiktok": "TikTok", "facebook": "Facebook", "instagram": "Instagram"}
LANGUAGES: dict[str, str] = {"Vietnamese": "Tiếng Việt", "English": "English", "Thai": "ไทย"}

Platform = Literal["tiktok", "facebook", "instagram"]
Language = Literal["Vietnamese", "English", "Thai"]


class Post(BaseModel):
    platform: Platform
    language: Language
    title: str = Field(default="", description="Facebook: a short headline/first line. Empty for TikTok/Instagram.")
    hook: str = Field(description="Text to put on screen in the first 2-3 seconds of the video (max ~8 words).")
    caption: str = Field(description="The post text, with line breaks and emojis as fits the platform. No hashtags here.")
    hashtags: list[str] = Field(default_factory=list, description="Hashtags without '#', no spaces inside.")
    first_comment: str = Field(default="", description="A comment to pin under the post that invites replies.")


class SocialPack(BaseModel):
    posts: list[Post]


SYSTEM = """You are a social media editor for short AI-made films (vertical or horizontal micro-dramas).
You get everything about one finished film (story, characters, scenes, dialogue, subtitles, reviews) and maybe
its cover and a contact sheet of frames. Write ready-to-paste posts to publish the film.

Platform rules:
- tiktok: caption 1-3 short lines (under ~150 characters before hashtags), a curiosity hook that makes people watch
  to the end, no spoilers of the ending; 4-6 hashtags mixing broad (fyp-style for that language/market) and niche.
- facebook: `title` = a strong first line; caption 3-6 short paragraphs telling the set-up and the emotion,
  ending with a question to start comments; 3-5 hashtags.
- instagram (Reels): caption 2-4 short lines with emojis, line breaks, a call to action (save/share/follow);
  8-15 hashtags.
`hook` is on-screen text for the first seconds; `first_comment` is a comment to pin.

Language rules: write each post natively in its language for that country's audience — not a word-for-word
translation. Vietnamese posts for Vietnamese viewers, Thai posts in natural Thai script for Thai viewers, English
posts for an international audience. Hashtags in the post's language or common English tags used in that market.
Character names may stay as they are. Be true to the film: never promise things that are not in it."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(out_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((Path(out_dir) / SOCIAL_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("posts"), list) else None


def _clean_tag(tag: str) -> str:
    return "".join(tag.strip().lstrip("#").split())


def generate(project_dir: Path, out_dir: Path, platforms: list[str], languages: list[str], *, note: str = "",
             with_pictures: bool = True, backend: str = "auto", api_key: str | None = None,
             timeout: int = 600) -> dict[str, Any]:
    """Write the posts and save them. ValueError for bad choices, RuntimeError when Claude cannot answer."""
    platforms = [p for p in PLATFORMS if p in set(platforms)]
    languages = [lang for lang in LANGUAGES if lang in set(languages)]
    if not platforms:
        raise ValueError("Chọn ít nhất một mạng xã hội.")
    if not languages:
        raise ValueError("Chọn ít nhất một ngôn ngữ.")
    note = (note or "").strip()[:1000]
    with film_chat.lock(out_dir):
        sections = film_chat.gather(project_dir, out_dir)
        images = [x for x in film_chat.pictures(project_dir, out_dir) if not x[0].startswith("Ảnh tham chiếu")] \
            if with_pictures else []
        wanted = [f"{p} / {lang}" for lang in languages for p in platforms]
        user = "\n\n".join([
            "# Tư liệu của bộ phim", film_chat.context_text(sections) or "(không có tư liệu)",
            "# Yêu cầu",
            "Write exactly one post for each of these (platform / language), in this order:\n- " + "\n- ".join(wanted),
            f"Extra wishes from the user: {note}" if note else "",
        ]).strip()
        pack = ai.ask(SYSTEM, user, SocialPack, images=images, backend=backend, api_key=api_key,
                      timeout=timeout, effort="medium")
        posts: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for post in pack.posts:
            key = (post.platform, post.language)
            if post.platform not in platforms or post.language not in languages or key in seen:
                continue
            seen.add(key)
            d = post.model_dump()
            d["hashtags"] = [t for t in (_clean_tag(x) for x in post.hashtags) if t][:20]
            posts.append(d)
        if not posts:
            raise RuntimeError("Claude không viết được bài đăng nào.")
        order = {(p, lang): i for i, (lang, p) in enumerate((lang, p) for lang in languages for p in platforms)}
        posts.sort(key=lambda d: order[(d["platform"], d["language"])])
        missing = [f"{PLATFORMS[p]} / {LANGUAGES[lang]}" for lang in languages for p in platforms if (p, lang) not in seen]
        data = {"posts": posts, "platforms": platforms, "languages": languages, "note": note,
                "missing": missing, "at": _now()}
        path = Path(out_dir) / SOCIAL_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return data
