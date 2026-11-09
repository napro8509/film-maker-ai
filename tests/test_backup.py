"""Backup / restore with rclone. A local folder stands in for Google Drive (no network)."""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from filmmaker.backup import (LOCAL_RECORD, MANIFEST, AutoBackup, BackupError, Rclone, check_name, check_remote,
                              collect, rewrite_paths)

needs_rclone = pytest.mark.skipif(not shutil.which("rclone"), reason="needs rclone")

SB = """
title: "Phim thử"
characters:
  - name: Lan
    description: "cô gái"
    image: assets/characters/lan.png
scenes:
  - id: s01
    mode: t2v
    prompt: "Lan cười"
  - id: s02
    mode: i2v
    image: start.png
    prompt: "chuyển động"
"""


def make_ui_project(ws: Path, name: str = "phim-thu") -> Path:
    d = ws / name
    (d / "assets" / "characters").mkdir(parents=True)
    (d / "storyboard.yaml").write_text(SB, encoding="utf-8")
    (d / "assets" / "characters" / "lan.png").write_bytes(b"png")
    (d / "start.png").write_bytes(b"start")
    (d / "ui.json").write_text("{}")
    (d / ".DS_Store").write_text("junk")
    out = d / "output" / "storyboard"
    (out / "clips").mkdir(parents=True)
    (out / "clips" / "s01.mp4").write_bytes(b"v1" * 1000)
    (out / "state.json.tmp").write_text("half-written")
    (out / "state.json").write_text(json.dumps({"project_id": "p1", "scenes": {"s01": {
        "status": "done", "video": str((out / "clips" / "s01.mp4").resolve()), "fingerprint": "x"}}}))
    return d


def test_collect_whole_ui_folder(tmp_path):
    d = make_ui_project(tmp_path)
    pf = collect(d / "storyboard.yaml", d / "output" / "storyboard")
    assert pf.name == "phim-thu" and pf.whole_folder
    assert pf.files == ["assets/characters/lan.png", "output/storyboard/clips/s01.mp4", "output/storyboard/state.json",
                        "start.png", "storyboard.yaml", "ui.json"]
    assert pf.out_dir == "output/storyboard"


def test_collect_cli_storyboard_only_takes_its_own_files(tmp_path):
    (tmp_path / "assets" / "characters").mkdir(parents=True)
    (tmp_path / "assets" / "characters" / "lan.png").write_bytes(b"png")
    (tmp_path / "start.png").write_bytes(b"s")
    (tmp_path / "other.yaml").write_text("title: x\nscenes: []\n")
    (tmp_path / "cafe.yaml").write_text(SB, encoding="utf-8")
    (tmp_path / "output" / "cafe" / "clips").mkdir(parents=True)
    (tmp_path / "output" / "cafe" / "clips" / "s01.mp4").write_bytes(b"v")
    (tmp_path / "output" / "other").mkdir(parents=True)
    (tmp_path / "output" / "other" / "x.mp4").write_bytes(b"v")
    pf = collect(tmp_path / "cafe.yaml", tmp_path / "output" / "cafe")
    assert pf.name == "cafe" and not pf.whole_folder
    assert pf.files == ["assets/characters/lan.png", "cafe.yaml", "output/cafe/clips/s01.mp4", "start.png"]


def test_collect_rejects_output_outside_project(tmp_path):
    d = make_ui_project(tmp_path / "ws")
    with pytest.raises(BackupError, match="phải nằm trong"):
        collect(d / "storyboard.yaml", tmp_path / "elsewhere")


def test_rewrite_paths():
    data = {"scenes": {"s01": {"video": "/old/p/output/clips/s01.mp4", "other": "/old/pX/a", "n": 3}}, "l": ["/old/p/a"]}
    assert rewrite_paths(data, "/old/p", "/new/q") == {
        "scenes": {"s01": {"video": "/new/q/output/clips/s01.mp4", "other": "/old/pX/a", "n": 3}}, "l": ["/new/q/a"]}


