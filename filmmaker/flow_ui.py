"""Image-to-video and image-to-image on the new Google Flow chat composer (flow.google.com), by driving the page.

gflow-cli refuses image-to-video on accounts that only have the agent chat composer
(exit 36). In the browser the same thing works by hand: "+" -> Upload media -> Add to
prompt -> type -> Start generation -> Approve. This script does exactly those clicks.
On accounts served the agent-only composer gflow refuses image-to-image too (#799), even
from local files; `i2i` attaches the character pictures the same way and downloads the
new image (images cost no credits, so nothing has to be approved).

It is a standalone script (stdlib + Playwright only) so it can run with the Python that
ships inside gflow-cli's own environment, and it reuses gflow's signed-in browser profile.
It prints one JSON document on stdout, shaped like gflow's:
  {"status": "ok", "local_path": ..., "credits": N}
  {"status": "fail", "error": {"exit_code": N, "detail": ..., "retryable": false, "spent": bool}}

Nothing is ever retried after "Approve" has been clicked, so credits are spent at most once.
For i2v, --timeout counts from the moment the driver starts: every long wait (opening the page,
uploads, Flow's offer, the render, each download) is cut to the time left, so the JSON is always
printed before the caller kills the process.
"""

from __future__ import annotations

import argparse
import json
import math
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
IMAGE_TILE = "flow-image-tile"
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


def _session(pw, profile: Path, channel: str | None, window: str, project: str, deadline: Deadline | None = None):
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
            _open_project(page, project, deadline)
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


# Flow's grid and chat re-render all the time; a locator can briefly point at a replaced node.
TRANSIENT_RE = re.compile(r"not attached to the DOM|element is not stable|is not visible|outside of the viewport|"
                          r"Execution context was destroyed|frame was detached|Timeout \d+ms exceeded", re.I)


def _transient(exc: Exception) -> bool:
    return type(exc).__module__.startswith("playwright") and bool(TRANSIENT_RE.search(str(exc)))


def _wait(fn, timeout: float, every: float = 0.5):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = fn()
        except Exception as exc:
            if not _transient(exc):
                raise
            v = None
        if v:
            return v
        time.sleep(every)
    return None


class Deadline:
    """One time budget for a whole i2v run, started when the driver starts (reads the module's `time`)."""

    def __init__(self, seconds: float):
        self.start = time.time()
        self.end = self.start + seconds

    def left(self) -> float:
        return max(0.0, self.end - time.time())

    def expired(self) -> bool:
        return self.left() <= 0

    def cap(self, seconds: float) -> float:
        return max(0.0, min(seconds, self.left()))

    def cap_ms(self, ms: float) -> int:
        return max(1, int(min(ms, self.left() * 1000)))


def _cap(deadline: Deadline | None, seconds: float) -> float:
    return seconds if deadline is None else deadline.cap(seconds)


def _pre_send_timeout(detail: str | None = None) -> Fail:
    """Out of time before anything was sent to Flow: exit 9, nothing spent. Never 23 (that one stops
    the whole render run), because nothing is wrong with the page; the next scene can still go."""
    return Fail(EXIT_TIMEOUT, detail or "Hết thời gian chờ trước khi gửi yêu cầu lên Flow (chưa gửi, chưa trừ credit) "
                "— thử render lại.", spent=False)


