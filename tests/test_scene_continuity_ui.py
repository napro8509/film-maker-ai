"""UI side of scene-continuity (AC35-AC38, app.js part of AC36) without a browser.

Two layers, as in test_dialogue_language_ui.py:
- static checks on filmmaker/web/static/app.js / progress.js (handlers writing `undefined`, confirmModal before
  every action that turns scenes "Đã đổi" with a Huỷ branch that neither saves nor starts a job, chip limit,
  job kind "locations");
- the pure helpers (locationImpact, locsLocked, rememberLocNames, renameLocationsInScenes, removeLocation,
  charRefLimit) and the real save() cut out of app.js and run under node with stubs.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "filmmaker" / "web" / "static"
APP = STATIC / "app.js"
SRC = APP.read_text(encoding="utf-8")
PROG = (STATIC / "progress.js").read_text(encoding="utf-8")
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def _func(name: str, src: str = SRC) -> str:
    """Source of a top-level `function name(` up to its closing brace at column 0."""
    m = re.search(rf"^(?:async )?function {re.escape(name)}\(.*?^}}\n", src, re.S | re.M)
    assert m, f"function {name} not found"
    return m.group(0)


# --- static checks ---------------------------------------------------------------------------------------

@needs_node
def test_js_parses():
    for f in ("app.js", "progress.js"):
        res = subprocess.run(["node", "--check", str(STATIC / f)], capture_output=True, text=True)
        assert res.returncode == 0, res.stderr


def test_progress_knows_location_jobs():  # design-review 2
    assert re.search(r'const PROG_KINDS = new Set\(\[[^\]]*"locations"', PROG)
    assert 'function progLocKey(l) { return "loc:" + String(l?.name || "").trim(); }' in PROG


def test_defaults_written_as_undefined():  # AC35
    story = _func("renderLocations")
    assert "sb.continuity = e.target.checked ? undefined : false" in story
    sl = _func("renderSceneLocation")
    assert "sc.continue_previous = e.target.checked ? undefined : false" in sl
    assert "sc.location = v || undefined" in sl
    assert '"— Không —"' in sl
    assert "if (!locs.length) delete sb.locations;" in _func("removeLocation")


def test_story_tab_has_locations_card():
    fn = _func("renderStory")
    assert fn.index("renderCharacters(sb)") < fn.index("renderLocations(sb)")
    loc = _func("renderLocations")
    for s in ('"Bối cảnh"', "Nối keyframe cảnh liền trước cùng bối cảnh (miễn phí)", "bối cảnh`", '"Thêm bối cảnh"'):
        assert s in loc, s
    row = _func("renderLocation")
    assert "progAttach(div, [progLocKey(l)])" in row
    assert 'fd.append("kind", "location")' in row
    assert "Prompt tạo ảnh bối cảnh" in row and "ls?.image_prompt" in row
    assert "markLocNames()" in row and 'zone(`locwarn-${i}`' in row
    assert "cảnh đã có clip dùng bối cảnh này sẽ chuyển Đã đổi (render lại tốn credit)" in row
    assert "tối đa 3 nhân vật mỗi cảnh; 2 nếu cảnh có bối cảnh" in _func("renderCharacters")


def _after(fn: str, start: str, *needles: str) -> None:
    i = fn.index(start)
    for n in needles:
        assert fn.index(n, i) > i, n


def test_confirm_before_changes_and_cancel_does_nothing():  # AC37 (static part)
    sl = _func("renderSceneLocation")
    i_imp = sl.index('kind: "assign"')
    i_conf = sl.index("await confirmModal(", i_imp)
    cancel = sl[i_conf:sl.index("return; }", i_conf) + len("return; }")]
    assert "e.target.value = val; return; }" in cancel and "changed()" not in cancel
    assert i_conf < sl.index("sc.location = v || undefined") < sl.index("changed()", i_conf)

    conf = _func("confirmLocImpact")
    assert "if (n <= 0) return true;" in conf and "confirmModal(" in conf

    row = _func("renderLocation")
    # Tạo ảnh / Tạo lại ảnh: confirm, Huỷ returns before the job
    i = row.index('impact("image"), url ? "Tạo lại ảnh bối cảnh"')
    seg = row[i - 60:row.index("makeLocationImages([l.name], true)", i)]
    assert "if (!(await confirmLocImpact(" in seg and seg.rstrip().endswith("return;")
    # upload: confirm before assigning, Huỷ does not touch l.image / changed()
    up = row[row.index('fd.append("kind", "location")'):row.index("const canMake")]
    i_c = up.index('confirmLocImpact(impact("image"), "Dùng ảnh vừa tải lên")')
    cancel = up[i_c:up.index("return; }", i_c)]
    assert "changed()" not in cancel and "l.image" not in cancel
    assert i_c < up.index("l.image = r.path; changed();")
    # picking / removing an image: confirm, Huỷ resets the select
    pick = row[row.index('"aria-label": "Ảnh bối cảnh"'):]
    assert 'confirmLocImpact(impact("image"), v ? "Đổi ảnh bối cảnh" : "Bỏ ảnh bối cảnh")' in pick
    assert '{ e.target.value = l.image || ""; return; }' in pick
    assert pick.index("confirmLocImpact(") < pick.index("l.image = v; changed();")
    # delete always asks, with the number of scenes using it and of scenes turning "Đã đổi"
    dl = row[row.index('title: "Xoá bối cảnh"'):]
    assert 'kind: "delete"' in dl and 'confirmModal("Xoá bối cảnh?", text, "Xoá", true)' in dl
    assert "Đang dùng ở ${using} cảnh" in dl and "${n} cảnh đã có clip sẽ chuyển “Đã đổi”" in dl
    assert dl.index("confirmModal(") < dl.index("removeLocation(sb, at)") < dl.index("changed()")
    # "Tạo ảnh cho N bối cảnh"
    loc = _func("renderLocations")
    mm = loc[loc.index("const makeMissing"):loc.index("return h(")]
    assert "if (!(await confirmLocImpact(n," in mm and mm.index("return;") < mm.index("makeLocationImages([])")
    mk = _func("makeLocationImages")
    assert "await flushSave();" in mk and "saveFailed()" in mk
    # names trimmed: the server matches the (trimmed) YAML names exactly (code-review 7)
    assert ('api("POST", `/api/projects/${S.cur.id}/locations/images`, '
            '{ names: names.map((n) => String(n ?? "").trim()), force })') in mk
    assert 'names: names.map((n) => String(n ?? "").trim())' in _func("makePortraits")


def test_scene_location_field():
    sl = _func("renderSceneLocation")
    assert "disabled: i2v || null" in sl and '"Ảnh → Video không dùng bối cảnh"' in sl
    assert "Nối tiếp keyframe cảnh trước" in sl
    assert "!i2v && sc.location ?" in sl
    assert "Có keyframe ${st.continuity_source} mới hơn; có thể tạo lại keyframe này để nối (miễn phí)" in sl
    assert "renderSceneLocation(sb, sc, i)" in _func("renderScene")


def test_chip_uses_char_ref_limit():  # AC36 (app.js chip)
    fn = _func("renderScene")
    assert "const lim = charRefLimit(sc);" in fn
    assert "nRefs < lim ? charImageUrl(c)" in fn
    assert "nRefs < 3" not in fn


def test_save_blocks_and_renames_locations():  # AC38 (static part)
    fn = _func("save")
    i_block = fn.index('setSaveState("blocked", "tên bối cảnh trống/trùng"); return; }')
    assert fn.index("locsLocked(sb)") < i_block < fn.index("renameLocationsInScenes(sb)") < fn.index('api("PUT"')
    assert "rememberLocNames(sentLocs)" in fn
    for f in ("openProject", "reloadProject", "renderYaml"):
        assert "rememberLocNames()" in _func(f), f


def test_location_job_handling():  # design-review 2
    done = _func("onJobDone")
    assert 'j.kind === "locations" && S.cur && S.cur.id === j.project && j.result?.images' in done
    assert '(j.kind === "portraits" || j.kind === "locations") && (S.tab === "story" || S.tab === "board")' in done
    poll = _func("pollJob")
    assert 'k.startsWith("loc:")' in poll and "syncLocImages(locs)" in poll
    assert 'k.slice("loc:".length)' in _func("syncLocImages")


# --- the real helpers under node -------------------------------------------------------------------------

HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const cut = (a, b) => { const i = src.indexOf(a), j = src.indexOf(b, i); if (i < 0 || j < 0) throw new Error(a); return src.slice(i, j); };
const S = { cur: null, charSaved: new WeakMap(), locSaved: new WeakMap(), locDescBase: new WeakMap() };
const puts = []; let saveState = null;
const api = async (m, url, body) => { puts.push(JSON.parse(JSON.stringify(body.storyboard))); return {}; };
const setSaveState = (st) => { saveState = st; };
const applyMeta = () => {};
eval(cut("function nameKey(", "/* Scenes linked to a character") + cut("/* ---------- locations (shared", "/* ---------- top bar")
     + _save + "; globalThis.L = { locKey, locNameProblems, locsLocked, locNamePairs, rememberLocNames, renameLocationsInScenes,"
     + " removeLocation, sceneHasLoc, charRefLimit, locationImpact, save };");
"""

