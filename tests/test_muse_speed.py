"""Muse speed-ups: one shared browser (`muse_ui.py serve`), request spacing from the previous send with a failure
backoff, and the serve loop of the driver itself."""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import pytest

from filmmaker import gflow as gflow_mod
from filmmaker import muse_ui
from filmmaker.gflow import Gflow, GflowError, MuseSession

FAKE_SERVE = """
import json, os, sys
print("booting", file=sys.stderr, flush=True)
if os.environ.get("FAKE_NO_READY"):
    sys.exit(3)
print("@@result " + json.dumps({"status": "ready"}), flush=True)
for line in sys.stdin:
    job = json.loads(line)
    if job.get("quit"):
        break
    argv = job["argv"]
    out = argv[argv.index("-o") + 1]
    print("  working on " + argv[0], file=sys.stderr, flush=True)
    if "--prompt" in argv and argv[argv.index("--prompt") + 1] == "refuse":
        res = {"status": "fail", "error": {"exit_code": 42, "detail": "Muse báo không tạo được ảnh này"}}
    else:
        open(out, "wb").write(b"x" * 10)
        res = {"status": "ok", "local_path": out, "pid": os.getpid()}
    print("@@result " + json.dumps(res), flush=True)
"""


@pytest.fixture
def fake_serve(tmp_path):
    script = tmp_path / "fake_serve.py"
    script.write_text(FAKE_SERVE, encoding="utf-8")
    return [sys.executable, str(script)]


def test_session_reuses_one_process_for_several_requests(tmp_path, fake_serve):
    sess = MuseSession(fake_serve, [], idle=30)
    lines: list[str] = []
    try:
        a = sess.request(["image", "--prompt", "a", "-o", str(tmp_path / "a.png")], 20, lines.append)
        b = sess.request(["image", "--prompt", "b", "-o", str(tmp_path / "b.png")], 20, lines.append)
    finally:
        sess.close()
    assert a["status"] == b["status"] == "ok"
    assert a["pid"] == b["pid"]  # the same browser process served both
    assert (tmp_path / "a.png").is_file() and (tmp_path / "b.png").is_file()
    assert "  working on image" in lines


def test_session_that_cannot_start_returns_none(tmp_path, fake_serve, monkeypatch):
    monkeypatch.setenv("FAKE_NO_READY", "1")
    sess = MuseSession(fake_serve, [], start_timeout=10)
    assert sess.request(["image", "--prompt", "a", "-o", str(tmp_path / "a.png")], 10, lambda _l: None) is None
    assert sess.broken


def test_session_closes_itself_when_idle(tmp_path, fake_serve):
    sess = MuseSession(fake_serve, [], idle=0.3)
    sess.request(["image", "--prompt", "a", "-o", str(tmp_path / "a.png")], 20, lambda _l: None)
    proc = sess.proc
    deadline = time.time() + 10
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.1)
    assert proc.poll() is not None and sess.proc is None


def test_gflow_muse_image_goes_through_the_session(tmp_path, fake_serve, monkeypatch):
    monkeypatch.delenv("MUSE_UI_CMD", raising=False)
    monkeypatch.delenv("FILMMAKER_MUSE_SESSION", raising=False)
    g = Gflow(log=lambda *_: None)
    monkeypatch.setattr(g, "muse_command", lambda: fake_serve)
    called = []
    monkeypatch.setattr(g, "_run_ui", lambda *a, **k: called.append(a) or {})
    try:
        p1 = g.muse_image("a cat", None, out=tmp_path / "1.png", aspect="9:16")
        p2 = g.muse_image("a dog", None, out=tmp_path / "2.png", aspect="9:16")
        with pytest.raises(GflowError) as exc:
            g.muse_image("refuse", None, out=tmp_path / "3.png", aspect="9:16")
    finally:
        g.close_muse()
    assert p1.is_file() and p2.is_file() and called == []
    assert exc.value.exit_code == 42 and "không tạo được" in str(exc.value)


def test_gflow_falls_back_to_one_shot_when_the_session_is_broken(tmp_path, fake_serve, monkeypatch):
    monkeypatch.delenv("MUSE_UI_CMD", raising=False)
    monkeypatch.setenv("FAKE_NO_READY", "1")
    g = Gflow(log=lambda *_: None)
    monkeypatch.setattr(g, "muse_command", lambda: fake_serve)
    monkeypatch.setattr(g, "_run_ui", lambda *a, **k: {"status": "ok", "local_path": str(tmp_path / "x.png")})
    assert g.muse_image("a cat", None, out=tmp_path / "x.png", aspect="9:16") == tmp_path / "x.png"


def test_session_is_off_for_a_custom_driver_or_when_disabled(monkeypatch):
    g = Gflow(log=lambda *_: None)
    monkeypatch.setenv("MUSE_UI_CMD", "x")
    assert g._muse_session_for(["py", "muse_ui.py"]) is None
    monkeypatch.delenv("MUSE_UI_CMD")
    monkeypatch.setenv("FILMMAKER_MUSE_SESSION", "0")
    assert g._muse_session_for(["py", "muse_ui.py"]) is None


# --- spacing Muse video requests -----------------------------------------------------


