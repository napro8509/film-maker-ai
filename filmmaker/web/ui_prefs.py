"""Per-project UI preferences (`ui.json`): final settings, thumbnail titles, storyboard board layout.

Every write goes through `update()`: one lock per project, atomic replace, and a broken file is moved aside
(`ui.json.broken-<time>`) instead of being silently overwritten.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")

UI_FILE = "ui.json"
EMPTY_BOARD: dict[str, Any] = {"cards": {}, "videos": [], "view": None}
MAX_CARDS = 1000
MAX_VIDEOS = 1000
MIN_ZOOM, MAX_ZOOM = 0.25, 2.0
CARD_KEY = re.compile(r"^(char|kf|vid):.+$", re.S)

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def lock(project_dir: Path) -> threading.Lock:
    """The lock guarding `ui.json` of this project (one per resolved path, in this process)."""
    key = str(Path(project_dir).resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _load(path: Path) -> dict | None:
    """Parsed prefs; None when the file is missing; raises ValueError when it is unreadable or not a dict."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    data = json.loads(text)  # ValueError (JSONDecodeError) when broken
    if not isinstance(data, dict):
        raise ValueError("ui.json is not an object")
    return data


def read(project_dir: Path) -> dict:
    """Prefs of the project; {} when the file is missing, broken or not an object. Never writes."""
    try:
        return _load(Path(project_dir) / UI_FILE) or {}
    except (OSError, ValueError):
        return {}


def _quarantine(path: Path) -> Path:
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    dest = path.with_name(f"{path.name}.broken-{stamp}")
    n = 2
    while dest.exists():
        dest, n = path.with_name(f"{path.name}.broken-{stamp}-{n}"), n + 1
    os.replace(path, dest)
    return dest


def update(project_dir: Path, fn: Callable[[dict], T]) -> T:
    """Read, let `fn` change the prefs in place, write back atomically; returns what `fn` returned."""
    path = Path(project_dir) / UI_FILE
    with lock(project_dir):
        try:
            prefs = _load(path) or {}
        except ValueError:  # really broken (bad JSON / not an object); an OSError while reading propagates
            _quarantine(path)  # keep the broken file for the user, start over
            prefs = {}
        result = fn(prefs)
        _write_atomic(path, json.dumps(prefs, indent=2, ensure_ascii=False))
        return result


def _write_atomic(path: Path, text: str) -> None:
    """Write via a fresh temp file in the same folder (never follows a planted symlink), then replace."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def board_of(prefs: dict) -> dict:
    """The saved board layout, or an empty one (a fresh copy) when missing or malformed."""
    try:
        return validate_board(prefs.get("board"))
    except ValueError:
        return copy.deepcopy(EMPTY_BOARD)


def _number(v: Any) -> bool:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:  # an int too large for a float (e.g. 10**400)
        return False


def validate_board(data: Any) -> dict:
    """Checked, normalised board layout {cards, videos, view}; ValueError with a Vietnamese message."""
    if not isinstance(data, dict):
        raise ValueError("Bố cục bảng phải là một đối tượng JSON")
    cards = data.get("cards", {})
    if not isinstance(cards, dict):
        raise ValueError("Danh sách card phải là một đối tượng")
    if len(cards) > MAX_CARDS:
        raise ValueError(f"Bảng có quá nhiều card (tối đa {MAX_CARDS})")
    out_cards: dict[str, dict[str, float]] = {}
    for key, pos in cards.items():
        if not isinstance(key, str) or not CARD_KEY.fullmatch(key):
            raise ValueError(f"Khoá card không hợp lệ: {key}")
        if not isinstance(pos, dict):
            raise ValueError(f"Toạ độ card {key} phải là {{x, y}}")
        if not (_number(pos.get("x")) and _number(pos.get("y"))):
            raise ValueError(f"Toạ độ card {key} phải là số")
        out_cards[key] = {"x": pos["x"], "y": pos["y"]}
    videos = data.get("videos", [])
    if not isinstance(videos, list) or not all(isinstance(v, str) for v in videos):
        raise ValueError("Danh sách video phải là danh sách chuỗi")
    if len(videos) > MAX_VIDEOS:
        raise ValueError(f"Bảng có quá nhiều video (tối đa {MAX_VIDEOS})")
    view = data.get("view")
    if view is not None:
        if not isinstance(view, dict):
            raise ValueError("Khung nhìn phải là {x, y, zoom}")
        if not all(_number(view.get(k)) for k in ("x", "y", "zoom")):
            raise ValueError("Khung nhìn (x, y, zoom) phải là số")
        if not MIN_ZOOM <= view["zoom"] <= MAX_ZOOM:
            raise ValueError("Mức thu phóng phải trong 25%–200%")
        view = {"x": view["x"], "y": view["y"], "zoom": view["zoom"]}
    return {"cards": out_cards, "videos": list(videos), "view": view}
