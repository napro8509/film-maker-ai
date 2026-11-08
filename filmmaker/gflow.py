"""Thin wrapper around the `gflow` command (gflow-cli).

Every call runs `gflow ... --json` and parses the JSON document gflow prints on
stdout. Errors come back as {"status": "fail", "error": {exit_code, retryable, ...}}.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
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


@dataclass
class Gflow:
    binary: str = field(default_factory=lambda: os.environ.get("GFLOW_BIN", "gflow"))
    profile: str | None = None
    timeout: int = 60 * 30  # a single Veo render can take several minutes
    dry_run: bool = False
    log: Any = print

    def available(self) -> bool:
        return shutil.which(shlex.split(self.binary)[0]) is not None

    def _cmd(self, *args: str) -> list[str]:
        cmd = [*shlex.split(self.binary), *args]
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
