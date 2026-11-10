"""gflow.py progress hooks: Popen streaming, gflow-cli JSON log events, @@progress lines, timings.json."""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from filmmaker import gflow as gflow_mod
from filmmaker import progress as prog
from filmmaker.gflow import EXIT_TIMEOUT, Gflow, GflowError, _stream

TESTS = Path(__file__).parent
FAKE = TESTS / "fake_gflow.py"
FAKE_UI = TESTS / "fake_flow_ui.py"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("FAKE_GFLOW_ENV_LOG", str(tmp_path / "env.log"))
    for k in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_EVENTS", "FAKE_GFLOW_DELAY", "FAKE_GFLOW_RAW_FAIL",
              "FAKE_FLOW_UI_PROGRESS", "FAKE_FLOW_UI_FAIL", "FAKE_FLOW_UI_DELAY",
              "GFLOW_CLI_LOG_LEVEL", "GFLOW_CLI_LOG_FORMAT"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def events_gflow(**kw):
    seen = []
    g = Gflow(log=lambda *_: None, on_event=lambda name, data: seen.append((name, data)), **kw)
    return g, seen


def timings(tmp_path):
    p = tmp_path / "timings.json"
    return json.loads(p.read_text())["runs"] if p.exists() else {}


def t2v(g, tmp_path, **kw):
    opts = {"aspect": "16:9", "model": None, "duration": None, "project": "proj-123", "out": tmp_path / "s01.mp4"}
    opts.update(kw)
    return g.text_to_video("a cat", **opts)


# --- _stream -----------------------------------------------------------------------


def test_stream_kills_child_on_exception(monkeypatch):
    procs = []
    real = subprocess.Popen

    class Boom:
        def __init__(self, f):
            self.f = f

        def read(self):
            raise RuntimeError("interrupted")

        def close(self):
            self.f.close()

    class P(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            procs.append(self)
            self.stdout = Boom(self.stdout)

    monkeypatch.setattr(gflow_mod.subprocess, "Popen", P)
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="interrupted"):
        _stream([sys.executable, "-c", "import time; time.sleep(30)"], 60, lambda line: None)
    assert procs and procs[0].poll() is not None
    assert time.monotonic() - t0 < 5


def test_stream_lines_timeout_and_on_line_errors():
    lines = []

    def on_line(line):
        lines.append(line)
        raise ValueError("ignored")

    code, out, timed_out = _stream([sys.executable, "-c",
                                    "import sys; print('a', file=sys.stderr); print('b  ', file=sys.stderr); print('{}')"],
                                   30, on_line)
    assert (code, out.strip(), timed_out) == (0, "{}", False) and lines == ["a", "b"]
    t0 = time.monotonic()
    code, _, timed_out = _stream([sys.executable, "-c", "import time; time.sleep(30)"], 0.5, lambda l: None)
    assert timed_out and code != 0 and time.monotonic() - t0 < 5


# --- run_json -----------------------------------------------------------------------


@needs_ffmpeg
def test_argv_unchanged_and_log_env_set(env):
    g = Gflow(log=lambda *_: None)
    t2v(g, env, model="veo-lite", duration=8)
    g.text_to_image("a dog", out=env / "k.png", aspect="9:16", model="nano2", project=None)
    calls = [json.loads(l) for l in (env / "calls.log").read_text().splitlines()]
    assert calls == [
        ["video", "t2v", "a cat", "--aspect", "16:9", "-o", str(env / "s01.mp4"), "--model", "veo-lite",
         "--duration", "8", "--project", "proj-123", "--json"],
        ["image", "t2i", "a dog", "--aspect", "9:16", "--model", "nano2", "-o", str(env / "k.png"), "--json"],
    ]
    envs = [json.loads(l) for l in (env / "env.log").read_text().splitlines()]
    assert envs == [{"GFLOW_CLI_LOG_LEVEL": "INFO", "GFLOW_CLI_LOG_FORMAT": "json"}] * 2


def test_log_env_overrides_user_text_format(env, monkeypatch):
    monkeypatch.setenv("GFLOW_CLI_LOG_FORMAT", "text")
    monkeypatch.setenv("GFLOW_CLI_LOG_LEVEL", "WARNING")
    Gflow(log=lambda *_: None).create_project("x")
    assert json.loads((env / "env.log").read_text()) == {"GFLOW_CLI_LOG_LEVEL": "INFO", "GFLOW_CLI_LOG_FORMAT": "json"}


