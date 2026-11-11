"""scene-continuity T3: `filmmaker locations` (fake gflow; AC12, AC28 via the CLI, AC29)."""
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from filmmaker.cli import main
from filmmaker.gflow import Gflow
from filmmaker.pipeline import Pipeline
from filmmaker.storyboard import load_storyboard

FAKE = Path(__file__).parent / "fake_gflow.py"
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


@pytest.fixture
def env(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(log))
    for var in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_CATALOG", "FAKE_GFLOW_NO_AUDIO", "FLOW_UI_CMD"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "assets" / "locations").mkdir(parents=True)
    (tmp_path / "assets" / "locations" / "old.png").write_bytes(b"old")

    def calls():
        lines = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return [json.loads(l) for l in lines]

    return calls


def write(tmp_path: Path, locations: list[dict], scenes: list[dict] | None = None) -> Path:
    data = {"title": "Loc", "aspect": "9:16", "model": "veo-lite", "image_model": "nano2", "style": "film",
            "locations": locations,
            "scenes": scenes or [{"id": "s01", "mode": "keyframe", "prompt": "a room"}]}
    p = tmp_path / "sb.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


LOCS = [{"name": "Phòng khách", "description": "teak table"},
        {"name": "Bếp", "description": "small kitchen", "image": "assets/locations/old.png"},
        {"name": "Sân", "description": "", "image_prompt": "An empty yard at dusk"}]


def run(p: Path, *args: str):
    return CliRunner().invoke(main, ["locations", str(p), "--pause", "0", *args])


def t2i_outs(calls) -> list[str]:
    assert not any(c[0] == "video" for c in calls)  # AC29
    return [Path(c[c.index("-o") + 1]).name for c in calls if c[:2] == ["image", "t2i"]]


def test_locations_cmd_only_missing(env, tmp_path):
    p = write(tmp_path, LOCS)
    res = run(p)
    assert res.exit_code == 0, res.output
    assert t2i_outs(env()) == ["phong-khach.png", "san.png"]
    raw = yaml.safe_load(p.read_text())
    assert [loc.get("image") for loc in raw["locations"]] == [
        "assets/locations/phong-khach.png", "assets/locations/old.png", "assets/locations/san.png"]
    assert (tmp_path / "assets" / "locations" / "phong-khach.png").is_file()
    assert "Xong: 2 ảnh bối cảnh" in res.output
    assert "[Phòng khách] ảnh bối cảnh: Location reference image: teak table." in res.output
    assert "[Sân] ảnh bối cảnh: An empty yard at dusk. Style: film." in res.output
    # everything has a picture now
    res = run(p)
    assert res.exit_code == 0 and "Tất cả bối cảnh đã có ảnh" in res.output
    assert env() == []


def test_locations_cmd_name_and_force(env, tmp_path):
    p = write(tmp_path, LOCS)
    res = run(p, "--name", "Bếp")
    assert res.exit_code == 0, res.output
    assert t2i_outs(env()) == ["bep.png"]
    assert yaml.safe_load(p.read_text())["locations"][1]["image"] == "assets/locations/bep.png"
    res = run(p, "--force")
    assert res.exit_code == 0, res.output
    assert t2i_outs(env()) == ["phong-khach.png", "bep.png", "san.png"]


def test_locations_cmd_unknown_name(env, tmp_path):
    res = run(write(tmp_path, LOCS), "--name", "Nhà kho")
    assert res.exit_code != 0 and "không có bối cảnh: Nhà kho" in res.output
    assert env() == []


def test_locations_cmd_empty_description(env, tmp_path):
    res = run(write(tmp_path, [{"name": "Phòng khách", "description": "  "}]))
    assert res.exit_code != 0
    assert "bối cảnh #1 cần có name và description (hoặc image_prompt)" in res.output
    assert env() == []


def test_locations_cmd_duplicate_slug(env, tmp_path):
    p = write(tmp_path, [{"name": "Phòng khách", "description": "a"}, {"name": "phong khach", "description": "b"},
                         {"name": "!!!", "description": "c"}])
    res = run(p)
    assert res.exit_code == 0, res.output
    assert t2i_outs(env()) == ["phong-khach.png", "phong-khach-2.png", "boi-canh-3.png"]


def test_locations_cmd_dry_run_writes_nothing(env, tmp_path):
    p = write(tmp_path, LOCS)
    before = p.read_text()
    res = run(p, "--dry-run")
    assert res.exit_code == 0, res.output
    assert " image t2i " in res.output
    assert p.read_text() == before and env() == []


def test_first_location_image_marks_changed(env, tmp_path):
    """AC12 / AC28: the scenes that were done and use the location are listed, and become "changed" after the
    first picture of that location; the others stay done."""
    scenes = [{"id": "s01", "mode": "keyframe", "prompt": "argue", "location": "Phòng khách"},
              {"id": "s02", "mode": "keyframe", "prompt": "cook", "location": "Bếp"},
              {"id": "s03", "mode": "keyframe", "prompt": "street"},
              {"id": "s04", "mode": "i2v", "image": "assets/locations/old.png", "prompt": "x",
               "location": "Phòng khách"}]
    p = write(tmp_path, LOCS, scenes)
    sb = load_storyboard(p)
    out = tmp_path / "output" / "sb"
    state = {"project_id": "proj-123", "scenes": {}}
    for s in sb.scenes:
        clip = out / "clips" / f"{s.id}.mp4"
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.write_bytes(b"clip")
        state["scenes"][s.id] = {"status": "done", "video": str(clip), "fingerprint": sb.scene_fingerprint(s)}
    (out / "state.json").write_text(json.dumps(state))
    res = run(p, "--dry-run")
    assert "⚠ Các cảnh đã có clip sẽ chuyển “Đã đổi” sau khi tạo ảnh bối cảnh: s01 (render lại video tốn credit; " \
           "keyframe tạo lại miễn phí)" in res.output
    res = run(p)
    assert res.exit_code == 0, res.output
    assert "sau khi tạo ảnh bối cảnh: s01 " in res.output
    sb = load_storyboard(p)
    pipe = Pipeline(sb, out, Gflow(dry_run=True, log=lambda *_: None), log=lambda *_: None)
    assert {s.id: pipe.scene_status(s) for s in sb.scenes} == {"s01": "changed", "s02": "done", "s03": "done",
                                                               "s04": "done"}
