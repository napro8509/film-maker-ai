"""One structured call to Claude, with optional pictures, through Claude Code (subscription) or the API.

Used by the review agents (reviewer.py). The planner and the editor keep their own calls.
Pictures are shrunk to small JPEGs first: the reviews only need to see what is in the frame,
and small files keep the calls fast.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

IMAGE_MAX = 768  # longest side of a picture sent to Claude


def pick_backend(pref: str = "auto", api_key: str | None = None) -> str | None:
    """auto | claude_code | api -> the backend that can run now, or None."""
    from .planner import claude_code_binary

    has_key = bool(api_key or os.environ.get("ANTHROPIC_API_KEY"))
    if pref == "claude_code":
        return "claude_code" if claude_code_binary() else None
    if pref == "api":
        return "api" if has_key else None
    if claude_code_binary():
        return "claude_code"
    return "api" if has_key else None


def shrink(src: Path, dest: Path, size: int = IMAGE_MAX) -> Path:
    """`src` (picture) -> small JPEG at `dest`. Falls back to a plain copy when ffmpeg can't read it."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale='if(gt(iw,ih),min({size},iw),-2)':'if(gt(iw,ih),-2,min({size},ih))'"
    try:
        proc = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-vf", vf, "-frames:v", "1",
                               "-q:v", "4", str(dest)], capture_output=True, timeout=60)
        if proc.returncode == 0 and dest.is_file():
            return dest
    except (OSError, subprocess.TimeoutExpired):
        pass
    shutil.copyfile(src, dest)
    return dest


def ask(system: str, user: str, schema: type[T], *, images: Sequence[tuple[str, Path]] = (),
        backend: str = "auto", api_key: str | None = None, timeout: int = 900, effort: str = "medium") -> T:
    """Ask Claude and parse the answer into `schema`. `images` = [(label, file)], shown in that order.
    RuntimeError when Claude cannot be reached or does not answer."""
    chosen = pick_backend(backend, api_key)
    if chosen is None:
        raise RuntimeError("Chưa dùng được Claude: cài Claude Code và đăng nhập, hoặc thêm Anthropic API key.")
    images = [(label, Path(p)) for label, p in images if p and Path(p).is_file()]
    with tempfile.TemporaryDirectory(prefix="fm-ai-") as tmp:
        small = [(label, shrink(p, Path(tmp) / "img" / f"{i:02d}.jpg")) for i, (label, p) in enumerate(images)]
        if chosen == "claude_code":
            return _ask_claude_code(system, user, schema, small, Path(tmp), timeout)
        return _ask_api(system, user, schema, small, api_key, effort)


def ask_text(system: str, user: str, *, images: Sequence[tuple[str, Path]] = (), backend: str = "auto",
             api_key: str | None = None, timeout: int = 900, effort: str = "medium") -> str:
    """Like `ask`, but the answer is free text (chat). Long Markdown answers do not always come back in a
    JSON shape, so chat does not ask for one. RuntimeError when Claude cannot be reached or does not answer."""
    chosen = pick_backend(backend, api_key)
    if chosen is None:
        raise RuntimeError("Chưa dùng được Claude: cài Claude Code và đăng nhập, hoặc thêm Anthropic API key.")
    images = [(label, Path(p)) for label, p in images if p and Path(p).is_file()]
    with tempfile.TemporaryDirectory(prefix="fm-ai-") as tmp:
        small = [(label, shrink(p, Path(tmp) / "img" / f"{i:02d}.jpg")) for i, (label, p) in enumerate(images)]
        if chosen == "claude_code":
            return _ask_claude_code(system, user, None, small, Path(tmp), timeout)
        return _ask_api_text(system, user, small, api_key, effort)


def _ask_claude_code(system: str, user: str, schema: type[T] | None, images: list[tuple[str, Path]], cwd: Path,
                     timeout: int) -> Any:
    from .planner import _subscription_env, claude_code_binary

    binary = claude_code_binary()
    if not binary:
        raise RuntimeError("không thấy lệnh `claude` (Claude Code)")
    prompt = user
    tools = ""
    if images:
        listing = "\n".join(f"- {label}: ./{p.relative_to(cwd).as_posix()}" for label, p in images)
        prompt = (f"{user}\n\nPictures to look at (open EVERY file with the Read tool before answering; "
                  f"they are part of the task):\n{listing}")
        tools = "Read"
    cmd = [binary, "-p", prompt, "--output-format", "json"]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema.model_json_schema())]
    cmd += ["--system-prompt", system, "--tools", tools, "--strict-mcp-config", "--no-session-persistence",
           # the user's own hooks (memory plugins, ...) would add their messages to the conversation
           "--settings", json.dumps({"disableAllHooks": True})]
    if tools:
        cmd += ["--allowedTools", tools]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=_subscription_env())
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Claude Code chạy quá lâu.") from exc
    except OSError as exc:  # the `claude` binary vanished or cannot run
        raise RuntimeError(f"Không chạy được Claude Code: {exc}") from exc
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError(f"Claude Code lỗi (exit {proc.returncode}): "
                           f"{(proc.stderr or proc.stdout).strip()[-400:]}") from None
    if schema is None and not data.get("is_error") and str(data.get("result") or "").strip():
        return str(data["result"]).strip()
    if data.get("is_error") or not data.get("structured_output"):
        msg = str(data.get("result") or data.get("subtype") or "không có kết quả")[:400]
        if "login" in msg.lower() or "auth" in msg.lower():
            msg += " — chạy `claude` trong Terminal và đăng nhập (/login)."
        raise RuntimeError(f"Claude Code không trả lời được: {msg}")
    return schema.model_validate(data["structured_output"])


def _api_client(api_key: str | None):
    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("cần cài thêm: pip install 'film-maker-ai[ai]' (gói anthropic)") from exc
    return anthropic.Anthropic()


def _api_content(user: str, images: list[tuple[str, Path]]) -> list[dict]:
    content: list[dict] = []
    for label, p in images:
        content.append({"type": "text", "text": label})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(p.read_bytes()).decode()}})
    content.append({"type": "text", "text": user})
    return content


def _ask_api_text(system: str, user: str, images: list[tuple[str, Path]], api_key: str | None, effort: str) -> str:
    from .planner import MODEL

    response = _api_client(api_key).messages.create(
        model=MODEL, max_tokens=16000, system=system, messages=[{"role": "user", "content": _api_content(user, images)}],
        output_config={"effort": effort},
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"}, extra_body={"fallbacks": "default"},
    )
    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()
    if response.stop_reason == "refusal" or not text:
        raise RuntimeError("Claude không trả lời được câu hỏi này.")
    return text


def _ask_api(system: str, user: str, schema: type[T], images: list[tuple[str, Path]], api_key: str | None,
             effort: str) -> T:
    from .planner import MODEL

    content = _api_content(user, images)
    response = _api_client(api_key).messages.parse(
        model=MODEL, max_tokens=16000, system=system, messages=[{"role": "user", "content": content}],
        output_format=schema, output_config={"effort": effort},
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"}, extra_body={"fallbacks": "default"},
    )
    if response.stop_reason == "refusal" or not response.parsed_output:
        raise RuntimeError("Claude không trả lời được yêu cầu review này.")
    return response.parsed_output
