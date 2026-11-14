"""Phân tích video: learn how a reference video is made, then make a new film in the same style.

1. fetch()          - the video from a link (yt-dlp; plain download for a direct .mp4) or a local file,
                      with its title, description and subtitles (the dialogue) when the site has them.
2. measure()        - ffmpeg: size / fps / length, shot cuts, black frames (fades), loudness per second
                      (where the sound rises and drops, silences) -> rhythm numbers.
3. run_rea()        - optional: rea-video (https://github.com/JuneYaooo/rea-video, its own venv) adds better
                      shot cuts, camera moves, music / voice / sound-effect stems, BPM and caption styles.
4. ask_claude()     - Claude looks at contact sheets of every shot + all the measurements and writes the
                      structured analysis (VideoAnalysis): shots, transitions, camera, look, sound, on-screen
                      text, characters, settings, story beats and an edit-style guide for the editor agent.
5. plan_from_analysis() - a new storyboard in the same style (planner.plan_auto) with `edit_style` set.

Everything is saved in one folder per analysis (analysis.json, report.md, source video, frames, rea/).
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import BaseModel, Field

Log = Callable[[str], None]
StopCheck = Callable[[], bool]

MAX_SECONDS = 600  # only the first 10 minutes are analysed
MAX_DOWNLOAD = 600 * 1024 * 1024
SCENE_THRESHOLD = 0.3  # ffmpeg scene score for a cut
MIN_SHOT = 0.35  # cuts closer than this are merged (flashes, glitches)
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
REA_HOME = Path("~/FilmMaker/.tools/rea-video").expanduser()
REA_TEXT_MAX = 9000  # characters of each rea-video Reel file sent to Claude
SUBS_MAX = 6000
MIN_SCENE_CUT = 1.5  # a new-film scene shorter than this on screen is merged with its neighbour
MAX_SCENE_CUT = 8.0  # one Veo clip is at most 8 s, so a longer stretch of the reference needs several scenes
MAX_SCENES = 20


class AnalysisError(RuntimeError):
    pass


class AnalysisStopped(Exception):
    """The user pressed Stop."""


# --- Claude's answer ----------------------------------------------------------


class ShotNote(BaseModel):
    n: int = Field(description="Shot number exactly as labelled on the contact sheets.")
    size: str = Field(description="Shot size in English: extreme wide, wide, full, medium, medium close-up, close-up, "
                                  "extreme close-up, insert, over-the-shoulder, POV, screen/graphic.")
    angle: str = Field(default="", description="English: eye-level, low angle, high angle, top-down, dutch, selfie...")
    movement: str = Field(default="", description="English camera move: static, handheld, push in, pull out, pan, "
                                                  "tilt, tracking, orbit, crane, zoom, whip pan, speed ramp...")
    subject: str = Field(description="What we see, one short English line (who / what / where).")
    transition_in: str = Field(default="cut", description="How this shot begins from the previous one (English): "
                               "cut, jump cut, match cut, crossfade, fade from black, flash, whip, zoom transition, "
                               "slide, glitch, wipe... Use 'start' for shot 1.")
    text_on_screen: str = Field(default="", description="Text burnt into the picture (caption, title, sticker), as "
                                                         "written; empty if none.")
    purpose: str = Field(default="", description="Role of the shot in the story / edit, short, in Vietnamese.")


class TransitionUse(BaseModel):
    type: str = Field(description="Transition kind in English (cut, jump cut, crossfade, fade to black, flash, whip...).")
    count: int = Field(description="How many times it is used.")
    where_vi: str = Field(default="", description="When and why the video uses it, in Vietnamese.")


class SoundMoment(BaseModel):
    at: float = Field(description="Seconds from the start.")
    what_vi: str = Field(description="What happens in the sound, in Vietnamese (nhạc dồn lên, im lặng, tiếng whoosh...).")


class LookNotes(BaseModel):
    palette_vi: str = Field(description="Main colours, in Vietnamese.")
    grade_vi: str = Field(description="Colour grade / filter (warm, teal-orange, desaturated, high contrast...), in Vietnamese.")
    lighting_vi: str = Field(description="Light: source, direction, hard/soft, time of day, in Vietnamese.")
    texture_vi: str = Field(default="", description="Grain, sharpness, lens look, aspect, in Vietnamese.")
    style_prompt: str = Field(description="ONE English paragraph to append to every image/video prompt of a new film "
                                          "so it gets the same look (genre, framing, colour, light, texture). "
                                          "No names, no brands, no text on screen.")


class SoundNotes(BaseModel):
    music_vi: str = Field(description="Music: mood, genre, instruments, tempo, how it is used, in Vietnamese. "
                                      "Say clearly when you can only infer it from the picture and the loudness.")
    voice_vi: str = Field(description="Voices: narration, dialogue, language, none, in Vietnamese.")
    sfx_vi: str = Field(default="", description="Sound effects and ambience used, in Vietnamese.")
    moments: list[SoundMoment] = Field(default_factory=list, description="Where the sound rises, drops, stops or hits.")
    music_query: str = Field(description="3-6 English words to search a music library for similar music, "
                                         "e.g. 'tense piano cinematic drama'.")


class TextStyle(BaseModel):
    subtitles_vi: str = Field(description="Subtitles/captions: are there any, position, font, size, colour, outline "
                                          "or box, words per line, animation, in Vietnamese.")
    titles_vi: str = Field(default="", description="Titles, hooks, labels, stickers, emoji, in Vietnamese.")


class Person(BaseModel):
    name: str = Field(description="A label for the character (role, e.g. 'the husband'), English.")
    look: str = Field(description="Look in English: age, face, hair, outfit (no real names).")
    role_vi: str = Field(default="", description="Role in the story, in Vietnamese.")


class Place(BaseModel):
    name: str = Field(description="Short English name of the place.")
    look: str = Field(description="English description: layout, furniture, colours, light.")


class Beat(BaseModel):
    start: float
    end: float
    label: str = Field(description="hook, setup, conflict, rising tension, twist, climax, resolution, call to action...")
    what_vi: str = Field(description="What happens, in Vietnamese.")


class VideoAnalysis(BaseModel):
    title_vi: str = Field(description="Short Vietnamese name for this video.")
    summary_vi: str = Field(description="3-5 sentences in Vietnamese: what the video is and how it is made.")
    genre_vi: str = Field(description="Format / genre, in Vietnamese (phim ngắn drama dọc, quảng cáo, vlog...).")
    hook_vi: str = Field(description="How the first 3 seconds grab attention, in Vietnamese.")
    why_it_works_vi: str = Field(default="", description="Why it works on its audience, in Vietnamese.")
    shots: list[ShotNote] = Field(default_factory=list, description="One entry per numbered shot on the sheets.")
    rhythm_vi: str = Field(description="Editing rhythm in Vietnamese: shot lengths, where it speeds up / slows down, "
                                       "cutting on beat or on action, use of the measured numbers.")
    transitions: list[TransitionUse] = Field(default_factory=list)
    camera_vi: str = Field(description="Framing and camera moves overall, in Vietnamese.")
    look: LookNotes
    sound: SoundNotes
    text: TextStyle
    characters: list[Person] = Field(default_factory=list)
    settings: list[Place] = Field(default_factory=list)
    beats: list[Beat] = Field(default_factory=list, description="Story beats in order, covering the whole video.")
    edit_style: str = Field(description="An English guide for an AI film editor who must cut a NEW film in the same "
                            "style: 6-12 short bullet lines about rhythm (target shot lengths, when to cut faster), "
                            "transitions (which, how long, where), fades in/out, music (mood, level, where it rises "
                            "or drops, ducking under speech), sound effects (which, where), colour look, subtitle style "
                            "and the opening hook. Concrete numbers, no names from the video.")
    film_recipe_vi: str = Field(description="How to make a new film like this, in Vietnamese. Follow the SCENE BUDGET "
                                            "in the brief exactly: same number of scenes, and for each scene its "
                                            "on-screen length after trimming (the lengths add up to the reference "
                                            "length), its shot type and what to prompt. Veo renders 4-8 s per clip; "
                                            "the editor keeps only that many seconds of each.")
    idea_suggestion_vi: str = Field(description="A NEW original story idea in the same format and genre "
                                                "(not a copy), 2-3 sentences in Vietnamese.")


SYSTEM = """You are a film analyst and editor. The user wants to make a NEW short AI film (scenes generated
with Google Veo, 4-8 s per clip, then cut by an editor agent) that feels like a reference video they found
online. You get measurements of the reference (ffmpeg, maybe rea-video) and contact sheets with frames of every
shot (each shot is labelled #n; on 3-column sheets the columns are start / middle / end of the shot).

Study it like a professional: the story and its beats, the hook, every shot (size, angle, camera move, subject,
how it starts, text burnt in), the editing rhythm (use the measured shot lengths, they are exact), the
transitions, the colour and light, the sound (you cannot hear it: use the loudness curve, the sound moments,
rea-video's audio notes and the subtitles; say when something is inferred), the subtitles and titles, the
characters and places.

Then turn it into tools for the new film: `look.style_prompt` (English, appended to every Veo prompt),
`edit_style` (English guide for the editor agent, with concrete numbers), `film_recipe_vi` and a new idea.
The new film must be as long as the reference, not longer: `film_recipe_vi` uses the SCENE BUDGET from the brief
(number of scenes and each scene's on-screen seconds, which add up to the reference length). Never pad a short
reference with extra scenes, and never present 4-8 s as the length a scene stays on screen.
Describe people by look and role only; never name or identify real people, never copy brand names or the
exact dialogue. Fields ending in _vi are for the user and are written in Vietnamese; the others in English.
Return one `shots` entry per labelled shot, in order."""


# --- small helpers ------------------------------------------------------------


def _need(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise AnalysisError(f"cần cài '{tool}' (ffmpeg) để phân tích video")
    return path


def _run(cmd: list[str], *, timeout: float, should_stop: StopCheck = lambda: False,
         cwd: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    """subprocess.run that the Stop button can interrupt."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=out, stderr=err, cwd=cwd, env=env)
        end = time.time() + timeout
        while proc.poll() is None:
            if should_stop():
                proc.kill()
                proc.wait()
                raise AnalysisStopped()
            if time.time() > end:
                proc.kill()
                proc.wait()
                raise subprocess.TimeoutExpired(cmd, timeout)
            time.sleep(0.2)
        out.seek(0)
        err.seek(0)
        return subprocess.CompletedProcess(cmd, proc.returncode, out.read().decode("utf-8", "replace"),
                                           err.read().decode("utf-8", "replace"))


def new_id(title: str = "") -> str:
    import unicodedata

    text = title.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:30].strip("-")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = slug or "video"
    return f"{stamp}-{base}"


def fmt_time(sec: float) -> str:
    sec = max(0.0, float(sec))
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


# --- 1. fetch -----------------------------------------------------------------


def ytdlp_command() -> list[str] | None:
    """yt-dlp as a command: $YTDLP_BIN, `yt-dlp` on PATH, or the yt_dlp module of this Python."""
    env = os.environ.get("YTDLP_BIN")
    if env is not None:
        return [env] if env else None
    found = shutil.which("yt-dlp")
    if found:
        return [found]
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return None
    return [sys.executable, "-m", "yt_dlp"]


def is_url(source: str) -> bool:
    return urllib.parse.urlparse(source.strip()).scheme in ("http", "https")


def _direct_media(url: str) -> bool:
    return urllib.parse.urlparse(url).path.lower().endswith(VIDEO_EXTS)


def _download(url: str, dest: Path, should_stop: StopCheck) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (FilmMaker video analysis)"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (http/https checked by is_url)
        ctype = resp.headers.get("Content-Type", "")
        if not (ctype.startswith("video/") or ctype in ("application/octet-stream", "binary/octet-stream")
                or _direct_media(url)):
            raise AnalysisError(f"link không phải file video ({ctype or 'không rõ loại'})")
        size = 0
        with open(dest, "wb") as f:
            while chunk := resp.read(1 << 20):
                if should_stop():
                    raise AnalysisStopped()
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise AnalysisError("video quá lớn (trên 600 MB)")
                f.write(chunk)
    return dest


def _clean_subs(text: str) -> str:
    """VTT / SRT -> '[m:ss] line' lines, without the repeated lines of rolling auto captions."""
    out: list[str] = []
    stamp = ""
    last = ""
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"(\d+:)?(\d{1,2}):(\d{2})[.,]\d+\s*-->", line)
        if m:
            h = int((m.group(1) or "0:")[:-1])
            stamp = f"[{h * 60 + int(m.group(2))}:{m.group(3)}]"
            continue
        if not line or line.isdigit() or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE", "STYLE")):
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if line and line != last:
            out.append(f"{stamp} {line}".strip())
            last = line
    return "\n".join(out)


def _pick_auto_caption(info: dict) -> str | None:
    """The auto captions in the video's own language (YouTube names them '<lang>-orig')."""
    auto = info.get("automatic_captions") or {}
    for key in auto:
        if key.endswith("-orig"):
            return key
    lang = info.get("language")
    return lang if lang in auto else None


def fetch(source: str, folder: Path, *, allow_local: bool = True, log: Log = print,
          should_stop: StopCheck = lambda: False) -> dict[str, Any]:
    """Put the video in folder/source.<ext>. Returns {file, url, title, description, uploader, subtitles}."""
    folder.mkdir(parents=True, exist_ok=True)
    source = source.strip()
    info: dict[str, Any] = {"url": source, "title": "", "description": "", "uploader": "", "subtitles": ""}
    if not is_url(source):
        path = Path(source).expanduser()
        if not allow_local:
            raise AnalysisError("hãy dán một link http(s) tới video")
        if not path.is_file():
            raise AnalysisError(f"không thấy file video: {source}")
        dest = folder / f"source{path.suffix.lower() or '.mp4'}"
        shutil.copyfile(path, dest)
        info.update(file=dest.name, url="", title=path.stem)
        return info

    ytdlp = ytdlp_command()
    if _direct_media(source) or not ytdlp:
        if not ytdlp and not _direct_media(source):
            log("Chưa có yt-dlp nên thử tải thẳng link (link YouTube / TikTok / Facebook cần yt-dlp: "
                "brew install yt-dlp).")
        log("Đang tải video…")
        dest = folder / f"source{Path(urllib.parse.urlparse(source).path).suffix.lower() or '.mp4'}"
        if dest.suffix not in VIDEO_EXTS:
            dest = folder / "source.mp4"
        try:
            _download(source, dest, should_stop)
        except AnalysisError as exc:
            if not ytdlp:
                raise AnalysisError(f"{exc}. Cài yt-dlp (brew install yt-dlp) để tải video từ trang web.") from None
            raise
        except OSError as exc:
            raise AnalysisError(f"không tải được video: {exc}") from None
        info.update(file=dest.name, title=Path(urllib.parse.urlparse(source).path).stem)
        return info

    log("Đang tải video bằng yt-dlp (≤ 720p)…")
    fmt = ("bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/bv*[height<=720]+ba/b[height<=720]/b")
    cmd = [*ytdlp, "--no-playlist", "--no-progress", "--no-warnings", "-f", fmt, "--merge-output-format", "mp4",
           "--max-filesize", "600M", "--write-info-json", "--write-subs", "--sub-langs", "all,-live_chat",
           "--sub-format", "vtt/srt/best", "-o", str(folder / "source.%(ext)s"), "--", source]
    try:
        proc = _run(cmd, timeout=1800, should_stop=should_stop)
    except subprocess.TimeoutExpired:
        raise AnalysisError("yt-dlp tải video quá lâu") from None
    videos = [p for p in folder.glob("source.*") if p.suffix.lower() in VIDEO_EXTS]
    if proc.returncode != 0 or not videos:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        raise AnalysisError("yt-dlp không tải được video: " + (" ".join(tail)[-500:] or f"exit {proc.returncode}"))
    info["file"] = videos[0].name
    meta: dict = {}
    for p in folder.glob("source*.info.json"):
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            meta = {}
        p.unlink(missing_ok=True)  # big (formats, thumbnails); what we need is kept in analysis.json
        break
    info.update(title=meta.get("title") or "", description=(meta.get("description") or "")[:3000],
                uploader=meta.get("uploader") or meta.get("channel") or "", url=meta.get("webpage_url") or source,
                tags=(meta.get("tags") or [])[:20])
    subs = sorted(folder.glob("source*.vtt")) + sorted(folder.glob("source*.srt"))
    if not subs and (key := _pick_auto_caption(meta)):
        log(f"Lấy phụ đề tự động ({key}) để biết lời thoại…")
        try:
            _run([*ytdlp, "--no-playlist", "--skip-download", "--no-warnings", "--write-auto-subs",
                  "--sub-langs", key, "--sub-format", "vtt/best", "-o", str(folder / "source.%(ext)s"), "--", source],
                 timeout=300, should_stop=should_stop)
        except subprocess.TimeoutExpired:
            pass
        subs = sorted(folder.glob("source*.vtt")) + sorted(folder.glob("source*.srt"))
    if subs:
        info["subtitles"] = _clean_subs(subs[0].read_text(encoding="utf-8", errors="replace"))[:SUBS_MAX]
        info["subtitles_file"] = subs[0].name
    return info


# --- 2. measure ---------------------------------------------------------------


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run([_need("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, timeout=60)
    try:
        data = json.loads(out.stdout or "{}")
    except ValueError:
        data = {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise AnalysisError("file không có hình (không phải video?)")
    w, h = int(video.get("width") or 0), int(video.get("height") or 0)
    rot = 0
    for sd in video.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    rot = int((video.get("tags") or {}).get("rotate", rot) or 0)
    if abs(rot) % 180 == 90:
        w, h = h, w
    num, _, den = str(video.get("r_frame_rate") or "0/1").partition("/")
    try:
        fps = round(float(num) / float(den or 1), 2)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise AnalysisError("không đọc được độ dài video")
    return {"duration": round(duration, 3), "width": w, "height": h, "fps": fps,
            "aspect": "9:16" if h > w else "16:9", "ratio": ratio_label(w, h), "has_audio": any(s.get("codec_type") == "audio" for s in streams)}


def ratio_label(w: int, h: int) -> str:
    """The frame shape as people name it: 16:9, 9:16, 4:3, 1:1, 2.39:1 ..."""
    if not w or not h:
        return "?"
    r = w / h
    for name, val in (("16:9", 16 / 9), ("9:16", 9 / 16), ("4:3", 4 / 3), ("3:4", 3 / 4), ("1:1", 1.0),
                      ("4:5", 4 / 5), ("1.85:1", 1.85), ("2.39:1", 2.39)):
        if abs(r - val) / val < 0.02:
            return name
    return f"{r:.2f}:1"


def parse_showinfo(stderr: str) -> list[float]:
    return sorted({round(float(m), 3) for m in re.findall(r"pts_time:\s*([\d.]+)", stderr)})


def detect_cuts(path: Path, duration: float, *, threshold: float = SCENE_THRESHOLD,
                should_stop: StopCheck = lambda: False) -> list[float]:
    proc = _run([_need("ffmpeg"), "-hide_banner", "-nostats", "-t", str(min(duration, MAX_SECONDS)), "-i", str(path),
                 "-an", "-vf", f"scale=256:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
                timeout=900, should_stop=should_stop)
    return parse_showinfo(proc.stderr)


def shots_from_cuts(cuts: list[float], duration: float, *, min_shot: float = MIN_SHOT) -> list[tuple[float, float]]:
    """Cut times -> [(start, end)] covering the whole (analysed) video; too-short shots are merged."""
    end_all = min(duration, MAX_SECONDS)
    bounds = [0.0]
    for c in sorted(cuts):
        if min_shot <= c <= end_all - min_shot and c - bounds[-1] >= min_shot:
            bounds.append(round(c, 3))
    bounds.append(round(end_all, 3))
    return list(zip(bounds[:-1], bounds[1:]))


def parse_blackdetect(stderr: str) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)", stderr)]


def detect_black(path: Path, duration: float, should_stop: StopCheck = lambda: False) -> list[tuple[float, float]]:
    proc = _run([_need("ffmpeg"), "-hide_banner", "-nostats", "-t", str(min(duration, MAX_SECONDS)), "-i", str(path),
                 "-an", "-vf", "scale=256:-2,blackdetect=d=0.08:pix_th=0.10", "-f", "null", "-"],
                timeout=900, should_stop=should_stop)
    return parse_blackdetect(proc.stderr)


def parse_ebur128(stderr: str) -> list[tuple[float, float]]:
    """[(t, momentary loudness LUFS)] from ffmpeg's ebur128 output: the `ametadata` print (pts_time:… then
    lavfi.r128.M=…) or the older verbose log lines (t: … TARGET: … M: …)."""
    pts = re.findall(r"pts_time:\s*([\d.]+)\s*\n.*?lavfi\.r128\.M=(-?[\d.]+|-?inf)", stderr)
    if not pts:
        pts = re.findall(r"t:\s*([\d.]+)\s+TARGET:.*?M:\s*(-?[\d.]+)", stderr)
    return [(float(t), float(m) if "inf" not in m else -120.0) for t, m in pts]


def per_second(points: list[tuple[float, float]], duration: float) -> list[float]:
    """Momentary loudness -> one value per second (mean of the points in it), floor -70 LUFS."""
    n = max(1, int(min(duration, MAX_SECONDS) + 0.999))
    buckets: list[list[float]] = [[] for _ in range(n)]
    for t, m in points:
        i = min(n - 1, int(max(0.0, t - 0.0001)))
        buckets[i].append(max(-70.0, m))
    out, prev = [], -70.0
    for b in buckets:
        prev = round(sum(b) / len(b), 1) if b else prev
        out.append(prev)
    return out


def loudness_curve(path: Path, duration: float, should_stop: StopCheck = lambda: False) -> list[float]:
    proc = _run([_need("ffmpeg"), "-hide_banner", "-nostats", "-t", str(min(duration, MAX_SECONDS)), "-i", str(path),
                 "-vn", "-af", "ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M", "-f", "null", "-"], timeout=900, should_stop=should_stop)
    return per_second(parse_ebur128(proc.stderr), duration)


def sound_moments(curve: list[float], *, jump: float = 6.0, silence: float = -45.0) -> list[dict[str, Any]]:
    """Where the sound clearly rises, drops, goes silent or comes back (per-second loudness)."""
    out: list[dict[str, Any]] = []
    if len(curve) < 3:
        return out
    smooth = [statistics.fmean(curve[max(0, i - 1):i + 2]) for i in range(len(curve))]
    silence = min(silence, statistics.median(curve) - 15)  # a quiet video is not "silent" all the time
    quiet = smooth[0] < silence
    last_at = -10
    for i in range(2, len(smooth)):
        before = statistics.fmean(smooth[max(0, i - 3):i])
        now = smooth[i]
        if quiet and now >= silence:
            out.append({"at": float(i), "kind": "sound_in", "db": round(now, 1)})
            quiet, last_at = False, i
        elif not quiet and now < silence:
            out.append({"at": float(i), "kind": "silence", "db": round(now, 1)})
            quiet, last_at = True, i
        elif i - last_at >= 3 and abs(now - before) >= jump:
            out.append({"at": float(i), "kind": "rise" if now > before else "drop", "db": round(now, 1),
                        "change": round(now - before, 1)})
            last_at = i
    return out


def rhythm_stats(shots: list[tuple[float, float]]) -> dict[str, Any]:
    lens = [round(b - a, 3) for a, b in shots]
    if not lens:
        return {}
    total = shots[-1][1] - shots[0][0]
    q = max(1, len(shots))
    quarters = []
    for k in range(4):
        lo, hi = total * k / 4, total * (k + 1) / 4
        inside = [b - a for a, b in shots if lo <= (a + b) / 2 < hi]
        quarters.append(round(statistics.fmean(inside), 2) if inside else None)
    return {
        "shots": q, "average": round(statistics.fmean(lens), 2), "median": round(statistics.median(lens), 2),
        "shortest": round(min(lens), 2), "longest": round(max(lens), 2),
        "cuts_per_minute": round((q - 1) / total * 60, 1) if total > 0 else 0,
        "first_3s_shots": sum(1 for a, _ in shots if a < 3.0),
        "average_by_quarter": quarters,
    }


def measure(video: Path, *, log: Log = print, should_stop: StopCheck = lambda: False) -> dict[str, Any]:
    info = probe(video)
    d = info["duration"]
    if d > MAX_SECONDS:
        log(f"Video dài {fmt_time(d)}: chỉ phân tích {MAX_SECONDS // 60} phút đầu.")
    log("Tìm các điểm cắt cảnh (ffmpeg)…")
    shots = shots_from_cuts(detect_cuts(video, d, should_stop=should_stop), d)
    log(f"  {len(shots)} shot.")
    blacks = detect_black(video, d, should_stop=should_stop)
    curve: list[float] = []
    moments: list[dict] = []
    if info["has_audio"]:
        log("Đo độ to của âm thanh theo từng giây…")
        curve = loudness_curve(video, d, should_stop=should_stop)
        moments = sound_moments(curve)
    return {**info, "shots": shots, "blacks": blacks, "loudness": curve, "moments": moments,
            "shot_source": "ffmpeg"}


# --- 3. rea-video (optional) --------------------------------------------------


def rea_binary() -> str | None:
    """$REA_VIDEO_BIN (empty = off), ~/FilmMaker/.tools/rea-video/.venv/bin/rea-video, or rea-video on PATH."""
    env = os.environ.get("REA_VIDEO_BIN")
    if env is not None:
        return env or None
    local = REA_HOME / ".venv" / "bin" / "rea-video"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return shutil.which("rea-video")


def _front_matter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        parts = text.split("\n---", 2)
        if len(parts) >= 2:
            try:
                data = yaml.safe_load(parts[0][3:]) or {}
            except yaml.YAMLError:
                data = {}
            body = parts[1].split("\n", 1)[1] if "\n" in parts[1] else ""
            if len(parts) == 3:
                body += parts[2]
            return (data if isinstance(data, dict) else {}), body
    return {}, text


def parse_timeline(text: str) -> list[dict[str, Any]]:
    """rea-video's timeline: one line per shot inside a ```videomd-timeline block (video/timeline.md), or
    ```reel-timeline (reel/timeline.md) in rea-video versions before the VIDEO.md format."""
    m = re.search(r"```(?:videomd|reel)-timeline\n(.*?)(?:```|\Z)", text, re.S)
    rows = []
    for line in (m.group(1) if m else "").splitlines():
        mm = re.match(r"#(\d+)\s+([\d.]+)-([\d.]+)\s*(.*)$", line.strip())
        if not mm:
            continue
        row: dict[str, Any] = {"shot": int(mm.group(1)), "start": float(mm.group(2)), "end": float(mm.group(3))}
        for part in mm.group(4).split(" | "):
            part = part.strip().lstrip("|").strip()
            if part:
                key, _, val = part.partition(" ")
                row[key] = val.strip()
        rows.append(row)
    return rows


# rea-video output: the card file at the top and one Markdown file per part in a folder. The current format is
# VIDEO.md + video/ (https://github.com/videomd/videomd); older versions wrote REEL.md + reel/.
REA_LAYOUTS = (("VIDEO.md", "video"), ("REEL.md", "reel"))
REA_PARTS = ("timeline", "content", "cast", "sound", "look", "packaging")


def read_reel(out: Path) -> dict[str, Any] | None:
    layout = next(((card, part) for card, part in REA_LAYOUTS if (out / card).is_file()), None)
    if not layout:
        return None
    card_name, part_dir = layout
    card, _ = _front_matter((out / card_name).read_text(encoding="utf-8", errors="replace"))
    texts, full = {}, {}
    for name in (card_name, *(f"{part_dir}/{part}.md" for part in REA_PARTS)):
        p = out / name
        if p.is_file():
            full[name] = p.read_text(encoding="utf-8", errors="replace")
            texts[name] = full[name][:REA_TEXT_MAX]
    # Parse the whole timeline: a long video's timeline is far longer than what is sent to Claude.
    timeline = parse_timeline(full.get(f"{part_dir}/timeline.md", ""))
    return {"card": card, "texts": texts, "timeline": timeline}


def run_rea(video: Path, out: Path, *, log: Log = print, should_stop: StopCheck = lambda: False,
            timeout: int = 1800) -> dict[str, Any] | None:
    """rea-video decompose; None (and a log line) when it is missing or fails. Never raises except on Stop."""
    binary = rea_binary()
    if not binary:
        log("rea-video chưa cài: bỏ qua lớp phân tích sâu (tách nhạc/giọng/hiệu ứng, BPM, phụ đề). "
            "Xem README mục Phân tích video để cài.")
        return None
    log("rea-video đang mổ xẻ video (cắt shot, chuyển động máy, tách giọng / nhạc / hiệu ứng, chữ trên hình)… "
        "lần đầu tải model ~200 MB, có thể mất vài phút.")
    try:
        proc = _run([binary, "decompose", str(video), "-o", str(out), "--lang", "en"], timeout=timeout,
                    should_stop=should_stop)
    except subprocess.TimeoutExpired:
        log("⚠ rea-video chạy quá lâu, bỏ qua.")
        return None
    except OSError as exc:
        log(f"⚠ Không chạy được rea-video ({exc}), bỏ qua.")
        return None
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-2:]
        log(f"⚠ rea-video lỗi (exit {proc.returncode}): {' '.join(tail)[-300:]} — tiếp tục không có nó.")
    reel = read_reel(out)
    if reel:
        card = reel["card"]
        log(f"  rea-video: {card.get('shots', len(reel['timeline']))} shot — {card.get('description', '')}")
    elif proc.returncode == 0:
        log(f"⚠ rea-video chạy xong nhưng không thấy {' / '.join(c for c, _ in REA_LAYOUTS)} trong {out} "
            "(định dạng đầu ra mới?) — tiếp tục không có nó.")
    return reel


# --- 4. contact sheets + Claude -----------------------------------------------


def _font() -> str | None:
    for f in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if Path(f).is_file():
            return f
    return None


def contact_sheets(video: Path, shots: list[tuple[float, float]], folder: Path, *, aspect: str = "16:9",
                   should_stop: StopCheck = lambda: False) -> list[dict[str, Any]]:
    """Sheets of numbered frames: [{"file": name, "shots": [n...], "columns": 3 | 4}].
    Up to 30 shots: one row per shot (start, middle, end). More: one middle frame per shot, 4 per row."""
    ffmpeg = _need("ffmpeg")
    folder.mkdir(parents=True, exist_ok=True)
    cw, ch = (192, 342) if aspect == "9:16" else (384, 216)
    per_shot = 3 if len(shots) <= 30 else 1
    cols, rows = (3, 4) if per_shot == 3 else (4, 4 if aspect == "9:16" else 5)
    per_sheet = rows if per_shot == 3 else cols * rows
    font = _font()
    sheets = []
    numbered = list(enumerate(shots, 1))[:160]
    for k in range(0, len(numbered), per_sheet):
        group = numbered[k:k + per_sheet]
        with tempfile.TemporaryDirectory() as tmp:
            i = 0
            for n, (a, b) in group:
                fracs = (0.12, 0.5, 0.88) if per_shot == 3 else (0.5,)
                for j, frac in enumerate(fracs):
                    if should_stop():
                        raise AnalysisStopped()
                    i += 1
                    vf = f"scale={cw}:{ch}:force_original_aspect_ratio=decrease,pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2"
                    if font and j == 0:
                        vf += (f",drawtext=fontfile='{font}':text='#{n}':x=6:y=6:fontsize=22:fontcolor=white:"
                               "box=1:boxcolor=black@0.65:boxborderw=4")
                    t = a + (b - a) * frac
                    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video),
                                    "-frames:v", "1", "-vf", vf, "-q:v", "4", str(Path(tmp) / f"{i:03}.jpg")],
                                   capture_output=True, timeout=60)
                    if not (Path(tmp) / f"{i:03}.jpg").is_file():  # past the last frame: black cell
                        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                                        f"color=black:s={cw}x{ch}", "-frames:v", "1", str(Path(tmp) / f"{i:03}.jpg")],
                                       capture_output=True, timeout=60)
            used_rows = rows if per_shot == 1 else len(group)
            if per_shot == 1:
                used_rows = (len(group) + cols - 1) // cols
            name = f"sheet{k // per_sheet + 1:02}.jpg"
            proc = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(Path(tmp) / "%03d.jpg"),
                                   "-frames:v", "1", "-vf", f"tile={cols}x{used_rows}:padding=4:color=white",
                                   "-q:v", "4", str(folder / name)], capture_output=True, timeout=60)
        if proc.returncode == 0 and (folder / name).is_file():
            sheets.append({"file": name, "shots": [n for n, _ in group], "columns": cols if per_shot == 1 else 3})
    return sheets


