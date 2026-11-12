"""Tests for the release export and publishing packager (filmmaker export)."""

import json
import zipfile
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from starlette.testclient import TestClient

from filmmaker import export, social
from filmmaker.cli import main
from filmmaker.export import ExportError, _safe_filename, export_package
from filmmaker.storyboard import Storyboard
from filmmaker.web.server import create_app


SB_DATA = {
    "title": "Gói Xôi Của Bà Tư",
    "aspect": "9:16",
    "characters": [{"name": "Bà Tư", "description": "old Vietnamese woman, gentle smile"}],
    "scenes": [
        {"id": "s01", "mode": "keyframe", "prompt": "Bà Tư mở gói xôi bốc khói", "duration": 8, "narration": "Gói xôi sáng sớm"},
        {"id": "s02", "mode": "t2v", "prompt": "Cậu bé mỉm cười nhận xôi", "duration": 8, "narration": "Nụ cười ấm áp"},
    ],
}

SOCIAL_DATA = {
    "updated_at": "2026-11-11T12:00:00Z",
    "posts": [
        {
            "platform": "tiktok",
            "language": "Vietnamese",
            "title": "",
            "hook": "Gói xôi 10k và câu chuyện ấm lòng",
            "caption": "Mỗi sáng góc phố ấy lại thoang thoảng mùi xôi nếp...",
            "hashtags": ["goixoi", "chuyenpho", "drama"],
            "first_comment": "Bạn thích ăn xôi gì nhất?",
        }
    ],
}


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "goi-xoi"
    out = d / "output" / "storyboard"
    (out / "reviews").mkdir(parents=True)
    (d / "storyboard.yaml").write_text(yaml.safe_dump(SB_DATA, allow_unicode=True), encoding="utf-8")
    (out / "final.mp4").write_bytes(b"FAKE_MP4_CONTENT")
    (out / "final.srt").write_text("1\n00:00:01,000 --> 00:00:04,000\nGói xôi sáng sớm\n", encoding="utf-8")
    (out / "cover.png").write_bytes(b"FAKE_COVER_PNG")
    (out / "social.json").write_text(json.dumps(SOCIAL_DATA, ensure_ascii=False), encoding="utf-8")
    (out / "reviews" / "contact_sheet.jpg").write_bytes(b"FAKE_CONTACT_SHEET")
    return d, out


def test_safe_filename():
    assert _safe_filename("Phim: Cơn Mưa / Sài Gòn?") == "Phim-Cơn-Mưa-Sài-Gòn"
    assert _safe_filename("   ") == "film"
    assert _safe_filename("Normal Title") == "Normal-Title"


def test_export_fails_without_final_mp4(tmp_path):
    d = tmp_path / "film"
    out = d / "output" / "film"
    out.mkdir(parents=True)
    sb = Storyboard.model_validate(SB_DATA)
    with pytest.raises(ExportError, match="Chưa có file phim hoàn chỉnh"):
        export_package(sb, out)


def test_export_package_directory(project_dir):
    d, out = project_dir
    sb = Storyboard.model_validate(SB_DATA)

    export_path = export_package(sb, out, zip_archive=False)
    assert export_path.is_dir()
    assert (export_path / "Gói-Xôi-Của-Bà-Tư.mp4").exists()
    assert (export_path / "Gói-Xôi-Của-Bà-Tư.srt").exists()
    assert (export_path / "Gói-Xôi-Của-Bà-Tư_cover.png").exists()
    assert (export_path / "Gói-Xôi-Của-Bà-Tư_contact_sheet.jpg").exists()
    assert (export_path / "social_posts.txt").exists()
    assert (export_path / "social.json").exists()
    assert (export_path / "release_manifest.json").exists()
    assert (export_path / "README.txt").exists()

    # Check manifest content
    manifest = json.loads((export_path / "release_manifest.json").read_text(encoding="utf-8"))
    assert manifest["title"] == "Gói Xôi Của Bà Tư"
    assert manifest["aspect"] == "9:16"
    assert manifest["scenes_count"] == 2

    # Check social formatted text
    social_text = (export_path / "social_posts.txt").read_text(encoding="utf-8")
    assert "TIKTOK" in social_text
    assert "Gói xôi 10k và câu chuyện ấm lòng" in social_text
    assert "#goixoi" in social_text