def _open_project(page, project: str, deadline: Deadline | None = None) -> None:
    goto_ms = 90_000 if deadline is None else deadline.cap_ms(90_000)
    try:
        page.goto(f"{FLOW}/project/{project}", wait_until="domcontentloaded", timeout=goto_ms)
    except Exception as exc:
        # On purpose: with a deadline, a page that does not load in time (even a slow network while time
        # is still left) is exit 9 spent=False, not 23, so the render run goes on with the next scene.
        if deadline is not None and (deadline.expired() or type(exc).__name__ == "TimeoutError"):
            if deadline.expired():
                raise _pre_send_timeout() from exc
            raise _pre_send_timeout(f"Không mở được trang Flow trong {goto_ms // 1000} giây (chưa gửi, "
                                    "chưa trừ credit) — kiểm tra mạng rồi thử render lại.") from exc
        raise
    ok = _wait(lambda: page.locator(EDITOR).count() > 0 or "accounts.google.com" in page.url or "/about" in page.url,
               _cap(deadline, 90))
    if "accounts.google.com" in page.url or "/about" in page.url:
        raise Fail(EXIT_AUTH, "Chưa đăng nhập Google trong profile của gflow. Chạy: gflow auth login --browser chrome")
    if not ok:
        if deadline is not None and deadline.expired():
            raise _pre_send_timeout()
        raise Fail(EXIT_DRIFT, "Không thấy ô chat của Flow (giao diện có thể đã thay đổi).")


def _attach(page, image: Path, deadline: Deadline | None = None) -> None:
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

    how = _wait(settled, _cap(deadline, 180), 1.0)
    if how is None and deadline is not None and deadline.expired():
        if menu.is_visible():
            page.keyboard.press("Escape")
        raise _pre_send_timeout()
    if how == "listed":
        time.sleep(1.5)  # let the thumbnail finish processing
        items.first.click()
        time.sleep(0.8)
        menu.locator("button", has_text="Add to prompt").first.click()
    if not _wait(lambda: page.locator(CHIP).count() > before, _cap(deadline, 60), 1.0):
        if menu.is_visible():
            page.keyboard.press("Escape")
        if deadline is not None and deadline.expired():
            raise _pre_send_timeout()
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


def _new_tile(page, before: list[str]) -> str | None:
    """Title of the newest video tile, if it was not there before (Flow names each new video and
    puts it first). Anything else is ignored, so an older clip is never taken for the new one."""
    _scroll_grid_top(page)
    now = _video_titles(page)
    if not now:
        return None
    title = now[0]
    if title not in before or now.count(title) > before.count(title):
        return title
    return None


def css_string(text: str) -> str:
    """`text` as a quoted CSS string. Not json.dumps: its \\u00f4 escapes mean nothing in CSS, so a title with
    an accented letter ("xôi") never matched and the finished clip was never downloaded."""
    out = []
    for ch in text:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\{ord(ch):x} ")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _tile(page, title: str):
    """Locator for the video tile with this title. Looked up again on every use, because the
    virtual grid swaps tile nodes while it scrolls and re-renders."""
    return (page.locator(f"{GRID_TILE}[aria-label={css_string(title)}]")
            .filter(has=page.locator(VIDEO_TILE)).first)


def _try_download(page, title: str, out: Path, deadline: Deadline | None = None) -> str:
    """Click Download > Original size and keep the video bytes the page fetches.
    Returns "ok", or "pending" if the clip is not ready yet. The browser's own file download is
    suppressed (it is what closed the browser before); the bytes come from the page's request.
    With a deadline, the wait for the bytes is cut to the time left (under 1 s left: not tried)."""
    if deadline is not None and deadline.left() < 1:
        return "pending"
    tile = _tile(page, title)
    if not tile.count():
        return "pending"
    try:
        tile.scroll_into_view_if_needed(timeout=5_000)
        tile.hover(timeout=5_000)
    except Exception as exc:
        if not _transient(exc):
            raise
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
        with page.expect_response(lambda r: VIDEO_URL in r.url, timeout=180_000 if deadline is None else deadline.cap_ms(180_000)) as resp:
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


class PollTried(Exception):
    """A download was attempted but the clip is not ready yet."""


def _close_menus(page) -> None:
    try:
        for _ in range(2):
            if page.locator(".cdk-overlay-container [role=menu]").count():
                page.keyboard.press("Escape")
    except Exception:
        pass


