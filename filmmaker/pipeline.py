"""Run a storyboard through gflow: project -> keyframes -> clips, resumable.

State lives in <out_dir>/state.json. A scene is marked "submitting" *before*
the paid video call starts, so if the run crashes we know a render may already
have been billed and we do not blindly resubmit it.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Collection, Literal

from .gflow import EXIT_NOT_PORTED, Gflow, GflowError
from .storyboard import Character, Scene, Storyboard
from .thumbnail import render_cover

DONE = "done"
SUBMITTING = "submitting"
FAILED = "failed"
MANUAL_CLIP_MIN_BYTES = 10_000  # a hand-downloaded clips/<id>.mp4 smaller than this is not adopted


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
    after_scene: Callable[[str], None] = lambda _sid: None  # a clip was rendered and saved (e.g. back it up)
    # checked right before each paid video submission; a returned reason means "do not send this scene"
    # (e.g. its model was retired, even if the run started before midnight of the retirement day)
    refuse: Callable[[Scene], str | None] = lambda _s: None


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

    @property
    def i2v_unsupported(self) -> bool:
        return bool(self.state.data.get("i2v_unsupported"))

    def has_keyframe(self, s: Scene) -> bool:
        st = self.state.scene(s.id)
        return (
            st.get("keyframe_prompt") == self.sb.image_prompt(s)
            and (st.get("keyframe_refs") or []) == self.sb.refs_signature(s)
            and self.keyframe_path(s).is_file()
        )

    def scene_status(self, s: Scene) -> str:
        """done | changed | interrupted | failed | todo"""
        st = self.state.scene(s.id)
        if self.is_done(s):
            return "done"
        if st.get("status") == DONE:
            return "changed"
        return {SUBMITTING: "interrupted", FAILED: "failed"}.get(st.get("status"), "todo")

    def has_manual_clip(self, s: Scene) -> bool:
        """clips/<id>.mp4 exists and is big enough to be a real clip (no state is written)."""
        clip = self.clip_path(s)
        return clip.is_file() and clip.stat().st_size >= MANUAL_CLIP_MIN_BYTES

    def scene_group(self, s: Scene) -> Literal["done", "interrupted", "todo"]:
        """done (has a valid clip) | interrupted (left in "submitting") | todo (todo/failed/changed)"""
        if self.is_done(s):
            return "done"
        if self.state.scene(s.id).get("status") == SUBMITTING:
            return "interrupted"
        return "todo"

    def video_plan(self, only: Collection[str] | None, force: Collection[str],
                   retry_unknown: bool) -> list[tuple[Scene, str]]:
        """What a video run would do with each scene: done | adopt | blocked | send. No side effects.

        `only` None or empty means every scene. Same rules, in the same order, as `run`."""
        plan: list[tuple[Scene, str]] = []
        for s in self.sb.scenes:
            if only and s.id not in only:
                continue
            submitting = self.state.scene(s.id).get("status") == SUBMITTING
            if s.id not in force and self.is_done(s):
                kind = "done"
            elif submitting and s.id not in force and self.has_manual_clip(s):
                kind = "adopt"
            elif submitting and not retry_unknown and s.id not in force:
                kind = "blocked"
            else:
                kind = "send"
            plan.append((s, kind))
        return plan

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
        for s, kind in self.video_plan(opts.only, opts.force, opts.retry_unknown):
            if kind == "done":
                report.skipped.append(s.id)
                continue
            if kind == "adopt":
                if self._adopt_manual_clip(s):
                    report.skipped.append(s.id)
                    continue
                # the file vanished between the two checks: decide as if there were no clip
                kind = "send" if opts.retry_unknown else "blocked"
            if kind == "blocked":
                msg = ("lần chạy trước bị ngắt khi đang render — có thể đã trừ credit. Nếu video đã có trên Flow, "
                       f"tải về và lưu thành {self.clip_path(s)} rồi chạy lại; nếu chưa có, chạy lại với "
                       "--retry-unknown hoặc --force " + s.id)
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

        sent = False  # pause only between real submissions, not after a scene `refuse` skipped
        for s in todo:
            if opts.should_stop():
                report.stopped = True
                self.log("Đã dừng theo yêu cầu (các cảnh đã xong vẫn được giữ).")
                break
            if sent and not self.g.dry_run:
                pause = random.uniform(*opts.pause)
                self.log(f"  (nghỉ {pause:.0f}s giữa các lần gửi)")
                self.sleep(pause)
            # Checked after the pause, which may cross midnight. Not re-checked inside _render_scene
            # (keyframe, retries, i2v->t2v fallback): a long scene can still cross it; the next run is blocked.
            reason = opts.refuse(s)
            if reason:
                report.failed[s.id] = reason
                self.log(f"[{s.id}] KHÔNG GỬI: {reason}")
                continue
            sent = True
            self.log(f"[{s.id}] {s.mode}: {s.prompt[:80]}")
            try:
                video = self._render_scene(s, project, opts)
            except GflowError as exc:
                if not self.g.dry_run:
                    # A request Flow may have billed stays "submitting" so it is not resent blindly.
                    self.state.update(s.id, status=SUBMITTING if exc.spent else FAILED, error=str(exc),
                                      exit_code=exc.exit_code)
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
            if not self.g.dry_run:
                opts.after_scene(s.id)
        return report

    def _keyframe(self, s: Scene, project: str, opts: RunOptions, *, remake: bool) -> Path:
        kf = self.keyframe_path(s)
        if self.has_keyframe(s) and not remake:
            self.log(f"  dùng lại keyframe {kf}")
            return kf
        prompt = self.sb.image_prompt(s)
        refs = self.sb.scene_refs(s)
        kw = dict(out=kf, aspect=self.sb.aspect, model=self.sb.image_model, project=project)
        img = None
        # Images are free on Flow, so retrying them (or falling back) is safe.
        if refs:
            self.log(f"  ảnh tham chiếu nhân vật: {', '.join(n for n, _ in refs)}")
            try:
                img = self._with_retries("tạo keyframe theo ảnh nhân vật",
                                         lambda: self.g.image_to_image(self.sb.for_flow(prompt), [p for _, p in refs], **kw),
                                         opts.max_retries)
            except GflowError as exc:
                if exc.fatal and exc.exit_code != EXIT_NOT_PORTED:
                    raise
                self.log(f"  ! Không tạo được ảnh theo ảnh nhân vật ({exc}). Tạo ảnh chỉ từ mô tả chữ.")
        if img is None:
            img = self._with_retries("tạo keyframe", lambda: self.g.text_to_image(self.sb.for_flow(prompt), **kw), opts.max_retries)
        if not self.g.dry_run:
            if Path(img) != kf and Path(img).is_file():  # keep the UI's preview path stable
                kf.parent.mkdir(parents=True, exist_ok=True)
                kf.write_bytes(Path(img).read_bytes())
            self.state.update(s.id, keyframe=str(kf), keyframe_prompt=prompt,
                              keyframe_refs=self.sb.refs_signature(s))
        return kf

    def generate_portraits(self, chars: list[tuple[Character, Path]], opts: RunOptions) -> RunReport:
        """Make reference images for characters (free on Flow). `chars` pairs each character with its output file."""
        report = RunReport()
        if not chars:
            self.log("Không có nhân vật nào cần tạo ảnh.")
            return report
        try:
            project = self.ensure_project()
        except GflowError as exc:
            report.aborted = str(exc)
            return report
        for idx, (c, dest) in enumerate(chars):
            if opts.should_stop():
                report.stopped = True
                self.log("Đã dừng theo yêu cầu.")
                break
            if idx > 0 and not self.g.dry_run:
                self.sleep(random.uniform(*opts.pause))
            prompt = self.sb.portrait_prompt(c)
            self.log(f"[{c.name}] ảnh nhân vật: {prompt[:100]}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                img = self._with_retries(
                    "tạo ảnh nhân vật",
                    lambda: self.g.text_to_image(self.sb.for_flow(prompt), out=dest, aspect=self.sb.aspect,
                                                 model=self.sb.image_model, project=project),
                    opts.max_retries)
            except GflowError as exc:
                report.failed[c.name] = str(exc)
                self.log(f"[{c.name}] LỖI: {exc}")
                if exc.fatal:
                    report.aborted = f"dừng vì lỗi nghiêm trọng (exit {exc.exit_code})"
                    break
                continue
            if not self.g.dry_run and Path(img) != dest and Path(img).is_file():
                dest.write_bytes(Path(img).read_bytes())
            report.done.append(c.name)
            self.log(f"[{c.name}] OK -> {dest}")
        return report

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

    def _text_video(self, s: Scene, prompt: str, common: dict, opts: RunOptions) -> tuple[Path, str | None, str]:
        """Text-to-video, with the characters' reference images (r2v) when there are any."""
        refs = self.sb.scene_refs(s)
        if refs and not self.state.data.get("r2v_unsupported"):
            self.log(f"  ảnh tham chiếu nhân vật: {', '.join(n for n, _ in refs)}")
            try:
                video, media_id = self._with_retries(
                    "render video",
                    lambda: self.g.reference_to_video(self.sb.for_flow(prompt), [p for _, p in refs], **common), opts.max_retries)
                return video, media_id, "r2v"
            except GflowError as exc:
                if exc.exit_code != EXIT_NOT_PORTED:
                    raise
                # Refused before submit, so no credits were spent.
                self.log("  ! Tài khoản Flow này chưa hỗ trợ video theo ảnh tham chiếu qua gflow (chưa trừ credit). "
                         "Dùng Chữ → Video thường.")
                self.state.data["r2v_unsupported"] = True
                self.state.save()
        video, media_id = self._with_retries("render video", lambda: self.g.text_to_video(self.sb.for_flow(prompt), **common),
                                             opts.max_retries)
        return video, media_id, "t2v"

    def _render_text_only(self, s: Scene, common: dict, opts: RunOptions) -> Path:
        prompt = self.sb.text_only_prompt(s)
        self.log(f"  (Chữ → Video) {prompt[:120]}")
        if not self.g.dry_run:
            self.state.update(s.id, status=SUBMITTING, fingerprint=self.sb.scene_fingerprint(s), error=None)
        video, media_id, how = self._text_video(s, prompt, common, opts)
        if not self.g.dry_run:
            if not Path(video).is_file():
                raise GflowError(f"gflow báo thành công nhưng không thấy file {video}", exit_code=1)
            self.state.update(s.id, status=DONE, video=str(video), media_id=media_id, rendered_as=how)
        return Path(video)

    def _render_scene(self, s: Scene, project: str, opts: RunOptions) -> Path:
        dry = self.g.dry_run
        model = s.model or self.sb.model
        out = self.clip_path(s)
        common = dict(aspect=self.sb.aspect, model=model, duration=s.duration, project=project, out=out)

        start_image: Path | None = None
        ui = self.i2v_unsupported and self.g.ui_available()  # gflow can't: drive the Flow chat page instead
        if s.mode == "keyframe" and self.i2v_unsupported and self.sb.fallback_t2v and not ui:
            return self._render_text_only(s, common, opts)
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
            video, media_id, how = self._text_video(s, prompt, common, opts)
            if not dry:
                if not Path(video).is_file():
                    raise GflowError(f"gflow báo thành công nhưng không thấy file {video}", exit_code=1)
                self.state.update(s.id, status=DONE, video=str(video), media_id=media_id, rendered_as=how)
            return Path(video)
        if ui:
            return self._render_via_ui(s, start_image, prompt, common)
        call = lambda: self.g.image_to_video(start_image, self.sb.for_flow(prompt), end_image=s.end_image, **common)
        try:
            video, media_id = self._with_retries("render video", call, opts.max_retries)
        except GflowError as exc:
            if exc.exit_code != EXIT_NOT_PORTED or start_image is None:
                raise
            # Refused before submit, so no credits were spent: forget the "submitting" mark.
            if not dry:
                self.state.update(s.id, status=None, fingerprint=None)
            self.log(f"  ! gflow từ chối Ảnh → Video trước khi gửi (chưa trừ credit): {exc}")
            self.state.data["i2v_unsupported"] = True
            self.state.data["i2v_error"] = str(exc)
            self.state.save()
            if self.g.ui_available():
                self.log("  → Làm Ảnh → Video qua giao diện chat của Flow (như bấm “+” rồi “Add to prompt” bằng tay).")
                return self._render_via_ui(s, start_image, prompt, common)
            if s.mode == "i2v":
                raise GflowError(
                    "tài khoản Flow của bạn (giao diện mới flow.google.com) chưa hỗ trợ tạo video từ ảnh qua gflow. "
                    "Đổi cảnh này sang kiểu “Chữ → Video” và mô tả ảnh trong prompt.",
                    exit_code=2, payload=exc.payload) from exc  # scene-specific: keep rendering the others
            if not self.sb.fallback_t2v:
                raise GflowError(
                    "gflow chưa làm được Ảnh → Video trên tài khoản Flow này nên đã dừng (chưa trừ credit). "
                    "Cập nhật gflow-cli rồi bấm “Thử lại Ảnh → Video”, hoặc bật “Tự chuyển sang Chữ → Video” "
                    "trong tab Nhân vật & phong cách.",
                    exit_code=EXIT_NOT_PORTED, payload=exc.payload) from exc  # fatal: stop the run
            self.log("  → Đã bật “Tự chuyển sang Chữ → Video”: render cảnh này và các cảnh sau bằng Chữ → Video.")
            return self._render_text_only(s, common, opts)

        if not dry:
            if not Path(video).is_file():
                raise GflowError(f"gflow báo thành công nhưng không thấy file {video}", exit_code=1)
            self.state.update(s.id, status=DONE, video=str(video), media_id=media_id, rendered_as=None)
        return Path(video)

    def _adopt_manual_clip(self, s: Scene) -> bool:
        """A render was interrupted after Flow billed it, and the user downloaded the clip from Flow
        by hand into clips/<id>.mp4: take that file instead of rendering (and paying) again."""
        clip = self.clip_path(s)
        if not self.has_manual_clip(s):
            return False
        self.log(f"[{s.id}] dùng video bạn đã tải về: {clip}")
        self.state.update(s.id, status=DONE, video=str(clip), fingerprint=self.sb.scene_fingerprint(s),
                          error=None, rendered_as="manual")
        return True

    def _render_via_ui(self, s: Scene, start_image: Path, prompt: str, common: dict) -> Path:
        """Image-to-video by driving the Flow chat page (flow_ui.py). Never retried: Flow may have billed it."""
        if s.end_image:
            self.log("  ! Ảnh kết thúc chưa dùng được qua giao diện chat; chỉ dùng ảnh bắt đầu.")
        if not self.g.dry_run:
            self.state.update(s.id, status=SUBMITTING, fingerprint=self.sb.scene_fingerprint(s), error=None)
        video, media_id = self.g.ui_image_to_video(start_image, self.sb.for_flow(prompt), **common)
        if not self.g.dry_run:
            if not Path(video).is_file():
                raise GflowError(f"không thấy file video {video}", exit_code=1)
            other = next((sid for sid, st in self.state.data["scenes"].items()
                          if sid != s.id and media_id and st.get("media_id") == media_id), None)
            if other:  # never accept a clip that already belongs to another scene
                raise GflowError(f"video tải về trùng với cảnh {other} (media {media_id}); video mới có thể vẫn "
                                 "đang tạo trên Flow — hãy kiểm tra trên Flow", exit_code=42,
                                 payload={"error": {"spent": True}})
            self.state.update(s.id, status=DONE, video=str(video), media_id=media_id, rendered_as="flow-ui")
        return Path(video)

    # --- thumbnail -------------------------------------------------------------

    def thumbnail_paths(self) -> tuple[Path, Path]:
        """(picture from Flow, finished cover with the title)."""
        return self.out / "thumbnail_raw.png", self.out / "thumbnail.jpg"

    def thumbnail_sig(self) -> dict:
        refs = [f"{n}:{hashlib.sha256(p.read_bytes()).hexdigest()[:12]}" for n, p in self.sb.thumbnail_refs()]
        return {"prompt": self.sb.thumbnail_prompt(), "refs": refs, "aspect": self.sb.aspect}

    def has_thumbnail_image(self) -> bool:
        raw, _ = self.thumbnail_paths()
        return raw.is_file() and self.state.data.get("thumbnail") == self.thumbnail_sig()

    def make_thumbnail(self, opts: RunOptions, *, remake: bool = False) -> Path:
        """Cover picture on Flow (free; reused while its prompt is unchanged) + the title printed on it."""
        raw, final = self.thumbnail_paths()
        if remake or not self.has_thumbnail_image():
            project = self.ensure_project()
            prompt = self.sb.thumbnail_prompt()
            refs = self.sb.thumbnail_refs()
            self.log(f"[thumbnail] ảnh bìa: {prompt[:100]}")
            kw = dict(out=raw, aspect=self.sb.aspect, model=self.sb.image_model, project=project)
            img = None
            if refs:
                self.log(f"  ảnh tham chiếu nhân vật: {', '.join(n for n, _ in refs)}")
                try:
                    img = self._with_retries("tạo ảnh bìa theo ảnh nhân vật",
                                             lambda: self.g.image_to_image(self.sb.for_flow(prompt), [p for _, p in refs], **kw),
                                             opts.max_retries)
                except GflowError as exc:
                    if exc.fatal and exc.exit_code != EXIT_NOT_PORTED:
                        raise
                    self.log(f"  ! Không tạo được ảnh theo ảnh nhân vật ({exc}). Tạo ảnh chỉ từ mô tả chữ.")
            if img is None:
                img = self._with_retries("tạo ảnh bìa", lambda: self.g.text_to_image(self.sb.for_flow(prompt), **kw),
                                         opts.max_retries)
            if self.g.dry_run:
                self.log(f"[thumbnail] (dry-run) sẽ lưu -> {final}")
                return final
            if Path(img) != raw and Path(img).is_file():
                raw.parent.mkdir(parents=True, exist_ok=True)
                raw.write_bytes(Path(img).read_bytes())
            self.state.data["thumbnail"] = self.thumbnail_sig()
            self.state.save()
        else:
            self.log("[thumbnail] dùng lại ảnh bìa đã có, chỉ in lại tiêu đề")
        render_cover(raw, final, aspect=self.sb.aspect, text=self.sb.thumbnail.text or self.sb.title, log=self.log)
        self.log(f"[thumbnail] OK -> {final}")
        return final


def send_list(pipe: Pipeline, only: Collection[str] | None, force: Collection[str],
              retry_unknown: bool) -> list[Scene]:
    """Scenes a video run would actually submit to Flow (kind "send" of `Pipeline.video_plan`)."""
    return [s for s, kind in pipe.video_plan(only, force, retry_unknown) if kind == "send"]