def test_spacing_counts_from_the_previous_send(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(gflow_mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(gflow_mod.random, "uniform", lambda lo, hi: 50.0)
    slept: list[float] = []
    g = Gflow(log=lambda *_: None, sleep=slept.append)
    g._muse_space_out()  # first request: no wait
    g._muse_sent_at = clock["t"]
    clock["t"] += 80  # the clip took 80 s: already more than the 50 s gap
    g._muse_space_out()
    assert slept == []
    g._muse_sent_at = clock["t"]
    clock["t"] += 20  # a quick refusal 20 s after the send
    g._muse_space_out()
    assert slept == [30.0]


def test_backoff_after_failures_and_reset_on_success(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(gflow_mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(gflow_mod.random, "uniform", lambda lo, hi: 45.0)
    slept: list[float] = []
    g = Gflow(log=lambda *_: None, sleep=slept.append)
    g._muse_video_done(False)
    assert g._muse_backoff == 60
    g._muse_video_done(False)
    g._muse_video_done(False)
    assert g._muse_backoff == 180  # 60, 120, capped at 180
    g._muse_sent_at = 0.0
    clock["t"] = 40.0
    g._muse_space_out()
    assert slept == [140.0]
    g._muse_video_done(True)
    assert g._muse_backoff == 0


def test_pipeline_no_longer_naps_between_muse_scenes():
    from filmmaker import pipeline

    assert not hasattr(pipeline, "MUSE_PAUSE")


# --- the driver's serve loop -----------------------------------------------------------


def test_serve_runs_each_request_on_the_same_session(monkeypatch, capsys, tmp_path):
    seen: list[tuple] = []

    class FakeCtx:
        pages = [object()]

        def close(self):
            seen.append(("close",))

    class FakePage:
        def is_closed(self):
            return False

    class FakePW:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    ctx, page = FakeCtx(), FakePage()
    monkeypatch.setattr(muse_ui, "_playwright", lambda: (lambda: FakePW()))
    monkeypatch.setattr(muse_ui, "_open", lambda pw, profile, channel, headless: (ctx, page))

    def fake_generate(args, kind, session=None):
        seen.append((kind, args.prompt, session))
        if args.prompt == "boom":
            raise muse_ui.Fail(muse_ui.EXIT_GENERATION_FAILED, "Failed to create")
        return {"status": "ok", "local_path": args.out}

    monkeypatch.setattr(muse_ui, "_generate", fake_generate)
    jobs = [{"argv": ["image", "--prompt", "one", "-o", str(tmp_path / "1.png")]},
            {"argv": ["i2v", "--prompt", "boom", "-o", str(tmp_path / "2.mp4")]},
            {"argv": ["image", "--prompt", "two", "-o", str(tmp_path / "3.png")]},
            {"quit": True}]
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(json.dumps(j) + "\n" for j in jobs)))
    assert muse_ui.main(["serve"]) == 0
    out = [json.loads(l[len(muse_ui.SERVE_RESULT):]) for l in capsys.readouterr().out.splitlines()
           if l.startswith(muse_ui.SERVE_RESULT)]
    assert [o["status"] for o in out] == ["ready", "ok", "fail", "ok"]
    assert out[2]["error"]["exit_code"] == muse_ui.EXIT_GENERATION_FAILED
    assert [s[:2] for s in seen[:3]] == [("image", "one"), ("video", "boom"), ("image", "two")]
    assert all(s[2] == (ctx, page) for s in seen[:3]) and seen[-1] == ("close",)
    assert muse_ui.SERVE_RESULT == gflow_mod.MUSE_SERVE_RESULT


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="needs node")
@pytest.mark.parametrize("name", ["PREVIEWS_JS", "SENT_JS", "MEDIA_STATE_JS", "VIDEOS_JS", "REPLY_AFTER_JS"])
def test_page_scripts_parse_as_javascript(name):
    import subprocess

    code = getattr(muse_ui, name)
    r = subprocess.run(["node", "-e", "new Function('return (' + process.argv[1] + ')')", code],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# --- a send that never reached the chat (22/11/2026: s04 waited 15 minutes for nothing) ------------------


class _Composer:
    def __init__(self, page):
        self.page = page

    def click(self):
        pass

    def fill(self, text):
        self.page.box = text

    def press(self, key):
        self.page.send()


class _ChatPage:
    """`deliver`: for each press of send, whether the message reaches the chat (else the page drops it)."""

    def __init__(self, deliver):
        self.deliver = list(deliver)
        self.box = "the prompt"
        self.chat: list[str] = []
        self.sends = 0

    def send(self):
        self.sends += 1
        ok = self.deliver.pop(0) if self.deliver else False
        if ok:
            self.chat.append(self.box)
        self.box = ""  # a re-render empties the box even when the message is lost

    def get_by_role(self, *_a, **_k):
        return SimpleNamespace(count=lambda: 0)

    def evaluate(self, js, arg=None):
        if js == muse_ui.SENT_JS:
            return not self.box and any(arg in m for m in self.chat)
        if js == muse_ui.UNSENT_JS:
            return bool(self.box)
        raise AssertionError(js)


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(muse_ui, "SENT_WAIT", 0.2)
    monkeypatch.setattr(muse_ui.time, "sleep", lambda s: None)


from types import SimpleNamespace  # noqa: E402


def test_message_lost_on_first_send_is_typed_and_sent_again(quick):
    page = _ChatPage([False, True])
    muse_ui._send_and_confirm(page, _Composer(page), "the prompt", "prompt", None)
    assert page.sends == 2 and page.chat == ["the prompt"]


def test_message_that_never_reaches_muse_fails_fast_and_unbilled(quick):
    page = _ChatPage([False, False])
    with pytest.raises(muse_ui.Fail) as exc:
        muse_ui._send_and_confirm(page, _Composer(page), "the prompt", "prompt", None)
    assert exc.value.code == muse_ui.EXIT_TIMEOUT and exc.value.spent is False
    assert page.sends == 2


def test_message_seen_at_once_is_sent_once(quick):
    page = _ChatPage([True])
    muse_ui._send_and_confirm(page, _Composer(page), "the prompt", "prompt", None)
    assert page.sends == 1
