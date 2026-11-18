"""Clone a clip: remake a reference video shot for shot with NEW people, everything else the same.

Same sound (the original audio track is kept as is), same gestures, expressions, timing, camera and set;
only the characters change. It works by video-to-video editing on Google Flow's Omni models (upload a clip,
ask for the people to be swapped), which keeps the motion of the source far better than re-generating
each shot from a picture.

1. init()   - fetch the video (link or file), measure it, cut it into segments of at most `max(durations)`
              seconds at the shot cuts (Omni edits at most ~10 s at a time), write a contact sheet and,
              when an AI model is set up, describe the people in it so the cast is easy to fill in.
              Everything goes in <folder>/clone.json.
2. cast     - each person of the source gets a new character: a picture you give (`ref`), a Flow character
              (`ref: character:<name>`), or a description (`new_look`) that make_cast() turns into a Flow
              character (free).
3. render() - each segment is uploaded with the cast pictures and edited by Omni (costs credits; done
              segments are never re-rendered unless forced).
4. stitch() - the edited segments are fitted to the exact length of the source pieces (trim or a tiny
              speed change), joined, and the ORIGINAL audio is laid under them -> final.mp4.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from . import video_analysis as va

Log = Callable[[str], None]

STATE = "clone.json"
DEFAULT_MODEL = "omni-flash"
DEFAULT_DURATIONS = (4, 6, 8, 10)  # what Omni Flash renders
MODES = ("edit", "regen")  # edit: Omni edits the uploaded source; regen: re-created from a timed description
REGEN_MODEL = "omni-1.2-flash"  # 3-15 s by the second: a whole short clip can be one continuous take
REGEN_DURATIONS = tuple(range(3, 16))
# clip lengths (s) each video model renders; regen on Veo Lite also passes Flow's filter for scenes with small children
MODEL_DURATIONS = {"veo-lite": (4, 6, 8), "omni-1.2-flash": REGEN_DURATIONS, "omni-flash": DEFAULT_DURATIONS}
# Meta Muse (muse.ai): always one 10 s clip; regen only (it cannot edit an uploaded video). Its pictures (cast,
# start frames) come from Muse too, so a Muse clone never touches Flow.
MUSE_MODEL = "muse"
MODEL_DURATIONS[MUSE_MODEL] = (10,)


def is_muse(data: dict) -> bool:
    return str(data.get("model") or "").strip().lower() == MUSE_MODEL
MERGE_BELOW = 2.0  # a segment shorter than this is joined to its neighbour when the pair still fits
FIT_SLACK = 0.02  # a piece up to 2 % longer than the longest clip is still one clip (slowed a hair)
RETIME_MAX = 0.25  # fit a render to its piece by changing speed at most this much (else trim / hold frames)


class CloneError(RuntimeError):
    pass


# --- segments -----------------------------------------------------------------


def plan_segments(duration: float, cuts: list[float], durations: tuple[int, ...] | list[int] = DEFAULT_DURATIONS,
                  *, merge_below: float = MERGE_BELOW) -> list[dict[str, Any]]:
    """Cut [0, duration] into pieces Omni can edit in one go: split at the shot cuts, split long shots evenly,
    join too-short pieces to a neighbour when the pair still fits. Each piece gets the clip length to ask for."""
    allowed = sorted({int(d) for d in durations if int(d) > 0}) or list(DEFAULT_DURATIONS)
    max_len = float(allowed[-1])
    pieces: list[list[float]] = []
    for start, end in va.shots_from_cuts(cuts, duration):
        n = max(1, math.ceil((end - start) / (max_len * (1 + FIT_SLACK)) - 1e-6))  # a hair over: retimed, not split
        step = (end - start) / n
        pieces += [[round(start + i * step, 3), round(start + (i + 1) * step, 3)] for i in range(n)]
    changed = True
    while changed and len(pieces) > 1:
        changed = False
        for i, (s, e) in enumerate(pieces):
            if e - s >= merge_below:
                continue
            options = [j for j in (i - 1, i + 1) if 0 <= j < len(pieces)
                       and max(pieces[j][1], e) - min(pieces[j][0], s) <= max_len]
            if not options:
                continue
            j = min(options, key=lambda k: pieces[k][1] - pieces[k][0])
            a, b = sorted((i, j))
            pieces[a:b + 1] = [[pieces[a][0], pieces[b][1]]]
            changed = True
            break
    out = []
    for k, (s, e) in enumerate(pieces, 1):
        length = e - s
        ask = next((d for d in allowed if d >= length - 0.15), allowed[-1])
        out.append({"id": f"seg{k:02d}", "start": s, "end": e, "length": round(length, 3), "duration": ask,
                    "status": "todo", "render": None, "note": ""})
    return out


def _ffmpeg() -> str:
    return va._need("ffmpeg")


def cut_piece(src: Path, start: float, end: float, dest: Path) -> Path:
    """Frame-accurate piece of the source (re-encoded, with its sound)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}",
                    "-c:v", "libx264", "-crf", "16", "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "192k", str(dest)], check=True, capture_output=True, timeout=300)
    return dest


