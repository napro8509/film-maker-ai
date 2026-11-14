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
POLL = {"tile": False}  # set once the grid shows the new video tile (Flow started making the clip)
#: After Flow says the job "is waiting in the queue", wait this long for it to start (a new tile) before giving up
#: on the scene so the run can go on to the next one. Once the tile is there, the normal deadline applies.
QUEUE_WAIT = 180
LAST_MEDIA: dict = {"id": None}


class Fail(Exception):
    def __init__(self, code: int, detail: str, *, spent: bool = False):
        super().__init__(detail)
        self.code, self.detail, self.spent = code, detail, spent


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def build_prompt(motion: str, *, refs: int, aspect: str, model: str | None, duration: int | None,
                 image: bool = True) -> str:
    if image and refs:
        # Flow's first-frame mode takes ONE image: asked for "the exact first frame" plus character references,
        # the agent stopped and asked which of the two to drop (and the render hung until the timeout). So this
        # asks for reference-to-video outright, with the start picture as the scene to begin on.
        parts = [f"Make exactly 1 video with Reference-to-Video, using all {refs + 1} attached images as "
                 "references; do not ask which mode to use. The first attached image is the scene: begin the video "
                 "on that picture, with the same composition, people, faces, clothes, lighting and setting.",
                 f"The other {refs} attached image(s) are character references: keep those characters' "
                 "faces identical."]
    elif image:
        parts = ["Make exactly 1 video from the first attached image: use that image as the exact first frame "
                 "and keep the same people, faces, clothes, lighting and setting."]
    else:
        parts = ["Make exactly 1 video."]
        if refs:
            parts.append(f"The {refs} attached image(s)/characters are references: keep those characters' faces, "
                         "hair and clothes identical.")
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


ASSET_TITLE = ".asset-title"
ASSET_ITEM = "button[role=option].asset-item"
ACTIVE = "asset-item-active"  # Flow's class for a selected upload
# In the open add menu: is our upload listed, selected (active), still processing, and is anything else selected?
_SELECTION_JS = """(menu, name) => {
  const items = Array.from(menu.querySelectorAll('button[role=option].asset-item'));
  const isMine = e => ((e.querySelector('.asset-title') || {}).innerText || '').trim() === name;
  const on = e => e.classList.contains('asset-item-active') || e.getAttribute('aria-selected') === 'true';
  const mine = items.find(isMine);
  return {mine: !!mine, active: !!mine && on(mine),
          busy: !!mine && !!mine.querySelector('mat-spinner, mat-progress-spinner, [role=progressbar], .mat-mdc-progress-spinner'),
          others: items.filter(e => !isMine(e) && on(e)).length};
}"""


def upload_name(image: Path) -> str:
    """A name no other upload in the project has: Flow lists uploads by file name, not always newest first,
    and the same name (s08.png) can be there twice. The only safe pick is the item with this exact name."""
    import uuid

    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", image.stem)[:40] or "image"
    return f"fm-{stem}-{uuid.uuid4().hex[:8]}{image.suffix.lower() or '.png'}"


def _attach(page, image: Path, deadline: Deadline | None = None) -> None:
    """Upload `image` and put exactly that picture in the chat box. Never another upload: a wrong picture
    attached here makes Veo animate the wrong scene (and costs credits)."""
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory(prefix="fm-up-") as tmp:
        name = upload_name(image)
        copy = Path(tmp) / name
        shutil.copyfile(image, copy)
        _attach_named(page, copy, image.name, deadline)


