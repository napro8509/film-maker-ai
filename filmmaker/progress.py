"""Per-item progress of a job (portraits, keyframes, scene videos) for the web UI.

Shared by `gflow.py`, `pipeline.py` and `web/server.py`; never imports fastapi or the server. Always import it as
`from . import progress as prog` and read the clock through `prog.now()`, so tests can monkeypatch
`filmmaker.progress.now` with a fake clock.

- `Reporter` holds the state machine of each item (queued → submitting → generating → downloading → done/failed) and
  calls `emit(key, item)` on every real change (`item=None` removes the item).
- `Gflow` reports the start of a generation command and gflow-cli's "downloading" events through
  `Reporter.gflow_event`, for the item currently marked `active`.
- `Timings` remembers how long successful generation commands took (`timings.json` next to `settings.json`), so
  `public_view` can show an estimated percentage when Flow reports none.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from . import settings

PHASES = ("queued", "submitting", "generating", "downloading", "done", "failed")
ACTIVE = {"queued", "submitting", "generating", "downloading"}
DEFAULT_EXPECTED = {"image": 30.0, "video": 150.0}
KEEP = 10  # records kept per kind, and used for the median
PUBLIC_FIELDS = ("phase", "percent", "source", "started", "attempt")
SUCCESSFUL = "MEDIA_GENERATION_STATUS_SUCCESSFUL"


def now() -> float:
    return time.time()


def timings_path() -> Path:
    return settings.settings_path().parent / "timings.json"


def _opt(args: Sequence[str], name: str) -> str | None:
    args = list(args)
    for i, a in enumerate(args[:-1]):
        if a == name:
            return args[i + 1]
    return None


def timing_kind(args: Sequence[str]) -> str | None:
    """Kind of a generation command for `timings.json`: "image" or "video:<model>:<duration>"; None otherwise."""
    if len(args) < 2:
        return None
    if args[0] == "image" and args[1] in ("t2i", "i2i"):
        return "image"
    if args[0] == "video" and args[1] in ("t2v", "i2v", "r2v"):
        return f"video:{_opt(args, '--model') or 'default'}:{_opt(args, '--duration') or 'default'}"
    return None


def gflow_event_phase(rec: dict) -> str | None:
    """Phase a gflow-cli log event moves the item to (table G3 of the spec), or None (no change)."""
    event = rec.get("event")
    if event in ("migrated.status", "ui_automation_video.poll_terminal"):
        return "downloading" if rec.get("status") == SUCCESSFUL else None
    if event in ("migrated.download", "ui_automation_video.video_saved"):
        return "downloading"
    return None


def parse_log_line(line: str) -> dict | None:
    """A gflow-cli JSON log line (an object with "event" or "level"), else None."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    if isinstance(rec, dict) and ("event" in rec or "level" in rec):
        return rec
    return None


def is_quiet_log(line: str) -> bool:
    """True for a JSON log line at debug/info level (kept out of error details)."""
    rec = parse_log_line(line)
    return rec is not None and str(rec.get("level", "")).lower() in ("debug", "info")


PROGRESS_RE = re.compile(r"^@@progress(?:\s+(.*))?$")


def parse_progress_line(line: str) -> tuple[bool, int | None]:
    """("@@progress 42") -> (True, 42); invalid values -> (True, None); any other line -> (False, None)."""
    m = PROGRESS_RE.match(line.strip())
    if not m:
        return False, None
    tok = (m.group(1) or "").strip()
    if not tok.isdigit():
        return True, None
    n = int(tok)
    return True, (n if 0 <= n <= 100 else None)