def _segments(m: dict) -> list[dict[str, Any]]:
    d = float(m.get("duration") or 0)
    end_all = min(d, MAX_SECONDS) if d > 0 else 0
    out: list[dict[str, Any]] = []
    for n, s in enumerate(m.get("shots") or [], 1):
        a, b = (s["start"], s["end"]) if isinstance(s, dict) else s
        num = s.get("n", n) if isinstance(s, dict) else n
        if end_all and a >= end_all:
            break
        b = min(b, end_all) if end_all else b
        if b > a:
            out.append({"shots": [num], "start": float(a), "end": float(b)})
    if out and end_all - out[-1]["end"] > 0.3:  # cut detection stopped early: the tail is one more stretch
        out.append({"shots": [], "start": out[-1]["end"], "end": end_all})
    elif out:
        out[-1]["end"] = max(out[-1]["end"], end_all)
    if not out and end_all > 0:
        out.append({"shots": [], "start": 0.0, "end": end_all})
    return out


def _join(segs: list[dict], i: int) -> None:
    a, b = segs[i], segs.pop(i + 1)
    a["shots"], a["end"] = a["shots"] + b["shots"], b["end"]


def scene_budget(m: dict, n: int | None = None) -> list[dict[str, Any]]:
    """How the reference's length splits into new-film scenes: [{shots, start, end, length}], in order.

    Every scene is one Veo clip (4-8 s) trimmed to `length`; the lengths add up to the reference duration,
    so a 15 s reference gives a ~15 s film. Neighbouring shots shorter than MIN_SCENE_CUT are merged, a stretch
    longer than one clip is split, and the count is capped at MAX_SCENES (or forced to `n`).
    """
    segs = _segments(m)
    if not segs:
        return []
    # stretches longer than one clip -> several scenes
    split: list[dict] = []
    for g in segs:
        length = g["end"] - g["start"]
        parts = max(1, math.ceil(length / MAX_SCENE_CUT - 1e-9))
        for k in range(parts):
            split.append({"shots": list(g["shots"]), "start": g["start"] + length * k / parts,
                          "end": g["start"] + length * (k + 1) / parts})
    segs = split
    if n:  # a chosen count: keep the reference's own shot boundaries as far as possible
        target = max(1, min(MAX_SCENES, n))
    else:  # the natural count: every scene at least MIN_SCENE_CUT on screen
        while len(segs) > 1:  # too-short scenes join their shorter neighbour
            lens = [g["end"] - g["start"] for g in segs]
            i = min(range(len(segs)), key=lambda k: lens[k])
            if lens[i] >= MIN_SCENE_CUT:
                break
            if i == 0:
                _join(segs, 0)
            elif i == len(segs) - 1:
                _join(segs, i - 1)
            else:
                _join(segs, i - 1 if lens[i - 1] <= lens[i + 1] else i)
        target = min(MAX_SCENES, len(segs))

    def pair_key(k: int) -> tuple[bool, float]:  # merge a pair with a too-short scene first, then the shortest
        a, b = segs[k]["end"] - segs[k]["start"], segs[k + 1]["end"] - segs[k + 1]["start"]
        return (min(a, b) >= MIN_SCENE_CUT, a + b)

    while len(segs) > target:
        _join(segs, min(range(len(segs) - 1), key=pair_key))
    while len(segs) < target:  # split the longest scene in two
        i = max(range(len(segs)), key=lambda k: segs[k]["end"] - segs[k]["start"])
        g = segs[i]
        mid = (g["start"] + g["end"]) / 2
        segs[i:i + 1] = [{"shots": list(g["shots"]), "start": g["start"], "end": mid},
                         {"shots": list(g["shots"]), "start": mid, "end": g["end"]}]
    for g in segs:
        g["shots"] = sorted(set(g["shots"]))
        g["start"], g["end"] = round(g["start"], 2), round(g["end"], 2)
        g["length"] = round(g["end"] - g["start"], 2)
    return segs


