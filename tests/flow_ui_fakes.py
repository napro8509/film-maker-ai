"""Fake clock and fake Playwright page for unit tests of filmmaker/flow_ui.py (no browser, no Flow).

FakeTime replaces the `time` module inside flow_ui (`monkeypatch.setattr(flow_ui, "time", ft)`): sleep() only
moves the clock. FakePage answers the few locator calls flow_ui makes; counts/texts are set per selector.
"""
from __future__ import annotations

from contextlib import contextmanager

from filmmaker import flow_ui


class FakeTime:
    def __init__(self, t: float = 0.0):
        self.t = float(t)

    def time(self) -> float:
        return self.t

    monotonic = time

    def sleep(self, s: float) -> None:
        self.t += max(0.0, float(s))


class TimeoutError(Exception):  # noqa: A001 - same class name Playwright uses
    pass


class FakeLocator:
    def __init__(self, page: "FakePage", sel: str):
        self.page, self.sel = page, sel

    @property
    def first(self) -> "FakeLocator":
        return self

    @property
    def last(self) -> "FakeLocator":
        return self

    def locator(self, sub: str, **_kw) -> "FakeLocator":
        return FakeLocator(self.page, f"{self.sel} >> {sub}")

    def filter(self, **_kw) -> "FakeLocator":
        return self

    def count(self) -> int:
        if self.sel == flow_ui.BUBBLE:
            return len(self.page.bubble_list())
        v = self.page.counts.get(self.sel, self.page.default_count)
        return v() if callable(v) else v

    def click(self, **_kw) -> None:
        self.page.clicks.append(self.sel)

    def evaluate(self, _js: str, _arg=None):
        self.page.clicks.append(self.sel)

    def is_visible(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        v = self.page.enabled.get(self.sel, True)
        return v() if callable(v) else v

    def is_disabled(self) -> bool:
        return False

    def get_attribute(self, _name: str):
        return None

    def inner_text(self) -> str:
        return self.page.texts.get(self.sel, "")

    def wait_for(self, **_kw) -> None:
        pass

    def scroll_into_view_if_needed(self, **_kw) -> None:
        pass

    def hover(self, **_kw) -> None:
        pass

    def evaluate_all(self, _js: str):
        if self.sel == flow_ui.BUBBLE:
            return self.page.bubble_list()
        return list(self.page.titles)


class FakeKeyboard:
    def __init__(self, page: "FakePage"):
        self.page = page

    def press(self, key: str) -> None:
        self.page.keys.append(key)

    def insert_text(self, text: str) -> None:
        self.page.typed.append(text)


class _NoResponse:
    @property
    def value(self):
        raise RuntimeError("Timeout exceeded")


class FakePage:
    """bubbles: list of texts, or a callable () -> list (e.g. depending on the fake clock)."""

    def __init__(self, *, bubbles=None, counts: dict | None = None, texts: dict | None = None,
                 url: str = "https://flow.google.com/project/p", default_count: int = 0, goto=None,
                 enabled: dict | None = None):
        self.bubbles = bubbles if bubbles is not None else []
        self.counts = dict(counts or {})
        self.enabled = dict(enabled or {})  # selector -> bool or () -> bool (is_enabled)
        self.texts = dict(texts or {})
        self.url = url
        self.default_count = default_count
        self.titles: list[str] = []
        self.clicks: list[str] = []
        self.keys: list[str] = []
        self.typed: list[str] = []
        self.goto_calls: list[dict] = []
        self.response_timeouts: list[float] = []
        self.files: list[str] = []
        self._goto = goto
        self.keyboard = FakeKeyboard(self)

    def bubble_list(self) -> list[str]:
        return list(self.bubbles() if callable(self.bubbles) else self.bubbles)

    def locator(self, sel: str, **_kw) -> FakeLocator:
        return FakeLocator(self, sel)

    def goto(self, url: str, **kw) -> None:
        self.goto_calls.append({"url": url, **kw})
        if self._goto:
            self._goto(url, **kw)

    def evaluate(self, _js: str):
        return None

    @contextmanager
    def expect_file_chooser(self, timeout: float = 30_000):
        class _Chooser:
            def set_files(_self, path: str) -> None:
                self.files.append(path)
        yield type("Info", (), {"value": _Chooser()})()

    @contextmanager
    def expect_response(self, _pred, timeout: float = 30_000):
        self.response_timeouts.append(timeout)
        yield _NoResponse()
