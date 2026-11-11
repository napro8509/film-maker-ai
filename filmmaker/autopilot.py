"""Autopilot: one button from an idea to a finished film with its cover, reviewed by AI agents on the way.

    storyboard -> review storyboard -> character portraits -> keyframes -> review keyframes (remake the bad ones)
    -> videos -> AI QC of each clip (flags problems, never pays for a re-render) -> AI edit (final.mp4)
    -> thumbnail -> review the whole film

Every step is resumable: the Pipeline skips what is already made, and `<out>/autopilot.json` remembers the
reviews already done, so pressing the button again continues where it stopped (stop button, no credits, a
crash). Reviews that cannot run (no Claude) are skipped, never fatal. Reports go to `<out>/reviews/`.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from . import durations as dur
from . import model_meta, reviewer, retirement
from . import progress as prog
from .flow_ui import BLOCKED_MARK
from .gflow import Gflow, GflowError
from .pipeline import DONE, SUBMITTING, Pipeline, RunOptions, RunReport
from .storyboard import Storyboard, load_storyboard
from .thumbnail import ThumbnailError

STAGES: list[tuple[str, str]] = [
    ("storyboard", "Viết storyboard"),
    ("review_storyboard", "Agent review storyboard"),
    ("characters", "Tạo ảnh nhân vật"),
    ("keyframes", "Tạo keyframe"),
    ("review_keyframes", "Agent review keyframe"),
    ("videos", "Render video"),
    ("review_clips", "AI QC clip"),
    ("edit", "AI dựng phim"),
    ("thumbnail", "Tạo thumbnail"),
    ("review_film", "Agent review cả phim"),
]
LABELS = dict(STAGES)
RETRY_WAIT = 30  # seconds before the second try of free images that failed
REVIEW_BATCH = 8  # pictures of scenes per review call (plus the portraits)


class AutoError(RuntimeError):
    """Stops the autopilot (the message says why and what to do)."""


@dataclass
class AutoSettings:
    review: bool = True
    keyframe_rounds: int = 2  # review -> remake rounds for keyframes (free)
    rerender_budget: int = 3  # paid re-renders the clip review may ask for, in the whole film
    resend_interrupted: bool = True  # resend a scene once if Flow was cut off mid-render (may cost a clip twice)
    burn_subtitles: bool = True
    burn_lang: str | None = None  # subtitle language burned into the picture (None = the narration's)
    music: Path | None = None
    music_volume: float = 0.25
    engine: str = "auto"  # editor: auto | ai | rules
    ai_backend: str = "auto"
    api_key: str | None = None
    max_retries: int = 2
    pause: tuple[float, float] = (8.0, 20.0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:50]


def _file_sig(p: Path) -> str:
    try:
        st = p.stat()
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


class Autopilot:
    def __init__(self, sb_path: Path, out_dir: Path, gflow: Gflow, *, settings: AutoSettings | None = None,
                 log: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep,
                 progress: prog.Emit | None = None, should_stop: Callable[[], bool] = lambda: False,
                 edit_raw: Callable[[Callable[[dict], bool]], bool] | None = None,
                 on_stage: Callable[[dict], None] | None = None):
        self.sb_path = Path(sb_path)
        self.out = Path(out_dir)
        self.g = gflow
        self.cfg = settings or AutoSettings()
        self.log = log
        self.sleep = sleep
        self.progress = progress
        self.should_stop = should_stop
        self._edit_raw = edit_raw
        self.on_stage = on_stage or (lambda _info: None)
        self.state_path = self.out / "autopilot.json"
        self.reviews_dir = self.out / "reviews"
        self.data: dict[str, Any] = {"stages": {}, "keyframe_ok": {}, "clip_reviewed": {}, "rerendered": [],
                                     "resent": []}
        try:
            self.data.update(json.loads(self.state_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    # --- small helpers -------------------------------------------------------------

    def save(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)

    def edit_raw(self, fn: Callable[[dict], bool]) -> bool:
        """Read-modify-write storyboard.yaml (the web passes a locked version)."""
        if self._edit_raw is not None:
            return self._edit_raw(fn)
        raw = yaml.safe_load(self.sb_path.read_text(encoding="utf-8"))
        if not fn(raw):
            return False
        self.sb_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
        return True

    def sb(self) -> Storyboard:
        return load_storyboard(self.sb_path)

    def pipe(self, sb: Storyboard | None = None) -> Pipeline:
        return Pipeline(sb or self.sb(), self.out, self.g, log=self.log, sleep=self.sleep, progress=self.progress)

    def opts(self, **kw: Any) -> RunOptions:
        return RunOptions(max_retries=self.cfg.max_retries, pause=self.cfg.pause, should_stop=self.should_stop, **kw)

    def ai_kw(self) -> dict[str, Any]:
        return {"backend": self.cfg.ai_backend, "api_key": self.cfg.api_key}

    def stage(self, key: str) -> dict[str, Any]:
        return self.data["stages"].setdefault(key, {})

    def mark(self, key: str, status: str, note: str = "", **extra: Any) -> None:
        st = self.stage(key)
        st.update(status=status, note=note, at=_now(), **extra)
        self.save()
        self.on_stage({"key": key, "label": LABELS[key], "status": status, "note": note})

    def write_review(self, name: str, review: Any) -> Path:
        self.reviews_dir.mkdir(parents=True, exist_ok=True)
        p = self.reviews_dir / f"{name}.json"
        p.write_text(json.dumps(review.model_dump() if hasattr(review, "model_dump") else review, indent=2,
                                ensure_ascii=False), encoding="utf-8")
        return p

    def check_stop(self) -> None:
        if self.should_stop():
            raise _Stopped()

    def _fatal(self, report: RunReport, what: str) -> None:
        if report.aborted and not report.stopped:
            raise AutoError(f"{what}: {report.aborted}")

    # --- run -----------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        started = time.time()
        steps = [k for k, _ in STAGES]
        result: dict[str, Any] = {}
        try:
            for i, key in enumerate(steps, 1):
                self.check_stop()
                self.log("")
                self.log(f"=== Bước {i}/{len(steps)}: {LABELS[key]} ===")
                self.on_stage({"key": key, "label": LABELS[key], "status": "running", "index": i, "total": len(steps)})
                t0 = time.time()
                getattr(self, f"_stage_{key}")(result)
                self.stage(key)["seconds"] = round(time.time() - t0, 1)
                self.save()
        except _Stopped:
            self.log("⏸ Đã dừng theo yêu cầu. Bấm lại “Tạo tự động” để làm tiếp từ chỗ dừng.")
            result["stopped"] = True
            return result
        mins = (time.time() - started) / 60
        self.log("")
        self.log(f"🎬 Xong toàn bộ trong {mins:.0f} phút: {result.get('final')}")
        if result.get("thumbnail"):
            self.log(f"🖼 Thumbnail: {result['thumbnail']}")
        if result.get("film_score") is not None:
            self.log(f"⭐ Agent chấm cả phim: {result['film_score']}/10 — {result.get('film_verdict')}")
        result["minutes"] = round(mins, 1)
        return result

    # --- stages ----------------------------------------------------------------------

    def _stage_storyboard(self, result: dict) -> None:
        sb = self.sb()  # the storyboard exists by now (the caller planned it)
        n_dlg = sum(len(s.spoken_lines()) for s in sb.scenes)
        self.log(f"“{sb.title}”: {len(sb.scenes)} cảnh, {len(sb.characters)} nhân vật, {n_dlg} câu thoại.")
        # Durations the model does not accept would block the render later: round them up now (free).
        pipe = self.pipe(sb)
        scenes, _ = dur.preview(pipe, model_meta.load())
        if scenes:
            edits = dur.edits_for(scenes, "up")
            self.edit_raw(lambda raw: bool(raw.update(dur.apply_to_raw(raw, edits))) or True)
            self.log("Làm tròn độ dài: " + ", ".join(f"{k} → {v}s" for k, v in edits.items()))
        self.mark("storyboard", "done", f"{len(sb.scenes)} cảnh")

    def _stage_review_storyboard(self, result: dict) -> None:
        if not self.cfg.review:
            self.mark("review_storyboard", "skipped", "tắt review")
            return
        if self.stage("review_storyboard").get("status") == "done":
            self.log("Đã review storyboard từ trước.")
            return
        sb = self.sb()
        self.log("Agent review đang đọc storyboard (kịch bản, liên tục, prompt, thoại)…")
        try:
            rev = reviewer.review_storyboard(sb, **self.ai_kw())
        except (RuntimeError, ValueError) as exc:
            self.log(f"⚠ Không review được storyboard ({exc}); làm tiếp.")
            self.mark("review_storyboard", "skipped", str(exc)[:200])
            return
        self.write_review("storyboard", rev)
        self.log(f"Điểm storyboard: {rev.score}/10 ({rev.verdict}). {rev.summary}")
        for f in rev.scenes:
            if f.issues:
                self.log(f"  [{f.id}] " + "; ".join(f.issues))
        changed: list[str] = []
        self.edit_raw(lambda raw: bool(changed.extend(reviewer.apply_scene_fixes(raw, rev.scenes))) or bool(changed))
        try:
            self.sb()
        except Exception as exc:  # a fix broke the storyboard: never keep that
            raise AutoError(f"bản sửa của agent review làm storyboard lỗi: {exc}") from exc
        if changed:
            self.log(f"Đã sửa theo review: {', '.join(changed)}")
        self.mark("review_storyboard", "done", f"{rev.score}/10, sửa {len(changed)} cảnh", score=rev.score,
                  verdict=rev.verdict, changed=changed)

    def _stage_characters(self, result: dict) -> None:
        sb = self.sb()
        pipe = self.pipe(sb)
        # A new portrait changes the references of every scene with that character, so their paid clips would
        # count as changed and be rendered again: never add one for a character already in a finished clip.
        in_clips = {c.name for s in sb.scenes if pipe.state.scene(s.id).get("status") == DONE
                    and Path(pipe.state.scene(s.id).get("video") or "").is_file() for c in sb.scene_characters(s)}
        picked, used = [], set()
        for i, c in enumerate(sb.characters):
            if c.image and Path(c.image).is_file():
                continue
            if not c.name.strip() or not c.description.strip():
                continue
            if c.name in in_clips:
                self.log(f"  {c.name}: đã có trong clip đã render, không tạo ảnh mới (để không phải render lại)")
                continue
            stem = slug(c.name) or f"nhan-vat-{i + 1}"
            while stem in used:
                stem += f"-{i + 1}"
            used.add(stem)
            picked.append((i, c, self.sb_path.parent / "assets" / "characters" / f"{stem}.png"))
        if not picked:
            self.mark("characters", "done", f"{len(sb.characters)} nhân vật đã có ảnh")
            return

        index = {c.name: i for i, c, _ in picked}

        def save_one(_idx: int, c: Any, dest: Path) -> None:
            i = index[c.name]  # `_idx` is the position in this attempt's list, not in `picked`
            rel = dest.relative_to(self.sb_path.parent).as_posix()

            def fn(raw: dict) -> bool:
                chars = raw.get("characters") or []
                if i < len(chars) and isinstance(chars[i], dict) and chars[i].get("name") == c.name:
                    chars[i]["image"] = rel
                    return True
                return False
            self.edit_raw(fn)

        failed: dict[str, str] = {}
        for attempt in (1, 2):
            todo = [(c, dest) for _, c, dest in picked if attempt == 1 or c.name in failed]
            if not todo:
                break
            pipe = self.pipe(sb)
            report = pipe.generate_portraits(todo, self.opts(), on_done=save_one)
            self.check_stop()
            self._fatal(report, "tạo ảnh nhân vật")
            failed = report.failed
            if failed and attempt == 1:
                self.log(f"Thử lại ảnh nhân vật lỗi sau {RETRY_WAIT}s: {', '.join(failed)}")
                self.sleep(RETRY_WAIT)  # Flow often answers "Something went wrong" to a burst of requests
        note = f"{len(picked) - len(failed)}/{len(picked)} ảnh"
        if failed:
            self.log(f"⚠ Chưa có ảnh: {', '.join(failed)} (cảnh của họ chỉ dùng mô tả chữ)")
        self.mark("characters", "done", note)

    def _keyframe_scenes(self, sb: Storyboard) -> list:
        return [s for s in sb.scenes if s.mode == "keyframe"]

    def _stage_keyframes(self, result: dict) -> None:
        for attempt in (1, 2):
            pipe = self.pipe()
            report = pipe.run(self.opts(keyframes_only=True))
            self.check_stop()
            self._fatal(report, "tạo keyframe")
            if not report.failed:
                break
            if attempt == 1:
                self.log(f"Thử lại keyframe lỗi sau {RETRY_WAIT}s: {', '.join(report.failed)}")
                self.sleep(RETRY_WAIT)
        pipe = self.pipe()
        missing = [s.id for s in self._keyframe_scenes(pipe.sb) if not pipe.has_keyframe(s)]
        if missing:
            self.log(f"⚠ Chưa có keyframe: {', '.join(missing)} (sẽ thử lại khi render video)")
        n = len(self._keyframe_scenes(pipe.sb))
        self.mark("keyframes", "done", f"{n - len(missing)}/{n} keyframe")

    def _stage_review_keyframes(self, result: dict) -> None:
        if not self.cfg.review:
            self.mark("review_keyframes", "skipped", "tắt review")
            return
        ok: dict[str, str] = self.data.setdefault("keyframe_ok", {})
        rounds = max(1, self.cfg.keyframe_rounds)
        remade_total: list[str] = []
        scores: dict[str, int] = {}
        unreviewed: set[str] = set()
        for rnd in range(1, rounds + 1):
            pipe = self.pipe()
            items = [(s.id, pipe.keyframe_path(s)) for s in self._keyframe_scenes(pipe.sb)
                     if pipe.has_keyframe(s) and ok.get(s.id) != _file_sig(pipe.keyframe_path(s))]
            if not items:
                break
            self.log(f"Agent review đang xem {len(items)} keyframe (vòng {rnd}/{rounds})…")
            revise: dict[str, str] = {}
            verdicts: list = []
            for chunk in _chunks(items, REVIEW_BATCH):
                verdicts += self._review_keyframe_chunk(pipe.sb, chunk, rnd, unreviewed)
            files = dict(items)
            for v in verdicts:
                scores[v.id] = v.score
                unreviewed.discard(v.id)
                tag = "✔" if v.verdict == "PASS" else "✎"
                self.log(f"  {tag} [{v.id}] {v.score}/10" + (f" — {'; '.join(v.issues)}" if v.issues else ""))
                if v.verdict == "REVISE" and v.image_prompt and rnd <= rounds - 1:
                    revise[v.id] = v.image_prompt
                else:
                    ok[v.id] = _file_sig(files[v.id])  # accepted (or out of rounds)
            self.save()
            if not revise:
                break
            self.log(f"Tạo lại keyframe theo góp ý: {', '.join(revise)}")
            self.edit_raw(lambda raw: any([reviewer.set_scene_field(raw, sid, "image_prompt", txt)
                                           for sid, txt in revise.items()]))
            report = self.pipe().run(self.opts(keyframes_only=True, only=set(revise), force_keyframes=set(revise)))
            self.check_stop()
            self._fatal(report, "tạo lại keyframe")
            remade_total += [sid for sid in revise if sid in report.done]
        avg = round(sum(scores.values()) / len(scores), 1) if scores else None
        missed = sorted(unreviewed - set(scores))
        if missed and not scores:
            self.mark("review_keyframes", "skipped", f"Claude không review được keyframe nào ({', '.join(missed)})",
                      unreviewed=missed)
            return
        note = f"điểm TB {avg}/10, làm lại {len(remade_total)}" if avg else "không có keyframe mới"
        if missed:
            note += f"; chưa review được {', '.join(missed)}"
            self.log(f"⚠ Claude không review được keyframe {', '.join(missed)}: hãy tự xem các ảnh này trước "
                     "khi render video.")
        self.mark("review_keyframes", "done", note, remade=remade_total, scores=scores, unreviewed=missed)

    def _review_keyframe_chunk(self, sb, chunk: list, rnd: int, unreviewed: set[str]) -> list:
        """Verdicts for these keyframes. A failed call (Claude Code sometimes gives up on a big structured answer)
        is split in halves and tried again, so one bad batch never skips the review of the rest of the film.
        A single picture gets two tries; then it is left unreviewed (and said so)."""
        rev, err = None, ""
        for _ in range(1 if len(chunk) > 1 else 2):
            try:
                rev = reviewer.review_keyframes(sb, chunk, **self.ai_kw())
                break
            except (RuntimeError, ValueError) as exc:
                err = str(exc)[:160]
        if rev is None:
            if len(chunk) > 1:
                half = (len(chunk) + 1) // 2
                self.log(f"⚠ Review {len(chunk)} keyframe một lần không được ({err}); chia nhỏ để thử lại…")
                return (self._review_keyframe_chunk(sb, chunk[:half], rnd, unreviewed)
                        + self._review_keyframe_chunk(sb, chunk[half:], rnd, unreviewed))
            self.log(f"⚠ Không review được keyframe {chunk[0][0]} ({err}).")
            unreviewed.add(chunk[0][0])
            return []
        self.write_review(f"keyframes_r{rnd}_{chunk[0][0]}", rev)
        ids = {sid for sid, _ in chunk}
        return [v for v in rev.scenes if v.id in ids]

    def _video_run(self, **kw: Any) -> RunReport:
        sb = self.sb()
        report = self.pipe(sb).run(self.opts(
            refuse=lambda s: retirement.refuse_retired(sb)(s) or dur.refuse_bad_duration(sb)(s), **kw))
        self.check_stop()
        self._fatal(report, "render video")
        return report

    def _stage_videos(self, result: dict) -> None:
        report = self._video_run(confirm=set())
        pipe = self.pipe()
        failed = [s.id for s in pipe.sb.scenes if not pipe.is_done(s) and
                  pipe.state.scene(s.id).get("status") != SUBMITTING]
        if failed:
            self.log(f"Thử lại cảnh lỗi (chưa trừ credit): {', '.join(failed)}")
            self._video_run(only=set(failed), confirm=set())
        self._soften_blocked()
        pipe = self.pipe()
        interrupted = [s.id for s in pipe.sb.scenes
                       if pipe.state.scene(s.id).get("status") == SUBMITTING and s.id not in self.data["resent"]]
        if interrupted and self.cfg.resend_interrupted:
            self.log(f"⚠ Cảnh bị ngắt khi Flow đang render (có thể đã trừ credit): {', '.join(interrupted)}. "
                     "Gửi lại một lần.")
            self.data["resent"] += interrupted
            self.save()
            self._video_run(only=set(interrupted), confirm=set(interrupted))
        pipe = self.pipe()
        missing = [s.id for s in pipe.sb.scenes if not pipe.is_done(s)]
        done = len(pipe.sb.scenes) - len(missing)
        if done == 0:
            raise AutoError("không render được cảnh nào; xem log ở trên")
        if missing:
            self.log(f"⚠ Còn thiếu clip: {', '.join(missing)} — phim vẫn được dựng từ các cảnh đã có; "
                     "bấm lại “Tạo tự động” để thử tiếp.")
        result["missing"] = missing
        self.mark("videos", "done" if not missing else "partial", f"{done}/{len(pipe.sb.scenes)} clip", missing=missing)

    def _soften_blocked(self) -> None:
        """Scenes Flow blocked after it started (credits spent): it made the clip and then deleted it (safety
        filter), or it could not make the clip's sound ("Audio generation failed"). Sending the same scene again
        fails the same way, so Claude rewrites it gently, then it is sent once more (counts as its one resend)."""
        pipe = self.pipe()
        blocked = []
        for s in pipe.sb.scenes:
            err = str(pipe.state.scene(s.id).get("error") or "")
            if pipe.is_done(s) or s.id in self.data["resent"]:
                continue
            if BLOCKED_MARK in err:
                blocked.append((s, "Flow đã xoá video (bộ lọc an toàn)"))
            elif reviewer.AUDIO_FAILED in err.lower():
                blocked.append((s, "Flow không tạo được âm thanh (thường do lời thoại hoặc cảnh trẻ em bị tổn thương)"))
        if not blocked:
            return
        fixed: list[str] = []
        for s, why in blocked:
            self.log(f"[{s.id}] {why}: nhờ Claude viết lại cảnh nhẹ nhàng hơn…")
            try:
                fix = reviewer.soften_scene(pipe.sb, s.id, str(pipe.state.scene(s.id).get("error")), **self.ai_kw())
            except (RuntimeError, ValueError) as exc:
                self.log(f"  ⚠ không viết lại được ({exc}); để nguyên.")
                continue
            fix.id = s.id
            if self.edit_raw(lambda raw: bool(reviewer.apply_scene_fixes(raw, [fix]))):
                self.log(f"  prompt mới: {(fix.prompt or fix.image_prompt or '')[:160]}")
                if fix.dialogue is not None:
                    self.log("  thoại mới: " + (" / ".join(f"{d.character}: {d.line}" for d in fix.dialogue) or "(bỏ thoại)"))
                fixed.append(s.id)
        if not fixed:
            return
        self.data["resent"] += fixed
        self.save()
        # a changed opening-frame prompt makes the keyframe stale: it is remade (free) before the video
        self._video_run(only=set(fixed), confirm=set(fixed))

    def _stage_review_clips(self, result: dict) -> None:
        """AI QC of every new clip (picture vs keyframe, characters, sound, dialogue). It only flags: a FAIL
        clip is listed for the user, who decides whether to pay for a re-render (Phim → Cảnh → Render lại)."""
        if not self.cfg.review:
            self.mark("review_clips", "skipped", "tắt review")
            return
        from . import qc

        pipe = self.pipe()
        self.log("AI QC đang xem và nghe từng clip mới…")
        qc.run(pipe, log=self.log, stop=self.should_stop, **self.ai_kw())
        self.check_stop()
        results = {s.id: qc.current(self.out, s.id, qc.clip_of(pipe, s)) for s in pipe.sb.scenes if pipe.is_done(s)}
        results = {k: v for k, v in results.items() if v}
        if not results:
            self.mark("review_clips", "skipped", "không QC được clip nào")
            return
        scores = {k: v["score"] for k, v in results.items()}
        failed = sorted(k for k, v in results.items() if v["verdict"] == "FAIL")
        avg = round(sum(scores.values()) / len(scores), 1)
        if failed:
            self.log(f"⚠ AI QC đánh FAIL: {', '.join(failed)} — xem lý do ở tab Cảnh và bấm “Render lại” nếu muốn "
                     "(tốn credit). Phim vẫn được dựng từ các clip hiện có.")
        result["qc_failed"] = failed
        self.mark("review_clips", "done", f"QC điểm TB {avg}/10, FAIL {len(failed)}", scores=scores, failed=failed)

    def _clips(self) -> list[tuple[str, Path]]:
        pipe = self.pipe()
        out = []
        for s in pipe.sb.scenes:
            st = pipe.state.scene(s.id)
            p = Path(st.get("video", ""))
            if st.get("status") == DONE and p.is_file():
                out.append((s.id, p))
        return out

    def _stage_edit(self, result: dict) -> None:
        from .editor import edit_film

        clips = self._clips()
        sig = hashlib.sha256(json.dumps([(sid, _file_sig(p)) for sid, p in clips]).encode()).hexdigest()[:16]
        final = self.out / "final.mp4"
        st = self.stage("edit")
        if st.get("status") == "done" and st.get("sig") == sig and final.is_file():
            self.log("Phim đã dựng từ đúng các clip này, dùng lại.")
            result["final"] = str(final)
            return
        from . import subtitles

        sb = self.sb()
        if subtitles.ensure(sb, self.edit_raw, backend=reviewer.ai.pick_backend(self.cfg.ai_backend, self.cfg.api_key),
                            api_key=self.cfg.api_key, log=self.log):
            sb = self.sb()
        ai_ok = self.cfg.engine != "rules" and reviewer.ai.pick_backend(self.cfg.ai_backend, self.cfg.api_key) is not None
        output, plan = edit_film(sb, clips, self.out, final, music=self.cfg.music, music_volume=self.cfg.music_volume,
                                 burn_subtitles=self.cfg.burn_subtitles, burn_lang=self.cfg.burn_lang,
                                 engine=self.cfg.engine, replan=True,
                                 backend=reviewer.ai.pick_backend(self.cfg.ai_backend, self.cfg.api_key) or "auto",
                                 ai_available=ai_ok, api_key=self.cfg.api_key, log=self.log)
        result["final"] = str(output)
        self.mark("edit", "done", f"{len(clips)} clip, dựng bằng {plan.source}", sig=sig)

    def _stage_thumbnail(self, result: dict) -> None:
        sb = self.sb()
        if not (sb.thumbnail.text or "").strip():
            try:
                from .planner import suggest_titles

                titles = suggest_titles(sb, backend=self.cfg.ai_backend, api_key=self.cfg.api_key)
                if titles:
                    title = titles[0]["text"]

                    def fn(raw: dict) -> bool:
                        th = raw.get("thumbnail") if isinstance(raw.get("thumbnail"), dict) else {}
                        th["text"] = title
                        raw["thumbnail"] = th
                        return True
                    self.edit_raw(fn)
                    self.log(f"Tiêu đề (Claude gợi ý): {title}")
                    sb = self.sb()
            except Exception as exc:
                self.log(f"  (không gợi ý được tiêu đề: {exc}; dùng tên phim)")
        pipe = self.pipe(sb)
        _, cover = pipe.thumbnail_paths()
        sig = f"{sb.thumbnail.text}|{sb.aspect}"
        st = self.stage("thumbnail")
        if st.get("status") == "done" and st.get("sig") == sig and pipe.has_thumbnail_image() and cover.is_file():
            self.log("Thumbnail đã có, dùng lại.")
            result["thumbnail"] = str(cover)
            return
        try:
            path = pipe.make_thumbnail(self.opts())
        except (GflowError, ThumbnailError) as exc:
            if isinstance(exc, GflowError) and exc.fatal:
                raise AutoError(f"tạo thumbnail: {exc}") from exc
            self.log(f"⚠ Chưa làm được thumbnail: {exc}")
            self.mark("thumbnail", "failed", str(exc)[:200])
            return
        result["thumbnail"] = str(path)
        self.mark("thumbnail", "done", path.name, sig=sig)

    def _stage_review_film(self, result: dict) -> None:
        final = self.out / "final.mp4"
        if not self.cfg.review or not final.is_file():
            self.mark("review_film", "skipped", "tắt review" if not self.cfg.review else "chưa có phim")
            return
        st = self.stage("review_film")
        sig = _file_sig(final) + "|" + _file_sig(self.out / "thumbnail.jpg")
        if st.get("status") == "done" and st.get("sig") == sig:
            result["film_score"], result["film_verdict"] = st.get("score"), st.get("verdict")
            self.log("Đã review đúng bản phim này từ trước.")
            return
        sb = self.sb()
        sheet = reviewer.contact_sheet(final, self.reviews_dir / "frames" / "film_sheet.jpg",
                                       cols=4, rows=4 if len(sb.scenes) <= 12 else 5)
        thumb = self.out / "thumbnail.jpg"
        extra = f"Film length: {reviewer.duration_of(final):.0f} s."
        if result.get("missing"):
            extra += f" Scenes still missing a clip: {', '.join(result['missing'])}."
        self.log("Agent review đang xem cả phim (khung hình toàn phim + ảnh bìa)…")
        try:
            rev = reviewer.review_film(sb, sheet, thumb if thumb.is_file() else None, extra, **self.ai_kw())
        except (RuntimeError, ValueError) as exc:
            self.log(f"⚠ Không review được cả phim ({exc}).")
            self.mark("review_film", "skipped", str(exc)[:200])
            return
        self.write_review("film", rev)
        self.log(f"Điểm cả phim: {rev.score}/10 ({rev.verdict}). {rev.summary}")
        for x in rev.strengths:
            self.log(f"  + {x}")
        for x in rev.issues:
            self.log(f"  - {x}")
        for x in rev.suggestions:
            self.log(f"  → {x}")
        if rev.thumbnail:
            self.log(f"  Ảnh bìa: {rev.thumbnail}")
        result["film_score"], result["film_verdict"] = rev.score, rev.verdict
        self.mark("review_film", "done", f"{rev.score}/10 {rev.verdict}", score=rev.score, verdict=rev.verdict, sig=sig)
        self.write_report()

    def write_report(self) -> Path:
        """reviews/report.md: what each agent said, for people."""
        lines = [f"# Báo cáo tự động — {self.sb().title}", ""]
        for key, label in STAGES:
            st = self.data["stages"].get(key) or {}
            secs = f" · {st['seconds'] / 60:.1f} phút" if st.get("seconds") else ""
            lines.append(f"- **{label}**: {st.get('status', '—')} {st.get('note', '')}{secs}")
        film = self.reviews_dir / "film.json"
        if film.is_file():
            f = json.loads(film.read_text(encoding="utf-8"))
            lines += ["", f"## Cả phim: {f['score']}/10 ({f['verdict']})", "", f["summary"], ""]
            lines += [f"- ✔ {x}" for x in f.get("strengths", [])]
            lines += [f"- ✘ {x}" for x in f.get("issues", [])]
            lines += [f"- → {x}" for x in f.get("suggestions", [])]
        p = self.reviews_dir / "report.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def public(self) -> dict[str, Any]:
        """Stage list for the UI."""
        return {"stages": [{"key": k, "label": lbl, **(self.data["stages"].get(k) or {})} for k, lbl in STAGES]}


class _Stopped(Exception):
    pass


def _chunks(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def load_public(out_dir: Path) -> dict[str, Any] | None:
    """autopilot.json + film review for the UI (None when the autopilot never ran here)."""
    p = Path(out_dir) / "autopilot.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    out: dict[str, Any] = {"stages": [{"key": k, "label": lbl, **(data.get("stages", {}).get(k) or {})}
                                      for k, lbl in STAGES]}
    film = Path(out_dir) / "reviews" / "film.json"
    try:
        out["film_review"] = json.loads(film.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        out["film_review"] = None
    sbr = Path(out_dir) / "reviews" / "storyboard.json"
    try:
        out["storyboard_review"] = json.loads(sbr.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        out["storyboard_review"] = None
    return out
