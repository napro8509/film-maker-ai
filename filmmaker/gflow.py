"""Thin wrapper around the `gflow` command (gflow-cli).

Every call runs `gflow ... --json` and parses the JSON document gflow prints on
stdout. Errors come back as {"status": "fail", "error": {exit_code, retryable, ...}}.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import random
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import progress as prog

# gflow exit codes worth knowing about (see gflow_cli/errors.py).
EXIT_AUTH_MISSING = 8
EXIT_TIMEOUT = 9
EXIT_WAF = 10
EXIT_BROWSER_CLOSED = 15
EXIT_UI_DRIFT = 23
EXIT_NO_CREDITS = 37
EXIT_NO_FLOW_ACCESS = 39
EXIT_NOT_PORTED = 36  # request type not supported on this account's Flow frontend (checked before submit)

# Errors where carrying on with the next scene is pointless.
FATAL_EXIT_CODES = {EXIT_AUTH_MISSING, EXIT_UI_DRIFT, EXIT_NO_CREDITS, EXIT_NO_FLOW_ACCESS, 12, 14, 36, 38}


class GflowError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int, retryable: bool = False, payload: dict | None = None):
        super().__init__(message)
        self.exit_code = exit_code
        self.retryable = retryable
        self.payload = payload or {}

    @property
    def fatal(self) -> bool:
        return self.exit_code in FATAL_EXIT_CODES

    @property
    def spent(self) -> bool:
        """The request may already have been billed: the Flow chat driver says so in the payload,
        gflow-cli says so in its hint (e.g. a poll timeout after the submit click)."""
        if ((self.payload or {}).get("error") or {}).get("spent"):
            return True
        text = str(self).lower()
        return "already clicked" in text or "billed" in text


def _extract_json(stdout: str) -> dict[str, Any] | None:
    """Return the last top-level JSON object printed on stdout (ignores banners/log lines)."""
    decoder = json.JSONDecoder()
    found = None
    i = 0
    while True:
        start = stdout.find("{", i)
        if start == -1:
            return found
        try:
            obj, end = decoder.raw_decode(stdout, start)
        except json.JSONDecodeError:
            i = start + 1
            continue
        if isinstance(obj, dict):
            found = obj
        i = end


def _split(cmd: str) -> list[str]:
    # Windows paths contain backslashes, which POSIX shell splitting would eat.
    return shlex.split(cmd, posix=os.name != "nt")


def _stream(args: list[str], timeout: float, on_line: Callable[[str], None],
            env: dict[str, str] | None = None) -> tuple[int, str, bool]:
    """Run a command, feeding each stderr line (rstripped) to `on_line` as it comes; return
    (returncode, stdout, timed_out).

    The child is killed after `timeout` seconds, and also on any exception in this thread (Ctrl+C included),
    like `subprocess.run` does, so no gflow render is left running orphaned. For the Flow chat driver this is
    new: before, an exception here left the driver running (Ctrl+C after Approve could still download the clip);
    now it is killed. SIGINT from a terminal reaches the driver's process group anyway.
    Exceptions raised by `on_line` are swallowed. The stderr pump is joined with a timeout (a grandchild such as
    a browser may keep the pipe open), so late lines can still reach `on_line` after this returns."""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    timed_out = threading.Event()

    def pump() -> None:
        try:
            for line in proc.stderr:
                try:
                    on_line(line.rstrip())
                except Exception:
                    pass
        except (OSError, ValueError):
            pass

    def kill() -> None:
        timed_out.set()
        try:
            proc.kill()
        except OSError:
            pass

    t = threading.Thread(target=pump, daemon=True)
    timer = threading.Timer(timeout, kill)
    timer.daemon = True
    try:
        t.start()
        timer.start()
        stdout = proc.stdout.read()
        proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        t.join(timeout=5)
        try:
            proc.stdout.close()
        except OSError:
            pass
    return proc.returncode, stdout or "", timed_out.is_set()


UI_SCRIPT = Path(__file__).with_name("flow_ui.py")
MUSE_SCRIPT = Path(__file__).with_name("muse_ui.py")

#: Video "models" that are not Flow: rendered by driving another site's chat page (see muse_ui.py).
#: Every video method below sends these there instead of to gflow, so the pipeline and the clone flow
#: need no change of their own.
MUSE_MODEL = "muse"


def is_muse(model: str | None) -> bool:
    return (model or "").strip().lower() == MUSE_MODEL


#: The Flow image model a picture falls back to when Muse could not make it.
FLOW_FALLBACK_IMAGE_MODEL = "nano2"


def other_image_model(model: str | None) -> str:
    """The other picture service: a picture Muse could not make is made on Flow, and the other way round."""
    return FLOW_FALLBACK_IMAGE_MODEL if is_muse(model) else MUSE_MODEL


#: muse_ui.py exit codes: Muse wrote back instead of making it / its own "Failed to create" task row.
MUSE_DECLINED, MUSE_FAILED = 41, 42

MUSE_BLOCK_HINT = ("Nếu cảnh này lỗi lặp lại: Muse hay chặn keyframe có nước mắt, mặt đau buồn hay chủ đề cái chết "
                   "— làm keyframe bình tĩnh hơn (sửa image_prompt rồi `run --keyframes-only --force <id>`), hoặc "
                   "đặt `model: veo-lite` cho riêng cảnh này (render trên Flow, tốn credit).")


def muse_fail_dir(out: Path) -> Path:
    """Screenshots of a failed Muse request for clip `out` (…/clips/s04.mp4 -> …/muse_failed/s04/)."""
    return out.parent.parent / "muse_failed" / out.stem


def muse_error(exc: "GflowError", shots: Path) -> "GflowError":
    """A Muse failure with what to do next. Muse's own "Failed to create" (nothing billed, often gone on the
    next try when requests come one after another) is retryable; a refusal in words is not."""
    if exc.exit_code not in (MUSE_DECLINED, MUSE_FAILED):
        return exc
    msg = f"{exc} {MUSE_BLOCK_HINT} Ảnh chụp lúc lỗi: {shots}"
    return GflowError(msg, exit_code=exc.exit_code, payload=exc.payload,
                      retryable=exc.exit_code == MUSE_FAILED and not exc.spent)

# Names the Flow chat agent understands for gflow's model aliases.
MODEL_NAMES = {
    "veo-fast": "Veo 3.1 Fast", "veo-quality": "Veo 3.1 Quality", "veo-lite": "Veo 3.1 Lite",
    "veo-lite-lp": "Veo 3.1 Lite (lower priority)", "omni-flash": "Omni Flash", "omni-1.2-flash": "Omni 1.2 Flash",
}

PROMPT_LOG_MAX = 300


def short_prompt(text: str, limit: int = PROMPT_LOG_MAX) -> str:
    """The prompt on one line (whitespace and newlines folded), cut to `limit` chars with "…"."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def ui_log_line(head: list[str], sub: str, opts: list[str], prompt: str) -> str:
    """The `  $ …` line shown for a flow_ui call: shell-quoted, one line, no --profile-dir/--project/-o/--timeout."""
    return "  $ " + shlex.join([*head, sub, *opts, "--prompt", short_prompt(prompt)])


