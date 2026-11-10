"""Join the generated clips into one film with ffmpeg (optional music + subtitles)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .storyboard import SUBTITLE_LANGS, Storyboard, subtitle_code


class AssembleError(RuntimeError):
    pass


def _need(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise AssembleError(f"cần cài '{tool}' (ffmpeg) để ghép phim")
    return path


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [_need("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def has_audio(path: Path) -> bool:
    out = subprocess.run(
        [_need("ffprobe"), "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return bool(out.strip())


def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def write_srt(entries: list[tuple[float, float, str]], path: Path) -> None:
    lines = []
    for i, (start, end, text) in enumerate(entries, 1):
        lines += [str(i), f"{_ts(start)} --> {_ts(end)}", text.strip(), ""]
    path.write_text("\n".join(lines), encoding="utf-8")


TRACK_TITLES = {"vi": "Tiếng Việt", "th": "ไทย", "en": "English"}


def subtitle_paths(output: Path) -> dict[str, Path]:
    """final.mp4 -> {"vi": final.vi.srt, "th": final.th.srt, "en": final.en.srt}."""
    return {code: output.with_suffix(f".{code}.srt") for code, _, _ in SUBTITLE_LANGS}


def write_subtitle_tracks(sb: Storyboard, timed: list[tuple[str, float, float]], output: Path,
                          log=print) -> dict[str, Path]:
    """timed = [(scene id, start, end)]. Writes final.<code>.srt for vi / th / en plus final.srt (the primary
    language, as before) and returns {code: path} of the tracks written; stale files are removed."""
    scenes = {s.id: s for s in sb.scenes}
    written: dict[str, Path] = {}
    for code, path in subtitle_paths(output).items():
        entries = [(a, b, line) for sid, a, b in timed
                   if sid in scenes and (line := sb.subtitle_line(scenes[sid], code))]
        if entries:
            write_srt(entries, path)
            written[code] = path
        elif path.exists():
            path.unlink()
    plain = output.with_suffix(".srt")
    primary = written.get(sb.primary_subtitle_code())
    if primary:
        plain.write_bytes(primary.read_bytes())
        log(f"Phụ đề ({', '.join(TRACK_TITLES[c] for c in written)}): {', '.join(p.name for p in written.values())}")
    elif plain.exists():
        plain.unlink()
    return written


def subtitle_track_args(tracks: dict[str, Path], first_input: int) -> tuple[list[str], list[str]]:
    """(input args, output args) that embed the .srt files as switchable MP4 subtitle tracks."""
    inputs: list[str] = []
    outputs: list[str] = []
    iso = {code: i for code, _, i in SUBTITLE_LANGS}
    for k, (code, path) in enumerate(tracks.items()):
        inputs += ["-f", "srt", "-sub_charenc", "UTF-8", "-i", str(path)]
        outputs += ["-map", f"{first_input + k}:0", f"-metadata:s:s:{k}", f"language={iso[code]}",
                    f"-metadata:s:s:{k}", f"title={TRACK_TITLES[code]}"]
    if tracks:
        outputs += ["-c:s", "mov_text"]
    return inputs, outputs


def burn_track(sb: Storyboard, tracks: dict[str, Path], language: str | None) -> Path | None:
    """The .srt to burn into the picture: the chosen track, else the primary one."""
    code = subtitle_code(language) or sb.primary_subtitle_code()
    return tracks.get(code) or next(iter(tracks.values()), None)


def assemble(sb: Storyboard, clips: list[tuple[str, Path]], output: Path, *, music: Path | None = None,
             music_volume: float = 0.25, burn_subtitles: bool = False, burn_language: str | None = None,
             log=print) -> Path:
    """Concatenate clips in storyboard order. Re-encodes so mixed clips join cleanly."""
    ffmpeg = _need("ffmpeg")
    if not clips:
        raise AssembleError("không có clip nào để ghép")
    width, height = (1920, 1080) if sb.aspect == "16:9" else (1080, 1920)
    output.parent.mkdir(parents=True, exist_ok=True)

    durations, timed = [], []
    t = 0.0
    for sid, path in clips:
        d = probe_duration(path)
        durations.append(d)
        timed.append((sid, t + 0.2, t + d - 0.2))
        t += d
    tracks = write_subtitle_tracks(sb, timed, output, log=log)
    burn = burn_track(sb, tracks, burn_language) if burn_subtitles else None

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for _, p in clips:
        cmd += ["-i", str(p)]
    filters, concat_in = [], []
    for i, ((_, p), d) in enumerate(zip(clips, durations)):
        filters.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v{i}]"
        )
        if has_audio(p):
            filters.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        else:  # Veo clips usually have audio, but fill silence if one does not.
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{d:.3f}[a{i}]")
        concat_in.append(f"[v{i}][a{i}]")
    filters.append(f"{''.join(concat_in)}concat=n={len(clips)}:v=1:a=1[vcat][acat]")

    vout, aout = "[vcat]", "[acat]"
    if burn:
        esc = str(burn).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        filters.append(f"[vcat]subtitles='{esc}'[vsub]")
        vout = "[vsub]"
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        mi = len(clips)
        filters.append(f"[{mi}:a]volume={music_volume},aresample=48000[bg]")
        filters.append(f"[acat][bg]amix=inputs=2:duration=first:dropout_transition=0[amix]")
        aout = "[amix]"
    sub_in, sub_out = subtitle_track_args(tracks, len(clips) + (1 if music else 0))
    cmd += sub_in

    cmd += ["-filter_complex", ";".join(filters), "-map", vout, "-map", aout, *sub_out,
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(output)]
    log(f"Ghép {len(clips)} clip ({t:.1f}s) -> {output}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssembleError(f"ffmpeg lỗi: {proc.stderr.strip()[-1500:]}")
    return output
