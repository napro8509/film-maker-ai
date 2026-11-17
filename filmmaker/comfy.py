"""ComfyUI (https://github.com/Comfy-Org/ComfyUI) workflow graph & server integration.

Provides two tightly integrated layers for the Storyboard Board ("Bảng"):
1. Bidirectional ComfyUI Workflow JSON (LiteGraph UI `workflow.json` v0.4 and `/prompt` API `workflow_api.json`)
   compilation and import for a Film Maker `Storyboard` + `ui.json` layout.
2. Zero-extra-dependency HTTP client (`urllib.request`) for talking to a local or remote ComfyUI instance
   (`/system_stats`, `/object_info`, `/queue`, `/upload/image`, `/prompt`, `/history`, `/view`, `/interrupt`)
   and running portrait, location, keyframe, and video clip jobs with live `progress.Reporter` updates.
"""

from __future__ import annotations

import copy
import json
import math
import mimetypes
import os
import random
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

from . import progress as prog
from .pipeline import DONE, State
from .storyboard import Storyboard, file_sha

DEFAULT_COMFYUI_URL = "http://127.0.0.1:8188"
BOARD_ENGINES = ("flow", "comfyui")
DEFAULT_STEPS = 20
DEFAULT_CFG = 7.0
DEFAULT_NEGATIVE = (
    "text, watermark, subtitle, caption, logo, signature, blurry, deformed, extra limbs, bad anatomy, low quality"
)

# ComfyUI LiteGraph slot types used across the Film Maker node graph.
SLOT_MODEL = "MODEL"
SLOT_CHARACTER = "CHARACTER"
SLOT_LOCATION = "LOCATION"
SLOT_IMAGE = "IMAGE"
SLOT_CONDITIONING = "CONDITIONING"
SLOT_VIDEO = "VIDEO"

VIDEO_NODE_CLASSES = (
    "WanImageToVideo",
    "SVD_img2vid_Conditioning",
    "LTXVConditioning",
    "VHS_VideoCombine",
    "SaveWEBM",
    "SaveAnimatedWEBP",
)


