"""Unit tests of filmmaker/progress.py: Reporter state machine, gflow event table, timings, estimated percentage."""
import json
import threading

import pytest

from filmmaker import progress as prog


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(prog, "now", c.now)
    return c


def recorder():
    calls = []
    return calls, lambda key, item: calls.append((key, None if item is None else dict(item)))


# --- small parsers -------------------------------------------------------------


def test_timing_kind():
    assert prog.timing_kind(["image", "t2i", "p", "--model", "nano"]) == "image"
    assert prog.timing_kind(["image", "i2i", "p", "--ref", "a.png"]) == "image"
    assert prog.timing_kind(["video", "t2v", "p", "--aspect", "16:9"]) == "video:default:default"
    assert prog.timing_kind(["video", "i2v", "--initial-frame", "k.png", "p", "--model", "veo-lite", "--duration", "8"]) \
        == "video:veo-lite:8"
    assert prog.timing_kind(["video", "r2v", "p", "--duration", "4"]) == "video:default:4"
    assert prog.timing_kind(["project", "create", "--name", "x"]) is None
    assert prog.timing_kind(["models"]) is None
    assert prog.timing_kind([]) is None


def test_gflow_event_phase_table():
    ok = "MEDIA_GENERATION_STATUS_SUCCESSFUL"
    assert prog.gflow_event_phase({"event": "migrated.submit_clicked"}) is None
    assert prog.gflow_event_phase({"event": "migrated.submit_observed"}) is None
    assert prog.gflow_event_phase({"event": "migrated.status", "status": "MEDIA_GENERATION_STATUS_ACTIVE"}) is None
    assert prog.gflow_event_phase({"event": "migrated.status", "status": "MEDIA_GENERATION_STATUS_FAILED"}) is None
    assert prog.gflow_event_phase({"event": "migrated.status", "status": ok}) == "downloading"
    assert prog.gflow_event_phase({"event": "migrated.download"}) == "downloading"
    assert prog.gflow_event_phase({"event": "ui_automation_video.generate_captured"}) is None
    assert prog.gflow_event_phase({"event": "ui_automation_video.poll_terminal", "status": ok}) == "downloading"
    assert prog.gflow_event_phase({"event": "ui_automation_video.poll_terminal", "status": "X"}) is None
    assert prog.gflow_event_phase({"event": "ui_automation_video.video_saved"}) == "downloading"
    assert prog.gflow_event_phase({"event": "agent_only.something"}) is None
    assert prog.gflow_event_phase({}) is None


def test_parse_log_line_and_quiet():
    assert prog.parse_log_line('{"event": "x", "level": "info"}') == {"event": "x", "level": "info"}
    assert prog.parse_log_line('{"level": "warning", "msg": "m"}') == {"level": "warning", "msg": "m"}
    assert prog.parse_log_line('{"status": "ok"}') is None
    assert prog.parse_log_line("boom: real error") is None
    assert prog.parse_log_line("{not json") is None
    assert prog.parse_log_line("[1, 2]") is None
    assert prog.is_quiet_log('{"event": "a", "level": "info"}')
    assert prog.is_quiet_log('{"event": "a", "level": "DEBUG"}')
    assert not prog.is_quiet_log('{"event": "a", "level": "error"}')
    assert not prog.is_quiet_log('{"event": "a", "level": "warning"}')
    assert not prog.is_quiet_log("boom: real error")


def test_parse_progress_line():
    assert prog.parse_progress_line("@@progress 42") == (True, 42)
    assert prog.parse_progress_line("@@progress 0") == (True, 0)
    assert prog.parse_progress_line("@@progress 100") == (True, 100)
    assert prog.parse_progress_line("@@progress abc") == (True, None)
    assert prog.parse_progress_line("@@progress 150") == (True, None)
    assert prog.parse_progress_line("@@progress -1") == (True, None)
    assert prog.parse_progress_line("@@progress") == (True, None)
    assert prog.parse_progress_line("  ⏳ đang tạo…") == (False, None)
    assert prog.parse_progress_line("x @@progress 4") == (False, None)


# --- timings -------------------------------------------------------------------


def test_timings_path_next_to_settings(tmp_path):
    assert prog.timings_path() == tmp_path / "timings.json"  # conftest: FILMMAKER_SETTINGS in tmp_path


