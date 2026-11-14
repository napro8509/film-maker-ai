"""Thin wrapper around the `gflow` command (gflow-cli).

Every call runs `gflow ... --json` and parses the JSON document gflow prints on
stdout. Errors come back as {"status": "fail", "error": {exit_code, retryable, ...}}.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
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
        args = ["image", "t2i", prompt, "--aspect", aspect, "--model", model, "-o", str(out)]
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
        args = ["image", "i2i", prompt]
        for r in refs:
            args += ["--ref", str(r)]
        args += ["--aspect", aspect, "--model", model, "-o", str(out)]
        if project:
            args += ["--project", project]
        payload = self.run_json(*args)
        images = payload.get("images") or []
        if images and images[0].get("local_path"):
            return Path(images[0]["local_path"])
        return out

    def _video_opts(self, *, aspect: str, model: str | None, duration: int | None, project: str | None, out: Path) -> list[str]:
        args = ["--aspect", aspect, "-o", str(out)]
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
        payload = self.run_json("video", "t2v", prompt, *self._video_opts(**kw))
        return self._video_result(payload, kw["out"])

    def image_to_video(self, image: Path, prompt: str, *, end_image: str | None = None, **kw: Any) -> tuple[Path, str | None]:
        args = ["video", "i2v", "--initial-frame", str(image)]
        if end_image:
            args += ["--end-frame", str(end_image)]
        payload = self.run_json(*args, prompt, *self._video_opts(**kw))
        return self._video_result(payload, kw["out"])

    def reference_to_video(self, prompt: str, refs: list[Path], **kw: Any) -> tuple[Path, str | None]:
        """Text-to-video with reference images ("ingredients") for the characters."""
        args = ["video", "r2v", prompt]
        for r in refs:
            args += ["--ref", str(r)]
        payload = self.run_json(*args, *self._video_opts(**kw))
        return self._video_result(payload, kw["out"])

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

    def ui_image_to_video(self, image: Path, prompt: str, *, refs: list[Path] | None = None, aspect: str,
                          model: str | None, duration: int | None, project: str | None, out: Path) -> tuple[Path, str | None]:
        cmd = self.ui_command()
        profile = self.ui_profile_dir()
        if not cmd or not (profile or os.environ.get("FLOW_UI_CMD")):
            raise GflowError("không tìm thấy Python/profile trình duyệt của gflow-cli để điều khiển Flow", exit_code=2)
        if not project:
            raise GflowError("cần project Flow để tạo video qua giao diện chat", exit_code=2)
        args = [*cmd, "i2v", "--project", project, "--image", str(image), "--prompt", prompt, "--aspect", aspect,
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
        opts = ["--image", str(image), *(a for r in refs or [] for a in ("--ref", str(r))), "--aspect", aspect]
        if model:
            opts += ["--model", MODEL_NAMES.get(model, model)]
        if duration:
            opts += ["--duration", str(duration)]
        self.log(ui_log_line(cmd, "i2v", opts, prompt))
        if self.dry_run:
            return out, None
        payload = self._run_ui(args, "video có thể vẫn đang tạo trên Flow", spent=True)
        local = payload.get("local_path")
        return (Path(local) if local else out), payload.get("media_id")

    def ui_image_to_image(self, prompt: str, refs: list[Path], *, out: Path, aspect: str, model: str,
                          project: str | None) -> Path:
        """Image guided by the characters' pictures through the Flow chat page (free), for accounts where
        gflow refuses i2i (agent-only composer, gflow #799)."""
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

    def _run_ui(self, args: list[str], timeout_note: str, *, spent: bool) -> dict:
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
            raise GflowError(f"điều khiển Flow quá {self.timeout}s; {timeout_note}",
                             exit_code=EXIT_TIMEOUT, payload={"error": {"spent": spent}})
        payload = _extract_json(stdout or "")
        if returncode != 0 or not payload or payload.get("status") != "ok":
            err = (payload or {}).get("error") or {}
            detail = err.get("detail") or "\n".join(err_lines[-5:]) or (stdout or "").strip()[-400:]
            raise GflowError(f"Flow (giao diện chat): {detail}", exit_code=int(err.get("exit_code") or returncode or 1),
                             retryable=False, payload=payload)
        return payload