HARNESS_BODY = r"""
const clone = (x) => JSON.parse(JSON.stringify(x));
const out = {};

// AC37: locationImpact
{
  const sb = { locations: [{ name: "Phòng khách", image: "assets/locations/a.png" }, { name: "Sân" }],
    scenes: [{ id: "s1", mode: "keyframe", location: "Phòng khách" }, { id: "s2", mode: "t2v", location: "phòng KHÁCH" },
             { id: "s3", mode: "i2v", image: "x.png", location: "Phòng khách" }, { id: "s4", mode: "keyframe", location: "Phòng khách" },
             { id: "s5", mode: "keyframe" }, { id: "s6", location: "Sân" }] };
  const st = { s1: { status: "done" }, s2: { status: "done" }, s3: { status: "done" }, s4: { status: "todo" },
               s5: { status: "done" }, s6: { status: "changed" } };
  const sc = (id) => sb.scenes.find((s) => s.id === id);
  const A = sb.locations[0], B = sb.locations[1];
  out.impact = {
    assignNew: L.locationImpact(sb, st, { scene: sc("s5"), to: "Phòng khách", kind: "assign" }),
    assignCase: L.locationImpact(sb, st, { scene: sc("s1"), to: "PHÒNG khách ", kind: "assign" }),
    assignOther: L.locationImpact(sb, st, { scene: sc("s1"), to: "Sân", kind: "assign" }),
    assignNone: L.locationImpact(sb, st, { scene: sc("s1"), to: "", kind: "assign" }),
    assignI2v: L.locationImpact(sb, st, { scene: sc("s3"), to: "Sân", kind: "assign" }),
    assignTodo: L.locationImpact(sb, st, { scene: sc("s4"), to: "Sân", kind: "assign" }),
    edit: L.locationImpact(sb, st, { location: A, kind: "edit" }),
    image: L.locationImpact(sb, st, { location: A, kind: "image" }),
    imageByName: L.locationImpact(sb, st, { location: "phòng khách", kind: "image" }),
    del: L.locationImpact(sb, st, { location: A, kind: "delete" }),
    rename: L.locationImpact(sb, st, { location: A, kind: "rename" }),
    editUnused: L.locationImpact(sb, st, { location: B, kind: "edit" }),
    none: L.locationImpact(sb, {}, { location: A, kind: "edit" }),
  };
  // charRefLimit: server value first, else 2 with a location (not i2v), 3 otherwise
  S.cur = { storyboard: sb, scene_state: { s1: { char_ref_limit: 2 }, s5: { char_ref_limit: 3 } } };
  out.limit = { s1: L.charRefLimit(sc("s1")), s5: L.charRefLimit(sc("s5")), s2: L.charRefLimit(sc("s2")),
                s3: L.charRefLimit(sc("s3")), s4unknown: L.charRefLimit({ id: "x", location: "Không có" }) };
}

// AC38: rename, swap, typing through an existing name, new location, save() blocked / sent
out.rename = (async () => {
  const sb = { title: "t", locations: [{ name: "Phòng khách", description: "d1" }, { name: "Bếp", description: "d2" }],
    scenes: [{ id: "s1", prompt: "p", location: "phòng khách" }, { id: "s2", prompt: "p", location: "Bếp" },
             { id: "s3", prompt: "p" }] };
  S.cur = { id: "p", storyboard: sb };
  L.rememberLocNames();
  const r = {};
  // typing "Phòng khách 2" in the 2nd row goes through "Phòng khách" (duplicate) then "" (blank) first
  sb.locations[1].name = "Phòng khách"; r.dupLocked = L.locsLocked(sb);
  S.pendingSave = true; await L.save(); r.dupState = saveState; r.dupPuts = puts.length; r.dupScenes = sb.scenes.map((s) => s.location ?? null);
  sb.locations[1].name = "  "; r.blankLocked = L.locsLocked(sb);
  S.pendingSave = true; await L.save(); r.blankPuts = puts.length; r.blankScenes = sb.scenes.map((s) => s.location ?? null);
  sb.locations[1].name = "Phòng khách 2"; r.okLocked = L.locsLocked(sb);
  S.pendingSave = true; await L.save(); r.okState = saveState; r.okPuts = puts.length; r.okScenes = sb.scenes.map((s) => s.location ?? null);
  // swap two names in one save
  sb.locations[0].name = "Phòng khách 2"; sb.locations[1].name = "Phòng khách";
  S.pendingSave = true; await L.save(); r.swapScenes = sb.scenes.map((s) => s.location ?? null);
  // a location added since the last save drags no scene along
  sb.locations.push({ name: "Sân", description: "" });
  sb.scenes[2].location = "Sân";
  sb.locations[2].name = "Sân sau";
  L.renameLocationsInScenes(sb); r.newScenes = sb.scenes.map((s) => s.location ?? null);
  r.lastPut = puts[puts.length - 1];
  return r;
})();

// QA note: a freshly added blank location is left out of the PUT, never `locations: - {}`
out.blank = (async () => {
  await out.rename;
  const sb = { title: "t", locations: [{ name: "", description: "" }], scenes: [{ id: "s1", prompt: "p" }] };
  S.cur = { id: "p", storyboard: sb }; L.rememberLocNames();
  const n0 = puts.length;
  S.pendingSave = true; await L.save();
  const onlyBlank = puts[n0];
  sb.locations.push({ name: "Bếp", description: "" });
  S.pendingSave = true; await L.save();
  // a blank row next to a used location does not block saving other edits (QA2 note)
  const sb2 = { title: "t", locations: [{ name: "Phòng khách", description: "d" }, { name: "", description: "" }],
    scenes: [{ id: "s1", prompt: "p", location: "Phòng khách" }] };
  S.cur = { id: "p", storyboard: sb2 }; L.rememberLocNames();
  const lockedBlank = L.locsLocked(sb2);
  sb2.title = "t2"; S.pendingSave = true; await L.save();
  const stateBlank = saveState, putBlank = puts[puts.length - 1];
  sb2.locations[1].description = "garden";  // typed something but no name yet: blocks again
  const lockedNoName = L.locsLocked(sb2);
  const n1 = puts.length; S.pendingSave = true; await L.save();
  return { onlyBlank, mixed: puts[n0 + 1], local: sb.locations.length, lockedBlank, stateBlank, putBlank,
           lockedNoName, noNameState: saveState, noNamePuts: puts.length - n1 };
})();

// AC38: removeLocation
{
  const sb = { locations: [{ name: "A" }, { name: "B" }],
    scenes: [{ id: "s1", prompt: "p", location: "a" }, { id: "s2", prompt: "p", location: "B" }, { id: "s3", prompt: "p" }] };
  const r1 = L.removeLocation(sb, 0);
  const after1 = clone(sb);
  const r2 = L.removeLocation(sb, 0);
  const twin = { locations: [{ name: "A" }, { name: " a " }], scenes: [{ id: "s1", prompt: "p", location: "A" }] };
  const r3 = L.removeLocation(twin, 0);
  out.remove = { r1, after1, r2, after2: clone(sb), hasKey: "locations" in sb, r3, twin: clone(twin), bad: L.removeLocation(sb, 5) };
}

Promise.all([out.rename, out.blank]).then(([r, b]) => { out.rename = r; out.blank = b; process.stdout.write(JSON.stringify(out)); });
"""