def _attach_named(page, image: Path, shown: str, deadline: Deadline | None) -> None:
    name = image.name
    before = page.locator(CHIP).count()
    page.locator(ADD_BTN).click()
    menu = page.locator(ADD_MENU)
    menu.wait_for(state="visible", timeout=15_000)
    uploads_tab = menu.locator('[role=tab]', has_text="Uploads")
    if uploads_tab.count():
        uploads_tab.first.click()
        time.sleep(0.8)
    with page.expect_file_chooser(timeout=15_000) as fc:
        menu.locator("button", has_text="Upload media").first.click()
    fc.value.set_files(str(image))
    log(f"  đang tải ảnh lên Flow: {shown}")
    mine = menu.locator(ASSET_ITEM).filter(has=page.locator(ASSET_TITLE, has_text=name)).first
    add = menu.locator("button", has_text="Add to prompt").first

    def give_up(msg: str) -> Fail:
        if menu.is_visible():
            page.keyboard.press("Escape")
        if deadline is not None and deadline.expired():
            return _pre_send_timeout()
        return Fail(EXIT_DRIFT, msg)

    def settled():
        if page.locator(CHIP).count() > before:
            return "chip"  # Flow attached the upload by itself
        if not menu.is_visible():
            return "closed"
        if mine.count():
            return "listed"
        return None

    how = _wait(settled, _cap(deadline, 180), 1.0)
    if how is None:
        raise give_up(f"Đã tải ảnh lên nhưng không thấy “{shown}” trong danh sách ảnh của Flow.")
    if how == "listed":
        # Seen on Flow 2026-11-11: the upload is listed (not always first) with a spinner while Flow processes
        # it; the selected upload has the class "asset-item-active" (aria-selected stays "false"); selection is
        # one-of (a click moves it) and Flow REMEMBERS the last one, even from an earlier session, so "Add to
        # prompt" is on before we pick anything and would attach that old picture. That is how clips were made
        # from the wrong scene. So: click only OUR item, and press Add only when ours is the one selected,
        # processed, and nothing else is selected.
        state = {"clicked": 0.0}

        def selected() -> bool:
            info = menu.evaluate(_SELECTION_JS, name) or {}
            if not info.get("mine"):
                return False
            if info.get("active"):
                return not info.get("busy") and not info.get("others") and add.is_enabled()
            if time.time() - state["clicked"] >= 4:
                mine.click()
                state["clicked"] = time.time()
            return False

        if not _wait(selected, _cap(deadline, 150), 1.0):
            raise give_up(f"Flow chưa xử lý xong ảnh “{shown}” vừa tải lên (chưa chọn được để gắn vào chat).")
        add.click()
    if not _wait(lambda: page.locator(CHIP).count() > before, _cap(deadline, 60), 1.0):
        raise give_up("Đã tải ảnh lên nhưng không gắn được vào ô chat của Flow.")
    if menu.is_visible():
        page.keyboard.press("Escape")


# --- Flow Characters (left panel "Characters" -> New character), checked on 2026-11-14 ----------------------
CHAR_BOX = "flow-character-prompt-box"
CHAR_EDITOR = f"{CHAR_BOX} .ProseMirror"
CHAR_SEND = f'{CHAR_BOX} button[aria-label="Start generation"]'
CHAR_NAME = 'flow-character-form input[aria-label="Character name"]'
CHAR_INFO = 'flow-character-form textarea[aria-label="Character personality"]'
CHAR_IMG = "flow-character-image-preview img.preview-image"
CHAR_PANEL = "flow-character-preview-panel"
CHAR_ID_RE = re.compile(r"/character/([0-9a-fA-F-]{36})")
CHARACTER_REF = "character:"  # --ref character:<name> = a Flow character of the project, attached by its name
CHAR_FAILED_RE = re.compile(r"couldn['’]t|could not|unable to|policy|violat|try a different|something went wrong|"
                            r"failed", re.I)
# Click the one add-menu item whose title is exactly `name` (a substring match would pick "Minh (cũ)" for "Minh").
_CLICK_EXACT_JS = """(menu, name) => {
  const it = Array.from(menu.querySelectorAll('button[role=option].asset-item'))
    .find(e => ((e.querySelector('.asset-title') || {}).innerText || '').trim() === name);
  if (it) it.click();
  return !!it;
}"""


def character_ref(name: str) -> str:
    return CHARACTER_REF + name


def split_refs(refs: list) -> list[tuple[str, str]]:
    """("character", name) or ("file", path) for each --ref (a str or Path), in order."""
    out = []
    for r in map(str, refs):
        out.append(("character", r[len(CHARACTER_REF):].strip()) if r.startswith(CHARACTER_REF) else ("file", r))
    return out


