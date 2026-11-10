"""CLI side of bad scene durations: `check`, blocking/warning in `run`/`make`, `round-durations`.

Everything runs in-process (CliRunner) with fake gflow + fake flow_ui; nothing reaches Flow. Settings point to a
tmp file with auto_backup off, and --no-backup is passed anyway.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from filmmaker.cli import main
from tests.duration_helpers import ac12_project, veo_lite_project
from tests.model_helpers import set_scene, stamp, ui_out_dir

FAKE = Path(__file__).parent / "fake_gflow.py"
FAKE_UI = Path(__file__).parent / "fake_flow_ui.py"
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="needs ffmpeg/ffprobe")


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings = tmp_path / "cfg" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"auto_backup": False}))
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(settings))
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "gflow.log"))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)
    monkeypatch.setenv("FLOW_UI_CMD", f"{sys.executable} {FAKE_UI}")
    monkeypatch.setenv("FAKE_FLOW_UI_LOG", str(tmp_path / "ui.log"))
    monkeypatch.delenv("FAKE_FLOW_UI_FAIL", raising=False)

    class E:
        root = tmp_path

        @staticmethod
        def logs() -> str:
            return "".join(p.read_text() for p in (tmp_path / "gflow.log", tmp_path / "ui.log") if p.exists())

    return E


def cli(*args):
    return CliRunner().invoke(main, [str(a) for a in args])


def project(env, scenes) -> Path:
    return veo_lite_project(env.root / "ws", "film", scenes)


def mixed(env) -> Path:
    """s01=5 todo, s02=7 interrupted, s03=6 todo (valid), s04=5 done."""
    p = project(env, [{"id": "s01", "duration": 5}, {"id": "s02", "duration": 7},
                      {"id": "s03", "duration": 6}, {"id": "s04", "duration": 5}])
    set_scene(p, "s02", "interrupted")
    set_scene(p, "s04", "done")
    return p


def state_bytes(sb: Path) -> bytes:
    f = ui_out_dir(sb) / "state.json"
    return f.read_bytes() if f.exists() else b""


S01 = "s01: 5 giây — veo-lite chỉ nhận 4, 6, 8 (lên 6 / xuống 4)"


# --- check ---------------------------------------------------------------------------

def test_check_lists_bad_durations(env):
    sb = mixed(env)
    res = cli("check", sb)
    assert res.exit_code == 1
    assert "✘ 2 cảnh chưa có clip có độ dài model không nhận:" in res.output
    assert f"  {S01}\n" in res.output
    assert "s02: 7 giây — veo-lite chỉ nhận 4, 6, 8 (lên 8 / xuống 6) — bị ngắt" in res.output
    assert "s04:" not in res.output  # done
    assert "round-durations" in res.output and "--up" in res.output
    assert "✔ Không cảnh nào dùng model sắp/đã ngừng" in res.output  # old part kept


def test_check_ok_line(env):
    sb = project(env, [{"id": "s01", "duration": 6}, {"id": "s02"}])
    res = cli("check", sb)
    assert "✔ Độ dài cảnh hợp lệ với model" in res.output
    assert "✘" not in res.output.split("✔ Không cảnh nào")[1]


def test_check_done_bad_duration_not_called_valid(env):
    """code-review 3: a done scene with a bad duration is out of scope, but "hợp lệ" would overstate it."""
    sb = project(env, [{"id": "s01", "duration": 6}, {"id": "s02", "duration": 5}])
    set_scene(sb, "s02", "done")
    for args in (("check", sb), ("round-durations", sb), ("round-durations", sb, "--up")):
        res = cli(*args)
        assert "✔ Độ dài cảnh hợp lệ với model" not in res.output, args
        assert ("✔ Không có cảnh chưa có clip nào có độ dài model không nhận (1 cảnh đã có clip vẫn sai độ dài: s02"
                in res.output), (args, res.output)
    assert res.exit_code == 0


def test_check_only_interrupted_no_hint(env):
    sb = project(env, [{"id": "s01", "duration": 7}])
    set_scene(sb, "s01", "interrupted")
    res = cli("check", sb)
    assert res.exit_code == 1
    assert "bị ngắt" in res.output and "round-durations" not in res.output


# --- run / make ------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", ["run", "make"])
def test_run_make_block_bad_duration(env, cmd):
    sb = mixed(env)
    before = state_bytes(sb)
    res = cli(cmd, sb, "--no-backup", "--retry-unknown", "--pause", "0-0")
    assert res.exit_code == 1, res.output
    assert "2 cảnh có độ dài model không nhận, không gửi gì lên Flow:" in res.output
    assert S01 in res.output
    assert "s02: 7 giây" in res.output and "bị ngắt — không tự làm tròn" in res.output
    assert "s04" not in res.output  # done, not forced
    assert "filmmaker round-durations" in res.output
    assert env.logs() == ""
    assert state_bytes(sb) == before
    out = ui_out_dir(sb)
    assert not (out / "keyframes").exists() and not (out / "clips").exists()  # stopped before pipe.run
    assert json.loads(state_bytes(sb)).get("project_id") is None


def test_force_done_bad_duration_blocked_with_note(env):
    sb = mixed(env)
    res = cli("run", sb, "--no-backup", "--only", "s04", "--force", "s04")
    assert res.exit_code == 1
    assert "s04: 5 giây" in res.output and "đã có clip — sửa tay `duration:`" in res.output
    assert "round-durations" not in res.output  # nothing roundable in the send set
    assert env.logs() == ""


@pytest.mark.parametrize("flag", ["--dry-run", "--keyframes-only"])
def test_dry_run_and_keyframes_only_warn(env, flag, monkeypatch):
    if flag == "--keyframes-only" and not shutil.which("ffmpeg"):
        pytest.skip("needs ffmpeg")
    sb = mixed(env)
    res = cli("run", sb, "--no-backup", "--pause", "0-0", flag)
    assert "⚠ Độ dài model không nhận:" in res.output
    assert S01 in res.output
    assert "không gửi gì lên Flow:" not in res.output
    # Ran on to the end-of-run summary (the exit code then follows the report, e.g. s02 interrupted is skipped).
    assert "cảnh sẽ được tạo" in res.output or "Xong: " in res.output, res.output
    assert res.exception is None or isinstance(res.exception, SystemExit), res.output
    assert env.logs() == ""


@needs_ffmpeg
def test_only_valid_scene_runs(env):
    sb = mixed(env)
    res = cli("run", sb, "--no-backup", "--pause", "0-0", "--only", "s03")
    assert "độ dài model không nhận" not in res.output
    assert res.exit_code == 0, res.output


def test_interrupted_without_retry_not_checked(env):
    sb = project(env, [{"id": "s01", "duration": 7}])
    set_scene(sb, "s01", "interrupted")
    res = cli("run", sb, "--no-backup", "--pause", "0-0")
    assert "độ dài model không nhận" not in res.output
    assert env.logs() == ""


# --- round-durations ---------------------------------------------------------------------

def test_round_durations_list_exit(env):
    sb = mixed(env)
    before = sb.read_bytes()
    res = cli("round-durations", sb)
    assert res.exit_code == 1
    lines = res.output.splitlines()
    assert lines[0] == S01
    assert lines[1].startswith("s02: 7 giây") and "bị ngắt" in lines[1]
    assert "Chạy lại với --up hoặc --down để sửa 1 cảnh." in res.output
    assert sb.read_bytes() == before


def test_round_durations_list_ok(env):
    sb = project(env, [{"id": "s01", "duration": 4}])
    res = cli("round-durations", sb)
    assert res.exit_code == 0 and "✔" in res.output


@pytest.mark.parametrize("flag,to", [("--up", 6), ("--down", 4)])
def test_round_writes_only_duration(env, flag, to):
    sb = mixed(env)
    raw = yaml.safe_load(sb.read_text())
    res = cli("round-durations", sb, flag)
    assert f"s01: 5 → {to} giây" in res.output
    assert "⚠ s02: 7 giây — bị ngắt, không tự làm tròn (kiểm tra Flow / tải clip về / sửa tay)" in res.output
    assert res.exit_code == 1  # s02 still needs manual work
    after = yaml.safe_load(sb.read_text())
    raw["scenes"][0]["duration"] = to
    assert after == raw  # s02 (interrupted) and s04 (done) untouched
    assert env.logs() == ""


def test_round_all_fixed_exit0(env):
    sb = project(env, [{"id": "s01", "duration": 9}, {"id": "s02", "duration": 3}])
    res = cli("round-durations", sb, "--up")
    assert res.exit_code == 0, res.output
    assert [s["duration"] for s in yaml.safe_load(sb.read_text())["scenes"]] == [8, 4]


def test_up_and_down_usage_error(env):
    sb = mixed(env)
    res = cli("round-durations", sb, "--up", "--down")
    assert res.exit_code == 2


def test_round_broken_storyboard_click_error(env):
    sb = env.root / "bad.yaml"
    sb.write_text("title: x\nscenes:\n  - {id: 'bad id', prompt: p}\n")
    res = cli("round-durations", sb, "--up")
    assert res.exit_code == 1
    assert "Storyboard còn lỗi, chưa làm tròn được" in res.output
    assert "Traceback" not in res.output
    assert isinstance(res.exception, SystemExit)


def test_round_write_error_is_click_error(env, monkeypatch):
    """code-review 5: an OSError while writing is a short message (no traceback), the file is not changed and no
    temp file is left behind."""
    import os
    sb = project(env, [{"id": "s01", "duration": 5}])
    before = sb.read_bytes()

    def boom(src, dst):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "replace", boom)
    res = cli("round-durations", sb, "--up")
    assert res.exit_code == 1 and isinstance(res.exception, SystemExit)
    assert f"Không ghi được {sb}: Permission denied (file chưa bị đổi)" in res.output
    assert "Traceback" not in res.output
    assert sb.read_bytes() == before
    assert not [x.name for x in sb.parent.iterdir() if x.name.endswith(".tmp")]


def test_rewrite_file_temp_name_is_fresh_and_mode_kept(env, tmp_path):
    """security 3: never a fixed "<name>.tmp" (a planted symlink there is left alone); file mode is kept."""
    from filmmaker import durations as dur
    sb = project(env, [{"id": "s01", "duration": 5}])
    sb.chmod(0o640)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    planted = sb.with_name(sb.name + ".tmp")
    planted.symlink_to(victim)
    dur.rewrite_file(sb, {"s01": 6})
    assert yaml.safe_load(sb.read_text())["scenes"][0]["duration"] == 6
    assert victim.read_text() == "keep" and planted.is_symlink()
    assert sb.stat().st_mode & 0o777 == 0o640
    assert [x.name for x in sb.parent.iterdir() if x.name.endswith(".tmp")] == [planted.name]


def test_round_up_nothing_keeps_file(env):
    import os
    sb = project(env, [{"id": "s01", "duration": 7}, {"id": "s02", "duration": 6}])
    set_scene(sb, "s01", "interrupted")
    os.utime(sb, (1_000_000_000, 1_000_000_000))
    before = sb.read_bytes()
    res = cli("round-durations", sb, "--up")
    assert res.exit_code == 1
    assert "⚠ s01: 7 giây — bị ngắt" in res.output
    assert sb.read_bytes() == before and sb.stat().st_mtime == 1_000_000_000
    assert env.logs() == ""


# --- AC5: adopted clips are not checked ------------------------------------------------------

def test_auto_adopt_bad_duration_not_checked(env, fake_clip_check):
    """Interrupted s01 (7 s) with a valid hand-downloaded clip newer than submitted_at: adopted, never blocked,
    even with --retry-unknown (which would otherwise send it)."""
    sb = project(env, [{"id": "s01", "duration": 7}])
    set_scene(sb, "s01", "interrupted", submitted_at=stamp(-60), manual_clip=True)
    res = cli("run", sb, "--no-backup", "--retry-unknown", "--pause", "0-0")
    assert "độ dài model không nhận" not in res.output, res.output
    assert env.logs() == ""
    st = json.loads(state_bytes(sb))["scenes"]["s01"]
    assert st["status"] == "done" and st.get("rendered_as") == "manual"


def test_adopt_flag_bad_duration_not_checked(env, fake_clip_check):
    """`--adopt s01` on a legacy interrupted scene (no submitted_at) with a valid clip: taken, never checked."""
    sb = project(env, [{"id": "s01", "duration": 7}, {"id": "s02", "duration": 6}])
    set_scene(sb, "s01", "interrupted", manual_clip=True)
    set_scene(sb, "s02", "done")
    res = cli("run", sb, "--no-backup", "--adopt", "s01", "--retry-unknown", "--pause", "0-0")
    assert "độ dài model không nhận" not in res.output, res.output
    assert env.logs() == ""
    assert json.loads(state_bytes(sb))["scenes"]["s01"]["status"] == "done"


def test_failed_adopt_bad_duration_not_checked(env, fake_clip_check):
    """`--adopt` whose clip is broken: the scene is skipped for this run (not sent), so not checked either."""
    sb = project(env, [{"id": "s01", "duration": 7}])
    set_scene(sb, "s01", "interrupted", manual_clip=True, clip_bytes=b"BROKEN" + b"\0" * 20_000)
    before = state_bytes(sb)
    res = cli("run", sb, "--no-backup", "--adopt", "s01", "--retry-unknown", "--pause", "0-0")
    assert "--adopt s01: không dùng được clip" in res.output
    assert "độ dài model không nhận" not in res.output, res.output
    # (the run still opens the Flow project before its per-scene `refuse` skips s01; nothing is rendered)
    assert not any(w in env.logs() for w in ('"video"', '"image"', "i2v", "t2v"))
    assert json.loads(state_bytes(sb))["scenes"]["s01"] == json.loads(before)["scenes"]["s01"]


def test_adopt_does_not_hide_other_bad_scene(env, fake_clip_check):
    sb = project(env, [{"id": "s01", "duration": 7}, {"id": "s02", "duration": 5}])
    set_scene(sb, "s01", "interrupted", submitted_at=stamp(-60), manual_clip=True)
    res = cli("run", sb, "--no-backup", "--retry-unknown", "--pause", "0-0")
    assert res.exit_code == 1
    assert "1 cảnh có độ dài model không nhận" in res.output and "s02: 5 giây" in res.output
    assert "s01:" not in res.output
    assert env.logs() == ""


def test_retry_unknown_interrupted_bad_blocked(env):
    sb = project(env, [{"id": "s01", "duration": 7}])
    set_scene(sb, "s01", "interrupted", submitted_at=stamp(-60))
    before = state_bytes(sb)
    res = cli("run", sb, "--no-backup", "--retry-unknown", "--pause", "0-0")
    assert res.exit_code == 1
    assert "bị ngắt — không tự làm tròn" in res.output and "round-durations" not in res.output
    assert env.logs() == "" and state_bytes(sb) == before


# --- AC6 / AC17 with --out ---------------------------------------------------------------------

def test_check_and_round_use_out_dir(env):
    """The state that says s01 is interrupted lives in a custom --out folder."""
    sb = project(env, [{"id": "s01", "duration": 7}, {"id": "s02", "duration": 5}])
    out = env.root / "elsewhere"
    set_scene(sb, "s01", "interrupted", out=out)
    res = cli("check", sb, "--out", out)
    assert res.exit_code == 1
    assert "s01: 7 giây — veo-lite chỉ nhận 4, 6, 8 (lên 8 / xuống 6) — bị ngắt" in res.output
    assert f"--out {out}" in res.output
    res = cli("round-durations", sb, "--out", out, "--up")
    assert "s02: 5 → 6 giây" in res.output and "⚠ s01: 7 giây — bị ngắt" in res.output
    assert res.exit_code == 1
    assert [s["duration"] for s in yaml.safe_load(sb.read_text())["scenes"]] == [7, 6]
    # Without --out the default folder has no state: s01 is todo there and gets rounded too.
    res = cli("round-durations", sb, "--up")
    assert res.exit_code == 0 and "s01: 7 → 8 giây" in res.output


# --- AC15 / AC17 on the AC12 fixture -------------------------------------------------------------

def test_round_up_fixture_ac12_keeps_state(env):
    sb = ac12_project(env.root / "ws")
    raw = yaml.safe_load(sb.read_text())
    before = state_bytes(sb)
    res = cli("round-durations", sb, "--up")
    assert res.exit_code == 1  # s05 interrupted still needs manual work
    assert res.output.splitlines()[:2] == ["s01: 5 → 6 giây", "s02: 7 → 8 giây"]
    assert "⚠ s05: 7 giây — bị ngắt" in res.output and "s04" not in res.output
    raw["scenes"][0]["duration"], raw["scenes"][1]["duration"] = 6, 8
    assert yaml.safe_load(sb.read_text()) == raw
    assert state_bytes(sb) == before
    assert env.logs() == ""  # round-durations never calls gflow
    res = cli("check", sb)
    assert "✘ 1 cảnh chưa có clip" in res.output and "s05: 7 giây" in res.output

