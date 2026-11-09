"""`filmmaker` command line."""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

import click

from . import model_meta, retirement
from . import settings as user_settings
from .assemble import AssembleError, assemble
from .backup import AutoBackup, BackupError, Rclone, last_backup
from .gflow import Gflow, GflowError
from .thumbnail import ThumbnailError
from .pipeline import DONE, Pipeline, RunOptions, State, send_list
from .storyboard import LANG_MAX, clean_dialogue_language, clean_language, load_storyboard, save_storyboard


def _out_dir(storyboard: Path, out: Path | None) -> Path:
    return out or storyboard.parent / "output" / storyboard.stem


def _parse_pause(value: str) -> tuple[float, float]:
    lo, _, hi = value.partition("-")
    lo_f = float(lo)
    return (lo_f, float(hi) if hi else lo_f)


@click.group()
@click.version_option(package_name="film-maker-ai")
def main() -> None:
    """Tự động làm video trên Google Flow bằng gflow-cli.

    \b
    Quy trình:  plan (ý tưởng -> storyboard.yaml)
                run  (tạo keyframe + clip trên Flow, chạy lại được)
                stitch (ghép thành phim bằng ffmpeg)
                edit (agent dựng phim: chuyển cảnh, cân âm, nhạc -> liền mạch)
                make (run + stitch một lần)
    """


@main.command()
@click.argument("storyboard", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path), help="Thư mục kết quả (mặc định output/<tên storyboard>).")
@click.option("--profile", help="gflow profile (tài khoản Google).")
def check(storyboard: Path | None, out: Path | None, profile: str | None) -> None:
    """Kiểm tra gflow, phiên đăng nhập và ffmpeg.

    Có STORYBOARD thì liệt kê thêm cảnh dùng model sắp ngừng/đã ngừng.
    """
    ok = True
    g = Gflow(profile=profile)
    if g.available():
        click.echo(f"✔ gflow: {shutil.which(g.binary.split()[0]) or g.binary}")
        res = g.auth_status()
        click.echo(res.stdout.strip() or res.stderr.strip())
        if res.returncode != 0:
            ok = False
            click.echo("✘ Chưa đăng nhập. Chạy: gflow auth login --browser chrome")
    else:
        ok = False
        click.echo("✘ Không thấy gflow. Cài: uv tool install gflow-cli && "
                   "uv tool run --from gflow-cli playwright install chromium")
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        click.echo(f"{'✔' if found else '✘'} {tool}: {found or 'chưa cài (cần cho bước ghép phim)'}")
        ok = ok and bool(found)
    if storyboard is not None:
        ok = _check_models(storyboard, out) and ok
    sys.exit(0 if ok else 1)


_GROUP_ORDER = (("todo", "Chưa có clip"), ("interrupted", "Bị ngắt"), ("done", "Đã có clip"))


def _retire_text(row: dict) -> str:
    when = "đã ngừng từ" if row["status"] == "retired" else "ngừng ngày"
    return f"{when} {row['retires_label']}"


def _check_models(storyboard: Path, out: Path | None) -> bool:
    """`check STORYBOARD`: scenes on retiring/retired models. False if a scene without a clip uses a retired one."""
    meta = model_meta.load()
    warning = model_meta.cli_warning(meta)
    if warning:
        click.echo(warning)
    try:
        sb = load_storyboard(storyboard)
    except Exception as exc:  # pydantic/yaml errors: report, don't crash
        click.echo(f"✘ Storyboard lỗi: {exc}")
        return False
    pipe = Pipeline(sb, _out_dir(storyboard, out), Gflow(dry_run=True, log=lambda *_: None),
                    log=lambda *_: None)
    rows = retirement.old_scenes(pipe, meta, sb.scenes)
    if not rows:
        click.echo("✔ Không cảnh nào dùng model sắp/đã ngừng")
        return True
    for group, title in _GROUP_ORDER:
        picked = [r for r in rows if r["group"] == group]
        if not picked:
            continue
        click.echo(f"⚠ {title} ({len(picked)} cảnh) dùng model sắp/đã ngừng:")
        for r in picked:
            click.echo(f"  {r['id']}  {r['model']}  {_retire_text(r)}")
    blocked = [r for r in rows if r["group"] == "todo" and r["status"] == "retired"]
    if blocked:
        click.echo(f"✘ {len(blocked)} cảnh chưa có clip dùng model đã ngừng: đổi model trong UI "
                   "hoặc sửa `model:` trong storyboard.")
    return not blocked