class Timings:
    """Durations (seconds) of the last successful generation commands, per kind."""

    def __init__(self, path: Path | None = None, warn: Callable[[str], None] | None = None):
        self.path = path or timings_path()
        self.warn = warn
        self._warned = False

    def _warn(self, msg: str) -> None:
        if self._warned or not self.warn:
            return
        self._warned = True
        try:
            self.warn(msg)
        except Exception:
            pass

    def _load(self) -> dict[str, list[float]]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            self._warn(f"⚠ không đọc được {self.path.name} ({exc}); dùng thời lượng mặc định")
            return {}
        try:
            data = json.loads(text)
            runs = data["runs"]
            if not isinstance(runs, dict):
                raise ValueError("runs")
        except (ValueError, KeyError, TypeError):
            self._warn(f"⚠ {self.path.name} hỏng; bỏ qua và dùng thời lượng mặc định")
            return {}
        out: dict[str, list[float]] = {}
        for kind, vals in runs.items():
            if isinstance(kind, str) and isinstance(vals, list):
                nums = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)
                        and math.isfinite(v) and v > 0]
                if nums:
                    out[kind] = nums[-KEEP:]
        return out

    def expected(self, kind: str) -> float:
        vals = self._load().get(kind)
        if vals:
            return float(statistics.median(vals[-KEEP:]))
        return DEFAULT_EXPECTED.get(kind.split(":")[0], DEFAULT_EXPECTED["video"])

    def record(self, kind: str, seconds: float) -> None:
        if not (seconds > 0 and math.isfinite(seconds)):
            return
        runs = self._load()
        runs[kind] = [*runs.get(kind, []), round(float(seconds), 2)][-KEEP:]
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({"version": 1, "runs": runs}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            self._warn(f"⚠ không ghi được {self.path.name} ({exc})")
            try:
                tmp.unlink()
            except OSError:
                pass


Emit = Callable[[str, dict | None], None]  # (key, item); item None = remove the item


class Reporter:
    """State machine of the job's items. Every public method runs inside `_lock` (check → set → emit is atomic):
    `gflow_event` runs on the stderr pump thread of `gflow._stream`, everything else on the pipeline thread."""

    def __init__(self, emit: Emit | None = None):
        self.emit = emit
        self.items: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._active: list[str] = []

    # --- helpers (call with the lock held) ---------------------------------

    def _send(self, key: str, item: dict | None) -> None:
        if not self.emit:
            return
        try:
            # The internal item (5 public fields + "expected"); `public_view` strips "expected" for the API.
            self.emit(key, None if item is None else dict(item))
        except Exception:
            pass

    def _set(self, key: str, **changes: Any) -> None:
        old = self.items.get(key)
        item = dict(old) if old else {"phase": "queued", "percent": None, "source": None, "started": None,
                                      "attempt": 1, "expected": None}
        item.update(changes)
        if item != old:
            self.items[key] = item
            self._send(key, item)

    def _drop(self, key: str) -> None:
        if key in self.items:
            del self.items[key]
            self._send(key, None)

    def _current(self) -> str | None:
        """Key of the innermost active item, if it is still running."""
        if not self._active:
            return None
        key = self._active[-1]
        item = self.items.get(key)
        return key if item and item["phase"] in ACTIVE else None

    # --- milestones from the pipeline ---------------------------------------

    def queued(self, key: str, attempt: int = 1) -> None:
        with self._lock:
            self._set(key, phase="queued", percent=None, source=None, started=None, attempt=attempt, expected=None)

    def submitting(self, key: str) -> None:
        with self._lock:
            self._set(key, phase="submitting", percent=None, source=None, started=None, expected=None)

    def done(self, key: str) -> None:
        with self._lock:
            self._set(key, phase="done", percent=None)

    def failed(self, key: str) -> None:
        with self._lock:
            self._set(key, phase="failed", percent=None)

    def drop(self, key: str) -> None:
        with self._lock:
            self._drop(key)

    def drop_queued(self) -> None:
        with self._lock:
            for key in [k for k, v in self.items.items() if v["phase"] == "queued"]:
                self._drop(key)

    def settle(self) -> None:
        """End of a loop: unstarted items go away, items left running (unexpected exception) become failed."""
        with self._lock:
            self.drop_queued()
            for key in [k for k, v in self.items.items() if v["phase"] in ACTIVE]:
                self._set(key, phase="failed", percent=None)

    @contextmanager
    def active(self, key: str) -> Iterator[None]:
        """Events of `Gflow` go to `key` while inside (nestable: a keyframe inside a scene video)."""
        with self._lock:
            self._active.append(key)
        try:
            yield
        finally:
            with self._lock:
                if key in self._active:
                    del self._active[len(self._active) - 1 - self._active[::-1].index(key)]

    def retry_wait(self, attempt: int) -> None:
        """`_with_retries` is about to sleep before another try: the active item waits again."""
        with self._lock:
            key = self._current()
            if key is not None:
                self._set(key, phase="queued", attempt=attempt, started=None, percent=None, source=None,
                          expected=None)

    # --- events from Gflow --------------------------------------------------

    def gflow_event(self, name: str, data: dict) -> None:
        with self._lock:
            key = self._current()
            if key is None:
                return
            phase = self.items[key]["phase"]
            if name == "start":
                self._set(key, phase="generating", started=now(), percent=None, source=data.get("source"),
                          expected=data.get("expected"))
            elif name == "downloading" and phase == "generating":
                self._set(key, phase="downloading")
            elif name == "percent" and phase == "generating":
                self._set(key, percent=data.get("percent"), source="flow")


def public_view(items: dict[str, dict], at: float) -> dict[str, dict]:
    """Copy of the items with the 5 public fields; the estimated percentage is computed here, at time `at`."""
    out: dict[str, dict] = {}
    for key, item in items.items():
        view = {k: item.get(k) for k in PUBLIC_FIELDS}
        source, started, expected = item.get("source"), item.get("started"), item.get("expected")
        if source == "estimate" and item.get("phase") in ("generating", "downloading") and started and expected:
            view["percent"] = max(0, min(95, math.floor((at - started) / expected * 100)))
        elif source != "flow":
            view["percent"] = None
        out[key] = view
    return out