def _attach_character(page, name: str, deadline: Deadline | None = None) -> None:
    """Put the project's Flow character `name` in the chat box ("+" -> Characters -> pick -> Add to prompt).
    Only the item titled exactly `name`: Flow remembers the last selected asset, so "Add to prompt" may be on
    before anything is picked (see _attach_named)."""
    before = page.locator(CHIP).count()
    page.locator(ADD_BTN).click()
    menu = page.locator(ADD_MENU)
    menu.wait_for(state="visible", timeout=15_000)
    tab = menu.locator("[role=tab]", has_text="Characters")
    if tab.count():
        tab.first.click()
        time.sleep(0.8)
    search = menu.locator('input[aria-label="Search assets"]')
    if search.count():
        search.first.fill(name)
        time.sleep(1.0)
    add = menu.locator("button", has_text="Add to prompt").first

    def give_up(msg: str) -> Fail:
        if menu.is_visible():
            page.keyboard.press("Escape")
        if deadline is not None and deadline.expired():
            return _pre_send_timeout()
        return Fail(EXIT_DRIFT, msg)

    if not _wait(lambda: (menu.evaluate(_SELECTION_JS, name) or {}).get("mine"), _cap(deadline, 30), 1.0):
        raise give_up(f"Không thấy nhân vật “{name}” trong mục Characters của project Flow.")
    state = {"clicked": 0.0}

    def selected() -> bool:
        info = menu.evaluate(_SELECTION_JS, name) or {}
        if info.get("active"):
            return not info.get("busy") and not info.get("others") and add.is_enabled()
        if time.time() - state["clicked"] >= 3:
            menu.evaluate(_CLICK_EXACT_JS, name)
            state["clicked"] = time.time()
        return False

    if not _wait(selected, _cap(deadline, 60), 1.0):
        raise give_up(f"Không chọn được nhân vật “{name}” để gắn vào chat của Flow.")
    add.click()
    if not _wait(lambda: page.locator(CHIP).count() > before, _cap(deadline, 60), 1.0):
        raise give_up(f"Không gắn được nhân vật “{name}” vào ô chat của Flow.")
    if menu.is_visible():
        page.keyboard.press("Escape")
    log(f"  đã gắn nhân vật Flow: {name}")


def _attach_refs(page, refs: list[str], deadline: Deadline | None = None) -> None:
    for kind, value in split_refs(refs):
        if kind == "character":
            _attach_character(page, value, deadline)
        else:
            _attach(page, Path(value).expanduser(), deadline)


RENAME_RPC = "rzMKMb"  # batchexecute call that saves entity_info.display_name (seen 2026-11-14)


def _set_character_name(page, name: str) -> None:
    """Type the name like a person: Flow only saves it (rzMKMb) on real key events, `fill()` changes the field
    without saving (measured 2026-11-14)."""
    inp = page.locator(CHAR_NAME)
    inp.wait_for(state="visible", timeout=30_000)
    edit = page.locator('flow-character-form button[aria-label="Edit name"]')
    if edit.count():
        edit.first.click()
        time.sleep(0.5)
    inp.click()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.press("Backspace")
    try:
        with page.expect_request(lambda r: RENAME_RPC in r.url or RENAME_RPC in (r.post_data or ""),
                                 timeout=15_000):
            page.keyboard.type(name, delay=20)
            page.keyboard.press("Enter")
    except Exception as exc:
        if type(exc).__name__ != "TimeoutError":
            raise
        log(f"  (chưa thấy Flow lưu tên “{name}”, vẫn tiếp tục)")
    if not _wait(lambda: inp.input_value().strip() == name, 10):
        raise Fail(EXIT_DRIFT, f"Không đặt được tên nhân vật “{name}” trên Flow.")
    time.sleep(1.5)


def _character_done(page) -> None:
    """Press Done on the character page (Flow saves the edits) and wait for it to leave the page."""
    done = page.locator("flow-character-edit-header button", has_text="Done")
    if not done.count():
        return
    url = page.url
    try:
        done.first.click()
        _wait(lambda: page.url != url, 10)
    except Exception:
        pass
    time.sleep(1.5)


