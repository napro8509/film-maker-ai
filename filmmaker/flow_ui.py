"""Image-to-video on the new Google Flow chat composer (flow.google.com), by driving the page.

gflow-cli refuses image-to-video on accounts that only have the agent chat composer
(exit 36). In the browser the same thing works by hand: "+" -> Upload media -> Add to
prompt -> type -> Start generation -> Approve. This script does exactly those clicks.

It is a standalone script (stdlib + Playwright only) so it can run with the Python that
ships inside gflow-cli's own environment, and it reuses gflow's signed-in browser profile.
It prints one JSON document on stdout, shaped like gflow's:
  {"status": "ok", "local_path": ..., "credits": N}
  {"status": "fail", "error": {"exit_code": N, "detail": ..., "retryable": false, "spent": bool}}

Nothing is ever retried after "Approve" has been clicked, so credits are spent at most once.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

FLOW = "https://flow.google.com"

# Selectors (Material icon names / aria-labels / Flow's own custom elements), checked on 2026-11-08.
PROMPT_BOX = "flow-creative-agent-prompt-box"
EDITOR = f"{PROMPT_BOX} .ProseMirror"
ADD_BTN = f'{PROMPT_BOX} button[aria-label="Add ingredients to the prompt box"]'
CLEAR_BTN = f'{PROMPT_BOX} button[aria-label="Clear prompt"]'
SEND_BTN = f'{PROMPT_BOX} button[aria-label="Start generation"]'
CHIP = f"{PROMPT_BOX} flow-ingredient-chip"
NEW_SESSION_BTN = 'flow-agent-panel button[aria-label="Start new session"]'
ADD_MENU = "flow-add-menu-popover-content"
PERMISSION = "flow-permission-message"
BUBBLE = "flow-chat-bubble"
VIDEO_TILE = "flow-video-tile"
GRID_TILE = "flow-grid-tile-container"  # aria-label = the media's title
VIDEO_URL = "flow-content.google/video/"  # what the page fetches when you click Download

EXIT_USAGE, EXIT_AUTH, EXIT_TIMEOUT, EXIT_DRIFT, EXIT_NO_CREDITS = 2, 8, 9, 23, 37
EXIT_AGENT_DECLINED, EXIT_GENERATION_FAILED = 41, 42


SPENT = {"approved": False}  # set once "Approve" was clicked
LAST_MEDIA: dict = {"id": None}


class Fail(Exception):
    def __init__(self, code: int, detail: str, *, spent: bool = False):
        super().__init__(detail)
        self.code, self.detail, self.spent = code, detail, spent


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def build_prompt(motion: str, *, refs: int, aspect: str, model: str | None, duration: int | None) -> str:
    parts = ["Make exactly 1 video from the first attached image: use that image as the exact first frame "
             "and keep the same people, faces, clothes, lighting and setting."]
    if refs:
        parts.append(f"The other {refs} attached image(s) are character references: keep those characters' faces identical.")
    spec = [f"aspect ratio {aspect}"]
    if model:
        spec.append(f"model {model}")
    if duration:
        spec.append(f"{duration} seconds")
    parts.append("Use " + ", ".join(spec) + ".")
    parts.append("Motion and action: " + motion.strip())
    return " ".join(parts)


OFFSCREEN = (-4000, -4000)


def _hide_window(ctx, page) -> None:
    """Keep a real (headed) browser out of sight: move it off-screen, and minimize it if the OS
    pulled it back onto a screen. A headed browser is what Google expects; it just isn't shown."""
    try:
        cdp = ctx.new_cdp_session(page)
        wid = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {
            "left": OFFSCREEN[0], "top": OFFSCREEN[1], "width": 1440, "height": 900, "windowState": "normal"}})
        b = cdp.send("Browser.getWindowBounds", {"windowId": wid})["bounds"]
        if b.get("left", 0) + b.get("width", 0) > 0 and b.get("top", 0) + b.get("height", 0) > 0:
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "minimized"}})
    except Exception as exc:  # cosmetic only: never fail a render over it
        log(f"  (không ẩn được cửa sổ trình duyệt: {str(exc).splitlines()[0]})")


