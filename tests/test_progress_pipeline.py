"""Pipeline progress callbacks (vid:/kf:/char: items), driven by the fake gflow and the fake Flow chat driver."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from filmmaker import progress as prog
from filmmaker.gflow import Gflow
from filmmaker.pipeline import Pipeline, RunOptions
from filmmaker.storyboard import load_storyboard

TESTS = Path(__file__).parent
FAKE = TESTS / "fake_gflow.py"
FAKE_UI = TESTS / "fake_flow_ui.py"
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

T2V3 = """
title: Progress film
aspect: "16:9"
model: veo-lite
scenes:
  - {id: s01, mode: t2v, prompt: "one"}
  - {id: s02, mode: t2v, prompt: "two"}
  - {id: s03, mode: t2v, prompt: "three"}
"""

KF = """
title: Keyframe film
aspect: "16:9"
model: veo-lite
characters:
  - {name: Lan, description: "short black hair"}
  - {name: "Bà Mai", description: "grey bun"}
scenes:
  - {id: s01, mode: keyframe, image_prompt: "Lan at a cafe", prompt: "push in"}
  - {id: s02, mode: keyframe, image_prompt: "a street", prompt: "pan"}
  - {id: s03, mode: t2v, prompt: "rain"}
"""

I2V = """
title: Chat film
aspect: "16:9"
model: veo-lite
scenes:
  - {id: s01, mode: i2v, image: start.png, prompt: "pan left"}
