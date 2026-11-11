"""Editor agent: turns the finished clips into one smooth film.

1. analyze()    - ffmpeg looks at every clip: length, black / frozen frames at the ends, silence, loudness.
2. ai_plan()    - Claude reads the story + the analysis and writes an edit plan (trims, transitions, fades, audio).
   auto_plan()  - same plan from simple rules when Claude is not available.
3. render()     - ffmpeg follows the plan: trims, xfade transitions with audio crossfades, fade in/out,
                  even loudness between clips, background music ducked under dialogue, subtitles in sync.

The plan is saved next to the clips (edit.json) so it can be changed by hand and rendered again for free.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field

from .assemble import (AssembleError, _need, burn_track, has_audio, probe_duration, subtitle_track_args,
                       write_subtitle_tracks)
from .storyboard import Storyboard

Log = Callable[[str], None]

# name -> (ffmpeg xfade transition or None for a straight cut, Vietnamese label)
TRANSITIONS: dict[str, tuple[str | None, str]] = {
    "cut": (None, "Cắt thẳng"),
    "crossfade": ("fade", "Hoà tan (crossfade)"),
    "fadeblack": ("fadeblack", "Mờ qua đen"),
    "fadewhite": ("fadewhite", "Loé trắng"),
    "dissolve": ("dissolve", "Tan hạt"),
    "smoothleft": ("smoothleft", "Lướt mềm sang trái"),
    "smoothright": ("smoothright", "Lướt mềm sang phải"),
    "slideleft": ("slideleft", "Trượt sang trái"),
    "slideright": ("slideright", "Trượt sang phải"),
    "wipeleft": ("wipeleft", "Gạt sang trái"),
    "circleopen": ("circleopen", "Mở tròn"),
    "circleclose": ("circleclose", "Đóng tròn"),
    "zoomin": ("zoomin", "Phóng to"),
    "hblur": ("hblur", "Nhoè ngang"),
    "radial": ("radial", "Quét tròn"),
}
TransitionType = Literal[
    "cut", "crossfade", "fadeblack", "fadewhite", "dissolve", "smoothleft", "smoothright", "slideleft",
    "slideright", "wipeleft", "circleopen", "circleclose", "zoomin", "hblur", "radial",
]

MAX_TRIM = 3.0
MIN_KEEP = 1.0  # a clip never gets shorter than this after trimming
MAX_TRANSITION = 2.0
FPS = 24


# --- plan ---------------------------------------------------------------------


class ClipEdit(BaseModel):
    id: str = Field(description="Scene id.")
    trim_start: float = Field(default=0.0, description=f"Seconds cut from the start (0-{MAX_TRIM}).")
    trim_end: float = Field(default=0.0, description=f"Seconds cut from the end (0-{MAX_TRIM}).")
    volume: float = Field(default=1.0, description="Gain for this clip's own audio (0-2, 1 = unchanged).")


class Transition(BaseModel):
    after: str = Field(description="Scene id the transition comes after (it leads into the next scene).")
    type: TransitionType = "crossfade"
    duration: float = Field(default=0.5, description=f"Seconds (0.2-{MAX_TRANSITION}); ignored for a cut.")
    reason: str = Field(default="", description="Short why, in Vietnamese.")


class EditPlan(BaseModel):
    clips: list[ClipEdit] = Field(default_factory=list, description="One entry per scene, in film order.")
    transitions: list[Transition] = Field(default_factory=list, description="One entry between every two scenes.")
    fade_in: float = Field(default=0.6, description="Fade from black at the start, seconds (0-3).")
    fade_out: float = Field(default=1.2, description="Fade to black (and silence) at the end, seconds (0-4).")
    duck_music: bool = Field(default=True, description="Lower the background music while the clips have sound.")
    even_loudness: bool = Field(default=True, description="Bring every clip to a similar loudness.")
    normalize: bool = Field(default=True, description="Normalize the whole mix to streaming loudness (-14 LUFS).")
    notes: str = Field(default="", description="The editor's short summary of the cut, in Vietnamese.")
    source: Literal["rules", "ai", "manual"] = "rules"


class ClipInfo(BaseModel):
    id: str
    path: str
    duration: float
    has_audio: bool
    mean_volume: float | None = None  # dB
    max_volume: float | None = None
    lead_black: float = 0.0  # seconds of black frames at the start
    tail_black: float = 0.0
    lead_freeze: float = 0.0  # seconds of frozen picture at the start
    tail_freeze: float = 0.0
    tail_silence: float = 0.0
    mostly_silent: bool = False


# --- analysis -----------------------------------------------------------------

_NUM = r"(-?[\d.]+)"


def _parse_detect(stderr: str, duration: float) -> dict:
    def spans(start_key: str, end_key: str) -> list[tuple[float, float]]:
        starts = [float(x) for x in re.findall(rf"{start_key}[:=]\s*{_NUM}", stderr)]
        ends = [float(x) for x in re.findall(rf"{end_key}[:=]\s*{_NUM}", stderr)]
        out = []
        for i, s in enumerate(starts):
            out.append((s, ends[i] if i < len(ends) else duration))
        return out

    def lead(sp):
        return round(max((e for s, e in sp if s <= 0.05), default=0.0), 3)

    def tail(sp):
        return round(max((duration - s for s, e in sp if e >= duration - 0.1), default=0.0), 3)

    black = spans("black_start", "black_end")
    freeze = spans("freeze_start", "freeze_end")
    silence = spans("silence_start", "silence_end")
    mean = re.search(rf"mean_volume:\s*{_NUM} dB", stderr)
    peak = re.search(rf"max_volume:\s*{_NUM} dB", stderr)
    silent_total = sum(min(e, duration) - max(s, 0) for s, e in silence)
    return {
        "lead_black": lead(black), "tail_black": tail(black),
        "lead_freeze": lead(freeze), "tail_freeze": tail(freeze),
        "tail_silence": tail(silence),
        "mean_volume": float(mean.group(1)) if mean else None,
        "max_volume": float(peak.group(1)) if peak else None,
        "mostly_silent": duration > 0 and silent_total / duration > 0.8,
    }


def analyze_clip(sid: str, path: Path) -> ClipInfo:
    ffmpeg = _need("ffmpeg")
    duration = probe_duration(path)
    audio = has_audio(path)
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
           "-vf", "blackdetect=d=0.1:pix_th=0.10,freezedetect=n=0.003:d=0.4"]
    cmd += ["-af", "silencedetect=n=-45dB:d=0.3,volumedetect"] if audio else ["-an"]
    cmd += ["-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssembleError(f"không phân tích được {path.name}: {proc.stderr.strip()[-400:]}")
    info = _parse_detect(proc.stderr, duration)
    if not audio:
        info.update(mean_volume=None, max_volume=None, mostly_silent=True, tail_silence=duration)
    return ClipInfo(id=sid, path=str(path), duration=round(duration, 3), has_audio=audio, **info)


def analyze(clips: list[tuple[str, Path]], *, cache: Path | None = None, log: Log = print) -> dict[str, ClipInfo]:
    """Analysis of every clip, cached by file size + mtime so re-editing is quick."""
    stored: dict = {}
    if cache and cache.is_file():
        try:
            stored = json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            stored = {}
    out: dict[str, ClipInfo] = {}
    for sid, path in clips:
        st = path.stat()
        key = f"{path}|{st.st_size}|{int(st.st_mtime)}"
        hit = stored.get(sid)
        if hit and hit.get("key") == key:
            out[sid] = ClipInfo.model_validate(hit["info"])
            continue
        log(f"  phân tích {sid} ({path.name})…")
        out[sid] = analyze_clip(sid, path)
        stored[sid] = {"key": key, "info": out[sid].model_dump()}
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(stored, indent=1, ensure_ascii=False), encoding="utf-8")
    return out


# --- rules --------------------------------------------------------------------


def _suggested_trims(info: ClipInfo) -> tuple[float, float]:
    # A picture frozen for most of the clip is a still shot, not a dead ending: leave it alone.
    still = info.lead_freeze > 0.6 * info.duration or info.tail_freeze > 0.6 * info.duration
    start = min(info.lead_black, 1.5)
    end = min(max(info.tail_black, 0.0 if still else info.tail_freeze), 2.0)
    return round(start, 2), round(end, 2)


def _loudness_gains(infos: list[ClipInfo]) -> dict[str, float]:
    """Gain per clip so they sit around the median loudness (within +-6 dB)."""
    loud = [i for i in infos if i.has_audio and i.mean_volume is not None and i.mean_volume > -55 and not i.mostly_silent]
    if len(loud) < 2:
        return {}
    target = statistics.median(i.mean_volume for i in loud)
    gains = {}
    for i in loud:
        db = max(-6.0, min(6.0, target - i.mean_volume))
        gains[i.id] = round(10 ** (db / 20), 2)
    return gains


def auto_plan(sb: Storyboard, infos: dict[str, ClipInfo], order: list[str]) -> EditPlan:
    """A safe, good-looking default: trim dead frames, soft crossfades, fade in/out, even loudness."""
    clips = []
    for sid in order:
        ts, te = _suggested_trims(infos[sid])
        clips.append(ClipEdit(id=sid, trim_start=ts, trim_end=te))
    transitions = [Transition(after=a, type="crossfade", duration=0.5, reason="Hoà tan nhẹ giữ mạch liền")
                   for a in order[:-1]]
    plan = EditPlan(clips=clips, transitions=transitions, source="rules",
                    notes="Bản dựng tự động: cắt khung đen/đứng hình ở đầu-cuối clip, hoà tan 0,5s giữa các cảnh, "
                          "mở/kết bằng mờ đen, cân bằng âm lượng giữa các clip.")
    return sanitize(plan, infos, order)


def sanitize(plan: EditPlan, infos: dict[str, ClipInfo], order: list[str]) -> EditPlan:
    """Make any plan (AI, hand-edited, or from an older set of clips) safe to render with these clips."""
    by_id = {c.id: c for c in plan.clips}
    clips: list[ClipEdit] = []
    for sid in order:
        c = by_id.get(sid) or ClipEdit(id=sid, trim_start=_suggested_trims(infos[sid])[0],
                                       trim_end=_suggested_trims(infos[sid])[1])
        d = infos[sid].duration
        ts = max(0.0, min(MAX_TRIM, float(c.trim_start or 0)))
        te = max(0.0, min(MAX_TRIM, float(c.trim_end or 0)))
        room = d - MIN_KEEP
        if ts + te > room:  # too much trimmed: scale both down
            k = max(0.0, room) / (ts + te) if ts + te else 0
            ts, te = ts * k, te * k
        vol = max(0.0, min(2.0, float(c.volume if c.volume is not None else 1.0)))
        clips.append(ClipEdit(id=sid, trim_start=round(ts, 3), trim_end=round(te, 3), volume=round(vol, 2)))
    if plan.even_loudness:
        gains = _loudness_gains([infos[s] for s in order])
        for c in clips:  # only clips the plan left at 1.0 get the automatic gain
            if c.volume == 1.0 and c.id in gains:
                c.volume = gains[c.id]
    length = {c.id: infos[c.id].duration - c.trim_start - c.trim_end for c in clips}

    tr_by = {t.after: t for t in plan.transitions}
    transitions: list[Transition] = []
    for a, b in zip(order, order[1:]):
        t = tr_by.get(a) or Transition(after=a)
        typ = t.type if t.type in TRANSITIONS else "crossfade"
        dur = 0.0 if typ == "cut" else max(0.2, min(MAX_TRANSITION, float(t.duration or 0.5)))
        dur = min(dur, 0.45 * length[a], 0.45 * length[b])  # never eat more than half of a clip
        if typ != "cut" and dur < 0.1:
            typ, dur = "cut", 0.0
        transitions.append(Transition(after=a, type=typ, duration=round(dur, 3), reason=t.reason[:200]))

    total = sum(length.values()) - sum(t.duration for t in transitions)
    fi = max(0.0, min(3.0, float(plan.fade_in), total / 4))
    fo = max(0.0, min(4.0, float(plan.fade_out), total / 3))
    return plan.model_copy(update={"clips": clips, "transitions": transitions,
                                   "fade_in": round(fi, 3), "fade_out": round(fo, 3), "notes": plan.notes[:1500]})


# --- timeline -----------------------------------------------------------------


def timeline(plan: EditPlan, infos: dict[str, ClipInfo]) -> tuple[list[tuple[str, float, float]], float]:
    """[(scene id, start in film, length)] and the film's total length."""
    rows, t = [], 0.0
    durs = [t.duration for t in plan.transitions] + [0.0]
    for i, c in enumerate(plan.clips):
        length = infos[c.id].duration - c.trim_start - c.trim_end
        rows.append((c.id, t, length))
        t += length - durs[i]
    return rows, t