@main.command()
@click.argument("idea")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=Path("storyboard.yaml"), show_default=True)
@click.option("-n", "--scenes", default=6, show_default=True, help="Số cảnh.")
@click.option("--aspect", type=click.Choice(["16:9", "9:16"]), default="16:9", show_default=True)
@click.option("--lang", "language", default=None,
              help="Ngôn ngữ phụ đề (narration), ví dụ: Vietnamese. Có --dialogue-lang thì được lưu vào "
                   "`subtitle_language` (chỉ để tham khảo).")
@click.option("--dialogue-lang", "dialogue_lang", default=None,
              help="Ngôn ngữ nhân vật nói, tên tiếng Anh (vd Vietnamese, English, Thai); 'none' = không thoại. "
                   "Được ép vào mọi prompt video. --lang vẫn là ngôn ngữ phụ đề (narration).")
@click.option("--backend", type=click.Choice(["auto", "claude_code", "api"]), default="auto", show_default=True,
              help="claude_code = dùng gói Claude (Pro/Max) qua lệnh `claude`; api = dùng ANTHROPIC_API_KEY.")
def plan(idea: str, output: Path, scenes: int, aspect: str, language: str | None, dialogue_lang: str | None,
         backend: str) -> None:
    """Viết storyboard từ một ý tưởng bằng Claude (qua Claude Code hoặc API key)."""
    from .planner import plan_auto

    if dialogue_lang is not None:  # checked before overwriting or calling Claude
        dialogue_lang = _cli_lang(dialogue_lang, "--dialogue-lang", clean_dialogue_language)
        if language is not None:
            language = _cli_lang(language, "--lang", clean_language)
    if output.exists() and not click.confirm(f"{output} đã tồn tại. Ghi đè?"):
        return
    click.echo("Đang viết storyboard...")
    try:
        sb, _ = plan_auto(idea, backend=backend, scenes=scenes, aspect=aspect, language_hint=language,
                          dialogue_language=dialogue_lang)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    save_storyboard(sb, output)
    click.echo(f"✔ {output}: {len(sb.scenes)} cảnh. Xem/sửa lại rồi chạy: filmmaker run {output} --dry-run")


def _cli_lang(value: str, opt: str, clean: Callable[[object], str | None]) -> str:
    """--dialogue-lang / --lang value -> cleaned by `clean`; empty, too long or multi-line -> exit 2."""
    try:
        out = clean(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint=f"'{opt}'") from exc
    if out is None:
        raise click.BadParameter(f"hãy nhập tên ngôn ngữ (tối đa {LANG_MAX} ký tự).", param_hint=f"'{opt}'")
    return out


def _run_options(f):
    f = click.option("--out", type=click.Path(path_type=Path), help="Thư mục kết quả (mặc định output/<tên storyboard>).")(f)
    f = click.option("--profile", help="gflow profile (tài khoản Google).")(f)
    f = click.option("--dry-run", is_flag=True, help="Chỉ in các lệnh gflow, không tốn credit.")(f)
    f = click.option("--only", multiple=True, help="Chỉ chạy cảnh này (lặp lại được).")(f)
    f = click.option("--force", multiple=True, help="Tạo lại cảnh này dù đã xong (lặp lại được).")(f)
    f = click.option("--retry-unknown", is_flag=True, help="Gửi lại cảnh bị ngắt giữa chừng lần trước (có thể tốn credit 2 lần).")(f)
    f = click.option("--adopt", multiple=True,
                     help="Dùng clip bạn đã tải về clips/<id>.mp4 cho cảnh bị ngắt từ bản cũ (lặp lại được).")(f)
    f = click.option("--retries", default=2, show_default=True, help="Số lần thử lại khi lỗi tạm thời.")(f)
    f = click.option("--pause", default="8-20", show_default=True, help="Nghỉ ngẫu nhiên (giây) giữa các lần gửi, dạng MIN-MAX.")(f)
    f = click.option("--fail-fast", is_flag=True, help="Dừng ngay ở cảnh lỗi đầu tiên.")(f)
    f = click.option("--keyframes-only", is_flag=True, help="Chỉ tạo ảnh keyframe (miễn phí) để duyệt trước khi render video.")(f)
    f = click.option("--backup/--no-backup", "auto_backup", default=None,
                     help="Backup lên Drive sau mỗi clip (mặc định theo Cài đặt “Tự backup”).")(f)
    return f


def _rclone(remote: str | None = None) -> Rclone:
    s = user_settings.load()
    return Rclone(remote or s.get("backup_remote") or user_settings.DEFAULT_BACKUP_REMOTE, log=click.echo)


def _auto_backup(storyboard: Path, out_dir: Path, enabled: bool | None) -> AutoBackup | None:
    if enabled is None:
        enabled = bool(user_settings.load().get("auto_backup"))
    if not enabled:
        return None
    r = _rclone()
    if not r.available():
        click.echo("⚠ Tự backup đang bật nhưng chưa cài rclone (brew install rclone) — bỏ qua backup.")
        return None
    return AutoBackup(lambda: r.backup(storyboard, out_dir), log=click.echo)


def _check_run_models(pipe: Pipeline, only, force, retry_unknown: bool, *, warn_only: bool,
                      skip: Collection[str] = ()) -> None:
    """Before sending anything: stop if a scene to send uses a retired model, warn about retiring ones.
    `skip`: scenes this run will not send whatever the plan says (a failed --adopt)."""
    meta = model_meta.load()
    warning = model_meta.cli_warning(meta)
    if warning:
        click.echo(warning)
    send = [s for s in send_list(pipe, set(only) or None, set(force), retry_unknown) if s.id not in skip]
    old = retirement.old_scenes(pipe, meta, send)
    retired = [r for r in old if r["status"] == "retired"]
    if retired and not warn_only:
        lines = "\n".join(f"  {r['id']}: {r['model']} ({_retire_text(r)})" for r in retired)
        raise click.ClickException(
            f"{len(retired)} cảnh dùng model đã ngừng, không gửi gì lên Flow:\n{lines}\n"
            "Cách sửa: đổi model trong UI hoặc sửa `model:` trong storyboard.")
    if old:
        click.echo("⚠ Model sắp ngừng/đã ngừng: "
                   + ", ".join(f"{r['id']} ({r['model']}, {_retire_text(r)})" for r in old))


def _adopt_clips(pipe: Pipeline, adopt, dry_run: bool) -> dict[str, str]:
    """`--adopt`: take clips/<id>.mp4 of legacy interrupted scenes. Returns {id: reason} of clips not usable."""
    failed: dict[str, str] = {}
    scenes = {s.id: s for s in pipe.sb.scenes}
    for sid in dict.fromkeys(adopt):
        if dry_run:
            click.echo(f"--adopt {sid}: (dry-run) không ghi gì")
            continue
        reason, msg = pipe.adopt_legacy_clip(scenes[sid])
        if reason == "not_adoptable":
            click.echo(f"--adopt {sid}: bỏ qua ({msg})")
        elif reason:
            click.echo(f"--adopt {sid}: không dùng được clip ({msg})")
            failed[sid] = f"--adopt: không dùng được clip ({msg})"
    return failed


def _do_run(storyboard: Path, out, profile, dry_run, only, force, retry_unknown, retries, pause, fail_fast,
            keyframes_only=False, auto_backup=None, adopt=()):
    sb = load_storyboard(storyboard)
    out_dir = _out_dir(storyboard, out)
    g = Gflow(profile=profile, dry_run=dry_run, log=click.echo)
    if not dry_run and not g.available():
        raise click.ClickException("không thấy lệnh gflow; chạy `filmmaker check`")
    unknown = (set(only) | set(adopt)) - {s.id for s in sb.scenes}
    if unknown:
        raise click.ClickException(f"không có cảnh: {', '.join(sorted(unknown))}")
    both = sorted(set(adopt) & set(force))
    if both:  # adopting then forcing would pay Flow again right after taking the clip
        raise click.ClickException(
            f"không dùng cùng lúc --adopt và --force cho {', '.join(both)}: "
            "--adopt dùng clip đã tải về, --force gửi lại (có thể tốn credit); chọn một")
    if dry_run:
        click.echo("== DRY RUN: không gửi gì lên Flow ==")
    pipe = Pipeline(sb, out_dir, g, log=click.echo)
    adopt_failed = _adopt_clips(pipe, adopt, dry_run)
    _check_run_models(pipe, only, force, retry_unknown, warn_only=dry_run or keyframes_only, skip=adopt_failed)
    retired = retirement.refuse_retired(sb) if not dry_run else (lambda _s: None)

    def refuse(s):
        # A scene whose --adopt clip was unusable is never sent in this run, even with --retry-unknown
        # (its clip is "invalid"/"unchecked", which --retry-unknown would resend: maybe billed twice).
        # --force <id> would be the way to resend, but --adopt and --force on one scene are refused above.
        return adopt_failed.get(s.id) or retired(s)

    ab = None if dry_run else _auto_backup(storyboard, out_dir, auto_backup)
    try:
        report = pipe.run(RunOptions(
            only=set(only) or None, force=set(force), retry_unknown=retry_unknown,
            max_retries=retries, pause=_parse_pause(pause), fail_fast=fail_fast,
            # Re-rendering a video keeps its approved keyframe; remake stills with --keyframes-only --force.
            keyframes_only=keyframes_only, force_keyframes=set(force) if keyframes_only else set(),
            after_scene=(lambda _sid: ab.trigger()) if ab else (lambda _sid: None),
            refuse=refuse,
        ))
    finally:
        if ab:
            ab.finish()
    report.failed.update(adopt_failed)
    click.echo("")
    if dry_run:
        click.echo(f"DRY RUN: {len(report.done)} cảnh sẽ được tạo, {len(report.skipped)} cảnh đã có. Chưa gửi gì lên Flow.")
        return sb, out_dir, report
    click.echo(f"Xong: {len(report.done)}  |  Bỏ qua (đã có): {len(report.skipped)}  |  Lỗi: {len(report.failed)}")
    for sid, err in report.failed.items():
        click.echo(f"  ✘ {sid}: {err}")
    if report.aborted:
        click.echo(f"⚠ {report.aborted}")
    return sb, out_dir, report


@main.command()
@click.argument("storyboard", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@_run_options
def run(storyboard: Path, **kw) -> None:
    """Tạo tất cả clip trên Google Flow. Chạy lại an toàn: cảnh đã xong sẽ được bỏ qua.

    Từ ngày model ngừng, cảnh sắp gửi dùng model đã ngừng bị chặn (không gửi gì, exit 1);
    --dry-run và --keyframes-only chỉ cảnh báo.

    Cảnh bị ngắt từ bản cũ có clip tải tay trong clips/: --adopt <id> để dùng clip đó (không tốn credit).
    """
    _, _, report = _do_run(storyboard, **kw)
    sys.exit(0 if report.ok else 1)


def _clips(sb, out_dir: Path) -> tuple[list[tuple[str, Path]], list[str]]:
    state = State(out_dir / "state.json")
    clips, missing = [], []
    for s in sb.scenes:
        st = state.scene(s.id)
        p = Path(st.get("video", ""))
        if st.get("status") == DONE and p.is_file():
            clips.append((s.id, p))
        else:
            missing.append(s.id)
    return clips, missing


def _do_stitch(sb, out_dir: Path, output: Path | None, music, volume, subtitles, allow_missing):
    clips, missing = _clips(sb, out_dir)
    if missing and not allow_missing:
        raise click.ClickException(f"còn thiếu cảnh: {', '.join(missing)} (dùng --allow-missing để ghép phần đã có)")
    output = output or out_dir / "final.mp4"
    try:
        assemble(sb, clips, output, music=music, music_volume=volume, burn_subtitles=subtitles, log=click.echo)
    except AssembleError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"🎬 Phim hoàn chỉnh: {output}")


def _stitch_options(f):
    f = click.option("-o", "--output", type=click.Path(path_type=Path), help="File phim (mặc định <out>/final.mp4).")(f)
    f = click.option("--music", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Nhạc nền.")(f)
    f = click.option("--music-volume", "volume", default=0.25, show_default=True)(f)
    f = click.option("--burn-subtitles", "subtitles", is_flag=True, help="In phụ đề (narration) lên hình.")(f)
    f = click.option("--allow-missing", is_flag=True, help="Vẫn ghép khi còn cảnh chưa có.")(f)
    return f


@main.command()
@click.argument("storyboard", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path))
@_stitch_options
def stitch(storyboard: Path, out, output, music, volume, subtitles, allow_missing) -> None:
    """Ghép các clip đã tạo thành một phim (ffmpeg, không tốn credit)."""
    sb = load_storyboard(storyboard)
    _do_stitch(sb, _out_dir(storyboard, out), output, music, volume, subtitles, allow_missing)


def _ai_ready(backend: str) -> bool:
    from .planner import claude_code_binary

    has_key = bool(user_settings.load().get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY"))
    if backend == "claude_code":
        return bool(claude_code_binary())
    if backend == "api":
        return has_key
    return bool(claude_code_binary()) or has_key


def _do_edit(sb, out_dir: Path, output: Path | None, music, volume, subtitles, allow_missing, *,
             engine: str = "auto", replan: bool = False, backend: str = "auto", plan_only: bool = False):
    from .editor import edit_film, make_plan, plan_path

    clips, missing = _clips(sb, out_dir)
    if missing and not allow_missing:
        raise click.ClickException(f"còn thiếu cảnh: {', '.join(missing)} (dùng --allow-missing để dựng phần đã có)")
    kw = dict(engine=engine, replan=replan, backend=backend, ai_available=_ai_ready(backend),
              api_key=user_settings.load().get("anthropic_api_key") or None, log=click.echo)
    try:
        if plan_only:
            make_plan(sb, clips, out_dir, has_music=bool(music), **kw)
            click.echo(f"✔ Kế hoạch dựng: {plan_path(out_dir)} (sửa được, rồi chạy `filmmaker edit` để xuất phim)")
            return
        output, _ = edit_film(sb, clips, out_dir, output, music=music, music_volume=volume,
                              burn_subtitles=subtitles, **kw)
    except (AssembleError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"🎬 Phim đã dựng: {output}")


def _edit_options(f):
    f = click.option("--engine", type=click.Choice(["auto", "ai", "rules"]), default="auto", show_default=True,
                     help="ai = agent Claude lập kế hoạch dựng; rules = luật mặc định; auto = AI nếu có.")(f)
    f = click.option("--replan", is_flag=True, help="Bỏ kế hoạch dựng đã lưu (edit.json), làm bản mới.")(f)
    f = click.option("--backend", type=click.Choice(["auto", "claude_code", "api"]), default="auto", show_default=True)(f)
    return f


@main.command()
@click.argument("storyboard", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path))
@_stitch_options
@_edit_options
@click.option("--plan-only", is_flag=True, help="Chỉ lập kế hoạch dựng (edit.json), chưa xuất phim.")
def edit(storyboard: Path, out, output, music, volume, subtitles, allow_missing, engine, replan, backend,
         plan_only) -> None:
    """Agent dựng phim: cắt thừa, chuyển cảnh, cân âm, nhạc nền -> phim liền mạch (không tốn credit).

    \b
    Lần đầu agent (Claude) xem kịch bản + phân tích từng clip rồi lập kế hoạch dựng, lưu ở
    <out>/edit.json. Sửa file đó rồi chạy lại để xuất theo ý mình; --replan để agent làm bản mới.
    """
    sb = load_storyboard(storyboard)
    _do_edit(sb, _out_dir(storyboard, out), output, music, volume, subtitles, allow_missing,
             engine=engine, replan=replan, backend=backend, plan_only=plan_only)


@main.command()
@click.argument("storyboard", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@_run_options
@_stitch_options
@click.option("--edit/--no-edit", "smart_edit", default=False,
              help="Dựng bằng agent dựng phim (chuyển cảnh, cân âm) thay vì nối thẳng.")
def make(storyboard: Path, output, music, volume, subtitles, allow_missing, smart_edit, **kw) -> None:
    """run + stitch: từ storyboard ra thẳng phim.

    Chặn cảnh dùng model đã ngừng giống `run`.
    """
    sb, out_dir, report = _do_run(storyboard, **kw)
    if kw["dry_run"] or kw["keyframes_only"]:
        return
    if not report.ok and not allow_missing:
        raise click.ClickException("có cảnh lỗi nên chưa ghép phim; sửa rồi chạy lại (các cảnh đã xong sẽ được giữ)")
    if smart_edit:
        _do_edit(sb, out_dir, output, music, volume, subtitles, allow_missing)
    else:
        _do_stitch(sb, out_dir, output, music, volume, subtitles, allow_missing)
    try:
        _do_thumbnail(sb, out_dir, kw.get("profile"), remake=False)
    except click.ClickException as exc:  # the film is done; a missing cover is not a failure
        click.echo(f"⚠ Chưa làm được thumbnail: {exc.message}")
    ab = _auto_backup(storyboard, out_dir, kw.get("auto_backup"))
    if ab:
        ab.finish()  # final.mp4 + thumbnail


def _do_thumbnail(sb, out_dir: Path, profile: str | None, *, remake: bool) -> Path:
    g = Gflow(profile=profile, log=click.echo)
    pipe = Pipeline(sb, out_dir, g, log=click.echo)
    if (remake or not pipe.has_thumbnail_image()) and not g.available():
        raise click.ClickException("không thấy lệnh gflow; chạy `filmmaker check`")
    try:
        return pipe.make_thumbnail(RunOptions(), remake=remake)
    except (GflowError, ThumbnailError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.command()
@click.argument("storyboard", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path))
@click.option("--profile", help="gflow profile (tài khoản Google).")
@click.option("--remake", is_flag=True, help="Tạo lại ảnh bìa trên Flow (mặc định dùng lại ảnh đã có, chỉ in lại tiêu đề).")
def thumbnail(storyboard: Path, out, profile, remake) -> None:
    """Tạo thumbnail cho video: ảnh bìa trên Flow (miễn phí) + in tiêu đề `thumbnail.text`."""
    sb = load_storyboard(storyboard)
    path = _do_thumbnail(sb, _out_dir(storyboard, out), profile, remake=remake)
    click.echo(f"🖼 Thumbnail: {path}")


def _slug(text: str) -> str:
    import re
    import unicodedata
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:50]


@main.command()
@click.argument("storyboard", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--name", "names", multiple=True, help="Chỉ tạo ảnh cho nhân vật này (lặp lại được).")
@click.option("--force", is_flag=True, help="Tạo lại cả nhân vật đã có ảnh.")
@click.option("--out", type=click.Path(path_type=Path))
@click.option("--profile", help="gflow profile (tài khoản Google).")
@click.option("--dry-run", is_flag=True, help="Chỉ in lệnh gflow, không gửi gì.")
@click.option("--pause", default="8-20", show_default=True, help="Nghỉ ngẫu nhiên (giây) giữa các lần gửi.")
def characters(storyboard: Path, names, force, out, profile, dry_run, pause) -> None:
    """Tạo ảnh tham chiếu nhân vật trên Flow (miễn phí) và ghi đường dẫn vào storyboard.

    Mặc định chỉ tạo cho nhân vật chưa có ảnh. Ảnh lưu ở assets/characters/<tên>.png.
    """
    import yaml
    sb = load_storyboard(storyboard)
    unknown = set(names) - {c.name for c in sb.characters}
    if unknown:
        raise click.ClickException(f"không có nhân vật: {', '.join(sorted(unknown))}")
    picked, used = [], set()
    for i, c in enumerate(sb.characters):
        if names and c.name not in names:
            continue
        if not names and not force and c.image and Path(c.image).is_file():
            continue
        if not c.name.strip() or not c.description.strip():
            raise click.ClickException(f"nhân vật #{i + 1} cần có name và description")
        stem = _slug(c.name) or f"nhan-vat-{i + 1}"
        while stem in used:
            stem += f"-{i + 1}"
        used.add(stem)
        picked.append((i, c, storyboard.parent / "assets" / "characters" / f"{stem}.png"))
    if not picked:
        click.echo("Tất cả nhân vật đã có ảnh (dùng --force hoặc --name để tạo lại).")
        return
    g = Gflow(profile=profile, dry_run=dry_run, log=click.echo)
    if not dry_run and not g.available():
        raise click.ClickException("không thấy lệnh gflow; chạy `filmmaker check`")
    pipe = Pipeline(sb, _out_dir(storyboard, out), g, log=click.echo)
    report = pipe.generate_portraits([(c, dest) for _, c, dest in picked],
                                     RunOptions(pause=_parse_pause(pause)))
    if not dry_run and report.done:
        raw = yaml.safe_load(storyboard.read_text(encoding="utf-8"))
        chars = raw.get("characters") or []
        for i, c, dest in picked:
            if c.name in report.done and i < len(chars) and chars[i].get("name") == c.name:
                chars[i]["image"] = dest.relative_to(storyboard.parent).as_posix()
        storyboard.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
    click.echo("")
    click.echo(f"Xong: {len(report.done)} ảnh nhân vật  |  Lỗi: {len(report.failed)}")
    for name, err in report.failed.items():
        click.echo(f"  ✘ {name}: {err}")
    if report.aborted:
        click.echo(f"⚠ {report.aborted}")
    sys.exit(0 if not report.failed and not report.aborted else 1)


STATUS_CODES = ("done", "changed", "interrupted", "failed", "todo")


def _status_payload(storyboard: Path, out_dir: Path, pipe: Pipeline) -> dict[str, Any]:
    """Machine-readable progress for `status --json`. Read-only: never saves state."""
    sb = pipe.sb
    scenes: list[dict[str, Any]] = []
    summary: dict[str, int] = {c: 0 for c in STATUS_CODES}
    for s in sb.scenes:
        st = pipe.state.scene(s.id)
        code = pipe.scene_status(s)
        summary[code] += 1
        v = st.get("video")
        has_kf = s.mode == "keyframe" and pipe.has_keyframe(s)
        scenes.append({
            "id": s.id,
            "mode": s.mode,
            "status": code,
            "prompt": s.prompt,
            "video": str(Path(v).resolve()) if v and Path(v).is_file() else None,
            "keyframe": str(pipe.keyframe_path(s).resolve()) if has_kf else None,
            "error": st.get("error") or None,
        })
    return {
        "title": sb.title,
        "storyboard": str(storyboard.resolve()),
        "out_dir": str(out_dir.resolve()),
        "project_id": sb.project_id or pipe.state.data.get("project_id") or None,
        "summary": {"total": len(scenes), **summary},
        "scenes": scenes,
    }


@main.command()
@click.argument("storyboard", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="In tiến độ dạng JSON (cho script/agent).")
def status(storyboard: Path, out, as_json: bool) -> None:
    """Xem tiến độ từng cảnh."""
    sb = load_storyboard(storyboard)
    out_dir = _out_dir(storyboard, out)
    pipe = Pipeline(sb, out_dir, Gflow(dry_run=True, log=lambda *_: None))
    if as_json:
        click.echo(json.dumps(_status_payload(storyboard, out_dir, pipe), ensure_ascii=False))
        return
    click.echo(f"{sb.title}  ({out_dir})  project={pipe.state.data.get('project_id') or '-'}")
    for s in sb.scenes:
        st = pipe.state.scene(s.id)
        if pipe.is_done(s):
            label = "✔ xong"
        elif st.get("status") == DONE:
            label = "↻ storyboard đã đổi -> sẽ tạo lại"
        else:
            label = {"submitting": "⚠ bị ngắt khi render", "failed": "✘ lỗi"}.get(st.get("status"), "· chưa làm")
        click.echo(f"  {s.id:<6} {s.mode:<8} {label}  {s.prompt[:60]}")
        if st.get("status") == "failed" and st.get("error"):
            click.echo(f"         {st['error'][:200]}")


@main.command()
@click.argument("storyboard", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path))
@click.option("--remote", help="Nơi lưu trên rclone (mặc định theo Cài đặt, gdrive:FilmMaker).")
@click.option("--list", "list_", is_flag=True, help="Liệt kê các bản backup trên Drive.")
def backup(storyboard: Path | None, out, remote, list_) -> None:
    """Backup dự án lên Google Drive bằng rclone (chỉ gửi file mới/đã đổi, không xoá gì trên Drive).

    \b
    Lần đầu: brew install rclone, rồi rclone config -> tạo remote tên "gdrive" (Google Drive).
    File bị thay (ví dụ clip render lại) được giữ bản cũ ở <remote>/.versions/.
    """
    r = _rclone(remote)
    try:
        if list_:
            names = r.list_backups()
            click.echo("\n".join(names) if names else f"Chưa có bản backup nào trên {r.remote}.")
            return
        if not storyboard:
            raise click.UsageError("cần STORYBOARD (hoặc --list)")
        out_dir = _out_dir(storyboard, out)
        prev = last_backup(out_dir)
        if prev:
            click.echo(f"Lần backup trước: {prev.get('at')} -> {prev.get('remote')}")
        r.backup(storyboard, out_dir)
    except BackupError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command()
