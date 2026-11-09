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
from typing import Any

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


UI_SCRIPT = Path(__file__).with_name("flow_ui.py")

# Names the Flow chat agent understands for gflow's model aliases.
MODEL_NAMES = {
    "veo-fast": "Veo 3.1 Fast", "veo-quality": "Veo 3.1 Quality", "veo-lite": "Veo 3.1 Lite",
    "veo-lite-lp": "Veo 3.1 Lite (lower priority)", "omni-flash": "Omni Flash", "omni-1.2-flash": "Omni 1.2 Flash",
}


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
    _ui_profile: Any = field(default=None, init=False, repr=False)

    def available(self) -> bool:
        return shutil.which(_split(self.binary)[0]) is not None

    def _cmd(self, *args: str) -> list[str]:
        cmd = [*_split(self.binary), *args]
        if self.profile:
            cmd += ["--profile", self.profile]
        return cmd

    def run_json(self, *args: str) -> dict[str, Any]:
        cmd = self._cmd(*args, "--json")
        self.log("  $ " + shlex.join(cmd))
        if self.dry_run:
            return {"status": "ok", "dry_run": True}
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise GflowError(f"gflow did not finish within {self.timeout}s", exit_code=EXIT_TIMEOUT) from exc
        payload = _extract_json(proc.stdout)
        if proc.returncode != 0 or payload is None or payload.get("status") == "fail":
            err = (payload or {}).get("error") or {}
            detail = err.get("detail") or err.get("title") or (proc.stderr or proc.stdout).strip()[-800:]
            hint = err.get("remediation_hint")
            msg = f"gflow exited {proc.returncode}: {detail}" + (f" (hint: {hint})" if hint else "")
            raise GflowError(
                msg,
                exit_code=int(err.get("exit_code") or proc.returncode or 1),
                retryable=bool(err.get("retryable", False)),
                payload=payload,
            )
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

    def image_to_image(self, prompt: str, refs: list[Path], *, out: Path, aspect: str, model: str,
                       project: str | None) -> Path:
        """Text-to-image guided by reference images (keeps characters' faces the same)."""
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
        self.log("  $ " + shlex.join(args[:2] + ["i2v", "--image", str(image), "…"]))
        if self.dry_run:
            return out, None
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        err_lines: list[str] = []

        def pump() -> None:
            for line in proc.stderr:  # progress lines from the driver
                line = line.rstrip()
                if line:
                    err_lines.append(line)
                    self.log(line)

        t = threading.Thread(target=pump, daemon=True)
        t.start()
        timed_out = threading.Event()

        def kill() -> None:
            timed_out.set()
            proc.kill()

        timer = threading.Timer(self.timeout, kill)
        timer.start()
        try:
            stdout = proc.stdout.read()
            proc.wait()
        finally:
            timer.cancel()
        t.join(timeout=5)
        if timed_out.is_set():
            raise GflowError(f"điều khiển Flow quá {self.timeout}s; video có thể vẫn đang tạo trên Flow",
                             exit_code=EXIT_TIMEOUT, payload={"error": {"spent": True}})
        payload = _extract_json(stdout or "")
        if proc.returncode != 0 or not payload or payload.get("status") != "ok":
            err = (payload or {}).get("error") or {}
            detail = err.get("detail") or "\n".join(err_lines[-5:]) or (stdout or "").strip()[-400:]
            raise GflowError(f"Flow (giao diện chat): {detail}", exit_code=int(err.get("exit_code") or proc.returncode or 1),
                             retryable=False, payload=payload)
        local = payload.get("local_path")
        return (Path(local) if local else out), payload.get("media_id")
