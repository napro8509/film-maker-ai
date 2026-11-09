"""`filmmaker status --json`: machine-readable progress (read-only, no gflow)."""

from __future__ import annotations

import json
import subprocess
from importlib.metadata import version
from pathlib import Path

import pytest
from click.testing import CliRunner

from filmmaker.cli import main
from filmmaker.gflow import Gflow
from filmmaker.pipeline import Pipeline
from filmmaker.storyboard import load_storyboard

KEYS = {"id", "mode", "status", "prompt", "video", "keyframe", "error"}
CODES = {"done", "changed", "interrupted", "failed", "todo"}
LONG_PROMPT = "Một cảnh quay rất dài mô tả quán cà phê buổi sáng, khói bốc lên từ ly cà phê phin, người qua lại " * 2
LONG_ERROR = "E" * 300

SB_YAML = f"""
title: "Quán cà phê"
scenes:
  - id: k1
    mode: keyframe
    image_prompt: "Quán cà phê lúc bình minh"
    prompt: "Máy quay tiến chậm vào quán"
  - id: t1
    mode: t2v
    prompt: "{LONG_PROMPT}"
  - id: i1
    mode: i2v
    image: start.png
    prompt: "Ảnh chuyển động nhẹ"
  - id: t2
    mode: t2v
    prompt: "Sữa đặc hòa vào cà phê"
  - id: t3
    mode: t2v
    prompt: "Phố đông xe máy"
  - id: t4
    mode: t2v
    prompt: "Lan mỉm cười"
"""


@pytest.fixture
def sb_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "start.png").write_bytes(b"png")
    sb_path = tmp_path / "cafe.yaml"
    sb_path.write_text(SB_YAML, encoding="utf-8")
    return sb_path


def out_of(sb_path: Path) -> Path:
    return sb_path.parent / "output" / sb_path.stem


def write_state(out: Path, data: dict) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    p = out / "state.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def run(*args: str):
    return CliRunner().invoke(main, ["status", *args])


def fp(sb_path: Path, sid: str) -> str:
    sb = load_storyboard(sb_path)
    return sb.scene_fingerprint(next(s for s in sb.scenes if s.id == sid))


def clip(out: Path, sid: str) -> Path:
    p = out / "clips" / f"{sid}.mp4"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"mp4")
    return p


def mixed_state(sb_path: Path) -> tuple[Path, dict]:
    """k1 done, t1 failed (long error), i1 changed (fp mismatch, clip kept),
    t2 changed (fp ok, clip deleted), t3 interrupted (with error), t4 todo."""
    out = out_of(sb_path)
    k1 = clip(out, "k1")
    i1 = clip(out, "i1")
    data = {
        "project_id": "state-pid",
        "scenes": {
            "k1": {"status": "done", "fingerprint": fp(sb_path, "k1"), "video": str(k1)},
            "t1": {"status": "failed", "error": LONG_ERROR},
            "i1": {"status": "done", "fingerprint": "stale", "video": str(i1)},
            "t2": {"status": "done", "fingerprint": fp(sb_path, "t2"), "video": str(out / "clips" / "t2.mp4")},
            "t3": {"status": "submitting", "error": "đã gửi, chưa rõ kết quả"},
        },
    }
    write_state(out, data)
    return out, data


def test_json_is_single_object_unicode(sb_env):
    res = run(str(sb_env), "--json")
    assert res.exit_code == 0, res.output
    assert isinstance(json.loads(res.stdout), dict)
    assert res.stdout.strip().count("\n") == 0
    assert "Quán cà phê" in res.stdout
    assert "\\u" not in res.stdout


@pytest.mark.parametrize("yaml_pid,state_pid,expected", [
    (None, "from-state", "from-state"),
    ("from-yaml", "from-state", "from-yaml"),
    (None, None, None),
])
def test_project_id_sources(sb_env, yaml_pid, state_pid, expected):
    if yaml_pid:
        sb_env.write_text(f'project_id: "{yaml_pid}"\n' + SB_YAML, encoding="utf-8")
    if state_pid:
        write_state(out_of(sb_env), {"project_id": state_pid, "scenes": {}})
    data = json.loads(run(str(sb_env), "--json").stdout)
    assert set(data) == {"title", "storyboard", "out_dir", "project_id", "summary", "scenes"}
    assert data["project_id"] == expected


def test_scenes_order_and_keys(sb_env):
    data = json.loads(run(str(sb_env), "--json").stdout)
    assert [s["id"] for s in data["scenes"]] == ["k1", "t1", "i1", "t2", "t3", "t4"]
    assert all(set(s) == KEYS for s in data["scenes"])
    assert [s["mode"] for s in data["scenes"]] == ["keyframe", "t2v", "i2v", "t2v", "t2v", "t2v"]
    assert data["storyboard"] == str(sb_env.resolve())


