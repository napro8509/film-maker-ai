"""Structured and free-text AI calls (Claude or Gemini Pro), with optional pictures.

Supports two AI models per project (`ai_model`):
- `claude` (default): Claude via Claude Code CLI subscription or Anthropic API.
- `gemini-pro`: Google Gemini Pro (`gemini-2.5-pro`) via Gemini REST API (`GEMINI_API_KEY`) or Gemini CLI (`gemini`).

Pictures are shrunk to small JPEGs first: the reviews and QC only need to see what is in the frame,
and small files keep calls fast.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Sequence, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

IMAGE_MAX = 768  # longest side of a picture sent to AI

DEFAULT_AI_MODEL = "claude"
GEMINI_PRO = "gemini-pro"
GEMINI_MODEL = "gemini-2.5-pro"

AI_MODELS: dict[str, dict[str, str]] = {
    "claude": {
        "key": "claude",
        "label": "Claude",
        "description": "Claude (qua gói Claude Code hoặc Anthropic API) — mặc định",
    },
    "gemini-pro": {
        "key": "gemini-pro",
        "label": "Gemini Pro",
        "description": "Google Gemini 2.5 Pro (qua Gemini API key hoặc Gemini CLI)",
    },
}

_GEMINI_ALIASES = frozenset({"gemini", "gemini-pro", "gemini_pro", "gemini-2.5-pro", "geminipro"})


def clean_ai_model(v: object) -> str | None:
    """Validate/normalize an AI model choice -> 'claude' | 'gemini-pro' | None."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, str):
        s = v.strip().lower()
        if s == "claude":
            return "claude"
        if s in _GEMINI_ALIASES:
            return GEMINI_PRO
    raise ValueError(f"must be one of: {', '.join(AI_MODELS)}")


def effective_ai_model(v: object) -> str:
    """Normalized AI model, defaulting to 'claude' when unset."""
    return clean_ai_model(v) or DEFAULT_AI_MODEL


def ai_model_label(v: object) -> str:
    """Human-readable label ('Claude' or 'Gemini Pro')."""
    return AI_MODELS[effective_ai_model(v)]["label"]


def gemini_model_id() -> str:
    return (os.environ.get("FILMMAKER_GEMINI_MODEL") or GEMINI_MODEL).strip() or GEMINI_MODEL


def resolve_gemini_key(gemini_api_key: str | None = None) -> str | None:
    key = (gemini_api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    return key or None


def gemini_cli_binary() -> str | None:
    val = os.environ.get("GEMINI_BIN", "gemini")
    return shutil.which(val) if val else None


def gemini_cli_status() -> dict[str, Any]:
    """{"available": bool, "logged_in": bool, "method": str | None} — fast, never blocks."""
    binary = gemini_cli_binary()
    if not binary:
        return {"available": False, "logged_in": False, "method": None}
    override = os.environ.get("GEMINI_CLI_LOGGED_IN")
    if override == "1":
        return {"available": True, "logged_in": True, "method": "gemini-cli"}
    if override == "0":
        return {"available": True, "logged_in": False, "method": None}
    gemini_dir = Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()) / ".gemini"
    try:
        if (gemini_dir / "oauth_creds.json").is_file() and (gemini_dir / "oauth_creds.json").stat().st_size > 0:
            return {"available": True, "logged_in": True, "method": "google-oauth"}
    except OSError:
        pass
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return {"available": True, "logged_in": True, "method": "api-key"}
    return {"available": True, "logged_in": False, "method": None}


def pick_backend(pref: str = "auto", api_key: str | None = None, *,
                 ai_model: str | None = None, gemini_api_key: str | None = None) -> str | None:
    """Resolve the backend that can run now for `ai_model` ('claude' default or 'gemini-pro'), or None."""
    model = effective_ai_model(ai_model)
    if model == GEMINI_PRO:
        has_key = bool(resolve_gemini_key(gemini_api_key))
        cli_st = gemini_cli_status()
        has_cli = bool(cli_st["available"] and cli_st["logged_in"])
        if pref in ("gemini_api", "api"):
            return "gemini_api" if has_key else None
        if pref == "gemini_cli":
            return "gemini_cli" if gemini_cli_binary() else None
        if has_key:
            return "gemini_api"
        if has_cli:
            return "gemini_cli"
        return None

    from .planner import claude_code_binary

    has_key = bool(api_key or os.environ.get("ANTHROPIC_API_KEY"))
    if pref == "claude_code":
        return "claude_code" if claude_code_binary() else None
    if pref == "api":
        return "api" if has_key else None
    if claude_code_binary():
        return "claude_code"
    return "api" if has_key else None


