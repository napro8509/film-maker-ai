"""flow_ui i2v: Flow's "queued" notice, one deadline from driver start, no send / no approve when time is short.
Fake page + fake clock only: no browser, no Flow, no credits."""
import json
from types import SimpleNamespace

import pytest

from filmmaker import flow_ui
from tests.flow_ui_fakes import FakePage, FakeTime, TimeoutError as FakeTimeoutError

# The real reply bubble for s10 of "bi-mat-cua-nguoi-chong" (state.json error, after "Flow báo tạo video lỗi: ").
S10 = ("warning\nFailed\nAudio generation failed. Please try a different prompt or send feedback.\n\n"
       "Your video generation has been scheduled and is waiting in the queue due to high demand. Feel free to check "
       "back in a few minutes to see if it's finished!\n\nthumb_up\nthumb_down\ncontent_copy\nflag")
OFFER = "I'll make 1 video generation, costing 5 credits."
APPROVE = f'{flow_ui.PERMISSION} >> [role=radio][aria-label="Approve"]:not(.read-only)'
APPROVE_CLICK = f'{flow_ui.PERMISSION} >> [role=radio][aria-label="Approve"]'
REJECT = f'{flow_ui.PERMISSION} >> [role=radio][aria-label="Reject"]'


@pytest.fixture(autouse=True)
def _reset_globals(monkeypatch):
    monkeypatch.setitem(flow_ui.SPENT, "approved", False)
    monkeypatch.setitem(flow_ui.LAST_MEDIA, "id", None)


@pytest.fixture
def ft(monkeypatch):
    clock = FakeTime(0.0)
    monkeypatch.setattr(flow_ui, "time", clock)
    return clock


def _args(tmp_path, **kw):
    return SimpleNamespace(out=str(tmp_path / "s10.mp4"), max_credits=0, dry_run=False, **kw)


# --- AC21: classify_replies ------------------------------------------------------------------------------------
def test_classify_s10_real_text_is_queued_with_audio():
    assert flow_ui.classify_replies([S10]) == ("queued", True)


def test_classify_failed_only():
    assert flow_ui.classify_replies(["Video generation failed. Please try again."]) == ("failed", False)


def test_classify_queued_then_failed():
    assert flow_ui.classify_replies([S10, "Video generation failed."]) == ("failed", True)


def test_classify_failed_then_queued():
    assert flow_ui.classify_replies(["Video generation failed.", "It is waiting in the queue."]) == ("queued", False)


def test_classify_plain_and_empty():
    assert flow_ui.classify_replies(["Here is your video, enjoy!"]) == (None, False)
    assert flow_ui.classify_replies([]) == (None, False)


def test_classify_ordinary_is_scheduled_is_not_queued():  # design-review minor 6: regex kept narrow
    assert flow_ui.classify_replies(["The release is scheduled for next week."]) == (None, False)


# --- AC22: queued keeps waiting, logs once ---------------------------------------------------------------------
def test_wait_video_queued_then_ok(ft, monkeypatch, tmp_path, capsys):
    out = tmp_path / "s10.mp4"
    page = FakePage(bubbles=[OFFER, S10])

    def poll(_page, _before, o, _last, every=20, deadline=None):
        if ft.t >= 900:
            o.write_bytes(b"x" * 2048)
            return True
        return False

    monkeypatch.setattr(flow_ui, "_poll_download", poll)
    dl = flow_ui.Deadline(1680)
    res = flow_ui._wait_video(page, [], 0, out, dl, 5)
    assert res["status"] == "ok" and res["local_path"] == str(out) and res["credits"] == 5
    err = capsys.readouterr().err
    assert err.count("Flow báo đang xếp hàng") == 1
    assert "tiếp tục chờ tối đa 28 phút" in err


