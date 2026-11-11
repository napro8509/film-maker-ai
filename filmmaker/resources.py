"""Music and sound effects from the web for the editor agent.

The agent asks for a sound by a short phrase ("door slam", "sad piano cinematic"); this module finds it on
Openverse (openly licensed audio from Freesound, Jamendo, Wikimedia...), downloads it next to the film and
keeps the credit. Only works that allow commercial use and modification are used: CC0 / public domain first,
then CC BY (the author must be credited: see CREDITS.txt in the same folder).

Nothing about the film is sent out: only the search phrase.
Set FILMMAKER_OFFLINE=1 to turn every search off.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel

from .assemble import probe_duration

Log = Callable[[str], None]
Kind = Literal["music", "sfx"]

API = "https://api.openverse.org/v1/audio/"
AGENT = "filmmaker-editor/0.1"
LICENSES = ("cc0", "pdm", "by")  # commercial use + changes allowed; best first
TYPES = ("mp3", "ogg", "wav", "flac", "m4a", "oga")
MAX_BYTES = 30 * 1024 * 1024
LICENSE_NAMES = {"cc0": "CC0 (không cần ghi nguồn)", "pdm": "Public domain (không cần ghi nguồn)",
                 "by": "CC BY (phải ghi tên tác giả)"}


class Asset(BaseModel):
    query: str
    kind: Kind
    file: str  # file name inside the assets folder
    duration: float
    title: str = ""
    creator: str = ""
    license: str = ""
    page: str = ""  # where the work lives (for the credit)

    def credit(self) -> str:
        who = f" - {self.creator}" if self.creator else ""
        return f"“{self.title or self.file}”{who} · {LICENSE_NAMES.get(self.license, self.license)} · {self.page}"


def offline() -> bool:
    return os.environ.get("FILMMAKER_OFFLINE", "").strip() not in ("", "0")


def _get(url: str, timeout: float = 25) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (https URLs built here or from the API)
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("file quá lớn")
    return data


def search(query: str, kind: Kind, *, get: Callable[[str], bytes] = _get) -> list[dict[str, Any]]:
    """Openverse results for the phrase, usable ones only (licence, type, a direct file)."""
    params = {"q": query, "license": ",".join(LICENSES), "page_size": "20", "mature": "false"}
    if kind == "sfx":
        params["length"] = "shortest"  # under 30 s
    else:
        params["length"] = "medium,long"
    data = json.loads(get(API + "?" + urllib.parse.urlencode(params)))
    out = []
    for r in data.get("results") or []:
        url = str(r.get("url") or "")
        ftype = str(r.get("filetype") or url.rsplit(".", 1)[-1]).lower()
        if not url.startswith("https://") or ftype not in TYPES or r.get("license") not in LICENSES:
            continue
        if (r.get("filesize") or 0) > MAX_BYTES:
            continue
        tags = " ".join(str(t.get("name") or "") for t in (r.get("tags") or []) if isinstance(t, dict))
        out.append({"url": url, "type": ftype, "duration": (r.get("duration") or 0) / 1000.0,
                    "text": f"{r.get('title') or ''} {tags}".lower()[:2000],
                    "title": str(r.get("title") or "")[:120], "creator": str(r.get("creator") or "")[:80],
                    "license": r["license"], "page": str(r.get("foreign_landing_url") or "")[:300]})
    return out


def rank(hits: list[dict[str, Any]], kind: Kind, want: float | None = None, query: str = "") -> list[dict[str, Any]]:
    """Best first. A usable length comes first (sfx: short; music: enough for the film, `want` seconds), then
    how many of the phrase's words the title and tags really contain, then works that need no credit."""
    words = [w for w in query.lower().split() if len(w) > 2]

    def score(h: dict[str, Any]) -> tuple:
        d, lic = h["duration"], LICENSES.index(h["license"])
        title = h.get("title", "").lower()  # a word in the title counts twice: tags are often loose
        miss = sum((w not in h.get("text", "")) + (w not in title) for w in words)
        if kind == "sfx":
            fits = 0 if 0.3 <= d <= 12 else 1 if d <= 30 else 2
            return (fits, miss, lic, abs(d - 3))
        need = want or 60.0
        fits = 0 if need * 0.5 <= d <= 900 else 1 if d >= 30 else 2
        return (fits, miss, abs(d - need) > need, lic)
    return sorted((h for h in hits if h["duration"] > 0.2), key=score)