def _poll_download(page, titles_before: list[str], out: Path, last_try: float, every: float = 20,
                   deadline: Deadline | None = None) -> bool:
    """One check of the grid: True once the new clip is saved to `out`.
    Raises PollTried after an attempt that found the clip not ready (at most every `every` seconds)."""
    title = _new_tile(page, titles_before)
    if title is None:
        return False
    busy = _tile(page, title).locator("[role=progressbar], mat-progress-spinner, mat-progress-bar").count()
    if busy or time.time() - last_try <= every:
        return False
    if _try_download(page, title, out, deadline) == "ok":
        return True
    raise PollTried()


def _bubble_texts(page) -> list[str]:
    return page.locator(BUBBLE).evaluate_all("els => els.map(e => e.innerText)")


CREDIT_RE = re.compile(r"(\d+)\s+video generations?,?\s+costing\s+(\d+)\s+credits?", re.I)
NO_CREDITS_RE = re.compile(r"not enough credits|insufficient credits|out of credits|run out of credits", re.I)
FAILED_RE = re.compile(r"(generation|video) (has )?failed|couldn['’]t (generate|create)|could not (generate|create)|"
                       r"unable to (generate|create)|violat|against (our|the) (policy|policies)|something went wrong", re.I)


# Flow's answer when it accepted the job but has not started it yet ("…has been scheduled and is waiting in the
# queue due to high demand…"). Kept narrow, so a real error is never taken for "queued".
QUEUED_RE = re.compile(r"waiting in (the )?queue|has been scheduled", re.I)
OFFER_RE = re.compile(r"would you like me to (kick off|start|generate)[^?]*\?", re.I)
BLOCKED_MARK = "Flow xoá video"  # autopilot looks for it to rewrite the prompt
# Flow made the clip and then removed it (its safety filter): "warning Video has been deleted".
DELETED_RE = re.compile(r"video has been (deleted|removed)|video was (deleted|removed)", re.I)


def after_offer(text: str) -> str:
    """The part of a bubble after Flow's offer. The agent's turn keeps the offer ("…kick off this 1 video generation,
    costing 5 credits?"), the Approve/Reject buttons and, folded in, its reasoning ("Show thinking"), which may talk
    about policies; what Flow says about the result comes after the offer in the SAME bubble."""
    ends = [m.end() for rx in (CREDIT_RE, OFFER_RE) for m in rx.finditer(text)]
    if not ends:
        return text
    rest = text[max(ends):]
    m = re.search(r"\bReject\b", rest)
    return rest[m.end():] if m else rest


AUDIO_FAILED_RE = re.compile(r"audio generation failed", re.I)
APPROVE_MIN_LEFT = 180  # seconds: with less left, Flow's offer is rejected (spent=False) instead of approved
SEND_MIN_LEFT = 240 + APPROVE_MIN_LEFT + 60  # = 480 s: with less left, nothing is sent (exit 9, spent=False)
# 240 = longest wait for Flow's offer; 60 = _stable_titles (<= 30 s) + waiting for the send button (<= 15 s) + slack
AUDIO_HINT = ("Flow báo “Audio generation failed”: thường do lời thoại/nội dung thoại — sửa lời thoại hoặc prompt "
              "rồi gửi lại (có thể tốn credit).")


def classify_replies(texts: list[str]) -> tuple[str | None, bool]:
    """Flow's replies after the offer (`_bubble_texts(page)[bubbles_before:][1:]`) -> ("queued" | "failed" | None,
    audio failed). The newest bubble with a signal decides; a queue notice wins over "failed" in the same bubble."""
    audio = any(AUDIO_FAILED_RE.search(t or "") for t in texts)
    for t in reversed(texts):
        t = after_offer(t or "")
        if DELETED_RE.search(t):
            return "deleted", audio
        if QUEUED_RE.search(t):
            return "queued", audio
        if FAILED_RE.search(t):
            return "failed", audio
    return None, audio