def frame_at(src: Path, t: float, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1",
                    "-q:v", "2", str(dest)], check=True, capture_output=True, timeout=120)
    return dest


def last_frame(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-sseof", "-0.1", "-i", str(src), "-update", "1", "-q:v", "2",
                    str(dest)], check=True, capture_output=True, timeout=120)
    return dest


def contact_sheet(src: Path, duration: float, dest: Path, *, cols: int = 6, rows: int = 4) -> Path:
    fps = (cols * rows) / max(duration, 0.1)
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-i", str(src), "-vf",
                    f"fps={fps:.4f},scale=240:-2,tile={cols}x{rows}", "-frames:v", "1", "-q:v", "3", str(dest)],
                   check=True, capture_output=True, timeout=300)
    return dest


# --- state ----------------------------------------------------------------------


def load(folder: Path) -> dict[str, Any]:
    p = Path(folder) / STATE
    if not p.is_file():
        raise CloneError(f"không thấy {p} — chạy `filmmaker clone init` trước")
    return json.loads(p.read_text(encoding="utf-8"))


def save(folder: Path, data: dict[str, Any]) -> None:
    p = Path(folder) / STATE
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


# --- people ---------------------------------------------------------------------


class SourcePerson(BaseModel):
    replaces: str = Field(description="How to point at this person in the source video, in English, so a video "
                                      "editor can find them: role, age, hair, clothes, where they are and what they "
                                      "do (e.g. 'the young woman with a long black braid in the white puffy dress').")
    role_vi: str = Field(description="Vai trò bằng tiếng Việt (mẹ, bố, em bé...).")
    suggested_look: str = Field(description="A NEW, clearly different look for the replacement (English, one "
                                            "sentence): same gender, age and role, different face, hair and clothes.")


class SourcePeople(BaseModel):
    people: list[SourcePerson]


PEOPLE_SYSTEM = ("You look at frames of a short video and list every person who appears on screen, the way a "
                 "casting director would before a recast. Be precise about what makes each one recognisable in "
                 "the frames. Never name real people.")


def describe_people(sheet: Path, *, backend: str = "auto", ai_model: str | None = None) -> list[dict[str, str]]:
    from . import ai as ai_mod

    res = ai_mod.ask(PEOPLE_SYSTEM, "Frames of the video in time order (left to right, top to bottom). List the "
                     "people.", SourcePeople, images=[("frames.jpg", sheet)], backend=backend, ai_model=ai_model,
                     timeout=600)
    return [p.model_dump() for p in res.people]


def _cast_name(i: int, role: str) -> str:
    base = re.sub(r"[^\wÀ-ỹ ]+", "", role or "").strip().title() or f"Nhân vật {i}"
    return base


