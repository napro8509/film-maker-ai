"""Shared fixtures for the bad-duration tests (flow-render-fixes): veo-lite projects in every scene group.

veo-lite accepts 4, 6, 8 seconds (package models.yaml). Builds on `tests/model_helpers.py` (`write_project`,
`set_scene`), which is only imported here, never changed.
"""
from __future__ import annotations

from pathlib import Path

from tests.model_helpers import set_scene, stamp, write_project

VEO_LITE = "veo-lite"
ALLOWED = [4, 6, 8]


def veo_lite_project(ws: Path, pid: str = "film", scenes: list[dict] | None = None) -> Path:
    """<ws>/<pid>/storyboard.yaml on veo-lite. `scenes` items as in `storyboard_dict` (id, duration, model, …)."""
    return write_project(ws, pid, VEO_LITE, scenes)


def ac12_project(ws: Path, pid: str = "film") -> Path:
    """Spec AC12: s01=5 todo, s02=7 todo, s03=6 todo (valid), s04=5 done, s05=7 interrupted."""
    p = veo_lite_project(ws, pid, [{"id": "s01", "duration": 5}, {"id": "s02", "duration": 7},
                                   {"id": "s03", "duration": 6}, {"id": "s04", "duration": 5},
                                   {"id": "s05", "duration": 7}])
    set_scene(p, "s04", "done")
    set_scene(p, "s05", "interrupted", submitted_at=stamp(-60))
    return p


def ac16_project(ws: Path, pid: str = "film") -> Path:
    """Spec AC16: s01=5 todo, s02=7 `submitting` with `submitted_at` and no clip, s03=6 done."""
    p = veo_lite_project(ws, pid, [{"id": "s01", "duration": 5}, {"id": "s02", "duration": 7},
                                   {"id": "s03", "duration": 6}])
    set_scene(p, "s02", "interrupted", submitted_at=stamp(-60))
    set_scene(p, "s03", "done")
    return p
