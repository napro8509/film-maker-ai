"""Script library ("Kho kịch bản"): every approved `script.md` in the workspace, ready to become a new film.

A film folder made by the screenwriter / script-reviewer agents holds `brief.md`, `script.md` (and
`script.final.md` once it passed) and `reviews/script-review.md`. Folders starting with "_" or "." are archives
(backups, drafts) and are not listed. Nothing here calls an AI or writes a file."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: Sent before the script text: the planner turns it into a storyboard without rewriting it.
SCRIPT_IDEA_HEAD = ("Chuyển ĐÚNG kịch bản đã duyệt dưới đây thành storyboard: giữ nguyên số cảnh, thứ tự cảnh, "
                    "nhân vật, trang phục, bối cảnh và từng chữ của lời thoại (không viết lại, không thêm thoại).")

SCRIPT_FILES = ("script.final.md", "script.md")
MAX_SCRIPT_CHARS = 60_000

#: "Ngôn ngữ thoại: Tiếng Thái" in brief.md -> the English name the planner and the video prompts use.
_LANGS = {"thái": "Thai", "thai": "Thai", "việt": "Vietnamese", "vietnamese": "Vietnamese", "anh": "English",
          "english": "English", "trung": "Chinese", "hàn": "Korean", "nhật": "Japanese", "lào": "Lao",
          "khmer": "Khmer", "campuchia": "Khmer", "indonesia": "Indonesian", "philippines": "Filipino"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}")


def script_file(folder: Path) -> Path | None:
    """The script of a film folder: the final (passed) version when there is one."""
    for name in SCRIPT_FILES:
        f = folder / name
        if f.is_file() and f.stat().st_size > 0:
            return f
    return None


def _read(path: Path, limit: int = MAX_SCRIPT_CHARS) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _title(text: str, fallback: str) -> tuple[str, str]:
    """(title, subtitle): the first "# " heading and an "*(…)*" line right under it (the translated title)."""
    lines = [l.strip() for l in text.splitlines()]
    for i, line in enumerate(lines):
        if line.startswith("# "):
            sub = next((l for l in lines[i + 1:i + 4] if l), "")
            m = re.fullmatch(r"\*?\((.+)\)\*?", sub)
            return line[2:].strip(), (m.group(1).strip() if m else "")
    return fallback, ""


def _section(text: str, name: str) -> str:
    """First paragraph of a "## <name>" section, or a "**<name>:** …" line (e.g. the logline)."""
    m = (re.search(rf"(?im)^##\s+{re.escape(name)}\s*$\n+(.+?)(?:\n\s*\n|\n#|\Z)", text, re.S)
         or re.search(rf"(?im)^\**{re.escape(name)}\s*:?\**\s*:?\s*(.+)$", text))
    return " ".join(m.group(1).replace("**", "").split()).lstrip(": ") if m else ""


def _review(folder: Path) -> tuple[float | None, str | None]:
    text = _read(folder / "reviews" / "script-review.md", 200_000)
    score = re.findall(r"TỔNG ĐIỂM\**\s*:?\s*\**\s*([0-9]+(?:[.,][0-9]+)?)", text)
    verdict = re.findall(r"VERDICT\s*:\s*([A-Z]+)", text)
    return (float(score[-1].replace(",", ".")) if score else None), (verdict[-1] if verdict else None)


def _brief_field(folder: Path, label: str) -> str:
    m = re.search(rf"(?im)^\s*-\s*{re.escape(label)}\s*:\s*(.+)$", _read(folder / "brief.md", 20_000))
    return m.group(1).strip() if m else ""


def language_name(text: str) -> str | None:
    """"Tiếng Thái (nhân vật người Thái)" -> "Thai"; None when unknown."""
    t = re.sub(r"\(.*?\)", "", text or "").casefold().replace("tiếng", " ")
    for word, name in _LANGS.items():
        if re.search(rf"\b{re.escape(word)}\b", t):
            return name
    return None


def _aspect(text: str) -> str | None:
    m = re.search(r"\b(9:16|16:9)\b", text or "")
    return m.group(1) if m else None


def entry(folder: Path) -> dict[str, Any] | None:
    """One library row, or None when the folder has no script."""
    f = script_file(folder)
    if f is None:
        return None
    text = _read(f)
    title, subtitle = _title(text, folder.name)
    score, verdict = _review(folder)
    scenes = len(set(re.findall(r"(?im)^#{2,4}\s*(s\d{2,3})\b", text)))
    return {
        "id": folder.name, "title": title, "subtitle": subtitle, "file": f.name,
        "logline": _section(text, "Logline")[:400],
        "score": score, "verdict": verdict, "scenes": scenes or None,
        "dialogue_language": language_name(_brief_field(folder, "Ngôn ngữ thoại")),
        "subtitle_language": language_name(_brief_field(folder, "Ngôn ngữ phụ đề")),
        "aspect": _aspect(_brief_field(folder, "Khung hình")),
        "has_film": (folder / "storyboard.yaml").is_file(),
        "updated": f.stat().st_mtime,
    }


def list_scripts(workspace: Path) -> list[dict[str, Any]]:
    """Every script in the workspace, newest first. Passed scripts before the others."""
    out = []
    try:
        folders = [d for d in workspace.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))]
    except OSError:
        return []
    for d in folders:
        e = entry(d)
        if e is not None:
            out.append(e)
    return sorted(out, key=lambda e: (e["verdict"] != "PASS", -e["updated"]))


def folder_of(workspace: Path, sid: str) -> Path:
    """The film folder of library id `sid`; ValueError for anything that is not a plain folder name with a script."""
    if not isinstance(sid, str) or not _ID.fullmatch(sid) or sid.startswith((".", "_")):
        raise ValueError("Mã kịch bản không hợp lệ.")
    d = (workspace / sid)
    if d.resolve().parent != workspace.resolve() or not d.is_dir() or script_file(d) is None:
        raise ValueError("Không thấy kịch bản này trong kho.")
    return d


def read_script(workspace: Path, sid: str) -> str:
    f = script_file(folder_of(workspace, sid))
    assert f is not None
    return _read(f).strip()


def idea_from_script(text: str, note: str | None = None) -> str:
    """The planner's input for an approved script: the instruction to keep it, an optional note, the script."""
    extra = f"\nGhi chú thêm: {note.strip()}" if note and note.strip() else ""
    return f"{SCRIPT_IDEA_HEAD}{extra}\n\n{text.strip()}"