def test_timings_defaults_median_kinds_corrupt(tmp_path):
    t = prog.Timings(tmp_path / "timings.json")
    assert t.expected("image") == 30.0
    assert t.expected("video:default:default") == 150.0
    assert t.expected("video:veo-lite:8") == 150.0
    for s in (60, 80, 100):
        t.record("video:veo-lite:8", s)
    assert t.expected("video:veo-lite:8") == 80.0
    assert t.expected("video:default:default") == 150.0  # other kind untouched
    data = json.loads((tmp_path / "timings.json").read_text())
    assert data["version"] == 1 and data["runs"]["video:veo-lite:8"] == [60, 80, 100]
    # only the last KEEP records are kept
    for s in range(1, 21):
        t.record("image", s)
    runs = json.loads((tmp_path / "timings.json").read_text())["runs"]
    assert runs["image"] == list(range(11, 21))
    assert t.expected("image") == 15.5
    # corrupt file: defaults, one warning per instance, no exception
    warns = []
    (tmp_path / "timings.json").write_text("{broken")
    t2 = prog.Timings(tmp_path / "timings.json", warn=warns.append)
    assert t2.expected("image") == 30.0
    assert t2.expected("video:x:y") == 150.0
    assert len(warns) == 1 and "timings.json" in warns[0]
    t2.record("image", 12)  # rewrites a valid file
    assert json.loads((tmp_path / "timings.json").read_text())["runs"] == {"image": [12]}
    # wrong shapes are ignored
    (tmp_path / "timings.json").write_text(json.dumps({"version": 1, "runs": {"image": ["x", True, -3, 40]}}))
    assert prog.Timings(tmp_path / "timings.json").expected("image") == 40.0
    assert not list(tmp_path.glob("*.tmp"))


