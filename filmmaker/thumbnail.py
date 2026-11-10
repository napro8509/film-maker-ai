"""Video thumbnail: a cover image made on Flow (free) + the title printed with ffmpeg.

The picture itself is generated without any text (AI image models garble Thai/Vietnamese
lettering); the title is drawn afterwards so it is always spelled right and can be changed
without making a new image.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

THAI = re.compile(r"[\u0E00-\u0E7F]")
COMPLEX = re.compile(r"[\u0E00-\u0E7F\u0600-\u06FF\u0900-\u0DFF\u1000-\u109F]")  # needs text shaping

FONTS = {
    "thai": [
        "/System/Library/Fonts/Supplemental/Thonburi.ttc", "/System/Library/Fonts/Thonburi.ttc",
        "/System/Library/Fonts/SukhumvitSet.ttc", "/System/Library/Fonts/Supplemental/SukhumvitSet.ttc",
        "/System/Library/Fonts/Supplemental/Ayuthaya.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansThai-Bold.ttf", "/usr/share/fonts/opentype/noto/NotoSansThai-Bold.ttf",
        "/usr/share/fonts/truetype/tlwg/Garuda-Bold.ttf", "C:/Windows/Fonts/leelaub.ttf", "C:/Windows/Fonts/tahomabd.ttf",
    ],
    "latin": [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ],
}


class ThumbnailError(RuntimeError):
    pass


def find_font(text: str) -> str | None:
    if os.environ.get("FILMMAKER_FONT") and Path(os.environ["FILMMAKER_FONT"]).is_file():
        return os.environ["FILMMAKER_FONT"]
    for f in FONTS["thai" if THAI.search(text) else "latin"] + FONTS["latin"]:
        if Path(f).is_file():
            return f
    return None


def _drawtext_caps(ffmpeg: str) -> tuple[bool, bool, bool]:
    """(drawtext available, can shape complex scripts such as Thai, has the `text_shaping` option).

    ffmpeg 6.1-8 had a `text_shaping` switch; newer builds dropped it and always shape when built
    with libharfbuzz, so look at the build configuration too."""
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-h", "filter=drawtext"], capture_output=True, text=True,
                             timeout=20).stdout
        conf = subprocess.run([ffmpeg, "-hide_banner", "-version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False, False, False
    has_opt = "text_shaping" in out
    return "fontfile" in out, has_opt or "--enable-libharfbuzz" in conf, has_opt


THAI_LEADING = "เแโใไ"  # vowels written before their consonant: never end a line on one


def _safe_cut(w: str, i: int) -> int:
    """Move a hard-split point back so it never splits a combining mark or a Thai leading vowel from its letter."""
    j = i
    while 0 < j < len(w) and (unicodedata.category(w[j]).startswith("M") or w[j - 1] in THAI_LEADING):
        j -= 1
    return j or i


def wrap(text: str, max_chars: int, max_lines: int = 3) -> list[str]:
    """Split a title into a few balanced lines (by words; by characters for unspaced scripts)."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return [text] if text else []
    words = text.split(" ")
    lines: list[str] = []
    cur = ""
    for w in words:
        while len(w) > max_chars:  # long unspaced run (e.g. Thai): hard-split
            room = max_chars - (len(cur) + 1 if cur else 0)
            if room < max_chars // 2 and cur:
                lines.append(cur)
                cur = ""
                continue
            room = _safe_cut(w, room)
            lines.append((cur + " " if cur else "") + w[:room])
            cur, w = "", w[room:]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= max_chars:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [" ".join(lines[max_lines - 1:])]
    if 1 < len(lines) <= 3 and all(len(w) <= max_chars for w in words):
        lines = _balanced(words, len(lines), max_chars) or lines
    return lines


def _balanced(words: list[str], n: int, max_chars: int) -> list[str] | None:
    """The same words in `n` lines with the shortest longest line ("Cơn Mưa / Sài Gòn", not "Cơn Mưa Sài / Gòn")."""
    best: tuple[int, list[str]] | None = None
    k = len(words)
    cuts = [(i,) for i in range(1, k)] if n == 2 else [(i, j) for i in range(1, k) for j in range(i + 1, k)]
    for c in cuts:
        bounds = [0, *c, k]
        lines = [" ".join(words[bounds[x]:bounds[x + 1]]) for x in range(n)]
        longest = max(len(x) for x in lines)
        if longest <= max_chars and (best is None or longest < best[0]):
            best = (longest, lines)
    return best[1] if best else None


def _esc(path: str) -> str:
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def render_cover(image: Path, out: Path, *, aspect: str, text: str | None, log=print) -> Path:
    """Crop the image to a thumbnail frame (1280x720 or 720x1280) and print the title on it."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ThumbnailError("cần cài ffmpeg để làm thumbnail")
    w, h = (1280, 720) if aspect == "16:9" else (720, 1280)
    filters = [f"scale={w}:{h}:force_original_aspect_ratio=increase", f"crop={w}:{h}", "setsar=1"]
    text = (text or "").strip()
    tmp = Path(tempfile.mkdtemp(prefix="fm-thumb-"))
    try:
        if text:
            has_drawtext, shaping, shaping_opt = _drawtext_caps(ffmpeg)
            font = find_font(text)
            if not has_drawtext:
                log("  ! ffmpeg này không có bộ vẽ chữ (drawtext): thumbnail không có tiêu đề.")
            elif not font:
                log("  ! Không tìm thấy font phù hợp (đặt FILMMAKER_FONT=/đường/dẫn/font.ttf): thumbnail không có tiêu đề.")
            elif COMPLEX.search(text) and not shaping:
                log("  ! ffmpeg này không ghép được chữ Thái đúng cách (thiếu text shaping): thumbnail không có tiêu đề. "
                    "Cài ffmpeg mới hơn (brew upgrade ffmpeg).")
            else:
                lines = wrap(text, 18 if aspect == "16:9" else 12)
                longest = max(len(l) for l in lines)
                cap = 0.09 if aspect == "16:9" else 0.13
                size = int(min(w * cap, (w * 0.86) / max(1, longest * 0.66)))
                gap = int(size * 0.28)
                block = len(lines) * size + (len(lines) - 1) * gap
                # vertical videos: keep clear of the Reels/Shorts buttons and caption at the bottom
                top = int(h * (0.94 if aspect == "16:9" else 0.78)) - block
                # soft dark gradient behind the title so it reads on any picture
                start = max(0, top - size)
                steps = 8
                band = max(1, (h - start) // steps)
                for k in range(steps):
                    filters.append(f"drawbox=x=0:y={start + k * band}:w=iw:h={h - start - k * band}:color=black@0.09:t=fill")
                for i, line in enumerate(lines):
                    tf = tmp / f"line{i}.txt"
                    tf.write_text(line, encoding="utf-8")
                    color = "0xFFD400" if i == 0 else "white"
                    opts = [f"fontfile='{_esc(font)}'", f"textfile='{_esc(str(tf))}'", f"fontsize={size}",
                            f"fontcolor={color}", "borderw=5", "bordercolor=black", "shadowx=3", "shadowy=3",
                            "shadowcolor=black@0.6", "x=(w-text_w)/2", f"y={top + i * (size + gap)}"]
                    if shaping_opt:
                        opts.append("text_shaping=1")
                    filters.append("drawtext=" + ":".join(opts))
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(image), "-vf", ",".join(filters),
               "-frames:v", "1", "-q:v", "2", str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise ThumbnailError(f"ffmpeg lỗi khi làm thumbnail: {proc.stderr.strip()[-800:]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out