# --- AC23/AC24: deadline counts from start ---------------------------------------------------------------------
def test_deadline_from_start(ft, monkeypatch, tmp_path):
    out = tmp_path / "s10.mp4"
    dl = flow_ui.Deadline(1680)
    ft.t = 600  # time spent before approving
    monkeypatch.setattr(flow_ui, "_poll_download", lambda *a, **k: False)
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._wait_video(FakePage(bubbles=[OFFER, S10]), [], 0, out, dl, 5)
    f = ei.value
    assert f.code == flow_ui.EXIT_TIMEOUT and f.spent is True
    assert ft.t <= 1680
    assert f.detail.startswith("Flow báo đang xếp hàng")
    assert "đã chờ 18 phút sau khi đồng ý" in f.detail
    assert str(out) in f.detail and "tự nhận clip" in f.detail
    assert flow_ui.AUDIO_HINT in f.detail


def test_timeout_without_queue_detail(ft, monkeypatch, tmp_path):
    monkeypatch.setattr(flow_ui, "_poll_download", lambda *a, **k: False)
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._wait_video(FakePage(bubbles=[OFFER]), [], 0, tmp_path / "a.mp4", flow_ui.Deadline(300), None)
    assert ei.value.code == 9 and ei.value.spent is True
    assert ei.value.detail.startswith("Đã chờ 5 phút sau khi đồng ý")
    assert flow_ui.AUDIO_HINT not in ei.value.detail


# --- AC25 ------------------------------------------------------------------------------------------------------
def test_failed_audio_hint(ft, monkeypatch, tmp_path):
    monkeypatch.setattr(flow_ui, "_poll_download", lambda *a, **k: False)
    page = FakePage(bubbles=[OFFER, "warning\nFailed\nAudio generation failed. Please try a different prompt."])
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._wait_video(page, [], 0, tmp_path / "a.mp4", flow_ui.Deadline(1680), 5)
    f = ei.value
    assert f.code == flow_ui.EXIT_GENERATION_FAILED and f.spent is True
    assert f.detail.startswith("Flow báo tạo video lỗi: warning")
    assert f.detail.endswith(flow_ui.AUDIO_HINT)


def test_failed_without_audio_no_hint(ft, monkeypatch, tmp_path):
    monkeypatch.setattr(flow_ui, "_poll_download", lambda *a, **k: False)
    page = FakePage(bubbles=[OFFER, "Video generation failed.", "  "])
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._wait_video(page, [], 0, tmp_path / "a.mp4", flow_ui.Deadline(1680), 5)
    assert ei.value.detail == "Flow báo tạo video lỗi: Video generation failed."


class _PageRedraw(Exception):
    """A Playwright-like transient error (flow_ui._transient looks at the module and the message)."""


_PageRedraw.__module__ = "playwright._impl._errors"


def test_failed_reads_bubbles_once_so_a_redraw_is_not_exit_23(ft, monkeypatch, tmp_path):
    """code-review 1: the "failed" branch reuses the texts it classified; a redraw right after is not fatal."""
    monkeypatch.setattr(flow_ui, "_poll_download", lambda *a, **k: False)
    calls = []

    def bubbles(_page):
        calls.append(1)
        if len(calls) > 1:
            raise _PageRedraw("Execution context was destroyed, most likely because of a navigation")
        return [OFFER, "Video generation failed."]

    monkeypatch.setattr(flow_ui, "_bubble_texts", bubbles)
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._wait_video(FakePage(), [], 0, tmp_path / "a.mp4", flow_ui.Deadline(1680), 5)
    assert ei.value.code == flow_ui.EXIT_GENERATION_FAILED and ei.value.spent is True
    assert ei.value.detail == "Flow báo tạo video lỗi: Video generation failed."
    assert len(calls) == 1


def test_wait_video_transient_bubble_read_keeps_waiting(ft, monkeypatch, tmp_path):
    monkeypatch.setattr(flow_ui, "_poll_download", lambda *a, **k: False)
    calls = []

    def bubbles(_page):
        calls.append(1)
        if len(calls) == 1:
            raise _PageRedraw("Element is not attached to the DOM")
        return [OFFER, "Video generation failed."]

    monkeypatch.setattr(flow_ui, "_bubble_texts", bubbles)
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._wait_video(FakePage(), [], 0, tmp_path / "a.mp4", flow_ui.Deadline(1680), 5)
    assert ei.value.code == flow_ui.EXIT_GENERATION_FAILED and len(calls) == 2