def init(source: str, folder: Path, *, durations: tuple[int, ...] | list[int] = DEFAULT_DURATIONS,
         model: str = DEFAULT_MODEL, describe: bool = True, backend: str = "auto", ai_model: str | None = None,
         mode: str = "edit", log: Log = print) -> dict[str, Any]:
    folder = Path(folder).expanduser()
    if str(model).strip().lower() == MUSE_MODEL and mode != "regen":
        raise CloneError("Muse chỉ dùng được chế độ “Tạo lại bằng AI” (regen): Muse không sửa được video tải lên")
    (folder / "source").mkdir(parents=True, exist_ok=True)
    log("Tải video gốc…")
    info = va.fetch(source, folder / "source", log=log)
    src = folder / "source" / info["file"]
    m = va.probe(src)
    if not m.get("has_audio"):
        log("  (video gốc không có tiếng — bản clone cũng sẽ không có tiếng)")
    log(f"  {m['width']}x{m['height']} {m['fps']} fps, {m['duration']:.2f}s")
    cuts = va.detect_cuts(src, m["duration"])
    segs = plan_segments(m["duration"], cuts, durations)
    log(f"  {len(cuts)} điểm cắt cảnh -> {len(segs)} đoạn: "
        + ", ".join(f"{s['id']} {s['start']:.1f}-{s['end']:.1f}s" for s in segs))
    for s in segs:
        cut_piece(src, s["start"], s["end"], folder / "segments" / f"{s['id']}.mp4")
        frame_at(src, s["start"] + 0.05, folder / "segments" / f"{s['id']}.jpg")
    sheet = contact_sheet(src, m["duration"], folder / "source" / "frames.jpg")
    cast: list[dict[str, Any]] = []
    if describe:
        try:
            log("AI đang xem video để liệt kê các nhân vật…")
            for i, p in enumerate(describe_people(sheet, backend=backend, ai_model=ai_model), 1):
                cast.append({"name": _cast_name(i, p.get("role_vi", "")), "role_vi": p.get("role_vi", ""),
                             "replaces": p["replaces"], "new_look": p.get("suggested_look", ""), "ref": ""})
        except Exception as exc:  # the cast can always be written by hand
            log(f"  (không tự liệt kê được nhân vật: {str(exc)[:200]} — điền mục cast trong {STATE} bằng tay)")
    data = {"source": info.get("url") or source, "title": info.get("title") or "", "file": f"source/{info['file']}",
            "duration": m["duration"], "width": m["width"], "height": m["height"], "fps": m["fps"],
            "aspect": "9:16" if m["height"] > m["width"] else "16:9", "has_audio": m.get("has_audio", False),
            "cuts": cuts, "mode": mode, "model": model, "durations": list(durations), "flow_project": None,
            "audio": "original", "extra": "", "cast": cast, "segments": segs}
    if is_muse(data):
        data["image_model"] = MUSE_MODEL
    save(folder, data)
    log(f"Đã tạo {folder / STATE}. Kiểm tra mục cast (replaces / new_look / ref) rồi chạy "
        f"`filmmaker clone cast` và `filmmaker clone run`.")
    return data


# --- Flow --------------------------------------------------------------------------


def ensure_project(folder: Path, data: dict, gf) -> str:
    if data.get("flow_project"):
        return data["flow_project"]
    if is_muse(data):
        return ""  # everything is made on Muse: no Flow project
    title = "Clone - " + (data.get("title") or Path(folder).name)[:60]
    data["flow_project"] = gf.create_project(title)
    save(folder, data)
    return data["flow_project"]


