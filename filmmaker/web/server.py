"""FastAPI backend for the local UI.

Runs on 127.0.0.1 only. Every request must use a localhost Host header (blocks
DNS-rebinding), and every non-GET API call must carry the per-launch token that
is embedded in the page, so other websites cannot start paid renders.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from .. import durations as dur
from .. import autopilot, film_chat, flow_caps, qc, social, subtitles, manual_clip, model_change, model_meta, retirement
from .. import progress as prog
from ..assemble import AssembleError, assemble
from ..backup import AutoBackup, BackupError, Rclone, check_name, check_remote, last_backup
from ..settings import DEFAULT_BACKUP_REMOTE, settings_path
from ..gflow import Gflow, GflowError, _extract_json, _split
from ..pipeline import DONE, Pipeline, RunOptions, State
from ..storyboard import (CAMERA_PRESETS, NO_DIALOGUE, Scene, Storyboard, clean_dialogue_language, clean_language,
                          load_storyboard, save_storyboard)
from ..thumbnail import ThumbnailError
from . import ui_prefs

STATIC = Path(__file__).parent / "static"
TEMPLATE = Path(__file__).parent.parent / "templates" / "storyboard.yaml"
SETTINGS_PATH = settings_path()
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}
BOARD_MAX_BYTES = 256 * 1024  # PUT /board body limit


# --- settings -----------------------------------------------------------------

DEFAULT_SETTINGS = {"profile": "", "pause_min": 8, "pause_max": 20, "retries": 2, "anthropic_api_key": "",
                    "planner": "auto",  # auto | claude_code | api
                    "flow_ui": True,  # image-to-video through the Flow chat page when gflow refuses it
                    "flow_ui_max_credits": 0,  # reject Flow's offer above this (0 = no limit)
                    "flow_ui_window": "hidden",  # show | hidden | headless
                    "backup_remote": DEFAULT_BACKUP_REMOTE,  # rclone remote:folder (Google Drive)
                    "auto_backup": False,  # back up after every rendered clip and at the end of each job
                    "auto_qc": True}  # AI QC of every new clip after a render (free; only flags problems)


def load_settings() -> dict[str, Any]:
    data = dict(DEFAULT_SETTINGS)
    try:
        data.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return data


def save_settings(data: dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        SETTINGS_PATH.chmod(0o600)  # holds the API key
    except OSError:
        pass


# --- background jobs ----------------------------------------------------------


class Stopped(Exception):
    pass


@dataclass
class Job:
    id: str
    kind: str
    project: str | None
    title: str
    status: str = "running"  # running | done | failed | stopped
    lines: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    finished: float | None = None
    stop_requested: bool = False
    # Per-item progress (char:<name>, kf:<sid>, vid:<sid>), written by the pipeline thread via set_progress.
    progress: dict[str, dict] = field(default_factory=dict)
    stage: dict[str, Any] = field(default_factory=dict)  # autopilot: the step now running
    _plock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set_progress(self, key: str, item: dict | None) -> None:
        with self._plock:
            if item is None:
                self.progress.pop(key, None)
            else:
                self.progress[key] = dict(item)

    def log(self, msg: str) -> None:
        for line in str(msg).splitlines() or [""]:
            self.lines.append(line)

    def sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if self.stop_requested:
                return
            time.sleep(min(0.5, end - time.time()))

    def public(self, since: int = 0) -> dict[str, Any]:
        with self._plock:
            snapshot = {k: dict(v) for k, v in self.progress.items()}
        return {
            "id": self.id, "kind": self.kind, "project": self.project, "title": self.title,
            "status": self.status, "lines": self.lines[since:], "total": len(self.lines),
            "result": self.result, "started": self.started, "finished": self.finished,
            "stop_requested": self.stop_requested,
            "progress": prog.public_view(snapshot, prog.now()),
            "stage": dict(self.stage),
        }


class Jobs:
    """One job at a time: gflow drives a single browser profile."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jobs: dict[str, Job] = {}
        self.current: Job | None = None

    def start(self, kind: str, title: str, project: str | None, fn: Callable[[Job], dict | None]) -> Job:
        with self.lock:
            if self.current and self.current.status == "running":
                raise HTTPException(409, detail={"message": f"Đang có việc chạy: {self.current.title}. Chờ xong hoặc bấm Dừng.",
                                                 "reason": "job_running"})
            job = Job(id=uuid.uuid4().hex[:10], kind=kind, project=project, title=title)
            self.jobs[job.id] = job
            self.current = job

        def runner() -> None:
            try:
                job.result = fn(job) or {}
                job.status = "stopped" if job.stop_requested else ("failed" if job.result.get("error") else "done")
            except Stopped:
                job.status = "stopped"
            except Exception as exc:  # report anything to the UI instead of dying silently
                job.log(f"LỖI: {exc}")
                if not isinstance(exc, (GflowError, AssembleError, ThumbnailError, BackupError, RuntimeError, ValueError)):
                    job.log(traceback.format_exc())
                job.result = {"error": str(exc)}
                job.status = "failed"
            finally:
                job.finished = time.time()

        threading.Thread(target=runner, daemon=True).start()
        return job


# --- helpers ------------------------------------------------------------------


def slugify(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:50] or "phim"