# --- _offer ----------------------------------------------------------------------------------------------------
def test_offer_expired_no_tile(ft, monkeypatch, tmp_path):
    monkeypatch.setattr(flow_ui, "_new_tile", lambda page, before: None)
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._offer(FakePage(bubbles=["thinking…"]), _args(tmp_path), [], 0, 0, flow_ui.Deadline(100))
    assert ei.value.code == flow_ui.EXIT_AGENT_DECLINED and ei.value.spent is False
    assert ei.value.detail.startswith("Hết thời gian chờ trước khi Flow đề nghị tạo video")
    assert ft.t <= 101
    assert flow_ui.SPENT["approved"] is False


def test_offer_expired_with_tile(ft, monkeypatch, tmp_path):
    monkeypatch.setattr(flow_ui, "_new_tile", lambda page, before: "new clip" if ft.t >= 100 else None)
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._offer(FakePage(), _args(tmp_path), [], 0, 0, flow_ui.Deadline(100))
    assert ei.value.code == flow_ui.EXIT_TIMEOUT and ei.value.spent is True
    assert str(tmp_path / "s10.mp4") in ei.value.detail


def test_offer_reject_when_little_left(ft, monkeypatch, tmp_path):
    monkeypatch.setattr(flow_ui, "_new_tile", lambda page, before: None)
    page = FakePage(counts={flow_ui.PERMISSION: 1, APPROVE: 1})
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._offer(page, _args(tmp_path), [], 0, 0, flow_ui.Deadline(flow_ui.APPROVE_MIN_LEFT - 30))
    assert ei.value.code == flow_ui.EXIT_AGENT_DECLINED and ei.value.spent is False
    assert REJECT in page.clicks and APPROVE_CLICK not in page.clicks
    assert flow_ui.SPENT["approved"] is False


def test_offer_approves_with_time_left(ft, monkeypatch, tmp_path):
    monkeypatch.setattr(flow_ui, "_new_tile", lambda page, before: None)
    page = FakePage(counts={flow_ui.PERMISSION: 1, APPROVE: 1},
                    texts={flow_ui.PERMISSION: "1 video generation, costing 5 credits"})
    assert flow_ui._offer(page, _args(tmp_path), [], 0, 0, flow_ui.Deadline(1000)) == 5
    assert APPROVE_CLICK in page.clicks and flow_ui.SPENT["approved"] is True


# --- _i2v_on_page: SEND_MIN_LEFT, titles_before ----------------------------------------------------------------
def test_no_send_when_little_left(ft, monkeypatch, tmp_path):
    dl = flow_ui.Deadline(1680)
    monkeypatch.setattr(flow_ui, "_attach", lambda page, p, deadline=None: setattr(ft, "t", 1300.0))
    called = []
    monkeypatch.setattr(flow_ui, "_stable_titles", lambda *a, **k: called.append(k) or [])
    page = FakePage()
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._i2v_on_page(page, _args(tmp_path), "prompt", [tmp_path / "k.png"], tmp_path / "s10.mp4", dl)
    assert ei.value.code == flow_ui.EXIT_TIMEOUT and ei.value.spent is False
    assert ei.value.detail.startswith("Chỉ còn 379 giây nên không gửi yêu cầu")
    assert flow_ui.SEND_BTN not in page.clicks
    assert called == []
    assert flow_ui.SPENT["approved"] is False


def test_titles_before_full_limit(ft, monkeypatch, tmp_path):
    dl = flow_ui.Deadline(1680)
    monkeypatch.setattr(flow_ui, "_attach", lambda page, p, deadline=None: None)
    seen = []
    monkeypatch.setattr(flow_ui, "_stable_titles", lambda page, settle=4.0, limit=30.0: seen.append(limit) or ["old"])
    monkeypatch.setattr(flow_ui, "_offer", lambda *a: 5)
    monkeypatch.setattr(flow_ui, "_wait_video", lambda page, tb, bb, out, d, credits: {"status": "ok", "tb": tb})
    page = FakePage()
    res = flow_ui._i2v_on_page(page, _args(tmp_path), "prompt", [tmp_path / "k.png"], tmp_path / "s10.mp4", dl)
    assert seen == [30.0] and res == {"status": "ok", "tb": ["old"]}
    assert flow_ui.SEND_BTN in page.clicks


