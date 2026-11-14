"""Video crawler: scan Facebook pages, TikTok channels, and YouTube channels to find the top most-viewed videos,
ready to feed straight into `video_analysis` (Phân tích video).

Pipeline:
1. parse_sources()      - clean, normalize, and deduplicate one or many channel / page / video links.
2. classify_source()    - detect platform (YouTube, TikTok, Facebook, other) and expand bare channels/pages
                          into their video + shorts/reels tabs so short-form hits are never missed.
3. crawl_source()       - YouTube & TikTok: `yt-dlp --flat-playlist --dump-json` (fast, includes view_count,
                          duration, thumbnails). Facebook pages: Relay JSON / HTML discovery on `/reels` and
                          `/videos` (+ optional headless Playwright scroll when needed) enriched via `yt-dlp`.
4. rank_videos()        - deduplicate across sources, apply optional duration / short-form filters, sort by
                          `view_count` descending, and pick global Top N (or Top N per source).
5. Ready for analysis   - `link_analyses()` matches crawled videos against `~/FilmMaker/.analyses/`;
                          `download_top_videos()` pre-fetches ≤720p MP4s; `analyze_crawled()` runs
                          `video_analysis.analyze()` on selected top videos in batch.
"""

from __future__ import annotations

import html as html_lib
import ipaddress
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from pydantic import BaseModel, Field

from . import video_analysis as va

Log = Callable[[str], None]
StopCheck = Callable[[], bool]

Platform = Literal["youtube", "tiktok", "facebook", "other"]

DEFAULT_TOP = 10
MAX_TOP = 100
DEFAULT_SCAN_LIMIT = 50
MAX_SCAN_LIMIT = 500
SHORT_MAX_SECONDS = 180.0
MAX_HTML_BYTES = 8 * 1024 * 1024
YTDLP_ENRICH_BATCH = 10

CRAWL_FILE = "crawl.json"
BROWSER_CHOICES = ("chrome", "safari", "brave", "edge", "firefox")
PLATFORM_LABELS: dict[str, str] = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "facebook": "Facebook",
    "other": "Khác",
}

_BARE_DOMAIN_PREFIXES = (
    "youtube.com/", "www.youtube.com/", "m.youtube.com/", "youtu.be/",
    "tiktok.com/", "www.tiktok.com/", "m.tiktok.com/", "vm.tiktok.com/", "vt.tiktok.com/",
    "facebook.com/", "www.facebook.com/", "m.facebook.com/", "web.facebook.com/",
    "fb.com/", "www.fb.com/", "fb.watch/",
)

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "igshid", "si", "feature", "pp", "is_from_webapp", "sender_device",
    "web_id", "_r", "_t", "mibexttid", "mibextid", "rdid", "share_url",
}


class CrawlError(RuntimeError):
    """Raised when crawling cannot proceed or every source failed."""


class CrawlStopped(Exception):
    """Raised when the user presses Stop."""


# --- data models --------------------------------------------------------------


class CrawledVideo(BaseModel):
    rank: int = 0
    id: str = ""
    platform: Platform = "other"
    url: str
    title: str = ""
    channel: str = ""
    source_url: str = ""
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    repost_count: int | None = None
    duration: float | None = None
    upload_date: str = ""
    timestamp: int | None = None
    thumbnail: str = ""
    description: str = ""
    is_short: bool = False
    downloaded_file: str = ""


class SourceReport(BaseModel):
    input_url: str
    normalized_url: str
    platform: Platform
    channel: str = ""
    scanned: int = 0
    kept: int = 0
    status: Literal["ok", "empty", "error"] = "ok"
    error: str = ""


class CrawlResult(BaseModel):
    version: int = 1
    id: str
    title: str
    created: float
    options: dict[str, Any] = Field(default_factory=dict)
    sources: list[SourceReport] = Field(default_factory=list)
    total_scanned: int = 0
    videos: list[CrawledVideo] = Field(default_factory=list)


# --- URL & safety helpers -----------------------------------------------------


def _has_control_chars(s: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in s)


def _is_safe_public_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    s = url.strip()
    if not s or s.startswith("-") or _has_control_chars(s) or any(ch.isspace() for ch in s):
        return False
    parsed = urllib.parse.urlparse(s)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return False
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host or host in ("localhost", "broadcasthost") or host.endswith((".local", ".localhost", ".internal")):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." in host
    return ip.is_global


def safe_http_url(url: str | None) -> str:
    """Return `url` stripped if it is a safe public http(s) URL, else empty string."""
    if not url:
        return ""
    s = str(url).strip()
    return s if _is_safe_public_url(s) else ""


def validate_browser(browser: str | None) -> str | None:
    if browser is None:
        return None
    b = browser.strip().lower()
    if not b or b == "none":
        return None
    if b not in BROWSER_CHOICES:
        raise CrawlError(f"Trình duyệt không hợp lệ '{browser}'. Chọn một trong: {', '.join(BROWSER_CHOICES)}.")
    return b


def _normalize_raw_token(token: str) -> str:
    s = token.strip().strip("\"'<>")
    lower = s.lower()
    if any(lower.startswith(prefix) for prefix in _BARE_DOMAIN_PREFIXES):
        s = "https://" + s
    return s


def parse_sources(raw: str | Iterable[str]) -> list[str]:
    """Parse one or many channel / page / video links from multiline text or an iterable of strings."""
    lines: list[str] = []
    if isinstance(raw, str):
        lines = raw.splitlines()
    else:
        for item in raw:
            if isinstance(item, str):
                lines.extend(item.splitlines())

    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # Strip common list prefixes ("- https://...", "1. https://...", "• https://...")
        s = re.sub(r"^(?:[-*•]+|\d+[.)])\s+", "", s).strip()
        for tok in re.split(r"[\s,;]+", s):
            if not tok:
                continue
            cand = _normalize_raw_token(tok)
            if not _is_safe_public_url(cand):
                raise CrawlError(f"Link không hợp lệ (chỉ nhận link http/https công khai): {tok[:120]}")
            key = _dedup_source_key(cand)
            if key not in seen:
                seen.add(key)
                out.append(cand)
    if not out:
        raise CrawlError("Hãy nhập ít nhất một link trang Facebook, kênh TikTok hoặc kênh YouTube.")
    return out


def _dedup_source_key(url: str) -> str:
    p = urllib.parse.urlparse(url.strip())
    host = re.sub(r"^(?:www|m|web)\.", "", (p.hostname or "").lower())
    path = p.path.rstrip("/") or "/"
    qs = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=False) if k.lower() not in _TRACKING_PARAMS]
    qs.sort()
    query = urllib.parse.urlencode(qs)
    return f"{host}{path}?{query}" if query else f"{host}{path}"


def detect_platform(url: str) -> Platform:
    host = (urllib.parse.urlparse(url.strip()).hostname or "").lower()
    if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        return "youtube"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok"
    if host in ("facebook.com", "fb.com", "fb.watch") or host.endswith((".facebook.com", ".fb.com")):
        return "facebook"
    return "other"


def canonical_video_key(url: str) -> str:
    """Stable key for a single video across URL forms (e.g. youtube shorts vs watch?v=, fb reel vs watch)."""
    s = safe_http_url(url)
    if not s:
        return ""
    p = urllib.parse.urlparse(s)
    host = (p.hostname or "").lower()
    path = p.path.rstrip("/")
    qs = urllib.parse.parse_qs(p.query)

    platform = detect_platform(s)
    if platform == "youtube":
        if host == "youtu.be":
            vid = path.lstrip("/").split("/")[0]
            if vid:
                return f"youtube:{vid}"
        if "v" in qs and qs["v"]:
            return f"youtube:{qs['v'][0]}"
        m = re.match(r"^/(?:shorts|live|embed|v)/([A-Za-z0-9_-]{6,20})(?:/|$)", path)
        if m:
            return f"youtube:{m.group(1)}"
    elif platform == "tiktok":
        m = re.search(r"/(?:video|photo)/(\d{8,25})(?:/|$)", path)
        if m:
            return f"tiktok:{m.group(1)}"
    elif platform == "facebook":
        for key in ("v", "video_id", "story_fbid"):
            if qs.get(key) and re.fullmatch(r"\d{8,25}|pfbid[A-Za-z0-9]+", qs[key][0]):
                return f"facebook:{qs[key][0]}"
        m = re.search(r"/(?:reels?|videos)/(?:[^/]+/)?(\d{8,25})(?:/|$)", path)
        if m:
            return f"facebook:{m.group(1)}"
    return _dedup_source_key(s)


