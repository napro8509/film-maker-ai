"""Auto-publish finished films to Facebook Reels and YouTube (Shorts / Video).

Supports:
- Facebook Graph API v20.0 (`video_reels` endpoint: start -> binary upload -> finish)
- YouTube Data API v3 (`videos.insert` resumable upload + `thumbnails.set` + OAuth2 refresh token)
- Auto-generating missing social copy via `social.py` (Claude) or falling back to storyboard metadata
- State persistence in `<out>/publish.json` so re-runs skip already-published platforms unless `force=True`
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from . import social
from .storyboard import Storyboard, load_storyboard

PUBLISH_FILE = "publish.json"
PUBLISH_PLATFORMS: dict[str, str] = {
    "facebook_reel": "Facebook Reels",
    "facebook_story": "Facebook Story",
    "youtube": "YouTube (Shorts / Video)",
}

PublishPlatform = Literal["facebook_reel", "facebook_story", "youtube"]
PrivacyStatus = Literal["public", "unlisted", "private"]

# Facebook Graph API endpoints
FB_GRAPH_VERSION = "v20.0"
FB_GRAPH_BASE = f"https://graph.facebook.com/{FB_GRAPH_VERSION}"

# YouTube Data API v3 endpoints
YT_TOKEN_URL = "https://oauth2.googleapis.com/token"
YT_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
YT_THUMB_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"


class PublishError(RuntimeError):
    """Raised when publishing to a platform fails."""


class PublishConfig(BaseModel):
    """Credentials and defaults for auto-publishing (stored in settings.json)."""
    fb_page_id: str = ""
    fb_access_token: str = ""
    yt_client_id: str = ""
    yt_client_secret: str = ""
    yt_refresh_token: str = ""
    yt_privacy: PrivacyStatus = "public"
    auto_publish: bool = False
    auto_publish_platforms: list[PublishPlatform] = Field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _file_sig(p: Path) -> str:
    try:
        st = p.stat()
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


def load_state(out_dir: Path) -> dict[str, Any]:
    """Load `<out>/publish.json`."""
    p = Path(out_dir) / PUBLISH_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("platforms", {})
            data.setdefault("history", [])
            return data
    except (OSError, ValueError):
        pass
    return {"platforms": {}, "history": []}


def save_state(out_dir: Path, data: dict[str, Any]) -> None:
    """Atomically save `<out>/publish.json`."""
    p = Path(out_dir) / PUBLISH_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def config_from_settings(settings: dict[str, Any]) -> PublishConfig:
    """Build PublishConfig from user settings dict."""
    raw_platforms = settings.get("auto_publish_platforms") or []
    valid_platforms = [p for p in raw_platforms if p in PUBLISH_PLATFORMS]
    privacy = settings.get("yt_privacy") or "public"
    if privacy not in ("public", "unlisted", "private"):
        privacy = "public"
    return PublishConfig(
        fb_page_id=str(settings.get("fb_page_id") or "").strip(),
        fb_access_token=str(settings.get("fb_access_token") or "").strip(),
        yt_client_id=str(settings.get("yt_client_id") or "").strip(),
        yt_client_secret=str(settings.get("yt_client_secret") or "").strip(),
        yt_refresh_token=str(settings.get("yt_refresh_token") or "").strip(),
        yt_privacy=privacy,
        auto_publish=bool(settings.get("auto_publish", False)),
        auto_publish_platforms=valid_platforms,
    )


def platform_configured(cfg: PublishConfig, platform: str) -> bool:
    if platform in ("facebook_reel", "facebook_story"):
        return bool(cfg.fb_page_id and cfg.fb_access_token)
    if platform == "youtube":
        return bool(cfg.yt_client_id and cfg.yt_client_secret and cfg.yt_refresh_token)
    return False


# --- Metadata preparation -----------------------------------------------------


def build_metadata(
    sb: Storyboard,
    out_dir: Path,
    platform: str,
    *,
    language: str | None = None,
    custom_title: str = "",
    custom_description: str = "",
) -> dict[str, Any]:
    """Build title, description, and tags for Facebook Reels or YouTube from social.json or storyboard."""
    social_data = social.load(out_dir)
    posts = (social_data or {}).get("posts") or []
    lang = language or sb.subtitle_language or sb.dialogue_language or "Vietnamese"
    if lang.lower() == "none":
        lang = "Vietnamese"

    # Pick the best matching post from social.json
    target_social_plat = "facebook" if platform in ("facebook_reel", "facebook_story") else "tiktok"
    matched_post = next(
        (p for p in posts if p.get("platform") == target_social_plat and p.get("language") == lang),
        None,
    ) or next(
        (p for p in posts if p.get("platform") == "facebook" and p.get("language") == lang),
        None,
    ) or (posts[0] if posts else None)

    base_title = custom_title.strip() or (
        (matched_post.get("title") or matched_post.get("hook") or "").strip() if matched_post else ""
    ) or (sb.thumbnail.text or "").strip() or (sb.title or "Phim ngắn AI").strip()

    if matched_post and not custom_description.strip():
        caption = (matched_post.get("caption") or "").strip()
        tags = [t.lstrip("#").strip() for t in (matched_post.get("hashtags") or []) if t.strip()]
    else:
        narrations = [s.narration.strip() for s in sb.scenes if s.narration and s.narration.strip()]
        caption = custom_description.strip() or "\n".join(narrations[:4]) or sb.title
        tags = ["phimngan", "aifilm", "shorts", "reels"]

    if platform == "youtube":
        is_short = sb.aspect == "9:16"
        if is_short and "#shorts" not in base_title.lower():
            # Keep YouTube title <= 100 chars
            suffix = " #Shorts"
            max_len = 100 - len(suffix)
            yt_title = (base_title[:max_len].rstrip() + suffix) if len(base_title) > max_len else (base_title + suffix)
        else:
            yt_title = base_title[:100]

        if is_short and not any(t.lower() == "shorts" for t in tags):
            tags = ["Shorts", *tags]

        hash_line = " ".join(f"#{t}" for t in tags)
        desc = f"{caption}\n\n{hash_line}".strip() if hash_line else caption
        return {
            "title": yt_title,
            "description": desc[:4900],
            "tags": tags[:30],
            "is_short": is_short,
        }

    if platform == "facebook_story":
        # Stories use a short text overlay; hashtags kept minimal
        if not any(t.lower() == "story" for t in tags):
            tags = ["Story", *tags]
        hash_line = " ".join(f"#{t}" for t in tags[:10])
        desc = f"{caption}\n\n{hash_line}".strip() if hash_line else caption
        return {
            "title": base_title[:255],
            "description": desc[:2200],
            "tags": tags,
        }

    # Facebook Reels
    if not any(t.lower() in ("reels", "fbreeels") for t in tags):
        tags = ["Reels", *tags]
    hash_line = " ".join(f"#{t}" for t in tags)
    header = f"{base_title}\n\n" if base_title and base_title not in caption else ""
    desc = f"{header}{caption}\n\n{hash_line}".strip()
    return {
        "title": base_title[:255],
        "description": desc[:2200],
        "tags": tags,
    }


# --- HTTP helper (stdlib urllib, easy to mock in tests) -----------------------


def _http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 120,
) -> tuple[int, dict[str, str], dict[str, Any]]:
    """Perform an HTTP request and parse JSON response."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            raw = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw.strip() else {}
            return status, resp_headers, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(raw)
        except ValueError:
            err_json = {"raw": raw[:500]}
        msg = (
            (err_json.get("error") or {}).get("message")
            if isinstance(err_json.get("error"), dict)
            else err_json.get("error_description") or err_json.get("error") or raw[:300]
        )
        raise PublishError(f"HTTP {exc.code} từ { urllib.parse.urlparse(url).netloc }: {msg}") from exc
    except urllib.error.URLError as exc:
        raise PublishError(f"Lỗi kết nối mạng tới {urllib.parse.urlparse(url).netloc}: {exc.reason}") from exc