# --- before sending: never 23 because of the deadline ----------------------------------------------------------
def _raise_timeout(*_a, **_k):
    raise FakeTimeoutError("Timeout 90000ms exceeded")


def test_open_project_goto_timeout_is_9(ft):
    dl = flow_ui.Deadline(10)
    ft.t = 20
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._open_project(FakePage(goto=_raise_timeout), "p", dl)
    assert ei.value.code == flow_ui.EXIT_TIMEOUT and ei.value.spent is False
    assert "chưa gửi, chưa trừ credit" in ei.value.detail


def test_open_project_slow_goto_with_time_left_is_9(ft):  # design-review minor 2: on purpose
    page = FakePage(goto=_raise_timeout)
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._open_project(page, "p", flow_ui.Deadline(1680))
    assert ei.value.code == flow_ui.EXIT_TIMEOUT and ei.value.spent is False
    assert ei.value.detail.startswith("Không mở được trang Flow trong 90 giây")
    assert page.goto_calls[0]["timeout"] == 90_000


def test_open_project_goto_capped(ft):
    page = FakePage(counts={flow_ui.EDITOR: 1})
    flow_ui._open_project(page, "p", flow_ui.Deadline(30))
    assert page.goto_calls[0]["timeout"] == 30_000


def test_open_project_no_deadline_reraises(ft):
    with pytest.raises(FakeTimeoutError):
        flow_ui._open_project(FakePage(goto=_raise_timeout), "p")


def test_open_project_wait_expired_is_9(ft):
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._open_project(FakePage(), "p", flow_ui.Deadline(50))
    assert ei.value.code == flow_ui.EXIT_TIMEOUT and ei.value.spent is False
    assert ft.t <= 51


def test_open_project_no_deadline_still_drift(ft):
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._open_project(FakePage(), "p")
    assert ei.value.code == flow_ui.EXIT_DRIFT


def test_open_project_auth_first(ft):
    page = FakePage(url="https://accounts.google.com/signin")
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._open_project(page, "p", flow_ui.Deadline(50))
    assert ei.value.code == flow_ui.EXIT_AUTH


def _png(tmp_path):
    p = tmp_path / "k.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    return p


def test_attach_expired_first_wait_is_9(ft, tmp_path):
    page = FakePage()
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._attach(page, _png(tmp_path), flow_ui.Deadline(100))
    assert ei.value.code == flow_ui.EXIT_TIMEOUT and ei.value.spent is False
    assert "Escape" in page.keys and ft.t <= 101


def test_attach_expired_second_wait_is_9(ft, tmp_path):
    items = f"{flow_ui.ADD_MENU} >> button[role=option].asset-item"
    page = FakePage(counts={items: lambda: 0 if ft.t < 5 else 1})
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._attach(page, _png(tmp_path), flow_ui.Deadline(30))
    assert ei.value.code == flow_ui.EXIT_TIMEOUT and ei.value.spent is False
    assert any("Add to prompt" in c or "button" in c for c in page.clicks)
    assert ft.t <= 31


def test_attach_no_deadline_still_drift(ft, tmp_path):
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._attach(FakePage(), _png(tmp_path))
    assert ei.value.code == flow_ui.EXIT_DRIFT


# --- downloads cut to the time left ----------------------------------------------------------------------------
def test_try_download_capped(ft, tmp_path):
    page = FakePage(default_count=1)
    assert flow_ui._try_download(page, "new", tmp_path / "a.mp4", flow_ui.Deadline(50)) == "pending"
    assert page.response_timeouts and page.response_timeouts[0] <= 50_000