@needs_ffmpeg
def test_run_json_popen_keeps_errors_timeout_payload(env, monkeypatch):
    g = Gflow(log=lambda *_: None)
    assert g.create_project("x") == "proj-123"
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "s01.mp4:10:true")
    monkeypatch.setenv("FAKE_GFLOW_HINT", "log in again")
    with pytest.raises(GflowError) as ei:
        t2v(g, env)
    e = ei.value
    assert e.exit_code == 10 and e.retryable and e.payload["status"] == "fail"
    assert str(e) == "gflow exited 10: fake s01.mp4 (hint: log in again)"
    monkeypatch.delenv("FAKE_GFLOW_FAIL")
    monkeypatch.setenv("FAKE_GFLOW_DELAY", "20")
    g = Gflow(log=lambda *_: None, timeout=1)
    t0 = time.monotonic()
    with pytest.raises(GflowError) as ei:
        t2v(g, env)
    assert ei.value.exit_code == EXIT_TIMEOUT and "did not finish within 1s" in str(ei.value)
    assert time.monotonic() - t0 < 8
    assert timings(env) == {}  # failures and timeouts are not recorded


def test_error_detail_drops_info_log_lines(env, monkeypatch):
    monkeypatch.setenv("FAKE_GFLOW_RAW_FAIL", "project create:3")
    with pytest.raises(GflowError) as ei:
        Gflow(log=lambda *_: None).create_project("x")
    msg = str(ei.value)
    assert ei.value.exit_code == 3
    assert "boom: real error" in msg
    assert '"level"' not in msg and "cli.start" not in msg and "auth.session_loaded" not in msg


def test_dry_run_no_process_no_timings(env):
    g, seen = events_gflow(dry_run=True)
    t2v(g, env)
    assert seen == [] and not (env / "calls.log").exists() and not (env / "timings.json").exists()


# --- gflow-cli events ---------------------------------------------------------------


@needs_ffmpeg
@pytest.mark.parametrize("preset", ["migrated", "uiauto"])
def test_events_migrated_and_uiauto(env, monkeypatch, preset):
    monkeypatch.setenv("FAKE_GFLOW_EVENTS", preset)
    g, seen = events_gflow()
    path, media = t2v(g, env, model="veo-lite", duration=8)
    assert path == env / "s01.mp4" and media == "m-s01"
    assert [n for n, _ in seen] == ["start", "downloading", "downloading"]
    assert seen[0][1] == {"kind": "video:veo-lite:8", "expected": 150.0, "source": "estimate"}


@needs_ffmpeg
def test_events_migrated_drive_reporter_phases(env, monkeypatch):
    monkeypatch.setenv("FAKE_GFLOW_EVENTS", "migrated")
    phases = []
    rep = prog.Reporter(lambda k, item: phases.append(item["phase"]))
    g = Gflow(log=lambda *_: None, on_event=rep.gflow_event)
    rep.submitting("vid:s01")
    with rep.active("vid:s01"):
        t2v(g, env)
    rep.done("vid:s01")
    assert phases == ["submitting", "generating", "downloading", "done"]


@needs_ffmpeg
def test_unrelated_events_ignored_and_stdout_json_ok(env, monkeypatch):
    evs = env / "events.jsonl"
    evs.write_text("\n".join(json.dumps(e) for e in [
        {"event": "migrated.submit_clicked"}, {"event": "migrated.status", "status": "MEDIA_GENERATION_STATUS_ACTIVE"},
        {"event": "migrated.status", "status": "MEDIA_GENERATION_STATUS_FAILED"}, {"event": "other.thing", "x": "{"},
        {"event": "agent_only_composer.done"}]))
    monkeypatch.setenv("FAKE_GFLOW_EVENTS", str(evs))
    g, seen = events_gflow()
    path, media = t2v(g, env)
    assert [n for n, _ in seen] == ["start"]
    assert path == env / "s01.mp4" and media == "m-s01"


@needs_ffmpeg
def test_image_has_no_downloading(env, monkeypatch):
    monkeypatch.setenv("FAKE_GFLOW_EVENTS", "migrated")
    g, seen = events_gflow()
    g.text_to_image("a dog", out=env / "k.png", aspect="16:9", model="nano2", project="p")
    g.image_to_image("a dog", ["m-1"], out=env / "k2.png", aspect="16:9", model="nano2", project="p")
    assert [n for n, _ in seen] == ["start", "start"]
    assert seen[0][1] == {"kind": "image", "expected": 30.0, "source": "estimate"}
    assert seen[1][1]["kind"] == "image" and seen[1][1]["expected"] == timings(env)["image"][0]  # t2i and i2i share


