"""Turn a one-line idea into a storyboard with Claude (optional step)."""

from __future__ import annotations

from .storyboard import Storyboard

MODEL = "claude-opus-5-5"

SYSTEM = """You are a film director writing shot lists for Google Flow (Veo video model).
Each scene becomes ONE short clip (4-8 seconds). Rules:
- Write every `prompt` and `image_prompt` in English (Veo understands English best), even if the idea is in another language.
- `prompt` = one clear action + one camera move (e.g. "slow dolly-in", "handheld tracking shot"), plus sound if useful. No scene cuts inside one clip.
- Prefer mode "keyframe": `image_prompt` describes the opening frame as a still photo (subject, setting, lighting, lens); `prompt` describes the motion from that frame.
- Define recurring people in `characters` with a fixed, specific look, and refer to them by exact name in prompts.
- Put the shared visual look in `style` once instead of repeating it.
- `narration` is an optional short subtitle line in the SAME language as the user's idea.
- Scene ids: s01, s02, ...
- Do not set project_id or image/end_image."""


def plan(idea: str, *, scenes: int = 6, aspect: str = "16:9", language_hint: str | None = None) -> Storyboard:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("cần cài thêm: pip install 'film-maker-ai[ai]' (gói anthropic)") from exc

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    user = f"Idea: {idea}\nNumber of scenes: {scenes}\nAspect ratio: {aspect}"
    if language_hint:
        user += f"\nNarration language: {language_hint}"

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