def test_export_package_zip(project_dir):
    d, out = project_dir
    sb = Storyboard.model_validate(SB_DATA)

    zip_file = export_package(sb, out, zip_archive=True)
    assert zip_file.is_file()
    assert zip_file.name.endswith(".zip")

    with zipfile.ZipFile(zip_file, "r") as zf:
        names = zf.namelist()
        assert "Gói-Xôi-Của-Bà-Tư.mp4" in names
        assert "Gói-Xôi-Của-Bà-Tư.srt" in names
        assert "Gói-Xôi-Của-Bà-Tư_cover.png" in names
        assert "social_posts.txt" in names
        assert "release_manifest.json" in names


def test_cli_export(project_dir):
    d, out = project_dir
    sb_yaml = d / "storyboard.yaml"

    runner = CliRunner()
    res = runner.invoke(main, ["export", str(sb_yaml), "--out", str(out), "--zip"])
    assert res.exit_code == 0
    assert "✔ Đã đóng gói:" in res.output
    assert (out / "exports" / "Gói-Xôi-Của-Bà-Tư_release.zip").exists()


def test_web_export_endpoints(project_dir):
    d, out = project_dir
    ws = d.parent
    token = "test-token"
    client = TestClient(create_app(ws, token=token))
    pid = d.name

    # 1. Post export zip
    res = client.post(f"/api/projects/{pid}/export?zip_archive=true", headers={"x-token": token})
    assert res.status_code == 200
    data = res.json()
    assert data["is_zip"] is True
    assert "download_url" in data

    # 2. Download zip
    dl = client.get(f"/api/projects/{pid}/export/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/zip"
    assert len(dl.content) > 0


# --- Auto-Publish tests ------------------------------------------------------


def test_build_metadata_for_platforms(project_dir):
    from filmmaker.publisher import build_metadata

    _, out = project_dir
    sb = Storyboard.model_validate(SB_DATA)

    yt_meta = build_metadata(sb, out, "youtube")
    assert "#Shorts" in yt_meta["title"]
    assert yt_meta["is_short"] is True
    assert "Shorts" in yt_meta["tags"]
    assert "#goixoi" in yt_meta["description"]

    fb_meta = build_metadata(sb, out, "facebook_reel")
    assert "Gói xôi 10k và câu chuyện ấm lòng" in fb_meta["title"]
    assert "Mỗi sáng góc phố ấy" in fb_meta["description"]
    assert "#Reels" in fb_meta["description"]


def test_cli_publish_dry_run(project_dir):
    d, out = project_dir
    runner = CliRunner()
    res = runner.invoke(main, ["publish", str(d / "storyboard.yaml"), "--out", str(out), "--dry-run"])
    assert res.exit_code == 0
    assert "[DRY RUN] Sẽ đăng lên Facebook Reels:" in res.output
    assert "[DRY RUN] Sẽ đăng lên YouTube (Shorts / Video):" in res.output


def test_web_publish_endpoints(project_dir):
    import time

    d, out = project_dir
    ws = d.parent
    client = TestClient(create_app(ws))
    hdr = {"x-token": client.app.state.token}
    pid = d.name

    r0 = client.get(f"/api/projects/{pid}/publish")
    assert r0.status_code == 200
    assert "facebook_reel" in r0.json()["platforms"]
    assert "youtube" in r0.json()["platforms"]

    r_pub = client.post(
        f"/api/projects/{pid}/publish",
        headers=hdr,
        json={"platforms": ["facebook_reel", "youtube"], "dry_run": True},
    )
    assert r_pub.status_code == 200
    job_id = r_pub.json()["id"]
    for _ in range(30):
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            break
        time.sleep(0.05)
    assert j["status"] == "done"
    assert "facebook_reel" in j["result"]["published"]
    assert "youtube" in j["result"]["published"]