def _clean(value: Any) -> Any:
    """Drop empty strings / None so the YAML stays tidy and optional fields stay optional."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if v is not None and v != ""}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        return value.strip() if "\n" not in value else value.rstrip()
    return value


def _validation_errors(exc: ValidationError) -> list[str]:
    out = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", ()))
        msg = e.get("msg", "").removeprefix("Value error, ")
        out.append(f"{loc}: {msg}" if loc else msg)
    return out


class SaveBody(BaseModel):
    storyboard: dict[str, Any]


class YamlBody(BaseModel):
    text: str


class SocialBody(BaseModel):
    platforms: list[str] = ["tiktok", "facebook", "instagram"]
    languages: list[str] = ["Vietnamese"]
    note: str = ""
    pictures: bool = True


class TranslateBody(BaseModel):
    langs: list[str] = []  # empty = every other subtitle language of the film
    force: bool = False  # translate again lines that already have a translation


class QCBody(BaseModel):
    only: list[str] = []
    force: bool = False


class AskBody(BaseModel):
    question: str
    pictures: bool = True  # send portraits, cover and a contact sheet of the film along


class NewProject(BaseModel):
    title: str = "Phim mới"
    source: str = "blank"  # blank | template


class PlanBody(BaseModel):
    idea: str
    scenes: int = 6
    aspect: str = "16:9"
    language: str = "Vietnamese"  # subtitle (narration) language
    languages: list[str] | None = None  # every subtitle language, narration's first (overrides `language`)
    dialogue_language: str | None = None  # language every character speaks; "none" = no dialogue


class RunBody(BaseModel):
    action: str  # dry | keyframes | video | stitch | make
    only: list[str] = []
    force: list[str] = []
    force_keyframes: list[str] = []
    retry_unknown: bool = False  # ignored on the web: only `confirm_resend` resends an interrupted scene
    confirm_resend: list[str] = []  # interrupted ("submitting") scenes the user ticked to send again
    fail_fast: bool = False
    music: str | None = None
    volume: float = 0.25
    burn_subtitles: bool = False
    burn_lang: str | None = None  # subtitle language burned into the picture (None = the narration's)
    allow_missing: bool = False
    remake_thumbnail: bool = False  # action=thumbnail: new picture on Flow, not just the title
    engine: str = "auto"  # action=edit: auto | ai | rules
    replan: bool = False  # action=edit: ignore the saved edit plan, let the agent make a new one
    smart_edit: bool = False  # action=make: finish with the editor agent instead of a plain stitch


class AutoBody(BaseModel):
    review: bool = True  # AI agents review storyboard, keyframes, clips and the finished film
    burn_subtitles: bool = True
    burn_lang: str | None = None
    rerender_budget: int = 3  # unused: AI QC only flags clips
    music: str | None = None


class AutoPlanBody(PlanBody, AutoBody):
    scenes: int = 10


class RestoreBody(BaseModel):
    name: str


class PortraitBody(BaseModel):
    names: list[str] = []  # empty = every character without a reference image
    force: bool = False  # remake even if the character already has one


class ModelChangeBody(BaseModel):
    target: str
    scenes: list[str] | None = None  # set = single-scene mode
    include: list[str] = []  # project mode: also switch these scenes, whatever their group
    rounding: str | None = None  # up | down | empty
    base: str | None = None  # content hash from the preview (required to apply)


class RoundBody(BaseModel):
    direction: str | None = None  # "up" | "down"
    base: str | None = None  # from durations/preview


class FinalSettings(BaseModel):
    music: str | None = None
    volume: float = 0.25
    burn_subtitles: bool = False
    burn_lang: str | None = None
    allow_missing: bool = False
    smart_edit: bool = True


# --- app ----------------------------------------------------------------------


def create_app(workspace: Path, *, port: int = 8765, token: str | None = None) -> FastAPI:
    workspace = workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    token = token or secrets.token_urlsafe(24)
    jobs = Jobs()
    app = FastAPI(title="Film Maker", docs_url=None, redoc_url=None)
    app.state.token = token
    app.state.jobs = jobs
    models_cache: dict[str, Any] = {}
    sb_locks: dict[str, threading.RLock] = {}
    sb_locks_guard = threading.Lock()

    def sb_lock(pid: str) -> threading.RLock:
        """Per-project lock around writes to storyboard.yaml (endpoints run in a threadpool)."""
        with sb_locks_guard:
            return sb_locks.setdefault(pid, threading.RLock())

    app.state.sb_lock = sb_lock
    claude_cache: dict[str, Any] = {}

    def claude_code(refresh: bool = False) -> dict[str, Any]:
        from ..planner import claude_code_status

        if refresh or not claude_cache:
            claude_cache.clear()
            claude_cache.update(claude_code_status())
        return dict(claude_cache)

    def planner_backend() -> str | None:
        """Which backend `plan` will use right now, or None if neither works."""
        s = load_settings()
        has_key = bool(s.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY"))
        cc = claude_code()
        cc_ok = cc["available"] and cc["logged_in"]
        choice = s.get("planner") or "auto"
        if choice == "claude_code":
            return "claude_code" if cc["available"] else None
        if choice == "api":
            return "api" if has_key else None
        return "claude_code" if cc_ok else ("api" if has_key else ("claude_code" if cc["available"] else None))

    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost", "testserver"}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host", "") not in allowed_hosts:
            return JSONResponse({"detail": "host not allowed"}, status_code=403)
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD"):
            if not secrets.compare_digest(request.headers.get("x-token", ""), token):
                return JSONResponse({"detail": "bad token"}, status_code=403)
        return await call_next(request)

    # ---- paths ----

    def pdir(pid: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", pid):
            raise HTTPException(404, "không có dự án này")
        d = workspace / pid
        if not (d / "storyboard.yaml").is_file():
            raise HTTPException(404, "không có dự án này")
        return d

    def sb_path(pid: str) -> Path:
        return pdir(pid) / "storyboard.yaml"

    def out_dir(pid: str) -> Path:
        p = sb_path(pid)
        return p.parent / "output" / p.stem  # same layout as the CLI

    def new_project_dir(title: str) -> tuple[str, Path]:
        base = slugify(title)
        pid, n = base, 2
        while (workspace / pid).exists():
            pid, n = f"{base}-{n}", n + 1
        d = workspace / pid
        (d / "assets").mkdir(parents=True)
        return pid, d

    def read_raw(pid: str) -> dict[str, Any]:
        data = yaml.safe_load(sb_path(pid).read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise HTTPException(400, "storyboard.yaml không hợp lệ")
        return data

    def write_raw(pid: str, data: dict[str, Any], *, clean: bool = True) -> None:
        """clean=False dumps `data` as is (rounding durations: the raw dict only differs in `duration`)."""
        text = yaml.safe_dump(_clean(data) if clean else data, allow_unicode=True, sort_keys=False, width=100)
        with sb_lock(pid):
            tmp = sb_path(pid).with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(sb_path(pid))

    def file_url(pid: str, path: Path | str | None) -> str | None:
        if not path:
            return None
        p = Path(path)
        if not p.is_absolute():
            p = pdir(pid) / p
        try:
            rel = p.resolve().relative_to(pdir(pid).resolve())
        except ValueError:
            return None
        if not p.is_file():
            return None
        return f"/api/projects/{pid}/file/{rel.as_posix()}?v={int(p.stat().st_mtime)}"

    def gflow(dry_run: bool = False, log: Callable[[str], None] = lambda *_: None) -> Gflow:
        s = load_settings()
        return Gflow(profile=s.get("profile") or None, dry_run=dry_run, log=log,
                     ui_enabled=bool(s.get("flow_ui", True)), ui_max_credits=int(s.get("flow_ui_max_credits") or 0),
                     ui_window=s.get("flow_ui_window") or "hidden")

    # ---- project summary / detail ----

    def summary(pid: str) -> dict[str, Any]:
        raw = read_raw(pid)
        scenes = raw.get("scenes") or []
        done = 0
        try:
            sb = load_storyboard(sb_path(pid))
            pipe = Pipeline(sb, out_dir(pid), gflow(True))
            done = sum(pipe.is_done(s) for s in sb.scenes)
        except Exception:
            pass
        final = out_dir(pid) / "final.mp4"
        thumb = None
        for s in scenes:
            sid = s.get("id") if isinstance(s, dict) else None
            if sid:
                thumb = file_url(pid, out_dir(pid) / "keyframes" / f"{sid}.png")
                if thumb:
                    break
        return {
            "id": pid, "title": raw.get("title") or pid, "aspect": raw.get("aspect", "16:9"),
            "scenes": len(scenes), "done": done, "has_final": final.is_file(), "thumb": thumb,
            "updated": sb_path(pid).stat().st_mtime,
        }

    def qc_view(pid: str, r: dict[str, Any] | None, video: str | None) -> dict[str, Any] | None:
        """The scene's QC for the UI, only when it is about the clip that is there now."""
        if not r or not video:
            return None
        clip = Path(video)
        if not clip.is_absolute():  # the CLI run with a relative --out stores relative paths
            clip = pdir(pid) / clip
        if r.get("sig") != qc.clip_sig(clip):
            return None
        keep = ("verdict", "score", "issues", "picture", "sound", "prompt", "match", "transcript", "language",
                "dialogue_checked", "at")
        return {k: r.get(k) for k in keep}

    def detail(pid: str) -> dict[str, Any]:
        raw = read_raw(pid)
        errors: list[str] = []
        scenes: dict[str, Any] = {}
        project_id = None
        clips_rendered = 0  # scenes whose state is DONE (any rendered_as, file or not): they keep their old language
        model_info: dict[str, Any] = {"model_warning": None, "model_note": None, "models_used": {}}
        st_data = State(out_dir(pid) / "state.json").data
        i2v_off = bool(st_data.get("i2v_unsupported"))
        try:
            Storyboard.model_validate(raw)
            sb = load_storyboard(sb_path(pid))
        except ValidationError as exc:
            errors = _validation_errors(exc)
            sb = None
        except Exception as exc:
            errors = [str(exc)]
            sb = None
        if sb:
            pipe = Pipeline(sb, out_dir(pid), gflow(True))
            project_id = sb.project_id or pipe.state.data.get("project_id")
            meta = model_meta.load()
            # While a job runs for this project its clips/<id>.mp4 may be half written: probing it on every
            # poll would cost an ffprobe each time (size changes, so no cache hit) and show "invalid" for a
            # clip still downloading. Nothing can be adopted during a job anyway, so report no clip until it ends.
            busy = bool(jobs.current and jobs.current.status == "running" and jobs.current.project == pid)
            model_info = {"model_warning": retirement.model_warning(pipe, meta),
                          "model_note": retirement.model_note(pipe),
                          "models_used": retirement.models_used(pipe, meta)}
            talk = sb.dialogue_warnings()
            qc_all = qc.load(out_dir(pid))
            for s in sb.scenes:
                st = pipe.state.scene(s.id)
                kf = pipe.keyframe_path(s)
                status = pipe.scene_status(s)
                eff = retirement.effective_model(sb, s)
                clips_rendered += status in ("done", "changed")
                scenes[s.id] = {
                    "status": status,
                    "error": st.get("error"),
                    "updated_at": st.get("updated_at"),
                    "keyframe_url": file_url(pid, kf) if s.mode == "keyframe" else None,
                    "keyframe_stale": s.mode == "keyframe" and kf.is_file() and not pipe.has_keyframe(s),
                    "clip_url": file_url(pid, st.get("video")) if st.get("video") else None,
                    "image_url": file_url(pid, s.image) if s.image else None,
                    "end_image_url": file_url(pid, s.end_image) if s.end_image else None,
                    "video_prompt": sb.for_flow_video(sb.text_only_prompt(s) if (i2v_off and sb.fallback_t2v and s.mode == "keyframe") else sb.video_prompt(s), s),
                    "rendered_as": st.get("rendered_as"),
                    "image_prompt": sb.for_flow(sb.image_prompt(s)) if s.mode == "keyframe" else None,
                    "model": s.model or sb.model,
                    "model_status": meta.status(eff),
                    "model_retires_label": meta.retires_label(eff),
                    # a duration the effective model does not accept (not for done scenes: the clip exists)
                    "bad_duration": ({"duration": s.duration, "model": eff, "allowed": meta.durations(eff)}
                                     if status != "done" and dur.is_bad(sb, s, meta) else None),
                    "refs": [n for n, _ in sb.scene_refs(s)],
                    "characters": [c.name for c in sb.scene_characters(s)],
                    "characters_explicit": s.characters is not None,
                    "dialogue_warnings": talk.get(s.id, []),
                    # ffprobe only for "submitting" scenes with a file, never while a job runs here
                    "manual_clip": None if busy else pipe.manual_clip_state(s),
                    "qc": qc_view(pid, qc_all.get(s.id), st.get("video")),
                }
        final = out_dir(pid) / "final.mp4"
        srt = final.with_suffix(".srt")
        thumb_jpg = out_dir(pid) / "thumbnail.jpg"
        thumb_info: dict[str, Any] = {"url": file_url(pid, thumb_jpg), "picture_ready": False, "prompt": None}
        if sb:
            tp = Pipeline(sb, out_dir(pid), gflow(True))
            thumb_info.update(picture_ready=tp.has_thumbnail_image(), prompt=sb.for_flow(sb.thumbnail_prompt()),
                              refs=[n for n, _ in sb.thumbnail_refs()])
        thumb_info["suggestions"] = saved_titles(pid)
        return {
            **summary(pid),
            "storyboard": raw,
            "yaml": sb_path(pid).read_text(encoding="utf-8"),
            "errors": errors,
            "scene_state": scenes,
            "clips_rendered": clips_rendered,
            "project_id": project_id,
            "final_url": file_url(pid, final),
            "subtitle_files": subtitle_files(pid, sb),
            "subtitle_missing": subtitles.missing(sb) if sb else {},
            "final_size": final.stat().st_size if final.is_file() else None,
            "srt_url": file_url(pid, srt),
            "final_prefs": ui_prefs.read(pdir(pid)).get("final", {}),
            "i2v_unsupported": i2v_off,
            "i2v_error": st_data.get("i2v_error"),
            "characters_state": characters_state(pid, raw, sb),
            "thumbnail": thumb_info,
            "backup": last_backup(out_dir(pid)),
            "autopilot": autopilot.load_public(out_dir(pid)),
            **model_info,
            "path": str(pdir(pid)),
        }

    def subtitle_files(pid: str, sb: Storyboard | None) -> list[dict[str, Any]]:
        """The .srt of each subtitle language next to final.mp4 (narration's language first)."""
        final = out_dir(pid) / "final.mp4"
        out = []
        for i, lang in enumerate(sb.subtitle_langs() if sb else []):
            url = file_url(pid, final.with_name(f"final.{subtitles.lang_code(lang)}.srt"))
            if not url and i == 0:
                url = file_url(pid, final.with_suffix(".srt"))
            out.append({"lang": lang, "code": subtitles.lang_code(lang), "url": url})
        return out

    def characters_state(pid: str, raw: dict[str, Any], sb: Storyboard | None) -> list[dict[str, Any]]:
        out = []
        for i, c in enumerate(raw.get("characters") or []):
            c = c if isinstance(c, dict) else {}
            model = sb.characters[i] if sb and i < len(sb.characters) else None
            out.append({
                "name": c.get("name") or "",
                "image_url": file_url(pid, c.get("image")) if c.get("image") else None,
                "image_missing": bool(c.get("image")) and not file_url(pid, c.get("image")),
                "portrait_prompt": sb.for_flow(sb.portrait_prompt(model)) if (sb and model and model.description.strip()) else None,
            })
        return out

    # ---- static page ----

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        return html.replace("__TOKEN__", token)

    @app.get("/static/{name}")
    def static(name: str):
        p = (STATIC / name).resolve()
        if p.parent != STATIC.resolve() or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "no-cache"})

    # ---- environment / settings ----

    gflow_version: dict[str, Any] = {}

    def gflow_ver(g: Gflow, refresh: bool) -> str | None:
        if refresh or "v" not in gflow_version:
            v = None
            if g.available():
                try:
                    proc = subprocess.run([*_split(g.binary), "--version"], capture_output=True, text=True, timeout=30)
                    m = re.search(r"\d+\.\d+[\w.]*", proc.stdout + proc.stderr)
                    v = m.group(0) if m else None
                except (OSError, subprocess.TimeoutExpired):
                    pass
            gflow_version["v"] = v
        return gflow_version["v"]

    @app.get("/api/env")
    def env(refresh: bool = False) -> dict[str, Any]:
        s = load_settings()
        g = gflow()
        return {
            "gflow": shutil.which(g.binary.split()[0]) if g.available() else None,
            "gflow_version": gflow_ver(g, refresh),
            "flow_ui": g.ui_available(),
            "ffmpeg": shutil.which("ffmpeg"),
            "ffprobe": shutil.which("ffprobe"),
            "anthropic_key": bool(s.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")),
            "claude_code": claude_code(refresh=refresh),
            "planner": planner_backend(),
            "workspace": str(workspace),
            "profile": s.get("profile") or None,
            "platform": sys.platform,
            "camera_presets": {k: [{"value": v, "label": l} for v, l in opts] for k, opts in CAMERA_PRESETS.items()},
            "qc": qc.status(),
        }

    @app.post("/api/env/auth")
    def auth_status() -> dict[str, Any]:
        g = gflow()
        if not g.available():
            return {"ok": False, "text": "Không thấy lệnh gflow."}
        try:
            res = g.auth_status()
        except subprocess.TimeoutExpired:
            return {"ok": False, "text": "Hết thời gian kiểm tra đăng nhập."}
        text = (res.stdout.strip() or res.stderr.strip())
        m = re.search(r"verified as (\S+?@\S+?)\.?\s*$", text, re.M)
        return {"ok": res.returncode == 0, "account": m.group(1) if m else None, "text": text[-2000:]}

    @app.post("/api/env/login")
    def login() -> dict[str, Any]:
        g = gflow()
        if not g.available():
            raise HTTPException(400, "Không thấy lệnh gflow.")
        subprocess.Popen(g._cmd("auth", "login", "--browser", "chrome"), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {"ok": True}

    @app.post("/api/env/credits")
    def credits() -> dict[str, Any]:
        g = gflow()
        if not g.available():
            raise HTTPException(400, "Không thấy lệnh gflow.")
        try:
            payload = g.run_json("credits", "user")
        except GflowError as exc:
            hint = (exc.payload.get("error") or {}).get("remediation_hint") if exc.payload else None
            return {"ok": False, "error": str(exc), "hint": hint}
        return {"ok": True, "data": payload}

    def raw_models() -> dict[str, Any] | None:
        """`gflow models --json`, cached for the process. None (and nothing cached) if gflow fails."""
        if models_cache:
            return models_cache
        g = gflow()
        if not g.available():
            return None
        try:
            proc = subprocess.run(g._cmd("models", "--json"), capture_output=True, text=True, timeout=10)
            data = _extract_json(proc.stdout) or {}
            video = [{"alias": m["aliases"][0], "max_duration": m.get("max_duration")}
                     for m in data.get("video", {}).get("models", []) if m.get("aliases")]
            image = [{"alias": m["aliases"][0], "label": m.get("label", "").split(" (")[0]}
                     for m in data.get("image", {}).get("models", []) if m.get("aliases")]
        except (OSError, subprocess.TimeoutExpired, KeyError, TypeError, AttributeError, IndexError):
            return None
        if not (video and image):
            return None
        models_cache.update({"video": video, "image": image})
        return models_cache

    @app.get("/api/models")
    def models() -> dict[str, Any]:
        # The metadata is merged after the cache and re-read on every call, so edits show at once.
        raw = raw_models()
        meta = model_meta.load()
        src = raw if raw is not None else meta.fallback
        video = []
        for m in src.get("video") or []:
            alias = m.get("alias")
            video.append({**m, "info": meta.info(alias), "status": meta.status(alias),
                          "retires_label": meta.retires_label(alias)})
        return {
            "video": video,
            "image": [dict(m) for m in src.get("image") or []],
            "source": "gflow" if raw is not None else "fallback",
            "today": meta.today.isoformat(),
            "plan": meta.plan,
            "default_video_model": meta.default_video_model,
            "metadata_error": meta.error_text,
        }

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        s = load_settings()
        key = s.get("anthropic_api_key") or ""
        s["anthropic_api_key"] = ""
        s["anthropic_key_set"] = bool(key)
        s["anthropic_key_hint"] = f"…{key[-4:]}" if key else ""
        s["anthropic_key_from_env"] = bool(os.environ.get("ANTHROPIC_API_KEY")) and not key
        return s

    @app.put("/api/settings")
    def put_settings(body: dict[str, Any]) -> dict[str, Any]:
        s = load_settings()
        if body.get("planner") in ("auto", "claude_code", "api"):
            s["planner"] = body["planner"]
        for k in ("profile", "pause_min", "pause_max", "retries", "flow_ui", "flow_ui_max_credits", "flow_ui_window",
                  "auto_backup", "auto_qc"):
            if k in body:
                s[k] = body[k]
        if "backup_remote" in body:
            try:
                s["backup_remote"] = check_remote(str(body["backup_remote"] or DEFAULT_BACKUP_REMOTE))
            except BackupError as exc:
                raise HTTPException(400, str(exc))
        s["auto_backup"] = bool(s.get("auto_backup"))
        s["auto_qc"] = bool(s.get("auto_qc", True))
        if body.get("anthropic_api_key"):
            s["anthropic_api_key"] = body["anthropic_api_key"].strip()
        if body.get("clear_anthropic_key"):
            s["anthropic_api_key"] = ""
        s["pause_min"] = max(0.0, float(s.get("pause_min") or 0))
        s["pause_max"] = max(float(s["pause_min"]), float(s.get("pause_max") or 0))
        s["retries"] = max(0, min(5, int(s.get("retries") or 0)))
        s["flow_ui"] = bool(s.get("flow_ui", True))
        s["flow_ui_max_credits"] = max(0, int(s.get("flow_ui_max_credits") or 0))
        if s.get("flow_ui_window") not in ("show", "hidden", "headless"):
            s["flow_ui_window"] = "hidden"
        save_settings(s)
        return get_settings()

    # ---- projects ----

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        out = []
        for d in workspace.iterdir():
            if d.is_dir() and not d.name.startswith(".") and (d / "storyboard.yaml").is_file():
                try:
                    out.append(summary(d.name))
                except Exception as exc:
                    out.append({"id": d.name, "title": d.name, "scenes": 0, "done": 0, "error": str(exc),
                                "updated": (d / "storyboard.yaml").stat().st_mtime})
        return sorted(out, key=lambda p: -p["updated"])

    @app.post("/api/projects")
    def create_project(body: NewProject) -> dict[str, Any]:
        title = body.title.strip() or "Phim mới"
        pid, d = new_project_dir(title)
        if body.source == "template":
            data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
            data["title"] = title if body.title.strip() else data["title"]
        else:
            data = {"title": title, "aspect": "16:9", "model": None, "image_model": "nano2", "style": "",
                    "characters": [], "scenes": [{"id": "s01", "mode": "keyframe", "image_prompt": "", "prompt": ""}]}
        data["model"] = model_meta.load().default_video_model  # new projects never start on a retiring model
        (d / "storyboard.yaml").write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return {"id": pid}

    @app.post("/api/projects/import")
    async def import_project(file: UploadFile = File(...)) -> dict[str, Any]:
        try:
            data = yaml.safe_load((await file.read()).decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise HTTPException(400, f"File YAML không đọc được: {exc}")
        if not isinstance(data, dict) or "scenes" not in data:
            raise HTTPException(400, "File không phải storyboard (thiếu 'scenes').")
        pid, d = new_project_dir(str(data.get("title") or Path(file.filename or "phim").stem))
        # Images referenced by absolute path are copied in so the project is self-contained.
        for s in data.get("scenes") or []:
            for attr in ("image", "end_image"):
                src = s.get(attr) if isinstance(s, dict) else None
                if src and Path(src).is_absolute() and Path(src).is_file():
                    dest = d / "assets" / Path(src).name
                    shutil.copy2(src, dest)
                    s[attr] = f"assets/{dest.name}"
        for c in data.get("characters") or []:
            src = c.get("image") if isinstance(c, dict) else None
            if src and Path(src).is_absolute() and Path(src).is_file():
                dest = d / "assets" / "characters" / Path(src).name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                c["image"] = f"assets/characters/{dest.name}"
        (d / "storyboard.yaml").write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return {"id": pid}

    def _lang_or_400(value: object, label: str, *, dialogue: bool) -> str:
        """A language sent by the "Phim mới" form: blank / too long / line break -> 400 (Vietnamese)."""
        try:
            v = (clean_dialogue_language if dialogue else clean_language)(value)
        except ValueError as exc:
            raise HTTPException(400, f"{label}: {exc}.")
        if v is None:
            raise HTTPException(400, f"{label}: hãy nhập tên ngôn ngữ.")
        return v

    def check_lang_fields(data: dict[str, Any], on_disk: dict[str, Any] | None = None) -> None:
        """Only the two language keys, when present (blank / null = not set). The rest of the storyboard keeps
        the "save a broken draft" behaviour, so the whole model is NOT validated here.
        With `on_disk`, a key left unchanged is not checked: a bad value hand-written in the YAML must not
        block every autosave of the other fields (it is still reported by the model errors)."""
        def clean_list(v: object) -> object:
            if v is None:
                return None
            if not isinstance(v, list):
                raise ValueError("phải là danh sách tên ngôn ngữ")
            return [clean_language(x) for x in v]

        for key, label, clean in (("dialogue_language", "Ngôn ngữ thoại", clean_dialogue_language),
                                  ("subtitle_language", "Ngôn ngữ phụ đề", clean_language),
                                  ("subtitle_languages", "Ngôn ngữ phụ đề", clean_list)):
            if key in data and not (on_disk is not None and key in on_disk and on_disk[key] == data[key]):
                try:
                    clean(data[key])
                except ValueError as exc:
                    raise HTTPException(400, f"{label}: {exc}.")

    def sub_langs_or_400(body: PlanBody) -> list[str]:
        """The subtitle languages of the "Phim mới" form (narration's first), deduplicated."""
        raw = body.languages if body.languages else [body.language]
        out: list[str] = []
        for v in raw:
            lang = _lang_or_400(v, "Ngôn ngữ phụ đề", dialogue=False)
            if lang.casefold() == NO_DIALOGUE:
                raise HTTPException(400, "Ngôn ngữ phụ đề: cần một ngôn ngữ cụ thể, không dùng “none”.")
            if lang.casefold() not in {o.casefold() for o in out}:
                out.append(lang)
        if len(out) > 6:
            raise HTTPException(400, "Ngôn ngữ phụ đề: tối đa 6 ngôn ngữ.")
        return out

    def add_subtitle_langs(sb: Storyboard, langs: list[str], backend: str | None, key: str | None,
                           log: Callable[[str], None]) -> None:
        """A new storyboard with several subtitle languages: store them and translate the narration now."""
        if len(langs) < 2:
            return
        sb.subtitle_languages = [sb.subtitle_language or langs[0], *langs[1:]]
        log("Claude đang dịch phụ đề sang " + ", ".join(langs[1:]) + "…")
        try:
            done = subtitles.translate(sb, backend=backend or "auto", api_key=key)
        except (RuntimeError, ValueError) as exc:
            log(f"⚠ Chưa dịch được phụ đề ({exc}); dịch lại sau ở tab Ghép phim.")
            return
        for s in sb.scenes:
            if s.id in done:
                s.subtitles = {**(s.subtitles or {}), **done[s.id]}

    @app.post("/api/projects/plan")
    def plan_project(body: PlanBody) -> dict[str, Any]:
        if not body.idea.strip():
            raise HTTPException(400, "Hãy nhập ý tưởng.")
        langs = sub_langs_or_400(body)
        language = langs[0]
        dialogue = (_lang_or_400(body.dialogue_language, "Ngôn ngữ thoại", dialogue=True)
                    if body.dialogue_language is not None else None)
        backend = planner_backend()
        if not backend:
            raise HTTPException(400, "Chưa dùng được Claude: cài Claude Code và đăng nhập, hoặc thêm Anthropic API key trong Cài đặt.")
        key = load_settings().get("anthropic_api_key") or None

        def work(job: Job) -> dict[str, Any]:
            from ..planner import plan_auto

            via = "Claude Code (gói Claude của bạn)" if backend == "claude_code" else "Anthropic API"
            job.log(f"Claude đang viết storyboard {body.scenes} cảnh qua {via}…")
            job.log(f"Ý tưởng: {body.idea[:300]}")
            dl_label = ("chưa đặt (Veo tự đoán)" if dialogue is None
                        else "không thoại" if dialogue == NO_DIALOGUE else dialogue)
            job.log(f"Ngôn ngữ — nhân vật nói: {dl_label}; phụ đề: {language}")
            sb, _ = plan_auto(body.idea, backend=backend, api_key=key, scenes=max(1, min(20, body.scenes)),
                              aspect=body.aspect, language_hint=language, dialogue_language=dialogue)
            add_subtitle_langs(sb, langs, backend, key, job.log)
            pid, d = new_project_dir(sb.title)
            save_storyboard(sb, d / "storyboard.yaml")
            remember_idea(d, body.idea)
            job.log(f"✔ Xong: “{sb.title}” — {len(sb.scenes)} cảnh, {len(sb.characters)} nhân vật.")
            return {"project": pid}

        return jobs.start("plan", "Viết storyboard bằng AI", None, work).public()

    @app.get("/api/projects/{pid}")
    def get_project(pid: str) -> dict[str, Any]:
        return detail(pid)

    @app.put("/api/projects/{pid}")
    def save_project(pid: str, body: SaveBody) -> dict[str, Any]:
        pdir(pid)
        try:
            on_disk = read_raw(pid)
        except (HTTPException, yaml.YAMLError, OSError):
            on_disk = None
        check_lang_fields(body.storyboard, on_disk)
        write_raw(pid, body.storyboard)
        return detail(pid)

    @app.put("/api/projects/{pid}/yaml")
    def save_yaml(pid: str, body: YamlBody) -> dict[str, Any]:
        try:
            data = yaml.safe_load(body.text)
        except yaml.YAMLError as exc:
            raise HTTPException(400, f"YAML lỗi: {exc}")
        if not isinstance(data, dict):
            raise HTTPException(400, "YAML phải là một bảng (title, scenes, ...).")
        check_lang_fields(data)
        with sb_lock(pid):
            sb_path(pid).write_text(body.text, encoding="utf-8")
        return detail(pid)

    @app.post("/api/projects/{pid}/duplicate")
    def duplicate(pid: str) -> dict[str, Any]:
        src = pdir(pid)
        raw = read_raw(pid)
        title = f"{raw.get('title') or pid} (bản sao)"
        new_id, d = new_project_dir(title)
        shutil.copytree(src / "assets", d / "assets", dirs_exist_ok=True)
        raw["title"] = title
        raw.pop("project_id", None)
        (d / "storyboard.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        src_prefs = ui_prefs.read(src)
        if "board" in src_prefs:
            src_board = src_prefs["board"]
            ui_prefs.update(d, lambda p: p.__setitem__("board", src_board))
        return {"id": new_id}

    @app.get("/api/projects/{pid}/board")
    def get_board(pid: str) -> dict[str, Any]:
        return ui_prefs.board_of(ui_prefs.read(pdir(pid)))

    @app.put("/api/projects/{pid}/board")
    async def put_board(pid: str, request: Request) -> dict[str, Any]:
        """Board layout (card positions, video links, view). Only ui.json: never the storyboard, never a job."""
        d = pdir(pid)
        too_big = HTTPException(413, f"Bố cục bảng quá lớn (tối đa {BOARD_MAX_BYTES // 1024} KB)")
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > BOARD_MAX_BYTES:
            raise too_big
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():  # stop early even without Content-Length (chunked)
            size += len(chunk)
            if size > BOARD_MAX_BYTES:
                raise too_big
            chunks.append(chunk)
        try:
            data = json.loads(b"".join(chunks))
        except ValueError:
            raise HTTPException(400, "Bố cục bảng không phải JSON hợp lệ")
        try:
            board = ui_prefs.validate_board(data)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        await run_in_threadpool(ui_prefs.update, d, lambda p: p.__setitem__("board", board))
        return board

    @app.delete("/api/projects/{pid}")
    def delete(pid: str) -> dict[str, Any]:
        d = pdir(pid)
        if jobs.current and jobs.current.status == "running" and jobs.current.project == pid:
            raise HTTPException(409, detail={"message": "Dự án đang chạy, hãy dừng trước.", "reason": "job_running"})
        trash = workspace / ".trash"
        trash.mkdir(exist_ok=True)
        dest = trash / f"{pid}-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.move(str(d), dest)  # recoverable: moved, not erased
        return {"ok": True, "trash": str(dest)}

    @app.post("/api/projects/{pid}/upload")
    async def upload(pid: str, file: UploadFile = File(...), kind: str = Form("image")) -> dict[str, Any]:
        d = pdir(pid) / "assets" / ("characters" if kind == "character" else "")
        d.mkdir(parents=True, exist_ok=True)
        name = Path(file.filename or "file").name
        ext = Path(name).suffix.lower()
        allowed = IMAGE_EXT if kind in ("image", "character") else AUDIO_EXT
        if ext not in allowed:
            raise HTTPException(400, f"Chỉ nhận file {', '.join(sorted(allowed))}")
        stem = slugify(Path(name).stem)
        dest = d / f"{stem}{ext}"
        n = 2
        while dest.exists():
            dest, n = d / f"{stem}-{n}{ext}", n + 1
        with dest.open("wb") as fh:
            while chunk := await file.read(1 << 20):
                fh.write(chunk)
        return {"path": dest.relative_to(pdir(pid)).as_posix(), "url": file_url(pid, dest)}

    @app.get("/api/projects/{pid}/assets")
    def assets(pid: str) -> dict[str, Any]:
        base = pdir(pid)
        d = base / "assets"
        files = sorted(f for f in d.iterdir() if f.is_file()) if d.is_dir() else []
        chars = d / "characters"
        char_files = sorted(f for f in chars.iterdir() if f.is_file()) if chars.is_dir() else []
        item = lambda f: {"path": f.relative_to(base).as_posix(), "url": file_url(pid, f)}
        return {
            "images": [item(f) for f in files + char_files if f.suffix.lower() in IMAGE_EXT],
            "audio": [item(f) for f in files if f.suffix.lower() in AUDIO_EXT],
        }

    @app.get("/api/projects/{pid}/file/{rel:path}")
    def project_file(pid: str, rel: str):
        base = pdir(pid).resolve()
        p = (base / rel).resolve()
        if base not in p.parents or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p)

    @app.post("/api/projects/{pid}/reveal")
    def reveal(pid: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        base = pdir(pid).resolve()
        target = base
        if body and body.get("path"):
            p = (base / body["path"]).resolve()
            if base in p.parents and p.exists():
                target = p
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)] if target.is_file() else ["open", str(target)])
        elif os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(target)] if target.is_file() else ["explorer", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target if target.is_dir() else target.parent)])
        return {"ok": True}

    @app.post("/api/projects/{pid}/reset-i2v")
    def reset_i2v(pid: str) -> dict[str, Any]:
        state = State(out_dir(pid) / "state.json")
        state.data.pop("i2v_unsupported", None)
        state.data.pop("i2v_error", None)
        state.save()
        g = gflow(True)
        flow_caps.remember(g.binary, g.profile, i2v_unsupported=None, i2v_error=None)  # the next project tries too
        return detail(pid)

    @app.put("/api/projects/{pid}/final-settings")
    def final_settings(pid: str, body: FinalSettings) -> dict[str, Any]:
        def put(prefs: dict[str, Any]) -> dict[str, Any]:
            prefs["final"] = body.model_dump()
            return prefs["final"]

        return ui_prefs.update(pdir(pid), put)

    def saved_titles(pid: str) -> list[dict[str, str]]:
        from ..planner import _clean_titles

        return _clean_titles(ui_prefs.read(pdir(pid)).get("thumbnail_titles"), 20)  # old projects stored plain strings

    def suggest_titles_for(pid: str, sb: Storyboard, log: Callable[[str], None] = lambda *_: None) -> list[dict[str, str]]:
        from ..planner import suggest_titles

        backend = planner_backend()
        if not backend:
            raise RuntimeError("Chưa dùng được Claude: cài Claude Code và đăng nhập, hoặc thêm Anthropic API key trong Cài đặt.")
        log("Claude đang nghĩ tiêu đề cho video…")
        titles = suggest_titles(sb, backend=backend, api_key=load_settings().get("anthropic_api_key") or None)
        if not titles:
            raise RuntimeError("Claude không trả về tiêu đề nào.")
        ui_prefs.update(pdir(pid), lambda p: p.__setitem__("thumbnail_titles", titles))
        return titles

    @app.post("/api/projects/{pid}/thumbnail/titles")
    def thumbnail_titles(pid: str) -> dict[str, Any]:
        """Title ideas for the thumbnail (Claude). Not a job: it does not touch Flow."""
        try:
            sb = load_storyboard(sb_path(pid))
        except (ValidationError, ValueError) as exc:
            raise HTTPException(400, "Storyboard còn lỗi: " + "; ".join(
                _validation_errors(exc) if isinstance(exc, ValidationError) else [str(exc)]))
        try:
            return {"titles": suggest_titles_for(pid, sb)}
        except RuntimeError as exc:
            raise HTTPException(400, str(exc))

    # ---- ask AI about the film ----

    def remember_idea(d: Path, idea: str) -> None:
        """Keep the idea the film was planned from (ui.json), so “Hỏi AI” knows it later."""
        idea = (idea or "").strip()[:8000]
        if idea:
            ui_prefs.update(d, lambda p: p.__setitem__("idea", idea))

    def ask_view(pid: str) -> dict[str, Any]:
        sections = film_chat.gather(pdir(pid), out_dir(pid))
        return {"messages": film_chat.load(out_dir(pid)),
                "context": [{"title": t, "chars": len(x)} for t, x in sections],
                "has_final": (out_dir(pid) / "final.mp4").is_file()}

    @app.get("/api/projects/{pid}/ask")
    def get_ask(pid: str) -> dict[str, Any]:
        return ask_view(pid)

    @app.post("/api/projects/{pid}/ask")
    def post_ask(pid: str, body: AskBody) -> dict[str, Any]:
        """A question about the film (Claude, free on Flow). Not a job: it can run while Flow renders."""
        backend = planner_backend()
        if not backend:
            raise HTTPException(400, "Chưa dùng được Claude: cài Claude Code và đăng nhập, hoặc thêm Anthropic API key trong Cài đặt.")
        if film_chat.lock(out_dir(pid)).locked():
            raise HTTPException(409, detail={"message": "Đang trả lời câu hỏi trước, đợi một chút.", "reason": "ask_busy"})
        try:
            film_chat.ask(pdir(pid), out_dir(pid), body.question, with_pictures=body.pictures, backend=backend,
                          api_key=load_settings().get("anthropic_api_key") or None)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except RuntimeError as exc:
            raise HTTPException(502, str(exc))
        return ask_view(pid)

    @app.delete("/api/projects/{pid}/ask")
    def delete_ask(pid: str) -> dict[str, Any]:
        film_chat.clear(out_dir(pid))
        return ask_view(pid)

    # ---- posts for social networks ----

    @app.get("/api/projects/{pid}/social")
    def get_social(pid: str) -> dict[str, Any]:
        return {"result": social.load(out_dir(pid)), "platforms": social.PLATFORMS, "languages": social.LANGUAGES}

    @app.post("/api/projects/{pid}/social")
    def post_social(pid: str, body: SocialBody) -> dict[str, Any]:
        """Write Facebook / TikTok / Instagram posts in the chosen languages (Claude, free on Flow)."""
        backend = planner_backend()
        if not backend:
            raise HTTPException(400, "Chưa dùng được Claude: cài Claude Code và đăng nhập, hoặc thêm Anthropic API key trong Cài đặt.")
        if film_chat.lock(out_dir(pid)).locked():
            raise HTTPException(409, detail={"message": "AI đang bận với phim này, đợi một chút.", "reason": "ask_busy"})
        try:
            social.generate(pdir(pid), out_dir(pid), body.platforms, body.languages, note=body.note,
                            with_pictures=body.pictures, backend=backend,
                            api_key=load_settings().get("anthropic_api_key") or None)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except RuntimeError as exc:
            raise HTTPException(502, str(exc))
        return get_social(pid)

    # ---- subtitles in several languages ----

    @app.post("/api/projects/{pid}/subtitles/translate")
    def translate_subtitles(pid: str, body: TranslateBody) -> dict[str, Any]:
        """Claude translates the narration into the film's other subtitle languages. Free on Flow."""
        try:
            sb = load_storyboard(sb_path(pid))
        except (ValidationError, ValueError) as exc:
            raise HTTPException(400, "Storyboard còn lỗi: " + "; ".join(
                _validation_errors(exc) if isinstance(exc, ValidationError) else [str(exc)]))
        if len(sb.subtitle_langs()) < 2:
            raise HTTPException(400, "Phim mới có một ngôn ngữ phụ đề: thêm ngôn ngữ ở tab Phong cách & nhân vật.")
        backend = planner_backend()
        if not backend:
            raise HTTPException(400, "Chưa dùng được Claude: cài Claude Code và đăng nhập, hoặc thêm Anthropic API key trong Cài đặt.")
        langs = [clean_language(x) for x in body.langs if clean_language(x)]
        try:
            done = subtitles.translate(sb, langs=langs or None, only_missing=not body.force, backend=backend,
                                       api_key=load_settings().get("anthropic_api_key") or None)
        except RuntimeError as exc:
            raise HTTPException(502, str(exc))
        with sb_lock(pid):
            raw = read_raw(pid)
            changed = subtitles.apply(raw, done)
            if changed:
                write_raw(pid, raw)
        return {"changed": changed, **detail(pid)}

    @app.post("/api/projects/{pid}/characters/portraits")
    def portraits(pid: str, body: PortraitBody) -> dict[str, Any]:
        """Generate reference images for characters (free on Flow) and store them in the storyboard."""
        try:
            sb = load_storyboard(sb_path(pid))
        except (ValidationError, ValueError) as exc:
            raise HTTPException(400, "Storyboard còn lỗi: " + "; ".join(
                _validation_errors(exc) if isinstance(exc, ValidationError) else [str(exc)]))
        wanted = set(body.names)
        unknown = wanted - {c.name for c in sb.characters}
        if unknown:
            raise HTTPException(400, f"Không có nhân vật: {', '.join(sorted(unknown))}")
        picked: list[tuple[int, Any, Path]] = []
        used: set[str] = set()
        for i, c in enumerate(sb.characters):
            if wanted and c.name not in wanted:
                continue
            if not wanted and c.image and Path(c.image).is_file() and not body.force:
                continue
            if not c.name.strip() or not c.description.strip():
                raise HTTPException(400, f"Nhân vật #{i + 1} cần có tên và mô tả trước khi tạo ảnh.")
            stem = slugify(c.name) if slugify(c.name) != "phim" else f"nhan-vat-{i + 1}"
            while stem in used:
                stem += f"-{i + 1}"
            used.add(stem)
            picked.append((i, c, pdir(pid) / "assets" / "characters" / f"{stem}.png"))
        if not picked:
            raise HTTPException(400, "Tất cả nhân vật đã có ảnh. Bấm “Tạo lại” ở từng nhân vật để làm lại.")
        settings = load_settings()

        def work(job: Job) -> dict[str, Any]:
            g = gflow(log=job.log)
            if not g.available():
                raise RuntimeError("không thấy lệnh gflow")
            pipe = Pipeline(sb, out_dir(pid), g, log=job.log, sleep=job.sleep, progress=job.set_progress)
            images: dict[str, str] = {}

            def save_one(idx: int, c: Any, dest: Path) -> None:
                """Store each image as soon as it is made, so the UI shows it before the job ends."""
                i = picked[idx][0]  # index in the storyboard (names may repeat)
                with sb_lock(pid):
                    raw = read_raw(pid)
                    chars = raw.get("characters") or []
                    if i < len(chars) and isinstance(chars[i], dict) and chars[i].get("name") == c.name:
                        rel = dest.relative_to(pdir(pid)).as_posix()
                        chars[i]["image"] = rel
                        images[c.name] = rel
                        write_raw(pid, raw)

            report = pipe.generate_portraits([(c, dest) for _, c, dest in picked], RunOptions(
                max_retries=int(settings.get("retries", 2)),
                pause=(float(settings.get("pause_min", 8)), float(settings.get("pause_max", 20))),
                should_stop=lambda: job.stop_requested), on_done=save_one)
            with sb_lock(pid):  # read-modify-write: a model change must not land in between
                raw = read_raw(pid)
                chars = raw.get("characters") or []
                for i, c, dest in picked:
                    if c.name in report.done and i < len(chars) and isinstance(chars[i], dict) and chars[i].get("name") == c.name:
                        rel = dest.relative_to(pdir(pid)).as_posix()
                        chars[i]["image"] = rel
                        images[c.name] = rel
                if images:
                    write_raw(pid, raw)
            job.log("")
            job.log(f"Xong: {len(report.done)} ảnh nhân vật | Lỗi: {len(report.failed)}")
            if report.aborted:
                job.log(f"⚠ {report.aborted}")
            res: dict[str, Any] = {"images": images, "failed": report.failed}
            if report.aborted or report.failed:
                res["error"] = report.aborted or f"{len(report.failed)} ảnh lỗi"
            return res

        title = "Tạo ảnh nhân vật" + (f" ({', '.join(c.name for _, c, _ in picked)})" if wanted else "")
        return jobs.start("portraits", title, pid, work).public()

    # ---- model change ----

    def model_change_plan(pid: str, body: ModelChangeBody) -> model_change.ChangePlan:
        try:
            sb = load_storyboard(sb_path(pid))
        except (ValidationError, ValueError) as exc:
            raise HTTPException(400, "Storyboard còn lỗi: " + "; ".join(
                _validation_errors(exc) if isinstance(exc, ValidationError) else [str(exc)]))
        try:
            return model_change.plan_change(Pipeline(sb, out_dir(pid), gflow(True)), model_meta.load(), body.target,
                                            scenes=body.scenes, include=body.include, rounding=body.rounding)
        except model_change.ChangeError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/projects/{pid}/model-change/preview")
    def model_change_preview(pid: str, body: ModelChangeBody) -> dict[str, Any]:
        pdir(pid)  # check pid before creating a lock for it
        with sb_lock(pid):  # hash and plan from the same file contents
            base = model_change.content_hash(sb_path(pid))
            plan = model_change_plan(pid, body)
        return plan.public(base)

    @app.post("/api/projects/{pid}/model-change")
    def model_change_apply(pid: str, body: ModelChangeBody) -> dict[str, Any]:
        """Only rewrites storyboard.yaml: no job, nothing sent to Flow (AC35)."""
        pdir(pid)  # check pid before creating a lock for it
        if jobs.current and jobs.current.status == "running" and jobs.current.project == pid:
            # a running video job keeps the storyboard it read at start: changing now could cost credits twice
            raise HTTPException(409, detail={"message": "Đang có job chạy, đợi xong rồi đổi model.",
                                             "reason": "job_running"})
        with sb_lock(pid):
            base = model_change.content_hash(sb_path(pid))  # before planning: the plan is made from these bytes
            plan = model_change_plan(pid, body)
            if not body.base:
                raise HTTPException(400, "Thiếu base (lấy từ bản xem trước).")
            if body.base != base:
                raise HTTPException(409, detail={"message": "Storyboard đã thay đổi từ lúc xem trước. Hãy xem trước lại.",
                                                 "reason": "stale_base"})
            if plan.bad_duration and not body.rounding:
                raise HTTPException(400, "Có cảnh có độ dài model mới không nhận: chọn làm tròn lên hoặc xuống.")
            changed = plan.has_changes()
            if changed:
                write_raw(pid, model_change.apply_to_raw(read_raw(pid), plan))
        if not changed:
            return {"changed": False, "message": "Không có gì thay đổi", "project": detail(pid)}
        return {"changed": True, "preview": plan.public(base), "project": detail(pid)}

    # ---- durations: round the scenes whose duration the model does not accept ----

    def durations_state(pid: str) -> tuple[Pipeline, model_meta.ModelMeta, str]:
        """(pipe, meta, base) from the file on disk. Call inside sb_lock(pid). 400 for a broken storyboard."""
        try:
            sb = load_storyboard(sb_path(pid))
        except (ValidationError, ValueError, yaml.YAMLError) as exc:
            raise HTTPException(400, "Storyboard còn lỗi: " + "; ".join(
                _validation_errors(exc) if isinstance(exc, ValidationError) else [str(exc)]))
        pipe = Pipeline(sb, out_dir(pid), gflow(True), log=lambda *_: None)
        meta = model_meta.load()
        # the scene groups and allowed durations are in the base: a scene rendered or a models.yaml edit
        # between preview and round gives 409 stale_base
        base = dur.base_of(sb_path(pid).read_bytes(), dur.groups(pipe), dur.allowed_map(pipe, meta))
        return pipe, meta, base

    @app.post("/api/projects/{pid}/durations/preview")
    def durations_preview(pid: str) -> dict[str, Any]:
        """Only reads: the roundable scenes, the interrupted ones skipped, and the base for the round."""
        pdir(pid)  # check pid before creating a lock for it
        with sb_lock(pid):
            pipe, meta, base = durations_state(pid)
            scenes, skipped = dur.preview(pipe, meta)
        return {"scenes": scenes, "skipped": skipped, "base": base}

    @app.post("/api/projects/{pid}/durations/round")
    def durations_round(pid: str, body: RoundBody) -> dict[str, Any]:
        """Only rewrites `duration` in storyboard.yaml: no job, nothing sent to Flow. Done and interrupted
        scenes are never changed (recomputed here from the current state, even when the base matches)."""
        pdir(pid)  # check pid before creating a lock for it
        if body.direction not in dur.DIRECTIONS:
            raise HTTPException(400, "direction phải là up hoặc down.")
        if not body.base:
            raise HTTPException(400, "Thiếu base (lấy từ bản xem trước).")
        with jobs.lock:  # no job may start for this project while the storyboard is written
            if jobs.current and jobs.current.status == "running" and jobs.current.project == pid:
                raise HTTPException(409, detail={"message": "Đang có job chạy, đợi xong rồi làm tròn độ dài.",
                                                 "reason": "job_running"})
            with sb_lock(pid):
                pipe, meta, base = durations_state(pid)
                if body.base != base:
                    raise HTTPException(409, detail={
                        "message": "Storyboard vừa thay đổi từ lúc xem trước. Hãy xem lại bảng rồi áp dụng.",
                        "reason": "stale_base"})
                scenes, skipped = dur.preview(pipe, meta)
                edits = dur.edits_for(scenes, body.direction)
                if edits:
                    write_raw(pid, dur.apply_to_raw(read_raw(pid), edits), clean=False)
        changed = [{"id": r["id"], "from": r["duration"], "to": edits[r["id"]]} for r in scenes]
        return {"changed": bool(changed), "scenes": changed, "skipped": skipped, "project": detail(pid)}

    # ---- run / estimate ----

    def check_ids(sb: Storyboard, body: RunBody) -> None:
        """400 for scene ids in only/force/confirm_resend that are not in the storyboard."""
        unknown = (set(body.only) | set(body.force) | set(body.confirm_resend)) - {s.id for s in sb.scenes}
        if unknown:
            raise HTTPException(400, f"Không có cảnh: {', '.join(sorted(unknown))}")

    @dataclass
    class WebPlan:
        send: list[Scene]
        blocked: list[Scene]
        adopt: list[Scene]
        needs_confirm: list[str]  # forced interrupted scenes not ticked in confirm_resend (storyboard order)
        retired: list[dict[str, Any]]  # sent or forced scenes whose model is retired (storyboard order)
        unverified_clip: list[str]  # blocked legacy interrupted scenes with a readable clip
        bad_duration: list[dict[str, Any]]  # sent scenes with a duration their model does not accept
        blocked_bad_duration: list[dict[str, Any]]  # blocked (interrupted) scenes with a bad duration

    def web_video_plan(pipe: Pipeline, body: RunBody) -> WebPlan:
        """What a web video run does with each scene: the one rule for /estimate and /run. On the web an
        interrupted scene is only resent when its id is in `confirm_resend` (`retry_unknown` is ignored)."""
        force = set(body.force)
        plan = pipe.video_plan(set(body.only), force, False, confirm=set(body.confirm_resend))  # empty only = all
        blocked = [s for s, kind in plan if kind == "blocked"]
        needs = [s.id for s in blocked if s.id in force]
        send = [s for s, kind in plan if kind == "send"]
        meta = model_meta.load()
        rows = {r["id"]: r for r in dur.scan(pipe, meta)}  # adopted scenes are not checked (AC5)
        return WebPlan(
            send=send,
            blocked=blocked,
            adopt=[s for s, kind in plan if kind == "adopt"],
            needs_confirm=needs,
            retired=retirement.retired_of(pipe, meta, [s for s, kind in plan if kind == "send" or s.id in needs]),
            unverified_clip=[s.id for s in blocked if pipe.manual_clip_state(s) == "unverified"],
            bad_duration=[rows[s.id] for s in send if s.id in rows],
            blocked_bad_duration=[rows[s.id] for s in blocked if s.id in rows],
        )

    @app.post("/api/projects/{pid}/estimate")
    def estimate(pid: str, body: RunBody) -> dict[str, Any]:
        try:
            sb = load_storyboard(sb_path(pid))
        except (ValidationError, ValueError) as exc:
            raise HTTPException(400, "Storyboard còn lỗi: " + "; ".join(
                _validation_errors(exc) if isinstance(exc, ValidationError) else [str(exc)]))
        check_ids(sb, body)
        pipe = Pipeline(sb, out_dir(pid), gflow(True))
        wp = web_video_plan(pipe, body)
        todo = wp.send
        kf_new = [s.id for s in todo if s.mode == "keyframe" and (not pipe.has_keyframe(s) or s.id in body.force_keyframes)]
        return {
            "renders": [{"id": s.id, "mode": s.mode, "model": s.model or sb.model or "mặc định",
                         "duration": s.duration, "prompt": s.prompt[:120]} for s in todo],
            "blocked": [s.id for s in wp.blocked],
            "adopt": [s.id for s in wp.adopt],
            "adopt_lines": [manual_clip.adopt_line(s.id, pipe.clip_path(s)) for s in wp.adopt],
            "unverified_clip": wp.unverified_clip,
            "new_keyframes": kf_new,
            "done": [s.id for s in sb.scenes if pipe.is_done(s)],
            "retired": wp.retired,
            "bad_duration": wp.bad_duration,
            "blocked_bad_duration": wp.blocked_bad_duration,
        }

    @app.post("/api/projects/{pid}/scenes/{sid}/adopt-clip")
    def adopt_clip(pid: str, sid: str) -> dict[str, Any]:
        """“Dùng clip này”: take clips/<sid>.mp4 for a scene interrupted by an older version. No job, nothing
        sent to Flow (dry gflow); the state is written all the same (`adopt_legacy_clip`)."""
        pdir(pid)
        try:
            sb = load_storyboard(sb_path(pid))
        except (ValidationError, ValueError) as exc:
            raise HTTPException(400, "Storyboard còn lỗi: " + "; ".join(
                _validation_errors(exc) if isinstance(exc, ValidationError) else [str(exc)]))
        s = next((x for x in sb.scenes if x.id == sid), None)
        if s is None:
            raise HTTPException(404, f"Không có cảnh: {sid}")
        # Probe once outside jobs.lock (cached per path/size/mtime): ffprobe may take up to PROBE_TIMEOUT,
        # and jobs.lock blocks every job start. Inside, adopt_legacy_clip checks again (a cache hit if unchanged).
        manual_clip.check_file(Pipeline(sb, out_dir(pid), gflow(True), log=lambda *_: None).clip_path(s))
        with jobs.lock:  # no job may start for this project while the state is written
            if jobs.current and jobs.current.status == "running" and jobs.current.project == pid:
                raise HTTPException(409, detail={"message": "Dự án đang chạy, đợi xong rồi dùng clip.",
                                                 "reason": "job_running"})
            reason, msg = Pipeline(sb, out_dir(pid), gflow(True), log=lambda *_: None).adopt_legacy_clip(s)
        if reason:
            raise HTTPException(409, detail={"message": f"[{sid}] {msg}", "reason": reason})
        return detail(pid)  # outside jobs.lock: ffprobe/retirement for the whole project need no lock

    # ---- editor agent: edit plan (edit.json) ----

    def edit_view(pid: str) -> dict[str, Any]:
        from ..editor import ClipInfo, load_plan, timeline, transition_catalog

        od = out_dir(pid)
        infos: dict[str, ClipInfo] = {}
        try:
            cache = json.loads((od / "edit_analysis.json").read_text(encoding="utf-8"))
            infos = {k: ClipInfo.model_validate(v["info"]) for k, v in cache.items()}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        plan = load_plan(od)
        total = None
        if plan and all(c.id in infos for c in plan.clips) and plan.clips:
            total = round(timeline(plan, infos)[1], 2)
        return {"plan": plan.model_dump() if plan else None, "transitions": transition_catalog(),
                "clips": {k: {"duration": v.duration, "has_audio": v.has_audio} for k, v in infos.items()},
                "total": total}

    @app.get("/api/projects/{pid}/edit-plan")
    def get_edit_plan(pid: str) -> dict[str, Any]:
        pdir(pid)
        return edit_view(pid)

    @app.put("/api/projects/{pid}/edit-plan")
    def put_edit_plan(pid: str, body: dict[str, Any]) -> dict[str, Any]:
        """Save a hand-tuned edit plan; the next “AI dựng phim” renders it as is (free)."""
        from ..editor import ClipInfo, EditPlan, sanitize, save_plan

        try:
            plan = EditPlan.model_validate({**body, "source": "manual"})
        except ValidationError as exc:
            raise HTTPException(400, "Kế hoạch dựng không hợp lệ: " + "; ".join(_validation_errors(exc)))
        od = out_dir(pid)
        try:
            cache = json.loads((od / "edit_analysis.json").read_text(encoding="utf-8"))
            infos = {k: ClipInfo.model_validate(v["info"]) for k, v in cache.items()}
        except (OSError, ValueError, KeyError, TypeError):
            infos = {}
        order = [c.id for c in plan.clips]
        if order and all(i in infos for i in order):
            plan = sanitize(plan, infos, order)
        save_plan(plan, od)
        return edit_view(pid)

    @app.delete("/api/projects/{pid}/edit-plan")
    def delete_edit_plan(pid: str) -> dict[str, Any]:
        from ..editor import plan_path

        plan_path(out_dir(pid)).unlink(missing_ok=True)
        return edit_view(pid)

    @app.post("/api/projects/{pid}/run")
    def run(pid: str, body: RunBody) -> dict[str, Any]:
        path = sb_path(pid)
        try:
            sb = load_storyboard(path)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(400, "Storyboard còn lỗi: " + "; ".join(
                _validation_errors(exc) if isinstance(exc, ValidationError) else [str(exc)]))
        check_ids(sb, body)
        if body.action not in {"dry", "keyframes", "video", "stitch", "edit", "make", "thumbnail"}:
            raise HTTPException(400, "action không hợp lệ")
        if body.engine not in {"auto", "ai", "rules"}:
            raise HTTPException(400, "action không hợp lệ")
        if body.action in ("video", "make"):
            wp = web_video_plan(Pipeline(sb, out_dir(pid), gflow(True)), body)
            retired = wp.retired
            if retired:  # before jobs.start: no job, no backup, nothing sent to Flow
                raise HTTPException(409, detail={"message": f"Không thể render: {len(retired)} cảnh dùng model đã ngừng",
                                                 "reason": "model_retired", "retired": retired})
            if wp.bad_duration:  # same: refused before anything is sent (AC8)
                ids = ", ".join(r["id"] for r in wp.bad_duration)
                raise HTTPException(409, detail={
                    "message": f"Không thể render: {len(wp.bad_duration)} cảnh có độ dài model không nhận ({ids})",
                    "reason": "bad_duration", "bad_duration": wp.bad_duration})
            if wp.needs_confirm:  # a forced interrupted scene the user did not tick: may cost credits twice
                ids = ", ".join(wp.needs_confirm)
                raise HTTPException(428, detail={
                    "message": f"Cảnh {ids} bị ngắt khi render lần trước, có thể đã trừ credit. Kiểm tra trên Flow "
                               "rồi tick ô xác nhận trong hộp Render để gửi lại.",
                    "reason": "needs_confirm", "scenes": wp.needs_confirm})
        settings = load_settings()
        music = None
        if body.music:
            music = (pdir(pid) / body.music).resolve()
            if pdir(pid).resolve() not in music.parents or not music.is_file():
                raise HTTPException(400, "Không thấy file nhạc.")
        titles = {"dry": "Xem trước (không tốn credit)", "keyframes": "Tạo ảnh keyframe",
                  "video": "Render video", "stitch": "Ghép phim", "edit": "AI dựng phim", "make": "Render + ghép phim",
                  "thumbnail": "Tạo thumbnail"}
        scope = f" ({', '.join(body.only)})" if body.only else ""

        def do_run(job: Job, *, dry: bool, keyframes_only: bool) -> dict[str, Any]:
            g = gflow(dry_run=dry, log=job.log)
            if not dry and not g.available():
                raise RuntimeError("không thấy lệnh gflow")
            if dry:
                job.log("== XEM TRƯỚC: không gửi gì lên Flow ==")
            pipe = Pipeline(sb, out_dir(pid), g, log=job.log, sleep=job.sleep, progress=None if dry else job.set_progress)
            retired = retirement.refuse_retired(sb) if not dry else (lambda _s: None)
            bad = dur.refuse_bad_duration(sb) if not dry else (lambda _s: None)
            report = pipe.run(RunOptions(
                only=set(body.only) or None, force=set(body.force), retry_unknown=False,
                confirm=set(body.confirm_resend),  # web: an interrupted scene is only resent when ticked
                max_retries=int(settings.get("retries", 2)),
                pause=(float(settings.get("pause_min", 8)), float(settings.get("pause_max", 20))),
                fail_fast=body.fail_fast, keyframes_only=keyframes_only,
                force_keyframes=set(body.force_keyframes),
                should_stop=lambda: job.stop_requested,
                after_scene=(lambda _sid: ab.trigger()) if ab else (lambda _sid: None),
                refuse=lambda s: retired(s) or bad(s),  # retired model first, then the duration
            ))
            job.log("")
            if dry:
                job.log(f"XEM TRƯỚC: {len(report.done)} cảnh sẽ được tạo, {len(report.skipped)} cảnh đã có.")
            else:
                job.log(f"Xong: {len(report.done)} | Bỏ qua: {len(report.skipped)} | Lỗi: {len(report.failed)}")
            for sid, err in report.failed.items():
                job.log(f"  ✘ {sid}: {err}")
            if report.aborted:
                job.log(f"⚠ {report.aborted}")
            res: dict[str, Any] = {"done": report.done, "skipped": report.skipped, "failed": report.failed}
            if report.aborted or report.failed:
                res["error"] = report.aborted or f"{len(report.failed)} cảnh lỗi"
            return res

        def ready_clips(job: Job) -> list[tuple[str, Path]]:
            state = State(out_dir(pid) / "state.json")
            pipe = Pipeline(sb, out_dir(pid), gflow(True))
            clips, missing = [], []
            for s in sb.scenes:
                v = Path(state.scene(s.id).get("video", ""))
                if pipe.is_done(s) or (body.allow_missing and state.scene(s.id).get("status") == DONE and v.is_file()):
                    clips.append((s.id, v))
                else:
                    missing.append(s.id)
            if missing and not body.allow_missing:
                raise RuntimeError(f"còn thiếu cảnh: {', '.join(missing)} (bật “Ghép cả khi thiếu cảnh” để ghép phần đã có)")
            if missing:
                job.log(f"Bỏ qua cảnh chưa có: {', '.join(missing)}")
            return clips

        def fresh_sb(job: Job) -> Storyboard:
            """Translate missing subtitles first (best effort), then the storyboard as saved."""
            def edit(fn: Callable[[dict], bool]) -> bool:
                with sb_lock(pid):
                    raw = read_raw(pid)
                    if not fn(raw):
                        return False
                    write_raw(pid, raw)
                    return True

            cur = load_storyboard(sb_path(pid))
            if subtitles.ensure(cur, edit, backend=planner_backend(),
                                api_key=settings.get("anthropic_api_key") or None, log=job.log):
                cur = load_storyboard(sb_path(pid))
            return cur

        def do_edit(job: Job) -> dict[str, Any]:
            from ..editor import edit_film

            clips = ready_clips(job)
            sb = fresh_sb(job)
            backend = planner_backend()
            output, plan = edit_film(
                sb, clips, out_dir(pid), music=music, music_volume=body.volume, burn_subtitles=body.burn_subtitles,
                burn_lang=body.burn_lang,
                engine=body.engine, replan=body.replan, backend=backend or "auto", ai_available=backend is not None,
                api_key=settings.get("anthropic_api_key") or None, log=job.log)
            job.log(f"🎬 Phim đã dựng: {output}")
            return {"final": str(output), "edit_source": plan.source}

        def do_stitch(job: Job) -> dict[str, Any]:
            clips = ready_clips(job)
            output = out_dir(pid) / "final.mp4"
            assemble(fresh_sb(job), clips, output, music=music, music_volume=body.volume,
                     burn_subtitles=body.burn_subtitles, burn_lang=body.burn_lang, log=job.log)
            job.log(f"🎬 Phim hoàn chỉnh: {output}")
            return {"final": str(output)}

        def do_thumbnail(job: Job, remake: bool) -> dict[str, Any]:
            res: dict[str, Any] = {}
            if not (sb.thumbnail.text or "").strip():  # no title yet: let Claude write one (editable later)
                try:
                    titles = saved_titles(pid) or suggest_titles_for(pid, sb, job.log)
                    title = titles[0]["text"]
                    sb.thumbnail.text = title
                    with sb_lock(pid):  # read-modify-write: a model change must not land in between
                        raw = read_raw(pid)
                        if not ((raw.get("thumbnail") or {}).get("text") or "").strip():
                            raw.setdefault("thumbnail", {})
                            if not isinstance(raw["thumbnail"], dict):
                                raw["thumbnail"] = {}
                            raw["thumbnail"]["text"] = title
                            write_raw(pid, raw)
                    vi = f" ({titles[0]['vi']})" if titles[0]["vi"] else ""
                    job.log(f"Tiêu đề (Claude gợi ý, sửa được): {title}{vi}")
                    res["title"] = title
                except Exception as exc:  # no Claude: fall back to the film title
                    job.log(f"  (không gợi ý được tiêu đề: {exc}; dùng tên phim)")
            g = gflow(log=job.log)
            pipe = Pipeline(sb, out_dir(pid), g, log=job.log, sleep=job.sleep)
            if (remake or not pipe.has_thumbnail_image()) and not g.available():
                raise RuntimeError("không thấy lệnh gflow")
            path = pipe.make_thumbnail(RunOptions(max_retries=int(settings.get("retries", 2))), remake=remake)
            res["thumbnail"] = str(path)
            return res

        ab: AutoBackup | None = None

        def work(job: Job) -> dict[str, Any]:
            nonlocal ab
            if body.action != "dry":
                ab = auto_backup(pid, job)
            try:
                return steps(job)
            finally:
                if ab:
                    ab.finish()  # whatever was made (clips, final.mp4, cover) goes to Drive

        def steps(job: Job) -> dict[str, Any]:
            if body.action == "thumbnail":
                return do_thumbnail(job, body.remake_thumbnail)
            if body.action == "dry":
                return do_run(job, dry=True, keyframes_only=False)
            if body.action == "keyframes":
                return do_run(job, dry=False, keyframes_only=True)
            if body.action == "stitch":
                return do_stitch(job)
            if body.action == "edit":
                return do_edit(job)
            res = do_run(job, dry=False, keyframes_only=False)
            if res.get("done") and not job.stop_requested and settings.get("auto_qc", True):
                run_qc(job, pid, set(res["done"]), force=False)
            if body.action == "make" and not job.stop_requested:
                if res.get("error") and not body.allow_missing:
                    job.log("Có cảnh lỗi nên chưa ghép phim.")
                    return res
                res.update(do_edit(job) if body.smart_edit else do_stitch(job))
                if not (out_dir(pid) / "thumbnail.jpg").is_file():
                    try:
                        res.update(do_thumbnail(job, False))
                    except Exception as exc:  # the film is done; a missing cover is not a failure
                        job.log(f"⚠ Chưa làm được thumbnail: {exc}")
            return res

        return jobs.start(body.action, titles[body.action] + scope, pid, work).public()

    # ---- AI QC of rendered clips ----

    def run_qc(job: Job, pid: str, only: set[str] | None, force: bool) -> dict[str, Any]:
        backend = planner_backend()
        if not backend:
            job.log("⚠ Bỏ qua AI QC: chưa dùng được Claude (cài Claude Code và đăng nhập, hoặc thêm API key).")
            return {}
        sb = load_storyboard(sb_path(pid))
        pipe = Pipeline(sb, out_dir(pid), gflow(True))
        job.log("")
        job.log("=== AI QC: xem hình, nghe âm thanh và lời thoại từng clip (miễn phí) ===")
        done = qc.run(pipe, only=only, force=force, log=job.log, stop=lambda: job.stop_requested,
                      backend=backend, api_key=load_settings().get("anthropic_api_key") or None)
        failed = sorted(k for k, v in done.items() if v["verdict"] == "FAIL")
        if failed:
            job.log(f"⚠ AI QC đánh FAIL: {', '.join(failed)} — xem lý do ở từng cảnh; “Render lại” nếu muốn (tốn credit).")
        return {"qc": {k: v["verdict"] for k, v in done.items()}, "qc_failed": failed}

    @app.post("/api/projects/{pid}/qc")
    def qc_project(pid: str, body: QCBody) -> dict[str, Any]:
        """AI QC of the rendered clips (all new ones, or `only`; `force` = check again). Free on Flow."""
        try:
            load_storyboard(sb_path(pid))
        except (ValidationError, ValueError) as exc:
            raise HTTPException(400, "Storyboard còn lỗi: " + "; ".join(
                _validation_errors(exc) if isinstance(exc, ValidationError) else [str(exc)]))
        if not planner_backend():
            raise HTTPException(400, "Chưa dùng được Claude: cài Claude Code và đăng nhập, hoặc thêm Anthropic API key trong Cài đặt.")
        scope = f" ({', '.join(body.only)})" if body.only else ""
        return jobs.start("qc", "AI QC clip" + scope, pid,
                          lambda job: run_qc(job, pid, set(body.only) or None, body.force)).public()

    @app.post("/api/qc/model")
    def qc_model() -> dict[str, Any]:
        """Download the speech-to-text model QC uses to check dialogue (once, about 550 MB)."""
        if not qc.whisper_binary():
            raise HTTPException(400, "Chưa có whisper-cli: chạy `brew install whisper-cpp` trong Terminal.")

        def work(job: Job) -> dict[str, Any]:
            path = qc.download_model(log=job.log)
            job.log(f"✔ Đã có model: {path}")
            return {"model": str(path)}

        return jobs.start("qc_model", "Tải model nhận dạng giọng nói", None, work).public()

    # ---- autopilot: one button from the idea to the finished film ----

    def auto_work(job: Job, pid: str, body: AutoBody) -> dict[str, Any]:
        from ..autopilot import AutoError, Autopilot, AutoSettings

        settings = load_settings()
        g = gflow(log=job.log)
        if not g.available():
            raise RuntimeError("không thấy lệnh gflow")
        music = None
        if body.music:
            music = (pdir(pid) / body.music).resolve()
            if pdir(pid).resolve() not in music.parents or not music.is_file():
                music = None

        def edit_raw(fn: Callable[[dict], bool]) -> bool:
            with sb_lock(pid):
                raw = read_raw(pid)
                if not fn(raw):
                    return False
                write_raw(pid, raw)
                return True

        def on_stage(info: dict) -> None:
            job.stage = {**job.stage, **info}

        pilot = Autopilot(sb_path(pid), out_dir(pid), g, settings=AutoSettings(
            review=body.review, burn_subtitles=body.burn_subtitles, burn_lang=body.burn_lang,
            rerender_budget=max(0, min(20, body.rerender_budget)), music=music,
            ai_backend=settings.get("planner") or "auto", api_key=settings.get("anthropic_api_key") or None,
            max_retries=int(settings.get("retries", 2)),
            pause=(float(settings.get("pause_min", 8)), float(settings.get("pause_max", 20)))),
            log=job.log, sleep=job.sleep, progress=job.set_progress, should_stop=lambda: job.stop_requested,
            edit_raw=edit_raw, on_stage=on_stage)
        ab = auto_backup(pid, job)
        try:
            res = pilot.run()
        except AutoError as exc:
            job.log(f"✘ {exc}")
            return {"error": str(exc), "project": pid}
        finally:
            if ab:
                ab.finish()
        return {**res, "project": pid}

    @app.post("/api/projects/autopilot")
    def autopilot_new(body: AutoPlanBody) -> dict[str, Any]:
        """“Tạo tự động toàn bộ” in the new-film popup: plan the storyboard, then run every step."""
        if not body.idea.strip():
            raise HTTPException(400, "Hãy nhập ý tưởng.")
        langs = sub_langs_or_400(body)
        language = langs[0]
        dialogue = (_lang_or_400(body.dialogue_language, "Ngôn ngữ thoại", dialogue=True)
                    if body.dialogue_language is not None else None)
        backend = planner_backend()
        if not backend:
            raise HTTPException(400, "Chưa dùng được Claude: cài Claude Code và đăng nhập, hoặc thêm Anthropic API key trong Cài đặt.")
        key = load_settings().get("anthropic_api_key") or None

        def work(job: Job) -> dict[str, Any]:
            from ..planner import plan_auto

            job.stage = {"key": "storyboard", "label": "Viết storyboard", "status": "running", "index": 1, "total": 10}
            job.log(f"=== Bước 1/10: Viết storyboard ({body.scenes} cảnh) ===")
            job.log(f"Ý tưởng: {body.idea[:300]}")
            sb, _ = plan_auto(body.idea, backend=backend, api_key=key, scenes=max(1, min(20, body.scenes)),
                              aspect=body.aspect, language_hint=language, dialogue_language=dialogue)
            add_subtitle_langs(sb, langs, backend, key, job.log)
            pid, d = new_project_dir(sb.title)
            save_storyboard(sb, d / "storyboard.yaml")
            remember_idea(d, body.idea)
            job.project = pid  # the UI opens the project while the rest runs
            job.title = f"Tạo tự động: {sb.title}"
            job.log(f"✔ “{sb.title}” — {len(sb.scenes)} cảnh, {len(sb.characters)} nhân vật.")
            return auto_work(job, pid, body)

        return jobs.start("auto", "Tạo tự động toàn bộ", None, work).public()

    @app.post("/api/projects/{pid}/autopilot")
    def autopilot_resume(pid: str, body: AutoBody) -> dict[str, Any]:
        """“Tạo tự động” on an existing project: every step still missing, reviews included."""
        try:
            sb = load_storyboard(sb_path(pid))
        except (ValidationError, ValueError) as exc:
            raise HTTPException(400, "Storyboard còn lỗi: " + "; ".join(
                _validation_errors(exc) if isinstance(exc, ValidationError) else [str(exc)]))
        wp = web_video_plan(Pipeline(sb, out_dir(pid), gflow(True)), RunBody(action="video"))
        if wp.retired:
            raise HTTPException(409, detail={"message": f"Không thể render: {len(wp.retired)} cảnh dùng model đã ngừng",
                                             "reason": "model_retired", "retired": wp.retired})
        return jobs.start("auto", f"Tạo tự động: {sb.title}", pid, lambda job: auto_work(job, pid, body)).public()

    # ---- backup (Google Drive via rclone) ----

    def rclone(log: Callable[[str], None] = lambda *_: None) -> Rclone:
        return Rclone(load_settings().get("backup_remote") or DEFAULT_BACKUP_REMOTE, log=log)

    def auto_backup(pid: str, job: Job) -> AutoBackup | None:
        if not load_settings().get("auto_backup"):
            return None
        r = rclone(job.log)
        if not r.available():
            job.log("⚠ Tự backup đang bật nhưng chưa cài rclone (brew install rclone) — bỏ qua backup.")
            return None
        return AutoBackup(lambda: r.backup(sb_path(pid), out_dir(pid)), log=job.log)

    @app.get("/api/backup")
    def backup_status() -> dict[str, Any]:
        r = rclone()
        out: dict[str, Any] = {"remote": r.remote, "auto": bool(load_settings().get("auto_backup")),
                               "available": r.available(), "version": None, "remotes": [], "configured": False}
        if out["available"]:
            out["version"] = r.version()
            try:
                out["remotes"] = r.remotes()
            except BackupError as exc:
                out["error"] = str(exc)
            name = r.remote.split(":", 1)[0] + ":" if ":" in r.remote else None
            out["configured"] = name is None or name in out["remotes"]  # a plain local folder needs no remote
        return out

    @app.get("/api/backups")
    def backups() -> dict[str, Any]:
        r = rclone()
        try:
            names = r.list_backups()
        except BackupError as exc:
            raise HTTPException(400, str(exc))
        local = {d.name for d in workspace.iterdir() if d.is_dir()} if workspace.is_dir() else set()
        return {"remote": r.remote, "items": [{"name": n, "local": n in local} for n in names]}

    @app.post("/api/projects/{pid}/backup")
    def backup_project(pid: str) -> dict[str, Any]:
        sb_path(pid)  # 404 for unknown projects

        def work(job: Job) -> dict[str, Any]:
            return {"backup": rclone(job.log).backup(sb_path(pid), out_dir(pid))}

        return jobs.start("backup", "Backup lên Drive", pid, work).public()

    @app.post("/api/backups/restore")
    def restore_backup(body: RestoreBody) -> dict[str, Any]:
        try:
            name = check_name(body.name)
        except BackupError as exc:
            raise HTTPException(400, str(exc))
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            raise HTTPException(400, "Bản backup này không phải dự án của giao diện; khôi phục bằng lệnh "
                                     f"`filmmaker restore {name}`.")
        if (workspace / name).exists():
            raise HTTPException(409, detail={
                "message": f"Đã có dự án “{name}” trên máy. Xoá hoặc đổi tên dự án đó trước khi khôi phục.",
                "reason": "project_exists"})

        def work(job: Job) -> dict[str, Any]:
            r = rclone(job.log)
            m = r.manifest(name)
            if m.get("storyboard") != "storyboard.yaml":
                raise BackupError(f"bản backup {name} được tạo từ CLI; khôi phục bằng lệnh `filmmaker restore {name}`")
            target = workspace / name
            try:
                r.restore(name, target)
            except BaseException:
                if target.exists() and not (target / "storyboard.yaml").is_file():
                    shutil.rmtree(target, ignore_errors=True)  # do not leave half a project behind
                raise
            return {"id": name}

        return jobs.start("restore", f"Khôi phục {name} từ Drive", name, work).public()

    # ---- jobs ----

    @app.get("/api/jobs/current")
    def current_job() -> dict[str, Any] | None:
        return jobs.current.public() if jobs.current else None

    @app.get("/api/jobs/{jid}")
    def job(jid: str, since: int = 0) -> dict[str, Any]:
        j = jobs.jobs.get(jid)
        if not j:
            raise HTTPException(404)
        return j.public(since)

    @app.post("/api/jobs/{jid}/stop")
    def stop(jid: str) -> dict[str, Any]:
        j = jobs.jobs.get(jid)
        if not j:
            raise HTTPException(404)
        if j.status == "running":
            j.stop_requested = True
            j.log("⏸ Đã yêu cầu dừng. Nếu đang render một cảnh, sẽ dừng ngay sau khi cảnh đó xong (để không mất credit).")
        return j.public(len(j.lines))

    return app


def serve(workspace: Path, *, port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn

    app = create_app(workspace, port=port)
    url = f"http://127.0.0.1:{port}/"
    print(f"Film Maker đang chạy: {url}\nThư mục dự án: {workspace.expanduser()}\nNhấn Ctrl+C để tắt.")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