def phrases(query: str) -> list[str]:
    """The phrase, then shorter versions of it: a long phrase often finds nothing."""
    words = query.split()
    out = [query] + [" ".join(words[:n]) for n in (3, 2) if len(words) > n]
    out += [" ".join(words[i:i + 2]) for i in range(1, len(words) - 1)]  # then every neighbouring pair
    return list(dict.fromkeys(out))[:6]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "sound"


class Library:
    """The film's downloaded sounds (<out>/edit_assets): one file per search phrase, reused on the next edit."""

    def __init__(self, folder: Path, *, log: Log = print, get: Callable[[str], bytes] = _get,
                 pause: float = 1.2) -> None:
        self.folder, self.log, self.get, self.pause = folder, log, get, pause
        self._index = folder / "index.json"
        self.assets: dict[str, Asset] = {}
        try:
            raw = json.loads(self._index.read_text(encoding="utf-8"))
            self.assets = {k: Asset.model_validate(v) for k, v in raw.items()}
        except (OSError, ValueError):
            pass
        self._last = 0.0
        self._blocked = False  # the service refused us (rate limit / no network): stop asking for this edit

    def path(self, asset: Asset) -> Path:
        return self.folder / asset.file

    def find(self, query: str, kind: Kind, want: float | None = None) -> Asset | None:
        """The sound for this phrase: from the folder if we already have it, else searched and downloaded."""
        key = f"{kind}:{query}"
        hit = self.assets.get(key)
        if hit and self.path(hit).is_file():
            return hit
        if offline() or self._blocked:
            return None
        hits: list[dict[str, Any]] = []
        try:
            for phrase in phrases(query):
                self._wait()
                hits = rank(search(phrase, kind, get=self.get), kind, want, phrase)
                if hits:
                    break
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._blocked = True
            self.log(f"⚠ Không tìm được âm thanh trên mạng ({exc}); bỏ qua các âm thanh chưa tải.")
            return None
        for h in hits[:4]:  # a dead link or a broken file: try the next result
            name = f"{kind}-{_slug(query)}.{h['type']}"
            dst = self.folder / name
            try:
                self._wait(0.3)
                self.folder.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(self.get(h["url"]))
                duration = probe_duration(dst)
            except Exception:  # noqa: BLE001 (any bad download: next candidate)
                dst.unlink(missing_ok=True)
                continue
            asset = Asset(query=query, kind=kind, file=name, duration=round(duration, 3), title=h["title"],
                          creator=h["creator"], license=h["license"], page=h["page"])
            self.assets[key] = asset
            self._save()
            self.log(f"  ↓ {'nhạc' if kind == 'music' else 'tiếng động'} “{query}”: {asset.title or name} "
                     f"({duration:.1f}s, {asset.license.upper()})")
            return asset
        self.log(f"  (không có âm thanh phù hợp cho “{query}”)")
        return None

    def _wait(self, gap: float | None = None) -> None:
        delay = (self.pause if gap is None else gap) - (time.time() - self._last)
        if delay > 0:
            time.sleep(delay)
        self._last = time.time()

    def _save(self) -> None:
        self._index.write_text(json.dumps({k: v.model_dump() for k, v in self.assets.items()}, ensure_ascii=False,
                                          indent=1), encoding="utf-8")

    def write_credits(self, used: list[Asset]) -> Path | None:
        """CREDITS.txt for the sounds in this cut (what to paste into the video description)."""
        path = self.folder / "CREDITS.txt"
        if not used:
            path.unlink(missing_ok=True)
            return None
        seen, lines = set(), ["Nhạc và tiếng động trong phim (nguồn: Openverse).",
                              "Mục CC BY phải ghi tên tác giả ở phần mô tả video.", ""]
        for a in used:
            if a.file not in seen:
                seen.add(a.file)
                lines.append(("Nhạc: " if a.kind == "music" else "Tiếng động: ") + a.credit())
        self.folder.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


__all__ = ["Asset", "Library", "offline", "phrases", "rank", "search"]