def budget_text(budget: list[dict]) -> str:
    """English lines for the planner / editor: the on-screen length of each new scene."""
    if not budget:
        return ""
    total = sum(b["length"] for b in budget)
    return (f"- Target length: about {total:.0f}s in total, like the reference (do not make it longer). "
            f"{len(budget)} scene{'' if len(budget) == 1 else 's'}; keep each on screen for: "
            + ", ".join(f"s{i:02d} {b['length']:.1f}s" for i, b in enumerate(budget, 1))
            + ". Each scene is ONE simple action that reads in that time; its 4-8 s clip is trimmed to it.")


def recipe_timing_vi(data: dict, n: int | None = None) -> str:
    """Vietnamese summary of the scene budget, shown above Claude's recipe (exact, from the measurements)."""
    m = data.get("measured") or {}
    budget = scene_budget(m, n)
    if not budget:
        return ""
    total = sum(b["length"] for b in budget)
    parts = []
    for i, b in enumerate(budget, 1):
        src = (" (shot " + ", ".join(f"#{k}" for k in b["shots"]) + ")") if b["shots"] else ""
        parts.append(f"{i}) {b['length']:.1f}s{src}")
    return (f"Khớp video gốc {total:.0f}s: {len(budget)} cảnh, thời lượng giữ lại của từng cảnh sau khi dựng: "
            + "; ".join(parts) + f". Tổng ≈ {total:.1f}s. Veo tạo mỗi clip 4–8s, khi dựng chỉ giữ đúng đoạn trên.")