def make_cast(folder: Path, gf, *, log: Log = print) -> list[dict]:
    """A Flow character (free) for every cast member that has a `new_look` but no `ref` yet."""
    folder = Path(folder)
    data = load(folder)
    todo = [c for c in data.get("cast") or [] if not (c.get("ref") or "").strip()]
    if not todo:
        log("Mọi nhân vật đã có ảnh / nhân vật Flow (ref).")
        return data.get("cast") or []
    project = ensure_project(folder, data, gf)
    muse = is_muse(data)
    for c in todo:
        look = (c.get("new_look") or "").strip()
        if not look:
            raise CloneError(f"nhân vật {c.get('name')!r} chưa có `new_look` (mô tả) hoặc `ref` (ảnh)")
        prompt = ("Photorealistic full-body character sheet on a plain light grey background, front view, standing: "
                  + look)
        out = folder / "cast" / f"{c['name']}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        if muse:  # a picture from Muse; the picture itself is the reference attached to every Muse request
            log(f"Tạo ảnh nhân vật “{c['name']}” bằng Muse…")
            pic = Path(gf.text_to_image(prompt, out=out, aspect=data.get("aspect") or "9:16", model=MUSE_MODEL,
                                        project=None))
            rel = str(pic.relative_to(folder)) if pic.is_relative_to(folder) else str(pic)
            c["ref"] = rel
            c["picture"] = rel
            save(folder, data)
            continue
        log(f"Tạo nhân vật Flow “{c['name']}”…")
        res = gf.ui_create_character(c["name"], prompt, out=out, project=project, info=c.get("role_vi") or None)
        c["ref"] = f"character:{res.get('name') or c['name']}"
        c["picture"] = str(Path(res.get("local_path") or out).relative_to(folder)) if Path(
            res.get("local_path") or out).is_relative_to(folder) else str(res.get("local_path") or out)
        save(folder, data)
    return data["cast"]


def segment_prompt(data: dict, seg: dict) -> str:
    swaps = []
    for c in data.get("cast") or []:
        if not (c.get("replaces") or "").strip():
            continue
        look = (c.get("new_look") or "").strip()
        swaps.append(f"Replace {c['replaces'].strip().rstrip('.')} with {c['name']}"
                     + (f" ({look.rstrip('.')})" if look else "") + ".")
    text = " ".join(swaps) or "Replace every person with the attached characters."
    text += (" If one of these people is not in this part of the video, simply ignore them. Mouths open and close "
             "exactly as in the video; expressions (smiles, pouts, laughs, surprise) match exactly.")
    for extra in (data.get("extra"), seg.get("extra")):
        if (extra or "").strip():
            text += " " + extra.strip()
    return text


def segment_refs(folder: Path, data: dict) -> list[str]:
    refs = []
    for c in data.get("cast") or []:
        r = (c.get("ref") or "").strip()
        if not r:
            continue
        if r.startswith("character:") and is_muse(data) and (c.get("picture") or "").strip():
            r = c["picture"].strip()  # Muse takes pictures only: the Flow character's picture stands in for it
        if r.startswith("character:"):
            refs.append(r)
        else:
            p = Path(r).expanduser()
            refs.append(str(p if p.is_absolute() else Path(folder) / p))
    return refs


# --- regen: re-create each segment with AI from a timed description (no upload of the source) -----------------


class Beat(BaseModel):
    start: float = Field(description="Seconds from the start of THIS segment.")
    end: float
    action: str = Field(description="What every visible person does in this moment, by their NEW name: body "
                                    "movement, hands, head turn, gaze direction, facial expression, mouth (closed, "
                                    "smiling, laughing, pouting...). Concrete and physical, one or two sentences.")


class SegmentPlan(BaseModel):
    setting: str = Field(description="The place exactly as seen: room, furniture, props and food and where they "
                                     "are, window and light, colours. No people.")
    camera: str = Field(description="Framing, height, angle and any camera movement (handheld drift, pan, push).")
    first_frame: str = Field(description="A still-image prompt for the FIRST frame: the setting and camera, then "
                                         "where each person is, their pose, gaze and expression, by NEW name.")
    beats: list[Beat] = Field(description="Back-to-back moments covering the whole segment, about one per second "
                                          "or per change, never longer than 2 s each.")
    sounds: str = Field(default="", description="Sound moments heard or implied (laugh, kiss, plate on table) "
                                                 "and when, if any.")


DESCRIBE_SYSTEM = ("You are a meticulous film director's assistant. You watch a short clip frame by frame and write "
                   "down exactly what happens so a video model can re-stage it identically with a NEW cast: every "
                   "movement, gesture, look and facial expression, with its timing, in the same place and framing. "
                   "Refer to the people only by the new cast names you are given and NEVER describe the faces, hair "
                   "or clothes of the people in the frames (the new cast looks different). Keep it wholesome and "
                   "factual.")