def test_non_generation_command_no_start(env):
    g, seen = events_gflow()
    g.create_project("x")
    assert seen == [] and not (env / "timings.json").exists()


def test_stale_token_event_dropped(env):
    g, seen = events_gflow()
    old = object()
    g._set_live(old)
    g._emit(old, "downloading")
    g._set_live(None)  # the command is over
    g._emit(old, "downloading")
    g._set_live(object())  # another command now runs
    g._emit(old, "downloading")
    assert seen == [("downloading", {})]


def test_on_event_errors_swallowed(env):
    def boom(name, data):
        raise RuntimeError("x")

    g = Gflow(log=lambda *_: None, on_event=boom)
    assert g.create_project("x") == "proj-123"


# --- timings.json ----------------------------------------------------------------------


@needs_ffmpeg
def test_success_records_timings_by_kind(env):
    g = Gflow(log=lambda *_: None)
    t2v(g, env, model="veo-lite", duration=8)
    t2v(g, env, out=env / "s02.mp4")
    g.text_to_image("a dog", out=env / "k.png", aspect="16:9", model="nano2", project=None)
    runs = timings(env)
    assert set(runs) == {"video:veo-lite:8", "video:default:default", "image"}
    assert all(len(v) == 1 and v[0] > 0 for v in runs.values())


@needs_ffmpeg
def test_corrupt_timings_warns_once_and_runs(env):
    (env / "timings.json").write_text("not json")
    logs = []
    g = Gflow(log=logs.append, on_event=lambda *a: None)
    for name in ("k1.png", "k2.png"):
        g.text_to_image("a", out=env / name, aspect="16:9", model="nano2", project=None)
    assert sum("timings.json" in l for l in logs) == 1
    assert len(timings(env)["image"]) == 2  # rewritten as a valid file


# --- Flow chat driver (@@progress) ------------------------------------------------------


@pytest.fixture
def ui(env, monkeypatch):
    monkeypatch.setenv("FLOW_UI_CMD", f"{sys.executable} {FAKE_UI}")
    (env / "k.png").write_bytes(b"png")

    def call(g):
        return g.ui_image_to_video(env / "k.png", "pan", aspect="16:9", model="veo-lite", duration=None,
                                   project="proj-123", out=env / "ui.mp4")
    return call


@needs_ffmpeg
def test_flow_chat_percent_events_and_no_log(ui, env, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_FLOW_UI_PROGRESS", "10,abc,150,42")
    seen = []
    g = Gflow(log=print, on_event=lambda n, d: seen.append((n, d)))
    path, media = ui(g)
    assert path == env / "ui.mp4" and media == "m-ui"
    assert seen == [("start", {"kind": None, "expected": None, "source": None}),
                    ("percent", {"percent": 10}), ("percent", {"percent": 42})]
    out = capsys.readouterr()
    assert "@@progress" not in out.out + out.err
    assert "đang tải ảnh lên Flow: fake" in out.out
    assert timings(env) == {}  # the chat path is not recorded


@needs_ffmpeg
def test_flow_chat_without_progress_only_start(ui, env):
    g, seen = events_gflow()
    ui(g)
    assert seen == [("start", {"kind": None, "expected": None, "source": None})]


def test_progress_line_not_in_error(ui, env, monkeypatch):
    monkeypatch.setenv("FAKE_FLOW_UI_PROGRESS", "30")
    monkeypatch.setenv("FAKE_FLOW_UI_FAIL", "23:false")
    with pytest.raises(GflowError) as ei:
        ui(Gflow(log=lambda *_: None))
    assert "@@progress" not in str(ei.value)


def test_progress_line_not_in_error_fallback_detail(env, monkeypatch, tmp_path):
    # driver without JSON payload: the detail comes from stderr lines, which must not include @@progress
    script = tmp_path / "drv.py"
    script.write_text("import sys\nprint('@@progress 30', file=sys.stderr)\nprint('real failure', file=sys.stderr)\n"
                      "sys.exit(5)\n")
    monkeypatch.setenv("FLOW_UI_CMD", f"{sys.executable} {script}")
    (tmp_path / "k.png").write_bytes(b"png")
    with pytest.raises(GflowError) as ei:
        Gflow(log=lambda *_: None).ui_image_to_video(tmp_path / "k.png", "pan", aspect="16:9", model=None,
                                                     duration=None, project="p", out=tmp_path / "o.mp4")
    assert "real failure" in str(ei.value) and "@@progress" not in str(ei.value)