def test_timings_write_error_warns(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    warns = []
    t = prog.Timings(blocker / "timings.json", warn=warns.append)  # parent is a file: cannot write
    t.record("image", 5)
    t.record("image", 6)
    assert len(warns) == 1


# --- Reporter ------------------------------------------------------------------


def test_reporter_lifecycle_and_emit_only_on_change(clock):
    calls, emit = recorder()
    r = prog.Reporter(emit)
    r.queued("vid:s01")
    r.queued("vid:s02")
    r.submitting("vid:s01")
    r.submitting("vid:s01")  # no change -> no callback
    with r.active("vid:s01"):
        r.gflow_event("start", {"kind": "video:default:default", "expected": 100.0, "source": "estimate"})
        clock.sleep(10)
        r.gflow_event("downloading", {})
        r.gflow_event("downloading", {})
    r.done("vid:s01")
    assert [(k, i["phase"]) for k, i in calls] == [
        ("vid:s01", "queued"), ("vid:s02", "queued"), ("vid:s01", "submitting"), ("vid:s01", "generating"),
        ("vid:s01", "downloading"), ("vid:s01", "done")]
    gen = calls[3][1]
    assert gen["started"] == 1000.0 and gen["source"] == "estimate" and gen["expected"] == 100.0
    assert calls[4][1]["started"] == 1000.0  # downloading keeps started
    assert set(calls[0][1]) == {"phase", "percent", "source", "started", "attempt", "expected"}


def test_gflow_event_without_active_is_ignored():
    calls, emit = recorder()
    r = prog.Reporter(emit)
    r.queued("vid:s01")
    r.gflow_event("start", {"expected": 1.0, "source": "estimate"})
    r.gflow_event("downloading", {})
    assert len(calls) == 1


def test_downloading_after_done_ignored(clock):
    calls, emit = recorder()
    r = prog.Reporter(emit)
    r.submitting("kf:s01")
    with r.active("kf:s01"):
        r.gflow_event("start", {"expected": 30.0, "source": "estimate"})
        r.done("kf:s01")
        n = len(calls)
        r.gflow_event("downloading", {})
        r.gflow_event("percent", {"percent": 40})
        r.gflow_event("start", {"expected": 30.0, "source": "estimate"})
    assert r.items["kf:s01"]["phase"] == "done"
    assert len(calls) == n


def test_percent_only_while_generating(clock):
    r = prog.Reporter()
    r.submitting("vid:s01")
    with r.active("vid:s01"):
        r.gflow_event("percent", {"percent": 5})  # not generating yet
        assert r.items["vid:s01"]["percent"] is None
        r.gflow_event("start", {"kind": None, "expected": None, "source": None})
        assert r.items["vid:s01"]["source"] is None
        r.gflow_event("percent", {"percent": 42})
    assert r.items["vid:s01"]["percent"] == 42 and r.items["vid:s01"]["source"] == "flow"


def test_nested_active_keyframe_inside_video(clock):
    r = prog.Reporter()
    r.submitting("vid:s01")
    with r.active("vid:s01"):
        r.submitting("kf:s01")
        with r.active("kf:s01"):
            r.gflow_event("start", {"expected": 30.0, "source": "estimate"})
        r.done("kf:s01")
        assert r.items["vid:s01"]["phase"] == "submitting"
        r.gflow_event("start", {"expected": 150.0, "source": "estimate"})
    assert r.items["kf:s01"]["phase"] == "done"
    assert r.items["vid:s01"]["phase"] == "generating"


def test_retry_wait_and_new_start(clock):
    r = prog.Reporter()
    r.submitting("vid:s01")
    with r.active("vid:s01"):
        r.gflow_event("start", {"expected": 150.0, "source": "estimate"})
        first = r.items["vid:s01"]["started"]
        r.retry_wait(2)
        it = r.items["vid:s01"]
        assert (it["phase"], it["attempt"], it["started"], it["percent"], it["source"]) == ("queued", 2, None, None, None)
        clock.sleep(30)
        r.gflow_event("start", {"expected": 150.0, "source": "estimate"})
    it = r.items["vid:s01"]
    assert it["phase"] == "generating" and it["attempt"] == 2 and it["started"] > first


def test_retry_wait_without_active_is_noop():
    calls, emit = recorder()
    r = prog.Reporter(emit)
    r.queued("vid:s01")
    r.retry_wait(2)  # e.g. make_thumbnail: nothing active
    assert r.items["vid:s01"]["attempt"] == 1 and len(calls) == 1
    r.submitting("vid:s02")
    r.failed("vid:s02")
    with r.active("vid:s02"):
        r.retry_wait(2)  # active item already failed
    assert r.items["vid:s02"]["phase"] == "failed" and r.items["vid:s02"]["attempt"] == 1


def test_drop_drop_queued_settle():
    calls, emit = recorder()
    r = prog.Reporter(emit)
    for k in ("vid:a", "vid:b", "vid:c", "vid:d"):
        r.queued(k)
    r.drop("vid:missing")
    assert len(calls) == 4
    r.drop("vid:d")
    assert calls[-1] == ("vid:d", None)
    r.submitting("vid:a")
    r.drop_queued()
    assert calls[-2:] == [("vid:b", None), ("vid:c", None)]
    assert list(r.items) == ["vid:a"]
    r.queued("vid:e")
    r.settle()
    assert "vid:e" not in r.items and r.items["vid:a"]["phase"] == "failed"
    r.done("vid:a")
    r.settle()  # done stays done
    assert r.items["vid:a"]["phase"] == "done"


def test_emit_exceptions_swallowed():
    def boom(key, item):
        raise RuntimeError("x")

    r = prog.Reporter(boom)
    r.queued("vid:s01")
    r.done("vid:s01")
    assert r.items["vid:s01"]["phase"] == "done"


def test_reporter_threadsafe(clock):
    seen: dict[str, list[str]] = {}
    lock = threading.Lock()

    def emit(key, item):
        with lock:
            seen.setdefault(key, []).append(item["phase"] if item else "drop")

    r = prog.Reporter(emit)
    stop = threading.Event()

    def noisy():
        while not stop.is_set():
            r.gflow_event("downloading", {})
            r.gflow_event("percent", {"percent": 50})

    t = threading.Thread(target=noisy)
    t.start()
    try:
        for i in range(200):
            key = f"vid:s{i}"
            r.submitting(key)
            with r.active(key):
                r.gflow_event("start", {"expected": 10.0, "source": "estimate"})
                r.done(key)
    finally:
        stop.set()
        t.join()
    assert all(v["phase"] == "done" for v in r.items.values()) and len(r.items) == 200
    for key, phases in seen.items():
        assert phases[-1] == "done", (key, phases)
        assert "downloading" not in phases[phases.index("done"):]


# --- public view ----------------------------------------------------------------


def _item(**kw):
    base = {"phase": "generating", "percent": None, "source": "estimate", "started": 1000.0, "attempt": 1,
            "expected": 100.0}
    base.update(kw)
    return base


def test_public_view_estimate_monotonic_capped():
    items = {"vid:s01": _item()}
    seq = [prog.public_view(items, 1000.0 + dt)["vid:s01"]["percent"] for dt in (0, 10, 37.9, 50, 94, 95, 200, 10_000)]
    assert seq == [0, 10, 37, 50, 94, 95, 95, 95]
    assert seq == sorted(seq)
    # generating -> downloading keeps started, so no drop
    items["vid:s01"]["phase"] = "downloading"
    assert prog.public_view(items, 1050.0)["vid:s01"]["percent"] == 50
    view = prog.public_view(items, 1050.0)["vid:s01"]
    assert set(view) == {"phase", "percent", "source", "started", "attempt"}
    # clock behind started -> 0, never negative
    assert prog.public_view(items, 900.0)["vid:s01"]["percent"] == 0


def test_null_on_submitting_and_done():
    items = {
        "a": _item(phase="submitting", started=None, source=None, expected=None),
        "b": _item(phase="done"),
        "c": _item(phase="queued", started=None, source=None, expected=None),
        "d": _item(phase="failed"),
        "e": _item(source="flow", percent=42, expected=None),
        "f": _item(source=None, expected=None),
    }
    view = prog.public_view(items, 1500.0)
    assert [view[k]["percent"] for k in "abcdef"] == [None, None, None, None, 42, None]
    assert items["e"]["expected"] is None and "expected" not in view["e"]  # input untouched, output stripped
