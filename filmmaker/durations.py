"""Scene durations the video model does not accept: the one place that defines a "bad duration".

A scene is bad when it has a `duration`, its effective model declares `durations` in the metadata, and the
duration is not in that list. `duration` None (Flow's default), a model without `durations`, an unknown model
and no model at all are never bad.

Used by the CLI (`check`, `run`/`make`, `round-durations`), the web server (`/estimate`, `/run`, `detail()`,
`durations/*`) and the planner. Pure helpers except `rewrite_file`. No web imports, and the pipeline is only
imported for type checking, so the planner does not pull it in.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Collection, Iterable, Mapping

import yaml

from . import model_meta

if TYPE_CHECKING:
    from .model_meta import ModelMeta
    from .pipeline import Pipeline
    from .storyboard import Scene, Storyboard

DIRECTIONS = ("up", "down")


def round_options(d: int, allowed: list[int]) -> tuple[int, int]:
    """(up, down): nearest allowed duration >= d and <= d, clamped to the ends of `allowed`."""
    up = min((x for x in allowed if x >= d), default=max(allowed))
    down = max((x for x in allowed if x <= d), default=min(allowed))
    return up, down


def _model(sb: Any, s: Any) -> str | None:
    """Same rule as `retirement.effective_model` (not imported: it would pull the pipeline into the planner)."""
    return s.model or sb.model or None


def allowed_for(sb: Any, s: Any, meta: "ModelMeta") -> list[int] | None:
    return meta.durations(_model(sb, s))


def is_bad(sb: Any, s: Any, meta: "ModelMeta") -> bool:
    if s.duration is None:
        return False
    allowed = allowed_for(sb, s, meta)
    return bool(allowed) and s.duration not in allowed


def bad_durations(sb: Any, meta: "ModelMeta", interrupted: Collection[str] = (), done: Collection[str] = (),
                  scenes: Iterable[Any] | None = None) -> list[dict]:
    """Bad scenes in the order of `scenes` (default: all of sb.scenes), as
    {id, model, duration, allowed, up, down, roundable, reason}. reason: "done" | "interrupted" | None
    ("done" wins when an id is in both sets); only reason None is roundable."""
    out = []
    for s in (sb.scenes if scenes is None else scenes):
        if not is_bad(sb, s, meta):
            continue
        allowed = allowed_for(sb, s, meta) or []
        up, down = round_options(s.duration, allowed)
        reason = "done" if s.id in done else "interrupted" if s.id in interrupted else None
        out.append({"id": s.id, "model": _model(sb, s), "duration": s.duration, "allowed": list(allowed),
                    "up": up, "down": down, "roundable": reason is None, "reason": reason})
    return out


def groups(pipe: "Pipeline") -> dict[str, str]:
    """{sid: scene_group} for every scene (done | interrupted | todo)."""
    return {s.id: pipe.scene_group(s) for s in pipe.sb.scenes}


def scan(pipe: "Pipeline", meta: "ModelMeta", scenes: Iterable[Any] | None = None) -> list[dict]:
    """`bad_durations` with the done/interrupted sets taken from the pipeline state. A "changed" scene (DONE
    with an old fingerprint) is in group "todo", so it is roundable."""
    g = groups(pipe)
    done = {sid for sid, x in g.items() if x == "done"}
    interrupted = {sid for sid, x in g.items() if x == "interrupted"}
    return bad_durations(pipe.sb, meta, interrupted, done, scenes)


def preview(pipe: "Pipeline", meta: "ModelMeta") -> tuple[list[dict], list[str]]:
    """(scenes, skipped): roundable rows {id, model, duration, allowed, up, down}, and the ids of interrupted
    scenes with a bad duration. Done scenes with a bad duration are in neither."""
    rows = scan(pipe, meta)
    keys = ("id", "model", "duration", "allowed", "up", "down")
    scenes = [{k: r[k] for k in keys} for r in rows if r["roundable"]]
    skipped = [r["id"] for r in rows if r["reason"] == "interrupted"]
    return scenes, skipped


def allowed_map(pipe: "Pipeline", meta: "ModelMeta") -> dict[str, list[int] | None]:
    """{sid: allowed durations} for every scene; pass to `base_of` so a models.yaml edit changes the base."""
    return {s.id: allowed_for(pipe.sb, s, meta) for s in pipe.sb.scenes}


def edits_for(rows: list[dict], direction: str) -> dict[str, int]:
    """{sid: up|down} for preview rows. ValueError for a direction other than "up"/"down"."""
    if direction not in DIRECTIONS:
        raise ValueError(f"direction phải là up hoặc down, không phải {direction!r}")
    return {r["id"]: r[direction] for r in rows}


def apply_to_raw(raw: dict, edits: dict[str, int]) -> dict:
    """A deep copy of the raw storyboard dict with only `duration` set for the scenes in `edits`."""
    out = copy.deepcopy(raw)
    for sc in out.get("scenes") or []:
        if isinstance(sc, dict) and sc.get("id") in edits:
            sc["duration"] = edits[sc["id"]]
    return out


def base_of(sb_bytes: bytes, groups: Mapping[str, str],
            allowed: Mapping[str, list[int] | None] | None = None) -> str:
    """Hash of storyboard.yaml + every scene's group (+ allowed durations when given). Any change between
    preview and round (file edited, a scene rendered, models.yaml edited) gives another base."""
    h = hashlib.sha256(sb_bytes + b"\0" + json.dumps(sorted(groups.items())).encode("utf-8"))
    if allowed is not None:
        h.update(b"\0" + json.dumps(sorted(allowed.items())).encode("utf-8"))
    return h.hexdigest()


def rewrite_file(path: Path, edits: dict[str, int]) -> None:
    """CLI: write storyboard.yaml back with only `duration` changed (comments are lost, as with every dump)."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    data = apply_to_raw(raw, edits)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
    # a fresh temp file in the same folder (never a fixed name that may already be a symlink); removed on error
    fd, tmp_name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def allowed_text(allowed: list[int]) -> str:
    return ", ".join(str(x) for x in allowed)


