"""Offline tests for `filmmaker.crawler`, CLI `filmmaker crawl`, `/api/crawls` web endpoints, and Crawl UI JS."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from filmmaker import crawler as cr
from filmmaker import planner
from filmmaker import video_analysis as va

APP_JS = Path(__file__).resolve().parent.parent / "filmmaker" / "web" / "static" / "app.js"


# --- URL parsing, normalization & SSRF safety ---------------------------------


def test_parse_sources_multiline_bare_domains_and_dedup():
    raw = """
    # Danh sách kênh mẫu
    1. youtube.com/@StudioAlpha?si=tracking123
    - https://www.youtube.com/@StudioAlpha/
    • m.tiktok.com/@creator_vn?is_from_webapp=1&sender_device=pc
    https://www.tiktok.com/@creator_vn, facebook.com/PagePhimHay?fbclid=IwAR999
    """
    urls = cr.parse_sources(raw)
    assert urls == [
        "https://youtube.com/@StudioAlpha?si=tracking123",
        "https://m.tiktok.com/@creator_vn?is_from_webapp=1&sender_device=pc",
        "https://facebook.com/PagePhimHay?fbclid=IwAR999",
    ]


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   \n # only comment \n",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://localhost:8000/videos",
        "http://127.0.0.1/secret",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.20/channel",
        "http://[::1]/channel",
        "http://internal.local/page",
        "-o /tmp/pwn",
    ],
)
def test_parse_sources_rejects_unsafe_or_empty(bad):
    with pytest.raises(cr.CrawlError):
        cr.parse_sources(bad)


def test_validate_browser():
    assert cr.validate_browser(None) is None
    assert cr.validate_browser("") is None
    assert cr.validate_browser("none") is None
    assert cr.validate_browser("Chrome") == "chrome"
    assert cr.validate_browser("safari") == "safari"
    with pytest.raises(cr.CrawlError):
        cr.validate_browser("chrome:/tmp/evil-profile")
    with pytest.raises(cr.CrawlError):
        cr.validate_browser("opera")


# --- Platform classification & canonical deduplication ------------------------


def test_classify_source_youtube_tiktok_facebook():
    yt = cr.classify_source("https://www.youtube.com/@PhimNganAI")
    assert yt["platform"] == "youtube" and yt["kind"] == "channel"
    assert yt["targets"] == [
        "https://www.youtube.com/@PhimNganAI/videos",
        "https://www.youtube.com/@PhimNganAI/shorts",
    ]

    yt_shorts_first = cr.classify_source("https://www.youtube.com/channel/UC123456789", shorts_only=True)
    assert yt_shorts_first["targets"] == [
        "https://www.youtube.com/channel/UC123456789/shorts",
        "https://www.youtube.com/channel/UC123456789/videos",
    ]

    yt_tab = cr.classify_source("https://www.youtube.com/@PhimNganAI/shorts")
    assert yt_tab["kind"] == "tab" and yt_tab["targets"] == ["https://www.youtube.com/@PhimNganAI/shorts"]

    yt_single = cr.classify_source("https://www.youtube.com/shorts/abcDEF12345")
    assert yt_single["kind"] == "single"

    tt = cr.classify_source("https://m.tiktok.com/@cinemavn?lang=vi")
    assert tt["platform"] == "tiktok" and tt["kind"] == "channel"
    assert tt["normalized_url"] == "https://www.tiktok.com/@cinemavn"
    assert tt["targets"] == ["https://www.tiktok.com/@cinemavn"]

    tt_single = cr.classify_source("https://www.tiktok.com/@cinemavn/video/7350000000000000001")
    assert tt_single["kind"] == "single"

    fb_page = cr.classify_source("https://www.facebook.com/BepNhaLan")
    assert fb_page["platform"] == "facebook" and fb_page["kind"] == "page"
    assert fb_page["targets"] == [
        "https://www.facebook.com/BepNhaLan/videos",
        "https://www.facebook.com/BepNhaLan/reels",
    ]

    fb_profile = cr.classify_source("https://www.facebook.com/profile.php?id=1000888999")
    assert fb_profile["kind"] == "page"
    assert fb_profile["targets"] == [
        "https://www.facebook.com/profile.php?id=1000888999&sk=reels_tab",
        "https://www.facebook.com/profile.php?id=1000888999&sk=videos",
    ]

    fb_reel = cr.classify_source("https://www.facebook.com/reel/123456789012")
    assert fb_reel["kind"] == "single"


def test_canonical_video_key_matches_equivalent_urls():
    assert (
        cr.canonical_video_key("https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share")
        == cr.canonical_video_key("https://youtu.be/dQw4w9WgXcQ")
        == cr.canonical_video_key("https://www.youtube.com/shorts/dQw4w9WgXcQ")
        == "youtube:dQw4w9WgXcQ"
    )
    assert (
        cr.canonical_video_key("https://www.facebook.com/reel/987654321098")
        == cr.canonical_video_key("https://www.facebook.com/watch/?v=987654321098")
        == cr.canonical_video_key("https://m.facebook.com/BepNhaLan/videos/mon-ngon/987654321098/")
        == "facebook:987654321098"
    )
    assert (
        cr.canonical_video_key("https://www.tiktok.com/@a/video/7399999999999999999")
        == cr.canonical_video_key("https://m.tiktok.com/v/video/7399999999999999999")
        == "tiktok:7399999999999999999"
    )


# --- Human view counts & Facebook title parsing -------------------------------


def test_parse_count_and_vietnamese_word_boundaries():
    assert cr.parse_count(42_500) == 42_500
    assert cr.parse_count("9.7K views") == 9_700
    assert cr.parse_count("1,2 Tr lượt xem") == 1_200_000
    assert cr.parse_count("2.5M") == 2_500_000
    assert cr.parse_count("1,5 tỷ lượt xem") == 1_500_000_000
    assert cr.parse_count("45 nghìn lượt xem") == 45_000
    assert cr.parse_count("1.234.567 lượt xem") == 1_234_567
    assert cr.parse_count("1,234,567 views") == 1_234_567
    assert cr.parse_count("No views") == 0
    assert cr.parse_count("0 lượt xem") == 0
    # Must NOT treat 'n' in Vietnamese words 'năm' / 'ngày' as 'N' (nghìn = 1000)
    assert cr.parse_count("5 năm trước") == 5
    assert cr.parse_count("10 ngày trước") == 10
    assert cr.fmt_views_vi(None) == "Chưa rõ"
    assert cr.fmt_views_vi(850) == "850"
    assert cr.fmt_views_vi(9_700) == "9,7 N"
    assert cr.fmt_views_vi(1_200_000) == "1,2 Tr"
    assert cr.fmt_views_vi(2_000_000_000) == "2 tỷ"


def test_parse_facebook_title():
    parsed = cr.parse_facebook_title(
        "9.7K views · 352 reactions | Bí quyết pha cà phê muối chuẩn vị Huế | Bếp Nhà Lan",
        "9.7K views · 352 reactions | Bếp Nhà Lan",
    )
    assert parsed["view_count"] == 9_700
    assert parsed["like_count"] == 352
    assert parsed["title"] == "Bí quyết pha cà phê muối chuẩn vị Huế"
    assert parsed["uploader"] == "Bếp Nhà Lan"


# --- Facebook HTML / Relay JSON discovery & redirect guard --------------------


def test_parse_facebook_page_html_relay_and_anchors():
    sample_html = """
    <html>
      <head>
        <meta property="og:title" content="Bếp Nhà Lan | Videos" />
      </head>
      <body>
        <script type="application/json">
        {"require":[["ScheduledServerJS",null,null,[{"__bbox":{"result":{"data":{"node":{
          "__typename":"FBReelsVideo",
          "id":"111222333444",
          "play_count":480000,
          "playable_duration_in_ms":28500,
          "publish_time":1710000000,
          "title":{"text":"Cà phê trứng 30 giây"},
          "owner":{"name":"Bếp Nhà Lan"},
          "reaction_count":{"count":9200},
          "preferred_thumbnail":{"image":{"uri":"https://scontent.xx.fbcdn.net/v/thumb1.jpg"}},
          "permalink_url":"/reel/111222333444"
        }}}}}]]]}
        </script>
        <a href="/reel/555666777888/" aria-label="1,4 Tr lượt xem">
          <img src="https://scontent.xx.fbcdn.net/v/thumb2.jpg" />
          <span>1,4 Tr</span>
        </a>
        <a href="/BepNhaLan/videos/999888777666/">Video chưa hiện view trên lưới</a>
      </body>
    </html>
    """
    page_name, items = cr.parse_facebook_page_html(sample_html, "https://www.facebook.com/BepNhaLan/reels")
    assert page_name == "Bếp Nhà Lan"
    by_id = {it["id"]: it for it in items}
    assert set(by_id) == {"111222333444", "555666777888", "999888777666"}
    assert by_id["111222333444"]["view_count"] == 480_000
    assert by_id["111222333444"]["duration"] == 28.5
    assert by_id["111222333444"]["title"] == "Cà phê trứng 30 giây"
    assert by_id["555666777888"]["view_count"] == 1_400_000
    assert by_id["555666777888"]["thumbnail"] == "https://scontent.xx.fbcdn.net/v/thumb2.jpg"
    assert by_id["999888777666"].get("view_count") is None


def test_facebook_redirect_handler_blocks_offsite_and_private_ips():
    handler = cr._FacebookRedirectHandler()
    req = urllib.request.Request("https://www.facebook.com/BepNhaLan/reels")
    ok_req = handler.redirect_request(req, None, 302, "Found", {}, "https://m.facebook.com/BepNhaLan/reels")
    assert ok_req is not None and ok_req.full_url == "https://m.facebook.com/BepNhaLan/reels"
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example.com/steal")
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1:8080/admin")


# --- End-to-end multi-platform crawl (offline fake yt-dlp + FB HTML) ----------


def _install_fake_crawler_io(monkeypatch):
    """Simulate YouTube (videos + shorts), TikTok (@channel), and Facebook (HTML + yt-dlp enrich) offline."""
    monkeypatch.setattr(va, "ytdlp_command", lambda: ["fake-yt-dlp"])
    monkeypatch.setattr(cr, "playwright_python", lambda: None)

    fb_pages = {
        "https://www.facebook.com/BepNhaLan/videos": """
            <html><head><title>Bếp Nhà Lan | Facebook</title></head><body>
              <a href="/BepNhaLan/videos/900000000001/"><span>Bún bò Huế</span></a>
            </body></html>
        """,
        "https://www.facebook.com/BepNhaLan/reels": """
            <html><head><title>Bếp Nhà Lan | Reels</title></head><body>
              <a href="/reel/900000000002"><span>2,1 Tr</span></a>
            </body></html>
        """,
    }

    def fake_fetch_fb(url: str, **kw) -> str:
        if "BrokenPage" in url:
            raise urllib.error.URLError("HTTP 404")
        return fb_pages.get(url, "<html></html>")

    monkeypatch.setattr(cr, "_fetch_fb_html", fake_fetch_fb)

    def fake_run(cmd: list[str], *, timeout: float, should_stop=lambda: False, env=None):
        if should_stop():
            raise cr.CrawlStopped()
        sep = cmd.index("--")
        targets = cmd[sep + 1:]
        is_flat = "--flat-playlist" in cmd
        lines: list[dict[str, Any]] = []

        if is_flat:
            t = targets[0]
            if t == "https://www.youtube.com/@StudioA/videos":
                lines = [
                    {
                        "id": "ytLong00001",
                        "webpage_url": "https://www.youtube.com/watch?v=ytLong00001",
                        "title": "Phim ngắn AI 10 phút",
                        "channel": "Studio A",
                        "view_count": 320_000,
                        "like_count": 12_000,
                        "duration": 600,
                        "timestamp": 1715000000,
                        "thumbnails": [{"url": "https://i.ytimg.com/vi/ytLong00001/hqdefault.jpg", "width": 480, "height": 360}],
                    },
                    # Duplicate short also listed in /videos with slightly older view_count
                    {
                        "id": "ytShort0002",
                        "webpage_url": "https://www.youtube.com/watch?v=ytShort0002",
                        "title": "Cảnh rượt đuổi 45s",
                        "channel": "Studio A",
                        "view_count": 1_750_000,
                        "duration": 45,
                    },
                ]
            elif t == "https://www.youtube.com/@StudioA/shorts":
                lines = [
                    {
                        "id": "ytShort0002",
                        "webpage_url": "https://www.youtube.com/shorts/ytShort0002",
                        "title": "Cảnh rượt đuổi 45s",
                        "channel": "Studio A",
                        "view_count": 1_800_000,
                        "like_count": 95_000,
                        "duration": 45,
                        "timestamp": 1716000000,
                        "thumbnails": [{"url": "https://i.ytimg.com/vi/ytShort0002/oar2.jpg", "width": 720, "height": 1280}],
                    }
                ]
            elif t == "https://www.tiktok.com/@CreatorB":
                lines = [
                    {
                        "id": "7300000000000000001",
                        "webpage_url": "https://www.tiktok.com/@CreatorB/video/7300000000000000001",
                        "title": "Biến hình cổ trang #xuhuong",
                        "uploader": "Creator B",
                        "view_count": 3_400_000,
                        "like_count": 280_000,
                        "comment_count": 4_100,
                        "duration": 22.5,
                        "timestamp": 1717000000,
                        "thumbnails": [
                            {"id": "dynamic_cover", "url": "https://p16.tiktokcdn.com/dyn.webp", "width": 720, "height": 1280},
                            {"id": "cover", "url": "https://p16.tiktokcdn.com/static.jpg", "width": 720, "height": 1280},
                        ],
                    },
                    {
                        "id": "7300000000000000002",
                        "webpage_url": "https://www.tiktok.com/@CreatorB/video/7300000000000000002",
                        "title": "Hậu trường quay đêm",
                        "uploader": "Creator B",
                        "view_count": 150_000,
                        "duration": 35.0,
                        "timestamp": 1714000000,
                    },
                ]
            else:
                return subprocess.CompletedProcess(cmd, 1, "", "ERROR: channel not found")
        else:
            # Batch info enrichment for Facebook videos
            for u in targets:
                if "900000000001" in u:
                    lines.append({
                        "id": "900000000001",
                        "webpage_url": "https://www.facebook.com/BepNhaLan/videos/900000000001/",
                        "title": "850K views · 14K reactions | Bún bò Huế nấu chậm 8 tiếng | Bếp Nhà Lan",
                        "uploader": "Bếp Nhà Lan",
                        "duration": 95.0,
                        "timestamp": 1713000000,
                        "thumbnail": "https://scontent.xx.fbcdn.net/v/bunbo.jpg",
                    })
                elif "900000000002" in u:
                    lines.append({
                        "id": "900000000002",
                        "webpage_url": "https://www.facebook.com/reel/900000000002",
                        "title": "2.1M views · 65K reactions | Cà phê muối kem béo | Bếp Nhà Lan",
                        "uploader": "Bếp Nhà Lan",
                        "duration": 31.0,
                        "timestamp": 1718000000,
                        "thumbnail": "https://scontent.xx.fbcdn.net/v/cafemuoi.jpg",
                    })

        stdout = "\n".join(json.dumps(row, ensure_ascii=False) for row in lines)
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr(cr, "_run_interruptible", fake_run)


def test_crawl_ranks_across_youtube_tiktok_facebook_and_saves(tmp_path, monkeypatch):
    _install_fake_crawler_io(monkeypatch)
    out_dir = tmp_path / ".crawls" / "20261114-multi"
    logs: list[str] = []

    data = cr.crawl(
        [
            "https://www.youtube.com/@StudioA",
            "https://www.tiktok.com/@CreatorB",
            "https://www.facebook.com/BepNhaLan",
            "https://www.facebook.com/BrokenPage",  # partial failure should not abort healthy sources
        ],
        out_dir,
        top_n=4,
        log=logs.append,
    )

    # Global top 4 across all 3 platforms, strictly descending by view_count:
    # 1. TikTok 3.4M -> 2. FB Reel 2.1M -> 3. YT Short 1.8M (deduped!) -> 4. FB Video 850K
    assert [v["view_count"] for v in data["videos"]] == [3_400_000, 2_100_000, 1_800_000, 850_000]
    assert [v["rank"] for v in data["videos"]] == [1, 2, 3, 4]
    assert [v["platform"] for v in data["videos"]] == ["tiktok", "facebook", "youtube", "facebook"]

    # Static cover preferred over dynamic_cover on TikTok
    assert data["videos"][0]["thumbnail"] == "https://p16.tiktokcdn.com/static.jpg"
    # Facebook title & views parsed cleanly from yt-dlp enrichment
    assert data["videos"][1]["title"] == "Cà phê muối kem béo"
    assert data["videos"][1]["channel"] == "Bếp Nhà Lan"
    # YouTube Short deduplicated across /videos and /shorts, keeping /shorts/ URL and higher view_count
    assert data["videos"][2]["url"] == "https://www.youtube.com/shorts/ytShort0002"
    assert data["videos"][3]["title"] == "Bún bò Huế nấu chậm 8 tiếng"

    statuses = {s["normalized_url"]: s["status"] for s in data["sources"]}
    assert statuses["https://www.facebook.com/BrokenPage"] == "error"
    assert data["total_scanned"] == 6

    # Saved files on disk
    assert (out_dir / "crawl.json").is_file()
    assert "Cà phê muối kem béo" in (out_dir / "report.md").read_text(encoding="utf-8")
    urls_lines = (out_dir / "urls.txt").read_text(encoding="utf-8").strip().splitlines()
    assert len(urls_lines) == 4 and urls_lines[0] == "https://www.tiktok.com/@CreatorB/video/7300000000000000001"

    listed = cr.list_crawls(tmp_path / ".crawls")
    assert len(listed) == 1
    assert listed[0]["id"] == "20261114-multi"
    assert listed[0]["videos_count"] == 4
    assert listed[0]["top_views"] == 3_400_000


def test_crawl_per_source_and_shorts_only_filters(monkeypatch):
    _install_fake_crawler_io(monkeypatch)
    # per_source=True with top_n=1 picks the #1 video from each of the 3 channels
    per = cr.crawl(
        ["https://www.youtube.com/@StudioA", "https://www.tiktok.com/@CreatorB", "https://www.facebook.com/BepNhaLan"],
        top_n=1,
        per_source=True,
        log=lambda _: None,
    )
    assert len(per["videos"]) == 3
    assert {v["platform"] for v in per["videos"]} == {"tiktok", "facebook", "youtube"}
    assert [v["view_count"] for v in per["videos"]] == [3_400_000, 2_100_000, 1_800_000]

    # shorts_only=True drops the 600s YouTube video
    shorts = cr.crawl(["https://www.youtube.com/@StudioA"], top_n=10, shorts_only=True, log=lambda _: None)
    assert [v["id"] for v in shorts["videos"]] == ["ytShort0002"]


def test_crawl_stop_raises_cleanly(monkeypatch):
    _install_fake_crawler_io(monkeypatch)
    with pytest.raises(cr.CrawlStopped):
        cr.crawl(["https://www.youtube.com/@StudioA"], should_stop=lambda: True, log=lambda _: None)


# --- Bridge to video_analysis (link, pre-download, batch analyze) -------------


def test_download_link_and_batch_analyze_crawled(tmp_path, monkeypatch):
    _install_fake_crawler_io(monkeypatch)
    crawl_dir = tmp_path / ".crawls" / "c1"
    analyses_dir = tmp_path / ".analyses"
    data = cr.crawl(
        ["https://www.youtube.com/@StudioA", "https://www.tiktok.com/@CreatorB"],
        crawl_dir,
        top_n=2,
        log=lambda _: None,
    )
    assert [v["id"] for v in data["videos"]] == ["7300000000000000001", "ytShort0002"]

    # 1. Pre-download top videos
    fetched_urls: list[str] = []

    def fake_fetch(source: str, folder: Path, **kw):
        fetched_urls.append(source)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "source.mp4").write_bytes(b"mp4bytes")
        return {"file": "source.mp4", "url": source, "title": "Downloaded", "subtitles": "Xin chào"}

    monkeypatch.setattr(va, "fetch", fake_fetch)
    data = cr.download_top_videos(data, crawl_dir, log=lambda _: None)
    assert len(fetched_urls) == 2
    assert data["videos"][0]["downloaded_file"] == "videos/01/source.mp4"
    assert (crawl_dir / "videos" / "01" / "source.mp4").is_file()

    # 2. Batch analyze reuses pre-downloaded local MP4 while preserving original web URL, title, channel, and subs
    analyze_calls: list[tuple[str, bool]] = []

    def fake_va_analyze(source: str, folder: Path, **kw):
        analyze_calls.append((source, bool(kw.get("allow_local"))))
        folder.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "id": folder.name,
            "created": time.time(),
            "source": {"file": "source.mp4", "url": None if kw.get("allow_local") else source, "title": "source"},
            "measured": {"duration": 22.5, "aspect": "9:16", "shots": [], "stats": {"shots": 3}},
            "ai": {"title_vi": f"Bản phân tích {len(analyze_calls)}"},
            "sheets": [],
        }
        (folder / "analysis.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        (folder / "report.md").write_text("# Report\n", encoding="utf-8")
        return payload

    monkeypatch.setattr(va, "analyze", fake_va_analyze)
    res = cr.analyze_crawled(data["videos"], analyses_dir, crawl_dir=crawl_dir, use_rea=False, log=lambda _: None)
    assert len(res["analyses"]) == 2 and res["errors"] == [] and res["skipped"] == []
    # Both calls used the local pre-downloaded MP4 path (allow_local=True) instead of hitting network again
    assert all(allow_local is True and src.endswith("source.mp4") for src, allow_local in analyze_calls)

    # Web metadata & subtitles restored into analysis.json so `list_analyses` and `link_analyses` match by canonical URL!
    first_saved = va.load(analyses_dir / res["analyses"][0])
    assert first_saved["source"]["url"] == "https://www.tiktok.com/@CreatorB/video/7300000000000000001"
    assert first_saved["source"]["title"] == "Biến hình cổ trang #xuhuong"
    assert first_saved["source"]["uploader"] == "Creator B"
    assert first_saved["subtitles"] == "Xin chào"

    linked = cr.link_analyses(cr.load(crawl_dir), analyses_dir)
    assert linked["analyzed_count"] == 2
    assert [v["analysis_id"] for v in linked["videos"]] == res["analyses"]

    # Second run without force=True skips both already-analyzed videos
    res_again = cr.analyze_crawled(data["videos"], analyses_dir, crawl_dir=crawl_dir, force=False, log=lambda _: None)
    assert len(res_again["skipped"]) == 2
    assert len(analyze_calls) == 2  # va.analyze was not invoked again


# --- Web API (/api/crawls) ----------------------------------------------------


fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filmmaker.web import server  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(planner, "claude_code_status", lambda: {"available": False, "logged_in": False})
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"auto_backup": False, "planner": "api", "anthropic_api_key": "x"}))
    monkeypatch.setattr(server, "SETTINGS_PATH", settings)
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(settings))
    c = TestClient(server.create_app(tmp_path / "ws", token="t0k"))
    c.headers["X-Token"] = "t0k"
    c.ws = tmp_path / "ws"
    return c


def _wait_job(c, job, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.03)
    raise AssertionError("job did not finish in time")


def test_web_crawls_lifecycle_and_security(client, monkeypatch):
    _install_fake_crawler_io(monkeypatch)
    c = client

    # /api/env preserves video_analysis keys and exposes crawler capabilities
    env = c.get("/api/env").json()
    assert set(env["video_analysis"]) == {"ytdlp", "rea_video"}
    assert env["crawler"]["ytdlp"] is True
    assert "chrome" in env["crawler"]["browsers"]

    # CSRF token required on POST
    unauth = TestClient(c.app)
    assert unauth.post("/api/crawls", json={"urls": "https://www.youtube.com/@StudioA"}).status_code == 403

    # Validation guards (SSRF, bad browser, bad top_n)
    assert c.post("/api/crawls", json={"urls": "http://127.0.0.1:9000/x"}).status_code == 400
    assert c.post("/api/crawls", json={"urls": "https://www.youtube.com/@StudioA", "cookies_from_browser": "evil"}).status_code == 400
    assert c.post("/api/crawls", json={"urls": "https://www.youtube.com/@StudioA", "top_n": 0}).status_code == 400

    # Start multi-platform crawl job
    start = c.post(
        "/api/crawls",
        json={
            "urls": "https://www.youtube.com/@StudioA\nhttps://www.tiktok.com/@CreatorB\nhttps://www.facebook.com/BepNhaLan",
            "top_n": 3,
        },
    )
    assert start.status_code == 200
    j = _wait_job(c, start.json())
    assert j["status"] == "done" and j["kind"] == "crawl", j["lines"]
    cid = j["result"]["crawl"]
    assert j["result"]["count"] == 3

    # .crawls is hidden from projects list
    assert c.get("/api/projects").json() == []

    # List & get crawl detail
    items = c.get("/api/crawls").json()
    assert [it["id"] for it in items] == [cid]
    assert items[0]["videos_count"] == 3 and items[0]["top_views"] == 3_400_000

    detail = c.get(f"/api/crawls/{cid}").json()
    assert [v["view_count"] for v in detail["videos"]] == [3_400_000, 2_100_000, 1_800_000]
    assert c.get(detail["report_url"]).status_code == 200
    assert "3,4 Tr" in c.get(detail["report_url"]).text
    assert len(c.get(detail["urls_file_url"]).text.strip().splitlines()) == 3

    # Path traversal blocked
    assert c.get(f"/api/crawls/{cid}/file/../../settings.json").status_code == 404
    assert c.get(f"/api/crawls/{cid}/file/%2e%2e/%2e%2e/settings.json").status_code == 404
    assert c.get("/api/crawls/..%2f..").status_code == 404

    # Analyze top 2 crawled videos directly from crawl endpoint
    def fake_va_analyze(source: str, folder: Path, **kw):
        folder.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "id": folder.name,
            "created": time.time(),
            "source": {"file": "source.mp4", "url": source, "title": "T"},
            "measured": {"duration": 30.0, "aspect": "9:16", "shots": [], "stats": {"shots": 4}},
            "ai": {"title_vi": "Phân tích từ crawl"},
            "sheets": [],
        }
        (folder / "analysis.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload

    monkeypatch.setattr(va, "analyze", fake_va_analyze)
    top2_urls = [v["url"] for v in detail["videos"][:2]]
    aj = _wait_job(c, c.post(f"/api/crawls/{cid}/analyze", json={"urls": top2_urls, "use_rea": False}).json())
    assert aj["status"] == "done" and aj["kind"] == "crawl_analyze", aj["lines"]
    assert len(aj["result"]["analyses"]) == 2

    # Reloading crawl detail links the newly created analyses
    detail_after = c.get(f"/api/crawls/{cid}").json()
    assert detail_after["analyzed_count"] == 2
    assert detail_after["videos"][0]["analysis_id"] == aj["result"]["analyses"][0]
    assert detail_after["videos"][2]["analysis_id"] is None

    # Deleting crawl removes .crawls/<cid> while keeping .analyses intact
    assert c.delete(f"/api/crawls/{cid}").json() == {"ok": True}
    assert not (c.ws / ".crawls" / cid).exists()
    assert len(c.get("/api/analyses").json()) == 2


# --- CLI (`filmmaker crawl`) --------------------------------------------------


def test_cli_crawl_json_and_analyze(tmp_path, monkeypatch):
    from filmmaker.cli import main

    _install_fake_crawler_io(monkeypatch)

    def fake_va_analyze(source: str, folder: Path, **kw):
        folder.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "id": folder.name,
            "created": time.time(),
            "source": {"file": "source.mp4", "url": source, "title": "CLI"},
            "measured": {"duration": 25.0, "aspect": "9:16", "shots": [], "stats": {"shots": 2}},
            "ai": {"title_vi": "CLI Phân tích"},
            "sheets": [],
        }
        (folder / "analysis.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload

    monkeypatch.setattr(va, "analyze", fake_va_analyze)

    src_file = tmp_path / "sources.txt"
    src_file.write_text("https://www.youtube.com/@StudioA\nhttps://www.tiktok.com/@CreatorB\n", encoding="utf-8")
    out_dir = tmp_path / "crawl_out"
    ana_dir = tmp_path / "ana_out"

    res = CliRunner().invoke(
        main,
        [
            "crawl",
            "https://www.facebook.com/BepNhaLan",
            "-f",
            str(src_file),
            "-n",
            "2",
            "-o",
            str(out_dir),
            "--analyses-dir",
            str(ana_dir),
            "--analyze",
            "--no-rea",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    parsed = json.loads(res.stdout)
    assert [v["view_count"] for v in parsed["videos"]] == [3_400_000, 2_100_000]
    assert parsed["analyzed_count"] == 2
    assert all(v["analysis_id"] for v in parsed["videos"])
    assert (out_dir / "report.md").is_file() and (out_dir / "urls.txt").is_file()


# --- Frontend JS helpers & Crawl view under node ------------------------------


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_crawl_ui_js_syntax_and_rendering(tmp_path):
    chk = subprocess.run(["node", "--check", str(APP_JS)], capture_output=True, text=True)
    assert chk.returncode == 0, chk.stderr

    harness = r"""
    const fs = require("fs");
    const src = fs.readFileSync(process.argv[2], "utf8");
    const fn = (n) => {
      const one = src.match(new RegExp("^(?:async )?function " + n + "\\([^\\n]*\\}\\n", "m"));
      if (one) return one[0];
      const m = src.match(new RegExp("^(?:async )?function " + n + "\\(.*?^}\\n", "ms"));
      if (!m) throw new Error("missing function " + n);
      return m[0];
    };

    class NodeFake {
      constructor(tag) {
        this.tag = tag;
        this.attrs = {};
        this.style = {};
        this.children = [];
        this.listeners = {};
        this.className = "";
        this.value = "";
        this.checked = false;
        this.disabled = false;
      }
      setAttribute(k, v) { this.attrs[k] = String(v); }
      addEventListener(k, cb) { this.listeners[k] = cb; }
      append(...items) { this.children.push(...items); }
      replaceChildren(...items) { this.children = [...items]; }
      get text() {
        return this.children.map((c) => (typeof c === "string" ? c : c.text || "")).join("");
      }
      findAll(pred, acc = []) {
        if (pred(this)) acc.push(this);
        for (const c of this.children) if (c && typeof c === "object") c.findAll(pred, acc);
        return acc;
      }
    }

    const document = {
      createElement: (tag) => {
        if (tag === "template") {
          return { set innerHTML(v) { this._v = v; }, get content() { return { firstChild: new NodeFake("svg") }; } };
        }
        return new NodeFake(tag);
      },
      createTextNode: (s) => String(s),
    };
    const Node = NodeFake;
    const store = {};
    const localGet = (k, d = null) => (k in store ? store[k] : d);
    const localSet = (k, v) => { store[k] = v; };
    const ICONS = { trend: "", scan: "", alert: "", video: "", external: "", eye: "", refresh: "", download: "", copy: "", x: "", trash: "" };
    const S = {
      env: { crawler: { ytdlp: true, browsers: ["chrome", "safari"] }, video_analysis: { ytdlp: true, rea_video: false }, planner: "api" },
      job: null,
      crawlView: true,
      crawls: [{ id: "c1", title: "Top 2 video", videos_count: 2, top_views: 3400000, analyzed_count: 1, platforms: ["tiktok", "youtube"], created: Date.now() / 1000 - 120 }],
      crawl: {
        id: "c1",
        title: "Top 2 video · Creator B, Studio A",
        total_scanned: 12,
        created: Date.now() / 1000 - 120,
        report_url: "/api/crawls/c1/file/report.md",
        urls_file_url: "/api/crawls/c1/file/urls.txt",
        sources: [
          { input_url: "https://www.tiktok.com/@CreatorB", normalized_url: "https://www.tiktok.com/@CreatorB", platform: "tiktok", channel: "Creator B", scanned: 7, kept: 1, status: "ok", error: "" },
          { input_url: "https://www.youtube.com/@StudioA", normalized_url: "https://www.youtube.com/@StudioA", platform: "youtube", channel: "Studio A", scanned: 5, kept: 1, status: "ok", error: "" },
        ],
        videos: [
          { rank: 1, platform: "tiktok", url: "https://www.tiktok.com/@CreatorB/video/1", title: "Clip triệu view", channel: "Creator B", view_count: 3400000, duration: 22, is_short: true, thumbnail: "javascript:alert(1)", analysis_id: "ana-1" },
          { rank: 2, platform: "youtube", url: "https://www.youtube.com/shorts/abc", title: "Short thứ hai", channel: "Studio A", view_count: 9700, duration: 45, is_short: true, thumbnail: "https://i.ytimg.com/vi/abc/hq.jpg", analysis_id: null },
        ],
      },
    };
    const FREE = () => new NodeFake("span");
    const openAnalyzeVideo = () => {};
    const openAnalysis = () => {};
    const copyText = () => {};
    const toast = () => {};
    const confirmModal = async () => false;
    const api = async () => ({});
    const startJob = () => {};

    eval(fn("icon"));
    eval(fn("field"));
    eval(fn("h"));
    eval(fn("put"));
    eval(fn("fmtDur"));
    eval(fn("ago"));
    eval(fn("jobRunning"));
    eval(fn("safeExternalUrl"));
    eval(fn("fmtViewsVi"));
    eval(fn("platLabel"));
    eval(fn("fmtUploadDate"));
    eval(fn("closeCrawler"));
    eval(fn("renderCrawlResult"));
    eval(fn("renderCrawlHistory"));
    eval(fn("renderCrawler"));

    const root = renderCrawler();
    const imgs = root.findAll((n) => n.tag === "img");
    const buttons = root.findAll((n) => n.tag === "button").map((b) => b.text);

    console.log(JSON.stringify({
      safeOk: safeExternalUrl("https://www.tiktok.com/@a"),
      safeJs: safeExternalUrl("javascript:alert(1)"),
      safeData: safeExternalUrl("data:text/html,<script>"),
      views: [fmtViewsVi(null), fmtViewsVi(850), fmtViewsVi(9700), fmtViewsVi(1200000), fmtViewsVi(2500000000)],
      date: fmtUploadDate("20261114"),
      imgSrcs: imgs.map((i) => i.attrs.src),
      hasAnalyzeBtn: buttons.some((t) => t.includes("Phân tích")),
      hasViewAnalysisBtn: buttons.some((t) => t.includes("Xem phân tích")),
      hasStartCrawlBtn: buttons.some((t) => t.includes("Bắt đầu crawl")),
      fullTextSample: root.text.includes("Clip triệu view") && root.text.includes("3,4 Tr") && root.text.includes("7 quét · giữ 1"),
    }));
    """
    js = tmp_path / "crawl_ui_harness.js"
    js.write_text(harness, encoding="utf-8")
    proc = subprocess.run(["node", str(js), str(APP_JS)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["safeOk"] == "https://www.tiktok.com/@a"
    assert out["safeJs"] is None
    assert out["safeData"] is None
    assert out["views"] == ["chưa rõ", "850", "9,7 N", "1,2 Tr", "2,5 tỷ"]
    assert out["date"] == "2026-11-14"
    # Malicious javascript: thumbnail on video #1 was stripped; valid https:// thumbnail on video #2 kept
    assert out["imgSrcs"] == ["https://i.ytimg.com/vi/abc/hq.jpg"]
    assert out["hasAnalyzeBtn"] is True
    assert out["hasViewAnalysisBtn"] is True
    assert out["hasStartCrawlBtn"] is True
    assert out["fullTextSample"] is True