def run_i2v(args) -> dict:
    dl = Deadline(args.timeout)  # first: the budget counts from the moment the driver starts
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
        ctx, page = _session(pw, Path(args.profile_dir).expanduser(), args.channel, window, args.project, deadline=dl)
        try:
            return _i2v_on_page(page, args, prompt, [image, *refs], out, dl)
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def _i2v_on_page(page, args, prompt: str, files: list[Path], out: Path, dl: Deadline) -> dict:
    if page.locator(NEW_SESSION_BTN).count():
        page.locator(NEW_SESSION_BTN).first.click()  # fresh chat: no context from earlier scenes
        time.sleep(1.5)
    if page.locator(CLEAR_BTN).count() and page.locator(CLEAR_BTN).first.is_visible():
        page.locator(CLEAR_BTN).first.click()
        time.sleep(0.5)

    for p in files:
        _attach(page, p, dl)

    editor = page.locator(EDITOR)
    editor.click()
    page.keyboard.insert_text(prompt)
    time.sleep(0.5)
    if args.dry_run:
        log("  (dry-run) đã gắn ảnh và gõ prompt, không bấm gửi.")
        page.locator(CLEAR_BTN).first.click()
        return {"status": "ok", "dry_run": True}

    if dl.left() < SEND_MIN_LEFT:  # not enough time to wait for the offer and the clip: do not spend anything
        left = int(dl.left())
        try:
            if page.locator(CLEAR_BTN).count():
                page.locator(CLEAR_BTN).first.click()
        except Exception:
            pass
        raise Fail(EXIT_TIMEOUT, f"Chỉ còn {left} giây nên không gửi yêu cầu (chưa gửi, chưa trừ credit) — thử render lại.",
                   spent=False)

    titles_before = _stable_titles(page, limit=30.0)  # full 30 s: an old clip must never look new
    perms_before = page.locator(PERMISSION).count()
    bubbles_before = page.locator(BUBBLE).count()
    send = page.locator(SEND_BTN)
    if not _wait(lambda: send.is_enabled(), 15):
        raise Fail(EXIT_DRIFT, "Nút gửi của Flow không bật lên.")
    send.click()
    log("  đã gửi yêu cầu, chờ Flow xác nhận…")
    credits = _offer(page, args, titles_before, perms_before, bubbles_before, dl)
    return _wait_video(page, titles_before, bubbles_before, out, dl, credits)


def _offer(page, args, titles_before: list[str], perms_before: int, bubbles_before: int, dl: Deadline) -> int | None:
    """Flow asks to confirm the cost (or starts straight away when "Confirm before generating" is Never).
    Returns the credits approved (None if Flow started by itself). From the end of this on, never resubmit."""
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

    state = _wait(asked, dl.cap(240), 1.0)
    if state == "credits":
        raise Fail(EXIT_NO_CREDITS, "Flow báo không đủ credit.")
    if state is None:
        texts = [t.strip() for t in _bubble_texts(page)[bubbles_before:] if t.strip()]
        reply = texts[-1][:400] if texts else "(không có)"
        if dl.expired():
            try:
                tile = _new_tile(page, titles_before)
            except Exception as exc:
                if not _transient(exc):
                    raise
                tile = None
            if tile is not None:  # Flow went ahead on its own: credits may be spent
                SPENT["approved"] = True
                out = Path(args.out).expanduser()
                raise Fail(EXIT_TIMEOUT, "Hết thời gian chờ: Flow đã bắt đầu tạo video (thấy ô video mới) nhưng hết giờ "
                           f"chờ. Video có thể vẫn đang tạo trên Flow: mở Flow kiểm tra, tải về lưu thành {out} rồi chạy "
                           "lại (app tự nhận clip, không tốn credit).", spent=True)
            raise Fail(EXIT_AGENT_DECLINED, "Hết thời gian chờ trước khi Flow đề nghị tạo video (chưa đồng ý, chưa trừ "
                       "credit). Trả lời của Flow: " + reply, spent=False)
        raise Fail(EXIT_AGENT_DECLINED, "Flow không đề nghị tạo video. Trả lời của Flow: " + reply)
    credits = None
    if state == "ask":
        perm = page.locator(PERMISSION).last
        if dl.left() < APPROVE_MIN_LEFT:  # too late to get the clip back: do not pay for it
            left = int(dl.left())
            perm.locator('[role=radio][aria-label="Reject"]').first.click()
            raise Fail(EXIT_AGENT_DECLINED, f"Gần hết thời gian chờ (còn {left} giây) nên không đồng ý đề nghị của Flow "
                       "(chưa trừ credit) — thử render lại.", spent=False)
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
    return credits