@pytest.fixture(scope="module")
def js(tmp_path_factory):
    if not shutil.which("node"):
        pytest.skip("needs node")
    d = tmp_path_factory.mktemp("ui")
    save_src = json.dumps(_func("save") + _func("withoutBlankLocations"))
    (d / "harness.js").write_text(f"const _save = {save_src};\n" + HARNESS + HARNESS_BODY, encoding="utf-8")
    res = subprocess.run(["node", str(d / "harness.js"), str(APP)], capture_output=True, text=True, cwd=d)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_location_impact(js):  # AC37
    assert js["impact"] == {
        "assignNew": 1, "assignCase": 0, "assignOther": 1, "assignNone": 1, "assignI2v": 0, "assignTodo": 0,
        "edit": 2, "image": 2, "imageByName": 2, "del": 2, "rename": 0, "editUnused": 0, "none": 0,
    }


def test_char_ref_limit(js):  # AC36 fallback
    assert js["limit"] == {"s1": 2, "s5": 3, "s2": 2, "s3": 3, "s4unknown": 3}


def test_rename_and_lock(js):  # AC38
    r = js["rename"]
    assert r["dupLocked"] and r["dupState"] == "blocked" and r["dupPuts"] == 0
    assert r["dupScenes"] == ["phòng khách", "Bếp", None]
    assert r["blankLocked"] and r["blankPuts"] == 0 and r["blankScenes"] == ["phòng khách", "Bếp", None]
    assert not r["okLocked"] and r["okState"] == "saved" and r["okPuts"] == 1
    assert r["okScenes"] == ["phòng khách", "Phòng khách 2", None]
    assert r["swapScenes"] == ["Phòng khách 2", "Phòng khách", None]
    assert r["newScenes"] == ["Phòng khách 2", "Phòng khách", "Sân"]


