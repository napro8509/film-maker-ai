"""A muse.ai page that comes up without its composer is reloaded once before the film is stopped."""
import pytest

from filmmaker import muse_ui


class Box:
    def __init__(self, page):
        self.page = page
        self.first = self

    def wait_for(self, **kw):
        if self.page.loads < self.page.shows_on:
            raise TimeoutError("no composer")


class Page:
    def __init__(self, shows_on):
        self.shows_on, self.loads, self.shots = shows_on, 0, []

    def goto(self, *a, **kw):
        self.loads += 1

    def locator(self, sel):
        return Box(self)

    def screenshot(self, path):
        self.shots.append(path)


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(muse_ui, "_logged_out", lambda page: False)
    monkeypatch.setattr(muse_ui, "NO_CHAT_SHOTS", tmp_path)
    monkeypatch.setitem(muse_ui.FAIL_SHOTS, "dir", None)


def test_composer_on_first_load():
    page = Page(shows_on=1)
    muse_ui._open_chat(page, None)
    assert page.loads == 1 and not page.shots


def test_reload_finds_the_composer():
    page = Page(shows_on=2)
    muse_ui._open_chat(page, None)
    assert page.loads == 2 and not page.shots


def test_never_there_fails_with_a_screenshot():
    page = Page(shows_on=9)
    with pytest.raises(muse_ui.Fail) as e:
        muse_ui._open_chat(page, None)
    assert e.value.code == muse_ui.EXIT_DRIFT
    assert page.loads == 2 and page.shots and page.shots[0].endswith("9-no-composer.png")


def test_signed_out_is_not_reloaded(monkeypatch, tmp_path):
    monkeypatch.setattr(muse_ui, "_logged_out", lambda page: True)
    monkeypatch.setitem(muse_ui.CURRENT, "profile", tmp_path)
    page = Page(shows_on=9)
    with pytest.raises(muse_ui.Fail) as e:
        muse_ui._open_chat(page, None)
    assert e.value.code == muse_ui.EXIT_AUTH and page.loads == 1
    assert muse_ui.session_status(tmp_path)["signed_in"] is False  # the app now asks to sign in first