def _character_image(page) -> str | None:
    """URL of the generated character picture, once Flow shows it."""
    return page.evaluate("""(sel) => {
      const i = document.querySelector(sel);
      // Seen both as flow-content.google/image/<id>?… and flow.google.com/asb/… (2026-11-14). Not `complete` /
      // `naturalWidth`: the driver's minimized window never decodes it. The download checks the bytes instead.
      return i && /flow-content\\.google\\/image\\/|\\/asb\\//.test(i.src || '') ? i.src : null;
    }""", CHAR_IMG)


def _download_url(ctx, url: str, out: Path) -> bool:
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


def run_char(args) -> dict:
    """Make a Flow character (Characters -> New character): generate its picture from the description, give it
    the film's name for the character, download the picture. Images cost no credits.
    --retire-id/--retire-name first renames an older character of the same name, so the name stays unique
    (the chat picker attaches characters by their exact name)."""
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise Fail(EXIT_USAGE, f"Python này chưa có Playwright ({sys.executable}). Hãy dùng Python của gflow-cli.") from exc

    with sync_playwright() as pw:
        window = "headless" if args.headless else args.window
        ctx, page = _session(pw, Path(args.profile_dir).expanduser(), args.channel, window, args.project)
        try:
            base = f"{FLOW}/project/{args.project}/character"
            if args.retire_id:
                try:
                    page.goto(f"{base}/{args.retire_id}", wait_until="domcontentloaded", timeout=90_000)
                    if _wait(lambda: page.locator(CHAR_NAME).count() > 0, 30):
                        _set_character_name(page, args.retire_name)
                        _character_done(page)  # leaving right away loses the new name
                        log(f"  đổi tên nhân vật cũ trên Flow thành “{args.retire_name}”")
                except Exception as exc:  # never blocks making the new one
                    log(f"  (không đổi tên được nhân vật cũ: {getattr(exc, 'detail', None) or str(exc).splitlines()[0]})")
            page.goto(base, wait_until="domcontentloaded", timeout=90_000)
            if not _wait(lambda: page.locator(CHAR_EDITOR).count() > 0, 60):
                raise Fail(EXIT_DRIFT, "Không thấy trang tạo nhân vật (Characters → New character) của Flow.")
            page.locator(CHAR_EDITOR).click()
            page.keyboard.insert_text(args.prompt)
            time.sleep(0.5)
            if args.dry_run:
                log("  (dry-run) đã gõ mô tả nhân vật, không bấm tạo.")
                return {"status": "ok", "dry_run": True}
            send = page.locator(CHAR_SEND)
            if not _wait(lambda: send.is_enabled(), 15):
                raise Fail(EXIT_DRIFT, "Nút tạo nhân vật của Flow không bật lên.")
            send.click()
            log("  đã gửi mô tả nhân vật, chờ Flow tạo ảnh…")
            started, src, entity = time.time(), None, None
            while time.time() - started < args.timeout:
                m = CHAR_ID_RE.search(page.url)
                entity = m.group(1) if m else entity
                try:
                    # The minimized window draws no frames, and the character page only shows the new picture
                    # on the next frame (seen 2026-11-14): a tiny capture makes Chrome draw one.
                    page.screenshot(type="jpeg", quality=10, clip={"x": 0, "y": 0, "width": 8, "height": 8},
                                    timeout=10_000)
                except Exception:
                    pass
                try:
                    src = _character_image(page) if entity else None
                except Exception as exc:
                    if not _transient(exc):
                        raise
                if src:
                    break
                panel = page.locator(CHAR_PANEL)
                text = panel.first.inner_text() if entity and panel.count() else ""
                if text and CHAR_FAILED_RE.search(text):
                    raise Fail(EXIT_GENERATION_FAILED, "Flow báo không tạo được ảnh nhân vật: " + text.strip()[:400])
                time.sleep(2)
            if not src:
                shot = out.with_name(out.stem + ".flow-error.png")
                try:
                    page.screenshot(path=str(shot))
                    seen = page.evaluate("s => Array.from(document.querySelectorAll(s)).map(i => (i.src || '').slice(0, 90))",
                                         CHAR_IMG)
                    log(f"  (ảnh màn hình Flow lúc lỗi: {shot}; trang: {page.url}; ảnh: {seen})")
                except Exception:
                    pass
                raise Fail(EXIT_TIMEOUT, f"Quá {args.timeout}s mà Flow chưa tạo xong ảnh nhân vật.")
            _set_character_name(page, args.name)
            if args.info:
                info = page.locator(CHAR_INFO)
                if info.count():
                    info.first.fill(args.info)
                    info.first.evaluate("e => e.blur()")
                    time.sleep(1.0)
            time.sleep(2)  # let Flow publish the full-size file behind the preview
            if not (_download_url(ctx, src, out) or ("/asb/" in src and _download_url(ctx, src + "=d", out))):
                raise Fail(EXIT_DRIFT, "Flow đã tạo nhân vật nhưng không tải được ảnh về.")
            _character_done(page)
            media = re.search(r"/image/([0-9a-fA-F-]{36})", src)
            log(f"  đã tạo nhân vật Flow “{args.name}” ({entity[:8]}…), ảnh: {out} ({out.stat().st_size // 1024} KB)")
            return {"status": "ok", "local_path": str(out), "entity_id": entity, "name": args.name,
                    "media_id": media.group(1) if media else None}
        finally:
            try:
                ctx.close()
            except Exception:
                pass


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
    POLL["tile"] = True
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