def test_blank_location_not_sent(js):  # QA note: "Thêm bối cảnh" alone never writes `locations: - {}`
    b = js["blank"]
    assert "locations" not in b["onlyBlank"]
    assert b["mixed"]["locations"] == [{"name": "Bếp", "description": ""}]
    assert b["local"] == 2  # the row stays on screen
    # a blank row does not lock the names, so other edits still save (QA2 note)
    assert b["lockedBlank"] is False and b["stateBlank"] == "saved"
    assert b["putBlank"]["title"] == "t2" and b["putBlank"]["locations"] == [{"name": "Phòng khách", "description": "d"}]
    # once something is typed in the row, an empty name blocks again
    assert b["lockedNoName"] is True and b["noNameState"] == "blocked" and b["noNamePuts"] == 0
    add = _func("renderLocations")
    add = add[add.index("const add = () =>"):add.index("\n", add.index("const add = () =>"))]
    assert "changed()" not in add


def test_pickers_split_location_images():  # code-review 8
    assert 'const LOC_ASSET_DIR = "assets/locations/";' in SRC
    assert "const imgs = otherImageAssets();" in _func("renderCharacter")
    row = _func("renderLocation")
    assert "const imgs = locImageAssets();" in row and "S.assets.images.map" not in row
    assert "S.assets.images.map" not in _func("renderCharacter")
    board = (STATIC / "board.js").read_text(encoding="utf-8")
    assert "const imgs = otherImageAssets();" in _func("bPickImage", board)


