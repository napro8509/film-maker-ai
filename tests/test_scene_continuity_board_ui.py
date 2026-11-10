"""Board side of scene-continuity (AC36): linkKind / bSceneCharMsgs read the per-scene character picture limit
(charRefLimit from app.js: 2 when the scene has a location, else 3). Real code cut out of board.js / app.js and
run under node with tiny stubs; skipped without node."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "filmmaker" / "web" / "static"
BOARD = STATIC / "board.js"
APP = STATIC / "app.js"
BSRC = BOARD.read_text(encoding="utf-8")
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def _func(name: str, src: str = BSRC) -> str:
    m = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}\n", src, re.S | re.M)
    assert m, f"function {name} not found"
    return m.group(0)


@needs_node
def test_board_js_parses():
    res = subprocess.run(["node", "--check", str(BOARD)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_static_limits():
    lk = _func("linkKind")
    assert lk.index('"đang tắt gửi ảnh nhân vật"') < lk.index("charRefLimit(")  # refs off still wins
    assert "đã đủ ${lim} ảnh nhân vật: ${refs.join(\", \")}" in lk
    assert ">= 3" not in lk
    msgs = _func("bSceneCharMsgs")
    assert "const lim = charRefLimit(sc);" in msgs and "withImg.length > lim" in msgs
    assert "Chỉ gửi ảnh của ${lim} nhân vật: ${refs.join(\", \")}" in msgs
    # loc: progress keys never reach the board's char-only sizing
    assert 'function bCardW(key) { return key.startsWith("char:")' in BSRC
    assert 'function bFallbackH(key) { return key.startsWith("char:")' in BSRC


HARNESS = r"""
const fs = require("fs");
const bsrc = fs.readFileSync(process.argv[2], "utf8"), asrc = fs.readFileSync(process.argv[3], "utf8");
const cut = (src, a, b) => { const i = src.indexOf(a), j = src.indexOf(b, i); if (i < 0 || j < 0) throw new Error(a); return src.slice(i, j); };
const h = (tag, attrs, ...kids) => ({ tag, cls: attrs && attrs.class, text: kids.flat(Infinity).filter((k) => typeof k === "string").join("") });
const icon = () => null;
const S = { cur: null, charSaved: new WeakMap() };
const charImageUrl = (c) => (c && c.image ? "u/" + c.image : null);
eval(cut(bsrc, "const bKey = ", "\n") + ";\n"
     + cut(bsrc, "function bChars()", "function bMode(") + cut(bsrc, "function bCharIndex(", "function bModeLabel(")
     + cut(bsrc, "function sceneLinks(", "function bVideoShown(")
     + _msgs
     + cut(asrc, "function nameKey(", "function charKey(")
     + cut(asrc, "/* ---------- locations (shared", "/* ---------- top bar")
     + "; globalThis.B = { linkKind, bSceneCharMsgs };");
const chars = [{ name: "Minh", image: "m.png" }, { name: "Lan", image: "l.png" }, { name: "Hoa", image: "h.png" }];
const mk = (extra, state) => {
  const sb = { characters: chars, locations: [{ name: "Phòng khách" }], ...extra };
  S.cur = { storyboard: sb, scene_state: state };
  return sb;
};
const out = {};
// scene with a location, 3 characters with pictures, server sent 2
{
  const sc = { id: "s1", mode: "keyframe", location: "phòng khách", characters: ["Minh", "Lan", "Hoa"] };
  mk({ scenes: [sc] }, { s1: { refs: ["Minh", "Lan"], char_ref_limit: 2 } });
  out.loc = { hoa: B.linkKind("Hoa", "s1"), minh: B.linkKind("Minh", "s1"), msgs: B.bSceneCharMsgs(sc).map((k) => k.text) };
  // no char_ref_limit in scene_state (older payload): fallback from the scene's location
  mk({ scenes: [sc] }, { s1: { refs: ["Minh", "Lan"] } });
  out.locFallback = B.linkKind("Hoa", "s1");
  mk({ scenes: [sc], use_character_refs: false }, { s1: { refs: [], char_ref_limit: 2 } });
  out.off = B.linkKind("Hoa", "s1");
}
// no location / i2v: limit 3
{
  const chars4 = [...chars, { name: "Tú", image: "t.png" }];
  const sc = { id: "s2", mode: "keyframe", characters: ["Minh", "Lan", "Hoa", "Tú"] };
  mk({ scenes: [sc], characters: chars4 }, { s2: { refs: ["Minh", "Lan", "Hoa"], char_ref_limit: 3 } });
  out.noLoc = { tu: B.linkKind("Tú", "s2"), msgs: B.bSceneCharMsgs(sc).map((k) => k.text) };
  mk({ scenes: [sc], characters: chars4 }, { s2: { refs: ["Minh", "Lan", "Hoa"] } });
  out.noLocFallback = B.linkKind("Tú", "s2");
  const iv = { id: "s3", mode: "i2v", image: "x.png", location: "Phòng khách", characters: ["Minh", "Lan", "Hoa", "Tú"] };
  mk({ scenes: [iv], characters: chars4 }, { s3: { refs: ["Minh", "Lan", "Hoa"] } });
  out.i2v = B.linkKind("Tú", "s3");
  mk({ scenes: [], characters: chars4 }, { s9: { refs: ["Minh", "Lan", "Hoa"] } });
  out.unknownScene = B.linkKind("Tú", "s9");
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def js(tmp_path_factory):
    if not shutil.which("node"):
        pytest.skip("needs node")
    d = tmp_path_factory.mktemp("board")
    msgs = _func("linkKind") + _func("bSceneCharMsgs") + _func("bMode")
    (d / "harness.js").write_text(f"const _msgs = {json.dumps(msgs)};\n" + HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(d / "harness.js"), str(BOARD), str(APP)], capture_output=True, text=True, cwd=d)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_location_scene_limit_is_two(js):  # AC36
    assert js["loc"]["hoa"] == {"desc": True, "reason": "đã đủ 2 ảnh nhân vật: Minh, Lan"}
    assert js["loc"]["minh"] == {"desc": False, "reason": ""}
    assert js["loc"]["msgs"] == ["Chỉ gửi ảnh của 2 nhân vật: Minh, Lan"]
    assert js["locFallback"]["reason"] == "đã đủ 2 ảnh nhân vật: Minh, Lan"
    assert "bối cảnh" not in js["loc"]["hoa"]["reason"] and "keyframe" not in js["loc"]["hoa"]["reason"]


def test_refs_off_wins(js):  # AC36
    assert js["off"] == {"desc": True, "reason": "đang tắt gửi ảnh nhân vật"}


def test_no_location_or_i2v_limit_is_three(js):  # AC36
    three = "đã đủ 3 ảnh nhân vật: Minh, Lan, Hoa"
    assert js["noLoc"]["tu"]["reason"] == three
    assert js["noLoc"]["msgs"] == ["Chỉ gửi ảnh của 3 nhân vật: Minh, Lan, Hoa"]
    assert js["noLocFallback"]["reason"] == three
    assert js["i2v"]["reason"] == three
    assert js["unknownScene"]["reason"] == three