START_MATCH_MIN = 0.75  # first frame vs start picture: right clip ~0.99, another scene's picture <= 0.45
_THUMB = (64, 36)


def _gray_thumb(path: Path, *, video: bool) -> list[int] | None:
    """A tiny grey picture (64x36) of an image, or of a video's first frame; None when ffmpeg cannot read it."""
    import subprocess

    w, h = _THUMB
    cmd = ["ffmpeg", "-loglevel", "error", "-i", str(path), "-frames:v", "1",
           "-vf", f"scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"]
    try:
        data = subprocess.run(cmd, capture_output=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return list(data) if len(data) == w * h else None


def frame_match(image: Path, clip: Path) -> float | None:
    """How alike the clip's first frame and the start picture are (correlation, -1..1); None if unknown."""
    a, b = _gray_thumb(image, video=False), _gray_thumb(clip, video=True)
    if a is None or b is None:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    da, db = [x - ma for x in a], [y - mb for y in b]
    den = math.sqrt(sum(x * x for x in da) * sum(y * y for y in db))
    return sum(x * y for x, y in zip(da, db)) / den if den else None


def check_start_frame(image: Path, out: Path) -> None:
    """Veo starts the clip on the attached picture. A clip that does not start on OUR picture was made from
    another upload: keep it aside as <id>.mismatch.mp4 and fail (credits are spent; never take it as the scene)."""
    score = frame_match(image, out)
    if score is None or score >= START_MATCH_MIN:
        return
    aside = out.with_name(out.stem + ".mismatch" + out.suffix)
    out.replace(aside)
    raise Fail(EXIT_GENERATION_FAILED, f"Clip Flow trả về không bắt đầu từ ảnh mở cảnh (độ khớp {score:.2f}): Flow đã "
               f"dùng nhầm ảnh khác. Đã để riêng clip đó ở {aside.name}; render lại cảnh này.", spent=True)


def run_i2v(args) -> dict:
    dl = Deadline(args.timeout)  # first: the budget counts from the moment the driver starts
    image = Path(args.image).expanduser() if args.image else None  # None = reference-to-video (characters only)
    refs = list(args.ref or [])
    if image is None and not refs:
        raise Fail(EXIT_USAGE, "cần ảnh bắt đầu (--image) hoặc ít nhất một ảnh/nhân vật tham chiếu (--ref)")
    to_check = [p for k, v in split_refs(refs) if k == "file" for p in (Path(v).expanduser(),)]
    for p in ([image] if image is not None else []) + to_check:
        if not p.is_file():
            raise Fail(EXIT_USAGE, f"không thấy ảnh {p}")
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(args.prompt, refs=len(refs), aspect=args.aspect, model=args.model, duration=args.duration,
                          image=image is not None)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise Fail(EXIT_USAGE, f"Python này chưa có Playwright ({sys.executable}). Hãy dùng Python của gflow-cli.") from exc

    files = ([str(image)] if image is not None else []) + refs
    with sync_playwright() as pw:
        window = "headless" if args.headless else args.window
        ctx, page = _session(pw, Path(args.profile_dir).expanduser(), args.channel, window, args.project, deadline=dl)
        try:
            result = _i2v_on_page(page, args, prompt, files, out, dl)
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    if result.get("status") == "ok" and not result.get("dry_run") and image is not None:
        check_start_frame(image, out)  # only image-to-video must start on the attached frame
    return result


def _i2v_on_page(page, args, prompt: str, files: list[str], out: Path, dl: Deadline) -> dict:
    if page.locator(NEW_SESSION_BTN).count():
        page.locator(NEW_SESSION_BTN).first.click()  # fresh chat: no context from earlier scenes
        time.sleep(1.5)
    if page.locator(CLEAR_BTN).count() and page.locator(CLEAR_BTN).first.is_visible():
        page.locator(CLEAR_BTN).first.click()
        time.sleep(0.5)

    _attach_refs(page, files, dl)

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


def _queue_tail(out: Path) -> str:
    return (f"Video có thể vẫn đang tạo trên Flow: mở Flow kiểm tra, tải video về lưu thành {out} rồi chạy lại "
            "(app tự nhận clip, không tốn credit).")


def _wait_video(page, titles_before: list[str], bubbles_before: int, out: Path, dl: Deadline,
                credits: int | None) -> dict:
    """Wait for the clip until the deadline. "failed" is exit 42, out of time is exit 9; both spent=True.
    "queued" waits at most QUEUE_WAIT for Flow to start (a new tile), else exit 9 so the run goes on to the next
    scene; once the tile is there it keeps waiting until the deadline."""
    approved_at = time.time()
    queued_seen = audio_seen = queued_logged = False
    queued_at: float | None = None
    POLL["tile"] = False
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
                queued_at = time.time()
                mins = math.ceil(min(QUEUE_WAIT, dl.left()) / 60)
                log(f"  Flow báo đang xếp hàng (đông người dùng) — tiếp tục chờ tối đa {mins} phút; Flow chưa bắt đầu "
                    "tạo thì bỏ qua cảnh này")
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
        if queued_at is not None and not POLL["tile"] and time.time() - queued_at >= QUEUE_WAIT:
            raise Fail(EXIT_TIMEOUT, f"Flow vẫn xếp hàng (đông người dùng) sau {math.ceil(QUEUE_WAIT / 60)} phút, chưa bắt "
                       "đầu tạo video nên bỏ qua cảnh này. " + _queue_tail(out)
                       + (" " + AUDIO_HINT if audio_seen else ""), spent=True)
        time.sleep(min(5.0, dl.left()))
    mins = round((time.time() - approved_at) / 60)
    tail = _queue_tail(out)
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
    refs = list(args.ref or [])
    if not refs:
        raise Fail(EXIT_USAGE, "cần ít nhất một ảnh tham chiếu (--ref)")
    for kind, value in split_refs(refs):
        if kind == "file" and not Path(value).expanduser().is_file():
            raise Fail(EXIT_USAGE, f"không thấy ảnh {value}")
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
            _attach_refs(page, refs)
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
    a.add_argument("--image", help="start frame (image-to-video); omit for reference-to-video (characters only)")
    a.add_argument("--ref", action="append")
    a.add_argument("--duration", type=int)
    a.add_argument("--max-credits", type=int, default=0, help="reject Flow's offer above this many credits (0 = no limit)")
    b = sub.add_parser("i2i", help="one image from a prompt + character reference images (free)")
    common(b, 300)
    b.add_argument("--ref", action="append", required=True)
    c = sub.add_parser("char", help="a Flow character (Characters -> New character) from a description (free)")
    common(c, 300)
    c.add_argument("--name", required=True)
    c.add_argument("--info", help="Character info (personality) shown to the Flow agent")
    c.add_argument("--retire-id", help="rename this older character of the project first")
    c.add_argument("--retire-name", default="")
    args = ap.parse_args(argv)
    try:
        result = {"i2i": run_i2i, "char": run_char}.get(args.cmd, run_i2v)(args)
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
