"""Automated Pre-Render (ViMax) & Post-Render (OpenMontage + VLM) QC gates for film scenes.

1. Pre-render QC gate (ViMax style):
   - Generates multiple keyframe candidates in parallel before calling the paid video API.
   - Scores every candidate locally (ffprobe, luminance/black-image check, optional OCR) and with a VLM
     (character portrait match, staging/composition, speaker facing, no burned-in text or deformities),
     picking the best candidate (`qc_keyframes.json`) and revising/remaking when all candidates fail.

2. Post-render QC gate (OpenMontage + AI QC):
   - OpenMontage local inspection (free, deterministic):
     * `ffprobe` stream/codec/resolution/fps/duration/packet compliance check,
     * 4 frames extracted across the clip (`<sid>_om_1.jpg`..`<sid>_om_4.jpg`) checked for black frames
       (pixel luminance + `blackdetect`) and text/caption errors (local OCR when installed + VLM 4-frame check),
     * audio track & level measurements (`volumedetect`, `silencedetect`, dead-air silence, 0 dBFS clipping).
   - Combined with start-frame vs keyframe match, offline `whisper.cpp` speech-to-text (+ VAD), who-speaks
     frame strip, and a multimodal VLM judge (`Claude` or `Gemini Pro`).
   - Final assembly gate (`filter_clips_for_assembly`): prevents `FAIL` clips from slipping into `final.mp4`.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from . import ai, manual_clip, reviewer, rules
from .storyboard import NO_DIALOGUE, Scene, Storyboard

QC_FILE = "qc.json"
KEYFRAME_QC_FILE = "qc_keyframes.json"
MODEL_NAME = "ggml-large-v3-turbo-q5_0.bin"
MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{MODEL_NAME}"
# Voice-activity detection: whisper only transcribes where someone speaks. Without it whisper invents speech on
# music and ambient sound ("I'm good. I'm good…", a Chinese line 6 times), and QC flagged clips nobody speaks in.
VAD_NAME = "ggml-silero-v5.1.2.bin"
VAD_URL = f"https://huggingface.co/ggml-org/whisper-vad/resolve/main/{VAD_NAME}"
QC_VERSION = 3  # 2: transcripts with voice-activity detection, who-speaks frames; older MINOR/FAIL are checked again
# 3: OCR reads only the caption strip and is a hint, never a FAIL by itself: older OCR-based FAILs are checked again
FRAMES = 6
OPENMONTAGE_FRAMES = 4
OPENMONTAGE_POSITIONS: tuple[float, ...] = (0.12, 0.37, 0.63, 0.88)
DEFAULT_KEYFRAME_CANDIDATES = 2
MAX_KEYFRAME_CANDIDATES = 4
BLACK_LUMA_MAX = 16.0  # 0..255 mean luma below which a frame is nearly pitch black
BLACK_DARK_RATIO = 0.94  # share of pixels <= 22 luma required to classify a frame as black
WRONG_START = rules.START_MATCH_MIN  # first frame vs keyframe below this: the clip was made from another picture

Verdict = Literal["PASS", "MINOR", "FAIL"]

_lock = threading.Lock()
_kf_lock = threading.Lock()


# --- answer shapes ---------------------------------------------------------------


class QCAnswer(BaseModel):
    verdict: Verdict = Field(description="PASS = good; MINOR = small flaws a viewer accepts; FAIL = should be "
                                         "rendered again (wrong scene/characters, broken picture, black frames, "
                                         "burned-in text, wrong or missing dialogue).")
    score: int = Field(description="1-10")
    issues: list[str] = Field(default_factory=list, description="Problems, short and concrete, in Vietnamese.")
    picture: str = Field(default="", description="One short sentence on the picture, in Vietnamese.")
    sound: str = Field(default="", description="One short sentence on the sound and dialogue, in Vietnamese.")
    black_frames: bool = Field(default=False, description="True if any of the 4 OpenMontage frames or clip frames is "
                                                          "an unintended black/blank/corrupted frame.")
    text_errors: bool = Field(default=False, description="True if any frame has burned-in subtitles, captions, "
                                                         "watermarks, logos or gibberish lettering.")
    prompt: str | None = Field(default=None, description="When FAIL and the action prompt is the cause: a corrected "
                                                         "action prompt (English). Name characters exactly.")


class KeyframeCandidateVerdict(BaseModel):
    index: int = Field(description="1-based candidate index (1..k).")
    verdict: Verdict = Field(description="PASS = ready for video; MINOR = usable with minor flaw; FAIL = broken "
                                         "(wrong character/face, text/watermark, extra limbs, black/corrupted, bad speaker staging).")
    score: int = Field(description="1-10 overall quality score for animating into the scene clip.")
    issues: list[str] = Field(default_factory=list, description="Short concrete issues of this candidate, in Vietnamese.")


class KeyframeSelection(BaseModel):
    best_index: int = Field(description="1-based index of the best candidate keyframe to send to the paid video API.")
    verdict: Verdict = Field(description="Verdict of the chosen best candidate (PASS, MINOR, or FAIL when every candidate fails).")
    score: int = Field(description="1-10 score of the chosen best candidate.")
    reason: str = Field(default="", description="One short sentence in Vietnamese explaining why this candidate won.")
    candidates: list[KeyframeCandidateVerdict] = Field(default_factory=list, description="Score and verdict for each candidate in order 1..k.")
    image_prompt: str | None = Field(default=None, description="When verdict is FAIL: a corrected English opening-frame "
                                                               "prompt that fixes the problem before regenerating.")


VIMAX_KEYFRAME_SYSTEM = reviewer.COMMON + """
You are the Pre-Render Keyframe QC Judge (ViMax best-of-k gate), running BEFORE an expensive video generation API call.
You receive the scene specification, the reference portraits of the characters (and location if available), and `k`
candidate keyframe images generated in parallel (`candidate 1` .. `candidate k`), along with local image checks.
Evaluate EVERY candidate on:
1. Character & setting consistency: exact character count, faces/hair matching reference portraits, same room/lighting.
   Outfits and props follow THIS scene's `image_prompt` and character descriptions: an item the scene text does not
   give the character now (a cloth or prop from later scenes) is an issue even when the portrait shows it.
2. Shot readiness & staging: matches `image_prompt` and sets up the upcoming `action` cleanly without clutter or clipped faces.
   When the scene has spoken dialogue, the speaker MUST have the clearest, largest visible face/mouth and every listener
   must have closed lips (an open-mouthed listener steals lip-sync in video models = FAIL).
3. Clean film frame: NO burned-in text, captions, signs with gibberish letters, watermarks, logos, extra/merged limbs,
   duplicated characters, or black/blank/corrupted regions (any text or severe anatomy error = FAIL).
Score every candidate (1-10) in `candidates`, pick `best_index` (the highest-quality candidate safe to animate), and
explain briefly in Vietnamese (`reason`). If ALL candidates have serious flaws that would ruin the paid video render,
set `verdict` to FAIL and return a corrected English `image_prompt`."""


SYSTEM = reviewer.COMMON + """
You are the QC checker of ONE rendered scene clip, right after it was downloaded. You get the scene as written,
the keyframe (the opening picture Veo was asked to animate), the portraits of the characters in the scene,
the 4 OpenMontage QC frames extracted across the clip (specifically to catch black/blank frames and burned-in text/caption
errors at full clarity), 6 frames across the clip in time order, and measurements: OpenMontage `ffprobe` stream compliance,
how well the clip's first frame matches the keyframe
(1.0 = same picture; under 0.75 means Veo animated ANOTHER picture: always FAIL), the audio levels, and a
speech-to-text transcript of the clip with the language it heard (the transcript can misspell words, above all
in Thai and Vietnamese: judge the meaning, not the spelling). Speech-to-text also INVENTS words on music, singing
and background noise: a short phrase repeated over and over, a language nobody in the film speaks, filler like
"Ah", "Oh my God", "Thank you", or speech in a quiet clip (mean volume under about -30 dB) is most likely not
real. Flag extra speech only when the transcript is plausible for the scene (it fits the place and people, uses
their names, makes sense); when unsure, say so and do not FAIL for it.
Check:
- Picture & OpenMontage 4-frame inspection: it is THIS scene (place, people, action from the keyframe), the right
  characters with the right faces and clothes, no morphing faces/bodies, extra limbs, people appearing or vanishing,
  frozen or black frames (if you see unintended black/blank frames, set `black_frames=true` and FAIL), and
  no burned-in subtitles, captions or gibberish text (any readable or fake text in the picture = set `text_errors=true`
  and FAIL). Veo burns subtitles into the bottom third above all while a line is spoken: look closely at the bottom
  third of all 4 OpenMontage frames, the 6-frame strip, and the who-speaks frames.
