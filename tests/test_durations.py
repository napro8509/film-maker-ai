"""filmmaker/durations.py: what counts as a bad scene duration, rounding, raw edits, base (AC1, AC2, AC14, AC15, AC19).

Pure tests: dry Gflow, tmp_path projects, no network. Settings are isolated by conftest.
"""
import subprocess
import sys

import pytest
import yaml

from filmmaker import durations as dur, model_change, model_meta
from filmmaker.storyboard import Storyboard
from tests.duration_helpers import ac12_project, ac16_project
from tests.model_helpers import pipeline, set_scene, storyboard_dict, ui_out_dir, write_override, write_project


def sb_of(scenes, model="veo-lite"):
    return Storyboard.model_validate(storyboard_dict(model, scenes))


# --- AC1 -----------------------------------------------------------------------

def test_bad_durations_rows():
    meta = model_meta.load()
    sb = sb_of([{"id": "s01", "duration": 5}, {"id": "s02", "duration": 7}, {"id": "s03", "duration": 9},
                {"id": "s04", "duration": 3}, {"id": "s05", "duration": 6}, {"id": "s06", "duration": 7},
                {"id": "s07", "duration": 5}])
    rows = dur.bad_durations(sb, meta, interrupted={"s06", "s07"}, done={"s07"})
    by = {r["id"]: r for r in rows}
    assert [r["id"] for r in rows] == ["s01", "s02", "s03", "s04", "s06", "s07"]  # s05 = 6 is valid
    assert {k: (by[k]["up"], by[k]["down"]) for k in ("s01", "s02", "s03", "s04")} == {
        "s01": (6, 4), "s02": (8, 6), "s03": (8, 8), "s04": (4, 4)}
    assert by["s01"] == {"id": "s01", "model": "veo-lite", "duration": 5, "allowed": [4, 6, 8], "up": 6, "down": 4,
                         "roundable": True, "reason": None}
    assert (by["s06"]["roundable"], by["s06"]["reason"]) == (False, "interrupted")
    assert (by["s07"]["roundable"], by["s07"]["reason"]) == (False, "done")  # done wins over interrupted


def test_round_options_shared_with_model_change():
    assert model_change.round_options is dur.round_options
    assert dur.round_options(10, [4, 6, 8]) == (8, 8)


# --- AC2 -----------------------------------------------------------------------

def test_not_bad_cases():
    meta = model_meta.load()
    sb = sb_of([{"id": "s01"}, {"id": "s02", "duration": 5, "model": "veo-fast"},
                {"id": "s03", "duration": 5, "model": "omni-1.2-flash"}, {"id": "s04", "duration": 5, "model": "nope"}])
    assert dur.bad_durations(sb, meta) == []
    empty = sb_of([{"id": "s01", "duration": 5, "model": ""}], model=None)  # Flow picks
    assert not dur.is_bad(empty, empty.scenes[0], meta) and dur.bad_durations(empty, meta) == []


def test_broken_metadata_file_no_crash(tmp_path, monkeypatch):
    bad = write_override(tmp_path / "models.yaml", "models: [oops\n")
    monkeypatch.setenv("FILMMAKER_MODELS", str(bad))
    meta = model_meta.load()
    assert meta.errors  # package values are used
    sb = sb_of([{"id": "s01", "duration": 5}])
    assert [r["id"] for r in dur.bad_durations(sb, meta)] == ["s01"]
    assert dur.refuse_bad_duration(sb)(sb.scenes[0]).startswith("độ dài 5 giây không hợp lệ với veo-lite")


# --- scan / preview / apply (AC1, AC14, AC15) ----------------------------------

def project(tmp_path):
    p = write_project(tmp_path / "ws", "film", "veo-lite", [
        {"id": "s01", "duration": 5}, {"id": "s02", "duration": 7}, {"id": "s03", "duration": 6},
        {"id": "s04", "duration": 5}, {"id": "s05", "duration": 7}, {"id": "s06", "duration": 9},
        {"id": "s07", "duration": 3}])
    set_scene(p, "s02", "failed")
    set_scene(p, "s04", "done")
    set_scene(p, "s05", "interrupted")
    set_scene(p, "s06", "changed")
    return p, set_scene(p, "s07", "todo")


def test_preview_and_apply_raw_only_duration(tmp_path):
    p, pipe = project(tmp_path)
    meta = model_meta.load()
    rows = dur.scan(pipe, meta)
    assert {r["id"]: r["reason"] for r in rows} == {"s01": None, "s02": None, "s04": "done",
                                                   "s05": "interrupted", "s06": None, "s07": None}
    assert [r["id"] for r in dur.scan(pipe, meta, [pipe.sb.scenes[0]])] == ["s01"]
    scenes, skipped = dur.preview(pipe, meta)
    assert [s["id"] for s in scenes] == ["s01", "s02", "s06", "s07"]  # changed is roundable (minor 1)
    assert set(scenes[0]) == {"id", "model", "duration", "allowed", "up", "down"}
    assert skipped == ["s05"]  # done s04 is in neither
    edits = dur.edits_for(scenes, "up")
    assert edits == {"s01": 6, "s02": 8, "s06": 8, "s07": 4}
    assert dur.edits_for(scenes, "down") == {"s01": 4, "s02": 6, "s06": 8, "s07": 4}
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    raw["extra_key"] = {"keep": [1, 2]}
    new = dur.apply_to_raw(raw, edits)
    assert raw["scenes"][0]["duration"] == 5  # input untouched
    diff = [(a["id"], k) for a, b in zip(raw["scenes"], new["scenes"]) for k in set(a) | set(b) if a.get(k) != b.get(k)]
    assert sorted(diff) == [("s01", "duration"), ("s02", "duration"), ("s06", "duration"), ("s07", "duration")]
    assert {k: v for k, v in new.items() if k != "scenes"} == {k: v for k, v in raw.items() if k != "scenes"}