def test_never_run_all_todo(sb_env):
    data = json.loads(run(str(sb_env), "--json").stdout)
    assert {s["status"] for s in data["scenes"]} == {"todo"}
    assert all(s["video"] is None and s["keyframe"] is None and s["error"] is None for s in data["scenes"])


def test_all_five_statuses(sb_env):
    out, _ = mixed_state(sb_env)
    res = run(str(sb_env), "--json")
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    by_id = {s["id"]: s for s in data["scenes"]}

    pipe = Pipeline(load_storyboard(sb_env), out, Gflow(dry_run=True, log=lambda *_: None))
    for s in pipe.sb.scenes:
        assert by_id[s.id]["status"] == pipe.scene_status(s)
        assert by_id[s.id]["status"] in CODES
    assert {k: v["status"] for k, v in by_id.items()} == {
        "k1": "done", "t1": "failed", "i1": "changed", "t2": "changed", "t3": "interrupted", "t4": "todo",
    }
    # AC6: video = clip on disk, whatever the status
    assert by_id["k1"]["video"] == str((out / "clips" / "k1.mp4").resolve())
    assert by_id["i1"]["video"] == str((out / "clips" / "i1.mp4").resolve())
    assert by_id["t2"]["video"] is None
    assert by_id["t4"]["video"] is None
    # AC8
    assert data["summary"] == {"total": 6, "done": 1, "changed": 2, "interrupted": 1, "failed": 1, "todo": 1}
    assert sum(data["summary"][c] for c in CODES) == data["summary"]["total"]


def test_error_full_and_any_status(sb_env):
    mixed_state(sb_env)
    by_id = {s["id"]: s for s in json.loads(run(str(sb_env), "--json").stdout)["scenes"]}
    assert by_id["t1"]["error"] == LONG_ERROR
    assert by_id["t3"]["error"] == "đã gửi, chưa rõ kết quả"
    assert by_id["k1"]["error"] is None
    assert by_id["t1"]["prompt"] == load_storyboard(sb_env).scenes[1].prompt
    assert len(by_id["t1"]["prompt"]) > 60


def test_empty_error_is_null(sb_env):
    # Deliberate (design): `st.get("error") or None` maps an empty string to null.
    write_state(out_of(sb_env), {"scenes": {"t1": {"status": "failed", "error": ""}}})
    by_id = {s["id"]: s for s in json.loads(run(str(sb_env), "--json").stdout)["scenes"]}
    assert by_id["t1"]["error"] is None


def test_video_relative_resolved_against_cwd(sb_env, tmp_path):
    out = out_of(sb_env)
    clip(out, "t4")
    rel = "output/cafe/clips/t4.mp4"
    write_state(out, {"scenes": {"t4": {"status": "done", "fingerprint": fp(sb_env, "t4"), "video": rel}}})
    by_id = {s["id"]: s for s in json.loads(run(str(sb_env), "--json").stdout)["scenes"]}
    assert by_id["t4"]["status"] == "done"
    assert by_id["t4"]["video"] == str((tmp_path / rel).resolve())
    assert Path(by_id["t4"]["video"]).is_absolute()


def test_empty_video_path_is_null(sb_env):
    write_state(out_of(sb_env), {"scenes": {"t4": {"status": "done", "video": ""}}})
    by_id = {s["id"]: s for s in json.loads(run(str(sb_env), "--json").stdout)["scenes"]}
    assert by_id["t4"]["video"] is None


def test_keyframe_field(sb_env):
    out = out_of(sb_env)
    sb = load_storyboard(sb_env)
    k1 = sb.scenes[0]
    kf_dir = out / "keyframes"
    kf_dir.mkdir(parents=True)
    for sid in ("k1", "t1", "i1"):
        (kf_dir / f"{sid}.png").write_bytes(b"png")
    write_state(out, {"scenes": {
        "k1": {"keyframe_prompt": sb.image_prompt(k1), "keyframe_refs": sb.refs_signature(k1)},
        "t1": {"keyframe_prompt": sb.image_prompt(sb.scenes[1]), "keyframe_refs": []},
    }})
    by_id = {s["id"]: s for s in json.loads(run(str(sb_env), "--json").stdout)["scenes"]}
    assert by_id["k1"]["keyframe"] == str((kf_dir / "k1.png").resolve())
    assert by_id["t1"]["keyframe"] is None
    assert by_id["i1"]["keyframe"] is None

    (kf_dir / "k1.png").unlink()
    by_id = {s["id"]: s for s in json.loads(run(str(sb_env), "--json").stdout)["scenes"]}
    assert by_id["k1"]["keyframe"] is None


