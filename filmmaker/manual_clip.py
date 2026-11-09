"""Checks for a clip the user downloaded from Flow by hand into clips/<id>.mp4.

A render that was interrupted after Flow may have billed it stays "submitting". If the user saves
the clip from Flow as clips/<id>.mp4, the app takes it instead of paying again, but only when the
file is complete (read with ffprobe) and newer than the submission (`submitted_at`).

Callers use this module by name (`manual_clip.check_file(...)`) so tests can monkeypatch
`probe_clip` / `ffprobe_available` / `shutil.which` everywhere, job threads included. No logging here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

MIN_BYTES = 10_000  # a hand-downloaded clips/<id>.mp4 smaller than this is not adopted
FileVerdict = Literal["missing", "unchecked", "invalid", "valid"]
TAIL_SLACK = 0.5  # seconds: the last video packet may end this much before the stream's duration
PROBE_TIMEOUT = 60  # seconds, for the single ffprobe call
_CACHE_MAX = 256

_cache: dict[tuple[str, int, int], bool] = {}


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def probe_clip(path: Path) -> bool:
    """True when ffprobe reads the whole file: a video stream, a duration > 0, and video packets up to the
    end (a cut-off download, even a faststart mp4 whose `moov` is intact, stops early). Not cached."""
    exe = shutil.which("ffprobe")
    if not exe:
        return False
    # The file comes from outside: read it only as a local mp4/mov (no playlist/concat demuxer, no
    # network or other protocol). Flow clips are mp4; anything else is "invalid" (never adopted).
    cmd = [exe, "-v", "error", "-protocol_whitelist", "file", "-f", "mov", "-select_streams", "v",
           "-show_entries", "stream=index,duration:packet=pts_time:format=duration",
           "-of", "json", "file:" + str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=PROBE_TIMEOUT)
        j = json.loads(proc.stdout)
        streams = j["streams"]
        if not streams:
            return False
        fmt = float(j["format"]["duration"])  # same field and parsing as assemble.probe_duration
        if not fmt > 0:
            return False
        try:
            ref = float(streams[0]["duration"])
        except (KeyError, TypeError, ValueError):
            ref = 0.0
        if not ref > 0:
            ref = fmt  # compare with the video stream when known, so a longer audio track does not matter
        pts = [t for t in (_seconds(p.get("pts_time")) for p in j.get("packets") or []) if t is not None]
        # max, not the last packet: with B-frames pts are not in order
        return bool(pts) and max(pts) >= ref - TAIL_SLACK
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError, KeyError, TypeError,
            IndexError):
        return False


def _seconds(value: Any) -> float | None:
    """A packet's pts_time, or None when ffprobe has none for it ("N/A", missing): that packet is skipped."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def check_file(path: Path) -> FileVerdict:
    """missing | unchecked (no ffprobe) | invalid (too small, or ffprobe can't read it all) | valid."""
    path = Path(path)
    try:
        if not path.is_file():
            return "missing"
        st = path.stat()
    except OSError:
        return "missing"
    if not ffprobe_available():
        return "unchecked"
    if st.st_size < MIN_BYTES:
        return "invalid"
    key = (str(path), st.st_size, st.st_mtime_ns)
    ok = _cache.get(key)
    if ok is None:
        ok = bool(probe_clip(path))
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = ok
    return "valid" if ok else "invalid"


def clear_cache() -> None:
    _cache.clear()


def stamp_now() -> str:
    """Now in UTC, rounded down to the second: "2026-11-09T08:15:42Z"."""
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(value: Any) -> float | None:
    """Epoch seconds of a `submitted_at` value ("...Z" or "+00:00"); None when missing or malformed."""
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def adopt_line(sid: str, clip: Path) -> str:
    return f"[{sid}] dùng clip bạn đã tải về ({clip}) thay vì gửi lại; xoá file này nếu muốn render lại"