def test_names_and_remotes_are_checked():
    for bad in ("", ".versions", "../x", "a/b"):
        with pytest.raises(BackupError):
            check_name(bad)
    for bad in ("", "  ", "--config=/x"):
        with pytest.raises(BackupError):
            check_remote(bad)
    assert check_remote(" gdrive:FilmMaker ") == "gdrive:FilmMaker"


def test_missing_remote_is_explained():
    msg = Rclone("gdrive:FilmMaker")._explain('CRITICAL: Failed to create file system: didn\'t find section in config file ("gdrive")')
    assert "rclone config" in msg and "gdrive" in msg


def test_missing_rclone_binary(tmp_path):
    r = Rclone(str(tmp_path / "remote"), binary="definitely-not-rclone-xyz")
    assert not r.available()
    with pytest.raises(BackupError, match="brew install rclone"):
        r.list_backups()


def test_auto_backup_merges_requests_and_survives_errors():
    calls, gate = [], threading.Event()
    logs: list[str] = []

    def run():
        calls.append(1)
        gate.wait(2)
        if len(calls) == 1:
            raise BackupError("mạng lỗi")

    ab = AutoBackup(run, log=logs.append)
    ab.trigger()
    time.sleep(0.05)
    for _ in range(5):  # all arrive while the first backup is running -> one more run
        ab.trigger()
    gate.set()
    ab.finish()
    assert 2 <= len(calls) <= 3
    assert ab.errors == 1 and any("mạng lỗi" in l for l in logs)


@needs_rclone
def test_backup_restore_roundtrip_with_versions(tmp_path):
    ws = tmp_path / "ws"
    d = make_ui_project(ws)
    out = d / "output" / "storyboard"
    remote = str(tmp_path / "drive" / "FilmMaker")
    logs: list[str] = []
    r = Rclone(remote, log=logs.append)

    rec = r.backup(d / "storyboard.yaml", out)
    assert rec["files"] == 6
    assert (out / LOCAL_RECORD).is_file()
    cloud = Path(remote) / "phim-thu"
    assert (cloud / "output/storyboard/clips/s01.mp4").read_bytes() == b"v1" * 1000
    assert not (cloud / ".DS_Store").exists() and not (cloud / "output/storyboard/state.json.tmp").exists()
    assert json.loads((cloud / MANIFEST).read_text())["root"] == str(d.resolve())
    assert any("s01.mp4" in l for l in logs)

    # re-render: the new clip replaces the old one, which is kept in .versions
    time.sleep(1.1)
    (out / "clips" / "s01.mp4").write_bytes(b"v2" * 1000)
    r.backup(d / "storyboard.yaml", out)
    assert (cloud / "output/storyboard/clips/s01.mp4").read_bytes() == b"v2" * 1000
    old = list((Path(remote) / ".versions" / "phim-thu").glob("*/output/storyboard/clips/s01.mp4"))
    assert len(old) == 1 and old[0].read_bytes() == b"v1" * 1000

    assert r.list_backups() == ["phim-thu"]

    # restore on "another machine" (different folder): paths in state.json follow
    target = tmp_path / "ws2" / "phim-thu"
    m = r.restore("phim-thu", target)
    assert m["name"] == "phim-thu"
    st = json.loads((target / "output/storyboard/state.json").read_text())
    video = Path(st["scenes"]["s01"]["video"])
    assert video == (target / "output/storyboard/clips/s01.mp4").resolve() and video.read_bytes() == b"v2" * 1000
    assert not (target / MANIFEST).exists()

    with pytest.raises(BackupError, match="đã có"):
        r.restore("phim-thu", target)
    with pytest.raises(BackupError):
        r.restore("khong-co", tmp_path / "x")