def _unheadless_ua(ctx, page) -> None:
    """Headless Chrome announces itself in the user agent; present the normal one instead."""
    try:
        ua = page.evaluate("navigator.userAgent")
        if "Headless" in ua:
            cdp = ctx.new_cdp_session(page)
            cdp.send("Network.setUserAgentOverride", {"userAgent": ua.replace("HeadlessChrome", "Chrome")})
    except Exception:
        pass


def _session(pw, profile: Path, channel: str | None, window: str, project: str):
    """Open the project page with gflow's signed-in profile. Tries Chrome, then Playwright's Chromium
    (whichever one gflow signed in with is the one that can read the profile's cookies).
    window: "show" (visible), "hidden" (real browser kept off-screen/minimized), "headless"."""
    args = ["--disable-blink-features=AutomationControlled", "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding", "--disable-background-timer-throttling"]
    if window == "hidden":
        args += [f"--window-position={OFFSCREEN[0]},{OFFSCREEN[1]}", "--window-size=1440,900"]
    kw = dict(headless=window == "headless", viewport={"width": 1440, "height": 900}, accept_downloads=True,
              args=args)
    last: Exception | None = None
    for ch in ([channel, None] if channel else [None]):
        try:
            ctx = pw.chromium.launch_persistent_context(str(profile), **({"channel": ch} if ch else {}), **kw)
        except Exception as exc:  # e.g. Chrome not installed, or the profile is in use
            last = exc
            log(f"  (không mở được trình duyệt {ch or 'chromium'}: {str(exc).splitlines()[0]})")
            continue
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if window == "hidden":
            _hide_window(ctx, page)
        elif window == "headless":
            _unheadless_ua(ctx, page)
        try:
            _open_project(page, project)
            return ctx, page
        except Fail as f:
            ctx.close()
            last = f
            if f.code != EXIT_AUTH:
                raise
            log(f"  (trình duyệt {ch or 'chromium'} chưa đăng nhập Flow, thử cách khác)")
    if isinstance(last, Fail):
        raise last
    if last and ("ProcessSingleton" in str(last) or "SingletonLock" in str(last)):
        raise Fail(EXIT_USAGE, "Profile trình duyệt của gflow đang được dùng (còn cửa sổ gflow mở?). Đóng nó rồi thử lại.")
    raise Fail(EXIT_USAGE, f"Không mở được trình duyệt: {last}")