def timed_sheets(src: Path, start: float, end: float, folder: Path, stem: str, *, step: float = 0.5,
                 cols: int = 3) -> list[Path]:
    """Frames every `step` seconds of [start, end) with their time (from the segment start) printed on them, a few
    per picture: the AI sees pictures at most 768 px, so one big sheet makes every frame too small to read faces."""
    font = va._font()
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{stem}.frames-*.jpg"):
        old.unlink()
    span = step * cols
    out, t, k = [], start, 1
    while t < end - step * 0.6:  # a tail shorter than a step has no frame of its own
        off = t - start  # the printed time counts from the segment start
        label = (f",drawtext=fontfile='{font}':text='%{{eif\\:t+{off:.3f}\\:d}}.%{{eif\\:mod((t+{off:.3f})*10+0.5\\,10)\\:d}}s'"
                 ":x=10:y=10:fontsize=34:fontcolor=white:box=1:boxcolor=black@0.6") if font else ""
        dest = folder / f"{stem}.frames-{k:02d}.jpg"
        subprocess.run([_ffmpeg(), "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(src), "-t",
                        f"{min(span, end - t):.3f}", "-vf", f"fps=1/{step}:round=down,scale=360:-2{label},tile={cols}x1",
                        "-frames:v", "1", "-q:v", "3", str(dest)], check=True, capture_output=True, timeout=300)
        if dest.is_file():
            out.append(dest)
        t, k = t + span, k + 1
    return out


def describe_segment(folder: Path, data: dict, seg: dict, *, backend: str = "auto",
                     ai_model: str | None = None) -> dict:
    sheets = timed_sheets(Path(folder) / data["file"], seg["start"], seg["end"], Path(folder) / "segments", seg["id"])
    cast = "\n".join(f"- {c['name']} ({c.get('role_vi', '')}) plays {c['replaces']}" for c in data.get("cast") or [])
    user = (f"The {len(sheets)} pictures show a {seg['end'] - seg['start']:.1f} s segment of a {data['aspect']} "
            "video, a frame every 0.5 s, in order (left to right in each picture, picture after picture); the time "
            f"from the segment start is printed on each frame. New cast:\n{cast}\n"
            "Look at EVERY frame. Track each person separately: when they enter or leave the frame, where they "
            "stand or sit, what they hold and carry, every touch between people (hugs, kisses on the cheek or head, "
            "a hand on a shoulder), head turns and gaze, and each facial expression change (neutral, pout, frown, "
            "smile, laugh with open mouth). Note who is only partly in frame. Describe the segment.")
    from . import ai as ai_mod

    plan = ai_mod.ask(DESCRIBE_SYSTEM, user, SegmentPlan, images=[(p.name, p) for p in sheets], backend=backend,
                      ai_model=ai_model, timeout=900)
    return plan.model_dump()


def beat_stretch(seg: dict, *, min_factor: float = 1.1) -> float:
    """How much longer the asked clip is than the source piece, when that is enough to matter (else 1)."""
    try:
        f = float(seg.get("duration") or 0) / float(seg.get("length") or (seg["end"] - seg["start"]))
    except (TypeError, ValueError, ZeroDivisionError, KeyError):
        return 1.0
    return f if f >= min_factor else 1.0