def test_try_download_no_time_left(ft, tmp_path):
    page = FakePage(default_count=1)
    dl = flow_ui.Deadline(10)
    ft.t = 9.5
    assert flow_ui._try_download(page, "new", tmp_path / "a.mp4", dl) == "pending"
    assert page.clicks == [] and page.response_timeouts == []


def test_main_pre_send_timeout_json(monkeypatch, capsys, tmp_path):
    def boom(_args):
        raise flow_ui._pre_send_timeout()

    monkeypatch.setattr(flow_ui, "run_i2v", boom)
    code = flow_ui.main(["i2v", "--project", "p", "--image", str(tmp_path / "k.png"), "--prompt", "x",
                         "-o", str(tmp_path / "a.mp4"), "--profile-dir", str(tmp_path)])
    doc = json.loads(capsys.readouterr().out)
    assert code == 9 and doc["error"]["exit_code"] == 9 and doc["error"]["spent"] is False


# --- AC21 input: only the bubbles after bubbles_before, without the offer bubble ---------------------------------
def test_wait_video_ignores_old_and_offer_bubbles(ft, monkeypatch, tmp_path):
    monkeypatch.setattr(flow_ui, "_poll_download", lambda *a, **k: False)
    # an older reply (before the send) and the offer bubble itself both mention a failure: neither counts
    page = FakePage(bubbles=["Video generation failed (an older scene).",
                             "If the generation failed I will retry. " + OFFER, "Working on it…"])
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._wait_video(page, [], 1, tmp_path / "a.mp4", flow_ui.Deadline(300), 5)
    assert ei.value.code == flow_ui.EXIT_TIMEOUT and ei.value.spent is True
    assert ei.value.detail.startswith("Đã chờ")


# --- AC22/AC23: queued keeps trying to download, each try gets the deadline -------------------------------------
def test_wait_video_queued_polls_with_deadline(ft, monkeypatch, tmp_path, capsys):
    seen = []

    def poll(_page, _before, _out, _last, every=20, deadline=None):
        seen.append(deadline)
        return False

    monkeypatch.setattr(flow_ui, "_poll_download", poll)
    dl = flow_ui.Deadline(600)
    with pytest.raises(flow_ui.Fail) as ei:
        flow_ui._wait_video(FakePage(bubbles=[OFFER, S10]), [], 0, tmp_path / "a.mp4", dl, 5)
    assert ei.value.code == flow_ui.EXIT_TIMEOUT and ei.value.spent is True
    assert len(seen) > 10 and all(d is dl for d in seen)  # kept trying while queued
    assert ft.t <= 600
    assert capsys.readouterr().err.count("Flow báo đang xếp hàng") == 1


# --- AC23: the --timeout gflow passes to the driver -------------------------------------------------------------
def test_ui_i2v_timeout_arg(tmp_path, monkeypatch):
    """Gflow.ui_image_to_video gives the driver --timeout <= Gflow.timeout - 120 (fake flow_ui, profile lookup
    patched so no `gflow auth status` runs)."""
    import sys
    from pathlib import Path

    from filmmaker.gflow import Gflow, GflowError

    fake = Path(__file__).parent / "fake_flow_ui.py"
    log = tmp_path / "ui.log"
    monkeypatch.setenv("FLOW_UI_CMD", f"{sys.executable} {fake}")
    monkeypatch.setenv("FAKE_FLOW_UI_LOG", str(log))
    monkeypatch.setenv("FAKE_FLOW_UI_FAIL", "9:true")  # no ffmpeg needed, nothing downloaded
    monkeypatch.setattr(Gflow, "ui_profile_dir", lambda self: tmp_path / "profile")
    for timeout in (1800, 600):
        g = Gflow(timeout=timeout, log=lambda *_: None)
        with pytest.raises(GflowError) as ei:
            g.ui_image_to_video(tmp_path / "k.png", "p", aspect="16:9", model="veo-lite", duration=6,
                                project="proj", out=tmp_path / "s01.mp4")
        assert ei.value.exit_code == 9
        argv = json.loads(log.read_text().splitlines()[-1])
        assert argv[0] == "i2v"
        assert int(argv[argv.index("--timeout") + 1]) <= timeout - 120