def _wait_video(page, titles_before: list[str], bubbles_before: int, out: Path, dl: Deadline,
                credits: int | None) -> dict:
    """Wait for the clip until the deadline. "queued" keeps waiting, "failed" is exit 42, out of time is exit 9;
    both spent=True."""
    approved_at = time.time()
    queued_seen = audio_seen = queued_logged = False
    last_note = 0.0
    last_try = 0.0
    while not dl.expired():
        try:
            replies = _bubble_texts(page)[bubbles_before:]
        except Exception as exc:
            if not _transient(exc):
                raise
            replies = []
        kind, audio = classify_replies(replies[1:])
        audio_seen = audio_seen or audio
        if kind == "deleted":
            raise Fail(EXIT_GENERATION_FAILED, f"{BLOCKED_MARK}: Flow đã tạo rồi tự xoá video (“Video has been deleted”, "
                       "thường do bộ lọc an toàn của Flow). Sửa prompt cho nhẹ nhàng hơn rồi gửi lại.", spent=True)
        if kind == "failed":
            # the texts just read, not a second read: a page re-render there would be exit 23
            texts = [after_offer(t).strip() for t in replies if t and after_offer(t).strip()]
            last = texts[-1] if texts else "(không có)"
            raise Fail(EXIT_GENERATION_FAILED, "Flow báo tạo video lỗi: " + last[:400]
                       + (" " + AUDIO_HINT if audio_seen else ""), spent=True)
        if kind == "queued":
            queued_seen = True
            if not queued_logged:
                queued_logged = True
                log(f"  Flow báo đang xếp hàng (đông người dùng) — tiếp tục chờ tối đa {math.ceil(dl.left() / 60)} phút")
        if time.time() - last_note > 60:
            last_note = time.time()
            log(f"  đang chờ Flow render… {int(time.time() - approved_at)}s")
        try:
            if _poll_download(page, titles_before, out, last_try, deadline=dl):
                log(f"  đã tải video về: {out} ({out.stat().st_size // 1024} KB)")
                return {"status": "ok", "local_path": str(out), "credits": credits, "succeeded": True,
                        "media_id": LAST_MEDIA["id"]}
        except PollTried:
            last_try = time.time()
        except Exception as exc:
            # Credits are already spent, so a re-rendering page must not end the wait.
            if not _transient(exc):
                raise
            log(f"  (trang Flow đang vẽ lại, thử lại: {str(exc).splitlines()[0][:120]})")
            _close_menus(page)
        time.sleep(min(5.0, dl.left()))
    mins = round((time.time() - approved_at) / 60)
    tail = (f"Video có thể vẫn đang tạo trên Flow: mở Flow kiểm tra, tải video về lưu thành {out} rồi chạy lại "
            "(app tự nhận clip, không tốn credit).")
    if queued_seen:
        raise Fail(EXIT_TIMEOUT, f"Flow báo đang xếp hàng (đông người dùng); đã chờ {mins} phút sau khi đồng ý mà chưa "
                   "tải được video. " + tail + (" " + AUDIO_HINT if audio_seen else ""), spent=True)
    raise Fail(EXIT_TIMEOUT, f"Đã chờ {mins} phút sau khi đồng ý mà chưa tải được video. " + tail, spent=True)


