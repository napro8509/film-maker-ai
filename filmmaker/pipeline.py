"""Run a storyboard through gflow: project -> keyframes -> clips, resumable.

State lives in <out_dir>/state.json. A scene is marked "submitting" *before*
the paid video call starts, so if the run crashes we know a render may already
have been billed and we do not blindly resubmit it.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .gflow import Gflow, GflowError
from .storyboard import Scene, Storyboard

DONE = "done"
SUBMITTING = "submitting"
FAILED = "failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"project_id": None, "scenes": {}}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
            self.data.setdefault("scenes", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def scene(self, sid: str) -> dict[str, Any]:
        return self.data["scenes"].setdefault(sid, {})

    def update(self, sid: str, **fields: Any) -> None:
        self.scene(sid).update(fields, updated_at=_now())
        self.save()


@dataclass
class RunOptions:
    only: set[str] | None = None  # run only these scene ids
    force: set[str] = field(default_factory=set)  # regenerate even if done
    retry_unknown: bool = False  # resubmit scenes left in "submitting" by a crash
    max_retries: int = 2
    pause: tuple[float, float] = (8.0, 20.0)  # random pause between paid submissions
    fail_fast: bool = False
    keyframes_only: bool = False  # only make the (free) keyframe stills, no paid video
    force_keyframes: set[str] = field(default_factory=set)  # remake these keyframes even if they exist
    should_stop: Callable[[], bool] = lambda: False  # checked between scenes (UI "stop" button)


@dataclass
class RunReport:
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    aborted: str | None = None
    stopped: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed and not self.aborted


class Pipeline:
    def __init__(self, sb: Storyboard, out_dir: Path, gflow: Gflow, log: Callable[[str], None] = print,
                 sleep: Callable[[float], None] = time.sleep):
        self.sb = sb
        self.out = out_dir
        self.g = gflow
        self.log = log
        self.sleep = sleep
        self.state = State(out_dir / "state.json")

    # --- helpers -----------------------------------------------------------

    def clip_path(self, s: Scene) -> Path:
        return self.out / "clips" / f"{s.id}.mp4"

    def keyframe_path(self, s: Scene) -> Path:
        return self.out / "keyframes" / f"{s.id}.png"

    def is_done(self, s: Scene) -> bool:
        st = self.state.scene(s.id)
        return (
            st.get("status") == DONE
            and st.get("fingerprint") == self.sb.scene_fingerprint(s)
            and Path(st.get("video", "")).is_file()
        )

    def has_keyframe(self, s: Scene) -> bool:
        st = self.state.scene(s.id)
        return st.get("keyframe_prompt") == self.sb.image_prompt(s) and self.keyframe_path(s).is_file()

    def scene_status(self, s: Scene) -> str:
        """done | changed | interrupted | failed | todo"""
        st = self.state.scene(s.id)
        if self.is_done(s):
            return "done"
        if st.get("status") == DONE:
            return "changed"
        return {SUBMITTING: "interrupted", FAILED: "failed"}.get(st.get("status"), "todo")

    def ensure_project(self) -> str:
        pid = self.sb.project_id or self.state.data.get("project_id")
        if pid:
            return pid
        self.log(f"Tạo project Flow mới: {self.sb.title}")
        pid = self.g.create_project(self.sb.title)
        if not self.g.dry_run:
            self.state.data["project_id"] = pid
            self.state.save()
        return pid

    def _with_retries(self, what: str, fn: Callable[[], Any], max_retries: int) -> Any:
        attempt = 0
        while True:
            try:
                return fn()
            except GflowError as exc:
                if exc.fatal or not exc.retryable or attempt >= max_retries:
                    raise
                attempt += 1
                wait = 30 * attempt + random.uniform(0, 15)
                self.log(f"  ! {what} lỗi tạm thời (exit {exc.exit_code}); thử lại lần {attempt}/{max_retries} sau {wait:.0f}s")
                self.sleep(wait)

    # --- main --------------------------------------------------------------

    def run(self, opts: RunOptions) -> RunReport:
        report = RunReport()
        (self.out / "clips").mkdir(parents=True, exist_ok=True)
        (self.out / "keyframes").mkdir(parents=True, exist_ok=True)

        scenes = [s for s in self.sb.scenes if not opts.only or s.id in opts.only]
        if opts.keyframes_only:
            return self._run_keyframes(scenes, opts, report)
        todo = []
        for s in scenes:
            st = self.state.scene(s.id)
            if s.id not in opts.force and self.is_done(s):
                report.skipped.append(s.id)
                continue
            if st.get("status") == SUBMITTING and not opts.retry_unknown and s.id not in opts.force:
                msg = ("lần chạy trước bị ngắt khi đang render — có thể đã trừ credit. "
                       "Kiểm tra trên Flow rồi chạy lại với --retry-unknown hoặc --force " + s.id)
                self.log(f"[{s.id}] BỎ QUA: {msg}")
                report.failed[s.id] = msg
                continue
            todo.append(s)

        if report.skipped:
            self.log(f"Đã xong từ trước (bỏ qua): {', '.join(report.skipped)}")
        if not todo:
            self.log("Không còn cảnh nào cần tạo.")
            return report

        try:
            project = self.ensure_project()
        except GflowError as exc:
            report.aborted = str(exc)
            return report
        self.log(f"Project: {project}")

        for idx, s in enumerate(todo):
            if opts.should_stop():
                report.stopped = True
                self.log("Đã dừng theo yêu cầu (các cảnh đã xong vẫn được giữ).")
                break
            if idx > 0 and not self.g.dry_run:
                pause = random.uniform(*opts.pause)
                self.log(f"  (nghỉ {pause:.0f}s giữa các lần gửi)")
                self.sleep(pause)
            self.log(f"[{s.id}] {s.mode}: {s.prompt[:80]}")
            try:
                video = self._render_scene(s, project, opts)
            except GflowError as exc:
                if not self.g.dry_run:
                    self.state.update(s.id, status=FAILED, error=str(exc), exit_code=exc.exit_code)
                report.failed[s.id] = str(exc)
                self.log(f"[{s.id}] LỖI: {exc}")
                if exc.fatal:
                    report.aborted = f"dừng toàn bộ vì lỗi nghiêm trọng ở {s.id} (exit {exc.exit_code})"
                    break
                if opts.fail_fast:
                    report.aborted = f"--fail-fast: dừng ở {s.id}"
                    break
                continue
            report.done.append(s.id)
            self.log(f"[{s.id}] {'(dry-run) sẽ lưu' if self.g.dry_run else 'OK'} -> {video}")
        return report

    def _keyframe(self, s: Scene, project: str, opts: RunOptions, *, remake: bool) -> Path:
        kf = self.keyframe_path(s)
        if self.has_keyframe(s) and not remake:
            self.log(f"  dùng lại keyframe {kf}")
            return kf
        # Images are free on Flow, so retrying them is safe.
        img = self._with_retries(
            "tạo keyframe",
            lambda: self.g.text_to_image(self.sb.image_prompt(s), out=kf, aspect=self.sb.aspect,
                                         model=self.sb.image_model, project=project),
            opts.max_retries,
        )
        if not self.g.dry_run:
            if Path(img) != kf and Path(img).is_file():  # keep the UI's preview path stable
                kf.parent.mkdir(parents=True, exist_ok=True)
                kf.write_bytes(Path(img).read_bytes())
            self.state.update(s.id, keyframe=str(kf), keyframe_prompt=self.sb.image_prompt(s))
        return kf

    def _run_keyframes(self, scenes: list[Scene], opts: RunOptions, report: RunReport) -> RunReport:
        """Make only the keyframe stills (free on Flow) so they can be reviewed before paying for video."""
        todo = []
        for s in scenes:
            if s.mode != "keyframe" or (self.has_keyframe(s) and s.id not in opts.force_keyframes):
                report.skipped.append(s.id)
            else:
                todo.append(s)
        if not todo:
            self.log("Không có keyframe nào cần tạo.")
            return report
        try:
            project = self.ensure_project()
        except GflowError as exc:
            report.aborted = str(exc)
            return report
        for idx, s in enumerate(todo):
            if opts.should_stop():
                report.stopped = True
                self.log("Đã dừng theo yêu cầu.")
                break
            if idx > 0 and not self.g.dry_run:
                self.sleep(random.uniform(*opts.pause))
            self.log(f"[{s.id}] keyframe: {(s.image_prompt or s.prompt)[:80]}")
            try:
                kf = self._keyframe(s, project, opts, remake=True)
            except GflowError as exc:
                report.failed[s.id] = str(exc)
                self.log(f"[{s.id}] LỖI: {exc}")
                if exc.fatal:
                    report.aborted = f"dừng vì lỗi nghiêm trọng ở {s.id} (exit {exc.exit_code})"
                    break
                continue
            report.done.append(s.id)
            self.log(f"[{s.id}] {'(dry-run) sẽ lưu' if self.g.dry_run else 'OK'} -> {kf}")
        return report

    def _render_scene(self, s: Scene, project: str, opts: RunOptions) -> Path:
        dry = self.g.dry_run
        model = s.model or self.sb.model
        out = self.clip_path(s)
        common = dict(aspect=self.sb.aspect, model=model, duration=s.duration, project=project, out=out)

        start_image: Path | None = None
        if s.mode == "keyframe":
            start_image = self._keyframe(s, project, opts, remake=s.id in opts.force_keyframes)
        elif s.mode == "i2v":
            start_image = Path(s.image)  # validated by the storyboard
            if not dry and not start_image.is_file():
                raise GflowError(f"không tìm thấy ảnh {start_image}", exit_code=2)

        if not dry:
            self.state.update(s.id, status=SUBMITTING, fingerprint=self.sb.scene_fingerprint(s), error=None)

        prompt = self.sb.video_prompt(s)
        if start_image is None:
            call = lambda: self.g.text_to_video(prompt, **common)
        else:
            call = lambda: self.g.image_to_video(start_image, prompt, end_image=s.end_image, **common)
        video, media_id = self._with_retries("render video", call, opts.max_retries)

        if not dry:
            if not Path(video).is_file():
                raise GflowError(f"gflow báo thành công nhưng không thấy file {video}", exit_code=1)
            self.state.update(s.id, status=DONE, video=str(video), media_id=media_id)
        return Path(video)