"""


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "start.png").write_bytes(b"png")
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("FLOW_UI_CMD", f"{sys.executable} {FAKE_UI}")
    for k in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_EVENTS", "FAKE_GFLOW_DELAY", "FAKE_GFLOW_RAW_FAIL",
              "FAKE_FLOW_UI_PROGRESS", "FAKE_FLOW_UI_FAIL", "FAKE_FLOW_UI_DELAY"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


class Rec:
    """Every progress callback, in order."""

    def __init__(self, on_item=None):
        self.calls: list[tuple[str, dict | None]] = []
        self.on_item = on_item

    def __call__(self, key, item):
        self.calls.append((key, None if item is None else dict(item)))
        if self.on_item and item is not None:
            self.on_item(key, item)

    def phases(self, key):
        return [i["phase"] if i else None for k, i in self.calls if k == key]

    def items(self, key):
        return [i for k, i in self.calls if k == key and i]

    def final(self):
        state: dict[str, dict] = {}
        for k, i in self.calls:
            if i is None:
                state.pop(k, None)
            else:
                state[k] = i
        return state

    def index(self, key, phase):
        return next(n for n, (k, i) in enumerate(self.calls) if k == key and i and i["phase"] == phase)


def make(env, text, rec=None, *, g=None, sleep=lambda *_: None, log=lambda *_: None):
    path = env / "story.yaml"
    path.write_text(text, encoding="utf-8")
    sb = load_storyboard(path)
    return Pipeline(sb, env / "out", g or Gflow(log=log), log=log, sleep=sleep, progress=rec)


def runs(env):
    p = env / "timings.json"
    return json.loads(p.read_text())["runs"] if p.exists() else {}


def opts(**kw):
    kw.setdefault("pause", (0, 0))
    return RunOptions(**kw)


# --- AC2 / AC3: order of callbacks -----------------------------------------------------


def test_video_job_callback_order(env):
    rec = Rec()
    pipe = make(env, T2V3, rec)
    pipe.run(opts(only={"s03"}))  # s03 done before
    rec.calls.clear()
    pipe = make(env, T2V3, rec)
    report = pipe.run(opts())
    assert report.done == ["s01", "s02"] and report.skipped == ["s03"]
    assert [(k, i["phase"]) for k, i in rec.calls[:2]] == [("vid:s01", "queued"), ("vid:s02", "queued")]
    assert rec.phases("vid:s01") == ["queued", "submitting", "generating", "done"]
    assert rec.phases("vid:s02") == ["queued", "submitting", "generating", "done"]
    assert rec.index("vid:s01", "done") < rec.index("vid:s02", "submitting")
    assert "vid:s03" not in {k for k, _ in rec.calls}
    final = rec.final()
    assert set(final) == {"vid:s01", "vid:s02"} and all(i["phase"] == "done" for i in final.values())
    gen = rec.items("vid:s01")[2]
    # s03's run was timed, so s01's estimate is that time, not the 150 s default.
    assert gen["source"] == "estimate" and gen["expected"] == runs(env)["video:veo-lite:default"][0] and gen["started"]


def test_keyframes_job_callback_order(env):
    rec = Rec()
    report = make(env, KF, rec).run(opts(keyframes_only=True))
    assert report.done == ["s01", "s02"]
    assert [(k, i["phase"]) for k, i in rec.calls[:2]] == [("kf:s01", "queued"), ("kf:s02", "queued")]
    assert rec.phases("kf:s01") == ["queued", "submitting", "generating", "done"]
    assert rec.phases("kf:s02") == ["queued", "submitting", "generating", "done"]
    assert rec.index("kf:s01", "done") < rec.index("kf:s02", "submitting")
    assert all(k.startswith("kf:") for k, _ in rec.calls)


def test_portraits_callback_order(env):
    rec = Rec()
    pipe = make(env, KF, rec, g=Gflow(log=lambda *_: None, flow_characters=False))  # gflow t2i path
    chars = [(c, env / "assets" / f"c{i}.png") for i, c in enumerate(pipe.sb.characters)]
    report = pipe.generate_portraits(chars, opts())
    assert report.done == ["Lan", "Bà Mai"]
    assert [(k, i["phase"]) for k, i in rec.calls[:2]] == [("char:Lan", "queued"), ("char:Bà Mai", "queued")]
    assert rec.phases("char:Lan") == ["queued", "submitting", "generating", "done"]
    assert rec.phases("char:Bà Mai") == ["queued", "submitting", "generating", "done"]
    assert rec.index("char:Lan", "done") < rec.index("char:Bà Mai", "submitting")
    assert rec.items("char:Lan")[2]["expected"] == 30.0


def test_portraits_on_done_gets_index(env):
    rec = Rec()
    pipe = make(env, KF, rec)
    chars = [(c, env / "assets" / f"c{i}.png") for i, c in enumerate(reversed(pipe.sb.characters))]
    seen = []

    def on_done(idx, c, dest):
        seen.append((idx, c.name, dest.is_file(), rec.phases(f"char:{c.name}")[-1]))
        if idx == 1:
            raise RuntimeError("disk full")

    logs = []
    pipe.log = logs.append
    report = pipe.generate_portraits(chars, opts(), on_done=on_done)
    assert seen == [(0, "Bà Mai", True, "generating"), (1, "Lan", True, "generating")]
    assert report.done == ["Bà Mai", "Lan"] and rec.final()["char:Lan"]["phase"] == "done"
    assert any("disk full" in l for l in logs)


def test_portraits_stop_and_fatal_drop_rest(env, monkeypatch):
    rec = Rec()
    pipe = make(env, KF, rec, g=Gflow(log=lambda *_: None, flow_characters=False))  # gflow t2i path
    chars = [(c, env / "assets" / f"c{i}.png") for i, c in enumerate(pipe.sb.characters)]
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "c0.png:37:false")
    report = pipe.generate_portraits(chars, opts())
    assert report.aborted and rec.final() == {"char:Lan": rec.final()["char:Lan"]}
    assert rec.final()["char:Lan"]["phase"] == "failed" and rec.phases("char:Bà Mai") == ["queued", None]
    monkeypatch.delenv("FAKE_GFLOW_FAIL")
    rec2 = Rec()
    pipe = make(env, KF, rec2)
    report = pipe.generate_portraits(chars, opts(should_stop=lambda: True))
    assert report.stopped and rec2.final() == {}


# --- AC4: failures --------------------------------------------------------------------


def test_failed_item_and_fatal_abort_drops_rest(env, monkeypatch):
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "s02.mp4:1:false")
    rec = Rec()
    report = make(env, T2V3, rec).run(opts())
    assert report.done == ["s01", "s03"] and "s02" in report.failed
    assert rec.phases("vid:s02") == ["queued", "submitting", "generating", "failed"]
    assert rec.final()["vid:s03"]["phase"] == "done"

    shutil.rmtree(env / "out")
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "s02.mp4:37:false")  # out of credits: fatal
    rec = Rec()
    report = make(env, T2V3, rec).run(opts())
    assert report.aborted and report.done == ["s01"]
    final = rec.final()
    assert final["vid:s02"]["phase"] == "failed" and "vid:s03" not in final
    assert rec.phases("vid:s03") == ["queued", None]

    shutil.rmtree(env / "out")
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "s02.mp4:1:false")
    rec = Rec()
    report = make(env, T2V3, rec).run(opts(fail_fast=True))
    assert report.aborted and set(rec.final()) == {"vid:s01", "vid:s02"}


# --- AC5: retry -----------------------------------------------------------------------


def test_retry_wait_is_queued_attempt2_then_new_started(env, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(prog, "now", clock.now)
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video t2v:9:true:1")
    rec = Rec()
    report = make(env, T2V3, rec, sleep=clock.sleep).run(opts(only={"s01"}, max_retries=1))
    assert report.done == ["s01"]
    items = rec.items("vid:s01")
    assert [(i["phase"], i["attempt"]) for i in items] == [
        ("queued", 1), ("submitting", 1), ("generating", 1), ("queued", 2), ("generating", 2), ("done", 2)]
    wait = items[3]
    assert wait["started"] is None and wait["percent"] is None and wait["source"] is None
    assert items[4]["started"] > items[2]["started"]
    # what the API would show while waiting
    assert prog.public_view({"vid:s01": wait}, clock.now())["vid:s01"]["percent"] is None


# --- AC6: stop --------------------------------------------------------------------------


def _stop_after_first_generating():
    flag = {"stop": False}

    def on_item(key, item):
        if key == "vid:s01" and item["phase"] == "generating":
            flag["stop"] = True

    return flag, on_item


def test_stop_keeps_running_item_drops_unstarted(env):
    flag, on_item = _stop_after_first_generating()
    rec = Rec(on_item)
    report = make(env, T2V3, rec).run(opts(should_stop=lambda: flag["stop"]))
    assert report.stopped and report.done == ["s01"]
    assert rec.final() == {"vid:s01": rec.final()["vid:s01"]} and rec.final()["vid:s01"]["phase"] == "done"
    assert rec.phases("vid:s02") == ["queued", None] and rec.phases("vid:s03") == ["queued", None]


def test_record_after_stop(env):
    flag, on_item = _stop_after_first_generating()
    make(env, T2V3, Rec(on_item)).run(opts(should_stop=lambda: flag["stop"]))
    assert len(runs(env)["video:veo-lite:default"]) == 1


# --- AC7: items dropped on the way -----------------------------------------------------


def test_refused_scene_dropped(env):
    rec = Rec()
    report = make(env, T2V3, rec).run(opts(refuse=lambda s: "model đã ngừng" if s.id == "s02" else None))
    assert report.done == ["s01", "s03"]
    assert rec.phases("vid:s02") == ["queued", None] and "vid:s02" not in rec.final()


def test_ensure_project_failure_empties_progress(env, monkeypatch):
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "project create:10:false")
    rec = Rec()
    report = make(env, T2V3, rec).run(opts())
    assert report.aborted and rec.calls and rec.final() == {}
    rec = Rec()
    make(env, KF, rec).run(opts(keyframes_only=True))
    assert rec.calls and rec.final() == {}
    rec = Rec()
    pipe = make(env, KF, rec)
    pipe.generate_portraits([(pipe.sb.characters[0], env / "c.png")], opts())
    assert rec.calls and rec.final() == {}


def test_keyframe_scene_in_video_job_has_kf_item(env):
    rec = Rec()
    report = make(env, KF, rec).run(opts(only={"s01"}))
    assert report.done == ["s01"]
    assert rec.phases("kf:s01") == ["submitting", "generating", "done"]
    assert rec.phases("vid:s01") == ["queued", "submitting", "generating", "done"]
    i_kf_sub, i_kf_done = rec.index("kf:s01", "submitting"), rec.index("kf:s01", "done")
    assert rec.index("vid:s01", "submitting") < i_kf_sub < i_kf_done < rec.index("vid:s01", "generating")
    # the keyframe is reused next time: no kf: item
    rec = Rec()
    make(env, KF, rec).run(opts(only={"s01"}, force={"s01"}))
    assert "kf:s01" not in {k for k, _ in rec.calls}


# --- AC9 / AC10: events and started ----------------------------------------------------


def test_downloading_keeps_started(env, monkeypatch):
    monkeypatch.setenv("FAKE_GFLOW_EVENTS", "migrated")
    rec = Rec()
    make(env, T2V3, rec).run(opts(only={"s01"}))
    items = rec.items("vid:s01")
    assert [i["phase"] for i in items] == ["queued", "submitting", "generating", "downloading", "done"]
    assert items[3]["started"] == items[2]["started"] and items[3]["source"] == "estimate"


def test_image_item_stays_generating_until_done(env, monkeypatch):
    monkeypatch.setenv("FAKE_GFLOW_EVENTS", "migrated")
    rec = Rec()
    make(env, KF, rec).run(opts(keyframes_only=True, only={"s01"}))
    assert rec.phases("kf:s01") == ["queued", "submitting", "generating", "done"]


def test_fallback_resets_started(env, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(prog, "now", clock.now)
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")  # gflow refuses i2v -> Flow chat driver

    def on_item(key, item):
        if item["phase"] == "generating":
            clock.t += 10

    rec = Rec(on_item)
    report = make(env, I2V, rec).run(opts())
    assert report.done == ["s01"]
    gens = [i for i in rec.items("vid:s01") if i["phase"] == "generating"]
    assert len(gens) == 2
    assert gens[0]["source"] == "estimate" and gens[1]["source"] is None
    assert gens[1]["started"] > gens[0]["started"]


# --- AC11: timings ---------------------------------------------------------------------


def test_keyframe_scene_records_two_kinds(env, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(prog, "now", clock.now)

    def on_item(key, item):
        if item["phase"] == "generating":
            clock.t += 20 if key.startswith("kf:") else 90

    make(env, KF, Rec(on_item), sleep=clock.sleep).run(opts(only={"s01"}))
    assert runs(env) == {"image": [20], "video:veo-lite:default": [90]}


def test_no_record_on_fail_timeout_dry_flowchat(env, monkeypatch):
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "s01.mp4:1:false")
    make(env, T2V3, Rec()).run(opts(only={"s01"}))
    assert runs(env) == {}
    monkeypatch.delenv("FAKE_GFLOW_FAIL")
    monkeypatch.setenv("FAKE_GFLOW_DELAY", "20")
    report = make(env, T2V3, Rec(), g=Gflow(log=lambda *_: None, timeout=1)).run(opts(only={"s02"}))
    assert report.failed and runs(env) == {}
    monkeypatch.delenv("FAKE_GFLOW_DELAY")
    make(env, T2V3, None, g=Gflow(dry_run=True, log=lambda *_: None)).run(opts())
    assert not (env / "timings.json").exists()
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    report = make(env, I2V, Rec()).run(opts())
    assert report.done == ["s01"] and runs(env) == {}


# --- AC12 / AC13: Flow chat percentage -------------------------------------------------


@pytest.fixture
def chat(env, monkeypatch):
    monkeypatch.setenv("FAKE_GFLOW_FAIL", "video i2v:36:false")
    return env


def test_flow_chat_percent_from_driver(chat, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_FLOW_UI_PROGRESS", "10,42")
    rec = Rec()
    report = make(chat, I2V, rec, g=Gflow(log=print), log=print).run(opts())
    assert report.done == ["s01"]
    items = rec.items("vid:s01")
    flow = [i for i in items if i["source"] == "flow" and i["phase"] == "generating"]
    assert [i["percent"] for i in flow] == [10, 42]
    last = items[-2]
    assert last["phase"] == "generating" and last["percent"] == 42
    assert prog.public_view({"vid:s01": last}, 0)["vid:s01"] == {
        "phase": "generating", "percent": 42, "source": "flow", "started": last["started"], "attempt": 1}
    out = capsys.readouterr()
    assert "@@progress" not in out.out + out.err


def test_flow_chat_no_percent_is_spinner(chat):
    rec = Rec()
    make(chat, I2V, rec).run(opts())
    last = rec.items("vid:s01")[-2]
    assert (last["phase"], last["percent"], last["source"]) == ("generating", None, None)
    assert prog.public_view({"vid:s01": last}, 1e12)["vid:s01"]["percent"] is None


def test_invalid_progress_lines_ignored(chat, monkeypatch):
    monkeypatch.setenv("FAKE_FLOW_UI_PROGRESS", "abc,150,-1")
    rec = Rec()
    lines = []
    make(chat, I2V, rec, g=Gflow(log=lines.append), log=lines.append).run(opts())
    last = rec.items("vid:s01")[-2]
    assert (last["phase"], last["percent"], last["source"]) == ("generating", None, None)
    assert not any("@@progress" in l for l in lines)


def test_progress_line_not_in_error(chat, monkeypatch):
    monkeypatch.setenv("FAKE_FLOW_UI_PROGRESS", "30")
    monkeypatch.setenv("FAKE_FLOW_UI_FAIL", "23:false")
    rec = Rec()
    report = make(chat, I2V, rec).run(opts())
    assert "@@progress" not in report.failed["s01"]
    assert rec.final()["vid:s01"]["phase"] == "failed"


# --- no progress callback: nothing changes ------------------------------------------------


def test_without_progress_gflow_has_no_hook(env):
    pipe = make(env, T2V3, None)
    assert pipe.g.on_event is None
    assert pipe.run(opts(only={"s01"})).done == ["s01"]
