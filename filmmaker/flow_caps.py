"""What this Google Flow account can do through gflow, remembered across projects.

A project's state.json already remembers, once found out, that gflow cannot do image-to-image (`i2i_ui`: go
straight to the Flow chat page), image-to-video (`i2v_unsupported`) or reference-to-video (`r2v_unsupported`)
on this account. Without this file every NEW project found it out again: one failing gflow run (a browser
launch, 20-60 s) per kind of request. The answer depends on the account, not the project, so it is kept here
(`flow_caps.json` next to settings.json), per gflow binary + profile, and copied into new projects.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import settings

KEYS = ("i2i_ui", "i2v_unsupported", "i2v_error", "r2v_unsupported")


def path() -> Path:
    return settings.settings_path().parent / "flow_caps.json"


def _key(binary: str, profile: str | None) -> str:
    return f"{binary}|{profile or ''}"


def _read() -> dict[str, Any]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load(binary: str, profile: str | None) -> dict[str, Any]:
    entry = _read().get(_key(binary, profile))
    return {k: v for k, v in entry.items() if k in KEYS} if isinstance(entry, dict) else {}


def remember(binary: str, profile: str | None, **found: Any) -> None:
    """Store flags (value None removes one). Never raises: this is only a speed-up."""
    try:
        data = _read()
        entry = data.setdefault(_key(binary, profile), {})
        for k, v in found.items():
            if k not in KEYS:
                continue
            if v is None:
                entry.pop(k, None)
            else:
                entry[k] = v
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass
