"""`filmmaker` command line."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import click

from .assemble import AssembleError, assemble
from .gflow import Gflow
from .pipeline import DONE, Pipeline, RunOptions, State
from .storyboard import load_storyboard, save_storyboard


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
                make (run + stitch một lần)
    """


@main.command()
@click.option("--profile", help="gflow profile (tài khoản Google).")
def check(profile: str | None) -> None:
    """Kiểm tra gflow, phiên đăng nhập và ffmpeg."""
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
    sys.exit(0 if ok else 1)


@main.command()
@click.argument("idea")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=Path("storyboard.yaml"), show_default=True)
@click.option("-n", "--scenes", default=6, show_default=True, help="Số cảnh.")
@click.option("--aspect", type=click.Choice(["16:9", "9:16"]), default="16:9", show_default=True)
@click.option("--lang", "language", default=None, help="Ngôn ngữ phụ đề, ví dụ: Vietnamese.")
def plan(idea: str, output: Path, scenes: int, aspect: str, language: str | None) -> None:
    """Viết storyboard từ một ý tưởng bằng Claude (cần ANTHROPIC_API_KEY)."""
    from .planner import plan as make_plan

    if output.exists() and not click.confirm(f"{output} đã tồn tại. Ghi đè?"):
        return
    click.echo("Đang viết storyboard...")
    sb = make_plan(idea, scenes=scenes, aspect=aspect, language_hint=language)
    save_storyboard(sb, output)
    click.echo(f"✔ {output}: {len(sb.scenes)} cảnh. Xem/sửa lại rồi chạy: filmmaker run {output} --dry-run")


def _run_options(f):
    f = click.option("--out", type=click.Path(path_type=Path), help="Thư mục kết quả (mặc định output/<tên storyboard>).")(f)
    f = click.option("--profile", help="gflow profile (tài khoản Google).")(f)
    f = click.option("--dry-run", is_flag=True, help="Chỉ in các lệnh gflow, không tốn credit.")(f)
    f = click.option("--only", multiple=True, help="Chỉ chạy cảnh này (lặp lại được).")(f)
    f = click.option("--force", multiple=True, help="Tạo lại cảnh này dù đã xong (lặp lại được).")(f)
    f = click.option("--retry-unknown", is_flag=True, help="Gửi lại cảnh bị ngắt giữa chừng lần trước (có thể tốn credit 2 lần).")(f)
    f = click.option("--retries", default=2, show_default=True, help="Số lần thử lại khi lỗi tạm thời.")(f)
    f = click.option("--pause", default="8-20", show_default=True, help="Nghỉ ngẫu nhiên (giây) giữa các lần gửi, dạng MIN-MAX.")(f)
    f = click.option("--fail-fast", is_flag=True, help="Dừng ngay ở cảnh lỗi đầu tiên.")(f)
    f = click.option("--keyframes-only", is_flag=True, help="Chỉ tạo ảnh keyframe (miễn phí) để duyệt trước khi render video.")(f)
    return f


def _do_run(storyboard: Path, out, profile, dry_run, only, force, retry_unknown, retries, pause, fail_fast,
            keyframes_only=False):
    sb = load_storyboard(storyboard)
    out_dir = _out_dir(storyboard, out)
    g = Gflow(profile=profile, dry_run=dry_run, log=click.echo)
    if not dry_run and not g.available():
        raise click.ClickException("không thấy lệnh gflow; chạy `filmmaker check`")
    unknown = set(only) - {s.id for s in sb.scenes}
    if unknown:
        raise click.ClickException(f"không có cảnh: {', '.join(sorted(unknown))}")
    if dry_run:
        click.echo("== DRY RUN: không gửi gì lên Flow ==")
    pipe = Pipeline(sb, out_dir, g, log=click.echo)
    report = pipe.run(RunOptions(
        only=set(only) or None, force=set(force), retry_unknown=retry_unknown,
        max_retries=retries, pause=_parse_pause(pause), fail_fast=fail_fast,
        keyframes_only=keyframes_only, force_keyframes=set(force),
    ))
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
    """Tạo tất cả clip trên Google Flow. Chạy lại an toàn: cảnh đã xong sẽ được bỏ qua."""
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


@main.command()
@click.argument("storyboard", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@_run_options
@_stitch_options
def make(storyboard: Path, output, music, volume, subtitles, allow_missing, **kw) -> None:
    """run + stitch: từ storyboard ra thẳng phim."""
    sb, out_dir, report = _do_run(storyboard, **kw)
    if kw["dry_run"] or kw["keyframes_only"]:
        return
    if not report.ok and not allow_missing:
        raise click.ClickException("có cảnh lỗi nên chưa ghép phim; sửa rồi chạy lại (các cảnh đã xong sẽ được giữ)")
    _do_stitch(sb, out_dir, output, music, volume, subtitles, allow_missing)


@main.command()
@click.argument("storyboard", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path))
def status(storyboard: Path, out) -> None:
    """Xem tiến độ từng cảnh."""
    sb = load_storyboard(storyboard)
    out_dir = _out_dir(storyboard, out)
    pipe = Pipeline(sb, out_dir, Gflow(dry_run=True, log=lambda *_: None))
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


if __name__ == "__main__":
    main()