def _brief(src: dict, m: dict, reel: dict | None) -> str:
    st = rhythm_stats(m["shots"])
    lines = [f"Reference video: {src.get('title') or '(no title)'}"
             + (f" by {src['uploader']}" if src.get("uploader") else ""),
             f"Length {m['duration']:.1f} s, {m['width']}x{m['height']} ({m.get('ratio') or m['aspect']}; the new film will be {m['aspect']}), {m['fps']} fps, "
             f"audio: {'yes' if m['has_audio'] else 'no'}"]
    if src.get("description"):
        lines.append("Description: " + " ".join(src["description"].split())[:800])
    lines.append(f"\nShots ({m.get('shot_source', 'ffmpeg')} cut detection, exact): " + json.dumps(st))
    lines.append("Shot list (#n start-end length):")
    lines += [f"#{n} {a:.2f}-{b:.2f} {b - a:.2f}s" for n, (a, b) in enumerate(m["shots"], 1)]
    if m.get("blacks"):
        lines.append("Black frames (fades / dips to black): "
                     + ", ".join(f"{a:.1f}-{b:.1f}s" for a, b in m["blacks"][:30]))
    if m.get("loudness"):
        lines.append("Loudness per second (LUFS, -70 = silence): " + " ".join(f"{v:.0f}" for v in m["loudness"]))
    if m.get("moments"):
        lines.append("Sound moments: " + "; ".join(
            f"{x['at']:.0f}s {x['kind']}" + (f" ({x['change']:+.0f} LU)" if "change" in x else "")
            for x in m["moments"][:40]))
    budget = scene_budget(m)
    if budget:
        lines.append(f"\nSCENE BUDGET for the new film (must total {m['duration']:.1f} s like the reference; use "
                     f"exactly {len(budget)} scenes in film_recipe_vi, each kept on screen this long after trimming "
                     "its 4-8 s Veo clip):")
        lines += [f"scene {i}: {b['length']:.1f}s, from reference shot(s) "
                  + ", ".join(f"#{n}" for n in b["shots"]) if b["shots"] else f"scene {i}: {b['length']:.1f}s"
                  for i, b in enumerate(budget, 1)]
    if src.get("subtitles"):
        lines.append("\nSubtitles / dialogue from the site (timed):\n" + src["subtitles"])
    if reel:
        lines.append("\nrea-video analysis (a forensic tool; 'made~' says how a shot was produced, ignore that "
                     "unless useful; its audio and caption notes are measured from the stems):")
        for name, text in reel["texts"].items():
            lines.append(f"--- {name} ---\n{text.strip()}")
    return "\n".join(lines)


