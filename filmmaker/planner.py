"""Turn a one-line idea into a storyboard with Claude (optional step)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from . import model_meta
from .storyboard import Storyboard

MODEL = "claude-opus-5-5"

SYSTEM = """You are a film director writing shot lists for Google Flow (Veo video model).
Each scene becomes ONE short clip (4-8 seconds). Rules:
- Write every `prompt` and `image_prompt` in English (Veo understands English best), even if the idea is in another language.
- `prompt` = one clear action + one camera move (e.g. "slow dolly-in", "handheld tracking shot"), plus sound if useful. No scene cuts inside one clip.
- Prefer mode "keyframe": `image_prompt` describes the opening frame as a still photo (subject, setting, lighting, lens); `prompt` describes the motion from that frame.
- Define recurring people in `characters` with a fixed, specific look, and refer to them by exact name in prompts.
- Put the shared visual look in `style` once instead of repeating it.
- `narration` is an optional short subtitle line in the SAME language as the user's idea. Subtitles are added from it later, so never ask for visible text in a prompt: no captions, subtitles, signs with words, title cards or written dialogue in `prompt` / `image_prompt`.
- If a character speaks, describe it as spoken audio in English, e.g. "she says angrily in Thai, voice only". Do not put the spoken words in quotation marks in the prompt: Veo tends to burn quoted lines into the picture as subtitles.
- Scene ids: s01, s02, ...
- Fill `thumbnail`: `text` = a short, catchy video title (max ~6 words) in the language the characters speak in the video (if unclear, the narration language); `prompt` = an English description of an eye-catching cover picture with the main characters by exact name at the story's most dramatic moment (no text in the picture).
- Do not set project_id or image/end_image."""


def _user_message(idea: str, scenes: int, aspect: str, language_hint: str | None) -> str:
    user = f"Idea: {idea}\nNumber of scenes: {scenes}\nAspect ratio: {aspect}"
    if language_hint:
        user += f"\nNarration language: {language_hint}"
    return user


def claude_code_binary() -> str | None:
    return shutil.which(os.environ.get("CLAUDE_BIN", "claude"))


def _subscription_env() -> dict[str, str]:
    # Without these, Claude Code uses the logged-in claude.ai account (Pro/Max plan)
    # instead of billing an API key.
    return {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}


def claude_code_status() -> dict:
    """{"available": bool, "logged_in": bool, "method": "claude.ai" | ...}"""
    binary = claude_code_binary()
    if not binary:
        return {"available": False, "logged_in": False, "method": None}
    try:
        proc = subprocess.run([binary, "auth", "status"], capture_output=True, text=True, timeout=30,
                              env=_subscription_env())
        data = json.loads(proc.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"available": True, "logged_in": False, "method": None}
    return {"available": True, "logged_in": bool(data.get("loggedIn")), "method": data.get("authMethod")}


def plan_with_claude_code(idea: str, *, scenes: int = 6, aspect: str = "16:9", language_hint: str | None = None,
                          timeout: int = 900) -> Storyboard:
    """Same as plan(), but through the local `claude` CLI so it runs on the user's Claude subscription."""
    binary = claude_code_binary()
    if not binary:
        raise RuntimeError("không thấy lệnh `claude` (Claude Code). Cài tại https://claude.com/claude-code")
    schema = json.dumps(Storyboard.model_json_schema())
    cmd = [binary, "-p", _user_message(idea, scenes, aspect, language_hint),
           "--output-format", "json", "--json-schema", schema, "--system-prompt", SYSTEM,
           "--tools", "", "--strict-mcp-config", "--no-session-persistence"]
    with tempfile.TemporaryDirectory() as cwd:  # keep any project CLAUDE.md out of the prompt
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                                  env=_subscription_env())
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Claude Code chạy quá lâu; hãy thử lại hoặc giảm số cảnh.") from exc
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        tail = (proc.stderr or proc.stdout).strip()[-600:]
        raise RuntimeError(f"Claude Code lỗi (exit {proc.returncode}): {tail}") from None
    if data.get("is_error") or not data.get("structured_output"):
        msg = str(data.get("result") or data.get("subtype") or "không có kết quả")[:600]
        if "login" in msg.lower() or "auth" in msg.lower():
            msg += " — chạy `claude` trong Terminal và đăng nhập (/login) bằng tài khoản Claude của bạn."
        raise RuntimeError(f"Claude Code không viết được storyboard: {msg}")
    sb = Storyboard.model_validate(data["structured_output"])
    sb.aspect = aspect  # type: ignore[assignment]
    return sb


def plan_auto(idea: str, *, backend: str = "auto", api_key: str | None = None, **kw) -> tuple[Storyboard, str]:
    """backend: auto | claude_code | api. auto prefers Claude Code (subscription, no API credit needed)."""
    if backend == "auto":
        backend = "claude_code" if claude_code_binary() else "api"
    if backend == "claude_code":
        sb, used = plan_with_claude_code(idea, **kw), "claude_code"
    else:
        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("chưa có ANTHROPIC_API_KEY (hoặc cài Claude Code để dùng gói Claude của bạn)")
        sb, used = plan(idea, **kw), "api"
    # New projects never start on a model Flow is retiring (default: veo-lite).
    return model_meta.normalize_new_storyboard(sb, model_meta.load()), used


