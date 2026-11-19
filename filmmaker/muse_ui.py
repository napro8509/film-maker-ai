"""Video (and images) from Meta's Muse agent (muse.ai), by driving its chat page.

Muse has no public video API: in the browser you ask the agent in chat ("make a 10 second video of ...",
optionally with a start picture attached) and it answers with the clip. This script does that with
Playwright, using its OWN signed-in browser profile (default ~/.filmmaker/muse-profile), so it never
locks gflow's profile while a Flow render is running.

Standalone (stdlib + Playwright only) so it runs with gflow-cli's Python, like flow_ui.py.
It prints one JSON document on stdout, shaped like flow_ui's:
  {"status": "ok", "local_path": ...}
  {"status": "fail", "error": {"exit_code": N, "detail": ..., "retryable": false, "spent": bool}}

Subcommands:
  login  open a visible window on muse.ai and wait until you have signed in (one time)
  probe  open the chat page and dump its controls (for fixing selectors when Muse changes)
  i2v    make one video: prompt, optional start image, download the mp4
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

MUSE = "https://muse.ai"
DEFAULT_PROFILE = Path("~/.filmmaker/muse-profile").expanduser()

EXIT_USAGE, EXIT_AUTH, EXIT_TIMEOUT, EXIT_DRIFT = 2, 8, 9, 23
EXIT_AGENT_DECLINED, EXIT_GENERATION_FAILED = 41, 42

SPENT = {"sent": False}  # set once the request was sent to Muse


class Fail(Exception):
    def __init__(self, code: int, detail: str, *, spent: bool = False):
        super().__init__(detail)
        self.code, self.detail, self.spent = code, detail, spent


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise Fail(EXIT_USAGE, f"Python này chưa có Playwright ({sys.executable}). Hãy dùng Python của gflow-cli.") from exc
    return sync_playwright


def _open(pw, profile: Path, channel: str | None, headless: bool):
    profile.mkdir(parents=True, exist_ok=True)
    kw = dict(headless=headless, viewport={"width": 1440, "height": 900}, accept_downloads=True,
              args=["--disable-blink-features=AutomationControlled"])
    last: Exception | None = None
    for ch in ([channel, None] if channel else [None]):
        try:
            ctx = pw.chromium.launch_persistent_context(str(profile), **({"channel": ch} if ch else {}), **kw)
            _restore_cookies(ctx, profile)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            return ctx, page
        except Exception as exc:
            last = exc
            log(f"  (không mở được trình duyệt {ch or 'chromium'}: {str(exc).splitlines()[0]})")
    if last and ("ProcessSingleton" in str(last) or "SingletonLock" in str(last)):
        raise Fail(EXIT_USAGE, f"Profile Muse ({profile}) đang được dùng ở cửa sổ khác. Đóng nó rồi thử lại.")
    raise Fail(EXIT_USAGE, f"Không mở được trình duyệt: {last}")


def _logged_out(page) -> bool:
    """Signed out, muse.ai shows its marketing page: a 'Log in or create an account' form, or a remembered
    account with a 'Log in' button (and a 'Log in' button in the header either way)."""
    try:
        return (page.get_by_text("Log in or create an account").count() > 0
                or page.get_by_role("button", name="Log in", exact=True).count() > 0
                or page.locator('button[aria-label^="Log in as"]').count() > 0
                # the steps in between: security code, password, "Mobile number or email"
                or page.locator('input[aria-label*="security code" i], input[type=password], '
                                'input[autocomplete="one-time-code"]').count() > 0
                or page.get_by_text("Mobile number or email").count() > 0)
    except Exception:
        return False


def _state_file(profile: Path) -> Path:
    """Meta's login cookies are session cookies, which Chrome drops when the window closes; `login` saves
    them here and every later run puts them back."""
    return profile / "muse-state.json"


def _restore_cookies(ctx, profile: Path) -> None:
    f = _state_file(profile)
    if not f.is_file():
        return
    try:
        cookies = json.loads(f.read_text()).get("cookies") or []
        if cookies:
            ctx.add_cookies(cookies)
    except Exception as exc:
        log(f"  (không nạp được cookie Muse đã lưu: {exc})")


def run_login(args) -> dict:
    with _playwright()() as pw:
        ctx, page = _open(pw, Path(args.profile_dir).expanduser(), args.channel, headless=False)
        try:
            page.goto(MUSE, wait_until="domcontentloaded", timeout=90_000)
            log("Hãy đăng nhập Muse trong cửa sổ vừa mở. ĐỪNG đóng cửa sổ: vào được trang chat là tool tự lưu và đóng.")
            profile = Path(args.profile_dir).expanduser()
            end = time.time() + args.timeout
            seen_form = False
            gone = 0
            while time.time() < end:
                time.sleep(3)
                try:
                    if page.is_closed():
                        break
                    out = _logged_out(page)
                except Exception:
                    break
                seen_form = seen_form or out
                # Only trust "signed in" after the login form was on screen and then stayed gone for ~25 s
                # (right after goto the marketing page is not rendered yet, which looks signed in too).
                gone = 0 if out else gone + 1
                if seen_form and gone >= 8:
                    ctx.storage_state(path=str(_state_file(profile)))
                    _state_file(profile).chmod(0o600)
                    log("  đã lưu phiên đăng nhập Muse")
                    break
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    if _check_signed_in(args):
        return {"status": "ok", "detail": "đã đăng nhập Muse"}
    raise Fail(EXIT_AUTH, "Chưa thấy đăng nhập Muse xong.")


def _check_signed_in(args) -> bool:
    """Reopen the profile headless and look again, after the page had time to render."""
    with _playwright()() as pw:
        ctx, page = _open(pw, Path(args.profile_dir).expanduser(), args.channel, headless=True)
        try:
            page.goto(MUSE, wait_until="domcontentloaded", timeout=90_000)
            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            time.sleep(4)
            return not _logged_out(page)
        finally:
            ctx.close()


PROBE_JS = """() => {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('button, a, input, textarea, [contenteditable=true], [role=button], [role=textbox], video')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const d = {tag: el.tagName.toLowerCase(), aria: el.getAttribute('aria-label'), role: el.getAttribute('role'),
               type: el.getAttribute('type'), ph: el.getAttribute('placeholder'), testid: el.getAttribute('data-testid'),
               text: (el.innerText || el.value || '').trim().slice(0, 60), href: el.getAttribute('href'),
               src: el.tagName === 'VIDEO' ? (el.currentSrc || el.src || '').slice(0, 120) : null,
               x: Math.round(r.x), y: Math.round(r.y)};
    const k = JSON.stringify(d);
    if (!seen.has(k)) { seen.add(k); out.push(d); }
  }
  return out;
}"""


def run_probe(args) -> dict:
    with _playwright()() as pw:
        ctx, page = _open(pw, Path(args.profile_dir).expanduser(), args.channel, headless=args.headless)
        try:
            page.goto(args.url or MUSE, wait_until="domcontentloaded", timeout=90_000)
            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            time.sleep(args.settle)
            if _logged_out(page):
                raise Fail(EXIT_AUTH, "Profile Muse chưa đăng nhập. Chạy: muse_ui.py login")
            shot = Path(args.out).expanduser() if args.out else None
            if shot:
                shot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(shot))
            return {"status": "ok", "url": page.url, "title": page.title(), "controls": page.evaluate(PROBE_JS),
                    "screenshot": str(shot) if shot else None}
        finally:
            ctx.close()


# Selectors, checked on 2026-11-17.
COMPOSER = 'textarea[aria-label="Message"]'
FILE_INPUT = 'input[type=file]'
SEND_NAMES = ("Send", "Send message", "Submit")

#: Every <video> in the page with its src, box, and whether it comes AFTER the last chat bubble holding `marker`
#: (our request). Old clips higher up in the chat load lazily and get a fresh blob: URL on every page load, so
#: "a src we had not seen" is not enough: only a video below our own message is the answer to it. Muse's avatar
#: (header, status panel) is a looping video too; it is told apart by size / position.
VIDEOS_JS = """([marker, head, sel]) => {
  // the deepest nodes holding a text (a bubble's own text node container, not its ancestors)
  const deepest = (t) => [...document.querySelectorAll('div, p, span')].filter(e =>
      (e.innerText || '').includes(t) && ![...e.children].some(c => (c.innerText || '').includes(t)));
  const FOLLOWS = Node.DOCUMENT_POSITION_FOLLOWING;
  let anchor = null, bound = null;
  if (marker) {
    const hits = deepest(marker);
    anchor = hits.length ? hits[hits.length - 1] : null;
    // the next request we sent after this one: a video below THAT belongs to the later request
    if (anchor && head) bound = deepest(head).find(e => anchor.compareDocumentPosition(e) & FOLLOWS) || null;
  }
  return [...document.querySelectorAll(sel || 'video')].map(v => {
    const r = v.getBoundingClientRect();
    const after = marker ? !!(anchor && (anchor.compareDocumentPosition(v) & FOLLOWS)) : true;
    const before = !bound || !!(v.compareDocumentPosition(bound) & FOLLOWS);
    return {src: v.currentSrc || v.src || (v.querySelector('source') || {}).src || '', w: Math.round(r.width),
            h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y), after: after && before};
  });
}"""

#: How many "Failed to create …" task rows Muse's activity panel shows (a render that failed on Muse's side).
FAILED_JS = """() => (document.body.innerText.match(/Failed to (create|generate)/gi) || []).length"""

#: Muse's chat text below our request (its answer when it writes instead of sending a video). Only the chat
#: column (left of the status panel), only text that comes after the bubble holding `marker`.
REPLY_AFTER_JS = """(marker) => {
  const deepest = [...document.querySelectorAll('div, p, span')].filter(e =>
      (e.innerText || '').includes(marker) && ![...e.children].some(c => (c.innerText || '').includes(marker)));
  const anchor = deepest.length ? deepest[deepest.length - 1] : null;
  if (!anchor) return '';
  // the composer (its "Message Muse" placeholder, the "Still sending" note) is not part of Muse's answer
  const comp = document.querySelector('textarea[aria-label="Message"]');
  let box = comp ? (comp.closest('form') || comp.parentElement) : null;
  for (let i = 0; box && !comp.closest('form') && i < 3 && box.parentElement; i++) box = box.parentElement;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const out = [];
  let n;
  while ((n = walker.nextNode())) {
    if (!(anchor.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING) || anchor.contains(n)) continue;
    if (box && box.contains(n)) continue;
    const el = n.parentElement;
    if (!el) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || r.x > 1000) continue;
    const t = n.textContent.trim();
    if (t) out.push(t);
  }
  return out.join(' ').slice(0, 1500);
}"""

DECLINE_RE = r"can't|cannot|can not|unable|not able|won't|not allowed|policy|sorry|limit reached|out of credits|try again later"


#: Every request starts with this, so the next request bubble bounds where an answer can be (fetch, i2v, image).
REQUEST_HEAD = "Make exactly 1 "


def build_image_prompt(description: str, *, refs: int, aspect: str) -> str:
    parts = [REQUEST_HEAD + "image now with your image generation tool (Muse Image). "
             "Do not ask me any questions and do not describe it, just generate it and send me the image."]
    if refs:
        parts.append(f"The {refs} attached image(s) are references, in the order they are attached: keep the "
                     "characters' faces, hair and clothes and the places exactly as in them.")
    parts.append(f"Image: aspect ratio {aspect}, one single frame (no collage, no text, no watermark).")
    parts.append("Description: " + description.strip())
    return " ".join(parts)


def build_prompt(motion: str, *, image: bool, aspect: str, duration: int | None, refs: int = 0) -> str:
    parts = [REQUEST_HEAD + "video now with your video generation tool (Muse Video), with sound. "
             "Do not ask me any questions, do not plan or write a script, just generate it and send me the video."]
    if image:
        parts.append(("The first attached image" if refs else "Use the attached image") +
                     (" is the exact first frame" if refs else " as the exact first frame") +
                     ": keep the same people, faces, clothes, lighting and setting.")
    if refs:
        which = f"The other {refs} attached image(s)" if image else f"The {refs} attached image(s)"
        parts.append(f"{which} are character references: keep those characters' faces, hair and clothes identical.")
    spec = [f"aspect ratio {aspect}"]
    if duration:
        spec.append(f"{duration} seconds long")
    parts.append("Video: " + ", ".join(spec) + ".")
    parts.append("Scene and action: " + motion.strip())
    return " ".join(parts)


def _marker(prompt: str) -> str:
    """A piece of our own message to find its bubble by (the tail: the scene text, unique per request)."""
    return prompt.strip()[-80:]


#: Seconds Muse's written answer must stay unchanged (and no video) before it counts as its final answer.
REPLY_SETTLE = 60


def _asks_or_declines(text: str) -> bool:
    import re
    t = text.strip()
    if re.search(r"generating|creating|working on|rendering|in progress|on it", t, re.I) and "?" not in t:
        return False
    return t.endswith("?") or bool(re.search(DECLINE_RE, t, re.I))


def _wait_for(fn, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _new_videos(page, before: set[str], marker: str = "", sel: str = "video") -> list[dict]:
    """New videos (or, with sel="img", pictures) Muse sent as the answer to the request holding `marker`."""
    out = []
    for v in page.evaluate(VIDEOS_JS, [marker, REQUEST_HEAD if marker else "", sel]):
        if not v.get("after", True):
            continue
        if not v["src"] or v["src"] in before or "/videos/avatars/" in v["src"]:
            continue
        if v["w"] < 120 or v["h"] < 120 or v["x"] > 1000:  # avatar / icons in the header / status panel
            continue
        out.append(v)
    return out


def _download(page, ctx, src: str, out: Path) -> None:
    if src.startswith("data:"):
        import base64
        out.write_bytes(base64.b64decode(src.split(",", 1)[1]))
    elif src.startswith("blob:"):
        data = page.evaluate("""async (u) => { const b = await (await fetch(u)).blob();
            return await new Promise(r => { const f = new FileReader(); f.onload = () => r(f.result.split(',')[1]);
            f.readAsDataURL(b); }); }""", src)
        import base64
        out.write_bytes(base64.b64decode(data))
    else:
        resp = ctx.request.get(src, timeout=180_000)
        if not resp.ok:
            raise Fail(EXIT_GENERATION_FAILED, f"Tải file Muse lỗi HTTP {resp.status}", spent=True)
        out.write_bytes(resp.body())


def _to_png_if_needed(out: Path) -> None:
    """Muse sends WebP; the pipeline's keyframes are PNG/JPEG. Convert in place with ffmpeg when the name asks
    for .png/.jpg (kept as WebP if ffmpeg is missing: most tools still read it)."""
    head = out.read_bytes()[:12]
    if not (head[:4] == b"RIFF" and head[8:12] == b"WEBP") or out.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        return
    import shutil
    import subprocess
    ff = shutil.which("ffmpeg")
    if not ff:
        return
    tmp = out.with_name(out.stem + ".muse.webp")
    out.replace(tmp)
    r = subprocess.run([ff, "-loglevel", "error", "-y", "-i", str(tmp), str(out)], capture_output=True)
    if r.returncode == 0 and out.is_file():
        tmp.unlink()
    else:
        tmp.replace(out)


#: Where screenshots of a failed request go (--fail-shots), even without --debug-dir: set by _generate.
FAIL_SHOTS: dict = {"dir": None}


def _shot(page, debug: Path | None, name: str) -> None:
    dirs = [d for d in (debug, FAIL_SHOTS["dir"] if name.startswith("9-") else None) if d]
    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(d / f"{name}.png"))
        except Exception:
            pass


def run_i2v(args) -> dict:
    return _generate(args, "video")


def run_image(args) -> dict:
    args.image, args.duration = None, None
    return _generate(args, "image")


def _generate(args, kind: str) -> dict:
    """One video (kind "video") or one picture (kind "image") from the Muse chat, saved at args.out."""
    end = time.time() + args.timeout
    image = Path(args.image).expanduser() if getattr(args, "image", None) else None
    refs = [Path(r).expanduser() for r in args.ref or []]
    for f in ([image] if image else []) + refs:
        if not f.is_file():
            raise Fail(EXIT_USAGE, f"không thấy ảnh {f}")
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    debug = Path(args.debug_dir).expanduser() if args.debug_dir else None
    if debug:
        debug.mkdir(parents=True, exist_ok=True)
    FAIL_SHOTS["dir"] = Path(args.fail_shots).expanduser() if getattr(args, "fail_shots", None) else None
    if kind == "image":
        prompt, sel, word, min_size = build_image_prompt(args.prompt, refs=len(refs), aspect=args.aspect), "img", "ảnh", 5_000
    else:
        prompt = build_prompt(args.prompt, image=image is not None, aspect=args.aspect, duration=args.duration,
                              refs=len(refs))
        sel, word, min_size = "video", "video", 10_000
    with _playwright()() as pw:
        ctx, page = _open(pw, Path(args.profile_dir).expanduser(), args.channel, headless=args.window == "headless")
        try:
            page.goto(MUSE, wait_until="domcontentloaded", timeout=90_000)
            box = page.locator(COMPOSER).first
            try:
                box.wait_for(state="visible", timeout=60_000)
            except Exception:
                if _logged_out(page):
                    raise Fail(EXIT_AUTH, "Profile Muse chưa đăng nhập (hoặc hết phiên). Chạy: muse_ui.py login")
                raise Fail(EXIT_DRIFT, "Không thấy ô chat của Muse (giao diện có thể đã thay đổi).")
            time.sleep(3)
            marker = _marker(prompt)
            before = {v["src"] for v in page.evaluate(VIDEOS_JS, ["", "", sel]) if v["src"]}
            # Muse's activity panel (with older "Failed to create" rows) loads a moment after the chat: count
            # those rows only once it is there, or an old row would look like a new failure.
            _wait_for(lambda: page.get_by_text("Today", exact=True).count() > 0, 15)
            failed_before = page.evaluate(FAILED_JS)
            files = [str(f) for f in ([image] if image else []) + refs]
            if files:
                try:
                    page.locator(FILE_INPUT).first.set_input_files(files)
                except Exception as exc:  # an input without `multiple`: attach the pictures one by one
                    if len(files) == 1 or "multiple" not in str(exc).lower():
                        raise
                    for f in files:
                        page.locator(FILE_INPUT).first.set_input_files(f)
                        time.sleep(2)
                time.sleep(3 + len(files))  # let the upload previews appear
            box.click()
            box.fill(prompt)
            _shot(page, debug, "1-typed")
            if args.dry_run:
                return {"status": "ok", "dry_run": True, "prompt": prompt}
            sent = False
            for name in SEND_NAMES:
                btn = page.get_by_role("button", name=name, exact=True)
                if btn.count() and btn.first.is_enabled():
                    btn.first.click()
                    sent = True
                    break
            if not sent:
                box.press("Enter")
            SPENT["sent"] = True
            log(f"  đã gửi yêu cầu lên Muse, chờ {word}…")
            time.sleep(5)
            _shot(page, debug, "2-sent")
            last_reply, n, reply_at = "", 0, time.time()
            while time.time() < end:
                vids = _new_videos(page, before, marker, sel)
                if vids:
                    time.sleep(3 if kind == "video" else 8)  # the player / a progressive image swaps in the final src
                    vids = _new_videos(page, before, marker, sel) or vids
                    src = vids[-1]["src"] if kind == "video" else vids[0]["src"]
                    log(f"  có {word}: {src[:100]}")
                    _shot(page, debug, "3-result")
                    _download(page, ctx, src, out)
                    if kind == "image":
                        _to_png_if_needed(out)
                    if out.stat().st_size < min_size:
                        raise Fail(EXIT_GENERATION_FAILED, f"File {word} Muse tải về quá nhỏ", spent=True)
                    return {"status": "ok", "local_path": str(out), "reply": page.evaluate(REPLY_AFTER_JS, marker)[:400]}
                if page.evaluate(FAILED_JS) > failed_before:
                    time.sleep(4)  # Muse writes a line about the failure right after the task row
                    _shot(page, debug, "9-failed")
                    said = (page.evaluate(REPLY_AFTER_JS, marker) or "").replace("\n", " ").strip()
                    raise Fail(EXIT_GENERATION_FAILED, f"Muse báo không tạo được {word} này (Failed to create…)."
                               + (f" Muse: {said[:300]}" if said else ""), spent=False)
                reply = page.evaluate(REPLY_AFTER_JS, marker)
                if reply != last_reply:
                    last_reply, reply_at = reply, time.time()
                    if reply:
                        log("  Muse: " + reply.replace("\n", " ")[:200])
                elif reply and time.time() - reply_at > REPLY_SETTLE and _asks_or_declines(reply):
                    # Muse wrote back (a question / a refusal) and nothing has been generating since
                    _shot(page, debug, "9-declined")
                    raise Fail(EXIT_AGENT_DECLINED, f"Muse không tạo {word}, trả lời: " + reply[:600], spent=False)
                n += 1
                if debug and n % 6 == 0:
                    _shot(page, debug, f"wait-{n:03d}")
                time.sleep(5)
            _shot(page, debug, "9-timeout")
            import re
            if last_reply and re.search(DECLINE_RE, last_reply, re.I):
                raise Fail(EXIT_AGENT_DECLINED, f"Muse không tạo {word}: " + last_reply[:500], spent=False)
            raise Fail(EXIT_TIMEOUT, f"Hết thời gian chờ {word} từ Muse. Trả lời cuối: " + last_reply[:300], spent=True)
        finally:
            ctx.close()


def run_fetch(args) -> dict:
    """Download the clip Muse made for an earlier request (e.g. after i2v timed out): the first video
    below the last chat bubble that contains --marker. Sends nothing, spends nothing."""
    end = time.time() + args.timeout
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    with _playwright()() as pw:
        ctx, page = _open(pw, Path(args.profile_dir).expanduser(), args.channel, headless=True)
        try:
            page.goto(MUSE, wait_until="domcontentloaded", timeout=90_000)
            try:
                page.locator(COMPOSER).first.wait_for(state="visible", timeout=60_000)
            except Exception:
                if _logged_out(page):
                    raise Fail(EXIT_AUTH, "Profile Muse chưa đăng nhập (hoặc hết phiên). Chạy: muse_ui.py login")
                raise
            while time.time() < end:
                vids = _new_videos(page, set(), args.marker)
                if vids:
                    time.sleep(3)
                    vids = _new_videos(page, set(), args.marker) or vids
                    _download(page, ctx, vids[0]["src"], out)
                    return {"status": "ok", "local_path": str(out)}
                time.sleep(5)
            raise Fail(EXIT_TIMEOUT, "Chưa thấy video nào sau tin nhắn đó.")
        finally:
            ctx.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(a) -> None:
        a.add_argument("--profile-dir", default=str(DEFAULT_PROFILE))
        a.add_argument("--channel", default="chrome")

    a = sub.add_parser("login", help="sign in to muse.ai once in the tool's own browser profile")
    common(a)
    a.add_argument("--timeout", type=int, default=900)
    p = sub.add_parser("probe", help="dump the chat page's controls (for fixing selectors)")
    common(p)
    p.add_argument("--url")
    p.add_argument("-o", "--out", help="save a screenshot here")
    p.add_argument("--settle", type=float, default=6)
    p.add_argument("--headless", action="store_true")
    v = sub.add_parser("i2v", help="one video from a prompt (+ optional start image)")
    common(v)
    v.add_argument("--prompt", required=True)
    v.add_argument("--image", help="start frame")
    v.add_argument("--ref", action="append", help="character reference picture, repeatable")
    v.add_argument("--aspect", default="9:16")
    v.add_argument("--duration", type=int, default=10)
    v.add_argument("-o", "--out", required=True)
    v.add_argument("--window", choices=["show", "headless"], default="headless")
    v.add_argument("--timeout", type=int, default=900)
    v.add_argument("--debug-dir", help="save screenshots of each step here")
    v.add_argument("--fail-shots", help="save a screenshot here only when the request fails")
    v.add_argument("--dry-run", action="store_true", help="attach + type only, do not send")
    im = sub.add_parser("image", help="one picture from a prompt (+ reference pictures)")
    common(im)
    im.add_argument("--prompt", required=True)
    im.add_argument("--ref", action="append", help="reference picture (character / place), repeatable")
    im.add_argument("--aspect", default="9:16")
    im.add_argument("-o", "--out", required=True)
    im.add_argument("--window", choices=["show", "headless"], default="headless")
    im.add_argument("--timeout", type=int, default=600)
    im.add_argument("--debug-dir", help="save screenshots of each step here")
    im.add_argument("--fail-shots", help="save a screenshot here only when the request fails")
    im.add_argument("--dry-run", action="store_true", help="attach + type only, do not send")
    f = sub.add_parser("fetch", help="download the clip Muse made for an earlier message (sends nothing)")
    common(f)
    f.add_argument("--marker", required=True, help="text from that message (e.g. the end of the scene prompt)")
    f.add_argument("-o", "--out", required=True)
    f.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args(argv)
    try:
        result = {"login": run_login, "probe": run_probe, "i2v": run_i2v, "image": run_image, "fetch": run_fetch}[args.cmd](args)
        code = 0
    except Fail as f:
        result = {"status": "fail", "error": {"exit_code": f.code, "detail": f.detail, "retryable": False, "spent": f.spent}}
        code = f.code
    except Exception as exc:
        result = {"status": "fail", "error": {"exit_code": EXIT_DRIFT, "detail": f"{type(exc).__name__}: {exc}".strip()[:800],
                                              "retryable": False, "spent": SPENT["sent"]}}
        code = EXIT_DRIFT
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