def _sheet_text(sheets: list[dict]) -> str:
    rows = []
    for s in sheets:
        if s["columns"] == 3:
            rows.append(f"- {s['file']}: one row per shot, top to bottom = "
                        + ", ".join(f"#{n}" for n in s["shots"]) + " (columns: start, middle, end)")
        else:
            rows.append(f"- {s['file']}: one middle frame per shot, left to right then down = "
                        + ", ".join(f"#{n}" for n in s["shots"]))
    return "\n".join(rows)


def ask_claude(brief: str, sheets: list[dict], folder: Path, *, backend: str = "auto", api_key: str | None = None,
               timeout: int = 1200, ai_model: str | None = None, gemini_api_key: str | None = None,
               log: Log = print, should_stop: StopCheck = lambda: False) -> VideoAnalysis:
    from . import ai as ai_mod
    from .planner import MODEL, _subscription_env, claude_code_binary

    user = brief + "\n\nWrite the analysis."
    if sheets:
        user += ("\n\nContact sheets (image files in the current folder; open EVERY one with the Read tool before "
                 "answering; the shot number is printed on the first frame of each shot):\n" + _sheet_text(sheets))
    if ai_mod.effective_ai_model(ai_model) == ai_mod.GEMINI_PRO or backend in ("gemini_api", "gemini_cli"):
        chosen = ai_mod.pick_backend(backend, ai_model=ai_mod.GEMINI_PRO, gemini_api_key=gemini_api_key)
        if not chosen:
            raise AnalysisError(ai_mod.not_ready_message(ai_mod.GEMINI_PRO))
        log(f"Gemini Pro ({'Gemini CLI' if chosen == 'gemini_cli' else 'API'}) đang xem {len(sheets)} bảng ảnh "
            "và viết bản phân tích… (thường 2–6 phút)")
        try:
            return ai_mod.ask(SYSTEM, user, VideoAnalysis,
                              images=[(s["file"], folder / s["file"]) for s in sheets],
                              backend=chosen, timeout=timeout, ai_model=ai_mod.GEMINI_PRO,
                              gemini_api_key=gemini_api_key)
        except RuntimeError as exc:
            raise AnalysisError(str(exc)) from None
    if backend == "auto":
        backend = "claude_code" if claude_code_binary() else "api"
    log(f"Claude ({'gói Claude Code' if backend == 'claude_code' else 'API'}) đang xem {len(sheets)} bảng ảnh "
        "và viết bản phân tích… (thường 2–6 phút)")
    if backend == "claude_code":
        binary = claude_code_binary()
        if not binary:
            raise AnalysisError("không thấy lệnh `claude` (Claude Code)")
        cmd = [binary, "-p", user, "--output-format", "json", "--json-schema",
               json.dumps(VideoAnalysis.model_json_schema()), "--system-prompt", SYSTEM,
               "--tools", "Read" if sheets else "", "--strict-mcp-config", "--no-session-persistence"]
        if sheets:
            cmd += ["--allowedTools", "Read"]
        try:
            proc = _run(cmd, timeout=timeout, should_stop=should_stop, cwd=str(folder), env=_subscription_env())
        except subprocess.TimeoutExpired:
            raise AnalysisError("Claude Code phân tích quá lâu; hãy thử lại.") from None
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            raise AnalysisError(f"Claude Code lỗi (exit {proc.returncode}): "
                                f"{(proc.stderr or proc.stdout).strip()[-400:]}") from None
        if data.get("is_error") or not data.get("structured_output"):
            raise AnalysisError("Claude Code không phân tích được: " + str(data.get("result") or "không có kết quả")[:400])
        return VideoAnalysis.model_validate(data["structured_output"])

    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AnalysisError("chưa có ANTHROPIC_API_KEY (hoặc cài Claude Code để dùng gói Claude của bạn)")
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise AnalysisError("cần cài thêm: pip install 'film-maker-ai[ai]' (gói anthropic)") from exc
    import base64

    content: list[dict] = [{"type": "text", "text": user.replace(
        "image files in the current folder; open EVERY one with the Read tool before answering",
        "attached below in this order")}]
    for s in sheets:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.standard_b64encode((folder / s["file"]).read_bytes()).decode()}})
    response = anthropic.Anthropic().messages.parse(
        model=MODEL, max_tokens=32000, system=SYSTEM, messages=[{"role": "user", "content": content}],
        output_format=VideoAnalysis, output_config={"effort": "medium"},
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"}, extra_body={"fallbacks": "default"},
    )
    if response.stop_reason == "refusal" or not response.parsed_output:
        raise AnalysisError("Claude không phân tích được video này.")
    return response.parsed_output