def describe(row: dict) -> str:
    """"s03: 5 giây — veo-lite chỉ nhận 4, 6, 8 (lên 6 / xuống 4)", plus a note for interrupted/done scenes."""
    text = (f"{row['id']}: {row['duration']} giây — {row['model']} chỉ nhận {allowed_text(row['allowed'])} "
            f"(lên {row['up']} / xuống {row['down']})")
    if row.get("reason") == "interrupted":
        text += " — bị ngắt — không tự làm tròn, sửa tay hoặc tải clip về"
    elif row.get("reason") == "done":
        text += " — đã có clip — sửa tay `duration:` trong storyboard rồi render lại"
    return text


def refuse_bad_duration(sb: "Storyboard") -> Callable[["Scene"], str | None]:
    """For RunOptions.refuse: re-reads the metadata for every scene (like `retirement.refuse_retired`)."""
    def refuse(s: "Scene") -> str | None:
        meta = model_meta.load()
        if not is_bad(sb, s, meta):
            return None
        return (f"độ dài {s.duration} giây không hợp lệ với {_model(sb, s)} "
                f"(chỉ nhận {allowed_text(allowed_for(sb, s, meta) or [])}); làm tròn (UI: hộp “Độ dài không hợp lệ” "
                "khi bấm Render, CLI: filmmaker round-durations … --up|--down) hoặc sửa `duration:` rồi render lại")
    return refuse


def round_new_storyboard(sb: Any, meta: "ModelMeta") -> Any:
    """Planner safety net (after `normalize_new_storyboard`): bad durations are rounded up. In place; returns sb."""
    for s in sb.scenes:
        if is_bad(sb, s, meta):
            s.duration = round_options(s.duration, allowed_for(sb, s, meta) or [])[0]
    return sb