# --- Facebook Reels uploader --------------------------------------------------


def upload_facebook_reel(
    video_path: Path,
    *,
    page_id: str,
    access_token: str,
    title: str,
    description: str,
    log: Callable[[str], None] = print,
    http_fn: Callable[..., tuple[int, dict[str, str], dict[str, Any]]] = _http_json,
) -> dict[str, Any]:
    """Upload a video to Facebook Page Reels via Graph API (start -> binary upload -> finish)."""
    if not video_path.is_file():
        raise PublishError(f"Không tìm thấy file video: {video_path}")
    if not page_id or not access_token:
        raise PublishError("Thiếu Facebook Page ID hoặc Page Access Token trong Cài đặt.")

    file_size = video_path.stat().st_size
    endpoint = f"{FB_GRAPH_BASE}/{urllib.parse.quote(page_id)}/video_reels"

    # Phase 1: Start upload session
    log(f"Facebook Reels: khởi tạo phiên tải lên cho Page {page_id}…")
    start_body = json.dumps({
        "upload_phase": "start",
        "access_token": access_token,
    }).encode("utf-8")
    _, _, start_res = http_fn(
        endpoint,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=start_body,
    )
    video_id = start_res.get("video_id")
    upload_url = start_res.get("upload_url") or f"https://rupload.facebook.com/video-upload/{FB_GRAPH_VERSION}/{video_id}"
    if not video_id:
        raise PublishError(f"Facebook không trả về video_id khi khởi tạo: {start_res}")

    # Phase 2: Upload binary file
    log(f"Facebook Reels: đang tải video lên ({file_size / (1024 * 1024):.1f} MB)…")
    video_bytes = video_path.read_bytes()
    _, _, upload_res = http_fn(
        upload_url,
        method="POST",
        headers={
            "Authorization": f"OAuth {access_token}",
            "offset": "0",
            "file_size": str(file_size),
            "Content-Type": "application/octet-stream",
        },
        data=video_bytes,
        timeout=600,
    )
    if upload_res.get("success") is False:
        raise PublishError(f"Facebook tải file thất bại: {upload_res}")

    # Phase 3: Finish & publish Reel
    log("Facebook Reels: hoàn tất và xuất bản…")
    finish_params = {
        "upload_phase": "finish",
        "access_token": access_token,
        "video_id": str(video_id),
        "video_state": "PUBLISHED",
        "description": description,
        "title": title,
    }
    finish_body = json.dumps(finish_params).encode("utf-8")
    _, _, finish_res = http_fn(
        endpoint,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=finish_body,
    )
    reel_url = f"https://www.facebook.com/reel/{video_id}"
    log(f"✔ Facebook Reels đã đăng: {reel_url}")
    return {
        "platform": "facebook_reel",
        "video_id": str(video_id),
        "url": reel_url,
        "title": title,
        "description": description,
        "at": _now(),
        "response": finish_res,
    }