def _wait(fn, timeout: float, every: float = 0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


def _open_project(page, project: str) -> None:
    page.goto(f"{FLOW}/project/{project}", wait_until="domcontentloaded", timeout=90_000)
    ok = _wait(lambda: page.locator(EDITOR).count() > 0 or "accounts.google.com" in page.url or "/about" in page.url, 90)
    if "accounts.google.com" in page.url or "/about" in page.url:
        raise Fail(EXIT_AUTH, "Chưa đăng nhập Google trong profile của gflow. Chạy: gflow auth login --browser chrome")
    if not ok:
        raise Fail(EXIT_DRIFT, "Không thấy ô chat của Flow (giao diện có thể đã thay đổi).")


def _attach(page, image: Path) -> None:
    before = page.locator(CHIP).count()
    page.locator(ADD_BTN).click()
    menu = page.locator(ADD_MENU)
    menu.wait_for(state="visible", timeout=15_000)
    uploads_tab = menu.locator('[role=tab]', has_text="Uploads")
    if uploads_tab.count():
        uploads_tab.first.click()
        time.sleep(0.8)
    items = menu.locator("button[role=option].asset-item")
    first_before = items.first.inner_text().strip() if items.count() else None
    n_before = items.count()
    with page.expect_file_chooser(timeout=15_000) as fc:
        menu.locator("button", has_text="Upload media").first.click()
    fc.value.set_files(str(image))
    log(f"  đang tải ảnh lên Flow: {image.name}")

    def settled():
        if page.locator(CHIP).count() > before:
            return "chip"
        if not menu.is_visible():
            return "closed"
        n = items.count()
        if n and (n > n_before or items.first.inner_text().strip() != first_before):
            return "listed"
        return None

    how = _wait(settled, 180, 1.0)
    if how == "listed":
        time.sleep(1.5)  # let the thumbnail finish processing
        items.first.click()
        time.sleep(0.8)
        menu.locator("button", has_text="Add to prompt").first.click()
    if not _wait(lambda: page.locator(CHIP).count() > before, 60, 1.0):
        if menu.is_visible():
            page.keyboard.press("Escape")
        raise Fail(EXIT_DRIFT, "Đã tải ảnh lên nhưng không gắn được vào ô chat của Flow.")
    if menu.is_visible():
        page.keyboard.press("Escape")


def _video_titles(page) -> list[str]:
    """Titles of the video tiles at the top of the grid (newest first)."""
    return page.locator(GRID_TILE).evaluate_all(
        "els => els.filter(e => e.querySelector('flow-video-tile')).map(e => e.getAttribute('aria-label') || '')")


def _scroll_grid_top(page) -> None:
    page.evaluate("""() => document.querySelectorAll('cdk-virtual-scroll-viewport, .virtual-scroll-container')
        .forEach(e => { try { e.scrollTop = 0; } catch (_) {} })""")


def _stable_titles(page, settle: float = 4.0, limit: float = 30.0) -> list[str]:
    """Video titles once the grid has stopped loading (same list for `settle` seconds)."""
    _scroll_grid_top(page)
    end, last, since = time.time() + limit, None, time.time()
    while time.time() < end:
        cur = _video_titles(page)
        if cur != last:
            last, since = cur, time.time()
        elif time.time() - since >= settle:
            break
        time.sleep(0.5)
    return last or []


def _new_tile(page, before: list[str]):
    """The newest video tile, if its title was not there before (Flow names each new video and
    puts it first). Anything else is ignored, so an older clip is never taken for the new one."""
    _scroll_grid_top(page)
    tiles = page.locator(GRID_TILE).filter(has=page.locator(VIDEO_TILE))
    if not tiles.count():
        return None
    first = tiles.first
    title = first.get_attribute("aria-label") or ""
    now = _video_titles(page)
    if title not in before or now.count(title) > before.count(title):
        return first
    return None


def _try_download(page, tile, out: Path) -> str:
    """Click Download > Original size and keep the video bytes the page fetches.
    Returns "ok", or "pending" if the clip is not ready yet. The browser's own file download is
    suppressed (it is what closed the browser before); the bytes come from the page's request."""
    tile.scroll_into_view_if_needed()
    try:
        tile.hover(timeout=5_000)
    except Exception:
        pass
    more = tile.locator('button[aria-label="More options"]').first
    if not more.count():
        return "pending"
    try:
        more.click(timeout=10_000)
    except Exception:  # hover-only button in a window that is not on screen: click it directly
        more.evaluate("b => b.click()")
    menu = page.locator(".cdk-overlay-container [role=menuitem]")
    dl = menu.filter(has_text="Download").first
    if not _wait(lambda: dl.count() > 0, 5) or (dl.get_attribute("aria-disabled") == "true") or dl.is_disabled():
        page.keyboard.press("Escape")
        return "pending"
    dl.click()
    orig = menu.filter(has_text="Original size").first
    if not _wait(lambda: orig.count() > 0, 5) or orig.get_attribute("aria-disabled") == "true" or orig.is_disabled():
        page.keyboard.press("Escape")
        page.keyboard.press("Escape")
        return "pending"
    page.evaluate("""() => { if (!window.__fmNoDl) { window.__fmNoDl = HTMLAnchorElement.prototype.click;
        HTMLAnchorElement.prototype.click = function () { if (!(this.href || '').startsWith('blob:')) return window.__fmNoDl.call(this); }; } }""")
    try:
        with page.expect_response(lambda r: VIDEO_URL in r.url, timeout=180_000) as resp:
            orig.click()
        r = resp.value
        body = r.body() if r.ok else b""
    except Exception as exc:
        log(f"  (chưa tải được video: {str(exc).splitlines()[0]})")
        body = b""
    finally:
        page.evaluate("""() => { if (window.__fmNoDl) { HTMLAnchorElement.prototype.click = window.__fmNoDl; delete window.__fmNoDl; } }""")
        for _ in range(2):
            if page.locator(".cdk-overlay-container [role=menu]").count():
                page.keyboard.press("Escape")
    if len(body) < 50_000 or b"ftyp" not in body[:64]:
        return "pending"
    out.write_bytes(body)
    m = re.search(r"/video/([0-9a-f-]{36})", r.url)
    LAST_MEDIA["id"] = m.group(1) if m else None
    return "ok"


def _bubble_texts(page) -> list[str]:
    return page.locator(BUBBLE).evaluate_all("els => els.map(e => e.innerText)")


CREDIT_RE = re.compile(r"(\d+)\s+video generations?,?\s+costing\s+(\d+)\s+credits?", re.I)
NO_CREDITS_RE = re.compile(r"not enough credits|insufficient credits|out of credits|run out of credits", re.I)
FAILED_RE = re.compile(r"(generation|video) (has )?failed|couldn['’]t (generate|create)|could not (generate|create)|"
                       r"unable to (generate|create)|violat|against (our|the) (policy|policies)|something went wrong", re.I)


def run_i2v(args) -> dict:
    image = Path(args.image).expanduser()
    refs = [Path(r).expanduser() for r in args.ref or []]
    for p in [image, *refs]:
        if not p.is_file():
            raise Fail(EXIT_USAGE, f"không thấy ảnh {p}")
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(args.prompt, refs=len(refs), aspect=args.aspect, model=args.model, duration=args.duration)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise Fail(EXIT_USAGE, f"Python này chưa có Playwright ({sys.executable}). Hãy dùng Python của gflow-cli.") from exc

    with sync_playwright() as pw:
        window = "headless" if args.headless else args.window
        ctx, page = _session(pw, Path(args.profile_dir).expanduser(), args.channel, window, args.project)
        try:
            if page.locator(NEW_SESSION_BTN).count():
                page.locator(NEW_SESSION_BTN).first.click()  # fresh chat: no context from earlier scenes
                time.sleep(1.5)
            if page.locator(CLEAR_BTN).count() and page.locator(CLEAR_BTN).first.is_visible():
                page.locator(CLEAR_BTN).first.click()
                time.sleep(0.5)

            for p in [image, *refs]:
                _attach(page, p)

            editor = page.locator(EDITOR)
            editor.click()
            page.keyboard.insert_text(prompt)
            time.sleep(0.5)
            if args.dry_run:
                log("  (dry-run) đã gắn ảnh và gõ prompt, không bấm gửi.")
                page.locator(CLEAR_BTN).first.click()
                return {"status": "ok", "dry_run": True}

            titles_before = _stable_titles(page)
            perms_before = page.locator(PERMISSION).count()
            bubbles_before = page.locator(BUBBLE).count()
            send = page.locator(SEND_BTN)
            if not _wait(lambda: send.is_enabled(), 15):
                raise Fail(EXIT_DRIFT, "Nút gửi của Flow không bật lên.")
            send.click()
            log("  đã gửi yêu cầu, chờ Flow xác nhận…")

            # 1) Flow asks to confirm the cost (or starts straight away when "Confirm before generating" is Never).
            def asked():
                if page.locator(PERMISSION).count() > perms_before:
                    p = page.locator(PERMISSION).last
                    if p.locator('[role=radio][aria-label="Approve"]:not(.read-only)').count():
                        return "ask"
                if _new_tile(page, titles_before) is not None:
                    return "started"
                texts = _bubble_texts(page)[bubbles_before:]
                if any(NO_CREDITS_RE.search(t) for t in texts):
                    return "credits"
                return None

            state = _wait(asked, 240, 1.0)
            if state == "credits":
                raise Fail(EXIT_NO_CREDITS, "Flow báo không đủ credit.")
            if state is None:
                texts = [t.strip() for t in _bubble_texts(page)[bubbles_before:] if t.strip()]
                raise Fail(EXIT_AGENT_DECLINED, "Flow không đề nghị tạo video. Trả lời của Flow: "
                           + (texts[-1][:400] if texts else "(không có)"))
            credits = None
            if state == "ask":
                perm = page.locator(PERMISSION).last
                text = perm.inner_text()
                m = CREDIT_RE.search(text)
                n_videos, credits = (int(m.group(1)), int(m.group(2))) if m else (None, None)
                if n_videos != 1 or (args.max_credits and credits and credits > args.max_credits):
                    perm.locator('[role=radio][aria-label="Reject"]').first.click()
                    raise Fail(EXIT_AGENT_DECLINED, f"Đã từ chối đề nghị của Flow (chưa trừ credit): {text.strip()[:300]}")
                perm.locator('[role=radio][aria-label="Approve"]').first.click()
                SPENT["approved"] = True
                log(f"  đã đồng ý: 1 video, {credits} credit")

            SPENT["approved"] = True  # from here credits may be spent: never resubmit
            started = time.time()
            last_note = 0.0
            last_try = 0.0
            res = None
            while time.time() - started < args.timeout:
                texts = _bubble_texts(page)[bubbles_before:]
                if any(FAILED_RE.search(t) for t in texts[1:]):
                    res = "failed"
                    break
                if time.time() - last_note > 60:
                    last_note = time.time()
                    log(f"  đang chờ Flow render… {int(time.time() - started)}s")
                tile = _new_tile(page, titles_before)
                busy = tile is not None and tile.locator('[role=progressbar], mat-progress-spinner, mat-progress-bar').count()
                # Try to fetch the clip at most every 20 s; a clip that is still rendering is refused.
                if tile is not None and not busy and time.time() - last_try > 20:
                    last_try = time.time()
                    if _try_download(page, tile, out) == "ok":
                        res = "ok"
                        break
                time.sleep(5)
            if res == "failed":
                texts = [t.strip() for t in _bubble_texts(page)[bubbles_before:] if t.strip()]
                raise Fail(EXIT_GENERATION_FAILED, "Flow báo tạo video lỗi: " + texts[-1][:400], spent=True)
            if res is None:
                raise Fail(EXIT_TIMEOUT, f"Quá {args.timeout}s mà chưa tải được video. Video có thể vẫn đang tạo trên Flow; "
                           "hãy tải về tay rồi đặt vào thư mục clips.", spent=True)
            log(f"  đã tải video về: {out} ({out.stat().st_size // 1024} KB)")
            return {"status": "ok", "local_path": str(out), "credits": credits, "succeeded": True,
                    "media_id": LAST_MEDIA["id"]}
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("i2v")
    a.add_argument("--project", required=True)
    a.add_argument("--image", required=True)
    a.add_argument("--ref", action="append")
    a.add_argument("--prompt", required=True)
    a.add_argument("--aspect", default="16:9")
    a.add_argument("--model")
    a.add_argument("--duration", type=int)
    a.add_argument("-o", "--out", required=True)
    a.add_argument("--profile-dir", required=True)
    a.add_argument("--channel", default="chrome")
    a.add_argument("--window", choices=["show", "hidden", "headless"], default="hidden",
                   help="show = visible window; hidden = real browser kept off-screen (default); headless = no window at all")
    a.add_argument("--headless", action="store_true", help="same as --window headless")
    a.add_argument("--timeout", type=int, default=1800)
    a.add_argument("--max-credits", type=int, default=0, help="reject Flow's offer above this many credits (0 = no limit)")
    a.add_argument("--dry-run", action="store_true", help="attach + type only, do not send")
    args = ap.parse_args(argv)
    try:
        result = run_i2v(args)
        code = 0
    except Fail as f:
        result = {"status": "fail", "error": {"exit_code": f.code, "detail": f.detail, "retryable": False, "spent": f.spent}}
        code = f.code
    except Exception as exc:  # unexpected page state: treat as UI drift, never retry
        result = {"status": "fail", "error": {"exit_code": EXIT_DRIFT, "detail": f"{type(exc).__name__}: {exc}".strip()[:800],
                                              "retryable": False, "spent": SPENT["approved"]}}
        code = EXIT_DRIFT
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