class ComfyError(RuntimeError):
    """Raised when a ComfyUI server request fails or rejects a workflow prompt."""

    def __init__(self, message: str, *, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


# --- settings & validation ----------------------------------------------------


def clean_comfyui_url(value: object) -> str:
    """Validate and normalize a ComfyUI server URL; None/blank -> DEFAULT_COMFYUI_URL."""
    if value is None:
        return DEFAULT_COMFYUI_URL
    if not isinstance(value, str):
        raise ValueError("Địa chỉ ComfyUI phải là chuỗi URL")
    if "\n" in value or "\r" in value or any(ord(ch) < 32 for ch in value):
        raise ValueError("Địa chỉ ComfyUI chứa ký tự không hợp lệ")
    raw = value.strip()
    if not raw:
        return DEFAULT_COMFYUI_URL
    if len(raw) > 500:
        raise ValueError("Địa chỉ ComfyUI quá dài (tối đa 500 ký tự)")
    if "://" not in raw:
        raw = "http://" + raw
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise ValueError("Địa chỉ ComfyUI chỉ hỗ trợ http:// hoặc https://")
    if not parts.hostname:
        raise ValueError("Địa chỉ ComfyUI thiếu tên máy chủ (ví dụ: 127.0.0.1:8188)")
    if parts.username or parts.password:
        raise ValueError("Không đặt mật khẩu trực tiếp trong địa chỉ ComfyUI")
    if parts.query or parts.fragment:
        raise ValueError("Địa chỉ ComfyUI không được chứa query/fragment")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("Cổng (port) của ComfyUI không hợp lệ") from exc
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    netloc = f"{host}:{port}" if port else host
    path = parts.path.rstrip("/")
    return f"{parts.scheme}://{netloc}{path}"


def clean_board_engine(value: object) -> str:
    """Normalize board execution engine: 'flow' (default) or 'comfyui'."""
    if value is None:
        return "flow"
    if not isinstance(value, str):
        raise ValueError("Engine bảng không hợp lệ")
    v = value.strip().lower()
    if v in ("", "flow", "gflow", "google-flow", "google_flow"):
        return "flow"
    if v in ("comfyui", "comfy", "comfy-ui", "comfy_ui"):
        return "comfyui"
    raise ValueError("Engine bảng chỉ nhận 'flow' hoặc 'comfyui'")


def clean_steps(value: object) -> int:
    if value is None or value == "":
        return DEFAULT_STEPS
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = int(str(value).strip())
        except ValueError as exc:
            raise ValueError("Số bước (steps) ComfyUI phải là số nguyên") from exc
    n = int(value)
    return max(1, min(150, n))


def clean_cfg(value: object) -> float:
    if value is None or value == "":
        return DEFAULT_CFG
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(str(value).strip())
        except ValueError as exc:
            raise ValueError("CFG ComfyUI phải là số") from exc
    f = float(value)
    if not math.isfinite(f):
        raise ValueError("CFG ComfyUI phải là số hữu hạn")
    return round(max(1.0, min(30.0, f)), 2)


def comfyui_config(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Effective ComfyUI settings merged with environment overrides."""
    s = settings or {}
    env_url = os.environ.get("COMFYUI_URL", "").strip()
    try:
        url = clean_comfyui_url(env_url or s.get("comfyui_url"))
    except ValueError:
        url = DEFAULT_COMFYUI_URL
    try:
        engine = clean_board_engine(s.get("board_engine"))
    except ValueError:
        engine = "flow"
    checkpoint = str(s.get("comfyui_checkpoint") or "").strip()
    return {
        "url": url,
        "engine": engine,
        "checkpoint": checkpoint,
        "steps": clean_steps(s.get("comfyui_steps")),
        "cfg": clean_cfg(s.get("comfyui_cfg")),
    }


def aspect_dimensions(aspect: str | None) -> tuple[int, int]:
    """Default generation resolution (divisible by 64) for 16:9 or 9:16."""
    return (576, 1024) if aspect == "9:16" else (1024, 576)


# --- HTTP client for ComfyUI --------------------------------------------------


class ComfyClient:
    """Lightweight stdlib client for a ComfyUI HTTP API server."""

    def __init__(self, base_url: str = DEFAULT_COMFYUI_URL, *, timeout: float = 15.0):
        self.base_url = clean_comfyui_url(base_url)
        self.timeout = timeout

    def _url(self, path: str, query: dict[str, Any] | None = None) -> str:
        u = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            qs = urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
            if qs:
                u = f"{u}?{qs}"
        return u

    def request_json(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        query: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        data_bytes = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self._url(path, query), data=data_bytes, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            raw = ""
            payload = None
            try:
                raw = exc.read().decode("utf-8", errors="replace")
                payload = json.loads(raw)
            except Exception:
                pass
            detail = _extract_comfy_error(payload) or raw.strip()[:300] or f"HTTP {exc.code}"
            raise ComfyError(f"ComfyUI trả lỗi ({exc.code}): {detail}", status=exc.code, payload=payload) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ComfyError(f"Không kết nối được ComfyUI tại {self.base_url}: {reason}") from exc
        except ValueError as exc:
            raise ComfyError(f"Phản hồi từ ComfyUI không phải JSON hợp lệ: {exc}") from exc

    def request_bytes(self, path: str, *, query: dict[str, Any] | None = None, timeout: float | None = None) -> bytes:
        req = urllib.request.Request(self._url(path, query), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise ComfyError(f"Không tải được kết quả từ ComfyUI (HTTP {exc.code})", status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ComfyError(f"Mất kết nối tới ComfyUI khi tải file: {getattr(exc, 'reason', exc)}") from exc

    def system_stats(self, *, timeout: float = 2.5) -> dict[str, Any]:
        res = self.request_json("GET", "/system_stats", timeout=timeout)
        return res if isinstance(res, dict) else {}

    def queue_info(self, *, timeout: float = 2.5) -> dict[str, Any]:
        res = self.request_json("GET", "/queue", timeout=timeout)
        return res if isinstance(res, dict) else {}

    def object_info(self, node_class: str | None = None, *, timeout: float = 4.0) -> dict[str, Any]:
        path = f"/object_info/{urllib.parse.quote(node_class, safe='')}" if node_class else "/object_info"
        res = self.request_json("GET", path, timeout=timeout)
        return res if isinstance(res, dict) else {}

    def available_checkpoints(self, *, timeout: float = 3.0) -> list[str]:
        try:
            info = self.object_info("CheckpointLoaderSimple", timeout=timeout)
        except ComfyError:
            return []
        node = info.get("CheckpointLoaderSimple") if isinstance(info, dict) else None
        req = ((node or {}).get("input") or {}).get("required") or {}
        ckpt = req.get("ckpt_name")
        if isinstance(ckpt, list) and ckpt:
            first = ckpt[0]
            if isinstance(first, list):
                return [str(x) for x in first if isinstance(x, str) and x.strip()]
        return []

    def available_video_nodes(self, *, timeout: float = 3.0) -> list[str]:
        found: list[str] = []
        for cls in VIDEO_NODE_CLASSES:
            try:
                info = self.object_info(cls, timeout=timeout)
                if isinstance(info, dict) and cls in info:
                    found.append(cls)
            except ComfyError:
                continue
        return found

    def upload_image(self, path: Path, *, subfolder: str = "filmmaker", overwrite: bool = True) -> dict[str, Any]:
        p = Path(path)
        if not p.is_file():
            raise ComfyError(f"Không tìm thấy ảnh tham chiếu: {p}")
        boundary = f"----FilmMakerBoundary{uuid.uuid4().hex}"
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        file_bytes = p.read_bytes()
        parts: list[bytes] = []

        def add_field(name: str, val: str) -> None:
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n'.encode("utf-8"))

        add_field("type", "input")
        if subfolder:
            add_field("subfolder", subfolder)
        add_field("overwrite", "true" if overwrite else "false")
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="image"; filename="{p.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8")
        )
        parts.append(file_bytes)
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        body = b"".join(parts)
        req = urllib.request.Request(
            self._url("/upload/image"),
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=max(self.timeout, 30.0)) as resp:
                out = json.loads(resp.read().decode("utf-8", errors="replace"))
                return out if isinstance(out, dict) else {"name": p.name}
        except urllib.error.HTTPError as exc:
            raise ComfyError(f"ComfyUI từ chối tải ảnh {p.name} (HTTP {exc.code})", status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ComfyError(f"Lỗi mạng khi tải ảnh lên ComfyUI: {getattr(exc, 'reason', exc)}") from exc

    def queue_prompt(self, prompt: dict[str, Any], *, client_id: str | None = None) -> str:
        payload: dict[str, Any] = {"prompt": prompt, "client_id": client_id or f"filmmaker-{uuid.uuid4().hex[:10]}"}
        res = self.request_json("POST", "/prompt", payload, timeout=max(self.timeout, 30.0))
        if not isinstance(res, dict) or not res.get("prompt_id"):
            err = _extract_comfy_error(res) or "ComfyUI không trả về prompt_id"
            raise ComfyError(err, payload=res)
        if res.get("node_errors"):
            err = _extract_comfy_error(res)
            if err:
                raise ComfyError(f"Workflow ComfyUI có lỗi node: {err}", payload=res)
        return str(res["prompt_id"])

    def interrupt(self) -> None:
        try:
            self.request_json("POST", "/interrupt", {}, timeout=5.0)
        except ComfyError:
            pass

    def wait_for_outputs(
        self,
        prompt_id: str,
        *,
        poll_interval: float = 0.5,
        timeout: float = 600.0,
        should_stop: Callable[[], bool] = lambda: False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> list[dict[str, Any]]:
        """Poll `/history/{prompt_id}` until outputs arrive, an error occurs, or `should_stop()` fires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if should_stop():
                self.interrupt()
                raise ComfyError("Đã dừng theo yêu cầu người dùng")
            hist = self.request_json("GET", f"/history/{urllib.parse.quote(prompt_id, safe='')}", timeout=10.0)
            entry = hist.get(prompt_id) if isinstance(hist, dict) else None
            if isinstance(entry, dict):
                st = entry.get("status") or {}
                if isinstance(st, dict) and st.get("status_str") == "error":
                    msg = _extract_history_error(st) or "ComfyUI báo lỗi khi thực thi workflow"
                    raise ComfyError(msg, payload=entry)
                outputs = entry.get("outputs")
                if isinstance(outputs, dict) and outputs:
                    files = _collect_output_files(outputs)
                    if files:
                        return files
                if isinstance(st, dict) and st.get("completed") is True:
                    files = _collect_output_files(outputs or {})
                    if files:
                        return files
                    raise ComfyError("ComfyUI chạy xong nhưng không tạo ra file ảnh/video nào", payload=entry)
            sleep(poll_interval)
        self.interrupt()
        raise ComfyError(f"Quá thời gian chờ ComfyUI xử lý ({int(timeout)}s)")

    def download_output(self, item: dict[str, Any], dest: Path) -> Path:
        """Download one `/view` entry from ComfyUI and write it atomically to `dest`."""
        filename = str(item.get("filename") or "")
        if not filename:
            raise ComfyError("Kết quả ComfyUI thiếu filename")
        raw = self.request_bytes(
            "/view",
            query={
                "filename": filename,
                "subfolder": item.get("subfolder") or "",
                "type": item.get("type") or "output",
            },
            timeout=60.0,
        )
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src_ext = Path(filename).suffix.lower()
        dst_ext = dest.suffix.lower()
        tmp_raw = dest.with_name(f".{dest.stem}.{uuid.uuid4().hex[:8]}{src_ext or dst_ext}")
        tmp_out = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            if dst_ext == ".mp4" and src_ext != ".mp4":
                tmp_raw.write_bytes(raw)
                _transcode_to_mp4(tmp_raw, tmp_out)
                os.replace(tmp_out, dest)
            elif dst_ext == ".png" and src_ext not in ("", ".png") and shutil.which("ffmpeg"):
                tmp_raw.write_bytes(raw)
                _transcode_to_png(tmp_raw, tmp_out)
                os.replace(tmp_out, dest)
            else:
                tmp_out.write_bytes(raw)
                os.replace(tmp_out, dest)
        finally:
            for p in (tmp_raw, tmp_out):
                try:
                    if p.exists():
                        p.unlink()
                except OSError:
                    pass
        return dest


def _extract_comfy_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if isinstance(err, str) and err.strip():
        return err.strip()
    if isinstance(err, dict):
        msg = err.get("message") or err.get("details") or err.get("type")
        if msg:
            extra = err.get("extra_info")
            if isinstance(extra, dict) and extra.get("exception_message"):
                return f"{msg}: {extra['exception_message']}"
            return str(msg)
    node_errs = payload.get("node_errors")
    if isinstance(node_errs, dict) and node_errs:
        parts = []
        for nid, info in list(node_errs.items())[:3]:
            if isinstance(info, dict):
                cls = info.get("class_type") or f"node #{nid}"
                errs = info.get("errors") or []
                first = errs[0].get("message") if errs and isinstance(errs[0], dict) else str(errs)
                parts.append(f"{cls}: {first}")
        if parts:
            return "; ".join(parts)
    return None


def _extract_history_error(st: dict[str, Any]) -> str | None:
    for item in st.get("messages") or []:
        if isinstance(item, list) and len(item) >= 2 and item[0] == "execution_error" and isinstance(item[1], dict):
            d = item[1]
            node_type = d.get("node_type") or d.get("node_id") or "node"
            exc_msg = d.get("exception_message") or d.get("exception_type") or "lỗi thực thi"
            return f"{node_type}: {str(exc_msg).strip()}"
    return None


def _collect_output_files(outputs: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for _nid, data in outputs.items():
        if not isinstance(data, dict):
            continue
        for key in ("videos", "gifs", "images"):
            items = data.get(key)
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict) and it.get("filename"):
                        out.append({**it, "media_kind": key})
    return out


def _transcode_to_mp4(src: Path, dst: Path) -> None:
    ff = shutil.which("ffmpeg")
    if not ff:
        shutil.copyfile(src, dst)
        return
    proc = subprocess.run(
        [
            ff, "-y", "-i", str(src),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(dst),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0 or not dst.is_file():
        raise ComfyError(f"Không chuyển đổi được video từ ComfyUI sang MP4: {proc.stderr[-240:]}")


def _transcode_to_png(src: Path, dst: Path) -> None:
    ff = shutil.which("ffmpeg")
    if not ff:
        shutil.copyfile(src, dst)
        return
    proc = subprocess.run(
        [ff, "-y", "-i", str(src), "-frames:v", "1", str(dst)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0 or not dst.is_file():
        shutil.copyfile(src, dst)


def render_motion_clip_from_image(
    image_path: Path,
    dest_mp4: Path,
    *,
    duration: int | float = 6,
    aspect: str = "16:9",
    movement: str | None = None,
) -> Path:
    """Synthesize an H.264 MP4 clip from a keyframe image using ffmpeg (used when ComfyUI has no video custom node)."""
    ff = shutil.which("ffmpeg")
    if not ff:
        raise ComfyError("Cần cài ffmpeg để tạo clip MP4 từ ảnh ComfyUI")
    w, h = (720, 1280) if aspect == "9:16" else (1280, 720)
    dur_s = max(2.0, min(30.0, float(duration or 6)))
    fps = 24
    frames = max(24, int(round(dur_s * fps)))
    mv = (movement or "").lower()
    if "out" in mv or "pull" in mv:
        z_expr = f"if(eq(on,1),1.14,max(1.0,zoom-0.14/{frames}))"
        x_expr, y_expr = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif "pan" in mv and "right" in mv:
        z_expr = "1.12"
        x_expr = f"(iw-iw/zoom)*on/{frames}"
        y_expr = "ih/2-(ih/zoom/2)"
    elif "pan" in mv:
        z_expr = "1.12"
        x_expr = f"(iw-iw/zoom)*(1-on/{frames})"
        y_expr = "ih/2-(ih/zoom/2)"
    elif "static" in mv or "locked" in mv:
        z_expr = "1.0"
        x_expr, y_expr = "0", "0"
    else:
        z_expr = f"min(1.14,1.0+0.14*on/{frames})"
        x_expr, y_expr = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"

    sw, sh = int(w * 1.25) // 2 * 2, int(h * 1.25) // 2 * 2
    vf = (
        f"scale={sw}:{sh}:force_original_aspect_ratio=increase,"
        f"crop={sw}:{sh},"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={w}x{h}:fps={fps},"
        f"format=yuv420p"
    )
    dest_mp4 = Path(dest_mp4)
    dest_mp4.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_mp4.with_name(f".{dest_mp4.stem}.{uuid.uuid4().hex[:8]}.mp4")
    try:
        proc = subprocess.run(
            [
                ff, "-y", "-loop", "1", "-i", str(image_path),
                "-vf", vf, "-t", f"{dur_s:.2f}",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(tmp),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0 or not tmp.is_file():
            raise ComfyError(f"ffmpeg không dựng được clip chuyển động: {proc.stderr[-240:]}")
        os.replace(tmp, dest_mp4)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return dest_mp4


def check_status(base_url: str = DEFAULT_COMFYUI_URL, *, timeout: float = 2.0) -> dict[str, Any]:
    """Probe a ComfyUI server and return normalized status metadata (never raises on network errors)."""
    try:
        url = clean_comfyui_url(base_url)
    except ValueError as exc:
        return {
            "online": False,
            "url": str(base_url or DEFAULT_COMFYUI_URL),
            "comfyui_version": None,
            "pytorch_version": None,
            "devices": [],
            "queue_running": 0,
            "queue_pending": 0,
            "checkpoints": [],
            "video_nodes": [],
            "error": str(exc),
        }

    client = ComfyClient(url, timeout=timeout)
    try:
        stats = client.system_stats(timeout=timeout)
    except ComfyError as exc:
        return {
            "online": False,
            "url": url,
            "comfyui_version": None,
            "pytorch_version": None,
            "devices": [],
            "queue_running": 0,
            "queue_pending": 0,
            "checkpoints": [],
            "video_nodes": [],
            "error": str(exc),
        }

    sys_info = stats.get("system") if isinstance(stats.get("system"), dict) else {}
    raw_devs = stats.get("devices") if isinstance(stats.get("devices"), list) else []
    devices = []
    for d in raw_devs[:8]:
        if isinstance(d, dict):
            devices.append({
                "name": str(d.get("name") or d.get("type") or "device"),
                "type": str(d.get("type") or ""),
                "vram_total": d.get("vram_total"),
                "vram_free": d.get("vram_free"),
            })
    q_run = q_pend = 0
    try:
        q = client.queue_info(timeout=timeout)
        q_run = len(q.get("queue_running") or [])
        q_pend = len(q.get("queue_pending") or [])
    except ComfyError:
        pass
    ckpts = client.available_checkpoints(timeout=timeout)
    vid_nodes = client.available_video_nodes(timeout=min(timeout, 1.5))
    return {
        "online": True,
        "url": url,
        "comfyui_version": sys_info.get("comfyui_version"),
        "pytorch_version": sys_info.get("pytorch_version"),
        "devices": devices,
        "queue_running": q_run,
        "queue_pending": q_pend,
        "checkpoints": ckpts,
        "video_nodes": vid_nodes,
        "error": None,
    }


# --- ComfyUI Workflow Graph (LiteGraph v0.4 + API Prompt) ---------------------


def _default_positions(sb: Storyboard) -> dict[str, dict[str, float]]:
    """Deterministic DAG column positions matching the ComfyUI board auto-layout."""
    pos: dict[str, dict[str, float]] = {}
    x_src = 40.0
    x_kf = 380.0
    x_vid = 760.0
    x_out = 1140.0
    y_src = 40.0
    pos["cfg:film"] = {"x": x_src, "y": y_src}
    y_src += 250.0
    for i, c in enumerate(sb.characters):
        key = f"char:{c.name.strip()}" if c.name.strip() else f"char:#{i}"
        pos[key] = {"x": x_src, "y": y_src}
        y_src += 340.0
    for i, loc in enumerate(sb.locations or []):
        key = f"loc:{loc.name.strip()}" if loc.name.strip() else f"loc:#{i}"
        pos[key] = {"x": x_src, "y": y_src}
        y_src += 320.0

    y_sc = 40.0
    row_step = 470.0 if sb.aspect == "9:16" else 340.0
    for sc in sb.scenes:
        if sc.mode == "keyframe":
            pos[f"kf:{sc.id}"] = {"x": x_kf, "y": y_sc}
        pos[f"vid:{sc.id}"] = {"x": x_vid, "y": y_sc}
        y_sc += row_step

    mid_y = max(40.0, (y_sc - row_step + 40.0) / 2.0) if sb.scenes else 40.0
    pos["out:final"] = {"x": x_out, "y": round(mid_y / 8.0) * 8.0}
    return pos


def _continuity_prev_scene(sb: Storyboard, idx: int) -> str | None:
    """Scene ID of `scenes[idx - 1]` when continuity chains its keyframe into `scenes[idx]`."""
    if idx <= 0 or sb.continuity is False:
        return None
    sc = sb.scenes[idx]
    if sc.mode != "keyframe" or sc.continue_previous is False:
        return None
    loc = sb.scene_location(sc)
    if loc is None:
        return None
    prev = sb.scenes[idx - 1]
    if prev.mode != "keyframe" or sb.scene_location(prev) is not loc:
        return None
    return prev.id


def build_comfy_workflow(
    sb: Storyboard,
    board: dict[str, Any] | None = None,
    *,
    scene_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a `Storyboard` + `ui.json` board layout into a genuine ComfyUI v0.4 LiteGraph `workflow.json`."""
    board = board or {}
    saved_cards = board.get("cards") if isinstance(board.get("cards"), dict) else {}
    defaults = _default_positions(sb)
    scene_state = scene_state or {}

    def xy_for(key: str, alt_key: str | None = None) -> list[float]:
        p = saved_cards.get(key) or (saved_cards.get(alt_key) if alt_key else None) or defaults.get(key) or {"x": 40, "y": 40}
        return [float(p.get("x", 40)), float(p.get("y", 40))]

    nodes: list[dict[str, Any]] = []
    links: list[list[Any]] = []
    groups: list[dict[str, Any]] = []
    card_keys: dict[str, str] = {}
    node_by_key: dict[str, dict[str, Any]] = {}
    next_nid = 1
    next_lid = 1

    def add_node(
        fm_key: str,
        comfy_type: str,
        title: str,
        pos: list[float],
        size: list[float],
        inputs: list[dict[str, Any]],
        outputs: list[dict[str, Any]],
        widgets: list[Any],
        color: str,
        bgcolor: str,
        props: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        nonlocal next_nid
        nid = next_nid
        next_nid += 1
        node = {
            "id": nid,
            "type": comfy_type,
            "title": title,
            "pos": pos,
            "size": size,
            "flags": {},
            "order": nid - 1,
            "mode": 0,
            "inputs": inputs,
            "outputs": [{**o, "links": []} for o in outputs],
            "properties": {"Node name for S&R": comfy_type, "fm_key": fm_key, **(props or {})},
            "widgets_values": widgets,
            "color": color,
            "bgcolor": bgcolor,
        }
        nodes.append(node)
        node_by_key[fm_key] = node
        card_keys[str(nid)] = fm_key
        return node

    def connect(src_key: str, src_slot: int, dst_key: str, dst_slot: int, slot_type: str) -> None:
        nonlocal next_lid
        src = node_by_key.get(src_key)
        dst = node_by_key.get(dst_key)
        if not src or not dst:
            return
        if src_slot >= len(src["outputs"]) or dst_slot >= len(dst["inputs"]):
            return
        lid = next_lid
        next_lid += 1
        src["outputs"][src_slot]["links"].append(lid)
        dst["inputs"][dst_slot]["link"] = lid
        links.append([lid, src["id"], src_slot, dst["id"], dst_slot, slot_type])

    # 1. FilmConfig node
    add_node(
        "cfg:film",
        "FilmMakerConfig",
        f"FilmConfig · {sb.title}",
        xy_for("cfg:film"),
        [250, 210],
        [],
        [{"name": "MODEL", "type": SLOT_MODEL, "slot_index": 0}],
        [
            sb.title,
            sb.aspect,
            sb.model or "veo-lite",
            sb.image_model or "nano2",
            sb.style or "",
            sb.continuity is not False,
            sb.use_character_refs is not False,
        ],
        "#2a3352",
        "#1b2035",
    )

    # 2. Character nodes
    char_key_by_fold: dict[str, str] = {}
    for i, c in enumerate(sb.characters):
        serial_key = f"char:{c.name.strip()}" if c.name.strip() else f"char:#{i}"
        char_key_by_fold[c.name.strip().casefold()] = serial_key
        add_node(
            serial_key,
            "FilmMakerCharacter",
            f"Character · {c.name}",
            xy_for(serial_key, f"char:#{i}"),
            [240, 290],
            [],
            [
                {"name": "CHARACTER", "type": SLOT_CHARACTER, "slot_index": 0},
                {"name": "IMAGE", "type": SLOT_IMAGE, "slot_index": 1},
            ],
            [c.name, c.description, c.image or ""],
            "#46255c",
            "#281636",
            {"character_name": c.name},
        )

    # 3. Location nodes
    loc_key_by_fold: dict[str, str] = {}
    for i, loc in enumerate(sb.locations or []):
        serial_key = f"loc:{loc.name.strip()}" if loc.name.strip() else f"loc:#{i}"
        loc_key_by_fold[loc.name.strip().casefold()] = serial_key
        add_node(
            serial_key,
            "FilmMakerLocation",
            f"Location · {loc.name}",
            xy_for(serial_key, f"loc:#{i}"),
            [240, 270],
            [],
            [
                {"name": "LOCATION", "type": SLOT_LOCATION, "slot_index": 0},
                {"name": "IMAGE", "type": SLOT_IMAGE, "slot_index": 1},
            ],
            [loc.name, loc.description, loc.image or ""],
            "#1b473a",
            "#112d25",
            {"location_name": loc.name},
        )

    # 4. Scene Keyframe + Video Sampler nodes
    scene_h = 390 if sb.aspect == "9:16" else 280
    for idx, sc in enumerate(sb.scenes):
        sc_chars = sb.scene_characters(sc)
        sc_loc = sb.scene_location(sc)
        prev_sid = _continuity_prev_scene(sb, idx)

        if sc.mode == "keyframe":
            kf_inputs: list[dict[str, Any]] = [
                {"name": "model", "type": SLOT_MODEL, "link": None},
                {"name": "location", "type": SLOT_LOCATION, "link": None},
                {"name": "prev_image", "type": SLOT_IMAGE, "link": None},
            ]
            for ci, ch in enumerate(sc_chars):
                kf_inputs.append({"name": f"char_{ci + 1}_{ch.name}", "type": SLOT_CHARACTER, "link": None})

            kf_pos = xy_for(f"kf:{sc.id}")
            add_node(
                f"kf:{sc.id}",
                "FilmMakerKeyframe",
                f"Keyframe · {sc.id}",
                kf_pos,
                [280, scene_h],
                kf_inputs,
                [{"name": "IMAGE", "type": SLOT_IMAGE, "slot_index": 0}],
                [
                    sc.id,
                    sc.image_prompt or "",
                    (sc.camera.shot if sc.camera and sc.camera.shot else ""),
                    sc.continue_previous is not False,
                    sc.characters is not None,
                ],
                "#1d3d5e",
                "#13273d",
                {"scene_id": sc.id, "explicit_characters": sc.characters is not None},
            )

        vid_inputs: list[dict[str, Any]] = [{"name": "model", "type": SLOT_MODEL, "link": None}]
        if sc.mode == "keyframe":
            vid_inputs.append({"name": "start_image", "type": SLOT_IMAGE, "link": None})
        elif sc.mode == "t2v":
            vid_inputs.append({"name": "location", "type": SLOT_LOCATION, "link": None})
            for ci, ch in enumerate(sc_chars):
                vid_inputs.append({"name": f"char_{ci + 1}_{ch.name}", "type": SLOT_CHARACTER, "link": None})
        elif sc.mode == "i2v":
            vid_inputs.append({"name": "image", "type": SLOT_IMAGE, "link": None})

        vid_pos = xy_for(f"vid:{sc.id}")
        add_node(
            f"vid:{sc.id}",
            "FilmMakerVideoSampler",
            f"VideoSampler · {sc.id}",
            vid_pos,
            [280, scene_h],
            vid_inputs,
            [{"name": "VIDEO", "type": SLOT_VIDEO, "slot_index": 0}],
            [
                sc.id,
                sc.mode,
                sc.prompt or "",
                sc.duration or 8,
                sc.model or sb.model or "veo-lite",
                sc.narration or "",
                (sc.camera.movement if sc.camera and sc.camera.movement else ""),
            ],
            "#543119",
            "#331e10",
            {"scene_id": sc.id, "mode": sc.mode, "status": (scene_state.get(sc.id) or {}).get("status", "todo")},
        )

        # Group box around scene nodes
        pts_x = [vid_pos[0]] + ([xy_for(f"kf:{sc.id}")[0]] if sc.mode == "keyframe" else [])
        pts_y = [vid_pos[1]] + ([xy_for(f"kf:{sc.id}")[1]] if sc.mode == "keyframe" else [])
        gx = min(pts_x) - 20
        gy = min(pts_y) - 44
        gw = (max(pts_x) + 280) - gx + 20
        gh = (max(pts_y) + scene_h) - gy + 20
        groups.append({
            "title": f"Cảnh {sc.id}" + (f" · {sc_loc.name}" if sc_loc else f" · {sc.mode}"),
            "bounding": [gx, gy, gw, gh],
            "color": "#264156",
            "font_size": 18,
            "flags": {},
        })

        # Wire connections for this scene
        if sc.mode == "keyframe":
            connect("cfg:film", 0, f"kf:{sc.id}", 0, SLOT_MODEL)
            if sc_loc:
                lk = loc_key_by_fold.get(sc_loc.name.strip().casefold())
                if lk:
                    connect(lk, 0, f"kf:{sc.id}", 1, SLOT_LOCATION)
            if prev_sid:
                connect(f"kf:{prev_sid}", 0, f"kf:{sc.id}", 2, SLOT_IMAGE)
            for ci, ch in enumerate(sc_chars):
                ck = char_key_by_fold.get(ch.name.strip().casefold())
                if ck:
                    connect(ck, 0, f"kf:{sc.id}", 3 + ci, SLOT_CHARACTER)
            connect("cfg:film", 0, f"vid:{sc.id}", 0, SLOT_MODEL)
            connect(f"kf:{sc.id}", 0, f"vid:{sc.id}", 1, SLOT_IMAGE)
        elif sc.mode == "t2v":
            connect("cfg:film", 0, f"vid:{sc.id}", 0, SLOT_MODEL)
            if sc_loc:
                lk = loc_key_by_fold.get(sc_loc.name.strip().casefold())
                if lk:
                    connect(lk, 0, f"vid:{sc.id}", 1, SLOT_LOCATION)
            for ci, ch in enumerate(sc_chars):
                ck = char_key_by_fold.get(ch.name.strip().casefold())
                if ck:
                    connect(ck, 0, f"vid:{sc.id}", 2 + ci, SLOT_CHARACTER)
        else:
            connect("cfg:film", 0, f"vid:{sc.id}", 0, SLOT_MODEL)

    # 5. Final Film Stitch / Combine node
    out_inputs = [{"name": f"clip_{sc.id}", "type": SLOT_VIDEO, "link": None} for sc in sb.scenes]
    add_node(
        "out:final",
        "FilmMakerSaveFilm",
        "SaveFilm · Ghép phim hoàn chỉnh",
        xy_for("out:final"),
        [270, max(190, 96 + 24 * len(sb.scenes))],
        out_inputs,
        [],
        [sb.title, sb.subtitle_language or "Vietnamese", "final.mp4"],
        "#1f4732",
        "#132b1f",
    )
    for idx, sc in enumerate(sb.scenes):
        connect(f"vid:{sc.id}", 0, "out:final", idx, SLOT_VIDEO)

    view = board.get("view") if isinstance(board.get("view"), dict) else None
    ds = {
        "scale": float(view.get("zoom", 1.0)) if view else 1.0,
        "offset": [float(view.get("x", 0.0)), float(view.get("y", 0.0))] if view else [0.0, 0.0],
    }
    shown_videos = list(board.get("videos") or [sc.id for sc in sb.scenes if sc.mode == "keyframe"])
    return {
        "last_node_id": next_nid - 1,
        "last_link_id": next_lid - 1,
        "nodes": nodes,
        "links": links,
        "groups": groups,
        "config": {},
        "extra": {
            "ds": ds,
            "filmmaker": {
                "version": 1,
                "title": sb.title,
                "aspect": sb.aspect,
                "card_keys": card_keys,
                "videos": shown_videos,
            },
        },
        "version": 0.4,
    }


def build_single_image_api_prompt(
    prompt: str,
    *,
    negative_prompt: str = DEFAULT_NEGATIVE,
    checkpoint: str = "v1-5-pruned-emaonly.safetensors",
    width: int = 1024,
    height: int = 576,
    steps: int = DEFAULT_STEPS,
    cfg: float = DEFAULT_CFG,
    seed: int | None = None,
    ref_image_name: str | None = None,
    denoise: float = 0.68,
    filename_prefix: str = "filmmaker",
    template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a ComfyUI `/prompt` API dictionary for generating one portrait, location, or keyframe image."""
    chosen_seed = int(seed if seed is not None else random.randint(1, 2**31 - 1))
    if template:
        return _apply_api_template(
            template,
            prompt=prompt,
            negative_prompt=negative_prompt,
            checkpoint=checkpoint,
            width=width,
            height=height,
            steps=steps,
            cfg=cfg,
            seed=chosen_seed,
            image_name=ref_image_name,
            filename_prefix=filename_prefix,
        )

    graph: dict[str, Any] = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["1", 1]}},
    }
    if ref_image_name:
        graph["4"] = {"class_type": "LoadImage", "inputs": {"image": ref_image_name}}
        graph["5"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["4", 0],
                "upscale_method": "lanczos",
                "width": width,
                "height": height,
                "crop": "center",
            },
        }
        graph["6"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["1", 2]}}
        latent_ref = ["6", 0]
        eff_denoise = max(0.25, min(0.95, float(denoise)))
    else:
        graph["4"] = {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        }
        latent_ref = ["4", 0]
        eff_denoise = 1.0

    graph["7"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": chosen_seed,
            "steps": clean_steps(steps),
            "cfg": clean_cfg(cfg),
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": eff_denoise,
            "model": ["1", 0],
            "positive": ["2", 0],
            "negative": ["3", 0],
            "latent_image": latent_ref,
        },
    }
    graph["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}}
    graph["9"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": filename_prefix, "images": ["8", 0]},
    }
    return graph


def build_project_api_prompt(
    sb: Storyboard,
    *,
    checkpoint: str = "v1-5-pruned-emaonly.safetensors",
    steps: int = DEFAULT_STEPS,
    cfg: float = DEFAULT_CFG,
) -> dict[str, Any]:
    """Export the entire storyboard's keyframe/scene generation pipeline as a flat ComfyUI `workflow_api.json`."""
    w, h = aspect_dimensions(sb.aspect)
    api_graph: dict[str, Any] = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": DEFAULT_NEGATIVE, "clip": ["1", 1]}},
    }
    nid = 10
    for sc in sb.scenes:
        pos_id = str(nid)
        lat_id = str(nid + 1)
        smp_id = str(nid + 2)
        dec_id = str(nid + 3)
        sav_id = str(nid + 4)
        nid += 10
        prompt_text = sb.for_flow(sb.image_prompt(sc) if sc.mode == "keyframe" else sb.video_prompt(sc))
        api_graph[pos_id] = {
            "class_type": "CLIPTextEncode",
            "_meta": {"title": f"Prompt · {sc.id}"},
            "inputs": {"text": prompt_text, "clip": ["1", 1]},
        }
        api_graph[lat_id] = {
            "class_type": "EmptyLatentImage",
            "_meta": {"title": f"Latent · {sc.id}"},
            "inputs": {"width": w, "height": h, "batch_size": 1},
        }
        api_graph[smp_id] = {
            "class_type": "KSampler",
            "_meta": {"title": f"Sampler · {sc.id}"},
            "inputs": {
                "seed": abs(hash(sc.id)) % (2**31 - 1) or 42,
                "steps": clean_steps(steps),
                "cfg": clean_cfg(cfg),
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": [pos_id, 0],
                "negative": ["2", 0],
                "latent_image": [lat_id, 0],
            },
        }
        api_graph[dec_id] = {
            "class_type": "VAEDecode",
            "_meta": {"title": f"Decode · {sc.id}"},
            "inputs": {"samples": [smp_id, 0], "vae": ["1", 2]},
        }
        api_graph[sav_id] = {
            "class_type": "SaveImage",
            "_meta": {"title": f"Save · {sc.id}"},
            "inputs": {"filename_prefix": f"filmmaker_{sc.id}", "images": [dec_id, 0]},
        }
    return api_graph


def is_api_prompt_format(data: Any) -> bool:
    """True when `data` is a ComfyUI API prompt dict (`{"3": {"class_type": "...", "inputs": {...}}}`)."""
    if not isinstance(data, dict) or not data or "nodes" in data:
        return False
    return all(
        isinstance(k, str)
        and isinstance(v, dict)
        and isinstance(v.get("class_type"), str)
        and isinstance(v.get("inputs"), dict)
        for k, v in data.items()
    )


def _apply_api_template(
    template: dict[str, Any],
    *,
    prompt: str,
    negative_prompt: str,
    checkpoint: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
    image_name: str | None,
    filename_prefix: str,
) -> dict[str, Any]:
    """Fill placeholders or standard ComfyUI node fields in a user-provided API prompt template."""
    replacements = {
        "{{prompt}}": prompt,
        "{{negative_prompt}}": negative_prompt,
        "{{checkpoint}}": checkpoint,
        "{{image}}": image_name or "",
        "{{filename_prefix}}": filename_prefix,
    }

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            if v == "{{width}}":
                return width
            if v == "{{height}}":
                return height
            if v == "{{steps}}":
                return steps
            if v == "{{cfg}}":
                return cfg
            if v == "{{seed}}":
                return seed
            out = v
            for token, rep in replacements.items():
                if token in out:
                    out = out.replace(token, rep)
            return out
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(val) for k, val in v.items()}
        return v

    out_graph: dict[str, Any] = walk(copy.deepcopy(template))
    patched_positive = False
    for node in out_graph.values():
        if not isinstance(node, dict):
            continue
        cls = node.get("class_type")
        inp = node.get("inputs")
        if not isinstance(inp, dict):
            continue
        if cls == "KSampler":
            if "seed" in inp and isinstance(inp["seed"], (int, float)):
                inp["seed"] = seed
        elif cls == "EmptyLatentImage":
            if "width" in inp and isinstance(inp["width"], (int, float)):
                inp["width"] = width
            if "height" in inp and isinstance(inp["height"], (int, float)):
                inp["height"] = height
        elif cls == "LoadImage" and image_name and "image" in inp:
            inp["image"] = image_name
        elif cls == "CLIPTextEncode" and not patched_positive and "{{prompt}}" not in json.dumps(template):
            meta_title = str((node.get("_meta") or {}).get("title") or "").lower()
            if "neg" not in meta_title and isinstance(inp.get("text"), str):
                inp["text"] = prompt
                patched_positive = True
    return out_graph


def import_comfy_workflow(
    workflow: Any,
    raw_sb: dict[str, Any],
    *,
    current_board: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Import a ComfyUI `workflow.json` (UI graph) or `workflow_api.json` (API template).

    Returns `{"kind": "ui_workflow" | "api_template", "board": {...}, "storyboard": {...}, "template": ...}`.
    """
    if not isinstance(workflow, dict):
        raise ValueError("File workflow ComfyUI phải là một đối tượng JSON")

    base_board = copy.deepcopy(current_board or {"cards": {}, "videos": [], "view": None})
    sb_out = copy.deepcopy(raw_sb)

    if is_api_prompt_format(workflow):
        return {
            "kind": "api_template",
            "board": base_board,
            "storyboard": sb_out,
            "template": copy.deepcopy(workflow),
        }

    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("JSON không đúng định dạng ComfyUI workflow (thiếu danh sách 'nodes' hoặc 'class_type')")

    raw_links = workflow.get("links") if isinstance(workflow.get("links"), list) else []
    link_map: dict[int, tuple[int, int, int, int, str]] = {}
    for entry in raw_links:
        if isinstance(entry, list) and len(entry) >= 5:
            try:
                lid = int(entry[0])
                link_map[lid] = (
                    int(entry[1]),
                    int(entry[2]),
                    int(entry[3]),
                    int(entry[4]),
                    str(entry[5]) if len(entry) > 5 else "",
                )
            except (TypeError, ValueError):
                continue

    extra = workflow.get("extra") if isinstance(workflow.get("extra"), dict) else {}
    fm_meta = extra.get("filmmaker") if isinstance(extra.get("filmmaker"), dict) else {}
    id_to_fm_key: dict[int, str] = {}
    raw_ck = fm_meta.get("card_keys") if isinstance(fm_meta.get("card_keys"), dict) else {}
    for k, v in raw_ck.items():
        try:
            if isinstance(v, str):
                id_to_fm_key[int(k)] = v
        except ValueError:
            pass

    node_by_id: dict[int, dict[str, Any]] = {}
    cards: dict[str, dict[str, float]] = dict(base_board.get("cards") or {})
    videos: set[str] = set(base_board.get("videos") or [])
    if isinstance(fm_meta.get("videos"), list):
        videos.update(str(v) for v in fm_meta["videos"] if isinstance(v, str))

    chars = list(sb_out.get("characters") or [])
    locs = list(sb_out.get("locations") or [])
    scenes = list(sb_out.get("scenes") or [])
    char_by_fold = {str(c.get("name") or "").strip().casefold(): c for c in chars if isinstance(c, dict) and c.get("name")}
    loc_by_fold = {str(l.get("name") or "").strip().casefold(): l for l in locs if isinstance(l, dict) and l.get("name")}
    scene_by_id = {str(s.get("id") or ""): s for s in scenes if isinstance(s, dict) and s.get("id")}

    has_fm_nodes = False
    imported_standard_prompts: list[tuple[int, str]] = []

    for node in nodes:
        if not isinstance(node, dict) or "id" not in node:
            continue
        try:
            nid = int(node["id"])
        except (TypeError, ValueError):
            continue
        node_by_id[nid] = node
        ntype = str(node.get("type") or "")
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        fm_key = str(props.get("fm_key") or id_to_fm_key.get(nid) or "")
        pos = node.get("pos")
        xy = None
        if isinstance(pos, list) and len(pos) >= 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in pos[:2]):
            xy = {"x": float(pos[0]), "y": float(pos[1])}
        elif isinstance(pos, dict) and isinstance(pos.get("0"), (int, float)) and isinstance(pos.get("1"), (int, float)):
            xy = {"x": float(pos["0"]), "y": float(pos["1"])}

        wvals = node.get("widgets_values") if isinstance(node.get("widgets_values"), list) else []

        if ntype.startswith("FilmMaker") or fm_key:
            has_fm_nodes = True
            if fm_key and xy and any(fm_key.startswith(p) for p in ("char:", "loc:", "cfg:", "kf:", "vid:", "out:")):
                cards[fm_key] = xy
            if ntype == "FilmMakerConfig" and len(wvals) >= 5:
                if isinstance(wvals[0], str) and wvals[0].strip():
                    sb_out["title"] = wvals[0].strip()
                if wvals[1] in ("16:9", "9:16"):
                    sb_out["aspect"] = wvals[1]
                if isinstance(wvals[4], str):
                    sb_out["style"] = wvals[4]
            elif ntype == "FilmMakerCharacter" and len(wvals) >= 2:
                cname = str(wvals[0] or "").strip()
                cdesc = str(wvals[1] or "")
                if cname:
                    existing = char_by_fold.get(cname.casefold())
                    if existing is not None:
                        existing["description"] = cdesc
                    else:
                        new_c: dict[str, Any] = {"name": cname, "description": cdesc}
                        if len(wvals) >= 3 and isinstance(wvals[2], str) and wvals[2].strip():
                            new_c["image"] = wvals[2].strip()
                        chars.append(new_c)
                        char_by_fold[cname.casefold()] = new_c
            elif ntype == "FilmMakerLocation" and len(wvals) >= 2:
                lname = str(wvals[0] or "").strip()
                ldesc = str(wvals[1] or "")
                if lname:
                    existing = loc_by_fold.get(lname.casefold())
                    if existing is not None:
                        existing["description"] = ldesc
                    else:
                        new_l: dict[str, Any] = {"name": lname, "description": ldesc}
                        if len(wvals) >= 3 and isinstance(wvals[2], str) and wvals[2].strip():
                            new_l["image"] = wvals[2].strip()
                        locs.append(new_l)
                        loc_by_fold[lname.casefold()] = new_l
            elif ntype == "FilmMakerKeyframe" and len(wvals) >= 2:
                sid = str(wvals[0] or "").strip()
                sc = scene_by_id.get(sid)
                if sc is not None and isinstance(wvals[1], str):
                    sc["image_prompt"] = wvals[1]
            elif ntype == "FilmMakerVideoSampler" and len(wvals) >= 3:
                sid = str(wvals[0] or "").strip()
                videos.add(sid)
                sc = scene_by_id.get(sid)
                if sc is not None:
                    if wvals[1] in ("keyframe", "t2v", "i2v"):
                        sc["mode"] = wvals[1]
                    if isinstance(wvals[2], str):
                        sc["prompt"] = wvals[2]
                    if len(wvals) >= 4 and isinstance(wvals[3], (int, float)) and not isinstance(wvals[3], bool):
                        sc["duration"] = max(1, int(wvals[3]))
        elif ntype == "CLIPTextEncode" and wvals and isinstance(wvals[0], str) and wvals[0].strip():
            title_l = str(node.get("title") or "").lower()
            if "neg" not in title_l and "watermark" not in wvals[0].lower():
                imported_standard_prompts.append((nid, wvals[0].strip()))

    # Reconstruct character, location, and continuity wiring on FilmMaker scene nodes
    if has_fm_nodes and link_map:
        for node in nodes:
            if not isinstance(node, dict):
                continue
            ntype = str(node.get("type") or "")
            props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            wvals = node.get("widgets_values") if isinstance(node.get("widgets_values"), list) else []
            if ntype not in ("FilmMakerKeyframe", "FilmMakerVideoSampler"):
                continue
            sid = str(props.get("scene_id") or (wvals[0] if wvals else "") or "").strip()
            sc = scene_by_id.get(sid)
            if sc is None:
                continue
            mode = str(sc.get("mode") or "keyframe")
            if (mode == "keyframe" and ntype != "FilmMakerKeyframe") or (mode == "t2v" and ntype != "FilmMakerVideoSampler"):
                continue
            linked_chars: list[str] = []
            linked_loc: str | None = None
            has_prev_img = False
            for inp in node.get("inputs") or []:
                if not isinstance(inp, dict):
                    continue
                lid = inp.get("link")
                if not isinstance(lid, int) or lid not in link_map:
                    continue
                src_id = link_map[lid][0]
                src_node = node_by_id.get(src_id) or {}
                src_type = str(src_node.get("type") or "")
                src_w = src_node.get("widgets_values") if isinstance(src_node.get("widgets_values"), list) else []
                if src_type == "FilmMakerCharacter" and src_w and isinstance(src_w[0], str) and src_w[0].strip():
                    cname = src_w[0].strip()
                    if cname.casefold() not in {x.casefold() for x in linked_chars}:
                        linked_chars.append(cname)
                elif src_type == "FilmMakerLocation" and src_w and isinstance(src_w[0], str) and src_w[0].strip():
                    linked_loc = src_w[0].strip()
                elif src_type == "FilmMakerKeyframe" and inp.get("name") == "prev_image":
                    has_prev_img = True

            if mode != "i2v":
                explicit_flag = bool(props.get("explicit_characters")) or ("characters" in sc)
                if explicit_flag or linked_chars:
                    sc["characters"] = linked_chars
                if linked_loc:
                    sc["location"] = linked_loc
                elif "location" in sc and ntype in ("FilmMakerKeyframe", "FilmMakerVideoSampler"):
                    sc.pop("location", None)
                if ntype == "FilmMakerKeyframe" and linked_loc:
                    if has_prev_img:
                        sc.pop("continue_previous", None)
                    elif sc.get("continue_previous") is False or (len(wvals) >= 4 and wvals[3] is False):
                        sc["continue_previous"] = False
    elif imported_standard_prompts and scenes:
        # Generic ComfyUI workflow imported onto an existing project: map positive prompts into scenes if blank
        for idx, (_nid, text) in enumerate(imported_standard_prompts):
            if idx < len(scenes) and isinstance(scenes[idx], dict):
                if not str(scenes[idx].get("image_prompt") or "").strip():
                    scenes[idx]["image_prompt"] = text
                if not str(scenes[idx].get("prompt") or "").strip():
                    scenes[idx]["prompt"] = text

    sb_out["characters"] = chars
    if locs:
        sb_out["locations"] = locs
    sb_out["scenes"] = scenes

    view = base_board.get("view")
    ds = extra.get("ds") if isinstance(extra.get("ds"), dict) else None
    if ds and isinstance(ds.get("offset"), list) and len(ds["offset"]) >= 2:
        try:
            zx = max(0.25, min(2.0, float(ds.get("scale", 1.0))))
            view = {"x": float(ds["offset"][0]), "y": float(ds["offset"][1]), "zoom": zx}
        except (TypeError, ValueError):
            pass

    return {
        "kind": "ui_workflow",
        "board": {"cards": cards, "videos": sorted(videos), "view": view},
        "storyboard": sb_out,
        "template": None,
    }


# --- High-level ComfyUI execution for Film Maker projects ---------------------


def select_checkpoint(client: ComfyClient, preferred: str = "") -> str:
    """Pick the configured checkpoint, or the first installed checkpoint on the ComfyUI server."""
    ckpts = client.available_checkpoints(timeout=4.0)
    if preferred and (not ckpts or preferred in ckpts):
        return preferred
    if ckpts:
        return ckpts[0]
    if preferred:
        return preferred
    raise ComfyError(
        "Máy chủ ComfyUI chưa có model checkpoint nào trong models/checkpoints/. "
        "Hãy tải ít nhất 1 checkpoint (SDXL / SD1.5 / Flux) hoặc nhập API Workflow riêng."
    )


def generate_image_on_comfy(
    client: ComfyClient,
    prompt: str,
    dest: Path,
    *,
    aspect: str = "16:9",
    ref_images: list[Path] | None = None,
    checkpoint: str = "",
    steps: int = DEFAULT_STEPS,
    cfg: float = DEFAULT_CFG,
    filename_prefix: str = "filmmaker",
    template: dict[str, Any] | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda *_: None,
) -> Path:
    """Upload optional reference image, queue ComfyUI prompt, wait for completion, and save PNG to `dest`."""
    ckpt = checkpoint or ("" if template else select_checkpoint(client, checkpoint))
    w, h = aspect_dimensions(aspect)
    uploaded_ref: str | None = None
    for ref in ref_images or []:
        if Path(ref).is_file():
            up = client.upload_image(Path(ref))
            sub = str(up.get("subfolder") or "").strip("/")
            name = str(up.get("name") or Path(ref).name)
            uploaded_ref = f"{sub}/{name}" if sub else name
            log(f"  ComfyUI: đã tải ảnh tham chiếu {Path(ref).name}")
            break

    api_prompt = build_single_image_api_prompt(
        prompt,
        checkpoint=ckpt or "v1-5-pruned-emaonly.safetensors",
        width=w,
        height=h,
        steps=steps,
        cfg=cfg,
        ref_image_name=uploaded_ref,
        filename_prefix=filename_prefix,
        template=template,
    )
    prompt_id = client.queue_prompt(api_prompt)
    log(f"  ComfyUI queue #{prompt_id[:8]}… ({w}×{h}, {steps} steps)")
    outputs = client.wait_for_outputs(prompt_id, should_stop=should_stop, sleep=sleep)
    img_item = next((o for o in outputs if o.get("media_kind") == "images"), outputs[0])
    return client.download_output(img_item, dest)


def run_comfy_keyframes(
    sb: Storyboard,
    out_dir: Path,
    client: ComfyClient,
    *,
    only: set[str] | None = None,
    force: set[str] | None = None,
    checkpoint: str = "",
    steps: int = DEFAULT_STEPS,
    cfg: float = DEFAULT_CFG,
    template: dict[str, Any] | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda *_: None,
    progress: prog.Emit | None = None,
) -> dict[str, Any]:
    """Generate scene keyframes via ComfyUI and record exact `state.json` signatures for `Pipeline.has_keyframe`."""
    from .gflow import Gflow
    from .pipeline import Pipeline

    out_dir = Path(out_dir).resolve()
    pipe = Pipeline(sb, out_dir, Gflow(dry_run=True, log=lambda *_: None))
    rep = prog.Reporter(progress)
    force = force or set()
    targets = [
        s for s in sb.scenes
        if s.mode == "keyframe" and (not only or s.id in only) and (s.id in force or not pipe.has_keyframe(s))
    ]
    for s in targets:
        rep.queued(f"kf:{s.id}")

    done: list[str] = []
    failed: dict[str, str] = {}
    try:
        for s in targets:
            if should_stop():
                break
            key = f"kf:{s.id}"
            rep.submitting(key)
            with rep.active(key):
                rep.gflow_event("start", {"source": "estimate", "expected": 15.0})
                try:
                    ref_items, src, _why = pipe.keyframe_ref_items(s)
                    refs = [it.path for it in ref_items if it.path.is_file()]
                    prompt = sb.for_flow(sb.image_prompt(s))
                    dest = pipe.keyframe_path(s)
                    log(f"[{s.id}] Tạo keyframe qua ComfyUI…")
                    generate_image_on_comfy(
                        client,
                        prompt,
                        dest,
                        aspect=sb.aspect,
                        ref_images=refs,
                        checkpoint=checkpoint,
                        steps=steps,
                        cfg=cfg,
                        filename_prefix=f"kf_{s.id}",
                        template=template,
                        should_stop=should_stop,
                        sleep=sleep,
                        log=log,
                    )
                    pipe.state.update(
                        s.id,
                        keyframe_prompt=sb.image_prompt(s),
                        keyframe_refs=sb.refs_signature(s),
                        keyframe_from=src.id if src else None,
                        keyframe_from_sha=file_sha(pipe.keyframe_path(src)) if src else None,
                    )
                    rep.done(key)
                    done.append(s.id)
                    log(f"[{s.id}] OK -> {dest}")
                except Exception as exc:
                    rep.failed(key)
                    failed[s.id] = str(exc)
                    log(f"[{s.id}] ✘ Lỗi ComfyUI: {exc}")
    finally:
        rep.settle()
    return {"done": done, "failed": failed}


def run_comfy_videos(
    sb: Storyboard,
    project_dir: Path,
    out_dir: Path,
    client: ComfyClient,
    *,
    only: set[str] | None = None,
    force: set[str] | None = None,
    checkpoint: str = "",
    steps: int = DEFAULT_STEPS,
    cfg: float = DEFAULT_CFG,
    template: dict[str, Any] | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda *_: None,
    progress: prog.Emit | None = None,
) -> dict[str, Any]:
    """Render scene `.mp4` clips via ComfyUI (using custom video template if provided, else ComfyUI frame + camera motion)."""
    from .gflow import Gflow
    from .pipeline import Pipeline

    out_dir = Path(out_dir).resolve()
    project_dir = Path(project_dir).resolve()
    pipe = Pipeline(sb, out_dir, Gflow(dry_run=True, log=lambda *_: None))
    rep = prog.Reporter(progress)
    force = force or set()
    targets = [
        s for s in sb.scenes
        if (not only or s.id in only) and (s.id in force or not pipe.is_done(s))
    ]
    for s in targets:
        rep.queued(f"vid:{s.id}")

    done: list[str] = []
    failed: dict[str, str] = {}
    try:
        for s in targets:
            if should_stop():
                break
            key = f"vid:{s.id}"
            rep.submitting(key)
            with rep.active(key):
                rep.gflow_event("start", {"source": "estimate", "expected": 25.0})
                try:
                    clip_dest = pipe.clip_path(s)
                    log(f"[{s.id}] Tạo video qua ComfyUI…")
                    # Ensure start frame exists first
                    start_img: Path | None = None
                    if s.mode == "keyframe":
                        kf_path = pipe.keyframe_path(s)
                        if not pipe.has_keyframe(s):
                            ref_items, src, _why = pipe.keyframe_ref_items(s)
                            generate_image_on_comfy(
                                client,
                                sb.for_flow(sb.image_prompt(s)),
                                kf_path,
                                aspect=sb.aspect,
                                ref_images=[it.path for it in ref_items if it.path.is_file()],
                                checkpoint=checkpoint,
                                steps=steps,
                                cfg=cfg,
                                filename_prefix=f"kf_{s.id}",
                                template=None,
                                should_stop=should_stop,
                                sleep=sleep,
                                log=log,
                            )
                            pipe.state.update(
                                s.id,
                                keyframe_prompt=sb.image_prompt(s),
                                keyframe_refs=sb.refs_signature(s),
                                keyframe_from=src.id if src else None,
                                keyframe_from_sha=file_sha(pipe.keyframe_path(src)) if src else None,
                            )
                        start_img = kf_path
                    elif s.mode == "i2v" and s.image:
                        cand = Path(s.image) if Path(s.image).is_absolute() else (project_dir / s.image)
                        if not cand.is_file():
                            raise ComfyError(f"Không tìm thấy ảnh đầu vào của cảnh {s.id}: {s.image}")
                        start_img = cand
                    else:
                        tmp_frame = out_dir / "keyframes" / f"{s.id}_t2v.png"
                        refs = [it.path for it in sb.scene_ref_items(s) if it.path.is_file()]
                        generate_image_on_comfy(
                            client,
                            sb.for_flow(sb.video_prompt(s)),
                            tmp_frame,
                            aspect=sb.aspect,
                            ref_images=refs,
                            checkpoint=checkpoint,
                            steps=steps,
                            cfg=cfg,
                            filename_prefix=f"t2v_{s.id}",
                            template=None,
                            should_stop=should_stop,
                            sleep=sleep,
                            log=log,
                        )
                        start_img = tmp_frame

                    # If the user supplied a custom video API template that outputs video/gif, run it directly
                    used_video_template = False
                    if template and any(
                        isinstance(n, dict) and n.get("class_type") in VIDEO_NODE_CLASSES
                        for n in template.values()
                    ):
                        up = client.upload_image(start_img)
                        sub = str(up.get("subfolder") or "").strip("/")
                        up_name = f"{sub}/{up.get('name')}" if sub else str(up.get("name") or start_img.name)
                        w, h = aspect_dimensions(sb.aspect)
                        v_prompt = _apply_api_template(
                            template,
                            prompt=sb.for_flow_video(sb.video_prompt(s), s),
                            negative_prompt=DEFAULT_NEGATIVE,
                            checkpoint=checkpoint or "v1-5-pruned-emaonly.safetensors",
                            width=w,
                            height=h,
                            steps=steps,
                            cfg=cfg,
                            seed=random.randint(1, 2**31 - 1),
                            image_name=up_name,
                            filename_prefix=f"vid_{s.id}",
                        )
                        pid_q = client.queue_prompt(v_prompt)
                        outs = client.wait_for_outputs(pid_q, should_stop=should_stop, sleep=sleep)
                        vid_item = next(
                            (o for o in outs if o.get("media_kind") in ("videos", "gifs")),
                            outs[0],
                        )
                        client.download_output(vid_item, clip_dest)
                        used_video_template = True

                    if not used_video_template:
                        mv = s.camera.movement if s.camera else None
                        render_motion_clip_from_image(
                            start_img,
                            clip_dest,
                            duration=s.duration or 6,
                            aspect=sb.aspect,
                            movement=mv,
                        )

                    State(out_dir / "state.json").update(
                        s.id,
                        status=DONE,
                        video=str(clip_dest),
                        fingerprint=sb.scene_fingerprint(s),
                        rendered_as="comfyui",
                        error=None,
                    )
                    rep.done(key)
                    done.append(s.id)
                    log(f"[{s.id}] OK -> {clip_dest}")
                except Exception as exc:
                    State(out_dir / "state.json").update(s.id, status="failed", error=str(exc))
                    rep.failed(key)
                    failed[s.id] = str(exc)
                    log(f"[{s.id}] ✘ Lỗi video ComfyUI: {exc}")
    finally:
        rep.settle()
    return {"done": done, "failed": failed}
