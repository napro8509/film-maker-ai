"""UI side of dialogue-language (AC1-AC4, AC18-AC20) without a browser (no playwright here).

Two layers:
- static checks that read filmmaker/web/static/app.js (labels, localStorage keys, POST body, order of
  validate -> localSet -> api, the "dl-warn" zone, openProject clearing S.dlChanged);
- `cleanLang` / `langPicker` / `h` cut out of app.js and run under node with a tiny fake DOM, so the option
  lists, "Khác…" handling and read() results are checked on the real code.
The manual checklist for a real browser is in .dev/features/dialogue-language/ui-checklist.md.
"""
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
    """Source of a top-level `function name(` up to its closing brace at column 0."""
    m = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}\n", SRC, re.S | re.M)
    assert m, f"function {name} not found in app.js"
    return m.group(0)


def _ai_form() -> str:
    start = SRC.index("const aiForm = () =>")
    return SRC[start:SRC.index("const importForm = () =>", start)]


# --- static checks ---------------------------------------------------------------------------------------

@needs_node
def test_app_js_parses():
    res = subprocess.run(["node", "--check", str(APP)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_new_film_popup_fields_and_storage():  # AC1, AC3
    f = _ai_form()
    assert 'field("Nhân vật nói", speak.el)' in f and 'field("Ngôn ngữ phụ đề", sub.el)' in f
    assert 'langPicker(DIALOGUE_LANGS, cleanLang(localGet("dialogueLang")) || "Vietnamese")' in f
    assert 'cleanLang(localGet("subtitleLang"))' in f and "langPicker(SUBTITLE_LANGS" in f
    assert 'langPicker(SUBTITLE_LANGS, subSaved || "Vietnamese")' in f  # "none" = Không phụ đề is remembered too
    assert "localStorage" not in f  # only through localGet/localSet ("fm." prefix)


def test_new_film_popup_validates_then_stores_then_posts():  # AC2, AC3, AC4
    f = _ai_form()
    go = f[f.index("const go = async"):]
    i_read = go.index("speak.read(), b = sub.read()")
    i_bad_a = go.index('if (!a.ok) { toast("Nhân vật nói: " + a.msg, "error");')
    i_bad_b = go.index('if (!b.ok) { toast("Ngôn ngữ phụ đề: " + b.msg, "error");')
    i_store = go.index('localSet("dialogueLang", a.value); localSet("subtitleLang", b.value);')
    i_post = go.index('api("POST", "/api/projects/plan"')
    assert i_read < i_bad_a < i_bad_b < i_store < i_post
    # each bad branch returns before storing anything
    for i in (i_bad_a, i_bad_b):
        line = go[i:go.index("\n", i)]
        assert line.rstrip().endswith("return; }"), line
    body = go[i_post:go.index("\n", i_post)]
    assert "language: b.value" in body and "dialogue_language: a.value" in body  # AC4
    # the store sits outside the try: kept even when the plan request / job fails
    assert go.index("try {") > i_store


# --- run the real cleanLang / langPicker under node ------------------------------------------------------

HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const cut = (a, b) => { const i = src.indexOf(a), j = src.indexOf(b, i); if (i < 0 || j < 0) throw new Error(a); return src.slice(i, j); };
class Node {}
class Text extends Node { constructor(t) { super(); this.text = t; } }
class El extends Node {
  constructor(tag) { super(); this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.hidden = false;
    this._value = ""; this._sel = undefined; this.listeners = {}; this.style = {}; this.className = ""; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "hidden") this.hidden = true; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  append(c) { this.children.push(c); }
  focus() {}
  options() { return this.children.filter((c) => c instanceof El && c.tagName === "OPTION"); }
  get text() { return this.children.map((c) => c.text).join(""); }
  get value() {
    if (this.tagName === "SELECT") { const o = this.options(); return this._sel === undefined ? (o[0] ? o[0].value : "") : this._sel; }
    return this._value;
  }
  set value(v) {
    if (this.tagName === "SELECT") { const o = this.options().find((x) => x.value === String(v)); this._sel = o ? o.value : ""; }
    else this._value = String(v);
  }
  fire(t) { (this.listeners[t] || []).forEach((f) => f({ target: this })); }
}
const document = { createElement: (t) => new El(t), createTextNode: (t) => new Text(t) };
eval(cut("function h(", "const $ = ") + cut("const LANG_OTHER", "function fmtBytes(") +
     "; globalThis.api = { cleanLang, langPicker, DIALOGUE_LANGS, SUBTITLE_LANGS, LANG_OTHER };");
const L = globalThis.api;  // const/let inside eval stay there: go through this object
const view = (p) => ({ select: p.select.value, other: !p.input.hidden, input: p.input.value, read: p.read(),
                       labels: p.select.options().map((o) => o.text), values: p.select.options().map((o) => o.value) });
const typed = (p, text) => { p.select.value = L.LANG_OTHER; p.select.fire("change"); p.input.value = text; return view(p); };
const out = {
  clean: [null, 5, "", "   ", "a\nb", "a\rb", "x".repeat(40), "x".repeat(41), "  Brazilian   Portuguese ", "none"].map(L.cleanLang),
  dVi: view(L.langPicker(L.DIALOGUE_LANGS, "Vietnamese")),
  dThai: view(L.langPicker(L.DIALOGUE_LANGS, "thai")),
  dNone: view(L.langPicker(L.DIALOGUE_LANGS, "None")),
  dJa: view(L.langPicker(L.DIALOGUE_LANGS, "Japanese")),
  dNull: view(L.langPicker(L.DIALOGUE_LANGS, null)),
  sVi: view(L.langPicker(L.SUBTITLE_LANGS, "Vietnamese")),
  uNull: view(L.langPicker(L.DIALOGUE_LANGS, null, { unset: true })),
  uBlank: view(L.langPicker(L.DIALOGUE_LANGS, "  ", { unset: true })),
  uThai: view(L.langPicker(L.DIALOGUE_LANGS, "Thai", { unset: true })),
  otherBlank: typed(L.langPicker(L.DIALOGUE_LANGS, "Vietnamese"), "   "),
  otherLong: typed(L.langPicker(L.SUBTITLE_LANGS, "Vietnamese"), "x".repeat(41)),
  otherOk: typed(L.langPicker(L.SUBTITLE_LANGS, "Vietnamese"), "  Brazilian   Portuguese "),
  otherNl: typed(L.langPicker(L.DIALOGUE_LANGS, "Vietnamese"), "Viet\nnamese"),
};
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def js(tmp_path_factory):
    if not shutil.which("node"):
        pytest.skip("needs node")
    d = tmp_path_factory.mktemp("ui")
    (d / "harness.js").write_text(HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(d / "harness.js"), str(APP)], capture_output=True, text=True, cwd=d)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


DIALOGUE_LABELS = ["Tiếng Việt", "English", "ไทย (Tiếng Thái)", "Không thoại (chỉ nhạc/âm thanh)", "Khác…"]
SUBTITLE_LABELS = ["Tiếng Việt", "English", "ไทย (Tiếng Thái)", "Không phụ đề", "Khác…"]


def test_clean_lang(js):
    assert js["clean"] == [None, None, None, None, None, None, "x" * 40, None, "Brazilian Portuguese", "none"]


def test_option_lists(js):  # AC1, AC19
    assert js["dVi"]["labels"] == DIALOGUE_LABELS
    assert js["dVi"]["values"] == ["Vietnamese", "English", "Thai", "none", "__other__"]
    assert js["sVi"]["labels"] == SUBTITLE_LABELS
    assert js["uNull"]["labels"] == ["Chưa đặt (Veo tự đoán)"] + DIALOGUE_LABELS
    assert js["uNull"]["values"][0] == ""


def test_initial_values(js):  # AC3 (remembered values) and AC19 (old project = Chưa đặt)
    assert js["dVi"]["select"] == "Vietnamese" and not js["dVi"]["other"]
    assert js["dVi"]["read"] == {"ok": True, "value": "Vietnamese"}
    assert js["dThai"]["select"] == "Thai"  # matched case-insensitively, saved as the preset value
    assert js["dNone"]["read"] == {"ok": True, "value": "none"}
    assert js["dJa"]["select"] == "__other__" and js["dJa"]["other"] and js["dJa"]["input"] == "Japanese"
    assert js["dJa"]["read"] == {"ok": True, "value": "Japanese"}
    assert js["dNull"]["select"] == "Vietnamese"  # popup: no saved choice -> Tiếng Việt
    for k in ("uNull", "uBlank"):
        assert js[k]["select"] == "" and js[k]["read"] == {"ok": True, "value": None}
    assert js["uThai"]["read"] == {"ok": True, "value": "Thai"}


def test_other_input_rules(js):  # AC2
    for k in ("otherBlank", "otherLong", "otherNl"):
        assert js[k]["other"] and js[k]["read"]["ok"] is False and js[k]["read"]["msg"], k
    assert js["otherOk"]["read"] == {"ok": True, "value": "Brazilian Portuguese"}
