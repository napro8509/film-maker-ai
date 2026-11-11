"""QA round 2 for scene-continuity: YAML key order written by the CLI, the CLI keeping a hand-written order,
and the scene image picker of the Cảnh tab (fake gflow only, tmp_path, no Flow)."""
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from filmmaker.cli import main
from filmmaker.storyboard import load_storyboard, save_storyboard
from tests.test_scene_continuity_ui import _func

FAKE = Path(__file__).parent / "fake_gflow.py"


def _keys(path: Path) -> list[str]:
    return list(yaml.safe_load(path.read_text(encoding="utf-8")))


def _sb(tmp_path: Path, order: str) -> Path:
    head = {"title": "Order", "style": "film",
            "characters": [{"name": "Minh", "description": "young man"}]}
    locs = {"locations": [{"name": "Phòng khách", "description": "teak table"}]}
    scenes = {"scenes": [{"id": "s01", "mode": "keyframe", "prompt": "Minh sits", "location": "phòng khách"}]}
    data = {**head, **scenes, **locs} if order == "after-scenes" else {**head, **locs, **scenes}
    p = tmp_path / "sb.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def test_save_storyboard_writes_locations_right_after_characters(tmp_path):  # code-review 1
    p = _sb(tmp_path, "after-scenes")
    out = tmp_path / "saved.yaml"
    save_storyboard(load_storyboard(p), out)
    keys = _keys(out)
    assert keys.index("locations") == keys.index("characters") + 1
    assert keys.index("locations") < keys.index("scenes")


def test_save_storyboard_old_film_has_no_location_keys(tmp_path):  # AC3 through the writer used by `plan`
    p = tmp_path / "old.yaml"
    p.write_text(yaml.safe_dump({"title": "Old", "scenes": [{"id": "s01", "mode": "t2v", "prompt": "x"}]}),
                 encoding="utf-8")
    out = tmp_path / "saved.yaml"
    save_storyboard(load_storyboard(p), out)
    text = out.read_text(encoding="utf-8")
    for key in ("locations", "location:", "continuity", "continue_previous"):
        assert key not in text


@pytest.mark.parametrize("order", ["after-scenes", "before-scenes"])
def test_locations_cmd_keeps_hand_written_key_order(tmp_path, monkeypatch, order):
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(tmp_path / "calls.log"))
    for var in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_CATALOG"):
        monkeypatch.delenv(var, raising=False)
    p = _sb(tmp_path, order)
    before = _keys(p)
    r = CliRunner().invoke(main, ["locations", str(p), "--pause", "0-0"])
    assert r.exit_code == 0, r.output
    assert _keys(p) == before  # only the image path is added, nothing is reordered
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert data["locations"][0]["image"] == "assets/locations/phong-khach.png"
    assert "video" not in (tmp_path / "calls.log").read_text()
    # second run: nothing to do, file unchanged
    snapshot = p.read_text(encoding="utf-8")
    r2 = CliRunner().invoke(main, ["locations", str(p), "--pause", "0-0"])
    assert r2.exit_code == 0 and "Tất cả bối cảnh đã có ảnh" in r2.output
    assert p.read_text(encoding="utf-8") == snapshot


def test_scene_image_picker_leaves_out_location_images():  # code-review 8, Cảnh tab
    body = _func("imagePicker")
    assert "S.assets.images.map" not in body and "otherImageAssets()" in body