- Sound: when the scene has dialogue, the expected lines are spoken (same meaning), in the expected language,
  once, by one speaker at a time; no extra lines nobody wrote, no line said twice, no other language. Silence or
  only music where a line was expected = FAIL. When the scene has no dialogue, nobody should talk: a
  character clearly saying words or a sentence (real speech per the rules above) = FAIL ("thoại thừa"), and give a
  `prompt` that keeps the action and says nobody speaks; only an indistinct background murmur, a single
  exclamation or crying/breathing = MINOR or PASS. A clip with no audio at all is FAIL unless sound is not expected.
- Who speaks: when the scene has dialogue you also get frames taken WHILE the line is heard (times from the
  transcript). Look whose mouth is open or changing shape across them. The line belongs to the character named in
  the expected dialogue: if another character visibly speaks it (their mouth moves while the speaker's stays shut,
  or the speaker has their back to the camera and someone else talks), that is FAIL ("sai người nói"), and give a
  `prompt` that puts the speaker facing the camera and saying the line, with the others listening silently.
  Check EVERY frame, not just some: Veo often splits one line between two people (the listener mouths the first
  words, then the speaker the rest). If in any frame a character who has no line talks (mouth open mid-word,
  gesturing as if speaking) while the line is heard, it is also FAIL ("sai người nói"), even when the right
  speaker talks in the other frames. Name the frames and who talks in each in `sound`.
  Exact lip-sync timing you cannot verify from stills: do not guess about it.
FAIL only for what a viewer would clearly notice; MINOR for small flaws; PASS when it is good.
Give `prompt` only when a FAIL would likely be fixed by a different action prompt."""


# --- tools: whisper, ffprobe, OpenMontage 4-frame & audio inspection -------------


def model_dir() -> Path:
    from .settings import settings_path

    return settings_path().parent / "models"


def whisper_binary() -> str | None:
    return shutil.which("whisper-cli") or shutil.which("whisper-cpp")


def tesseract_binary() -> str | None:
    if os.environ.get("FILMMAKER_NO_OCR") == "1":
        return None
    return shutil.which("tesseract")


def whisper_model() -> Path | None:
    env = os.environ.get("FILMMAKER_WHISPER_MODEL")
    for p in ([Path(env)] if env else []) + [model_dir() / MODEL_NAME]:
        if p.is_file() and p.stat().st_size > 10_000_000:
            return p
    return None


def vad_model() -> Path | None:
    p = model_dir() / VAD_NAME
    return p if p.is_file() and p.stat().st_size > 100_000 else None


def status() -> dict[str, Any]:
    """What QC can do on this machine."""
    return {
        "whisper": bool(whisper_binary()),
        "model": bool(whisper_model()),
        "model_name": MODEL_NAME,
        "model_path": str(model_dir() / MODEL_NAME),
        "vad": bool(vad_model()),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "ocr": bool(tesseract_binary()),
        "openmontage_frames": OPENMONTAGE_FRAMES,
        "default_keyframe_candidates": DEFAULT_KEYFRAME_CANDIDATES,
    }


def download_model(log: Callable[[str], None] = lambda *_: None) -> Path:
    """Fetch the whisper model once (about 550 MB) and the speech detector (about 1 MB) into the settings folder."""
    dest = model_dir() / MODEL_NAME
    if not vad_model():
        log(f"Đang tải bộ phát hiện giọng nói ({VAD_NAME}, khoảng 1 MB)…")
        _fetch(VAD_URL, model_dir() / VAD_NAME, 100_000)
    if whisper_model():
        return dest
    log(f"Đang tải model nhận dạng giọng nói ({MODEL_NAME}, khoảng 550 MB)…")
    return _fetch(MODEL_URL, dest, 10_000_000, log)


def _fetch(url: str, dest: Path, min_size: int, log: Callable[[str], None] = lambda *_: None) -> Path:
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=60) as r, part.open("wb") as f:
        total, done, step = int(r.headers.get("Content-Length") or 0), 0, 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total and done * 10 // total > step:
                step = done * 10 // total
                log(f"  {step * 10}%")
    if part.stat().st_size < min_size:
        part.unlink(missing_ok=True)
        raise RuntimeError("tải model không thành công")
    part.replace(dest)
    return dest


def _parse_fps(rate: str | None) -> float | None:
    if not rate or rate == "0/0":
        return None
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            d = float(den)
            return round(float(num) / d, 2) if d > 0 else None
        return round(float(rate), 2)
    except (ValueError, ZeroDivisionError):
        return None


def _frame_luma_stats(path: Path) -> dict[str, Any]:
    """Fast 64x36 grayscale luminance measurement via ffmpeg (no external imaging libraries needed)."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", str(path), "-vf", "scale=64:36",
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"mean_luma": None, "dark_ratio": None, "is_black": False}
    raw = proc.stdout
    if proc.returncode != 0 or not raw:
        return {"mean_luma": None, "dark_ratio": None, "is_black": False}
    n = len(raw)
    mean_y = round(sum(raw) / n, 2)
    dark_ratio = round(sum(1 for b in raw if b <= 22) / n, 3)
    is_black = bool(mean_y <= BLACK_LUMA_MAX and dark_ratio >= BLACK_DARK_RATIO)
    return {"mean_luma": mean_y, "dark_ratio": dark_ratio, "is_black": is_black}