def plan(idea: str, *, scenes: int = 6, aspect: str = "16:9", language_hint: str | None = None) -> Storyboard:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("cần cài thêm: pip install 'film-maker-ai[ai]' (gói anthropic)") from exc

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    user = _user_message(idea, scenes, aspect, language_hint)

    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_format=Storyboard,
        output_config={"effort": "medium"},
        # If a safety classifier declines, let the API retry on a fallback model.
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={"fallbacks": "default"},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude từ chối ý tưởng này; hãy thử diễn đạt lại.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Kịch bản quá dài; hãy giảm số cảnh.")
    sb = response.parsed_output
    sb.aspect = aspect  # type: ignore[assignment]
    return sb


# --- video titles for the thumbnail -------------------------------------------

TITLE_SYSTEM = """You write titles for short drama videos posted on Facebook / TikTok / YouTube Shorts.
Given a storyboard, propose catchy, emotional, curiosity-driven titles that make people click.
Write each title in the VIDEO'S LANGUAGE: the language the characters speak in the video
(look for hints such as "says ... in Thai" in the scene prompts, or a Language line). Only if nothing
says what language is spoken, use the language of the narration lines, else of the film title.
Each title: at most ~8 words, no hashtags, no emojis, no quotation marks, no ending period.
For each title also give `vi`: a natural Vietnamese translation, so a Vietnamese editor understands it
(leave `vi` empty if the title is already in Vietnamese).
Vary the angle: the secret, the twist, the conflict, the emotion. Return only the titles."""


def _story_summary(sb: Storyboard) -> str:
    lines = [f"Film title: {sb.title}"]
    if sb.characters:
        lines.append("Characters: " + "; ".join(f"{c.name} ({c.description[:80]})" for c in sb.characters))
    narr = [s.narration for s in sb.scenes if s.narration]
    if narr:
        lines.append("Narration lines (subtitle language): " + " / ".join(narr))
    lines.append("Scenes: " + " | ".join(
        f"{s.id}: {' / '.join(x[:300] for x in (s.image_prompt, s.prompt) if x)}" for s in sb.scenes))
    return "\n".join(lines)


def _clean_line(t) -> str:
    return " ".join(str(t or "").split()).strip(" \"'“”‘’«».。")


def _clean_titles(items, n: int) -> list[dict[str, str]]:
    """[{"text": title in the video's language, "vi": Vietnamese translation or ""}]. Accepts plain strings too."""
    out: list[dict[str, str]] = []
    for it in items or []:
        if isinstance(it, dict):
            text, vi = _clean_line(it.get("text") or it.get("title")), _clean_line(it.get("vi"))
        else:
            text, vi = _clean_line(it), ""
        if text and all(o["text"] != text for o in out):
            out.append({"text": text, "vi": "" if vi == text else vi})
    return out[:n]


def suggest_titles(sb: Storyboard, *, backend: str = "auto", api_key: str | None = None, n: int = 5,
                   timeout: int = 180) -> list[dict[str, str]]:
    """A few title ideas for the thumbnail, written by Claude (Claude Code subscription or API key)."""
    user = f"{_story_summary(sb)}\n\nWrite {n} title options."
    if backend == "auto":
        backend = "claude_code" if claude_code_binary() else "api"
    if backend == "claude_code":
        binary = claude_code_binary()
        if not binary:
            raise RuntimeError("không thấy lệnh `claude` (Claude Code)")
        item = {"type": "object", "properties": {"text": {"type": "string"}, "vi": {"type": "string"}},
                "required": ["text", "vi"]}
        schema = json.dumps({"type": "object", "properties": {"titles": {"type": "array", "items": item}},
                             "required": ["titles"]})
        cmd = [binary, "-p", user, "--output-format", "json", "--json-schema", schema, "--system-prompt", TITLE_SYSTEM,
               "--tools", "", "--strict-mcp-config", "--no-session-persistence"]
        with tempfile.TemporaryDirectory() as cwd:
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                                      env=_subscription_env())
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Claude Code chạy quá lâu; hãy thử lại.") from exc
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            raise RuntimeError(f"Claude Code lỗi (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[-400:]}") from None
        if data.get("is_error") or not data.get("structured_output"):
            raise RuntimeError("Claude Code không gợi ý được tiêu đề: " + str(data.get("result") or "không có kết quả")[:400])
        return _clean_titles(data["structured_output"].get("titles"), n)

    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("chưa có ANTHROPIC_API_KEY (hoặc cài Claude Code để dùng gói Claude của bạn)")
    try:
        import anthropic
        from pydantic import BaseModel
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("cần cài thêm: pip install 'film-maker-ai[ai]' (gói anthropic)") from exc

    class Title(BaseModel):
        text: str
        vi: str

    class Titles(BaseModel):
        titles: list[Title]

    response = anthropic.Anthropic().messages.parse(
        model=MODEL, max_tokens=2000, system=TITLE_SYSTEM, messages=[{"role": "user", "content": user}],
        output_format=Titles, output_config={"effort": "low"},
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"}, extra_body={"fallbacks": "default"},
    )
    if response.stop_reason == "refusal" or not response.parsed_output:
        raise RuntimeError("Claude không gợi ý được tiêu đề cho phim này.")
    return _clean_titles([t.model_dump() for t in response.parsed_output.titles], n)