def regen_prompts(data: dict, seg: dict) -> tuple[str, str]:
    """(still prompt for the first frame, motion prompt for the clip) from the segment's description."""
    plan = seg.get("plan") or {}
    looks = " ".join(f"{c['name']}: {c['new_look'].rstrip('.')}." for c in data.get("cast") or [] if c.get("new_look"))
    style = ("Photorealistic, candid smartphone video look, soft natural daylight, " + data["aspect"] + " vertical "
             "frame." if data["aspect"] == "9:16" else "Photorealistic, candid video look, soft natural daylight.")
    still = f"{plan.get('first_frame', '')} Setting: {plan.get('setting', '')} Camera: {plan.get('camera', '')} " \
            f"{looks} {style}"
    # A model with one fixed clip length longer than the piece (Muse: always 10 s for a ~7.6 s piece) is sped up
    # at stitch time to fit the original sound; stretch the beat times by the same factor so the action still
    # lands on the sound after that. Small differences (Veo 8 s for 7.6 s) are left alone, as before.
    stretch = beat_stretch(seg)
    beats = " ".join(f"[{b['start'] * stretch:.1f}-{b['end'] * stretch:.1f}s] {b['action']}"
                     for b in plan.get("beats") or [])
    cast = f" Characters (keep these exact looks, also for anyone who walks in later): {looks}" if looks else ""
    motion = (f"One continuous shot, no cuts. Camera: {plan.get('camera', '').rstrip('.')}.{cast} "
              f"Follow this timing exactly: {beats}"
              + (f" Sounds: {plan['sounds']}" if plan.get("sounds") else "")
              + " No speech, no subtitles, no text on screen.")
    for extra in (data.get("extra"), seg.get("extra")):
        if (extra or "").strip():
            motion += " " + extra.strip()
    return " ".join(still.split()), " ".join(motion.split())


def _previous(data: dict, seg: dict) -> dict | None:
    segs = data.get("segments") or []
    i = next((n for n, x in enumerate(segs) if x["id"] == seg["id"]), 0)
    return segs[i - 1] if i > 0 and abs(segs[i - 1]["end"] - seg["start"]) < 0.05 else None


def _regen_segment(folder: Path, data: dict, seg: dict, gf, refs: list[str], project: str, out: Path, *,
                   log: Log = print) -> Path:
    if not seg.get("plan"):
        log(f"{seg['id']}: AI đang xem từng nửa giây để mô tả hành động…")
        seg["plan"] = describe_segment(folder, data, seg)
        save(folder, data)
    still, motion = regen_prompts(data, seg)
    kf = folder / "keyframes" / f"{seg['id']}.png"
    prev = _previous(data, seg)
    if not kf.is_file() and prev and prev.get("status") == "done" and (folder / (prev.get("render") or "")).is_file() \
            and not gf.dry_run:
        # continue the take: the next clip starts exactly where the previous render ended
        log(f"{seg['id']}: lấy khung cuối của {prev['id']} làm ảnh mở cảnh để nối liền…")
        last_frame(folder / prev["render"], kf)
    if not kf.is_file() and prev and prev.get("status") != "done" and not gf.dry_run:
        raise CloneError(f"chờ {prev['id']} xong trước: đoạn này nối tiếp từ khung cuối của nó")
    if not kf.is_file():
        log(f"{seg['id']}: tạo ảnh mở cảnh với dàn nhân vật mới…")
        kf.parent.mkdir(parents=True, exist_ok=True)
        if is_muse(data):
            names = [c["name"] for c in data.get("cast") or [] if (c.get("ref") or "").strip()]
            still = ("Reference images, in the order attached: " + ", ".join(names)
                     + " (keep each one's face, hair and clothes exactly). " + still)
        if not gf.dry_run:
            kf = gf.ui_image_to_image(still, refs, out=kf, aspect=data["aspect"],
                                      model=data.get("image_model") or "nano2", project=project)
    log(f"{seg['id']} ({seg['start']:.1f}-{seg['end']:.1f}s) -> tạo video từ ảnh mở cảnh…")
    path, _ = gf.ui_image_to_video(kf, motion, refs=None, aspect=data["aspect"], model=data.get("model"),
                                   duration=seg.get("duration"), project=project, out=out)
    return path


