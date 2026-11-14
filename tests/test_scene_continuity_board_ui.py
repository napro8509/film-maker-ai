"""Board side of scene-continuity: linkKind / bSceneCharMsgs report which characters have their picture sent.
Characters are no longer capped (they go through Flow characters), so every character with a picture is sent and
only characters without one fall back to description. Real code cut out of board.js / app.js and run under node
with tiny stubs; skipped without node."""
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
// scene with a location and 4 characters that all have pictures: every one is sent, no cap
{
  const chars4 = [...chars, { name: "Tú", image: "t.png" }];
  const sc = { id: "s1", mode: "keyframe", location: "phòng khách", characters: ["Minh", "Lan", "Hoa", "Tú"] };
  mk({ scenes: [sc], characters: chars4 }, { s1: { refs: ["Minh", "Lan", "Hoa", "Tú"] } });
  out.loc = { tu: B.linkKind("Tú", "s1"), minh: B.linkKind("Minh", "s1"), msgs: B.bSceneCharMsgs(sc).map((k) => k.text) };
  // use_character_refs: false wins over everything
  mk({ scenes: [sc], characters: chars4, use_character_refs: false }, { s1: { refs: [] } });
  out.off = B.linkKind("Tú", "s1");
}
// a character without a picture: only its description is sent, and the board says so
{
  const chars2 = [{ name: "Minh", image: "m.png" }, { name: "Nam" }];
  const sc = { id: "s2", mode: "keyframe", characters: ["Minh", "Nam"] };
  mk({ scenes: [sc], characters: chars2 }, { s2: { refs: ["Minh"] } });
  out.noImg = { nam: B.linkKind("Nam", "s2"), minh: B.linkKind("Minh", "s2"), msgs: B.bSceneCharMsgs(sc).map((k) => k.text) };
}
// i2v sends no character pictures; an unknown scene id behaves the same as "picture not sent"
{
  const chars4 = [...chars, { name: "Tú", image: "t.png" }];
  const iv = { id: "s3", mode: "i2v", image: "x.png", location: "Phòng khách", characters: ["Minh", "Lan", "Hoa", "Tú"] };
  mk({ scenes: [iv], characters: chars4 }, { s3: { refs: [] } });
  out.i2v = B.linkKind("Tú", "s3");
  mk({ scenes: [], characters: chars4 }, { s9: { refs: [] } });
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


def test_location_scene_sends_every_character(js):
    # A scene with a location and four characters with pictures: all of them are sent, nothing is dropped.
    assert js["loc"]["tu"] == {"desc": False, "reason": ""}
    assert js["loc"]["minh"] == {"desc": False, "reason": ""}
    assert js["loc"]["msgs"] == []


def test_refs_off_wins(js):
    assert js["off"] == {"desc": True, "reason": "đang tắt gửi ảnh nhân vật"}


def test_character_without_picture_uses_description(js):
    assert js["noImg"]["nam"] == {"desc": True, "reason": "chưa có ảnh"}
    assert js["noImg"]["minh"] == {"desc": False, "reason": ""}
    assert js["noImg"]["msgs"] == ["Nam chưa có ảnh tham chiếu — chỉ dùng mô tả"]


def test_i2v_and_unknown_scene_send_no_picture(js):
    assert js["i2v"] == {"desc": True, "reason": "chưa có ảnh"}
    assert js["unknownScene"] == {"desc": True, "reason": "chưa có ảnh"}