# --- 5. the whole job ---------------------------------------------------------


def measured_edit_style(m: dict) -> str:
    """The guide lines that come straight from the measurements (exact numbers)."""
    st = rhythm_stats(m["shots"])
    if not st:
        return ""
    q = [f"{v:.1f}s" if v else "-" for v in st["average_by_quarter"]]
    lines = [f"- Measured on the reference ({m['duration']:.0f}s): {st['shots']} shots, average shot {st['average']}s "
             f"(median {st['median']}s, {st['shortest']}-{st['longest']}s), {st['cuts_per_minute']} cuts per minute; "
             f"average shot by quarter: {', '.join(q)}."]
    if st["first_3s_shots"] >= 2:
        lines.append(f"- The first 3 seconds have {st['first_3s_shots']} shots: open fast.")
    if m.get("blacks"):
        lines.append(f"- {len(m['blacks'])} dips to black (fades) in the reference.")
    return "\n".join(lines)


def edit_style_text(data: dict) -> str:
    ai = data.get("ai") or {}
    parts = [measured_edit_style(_measured(data)), (ai.get("edit_style") or "").strip()]
    look = ai.get("look") or {}
    sound = ai.get("sound") or {}
    if sound.get("music_query"):
        parts.append(f"- Music like the reference: {sound['music_query']}.")
    if look.get("grade_vi"):
        parts.append(f"- Colour look (Vietnamese note): {look['grade_vi']}")
    return "\n".join(p for p in parts if p)


def _measured(data: dict) -> dict:
    m = dict(data.get("measured") or {})
    m["shots"] = [(s["start"], s["end"]) for s in m.get("shots") or []]
    return m


def analysis_path(folder: Path) -> Path:
    return folder / "analysis.json"


def load(folder: Path) -> dict[str, Any]:
    return json.loads(analysis_path(folder).read_text(encoding="utf-8"))


def analyze(source: str, folder: Path, *, use_rea: bool = True, backend: str = "auto", api_key: str | None = None,
            ai_model: str | None = None, gemini_api_key: str | None = None,
            allow_local: bool = True, log: Log = print, should_stop: StopCheck = lambda: False) -> dict[str, Any]:
    """Run every step and save folder/analysis.json + report.md. Returns the saved data."""
    from . import ai as ai_mod

    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(json.dumps({"source": source, "started": time.time()}), encoding="utf-8")
    src = fetch(source, folder, allow_local=allow_local, log=log, should_stop=should_stop)
    video = folder / src["file"]
    m = measure(video, log=log, should_stop=should_stop)
    log(f"Video {fmt_time(m['duration'])}, {m['width']}x{m['height']} ({m.get('ratio') or m['aspect']}), {len(m['shots'])} shot.")
    reel = None
    if use_rea:
        reel = run_rea(video, folder / "rea", log=log, should_stop=should_stop)
        tl = (reel or {}).get("timeline") or []
        if len(tl) >= 1:
            shots = [(r["start"], r["end"]) for r in tl if r["end"] > r["start"]]
            if shots:
                m["shots"], m["shot_source"] = shots, "rea-video"
    else:
        log("Bỏ qua rea-video (đã tắt).")
    log(f"Trích ảnh từng shot cho {ai_mod.ai_model_label(ai_model)} xem…")
    sheets = contact_sheets(video, m["shots"], folder / "frames", aspect=m["aspect"], should_stop=should_stop)
    brief = _brief(src, m, reel)
    (folder / "brief.txt").write_text(brief, encoding="utf-8")
    extra_kw: dict[str, Any] = {}
    if ai_model:
        extra_kw["ai_model"] = ai_model
    if gemini_api_key:
        extra_kw["gemini_api_key"] = gemini_api_key
    ai = ask_claude(brief, sheets, folder / "frames", backend=backend, api_key=api_key, log=log,
                    should_stop=should_stop, **extra_kw)
    data = {
        "version": 1, "id": folder.name, "created": time.time(),
        **({"ai_model": ai_mod.GEMINI_PRO} if ai_mod.effective_ai_model(ai_model) == ai_mod.GEMINI_PRO else {}),
        "source": {k: v for k, v in src.items() if k != "subtitles"},
        "subtitles": src.get("subtitles", ""),
        "measured": {**{k: v for k, v in m.items() if k != "shots"},
                     "shots": [{"n": n, "start": a, "end": b, "length": round(b - a, 3)}
                               for n, (a, b) in enumerate(m["shots"], 1)],
                     "stats": rhythm_stats(m["shots"])},
        "rea": ({"used": True, "card": reel["card"], "timeline": reel["timeline"]} if reel else {"used": False}),
        "sheets": [{**s, "file": f"frames/{s['file']}"} for s in sheets],
        "ai": ai.model_dump(),
    }
    data["edit_style"] = edit_style_text(data)
    analysis_path(folder).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (folder / "report.md").write_text(report_markdown(data), encoding="utf-8")
    log(f"✔ Phân tích xong: “{ai.title_vi}” — {len(m['shots'])} shot, {len(ai.beats)} nhịp truyện.")
    return data


