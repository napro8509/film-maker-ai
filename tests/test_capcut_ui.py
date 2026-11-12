"""UI side of the CapCut export, without a browser: editPlanView and runAction cut out of app.js and run under node
with a tiny fake `h` and fake api / toast / job helpers."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "filmmaker" / "web" / "static" / "app.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")

HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const fn = (name) => { const m = src.match(new RegExp("^(?:async )?function " + name + "\\(.*?^}\\n", "ms")); if (!m) throw new Error(name); return m[0]; };

const h = (tag, props, ...kids) => ({ tag, props: props || {}, kids: kids.flat(Infinity).filter((k) => k != null && k !== false) });
const all = (n) => (n && n.tag ? [n, ...n.kids.flatMap(all)] : []);
const text = (n) => (n && n.tag ? n.kids.map(text).join("") : String(n ?? ""));
const icon = () => null, FREE = () => null, PAID = () => null, fmtDur = (s) => s + "s", NATIVE = () => true;
let S, calls = [], toasts = [], runs = [], jobs = [], zones = 0, running = false;
const jobRunning = () => running, refreshZone = () => { zones++; }, toast = (m, k = "") => toasts.push({ m, k });
const api = async (method, url, body) => { calls.push({ method, url, body: JSON.parse(JSON.stringify(body ?? null)) });
  return method === "PUT" ? { ...S.edit.data, plan: body } : { id: "j1" }; };
const flushSave = async () => {}, saveFailed = () => false, prettyError = (e) => e, confirmModal = async () => true;
const confirmPaid = async () => true, startJob = (j) => jobs.push(j), loadEditPlan = () => {};
const finalPrefs = () => ({ music: "assets/m.mp3", volume: 0.3, burn_subtitles: true, allow_missing: false, smart_edit: true });

const view = () => ({ plan: { source: "ai", notes: "", fade_in: 0.5, fade_out: 1, even_loudness: true, duck_music: true, normalize: true,
    clips: [{ id: "s01", trim_start: 0, trim_end: 0, volume: 1 }, { id: "s02", trim_start: 0, trim_end: 0, volume: 1 }, { id: "s03", trim_start: 0, trim_end: 0, volume: 1 }],
    transitions: [{ after: "s01", type: "crossfade", duration: 0.5, reason: "", capcut: null }, { after: "s02", type: "cut", duration: 0, reason: "", capcut: null }] },
  transitions: [{ id: "cut", label: "Cắt thẳng" }, { id: "crossfade", label: "Hoà tan (crossfade)" }, { id: "fadeblack", label: "Mờ qua đen" }],
  capcut_transitions: [{ id: "dissolve", label: "Hoà tan" }, { id: "ink", label: "Loang mực" }], capcut_default: { cut: null, crossfade: "dissolve" },
  clips: {}, total: 12 });
const reset = (capcut = true) => { S = { cur: { id: "p1", errors: [], storyboard: { scenes: [] } }, env: { gflow: false, ffmpeg: true, capcut }, tab: "final" };
  S.edit = { pid: "p1", data: view(), dirty: false }; calls = []; toasts = []; jobs = []; };

eval(fn("editPlanView"));
const realRunAction = eval("(" + fn("runAction").replace(/^async function runAction/, "async function") + ")");
let runAction = async (action, extra = {}) => { runs.push([action, extra]); };

(async () => {
  const out = {};
  reset();
  let tree = editPlanView();
  const capSel = () => all(tree).filter((n) => n.tag === "select" && /CapCut/.test(n.props["aria-label"] || ""));
  out.selects = capSel().map((n) => n.props["aria-label"]);
  out.options = capSel()[0].kids.map(text);
  capSel()[0].props.onchange({ target: { value: "ink" } });
  out.picked = [S.edit.data.plan.transitions[0].capcut, S.edit.dirty];
  tree = editPlanView();
  const typeSel = all(tree).find((n) => n.tag === "select" && n.props["aria-label"] === "Chuyển cảnh sau s01");
  typeSel.props.onchange({ target: { value: "fadeblack" } });
  out.cleared = S.edit.data.plan.transitions[0].capcut;
  tree = editPlanView();
  const btn = all(tree).find((n) => n.tag === "button" && /CapCut/.test(text(n)));
  out.button = text(btn);
  await btn.props.onclick();
  out.saved = [calls[0].method, calls[0].url, calls[0].body.transitions[0].type];
  out.ran = runs.map((r) => r[0]);

  reset();
  out.basic_rows = all(editPlanView()).filter((n) => n.tag === "select" && /Chuyển động máy/.test(n.props["aria-label"] || "")).length;
  Object.assign(S.edit.data.plan, { pro: true, look: "冷蓝", look_strength: 0.5, title: null, music_query: "sad piano" });
  S.edit.data.plan.clips.forEach((c) => Object.assign(c, { motion: "none", effect: null, filter: null, music: 1, sounds: [], note: "" }));
  S.edit.data.plan.clips[0].sounds = [{ query: "door slam", at: 1, volume: 0.6 }];
  S.edit.data.capcut_filters = ["冷蓝", "BW 2"]; S.edit.data.capcut_effects = ["胶片"]; S.edit.data.credits = "Nhạc: x";
  tree = editPlanView();
  const byLabel = (l) => all(tree).find((n) => n.props["aria-label"] === l);
  out.pro_rows = all(tree).filter((n) => n.tag === "select" && /Chuyển động máy/.test(n.props["aria-label"] || "")).length;
  byLabel("Chuyển động máy s01").props.onchange({ target: { value: "push_in" } });
  byLabel("Hiệu ứng hình s02").props.onchange({ target: { value: "胶片" } });
  byLabel("Mức nhạc nền s02").props.onchange({ target: { value: "35" } });
  byLabel("Màu phim").props.onchange({ target: { value: "" } });
  byLabel("Tiêu đề mở đầu").props.onchange({ target: { value: " Di chúc " } });
  all(tree).find((n) => n.tag === "span" && /door slam/.test(text(n))).props.onclick();
  const P = S.edit.data.plan;
  out.pro_edit = [P.clips[0].motion, P.clips[1].effect, P.clips[1].music, P.look, P.title, P.clips[0].sounds.length];
  out.credits = all(tree).some((n) => n.tag === "pre" && text(n) === "Nhạc: x");

  reset(false);
  out.no_capcut_button = all(editPlanView()).some((n) => n.tag === "button" && /CapCut/.test(text(n)));

  reset();
  await realRunAction("capcut");
  out.post = calls.map((c) => [c.method, c.url, c.body.action, c.body.music, c.body.burn_subtitles]);
  out.toasts = toasts;
  reset(); S.env.ffmpeg = false;
  await realRunAction("capcut");
  out.no_ffmpeg = [calls.length, toasts[0].k];
  console.log(JSON.stringify(out));
})();
"""


