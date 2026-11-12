"""Left panel collapse / drawer and the hide button of the image->video banner, run under node with a fake DOM."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "filmmaker" / "web" / "static" / "app.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")

HARNESS = r"""
const src = require("fs").readFileSync(process.argv[2], "utf8");
const fn = (n) => src.match(new RegExp("^function " + n + "\\(.*?^}\\n", "ms"))[0];
const cls = new Set();
const document = { body: { classList: {
  toggle: (c, on) => { const v = on === undefined ? !cls.has(c) : !!on; v ? cls.add(c) : cls.delete(c); return v; },
  contains: (c) => cls.has(c) } } };
const attrs = {}; const btn = { setAttribute: (k, v) => { attrs[k] = v; }, title: "" };
const $ = () => btn;
let narrow = false; const window = { matchMedia: () => ({ matches: narrow }) };
const store = {}; const localSet = (k, v) => { store[k] = v; };
eval(src.match(/^const NARROW = .*$/m)[0].replace("const ", "var "));
eval(fn("toggleSidebar")); eval(fn("syncMenuButton"));
const out = {};
toggleSidebar(); out.wideCollapse = [cls.has("side-collapsed"), store.sideCollapsed, attrs["aria-expanded"], attrs["aria-label"]];
toggleSidebar(); out.wideExpand = [cls.has("side-collapsed"), store.sideCollapsed, attrs["aria-expanded"]];
narrow = true;
toggleSidebar(); out.narrowOpen = [cls.has("side-open"), cls.has("side-collapsed"), attrs["aria-expanded"]];
toggleSidebar(false); out.narrowClose = [cls.has("side-open"), store.sideCollapsed];
console.log(JSON.stringify(out));
"""


def test_menu_button_collapses_wide_and_opens_drawer_narrow(tmp_path):
    js = tmp_path / "h.js"
    js.write_text(HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(js), str(APP)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["wideCollapse"] == [True, True, "false", "Mở rộng danh sách dự án"]  # remembered
    assert out["wideExpand"] == [False, False, "true"]
    assert out["narrowOpen"] == [True, False, "true"]  # narrow: a drawer, the rail setting is untouched
    assert out["narrowClose"] == [False, False]


def test_static_wiring():
    src = APP.read_text(encoding="utf-8")
    # opening a project closes the drawer but never collapses the rail
    assert 'if (document.body.classList.contains("side-open")) toggleSidebar(false);' in src
    assert 'localGet("sideCollapsed", false)' in src
    # the image->video banner can be hidden per project; it comes back when its message changes
    assert "i2vHidden.${S.cur.id}" in src and "banner-close" in src and "localGet(i2vKey) !== i2vSig" in src
    css = (APP.parent / "app.css").read_text(encoding="utf-8")
    assert "body.side-collapsed { --sidebar-w: 72px; }" in css and "#btn-menu { display: inline-flex; }" in css