def render(folder: Path, gf, *, only: set[str] | None = None, force: bool = False, agree_rights: bool = False,
           log: Log = print) -> dict:
    folder = Path(folder)
    data = load(folder)
    refs = segment_refs(folder, data)
    missing = [c["name"] for c in data.get("cast") or [] if not (c.get("ref") or "").strip()]
    if missing:
        raise CloneError("chưa có ảnh/nhân vật Flow cho: " + ", ".join(missing) + " — chạy `filmmaker clone cast`")
    if not refs:
        raise CloneError("mục cast trống: cần ít nhất một nhân vật mới")
    if is_muse(data) and data.get("mode") != "regen":
        raise CloneError("Muse chỉ dùng được chế độ “Tạo lại bằng AI” (regen)")
    project = ensure_project(folder, data, gf)
    for seg in data["segments"]:
        if only and seg["id"] not in only:
            continue
        if seg.get("keep"):
            log(f"{seg['id']}: giữ nguyên cảnh gốc (keep)")
            continue
        out = folder / "renders" / f"{seg['id']}.mp4"
        if out.is_file() and seg.get("status") == "done" and not force:
            log(f"{seg['id']}: đã có, bỏ qua")
            continue
        if out.is_file() and seg.get("status") != "done" and not force:
            seg.update(status="done", render=str(out.relative_to(folder)))  # a clip saved by hand from Flow
            save(folder, data)
            log(f"{seg['id']}: nhận clip có sẵn {out.name}")
            continue
        try:
            if data.get("mode") == "regen":
                path = _regen_segment(folder, data, seg, gf, refs, project, out, log=log)
            else:
                log(f"{seg['id']} ({seg['start']:.1f}-{seg['end']:.1f}s) -> Omni thay nhân vật…")
                path, _ = gf.ui_video_to_video(folder / "segments" / f"{seg['id']}.mp4", segment_prompt(data, seg),
                                               refs=refs, aspect=data["aspect"], model=data.get("model"),
                                               duration=seg.get("duration"), project=project, out=out,
                                               agree_rights=agree_rights)
        except Exception as exc:
            seg.update(status="failed", note=str(exc)[:500])
            save(folder, data)
            log(f"  lỗi: {str(exc)[:300]}")
            continue
        if gf.dry_run:
            continue
        seg.update(status="done", render=str(Path(path).relative_to(folder)) if Path(path).is_relative_to(folder)
                   else str(path), note="")
        save(folder, data)
    return data


# --- stitch -------------------------------------------------------------------------


def _archive(path: Path) -> Path | None:
    """Move a file aside as name.vN.ext (never overwrite an older take)."""
    if not path.is_file():
        return None
    n = 1
    while (dest := path.with_name(f"{path.stem}.v{n}{path.suffix}")).exists():
        n += 1
    path.replace(dest)
    return dest


def redo(folder: Path, seg_id: str, *, keyframe: bool = False) -> dict:
    """Mark one segment to be rendered again (its old take kept as .vN). In regen mode the segments that continue
    from it lose their start frame too, so the next render re-joins them to the new take."""
    folder = Path(folder)
    data = load(folder)
    ids = [s["id"] for s in data["segments"]]
    if seg_id not in ids:
        raise CloneError(f"không có đoạn {seg_id}")
    i = ids.index(seg_id)
    seg = data["segments"][i]
    _archive(folder / "renders" / f"{seg_id}.mp4")
    if keyframe:
        _archive(folder / "keyframes" / f"{seg_id}.png")
    seg.update(status="todo", render=None, note="")
    if data.get("mode") == "regen":
        for nxt in data["segments"][i + 1:]:
            if _previous(data, nxt) is None:
                break
            if _archive(folder / "keyframes" / f"{nxt['id']}.png") or nxt.get("status") == "done":
                _archive(folder / "renders" / f"{nxt['id']}.mp4")
                nxt.update(status="todo", render=None, note=f"cần render lại để nối liền với {seg_id} mới")
    save(folder, data)
    return data


def _duration(path: Path) -> float:
    return float(va.probe(path)["duration"])