# --- Facebook Story uploader --------------------------------------------------


def upload_facebook_story(
    video_path: Path,
    *,
    page_id: str,
    access_token: str,
    description: str,
    log: Callable[[str], None] = print,
    http_fn: Callable[..., tuple[int, dict[str, str], dict[str, Any]]] = _http_json,
) -> dict[str, Any]:
    """Upload a video to Facebook Page Story via Graph API (start -> binary upload -> finish)."""
    if not video_path.is_file():
        raise PublishError(f"Không tìm thấy file video: {video_path}")
    if not page_id or not access_token:
        raise PublishError("Thiếu Facebook Page ID hoặc Page Access Token trong Cài đặt.")

    file_size = video_path.stat().st_size
    endpoint = f"{FB_GRAPH_BASE}/{urllib.parse.quote(page_id)}/video_stories"

    # Phase 1: Start upload session
    log(f"Facebook Story: khởi tạo phiên tải lên cho Page {page_id}…")
    start_body = json.dumps({
        "upload_phase": "start",
        "access_token": access_token,
    }).encode("utf-8")
    _, _, start_res = http_fn(
        endpoint,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=start_body,
    )
    video_id = start_res.get("video_id")
    upload_url = start_res.get("upload_url") or f"https://rupload.facebook.com/video-upload/{FB_GRAPH_VERSION}/{video_id}"
    if not video_id:
        raise PublishError(f"Facebook không trả về video_id khi khởi tạo Story: {start_res}")

    # Phase 2: Upload binary file
    log(f"Facebook Story: đang tải video lên ({file_size / (1024 * 1024):.1f} MB)…")
    video_bytes = video_path.read_bytes()
    _, _, upload_res = http_fn(
        upload_url,
        method="POST",
        headers={
            "Authorization": f"OAuth {access_token}",
            "offset": "0",
            "file_size": str(file_size),
            "Content-Type": "application/octet-stream",
        },
        data=video_bytes,
        timeout=600,
    )
    if upload_res.get("success") is False:
        raise PublishError(f"Facebook tải file Story thất bại: {upload_res}")

    # Phase 3: Finish & publish Story
    log("Facebook Story: hoàn tất và xuất bản…")
    finish_params = {
        "upload_phase": "finish",
        "access_token": access_token,
        "video_id": str(video_id),
        "video_state": "PUBLISHED",
        "description": description,
    }
    finish_body = json.dumps(finish_params).encode("utf-8")
    _, _, finish_res = http_fn(
        endpoint,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=finish_body,
    )
    story_url = f"https://www.facebook.com/stories/{page_id}/{video_id}"
    log(f"✔ Facebook Story đã đăng: {story_url}")
    return {
        "platform": "facebook_story",
        "video_id": str(video_id),
        "url": story_url,
        "description": description,
        "at": _now(),
        "response": finish_res,
    }


