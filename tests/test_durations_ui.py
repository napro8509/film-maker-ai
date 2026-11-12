"""UI side of flow-render-fixes (AC10, AC11, AC13, AC16, AC20, AC28) without a browser (no playwright here).

Two layers:
- static checks that read filmmaker/web/static/app.js (order of checks in confirmPaid/runAction, lineClass order, the
  bad_duration line in renderSceneActions);
- the real helpers, renderSceneActions, openDurationFix, confirmPaid, runAction and lineClass cut out of app.js and run
  under node with a tiny fake DOM and fake api/modal/toast/flushSave/saveFailed/reloadProject, recording every call.
The manual checklist for a real browser is in .dev/features/flow-render-fixes/ui-checklist.md.
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


# --- static checks ---------------------------------------------------------------------------------------

@needs_node
def test_app_js_parses():  # AC32
    res = subprocess.run(["node", "--check", str(APP)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


# --- run the real code under node ------------------------------------------------------------------------

HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const cut = (a, b) => { const i = src.indexOf(a), j = src.indexOf(b, i + a.length); if (i < 0 || j < 0) throw new Error(a); return src.slice(i, j); };

class Node {}
class Text extends Node { constructor(t) { super(); this.text = t; } }
class El extends Node {
  constructor(tag) { super(); this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.listeners = {};
    this.style = {}; this.className = ""; this.disabled = false; this.checked = false; this.value = ""; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  append(...cs) { this.children.push(...cs); }
  replaceChildren(...cs) { this.children = cs.map((c) => (c instanceof Node ? c : new Text(String(c)))); }
  remove() {}
  focus() {}
  get id() { return this.attrs.id; }
  get text() { return this.children.map((c) => c.text).join(""); }
  get textContent() { return this.text; }
  set textContent(t) { this.children = [new Text(String(t))]; }
  fire(t) { (this.listeners[t] || []).forEach((f) => f({ target: this })); }
  all() { const out = [this]; for (const c of this.children) if (c instanceof El) out.push(...c.all()); return out; }
}
const document = { createElement: (t) => new El(t), createTextNode: (t) => new Text(t),
  getElementById: (id) => { for (const m of modals.filter((x) => x.open)) for (const b of m.btns) if (b.id === id) return b; return null; } };

// ---- fakes the cut code calls ----
let S, calls, modals, toasts, flushes, saveBad, reloads, modelChanges, jobs, apiImpl, running;
const reset = () => { S = { cur: { id: "p1", errors: [], scene_state: {}, storyboard: { scenes: [] } }, env: { gflow: true, ffmpeg: true }, saveError: "disk full" };
  calls = []; modals = []; toasts = []; flushes = 0; saveBad = false; reloads = []; modelChanges = []; jobs = []; running = false;
  apiImpl = () => { throw new Error("unexpected api"); }; };
const api = async (method, url, body) => { calls.push({ method, url: url.replace("/api/projects/p1", ""), body: body === undefined ? null : JSON.parse(JSON.stringify(body)) }); return apiImpl(url.replace("/api/projects/p1", ""), body); };
const httpErr = (status, detail) => { const e = new Error(detail.message || "err"); e.status = status; e.detail = detail; return e; };
const modal = ({ title, body, buttons = [], onClose }) => {
  const m = { title, body, open: true };
  const close = () => { if (!m.open) return; m.open = false; onClose && onClose(); };
  m.btns = buttons.map((b) => h("button", { id: b.id, disabled: b.disabled, onclick: () => (b.onclick ? b.onclick(close) : close()) }, b.label));
  modals.push(m);
  return close;
};
const toast = (msg, kind = "") => toasts.push({ msg, kind });
const flushSave = async () => { flushes++; };
const saveFailed = () => saveBad;
const reloadProject = (d) => reloads.push(d);
const jobRunning = () => running;
const openModelChange = (o) => modelChanges.push(o);
const icon = () => null;
const FREE = () => null, PAID = () => null;
const NATIVE = () => true;
const startJob = (j) => jobs.push(j);
const refreshCurrent = () => {}, refreshZones = () => {}, applyMeta = () => {}, localGet = () => null, localSet = () => {};
const prettyError = (e) => String(e), finalPrefs = () => ({}), confirmModal = async () => true, lightbox = () => {};
const MODES = [["keyframe", "Ảnh → Video"]];

eval(cut("function h(", "const $ = ") + cut("function put(", "const esc = ") +
     cut("function durationRowText(", "/* ---------- story tab") +
     cut("async function runAction(", "function startJob(") + cut("function lineClass(", "function appendLog("));

const tick = async (n = 6) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };
const top = () => { const o = modals.filter((m) => m.open); return o[o.length - 1] || null; };
const btn = (m, label) => (m.btns.find((b) => b.text === label) || m.body.all().find((e) => e.tagName === "BUTTON" && e.text === label));
const click = async (b) => { if (!b) throw new Error("no button"); b.fire("click"); await tick(); };
const view = (m) => m && ({ title: m.title, open: m.open, text: m.body.text, btns: m.btns.map((b) => ({ label: b.text, id: b.id || null, disabled: b.disabled, hidden: b.style.display === "none" })),
  rows: m.body.all().filter((e) => e.tagName === "TR").slice(1).map((tr) => tr.children.map((td) => td.text)) });
const summary = (extra = {}) => ({ calls: calls.map((c) => c.method + " " + c.url), bodies: calls.map((c) => c.body), toasts, flushes, reloads, modalCount: modals.length, ...extra });

const R = (id, duration, roundable, reason = null, extra = {}) => ({ id, model: "veo-lite", duration, allowed: [4, 6, 8], up: roundable ? duration + 1 : null, down: roundable ? duration - 1 : null, roundable, reason, ...extra });
const PV = (base, ...rows) => ({ scenes: rows.map(([id, duration]) => ({ id, model: "veo-lite", duration, allowed: [4, 6, 8], up: duration + 1, down: duration - 1 })), skipped: ["s05"], base });
const EST = (o = {}) => ({ renders: [], blocked: [], new_keyframes: [], retired: [], bad_duration: [], blocked_bad_duration: [], adopt: [], unverified_clip: [], ...o });

const scenarios = {};

scenarios.helpers = async () => ({
  row: durationRowText(R("s02", 7, false)),
  interrupted: durationManualReason(R("s02", 7, false, "interrupted")),
  done: durationManualReason(R("s04", 5, false, "done")),
  other: durationManualReason(R("s01", 5, true)),
  resend: resendableBlocked({ blocked: ["s02", "s04", "s06"], blocked_bad_duration: [R("s02", 7, false, "interrupted")] }),
  resendNone: resendableBlocked({ blocked: ["s02"], blocked_bad_duration: [R("s02", 7, false, "interrupted")] }),
  resendOld: resendableBlocked({ blocked: ["s02"] }),
  bbd: blockedBadDurationText(R("s02", 7, false, "interrupted")),
  why: [interruptedReasonText("Flow (giao diện chat): Flow báo đang xếp hàng"), interruptedReasonText("Lỗi khác"),
        interruptedReasonText(""), interruptedReasonText(null), interruptedReasonText(undefined), interruptedReasonText("  ")],
  whyLong: interruptedReasonText("Flow (giao diện chat): " + "x".repeat(400)),
  why300: interruptedReasonText("y".repeat(300)),
  lines: ["  $ flow_ui i2v --prompt 'Error in the rain'", "  ✘ LỖI s01", "Error: boom", "  OK -> clips/s01.mp4", "⚠ BỎ QUA s02", "plain", "$ not indented"].map(lineClass),
});

scenarios.cards = async () => {
  const st = {
    todo: { status: "todo", bad_duration: { duration: 7, model: "veo-lite", allowed: [4, 6, 8] } },
    ok: { status: "todo", bad_duration: null },
    intr: { status: "interrupted", error: "Flow (giao diện chat): Flow báo đang xếp hàng — " + "z".repeat(400), bad_duration: { duration: 7, model: "veo-lite", allowed: [4, 6, 8] } },
    intrNoErr: { status: "interrupted", error: "", bad_duration: null },
    intrManual: { status: "interrupted", error: "x", manual_clip: "ok", bad_duration: { duration: 7, model: "veo-lite", allowed: [4, 6, 8] } },
    done: { status: "done", bad_duration: null },
  };
  S.cur.scene_state = st;
  const out = {};
  for (const id of Object.keys(st)) {
    const el = renderSceneActions({ id, mode: "text" });
    out[id] = { text: el.text, msgs: el.all().filter((e) => /scene-msg/.test(e.className)).map((e) => [e.className, e.text]) };
  }
  return out;
};

// AC11/AC12: flushSave -> preview once -> pick direction -> round with that base -> reloadProject, "applied"
scenarios.applyFlow = async () => {
  apiImpl = (url) => {
    if (url === "/durations/preview") return PV("B1", ["s01", 5], ["s02", 7]);
    if (url === "/durations/round") return { changed: true, scenes: [], skipped: ["s05"], project: { id: "p1", marker: "new" } };
  };
  let result;
  openDurationFix([R("s01", 5, true)], { scene: "s01" }).then((r) => { result = r; });
  await tick();
  const m = top();
  const before = view(m);
  await click(btn(m, "Làm tròn lên"));
  const afterUp = view(m);
  await click(btn(m, "Làm tròn xuống"));
  const afterDown = view(m);
  const previewsBeforeApply = calls.length;
  await click(btn(m, "Làm tròn lên"));
  await click(m.btns.find((b) => b.id === "df-ok"));
  await tick();
  return summary({ before, afterUp, afterDown, previewsBeforeApply, result, open: m.open });
};

scenarios.saveFails = async () => {
  saveBad = true;
  const result = await openDurationFix([R("s01", 5, true)]);
  return summary({ result });
};

scenarios.stale = async () => {
  let n = 0;
  apiImpl = (url) => {
    if (url === "/durations/preview") { n++; return n === 1 ? PV("B1", ["s01", 5]) : PV("B2", ["s01", 5], ["s03", 3]); }
    if (url === "/durations/round") {
      if (calls.filter((c) => c.url === "/durations/round").length === 1) throw httpErr(409, { message: "Storyboard vừa thay đổi…", reason: "stale_base" });
      return { changed: true, scenes: [], skipped: [], project: { id: "p1" } };
    }
  };
  let result;
  openDurationFix([R("s01", 5, true)]).then((r) => { result = r; });
  await tick();
  const m = top();
  await click(btn(m, "Làm tròn lên"));
  await click(m.btns.find((b) => b.id === "df-ok"));
  await tick();
  const afterStale = summary({ view: view(m), result });
  await click(m.btns.find((b) => b.id === "df-ok"));
  await tick();
  return { afterStale, end: summary({ result, open: m.open }) };
};

scenarios.manualOnly = async () => {
  let result;
  openDurationFix([R("s02", 7, false, "interrupted"), R("s04", 5, false, "done")]).then((r) => { result = r; });
  await tick();
  const m = top();
  const v = view(m);
  await click(m.btns[0]);
  return summary({ view: v, result });
};

scenarios.previewError = async () => {
  apiImpl = (url) => { if (url === "/durations/preview") throw httpErr(400, { message: "Storyboard còn lỗi: x" }); };
  openDurationFix([R("s01", 5, true)]);
  await tick();
  return summary({ view: view(top()) });
};

scenarios.cancel = async () => {
  apiImpl = () => PV("B1", ["s01", 5]);
  let result;
  openDurationFix([R("s01", 5, true)]).then((r) => { result = r; });
  await tick();
  const m = top();
  await click(btn(m, "Làm tròn lên"));
  await click(btn(m, "Huỷ"));
  return summary({ result });
};

scenarios.emptyPreview = async () => {
  apiImpl = () => PV("B1");
  openDurationFix([R("s01", 5, true)]);
  await tick();
  const m = top();
  const b = btn(m, "Làm tròn lên");
  return summary({ view: view(m), hasSeg: !!b });
};

scenarios.jobRunningApply = async () => {
  apiImpl = (url) => {
    if (url === "/durations/preview") return PV("B1", ["s01", 5]);
    throw httpErr(409, { message: "Đang có job chạy", reason: "job_running" });
  };
  openDurationFix([R("s01", 5, true)]);
  await tick();
  const m = top();
  await click(btn(m, "Làm tròn lên"));
  await click(m.btns.find((b) => b.id === "df-ok"));
  return summary({ open: m.open, view: view(m) });
};

// confirmPaid -------------------------------------------------------------------------------------------
scenarios.retiredFirst = async () => {
  apiImpl = () => EST({ retired: [{ id: "s01" }], bad_duration: [R("s01", 5, true)], renders: [{ id: "s01" }] });
  const r = await confirmPaid({ action: "video" }, {});
  return summary({ result: r, modelChanges });
};

// AC10 + AC11: box instead of credit box; after apply /estimate again, then credit box
scenarios.paidRoundThenCredit = async () => {
  let ests = 0;
  apiImpl = (url) => {
    if (url === "/estimate") { ests++; return ests === 1 ? EST({ bad_duration: [R("s01", 5, true)], renders: [{ id: "s01", mode: "text", model: "veo-lite", duration: 5 }] })
      : EST({ renders: [{ id: "s01", mode: "text", model: "veo-lite", duration: 6 }] }); }
    if (url === "/durations/preview") return PV("B1", ["s01", 5]);
    if (url === "/durations/round") return { changed: true, scenes: [], skipped: [], project: { id: "p1" } };
  };
  const body = { action: "video", only: [], force: [], retry_unknown: true };
  let result;
  confirmPaid(body, {}).then((r) => { result = r; });
  await tick();
  const first = view(top());
  await click(btn(top(), "Làm tròn lên"));
  await click(top().btns.find((b) => b.id === "df-ok"));
  await tick();
  const credit = view(top());
  await click(top().btns.find((b) => b.id === "paid-ok"));
  await tick();
  return summary({ first, credit, result, body });
};

scenarios.paidAgainStillRoundable = async () => {
  apiImpl = (url) => {
    if (url === "/estimate") return EST({ bad_duration: [R("s01", 5, true)], renders: [{ id: "s01" }] });
    if (url === "/durations/preview") return PV("B1", ["s01", 5]);
    if (url === "/durations/round") return { changed: false, scenes: [], skipped: [], project: { id: "p1" } };
  };
  let result;
  confirmPaid({ action: "video" }, {}).then((r) => { result = r; });
  await tick();
  await click(btn(top(), "Làm tròn lên"));
  await click(top().btns.find((b) => b.id === "df-ok"));
  await tick();
  return summary({ result, openNow: !!top() });
};

// AC16: after apply only roundable:false remains -> box shows once more, "Đóng" only, no second round
scenarios.paidAgainManualOnly = async () => {
  let ests = 0;
  apiImpl = (url) => {
    if (url === "/estimate") { ests++; return ests === 1 ? EST({ bad_duration: [R("s01", 5, true), R("s04", 5, false, "done")] })
      : EST({ bad_duration: [R("s04", 5, false, "done")] }); }
    if (url === "/durations/preview") return PV("B1", ["s01", 5]);
    if (url === "/durations/round") return { changed: true, scenes: [], skipped: [], project: { id: "p1" } };
  };
  let result;
  confirmPaid({ action: "video" }, {}).then((r) => { result = r; });
  await tick();
  await click(btn(top(), "Làm tròn lên"));
  await click(top().btns.find((b) => b.id === "df-ok"));
  await tick();
  const second = view(top());
  await click(top().btns[0]);
  await tick();
  return summary({ second, result, openNow: !!top() });
};

// AC13: "Gửi lại" on wrong-length interrupted s02 -> tick disabled, "Render 0 clip" disabled, Huỷ -> false
scenarios.creditResendBad = async () => {
  apiImpl = () => EST({ blocked: ["s02"], blocked_bad_duration: [R("s02", 7, false, "interrupted")] });
  const body = { action: "video", only: ["s02"], force: ["s02"] };
  let result;
  confirmPaid(body, { scene: "s02" }).then((r) => { result = r; });
  await tick();
  const m = top();
  const cb = m.body.all().find((e) => e.tagName === "INPUT");
  const v = view(m);
  await click(btn(m, "Huỷ"));
  return summary({ view: v, cbDisabled: cb.disabled, result, body });
};

// AC13: tick only resends blocked - blocked_bad_duration; count excludes the wrong-length ones
scenarios.creditResendMixed = async () => {
  apiImpl = () => EST({ renders: [{ id: "s01", mode: "text", model: "veo-lite", duration: 6 }], blocked: ["s02", "s04"], blocked_bad_duration: [R("s02", 7, false, "interrupted")] });
  const body = { action: "video", only: [], force: [] };
  let result;
  confirmPaid(body, {}).then((r) => { result = r; });
  await tick();
  const m = top();
  const cb = m.body.all().find((e) => e.tagName === "INPUT");
  const label0 = m.btns[1].text;
  cb.checked = true; cb.fire("change");
  const label1 = m.btns[1].text, resend1 = [...body.confirm_resend];
  cb.checked = false; cb.fire("change");
  const resend0 = [...body.confirm_resend];
  cb.checked = true; cb.fire("change");
  await click(m.btns[1]);
  return summary({ cbDisabled: cb.disabled, label0, label1, resend1, resend0, result, body });
};

// AC13: whole film after rounding s01, s02 still wrong & interrupted
scenarios.creditWholeFilm = async () => {
  apiImpl = () => EST({ renders: [{ id: "s01", mode: "text", model: "veo-lite", duration: 6 }], blocked: ["s02"], blocked_bad_duration: [R("s02", 7, false, "interrupted")] });
  const body = { action: "video", only: [], force: [] };
  let result;
  confirmPaid(body, {}).then((r) => { result = r; });
  await tick();
  const m = top();
  const cb = m.body.all().find((e) => e.tagName === "INPUT");
  const v = view(m);
  await click(m.btns[1]);
  return summary({ view: v, cbDisabled: cb.disabled, result, body });
};

// runAction ---------------------------------------------------------------------------------------------
const runEst = () => EST({ renders: [{ id: "s02", mode: "text", model: "veo-lite", duration: 7 }] });
scenarios.run409Manual = async () => {
  apiImpl = (url) => {
    if (url === "/estimate") return runEst();
    if (url === "/run") throw httpErr(409, { message: "Không thể render", reason: "bad_duration", bad_duration: [R("s02", 7, false, "interrupted")] });
  };
  const p = runAction("video", { only: ["s02"], force: ["s02"] }, { scene: "s02" });
  await tick();
  await click(top().btns.find((b) => b.id === "paid-ok"));
  await tick();
  const box = view(top());
  await click(top().btns[0]);
  await p;
  return summary({ box, jobs, openNow: !!top() });
};

scenarios.run409Roundable = async () => {
  apiImpl = (url) => {
    if (url === "/estimate") return runEst();
    if (url === "/run") throw httpErr(409, { message: "Không thể render", reason: "bad_duration", bad_duration: [R("s02", 7, true)] });
    if (url === "/durations/preview") return PV("B1", ["s02", 7]);
    if (url === "/durations/round") return { changed: true, scenes: [], skipped: [], project: { id: "p1" } };
  };
  const p = runAction("video", { only: ["s02"] }, { scene: "s02" });
  await tick();
  await click(top().btns.find((b) => b.id === "paid-ok"));
  await tick();
  await click(btn(top(), "Làm tròn xuống"));
  await click(top().btns.find((b) => b.id === "df-ok"));
  await p;
  return summary({ jobs, openNow: !!top() });
};

(async () => {
  const out = {};
  for (const [k, f] of Object.entries(scenarios)) { reset(); out[k] = await f(); }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e.stack || e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def js(tmp_path_factory):
    if not shutil.which("node"):
        pytest.skip("needs node")
    d = tmp_path_factory.mktemp("ui")
    (d / "harness.js").write_text(HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(d / "harness.js"), str(APP)], capture_output=True, text=True, cwd=d, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_pure_helpers(js):  # AC10, AC13, AC28
    h = js["helpers"]
    assert h["row"] == "7 giây — veo-lite chỉ nhận 4, 6, 8"
    assert h["interrupted"] == ("bị ngắt khi render, có thể Flow đã trừ credit — app không tự đổi độ dài; kiểm tra Flow/tải "
                                "clip về clips/s02.mp4, hoặc sửa tay độ dài trong editor")
    assert h["done"] == "đã có clip — sửa tay độ dài trong editor rồi bấm Render lại"
    assert h["other"] == ""
    assert h["resend"] == ["s04", "s06"] and h["resendNone"] == [] and h["resendOld"] == ["s02"]
    assert h["bbd"] == ("Cảnh s02: 7 giây — veo-lite chỉ nhận 4, 6, 8. Bị ngắt nên app không tự làm tròn: kiểm tra Flow / "
                        "tải clip về clips/s02.mp4, hoặc sửa tay độ dài trong editor rồi bấm lại.")
    assert h["why"] == ["Flow báo đang xếp hàng", "Lỗi khác", "", "", "", ""]
    assert h["whyLong"] == "x" * 299 + "…" and len(h["whyLong"]) == 300
    assert h["why300"] == "y" * 300  # exactly 300 is kept whole


def test_line_class(js):  # command lines with "Error" in the prompt are not error lines
    assert js["helpers"]["lines"] == ["l-cmd", "l-err", "l-err", "l-ok", "l-warn", "", ""]


def test_scene_cards(js):  # AC20, AC28
    c = js["cards"]
    warn = [t for k, t in c["todo"]["msgs"] if "warn" in k]
    assert warn == ["7 giây — veo-lite chỉ nhận 4, 6, 8 — bấm Render để làm tròn, hoặc chọn độ dài khác"]
    assert c["ok"]["msgs"] == [] and c["done"]["msgs"] == []  # null flag -> no line
    i = c["intr"]["msgs"]
    texts = [t for _, t in i]
    assert texts[0].startswith("Lần trước bị ngắt khi đang render, có thể Flow đã trừ credit")
    assert i[1][0] == "scene-msg small" and texts[1].startswith("Lý do lần trước: Flow báo đang xếp hàng")
    assert "Flow (giao diện chat)" not in texts[1] and len(texts[1]) == len("Lý do lần trước: ") + 300
    assert texts[2] == "7 giây — veo-lite chỉ nhận 4, 6, 8 — bị ngắt nên app không tự làm tròn; sửa tay độ dài hoặc tải clip về"
    assert not any("Lý do lần trước" in t for _, t in c["intrNoErr"]["msgs"])  # empty error -> no reason line
    # a hand-downloaded clip that will be taken in hides the duration advice
    m = [t for _, t in c["intrManual"]["msgs"]]
    assert any(t.startswith("Đã thấy clip bạn tải về") for t in m)
    assert not any("chỉ nhận" in t for t in m)


def test_duration_fix_apply(js):  # AC10, AC11, AC12
    r = js["applyFlow"]
    assert r["calls"] == ["POST /durations/preview", "POST /durations/round"]  # preview exactly once
    assert r["flushes"] == 1 and r["previewsBeforeApply"] == 1
    assert r["bodies"][1] == {"direction": "up", "base": "B1"}
    assert r["result"] == "applied" and r["open"] is False and r["reloads"] == [{"id": "p1", "marker": "new"}]
    b = r["before"]
    assert b["title"] == "Độ dài không hợp lệ"
    assert "Áp dụng sẽ đổi độ dài 2 cảnh trong cả phim" in b["text"]  # N = preview rows, not the 1 row passed in
    assert "Dù bạn chỉ render cảnh s01" in b["text"]
    assert [x["label"] for x in b["btns"]] == ["Huỷ", "Áp dụng"]
    assert b["btns"][1]["id"] == "df-ok" and b["btns"][1]["disabled"] is True  # no direction yet
    assert b["rows"] == [["s01", "veo-lite", "5 giây", "4, 6, 8", "lên 6 / xuống 4"],
                         ["s02", "veo-lite", "7 giây", "4, 6, 8", "lên 8 / xuống 6"]]
    assert r["afterUp"]["btns"][1]["disabled"] is False
    assert [row[-1] for row in r["afterUp"]["rows"]] == ["6 giây", "8 giây"]
    assert [row[-1] for row in r["afterDown"]["rows"]] == ["4 giây", "6 giây"]


def test_duration_fix_save_failed(js):  # AC11: unsaved editor -> no API call
    r = js["saveFails"]
    assert r["result"] == "closed" and r["calls"] == [] and r["modalCount"] == 0
    assert r["toasts"] == [{"msg": "Chưa lưu được storyboard: disk full", "kind": "error"}]


def test_duration_fix_stale_base(js):  # AC11: 409 stale_base -> preview again, never round on its own
    a = js["stale"]["afterStale"]
    assert a["calls"] == ["POST /durations/preview", "POST /durations/round", "POST /durations/preview"]
    assert a.get("result") is None and a["view"]["open"] is True  # still open, not resolved
    assert "Storyboard vừa thay đổi — đã tải lại bảng, xem lại rồi bấm Áp dụng" in a["view"]["text"]
    assert "Áp dụng sẽ đổi độ dài 2 cảnh trong cả phim" in a["view"]["text"]  # table refreshed
    e = js["stale"]["end"]
    assert e["calls"][-1] == "POST /durations/round" and e["bodies"][-1] == {"direction": "up", "base": "B2"}
    assert e["result"] == "applied"


def test_duration_fix_manual_only(js):  # AC10, AC16: no preview, only "Đóng"
    r = js["manualOnly"]
    assert r["calls"] == [] and r["flushes"] == 0 and r["result"] == "closed"
    v = r["view"]
    assert [b["label"] for b in v["btns"]] == ["Đóng"] and v["rows"] == []
    assert "Cần bạn tự xử lý" in v["text"] and "Làm tròn lên" not in v["text"]
    assert ("s02: 7 giây — veo-lite chỉ nhận 4, 6, 8. Bị ngắt khi render, có thể Flow đã trừ credit") in v["text"]
    assert "s04: 5 giây — veo-lite chỉ nhận 4, 6, 8. Đã có clip — sửa tay độ dài trong editor rồi bấm Render lại" in v["text"]


def test_duration_fix_preview_error(js):  # AC11: preview error -> error shown, no "Áp dụng"
    v = js["previewError"]["view"]
    assert "Không xem trước được: Storyboard còn lỗi: x" in v["text"]
    ok = [b for b in v["btns"] if b["id"] == "df-ok"][0]
    assert ok["hidden"] is True and ok["disabled"] is True


def test_duration_fix_cancel_and_empty(js):
    assert js["cancel"]["result"] == "closed" and js["cancel"]["calls"] == ["POST /durations/preview"]
    e = js["emptyPreview"]
    assert "Không còn cảnh nào cần làm tròn" in e["view"]["text"] and not e["hasSeg"]
    assert [b for b in e["view"]["btns"] if b["id"] == "df-ok"][0]["disabled"] is True


def test_duration_fix_job_running(js):
    r = js["jobRunningApply"]
    assert r["open"] is True and r["reloads"] == []
    assert r["toasts"] == [{"msg": "Đang có job chạy", "kind": "error"}]
    assert r["view"]["btns"][1]["disabled"] is False  # can try again once the job ends


def test_confirm_paid_retired_first(js):  # AC10
    r = js["retiredFirst"]
    assert r["result"] is False and r["calls"] == ["POST /estimate"] and r["modalCount"] == 0
    assert r["modelChanges"] == [{"include": ["s01"], "blockedCount": 1}]


def test_confirm_paid_rounds_then_credit(js):  # AC10, AC11
    r = js["paidRoundThenCredit"]
    assert r["calls"] == ["POST /estimate", "POST /durations/preview", "POST /durations/round", "POST /estimate"]
    assert r["first"]["title"] == "Độ dài không hợp lệ"
    assert r["credit"]["title"] == "Xác nhận render video" and r["credit"]["btns"][1]["label"] == "Render 1 clip"
    assert r["result"] is True
    assert r["body"]["confirm_resend"] == [] and "retry_unknown" not in r["body"]


def test_confirm_paid_again_still_roundable(js):  # minor 7: no endless loop
    r = js["paidAgainStillRoundable"]
    assert r["result"] is False and r["modalCount"] == 1 and not r["openNow"]
    assert r["calls"] == ["POST /estimate", "POST /durations/preview", "POST /durations/round", "POST /estimate"]
    assert r["toasts"][-1]["kind"] == "error" and r["toasts"][-1]["msg"].startswith("Vẫn còn cảnh có độ dài model không nhận")


def test_confirm_paid_again_manual_only(js):  # AC16: shown once more, "Đóng" only, no second round
    r = js["paidAgainManualOnly"]
    assert r["result"] is False and r["modalCount"] == 2 and not r["openNow"]
    assert r["calls"] == ["POST /estimate", "POST /durations/preview", "POST /durations/round", "POST /estimate"]
    s = r["second"]
    assert [b["label"] for b in s["btns"]] == ["Đóng"]
    assert "Đã làm tròn xong, nhưng vẫn còn cảnh app không tự sửa được." in s["text"]


def test_credit_box_resend_bad_only(js):  # AC13, AC16: "Gửi lại" on s02
    r = js["creditResendBad"]
    assert r["cbDisabled"] is True and r["result"] is False
    assert r["view"]["btns"][1] == {"label": "Render 0 clip", "id": "paid-ok", "disabled": True, "hidden": False}
    assert "Cảnh s02: 7 giây — veo-lite chỉ nhận 4, 6, 8. Bị ngắt nên app không tự làm tròn" in r["view"]["text"]
    assert r["calls"] == ["POST /estimate"] and r["body"]["confirm_resend"] == []


def test_credit_box_resend_mixed(js):  # AC13: tick = blocked - blocked_bad_duration
    r = js["creditResendMixed"]
    assert r["cbDisabled"] is False
    assert r["label0"] == "Render 1 clip" and r["label1"] == "Render 2 clip"
    assert r["resend1"] == ["s04"] and r["resend0"] == []
    assert r["result"] is True and r["body"]["confirm_resend"] == ["s04"]


def test_credit_box_whole_film(js):  # AC13: whole film after rounding s01
    r = js["creditWholeFilm"]
    assert r["cbDisabled"] is True
    assert r["view"]["btns"][1]["label"] == "Render 1 clip" and r["view"]["btns"][1]["disabled"] is False
    assert r["view"]["rows"][0][0] == "s01"
    assert r["result"] is True and r["body"]["confirm_resend"] == []


def test_run_action_409_manual(js):  # AC16: 409 from /run -> box with "Đóng" only, no re-run, no round
    r = js["run409Manual"]
    assert r["calls"] == ["POST /estimate", "POST /run"] and r["jobs"] == []
    assert [b["label"] for b in r["box"]["btns"]] == ["Đóng"] and "Cần bạn tự xử lý" in r["box"]["text"]
    assert not r["openNow"]
    assert r["bodies"][1]["confirm_resend"] == []


def test_run_action_409_roundable(js):  # AC10: applied -> toast, /run not called again
    r = js["run409Roundable"]
    assert r["calls"] == ["POST /estimate", "POST /run", "POST /durations/preview", "POST /durations/round"]
    assert r["bodies"][3] == {"direction": "down", "base": "B1"}
    assert r["jobs"] == [] and not r["openNow"]
    assert {"msg": "Đã làm tròn độ dài. Bấm Render khi sẵn sàng.", "kind": "ok"} in r["toasts"]
