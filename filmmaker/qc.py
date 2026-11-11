"""AI QC of each rendered scene clip: right scene, right characters, clean picture, right sound and dialogue.

For every clip it gathers, for free (no Flow credits):
- how well the clip's first frame matches the scene's keyframe (the start picture Veo was given),
- 6 frames across the clip, the keyframe and the portraits of the characters in the scene,
- the sound: is there an audio track, how loud, how much of it is silence,
- what is said: a transcript by whisper.cpp (offline), with the language it heard,
then one Claude call judges it all against the scene (prompt, content direction, expected dialogue).

Results are kept in `<out>/qc.json` per scene, tied to the clip file, so a new clip is checked again.
QC only flags: it never renders anything (re-rendering costs credits, the user decides).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from . import ai, reviewer
from .storyboard import NO_DIALOGUE, Scene, Storyboard

QC_FILE = "qc.json"
MODEL_NAME = "ggml-large-v3-turbo-q5_0.bin"
MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{MODEL_NAME}"
FRAMES = 6
WRONG_START = 0.75  # first frame vs keyframe below this: the clip was made from another picture

Verdict = Literal["PASS", "MINOR", "FAIL"]

_lock = threading.Lock()


# --- answer shape ----------------------------------------------------------------


class QCAnswer(BaseModel):
    verdict: Verdict = Field(description="PASS = good; MINOR = small flaws a viewer accepts; FAIL = should be "
                                         "rendered again (wrong scene/characters, broken picture, wrong or missing dialogue).")
    score: int = Field(description="1-10")
    issues: list[str] = Field(default_factory=list, description="Problems, short and concrete, in Vietnamese.")
    picture: str = Field(default="", description="One short sentence on the picture, in Vietnamese.")
    sound: str = Field(default="", description="One short sentence on the sound and dialogue, in Vietnamese.")
    prompt: str | None = Field(default=None, description="When FAIL and the action prompt is the cause: a corrected "
                                                         "action prompt (English). Name characters exactly.")


SYSTEM = reviewer.COMMON + """
You are the QC checker of ONE rendered scene clip, right after it was downloaded. You get the scene as written,
the keyframe (the opening picture Veo was asked to animate), the portraits of the characters in the scene,
6 frames across the clip in time order, and measurements: how well the clip's first frame matches the keyframe
(1.0 = same picture; under 0.75 means Veo animated ANOTHER picture: always FAIL), the audio levels, and a
speech-to-text transcript of the clip with the language it heard (the transcript can misspell words, above all
in Thai and Vietnamese: judge the meaning, not the spelling).
Check:
- Picture: it is THIS scene (place, people, action from the keyframe), the right characters with the right faces
  and clothes, no morphing faces/bodies, extra limbs, people appearing or vanishing, frozen or black frames, and
  no burned-in subtitles, captions or gibberish text (any readable or fake text in the picture = FAIL).
- Sound: when the scene has dialogue, the expected lines are spoken (same meaning), in the expected language,
  once, by one speaker at a time; no extra lines nobody wrote, no line said twice, no other language. Silence or
  only music where a line was expected = FAIL. When the scene has no dialogue, speech in the clip is a flaw
  (MINOR, FAIL if it is long or says something odd). A clip with no audio at all is FAIL unless sound is not expected.