def fit_filter(render_len: float, piece_len: float, *, retime_max: float = RETIME_MAX) -> tuple[str, float]:
    """The setpts filter that makes a render last `piece_len`, and the speed factor used (1 = untouched).
    A render a little off is retimed (keeps every motion in step with the original sound); a much longer one
    is trimmed; a much shorter one is slowed down as far as allowed and the last frame is held."""
    if render_len <= 0 or piece_len <= 0:
        return "setpts=PTS-STARTPTS", 1.0
    ratio = piece_len / render_len
    if abs(ratio - 1) <= 0.01:
        return "setpts=PTS-STARTPTS", 1.0
    if ratio < 1 - retime_max:  # much longer: trim the end (edits keep the source timing from the start)
        return "setpts=PTS-STARTPTS", 1.0
    ratio = min(ratio, 1 + retime_max)
    return f"setpts={ratio:.5f}*(PTS-STARTPTS)", ratio


def stitch(folder: Path, output: Path | None = None, *, allow_missing: bool = False, audio: str | None = None,
           log: Log = print) -> Path:
    folder = Path(folder)
    data = load(folder)
    src = folder / data["file"]
    w, h, fps = data["width"], data["height"], data.get("fps") or 24
    w, h = w - w % 2, h - h % 2
    audio = audio or data.get("audio") or "original"
    parts: list[Path] = []
    work = folder / "renders" / "fit"
    work.mkdir(parents=True, exist_ok=True)
    for seg in data["segments"]:
        piece = folder / "segments" / f"{seg['id']}.mp4"
        r = folder / seg["render"] if seg.get("render") else None
        use = r if (r and r.is_file() and not seg.get("keep")) else None
        if use is None:
            if not (allow_missing or seg.get("keep")):
                raise CloneError(f"{seg['id']} chưa render xong (dùng --allow-missing để tạm dùng cảnh gốc)")
            use = piece
            log(f"  {seg['id']}: dùng cảnh gốc")
        length = float(seg["end"]) - float(seg["start"])
        vf, ratio = fit_filter(_duration(use), length)
        if ratio != 1.0:
            log(f"  {seg['id']}: chỉnh tốc độ x{1 / ratio:.3f} cho khớp {length:.2f}s")
        dest = work / f"{seg['id']}.mp4"
        subprocess.run([_ffmpeg(), "-v", "error", "-y", "-i", str(use), "-an", "-vf",
                        f"{vf},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},"
                        f"tpad=stop_mode=clone:stop_duration=2", "-t", f"{length:.3f}", "-c:v", "libx264",
                        "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p", str(dest)],
                       check=True, capture_output=True, timeout=600)
        parts.append(dest)
    listing = work / "list.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    video_only = work / "video.mp4"
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
                    str(video_only)], check=True, capture_output=True, timeout=600)
    output = Path(output) if output else folder / "final.mp4"
    if audio == "original" and data.get("has_audio"):
        cmd = [_ffmpeg(), "-v", "error", "-y", "-i", str(video_only), "-i", str(src), "-map", "0:v", "-map", "1:a",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{data['duration']:.3f}", str(output)]
    else:  # "none", or a silent source
        cmd = [_ffmpeg(), "-v", "error", "-y", "-i", str(video_only), "-c", "copy", str(output)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    log(f"Đã ghép: {output} ({_duration(output):.2f}s, gốc {data['duration']:.2f}s)")
    return output


def compare(folder: Path, final: Path | None = None) -> Path:
    """Side-by-side original | clone (same sound) to check that every gesture lines up."""
    folder = Path(folder)
    data = load(folder)
    final = Path(final) if final else folder / "final.mp4"
    out = folder / "compare.mp4"
    h = data["height"] - data["height"] % 2
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-i", str(folder / data["file"]), "-i", str(final),
                    "-filter_complex", f"[0:v]scale=-2:{h}[a];[1:v]scale=-2:{h}[b];[a][b]hstack=2[v]",
                    "-map", "[v]", "-map", "1:a?", "-c:v", "libx264", "-crf", "20", "-preset", "fast",
                    "-shortest", str(out)], check=True, capture_output=True, timeout=600)
    return out


__all__ = ["init", "make_cast", "render", "stitch", "compare", "plan_segments", "fit_filter", "segment_prompt",
           "segment_refs", "load", "save", "CloneError", "regen_prompts", "describe_segment", "MODES",
           "REGEN_MODEL", "REGEN_DURATIONS"]
