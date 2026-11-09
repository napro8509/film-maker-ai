"""Warnings and blocking for video models Flow is retiring (no web imports; used by server and CLI).

The metadata (retirement dates, prices) comes from `model_meta`; which scenes a render would send
comes from `Pipeline.video_plan`. Always call `model_meta.load()` / `model_meta.today()` through the
module so tests can swap the date.
"""

from __future__ import annotations

from typing import Callable, Iterable

from . import model_meta
from .model_meta import ModelMeta
from .pipeline import Pipeline
from .storyboard import Scene, Storyboard

GROUP_LABEL = {"done": "đã có clip", "interrupted": "bị ngắt", "todo": "chưa có clip"}
MODEL_NOTE = ("Flow tự chọn model; nếu Flow đổi model mặc định thì app không biết và cảnh sẽ không bị "
              "đánh dấu render lại. Nên chọn model cụ thể cho các cảnh chưa có clip.")


def effective_model(sb: Storyboard, s: Scene) -> str | None:
    """The model `_render_scene` sends for this scene (empty string counts as none)."""
    return s.model or sb.model or None


def old_scenes(pipe: Pipeline, meta: ModelMeta, scenes: Iterable[Scene]) -> list[dict]:
    """Scenes whose effective model is retiring or retired, with their group (done/interrupted/todo)."""
    out = []
    for s in scenes:
        m = effective_model(pipe.sb, s)
        if meta.is_old(m):
            out.append({"id": s.id, "model": m, "status": meta.status(m),
                        "retires_label": meta.retires_label(m), "group": pipe.scene_group(s)})
    return out


def retired_of(pipe: Pipeline, meta: ModelMeta, scenes: Iterable[Scene]) -> list[dict]:
    """Scenes whose effective model is already retired (pass the scenes a render would send)."""
    out = []
    for s in scenes:
        m = effective_model(pipe.sb, s)
        if meta.status(m) == "retired":
            out.append({"id": s.id, "model": m, "retires_label": meta.retires_label(m)})
    return out


def sentence(alias: str, status: str, label: str | None, todo: int, done: int, interrupted: int) -> str:
    """Banner line, e.g. "Model veo-fast ngừng ngày 05/12: 4 cảnh chưa có clip, 3 cảnh đã có clip, 1 cảnh bị ngắt." """
    when = f"đã ngừng từ {label}" if status == "retired" else f"ngừng ngày {label}"
    parts = [f"{todo} cảnh chưa có clip", f"{done} cảnh đã có clip"]
    if interrupted:
        parts.append(f"{interrupted} cảnh bị ngắt")
    return f"Model {alias} {when}: {', '.join(parts)}."


def model_warning(pipe: Pipeline, meta: ModelMeta) -> dict | None:
    """One entry/line per retiring or retired model (in order of first use), or None if no scene uses one."""
    counts: dict[str, dict] = {}
    for row in old_scenes(pipe, meta, pipe.sb.scenes):
        m = counts.setdefault(row["model"], {"alias": row["model"], "status": row["status"],
                                             "retires_label": row["retires_label"],
                                             "todo": 0, "done": 0, "interrupted": 0})
        m[row["group"]] += 1
    if not counts:
        return None
    models = list(counts.values())
    lines = [sentence(m["alias"], m["status"], m["retires_label"], m["todo"], m["done"], m["interrupted"])
             for m in models]
    return {"models": models, "lines": lines, "text": "\n".join(lines)}


def model_note(pipe: Pipeline) -> str | None:
    """MODEL_NOTE when at least one scene without a clip has no model (Flow picks one)."""
    for s in pipe.sb.scenes:
        if effective_model(pipe.sb, s) is None and pipe.scene_group(s) == "todo":
            return MODEL_NOTE
    return None


def models_used(pipe: Pipeline, meta: ModelMeta) -> dict[str, dict]:
    """alias -> {status, retires_label, known} for every effective model in the storyboard."""
    out: dict[str, dict] = {}
    for s in pipe.sb.scenes:
        m = effective_model(pipe.sb, s)
        if m and m not in out:
            out[m] = {"status": meta.status(m), "retires_label": meta.retires_label(m),
                      "known": meta.entry(m) is not None}
    return out


def refuse_retired(sb: Storyboard) -> Callable[[Scene], str | None]:
    """For RunOptions.refuse: re-reads the metadata and today's date for every scene, so a run that
    started before midnight of the retirement day stops sending that model after midnight."""
    def refuse(s: Scene) -> str | None:
        meta = model_meta.load()
        m = effective_model(sb, s)
        if meta.status(m) != "retired":
            return None
        return (f"model {m} đã ngừng từ {meta.retires_label(m)}; đổi model (UI: “Đổi model…”, "
                "hoặc sửa `model:` trong storyboard) rồi render lại")
    return refuse
