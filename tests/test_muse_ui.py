"""muse_ui: prompt building, telling Muse's new clip apart from its avatar videos, sign-in detection."""
import json

from filmmaker import muse_ui


def test_prompt_with_start_image_asks_for_exact_first_frame():
    p = muse_ui.build_prompt("A cat yawns.", image=True, aspect="9:16", duration=10)
    assert "attached image as the exact first frame" in p
    assert "aspect ratio 9:16, 10 seconds long" in p
    assert p.endswith("Scene and action: A cat yawns.")
    assert "Do not ask me any questions" in p


def test_prompt_without_image_has_no_attachment_line():
    p = muse_ui.build_prompt("  A dog runs.  ", image=False, aspect="16:9", duration=None)
    assert "attached" not in p
    assert "aspect ratio 16:9." in p
    assert p.endswith("A dog runs.")


class FakePage:
    def __init__(self, videos=(), counts=None):
        self.videos = list(videos)
        self.counts = counts or {}

    def evaluate(self, js, *a):
        return self.videos

    def get_by_text(self, text):
        return FakeLoc(self.counts.get(text, 0))

    def get_by_role(self, role, name=None, exact=False):
        return FakeLoc(self.counts.get(f"{role}:{name}", 0))

    def locator(self, sel):
        return FakeLoc(self.counts.get("css" if "security code" in sel else sel, 0))


class FakeLoc:
    def __init__(self, n):
        self.n = n

    def count(self):
        return self.n


def test_new_videos_skips_avatar_old_and_tiny():
    page = FakePage([
        {"src": "https://muse.ai/videos/avatars/hatch_working.mp4", "w": 400, "h": 400, "x": 300, "y": 100},
        {"src": "blob:https://muse.ai/avatar", "w": 128, "h": 128, "x": 1197, "y": 70},  # status panel
        {"src": "blob:https://muse.ai/old", "w": 300, "h": 500, "x": 300, "y": 100},
        {"src": "blob:https://muse.ai/small", "w": 40, "h": 40, "x": 548, "y": 12},
        {"src": "https://video.fbcdn.net/new.mp4", "w": 300, "h": 530, "x": 260, "y": 200},
    ])
    new = muse_ui._new_videos(page, before={"blob:https://muse.ai/old"})
    assert [v["src"] for v in new] == ["https://video.fbcdn.net/new.mp4"]


def test_new_videos_only_below_our_own_message():
    """An old clip higher in the chat gets a fresh blob: URL each page load; only one below our request counts."""
    page = FakePage([
        {"src": "blob:https://muse.ai/reloaded-old", "w": 280, "h": 280, "x": 216, "y": 78, "after": False},
        {"src": "blob:https://muse.ai/ours", "w": 280, "h": 500, "x": 216, "y": 760, "after": True},
    ])
    new = muse_ui._new_videos(page, before=set(), marker="looks at the camera and meows softly.")
    assert [v["src"] for v in new] == ["blob:https://muse.ai/ours"]


def test_marker_is_the_scene_tail():
    p = muse_ui.build_prompt("The cat wakes up and meows softly.", image=True, aspect="9:16", duration=10)
    assert muse_ui._marker(p).endswith("The cat wakes up and meows softly.")
    assert len(muse_ui._marker(p)) == 80


def test_logged_out_detects_each_sign_in_step():
    assert muse_ui._logged_out(FakePage(counts={"Log in or create an account": 1}))
    assert muse_ui._logged_out(FakePage(counts={"button:Log in": 1}))
    assert muse_ui._logged_out(FakePage(counts={"css": 1}))  # 6-digit security code
    assert not muse_ui._logged_out(FakePage())


def test_restore_cookies_puts_saved_session_back(tmp_path):
    (tmp_path / "muse-state.json").write_text(json.dumps({"cookies": [{"name": "a", "value": "b"}]}))

    class Ctx:
        added = None

        def cookies(self):
            return []

        def add_cookies(self, c):
            Ctx.added = c

    muse_ui._restore_cookies(Ctx(), tmp_path)
    assert Ctx.added == [{"name": "a", "value": "b"}]


def test_restore_never_writes_an_old_copy_over_a_renewed_cookie(tmp_path):
    """F17: the saved sign-in cookie from the day of login overwrote the one Muse had renewed: signed out."""
    old = {"name": "hatch_sess", "value": "old", "domain": ".muse.ai", "path": "/"}
    gone = {"name": "csrf", "value": "x", "domain": "muse.ai", "path": "/"}
    (tmp_path / "muse-state.json").write_text(json.dumps({"cookies": [old, gone]}))

    class Ctx:
        added = None

        def cookies(self):
            return [{**old, "value": "renewed"}]

        def add_cookies(self, c):
            Ctx.added = c

    muse_ui._restore_cookies(Ctx(), tmp_path)
    assert Ctx.added == [gone]


def _state(tmp_path, **extra):
    import time

    cookie = {"name": "hatch_sess", "value": "v", "domain": ".muse.ai", "path": "/", "expires": time.time() + 86400 * 20}
    (tmp_path / "muse-state.json").write_text(json.dumps({"cookies": [cookie], **extra}))


def test_session_status(tmp_path):
    assert muse_ui.session_status(tmp_path)["signed_in"] is False  # never signed in
    _state(tmp_path)
    st = muse_ui.session_status(tmp_path)
    assert st["signed_in"] is True and 19 < st["days_left"] <= 20
    muse_ui._mark_signed_out(tmp_path)
    assert muse_ui.session_status(tmp_path)["signed_in"] is False
    _state(tmp_path)  # signing in again writes a fresh state
    assert muse_ui.session_status(tmp_path)["signed_in"] is True


def test_restore_cookies_without_file_is_a_no_op(tmp_path):
    class Ctx:
        def add_cookies(self, c):
            raise AssertionError("should not be called")

    muse_ui._restore_cookies(Ctx(), tmp_path)


def test_missing_image_fails_before_opening_a_browser(tmp_path, capsys):
    code = muse_ui.main(["i2v", "--prompt", "x", "--image", str(tmp_path / "nope.png"), "-o", str(tmp_path / "o.mp4")])
    assert code == muse_ui.EXIT_USAGE
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "fail" and out["error"]["spent"] is False


def test_prompt_starts_with_the_request_head_used_to_bound_fetch():
    """fetch stops at the next request bubble, found by this fixed opening of every prompt."""
    p = muse_ui.build_prompt("x", image=False, aspect="9:16", duration=10)
    assert p.startswith(muse_ui.REQUEST_HEAD)


def test_videos_js_and_failed_js_are_valid_js_strings():
    assert "([marker, head, sel])" in muse_ui.VIDEOS_JS
    assert "\n" not in muse_ui.FAILED_JS  # a real newline inside a JS regex literal breaks it


def test_asks_or_declines():
    assert muse_ui._asks_or_declines("I still can't make that exact podium take. Want me to do that one?")
    assert muse_ui._asks_or_declines("Sorry, that request goes against our policy.")
    assert not muse_ui._asks_or_declines("Generating your 10-second video now.")
    assert not muse_ui._asks_or_declines("Here it is.")