def subtitle_times(plan: EditPlan, infos: dict[str, ClipInfo]) -> list[tuple[str, float, float]]:
    """[(scene id, start, end)] where a subtitle line can show, inside the transitions."""
    rows, _ = timeline(plan, infos)
    durs_in = [0.0] + [t.duration for t in plan.transitions]
    durs_out = [t.duration for t in plan.transitions] + [0.0]
    out = []
    for i, (sid, start, length) in enumerate(rows):
        a = start + max(0.2, durs_in[i] / 2)
        b = start + length - max(0.2, durs_out[i] / 2)
        if b - a > 0.3:
            out.append((sid, round(a, 3), round(b, 3)))
    return out


def subtitle_entries(sb: Storyboard, plan: EditPlan, infos: dict[str, ClipInfo],
                     code: str | None = None) -> list[tuple[float, float, str]]:
    """Timed lines of one track (default: the primary language)."""
    code = code or sb.primary_subtitle_code()
    scenes = {s.id: s for s in sb.scenes}
    return [(a, b, line) for sid, a, b in subtitle_times(plan, infos)
            if sid in scenes and (line := sb.subtitle_line(scenes[sid], code))]


# --- render -------------------------------------------------------------------


def build_filtergraph(plan: EditPlan, infos: dict[str, ClipInfo], *, width: int, height: int,
                      music_input: int | None, music_volume: float, subtitles: Path | None) -> tuple[str, str, str]:
    """(filter_complex, video label, audio label). Inputs 0..n-1 are the clips in plan order."""
    f: list[str] = []
    rows, total = timeline(plan, infos)
    for i, c in enumerate(plan.clips):
        info = infos[c.id]
        a, b = c.trim_start, info.duration - c.trim_end
        f.append(f"[{i}:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,"
                 f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                 f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p,settb=AVTB[v{i}]")
        length = b - a
        if info.has_audio:
            f.append(f"[{i}:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
                     f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={c.volume:.2f},"
                     f"apad=whole_dur={length:.3f},atrim=0:{length:.3f}[a{i}]")
        else:
            f.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{length:.3f},aformat=sample_fmts=fltp[a{i}]")

    v, a = "[v0]", "[a0]"
    cur = rows[0][2]
    for k, t in enumerate(plan.transitions, start=1):
        nv, na = f"[vx{k}]", f"[ax{k}]"
        xf = TRANSITIONS[t.type][0]
        if xf is None or t.duration <= 0:
            f.append(f"{v}[v{k}]concat=n=2:v=1:a=0{nv}")
            f.append(f"{a}[a{k}]concat=n=2:v=0:a=1{na}")
        else:
            f.append(f"{v}[v{k}]xfade=transition={xf}:duration={t.duration:.3f}:offset={cur - t.duration:.3f}{nv}")
            f.append(f"{a}[a{k}]acrossfade=d={t.duration:.3f}:c1=tri:c2=tri{na}")
        cur += rows[k][2] - t.duration
        v, a = nv, na

    vf = []
    if plan.fade_in > 0:
        vf.append(f"fade=t=in:st=0:d={plan.fade_in:.3f}")
    if plan.fade_out > 0:
        vf.append(f"fade=t=out:st={max(0, total - plan.fade_out):.3f}:d={plan.fade_out:.3f}")
    if subtitles:
        esc = str(subtitles).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        vf.append(f"subtitles='{esc}'")
    if vf:
        f.append(f"{v}{','.join(vf)}[vout]")
        v = "[vout]"

    if music_input is not None:
        mfade = max(plan.fade_out, 1.5)
        f.append(f"[{music_input}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
                 f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={music_volume:.2f},"
                 f"afade=t=in:st=0:d=1.5,afade=t=out:st={max(0, total - mfade):.3f}:d={mfade:.3f}[bg]")
        if plan.duck_music:
            f.append(f"{a}asplit=2[dlg][key]")
            f.append("[bg][key]sidechaincompress=threshold=0.04:ratio=6:attack=30:release=600:makeup=1[bgd]")
            f.append("[dlg][bgd]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
        else:
            f.append(f"{a}[bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
        a = "[mix]"

    af = []
    if plan.fade_in > 0:
        af.append(f"afade=t=in:st=0:d={min(plan.fade_in, 1.0):.3f}")
    if plan.fade_out > 0:
        af.append(f"afade=t=out:st={max(0, total - plan.fade_out):.3f}:d={plan.fade_out:.3f}")
    if plan.normalize:
        af += ["loudnorm=I=-14:TP=-1.5:LRA=11", "aresample=48000"]
    af.append("alimiter=limit=0.95")
    f.append(f"{a}{','.join(af)}[aout]")
    return ";".join(f), v, "[aout]"


def render(sb: Storyboard, clips: list[tuple[str, Path]], plan: EditPlan, infos: dict[str, ClipInfo], output: Path,
           *, music: Path | None = None, music_volume: float = 0.25, burn_subtitles: bool = False,
           burn_language: str | None = None, log: Log = print) -> Path:
    ffmpeg = _need("ffmpeg")
    paths = dict(clips)
    width, height = (1920, 1080) if sb.aspect == "16:9" else (1080, 1920)
    output.parent.mkdir(parents=True, exist_ok=True)

    tracks = write_subtitle_tracks(sb, subtitle_times(plan, infos), output, log=log)
    burn = burn_track(sb, tracks, burn_language) if burn_subtitles else None

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for c in plan.clips:
        cmd += ["-i", str(paths[c.id])]
    music_input = None
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        music_input = len(plan.clips)
    graph, v, a = build_filtergraph(plan, infos, width=width, height=height, music_input=music_input,
                                    music_volume=music_volume, subtitles=burn)
    sub_in, sub_out = subtitle_track_args(tracks, len(plan.clips) + (1 if music else 0))
    cmd += sub_in
    _, total = timeline(plan, infos)
    tmp = output.with_name(output.stem + ".editing" + output.suffix)
    cmd += ["-filter_complex", graph, "-map", v, "-map", a, *sub_out, "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)]
    cuts = sum(1 for t in plan.transitions if t.type == "cut")
    log(f"Dựng {len(plan.clips)} cảnh ({total:.1f}s), {len(plan.transitions) - cuts} chuyển cảnh, "
        f"{cuts} cắt thẳng{', có nhạc nền' if music else ''} -> {output}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise AssembleError(f"ffmpeg lỗi khi dựng phim: {proc.stderr.strip()[-1500:]}")
    os.replace(tmp, output)  # the old film stays playable until the new one is complete
    return output


# --- Claude (the editor agent) ------------------------------------------------

SYSTEM = """You are a senior film editor. A short film was generated scene by scene by an AI video model (Veo):
each scene is a separate 4-8 second clip, so cuts can feel disjointed. Your job is an edit plan that makes the
film flow as ONE continuous story. You do not see the pictures; you get the script of each scene and an ffmpeg
analysis of each clip.

Decide, for every scene, in film order:
- trim_start / trim_end (seconds): cut black or frozen frames, slow starts and dead endings. Use the analysis
  (lead_black, tail_black, lead_freeze, tail_freeze, tail_silence) and the suggested trims. Never cut into
  speech or the key action; keep at least ~2.5 s of every clip. AI clips often end with a frozen or drifting
  last second: trimming 0.3-1.0 s from the end is common. 0 is fine when the clip is clean.
- volume: usually 1.0 (loudness is evened out automatically). Lower (0.6-0.8) a clip whose sound would fight
  the music or the next scene; raise only if a key line is too quiet.

For every pair of neighbouring scenes, one transition into the next scene:
- "cut" for continuous action, the same place/time, dialogue back-and-forth, fast tension. Straight cuts are
  the backbone of good editing; do not put an effect on every cut.
- "crossfade" (0.4-0.8 s) for a soft link: same mood, small jump in place, or emotional continuity.
- "fadeblack" (0.6-1.2 s) for a jump in time, a change of chapter, after a dramatic beat or before a reveal.
- "fadewhite" (0.3-0.6 s) for a flash: memory, shock, dream, a sudden realisation.
- "smoothleft"/"smoothright"/"slideleft"/"slideright"/"wipeleft" (0.4-0.7 s) for a change of place in a light,
  energetic or comic film. Rarely in drama.
- "circleopen"/"circleclose"/"zoomin"/"radial"/"hblur"/"dissolve": stylised; only when the tone clearly asks.
Keep the style consistent: pick 2-3 transition types for the whole film. Give each transition a short `reason`
in Vietnamese.

Also: fade_in (0.3-1.0 s from black), fade_out (0.8-2.5 s ending), duck_music (true when there is music and the
clips have dialogue or important sound), even_loudness (normally true), normalize (normally true).
`notes`: 2-4 sentences in Vietnamese explaining the rhythm and the choices, for the user.
Return exactly one clips entry per scene and one transitions entry per neighbouring pair, using the given ids."""


def _brief(sb: Storyboard, infos: dict[str, ClipInfo], order: list[str], has_music: bool) -> str:
    scenes = {s.id: s for s in sb.scenes}
    lines = [f"Film: {sb.title} ({sb.aspect})"]
    if sb.style:
        lines.append(f"Style: {sb.style}")
    lines.append(f"Background music: {'yes' if has_music else 'no'}")
    lines.append("Scenes in film order:")
    for sid in order:
        s, i = scenes[sid], infos[sid]
        ts, te = _suggested_trims(i)
        sound = ("no audio track" if not i.has_audio else
                 f"mean {i.mean_volume} dB, {'mostly silent' if i.mostly_silent else 'has sound'}, "
                 f"silent tail {i.tail_silence:.2f}s")
        lines.append(
            f"- {sid}: {i.duration:.2f}s | black start/end {i.lead_black:.2f}/{i.tail_black:.2f}s | "
            f"frozen start/end {i.lead_freeze:.2f}/{i.tail_freeze:.2f}s | {sound} | suggested trims {ts}/{te}\n"
            f"  opening frame: {(s.image_prompt or '-')[:400]}\n  action: {s.prompt[:500]}"
            + (f"\n  narration: {s.narration}" if s.narration else ""))
    return "\n".join(lines)


def ai_plan(sb: Storyboard, infos: dict[str, ClipInfo], order: list[str], *, has_music: bool,
            backend: str = "auto", api_key: str | None = None, timeout: int = 600, log: Log = print) -> EditPlan:
    from .planner import MODEL, _subscription_env, claude_code_binary

    user = _brief(sb, infos, order, has_music) + "\n\nWrite the edit plan."
    if backend == "auto":
        backend = "claude_code" if claude_code_binary() else "api"
    log(f"Agent dựng phim (Claude, {'gói Claude Code' if backend == 'claude_code' else 'API'}) đang xem kịch bản và các clip…")
    if backend == "claude_code":
        binary = claude_code_binary()
        if not binary:
            raise RuntimeError("không thấy lệnh `claude` (Claude Code)")
        schema = json.dumps(EditPlan.model_json_schema())
        cmd = [binary, "-p", user, "--output-format", "json", "--json-schema", schema, "--system-prompt", SYSTEM,
               "--tools", "", "--strict-mcp-config", "--no-session-persistence"]
        with tempfile.TemporaryDirectory() as cwd:
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                                      env=_subscription_env())
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Claude Code chạy quá lâu khi lập kế hoạch dựng.") from exc
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            raise RuntimeError(f"Claude Code lỗi (exit {proc.returncode}): "
                               f"{(proc.stderr or proc.stdout).strip()[-400:]}") from None
        if data.get("is_error") or not data.get("structured_output"):
            raise RuntimeError("Claude Code không lập được kế hoạch dựng: " + str(data.get("result") or "không có kết quả")[:400])
        plan = EditPlan.model_validate(data["structured_output"])
    else:
        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("chưa có ANTHROPIC_API_KEY (hoặc cài Claude Code để dùng gói Claude của bạn)")
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("cần cài thêm: pip install 'film-maker-ai[ai]' (gói anthropic)") from exc
        response = anthropic.Anthropic().messages.parse(
            model=MODEL, max_tokens=8000, system=SYSTEM, messages=[{"role": "user", "content": user}],
            output_format=EditPlan, output_config={"effort": "medium"},
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"}, extra_body={"fallbacks": "default"},
        )
        if response.stop_reason == "refusal" or not response.parsed_output:
            raise RuntimeError("Claude không lập được kế hoạch dựng cho phim này.")
        plan = response.parsed_output
    plan.source = "ai"
    return sanitize(plan, infos, order)


# --- whole job ----------------------------------------------------------------


def plan_path(out_dir: Path) -> Path:
    return out_dir / "edit.json"


def load_plan(out_dir: Path) -> EditPlan | None:
    p = plan_path(out_dir)
    if not p.is_file():
        return None
    try:
        return EditPlan.model_validate_json(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def save_plan(plan: EditPlan, out_dir: Path) -> Path:
    p = plan_path(out_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return p


def make_plan(sb: Storyboard, clips: list[tuple[str, Path]], out_dir: Path, *, engine: str = "auto",
              replan: bool = False, has_music: bool = False, backend: str = "auto", api_key: str | None = None,
              ai_available: bool = True, log: Log = print) -> tuple[EditPlan, dict[str, ClipInfo]]:
    """engine: auto (Claude if available, else rules) | ai | rules. A saved plan is reused unless replan."""
    if not clips:
        raise AssembleError("không có clip nào để dựng")
    order = [sid for sid, _ in clips]
    log(f"Phân tích {len(clips)} clip…")
    infos = analyze(clips, cache=out_dir / "edit_analysis.json", log=log)
    saved = None if replan else load_plan(out_dir)
    if saved:
        log(f"Dùng bản dựng đã lưu ({plan_path(out_dir).name}, nguồn: {saved.source}). Bấm “Dựng lại từ đầu” để agent làm bản mới.")
        plan = sanitize(saved, infos, order)
    elif engine == "rules" or (engine == "auto" and not ai_available):
        if engine == "auto":
            log("Chưa dùng được Claude nên dựng theo luật mặc định (vẫn có chuyển cảnh, cân âm, mở/kết mờ).")
        plan = auto_plan(sb, infos, order)
    else:
        try:
            plan = ai_plan(sb, infos, order, has_music=has_music, backend=backend, api_key=api_key, log=log)
        except RuntimeError as exc:
            if engine == "ai":
                raise
            log(f"⚠ Agent AI không chạy được ({exc}); dựng theo luật mặc định.")
            plan = auto_plan(sb, infos, order)
    save_plan(plan, out_dir)
    if plan.notes:
        log(f"Ghi chú dựng: {plan.notes}")
    for t in plan.transitions:
        label = TRANSITIONS[t.type][1]
        log(f"  {t.after} → {label}{'' if t.type == 'cut' else f' {t.duration:.1f}s'}"
            f"{f' — {t.reason}' if t.reason else ''}")
    return plan, infos


def edit_film(sb: Storyboard, clips: list[tuple[str, Path]], out_dir: Path, output: Path | None = None, *,
              music: Path | None = None, music_volume: float = 0.25, burn_subtitles: bool = False,
              burn_language: str | None = None, engine: str = "auto", replan: bool = False, backend: str = "auto", api_key: str | None = None,
              ai_available: bool = True, log: Log = print) -> tuple[Path, EditPlan]:
    plan, infos = make_plan(sb, clips, out_dir, engine=engine, replan=replan, has_music=bool(music),
                            backend=backend, api_key=api_key, ai_available=ai_available, log=log)
    output = output or out_dir / "final.mp4"
    render(sb, clips, plan, infos, output, music=music, music_volume=music_volume,
           burn_subtitles=burn_subtitles, burn_language=burn_language, log=log)
    return output, plan


def transition_catalog() -> list[dict[str, str]]:
    return [{"id": k, "label": v[1]} for k, v in TRANSITIONS.items()]


__all__ = ["EditPlan", "ClipEdit", "Transition", "ClipInfo", "TRANSITIONS", "analyze", "auto_plan", "ai_plan",
           "sanitize", "timeline", "subtitle_times", "subtitle_entries", "build_filtergraph", "render", "make_plan", "edit_film",
           "load_plan", "save_plan", "plan_path", "transition_catalog"]
