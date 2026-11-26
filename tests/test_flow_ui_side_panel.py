"""Flow UI 2026-11-26: the chat opens as a compact card that hides Flow's offer; the driver moves it to the side
panel, starts a new chat through "Open session history" -> "Create new session", and clears the prompt by keys."""
from __future__ import annotations

from filmmaker import flow_ui


class Loc:
    def __init__(self, page, sel, text=None):
        self.page, self.sel, self.text = page, sel, text

    first = property(lambda self: self)

    def _key(self):
        return (self.sel, self.text) if self.text else self.sel

    def count(self):
        return self.page.present(self._key())

    def is_visible(self):
        return self.count() > 0

    def click(self, **_kw):
        self.page.click(self._key())

    def inner_text(self):
        return self.page.editor_text


class Keyboard:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        self.page.keys.append(key)
        if key == "Backspace":
            self.page.editor_text = ""
            self.page.chips = 0


class Page:
    """compact: the card with "Use side panel"; panel: the side panel with the history button."""

    def __init__(self, mode="compact", old_button=False):
        self.mode, self.old_button, self.history_open = mode, old_button, False
        self.clicks, self.keys, self.editor_text, self.chips = [], [], "old text", 1
        self.keyboard = Keyboard(self)

    def locator(self, sel, has_text=None):
        return Loc(self, sel, has_text)

    def present(self, key):
        panel = self.mode == "panel"
        table = {
            flow_ui.SIDE_PANEL_BTN: not panel,
            flow_ui.AGENT_PANEL: panel,
            f"{flow_ui.AGENT_PANEL} {flow_ui.PROMPT_BOX}": panel,
            f"{flow_ui.AGENT_PANEL} {flow_ui.EDITOR}": panel and not self.history_open,
            flow_ui.HISTORY_BTN: panel and not self.old_button,
            flow_ui.NEW_SESSION_BTN: panel and self.old_button,
            (f"{flow_ui.AGENT_PANEL} button", flow_ui.CREATE_SESSION_TEXT): panel and self.history_open,
            flow_ui.CLEAR_BTN: False,
            flow_ui.EDITOR: True,
            flow_ui.CHIP: self.chips,
        }
        return int(table.get(key, 0))

    def click(self, key):
        self.clicks.append(key)
        if key == flow_ui.SIDE_PANEL_BTN:
            self.mode = "panel"
        elif key == flow_ui.HISTORY_BTN:
            self.history_open = True
        elif isinstance(key, tuple):
            self.history_open = False


def _no_sleep(monkeypatch):
    monkeypatch.setattr(flow_ui.time, "sleep", lambda _s: None)


def test_compact_card_is_moved_to_side_panel_then_new_session(monkeypatch):
    _no_sleep(monkeypatch)
    page = Page("compact")
    flow_ui._new_session(page)
    assert page.clicks == [flow_ui.SIDE_PANEL_BTN, flow_ui.HISTORY_BTN,
                           (f"{flow_ui.AGENT_PANEL} button", flow_ui.CREATE_SESSION_TEXT)]


def test_side_panel_already_open_is_left_alone(monkeypatch):
    _no_sleep(monkeypatch)
    page = Page("panel")
    flow_ui._side_panel(page)
    assert page.clicks == []


def test_old_start_new_session_button_still_used(monkeypatch):
    _no_sleep(monkeypatch)
    page = Page("panel", old_button=True)
    flow_ui._new_session(page)
    assert page.clicks == [flow_ui.NEW_SESSION_BTN]


def test_to_side_panel_clicks_only_when_button_is_there(monkeypatch):
    _no_sleep(monkeypatch)
    page = Page("panel")
    flow_ui._to_side_panel(page)
    assert page.clicks == []
    page = Page("compact")
    flow_ui._to_side_panel(page)
    assert page.clicks == [flow_ui.SIDE_PANEL_BTN] and page.mode == "panel"


def test_clear_prompt_without_clear_button_uses_select_all_backspace(monkeypatch):
    _no_sleep(monkeypatch)
    page = Page("panel")
    flow_ui._clear_prompt(page)
    assert page.keys[:2] == ["ControlOrMeta+A", "Backspace"]
    assert page.editor_text == "" and page.chips == 0