def not_ready_message(ai_model: str | None = None) -> str:
    if effective_ai_model(ai_model) == GEMINI_PRO:
        return ("Chưa dùng được Gemini Pro: thêm Gemini API key (GEMINI_API_KEY) trong Cài đặt, "
                "hoặc cài và đăng nhập Gemini CLI.")
    return "Chưa dùng được Claude: cài Claude Code và đăng nhập, hoặc thêm Anthropic API key."


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
        backend: str = "auto", api_key: str | None = None, timeout: int = 900, effort: str = "medium",
        ai_model: str | None = None, gemini_api_key: str | None = None) -> T:
    """Ask the configured AI model (Claude or Gemini Pro) and parse the answer into `schema`."""
    model = effective_ai_model(ai_model)
    chosen = pick_backend(backend, api_key, ai_model=model, gemini_api_key=gemini_api_key)
    if chosen is None:
        raise RuntimeError(not_ready_message(model))
    images = [(label, Path(p)) for label, p in images if p and Path(p).is_file()]
    with tempfile.TemporaryDirectory(prefix="fm-ai-") as tmp:
        small = [(label, shrink(p, Path(tmp) / "img" / f"{i:02d}.jpg")) for i, (label, p) in enumerate(images)]
        if model == GEMINI_PRO:
            raw_schema = schema.model_json_schema()
            if chosen == "gemini_cli":
                raw = _ask_gemini_cli(system, user, raw_schema, small, Path(tmp), timeout)
            else:
                raw = _ask_gemini_api_json(system, user, raw_schema, small, gemini_api_key, timeout)
            return schema.model_validate(raw)
        if chosen == "claude_code":
            return _ask_claude_code(system, user, schema, small, Path(tmp), timeout)
        return _ask_api(system, user, schema, small, api_key, effort)


def ask_text(system: str, user: str, *, images: Sequence[tuple[str, Path]] = (), backend: str = "auto",
             api_key: str | None = None, timeout: int = 900, effort: str = "medium",
             ai_model: str | None = None, gemini_api_key: str | None = None) -> str:
    """Like `ask`, but the answer is free text (chat)."""
    model = effective_ai_model(ai_model)
    chosen = pick_backend(backend, api_key, ai_model=model, gemini_api_key=gemini_api_key)
    if chosen is None:
        raise RuntimeError(not_ready_message(model))
    images = [(label, Path(p)) for label, p in images if p and Path(p).is_file()]
    with tempfile.TemporaryDirectory(prefix="fm-ai-") as tmp:
        small = [(label, shrink(p, Path(tmp) / "img" / f"{i:02d}.jpg")) for i, (label, p) in enumerate(images)]
        if model == GEMINI_PRO:
            if chosen == "gemini_cli":
                return _ask_gemini_cli(system, user, None, small, Path(tmp), timeout)
            return _ask_gemini_api_text(system, user, small, gemini_api_key, timeout)
        if chosen == "claude_code":
            return _ask_claude_code(system, user, None, small, Path(tmp), timeout)
        return _ask_api_text(system, user, small, api_key, effort)


def draft_json(system: str, user: str, schema: type[BaseModel] | dict[str, Any], *,
               images: Sequence[tuple[str, Path]] = (), backend: str = "auto",
               gemini_api_key: str | None = None, timeout: int = 900,
               max_output_tokens: int = 32768) -> dict[str, Any]:
    """Structured JSON dict call to Gemini Pro (via Gemini API or Gemini CLI)."""
    chosen = pick_backend(backend, ai_model=GEMINI_PRO, gemini_api_key=gemini_api_key)
    if chosen is None:
        raise RuntimeError(not_ready_message(GEMINI_PRO))
    raw_schema = schema if isinstance(schema, dict) else schema.model_json_schema()
    valid_imgs = [(label, Path(p)) for label, p in images if p and Path(p).is_file()]
    with tempfile.TemporaryDirectory(prefix="fm-gemini-") as tmp:
        small = [(label, shrink(p, Path(tmp) / "img" / f"{i:02d}.jpg")) for i, (label, p) in enumerate(valid_imgs)]
        if chosen == "gemini_cli":
            out = _ask_gemini_cli(system, user, raw_schema, small, Path(tmp), timeout)
        else:
            out = _ask_gemini_api_json(system, user, raw_schema, small, gemini_api_key, timeout,
                                       max_output_tokens=max_output_tokens)
    if not isinstance(out, dict):
        raise RuntimeError("Gemini Pro trả về dữ liệu không đúng định dạng JSON object.")
    return out