# --- YouTube Data API v3 uploader ---------------------------------------------


def refresh_youtube_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    *,
    http_fn: Callable[..., tuple[int, dict[str, str], dict[str, Any]]] = _http_json,
) -> str:
    """Exchange OAuth2 refresh_token for a short-lived YouTube access_token."""
    if not (client_id and client_secret and refresh_token):
        raise PublishError("Thiếu YouTube Client ID, Client Secret hoặc Refresh Token trong Cài đặt.")
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode("utf-8")
    _, _, res = http_fn(
        YT_TOKEN_URL,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=body,
    )
    token = res.get("access_token")
    if not token:
        raise PublishError(f"Không lấy được access_token từ Google OAuth2: {res}")
    return str(token)


def upload_youtube_video(
    video_path: Path,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    title: str,
    description: str,
    tags: list[str] | None = None,
    privacy: PrivacyStatus = "public",
    thumbnail_path: Path | None = None,
    log: Callable[[str], None] = print,
    http_fn: Callable[..., tuple[int, dict[str, str], dict[str, Any]]] = _http_json,
) -> dict[str, Any]:
    """Upload a video (or YouTube Short) via YouTube Data API v3 Resumable Upload."""
    if not video_path.is_file():
        raise PublishError(f"Không tìm thấy file video: {video_path}")

    log("YouTube: đang xác thực OAuth2…")
    access_token = refresh_youtube_access_token(
        client_id, client_secret, refresh_token, http_fn=http_fn
    )

    file_size = video_path.stat().st_size
    meta_payload = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": (tags or [])[:30],
            "categoryId": "24",  # Entertainment / Film
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    meta_bytes = json.dumps(meta_payload, ensure_ascii=False).encode("utf-8")

    # Step 1: Initiate resumable upload session
    log(f"YouTube: khởi tạo phiên tải lên (“{title}”, chế độ: {privacy})…")
    _, resp_headers, init_res = http_fn(
        YT_UPLOAD_URL,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(file_size),
            "X-Upload-Content-Type": "video/mp4",
        },
        data=meta_bytes,
    )
    upload_location = resp_headers.get("location") or init_res.get("upload_url")
    if not upload_location:
        raise PublishError("YouTube không trả về đường dẫn Resumable Upload (Header Location).")

    # Step 2: Upload video binary
    log(f"YouTube: đang tải video lên ({file_size / (1024 * 1024):.1f} MB)…")
    video_bytes = video_path.read_bytes()
    _, _, upload_res = http_fn(
        upload_location,
        method="PUT",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Length": str(file_size),
            "Content-Type": "video/mp4",
        },
        data=video_bytes,
        timeout=600,
    )
    video_id = upload_res.get("id")
    if not video_id:
        raise PublishError(f"YouTube không trả về video ID sau khi tải lên: {upload_res}")

    # Step 3: Optional custom thumbnail upload
    thumb_uploaded = False
    if thumbnail_path and thumbnail_path.is_file():
        try:
            log(f"YouTube: đang đặt ảnh bìa ({thumbnail_path.name})…")
            mime = "image/png" if thumbnail_path.suffix.lower() == ".png" else "image/jpeg"
            http_fn(
                f"{YT_THUMB_URL}?videoId={urllib.parse.quote(str(video_id))}",
                method="POST",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": mime,
                },
                data=thumbnail_path.read_bytes(),
            )
            thumb_uploaded = True
        except PublishError as exc:
            log(f"⚠ YouTube: đã đăng video nhưng chưa đặt được ảnh bìa ({exc})")

    is_short = "#shorts" in title.lower()
    yt_url = f"https://www.youtube.com/shorts/{video_id}" if is_short else f"https://youtu.be/{video_id}"
    log(f"✔ YouTube đã đăng: {yt_url}")
    return {
        "platform": "youtube",
        "video_id": str(video_id),
        "url": yt_url,
        "title": title,
        "description": description,
        "privacy": privacy,
        "thumbnail_uploaded": thumb_uploaded,
        "at": _now(),
    }


# --- Orchestrator -------------------------------------------------------------


