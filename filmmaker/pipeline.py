"""Run a storyboard through gflow: project -> keyframes -> clips, resumable.

State lives in <out_dir>/state.json. A scene is marked "submitting" *before*
the paid video call starts, so if the run crashes we know a render may already
have been billed and we do not blindly resubmit it.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Collection, Literal

from . import durations as dur
from . import flow_caps, manual_clip, model_meta
from . import progress as prog
from .gflow import EXIT_NOT_PORTED, Gflow, GflowError
from .flow_ui import character_ref
from .storyboard import MAX_REFS, Character, Location, RefItem, Scene, Storyboard, file_sha  # noqa: F401
from .thumbnail import render_cover

DONE = "done"
SUBMITTING = "submitting"
FAILED = "failed"
MANUAL_CLIP_MIN_BYTES = manual_clip.MIN_BYTES  # a hand-downloaded clips/<id>.mp4 smaller than this is not adopted
# State of clips/<id>.mp4 for a scene left in "submitting" (see Pipeline.manual_clip_state).
ManualClip = Literal["ok", "unverified", "stale", "invalid", "unchecked"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_UNSET: Any = object()


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
    # between free image generations (portraits, keyframes); None = a quarter of `pause`
    image_pause: tuple[float, float] | None = None
    fail_fast: bool = False
    keyframes_only: bool = False  # only make the (free) keyframe stills, no paid video
    force_keyframes: set[str] = field(default_factory=set)  # remake these keyframes even if they exist
    should_stop: Callable[[], bool] = lambda: False  # checked between scenes (UI "stop" button)
    after_scene: Callable[[str], None] = lambda _sid: None  # a clip was rendered and saved (e.g. back it up)
    # checked right before each paid video submission; a returned reason means "do not send this scene"
    # (e.g. its model was retired, even if the run started before midnight of the retirement day)
    refuse: Callable[[Scene], str | None] = lambda _s: None
    # web: only resend "submitting" scenes whose id is here; None = CLI rules (force/retry_unknown)
    confirm: set[str] | None = None


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
                 sleep: Callable[[float], None] = time.sleep, progress: prog.Emit | None = None):
        self.sb = sb
        # Absolute: state.json stores clip/keyframe paths under it, and a relative --out would make them relative
        # to wherever the CLI ran (the app, running elsewhere, then sees the clips as missing: "changed").
        self.out = Path(out_dir).resolve()
        self.g = gflow
        self.log = log
        self.sleep = sleep
        # Per-item progress for the web UI (keys char:<name>, kf:<sid>, vid:<sid>); a no-op without `progress`.
        self.rep = prog.Reporter(progress)
        if progress is not None:
            self.g.on_event = self.rep.gflow_event
        self.state = State(out_dir / "state.json")
        if not gflow.dry_run:
            # What this account cannot do through gflow was found out in an earlier project: skip the failing try.
            for k, v in flow_caps.load(gflow.binary, gflow.profile).items():
                self.state.data.setdefault(k, v)
        self._flow_media: dict[str, dict[str, str]] = {}  # project -> {sha256 of file: media id}
        self._no_media_refs: set[str] = set()  # projects where Flow refused media-id references (send files)
        self._dry_kf: set[str] = set()  # dry-run: keyframes this run "made" already (so the next scene can chain)
        self._no_flow_chars = False  # the Flow Characters page failed this run: portraits go back to plain t2i

    def _sha(self, path: Path) -> str | None:
        try:
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError:
            return None

    def flow_refs(self, refs: list[tuple[str, Path]], project: str) -> list[Path | str]:
        """Reference images for i2i: the media id when the same picture is already in the Flow project
        (e.g. a character portrait made there), so Flow reuses it instead of getting a duplicate upload."""
        if project not in self._flow_media:
            known: dict[str, str] = {}
            # Prefer the image Flow generated (has a prompt) over a later re-upload of the same file.
            rows = sorted(self.g.catalog_images(project), key=lambda r: not r.get("prompt"))
            for row in rows:
                sha = self._sha(Path(row["local_path"]))
                if sha:
                    known.setdefault(sha, row["media_id"])
            self._flow_media[project] = known
        known = self._flow_media[project]
        out: list[Path | str] = []
        for name, p in refs:
            mid = known.get(self._sha(p) or "")
            if mid:
                self.log(f"  {name}: dùng lại ảnh đã có trên Flow ({mid[:8]}…), không tải lên lại")
            out.append(mid or p)
        return out

    def _image_with_refs(self, what: str, prompt: str, refs: list[tuple[str, Path]], project: str,
                         kw: dict, opts: RunOptions, *, log_line: str | None = None) -> Path | None:
        """i2i with the characters' pictures; None when Flow can't do it here (the caller makes a text-only image).

        The migrated Flow frontend (flow.google.com) refuses some media ids as references (exit 36, gflow #639)
        but takes local files, so on that error the request is sent again with the files. On the agent-only
        composer gflow refuses i2i altogether (#799): then the Flow chat page is driven instead (flow_ui i2i),
        and the project remembers it so the next images go there straight away.

        `log_line` None logs the usual "ảnh tham chiếu nhân vật" line; "" logs nothing (the caller already did)."""
        if log_line is None:
            self.log(f"  ảnh tham chiếu nhân vật: {', '.join(n for n, _ in refs)}")
        elif log_line:
            self.log(log_line)
        files = [p for _, p in refs]
        # Say which picture is whom (Nano Banana follows labelled references better); not part of any fingerprint.
        order = ["first", "second", "third", "fourth"]
        ui_prompt = ("Reference images: " + "; ".join(
            f"the {order[i] if i < len(order) else str(i + 1) + 'th'} is {n} (keep {n}'s face, hair and clothes exactly)"
            for i, (n, _) in enumerate(refs)) + ". " + prompt)
        # gflow's own i2i keeps the plain prompt (golden argv); the Flow chat page gets the labels
        if self.state.data.get("i2i_ui") and self.g.ui_available():
            return self._image_via_ui(what, ui_prompt, self.ui_refs(refs, project), kw)
        ref_args = files if project in self._no_media_refs else self.flow_refs(refs, project)
        while True:
            try:
                return self._with_retries(what, lambda: self.g.image_to_image(self.sb.for_flow(prompt), ref_args, **kw),
                                          opts.max_retries)
            except GflowError as exc:
                if exc.fatal and exc.exit_code != EXIT_NOT_PORTED:
                    raise
                if exc.exit_code == EXIT_NOT_PORTED and ref_args != files:
                    self.log("  Flow chưa nhận ảnh tham chiếu theo mã ảnh trên Flow; gửi lại bằng file ảnh nhân vật")
                    self._no_media_refs.add(project)
                    ref_args = files
                    continue
                if exc.exit_code == EXIT_NOT_PORTED and self.g.ui_available():
                    self.log("  gflow chưa tạo được ảnh theo ảnh nhân vật trên giao diện Flow mới; "
                             "chuyển sang tạo qua giao diện chat của Flow (miễn phí).")
                    self.state.data["i2i_ui"] = True
                    self.state.save()
                    self._remember(i2i_ui=True)
                    return self._image_via_ui(what, ui_prompt, self.ui_refs(refs, project), kw)
                self.log(f"  ! Không tạo được ảnh theo ảnh nhân vật ({exc}). Tạo ảnh chỉ từ mô tả chữ.")
                return None

    def flow_character(self, name: str, project: str, image: Path | None = None) -> dict[str, Any] | None:
        """The Flow character made for `name` in `project` (state.json "flow_characters"), or None. With `image`,
        only while that file is still the picture the character was made with (a replaced picture means the
        storyboard no longer matches the Flow character)."""
        fc = (self.state.data.get("flow_characters") or {}).get(name.strip())
        if not fc or fc.get("project") != project or not fc.get("flow_name"):
            return None
        if image is not None and (not fc.get("sha") or fc["sha"] != self._sha(image)):
            return None
        return fc

    def ui_refs(self, refs: list[tuple[str, Path]], project: str) -> list[Path | str]:
        """References for the Flow chat page: a character made on Flow's Characters page is attached by its
        name (Flow keeps its face itself); anything else is uploaded as a picture."""
        out: list[Path | str] = []
        for name, p in refs:
            fc = self.flow_character(name, project, p)
            if fc:
                self.log(f"  {name}: dùng nhân vật Flow “{fc['flow_name']}” (không tải ảnh lên lại)")
                out.append(character_ref(fc["flow_name"]))
            else:
                out.append(p)
        return out

    def _image_via_ui(self, what: str, prompt: str, files: list[Path | str], kw: dict) -> Path | None:
        self.log(f"  → {what} qua giao diện chat của Flow")
        try:
            return self.g.ui_image_to_image(self.sb.for_flow(prompt), files, **kw)
        except GflowError as exc:
            self.log(f"  ! Không tạo được ảnh theo ảnh nhân vật qua giao diện chat ({exc}). Tạo ảnh chỉ từ mô tả chữ.")
            return None

    def _remember(self, **flags: Any) -> None:
        if not self.g.dry_run:
            flow_caps.remember(self.g.binary, self.g.profile, **flags)

    def _image_pause(self, opts: RunOptions) -> float:
        lo, hi = opts.image_pause or (opts.pause[0] / 4, opts.pause[1] / 4)
        return random.uniform(lo, hi)

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

    # --- continuity (start a keyframe from the previous scene's keyframe) -------------------------------

    def continuity_check(self, s: Scene, planned: Collection[str] = ()) -> tuple[Scene | None, str]:
        """(previous scene whose keyframe the keyframe of `s` starts from, or None; why not, in Vietnamese).
        The reason is "" when `s` is not a keyframe scene with a location. `planned` = scenes whose keyframe this
        (dry) run makes before `s`. The previous scene is the one right before `s` in the storyboard (not --only)."""
        loc = self.sb.scene_location(s)
        if s.mode != "keyframe" or loc is None:
            return None, ""
        if self.sb.continuity is False:
            return None, "phim tắt nối"
        if s.continue_previous is False:
            return None, "cảnh này tắt nối"
        idx = next((i for i, x in enumerate(self.sb.scenes) if x.id == s.id), 0)
        if idx == 0:
            return None, "cảnh đầu"
        prev = self.sb.scenes[idx - 1]
        if prev.mode != "keyframe":
            return None, "cảnh trước không phải keyframe"
        if self.sb.scene_location(prev) is not loc:
            return None, "cảnh trước khác bối cảnh"
        if not (prev.id in planned or self.has_keyframe(prev)):
            return None, f"chưa có keyframe {prev.id} mới nhất"
        return prev, ""

    def continuity_source(self, s: Scene) -> Scene | None:
        return self.continuity_check(s)[0]

    def keyframe_ref_items(self, s: Scene, planned: Collection[str] = ()) -> tuple[list[RefItem], Scene | None, str]:
        """(reference images of the keyframe of `s` in sending order, chained previous scene or None, reason)."""
        src, why = self.continuity_check(s, planned)
        prev = (src.id, self.keyframe_path(src)) if src is not None else None
        return self.sb.scene_ref_items(s, prev=prev), src, why

    def continuity_outdated(self, s: Scene, src: Scene | None = _UNSET) -> bool:
        """The keyframe of `s` could start from a newer keyframe of the previous scene than the one it used
        (or used none). `src` = `continuity_source(s)` when already known. A soft hint: never changes the status."""
        if not self.has_keyframe(s):
            return False
        if src is _UNSET:
            src = self.continuity_source(s)
        if src is None:
            return False
        st = self.state.scene(s.id)
        return st.get("keyframe_from") != src.id or st.get("keyframe_from_sha") != file_sha(self.keyframe_path(src))

    def _log_refs(self, s: Scene, items: list[RefItem]) -> None:
        """The reference images of a scene with a location (scenes without one keep the old log line)."""
        if self.sb.scene_location(s) is None:
            return
        if items:
            self.log(f"  ảnh tham chiếu: {', '.join(it.label for it in items)}")
        for name in self.sb.dropped_char_refs(s):
            self.log(f"  bỏ ảnh {name}: hết chỗ (tối đa {MAX_REFS})")

    def scene_status(self, s: Scene) -> str:
        """done | changed | interrupted | failed | todo"""
        st = self.state.scene(s.id)
        if self.is_done(s):
            return "done"
        if st.get("status") == DONE:
            return "changed"
        return {SUBMITTING: "interrupted", FAILED: "failed"}.get(st.get("status"), "todo")

    def is_legacy_interrupted(self, s: Scene) -> bool:
        """Left in "submitting" by an older version (no usable `submitted_at`): the app cannot tell whether
        clips/<id>.mp4 is the new clip or an old one. The only definition of this case."""
        st = self.state.scene(s.id)
        return st.get("status") == SUBMITTING and manual_clip.parse_stamp(st.get("submitted_at")) is None

    def manual_clip_state(self, s: Scene) -> ManualClip | None:
        """None (not "submitting", or no clips/<id>.mp4) | unchecked (no ffprobe) | invalid (cannot be read
        in full) | unverified (legacy interrupted scene) | stale (older than the submission) | ok (will be
        adopted). In this order of priority. No state is written."""
        if self.state.scene(s.id).get("status") != SUBMITTING:
            return None
        clip = self.clip_path(s)
        verdict = manual_clip.check_file(clip)
        if verdict == "missing":
            return None
        if verdict in ("unchecked", "invalid"):
            return verdict
        if self.is_legacy_interrupted(s):
            return "unverified"
        sent = manual_clip.parse_stamp(self.state.scene(s.id).get("submitted_at"))
        try:
            mtime = clip.stat().st_mtime
        except OSError:
            return None
        return "stale" if mtime < sent else "ok"

    def has_manual_clip(self, s: Scene) -> bool:
        """The scene is "submitting" and clips/<id>.mp4 is a complete clip saved after the submission,
        i.e. it will be adopted (no state is written)."""
        return self.manual_clip_state(s) == "ok"

    def scene_group(self, s: Scene) -> Literal["done", "interrupted", "todo"]:
        """done (has a valid clip) | interrupted (left in "submitting") | todo (todo/failed/changed)"""
        if self.is_done(s):
            return "done"
        if self.state.scene(s.id).get("status") == SUBMITTING:
            return "interrupted"
        return "todo"

    def _plan_kind(self, s: Scene, force: Collection[str], retry_unknown: bool, confirm: Collection[str] | None,
                   *, allow_adopt: bool = True) -> str:
        """done | adopt | blocked | send for one scene (the rule of `video_plan`). No side effects."""
        if self.state.scene(s.id).get("status") != SUBMITTING:
            return "done" if (s.id not in force and self.is_done(s)) else "send"
        mc = self.manual_clip_state(s)
        if allow_adopt and mc == "ok":
            return "adopt"  # even when forced: it is almost surely the clip already paid for
        if confirm is not None:  # web: only what the user ticked, whatever force/retry_unknown say
            return "send" if s.id in confirm else "blocked"
        if s.id in force:
            return "send"
        # --retry-unknown never resends a legacy interrupted scene that has a clip (use --adopt/--force).
        # Without ffprobe that clip is "unchecked", not "unverified", so it IS resent (as with no clip, AC8).
        if retry_unknown and mc != "unverified":
            return "send"
        return "blocked"

    def video_plan(self, only: Collection[str] | None, force: Collection[str],
                   retry_unknown: bool, confirm: Collection[str] | None = None) -> list[tuple[Scene, str]]:
        """What a video run would do with each scene: done | adopt | blocked | send. No side effects.

        `only` None or empty means every scene. `confirm` not None (web) means a "submitting" scene is sent
        only when its id is in it. Same rules, in the same order, as `run`."""
        plan: list[tuple[Scene, str]] = []
        for s in self.sb.scenes:
            if only and s.id not in only:
                continue
            plan.append((s, self._plan_kind(s, force, retry_unknown, confirm)))
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
                self.rep.retry_wait(attempt + 1)  # the active item waits again (no-op for e.g. the cover image)
                self.sleep(wait)

    # --- main --------------------------------------------------------------

    def run(self, opts: RunOptions) -> RunReport:
        report = RunReport()
        (self.out / "clips").mkdir(parents=True, exist_ok=True)
        (self.out / "keyframes").mkdir(parents=True, exist_ok=True)

        scenes = [s for s in self.sb.scenes if not opts.only or s.id in opts.only]
        # Once per run (dry-run too), only when there is a hand-downloaded clip that cannot be checked.
        if not manual_clip.ffprobe_available() and any(
                self.state.scene(s.id).get("status") == SUBMITTING and self.clip_path(s).is_file() for s in scenes):
            self.log("⚠ không có ffprobe nên không kiểm được clip tải tay; cài ffmpeg (brew install ffmpeg) "
                     "để app tự nhận clip")
        if opts.keyframes_only:
            return self._run_keyframes(scenes, opts, report)
        todo = []
        resend: set[str] = set()  # "submitting" scenes that will be sent again (may be billed twice)
        for s, kind in self.video_plan(opts.only, opts.force, opts.retry_unknown, opts.confirm):
            if kind == "done":
                report.skipped.append(s.id)
                continue
            if kind == "adopt":
                if self._adopt_manual_clip(s):
                    report.skipped.append(s.id)
                    continue
                # the file vanished or broke between the two checks: decide as if there were no clip
                # (web: only a confirmed scene is sent)
                kind = self._plan_kind(s, opts.force, opts.retry_unknown, opts.confirm, allow_adopt=False)
            if kind == "blocked":
                msg = self._blocked_message(s, opts)
                self.log(f"[{s.id}] BỎ QUA: {msg}")
                report.failed[s.id] = msg
                continue
            if self.state.scene(s.id).get("status") == SUBMITTING:
                resend.add(s.id)
            todo.append(s)

        if report.skipped:
            self.log(f"Đã xong từ trước (bỏ qua): {', '.join(report.skipped)}")
        if not todo:
            self.log("Không còn cảnh nào cần tạo.")
            return report

        for s in todo:
            self.rep.queued(f"vid:{s.id}")
        try:
            project = self.ensure_project()
        except GflowError as exc:
            self.rep.drop_queued()
            report.aborted = str(exc)
            return report
        self.log(f"Project: {project}")
        try:
            return self._send_scenes(todo, resend, project, opts, report)
        finally:
            self.rep.settle()

    def _send_scenes(self, todo: list[Scene], resend: set[str], project: str, opts: RunOptions,
                     report: RunReport) -> RunReport:
        sent = False  # pause only between real submissions, not after a scene `refuse` skipped
        for s in todo:
            if opts.should_stop():
                self.rep.drop_queued()
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
                self.rep.drop(f"vid:{s.id}")
                report.failed[s.id] = reason
                self.log(f"[{s.id}] KHÔNG GỬI: {reason}")
                continue
            sent = True
            if s.id in resend and not self.g.dry_run:
                self.log(f"[{s.id}] có thể đã bị trừ credit ở lần trước, đang gửi lại")
            self.log(f"[{s.id}] {s.mode}: {s.prompt[:80]}")
            started = manual_clip.stamp_now()
            self.rep.submitting(f"vid:{s.id}")
            try:
                with self.rep.active(f"vid:{s.id}"):
                    video = self._render_scene(s, project, opts)
            except GflowError as exc:
                self.rep.failed(f"vid:{s.id}")
                if not self.g.dry_run:
                    # A request Flow may have billed stays "submitting" so it is not resent blindly.
                    if exc.spent:
                        fields: dict[str, Any] = dict(status=SUBMITTING, error=str(exc), exit_code=exc.exit_code)
                        # Billed before the video call (e.g. keyframe): `submitted_at` must still belong to
                        # this attempt, never to an older one, so an old clips/<id>.mp4 is not adopted later.
                        prev = manual_clip.parse_stamp(self.state.scene(s.id).get("submitted_at"))
                        if prev is None or prev < manual_clip.parse_stamp(started):
                            fields["submitted_at"] = started
                        self.state.update(s.id, **fields)
                    else:
                        self.state.update(s.id, status=FAILED, error=str(exc), exit_code=exc.exit_code,
                                          submitted_at=None)
                report.failed[s.id] = str(exc)
                self.log(f"[{s.id}] LỖI: {exc}")
                if exc.fatal:
                    self.rep.drop_queued()
                    report.aborted = f"dừng toàn bộ vì lỗi nghiêm trọng ở {s.id} (exit {exc.exit_code})"
                    break
                if opts.fail_fast:
                    self.rep.drop_queued()
                    report.aborted = f"--fail-fast: dừng ở {s.id}"
                    break
                continue
            report.done.append(s.id)
            self.log(f"[{s.id}] {'(dry-run) sẽ lưu' if self.g.dry_run else 'OK'} -> {video}")
            self.rep.done(f"vid:{s.id}")  # before after_scene: a slow backup must not keep the overlay
            if not self.g.dry_run:
                opts.after_scene(s.id)
        return report

    def _keyframe(self, s: Scene, project: str, opts: RunOptions, *, remake: bool) -> Path:
        kf = self.keyframe_path(s)
        if self.has_keyframe(s) and not remake:
            self.log(f"  dùng lại keyframe {kf}")
            return kf
        key = f"kf:{s.id}"
        self.rep.submitting(key)
        try:
            with self.rep.active(key):
                kf = self._make_keyframe(s, project, opts)
        except GflowError:
            self.rep.failed(key)
            raise
        self.rep.done(key)
        return kf

    def _make_keyframe(self, s: Scene, project: str, opts: RunOptions) -> Path:
        kf = self.keyframe_path(s)
        prompt = self.sb.image_prompt(s)
        loc = self.sb.scene_location(s)
        items, src, why = self.keyframe_ref_items(s, self._dry_kf if self.g.dry_run else ())
        if loc is not None:
            if src is not None and self.g.dry_run and not self.has_keyframe(src):
                self.log(f"  sẽ nối keyframe {src.id} (nếu tạo xong)")
            elif src is not None:
                self.log(f"  nối keyframe {src.id} (giữ ánh sáng, vị trí người)")
            elif why:
                fallback = "dùng ảnh bối cảnh" if self.sb.location_image(loc) else "bối cảnh chưa có ảnh"
                self.log(f"  không nối keyframe cảnh trước: {why}; {fallback}")
            self._log_refs(s, items)
        # The sha of the keyframe chained from, read before the call (`keyframe_from_sha`).
        src_sha = file_sha(self.keyframe_path(src)) if src is not None else None
        refs = [(it.label, it.path) for it in items]
        kw = dict(out=kf, aspect=self.sb.aspect, model=self.sb.image_model, project=project)
        sent = self.sb.for_flow_keyframe(prompt, s)  # + dialogue staging; state keeps `prompt` (fingerprint)
        img = None
        # Images are free on Flow, so retrying them (or falling back) is safe.
        if refs:
            img = self._image_with_refs("tạo keyframe theo ảnh nhân vật", sent, refs, project, kw, opts,
                                        log_line="" if loc is not None else None)
        chained = src is not None and img is not None
        if img is None:
            img = self._with_retries("tạo keyframe", lambda: self.g.text_to_image(sent, **kw), opts.max_retries)
        if not self.g.dry_run:
            if Path(img) != kf and Path(img).is_file():  # keep the UI's preview path stable
                kf.parent.mkdir(parents=True, exist_ok=True)
                kf.write_bytes(Path(img).read_bytes())
            extra: dict[str, Any] = {}
            # Old storyboards (no location) never get the new keys in state.json.
            if loc is not None or "keyframe_from" in self.state.scene(s.id):
                extra = dict(keyframe_from=src.id if chained else None,
                             keyframe_from_sha=src_sha if chained else None)
            self.state.update(s.id, keyframe=str(kf), keyframe_prompt=prompt,
                              keyframe_refs=self.sb.refs_signature(s), **extra)
        else:
            self._dry_kf.add(s.id)
        return kf

    def generate_portraits(self, chars: list[tuple[Character, Path]], opts: RunOptions,
                           on_done: Callable[[int, Character, Path], None] | None = None) -> RunReport:
        """Make reference images for characters (free on Flow). `chars` pairs each character with its output file.

        `on_done(idx, character, dest)` runs right after each image is saved; `idx` is the index in `chars`."""
        return self._free_images(chars, opts, on_done, key_prefix="char", label="ảnh nhân vật",
                                 none_msg="Không có nhân vật nào cần tạo ảnh.", prompt_of=self.sb.portrait_prompt)

    def generate_location_images(self, locs: list[tuple[Location, Path]], opts: RunOptions,
                                 on_done: Callable[[int, Location, Path], None] | None = None) -> RunReport:
        """Make reference images for locations (free on Flow), like `generate_portraits` (progress keys loc:<name>)."""
        return self._free_images(locs, opts, on_done, key_prefix="loc", label="ảnh bối cảnh",
                                 none_msg="Không có bối cảnh nào cần tạo ảnh.", prompt_of=self.sb.location_prompt)

    def _free_images(self, items: list[tuple[Any, Path]], opts: RunOptions,
                     on_done: Callable[[int, Any, Path], None] | None, *, key_prefix: str, label: str,
                     none_msg: str, prompt_of: Callable[[Any], str]) -> RunReport:
        report = RunReport()
        if not items:
            self.log(none_msg)
            return report
        for c, _ in items:
            self.rep.queued(f"{key_prefix}:{c.name.strip()}")
        try:
            project = self.ensure_project()
        except GflowError as exc:
            self.rep.drop_queued()
            report.aborted = str(exc)
            return report
        try:
            return self._free_images_loop(items, project, opts, on_done, report, key_prefix=key_prefix,
                                          label=label, prompt_of=prompt_of)
        finally:
            self.rep.settle()

    def _free_images_loop(self, items: list[tuple[Any, Path]], project: str, opts: RunOptions,
                          on_done: Callable[[int, Any, Path], None] | None, report: RunReport, *, key_prefix: str,
                          label: str, prompt_of: Callable[[Any], str]) -> RunReport:
        for idx, (c, dest) in enumerate(items):
            key = f"{key_prefix}:{c.name.strip()}"
            if opts.should_stop():
                self.rep.drop_queued()
                report.stopped = True
                self.log("Đã dừng theo yêu cầu.")
                break
            if idx > 0 and not self.g.dry_run:
                self.sleep(self._image_pause(opts))
            prompt = prompt_of(c)
            self.log(f"[{c.name}] {label}: {prompt[:100]}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.rep.submitting(key)
            try:
                with self.rep.active(key):
                    img = None
                    if key_prefix == "char" and self.use_flow_characters():
                        img = self._flow_character_image(c, prompt, dest, project)
                    if img is None:
                        img = self._with_retries(
                            f"tạo {label}",
                            lambda: self.g.text_to_image(self.sb.for_flow(prompt), out=dest, aspect=self.sb.aspect,
                                                         model=self.sb.image_model, project=project),
                            opts.max_retries)
            except GflowError as exc:
                self.rep.failed(key)
                report.failed[c.name] = str(exc)
                self.log(f"[{c.name}] LỖI: {exc}")
                if exc.fatal:
                    self.rep.drop_queued()
                    report.aborted = f"dừng vì lỗi nghiêm trọng (exit {exc.exit_code})"
                    break
                continue
            if not self.g.dry_run and Path(img) != dest and Path(img).is_file():
                dest.write_bytes(Path(img).read_bytes())
            if on_done is not None and not self.g.dry_run:
                try:
                    on_done(idx, c, dest)
                except Exception as exc:  # saving early is a bonus: the job still writes everything at the end
                    self.log(f"[{c.name}] ⚠ chưa lưu ảnh vào storyboard ngay được: {exc}")
            report.done.append(c.name)
            self.log(f"[{c.name}] OK -> {dest}")
            self.rep.done(key)
        return report

    def use_flow_characters(self) -> bool:
        """Character pictures are made on Flow's Characters page (a reusable Flow character) rather than as a
        plain text-to-image picture: needs the Flow page driver (gflow's own `character create` uses a labs route
        that accounts moved to flow.google.com no longer have)."""
        return bool(self.g.flow_characters and not self._no_flow_chars and self.g.ui_available())

    def _flow_character_image(self, c: Character, prompt: str, dest: Path, project: str) -> Path | None:
        """Make `c` as a Flow character and save its picture to `dest`; None when that did not work (the caller
        then makes a plain picture from the prompt, as before)."""
        name = c.name.strip()
        old = self.flow_character(name, project)
        retire = None
        if old and old.get("entity_id"):
            retire = (old["entity_id"], f"{name} (cũ {datetime.now().strftime('%d-%m %H%M')})")
        self.log(f"[{c.name}] tạo nhân vật trên Flow (Characters → New character)")
        try:
            res = self.g.ui_create_character(name, self.sb.for_flow(prompt), out=dest, project=project, retire=retire)
        except GflowError as exc:
            if exc.exit_code in (23, 2):  # page changed / no driver: stop trying for the rest of this run
                self._no_flow_chars = True
            self.log(f"[{c.name}] ! Không tạo được nhân vật trên Flow ({exc}); tạo ảnh bằng prompt như cũ.")
            return None
        img = Path(res.get("local_path") or dest)
        if self.g.dry_run:
            return img
        if img != dest and img.is_file():
            dest.write_bytes(img.read_bytes())
        chars = self.state.data.setdefault("flow_characters", {})
        chars[name] = {"project": project, "entity_id": res.get("entity_id"), "flow_name": res.get("name") or name,
                       "media_id": res.get("media_id"), "sha": self._sha(dest), "made_at": _now()}
        self.state.save()
        return dest

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
        for s in todo:
            self.rep.queued(f"kf:{s.id}")
        try:
            project = self.ensure_project()
        except GflowError as exc:
            self.rep.drop_queued()
            report.aborted = str(exc)
            return report
        try:
            return self._keyframes_loop(todo, project, opts, report)
        finally:
            self.rep.settle()

    def _keyframes_loop(self, todo: list[Scene], project: str, opts: RunOptions, report: RunReport) -> RunReport:
        for idx, s in enumerate(todo):
            if opts.should_stop():
                self.rep.drop_queued()
                report.stopped = True
                self.log("Đã dừng theo yêu cầu.")
                break
            if idx > 0 and not self.g.dry_run:
                self.sleep(self._image_pause(opts))
            self.log(f"[{s.id}] keyframe: {(s.image_prompt or s.prompt)[:80]}")
            try:
                kf = self._keyframe(s, project, opts, remake=True)
            except GflowError as exc:
                report.failed[s.id] = str(exc)
                self.log(f"[{s.id}] LỖI: {exc}")
                if exc.fatal:
                    self.rep.drop_queued()
                    report.aborted = f"dừng vì lỗi nghiêm trọng ở {s.id} (exit {exc.exit_code})"
                    break
                continue
            report.done.append(s.id)
            self.log(f"[{s.id}] {'(dry-run) sẽ lưu' if self.g.dry_run else 'OK'} -> {kf}")
        return report

    def _text_video(self, s: Scene, prompt: str, common: dict, opts: RunOptions) -> tuple[Path, str | None, str]:
        """Text-to-video, with the characters' reference images (r2v) when there are any."""
        refs = self.sb.scene_refs(s)  # location picture + characters; never the previous keyframe
        if refs and not self.state.data.get("r2v_unsupported"):
            if self.sb.scene_location(s) is not None:
                self._log_refs(s, self.sb.scene_ref_items(s))
            else:
                self.log(f"  ảnh tham chiếu nhân vật: {', '.join(n for n, _ in refs)}")
            try:
                video, media_id = self._with_retries(
                    "render video",
                    lambda: self.g.reference_to_video(self.sb.for_flow_video(prompt, s), [p for _, p in refs], **common), opts.max_retries)
                return video, media_id, "r2v"
            except GflowError as exc:
                if exc.exit_code != EXIT_NOT_PORTED:
                    raise
                # Refused before submit, so no credits were spent.
                self.log("  ! Tài khoản Flow này chưa hỗ trợ video theo ảnh tham chiếu qua gflow (chưa trừ credit). "
                         "Dùng Chữ → Video thường.")
                self.state.data["r2v_unsupported"] = True
                self.state.save()
                self._remember(r2v_unsupported=True)
        video, media_id = self._with_retries("render video", lambda: self.g.text_to_video(self.sb.for_flow_video(prompt, s), **common),
                                             opts.max_retries)
        return video, media_id, "t2v"

    def _render_text_only(self, s: Scene, common: dict, opts: RunOptions) -> Path:
        prompt = self.sb.text_only_prompt(s)
        self.log(f"  (Chữ → Video) {prompt[:120]}")
        if not self.g.dry_run:
            self.state.update(s.id, status=SUBMITTING, fingerprint=self.sb.scene_fingerprint(s), error=None,
                              submitted_at=manual_clip.stamp_now())
        video, media_id, how = self._text_video(s, prompt, common, opts)
        if not self.g.dry_run:
            if not Path(video).is_file():
                raise GflowError(f"gflow báo thành công nhưng không thấy file {video}", exit_code=1)
            self.state.update(s.id, status=DONE, video=str(video), media_id=media_id, rendered_as=how,
                              submitted_at=None)
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
            self.state.update(s.id, status=SUBMITTING, fingerprint=self.sb.scene_fingerprint(s), error=None,
                              submitted_at=manual_clip.stamp_now())

        prompt = self.sb.video_prompt(s)
        if start_image is None:
            video, media_id, how = self._text_video(s, prompt, common, opts)
            if not dry:
                if not Path(video).is_file():
                    raise GflowError(f"gflow báo thành công nhưng không thấy file {video}", exit_code=1)
                self.state.update(s.id, status=DONE, video=str(video), media_id=media_id, rendered_as=how,
                              submitted_at=None)
            return Path(video)
        if ui:
            return self._render_via_ui(s, start_image, prompt, common)
        call = lambda: self.g.image_to_video(start_image, self.sb.for_flow_video(prompt, s), end_image=s.end_image, **common)
        try:
            video, media_id = self._with_retries("render video", call, opts.max_retries)
        except GflowError as exc:
            if exc.exit_code != EXIT_NOT_PORTED or start_image is None:
                raise
            # Refused before submit, so no credits were spent: forget the "submitting" mark.
            if not dry:
                self.state.update(s.id, status=None, fingerprint=None, submitted_at=None)
            self.log(f"  ! gflow từ chối Ảnh → Video trước khi gửi (chưa trừ credit): {exc}")
            self.state.data["i2v_unsupported"] = True
            self.state.data["i2v_error"] = str(exc)
            self.state.save()
            self._remember(i2v_unsupported=True, i2v_error=str(exc))
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
            self.state.update(s.id, status=DONE, video=str(video), media_id=media_id, rendered_as=None,
                              submitted_at=None)
        return Path(video)

    def _adopt_manual_clip(self, s: Scene, *, unverified_ok: bool = False, dry: bool | None = None) -> bool:
        """A render was interrupted after Flow billed it, and the user downloaded the clip from Flow
        by hand into clips/<id>.mp4: take that file instead of rendering (and paying) again.

        Only a complete clip saved after the submission ("ok"), or with `unverified_ok` the clip of a legacy
        interrupted scene. `dry` None follows the gflow dry-run. The only place a clip is adopted."""
        dry = self.g.dry_run if dry is None else dry
        mc = self.manual_clip_state(s)
        if not (mc == "ok" or (unverified_ok and mc == "unverified")):
            return False
        clip = self.clip_path(s)
        if dry:
            self.log(f"[{s.id}] (dry-run) sẽ dùng clip bạn đã tải về ({clip})")
            return True
        self.log(manual_clip.adopt_line(s.id, clip))
        self.state.update(s.id, status=DONE, video=str(clip), fingerprint=self.sb.scene_fingerprint(s),
                          error=None, rendered_as="manual", submitted_at=None)
        return True

    def adopt_legacy_clip(self, s: Scene) -> tuple[str | None, str]:
        """Take clips/<id>.mp4 for a legacy interrupted scene (CLI --adopt, web "Dùng clip này"), after
        checking the file. Always writes the state, whatever `g.dry_run` is (the web route uses a dry gflow so
        nothing can reach Flow). Returns (None, message) when adopted, else ("not_adoptable" | "clip_invalid",
        reason)."""
        if not self.is_legacy_interrupted(s):
            if self.state.scene(s.id).get("status") != SUBMITTING:
                return "not_adoptable", "cảnh không ở trạng thái bị ngắt"
            return "not_adoptable", "cảnh đã có mốc gửi, app tự nhận clip mới khi chạy"
        clip = self.clip_path(s)
        mc = self.manual_clip_state(s)
        if mc == "unverified":
            if self._adopt_manual_clip(s, unverified_ok=True, dry=False):
                return None, manual_clip.adopt_line(s.id, clip)
            mc = self.manual_clip_state(s)  # the file changed or vanished between the two checks
            if mc == "unverified":
                return "clip_invalid", "clip vừa thay đổi, thử lại"
        if mc is None:
            return "clip_invalid", f"không thấy {clip}"
        if mc == "unchecked":
            return "clip_invalid", "Chưa cài ffprobe nên không kiểm được clip (brew install ffmpeg)"
        return "clip_invalid", "không đọc được trọn clip, có thể tải dở — tải lại từ Flow"

    def _blocked_message(self, s: Scene, opts: RunOptions) -> str:
        """Why an interrupted scene is not sent, what to do with the clip, and how to resend it."""
        clip = self.clip_path(s)
        web = opts.confirm is not None
        mc = self.manual_clip_state(s)
        how = ("bấm “Gửi lại” trên thẻ cảnh và tick ô xác nhận trong hộp Render" if web
               else f"chạy lại với --force {s.id}" if mc == "unverified"  # --retry-unknown skips it (AC10)
               else f"chạy lại với --retry-unknown hoặc --force {s.id}")
        meta = model_meta.load()
        if dur.is_bad(self.sb, s, meta):
            # a resend is refused anyway (the UI tick box is off for it): say the duration comes first
            allowed = dur.allowed_text(dur.allowed_for(self.sb, s, meta) or [])
            how = (f"sửa tay `duration:` ({s.duration} giây, {s.model or self.sb.model} chỉ nhận {allowed}; "
                   f"bị ngắt nên app không tự làm tròn) rồi {how}")
        msg = ("lần chạy trước bị ngắt khi đang render — có thể đã trừ credit. Nếu video đã có trên Flow, "
               f"tải về và lưu thành {clip} rồi chạy lại; nếu chưa có, {how}")
        if mc == "stale":
            msg += (f". Có {clip} nhưng clip cũ hơn lần gửi; nếu chắc đây là bản vừa tải từ Flow, "
                    f"chạy `touch {clip}` rồi chạy lại")
        elif mc == "invalid":
            msg += f". Có {clip} nhưng không đọc được trọn clip, có thể tải dở — tải lại từ Flow"
        elif mc == "unchecked":
            msg += f". Có {clip} nhưng chưa cài ffprobe nên không kiểm được clip"
        elif mc == "unverified":
            msg += (". Có clip trong thư mục nhưng chưa rõ mới hay cũ: "
                    + ("bấm “Dùng clip này” trên thẻ cảnh" if web else
                       f"chạy lại với --adopt {s.id} để dùng clip này, hoặc --force {s.id} để gửi lại "
                       "(có thể tốn credit)"))
        return msg

    def _render_via_ui(self, s: Scene, start_image: Path, prompt: str, common: dict) -> Path:
        """Image-to-video by driving the Flow chat page (flow_ui.py). Never retried: Flow may have billed it."""
        if s.end_image:
            self.log("  ! Ảnh kết thúc chưa dùng được qua giao diện chat; chỉ dùng ảnh bắt đầu.")
        if not self.g.dry_run:
            self.state.update(s.id, status=SUBMITTING, fingerprint=self.sb.scene_fingerprint(s), error=None,
                              submitted_at=manual_clip.stamp_now())
        video, media_id = self.g.ui_image_to_video(start_image, self.sb.for_flow_video(prompt, s), **common)
        if not self.g.dry_run:
            if not Path(video).is_file():
                raise GflowError(f"không thấy file video {video}", exit_code=1)
            other = next((sid for sid, st in self.state.data["scenes"].items()
                          if sid != s.id and media_id and st.get("media_id") == media_id), None)
            if other:  # never accept a clip that already belongs to another scene
                # Move it out of clips/<id>.mp4, or the next run would adopt it as this scene's clip.
                tag = re.sub(r"[^A-Za-z0-9_-]", "", media_id)[:12] or "x"
                dup = self.clip_path(s).with_name(f"{s.id}.dup-{tag}.mp4")
                # Only ever touch a file in our clips/ that is not another scene's clip (the driver's
                # local_path is not checked to be clips/<id>.mp4).
                vpath = Path(video).resolve()
                theirs = {self.clip_path(x).resolve() for x in self.sb.scenes if x.id != s.id} | {
                    Path(st["video"]).resolve() for sid, st in self.state.data["scenes"].items()
                    if sid != s.id and st.get("video")}
                if (self.out / "clips").resolve() not in vpath.parents or vpath in theirs:
                    moved = f"; KHÔNG dời file {video} vì không phải clip của cảnh này, hãy kiểm tra tay"
                else:
                    try:
                        Path(video).replace(dup)
                        moved = f"; đã chuyển file sang {dup}"
                    except OSError:
                        try:
                            Path(video).unlink(missing_ok=True)
                            moved = "; đã xoá file tải về"
                        except OSError:
                            moved = f"; KHÔNG dời được file {video}, hãy xoá tay trước khi chạy lại"
                raise GflowError(f"video tải về trùng với cảnh {other} (media {media_id}); video mới có thể vẫn "
                                 "đang tạo trên Flow — hãy kiểm tra trên Flow" + moved, exit_code=42,
                                 payload={"error": {"spent": True}})
            self.state.update(s.id, status=DONE, video=str(video), media_id=media_id, rendered_as="flow-ui",
                              submitted_at=None)
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
                img = self._image_with_refs("tạo ảnh bìa theo ảnh nhân vật", prompt, refs, project, kw, opts)
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
              retry_unknown: bool, confirm: Collection[str] | None = None) -> list[Scene]:
    """Scenes a video run would actually submit to Flow (kind "send" of `Pipeline.video_plan`)."""
    return [s for s, kind in pipe.video_plan(only, force, retry_unknown, confirm) if kind == "send"]