def test_edits_for_rejects_direction():
    with pytest.raises(ValueError):
        dur.edits_for([], "sideways")


def test_rewrite_file_only_duration(tmp_path):
    p, _ = project(tmp_path)
    before = yaml.safe_load(p.read_text(encoding="utf-8"))
    dur.rewrite_file(p, {"s01": 6})
    after = yaml.safe_load(p.read_text(encoding="utf-8"))
    before["scenes"][0]["duration"] = 6
    assert after == before
    assert not p.with_name(p.name + ".tmp").exists()


def test_base_changes_with_file_group_and_allowed(tmp_path):
    p, pipe = project(tmp_path)
    meta = model_meta.load()
    data = p.read_bytes()
    g = dur.groups(pipe)
    assert g["s04"] == "done" and g["s05"] == "interrupted" and g["s01"] == "todo"
    base = dur.base_of(data, g)
    assert base == dur.base_of(data, dict(reversed(list(g.items()))))
    assert dur.base_of(data + b"\n", g) != base
    assert dur.base_of(data, {**g, "s01": "done"}) != base
    a = dur.allowed_map(pipe, meta)
    assert a["s01"] == [4, 6, 8]
    assert dur.base_of(data, g, a) != dur.base_of(data, g, {**a, "s01": [4, 8]})


# --- text ----------------------------------------------------------------------

def test_describe_and_refuse():
    row = {"id": "s03", "model": "veo-lite", "duration": 5, "allowed": [4, 6, 8], "up": 6, "down": 4,
           "roundable": True, "reason": None}
    assert dur.describe(row) == "s03: 5 giây — veo-lite chỉ nhận 4, 6, 8 (lên 6 / xuống 4)"
    assert dur.describe({**row, "reason": "interrupted"}).endswith(
        " — bị ngắt — không tự làm tròn, sửa tay hoặc tải clip về")
    assert dur.describe({**row, "reason": "done"}).endswith(" — đã có clip — sửa tay `duration:` trong storyboard rồi render lại")
    sb = sb_of([{"id": "s01", "duration": 5}, {"id": "s02", "duration": 6}, {"id": "s03"}])
    refuse = dur.refuse_bad_duration(sb)
    msg = refuse(sb.scenes[0])
    assert "độ dài 5 giây không hợp lệ với veo-lite (chỉ nhận 4, 6, 8)" in msg and "round-durations" in msg
    assert refuse(sb.scenes[1]) is None and refuse(sb.scenes[2]) is None


# --- AC19 ----------------------------------------------------------------------

def test_round_new_storyboard_up():
    meta = model_meta.load()
    sb = sb_of([{"id": "s01", "duration": 5}, {"id": "s02", "duration": 7}, {"id": "s03"},
                {"id": "s04", "duration": 5, "model": "veo-fast"}, {"id": "s05", "duration": 9}])
    assert dur.round_new_storyboard(sb, meta) is sb
    assert [s.duration for s in sb.scenes] == [6, 8, None, 5, 8]


def test_module_does_not_import_web_or_pipeline():
    code = ("import sys, filmmaker.durations; "
            "print(any(m.startswith(('filmmaker.web', 'filmmaker.pipeline')) for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False"


# --- spec fixtures (AC1, AC15, AC16) ---------------------------------------------

def scene_of(pipe, sid):
    return next(s for s in pipe.sb.scenes if s.id == sid)


def test_scan_fixture_ac12(tmp_path):
    p = ac12_project(tmp_path / "ws")
    pipe = pipeline(p)
    meta = model_meta.load()
    assert {r["id"]: (r["roundable"], r["reason"]) for r in dur.scan(pipe, meta)} == {
        "s01": (True, None), "s02": (True, None), "s04": (False, "done"), "s05": (False, "interrupted")}
    state_file = ui_out_dir(p) / "state.json"
    state = state_file.read_bytes()
    fp_done = pipe.state.scene("s04").get("fingerprint")
    scenes, skipped = dur.preview(pipe, meta)
    assert [(s["id"], s["up"], s["down"]) for s in scenes] == [("s01", 6, 4), ("s02", 8, 6)]
    assert skipped == ["s05"]
    dur.rewrite_file(p, dur.edits_for(scenes, "up"))
    assert [s["duration"] for s in yaml.safe_load(p.read_text(encoding="utf-8"))["scenes"]] == [6, 8, 6, 5, 7]
    # AC15: state.json untouched to the byte; the done scene keeps its fingerprint and status
    assert state_file.read_bytes() == state
    after = pipeline(p)
    s04 = scene_of(after, "s04")
    assert after.sb.scene_fingerprint(s04) == fp_done and after.scene_group(s04) == "done"
    assert after.scene_group(scene_of(after, "s05")) == "interrupted"


def test_scan_fixture_ac16(tmp_path):
    pipe = pipeline(ac16_project(tmp_path / "ws"))
    meta = model_meta.load()
    rows = dur.scan(pipe, meta)
    assert [(r["id"], r["roundable"], r["reason"]) for r in rows] == [("s01", True, None),
                                                                      ("s02", False, "interrupted")]
    scenes, skipped = dur.preview(pipe, meta)
    assert [s["id"] for s in scenes] == ["s01"] and skipped == ["s02"]


def test_scene_model_overrides_storyboard_model():
    meta = model_meta.load()
    sb = sb_of([{"id": "s01", "duration": 5, "model": "veo-fast"}, {"id": "s02", "duration": 5}], model="veo-fast")
    sb.scenes[1].model = "veo-lite"
    assert [(r["id"], r["model"]) for r in dur.bad_durations(sb, meta)] == [("s02", "veo-lite")]
