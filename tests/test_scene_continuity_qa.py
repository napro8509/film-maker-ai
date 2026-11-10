"""QA additions for scene-continuity: the scene "Bối cảnh" picker rendered under node with a fake h().

renderSceneLocation is cut out of app.js (same _func pattern as test_scene_continuity_ui.py) and run with stubs,
so the options the user sees can be checked without a browser."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "filmmaker" / "web" / "static" / "app.js"
SRC = APP.read_text(encoding="utf-8")
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def _func(name: str) -> str:
    m = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}\n", SRC, re.S | re.M)
    assert m, f"function {name} not found"
    return m.group(0)


HARNESS = r"""
const h = (tag, attrs, ...kids) => ({ tag, attrs: attrs || {}, kids: kids.flat(Infinity).filter((k) => k != null && k !== false) });
const field = (label, el, note) => ({ tag: "field", label, note, kids: [el] });
const zone = () => null;
const icon = () => null;
const S = { cur: { scene_state: {} } };
const locationImpact = () => 0;
const confirmModal = async () => true;
const changed = () => {}; const renderMain = () => {};
%FUNCS%
const sb = { locations: [{ name: "Phòng khách", description: "room" }, { name: "Bếp", description: "kitchen" }],
  scenes: [] };
function picker(sc) {
  const root = renderSceneLocation(sb, sc, 0);
  let sel = null;
  const walk = (n) => { if (!n || typeof n !== "object") return; if (n.tag === "select") sel = n; (n.kids || []).forEach(walk); };
  walk(root);
  const opts = sel.kids.filter((k) => k && k.tag === "option").map((o) => o.kids.join(""));
  return { value: sel.attrs.value, disabled: !!sel.attrs.disabled, options: opts, note: root.kids[0].note };
}
console.log(JSON.stringify({
  keyframe: picker({ id: "s01", mode: "keyframe", location: "phòng KHÁCH" }),
  i2v: picker({ id: "s04", mode: "i2v", location: "Phòng khách" }),
  unknown: picker({ id: "s05", mode: "keyframe", location: "Gác mái" }),
}));
"""


@pytest.fixture(scope="module")
def pickers(tmp_path_factory):
    if not shutil.which("node"):
        pytest.skip("needs node")
    funcs = "".join(_func(n) for n in ("nameKey", "locKey", "sceneLoc", "renderSceneLocation"))
    d = tmp_path_factory.mktemp("qa")
    (d / "h.js").write_text(HARNESS.replace("%FUNCS%", funcs), encoding="utf-8")
    res = subprocess.run(["node", str(d / "h.js")], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@needs_node
def test_keyframe_picker_matches_case_insensitively(pickers):
    p = pickers["keyframe"]
    assert p["value"] == "Phòng khách" and not p["disabled"]
    assert p["options"] == ["— Không —", "Phòng khách", "Bếp"]


@needs_node
def test_unknown_location_is_flagged(pickers):
    assert "Gác mái (không có trong danh sách)" in pickers["unknown"]["options"]


@needs_node
def test_i2v_picker_locked_without_false_unknown_option(pickers):
    """D7: an i2v scene shows a locked picker with "Ảnh → Video không dùng bối cảnh". A location that IS in the
    list must not be labelled "(không có trong danh sách)" just because the scene is i2v."""
    p = pickers["i2v"]
    assert p["disabled"] is True
    assert p["note"] == "Ảnh → Video không dùng bối cảnh"
    assert not any("không có trong danh sách" in o for o in p["options"]), p["options"]
