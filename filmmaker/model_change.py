"""Switch scenes to another video model without losing finished clips (AC27–AC39).

`plan_change` works out what would change (preview); `apply_to_raw` applies that plan to the raw
YAML dict of storyboard.yaml, touching only `model` / `duration` keys. Empty strings in `model`
count as no model. No web imports: the server turns `ChangeError` into HTTP 400.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .durations import round_options  # moved; model_change.round_options still works
from .model_meta import ModelMeta
from .pipeline import Pipeline
from .retirement import effective_model

DEFAULT_SECONDS = 8  # what Flow renders when a scene has no duration (credit estimate only)
ROUNDING = (None, "", "up", "down")


class ChangeError(ValueError):
    """Bad request for a model change (unknown/retiring target, unknown scene id, ...)."""


@dataclass
class ChangePlan:
    target: str
    mode: str  # "project" | "scene"
    change: list[str] = field(default_factory=list)
    redo: list[str] = field(default_factory=list)
    interrupted: list[str] = field(default_factory=list)
    keep: list[str] = field(default_factory=list)
    pin: list[str] = field(default_factory=list)
    bad_duration: list[dict] = field(default_factory=list)  # {id, duration, up, down}
    storyboard_model: dict | None = None  # {"from", "to"} or None when unchanged
    credits: float | int | None = 0
    credits_approx: bool = False
    edits: dict[str, dict] = field(default_factory=dict)  # sid -> {"model": str | None (drop)} / {"duration": int}
    sb_model_new: str | None = None

    def has_changes(self) -> bool:
        return bool(self.edits) or self.storyboard_model is not None

    def public(self, base: str) -> dict[str, Any]:
        return {
            "mode": self.mode, "target": self.target,
            "change": list(self.change), "redo": list(self.redo), "interrupted": list(self.interrupted),
            "keep": list(self.keep), "pin": list(self.pin), "bad_duration": [dict(x) for x in self.bad_duration],
            "storyboard_model": dict(self.storyboard_model) if self.storyboard_model else None,
            "credits": self.credits, "credits_approx": self.credits_approx,
            "has_changes": self.has_changes(), "base": base,
        }


def _unique(ids: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def plan_change(pipe: Pipeline, meta: ModelMeta, target: str, *, scenes: list[str] | None = None,
                include: Iterable[str] = (), rounding: str | None = None) -> ChangePlan:
    sb = pipe.sb
    if not isinstance(target, str) or meta.entry(target) is None:
        raise ChangeError(f"Model {target} không có trong metadata.")
    if meta.status(target) != "active":
        raise ChangeError(f"Model {target} sắp ngừng hoặc đã ngừng, hãy chọn model khác.")
    if rounding not in ROUNDING:
        raise ChangeError("rounding phải là up, down hoặc để trống.")
    rounding = rounding or None
    include = _unique(include or ())
    if scenes is not None and include:
        raise ChangeError("Không dùng scenes và include cùng lúc.")
    by_id = {s.id: s for s in sb.scenes}
    unknown = sorted(set(scenes or ()) | set(include)) if scenes is not None else sorted(set(include))
    unknown = [x for x in unknown if x not in by_id]
    if unknown:
        raise ChangeError(f"Không có cảnh: {', '.join(unknown)}")

    mode = "project" if scenes is None else "scene"
    old = sb.model or None
    new_sb = (target if meta.is_old(old) else old) if mode == "project" else old
    plan = ChangePlan(target=target, mode=mode, sb_model_new=new_sb)
    group = {s.id: pipe.scene_group(s) for s in sb.scenes}

    if mode == "project":
        wanted = {s.id for s in sb.scenes if group[s.id] == "todo" and meta.is_old(effective_model(sb, s))}
        wanted |= set(include)
    else:
        wanted = set(scenes)
    allowed = meta.durations(target)
    unit = meta.price_unit(target)
    price = meta.credits(target)
    total: float | int = 0

    for s in sb.scenes:  # storyboard order
        if s.id not in wanted or effective_model(sb, s) == target:
            continue  # already on target: never checked or rounded (AC33)
        plan.change.append(s.id)
        edit: dict[str, Any] = {"model": None if new_sb == target else target}
        seconds = s.duration
        if allowed and s.duration is not None and s.duration not in allowed:
            up, down = round_options(s.duration, allowed)
            plan.bad_duration.append({"id": s.id, "duration": s.duration, "up": up, "down": down})
            if rounding:
                seconds = edit["duration"] = up if rounding == "up" else down
        plan.edits[s.id] = edit
        if group[s.id] == "done":
            plan.redo.append(s.id)
        elif group[s.id] == "interrupted":
            plan.interrupted.append(s.id)
        if price is not None:
            if unit == "second":
                if seconds is None:
                    seconds, plan.credits_approx = DEFAULT_SECONDS, True
                total += price * seconds
            else:
                total += price

    if mode == "project":
        changed = set(plan.change)
        for s in sb.scenes:
            if group[s.id] not in ("done", "interrupted") or s.id in changed:
                continue
            if meta.is_old(effective_model(sb, s)) and s.id not in include:
                plan.keep.append(s.id)
            if new_sb != old and not (s.model or None):
                plan.pin.append(s.id)  # keeps `scene.model or sb.model` (the fingerprint) as it was (AC30)
                plan.edits[s.id] = {"model": old}

    plan.storyboard_model = {"from": old, "to": new_sb} if new_sb != old else None
    plan.credits = None if price is None else total
    return plan


def apply_to_raw(raw: dict, plan: ChangePlan) -> dict:
    """A copy of the raw storyboard dict with the plan applied; nothing but model/duration is touched."""
    out = copy.deepcopy(raw)
    for sc in out.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        edit = plan.edits.get(sc.get("id"))
        if not edit:
            continue
        if "model" in edit:
            if edit["model"] is None:
                sc.pop("model", None)
            else:
                sc["model"] = edit["model"]
        if "duration" in edit:
            sc["duration"] = edit["duration"]
    if plan.storyboard_model is not None:
        if plan.sb_model_new is None:
            out.pop("model", None)
        else:
            out["model"] = plan.sb_model_new
    return out


def content_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
