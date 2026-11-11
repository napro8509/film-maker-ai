"""Export and package completed films for publishing and distribution.

Collects final.mp4, subtitles (.srt), cover art, social media posts, and review summaries
into a clean release folder or a ready-to-publish .zip archive.
"""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import social
from .storyboard import Storyboard, load_storyboard


class ExportError(RuntimeError):
    pass


def _safe_filename(text: str, default: str = "film") -> str:
    """Convert title/text into a clean, filesystem-safe filename."""
    # Remove accents/diacritics or non-ascii/special characters for safer file paths if needed,
    # or keep unicode letters and numbers.
    cleaned = re.sub(r'[\\/*?:"<>|]', "", text).strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    return cleaned or default


def _format_social_text(social_data: dict[str, Any]) -> str:
    """Format social.json into ready-to-copy text for creators."""
    posts = social_data.get("posts") or []
    if not posts:
        return ""

    lines = [
        "============================================================",
        "             NỘI DUNG ĐĂNG MẠNG XÃ HỘI (SOCIAL POSTS)       ",
        "============================================================",
        "",
    ]
    for p in posts:
        platform = social.PLATFORMS.get(p.get("platform", ""), p.get("platform", "").capitalize())
        lang = social.LANGUAGES.get(p.get("language", ""), p.get("language", ""))
        lines.append(f"--- {platform.upper()} ({lang}) ---")
        if p.get("title"):
            lines.append(f"Tiêu đề: {p['title']}")
        if p.get("hook"):
            lines.append(f"Hook (chữ trên hình 2-3s đầu): {p['hook']}")
        lines.append("\nCaption:")
        lines.append(p.get("caption", "").strip())
        if p.get("hashtags"):
            tags = " ".join(f"#{t}" if not t.startswith("#") else t for t in p["hashtags"])
            lines.append(f"\nHashtags:\n{tags}")
        if p.get("first_comment"):
            lines.append(f"\nBình luận ghim (First Comment):\n{p['first_comment']}")
        lines.append("\n" + "-" * 50 + "\n")
    return "\n".join(lines).strip() + "\n"


def export_package(
    sb: Storyboard,
    out_dir: Path,
    dest: Path | None = None,
    *,
    zip_archive: bool = False,
    include_contact_sheet: bool = True,
    log=print,
) -> Path:
    """Package finished film deliverables.

    Returns the path to the exported directory or the .zip file.
    """
    final_mp4 = out_dir / "final.mp4"
    if not final_mp4.exists():
        raise ExportError(f"Chưa có file phim hoàn chỉnh ({final_mp4}). Hãy chạy 'filmmaker auto' hoặc 'filmmaker assemble' trước.")

    title = sb.title or out_dir.name
    slug = _safe_filename(title, default=out_dir.name)

    base_export_dir = dest or (out_dir / "exports")
    target_dir = base_export_dir / slug
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    log(f"Đang đóng gói phim '{title}' vào: {target_dir}")

    exported_files: list[str] = []

    # 1. Video file
    video_dest = target_dir / f"{slug}.mp4"
    shutil.copy2(final_mp4, video_dest)
    exported_files.append(video_dest.name)

    # 2. Subtitles (.srt)
    srt_files = list(out_dir.glob("*.srt"))
    for srt in srt_files:
        srt_name = f"{slug}.srt" if srt.name == "final.srt" else f"{slug}_{srt.name}"
        dest_srt = target_dir / srt_name
        shutil.copy2(srt, dest_srt)
        exported_files.append(dest_srt.name)

    # 3. Cover & Thumbnails
    cover = out_dir / "cover.png"
    if cover.exists():
        dest_cover = target_dir / f"{slug}_cover.png"
        shutil.copy2(cover, dest_cover)
        exported_files.append(dest_cover.name)

    # Optional contact sheet
    if include_contact_sheet:
        contact_sheet = out_dir / "reviews" / "contact_sheet.jpg"
        if contact_sheet.exists():
            dest_cs = target_dir / f"{slug}_contact_sheet.jpg"
            shutil.copy2(contact_sheet, dest_cs)
            exported_files.append(dest_cs.name)

    # 4. Social Posts
    social_data = social.load(out_dir)
    if social_data:
        social_txt = _format_social_text(social_data)
        if social_txt:
            dest_social = target_dir / "social_posts.txt"
            dest_social.write_text(social_txt, encoding="utf-8")
            exported_files.append(dest_social.name)

            dest_social_json = target_dir / "social.json"
            dest_social_json.write_text(json.dumps(social_data, ensure_ascii=False, indent=2), encoding="utf-8")
            exported_files.append(dest_social_json.name)

    # 5. Manifest & Readme
    manifest = {
        "title": title,
        "slug": slug,
        "aspect": sb.aspect,
        "scenes_count": len(sb.scenes),
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": exported_files,
    }
    (target_dir / "release_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    readme = [
        f"DỰ ÁN PHIM: {title}",
        "=" * 40,
        f"- Tỉ lệ khung hình: {sb.aspect}",
        f"- Số cảnh: {len(sb.scenes)}",
        f"- Ngày xuất gói: {manifest['exported_at']}",
        "",
        "DANH SÁCH FILE PHÁT HÀNH:",
    ]
    for fn in exported_files:
        readme.append(f"  • {fn}")
    readme.append("\nChúc bạn đăng tải phim thành công trên các nền tảng!")
    (target_dir / "README.txt").write_text("\n".join(readme), encoding="utf-8")

    # 6. Zip archive if requested
    if zip_archive:
        zip_path = base_export_dir / f"{slug}_release.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in target_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, arcname=f.relative_to(target_dir))
        log(f"Đã tạo file nén phát hành: {zip_path}")
        return zip_path

    return target_dir