def _gflow_python(binary: str) -> str | None:
    """The Python inside gflow-cli's own environment (it has Playwright and the browsers)."""
    exe = shutil.which(_split(binary)[0])
    if not exe:
        return None
    real = Path(exe).resolve()
    if real.suffix == ".py" or "python" in real.name:  # GFLOW_BIN="python fake.py" in tests
        return None
    try:
        first = real.read_bytes()[:400].split(b"\n", 1)[0].decode(errors="ignore")
    except OSError:
        first = ""
    if first.startswith("#!"):
        cand = first[2:].strip().split()[0] if first[2:].strip() else ""
        if cand and Path(cand).is_file():
            return cand
    for name in ("python", "python3", "python.exe"):
        p = real.parent / name
        if p.is_file():
            return str(p)
    return None


def _tcc_direct(args: list[str]) -> tuple[list[str], dict[str, str] | None]:
    """On macOS, run a framework Python's inner Python.app binary directly instead of its bin/ stub.

    The stub (python3.12 of python.org/Homebrew builds) relaunches Python.app and disclaims
    responsibility, so macOS privacy (TCC) treats it as a separate app: it cannot use Film Maker's
    access to ~/Desktop, ~/Documents… and gets "Operation not permitted" with no prompt. Launched
    directly, the process stays Film Maker's: access is asked for (and granted) in Film Maker's name.
    __PYVENV_LAUNCHER__ is what the stub sets so the venv (gflow-cli's, with Playwright) is kept."""
    if sys.platform != "darwin" or not args:
        return args, None
    try:
        real = Path(args[0]).resolve()
    except OSError:
        return args, None
    if real.parent.name != "bin" or real.parent.parent.parent.name != "Versions":
        return args, None
    inner = real.parent.parent / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    if not inner.is_file():
        return args, None
    return [str(inner), *args[1:]], {**os.environ, "__PYVENV_LAUNCHER__": args[0]}


def _profile_roots() -> list[Path]:
    home = Path.home()
    roots = [home / "Library" / "Application Support" / "gflow-cli", home / ".local" / "share" / "gflow-cli"]
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "gflow-cli")
    return roots


#: Stdout prefix of each answer of `muse_ui.py serve` (kept in sync with muse_ui.SERVE_RESULT: that script runs
#: with gflow-cli's Python and cannot be imported here).
MUSE_SERVE_RESULT = "@@result "
#: Seconds between the STARTS of two Muse video requests (random in this range). A Thai film had 5 of 10
#: back-to-back requests fail ("Failed to create"); spaced out they went through. Counted from the previous send, a
#: clip that takes ~80 s to make already covers it, so the run no longer sleeps ~55 s after every clip.
MUSE_VIDEO_GAP = (45.0, 75.0)
#: After a failed Muse video the next one waits longer: 60 s, then 120 s, at most 180 s; a success resets it.
MUSE_BACKOFF = (60.0, 180.0)
#: An idle Muse browser is closed after this many seconds, so the profile is free for login / fetch / other runs.
MUSE_SESSION_IDLE = 120.0