def publish_film(
    project_dir: Path,
    out_dir: Path,
    platforms: list[str],
    cfg: PublishConfig,
    *,
    force: bool = False,
    dry_run: bool = False,
    language: str | None = None,
    custom_title: str = "",
    custom_description: str = "",
    ai_backend: str | None = None,
    api_key: str | None = None,
    log: Callable[[str], None] = print,
    http_fn: Callable[..., tuple[int, dict[str, str], dict[str, Any]]] = _http_json,
) -> dict[str, Any]:
    """Publish `<out>/final.mp4` to the selected platforms (`facebook_reel`, `youtube`).

    - Automatically generates social copy via `social.generate()` if `social.json` is missing and Claude is available.
    - Skips platforms that already published the exact same `final.mp4` unless `force=True`.
    - Records outcomes in `<out>/publish.json`.
    """
    sb_path = Path(project_dir) / "storyboard.yaml"
    sb = load_storyboard(sb_path)
    out_dir = Path(out_dir)
    final_mp4 = out_dir / "final.mp4"
    if not final_mp4.is_file():
        raise PublishError(f"Chưa có phim hoàn chỉnh ({final_mp4}). Hãy ghép hoặc dựng phim trước khi đăng.")

    picked = [p for p in PUBLISH_PLATFORMS if p in set(platforms)]
    if not picked:
        raise ValueError("Chọn ít nhất một nền tảng để đăng (facebook_reel, youtube).")

    # Ensure social posts exist if possible so captions/hashtags are rich
    if not social.load(out_dir) and ai_backend and not custom_description.strip():
        try:
            log("Đang nhờ AI viết caption và hashtag cho bài đăng…")
            lang = language or sb.subtitle_language or "Vietnamese"
            if lang not in social.LANGUAGES:
                lang = "Vietnamese"
            social.generate(project_dir, out_dir, ["facebook", "tiktok"], [lang], backend=ai_backend, api_key=api_key)
        except Exception as exc:
            log(f"  (không tạo được bài đăng AI: {exc}; dùng thông tin từ storyboard)")

    state = load_state(out_dir)
    vid_sig = _file_sig(final_mp4)
    thumb = out_dir / "thumbnail.jpg"
    if not thumb.is_file():
        thumb = out_dir / "cover.png"

    results: dict[str, Any] = {}
    errors: dict[str, str] = {}

    for plat in picked:
        prev = state["platforms"].get(plat) or {}
        if not force and not dry_run and prev.get("status") == "published" and prev.get("video_sig") == vid_sig:
            log(f"• {PUBLISH_PLATFORMS[plat]}: đã đăng bản video này ({prev.get('url')}), bỏ qua (dùng --force để đăng lại).")
            results[plat] = prev
            continue

        meta = build_metadata(
            sb,
            out_dir,
            plat,
            language=language,
            custom_title=custom_title,
            custom_description=custom_description,
        )

        if dry_run:
            log(f"[DRY RUN] Sẽ đăng lên {PUBLISH_PLATFORMS[plat]}:")
            log(f"  File: {final_mp4}")
            log(f"  Tiêu đề: {meta['title']}")
            log(f"  Mô tả: {meta['description'][:160]}…")
            results[plat] = {
                "platform": plat,
                "status": "dry_run",
                "title": meta["title"],
                "description": meta["description"],
                "tags": meta.get("tags", []),
            }
            continue

        try:
            if plat == "facebook_reel":
                info = upload_facebook_reel(
                    final_mp4,
                    page_id=cfg.fb_page_id,
                    access_token=cfg.fb_access_token,
                    title=meta["title"],
                    description=meta["description"],
                    log=log,
                    http_fn=http_fn,
                )
            elif plat == "facebook_story":
                info = upload_facebook_story(
                    final_mp4,
                    page_id=cfg.fb_page_id,
                    access_token=cfg.fb_access_token,
                    description=meta["description"],
                    log=log,
                    http_fn=http_fn,
                )
            elif plat == "youtube":
                info = upload_youtube_video(
                    final_mp4,
                    client_id=cfg.yt_client_id,
                    client_secret=cfg.yt_client_secret,
                    refresh_token=cfg.yt_refresh_token,
                    title=meta["title"],
                    description=meta["description"],
                    tags=meta.get("tags"),
                    privacy=cfg.yt_privacy,
                    thumbnail_path=thumb if thumb.is_file() else None,
                    log=log,
                    http_fn=http_fn,
                )
            else:
                continue

            entry = {**info, "status": "published", "video_sig": vid_sig}
            state["platforms"][plat] = entry
            state["history"].append(entry)
            save_state(out_dir, state)
            results[plat] = entry
        except PublishError as exc:
            log(f"✘ {PUBLISH_PLATFORMS[plat]} lỗi: {exc}")
            err_entry = {
                "platform": plat,
                "status": "failed",
                "error": str(exc),
                "title": meta["title"],
                "at": _now(),
            }
            state["platforms"][plat] = err_entry
            save_state(out_dir, state)
            errors[plat] = str(exc)

    return {
        "results": results,
        "errors": errors,
        "state": state,
    }