@functools.lru_cache(maxsize=8)
def _tesseract_lang(binary: str) -> str:
    """Pick installed Tesseract languages (`eng`, plus `vie`/`tha` when available) once per binary."""
    try:
        proc = subprocess.run([binary, "--list-langs"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return "eng"
    if proc.returncode != 0:
        return "eng"
    avail = {ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()}
    picked = [code for code in ("eng", "vie", "tha") if code in avail]
    return "+".join(picked) if picked else "eng"


#: Tesseract word confidence (0..100) a word needs to count. Texture read as letters (wood grain, checked cloth,
#: straw) comes back as short words under ~70; real captions after the preprocessing below come back at 85+.
OCR_MIN_CONF = 80
#: Bottom share of the frame that is read: Veo/Muse burn subtitles into the bottom third. Signs and other text
#: elsewhere in the picture are left to the VLM, which sees the whole frame.
OCR_BOTTOM_SHARE = 0.4
#: Luma cut-offs: everything brighter becomes black ink on white. Two passes catch white captions (bright cut)
#: and yellow / smaller anti-aliased captions (lower cut) while dark background texture drops out.
OCR_LUMA_CUTS: tuple[int, ...] = (150, 200)
_OCR_TOKEN_RE = re.compile(r"[A-Za-zÀ-ỹ฀-๿]{2,}")
_OCR_EDGE_RE = re.compile(r"^[^\wÀ-ỹ฀-๿]+|[^\wÀ-ỹ฀-๿]+$")


def _ocr_words(binary: str, image: Path, lang: str) -> list[str]:
    """Confident words in `image` (Tesseract TSV output, `--psm 6` on the preprocessed caption strip)."""
    try:
        proc = subprocess.run([binary, str(image), "stdout", "--psm", "6", "-l", lang, "tsv"],
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    out: list[str] = []
    for line in (proc.stdout or "").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 12:
            continue
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        word = _OCR_EDGE_RE.sub("", cols[11].strip())
        if conf >= OCR_MIN_CONF and _OCR_TOKEN_RE.fullmatch(word):
            out.append(word)
    return out


def ocr_frame(path: Path) -> str:
    """Local OCR hint (via `tesseract` when installed) for burned-in captions on a frame: '' when none.

    Tesseract on a whole natural picture (the old `--psm 6` on the full frame) reads wood grain, cloth patterns and
    straw as gibberish "words", which failed good keyframes and clips. So only the bottom caption strip is read,
    upscaled and thresholded so bright caption ink stands out, and only confident whole words count. The result is a
    hint for the VLM judge, never a FAIL on its own (`openmontage_check`, `_local_candidate_score`).
    """
    binary = tesseract_binary()
    if not binary or not path.is_file() or not shutil.which("ffmpeg"):
        return ""
    lang = _tesseract_lang(binary)
    words: list[str] = []
    with tempfile.TemporaryDirectory(prefix="fm-ocr-") as tmp:
        for cut in OCR_LUMA_CUTS:
            pre = Path(tmp) / f"strip_{cut}.png"
            vf = (f"crop=iw:ih*{OCR_BOTTOM_SHARE}:0:ih*{1 - OCR_BOTTOM_SHARE},"
                  "scale='max(1080,iw*2)':-1:flags=lanczos,format=gray,"
                  f"lutyuv=y='if(gt(val,{cut}),0,255)'")
            try:
                subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-vf", vf, str(pre)],
                               capture_output=True, timeout=30, check=True)
            except (OSError, subprocess.SubprocessError):
                continue
            words += _ocr_words(binary, pre, lang)
    seen = list(dict.fromkeys(words))
    long_words = [w for w in seen if len(w) >= 3]
    if len(long_words) >= 2 or any(len(w) >= 5 for w in long_words):
        return " ".join(seen)[:160]
    return ""


def inspect_image(path: Path) -> dict[str, Any]:
    """Local technical & visual sanity check of a candidate keyframe image (ffprobe + luminance + optional OCR)."""
    out: dict[str, Any] = {"valid": False, "width": None, "height": None, "codec": None,
                           "mean_luma": None, "dark_ratio": None, "is_black": False, "ocr_text": ""}
    if not path or not path.is_file():
        return out
    try:
        if path.stat().st_size == 0:
            return out
    except OSError:
        return out
    exe = shutil.which("ffprobe")
    if exe:
        try:
            proc = subprocess.run(
                [exe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name,width,height", "-of", "json", str(path)],
                capture_output=True, text=True, timeout=20,
            )
            if proc.returncode == 0:
                streams = (json.loads(proc.stdout or "{}").get("streams")) or []
                if streams:
                    st = streams[0]
                    w, h = int(st.get("width") or 0), int(st.get("height") or 0)
                    if w > 0 and h > 0:
                        out.update(valid=True, width=w, height=h, codec=st.get("codec_name"))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    luma = _frame_luma_stats(path)
    out.update(luma)
    if not exe and luma["mean_luma"] is not None:
        out["valid"] = True
    if out["valid"] and not out["is_black"]:
        out["ocr_text"] = ocr_frame(path)
    # Technical soundness only: an OCR hit is a hint for the VLM (see `ocr_frame`), not a broken picture.
    out["local_ok"] = bool(out["valid"] and not out["is_black"])
    return out


def ffprobe_clip(clip: Path, *, expected_aspect: str | None = None) -> dict[str, Any]:
    """OpenMontage `ffprobe` media compliance inspection: container, video/audio streams, resolution, fps, duration."""
    info: dict[str, Any] = {
        "valid": False,
        "has_video": False,
        "has_audio": False,
        "codec": None,
        "width": None,
        "height": None,
        "fps": None,
        "duration": 0.0,
        "size_bytes": 0,
        "packets_ok": None,
        "aspect_match": None,
    }
    if not clip or not clip.is_file():
        return info
    try:
        info["size_bytes"] = clip.stat().st_size
    except OSError:
        return info
    if info["size_bytes"] <= 0:
        return info
    exe = shutil.which("ffprobe")
    if not exe:
        return info
    try:
        proc = subprocess.run(
            [exe, "-v", "error",
             "-show_entries", "stream=index,codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,duration:format=duration,format_name",
             "-of", "json", str(clip)],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            return info
        data = json.loads(proc.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return info
    streams = data.get("streams") or []
    vstreams = [s for s in streams if s.get("codec_type") == "video"]
    astreams = [s for s in streams if s.get("codec_type") == "audio"]
    info["has_video"] = bool(vstreams)
    info["has_audio"] = bool(astreams)
    dur = 0.0
    try:
        dur = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    if vstreams:
        vs = vstreams[0]
        w = int(vs.get("width") or 0)
        h = int(vs.get("height") or 0)
        if not dur:
            try:
                dur = float(vs.get("duration") or 0.0)
            except (TypeError, ValueError):
                dur = 0.0
        info.update(
            codec=vs.get("codec_name"),
            width=w or None,
            height=h or None,
            fps=_parse_fps(vs.get("avg_frame_rate")) or _parse_fps(vs.get("r_frame_rate")),
        )
        if expected_aspect in ("16:9", "9:16") and w > 0 and h > 0 and w != h:
            info["aspect_match"] = (w > h) if expected_aspect == "16:9" else (h > w)
    info["duration"] = round(max(0.0, dur), 3)
    packets_ok: bool | None = None
    if info["has_video"] and dur >= 0.5 and clip.suffix.lower() in (".mp4", ".mov", ".m4v"):
        packets_ok = manual_clip.probe_clip(clip)
    info["packets_ok"] = packets_ok
    info["valid"] = bool(
        info["has_video"]
        and (info["width"] or 0) > 0
        and (info["height"] or 0) > 0
        and info["duration"] > 0.1
        and (packets_ok is not False)
    )
    return info


def extract_four_frames(clip: Path, work: Path, sid: str, duration: float | None = None) -> list[dict[str, Any]]:
    """OpenMontage 4-frame sampling: extract 4 frames across the clip and check each for black frames & text errors."""
    dur = duration if duration is not None and duration > 0 else reviewer.duration_of(clip)
    if dur <= 0:
        return []
    work.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    for idx, pos in enumerate(OPENMONTAGE_POSITIONS, 1):
        t = max(0.0, min(dur - 0.04, dur * pos))
        dest = work / f"{sid}_om_{idx}.jpg"
        dest.unlink(missing_ok=True)
        try:
            proc = subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(clip),
                 "-frames:v", "1", "-vf", "scale=-2:480", "-q:v", "3", str(dest)],
                capture_output=True, timeout=60,
            )
            if (proc.returncode != 0 or not dest.is_file()) and dur > 0.2:
                subprocess.run(
                    ["ffmpeg", "-loglevel", "error", "-y", "-sseof", "-0.15", "-i", str(clip),
                     "-frames:v", "1", "-vf", "scale=-2:480", "-q:v", "3", str(dest)],
                    capture_output=True, timeout=60,
                )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if not dest.is_file():
            continue
        luma = _frame_luma_stats(dest)
        ocr = "" if luma["is_black"] else ocr_frame(dest)
        frames.append({
            "index": idx,
            "time": round(t, 2),
            "path": str(dest),
            "mean_luma": luma["mean_luma"],
            "dark_ratio": luma["dark_ratio"],
            "is_black": luma["is_black"],
            "ocr_text": ocr,
        })
    return frames


def black_span(clip: Path) -> float:
    """Total seconds of black video detected across the clip (`blackdetect`)."""
    try:
        err = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(clip), "-vf", "blackdetect=d=0.15:pix_th=0.10", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=90,
        ).stderr
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    total = 0.0
    for m in re.finditer(r"black_duration:\s*([\d.]+)", err or ""):
        try:
            total += float(m.group(1))
        except ValueError:
            pass
    return round(total, 3)


def audio_stats(clip: Path, *, has_audio: bool | None = None, duration: float | None = None) -> dict[str, Any]:
    """Audio track present, mean/max volume (dB), silent share, and OpenMontage dead-air / clipping indicators."""
    out: dict[str, Any] = {"has_audio": False}
    if has_audio is False:
        return out
    if has_audio is None:
        try:
            probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                                    "-of", "csv=p=0", str(clip)], capture_output=True, text=True, timeout=60).stdout
        except (OSError, subprocess.TimeoutExpired):
            return out
        if not probe.strip():
            return out
    out["has_audio"] = True
    try:
        err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(clip), "-af",
                              "volumedetect,silencedetect=n=-40dB:d=0.3", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=120).stderr
    except (OSError, subprocess.TimeoutExpired):
        return out
    silent = 0.0
    hist_0db = 0
    for line in err.splitlines():
        if "mean_volume:" in line:
            try:
                out["mean_volume_tmp"] = float(line.split("mean_volume:")[1].split()[0])
                out["mean_db"] = out.pop("mean_volume_tmp")
            except (ValueError, IndexError):
                out.pop("mean_volume_tmp", None)
        elif "max_volume:" in line:
            try:
                out["max_db"] = float(line.split("max_volume:")[1].split()[0])
            except (ValueError, IndexError):
                pass
        elif "histogram_0db:" in line:
            try:
                hist_0db += int(line.split("histogram_0db:")[1].split()[0])
            except (ValueError, IndexError):
                pass
        elif "silence_duration:" in line:
            try:
                silent += float(line.split("silence_duration:")[1].split()[0])
            except (ValueError, IndexError):
                pass
    dur = duration if (duration is not None and duration > 0) else reviewer.duration_of(clip)
    out["duration"] = round(dur, 2)
    out["silent_share"] = round(min(1.0, silent / dur), 2) if dur else None
    mean_db = out.get("mean_db")
    max_db = out.get("max_db")
    silent_share = out.get("silent_share")
    out["dead_air"] = bool(
        (mean_db is not None and mean_db <= -55.0)
        or (silent_share is not None and silent_share >= 0.98 and (mean_db is None or mean_db <= -42.0))
    )
    out["clipping"] = bool(max_db is not None and max_db >= 0.0 and hist_0db > 500)
    return out