- Lip-sync you cannot verify from stills: do not guess about it.
FAIL only for what a viewer would clearly notice; MINOR for small flaws; PASS when it is good.
Give `prompt` only when a FAIL would likely be fixed by a different action prompt."""


# --- tools: whisper, audio, frames -----------------------------------------------


def model_dir() -> Path:
    from .settings import settings_path

    return settings_path().parent / "models"


def whisper_binary() -> str | None:
    return shutil.which("whisper-cli") or shutil.which("whisper-cpp")


def whisper_model() -> Path | None:
    env = os.environ.get("FILMMAKER_WHISPER_MODEL")
    for p in ([Path(env)] if env else []) + [model_dir() / MODEL_NAME]:
        if p.is_file() and p.stat().st_size > 10_000_000:
            return p
    return None


def status() -> dict[str, Any]:
    """What QC can do on this machine."""
    return {"whisper": bool(whisper_binary()), "model": bool(whisper_model()), "model_name": MODEL_NAME,
            "model_path": str(model_dir() / MODEL_NAME), "ffmpeg": bool(shutil.which("ffmpeg"))}


def download_model(log: Callable[[str], None] = lambda *_: None) -> Path:
    """Fetch the whisper model once (about 550 MB) into the settings folder."""
    import urllib.request

    dest = model_dir() / MODEL_NAME
    if whisper_model():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".part")
    log(f"Đang tải model nhận dạng giọng nói ({MODEL_NAME}, khoảng 550 MB)…")
    with urllib.request.urlopen(MODEL_URL, timeout=60) as r, part.open("wb") as f:
        total, done, step = int(r.headers.get("Content-Length") or 0), 0, 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total and done * 10 // total > step:
                step = done * 10 // total
                log(f"  {step * 10}%")
    if part.stat().st_size < 10_000_000:
        part.unlink(missing_ok=True)
        raise RuntimeError("tải model không thành công")
    part.replace(dest)
    return dest


def audio_stats(clip: Path) -> dict[str, Any]:
    """Audio track present, mean/max volume (dB) and the share of the clip that is silent."""
    out: dict[str, Any] = {"has_audio": False}
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
    for line in err.splitlines():
        if "mean_volume:" in line:
            out["mean_db"] = float(line.split("mean_volume:")[1].split()[0])
        elif "max_volume:" in line:
            out["max_db"] = float(line.split("max_volume:")[1].split()[0])
        elif "silence_duration:" in line:
            silent += float(line.split("silence_duration:")[1].split()[0])
    dur = reviewer.duration_of(clip)
    out["duration"] = round(dur, 2)
    out["silent_share"] = round(min(1.0, silent / dur), 2) if dur else None
    return out


def transcribe(clip: Path) -> dict[str, Any] | None:
    """{"language", "text", "segments": [{"start", "end", "text"}]} via whisper.cpp; None when it is not set up."""
    binary, model = whisper_binary(), whisper_model()
    if not binary or not model:
        return None
    with tempfile.TemporaryDirectory(prefix="fm-qc-") as tmp:
        wav, base = Path(tmp) / "a.wav", Path(tmp) / "t"
        try:
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(clip), "-vn", "-ac", "1", "-ar", "16000",
                            str(wav)], check=True, capture_output=True, timeout=120)
            subprocess.run([binary, "-m", str(model), "-f", str(wav), "-l", "auto", "-oj", "-of", str(base), "-np",
                            "-t", str(max(2, (os.cpu_count() or 4) // 2))],
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
    return {"language": lang, "text": " ".join(s["text"] for s in segs), "segments": segs}


# --- one scene -------------------------------------------------------------------


def clip_sig(p: Path) -> str:
    try:
        st = p.stat()
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


def expected_dialogue(sb: Storyboard, s: Scene) -> str:
    if sb.dialogue_language == NO_DIALOGUE:
        return "No dialogue: nobody should speak (ambient sound and music only)."
    lines = [d for d in (s.dialogue or []) if d.line.strip()]
    lang = sb.dialogue_language or "the characters' natural language"
    if not lines:
        return f"No dialogue in this scene (nobody should say words). Film language: {lang}."
    rows = "\n".join(f"- {d.character}: {d.line}" + (f" ({d.delivery})" if d.delivery else "") for d in lines)
    return f"Expected dialogue, in {lang}, in this order, each line once:\n{rows}"


def check_scene(sb: Storyboard, s: Scene, clip: Path, keyframe: Path | None, work: Path, **ai_kw) -> dict[str, Any]:
    """QC of one clip. Returns the stored result (verdict, score, issues, measurements)."""
    from .flow_ui import frame_match

    raw_match = frame_match(keyframe, clip) if keyframe and keyframe.is_file() else None
    match = round(raw_match, 3) if raw_match is not None and raw_match == raw_match else None  # None or NaN: unknown
    sound = audio_stats(clip)
    speech = transcribe(clip)
    strip = reviewer._sheet(clip, work / f"{s.id}_frames.jpg", cols=FRAMES, rows=1, height=300)
    images: list[tuple[str, Path]] = []
    if keyframe and keyframe.is_file():
        images.append((f"scene {s.id} keyframe (the picture Veo was asked to animate)", keyframe))
    names = {c.name for c in sb.scene_characters(s)}
    images += [(label, p) for label, p in reviewer.portraits(sb) if label.removeprefix("portrait of ") in names]
    if strip:
        images.append((f"scene {s.id} clip: {FRAMES} frames in time order", strip))
    facts = [f"Start-frame match with the keyframe: {match if match is not None else 'unknown'}",
             f"Audio: {json.dumps(sound)}"]
    if speech is None:
        facts.append("Transcript: not available (speech-to-text is not set up): judge the sound from the audio "
                     "levels only and say the dialogue could not be checked.")
    else:
        facts.append(f"Transcript (language heard: {speech['language'] or 'unknown'}): "
                     + (" | ".join(f"[{x['start']}-{x['end']}s] {x['text']}" for x in speech["segments"]) or "(no speech)"))
    user = "\n".join([
        reviewer.storyboard_brief(sb), "",
        f"QC scene {s.id} ({s.duration}s).",
        f"Action prompt: {s.prompt}",
        f"Opening frame: {s.image_prompt or '-'}",
        f"Content direction: {s.content_direction or '-'}",
        expected_dialogue(sb, s), "", *facts])
    ans = ai.ask(SYSTEM, user, QCAnswer, images=images, **ai_kw)
    verdict, issues = ans.verdict, list(ans.issues)
    if match is not None and match < WRONG_START and verdict != "FAIL":  # never trust a clip of another picture
        verdict = "FAIL"
        issues.insert(0, f"Clip không bắt đầu từ keyframe của cảnh (độ khớp {match:.2f}): Flow đã dùng nhầm ảnh.")
    return {"verdict": verdict, "score": ans.score, "issues": issues, "picture": ans.picture, "sound": ans.sound,
            "prompt": ans.prompt, "match": match, "audio": sound,
            "transcript": speech["text"] if speech else None, "language": speech["language"] if speech else None,
            "dialogue_checked": speech is not None, "clip": clip.name, "sig": clip_sig(clip),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


# --- storage and runs ------------------------------------------------------------


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
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)


def current(out_dir: Path, sid: str, clip: Path | None) -> dict[str, Any] | None:
    """The stored QC of this scene if it is about the clip that is there now."""
    r = load(out_dir).get(sid)
    if not r or not clip or not clip.is_file() or r.get("sig") != clip_sig(clip):
        return None
    return r


def clip_of(pipe, s: Scene) -> Path:
    """The scene's clip: the path in state.json, or clips/<id>.mp4 when that path is relative to another folder
    (a CLI run with a relative --out stores it relative to where it ran)."""
    p = Path(pipe.state.scene(s.id).get("video") or "")
    return p if p.is_file() else pipe.clip_path(s)


def run(pipe, *, only: set[str] | None = None, force: bool = False, log: Callable[[str], None] = print,
        stop: Callable[[], bool] = lambda: False, **ai_kw) -> dict[str, dict[str, Any]]:
    """QC every rendered clip that has no QC for its current file (or the `only` scenes; `force` = again).
    Returns {scene id: result}. A scene whose QC cannot run is logged and skipped."""
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
    if not whisper_model():
        log("⚠ QC chưa kiểm tra được lời thoại: chưa có model nhận dạng giọng nói (Cài đặt → AI QC → Tải model).")
    work = out / "qc_frames"
    work.mkdir(parents=True, exist_ok=True)
    done: dict[str, dict[str, Any]] = {}
    for s, clip in todo:
        if stop():
            break
        log(f"[{s.id}] AI QC đang xem và nghe clip…")
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
