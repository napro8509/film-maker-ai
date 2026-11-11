"""Join the generated clips into one film with ffmpeg (optional music + subtitles)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .storyboard import Storyboard


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


def write_subtitles(sb: Storyboard, output: Path, entries_for, log=print) -> dict[str, Path]:
    """One .srt per subtitle language next to `output`: <stem>.srt is the narration's language (as before),
    plus <stem>.<code>.srt for every language of the film. `entries_for(lang)` gives the timed lines
    (lang None = narration). Returns {language or "": path}; files of languages no longer used are removed."""
    from .subtitles import lang_code

    files: dict[str, Path] = {}
    main = output.with_suffix(".srt")
    rows = entries_for(None)
    if rows:
        write_srt(rows, main)
        files[""] = main
        log(f"Phụ đề: {main}")
    elif main.exists():
        main.unlink()
    langs = sb.subtitle_langs()
    keep = set()
    for i, lang in enumerate(langs):
        path = output.with_name(f"{output.stem}.{lang_code(lang)}.srt")
        rows = entries_for(None if i == 0 else lang)
        if not rows:
            continue
        if i and len(rows) < len(entries_for(None)):
            log(f"⚠ Phụ đề {lang} còn thiếu {len(entries_for(None)) - len(rows)} câu (chưa dịch).")
        write_srt(rows, path)
        files[lang] = path
        keep.add(path.name)
        if i:
            log(f"Phụ đề {lang}: {path}")
    for old in output.parent.glob(f"{output.stem}.*.srt"):  # languages removed from the film
        if old.name not in keep and not old.name.endswith(".editing.srt"):
            old.unlink(missing_ok=True)
    return files


def burn_path(files: dict[str, Path], burn_lang: str | None) -> Path | None:
    """The .srt to burn into the picture: the chosen language, else the narration's."""
    if burn_lang:
        for lang, p in files.items():
            if lang and lang.casefold() == burn_lang.casefold():
                return p
    return files.get("")


def assemble(sb: Storyboard, clips: list[tuple[str, Path]], output: Path, *, music: Path | None = None,
             music_volume: float = 0.25, burn_subtitles: bool = False, burn_lang: str | None = None,
             log=print) -> Path:
    """Concatenate clips in storyboard order. Re-encodes so mixed clips join cleanly."""
    ffmpeg = _need("ffmpeg")
    if not clips:
        raise AssembleError("không có clip nào để ghép")
    width, height = (1920, 1080) if sb.aspect == "16:9" else (1080, 1920)
    output.parent.mkdir(parents=True, exist_ok=True)

    by_id = {s.id: s for s in sb.scenes}
    durations, spans = [], []
    t = 0.0
    for sid, path in clips:
        d = probe_duration(path)
        durations.append(d)
        spans.append((sid, t, d))
        t += d

    def entries_for(lang):
        out = []
        for sid, start, d in spans:
            text = sb.subtitle_text(by_id[sid], lang) if sid in by_id else None
            if text:
                out.append((start + 0.2, start + d - 0.2, text))
        return out

    burn = burn_path(write_subtitles(sb, output, entries_for, log), burn_lang) if burn_subtitles else None

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

    cmd += ["-filter_complex", ";".join(filters), "-map", vout, "-map", aout,
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(output)]
    log(f"Ghép {len(clips)} clip ({t:.1f}s) -> {output}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssembleError(f"ffmpeg lỗi: {proc.stderr.strip()[-1500:]}")
    return output
