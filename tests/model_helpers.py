"""Helpers for the model-retirement tests: projects with scenes in every group, fixed dates, override files.

Scene kinds for `set_scene`:
  done        state DONE + current fingerprint + a video file      -> group "done"
  changed     state DONE + an old fingerprint + a video file       -> group "todo" (scene_status "changed")
  interrupted state SUBMITTING                                     -> group "interrupted"
  failed      state FAILED                                         -> group "todo"
  todo        no state                                             -> group "todo"
`manual_clip=True` also writes clips/<id>.mp4 of MANUAL_CLIP_BYTES (a valid hand-downloaded clip).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from filmmaker import model_meta
from filmmaker.gflow import Gflow
from filmmaker.pipeline import DONE, FAILED, SUBMITTING, Pipeline
from filmmaker.storyboard import load_storyboard

MANUAL_CLIP_BYTES = 20_000


def set_today(monkeypatch, y: int, m: int, d: int) -> None:
    monkeypatch.setattr(model_meta, "today", lambda: date(y, m, d))


def storyboard_dict(model: str | None = "veo-fast", scenes: list[dict] | None = None, title: str = "Phim thử") -> dict:
    """`scenes` items: {"id": ..., "model": ..., "duration": ...}; mode t2v and a prompt are filled in."""
    scenes = scenes or [{"id": "s01"}]
    out = {"title": title, "aspect": "16:9"}
    if model is not None:
        out["model"] = model
    out["scenes"] = [{"mode": "t2v", "prompt": f"scene {sc['id']}", **sc} for sc in scenes]
    return out


def write_project(workspace: Path, pid: str, model: str | None = "veo-fast",
                  scenes: list[dict] | None = None) -> Path:
    """A web-UI project folder (<workspace>/<pid>/storyboard.yaml). Returns the storyboard path."""
    d = workspace / pid
    (d / "assets").mkdir(parents=True, exist_ok=True)
    p = d / "storyboard.yaml"
    p.write_text(yaml.safe_dump(storyboard_dict(model, scenes, title=pid), allow_unicode=True, sort_keys=False),
                 encoding="utf-8")
    return p


def ui_out_dir(sb_path: Path) -> Path:
    """Same layout as the server and the CLI: <dir>/output/<stem>."""
    return sb_path.parent / "output" / sb_path.stem


def pipeline(sb_path: Path, out: Path | None = None) -> Pipeline:
    return Pipeline(load_storyboard(sb_path), out or ui_out_dir(sb_path), Gflow(dry_run=True, log=lambda *_: None),
                    log=lambda *_: None)


def set_scene(sb_path: Path, sid: str, kind: str, *, manual_clip: bool = False, out: Path | None = None) -> Pipeline:
    pipe = pipeline(sb_path, out)
    s = next(x for x in pipe.sb.scenes if x.id == sid)
    if manual_clip:
        clip = pipe.clip_path(s)
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.write_bytes(b"\0" * MANUAL_CLIP_BYTES)
    if kind in ("done", "changed"):
        video = pipe.out / "rendered" / f"{sid}.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"\0" * MANUAL_CLIP_BYTES)
        fp = pipe.sb.scene_fingerprint(s) if kind == "done" else "old-fingerprint"
        pipe.state.update(sid, status=DONE, video=str(video), fingerprint=fp)
    elif kind == "interrupted":
        pipe.state.update(sid, status=SUBMITTING, fingerprint=pipe.sb.scene_fingerprint(s))
    elif kind == "failed":
        pipe.state.update(sid, status=FAILED, error="boom")
    elif kind != "todo":
        raise ValueError(kind)
    return pipe


def four_groups(workspace: Path, pid: str = "film", model: str | None = "veo-fast") -> Path:
    """s01 done, s02 interrupted, s03 changed, s04 todo; all on the storyboard model."""
    p = write_project(workspace, pid, model, [{"id": "s01"}, {"id": "s02"}, {"id": "s03"}, {"id": "s04"}])
    set_scene(p, "s01", "done")
    set_scene(p, "s02", "interrupted")
    set_scene(p, "s03", "changed")
    return p


def write_override(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