@needs_rclone
def test_restored_project_is_seen_as_done(tmp_path):
    """The point of fixing paths: a restored clip must not be rendered (paid) again."""
    from filmmaker.gflow import Gflow
    from filmmaker.pipeline import Pipeline
    from filmmaker.storyboard import load_storyboard

    d = make_ui_project(tmp_path / "ws")
    sb = load_storyboard(d / "storyboard.yaml")
    pipe = Pipeline(sb, d / "output" / "storyboard", Gflow(dry_run=True, log=lambda *_: None))
    pipe.state.update("s01", status="done", fingerprint=sb.scene_fingerprint(sb.scenes[0]),
                      video=str(pipe.clip_path(sb.scenes[0]).resolve()))
    r = Rclone(str(tmp_path / "drive"), log=lambda *_: None)
    r.backup(d / "storyboard.yaml", d / "output" / "storyboard")
    shutil.rmtree(d)  # the laptop is gone

    target = tmp_path / "new-mac" / "phim-thu"
    r.restore("phim-thu", target)
    sb2 = load_storyboard(target / "storyboard.yaml")
    pipe2 = Pipeline(sb2, target / "output" / "storyboard", Gflow(dry_run=True, log=lambda *_: None))
    assert pipe2.is_done(sb2.scenes[0])


@needs_rclone
def test_cli_run_with_auto_backup_then_restore(tmp_path, monkeypatch):
    import sys

    from click.testing import CliRunner

    from filmmaker.cli import main

    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {Path(__file__).parent / 'fake_gflow.py'}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "calls.log"))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)
    remote = str(tmp_path / "drive" / "FilmMaker")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"backup_remote": remote, "auto_backup": True}))
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(settings))
    proj = tmp_path / "proj"
    proj.mkdir()
    sb = proj / "cafe.yaml"
    sb.write_text('title: "Cà phê"\nscenes:\n  - {id: s01, mode: t2v, prompt: "coffee"}\n'
                  '  - {id: s02, mode: t2v, prompt: "milk"}\n', encoding="utf-8")

    res = CliRunner().invoke(main, ["run", str(sb), "--pause", "0"])
    assert res.exit_code == 0, res.output
    assert "Đã backup xong" in res.output
    cloud = Path(remote) / "cafe"
    assert (cloud / "output/cafe/clips/s01.mp4").is_file() and (cloud / "output/cafe/clips/s02.mp4").is_file()

    res = CliRunner().invoke(main, ["run", str(sb), "--pause", "0", "--no-backup", "--force", "s01"])
    assert res.exit_code == 0 and "☁" not in res.output

    res = CliRunner().invoke(main, ["backup", "--list"])
    assert res.output.split() == ["cafe"]

    target = tmp_path / "other"
    res = CliRunner().invoke(main, ["restore", "cafe", "--to", str(target)])
    assert res.exit_code == 0, res.output
    calls_before = (tmp_path / "calls.log").read_text().count("\n")
    res = CliRunner().invoke(main, ["run", str(target / "cafe.yaml"), "--pause", "0", "--no-backup"])
    assert res.exit_code == 0 and "Bỏ qua (đã có): 2" in res.output
    assert (tmp_path / "calls.log").read_text().count("\n") == calls_before  # nothing sent to Flow


def test_cli_backup_explains_missing_rclone(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from filmmaker.cli import main

    monkeypatch.setenv("RCLONE_BIN", "definitely-not-rclone-xyz")
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(tmp_path / "none.json"))
    d = make_ui_project(tmp_path)
    res = CliRunner().invoke(main, ["backup", str(d / "storyboard.yaml")])
    assert res.exit_code != 0 and "brew install rclone" in res.output


@needs_rclone
def test_restore_project_that_was_never_rendered(tmp_path):
    d = tmp_path / "ws" / "moi"
    d.mkdir(parents=True)
    (d / "storyboard.yaml").write_text(SB, encoding="utf-8")
    r = Rclone(str(tmp_path / "drive"), log=lambda *_: None)
    r.backup(d / "storyboard.yaml", d / "output" / "storyboard")
    target = tmp_path / "ws2" / "moi"
    r.restore("moi", target)
    assert (target / "storyboard.yaml").is_file() and (target / "output/storyboard" / LOCAL_RECORD).is_file()