IMAGE_NAMES = {"nano2": "Nano Banana 2", "nanopro": "Nano Banana Pro", "imagen4": "Imagen 4"}


def build_image_prompt(prompt: str, *, refs: int, aspect: str, model: str | None) -> str:
    spec = [f"aspect ratio {aspect}"]
    if model:
        spec.append(f"model {IMAGE_NAMES.get(model, model)}")
    return (f"Make exactly 1 image (not a video), {', '.join(spec)}. "
            f"The {refs} attached image(s) are character references: keep those characters' faces, hair and "
            "clothes identical, but do not copy their pose or background. Image: " + prompt.strip())


def _image_ids(page) -> list[str]:
    """Media ids of the image tiles at the top of the grid (newest first)."""
    _scroll_grid_top(page)
    return page.locator(GRID_TILE).evaluate_all(
        "els => els.map(e => e.querySelector('flow-image-tile img[data-media-id]'))"
        ".filter(Boolean).map(i => i.getAttribute('data-media-id'))")


def _stable_image_ids(page, settle: float = 4.0, limit: float = 30.0) -> list[str]:
    end, last, since = time.time() + limit, None, time.time()
    while time.time() < end:
        cur = _image_ids(page)
        if cur != last:
            last, since = cur, time.time()
        elif time.time() - since >= settle:
            break
        time.sleep(0.5)
    return last or []


def _new_image(page, before: list[str]) -> tuple[str, str] | None:
    """(media id, src) of the newest image tile if it was not in the grid before and has loaded."""
    _scroll_grid_top(page)
    found = page.locator(GRID_TILE).evaluate_all(
        "els => els.map(e => e.querySelector('flow-image-tile img[data-media-id]')).filter(Boolean)"
        ".slice(0, 1).map(i => [i.getAttribute('data-media-id'), i.getAttribute('src') || ''])")
    if not found:
        return None
    mid, src = found[0]
    if mid in before or "/asb/" not in src:
        return None
    return mid, src


def _download_image(ctx, src: str, out: Path) -> bool:
    """Original-size image: the tile shows a 512px thumbnail ("=s512-rw"); "=d" is the original file."""
    url = src.rsplit("=", 1)[0] + "=d" if "=" in src.rsplit("/", 1)[-1] else src + "=d"
    try:
        r = ctx.request.get(url, timeout=120_000)
        body = r.body() if r.ok else b""
    except Exception as exc:
        log(f"  (chưa tải được ảnh: {str(exc).splitlines()[0]})")
        return False
    if len(body) < 10_000 or not (body[:3] == b"\xff\xd8\xff" or body[:8] == b"\x89PNG\r\n\x1a\n"
                                  or (body[:4] == b"RIFF" and body[8:12] == b"WEBP")):
        return False
    out.write_bytes(body)
    return True