def openmontage_check(clip: Path, work: Path, *, sb: Storyboard | None = None, s: Scene | None = None) -> dict[str, Any]:
    """OpenMontage post-render quality gate: `ffprobe` + 4 extracted frames (black frame & text check) + audio levels."""
    sid = s.id if s is not None else clip.stem
    probe = ffprobe_clip(clip, expected_aspect=sb.aspect if sb is not None else None)
    issues: list[str] = []
    if not probe["valid"]:
        issues.append("OpenMontage ffprobe: file video lỗi, cụt hoặc không có luồng hình hợp lệ.")
        return {
            "passed": False,
            "verdict": "FAIL",
            "score": 1,
            "issues": issues,
            "ffprobe": probe,
            "frames": [],
            "frame_files": [],
            "black_frame_indices": [],
            "black_span_sec": 0.0,
            "ocr_texts": [],
            "audio": {"has_audio": False},
        }
    dur = probe["duration"]
    frames = extract_four_frames(clip, work, sid, duration=dur)
    b_span = black_span(clip)
    sound = audio_stats(clip, has_audio=probe["has_audio"], duration=dur)
    black_idx = [f["index"] for f in frames if f["is_black"]]
    ocr_hits = [f"frame #{f['index']} ({f['time']}s): {f['ocr_text']}" for f in frames if f.get("ocr_text")]

    hard_issues: list[str] = []
    minor_issues: list[str] = []
    if not frames:
        hard_issues.append("OpenMontage: không trích xuất được 4 khung hình kiểm tra từ video.")
    else:
        # Interior frames (#2, #3) being black = mid-clip dropout; >=2 of 4 black, a sampled black frame with >=0.8s
        # black span, or >=35% black = dead/blank render. A brief <=0.6s fade touching only #1/#4 is MINOR/trimmed.
        severe_black = (
            2 in black_idx
            or 3 in black_idx
            or len(black_idx) >= 2
            or (bool(black_idx) and b_span >= 0.8)
            or (dur > 0 and b_span >= max(0.8, 0.35 * dur))
        )
        if severe_black:
            where = ", ".join(f"#{i}" for i in black_idx) if black_idx else f"{b_span:.1f}s"
            hard_issues.append(f"OpenMontage phát hiện khung hình đen trong video ({where}, tổng {b_span:.1f}s đen).")
        elif black_idx:
            where = ", ".join(f"#{i}" for i in black_idx)
            minor_issues.append(f"OpenMontage: frame {where} tối đen ngắn ở rìa clip ({b_span:.1f}s).")

    allow_text = bool(sb is not None and sb.audio == "onscreen_text")
    if ocr_hits and not allow_text:  # a hint only (see `ocr_frame`): the VLM judge confirms or dismisses it
        minor_issues.append(ocr_hint_issue("; ".join(ocr_hits)))

    has_lines = bool(sb is not None and s is not None and scene_lines(sb, s))
    if has_lines and not sound.get("has_audio"):
        hard_issues.append("OpenMontage: clip thiếu kênh âm thanh dù cảnh có lời thoại.")
    elif has_lines and sound.get("dead_air"):
        hard_issues.append(f"OpenMontage: âm thanh mất tiếng / câm bất thường ({sound.get('mean_db')} dB) dù cảnh có lời thoại.")
    if sound.get("clipping"):
        minor_issues.append("OpenMontage: mức âm thanh chạm ngưỡng rè / vỡ tiếng (0 dBFS clipping).")

    issues = hard_issues + minor_issues
    verdict: Verdict = "FAIL" if hard_issues else ("MINOR" if minor_issues else "PASS")
    score = 2 if not probe["valid"] else (3 if hard_issues else (6 if minor_issues else 9))
    return {
        "passed": not hard_issues,
        "verdict": verdict,
        "score": score,
        "issues": issues,
        "ffprobe": probe,
        "frames": frames,
        "frame_files": [Path(f["path"]).name for f in frames if f.get("path")],
        "black_frame_indices": black_idx,
        "black_span_sec": b_span,
        "ocr_texts": ocr_hits,
        "audio": sound,
    }


def _looped(segs: list[dict[str, Any]]) -> bool:
    """The same short phrase over and over: whisper's typical made-up output on music or background noise."""
    texts = ["".join(ch for ch in s["text"].casefold() if ch.isalnum()) for s in segs]
    return len(texts) >= 3 and len(set(texts)) == 1


FAINT_VAD = 0.3  # second, more sensitive listen when a line was expected and nothing was heard (whispers)