def classify_source(url: str, *, shorts_only: bool = False) -> dict[str, Any]:
    """Classify an input URL and return target URL(s) to scan."""
    url = url.strip()
    platform = detect_platform(url)
    p = urllib.parse.urlparse(url)
    host = (p.hostname or "").lower()
    path = re.sub(r"/+", "/", p.path).rstrip("/")
    qs = urllib.parse.parse_qs(p.query)

    if platform == "youtube":
        if host == "youtu.be":
            return {"platform": "youtube", "kind": "single", "normalized_url": url, "targets": [url],
                    "label": path.lstrip("/") or "YouTube"}
        if path in ("/watch", "/watch/"):
            if "list" in qs and "v" not in qs:
                pl = f"https://www.youtube.com/playlist?list={qs['list'][0]}"
                return {"platform": "youtube", "kind": "tab", "normalized_url": pl, "targets": [pl],
                        "label": f"Playlist {qs['list'][0][:12]}"}
            return {"platform": "youtube", "kind": "single", "normalized_url": url, "targets": [url],
                    "label": qs.get("v", ["YouTube"])[0]}
        if re.match(r"^/(?:shorts|live|clip|embed)/[^/]+", path):
            norm = f"https://www.youtube.com{path}"
            return {"platform": "youtube", "kind": "single", "normalized_url": norm, "targets": [norm],
                    "label": path.split("/")[-1]}
        if path == "/playlist" and "list" in qs:
            norm = f"https://www.youtube.com/playlist?list={qs['list'][0]}"
            return {"platform": "youtube", "kind": "tab", "normalized_url": norm, "targets": [norm],
                    "label": f"Playlist {qs['list'][0][:12]}"}

        # Channel URLs: /@handle, /channel/UC..., /c/Name, /user/Name, or /Name
        parts = [seg for seg in path.split("/") if seg]
        yt_tabs = {"videos", "shorts", "streams", "playlists", "releases", "podcasts", "featured"}
        if parts:
            is_prefixed = parts[0] in ("channel", "c", "user") and len(parts) >= 2
            base_parts = parts[:2] if is_prefixed else parts[:1]
            rest = parts[2:] if is_prefixed else parts[1:]
            label = base_parts[-1]
            base = "https://www.youtube.com/" + "/".join(base_parts)
            if rest and rest[0].lower() in yt_tabs:
                tab_url = f"{base}/{rest[0].lower()}"
                return {"platform": "youtube", "kind": "tab", "normalized_url": tab_url, "targets": [tab_url],
                        "label": label}
            if not rest:
                tabs = [f"{base}/shorts", f"{base}/videos"] if shorts_only else [f"{base}/videos", f"{base}/shorts"]
                return {"platform": "youtube", "kind": "channel", "normalized_url": base, "targets": tabs,
                        "label": label}
        return {"platform": "youtube", "kind": "tab", "normalized_url": url, "targets": [url], "label": "YouTube"}

    if platform == "tiktok":
        if host in ("vm.tiktok.com", "vt.tiktok.com") or re.search(r"/(?:video|photo|t)/", path):
            return {"platform": "tiktok", "kind": "single", "normalized_url": url, "targets": [url],
                    "label": path.split("/")[-1] or "TikTok"}
        m = re.match(r"^/@([\w.-]+)", path)
        if m:
            handle = m.group(1)
            norm = f"https://www.tiktok.com/@{handle}"
            return {"platform": "tiktok", "kind": "channel", "normalized_url": norm, "targets": [norm],
                    "label": f"@{handle}"}
        return {"platform": "tiktok", "kind": "channel", "normalized_url": url, "targets": [url], "label": "TikTok"}

    if platform == "facebook":
        if host == "fb.watch":
            return {"platform": "facebook", "kind": "single", "normalized_url": url, "targets": [url],
                    "label": "Facebook Watch"}
        # Single reel: /reel/123 or /reels/123
        m_reel = re.match(r"^/reels?/(\d{8,25})$", path)
        if m_reel:
            norm = f"https://www.facebook.com/reel/{m_reel.group(1)}"
            return {"platform": "facebook", "kind": "single", "normalized_url": norm, "targets": [norm],
                    "label": f"Reel {m_reel.group(1)[:10]}"}
        # Single video / post / watch / share
        if (path in ("/watch", "/watch/live", "/video.php", "/video/video.php", "/permalink.php", "/story.php", "/photo.php")
                or re.match(r"^/share/[vr]/", path)
                or re.search(r"/(?:videos|posts)/(?:[^/]+/)?(?:\d{8,25}|pfbid[A-Za-z0-9]+)$", path)):
            norm_q = f"?{p.query}" if p.query else ""
            norm = f"https://www.facebook.com{path}{norm_q}"
            return {"platform": "facebook", "kind": "single", "normalized_url": norm, "targets": [norm],
                    "label": "Facebook Video"}
        # Profile by numeric ID: profile.php?id=12345
        if path == "/profile.php" and qs.get("id"):
            pid = re.sub(r"[^\w.-]", "", qs["id"][0])
            sk = (qs.get("sk") or [""])[0].lower()
            base = f"https://www.facebook.com/profile.php?id={pid}"
            if sk in ("reels_tab", "reels"):
                return {"platform": "facebook", "kind": "page", "normalized_url": f"{base}&sk=reels_tab",
                        "targets": [f"{base}&sk=reels_tab"], "label": f"FB {pid}"}
            if sk == "videos":
                return {"platform": "facebook", "kind": "page", "normalized_url": f"{base}&sk=videos",
                        "targets": [f"{base}&sk=videos"], "label": f"FB {pid}"}
            targets = [f"{base}&sk=reels_tab", f"{base}&sk=videos"]
            return {"platform": "facebook", "kind": "page", "normalized_url": base, "targets": targets,
                    "label": f"FB {pid}"}
        # Named page or /people/<slug>/<id>
        parts = [seg for seg in path.split("/") if seg]
        if parts and parts[0] == "pg" and len(parts) >= 2:
            parts = parts[1:]
        if parts:
            if parts[0] == "people" and len(parts) >= 3:
                base_parts, rest = parts[:3], parts[3:]
                label = parts[1]
            else:
                base_parts, rest = parts[:1], parts[1:]
                label = parts[0]
            base = "https://www.facebook.com/" + "/".join(base_parts)
            sub = rest[0].lower() if rest else ""
            if sub in ("reels", "videos"):
                tab_url = f"{base}/{sub}"
                return {"platform": "facebook", "kind": "page", "normalized_url": tab_url, "targets": [tab_url],
                        "label": label}
            targets = [f"{base}/reels", f"{base}/videos"] if shorts_only else [f"{base}/videos", f"{base}/reels"]
            return {"platform": "facebook", "kind": "page", "normalized_url": base, "targets": targets,
                    "label": label}
        return {"platform": "facebook", "kind": "page", "normalized_url": url, "targets": [url], "label": "Facebook"}

    label = (p.hostname or "source").removeprefix("www.")
    return {"platform": "other", "kind": "tab", "normalized_url": url, "targets": [url], "label": label}


# --- human numbers & Facebook title helpers -----------------------------------


_MULTIPLIERS: list[tuple[str, float]] = [
    ("tỷ", 1_000_000_000),
    ("tỉ", 1_000_000_000),
    ("triệu", 1_000_000),
    ("nghìn", 1_000),
    ("ngàn", 1_000),
    ("tr", 1_000_000),
    ("b", 1_000_000_000),
    ("m", 1_000_000),
    ("k", 1_000),
    ("n", 1_000),
]

_COUNT_RE = re.compile(
    r"(?<!\w)(\d+(?:[.,]\d+)*)\s*(tỷ|tỉ|triệu|nghìn|ngàn|tr|b|m|k|n)?(?!\w)",
    re.IGNORECASE,
)