def run_i2i(args) -> dict:
    refs = [Path(r).expanduser() for r in args.ref or []]
    if not refs:
        raise Fail(EXIT_USAGE, "cần ít nhất một ảnh tham chiếu (--ref)")
    for p in refs:
        if not p.is_file():
            raise Fail(EXIT_USAGE, f"không thấy ảnh {p}")
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    prompt = build_image_prompt(args.prompt, refs=len(refs), aspect=args.aspect, model=args.model)
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
            for p in refs:
                _attach(page, p)
            editor = page.locator(EDITOR)
            editor.click()
            page.keyboard.insert_text(prompt)
            time.sleep(0.5)
            if args.dry_run:
                log("  (dry-run) đã gắn ảnh và gõ prompt, không bấm gửi.")
                page.locator(CLEAR_BTN).first.click()
                return {"status": "ok", "dry_run": True}

            before = _stable_image_ids(page)  # after the uploads, so an uploaded picture is never taken for the result
            perms_before = page.locator(PERMISSION).count()
            bubbles_before = page.locator(BUBBLE).count()
            send = page.locator(SEND_BTN)
            if not _wait(lambda: send.is_enabled(), 15):
                raise Fail(EXIT_DRIFT, "Nút gửi của Flow không bật lên.")
            send.click()
            log("  đã gửi yêu cầu tạo ảnh, chờ Flow…")

            started, answered, res = time.time(), False, None
            while time.time() - started < args.timeout:
                # Images are free; Flow may still ask to confirm. Never approve anything that makes a video.
                if not answered and page.locator(PERMISSION).count() > perms_before:
                    perm = page.locator(PERMISSION).last
                    approve = perm.locator('[role=radio][aria-label="Approve"]:not(.read-only)')
                    if approve.count():
                        text = perm.inner_text()
                        if re.search(r"\bvideos?\b", text, re.I):
                            perm.locator('[role=radio][aria-label="Reject"]').first.click()
                            raise Fail(EXIT_AGENT_DECLINED, f"Flow đề nghị tạo video thay vì ảnh, đã từ chối: {text.strip()[:300]}")
                        approve.first.click()
                        answered = True
                        log("  đã đồng ý tạo ảnh")
                texts = _bubble_texts(page)[bubbles_before:]
                if any(FAILED_RE.search(t) for t in texts[1:]):
                    res = "failed"
                    break
                try:
                    new = _new_image(page, before)
                except Exception as exc:
                    if not _transient(exc):
                        raise
                    new = None
                if new:
                    time.sleep(2)  # let Flow finish publishing the file behind the thumbnail
                    if _download_image(ctx, new[1], out):
                        LAST_MEDIA["id"] = new[0]
                        res = "ok"
                        break
                time.sleep(3)
            if res == "failed":
                texts = [t.strip() for t in _bubble_texts(page)[bubbles_before:] if t.strip()]
                raise Fail(EXIT_GENERATION_FAILED, "Flow báo tạo ảnh lỗi: " + texts[-1][:400])
            if res is None:
                texts = [t.strip() for t in _bubble_texts(page)[bubbles_before:] if t.strip()]
                raise Fail(EXIT_TIMEOUT, f"Quá {args.timeout}s mà chưa có ảnh mới trên Flow. Trả lời của Flow: "
                           + (texts[-1][:300] if texts else "(không có)"))
            log(f"  đã tải ảnh về: {out} ({out.stat().st_size // 1024} KB)")
            return {"status": "ok", "local_path": str(out), "succeeded": True, "media_id": LAST_MEDIA["id"],
                    "images": [{"local_path": str(out), "media_id": LAST_MEDIA["id"]}]}
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(a, timeout: int) -> None:
        a.add_argument("--project", required=True)
        a.add_argument("--prompt", required=True)
        a.add_argument("--aspect", default="16:9")
        a.add_argument("--model")
        a.add_argument("-o", "--out", required=True)
        a.add_argument("--profile-dir", required=True)
        a.add_argument("--channel", default="chrome")
        a.add_argument("--window", choices=["show", "hidden", "headless"], default="hidden",
                       help="show = visible window; hidden = real browser kept off-screen (default); headless = no window at all")
        a.add_argument("--headless", action="store_true", help="same as --window headless")
        a.add_argument("--timeout", type=int, default=timeout)
        a.add_argument("--dry-run", action="store_true", help="attach + type only, do not send")

    a = sub.add_parser("i2v")
    common(a, 1800)
    a.add_argument("--image", required=True)
    a.add_argument("--ref", action="append")
    a.add_argument("--duration", type=int)
    a.add_argument("--max-credits", type=int, default=0, help="reject Flow's offer above this many credits (0 = no limit)")
    b = sub.add_parser("i2i", help="one image from a prompt + character reference images (free)")
    common(b, 300)
    b.add_argument("--ref", action="append", required=True)
    args = ap.parse_args(argv)
    try:
        result = run_i2i(args) if args.cmd == "i2i" else run_i2v(args)
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