def test_out_option_and_default_out_dir(sb_env, tmp_path):
    data = json.loads(run(str(sb_env), "--json").stdout)
    assert data["out_dir"] == str((tmp_path / "output" / "cafe").resolve())
    data = json.loads(run(str(sb_env), "--out", "other", "--json").stdout)
    assert data["out_dir"] == str((tmp_path / "other").resolve())


def test_out_option_reads_state_there(sb_env, tmp_path):
    write_state(tmp_path / "other", {"project_id": "other-pid", "scenes": {"t1": {"status": "failed"}}})
    data = json.loads(run(str(sb_env), "--json", "--out", "other").stdout)
    assert data["project_id"] == "other-pid"
    assert data["summary"]["failed"] == 1


def test_text_mode_unchanged(sb_env):
    out, _ = mixed_state(sb_env)
    res = run(str(sb_env))
    assert res.exit_code == 0, res.output
    p = load_storyboard(sb_env).scenes[1].prompt
    expected = "\n".join([
        f"Quán cà phê  ({out})  project=state-pid",
        f"  {'k1':<6} {'keyframe':<8} ✔ xong  Máy quay tiến chậm vào quán",
        f"  {'t1':<6} {'t2v':<8} ✘ lỗi  {p[:60]}",
        f"         {LONG_ERROR[:200]}",
        f"  {'i1':<6} {'i2v':<8} ↻ storyboard đã đổi -> sẽ tạo lại  Ảnh chuyển động nhẹ",
        f"  {'t2':<6} {'t2v':<8} ↻ storyboard đã đổi -> sẽ tạo lại  Sữa đặc hòa vào cà phê",
        f"  {'t3':<6} {'t2v':<8} ⚠ bị ngắt khi render  Phố đông xe máy",
        f"  {'t4':<6} {'t2v':<8} · chưa làm  Lan mỉm cười",
    ]) + "\n"
    assert res.stdout == expected


def test_text_mode_project_from_state_only(sb_env):
    sb_env.write_text('project_id: "from-yaml"\n' + SB_YAML, encoding="utf-8")
    res = run(str(sb_env))
    assert res.stdout.splitlines()[0].endswith("project=-")


def _boom(*_a, **_k):
    raise AssertionError("status --json must not call gflow/Flow")


@pytest.fixture
def no_gflow(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(Gflow, "run_json", _boom)
    monkeypatch.setattr(Pipeline, "ensure_project", _boom)


def test_json_writes_nothing_existing_state(sb_env, no_gflow):
    out, _ = mixed_state(sb_env)
    state = out / "state.json"
    before = (state.read_bytes(), state.stat().st_mtime_ns)
    files_before = sorted(p.relative_to(out) for p in out.rglob("*"))
    res = run(str(sb_env), "--json")
    assert res.exit_code == 0, res.output
    assert (state.read_bytes(), state.stat().st_mtime_ns) == before
    assert sorted(p.relative_to(out) for p in out.rglob("*")) == files_before


def test_json_writes_nothing_never_run(sb_env, tmp_path, no_gflow):
    res = run(str(sb_env), "--json")
    assert res.exit_code == 0, res.output
    assert not (tmp_path / "output").exists()


def test_help_lists_json():
    res = CliRunner().invoke(main, ["status", "--help"])
    assert res.exit_code == 0
    assert "--json" in res.output
    assert "In tiến độ dạng JSON" in res.output


def _click_at_least_8_2() -> bool:
    major, minor = (int(x) for x in version("click").split(".")[:2])
    return (major, minor) >= (8, 2)


@pytest.mark.parametrize("case", ["missing", "bad_yaml", "no_scenes", "dup_ids"])
def test_bad_storyboard_json(tmp_path, monkeypatch, case):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "bad.yaml"
    if case == "missing":
        if not _click_at_least_8_2():
            pytest.skip("click < 8.2 mixes stderr into CliRunner stdout")
    elif case == "bad_yaml":
        p.write_text("title: [oops\nscenes: {", encoding="utf-8")
    elif case == "no_scenes":
        p.write_text('title: "x"\nscenes: []\n', encoding="utf-8")
    else:
        p.write_text('title: "x"\nscenes:\n  - {id: a, mode: t2v, prompt: p}\n  - {id: a, mode: t2v, prompt: q}\n',
                     encoding="utf-8")
    res = run(str(p), "--json")
    assert res.exit_code != 0
    if case == "missing":
        assert res.exit_code == 2
    assert res.stdout == ""