def list_analyses(root: Path) -> list[dict[str, Any]]:
    out = []
    if not root.is_dir():
        return out
    for d in root.iterdir():
        p = analysis_path(d)
        if not p.is_file():
            continue
        try:
            data = load(d)
        except (OSError, ValueError):
            continue
        sheets = data.get("sheets") or []
        out.append({"id": d.name, "title": (data.get("ai") or {}).get("title_vi") or data["source"].get("title") or d.name,
                    "url": data["source"].get("url", ""), "created": data.get("created", 0),
                    "duration": (data.get("measured") or {}).get("duration"),
                    "aspect": (data.get("measured") or {}).get("aspect"),
                    "thumb": sheets[0]["file"] if sheets else None})
    return sorted(out, key=lambda x: -x["created"])


# --- report -------------------------------------------------------------------


def report_markdown(data: dict) -> str:
    ai, m, src = data.get("ai") or {}, data.get("measured") or {}, data.get("source") or {}
    st = m.get("stats") or {}
    notes = {s["n"]: s for s in ai.get("shots") or []}
    out = [f"# {ai.get('title_vi') or src.get('title') or 'Phân tích video'}", ""]
    if src.get("url"):
        out.append(f"Nguồn: {src['url']}")
    out += [f"Độ dài {fmt_time(m.get('duration', 0))} · {m.get('width')}x{m.get('height')} ({m.get('ratio') or m.get('aspect')}) · "
            f"{st.get('shots', 0)} shot · trung bình {st.get('average', 0)}s/shot · {st.get('cuts_per_minute', 0)} cắt/phút",
            "", "## Tóm tắt", ai.get("summary_vi", ""), "", f"**Thể loại:** {ai.get('genre_vi', '')}",
            f"**Mở đầu (hook):** {ai.get('hook_vi', '')}", f"**Vì sao hiệu quả:** {ai.get('why_it_works_vi', '')}",
            "", "## Nhịp truyện"]
    out += [f"- {fmt_time(b['start'])}–{fmt_time(b['end'])} **{b['label']}**: {b['what_vi']}" for b in ai.get("beats") or []]
    out += ["", "## Nhịp dựng", ai.get("rhythm_vi", ""), "", "## Chuyển cảnh"]
    out += [f"- {t['type']} ×{t['count']}: {t.get('where_vi', '')}" for t in ai.get("transitions") or []]
    out += ["", "## Máy quay", ai.get("camera_vi", ""), "", "## Màu & ánh sáng"]
    look = ai.get("look") or {}
    out += [f"- Màu: {look.get('palette_vi', '')}", f"- Chỉnh màu: {look.get('grade_vi', '')}",
            f"- Ánh sáng: {look.get('lighting_vi', '')}", f"- Chất hình: {look.get('texture_vi', '')}",
            f"- Prompt phong cách: `{look.get('style_prompt', '')}`", "", "## Âm thanh"]
    snd = ai.get("sound") or {}
    out += [f"- Nhạc: {snd.get('music_vi', '')}", f"- Giọng: {snd.get('voice_vi', '')}",
            f"- Hiệu ứng: {snd.get('sfx_vi', '')}"]
    out += [f"- {fmt_time(x['at'])}: {x['what_vi']}" for x in snd.get("moments") or []]
    txt = ai.get("text") or {}
    out += ["", "## Chữ trên hình", f"- Phụ đề: {txt.get('subtitles_vi', '')}", f"- Tiêu đề / nhãn: {txt.get('titles_vi', '')}",
            "", "## Nhân vật"]
    out += [f"- **{p['name']}** ({p.get('role_vi', '')}): {p['look']}" for p in ai.get("characters") or []]
    out += ["", "## Bối cảnh"] + [f"- **{p['name']}**: {p['look']}" for p in ai.get("settings") or []]
    out += ["", "## Danh sách shot", "", "| # | Thời gian | Dài | Cỡ cảnh | Góc | Máy | Vào bằng | Nội dung | Chữ |",
            "|---|---|---|---|---|---|---|---|---|"]
    for s in m.get("shots") or []:
        n = notes.get(s["n"], {})
        cells = [str(s["n"]), fmt_time(s["start"]), f"{s['length']:.1f}s", n.get("size", ""), n.get("angle", ""),
                 n.get("movement", ""), n.get("transition_in", ""), n.get("subject", ""), n.get("text_on_screen", "")]
        out.append("| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in cells) + " |")
    out += ["", "## Hướng dẫn dựng (cho agent dựng phim)", "", "```", data.get("edit_style", ""), "```", "",
            "## Cách làm phim tương tự", recipe_timing_vi(data), "", ai.get("film_recipe_vi", ""), "", "## Gợi ý ý tưởng mới",
            ai.get("idea_suggestion_vi", ""), ""]
    if (data.get("rea") or {}).get("used"):
        out += ["## rea-video", f"{(data['rea'].get('card') or {}).get('description', '')} — chi tiết: rea/report.html", ""]
    return "\n".join(out)


# --- 6. a new film in the same style ------------------------------------------


def suggested_scenes(data: dict) -> int:
    """As many scenes as fit the reference's length (see scene_budget), so the new film is as long as it."""
    budget = scene_budget(data.get("measured") or {})
    if budget:
        return len(budget)
    d = (data.get("measured") or {}).get("duration") or 30
    return max(1, min(MAX_SCENES, round(d / 6)))


def film_brief(data: dict, scenes: int | None = None) -> str:
    """The reference style, in English, for the planner (added to the idea)."""
    ai, m = data.get("ai") or {}, data.get("measured") or {}
    st = m.get("stats") or {}
    budget = scene_budget(m, scenes)
    look, snd, txt = ai.get("look") or {}, ai.get("sound") or {}, ai.get("text") or {}
    lines = ["", "REFERENCE STYLE (make the new film feel like this reference video; do NOT copy its story, "
             "people, names or dialogue):",
             f"- Format: {ai.get('genre_vi', '')}; reference length {m.get('duration', 0):.0f}s, {st.get('shots', 0)} "
             f"shots, average shot {st.get('average', 0)}s (the new film's clips are longer; the editor trims them).",
             f"- Hook (first seconds): {ai.get('hook_vi', '')}",
             *([budget_text(budget)] if budget else []),
             "- Story structure to follow (beats): " + " -> ".join(
                 f"{b['label']} ({b['what_vi'][:120]})" for b in ai.get("beats") or []),
             f"- Visual look (use it in `style`): {look.get('style_prompt', '')}",
             f"- Camera: {ai.get('camera_vi', '')}",
             "- Typical shots: " + "; ".join(f"{s['size']} {s.get('angle', '')} {s.get('movement', '')}".strip()
                                             for s in (ai.get("shots") or [])[:24]),
             f"- Sound: {snd.get('music_vi', '')} {snd.get('sfx_vi', '')}",
             f"- Voices: {snd.get('voice_vi', '')}",
             f"- On-screen text: {txt.get('subtitles_vi', '')}"]
    if ai.get("characters"):
        lines.append("- Character archetypes (invent new people of these kinds): "
                     + "; ".join(f"{p['name']}: {p.get('role_vi', '')}" for p in ai["characters"]))
    if ai.get("settings"):
        lines.append("- Kinds of places: " + "; ".join(f"{p['name']}: {p['look'][:160]}" for p in ai["settings"]))
    lines.append("Notes in Vietnamese above are for meaning only; write the storyboard as usual.")
    return "\n".join(lines)


FILM_MODES = ("style", "same")  # style: new story in the same style; same: remake the reference, new people only


def remake_brief(data: dict, scenes: int | None = None) -> str:
    """Planner brief for a shot-for-shot remake of the reference where only the people are new (English)."""
    ai, m = data.get("ai") or {}, data.get("measured") or {}
    notes = {s["n"]: s for s in ai.get("shots") or []}
    budget = scene_budget(m, scenes)
    look, snd, txt = ai.get("look") or {}, ai.get("sound") or {}, ai.get("text") or {}
    lines = ["REMAKE THE REFERENCE VIDEO. Keep everything the same: the story and its beats, every action and "
             "reaction in the same order, shot sizes, angles, camera moves, places, props, light and colour, the "
             "timing, the sound moments, the on-screen text style and the meaning of what is said. Change ONLY the "
             "people: invent NEW characters (new names, faces, hair, outfits; similar age and the same roles). "
             "Never describe or resemble the real people of the reference.",
             f"Summary of the reference: {ai.get('summary_vi', '')}",
             f"Reference length {m.get('duration', 0):.1f}s. Write exactly {len(budget) or scenes} scenes, in this "
             "order; the seconds are how long each stays on screen, so give each ONE action that reads in that time "
             "(a short scene can be 2 s: one look, one gesture, one reaction):"]
    for i, b in enumerate(budget, 1):
        shot_txt = " / ".join(
            ", ".join(x for x in (notes[k].get("size"), notes[k].get("angle"), notes[k].get("movement")) if x)
            + f": {notes[k].get('subject', '')}"
            + (f" (purpose: {notes[k]['purpose']})" if notes[k].get("purpose") else "")
            + (f" [text on screen: {notes[k]['text_on_screen']}]" if notes[k].get("text_on_screen") else "")
            for k in b["shots"] if k in notes)
        lines.append(f"- s{i:02d}, {b['length']:.1f}s, reference {b['start']:.1f}-{b['end']:.1f}s"
                     + (f" (shot {', '.join(f'#{k}' for k in b['shots'])}): {shot_txt}" if shot_txt else ""))
    if ai.get("beats"):
        lines.append("Beats: " + " -> ".join(f"{b['start']:.0f}-{b['end']:.0f}s {b['label']}: {b['what_vi']}"
                                             for b in ai["beats"]))
    if ai.get("characters"):
        lines.append("Roles to recast with new people (keep the role, not the look): "
                     + "; ".join(f"{p['name']} ({p.get('role_vi', '')})" for p in ai["characters"]))
    if ai.get("settings"):
        lines.append("Places (keep them): " + "; ".join(f"{p['name']}: {p['look'][:200]}" for p in ai["settings"]))
    lines += [f"Visual look (use it in `style`): {look.get('style_prompt', '')}",
              f"Camera: {ai.get('camera_vi', '')}",
              f"Sound: {snd.get('music_vi', '')} {snd.get('sfx_vi', '')} Voices: {snd.get('voice_vi', '')}",
              f"On-screen text: {txt.get('subtitles_vi', '')} {txt.get('titles_vi', '')}"]
    if (data.get("subtitles") or "").strip():
        lines.append("What is said in the reference (timed; keep the meaning and where it falls, say it in the "
                     "film's dialogue language with the new characters' names):\n" + data["subtitles"].strip()[:2500])
    lines.append("Notes in Vietnamese above are for meaning only; write the storyboard as usual.")
    return "\n".join(lines)


def _clip_durations(sb, meta) -> None:
    """Render each timed scene at the shortest clip the model allows that still covers its screen_time."""
    from . import durations as durations_mod

    for s in sb.scenes:
        if not s.screen_time:
            continue
        allowed = sorted(durations_mod.allowed_for(sb, s, meta) or [])
        if allowed:
            s.duration = next((x for x in allowed if x >= s.screen_time), allowed[-1])
        elif s.duration and s.duration < s.screen_time + 0.5:  # unknown lengths: a clip too short for the scene
            s.duration = None  # Flow's default (8 s) leaves room for the action and the line


def plan_from_analysis(data: dict, idea: str = "", *, scenes: int | None = None, language_hint: str | None = None,
                       dialogue_language: str | None = None, backend: str = "auto", api_key: str | None = None,
                       ai_model: str | None = None, gemini_api_key: str | None = None,
                       mode: str = "style", fmt: str | None = None):
    """A new Storyboard from the analysed video. Returns (storyboard, backend).

    `fmt`: the film format. None picks the new (basic) flow: `clip` for a reference up to 90 s, else `short_film`;
    `short_drama` keeps the deprecated drama planner (planner.plan_auto).

    mode "style": a NEW story in the same format and style; "same": a remake of the reference where only the
    characters are new. Either way each scene gets `screen_time` from the scene budget, so the cut film is as
    long as the reference (a scene can be 2 s on screen; its clip is rendered short and trimmed)."""
    from . import ai as ai_mod
    from . import formats, model_meta

    if mode not in FILM_MODES:
        raise ValueError(f"mode must be one of {FILM_MODES}")
    extra_ai: dict[str, Any] = {}
    if ai_mod.effective_ai_model(ai_model) == ai_mod.GEMINI_PRO:
        extra_ai["ai_model"] = ai_mod.GEMINI_PRO
    if gemini_api_key:
        extra_ai["gemini_api_key"] = gemini_api_key
    ai = data.get("ai") or {}
    aspect = (data.get("measured") or {}).get("aspect") or "16:9"
    n = max(1, min(MAX_SCENES, scenes or suggested_scenes(data)))
    if mode == "same":
        extra = (idea or "").strip()
        prompt = remake_brief(data, n) + (f"\nExtra wishes from the user: {extra}" if extra else "")
    else:
        idea = (idea or "").strip() or (
            "Invent a NEW original story in the same format and genre as the reference. Starting point: "
            + (ai.get("idea_suggestion_vi") or ai.get("genre_vi") or "a short emotional drama"))
        prompt = idea + "\n" + film_brief(data, n)
    style_prompt = ((ai.get("look") or {}).get("style_prompt") or "").strip()
    if fmt is None:
        fmt = "clip" if float((data.get("measured") or {}).get("duration") or 0) <= 90 else "short_film"
    if fmt not in formats.FORMATS:
        raise ValueError(f"định dạng {fmt!r} không hợp lệ")
    if formats.is_deprecated(fmt):
        from .planner import plan_auto

        sb, used = plan_auto(prompt, backend=backend, api_key=api_key, scenes=n, aspect=aspect,
                             language_hint=language_hint, dialogue_language=dialogue_language, **extra_ai)
        sb.format = formats.DRAMA
    else:
        from .basic_planner import plan_basic

        speaks = bool(dialogue_language) and dialogue_language.casefold() != "none"
        sb, used = plan_basic(prompt, fmt=fmt, audio="dialogue" if speaks else "silent",
                              visual="other" if style_prompt else "realistic", custom_style=style_prompt,
                              scenes=n, aspect=aspect if aspect in ("16:9", "9:16") else "9:16",
                              language=dialogue_language if speaks else language_hint,
                              backend=backend, api_key=api_key, **extra_ai)
    budget = scene_budget(data.get("measured") or {}, len(sb.scenes) or n)
    for s, b in zip(sb.scenes, budget):
        s.screen_time = b["length"]
    _clip_durations(sb, model_meta.load())
    style = data.get("edit_style") or edit_style_text(data) or ""
    sb.edit_style = "\n".join(x for x in (budget_text(budget), style) if x) or None
    if style_prompt and not (sb.style or "").strip():
        sb.style = style_prompt
    return sb, used


def copy_reference(analysis_dir: Path, project_dir: Path) -> Path:
    """Keep the analysis with the new project (project/reference/): json, report, frames; not the video."""
    dest = project_dir / "reference"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("analysis.json", "report.md"):
        if (analysis_dir / name).is_file():
            shutil.copy2(analysis_dir / name, dest / name)
    if (analysis_dir / "frames").is_dir():
        shutil.copytree(analysis_dir / "frames", dest / "frames", dirs_exist_ok=True)
    return dest


__all__ = ["VideoAnalysis", "AnalysisError", "AnalysisStopped", "analyze", "fetch", "measure", "run_rea",
           "contact_sheets", "ask_claude", "plan_from_analysis", "film_brief", "edit_style_text", "list_analyses",
           "report_markdown", "load", "new_id", "rea_binary", "ytdlp_command", "copy_reference", "suggested_scenes",
           "scene_budget", "recipe_timing_vi", "budget_text",
           "remake_brief", "FILM_MODES"]