@click.argument("name")
@click.option("--to", "target", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="Thư mục đích (mặc định ~/FilmMaker/<name>, dùng được ngay trong giao diện).")
@click.option("--remote", help="Nơi lưu trên rclone (mặc định theo Cài đặt, gdrive:FilmMaker).")
@click.option("--overwrite", is_flag=True, help="Ghi đè nếu dự án đã có trên máy.")
def restore(name: str, target: Path | None, remote, overwrite) -> None:
    """Tải dự án NAME từ Drive về máy (xem tên bằng `filmmaker backup --list`).

    Đường dẫn trong state.json được sửa theo chỗ mới, nên các cảnh đã render không bị render lại.
    """
    r = _rclone(remote)
    target = target or Path.home() / "FilmMaker" / name
    try:
        m = r.restore(name, target, overwrite=overwrite)
    except BackupError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Xem tiến độ: filmmaker status {target / m['storyboard']}")


@main.command()
@click.argument("output", type=click.Path(path_type=Path), default=Path("storyboard.yaml"))
def template(output: Path) -> None:
    """Tạo file storyboard mẫu."""
    if output.exists():
        raise click.ClickException(f"{output} đã tồn tại")
    src = Path(__file__).parent / "templates" / "storyboard.yaml"
    output.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    click.echo(f"✔ {output}")


@main.command()
@click.option("--workspace", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="Thư mục chứa các dự án phim (mặc định ~/FilmMaker).")
@click.option("--port", default=8765, show_default=True)
@click.option("--no-browser", is_flag=True, help="Không tự mở trình duyệt.")
def ui(workspace: Path | None, port: int, no_browser: bool) -> None:
    """Mở giao diện web trên máy (http://127.0.0.1:8765)."""
    try:
        from .web.server import serve
    except ImportError as exc:
        raise click.ClickException("cần cài thêm: pip install -e '.[ui]'") from exc
    serve(workspace or Path.home() / "FilmMaker", port=port, open_browser=not no_browser)


@main.command()
@click.option("--workspace", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="Thư mục chứa các dự án phim (mặc định ~/FilmMaker).")
def app(workspace: Path | None) -> None:
    """Mở Film Maker dạng cửa sổ app macOS (không cần trình duyệt)."""
    from .desktop import main as desktop_main

    desktop_main(workspace)


if __name__ == "__main__":
    main()
