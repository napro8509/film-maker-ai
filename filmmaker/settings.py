"""User settings shared by the CLI and the web UI (~/.config/filmmaker/settings.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_BACKUP_REMOTE = "gdrive:FilmMaker"


def settings_path() -> Path:
    return Path(os.environ.get("FILMMAKER_SETTINGS", Path.home() / ".config" / "filmmaker" / "settings.json"))


def load(path: Path | None = None) -> dict[str, Any]:
    try:
        return json.loads((path or settings_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