def transcribe(clip: Path, vad_threshold: float | None = None, *, use_vad: bool = True) -> dict[str, Any] | None:
    """{"language", "text", "segments": [{"start", "end", "text"}], "vad", "dropped"} via whisper.cpp;
    None when it is not set up. With the speech detector, only stretches where someone speaks are transcribed.
    `use_vad=False` listens to the whole clip (a line cried out over rain, which the detector skips)."""
    binary, model = whisper_binary(), whisper_model()
    vad = vad_model() if use_vad else None
    if not binary or not model:
        return None
    with tempfile.TemporaryDirectory(prefix="fm-qc-") as tmp:
        wav, base = Path(tmp) / "a.wav", Path(tmp) / "t"
        try:
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(clip), "-vn", "-ac", "1", "-ar", "16000",
                            str(wav)], check=True, capture_output=True, timeout=120)
            subprocess.run([binary, "-m", str(model), "-f", str(wav), "-l", "auto", "-oj", "-of", str(base), "-np",
                            "-t", str(max(2, (os.cpu_count() or 4) // 2)),
                            *(["--vad", "-vm", str(vad)] if vad else []),
                            *(["-vt", str(vad_threshold)] if vad and vad_threshold else [])],
                           check=True, capture_output=True, timeout=600)
            data = json.loads((base.with_suffix(".json")).read_text(encoding="utf-8", errors="replace"))
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
    segs = []
    for s in data.get("transcription") or []:
        text = str(s.get("text") or "").strip()
        if text and not (text.startswith("[") and text.endswith("]")):  # [Music], [BLANK_AUDIO]
            off = s.get("offsets") or {}
            segs.append({"start": round(off.get("from", 0) / 1000, 2), "end": round(off.get("to", 0) / 1000, 2),
                         "text": text})
    lang = (data.get("result") or {}).get("language")
    dropped = _looped(segs)
    if dropped:
        segs = []
    return {"language": lang if segs else None, "text": " ".join(s["text"] for s in segs), "segments": segs,
            "vad": bool(vad), "dropped": dropped}


# --- ViMax Pre-Render Keyframe Candidate Selection & Gate ------------------------


OCR_HINT_PREFIX = "OCR cục bộ nghi có chữ"


def ocr_hint_issue(text: str) -> str:
    return f"{OCR_HINT_PREFIX} ({text[:60]}) — chưa xác nhận, cần xem lại."


def _local_candidate_score(meta: dict[str, Any]) -> tuple[Verdict, int, list[str]]:
    """Deterministic fallback score for one keyframe candidate when no VLM is reachable."""
    if not meta.get("valid"):
        return "FAIL", 1, ["Ảnh keyframe bị lỗi định dạng hoặc trống."]
    if meta.get("is_black"):
        return "FAIL", 2, ["Ảnh keyframe bị tối đen."]
    if meta.get("ocr_text"):  # a hint only (see `ocr_frame`): the VLM, when reachable, decides
        return "MINOR", 5, [ocr_hint_issue(meta["ocr_text"])]
    luma = meta.get("mean_luma")
    # Healthy exposure sits roughly around 35..220 mean luma.
    if luma is not None and (luma < 25 or luma > 242):
        return "MINOR", 6, ["Độ sáng ảnh quá tối hoặc cháy sáng."]
    return "PASS", 8, []


def select_best_keyframe(sb: Storyboard, s: Scene, candidates: list[Path], *, allow_vlm: bool = True,
                         **ai_kw) -> dict[str, Any]:
    """ViMax pre-render gate: evaluate `candidates` (generated in parallel) with local checks + VLM and return the
    winning candidate (`best_index` 1..k, `best_path`, `verdict`, `score`, `reason`, `candidates`, `image_prompt`)."""
    valid_paths = [Path(p) for p in candidates]
    if not valid_paths:
        raise ValueError(f"scene {s.id}: no keyframe candidates to score")
    locals_meta = [inspect_image(p) for p in valid_paths]
    local_verdicts = [_local_candidate_score(m) for m in locals_meta]

    eff_kw = dict(ai_kw)
    if "ai_model" not in eff_kw and sb.ai_model:
        eff_kw["ai_model"] = sb.ai_model

    vlm_ans: KeyframeSelection | None = None
    if allow_vlm:
        images: list[tuple[str, Path]] = []
        names = {c.name for c in sb.scene_characters(s)}
        images += [(label, p) for label, p in reviewer.portraits(sb) if label.removeprefix("portrait of ") in names]
        loc = sb.scene_location(s)
        loc_img = sb.location_image(loc) if loc is not None else None
        if loc_img is not None:
            images.append((f"location reference ({loc.name})", loc_img))
        cand_facts: list[str] = []
        for idx, (p, meta) in enumerate(zip(valid_paths, locals_meta), 1):
            if p.is_file():
                images.append((f"candidate {idx} for scene {s.id}", p))
            cand_facts.append(
                f"- candidate {idx}: size={meta.get('width')}x{meta.get('height')}, "
                f"mean_luma={meta.get('mean_luma')}, black={meta.get('is_black')}, "
                f"local_ocr_hint={meta.get('ocr_text') or 'none'}"
            )
        user = "\n".join([
            reviewer.storyboard_brief(sb),
            "",
            f"Pre-render ViMax QC for scene {s.id} ({len(valid_paths)} candidate keyframe(s)).",
            f"Opening-frame prompt: {s.image_prompt or s.prompt}",
            f"Upcoming video action: {sb.video_prompt(s) if sb.basic else s.prompt}",
            f"Content direction: {s.content_direction or '-'}",
            expected_dialogue(sb, s),
            "",
            "Local technical checks of candidates (`local_ocr_hint` is a noisy Tesseract read of the bottom "
            "caption strip: look at the picture yourself and flag text only if you actually see letters):",
            *cand_facts,
            "",
            f"Score every candidate 1..{len(valid_paths)} and choose `best_index`.",
        ])
        try:
            vlm_ans = ai.ask(VIMAX_KEYFRAME_SYSTEM, user, KeyframeSelection, images=images, **eff_kw)
        except (RuntimeError, ValueError):
            vlm_ans = None

    verdict_rank = {"PASS": 2, "MINOR": 1, "FAIL": 0}
    cand_rows: list[dict[str, Any]] = []
    by_idx = {c.index: c for c in (vlm_ans.candidates if vlm_ans else [])}
    for idx, (p, meta, (lv, lscore, lissues)) in enumerate(zip(valid_paths, locals_meta, local_verdicts), 1):
        vc = by_idx.get(idx)
        if vc is not None:
            v_verdict: Verdict = "FAIL" if lv == "FAIL" else vc.verdict
            v_score = min(vc.score, 3) if lv == "FAIL" else max(1, min(10, int(vc.score)))
            # The VLM looked at the picture: it confirms or dismisses the OCR hint itself.
            kept_local = [x for x in lissues if not x.startswith(OCR_HINT_PREFIX)]
            v_issues = list(dict.fromkeys([*kept_local, *vc.issues]))
        else:
            v_verdict, v_score, v_issues = lv, lscore, list(lissues)
        cand_rows.append({
            "index": idx,
            "file": p.name,
            "path": str(p),
            "verdict": v_verdict,
            "score": v_score,
            "issues": v_issues,
            "local_ok": meta["local_ok"],
            "mean_luma": meta["mean_luma"],
        })

    if vlm_ans is not None and 1 <= vlm_ans.best_index <= len(cand_rows):
        chosen = cand_rows[vlm_ans.best_index - 1]
        # Never pick a locally broken/black candidate if another candidate passed local checks.
        if not chosen["local_ok"] and any(r["local_ok"] for r in cand_rows):
            chosen = max(cand_rows, key=lambda r: (r["local_ok"], verdict_rank[r["verdict"]], r["score"], -r["index"]))
        elif vlm_ans.candidates:
            # Also prefer a PASS/MINOR candidate over a FAIL if VLM inconsistently pointed at a lower one.
            best_ranked = max(cand_rows, key=lambda r: (r["local_ok"], verdict_rank[r["verdict"]], r["score"], -r["index"]))
            if chosen["verdict"] == "FAIL" and best_ranked["verdict"] != "FAIL":
                chosen = best_ranked
        reason = vlm_ans.reason or ""
        revised_prompt = vlm_ans.image_prompt if chosen["verdict"] == "FAIL" else None
        # When VLM omitted per-candidate entries, reflect top-level score/verdict on the chosen candidate.
        if not vlm_ans.candidates and chosen["local_ok"]:
            chosen["verdict"] = vlm_ans.verdict
            chosen["score"] = max(1, min(10, int(vlm_ans.score)))
            if vlm_ans.verdict == "FAIL":
                revised_prompt = vlm_ans.image_prompt
    else:
        chosen = max(cand_rows, key=lambda r: (r["local_ok"], verdict_rank[r["verdict"]], r["score"], -r["index"]))
        reason = "Chấm chọn cục bộ (chưa kết nối VLM)." if len(cand_rows) > 1 else ""
        revised_prompt = None

    best_idx = int(chosen["index"])
    return {
        "best_index": best_idx,
        "best_path": str(valid_paths[best_idx - 1]),
        "verdict": chosen["verdict"],
        "score": chosen["score"],
        "issues": chosen["issues"],
        "reason": reason,
        "image_prompt": revised_prompt,
        "num_candidates": len(cand_rows),
        "candidates": [
            {"index": r["index"], "file": r["file"], "verdict": r["verdict"], "score": r["score"],
             "issues": r["issues"], "mean_luma": r["mean_luma"]}
            for r in cand_rows
        ],
        "vlm_used": vlm_ans is not None,
        "v": QC_VERSION,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def load_keyframe_qc(out_dir: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads((Path(out_dir) / KEYFRAME_QC_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_keyframe_qc(out_dir: Path, sid: str, result: dict[str, Any]) -> None:
    with _kf_lock:
        data = load_keyframe_qc(out_dir)
        data[sid] = result
        path = Path(out_dir) / KEYFRAME_QC_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)


#: Issue texts the old whole-frame OCR (QC_VERSION < 3) wrote when it alone failed a keyframe or clip.
_OLD_OCR_ISSUES = ("Ảnh keyframe dính chữ trên hình", "OpenMontage phát hiện chữ/phụ đề dính trên khung hình")


def _old_ocr_verdict(r: dict[str, Any]) -> bool:
    """A stored non-PASS verdict from before QC_VERSION 3 that cites the old OCR: it may be texture read as text."""
    if r.get("v", 1) >= 3 or r.get("verdict") == "PASS":
        return False
    texts = [*(r.get("issues") or []), *(x for c in (r.get("candidates") or []) for x in (c.get("issues") or []))]
    return any(str(x).startswith(_OLD_OCR_ISSUES) for x in texts)


def accept_keyframe(out_dir: Path, sid: str, kf: Path, verdict: Verdict, score: int, note: str) -> None:
    """Record that a later, closer look (the agent keyframe review) accepted this keyframe: its Pre-QC FAIL flag
    must not make the render step remake an approved picture (it once remade s01 three more times, 6 images)."""
    if not kf.is_file():
        return
    rec = dict(load_keyframe_qc(out_dir).get(sid) or {})
    if rec.get("sig") == clip_sig(kf) and rec.get("verdict") != "FAIL":
        return  # Pre-QC already passed this very file
    rec.update(verdict=verdict, score=int(score), sig=clip_sig(kf), v=QC_VERSION, accepted_by=note,
               at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    save_keyframe_qc(out_dir, sid, rec)


def current_keyframe_qc(out_dir: Path, sid: str, kf: Path | None) -> dict[str, Any] | None:
    r = load_keyframe_qc(out_dir).get(sid)
    if not r or not kf or not kf.is_file() or r.get("sig") != clip_sig(kf):
        return None
    if _old_ocr_verdict(r):
        return None  # scored again with the caption-strip OCR, which no longer reads wood or cloth as text
    return r


# --- one scene (post-render clip QC: OpenMontage + VLM) --------------------------


SPEAK_FRAMES = 6  # spread over the whole line: a line split between two mouths must show


def speaking_frames(clip: Path, segments: list[dict[str, Any]], dest: Path) -> tuple[Path, list[float]] | None:
    """A strip of frames taken while speech is heard (the first line, or the first two seconds-or-more of speech
    when whisper split it into short pieces), to see whose mouth moves."""
    segs = [s for s in segments if s["end"] > s["start"]]
    if not segs:
        return None
    a, b = segs[0]["start"], segs[0]["end"]
    for s in segs[1:]:  # "Không được, đoạn này dài quá" | "Nắm tay nhau…": one line, two pieces
        if b - a >= 2.5 or s["start"] - b > 0.6:
            break
        b = s["end"]
    times = [round(a + (b - a) * (i + 0.5) / SPEAK_FRAMES, 2) for i in range(SPEAK_FRAMES)]
    sel = "+".join(f"lt(prev_pts*TB\\,{x})*gte(pts*TB\\,{x})" for x in times)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(clip), "-vf",
                               f"select='{sel}',scale=-2:420,tile={SPEAK_FRAMES}x1", "-frames:v", "1",
                               "-fps_mode", "passthrough", "-q:v", "4", str(dest)],
                              capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (dest, times) if proc.returncode == 0 and dest.is_file() else None


def clip_sig(p: Path) -> str:
    try:
        st = p.stat()
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


def scene_lines(sb: Storyboard, s: Scene) -> list[tuple[str, str, str]]:
    """(who, words, delivery) the clip should say. Drama: the scene's `dialogue` rows. I2V formats: the line of
    `video_shot.speaker` (who = the speaker's visual tag), or with a voice-over the narrator's `narration`."""
    if sb.dialogue_language == NO_DIALOGUE:
        return []
    if sb.basic:
        sp = s.video_shot.speaker if s.video_shot is not None else None
        if sb.audio == "dialogue" and sp and sp.line.strip():
            return [(sp.visual_tag or "the speaker", sp.line.strip(), sp.voice_style or "")]
        if sb.audio == "voiceover" and s.narration and s.narration.strip():
            return [("a narrator voice-over (not a character on screen; nobody on screen moves the lips)",
                     s.narration.strip(), "")]
        return []
    return [(d.character, d.line, d.delivery or "") for d in (s.dialogue or []) if d.line.strip()]


def expected_dialogue(sb: Storyboard, s: Scene) -> str:
    if sb.dialogue_language == NO_DIALOGUE:
        return "No dialogue: nobody should speak (ambient sound and music only)."
    lines = scene_lines(sb, s)
    lang = sb.dialogue_language or "the characters' natural language"
    if not lines:
        return f"No dialogue in this scene (nobody should say words). Film language: {lang}."
    rows = "\n".join(f"- {who}: {words}" + (f" ({how})" if how else "") for who, words, how in lines)
    return f"Expected dialogue, in {lang}, in this order, each line once:\n{rows}"


def uses_reference_video(sb: Storyboard, s: Scene) -> bool:
    """True when the clip was rendered with character refs next to the start picture: Flow then makes it as
    Reference-to-Video, which reframes the shot, so a low first-frame match does not mean another picture."""
    try:
        return bool(sb.start_frame_char_refs(s))
    except Exception:
        return False


def _om_summary(om: dict[str, Any], *, black_flag: bool = False, text_flag: bool = False) -> dict[str, Any]:
    return {
        "passed": bool(om["passed"] and not black_flag and not text_flag),
        "ffprobe": om["ffprobe"],
        "frames_checked": len(om["frames"]),
        "frame_files": [Path(f["path"]).name for f in om["frames"] if f.get("path")],
        "black_frames": om["black_frame_indices"],
        "black_span_sec": om["black_span_sec"],
        "black_flagged": bool(om["black_frame_indices"] or black_flag),
        "text_errors": bool(text_flag),  # confirmed by the VLM; `ocr_texts` alone is only a hint
        "ocr_texts": om["ocr_texts"],
        "audio": om["audio"],
        "issues": om["issues"],
    }


def check_scene_openmontage(sb: Storyboard, s: Scene, clip: Path, keyframe: Path | None, work: Path) -> dict[str, Any]:
    """Post-render QC using OpenMontage deterministic checks only (`ffprobe` + 4 frames black/OCR + audio + start-frame
    match), for environments without a VLM backend or `--local-only` runs."""
    from .flow_ui import frame_match

    raw_match = frame_match(keyframe, clip) if keyframe and keyframe.is_file() else None
    reframed = keyframe is not None and uses_reference_video(sb, s)
    match = round(raw_match, 3) if raw_match is not None and raw_match == raw_match else None
    om = openmontage_check(clip, work, sb=sb, s=s)
    issues = list(om["issues"])
    verdict: Verdict = om["verdict"]
    score: int = om["score"]
    if match is not None and match < WRONG_START and not reframed:
        issues.insert(0, f"Clip không bắt đầu từ keyframe của cảnh (độ khớp {match:.2f}): Flow đã dùng nhầm ảnh.")
        verdict = "FAIL"
        score = min(score, 3)
    probe = om["ffprobe"]
    res_txt = f"{probe['width']}x{probe['height']}" if probe.get("width") and probe.get("height") else "không rõ"
    pic_msg = (
        f"OpenMontage kiểm tra {len(om['frames'])}/4 frame ({res_txt}): "
        + ("phát hiện lỗi hình/chữ." if issues else "không có khung hình đen hay chữ lỗi.")
    )
    sound = om["audio"]
    snd_msg = (
        f"Âm thanh trung bình {sound.get('mean_db', '—')} dB, im lặng {int((sound.get('silent_share') or 0) * 100)}%."
        if sound.get("has_audio")
        else "Không có kênh âm thanh."
    )
    return {
        "verdict": verdict,
        "score": score,
        "issues": issues,
        "picture": pic_msg,
        "sound": snd_msg,
        "prompt": None,
        "match": match,
        "audio": sound,
        "openmontage": _om_summary(om),
        "transcript": None,
        "language": None,
        "dialogue_checked": False,
        "vlm_checked": False,
        "vad": False,
        "v": QC_VERSION,
        "clip": clip.name,
        "sig": clip_sig(clip),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def check_scene(sb: Storyboard, s: Scene, clip: Path, keyframe: Path | None, work: Path, **ai_kw) -> dict[str, Any]:
    """QC of one clip: OpenMontage (`ffprobe` + 4 extracted frames + audio) combined with Whisper + VLM judge."""
    from .flow_ui import frame_match

    raw_match = frame_match(keyframe, clip) if keyframe and keyframe.is_file() else None
    reframed = keyframe is not None and uses_reference_video(sb, s)
    match = round(raw_match, 3) if raw_match is not None and raw_match == raw_match else None  # None or NaN: unknown
    om = openmontage_check(clip, work, sb=sb, s=s)
    sound = om["audio"]
    speech = transcribe(clip)
    has_lines = bool(scene_lines(sb, s))
    if has_lines and speech and speech.get("vad") and not speech["segments"]:
        soft = transcribe(clip, vad_threshold=FAINT_VAD)  # a whispered line the detector skipped
        if soft and soft["segments"]:
            speech = {**soft, "faint": True}
        else:  # sobbing or shouting over rain / music: the detector hears no voice at any threshold
            whole = transcribe(clip, use_vad=False)
            if whole and whole["segments"]:
                speech = {**whole, "faint": True}
    strip = reviewer._sheet(clip, work / f"{s.id}_frames.jpg", cols=FRAMES, rows=1, height=300)
    images: list[tuple[str, Path]] = []
    if keyframe and keyframe.is_file():
        images.append((f"scene {s.id} keyframe (the picture Veo was asked to animate)", keyframe))
    names = {c.name for c in sb.scene_characters(s)}
    images += [(label, p) for label, p in reviewer.portraits(sb) if label.removeprefix("portrait of ") in names]
    # OpenMontage 4 extracted frames (placed before the 6-frame strip so images[0] = keyframe and images[-1] = strip/speaking).
    for f in om["frames"]:
        fp = Path(f["path"])
        if fp.is_file():
            images.append((f"scene {s.id} OpenMontage QC frame {f['index']}/4 (at {f['time']}s)", fp))
    if strip:
        images.append((f"scene {s.id} clip: {FRAMES} frames in time order", strip))
    talk = speaking_frames(clip, speech["segments"], work / f"{s.id}_speaking.jpg") if speech and has_lines else None
    if talk:
        images.append((f"scene {s.id}: {SPEAK_FRAMES} frames while the line is heard (at "
                       + ", ".join(f"{x}s" for x in talk[1]) + "): whose mouth moves?", talk[0]))
    om_fact = {
        "ffprobe_valid": om["ffprobe"]["valid"],
        "resolution": f"{om['ffprobe'].get('width')}x{om['ffprobe'].get('height')}",
        "fps": om["ffprobe"].get("fps"),
        "duration": om["ffprobe"].get("duration"),
        "frames_extracted": len(om["frames"]),
        "black_frame_indices": om["black_frame_indices"],
        "black_span_sec": om["black_span_sec"],
        "ocr_texts": om["ocr_texts"],
    }
    facts = [
        (f"Start-frame match with the keyframe: not meaningful ({match}): rendered as Reference-to-Video with "
         "character references, which keeps the scene but reframes the shot; judge the content, not the framing"
         if reframed else f"Start-frame match with the keyframe: {match if match is not None else 'unknown'}"),
        f"OpenMontage post-render check: {json.dumps(om_fact, ensure_ascii=False)}"
        + (" (`ocr_texts` is a noisy Tesseract read of the bottom caption strip: it often turns wood grain, cloth or"
           " straw into letters; set `text_errors` only if you see real text in the frames yourself)"
           if om["ocr_texts"] else ""),
        f"Audio: {json.dumps(sound)}",
    ]
    if speech is None:
        facts.append("Transcript: not available (speech-to-text is not set up): judge the sound from the audio "
                     "levels only and say the dialogue could not be checked.")
    else:
        how = ("FAINT: nothing was heard at first, a more sensitive listen found a voice that is very soft or buried in other sound (a whisper, a sob over rain?): the "
               "words may be wrong; do not FAIL for a missing line" if speech.get("faint") else
               "speech detector on: only stretches with a voice were transcribed" if speech.get("vad") else
               "no speech detector: on music or ambient sound the transcript may be made up")
        facts.append(f"Transcript ({how}; language heard: {speech['language'] or 'unknown'}): "
                     + (" | ".join(f"[{x['start']}-{x['end']}s] {x['text']}" for x in speech["segments"]) or "(no speech)"))
    user = "\n".join([
        reviewer.storyboard_brief(sb), "",
        f"QC scene {s.id} ({s.duration}s).",
        # I2V formats: the prompt actually sent (speaker, gaze, listeners); `prompt` there is only a summary
        f"Action prompt: {sb.video_prompt(s) if sb.basic else s.prompt}",
        f"Opening frame: {s.image_prompt or '-'}",
        f"Content direction: {s.content_direction or '-'}",
        expected_dialogue(sb, s), "", *facts])
    if "ai_model" not in ai_kw and sb.ai_model:
        ai_kw = {"ai_model": sb.ai_model, **ai_kw}
    ans = ai.ask(SYSTEM, user, QCAnswer, images=images, **ai_kw)
    verdict, issues = ans.verdict, list(ans.issues)
    score = ans.score
    if ans.black_frames and not any("khung hình đen" in x.lower() for x in issues):
        issues.insert(0, "VLM phát hiện khung hình đen / trống trong 4 frame hậu kiểm.")
    if ans.text_errors and not any("chữ" in x.lower() or "phụ đề" in x.lower() for x in issues):
        issues.insert(0, "VLM phát hiện lỗi chữ / phụ đề dính trên khung hình.")
    if (not om["passed"] or ans.black_frames or ans.text_errors) and verdict != "FAIL":
        verdict = "FAIL"
        score = min(score, 4)
    for m in reversed(om["issues"]):
        if m.startswith(OCR_HINT_PREFIX) and not ans.text_errors:
            continue  # the VLM looked at the frames and saw no text: the OCR hint was texture
        if m not in issues:
            issues.insert(0, m)
    if match is not None and match < WRONG_START and not reframed and verdict != "FAIL":  # never trust a clip of another picture
        verdict = "FAIL"
        issues.insert(0, f"Clip không bắt đầu từ keyframe của cảnh (độ khớp {match:.2f}): Flow đã dùng nhầm ảnh.")
    elif match is not None and match < WRONG_START and not reframed and not any("nhầm ảnh" in x for x in issues):
        issues.insert(0, f"Clip không bắt đầu từ keyframe của cảnh (độ khớp {match:.2f}): Flow đã dùng nhầm ảnh.")
    return {
        "verdict": verdict,
        "score": score,
        "issues": issues,
        "picture": ans.picture,
        "sound": ans.sound,
        "prompt": ans.prompt,
        "match": match,
        "audio": sound,
        "openmontage": _om_summary(om, black_flag=ans.black_frames, text_flag=ans.text_errors),
        "transcript": speech["text"] if speech else None,
        "language": speech["language"] if speech else None,
        "dialogue_checked": speech is not None,
        "vlm_checked": True,
        "vad": bool(speech and speech.get("vad")),
        "v": QC_VERSION,
        "clip": clip.name,
        "sig": clip_sig(clip),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --- storage, runs, and final assembly gate --------------------------------------


def load(out_dir: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads((Path(out_dir) / QC_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(out_dir: Path, sid: str, result: dict[str, Any]) -> None:
    with _lock:
        data = load(out_dir)
        data[sid] = result
        path = Path(out_dir) / QC_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)


def current(out_dir: Path, sid: str, clip: Path | None) -> dict[str, Any] | None:
    """The stored QC of this scene if it is about the clip that is there now."""
    r = load(out_dir).get(sid)
    if not r or not clip or not clip.is_file() or r.get("sig") != clip_sig(clip):
        return None
    if r.get("v", 1) < 2 and r.get("verdict") != "PASS" and r.get("dialogue_checked"):
        return None  # judged on a transcript that may have been made up: check again
    if _old_ocr_verdict(r):
        return None  # failed by the old whole-frame OCR, which read texture as text: check again
    return r


def clip_of(pipe, s: Scene) -> Path:
    """The scene's clip: the path in state.json, or clips/<id>.mp4 when that path is relative to another folder
    (a CLI run with a relative --out stores it relative to where it ran)."""
    p = Path(pipe.state.scene(s.id).get("video") or "")
    return p if p.is_file() else pipe.clip_path(s)


def run_openmontage(pipe, *, only: set[str] | None = None, force: bool = False, log: Callable[[str], None] = print,
                    stop: Callable[[], bool] = lambda: False) -> dict[str, dict[str, Any]]:
    """Run deterministic OpenMontage post-render QC (`ffprobe` + 4 extracted frames + audio) on rendered clips."""
    from .pipeline import DONE

    sb, out = pipe.sb, Path(pipe.out)
    todo = []
    for s in sb.scenes:
        st = pipe.state.scene(s.id)
        clip = clip_of(pipe, s)
        if (only and s.id not in only) or st.get("status") != DONE or not clip.is_file():
            continue
        if force or current(out, s.id, clip) is None:
            todo.append((s, clip))
    if not todo:
        return {}
    work = out / "qc_frames"
    work.mkdir(parents=True, exist_ok=True)
    done: dict[str, dict[str, Any]] = {}
    for s, clip in todo:
        if stop():
            break
        log(f"[{s.id}] OpenMontage hậu kiểm (ffprobe, 4 frame đen/chữ, âm thanh)…")
        r = check_scene_openmontage(sb, s, clip, pipe.keyframe_path(s) if s.mode == "keyframe" else None, work)
        _save(out, s.id, r)
        done[s.id] = r
        tag = {"PASS": "✔", "MINOR": "~", "FAIL": "✘"}[r["verdict"]]
        log(f"  {tag} [{s.id}] OpenMontage {r['verdict']} {r['score']}/10"
            + (f" — {'; '.join(r['issues'])}" if r["issues"] else ""))
    return done


def run(pipe, *, only: set[str] | None = None, force: bool = False, log: Callable[[str], None] = print,
        stop: Callable[[], bool] = lambda: False, **ai_kw) -> dict[str, dict[str, Any]]:
    """QC every rendered clip that has no VLM QC for its current file (or the `only` scenes; `force` = again).
    Returns {scene id: result}. A scene whose QC cannot run is logged and skipped."""
    from .pipeline import DONE

    sb, out = pipe.sb, Path(pipe.out)
    todo = []
    for s in sb.scenes:
        st = pipe.state.scene(s.id)
        clip = clip_of(pipe, s)
        if (only and s.id not in only) or st.get("status") != DONE or not clip.is_file():
            continue
        existing = current(out, s.id, clip)
        if force or existing is None or existing.get("vlm_checked") is False:
            todo.append((s, clip))
    if not todo:
        return {}
    if not whisper_model():
        log("⚠ QC chưa kiểm tra được lời thoại: chưa có model nhận dạng giọng nói (Cài đặt → AI QC → Tải model).")
    elif not vad_model():
        try:
            _fetch(VAD_URL, model_dir() / VAD_NAME, 100_000)
            log("Đã tải bộ phát hiện giọng nói (để QC không nghe nhầm nhạc/tiếng nền thành lời thoại).")
        except Exception as exc:  # noqa: BLE001 - QC still runs, with the looped-phrase filter only
            log(f"⚠ Không tải được bộ phát hiện giọng nói ({exc}): QC có thể nghe nhầm nhạc nền thành lời.")
    work = out / "qc_frames"
    work.mkdir(parents=True, exist_ok=True)
    done: dict[str, dict[str, Any]] = {}
    for s, clip in todo:
        if stop():
            break
        log(f"[{s.id}] AI QC + OpenMontage đang xem 4 frame, kiểm ffprobe và nghe clip…")
        try:
            r = check_scene(sb, s, clip, pipe.keyframe_path(s) if s.mode == "keyframe" else None, work, **ai_kw)
        except (RuntimeError, ValueError) as exc:
            log(f"  ⚠ [{s.id}] không QC được ({exc})")
            continue
        _save(out, s.id, r)
        done[s.id] = r
        tag = {"PASS": "✔", "MINOR": "~", "FAIL": "✘"}[r["verdict"]]
        log(f"  {tag} [{s.id}] QC {r['verdict']} {r['score']}/10" + (f" — {'; '.join(r['issues'])}" if r["issues"] else ""))
    return done


#: Most clips the assembly gate drops for a judgement call (VLM FAIL on a technically sound clip): a quarter of the
#: film, at least one. Past that, dropping would gut the story (a 10-scene film once came out with 5 scenes after
#: texture was read as text), so those clips stay in and the user is told to look at them. Broken files, black
#: renders and confirmed burned-in text are always dropped.
MAX_GATE_DROP_SHARE = 0.25


def _technical_fail(rec: dict[str, Any]) -> bool:
    """FAIL for a defect in the file itself (unreadable, black/dead render, missing sound, confirmed burned-in
    text), not a judgement of the content: such a clip would spoil the cut whatever the story needs."""
    om = rec.get("openmontage") or {}
    if (om.get("ffprobe") or {}).get("valid") is False:
        return True
    return om.get("passed") is False or bool(om.get("black_flagged") or om.get("text_errors"))


def gate_drop_limit(n_clips: int) -> int:
    return max(1, int(n_clips * MAX_GATE_DROP_SHARE))


def filter_clips_for_assembly(
    sb: Storyboard,
    out_dir: Path,
    clips: list[tuple[str, Path]],
    *,
    exclude_failed: bool = True,
    run_local_qc: bool = True,
    log: Callable[[str], None] = print,
) -> tuple[list[tuple[str, Path]], list[str]]:
    """Final assembly gate (OpenMontage + AI QC): keep broken/FAIL clips out of the final stitched/edited film.

    - Checks any existing `qc.json` verdict for each clip; if none is stored yet and `run_local_qc` is True,
      runs OpenMontage (`ffprobe` + 4 extracted frames + audio) on the spot so corrupted/black clips never slip
      into `final.mp4` unchecked.
    - Technical FAILs (`_technical_fail`) are always dropped. Content FAILs (the VLM's judgement on a sound file)
      are dropped only while the film loses at most `gate_drop_limit` clips; past that they are all kept with a
      warning, so a run of QC false alarms cannot cut half the story out.
    - Returns `(kept_clips, rejected_scene_ids)` in scene order; `rejected` lists only clips actually left out. If
      every clip in the film was dropped, keeps readable clips with a warning so assembly does not crash with an
      empty list, while still dropping unreadable/corrupted files that would break `ffmpeg`.
    """
    if not clips or not exclude_failed:
        return list(clips), []
    out_dir = Path(out_dir)
    scenes_by_id = {s.id: s for s in sb.scenes}
    work = out_dir / "qc_frames"
    has_ffprobe = bool(shutil.which("ffprobe"))
    rows: list[tuple[str, Path, str]] = []  # (sid, path, "ok" | "technical" | "content")
    unreadable: set[str] = set()

    for sid, path in clips:
        s = scenes_by_id.get(sid)
        rec = current(out_dir, sid, path)
        if rec is None and run_local_qc and has_ffprobe and s is not None:
            kf = out_dir / "keyframes" / f"{sid}.png"
            rec = check_scene_openmontage(sb, s, path, kf if s.mode == "keyframe" and kf.is_file() else None, work)
            if rec["verdict"] == "FAIL":
                _save(out_dir, sid, rec)
                log(f"  ✘ [{sid}] Hậu kiểm OpenMontage chặn clip lỗi: {'; '.join(rec['issues'])}")
        if rec is not None and rec.get("verdict") == "FAIL":
            rows.append((sid, path, "technical" if _technical_fail(rec) else "content"))
            if ((rec.get("openmontage") or {}).get("ffprobe") or {}).get("valid") is False:
                unreadable.add(sid)
        else:
            rows.append((sid, path, "ok"))

    technical = [sid for sid, _, kind in rows if kind == "technical"]
    content = [sid for sid, _, kind in rows if kind == "content"]
    limit = gate_drop_limit(len(clips))
    drop_content = len(technical) + len(content) <= limit
    if content and not drop_content:
        log(f"⚠ AI QC đánh FAIL {len(content)} clip ({', '.join(content)}), nhưng loại hết sẽ mất "
            f"{len(technical) + len(content)}/{len(clips)} cảnh (giới hạn {limit}): GIỮ LẠI các clip này trong phim — "
            "hãy xem lý do ở tab Cảnh, rồi render lại hoặc bỏ cảnh bằng tay.")
    drop = set(technical) | (set(content) if drop_content else set())
    kept = [(sid, path) for sid, path, _ in rows if sid not in drop]
    rejected = [sid for sid, _, _ in rows if sid in drop]

    if rejected and kept:
        log(f"🚫 Cổng QC loại bỏ {len(rejected)} clip lỗi khỏi bản ghép cuối: {', '.join(rejected)}")
        return kept, rejected
    if rejected and not kept:
        readable = [(sid, path) for sid, path, _ in rows if sid not in unreadable]
        if readable:
            log(f"⚠ Tất cả {len(rejected)} clip đều bị đánh dấu FAIL ({', '.join(rejected)}); "
                "giữ tạm các file đọc được để tránh bản ghép rỗng — hãy render lại cảnh lỗi.")
            return readable, rejected
    return kept, rejected
    if rejected and not kept and readable_fallback:
        log(f"⚠ Tất cả {len(rejected)} clip đều bị đánh dấu FAIL ({', '.join(rejected)}); "
            "giữ tạm các file đọc được để tránh bản ghép rỗng — hãy render lại cảnh lỗi.")
        return readable_fallback, rejected
    return kept, rejected