def test_make_buttons_follow_typing():  # QA note: "Tạo ảnh" unlocks as soon as name + description are typed
    row = _func("renderLocation")
    assert "zone(`locmake-${i}`" in row and row.count("refreshLocMake(i)") == 3
    assert 'zone("locmake-all"' in _func("renderLocations")
    assert ("function refreshLocMake(i) { refreshZone(`locmake-${i}`); "
            'refreshZone("locmake-all"); }') in SRC


def test_remove_location(js):  # AC38
    m = js["remove"]
    assert m["r1"] == ["s1"]
    assert m["after1"]["locations"] == [{"name": "B"}]
    assert "location" not in m["after1"]["scenes"][0] and m["after1"]["scenes"][1]["location"] == "B"
    assert m["r2"] == ["s2"] and not m["hasKey"] and "locations" not in m["after2"]
    assert all("location" not in s for s in m["after2"]["scenes"])
    assert m["r3"] == [] and m["twin"]["scenes"][0]["location"] == "A"  # same-name twin keeps the link
    assert m["bad"] == []


def test_results_validate_as_storyboard(js):  # AC38: the storyboards the UI sends pass the server model
    sb_mod = pytest.importorskip("filmmaker.storyboard")
    if not hasattr(sb_mod, "Location"):
        pytest.skip("backend T1 (Location) not merged yet")
    for data in (js["rename"]["lastPut"], js["remove"]["after1"], js["remove"]["after2"]):
        sb_mod.Storyboard.model_validate({"title": "t", **data})