# --- Claude backends -----------------------------------------------------------


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


# --- Gemini Pro backends -------------------------------------------------------


def _extract_json(text: str) -> Any:
    """Parse JSON from model output, stripping optional Markdown code fences."""
    s = (text or "").strip()
    if not s:
        raise ValueError("empty response")
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    try:
        return json.loads(s)
    except ValueError:
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end > start:
            return json.loads(s[start:end + 1])
        raise


def _gemini_http_post(url: str, payload: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    """Low-level HTTPS POST to Google Generative Language API."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(err_body)
            detail = str((parsed.get("error") or {}).get("message") or err_body)[:500]
        except Exception:
            detail = (err_body or str(exc))[:500]
        raise RuntimeError(f"Gemini API lỗi (HTTP {exc.code}): {detail}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Không kết nối được Gemini API: {exc.reason}") from None
    except TimeoutError as exc:
        raise RuntimeError("Gemini API chạy quá lâu; hãy thử lại.") from exc
    except OSError as exc:
        raise RuntimeError(f"Lỗi mạng khi gọi Gemini API: {exc}") from exc
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(f"Gemini API trả về dữ liệu không hợp lệ: {raw[:300]}") from exc


def _gemini_parts(user: str, images: list[tuple[str, Path]]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for label, p in images:
        if label:
            parts.append({"text": label})
        parts.append({
            "inlineData": {
                "mimeType": "image/jpeg",
                "data": base64.b64encode(p.read_bytes()).decode("ascii"),
            }
        })
    parts.append({"text": user})
    return parts


def _gemini_generate(system: str, user: str, images: list[tuple[str, Path]], *,
                     json_schema: dict[str, Any] | None, gemini_api_key: str | None,
                     timeout: int, max_output_tokens: int = 16384) -> str:
    key = resolve_gemini_key(gemini_api_key)
    if not key:
        raise RuntimeError("chưa có GEMINI_API_KEY (thêm trong Cài đặt hoặc biến môi trường GEMINI_API_KEY)")
    model_id = gemini_model_id()
    base = os.environ.get("FILMMAKER_GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    url = f"{base}/models/{model_id}:generateContent"

    gen_cfg: dict[str, Any] = {"maxOutputTokens": max_output_tokens}
    sys_text = system or ""
    if json_schema is not None:
        gen_cfg["responseMimeType"] = "application/json"
        gen_cfg["responseJsonSchema"] = json_schema

    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": _gemini_parts(user, images)}],
        "generationConfig": gen_cfg,
    }
    if sys_text:
        payload["systemInstruction"] = {"parts": [{"text": sys_text}]}

    try:
        data = _gemini_http_post(url, payload, key, timeout)
    except RuntimeError as exc:
        # Fallback if a custom/older Gemini model rejects `responseJsonSchema`
        if json_schema is not None and ("responseJsonSchema" in str(exc) or "response_json_schema" in str(exc)):
            fallback_cfg = {"maxOutputTokens": max_output_tokens, "responseMimeType": "application/json"}
            schema_prompt = f"{sys_text}\n\nRespond ONLY with valid JSON conforming to this JSON Schema:\n{json.dumps(json_schema)}".strip()
            fallback_payload = {
                "contents": payload["contents"],
                "generationConfig": fallback_cfg,
                "systemInstruction": {"parts": [{"text": schema_prompt}]},
            }
            data = _gemini_http_post(url, fallback_payload, key, timeout)
        else:
            raise

    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise RuntimeError(f"Gemini Pro từ chối yêu cầu ({block}); hãy thử diễn đạt lại.")
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini Pro không trả về kết quả nào.")
    cand = candidates[0] or {}
    finish = str(cand.get("finishReason") or "")
    if finish in ("SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
        raise RuntimeError(f"Gemini Pro chặn nội dung ({finish}); hãy thử diễn đạt lại.")
    parts = ((cand.get("content") or {}).get("parts")) or []
    # Filter out Gemini 2.5 thinking parts (`"thought": true`) so only the actual answer is read.
    text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict) and not p.get("thought")).strip()
    if not text:
        if finish == "MAX_TOKENS":
            raise RuntimeError("Gemini Pro vượt giới hạn độ dài đầu ra; hãy giảm số cảnh.")
        raise RuntimeError(f"Gemini Pro không trả lời được ({finish or 'trống'}).")
    return text


def _ask_gemini_api_text(system: str, user: str, images: list[tuple[str, Path]],
                         gemini_api_key: str | None, timeout: int) -> str:
    return _gemini_generate(system, user, images, json_schema=None,
                            gemini_api_key=gemini_api_key, timeout=timeout, max_output_tokens=16384)


def _ask_gemini_api_json(system: str, user: str, json_schema: dict[str, Any], images: list[tuple[str, Path]],
                         gemini_api_key: str | None, timeout: int, max_output_tokens: int = 32768) -> Any:
    text = _gemini_generate(system, user, images, json_schema=json_schema,
                            gemini_api_key=gemini_api_key, timeout=timeout, max_output_tokens=max_output_tokens)
    try:
        return _extract_json(text)
    except ValueError as exc:
        raise RuntimeError(f"Gemini Pro trả về JSON không hợp lệ: {text[:300]}") from exc


def _ask_gemini_cli(system: str, user: str, json_schema: dict[str, Any] | None,
                    images: list[tuple[str, Path]], cwd: Path, timeout: int) -> Any:
    binary = gemini_cli_binary()
    if not binary:
        raise RuntimeError("không thấy lệnh `gemini` (Gemini CLI)")
    sections: list[str] = []
    if system:
        sections.append(f"System instructions:\n{system}")
    sections.append(user)
    if images:
        listing = "\n".join(f"- {label}: @{p.relative_to(cwd).as_posix()}" for label, p in images)
        sections.append(f"Pictures to inspect in the workspace:\n{listing}")
    if json_schema is not None:
        sections.append(
            "IMPORTANT: Return ONLY a single valid JSON object (no markdown fences, no commentary) "
            f"strictly conforming to this JSON Schema:\n{json.dumps(json_schema)}"
        )
    prompt = "\n\n".join(sections)
    cmd = [binary, "-p", prompt, "-m", gemini_model_id(), "-o", "json"]
    if images:
        cmd += ["--approval-mode", "plan"]
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Gemini CLI chạy quá lâu.") from exc
    except OSError as exc:
        raise RuntimeError(f"Không chạy được Gemini CLI: {exc}") from exc

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0 and not stdout:
        tail = (stderr or stdout)[-400:]
        if "login" in tail.lower() or "auth" in tail.lower():
            tail += " — chạy `gemini` trong Terminal để đăng nhập hoặc thêm GEMINI_API_KEY."
        raise RuntimeError(f"Gemini CLI lỗi (exit {proc.returncode}): {tail}")

    reply_text = stdout
    try:
        envelope = json.loads(stdout)
        if isinstance(envelope, dict):
            if envelope.get("error"):
                err_obj = envelope["error"]
                err_msg = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj)
                raise RuntimeError(f"Gemini CLI không trả lời được: {str(err_msg)[:400]}")
            if "structured_output" in envelope and envelope["structured_output"] is not None:
                return envelope["structured_output"] if json_schema is not None else json.dumps(envelope["structured_output"], ensure_ascii=False)
            if isinstance(envelope.get("response"), str):
                reply_text = envelope["response"].strip()
            elif isinstance(envelope.get("result"), str):
                reply_text = envelope["result"].strip()
    except ValueError:
        pass

    if json_schema is None:
        if not reply_text:
            raise RuntimeError(f"Gemini CLI không trả lời được: {(stderr or 'không có kết quả')[-400:]}")
        return reply_text

    try:
        return _extract_json(reply_text)
    except ValueError:
        raise RuntimeError(f"Gemini CLI trả về JSON không hợp lệ: {(reply_text or stderr)[:400]}") from None