class MuseSession:
    """One `muse_ui.py serve` process: a single signed-in browser kept open across Muse requests. Starting Chrome
    for every picture cost several seconds each, and a film asks Muse for 30-40 pictures. Requests are serialized.
    Any trouble starting or talking to it makes `request` return None, and the caller runs the old one-shot way."""

    def __init__(self, cmd: list[str], extra: list[str], *, idle: float = MUSE_SESSION_IDLE, start_timeout: float = 120):
        self.cmd, self.extra, self.idle, self.start_timeout = cmd, extra, idle, start_timeout
        self.proc: subprocess.Popen | None = None
        self._results: "queue.Queue[str | None]" = queue.Queue()
        self._on_line: Callable[[str], None] = lambda _l: None
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self.broken = False

    def _alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _start(self) -> bool:
        run_args, env = _tcc_direct([*self.cmd, "serve", *self.extra])
        try:
            self.proc = subprocess.Popen(run_args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
        except OSError:
            self.broken = True
            return False
        self._results = queue.Queue()
        proc, results = self.proc, self._results

        def out_pump() -> None:
            try:
                for line in proc.stdout:
                    if line.startswith(MUSE_SERVE_RESULT):
                        results.put(line[len(MUSE_SERVE_RESULT):].strip())
            except (OSError, ValueError):
                pass
            results.put(None)  # the process ended

        def err_pump() -> None:
            try:
                for line in proc.stderr:
                    try:
                        self._on_line(line.rstrip())
                    except Exception:
                        pass
            except (OSError, ValueError):
                pass

        threading.Thread(target=out_pump, daemon=True).start()
        threading.Thread(target=err_pump, daemon=True).start()
        try:
            ready = results.get(timeout=self.start_timeout)
        except queue.Empty:
            ready = None
        if not ready or (json.loads(ready).get("status") != "ready" if ready.startswith("{") else True):
            self.close()
            self.broken = True
            return False
        return True

    def request(self, argv: list[str], timeout: float, on_line: Callable[[str], None]) -> dict | None:
        """The driver's JSON answer, None when the session cannot be used (caller falls back), or raises
        TimeoutError after `timeout` seconds (the browser is closed then, like a one-shot run is killed)."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            if self.broken or (not self._alive() and not self._start()):
                return None
            self._on_line = on_line
            try:
                self.proc.stdin.write(json.dumps({"argv": argv}, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError):
                self.close()
                return None
            try:
                line = self._results.get(timeout=timeout)
            except queue.Empty:
                self.close()
                raise TimeoutError
            finally:
                self._on_line = lambda _l: None
            if line is None:  # the process died mid-request
                self.close()
                raise ConnectionError("trình duyệt Muse đã đóng giữa chừng")
            self._timer = threading.Timer(self.idle, self.close)
            self._timer.daemon = True
            self._timer.start()
            try:
                return json.loads(line)
            except ValueError:
                return {"status": "fail", "error": {"exit_code": 23, "detail": line[:400]}}

    def close(self) -> None:
        proc, self.proc = self.proc, None
        if self._timer is not None:
            self._timer.cancel()
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.stdin.write(json.dumps({"quit": True}) + "\n")
            proc.stdin.flush()
            proc.wait(timeout=10)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass


@dataclass
class Gflow:
    binary: str = field(default_factory=lambda: os.environ.get("GFLOW_BIN", "gflow"))
    profile: str | None = None
    timeout: int = 60 * 30  # a single Veo render can take several minutes
    dry_run: bool = False
    log: Any = print
    ui_enabled: bool = True  # image-to-video through the Flow chat page when gflow refuses it
    ui_max_credits: int = 0  # reject Flow's offer above this (0 = no limit)
    ui_window: str = "hidden"  # show | hidden | headless (browser window of the Flow chat driver)
    # Character pictures are made as Flow characters (left panel Characters) when the Flow page driver works,
    # and keyframes then attach those characters by name instead of uploading their pictures.
    flow_characters: bool = True
    _ui_profile: Any = field(default=None, init=False, repr=False)
    # Progress hook (web UI): on_event("start" | "downloading" | "percent", data) for the running command.
    on_event: Callable[[str, dict], None] | None = field(default=None, repr=False)
    _timings: prog.Timings | None = field(default=None, init=False, repr=False)
    _live: object | None = field(default=None, init=False, repr=False)  # token of the command now running
    _elock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _muse_session: MuseSession | None = field(default=None, init=False, repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    _muse_sent_at: float | None = field(default=None, init=False, repr=False)  # monotonic start of the last video
    _muse_backoff: float = field(default=0.0, init=False, repr=False)

    def _muse_space_out(self) -> None:
        """Wait so this Muse video starts at least MUSE_VIDEO_GAP (or the failure backoff) after the last one."""
        if self._muse_sent_at is None:
            return
        gap = max(random.uniform(*MUSE_VIDEO_GAP), self._muse_backoff)
        wait = gap - (time.monotonic() - self._muse_sent_at)
        if wait >= 1:
            why = " (Muse vừa báo lỗi)" if self._muse_backoff else ""
            self.log(f"  (chờ {wait:.0f}s cho đủ {gap:.0f}s giữa 2 lần gửi video Muse{why})")
            self.sleep(wait)

    def _muse_video_done(self, ok: bool) -> None:
        lo, hi = MUSE_BACKOFF
        self._muse_backoff = 0.0 if ok else min(hi, max(lo, self._muse_backoff * 2))

    def available(self) -> bool:
        return shutil.which(_split(self.binary)[0]) is not None

    def _cmd(self, *args: str) -> list[str]:
        cmd = [*_split(self.binary), *args]
        if self.profile:
            cmd += ["--profile", self.profile]
        return cmd

    def _timings_obj(self) -> prog.Timings:
        if self._timings is None:
            self._timings = prog.Timings(warn=self.log)
        return self._timings

    def _emit(self, token: object, name: str, **data: Any) -> None:
        """Send a progress event, only while `token`'s command is the one running (late pump lines are dropped)."""
        with self._elock:
            if token is not self._live or not self.on_event:
                return
            try:
                self.on_event(name, data)
            except Exception:
                pass

    def _set_live(self, token: object | None) -> None:
        with self._elock:
            self._live = token

    def run_json(self, *args: str) -> dict[str, Any]:
        cmd = self._cmd(*args, "--json")
        self.log("  $ " + shlex.join(cmd))
        if self.dry_run:
            return {"status": "ok", "dry_run": True}
        # gflow logs JSON at INFO on stderr: its events tell when Flow is done and the file is downloading.
        env = {**os.environ, "GFLOW_CLI_LOG_LEVEL": "INFO", "GFLOW_CLI_LOG_FORMAT": "json"}
        kind = prog.timing_kind(args)
        token = object()
        err_lines: list[str] = []

        def on_line(line: str) -> None:
            if not line:
                return
            err_lines.append(line)
            rec = prog.parse_log_line(line)
            if rec is not None and prog.gflow_event_phase(rec) == "downloading":
                self._emit(token, "downloading")

        self._set_live(token)
        try:
            t0 = prog.now()
            if kind:
                self._emit(token, "start", kind=kind, expected=self._timings_obj().expected(kind), source="estimate")
            returncode, stdout, timed_out = _stream(cmd, self.timeout, on_line, env)
        finally:
            self._set_live(None)
        if timed_out:
            raise GflowError(f"gflow did not finish within {self.timeout}s", exit_code=EXIT_TIMEOUT)
        payload = _extract_json(stdout)
        if returncode != 0 or payload is None or payload.get("status") == "fail":
            err = (payload or {}).get("error") or {}
            stderr = "\n".join(l for l in err_lines if not prog.is_quiet_log(l))
            detail = err.get("detail") or err.get("title") or (stderr or stdout).strip()[-800:]
            hint = err.get("remediation_hint")
            if re.search(r"(?i)violate (?:our|the) polic", str(detail)):
                # Flow's agent puts the reason after the first line ("Last reply: error\n\nThis generation might
                # violate our policies"), and logs show only the first line: say it plainly, up front.
                detail = ("Flow chặn prompt này vì chính sách nội dung (thường do mô tả trẻ em: tuổi cụ thể, ảnh toàn "
                          "thân trên nền studio). Hãy đổi cách mô tả. | " + " ".join(str(detail).split())[:300])
            msg = f"gflow exited {returncode}: {detail}" + (f" (hint: {hint})" if hint else "")
            raise GflowError(
                msg,
                exit_code=int(err.get("exit_code") or returncode or 1),
                retryable=bool(err.get("retryable", False)),
                payload=payload,
            )
        if kind:
            self._timings_obj().record(kind, prog.now() - t0)
        return payload

    # --- commands ----------------------------------------------------------

    def auth_status(self) -> subprocess.CompletedProcess:
        return subprocess.run(self._cmd("auth", "status"), capture_output=True, text=True, timeout=300)

    def create_project(self, title: str) -> str:
        payload = self.run_json("project", "create", "--name", title)
        return payload.get("project_id") or "DRY-RUN-PROJECT"

    def text_to_image(self, prompt: str, *, out: Path, aspect: str, model: str, project: str | None) -> Path:
        if is_muse(model):
            return self.muse_image(prompt, [], out=out, aspect=aspect)
        args = ["image", "t2i", prompt, "--aspect", aspect, "--model", model, "--out-file", str(out)]
        if project:
            args += ["--project", project]
        payload = self.run_json(*args)
        images = payload.get("images") or []
        if images and images[0].get("local_path"):
            return Path(images[0]["local_path"])
        return out

    def catalog_images(self, project: str) -> list[dict[str, Any]]:
        """Images gflow knows about in a Flow project (generated or uploaded), from its local catalog.

        Each row has `media_id` and `local_path`. Best effort: [] if gflow can't list them."""
        if self.dry_run or not project:
            return []
        try:
            proc = subprocess.run(self._cmd("data", "list", "images", "--all-copies", "--limit", "1000", "--json"),
                                  capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            return []
        rows = []
        for line in proc.stdout.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("project_id") == project and row.get("media_id") and row.get("local_path"):
                rows.append(row)
        return rows

    def image_to_image(self, prompt: str, refs: list[Path | str], *, out: Path, aspect: str, model: str,
                       project: str | None) -> Path:
        """Text-to-image guided by reference images (keeps characters' faces the same).

        A ref is a local file (gflow uploads it into the project) or the media id of an image already in it."""
        if is_muse(model):
            return self.muse_image(prompt, refs, out=out, aspect=aspect)
        args = ["image", "i2i", prompt]
        for r in refs:
            args += ["--ref", str(r)]
        args += ["--aspect", aspect, "--model", model, "--out-file", str(out)]
        if project:
            args += ["--project", project]
        payload = self.run_json(*args)
        images = payload.get("images") or []
        if images and images[0].get("local_path"):
            return Path(images[0]["local_path"])
        return out

    def _video_opts(self, *, aspect: str, model: str | None, duration: int | None, project: str | None, out: Path) -> list[str]:
        args = ["--aspect", aspect, "--out-file", str(out)]
        if model:
            args += ["--model", model]
        if duration:
            args += ["--duration", str(duration)]
        if project:
            args += ["--project", project]
        return args

    def _video_result(self, payload: dict[str, Any], out: Path) -> tuple[Path, str | None]:
        local = payload.get("local_path")
        return (Path(local) if local else out), payload.get("media_id")

    def text_to_video(self, prompt: str, **kw: Any) -> tuple[Path, str | None]:
        if is_muse(kw.get("model")):
            return self.muse_video(None, prompt, **kw)
        payload = self.run_json("video", "t2v", prompt, *self._video_opts(**kw))
        return self._video_result(payload, kw["out"])

    def image_to_video(self, image: Path, prompt: str, *, end_image: str | None = None, **kw: Any) -> tuple[Path, str | None]:
        if is_muse(kw.get("model")):
            return self.muse_video(image, prompt, **kw)
        args = ["video", "i2v", "--initial-frame", str(image)]
        if end_image:
            args += ["--end-frame", str(end_image)]
        payload = self.run_json(*args, prompt, *self._video_opts(**kw))
        return self._video_result(payload, kw["out"])

    def reference_to_video(self, prompt: str, refs: list[Path], **kw: Any) -> tuple[Path, str | None]:
        """Text-to-video with reference images ("ingredients") for the characters."""
        if is_muse(kw.get("model")):
            return self.muse_video(None, prompt, refs=refs, **kw)
        args = ["video", "r2v", prompt]
        for r in refs:
            args += ["--ref", str(r)]
        payload = self.run_json(*args, *self._video_opts(**kw))
        return self._video_result(payload, kw["out"])

    # --- video through Meta's Muse agent (see muse_ui.py) ----------------------

    def _log_full_prompt(self, prompt: str) -> None:
        """Dry-run: the whole video prompt as it will be sent (the `$` line above is cut at PROMPT_LOG_MAX)."""
        if self.dry_run and len(" ".join(prompt.split())) > PROMPT_LOG_MAX:
            self.log("  prompt video đầy đủ: " + " ".join(prompt.split()))

    def muse_command(self) -> list[str] | None:
        if os.environ.get("MUSE_UI_CMD"):
            return _split(os.environ["MUSE_UI_CMD"])
        py = os.environ.get("FLOW_UI_PYTHON") or _gflow_python(self.binary)
        return [py, str(MUSE_SCRIPT)] if py else None

    def muse_video(self, image: Path | None, prompt: str, *, refs: list[Path | str] | None = None,
                   aspect: str, duration: int | None, out: Path, **_: Any) -> tuple[Path, None]:
        """One clip from muse.ai. `image` is the start frame (or None), `refs` character pictures; Flow
        characters given by name ("character:<name>") and Flow media ids cannot go to Muse and are left out.
        Muse has no project, no media id and no credits, so the extra keyword arguments are ignored."""
        cmd = self.muse_command()
        if not cmd:
            raise GflowError("không tìm thấy Python có Playwright (của gflow-cli) để điều khiển Muse", exit_code=2)
        files = [Path(r) for r in refs or [] if not str(r).startswith("character:") and Path(r).is_file()]
        skipped = len(refs or []) - len(files)
        args = [*cmd, "i2v", "--prompt", prompt, "--aspect", aspect, "-o", str(out),
                "--timeout", str(max(300, self.timeout - 60))]
        if image is not None:
            args += ["--image", str(image)]
        for f in files:
            args += ["--ref", str(f)]
        if duration:
            args += ["--duration", str(duration)]
        if os.environ.get("MUSE_UI_PROFILE"):
            args += ["--profile-dir", os.environ["MUSE_UI_PROFILE"]]
        shots = muse_fail_dir(out)
        args += ["--fail-shots", str(shots)]
        opts = ([*(["--image", str(image)] if image is not None else []),
                 *(a for f in files for a in ("--ref", str(f))), "--aspect", aspect])
        self.log(ui_log_line(cmd, "i2v", opts, prompt))
        self._log_full_prompt(prompt)
        if skipped:
            self.log(f"  (bỏ {skipped} nhân vật Flow/ảnh không có file: Muse chỉ nhận ảnh trên máy)")
        if self.dry_run:
            return out, None
        self._muse_space_out()
        self._muse_sent_at = time.monotonic()
        try:
            payload = self._run_muse(cmd, args, "video có thể vẫn đang tạo trên Muse (lấy lại bằng muse_ui.py fetch)",
                                     spent=True)
        except GflowError as exc:
            self._muse_video_done(False)
            raise muse_error(exc, shots) from exc
        self._muse_video_done(True)
        local = payload.get("local_path")
        return (Path(local) if local else out), None

    def muse_image(self, prompt: str, refs: list[Path | str] | None, *, out: Path, aspect: str) -> Path:
        """One picture from muse.ai (Muse Image), guided by local reference pictures (characters, places).
        Flow media ids and Flow characters by name cannot go to Muse and are left out."""
        cmd = self.muse_command()
        if not cmd:
            raise GflowError("không tìm thấy Python có Playwright (của gflow-cli) để điều khiển Muse", exit_code=2)
        files = [Path(r) for r in refs or [] if not str(r).startswith("character:") and Path(r).is_file()]
        skipped = len(refs or []) - len(files)
        args = [*cmd, "image", "--prompt", prompt, "--aspect", aspect, "-o", str(out),
                "--timeout", str(max(300, min(900, self.timeout - 60)))]
        for f in files:
            args += ["--ref", str(f)]
        if os.environ.get("MUSE_UI_PROFILE"):
            args += ["--profile-dir", os.environ["MUSE_UI_PROFILE"]]
        self.log(ui_log_line(cmd, "image", [*(a for f in files for a in ("--ref", str(f))), "--aspect", aspect],
                             prompt))
        if skipped:
            self.log(f"  (bỏ {skipped} ảnh tham chiếu không có file trên máy: Muse chỉ nhận ảnh trên máy)")
        if self.dry_run:
            return out
        payload = self._run_muse(cmd, args, "ảnh có thể vẫn đang tạo trên Muse", spent=False)
        local = payload.get("local_path")
        return Path(local) if local else out

    def _muse_session_for(self, cmd: list[str]) -> MuseSession | None:
        """The shared Muse browser (`muse_ui.py serve`), or None for the one-shot way: a test / custom driver
        (MUSE_UI_CMD), FILMMAKER_MUSE_SESSION=0, or a session that failed to start."""
        if os.environ.get("MUSE_UI_CMD") or os.environ.get("FILMMAKER_MUSE_SESSION") == "0":
            return None
        if self._muse_session is None:
            extra = ["--profile-dir", os.environ["MUSE_UI_PROFILE"]] if os.environ.get("MUSE_UI_PROFILE") else []
            self._muse_session = MuseSession(cmd, extra)
            atexit.register(self._muse_session.close)
        return None if self._muse_session.broken else self._muse_session

    def close_muse(self) -> None:
        if self._muse_session is not None:
            self._muse_session.close()

    def _run_muse(self, cmd: list[str], args: list[str], timeout_note: str, *, spent: bool) -> dict:
        """A Muse request through the shared browser when it works, else one browser for this request."""
        sess = self._muse_session_for(cmd)
        if sess is None:
            return self._run_ui(args, timeout_note, spent=spent, who="Muse")
        token = object()
        err_lines: list[str] = []

        def on_line(line: str) -> None:
            if line:
                err_lines.append(line)
                self.log(line)

        self._set_live(token)
        try:
            self._emit(token, "start", kind=None, expected=None, source=None)
            payload = sess.request(args[len(cmd):], self.timeout, on_line)
        except TimeoutError:
            raise GflowError(f"điều khiển Muse quá {self.timeout}s; {timeout_note}",
                             exit_code=EXIT_TIMEOUT, payload={"error": {"spent": spent}}) from None
        except ConnectionError as exc:
            raise GflowError(f"Muse (giao diện chat): {exc}", exit_code=23,
                             payload={"error": {"spent": spent}}) from None
        finally:
            self._set_live(None)
        if payload is None:  # the shared browser could not start: this request the old way
            self.log("  (không mở được phiên Muse dùng chung; mở trình duyệt riêng cho yêu cầu này)")
            return self._run_ui(args, timeout_note, spent=spent, who="Muse")
        if payload.get("status") != "ok":
            err = payload.get("error") or {}
            detail = err.get("detail") or "\n".join(err_lines[-5:])
            raise GflowError(f"Muse (giao diện chat): {detail}", exit_code=int(err.get("exit_code") or 1),
                             retryable=False, payload=payload)
        return payload

    # --- image-to-video through the Flow chat page (see flow_ui.py) ------------

    def ui_command(self) -> list[str] | None:
        if os.environ.get("FLOW_UI_CMD"):
            return _split(os.environ["FLOW_UI_CMD"])
        py = os.environ.get("FLOW_UI_PYTHON") or _gflow_python(self.binary)
        return [py, str(UI_SCRIPT)] if py else None

    def ui_profile_dir(self) -> Path | None:
        if self._ui_profile is not None:
            return self._ui_profile or None
        found: Path | None = None
        if os.environ.get("FLOW_UI_PROFILE"):
            found = Path(os.environ["FLOW_UI_PROFILE"]).expanduser()
        else:
            dirs = [d for r in _profile_roots() if r.is_dir() for d in sorted(r.glob("profile_*")) if d.is_dir()]
            if self.profile:
                dirs = [d for d in dirs if d.name == f"profile_{self.profile}"] or dirs
            if len(dirs) == 1:
                found = dirs[0]
            elif dirs:  # several Google accounts: ask gflow which one is active
                try:
                    text = self.auth_status().stdout
                    m = re.search(r"profile:\s*(.+?)\n\s*exists:", text, re.S)
                    if m:
                        cand = Path(re.sub(r"\s*\n\s*", "", m.group(1)).strip())
                        found = cand if cand.is_dir() else None
                except (OSError, subprocess.TimeoutExpired):
                    pass
        self._ui_profile = found or False
        return found

    def ui_available(self) -> bool:
        return bool(self.ui_enabled and self.ui_command() and (os.environ.get("FLOW_UI_CMD") or self.ui_profile_dir()))

    def ui_image_to_video(self, image: Path | None, prompt: str, *, refs: list[Path] | None = None, aspect: str,
                          model: str | None, duration: int | None, project: str | None, out: Path) -> tuple[Path, str | None]:
        """Video through the Flow chat page. `image` is the start frame for image-to-video, or None for
        reference-to-video (characters only, attached by name / picture). `refs` are the extra references."""
        if is_muse(model):
            return self.muse_video(image, prompt, refs=refs, aspect=aspect, duration=duration, out=out)
        cmd = self.ui_command()
        profile = self.ui_profile_dir()
        if not cmd or not (profile or os.environ.get("FLOW_UI_CMD")):
            raise GflowError("không tìm thấy Python/profile trình duyệt của gflow-cli để điều khiển Flow", exit_code=2)
        if not project:
            raise GflowError("cần project Flow để tạo video qua giao diện chat", exit_code=2)
        start = ["--image", str(image)] if image is not None else []
        args = [*cmd, "i2v", "--project", project, *start, "--prompt", prompt, "--aspect", aspect,
                "-o", str(out), "--profile-dir", str(profile or "none"), "--timeout", str(self.timeout - 120)]
        for r in refs or []:
            args += ["--ref", str(r)]
        if model:
            args += ["--model", MODEL_NAMES.get(model, model)]
        if duration:
            args += ["--duration", str(duration)]
        if self.ui_max_credits:
            args += ["--max-credits", str(self.ui_max_credits)]
        if self.ui_window in ("show", "hidden", "headless"):
            args += ["--window", self.ui_window]
        opts = [*start, *(a for r in refs or [] for a in ("--ref", str(r))), "--aspect", aspect]
        if model:
            opts += ["--model", MODEL_NAMES.get(model, model)]
        if duration:
            opts += ["--duration", str(duration)]
        self.log(ui_log_line(cmd, "i2v", opts, prompt))
        self._log_full_prompt(prompt)
        if self.dry_run:
            return out, None
        payload = self._run_ui(args, "video có thể vẫn đang tạo trên Flow", spent=True)
        local = payload.get("local_path")
        return (Path(local) if local else out), payload.get("media_id")

    def ui_video_to_video(self, video: Path, prompt: str, *, refs: list[str] | None = None, aspect: str,
                          model: str | None, duration: int | None, project: str | None,
                          out: Path, agree_rights: bool = False) -> tuple[Path, str | None]:
        """Edit an uploaded clip through the Flow chat page (Omni): swap the people for `refs` (picture paths
        or `character:<name>`), keep motion, expressions, camera and set. Costs credits."""
        cmd = self.ui_command()
        profile = self.ui_profile_dir()
        if not cmd or not (profile or os.environ.get("FLOW_UI_CMD")):
            raise GflowError("không tìm thấy Python/profile trình duyệt của gflow-cli để điều khiển Flow", exit_code=2)
        if not project:
            raise GflowError("cần project Flow để sửa video qua giao diện chat", exit_code=2)
        args = [*cmd, "v2v", "--project", project, "--video", str(video), "--prompt", prompt, "--aspect", aspect,
                "-o", str(out), "--profile-dir", str(profile or "none"), "--timeout", str(max(600, self.timeout - 120))]
        opts = ["--video", str(video)]
        for r in refs or []:
            args += ["--ref", str(r)]
            opts += ["--ref", str(r)]
        if model:
            args += ["--model", MODEL_NAMES.get(model, model)]
            opts += ["--model", MODEL_NAMES.get(model, model)]
        if duration:
            args += ["--duration", str(duration)]
            opts += ["--duration", str(duration)]
        if self.ui_max_credits:
            args += ["--max-credits", str(self.ui_max_credits)]
        if agree_rights:  # the user confirmed they own / may use the uploaded video
            args.append("--agree-video-rights")
        if self.ui_window in ("show", "hidden", "headless"):
            args += ["--window", self.ui_window]
        self.log(ui_log_line(cmd, "v2v", opts, prompt))
        self._log_full_prompt(prompt)
        if self.dry_run:
            return out, None
        payload = self._run_ui(args, "video có thể vẫn đang tạo trên Flow", spent=True)
        local = payload.get("local_path")
        return (Path(local) if local else out), payload.get("media_id")

    def ui_image_to_image(self, prompt: str, refs: list[Path], *, out: Path, aspect: str, model: str,
                          project: str | None) -> Path:
        """Image guided by the characters' pictures through the Flow chat page (free), for accounts where
        gflow refuses i2i (agent-only composer, gflow #799)."""
        if is_muse(model):
            return self.muse_image(prompt, refs, out=out, aspect=aspect)
        cmd = self.ui_command()
        profile = self.ui_profile_dir()
        if not cmd or not (profile or os.environ.get("FLOW_UI_CMD")):
            raise GflowError("không tìm thấy Python/profile trình duyệt của gflow-cli để điều khiển Flow", exit_code=2)
        if not project:
            raise GflowError("cần project Flow để tạo ảnh qua giao diện chat", exit_code=2)
        args = [*cmd, "i2i", "--project", project, "--prompt", prompt, "--aspect", aspect, "--model", model,
                "-o", str(out), "--profile-dir", str(profile or "none"), "--timeout", str(min(600, self.timeout))]
        for r in refs:
            args += ["--ref", str(r)]
        if self.ui_window in ("show", "hidden", "headless"):
            args += ["--window", self.ui_window]
        self.log(ui_log_line(cmd, "i2i", [*(a for r in refs for a in ("--ref", str(r))), "--aspect", aspect,
                                          "--model", model], prompt))
        if self.dry_run:
            return out
        payload = self._run_ui(args, "ảnh có thể vẫn đang tạo trên Flow", spent=False)
        local = payload.get("local_path")
        return Path(local) if local else out

    def ui_create_character(self, name: str, prompt: str, *, out: Path, project: str | None, info: str | None = None,
                            retire: tuple[str, str] | None = None) -> dict[str, Any]:
        """A Flow character (left panel Characters -> New character) made from `prompt` and named `name`; its
        picture is saved to `out`. Free (an image). `retire` = (entity id, new name): rename that older character
        first so `name` stays unique in the project. Returns {"local_path", "entity_id", "name", "media_id"}."""
        cmd = self.ui_command()
        profile = self.ui_profile_dir()
        if not cmd or not (profile or os.environ.get("FLOW_UI_CMD")):
            raise GflowError("không tìm thấy Python/profile trình duyệt của gflow-cli để điều khiển Flow", exit_code=2)
        if not project:
            raise GflowError("cần project Flow để tạo nhân vật", exit_code=2)
        args = [*cmd, "char", "--project", project, "--name", name, "--prompt", prompt, "-o", str(out),
                "--profile-dir", str(profile or "none"), "--timeout", str(min(600, self.timeout))]
        if info:
            args += ["--info", info]
        if retire:
            args += ["--retire-id", retire[0], "--retire-name", retire[1]]
        if self.ui_window in ("show", "hidden", "headless"):
            args += ["--window", self.ui_window]
        self.log(ui_log_line(cmd, "char", ["--name", name], prompt))
        if self.dry_run:
            return {"local_path": str(out), "entity_id": None, "name": name, "media_id": None}
        payload = self._run_ui(args, "nhân vật có thể vẫn đang tạo trên Flow", spent=False)
        return {"local_path": payload.get("local_path") or str(out), "entity_id": payload.get("entity_id"),
                "name": payload.get("name") or name, "media_id": payload.get("media_id")}

    def _run_ui(self, args: list[str], timeout_note: str, *, spent: bool, who: str = "Flow") -> dict:
        """Run the Flow chat driver (streams its log and "@@progress" lines) and return its ok payload."""
        token = object()
        err_lines: list[str] = []

        def on_line(line: str) -> None:  # progress lines from the driver
            if not line:
                return
            is_prog, pct = prog.parse_progress_line(line)
            if is_prog:  # "@@progress N": % read on the Flow page, never logged nor part of an error
                if pct is not None:
                    self._emit(token, "percent", percent=pct)
                return
            err_lines.append(line)
            self.log(line)

        self._set_live(token)
        try:
            self._emit(token, "start", kind=None, expected=None, source=None)
            run_args, env = _tcc_direct(args)
            returncode, stdout, timed_out = _stream(run_args, self.timeout, on_line, env)
        finally:
            self._set_live(None)
        if timed_out:
            raise GflowError(f"điều khiển {who} quá {self.timeout}s; {timeout_note}",
                             exit_code=EXIT_TIMEOUT, payload={"error": {"spent": spent}})
        payload = _extract_json(stdout or "")
        if returncode != 0 or not payload or payload.get("status") != "ok":
            err = (payload or {}).get("error") or {}
            detail = err.get("detail") or "\n".join(err_lines[-5:]) or (stdout or "").strip()[-400:]
            raise GflowError(f"{who} (giao diện chat): {detail}", exit_code=int(err.get("exit_code") or returncode or 1),
                             retryable=False, payload=payload)
        return payload