def test_capcut_controls_in_the_edit_plan_and_run_action(tmp_path):
    js = tmp_path / "h.js"
    js.write_text(HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(js), str(APP)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["selects"] == ["Chuyển cảnh CapCut sau s01"]  # a straight cut has no effect to pick
    assert out["options"] == ["CapCut: Hoà tan", "CapCut: Hoà tan", "CapCut: Loang mực"]  # first = automatic choice
    assert out["picked"] == ["ink", True]
    assert out["cleared"] is None  # a new basic transition drops the old CapCut pick
    assert out["button"] == "Lưu & mở bằng CapCut"
    assert out["saved"] == ["PUT", "/api/projects/p1/edit-plan", "fadeblack"] and out["ran"] == ["capcut"]
    assert out["no_capcut_button"] is False
    # The finishing layer shows only on a plan that has it, and every control writes back into the plan.
    assert out["basic_rows"] == 0 and out["pro_rows"] == 3
    assert out["pro_edit"] == ["push_in", "胶片", 0.35, None, "Di chúc", 0] and out["credits"] is True
    # No gflow needed (nothing is sent to Flow); the film settings ride along.
    assert out["post"] == [["POST", "/api/projects/p1/run", "capcut", "assets/m.mp3", True]] and out["toasts"] == []
    assert out["no_ffmpeg"] == [0, "error"]


def test_final_tab_has_the_capcut_button():
    src = APP.read_text(encoding="utf-8")
    # With CapCut the main build button is the full editor; the ffmpeg cut stays as the quick option.
    assert 'onclick: () => runAction("capcut")' in src and "AI dựng phim (CapCut)" in src
    assert "Dựng nhanh (ffmpeg)" in src and "S.env.capcut" in src