def parse_count(value: Any) -> int | None:
    """Parse an integer or human-formatted count ('9.7K views', '1,2 Tr lượt xem', '1.234.567', 45000)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value >= 0 else None
    if not isinstance(value, str):
        return None
    s = html_lib.unescape(value).replace("\xa0", " ").strip()
    if not s:
        return None
    if re.search(r"\bno\s+views\b|\b0\s+lượt\s+xem\b", s, re.IGNORECASE):
        return 0
    m = _COUNT_RE.search(s)
    if not m:
        return None
    num_raw = re.sub(r"\s+", "", m.group(1))
    unit = (m.group(2) or "").lower()
    mult = next((mul for suf, mul in _MULTIPLIERS if unit == suf), 1.0)

    if mult > 1.0:
        # With a unit suffix ('1.2M', '1,2 Tr'), a single dot or comma is a decimal separator.
        norm = num_raw.replace(",", ".")
        if norm.count(".") > 1:
            parts = norm.split(".")
            norm = "".join(parts[:-1]) + "." + parts[-1]
        try:
            return max(0, int(round(float(norm) * mult)))
        except ValueError:
            return None

    # Without a unit suffix: '1,234,567' or '1.234.567' are thousands separators.
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", num_raw):
        return int(num_raw.replace(",", ""))
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", num_raw):
        return int(num_raw.replace(".", ""))
    digits = re.sub(r"[.,]", "", num_raw)
    return int(digits) if digits.isdigit() else None


def fmt_views_vi(count: int | None) -> str:
    """Short Vietnamese count string: 1,2 Tr · 45,6 N · 850."""
    if count is None:
        return "Chưa rõ"
    n = max(0, int(count))
    if n >= 1_000_000_000:
        v = f"{n / 1_000_000_000:.1f}".removesuffix(".0").replace(".", ",")
        return f"{v} tỷ"
    if n >= 1_000_000:
        v = f"{n / 1_000_000:.1f}".removesuffix(".0").replace(".", ",")
        return f"{v} Tr"
    if n >= 1_000:
        v = f"{n / 1_000:.1f}".removesuffix(".0").replace(".", ",")
        return f"{v} N"
    return str(n)


def parse_facebook_title(raw_title: str, uploader: str = "") -> dict[str, Any]:
    """Extract embedded view/reaction counts and clean caption from Facebook's title format:
    '9.7K views · 352 reactions | Actual title | Page Name'."""
    text = html_lib.unescape(raw_title or "").strip()
    up_clean = ""
    if uploader:
        up_clean = html_lib.unescape(uploader).strip()
        if "|" in up_clean and re.search(r"\b(?:views?|lượt\s+xem|reactions?|cảm\s+xúc|plays?)\b",
                                         up_clean.split("|")[0], re.IGNORECASE):
            up_clean = up_clean.split("|")[-1].strip()

    out: dict[str, Any] = {"title": text, "view_count": None, "like_count": None, "uploader": up_clean}
    if not text:
        return out

    parts = [p.strip() for p in text.split("|")]
    first = parts[0]
    has_stats_head = bool(re.search(r"\b(?:views?|lượt\s+xem|reactions?|cảm\s+xúc|plays?)\b", first, re.IGNORECASE))
    if has_stats_head:
        for seg in re.split(r"[·•\-]", first):
            if re.search(r"\b(?:views?|lượt\s+xem|plays?)\b", seg, re.IGNORECASE) and out["view_count"] is None:
                out["view_count"] = parse_count(seg)
            elif re.search(r"\b(?:reactions?|cảm\s+xúc|likes?|thích)\b", seg, re.IGNORECASE) and out["like_count"] is None:
                out["like_count"] = parse_count(seg)
        rest = [p for p in parts[1:] if p]
        if rest:
            if len(rest) >= 2 and (not up_clean or rest[-1].casefold() == up_clean.casefold()):
                out["uploader"] = rest[-1]
                rest = rest[:-1]
            out["title"] = " | ".join(rest).strip() or text

    return out


# --- subprocess & yt-dlp helpers ----------------------------------------------


def _run_interruptible(cmd: list[str], *, timeout: float, should_stop: StopCheck = lambda: False,
                       env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    try:
        return va._run(cmd, timeout=timeout, should_stop=should_stop, env=env)
    except va.AnalysisStopped:
        raise CrawlStopped() from None


def _pick_thumbnail(raw: dict[str, Any]) -> str:
    direct = safe_http_url(raw.get("thumbnail"))
    if direct:
        return direct
    thumbs = raw.get("thumbnails")
    if not isinstance(thumbs, list):
        return ""
    best_url = ""
    best_score = (-1, -1)
    for t in thumbs:
        if not isinstance(t, dict):
            continue
        u = safe_http_url(t.get("url"))
        if not u:
            continue
        tid = str(t.get("id") or "").lower()
        is_static = 0 if "dynamic" in tid or u.lower().endswith(".heic") else 1
        area = int(t.get("width") or 0) * int(t.get("height") or 0)
        score = (is_static, area)
        if score >= best_score:
            best_score = score
            best_url = u
    return best_url


def _format_upload_date(raw_date: Any, timestamp: int | None) -> str:
    if isinstance(raw_date, str) and re.fullmatch(r"\d{8}", raw_date.strip()):
        d = raw_date.strip()
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    if isinstance(timestamp, (int, float)) and timestamp > 0:
        try:
            return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            return ""
    return ""


def _int_or_none(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        iv = int(v)
        return iv if iv >= 0 else None
    except (TypeError, ValueError):
        return None


def _float_or_none(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        fv = float(v)
        return round(fv, 2) if math.isfinite(fv) and fv > 0 else None
    except (TypeError, ValueError):
        return None


def _entry_to_video(raw: dict[str, Any], *, source_url: str, platform_hint: Platform = "other",
                    channel_hint: str = "") -> CrawledVideo | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("_type") in ("playlist", "multi_video"):
        return None
    live_status = str(raw.get("live_status") or "").lower()
    if live_status in ("is_upcoming", "is_live"):
        return None
    avail = str(raw.get("availability") or "").lower()
    if avail in ("private", "premium_only", "subscriber_only", "needs_auth"):
        return None

    vid = str(raw.get("id") or "").strip()
    url = safe_http_url(raw.get("webpage_url")) or safe_http_url(raw.get("url"))
    if not url and vid and platform_hint == "youtube" and re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
        url = f"https://www.youtube.com/watch?v={vid}"
    if not url:
        return None

    platform = detect_platform(url)
    if platform == "other" and platform_hint != "other":
        platform = platform_hint

    title = html_lib.unescape(str(raw.get("title") or "").strip())
    if title in ("[Private video]", "[Deleted video]", "[Unavailable video]"):
        return None

    uploader = str(raw.get("channel") or raw.get("uploader") or raw.get(" uploader_id") or channel_hint or "").strip()
    view_count = _int_or_none(raw.get("view_count"))
    if view_count is None:
        view_count = parse_count(raw.get("view_count_text"))
    like_count = _int_or_none(raw.get("like_count"))
    comment_count = _int_or_none(raw.get("comment_count"))
    repost_count = _int_or_none(raw.get("repost_count"))

    if platform == "facebook":
        fb_parsed = parse_facebook_title(title, uploader)
        title = fb_parsed["title"]
        if fb_parsed["uploader"]:
            uploader = fb_parsed["uploader"]
        if view_count is None and fb_parsed["view_count"] is not None:
            view_count = fb_parsed["view_count"]
        if like_count is None and fb_parsed["like_count"] is not None:
            like_count = fb_parsed["like_count"]

    duration = _float_or_none(raw.get("duration"))
    timestamp = _int_or_none(raw.get("timestamp")) or _int_or_none(raw.get("release_timestamp"))
    upload_date = _format_upload_date(raw.get("upload_date"), timestamp)
    thumbnail = _pick_thumbnail(raw)
    desc = html_lib.unescape(str(raw.get("description") or "").strip())[:500]
    if not title and desc:
        title = " ".join(desc.split())[:100]

    url_lower = url.lower()
    is_short = (
        platform == "tiktok"
        or "/shorts/" in url_lower
        or "/reel/" in url_lower
        or bool(raw.get("is_short"))
        or (duration is not None and duration <= SHORT_MAX_SECONDS)
    )

    if not vid:
        key = canonical_video_key(url)
        vid = key.split(":", 1)[-1] if ":" in key else ""

    return CrawledVideo(
        id=vid,
        platform=platform,
        url=url,
        title=title or f"Video {vid or 'không tiêu đề'}",
        channel=uploader or channel_hint,
        source_url=source_url,
        view_count=view_count,
        like_count=like_count,
        comment_count=comment_count,
        repost_count=repost_count,
        duration=duration,
        upload_date=upload_date,
        timestamp=timestamp,
        thumbnail=thumbnail,
        description=desc,
        is_short=is_short,
    )


def _merge_video(a: CrawledVideo, b: CrawledVideo) -> CrawledVideo:
    """Merge duplicate video entries (e.g. discovered from HTML then enriched by yt-dlp)."""
    views = a.view_count if (a.view_count is not None and (b.view_count is None or a.view_count >= b.view_count)) else b.view_count
    a_real = bool(a.title and not a.title.startswith("Video "))
    b_real = bool(b.title and not b.title.startswith("Video "))
    if a_real and b_real:
        title = b.title if len(b.title) > len(a.title) else a.title
    elif b_real:
        title = b.title
    else:
        title = a.title or b.title
    url = a.url
    if "/watch?v=" in a.url and "/shorts/" in b.url:
        url = b.url
    return CrawledVideo(
        rank=a.rank or b.rank,
        id=a.id or b.id,
        platform=a.platform if a.platform != "other" else b.platform,
        url=url,
        title=title,
        channel=a.channel or b.channel,
        source_url=a.source_url or b.source_url,
        view_count=views,
        like_count=a.like_count if a.like_count is not None else b.like_count,
        comment_count=a.comment_count if a.comment_count is not None else b.comment_count,
        repost_count=a.repost_count if a.repost_count is not None else b.repost_count,
        duration=a.duration if a.duration is not None else b.duration,
        upload_date=a.upload_date or b.upload_date,
        timestamp=a.timestamp if a.timestamp is not None else b.timestamp,
        thumbnail=a.thumbnail or b.thumbnail,
        description=a.description or b.description,
        is_short=a.is_short or b.is_short,
        downloaded_file=a.downloaded_file or b.downloaded_file,
    )


def _parse_jsonl(stdout: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _ytdlp_cookie_args(cookies_from_browser: str | None) -> list[str]:
    b = validate_browser(cookies_from_browser)
    return ["--cookies-from-browser", b] if b else []


def _ytdlp_flat(ytdlp: list[str], target_url: str, *, scan_limit: int, cookies_from_browser: str | None,
                timeout: float = 180.0, should_stop: StopCheck = lambda: False) -> tuple[list[dict[str, Any]], str]:
    cmd = [
        *ytdlp,
        "--flat-playlist",
        "--dump-json",
        "--no-progress",
        "--no-warnings",
        "--ignore-errors",
        "--playlist-end", str(max(1, min(MAX_SCAN_LIMIT, int(scan_limit)))),
        "--extractor-args", "youtubetab:approximate_date",
        *_ytdlp_cookie_args(cookies_from_browser),
        "--",
        target_url,
    ]
    try:
        proc = _run_interruptible(cmd, timeout=timeout, should_stop=should_stop)
    except subprocess.TimeoutExpired:
        return [], "quá thời gian chờ khi quét danh sách video"
    rows = _parse_jsonl(proc.stdout)
    err = ""
    if not rows and proc.returncode != 0:
        tail = [ln.strip() for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()][-2:]
        err = " ".join(tail)[-400:] or f"yt-dlp exit {proc.returncode}"
    return rows, err


def _ytdlp_batch_info(ytdlp: list[str], urls: list[str], *, cookies_from_browser: str | None,
                      timeout: float = 180.0, should_stop: StopCheck = lambda: False) -> tuple[list[dict[str, Any]], str]:
    safe_urls = [u for u in urls if _is_safe_public_url(u)]
    if not safe_urls:
        return [], ""
    cmd = [
        *ytdlp,
        "--dump-json",
        "--skip-download",
        "--no-playlist",
        "--no-progress",
        "--no-warnings",
        "--ignore-errors",
        *_ytdlp_cookie_args(cookies_from_browser),
        "--",
        *safe_urls,
    ]
    try:
        proc = _run_interruptible(cmd, timeout=timeout, should_stop=should_stop)
    except subprocess.TimeoutExpired:
        return [], "quá thời gian chờ khi đọc thông tin video"
    rows = _parse_jsonl(proc.stdout)
    err = ""
    if not rows and proc.returncode != 0:
        tail = [ln.strip() for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()][-2:]
        err = " ".join(tail)[-400:] or f"yt-dlp exit {proc.returncode}"
    return rows, err


# --- Facebook page discovery --------------------------------------------------


class _FacebookRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        resolved = urllib.parse.urljoin(req.full_url, newurl)
        if not _is_safe_public_url(resolved) or detect_platform(resolved) != "facebook":
            raise urllib.error.URLError("redirect outside Facebook refused")
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def _fetch_fb_html(url: str, *, timeout: float = 25.0, should_stop: StopCheck = lambda: False) -> str:
    if not _is_safe_public_url(url) or detect_platform(url) != "facebook":
        raise CrawlError("Link Facebook không hợp lệ.")
    opener = urllib.request.build_opener(_FacebookRedirectHandler())
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        },
    )
    chunks: list[bytes] = []
    size = 0
    with opener.open(req, timeout=timeout) as resp:
        while chunk := resp.read(256 * 1024):
            if should_stop():
                raise CrawlStopped()
            size += len(chunk)
            chunks.append(chunk)
            if size >= MAX_HTML_BYTES:
                break
    return b"".join(chunks).decode("utf-8", errors="replace")


def _clean_fb_page_title(raw_title: str) -> str:
    t = html_lib.unescape(raw_title or "").strip()
    t = re.sub(r"\s*[|\-–]\s*(?:Facebook|Meta|Videos|Reels|Video|Thước phim)\s*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*[|\-–]\s*(?:Facebook|Meta)\s*$", "", t, flags=re.IGNORECASE)
    if t.lower() in ("facebook", "log in or sign up to view", "đăng nhập facebook", "log into facebook"):
        return ""
    return t.strip()


def _walk_relay_nodes(obj: Any, out: dict[str, dict[str, Any]], *, default_reel: bool = False) -> None:
    if isinstance(obj, list):
        for item in obj:
            _walk_relay_nodes(item, out, default_reel=default_reel)
        return
    if not isinstance(obj, dict):
        return

    typename = str(obj.get("__typename") or "")
    vid = str(obj.get("video_id") or (obj.get("id") if (
        typename in ("Video", "FBReelsVideo", "ShortFormVideo")
        or "playable_duration_in_ms" in obj
        or "playable_url" in obj
        or "play_count" in obj
        or "video_view_count" in obj
        or "play_count_reduced" in obj
    ) else "") or "").strip()

    if re.fullmatch(r"\d{8,25}", vid):
        rec = out.setdefault(vid, {"id": vid})
        for vk in ("play_count", "video_view_count", "view_count", "reel_play_count"):
            c = parse_count(obj.get(vk))
            if c is not None and (rec.get("view_count") is None or c > rec["view_count"]):
                rec["view_count"] = c
        for tk in ("play_count_reduced", "view_count_text", "video_view_count_renderer", "accessibility_caption"):
            val = obj.get(tk)
            if isinstance(val, dict):
                val = val.get("text") or val.get("simpleText")
            c = parse_count(val)
            if c is not None and rec.get("view_count") is None:
                rec["view_count"] = c

        dur_ms = _float_or_none(obj.get("playable_duration_in_ms"))
        if dur_ms:
            rec.setdefault("duration", round(dur_ms / 1000.0, 2))
        elif _float_or_none(obj.get("length_in_second")):
            rec.setdefault("duration", _float_or_none(obj.get("length_in_second")))

        ts = _int_or_none(obj.get("publish_time")) or _int_or_none(obj.get("creation_time"))
        if ts:
            rec.setdefault("timestamp", ts)

        for title_key in ("title", "savable_description", "message", "name"):
            tv = obj.get(title_key)
            if isinstance(tv, dict):
                tv = tv.get("text")
            if isinstance(tv, str) and tv.strip() and not rec.get("title"):
                rec["title"] = tv.strip()[:240]

        owner = obj.get("owner") or obj.get("video_owner") or {}
        if isinstance(owner, dict) and isinstance(owner.get("name"), str) and owner["name"].strip():
            rec.setdefault("channel", owner["name"].strip())

        for lk in ("reaction_count", "likers", "reactors"):
            lv = obj.get(lk)
            c = _int_or_none(lv.get("count") if isinstance(lv, dict) else lv)
            if c is not None and rec.get("like_count") is None:
                rec["like_count"] = c
        for ck in ("total_comment_count", "comment_count", "comments"):
            cv = obj.get(ck)
            c = _int_or_none(cv.get("count") if isinstance(cv, dict) else cv)
            if c is not None and rec.get("comment_count") is None:
                rec["comment_count"] = c
        for sk in ("share_count", "reshares"):
            sv = obj.get(sk)
            c = _int_or_none(sv.get("count") if isinstance(sv, dict) else sv)
            if c is not None and rec.get("repost_count") is None:
                rec["repost_count"] = c

        for th_path in (
            obj.get("preferred_thumbnail", {}).get("image", {}).get("uri") if isinstance(obj.get("preferred_thumbnail"), dict) and isinstance(obj.get("preferred_thumbnail", {}).get("image"), dict) else None,
            obj.get("thumbnailImage", {}).get("uri") if isinstance(obj.get("thumbnailImage"), dict) else None,
            obj.get("image", {}).get("uri") if isinstance(obj.get("image"), dict) else None,
        ):
            u = safe_http_url(th_path)
            if u and not rec.get("thumbnail"):
                rec["thumbnail"] = u

        for uk in ("permalink_url", "url", "wwwUrl"):
            cand = obj.get(uk)
            if isinstance(cand, str) and cand.startswith("/"):
                cand = "https://www.facebook.com" + cand
            u = safe_http_url(cand)
            if u and detect_platform(u) == "facebook" and any(seg in u for seg in ("/reel/", "/videos/", "/watch")):
                rec.setdefault("url", u)

        is_reel = bool(obj.get("is_reel")) or typename in ("FBReelsVideo", "ShortFormVideo") or default_reel
        if is_reel:
            rec["is_short"] = True
        if not rec.get("url"):
            rec["url"] = f"https://www.facebook.com/reel/{vid}" if is_reel else f"https://www.facebook.com/watch/?v={vid}"

    for val in obj.values():
        if isinstance(val, (dict, list)):
            _walk_relay_nodes(val, out, default_reel=default_reel)


def parse_facebook_page_html(html_text: str, page_url: str = "") -> tuple[str, list[dict[str, Any]]]:
    """Extract page name and candidate video dicts from Facebook's `/reels` or `/videos` HTML."""
    if not html_text:
        return "", []
    default_reel = "/reel" in page_url.lower() or "reels_tab" in page_url.lower()

    page_name = ""
    m_og = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
    if not m_og:
        m_og = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html_text, re.IGNORECASE)
    if m_og:
        page_name = _clean_fb_page_title(m_og.group(1))
    if not page_name:
        m_t = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
        if m_t:
            page_name = _clean_fb_page_title(m_t.group(1))

    found: dict[str, dict[str, Any]] = {}

    # 1. Relay JSON script blocks
    for m in re.finditer(r"<script[^>]+type=[\"']application/json[\"'][^>]*>(.*?)</script>", html_text, re.DOTALL | re.IGNORECASE):
        blob = m.group(1).strip()
        if not blob:
            continue
        try:
            parsed = json.loads(blob)
        except ValueError:
            continue
        _walk_relay_nodes(parsed, found, default_reel=default_reel)

    # 2. Anchor tags (<a href=".../reel/123"...>1.2M</a> or aria-label)
    for m in re.finditer(r"<a\b([^>]*?)href=[\"']([^\"']+)[\"']([^>]*)>(.*?)</a>", html_text, re.DOTALL | re.IGNORECASE):
        attrs = html_lib.unescape(m.group(1) + " " + m.group(3))
        href = html_lib.unescape(m.group(2)).replace("\\/", "/")
        inner_html = m.group(4)
        inner_txt = html_lib.unescape(re.sub(r"<[^>]+>", " ", inner_html)).strip()

        vid, is_reel, full_url = _extract_fb_video_ref(href, default_reel=default_reel)
        if not vid:
            continue
        rec = found.setdefault(vid, {"id": vid, "url": full_url, "is_short": is_reel})
        rec.setdefault("url", full_url)
        if is_reel:
            rec["is_short"] = True

        aria_m = re.search(r'aria-label=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
        label_txt = aria_m.group(1).strip() if aria_m else ""
        for snippet in (label_txt, inner_txt):
            if not snippet:
                continue
            if rec.get("view_count") is None:
                # Plain compact count on Reel grid cards ('1.2M', '450K', '1,2 Tr', '85K views')
                if re.fullmatch(r"\d[\d.,\s]*(?:tỷ|tỉ|triệu|nghìn|ngàn|tr|b|m|k|n)?(?:\s*(?:views?|lượt\s+xem|plays?))?",
                                snippet, re.IGNORECASE):
                    rec["view_count"] = parse_count(snippet)
                elif re.search(r"\b(?:views?|lượt\s+xem|plays?)\b", snippet, re.IGNORECASE):
                    rec["view_count"] = parse_count(snippet)
            if not rec.get("title") and len(snippet) > 8 and not re.fullmatch(r"[\d.,\sA-Za-z]{1,10}", snippet):
                fb_t = parse_facebook_title(snippet, page_name)
                if fb_t["view_count"] is not None and rec.get("view_count") is None:
                    rec["view_count"] = fb_t["view_count"]
                cleaned = fb_t["title"]
                if cleaned and not re.fullmatch(r"[\d.,\s]+(?:views?|lượt\s+xem)?", cleaned, re.IGNORECASE):
                    rec["title"] = cleaned[:240]

        img_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', inner_html, re.IGNORECASE)
        if img_m and not rec.get("thumbnail"):
            th = safe_http_url(html_lib.unescape(img_m.group(1)))
            if th:
                rec["thumbnail"] = th

    # 3. Escaped or plain URLs anywhere in the document
    normalized_raw = html_text.replace("\\/", "/")
    for rx, is_reel in (
        (r"/(?:reels?)/(\d{8,25})(?=[/?\"'#&\s<])", True),
        (r"/videos/(?:[^/\"'?#\s<>]+/)?(\d{8,25})(?=[/?\"'#&\s<])", False),
        (r"/watch/?\?(?:[^\"'#\s<>]*?&)?v=(\d{8,25})", False),
    ):
        for mm in re.finditer(rx, normalized_raw):
            vid = mm.group(1)
            url = f"https://www.facebook.com/reel/{vid}" if is_reel else f"https://www.facebook.com/watch/?v={vid}"
            rec = found.setdefault(vid, {"id": vid, "url": url, "is_short": is_reel or default_reel})
            rec.setdefault("url", url)

    items = []
    for vid, rec in found.items():
        if page_name and not rec.get("channel"):
            rec["channel"] = page_name
        items.append(rec)
    return page_name, items


def _extract_fb_video_ref(href: str, *, default_reel: bool = False) -> tuple[str, bool, str]:
    s = href.strip()
    if s.startswith("/"):
        s = "https://www.facebook.com" + s
    if not _is_safe_public_url(s) or detect_platform(s) != "facebook":
        return "", False, ""
    p = urllib.parse.urlparse(s)
    qs = urllib.parse.parse_qs(p.query)
    m_reel = re.search(r"/reels?/(\d{8,25})(?:/|$)", p.path)
    if m_reel:
        vid = m_reel.group(1)
        return vid, True, f"https://www.facebook.com/reel/{vid}"
    m_vid = re.search(r"/videos/(?:[^/]+/)?(\d{8,25})(?:/|$)", p.path)
    if m_vid:
        vid = m_vid.group(1)
        clean_path = p.path.rstrip("/")
        return vid, default_reel, f"https://www.facebook.com{clean_path}/"
    for k in ("v", "video_id"):
        if qs.get(k) and re.fullmatch(r"\d{8,25}", qs[k][0]):
            vid = qs[k][0]
            return vid, default_reel, f"https://www.facebook.com/watch/?v={vid}"
    return "", False, ""


def playwright_python() -> str | None:
    """Python interpreter with Playwright installed ($FILMMAKER_PLAYWRIGHT_PYTHON, gflow's venv, or current Python)."""
    env = os.environ.get("FILMMAKER_PLAYWRIGHT_PYTHON")
    if env is not None:
        return env or None
    from .gflow import _gflow_python

    gp = _gflow_python(os.environ.get("GFLOW_BIN") or "gflow")
    if gp:
        return gp
    try:
        import playwright  # noqa: F401
    except ImportError:
        return None
    return sys.executable


_FB_PLAYWRIGHT_DRIVER = r"""
import json, sys, time
from playwright.sync_api import sync_playwright

urls = json.loads(sys.argv[1])
scrolls = max(1, min(8, int(sys.argv[2])))
pages_html = []
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        locale="vi-VN",
        user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        viewport={"width": 1280, "height": 900},
    )
    page = ctx.new_page()
    for u in urls:
        try:
            page.goto(u, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
            page.keyboard.press("Escape")
            for _ in range(scrolls):
                page.mouse.wheel(0, 2200)
                page.wait_for_timeout(900)
            pages_html.append({"url": u, "html": page.content()})
        except Exception as exc:
            pages_html.append({"url": u, "html": "", "error": str(exc)})
    browser.close()
print(json.dumps({"pages": pages_html}))
"""


def _discover_facebook_playwright(tab_urls: list[str], *, scan_limit: int,
                                  should_stop: StopCheck = lambda: False) -> list[tuple[str, str]]:
    py = playwright_python()
    if not py:
        return []
    scrolls = max(2, min(6, math.ceil(scan_limit / 15)))
    try:
        proc = _run_interruptible(
            [py, "-c", _FB_PLAYWRIGHT_DRIVER, json.dumps(tab_urls), str(scrolls)],
            timeout=60.0,
            should_stop=should_stop,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    try:
        payload = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (IndexError, ValueError):
        return []
    return [(str(p.get("url") or ""), str(p.get("html") or "")) for p in (payload.get("pages") or []) if p.get("html")]


def _crawl_facebook_page(info: dict[str, Any], *, ytdlp: list[str], scan_limit: int,
                         cookies_from_browser: str | None, log: Log,
                         should_stop: StopCheck) -> tuple[str, list[CrawledVideo], str]:
    source_url = info["normalized_url"]
    targets: list[str] = info["targets"]
    by_key: dict[str, CrawledVideo] = {}
    channel_name = ""
    http_errors: list[str] = []

    for tab_url in targets:
        if should_stop():
            raise CrawlStopped()
        try:
            html_text = _fetch_fb_html(tab_url, should_stop=should_stop)
        except CrawlStopped:
            raise
        except Exception as exc:
            http_errors.append(str(exc))
            continue
        pname, raw_items = parse_facebook_page_html(html_text, tab_url)
        if pname and not channel_name:
            channel_name = pname
        for item in raw_items:
            v = _entry_to_video(item, source_url=source_url, platform_hint="facebook",
                                channel_hint=channel_name or info["label"])
            if v:
                k = canonical_video_key(v.url)
                by_key[k] = _merge_video(by_key[k], v) if k in by_key else v

    if not by_key:
        for tab_url, html_text in _discover_facebook_playwright(targets, scan_limit=scan_limit, should_stop=should_stop):
            pname, raw_items = parse_facebook_page_html(html_text, tab_url)
            if pname and not channel_name:
                channel_name = pname
            for item in raw_items:
                v = _entry_to_video(item, source_url=source_url, platform_hint="facebook",
                                    channel_hint=channel_name or info["label"])
                if v:
                    k = canonical_video_key(v.url)
                    by_key[k] = _merge_video(by_key[k], v) if k in by_key else v

    # Fallback: try yt-dlp flat-playlist directly (supports custom yt-dlp plugins or future extractors)
    ytdlp_err = ""
    if not by_key:
        rows, ytdlp_err = _ytdlp_flat(ytdlp, source_url, scan_limit=scan_limit,
                                      cookies_from_browser=cookies_from_browser, should_stop=should_stop)
        for row in rows:
            v = _entry_to_video(row, source_url=source_url, platform_hint="facebook",
                                channel_hint=channel_name or info["label"])
            if v:
                if v.channel and not channel_name:
                    channel_name = v.channel
                k = canonical_video_key(v.url)
                by_key[k] = _merge_video(by_key[k], v) if k in by_key else v

    # Cap candidates to scan_limit (keeping ones that already have view_count first)
    candidates = list(by_key.values())
    candidates.sort(key=lambda v: (v.view_count is not None, v.view_count or -1), reverse=True)
    candidates = candidates[:scan_limit]
    by_key = {canonical_video_key(v.url): v for v in candidates}

    # Enrich Facebook candidates missing view_count or real title via batched yt-dlp calls
    need_enrich = [v.url for v in candidates if v.view_count is None or v.title.startswith("Video ")]
    if need_enrich:
        log(f"  Đang đọc chi tiết lượt xem cho {len(need_enrich)} video Facebook…")
        for i in range(0, len(need_enrich), YTDLP_ENRICH_BATCH):
            if should_stop():
                raise CrawlStopped()
            batch = need_enrich[i:i + YTDLP_ENRICH_BATCH]
            rows, _ = _ytdlp_batch_info(ytdlp, batch, cookies_from_browser=cookies_from_browser,
                                        should_stop=should_stop)
            for row in rows:
                v = _entry_to_video(row, source_url=source_url, platform_hint="facebook",
                                    channel_hint=channel_name or info["label"])
                if v:
                    if v.channel and not channel_name:
                        channel_name = v.channel
                    k = canonical_video_key(v.url)
                    by_key[k] = _merge_video(by_key[k], v) if k in by_key else v

    videos = list(by_key.values())
    if channel_name:
        for v in videos:
            if not v.channel or v.channel == info["label"]:
                v.channel = channel_name

    err = ""
    if not videos:
        detail = ytdlp_err or (http_errors[-1] if http_errors else "")
        hint = " (thử bật Cookie trình duyệt nếu trang yêu cầu đăng nhập Facebook)" if not cookies_from_browser else ""
        err = f"Không tìm thấy video công khai trên trang Facebook này{hint}" + (f": {detail}" if detail else "")
    return channel_name or info["label"], videos, err


# --- crawling a single source -------------------------------------------------


def crawl_source(input_url: str, *, ytdlp: list[str], scan_limit: int = DEFAULT_SCAN_LIMIT,
                 shorts_only: bool = False, cookies_from_browser: str | None = None,
                 log: Log = print, should_stop: StopCheck = lambda: False) -> tuple[SourceReport, list[CrawledVideo]]:
    """Crawl one input URL (channel, page, playlist, or single video) and return (SourceReport, videos)."""
    info = classify_source(input_url, shorts_only=shorts_only)
    platform: Platform = info["platform"]
    kind = info["kind"]
    norm_url = info["normalized_url"]

    if kind == "single":
        rows, err = _ytdlp_batch_info(ytdlp, [norm_url], cookies_from_browser=cookies_from_browser,
                                      should_stop=should_stop)
        videos = [v for r in rows if (v := _entry_to_video(r, source_url=norm_url, platform_hint=platform,
                                                           channel_hint=info["label"]))]
        ch = videos[0].channel if videos else info["label"]
        status: Literal["ok", "empty", "error"] = "ok" if videos else ("error" if err else "empty")
        return SourceReport(
            input_url=input_url, normalized_url=norm_url, platform=platform, channel=ch,
            scanned=len(videos), status=status, error=err if not videos else "",
        ), videos

    if platform == "facebook" and kind == "page":
        ch, videos, err = _crawl_facebook_page(
            info, ytdlp=ytdlp, scan_limit=scan_limit, cookies_from_browser=cookies_from_browser,
            log=log, should_stop=should_stop,
        )
        status = "ok" if videos else ("error" if err else "empty")
        return SourceReport(
            input_url=input_url, normalized_url=norm_url, platform="facebook", channel=ch,
            scanned=len(videos), status=status, error=err,
        ), videos

    # YouTube / TikTok / other playlist or channel tabs
    by_key: dict[str, CrawledVideo] = {}
    channel_name = ""
    errors: list[str] = []
    for target_url in info["targets"]:
        if should_stop():
            raise CrawlStopped()
        rows, err = _ytdlp_flat(ytdlp, target_url, scan_limit=scan_limit,
                                cookies_from_browser=cookies_from_browser, should_stop=should_stop)
        if err:
            errors.append(err)
        for row in rows:
            v = _entry_to_video(row, source_url=norm_url, platform_hint=platform,
                                channel_hint=channel_name or info["label"])
            if v:
                if v.channel and not channel_name:
                    channel_name = v.channel
                k = canonical_video_key(v.url)
                by_key[k] = _merge_video(by_key[k], v) if k in by_key else v

    videos = list(by_key.values())
    err_msg = ""
    if not videos and errors:
        err_msg = errors[0]
        if platform == "tiktok" and not cookies_from_browser:
            err_msg += " (thử chọn Cookie trình duyệt nếu TikTok chặn truy cập)"
    status = "ok" if videos else ("error" if err_msg else "empty")
    return SourceReport(
        input_url=input_url,
        normalized_url=norm_url,
        platform=platform,
        channel=channel_name or info["label"],
        scanned=len(videos),
        status=status,
        error=err_msg,
    ), videos


# --- ranking & filtering ------------------------------------------------------


def _sort_key(v: CrawledVideo) -> tuple[bool, int, int, int]:
    return (
        v.view_count is not None,
        v.view_count if v.view_count is not None else -1,
        v.like_count or 0,
        v.timestamp or 0,
    )


def _passes_filter(v: CrawledVideo, *, min_duration: float | None = None,
                   max_duration: float | None = None, shorts_only: bool = False) -> bool:
    if shorts_only:
        if v.duration is not None and v.duration > SHORT_MAX_SECONDS:
            return False
        if v.duration is None and not v.is_short:
            return False
    if min_duration is not None and min_duration > 0:
        if v.duration is None or v.duration < min_duration:
            return False
    if max_duration is not None and max_duration > 0:
        if v.duration is None or v.duration > max_duration:
            return False
    return True


def rank_videos(videos_by_source: list[tuple[SourceReport, list[CrawledVideo]]], *,
                top_n: int = DEFAULT_TOP, per_source: bool = False,
                min_duration: float | None = None, max_duration: float | None = None,
                shorts_only: bool = False) -> list[CrawledVideo]:
    """Deduplicate, filter, sort by view_count descending, and assign 1-based ranks."""
    top_n = max(1, min(MAX_TOP, int(top_n)))

    if per_source:
        chosen_by_key: dict[str, CrawledVideo] = {}
        for rep, vids in videos_by_source:
            local: dict[str, CrawledVideo] = {}
            for v in vids:
                if not _passes_filter(v, min_duration=min_duration, max_duration=max_duration, shorts_only=shorts_only):
                    continue
                k = canonical_video_key(v.url)
                local[k] = _merge_video(local[k], v) if k in local else v
            picked = sorted(local.values(), key=_sort_key, reverse=True)[:top_n]
            for v in picked:
                k = canonical_video_key(v.url)
                chosen_by_key[k] = _merge_video(chosen_by_key[k], v) if k in chosen_by_key else v
        ranked = sorted(chosen_by_key.values(), key=_sort_key, reverse=True)
    else:
        pool: dict[str, CrawledVideo] = {}
        for _rep, vids in videos_by_source:
            for v in vids:
                if not _passes_filter(v, min_duration=min_duration, max_duration=max_duration, shorts_only=shorts_only):
                    continue
                k = canonical_video_key(v.url)
                pool[k] = _merge_video(pool[k], v) if k in pool else v
        ranked = sorted(pool.values(), key=_sort_key, reverse=True)[:top_n]

    kept_per_source: dict[str, int] = {}
    out: list[CrawledVideo] = []
    for idx, v in enumerate(ranked, 1):
        copy = v.model_copy(update={"rank": idx})
        out.append(copy)
        kept_per_source[copy.source_url] = kept_per_source.get(copy.source_url, 0) + 1

    for rep, _ in videos_by_source:
        rep.kept = kept_per_source.get(rep.normalized_url, 0)

    return out


# --- main crawl entry point & persistence -------------------------------------


def _crawl_title(sources: list[SourceReport], count: int) -> str:
    names = [s.channel or PLATFORM_LABELS.get(s.platform, s.platform) for s in sources if s.status == "ok"]
    if not names:
        names = [s.channel or PLATFORM_LABELS.get(s.platform, s.platform) for s in sources]
    head = ", ".join(names[:2])
    more = f" (+{len(names) - 2} kênh)" if len(names) > 2 else ""
    return f"Top {count} video · {head}{more}" if head else f"Top {count} video"


def crawl_path(folder: Path) -> Path:
    return folder / CRAWL_FILE


def save(data: dict[str, Any], folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    target = crawl_path(folder)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(target)
    (folder / "report.md").write_text(report_markdown(data), encoding="utf-8")
    urls_text = "\n".join(v["url"] for v in data.get("videos") or [] if v.get("url"))
    (folder / "urls.txt").write_text((urls_text + "\n") if urls_text else "", encoding="utf-8")


def load(folder: Path) -> dict[str, Any]:
    return json.loads(crawl_path(folder).read_text(encoding="utf-8"))


def crawl(sources: str | Iterable[str], folder: Path | None = None, *,
          top_n: int = DEFAULT_TOP, per_source: bool = False, scan_limit: int = DEFAULT_SCAN_LIMIT,
          shorts_only: bool = False, min_duration: float | None = None, max_duration: float | None = None,
          cookies_from_browser: str | None = None, log: Log = print,
          should_stop: StopCheck = lambda: False) -> dict[str, Any]:
    """Crawl all provided channel/page links, rank top most-viewed videos, and optionally save to `folder`."""
    urls = parse_sources(sources)
    cookies_from_browser = validate_browser(cookies_from_browser)
    top_n = max(1, min(MAX_TOP, int(top_n)))
    scan_limit = max(5, min(MAX_SCAN_LIMIT, int(scan_limit)))
    if min_duration is not None and max_duration is not None and min_duration > max_duration:
        raise CrawlError("Độ dài tối thiểu không được lớn hơn độ dài tối đa.")

    ytdlp = va.ytdlp_command()
    if not ytdlp:
        raise CrawlError("Cần cài 'yt-dlp' (brew install yt-dlp) để crawl video từ YouTube / TikTok / Facebook.")

    results: list[tuple[SourceReport, list[CrawledVideo]]] = []
    for idx, url in enumerate(urls, 1):
        if should_stop():
            raise CrawlStopped()
        plat = PLATFORM_LABELS.get(detect_platform(url), "Web")
        log(f"[{idx}/{len(urls)}] Đang quét {plat}: {url}")
        rep, vids = crawl_source(
            url,
            ytdlp=ytdlp,
            scan_limit=scan_limit,
            shorts_only=shorts_only,
            cookies_from_browser=cookies_from_browser,
            log=log,
            should_stop=should_stop,
        )
        if rep.status == "error":
            log(f"  ⚠ Lỗi khi quét {url}: {rep.error}")
        elif not vids:
            log(f"  ⚠ Không tìm thấy video công khai nào tại {url}.")
        else:
            best = max((v.view_count for v in vids if v.view_count is not None), default=None)
            best_txt = f", cao nhất {fmt_views_vi(best)} lượt xem" if best is not None else ""
            log(f"  ✔ {rep.channel}: quét được {len(vids)} video{best_txt}.")
        results.append((rep, vids))

    total_scanned = sum(rep.scanned for rep, _ in results)
    ranked = rank_videos(
        results,
        top_n=top_n,
        per_source=per_source,
        min_duration=min_duration,
        max_duration=max_duration,
        shorts_only=shorts_only,
    )

    if not ranked:
        errs = [f"{r.normalized_url}: {r.error}" for r, _ in results if r.error]
        if errs:
            raise CrawlError("Không lấy được video nào. " + " | ".join(errs[:3]))
        if total_scanned > 0:
            raise CrawlError(f"Đã quét {total_scanned} video nhưng không có video nào khớp bộ lọc (độ dài / Shorts).")
        raise CrawlError("Không tìm thấy video công khai nào từ các link đã nhập.")

    source_reports = [r for r, _ in results]
    first_label = source_reports[0].channel or detect_platform(urls[0])
    cid = folder.name if folder else va.new_id(f"crawl-{first_label}")
    title = _crawl_title(source_reports, len(ranked))

    model = CrawlResult(
        id=cid,
        title=title,
        created=time.time(),
        options={
            "top_n": top_n,
            "per_source": per_source,
            "scan_limit": scan_limit,
            "shorts_only": shorts_only,
            "min_duration": min_duration,
            "max_duration": max_duration,
            "cookies_from_browser": cookies_from_browser,
        },
        sources=source_reports,
        total_scanned=total_scanned,
        videos=ranked,
    )
    data = model.model_dump()
    if folder is not None:
        save(data, folder)
    log(f"✔ Đã chọn ra {len(ranked)} video nhiều view nhất (từ {total_scanned} video đã quét).")
    return data


# --- report & history ---------------------------------------------------------


def report_markdown(data: dict[str, Any]) -> str:
    lines = [
        f"# {data.get('title') or 'Kết quả crawl video'}",
        "",
        f"Tổng số video đã quét: **{data.get('total_scanned', 0)}** · Chọn ra: **{len(data.get('videos') or [])}** video",
        "",
        "## Nguồn đã quét",
    ]
    for s in data.get("sources") or []:
        plat = PLATFORM_LABELS.get(s.get("platform", "other"), "Khác")
        st = f"{s.get('scanned', 0)} quét, giữ {s.get('kept', 0)}"
        if s.get("error"):
            st += f" — ⚠ {s['error']}"
        lines.append(f"- **{plat}** · {s.get('channel') or s.get('normalized_url')}: {st} ({s.get('input_url')})")

    lines += [
        "",
        "## Top video nhiều view nhất",
        "",
        "| # | Nền tảng | Lượt xem | Thích | Dài | Ngày đăng | Kênh | Tiêu đề | Link |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for v in data.get("videos") or []:
        cells = [
            str(v.get("rank") or ""),
            PLATFORM_LABELS.get(v.get("platform", "other"), "Khác"),
            fmt_views_vi(v.get("view_count")),
            fmt_views_vi(v.get("like_count")) if v.get("like_count") is not None else "-",
            va.fmt_time(v["duration"]) if v.get("duration") else "-",
            v.get("upload_date") or "-",
            str(v.get("channel") or ""),
            str(v.get("title") or ""),
            str(v.get("url") or ""),
        ]
        lines.append("| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in cells) + " |")
    lines.append("")
    return "\n".join(lines)


def _existing_analyses_map(analyses_root: Path | None) -> dict[str, dict[str, str]]:
    """Map canonical_video_key -> {id, title} for every completed analysis in `analyses_root`."""
    mapping: dict[str, dict[str, str]] = {}
    if not analyses_root or not analyses_root.is_dir():
        return mapping
    for item in va.list_analyses(analyses_root):
        key = canonical_video_key(item.get("url") or "")
        if key and key not in mapping:
            mapping[key] = {"id": item["id"], "title": item.get("title") or item["id"]}
    return mapping


def link_analyses(data: dict[str, Any], analyses_root: Path | None) -> dict[str, Any]:
    """Attach `analysis_id` and `analysis_title` to any crawled video that has already been analyzed."""
    existing = _existing_analyses_map(analyses_root)
    videos = []
    analyzed_count = 0
    for v in data.get("videos") or []:
        row = dict(v)
        hit = existing.get(canonical_video_key(row.get("url") or ""))
        if hit:
            row["analysis_id"] = hit["id"]
            row["analysis_title"] = hit["title"]
            analyzed_count += 1
        else:
            row["analysis_id"] = None
            row["analysis_title"] = None
        videos.append(row)
    return {**data, "videos": videos, "analyzed_count": analyzed_count}


def list_crawls(root: Path, analyses_root: Path | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    existing = _existing_analyses_map(analyses_root)
    for d in root.iterdir():
        p = crawl_path(d)
        if not p.is_file():
            continue
        try:
            data = load(d)
        except (OSError, ValueError):
            continue
        videos = data.get("videos") or []
        sources = data.get("sources") or []
        platforms = list(dict.fromkeys(s.get("platform", "other") for s in sources))
        top_views = max((v.get("view_count") for v in videos if isinstance(v.get("view_count"), int)), default=None)
        analyzed_n = sum(1 for v in videos if canonical_video_key(v.get("url") or "") in existing)
        thumb = next((safe_http_url(v.get("thumbnail")) for v in videos if safe_http_url(v.get("thumbnail"))), None)
        out.append({
            "id": d.name,
            "title": data.get("title") or d.name,
            "created": data.get("created", 0),
            "sources_count": len(sources),
            "videos_count": len(videos),
            "total_scanned": data.get("total_scanned", 0),
            "platforms": platforms,
            "top_views": top_views,
            "analyzed_count": analyzed_n,
            "thumb": thumb,
        })
    return sorted(out, key=lambda x: -float(x["created"] or 0))


# --- pre-download & batch bridge to video_analysis ----------------------------


def download_top_videos(data: dict[str, Any], folder: Path, *, log: Log = print,
                        should_stop: StopCheck = lambda: False) -> dict[str, Any]:
    """Pre-download ≤720p MP4s for crawled top videos into `folder/videos/<rank>/` so analysis is instant."""
    videos = data.get("videos") or []
    for i, v in enumerate(videos, 1):
        if should_stop():
            raise CrawlStopped()
        rel = v.get("downloaded_file") or ""
        if rel:
            existing = (folder / rel).resolve()
            if folder.resolve() in existing.parents and existing.is_file():
                continue
        url = v.get("url") or ""
        if not _is_safe_public_url(url):
            continue
        rank = int(v.get("rank") or i)
        sub = folder / "videos" / f"{rank:02d}"
        log(f"[{i}/{len(videos)}] Tải sẵn video #{rank}: {v.get('title') or url}")
        try:
            fetched = va.fetch(url, sub, allow_local=False, log=log, should_stop=should_stop)
        except va.AnalysisStopped:
            shutil.rmtree(sub, ignore_errors=True)
            raise CrawlStopped() from None
        except va.AnalysisError as exc:
            shutil.rmtree(sub, ignore_errors=True)
            log(f"  ⚠ Không tải trước được #{rank}: {exc}")
            continue
        v["downloaded_file"] = f"videos/{rank:02d}/{fetched['file']}"
        if fetched.get("subtitles"):
            (sub / "subtitles.txt").write_text(fetched["subtitles"], encoding="utf-8")
    save(data, folder)
    return data


def analyze_crawled(videos: list[dict[str, Any]], analyses_root: Path, *,
                    crawl_dir: Path | None = None, use_rea: bool = True, force: bool = False,
                    backend: str = "auto", api_key: str | None = None, log: Log = print,
                    should_stop: StopCheck = lambda: False) -> dict[str, Any]:
    """Run `video_analysis.analyze()` on one or multiple crawled videos, reusing pre-downloaded files if present."""
    if not videos:
        raise CrawlError("Chưa chọn video nào để phân tích.")
    analyses_root.mkdir(parents=True, exist_ok=True)
    existing = {} if force else _existing_analyses_map(analyses_root)

    analyzed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    errors: list[str] = []

    for idx, v in enumerate(videos, 1):
        if should_stop():
            raise CrawlStopped()
        url = str(v.get("url") or "").strip()
        if not _is_safe_public_url(url):
            errors.append(f"Link không hợp lệ: {url[:100]}")
            continue
        key = canonical_video_key(url)
        if not force and key in existing:
            hit = existing[key]
            log(f"[{idx}/{len(videos)}] ⏭ Đã có bản phân tích cho “{v.get('title') or url}” → {hit['id']}")
            skipped.append({"url": url, "analysis": hit["id"], "title": hit["title"]})
            analyzed.append({"url": url, "analysis": hit["id"], "title": hit["title"]})
            continue

        # Reuse pre-downloaded video inside crawl_dir when available
        local_video: Path | None = None
        local_subs = ""
        rel_dl = str(v.get("downloaded_file") or "").strip()
        if crawl_dir and rel_dl:
            cand = (crawl_dir / rel_dl).resolve()
            if crawl_dir.resolve() in cand.parents and cand.is_file():
                local_video = cand
                subs_file = cand.parent / "subtitles.txt"
                if subs_file.is_file():
                    local_subs = subs_file.read_text(encoding="utf-8", errors="replace")

        slug_hint = str(v.get("title") or v.get("channel") or re.sub(r"^https?://(www\.)?", "", url).split("/")[0])
        base_aid = va.new_id(slug_hint)
        aid, n_try = base_aid, 2
        while (analyses_root / aid).exists():
            aid = f"{base_aid}-{n_try}"
            n_try += 1
        dest = analyses_root / aid

        log(f"[{idx}/{len(videos)}] Phân tích video #{v.get('rank') or idx} ({fmt_views_vi(v.get('view_count'))} view): "
            f"{v.get('title') or url}")
        try:
            if local_video is not None:
                data = va.analyze(str(local_video), dest, use_rea=use_rea, backend=backend, api_key=api_key,
                                  allow_local=True, log=log, should_stop=should_stop)
                # Restore original web metadata when analyzed from pre-downloaded local cache
                src = data.setdefault("source", {})
                src["url"] = src.get("url") or url
                if v.get("title") and (not src.get("title") or src.get("title") == local_video.stem):
                    src["title"] = v["title"]
                if v.get("channel") and not src.get("uploader"):
                    src["uploader"] = v["channel"]
                if local_subs and not data.get("subtitles"):
                    data["subtitles"] = local_subs
                va.analysis_path(dest).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                (dest / "report.md").write_text(va.report_markdown(data), encoding="utf-8")
            else:
                data = va.analyze(url, dest, use_rea=use_rea, backend=backend, api_key=api_key,
                                  allow_local=False, log=log, should_stop=should_stop)
        except va.AnalysisStopped:
            shutil.rmtree(dest, ignore_errors=True)
            raise CrawlStopped() from None
        except (va.AnalysisError, RuntimeError) as exc:
            shutil.rmtree(dest, ignore_errors=True)
            msg = f"{v.get('title') or url}: {exc}"
            log(f"  ⚠ Lỗi phân tích: {msg}")
            errors.append(msg)
            continue
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise

        title_vi = (data.get("ai") or {}).get("title_vi") or v.get("title") or aid
        analyzed.append({"url": url, "analysis": aid, "title": title_vi})
        if key:
            existing[key] = {"id": aid, "title": title_vi}

    if not analyzed and errors:
        raise CrawlError("Không phân tích được video nào: " + " | ".join(errors[:3]))

    return {
        "analyses": [a["analysis"] for a in analyzed],
        "items": analyzed,
        "skipped": [s["analysis"] for s in skipped],
        "errors": errors,
    }


__all__ = [
    "BROWSER_CHOICES",
    "CRAWL_FILE",
    "CrawlError",
    "CrawlResult",
    "CrawlStopped",
    "CrawledVideo",
    "DEFAULT_SCAN_LIMIT",
    "DEFAULT_TOP",
    "MAX_SCAN_LIMIT",
    "MAX_TOP",
    "PLATFORM_LABELS",
    "Platform",
    "SourceReport",
    "analyze_crawled",
    "canonical_video_key",
    "classify_source",
    "crawl",
    "crawl_path",
    "crawl_source",
    "detect_platform",
    "download_top_videos",
    "fmt_views_vi",
    "link_analyses",
    "list_crawls",
    "load",
    "parse_count",
    "parse_facebook_page_html",
    "parse_facebook_title",
    "parse_sources",
    "playwright_python",
    "rank_videos",
    "report_markdown",
    "safe_http_url",
    "save",
    "validate_browser",
]
