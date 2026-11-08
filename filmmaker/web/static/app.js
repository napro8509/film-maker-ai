"use strict";
/* Film Maker UI — vanilla JS, no build step. */

const TOKEN = document.querySelector('meta[name="token"]').content;
const S = {
  env: null, settings: null, models: { video: [], image: [] }, projects: [], cur: null, tab: "scenes",
  assets: { images: [], audio: [] }, job: null, jobSince: 0, jobOpen: false, pollT: null,
  saveTimer: null, saving: false, pendingSave: false, saveState: "saved", saveError: "", auth: null,
  zones: new Map(), playIdx: 0,
};
const MODES = [
  ["keyframe", "Ảnh → Video", "Tạo ảnh mở đầu trước (miễn phí), duyệt rồi mới render. Giữ nhân vật đồng nhất nhất."],
  ["t2v", "Chữ → Video", "Tạo thẳng video từ mô tả."],
  ["i2v", "Ảnh của bạn → Video", "Làm chuyển động một ảnh bạn tải lên."],
];
const STATUS = {
  done: ["done", "✔ Xong"], changed: ["changed", "↻ Đã sửa · cần render lại"], interrupted: ["interrupted", "⚠ Bị ngắt khi render"],
  failed: ["failed", "✘ Lỗi"], todo: ["todo", "· Chưa làm"], running: ["running", "⏳ Đang làm"],
};

/* ---------- utils ---------- */

async function api(method, url, body, isForm) {
  const opt = { method, headers: {} };
  if (method !== "GET") opt.headers["X-Token"] = TOKEN;
  if (body !== undefined) {
    if (isForm) opt.body = body;
    else { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  }
  const r = await fetch(url, opt);
  let data = null;
  try { data = await r.json(); } catch { /* empty */ }
  if (!r.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === "string" ? d : d ? JSON.stringify(d) : `Lỗi máy chủ (HTTP ${r.status})`);
  }
  return data;
}

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  let value;
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") value = v;
    else if (k === "checked") el.checked = !!v;
    else if (k === "disabled") el.disabled = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  if (value !== undefined) el.value = value;
  return el;
}

const $ = (sel) => document.querySelector(sel);
function put(el, ...kids) { el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false)); return el; }
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function localGet(k, d = null) { try { const v = localStorage.getItem("fm." + k); return v == null ? d : JSON.parse(v); } catch { return d; } }
function localSet(k, v) { try { localStorage.setItem("fm." + k, JSON.stringify(v)); } catch { /* private mode */ } }
function fmtBytes(n) { return n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.round(n / 1e3) + " KB"; }
function fmtDur(sec) { sec = Math.round(sec); const m = Math.floor(sec / 60), s = sec % 60; return m ? `${m}p ${String(s).padStart(2, "0")}s` : `${s}s`; }
function ago(ts) {
  const d = (Date.now() / 1000 - ts);
  if (d < 60) return "vừa xong"; if (d < 3600) return Math.floor(d / 60) + " phút trước";
  if (d < 86400) return Math.floor(d / 3600) + " giờ trước"; return Math.floor(d / 86400) + " ngày trước";
}

function toast(msg, kind = "", ms = 4000) {
  const t = h("div", { class: "toast " + kind }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), ms);
}

function modal({ title, body, buttons = [], wide = false, onClose }) {
  const root = $("#modal-root");
  const close = () => { ov.remove(); document.removeEventListener("keydown", onKey); onClose && onClose(); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const foot = buttons.length ? h("div", { class: "modal-foot" }, buttons.map((b) =>
    h("button", { class: "btn " + (b.cls || ""), disabled: b.disabled, id: b.id, onclick: () => b.onclick ? b.onclick(close) : close() }, b.label))) : null;
  const ov = h("div", { class: "overlay", onmousedown: (e) => { if (e.target === ov) close(); } },
    h("div", { class: "modal" + (wide ? " wide" : "") },
      h("div", { class: "modal-head" }, h("h2", {}, title), h("button", { class: "btn ghost icon", onclick: close, "aria-label": "Đóng" }, "✕")),
      h("div", { class: "modal-body" }, body), foot));
  root.append(ov);
  document.addEventListener("keydown", onKey);
  return close;
}

function confirmModal(title, text, okLabel = "Đồng ý", danger = false) {
  return new Promise((resolve) => {
    let done = false;
    modal({
      title, body: h("p", {}, text), onClose: () => { if (!done) resolve(false); },
      buttons: [
        { label: "Huỷ" },
        { label: okLabel, cls: danger ? "danger" : "primary", onclick: (c) => { done = true; resolve(true); c(); } },
      ],
    });
  });
}

function lightbox(url) {
  const ov = h("div", { class: "overlay lightbox", onclick: () => ov.remove() }, h("img", { src: url }));
  $("#modal-root").append(ov);
}

/* "zones" are small parts of the page that re-render when status changes, without touching form inputs. */
function zone(id, fn) {
  S.zones.set(id, fn);
  const el = fn();
  el.id = id;
  return el;
}
function refreshZones() {
  for (const [id, fn] of S.zones) {
    const old = document.getElementById(id);
    if (!old) { S.zones.delete(id); continue; }
    // Do not interrupt a clip the user is watching.
    const v = old.querySelector("video");
    if (v && !v.paused) continue;
    const el = fn();
    el.id = id;
    if (old.outerHTML !== el.outerHTML) old.replaceWith(el);
  }
}
function refreshZone(id) {
  const fn = S.zones.get(id), old = document.getElementById(id);
  if (!fn || !old) return;
  const el = fn(); el.id = id;
  if (old.outerHTML !== el.outerHTML) old.replaceWith(el);
}

/* ---------- boot ---------- */

async function init() {
  $("#btn-settings").onclick = openSettings;
  $("#btn-credits").onclick = openCredits;
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); flushSave().then(() => toast("Đã lưu", "ok", 1500)); }
  });
  window.addEventListener("beforeunload", (e) => { if (S.pendingSave || S.saving) { save(); e.preventDefault(); e.returnValue = ""; } });
  window.addEventListener("hashchange", () => {
    const id = decodeURIComponent(location.hash.slice(1));
    if (id && (!S.cur || S.cur.id !== id) && S.projects.some((p) => p.id === id)) openProject(id);
  });

  try {
    [S.env, S.settings] = await Promise.all([api("GET", "/api/env"), api("GET", "/api/settings")]);
  } catch (e) {
    $("#main").append(h("div", { class: "banner err" }, "Không kết nối được máy chủ: " + e.message));
    return;
  }
  S.auth = localGet("auth");
  renderTopbar();
  api("GET", "/api/models").then((m) => { S.models = m; if (S.cur && S.tab === "story") renderMain(); }).catch(() => {});
  await loadProjects();
  const want = decodeURIComponent(location.hash.slice(1)) || localGet("lastProject");
  const pick = S.projects.some((p) => p.id === want) ? want : S.projects[0]?.id;
  if (pick) await openProject(pick); else renderMain();
  const j = await api("GET", "/api/jobs/current").catch(() => null);
  if (j) { S.job = { ...j, lines: [] }; S.jobSince = 0; S.jobOpen = j.status === "running"; renderJobbar(); pollJob(); }
  setInterval(tickElapsed, 1000);
}

async function loadProjects() {
  S.projects = await api("GET", "/api/projects");
  renderSidebar();
}

async function openProject(id) {
  await flushSave();
  try {
    const [d, a] = await Promise.all([api("GET", `/api/projects/${id}`), api("GET", `/api/projects/${id}/assets`)]);
    S.cur = d; S.assets = a; S.playIdx = 0;
  } catch (e) { toast(e.message, "error"); return; }
  localSet("lastProject", id);
  if (location.hash.slice(1) !== id) history.replaceState(null, "", "#" + id);
  S.saveState = "saved";
  renderSidebar();
  renderMain();
}

async function refreshCurrent() {
  if (!S.cur) return;
  try {
    const d = await api("GET", `/api/projects/${S.cur.id}`);
    if (S.cur && S.cur.id === d.id) applyMeta(d);
  } catch { /* project may have been deleted */ }
}

function applyMeta(d) {
  const sb = S.cur.storyboard;
  Object.assign(S.cur, d);
  S.cur.storyboard = sb; // keep the local copy the user is editing
  refreshZones();
  const p = S.projects.find((x) => x.id === d.id);
  if (p) { Object.assign(p, { title: d.title, scenes: d.scenes, done: d.done, has_final: d.has_final, thumb: d.thumb }); renderSidebar(); }
}

/* ---------- saving ---------- */

function changed() {
  S.pendingSave = true;
  setSaveState("pending");
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(save, 700);
}

async function save() {
  if (!S.cur || !S.pendingSave) return;
  if (S.saving) { clearTimeout(S.saveTimer); S.saveTimer = setTimeout(save, 300); return; }
  S.saving = true; S.pendingSave = false; setSaveState("saving");
  const pid = S.cur.id;
  try {
    const d = await api("PUT", `/api/projects/${pid}`, { storyboard: S.cur.storyboard });
    if (S.cur && S.cur.id === pid) applyMeta(d);
    setSaveState(S.pendingSave ? "pending" : "saved");
  } catch (e) {
    S.pendingSave = true;
    setSaveState("error", e.message);
  } finally { S.saving = false; }
}

async function flushSave() {
  clearTimeout(S.saveTimer);
  while (S.saving) await new Promise((r) => setTimeout(r, 50));
  if (S.pendingSave) await save();
}

function setSaveState(st, err = "") {
  S.saveState = st; S.saveError = err;
  const el = document.getElementById("savestate");
  if (el) { const n = renderSaveState(); n.id = "savestate"; el.replaceWith(n); }
}
function renderSaveState() {
  const t = { saved: "✓ Đã lưu", pending: "Chưa lưu…", saving: "Đang lưu…", error: "Lỗi lưu: " + S.saveError }[S.saveState];
  return h("span", { class: "savestate" + (S.saveState === "error" ? " error" : "") }, t);
}

/* ---------- top bar ---------- */

function renderTopbar() {
  const e = S.env, box = $("#envchips");
  put(box, 
    h("span", { class: "chip " + (e.gflow ? "ok" : "bad"), title: e.gflow || "Chưa cài gflow-cli" }, e.gflow ? "✔ gflow" : "✘ gflow"),
    h("span", { class: "chip " + (e.ffmpeg ? "ok" : "bad"), title: e.ffmpeg || "Chưa cài ffmpeg" }, e.ffmpeg ? "✔ ffmpeg" : "✘ ffmpeg"),
    authChip(),
    h("span", { class: "chip " + (e.anthropic_key ? "ok" : "warn"), title: "Dùng để viết storyboard bằng AI" },
      e.anthropic_key ? "✔ Claude AI" : "Claude AI: chưa có key"),
  );
}

function authChip() {
  const a = S.auth;
  if (a === "checking") return h("span", { class: "chip" }, h("span", { class: "spinner" }), " Đang kiểm tra Google…");
  if (!a) return h("button", { class: "chip", onclick: checkAuth, title: "Mở Flow ngầm để kiểm tra phiên đăng nhập" }, "Google: bấm để kiểm tra");
  const when = a.at ? ` (${ago(a.at)})` : "";
  return h("button", { class: "chip " + (a.ok ? "ok" : "bad"), onclick: checkAuth, title: (a.text || "") + "\n\nBấm để kiểm tra lại" },
    a.ok ? `✔ ${a.account || "Đã đăng nhập Google"}${when}` : "✘ Chưa đăng nhập Google");
}

async function checkAuth() {
  S.auth = "checking"; renderTopbar();
  try {
    const r = await api("POST", "/api/env/auth");
    S.auth = { ...r, at: Date.now() / 1000 };
    localSet("auth", S.auth);
    if (!r.ok) {
      modal({
        title: "Chưa đăng nhập Google Flow",
        body: h("div", { class: "stack" }, h("p", {}, "gflow cần đăng nhập tài khoản Google có quyền dùng Flow."), h("pre", { class: "box" }, r.text || "")),
        buttons: [{ label: "Đóng" }, { label: "Mở Chrome để đăng nhập", cls: "primary", onclick: (c) => { c(); startLogin(); } }],
      });
    }
  } catch (e) { S.auth = null; toast(e.message, "error"); }
  renderTopbar();
}

async function startLogin() {
  try {
    await api("POST", "/api/env/login");
    toast("Đã mở cửa sổ đăng nhập. Đăng nhập xong, bấm vào chip Google để kiểm tra lại.", "", 8000);
    S.auth = null; localSet("auth", null); renderTopbar();
  } catch (e) { toast(e.message, "error"); }
}

/* ---------- sidebar ---------- */

function renderSidebar() {
  const sb = $("#sidebar");
  const running = S.job && S.job.status === "running" ? S.job.project : null;
  put(sb, 
    h("button", { class: "btn primary", style: { width: "100%" }, onclick: openNewProject }, "＋ Phim mới"),
    h("div", { class: "side-title" }, `Dự án (${S.projects.length})`),
    S.projects.length ? null : h("p", { class: "muted small", style: { padding: "0 4px" } }, "Chưa có dự án nào."),
    S.projects.map((p) => {
      const pct = p.scenes ? Math.round((p.done / p.scenes) * 100) : 0;
      return h("div", { class: "proj" + (S.cur && S.cur.id === p.id ? " active" : ""), onclick: () => openProject(p.id), title: p.title },
        h("div", { class: "thumb", style: p.thumb ? { backgroundImage: `url("${p.thumb}")` } : null }, p.thumb ? "" : "🎞"),
        h("div", { class: "meta" },
          h("div", { class: "name" }, p.title),
          h("div", { class: "sub" }, running === p.id ? "⏳ đang chạy · " : "", p.has_final ? "🎬 có phim · " : "", `${p.done}/${p.scenes} cảnh`),
          h("div", { class: "bar" }, h("i", { style: { width: pct + "%" } }))));
    }),
    h("div", { class: "side-title" }, "Thư mục"),
    h("div", { class: "small muted mono", style: { padding: "0 4px", wordBreak: "break-all" } }, S.env ? S.env.workspace : ""),
  );
}

/* ---------- main ---------- */

function renderMain() {
  const m = $("#main");
  S.zones.clear();
  if (!S.cur) { put(m, renderEmpty()); return; }
  put(m, 
    renderHeader(),
    zone("errors-zone", renderErrors),
    h("div", { class: "tabs" },
      [["scenes", `🎬 Cảnh (${S.cur.storyboard.scenes?.length || 0})`], ["story", "🎭 Nhân vật & phong cách"], ["final", "🎞 Phim hoàn chỉnh"], ["yaml", "📝 YAML"]]
        .map(([k, l]) => h("button", { class: S.tab === k ? "on" : "", onclick: () => switchTab(k) }, l))),
    S.tab === "scenes" ? renderScenes() : S.tab === "story" ? renderStory() : S.tab === "final" ? renderFinal() : renderYaml(),
  );
}

async function switchTab(k) {
  if (k === "yaml") { await flushSave(); await refreshCurrent(); }
  S.tab = k;
  renderMain();
}

function renderEmpty() {
  return h("div", { class: "empty" },
    h("div", { style: { fontSize: "44px" } }, "🎬"),
    h("h1", {}, "Làm phim bằng Google Flow"),
    h("p", { class: "muted" }, "Từ một ý tưởng ra một bộ phim ngắn: viết kịch bản, tạo từng cảnh, rồi ghép lại."),
    h("div", { class: "steps" },
      [["Tạo dự án", "Viết storyboard bằng AI từ một câu ý tưởng, hoặc tự viết từng cảnh."],
        ["Tạo ảnh keyframe", "Miễn phí. Xem trước ảnh mở đầu của từng cảnh, sửa prompt đến khi ưng."],
        ["Render video", "Tốn credit. Mỗi cảnh thành một clip. Cảnh đã xong sẽ không bị làm lại."],
        ["Ghép phim", "Nối các clip, thêm nhạc nền và phụ đề, xuất ra file MP4."]]
        .map(([t, d], i) => h("div", { class: "card step" }, h("div", { class: "n" }, i + 1), h("div", {}, h("b", {}, t), h("div", { class: "muted small" }, d))))),
    h("button", { class: "btn primary", onclick: openNewProject }, "＋ Tạo phim đầu tiên"));
}

function jobRunning() { return !!(S.job && S.job.status === "running"); }
function sceneDur(sc) { return Number(sc.duration) || 8; }

function renderHeader() {
  const sb = S.cur.storyboard;
  return h("div", { class: "phead" },
    h("div", { style: { flex: "1", minWidth: "260px" } },
      h("h1", {}, sb.title || "(chưa đặt tên)"),
      zone("stats-zone", () => {
        const total = sb.scenes?.length || 0, done = S.cur.done || 0;
        const len = (sb.scenes || []).reduce((a, s) => a + sceneDur(s), 0);
        return h("div", { class: "stats" },
          h("span", { class: "progress" }, h("span", { class: "bar" }, h("i", { style: { width: (total ? done / total * 100 : 0) + "%" } })), `${done}/${total} cảnh xong`),
          h("span", {}, `⏱ ~${fmtDur(len)}`),
          h("span", {}, sb.aspect === "9:16" ? "📱 Dọc 9:16" : "🖥 Ngang 16:9"),
          S.cur.project_id ? h("span", { class: "mono", title: "ID project trên Google Flow" }, "Flow: " + S.cur.project_id) : null,
          S.cur.has_final ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); switchTab("final"); } }, "🎬 Đã có phim") : null,
          h("span", { id: "savestate" }, renderSaveState()));
      })),
    zone("actions-zone", () => {
      const busy = jobRunning();
      return h("div", { class: "actions" },
        h("button", { class: "btn", disabled: busy, onclick: () => runAction("dry"), title: "In ra các lệnh sẽ gửi lên Flow, không tạo gì" }, "👁 Xem trước ", h("span", { class: "free" }, "0 credit")),
        h("button", { class: "btn", disabled: busy, onclick: () => runAction("keyframes"), title: "Tạo ảnh mở đầu cho các cảnh kiểu Ảnh → Video" }, "🖼 Tạo keyframe ", h("span", { class: "free" }, "miễn phí")),
        h("button", { class: "btn primary", disabled: busy, onclick: () => runAction("video"), title: "Render các cảnh chưa xong" }, "🎬 Render video ", h("span", { class: "paid" }, "credit")),
        h("button", { class: "btn", disabled: busy, onclick: () => { S.tab = "final"; renderMain(); } }, "🎞 Ghép phim"),
        h("button", { class: "btn ghost icon", title: "Thêm", onclick: (e) => projectMenu(e.currentTarget) }, "⋯"));
    }));
}

function projectMenu(anchor) {
  const close = modal({
    title: "Dự án: " + (S.cur.storyboard.title || S.cur.id),
    body: h("div", { class: "stack" },
      h("div", { class: "small muted mono", style: { wordBreak: "break-all" } }, S.cur.path),
      h("div", { class: "row" },
        h("button", { class: "btn", onclick: () => { api("POST", `/api/projects/${S.cur.id}/reveal`, {}); close(); } }, "📂 Mở thư mục"),
        h("button", { class: "btn", onclick: async () => { close(); await flushSave(); const r = await api("POST", `/api/projects/${S.cur.id}/duplicate`); await loadProjects(); openProject(r.id); toast("Đã nhân bản (không gồm video đã render)"); } }, "⧉ Nhân bản"),
        h("a", { class: "btn", href: `/api/projects/${S.cur.id}/file/storyboard.yaml`, download: `${S.cur.id}.yaml` }, "⬇ Tải storyboard.yaml"),
        h("button", { class: "btn danger", onclick: async () => {
          close();
          if (!(await confirmModal("Xoá dự án?", "Dự án sẽ được chuyển vào thư mục .trash trong workspace (có thể khôi phục bằng tay).", "Xoá", true))) return;
          try { await api("DELETE", `/api/projects/${S.cur.id}`); S.cur = null; await loadProjects(); if (S.projects[0]) openProject(S.projects[0].id); else renderMain(); toast("Đã chuyển vào .trash"); }
          catch (e) { toast(e.message, "error"); }
        } }, "🗑 Xoá")),
    ),
  });
}

function renderErrors() {
  const errs = S.cur.errors || [];
  if (!errs.length) return h("div");
  return h("div", { class: "banner err" }, h("b", {}, "Storyboard còn lỗi, chưa chạy được:"),
    h("ul", {}, errs.map((e) => h("li", {}, prettyError(e)))));
}
function prettyError(e) {
  return e.replace(/^scenes\.(\d+)\./, (_, i) => `Cảnh ${+i + 1}: `).replace("Field required", "chưa điền")
    .replace("mode=i2v needs 'image'", "kiểu “Ảnh của bạn → Video” cần chọn ảnh");
}

/* ---------- scenes tab ---------- */

function nextSceneId() {
  const nums = (S.cur.storyboard.scenes || []).map((s) => parseInt(String(s.id).replace(/\D/g, ""), 10)).filter((n) => !isNaN(n));
  let n = (nums.length ? Math.max(...nums) : 0) + 1;
  const ids = new Set((S.cur.storyboard.scenes || []).map((s) => s.id));
  while (ids.has("s" + String(n).padStart(2, "0"))) n++;
  return "s" + String(n).padStart(2, "0");
}

function renderScenes() {
  const sb = S.cur.storyboard;
  sb.scenes = sb.scenes || [];
  const list = h("div", { class: "scenes" }, sb.scenes.map((sc, i) => renderScene(sc, i)));
  list.append(h("button", { class: "add-scene", onclick: () => {
    sb.scenes.push({ id: nextSceneId(), mode: "keyframe", image_prompt: "", prompt: "" });
    changed(); renderMain();
    setTimeout(() => { const all = document.querySelectorAll(".scene"); all[all.length - 1]?.scrollIntoView({ behavior: "smooth", block: "center" }); }, 50);
  } }, "＋ Thêm cảnh"));
  return list;
}

function currentSceneInJob() {
  if (!jobRunning() || S.job.project !== S.cur?.id) return null;
  for (let i = S.job.lines.length - 1; i >= 0; i--) {
    const m = /^\[([A-Za-z0-9_-]+)\]/.exec(S.job.lines[i]);
    if (m) return /\] (OK|LỖI|BỎ QUA|\(dry-run\))/.test(S.job.lines[i]) ? null : m[1];
  }
  return null;
}

function sceneStatusKey(sc) {
  if (currentSceneInJob() === sc.id) return "running";
  return S.cur.scene_state?.[sc.id]?.status || "todo";
}

function renderScene(sc, i) {
  const sb = S.cur.storyboard;
  const move = (d) => { const j = i + d; if (j < 0 || j >= sb.scenes.length) return; [sb.scenes[i], sb.scenes[j]] = [sb.scenes[j], sb.scenes[i]]; changed(); renderMain(); };
  const field = (key, label, ph, rows = 2, help) => h("label", { class: "field" },
    h("span", { class: "lbl" }, label, help ? h("span", { class: "muted", style: { fontWeight: 400 } }, " · " + help) : null),
    h("textarea", { rows, placeholder: ph, value: sc[key] || "", oninput: (e) => { sc[key] = e.target.value; changed(); refreshZone(`chars-${i}`); } }));

  const cardId = "scene-" + i;
  return h("div", { class: "card scene", id: cardId },
    h("div", { class: "scene-head" },
      h("span", { class: "scene-num" }, "#" + (i + 1)),
      h("input", { type: "text", class: "scene-id", value: sc.id, title: "Mã cảnh (đổi mã sẽ mất liên kết với clip đã render)",
        onchange: (e) => { const v = e.target.value.trim().replace(/[^A-Za-z0-9_-]/g, ""); if (!v) { e.target.value = sc.id; return; } sc.id = v; e.target.value = v; changed(); } }),
      h("div", { class: "seg" }, MODES.map(([k, l, t]) => h("button", { class: sc.mode === k ? "on" : "", title: t,
        onclick: () => { sc.mode = k; changed(); renderMain(); } }, l))),
      zone(`badge-${i}`, () => { const [c, l] = STATUS[sceneStatusKey(sc)] || STATUS.todo; return h("span", { class: "badge " + c }, l); }),
      h("span", { class: "spacer" }),
      h("button", { class: "btn ghost icon", title: "Lên", disabled: i === 0, onclick: () => move(-1) }, "↑"),
      h("button", { class: "btn ghost icon", title: "Xuống", disabled: i === sb.scenes.length - 1, onclick: () => move(1) }, "↓"),
      h("button", { class: "btn ghost icon", title: "Nhân bản cảnh", onclick: () => { sb.scenes.splice(i + 1, 0, { ...sc, id: nextSceneId() }); changed(); renderMain(); } }, "⧉"),
      h("button", { class: "btn ghost icon danger", title: "Xoá cảnh", onclick: async () => {
        if (sb.scenes.length === 1) { toast("Phim cần ít nhất một cảnh", "error"); return; }
        const hasWork = S.cur.scene_state?.[sc.id]?.clip_url;
        if (hasWork && !(await confirmModal("Xoá cảnh?", `Cảnh ${sc.id} đã có clip. Clip vẫn nằm trong thư mục output nhưng sẽ không được ghép vào phim.`, "Xoá", true))) return;
        sb.scenes.splice(i, 1); changed(); renderMain();
      } }, "🗑")),
    h("div", { class: "scene-body" },
      h("div", { class: "media-col" }, zone(`media-${i}`, () => renderSceneMedia(sc)), zone(`sactions-${i}`, () => renderSceneActions(sc))),
      h("div", { class: "scene-fields" },
        sc.mode === "keyframe" ? field("image_prompt", "Ảnh mở đầu", "Mô tả khung hình đầu tiên như một bức ảnh: chủ thể, bối cảnh, ánh sáng, góc máy…", 3, "nên viết tiếng Anh") : null,
        sc.mode === "i2v" ? h("div", { class: "grid2" }, imagePicker(sc, "image", "Ảnh bắt đầu"), imagePicker(sc, "end_image", "Ảnh kết thúc (tuỳ chọn)", true)) : null,
        field("prompt", sc.mode === "t2v" ? "Nội dung video" : "Chuyển động / hành động", "Một hành động + một chuyển động máy quay, ví dụ: slow dolly-in, she smiles…", 3, "nên viết tiếng Anh"),
        zone(`chars-${i}`, () => {
          const text = `${sc.prompt || ""} ${sc.mode === "keyframe" ? sc.image_prompt || "" : ""}`;
          const found = (sb.characters || []).filter((c) => c.name && new RegExp(`\\b${c.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i").test(text));
          return found.length ? h("div", { class: "char-chips" }, h("span", { class: "small muted" }, "Nhân vật tự thêm mô tả:"), found.map((c) => h("span", { class: "char-chip", title: c.description }, c.name))) : h("div");
        }),
        h("div", { class: "grid3" },
          h("label", { class: "field" }, h("span", { class: "lbl" }, "Độ dài"), durationSelect(sc)),
          h("label", { class: "field" }, h("span", { class: "lbl" }, "Model video"),
            h("select", { value: sc.model || "", onchange: (e) => { sc.model = e.target.value || undefined; changed(); renderMain(); } },
              h("option", { value: "" }, `Theo phim (${sb.model || "mặc định"})`),
              videoModels().map((m) => h("option", { value: m }, m)))),
          h("label", { class: "field" }, h("span", { class: "lbl" }, "Phụ đề / lời dẫn"),
            h("input", { type: "text", value: sc.narration || "", placeholder: "Không bắt buộc", oninput: (e) => { sc.narration = e.target.value; changed(); } }))),
        zone(`preview-${i}`, () => {
          const st = S.cur.scene_state?.[sc.id];
          if (!st) return h("div");
          return h("details", { class: "prompt-preview", open: localGet("previewOpen", false) || null, ontoggle: (e) => localSet("previewOpen", e.target.open) },
            h("summary", {}, "Prompt thực tế gửi lên Flow"),
            st.image_prompt ? [h("div", { class: "small muted", style: { marginTop: "6px" } }, "Ảnh:"), h("pre", {}, st.image_prompt)] : null,
            h("div", { class: "small muted", style: { marginTop: "6px" } }, "Video:"), h("pre", {}, st.video_prompt));
        }),
      )));
}

function videoModels() {
  const list = (S.models.video || []).map((m) => m.alias);
  return list.length ? list : ["veo-fast", "veo-quality", "veo-lite", "omni-flash"];
}
function maxDuration(model) {
  const m = (S.models.video || []).find((x) => x.alias === model);
  return m?.max_duration || 8;
}
function durationSelect(sc) {
  const max = maxDuration(sc.model || S.cur.storyboard.model);
  const opts = [4, 6, 8, 10, 12, 15].filter((d) => d <= max);
  if (sc.duration && !opts.includes(+sc.duration)) opts.push(+sc.duration);
  return h("select", { value: sc.duration ? String(sc.duration) : "", onchange: (e) => { sc.duration = e.target.value ? +e.target.value : undefined; changed(); refreshZone("stats-zone"); } },
    h("option", { value: "" }, "Mặc định"), opts.map((d) => h("option", { value: String(d) }, `${d} giây${d > max ? " (quá giới hạn model)" : ""}`)));
}

function assetUrl(path) { return S.assets.images.find((a) => a.path === path)?.url || null; }

function imagePicker(sc, attr, label, optional = false) {
  const val = sc[attr] || "";
  const url = assetUrl(val) || S.cur.scene_state?.[sc.id]?.[attr === "image" ? "image_url" : "end_image_url"];
  const fileInput = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", class: "hidden", onchange: async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f); fd.append("kind", "image");
    try {
      const r = await api("POST", `/api/projects/${S.cur.id}/upload`, fd, true);
      S.assets = await api("GET", `/api/projects/${S.cur.id}/assets`);
      sc[attr] = r.path; changed(); renderMain();
    } catch (err) { toast(err.message, "error"); }
  } });
  const isAsset = !val || S.assets.images.some((a) => a.path === val);
  return h("div", { class: "field" }, h("span", { class: "lbl" }, label),
    h("div", { class: "picker" },
      h("div", { class: "pthumb", style: url ? { backgroundImage: `url("${url}")`, cursor: "zoom-in" } : null, onclick: (e) => { if (url) { e.preventDefault(); lightbox(url); } } }),
      h("select", { value: val, onchange: (e) => { sc[attr] = e.target.value || undefined; changed(); renderMain(); } },
        h("option", { value: "" }, optional ? "— không dùng —" : "— chọn ảnh —"),
        isAsset ? null : h("option", { value: val }, val),
        S.assets.images.map((a) => h("option", { value: a.path }, a.path.replace("assets/", "")))),
      h("button", { class: "btn sm", onclick: (e) => { e.preventDefault(); fileInput.click(); } }, "Tải lên"), fileInput));
}

function renderSceneMedia(sc) {
  const st = S.cur.scene_state?.[sc.id] || {};
  const portrait = S.cur.storyboard.aspect === "9:16";
  const box = h("div", { class: "media" + (portrait ? " portrait" : "") });
  const kf = st.keyframe_url;
  const startImg = sc.mode === "i2v" ? (assetUrl(sc.image) || st.image_url) : null;
  const stale = st.status === "changed";
  if (st.clip_url) {
    box.append(h("video", { src: st.clip_url, controls: true, preload: "metadata", poster: kf || startImg || null, playsinline: true }));
    if (stale) box.append(h("span", { class: "tag warn" }, "Clip cũ · prompt đã đổi"));
  } else if (kf) {
    box.append(h("img", { src: kf, onclick: () => lightbox(kf), alt: "keyframe" }));
    box.append(h("span", { class: "tag" + (st.keyframe_stale ? " warn" : "") }, st.keyframe_stale ? "Ảnh cũ · prompt đã đổi" : "Keyframe · chưa có video"));
  } else if (startImg) {
    box.append(h("img", { src: startImg, onclick: () => lightbox(startImg), alt: "ảnh bắt đầu" }));
    box.append(h("span", { class: "tag" }, "Ảnh của bạn"));
  } else {
    box.append(h("div", { class: "ph" }, h("div", {}, h("b", {}, sc.mode === "t2v" ? "🎥" : "🖼"),
      sc.mode === "keyframe" ? "Bấm “Tạo ảnh” để xem trước khung hình (miễn phí)" : sc.mode === "i2v" ? "Chọn hoặc tải ảnh bắt đầu" : "Video sẽ hiện ở đây sau khi render")));
  }
  const wrap = h("div", { class: "stack" }, box);
  if (st.clip_url && kf) wrap.append(h("div", { class: "media-thumbs" }, h("button", { class: "btn sm", onclick: () => lightbox(kf) }, "🖼 Xem ảnh keyframe")));
  return wrap;
}

function renderSceneActions(sc) {
  const st = S.cur.scene_state?.[sc.id] || {};
  const busy = jobRunning();
  const kids = [];
  if (st.status === "failed" && st.error) kids.push(h("div", { class: "scene-err" }, st.error));
  if (st.status === "interrupted") {
    kids.push(h("div", { class: "scene-warn" }, "Lần trước bị ngắt khi đang render, có thể Flow đã trừ credit. Hãy mở Flow xem clip đã có chưa trước khi gửi lại."));
  }
  const row = h("div", { class: "row" });
  if (sc.mode === "keyframe") {
    const hasKf = !!st.keyframe_url && !st.keyframe_stale;
    row.append(h("button", { class: "btn sm", disabled: busy, title: "Ảnh trên Flow miễn phí",
      onclick: () => runAction("keyframes", { only: [sc.id], force_keyframes: [sc.id] }) }, hasKf ? "↻ Tạo lại ảnh" : "🖼 Tạo ảnh", h("span", { class: "free" }, "free")));
  }
  const label = st.status === "done" ? "↻ Render lại" : st.status === "interrupted" ? "Gửi lại" : "🎬 Render";
  row.append(h("button", { class: "btn sm" + (st.status === "done" ? "" : " primary"), disabled: busy,
    onclick: () => runAction("video", { only: [sc.id], force: st.status === "done" || st.status === "interrupted" ? [sc.id] : [] }) },
  label, h("span", { class: "paid" }, "credit")));
  if (st.clip_url) row.append(h("a", { class: "btn sm ghost", href: st.clip_url.split("?")[0], download: `${sc.id}.mp4`, title: "Tải clip" }, "⬇"));
  kids.push(row);
  return h("div", { class: "stack" }, kids);
}

/* ---------- story tab ---------- */

function renderStory() {
  const sb = S.cur.storyboard;
  sb.characters = sb.characters || [];
  const set = (k, v) => { sb[k] = v; changed(); };
  const imageModels = (S.models.image || []).length ? S.models.image : [{ alias: "nano2", label: "Nano Banana 2" }];
  return h("div", { class: "stack" },
    h("div", { class: "card card-pad section" }, h("h3", {}, "Thông tin phim"),
      h("div", { class: "stack" },
        h("label", { class: "field" }, h("span", { class: "lbl" }, "Tên phim"),
          h("input", { type: "text", value: sb.title || "", oninput: (e) => { set("title", e.target.value); const t = document.querySelector(".phead h1"); if (t) t.textContent = e.target.value || "(chưa đặt tên)"; } })),
        h("div", { class: "grid3" },
          h("div", { class: "field" }, h("span", { class: "lbl" }, "Khung hình"),
            h("div", { class: "seg" }, [["16:9", "🖥 Ngang 16:9"], ["9:16", "📱 Dọc 9:16"]].map(([v, l]) =>
              h("button", { class: (sb.aspect || "16:9") === v ? "on" : "", onclick: () => { set("aspect", v); renderMain(); } }, l)))),
          h("label", { class: "field" }, h("span", { class: "lbl" }, "Model video"),
            h("select", { value: sb.model || "", onchange: (e) => { set("model", e.target.value || undefined); } },
              h("option", { value: "" }, "Mặc định của Flow"), videoModels().map((m) => h("option", { value: m }, m)))),
          h("label", { class: "field" }, h("span", { class: "lbl" }, "Model ảnh (keyframe)"),
            h("select", { value: sb.image_model || "nano2", onchange: (e) => set("image_model", e.target.value) },
              imageModels.map((m) => h("option", { value: m.alias }, m.label ? `${m.label} (${m.alias})` : m.alias))))),
        h("p", { class: "small muted", style: { margin: 0 } }, "Mẹo: veo-fast rẻ và nhanh để thử; chuyển sang veo-quality khi đã ưng kịch bản. Đổi model sẽ làm các cảnh cần render lại."),
        h("label", { class: "field" }, h("span", { class: "lbl" }, "Phong cách hình ảnh · thêm vào mọi prompt"),
          h("textarea", { rows: 2, value: sb.style || "", placeholder: "cinematic, warm morning light, shallow depth of field, 35mm film grain",
            oninput: (e) => set("style", e.target.value) })),
        h("details", {}, h("summary", { class: "small muted", style: { cursor: "pointer" } }, "Nâng cao"),
          h("label", { class: "field", style: { marginTop: "8px" } }, h("span", { class: "lbl" }, "Dùng lại project có sẵn trên Flow (project_id)"),
            h("input", { type: "text", value: sb.project_id || "", placeholder: S.cur.project_id ? `Đang dùng: ${S.cur.project_id}` : "Để trống: tự tạo project mới",
              oninput: (e) => set("project_id", e.target.value || undefined) }))))),
    h("div", { class: "card card-pad section" },
      h("div", { class: "row", style: { marginBottom: "10px" } }, h("h3", { style: { margin: 0 } }, "Nhân vật"), h("span", { class: "spacer" }),
        h("button", { class: "btn sm", onclick: () => { sb.characters.push({ name: "", description: "" }); changed(); renderMain(); } }, "＋ Thêm nhân vật")),
      h("p", { class: "small muted", style: { marginTop: 0 } }, "Mô tả ngoại hình cố định một lần. Khi tên nhân vật xuất hiện trong prompt một cảnh, mô tả sẽ được tự thêm vào, giúp nhân vật trông giống nhau ở mọi cảnh."),
      sb.characters.length ? sb.characters.map((c, i) => h("div", { class: "char-row" },
        h("input", { type: "text", value: c.name, placeholder: "Tên, ví dụ: Lan", oninput: (e) => { c.name = e.target.value; changed(); } }),
        h("textarea", { rows: 2, value: c.description, placeholder: "a Vietnamese woman in her late 20s, shoulder-length black hair, cream linen shirt…",
          oninput: (e) => { c.description = e.target.value; changed(); } }),
        h("button", { class: "btn ghost icon danger", title: "Xoá", onclick: () => { sb.characters.splice(i, 1); changed(); renderMain(); } }, "🗑")))
        : h("p", { class: "muted small" }, "Chưa có nhân vật.")));
}

/* ---------- final tab ---------- */

function finalPrefs() {
  return { music: null, volume: 0.25, burn_subtitles: false, allow_missing: false, ...(S.cur.final_prefs || {}) };
}
function saveFinalPrefs(p) {
  S.cur.final_prefs = p;
  api("PUT", `/api/projects/${S.cur.id}/final-settings`, p).catch((e) => toast(e.message, "error"));
}

function readyClips() {
  return (S.cur.storyboard.scenes || []).map((sc) => ({ sc, st: S.cur.scene_state?.[sc.id] })).filter((x) => x.st && x.st.clip_url && x.st.status === "done");
}

function renderFinal() {
  const p = finalPrefs();
  const portrait = S.cur.storyboard.aspect === "9:16";
  const musicInput = h("input", { type: "file", accept: "audio/*", class: "hidden", onchange: async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f); fd.append("kind", "audio");
    try {
      const r = await api("POST", `/api/projects/${S.cur.id}/upload`, fd, true);
      S.assets = await api("GET", `/api/projects/${S.cur.id}/assets`);
      saveFinalPrefs({ ...finalPrefs(), music: r.path }); renderMain();
    } catch (err) { toast(err.message, "error"); }
  } });
  const musicUrl = S.assets.audio.find((a) => a.path === p.music)?.url;
  const total = S.cur.storyboard.scenes?.length || 0;

  return h("div", { class: "final-grid" },
    h("div", { class: "stack" },
      zone("final-zone", () => {
        if (!S.cur.final_url) return h("div", { class: "card card-pad" }, h("h3", {}, "Phim hoàn chỉnh"),
          h("div", { class: "player" + (portrait ? " portrait" : "") }, h("div", { class: "ph" }, "Chưa ghép phim. Render xong các cảnh rồi bấm “Ghép phim”.")));
        return h("div", { class: "card card-pad" }, h("h3", {}, "🎬 Phim hoàn chỉnh"),
          h("div", { class: "player" + (portrait ? " portrait" : "") }, h("video", { src: S.cur.final_url, controls: true, preload: "metadata", playsinline: true })),
          h("div", { class: "row", style: { marginTop: "10px" } },
            h("a", { class: "btn primary sm", href: S.cur.final_url.split("?")[0], download: `${S.cur.id}.mp4` }, "⬇ Tải MP4"),
            S.cur.srt_url ? h("a", { class: "btn sm", href: S.cur.srt_url.split("?")[0], download: `${S.cur.id}.srt` }, "⬇ Phụ đề .srt") : null,
            h("button", { class: "btn sm", onclick: () => api("POST", `/api/projects/${S.cur.id}/reveal`, { path: "output/storyboard/final.mp4" }) }, "📂 Mở trong Finder"),
            h("span", { class: "spacer" }), S.cur.final_size ? h("span", { class: "small muted" }, fmtBytes(S.cur.final_size)) : null));
      }),
      zone("playlist-zone", renderPlaylist)),
    h("div", { class: "stack" },
      h("div", { class: "card card-pad" }, h("h3", {}, "Ghép phim"),
        h("div", { class: "stack" },
          h("div", { class: "field" }, h("span", { class: "lbl" }, "Nhạc nền"),
            h("div", { class: "row" },
              h("select", { style: { flex: "1" }, value: p.music || "", onchange: (e) => { saveFinalPrefs({ ...finalPrefs(), music: e.target.value || null }); renderMain(); } },
                h("option", { value: "" }, "Không có nhạc"), S.assets.audio.map((a) => h("option", { value: a.path }, a.path.replace("assets/", "")))),
              h("button", { class: "btn sm", onclick: () => musicInput.click() }, "Tải lên"), musicInput)),
          musicUrl ? h("audio", { src: musicUrl, controls: true, style: { width: "100%" } }) : null,
          p.music ? h("label", { class: "field" }, h("span", { class: "lbl" }, "Âm lượng nhạc: ", h("span", { id: "volv" }, Math.round(p.volume * 100) + "%")),
            h("input", { type: "range", min: 0, max: 1, step: 0.05, value: p.volume,
              oninput: (e) => { document.getElementById("volv").textContent = Math.round(e.target.value * 100) + "%"; },
              onchange: (e) => saveFinalPrefs({ ...finalPrefs(), volume: +e.target.value }) })) : null,
          h("label", { class: "check" }, h("input", { type: "checkbox", checked: p.burn_subtitles, onchange: (e) => saveFinalPrefs({ ...finalPrefs(), burn_subtitles: e.target.checked }) }),
            h("span", {}, "In phụ đề lên hình", h("div", { class: "small muted" }, "Luôn xuất thêm file .srt riêng nếu có lời dẫn."))),
          h("label", { class: "check" }, h("input", { type: "checkbox", checked: p.allow_missing, onchange: (e) => saveFinalPrefs({ ...finalPrefs(), allow_missing: e.target.checked }) }),
            h("span", {}, "Ghép cả khi còn thiếu cảnh", h("div", { class: "small muted" }, "Bỏ qua các cảnh chưa render để xem thử bản nháp."))),
          zone("final-actions", () => {
            const n = readyClips().length, busy = jobRunning();
            return h("div", { class: "stack" },
              h("div", { class: "small muted" }, `${n}/${total} cảnh đã có clip.`),
              h("div", { class: "row" },
                h("button", { class: "btn primary", disabled: busy || !n, onclick: () => runAction("stitch") }, "🎞 Ghép phim ", h("span", { class: "free" }, "miễn phí")),
                h("button", { class: "btn", disabled: busy, onclick: () => runAction("make"), title: "Render các cảnh còn thiếu rồi ghép" }, "Render nốt + ghép ", h("span", { class: "paid" }, "credit"))));
          }))),
      h("div", { class: "card card-pad" }, h("h3", {}, "Lời dẫn / phụ đề"),
        (S.cur.storyboard.scenes || []).some((s) => s.narration)
          ? h("ol", { class: "narr-list" }, S.cur.storyboard.scenes.filter((s) => s.narration).map((s) => h("li", {}, h("span", { class: "mono muted" }, s.id + " "), s.narration)))
          : h("p", { class: "small muted" }, "Chưa có. Thêm “Phụ đề / lời dẫn” ở từng cảnh."))));
}

function renderPlaylist() {
  const clips = readyClips();
  if (!clips.length) return h("div");
  const portrait = S.cur.storyboard.aspect === "9:16";
  if (S.playIdx >= clips.length) S.playIdx = 0;
  const cur = clips[S.playIdx];
  const sub = h("div", { class: "subtitle" }, cur.sc.narration || "");
  const video = h("video", { src: cur.st.clip_url, controls: true, preload: "metadata", playsinline: true, poster: cur.st.keyframe_url || null,
    onended: () => { if (S.playIdx < clips.length - 1) { S.playIdx++; const z = document.getElementById("playlist-zone"); const n = renderPlaylist(); n.id = "playlist-zone"; z.replaceWith(n); n.querySelector("video").play(); } } });
  return h("div", { class: "card card-pad" },
    h("div", { class: "row", style: { marginBottom: "8px" } }, h("h3", { style: { margin: 0 } }, "Xem nhanh các clip nối tiếp"), h("span", { class: "spacer" }),
      h("span", { class: "small muted" }, `Cảnh ${S.playIdx + 1}/${clips.length} · ${cur.sc.id}`)),
    h("div", { class: "player" + (portrait ? " portrait" : "") }, video, sub),
    h("div", { class: "playlist" }, clips.map((c, i) => h("button", { class: i === S.playIdx ? "on" : "", onclick: () => {
      S.playIdx = i; const z = document.getElementById("playlist-zone"); const n = renderPlaylist(); n.id = "playlist-zone"; z.replaceWith(n); n.querySelector("video").play();
    } }, h("div", { class: "pl-img", style: c.st.keyframe_url ? { backgroundImage: `url("${c.st.keyframe_url}")` } : null }), h("div", { class: "pl-lbl" }, `${i + 1}. ${c.sc.id}`)))),
    h("p", { class: "small muted", style: { margin: "6px 0 0" } }, "Chỉ để xem thử thứ tự, chưa có nhạc. Bấm “Ghép phim” để xuất file MP4."));
}

/* ---------- yaml tab ---------- */

function renderYaml() {
  const ta = h("textarea", { class: "yaml", spellcheck: "false", value: S.cur.yaml });
  return h("div", { class: "stack" },
    h("div", { class: "banner info" }, "Sửa trực tiếp file storyboard.yaml. Bấm “Lưu YAML” để áp dụng. Lưu ý: khi sửa bằng các ô nhập ở tab khác, chú thích (#) trong file sẽ bị bỏ."),
    ta,
    h("div", { class: "row" },
      h("button", { class: "btn primary", onclick: async () => {
        try {
          const d = await api("PUT", `/api/projects/${S.cur.id}/yaml`, { text: ta.value });
          S.cur = d; toast(d.errors.length ? "Đã lưu, nhưng storyboard còn lỗi" : "Đã lưu YAML", d.errors.length ? "error" : "ok");
          renderMain(); loadProjects();
        } catch (e) { toast(e.message, "error"); }
      } }, "Lưu YAML"),
      h("button", { class: "btn", onclick: () => { ta.value = S.cur.yaml; } }, "Hoàn tác")));
}

/* ---------- run & jobs ---------- */

async function runAction(action, extra = {}) {
  if (!S.cur) return;
  await flushSave();
  if (S.saveState === "error") { toast("Chưa lưu được storyboard: " + S.saveError, "error"); return; }
  if (S.cur.errors?.length) { toast("Storyboard còn lỗi: " + prettyError(S.cur.errors[0]), "error"); return; }
  if (action !== "dry" && action !== "stitch" && !S.env.gflow) { toast("Chưa cài gflow-cli", "error"); return; }
  if ((action === "stitch" || action === "make") && !S.env.ffmpeg) { toast("Chưa cài ffmpeg nên không ghép phim được", "error"); return; }
  const body = { action, only: [], force: [], force_keyframes: [], ...extra };
  if (action === "stitch" || action === "make") Object.assign(body, finalPrefs());
  if (action === "video" || action === "make") {
    const ok = await confirmPaid(body);
    if (!ok) return;
  }
  if (action === "keyframes" && !extra.only) {
    const kfScenes = S.cur.storyboard.scenes.filter((s) => s.mode === "keyframe");
    if (!kfScenes.length) { toast("Không có cảnh nào kiểu “Ảnh → Video”"); return; }
  }
  try {
    const j = await api("POST", `/api/projects/${S.cur.id}/run`, body);
    startJob(j);
    if ("Notification" in window && Notification.permission === "default" && action !== "dry") Notification.requestPermission();
  } catch (e) { toast(e.message, "error"); }
}

async function confirmPaid(body) {
  let est;
  try { est = await api("POST", `/api/projects/${S.cur.id}/estimate`, body); } catch (e) { toast(e.message, "error"); return false; }
  if (!est.renders.length && !est.blocked.length) {
    if (body.action === "make") return true; // nothing to render: go straight to stitching
    toast("Tất cả cảnh đã render xong. Muốn làm lại cảnh nào thì bấm “Render lại” ở cảnh đó.");
    return false;
  }
  return new Promise((resolve) => {
    let done = false, retry = false;
    const count = () => est.renders.length + (retry ? est.blocked.length : 0);
    const okBtnLabel = () => `Render ${count()} clip`;
    const body2 = h("div", { class: "stack" },
      est.renders.length ? h("table", { class: "list" },
        h("tr", {}, h("th", {}, "Cảnh"), h("th", {}, "Kiểu"), h("th", {}, "Model"), h("th", {}, "Độ dài"), h("th", {}, "Nội dung")),
        est.renders.map((r) => h("tr", {}, h("td", { class: "mono" }, r.id), h("td", {}, MODES.find((m) => m[0] === r.mode)?.[1] || r.mode),
          h("td", { class: "mono" }, r.model), h("td", {}, r.duration ? r.duration + "s" : "mặc định"), h("td", { class: "small" }, r.prompt)))) : null,
      est.new_keyframes.length ? h("p", { class: "small muted", style: { margin: 0 } }, `Sẽ tạo trước ${est.new_keyframes.length} ảnh keyframe chưa có (miễn phí): ${est.new_keyframes.join(", ")}. Muốn duyệt ảnh trước thì bấm Huỷ rồi dùng “Tạo keyframe”.`) : null,
      est.blocked.length ? h("div", { class: "banner warn", style: { margin: 0 } },
        h("div", {}, `Cảnh ${est.blocked.join(", ")} bị ngắt khi render lần trước, có thể Flow đã trừ credit. Hãy kiểm tra trên Flow trước.`),
        h("label", { class: "check", style: { marginTop: "8px" } }, h("input", { type: "checkbox", onchange: (e) => { retry = e.target.checked; body.retry_unknown = retry; document.getElementById("paid-ok").textContent = okBtnLabel(); document.getElementById("paid-ok").disabled = !count(); } }),
          h("span", {}, "Tôi đã kiểm tra, gửi lại các cảnh này"))) : null,
      h("div", { class: "banner info", style: { margin: 0 } }, "Mỗi clip sẽ trừ credit trên tài khoản Google Flow của bạn. Có thể bấm Dừng bất cứ lúc nào; cảnh đang render sẽ được làm nốt."),
    );
    modal({
      title: "Xác nhận render video", body: body2, wide: true, onClose: () => { if (!done) resolve(false); },
      buttons: [{ label: "Huỷ" }, { label: okBtnLabel(), id: "paid-ok", cls: "primary", disabled: !count(), onclick: (c) => { done = true; resolve(true); c(); } }],
    });
  });
}

function startJob(j) {
  S.job = { ...j, lines: [...j.lines] };
  S.jobSince = j.total;
  S.jobOpen = true;
  renderJobbar();
  refreshZones();
  renderSidebar();
  pollJob();
}

async function pollJob() {
  if (!S.job) return;
  clearTimeout(S.pollT);
  try {
    const d = await api("GET", `/api/jobs/${S.job.id}?since=${S.jobSince}`);
    const fresh = d.lines;
    S.job.lines.push(...fresh);
    S.jobSince = d.total;
    const wasRunning = S.job.status === "running";
    Object.assign(S.job, { status: d.status, result: d.result, finished: d.finished, stop_requested: d.stop_requested });
    appendLog(fresh);
    updateJobHead();
    if (fresh.some((l) => /^\[[^\]]+\]/.test(l))) {
      refreshZones();
      if (fresh.some((l) => /\] OK -> /.test(l)) && S.cur && S.cur.id === S.job.project) refreshCurrent();
    }
    if (d.status === "running") S.pollT = setTimeout(pollJob, 1000);
    else if (wasRunning) onJobDone();
  } catch { S.pollT = setTimeout(pollJob, 3000); }
}

async function onJobDone() {
  const j = S.job;
  document.title = "Film Maker";
  const msg = j.status === "done" ? `✔ ${j.title}: xong` : j.status === "stopped" ? `⏸ ${j.title}: đã dừng` : `✘ ${j.title}: ${j.result?.error || "lỗi"}`;
  toast(msg, j.status === "done" ? "ok" : j.status === "failed" ? "error" : "", 6000);
  if (j.status === "done") setTimeout(() => { if (S.job === j && S.jobOpen) { S.jobOpen = false; document.getElementById("joblog")?.classList.add("hidden"); updateJobHead(); } }, 2500);
  if ("Notification" in window && Notification.permission === "granted" && document.hidden && j.kind !== "dry") {
    try { new Notification("Film Maker", { body: msg }); } catch { /* ignore */ }
  }
  await loadProjects();
  if (j.kind === "plan" && j.result?.project) { await openProject(j.result.project); return; }
  if (S.cur && S.cur.id === j.project) {
    await refreshCurrent();
    if (j.kind === "stitch" || (j.kind === "make" && j.result?.final)) { S.tab = "final"; renderMain(); }
  }
  refreshZones();
}

function renderJobbar() {
  const bar = $("#jobbar");
  if (!S.job) { bar.classList.add("hidden"); return; }
  bar.classList.remove("hidden");
  const log = h("pre", { class: "joblog" + (S.jobOpen ? "" : " hidden"), id: "joblog" });
  put(bar, h("div", { class: "job-head", id: "jobhead" }), log);
  appendLog(S.job.lines, true);
  updateJobHead();
}

function lineClass(l) {
  if (/LỖI|✘|lỗi:|Error/.test(l)) return "l-err";
  if (/^\s+\$ /.test(l)) return "l-cmd";
  if (/OK ->|✔|🎬/.test(l)) return "l-ok";
  if (/⚠|BỎ QUA|⏸|!/.test(l)) return "l-warn";
  return "";
}

function appendLog(lines, reset = false) {
  const el = document.getElementById("joblog");
  if (!el) return;
  if (reset) put(el, );
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  for (const l of lines) el.append(h("div", { class: lineClass(l) }, l || " "));
  if (atBottom || reset) el.scrollTop = el.scrollHeight;
}

function updateJobHead() {
  const head = document.getElementById("jobhead");
  if (!head || !S.job) return;
  const j = S.job;
  const last = [...j.lines].reverse().find((l) => l.trim()) || "";
  const statusText = { running: "Đang chạy", done: "Xong", failed: "Lỗi", stopped: "Đã dừng" }[j.status];
  put(head, 
    h("span", { class: "dot " + j.status }),
    h("span", { class: "title" }, j.title),
    h("span", { class: "small muted" }, statusText, " · ", h("span", { id: "elapsed" }, elapsedText())),
    h("span", { class: "last", title: last }, last),
    j.status === "running" ? h("button", { class: "btn sm danger", disabled: j.stop_requested, onclick: stopJob }, j.stop_requested ? "Đang dừng…" : "⏹ Dừng") : null,
    h("button", { class: "btn sm ghost", title: "Chép log", onclick: () => { navigator.clipboard?.writeText(j.lines.join("\n")); toast("Đã chép log", "", 1500); } }, "⧉"),
    h("button", { class: "btn sm ghost", onclick: () => { S.jobOpen = !S.jobOpen; document.getElementById("joblog").classList.toggle("hidden", !S.jobOpen); updateJobHead(); } }, S.jobOpen ? "▾ Ẩn log" : "▴ Xem log"),
    j.status !== "running" ? h("button", { class: "btn sm ghost icon", title: "Đóng", onclick: () => { S.job = null; renderJobbar(); } }, "✕") : null,
  );
  document.title = j.status === "running" ? `⏳ ${j.title} · Film Maker` : "Film Maker";
}

function elapsedText() {
  const j = S.job;
  if (!j) return "";
  return fmtDur(((j.finished || Date.now() / 1000) - j.started));
}
function tickElapsed() {
  if (S.job && S.job.status === "running") { const el = document.getElementById("elapsed"); if (el) el.textContent = elapsedText(); }
}

async function stopJob() {
  try { const d = await api("POST", `/api/jobs/${S.job.id}/stop`); S.job.stop_requested = true; S.job.lines.push(...d.lines); appendLog(d.lines); S.jobSince = d.total; updateJobHead(); }
  catch (e) { toast(e.message, "error"); }
}

/* ---------- new project ---------- */

function openNewProject() {
  let mode = S.env.anthropic_key ? "ai" : "template";
  const body = h("div");
  const render = () => {
    const choices = [["ai", "✨ Từ ý tưởng", "Claude viết storyboard cho bạn"], ["template", "📋 Từ mẫu", "Phim mẫu 3 cảnh để làm quen"],
      ["blank", "📄 Trống", "Tự viết từng cảnh"], ["import", "📥 Nhập file", "Mở file storyboard .yaml có sẵn"]];
    const form = { ai: aiForm, template: () => titleForm("Ly cà phê đầu tiên"), blank: () => titleForm(""), import: importForm }[mode]();
    put(body, h("div", { class: "choice" }, choices.map(([k, t, d]) => h("button", { class: mode === k ? "on" : "", onclick: () => { mode = k; render(); } }, h("b", {}, t), h("span", {}, d)))), form);
  };
  let close;
  const titleForm = (def) => {
    const inp = h("input", { type: "text", value: def, placeholder: "Tên phim" });
    setTimeout(() => inp.focus(), 30);
    const go = async () => {
      try { const r = await api("POST", "/api/projects", { title: inp.value, source: mode }); close(); await loadProjects(); await openProject(r.id); }
      catch (e) { toast(e.message, "error"); }
    };
    inp.onkeydown = (e) => { if (e.key === "Enter") go(); };
    return h("div", { class: "stack" }, h("label", { class: "field" }, h("span", { class: "lbl" }, "Tên phim"), inp),
      h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "btn primary", onclick: go }, "Tạo dự án")));
  };
  const aiForm = () => {
    if (!S.env.anthropic_key) return h("div", { class: "banner warn" }, "Cần Anthropic API key để dùng Claude. ",
      h("a", { href: "#", onclick: (e) => { e.preventDefault(); close(); openSettings(); } }, "Thêm key trong Cài đặt"), ".");
    const idea = h("textarea", { rows: 4, placeholder: "Ví dụ: Một cô gái mở quán cà phê nhỏ ở Hà Nội, buổi sáng đầu tiên đón vị khách đầu tiên là ông cụ hàng xóm…", value: localGet("draftIdea", "") , oninput: (e) => localSet("draftIdea", e.target.value) });
    const n = h("input", { type: "number", min: 1, max: 20, value: 6 });
    const aspect = h("select", {}, h("option", { value: "16:9" }, "Ngang 16:9"), h("option", { value: "9:16" }, "Dọc 9:16 (TikTok/Shorts)"));
    const lang = h("select", {}, h("option", { value: "Vietnamese" }, "Tiếng Việt"), h("option", { value: "English" }, "English"));
    setTimeout(() => idea.focus(), 30);
    const go = async (e) => {
      if (!idea.value.trim()) { toast("Hãy nhập ý tưởng", "error"); return; }
      e.target.disabled = true;
      try {
        const j = await api("POST", "/api/projects/plan", { idea: idea.value, scenes: +n.value || 6, aspect: aspect.value, language: lang.value });
        close(); localSet("draftIdea", ""); startJob(j); toast("Claude đang viết storyboard, thường mất 1–2 phút…", "", 6000);
      } catch (err) { toast(err.message, "error"); e.target.disabled = false; }
    };
    return h("div", { class: "stack" },
      h("label", { class: "field" }, h("span", { class: "lbl" }, "Ý tưởng phim"), idea),
      h("div", { class: "grid3" }, h("label", { class: "field" }, h("span", { class: "lbl" }, "Số cảnh"), n),
        h("label", { class: "field" }, h("span", { class: "lbl" }, "Khung hình"), aspect), h("label", { class: "field" }, h("span", { class: "lbl" }, "Ngôn ngữ phụ đề"), lang)),
      h("p", { class: "small muted", style: { margin: 0 } }, "Prompt sẽ được viết bằng tiếng Anh cho Veo hiểu tốt nhất. Bạn xem và sửa lại trước khi tạo bất cứ thứ gì."),
      h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "btn primary", onclick: go }, "✨ Viết storyboard")));
  };
  const importForm = () => {
    const inp = h("input", { type: "file", accept: ".yaml,.yml" });
    return h("div", { class: "stack" }, h("label", { class: "field" }, h("span", { class: "lbl" }, "File storyboard (.yaml)"), inp),
      h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "btn primary", onclick: async () => {
        if (!inp.files[0]) { toast("Chọn file trước", "error"); return; }
        const fd = new FormData(); fd.append("file", inp.files[0]);
        try { const r = await api("POST", "/api/projects/import", fd, true); close(); await loadProjects(); await openProject(r.id); toast("Đã nhập storyboard", "ok"); }
        catch (e) { toast(e.message, "error"); }
      } }, "Nhập")));
  };
  render();
  close = modal({ title: "Phim mới", body, wide: true });
}

/* ---------- settings & credits ---------- */

async function openSettings() {
  let s;
  try { s = await api("GET", "/api/settings"); } catch (e) { toast(e.message, "error"); return; }
  const key = h("input", { type: "password", placeholder: s.anthropic_key_set ? `Đã lưu (${s.anthropic_key_hint}) · nhập để thay` : s.anthropic_key_from_env ? "Đang dùng biến môi trường ANTHROPIC_API_KEY" : "sk-ant-…", autocomplete: "off" });
  const profile = h("input", { type: "text", value: s.profile || "", placeholder: "Để trống: dùng tài khoản mặc định" });
  const pmin = h("input", { type: "number", min: 0, value: s.pause_min });
  const pmax = h("input", { type: "number", min: 0, value: s.pause_max });
  const retries = h("input", { type: "number", min: 0, max: 5, value: s.retries });
  let clearKey = false;
  const theme = h("select", { value: localGet("theme", "auto"), onchange: (e) => { localSet("theme", e.target.value); applyTheme(); } },
    h("option", { value: "auto" }, "Theo hệ thống"), h("option", { value: "light" }, "Sáng"), h("option", { value: "dark" }, "Tối"));
  modal({
    title: "Cài đặt", wide: true,
    body: h("div", { class: "stack" },
      h("div", { class: "card card-pad" }, h("h3", {}, "Google Flow (gflow)"),
        h("div", { class: "stack" },
          h("label", { class: "field" }, h("span", { class: "lbl" }, "gflow profile (tài khoản Google)"), profile),
          h("div", { class: "row" },
            h("button", { class: "btn sm", onclick: () => checkAuth() }, "Kiểm tra đăng nhập"),
            h("button", { class: "btn sm", onclick: () => startLogin() }, "Đăng nhập lại bằng Chrome"),
            h("a", { class: "btn sm ghost", href: "https://flow.google.com/", target: "_blank", rel: "noopener" }, "Mở Google Flow ↗")))),
      h("div", { class: "card card-pad" }, h("h3", {}, "An toàn khi render"),
        h("div", { class: "grid3" },
          h("label", { class: "field" }, h("span", { class: "lbl" }, "Nghỉ tối thiểu (giây)"), pmin),
          h("label", { class: "field" }, h("span", { class: "lbl" }, "Nghỉ tối đa (giây)"), pmax),
          h("label", { class: "field" }, h("span", { class: "lbl" }, "Số lần thử lại khi lỗi tạm"), retries)),
        h("p", { class: "small muted", style: { marginBottom: 0 } }, "Nghỉ ngẫu nhiên giữa các lần gửi để Flow không coi là bot. Chỉ thử lại các lỗi mà gflow báo là tạm thời.")),
      h("div", { class: "card card-pad" }, h("h3", {}, "Claude AI (viết storyboard)"),
        h("label", { class: "field" }, h("span", { class: "lbl" }, "Anthropic API key"), key),
        h("div", { class: "row", style: { marginTop: "8px" } },
          s.anthropic_key_set ? h("label", { class: "check" }, h("input", { type: "checkbox", onchange: (e) => { clearKey = e.target.checked; } }), h("span", { class: "small" }, "Xoá key đã lưu")) : null,
          h("span", { class: "spacer" }), h("a", { class: "small", href: "https://console.anthropic.com/settings/keys", target: "_blank", rel: "noopener" }, "Lấy API key ↗")),
        h("p", { class: "small muted", style: { marginBottom: 0 } }, "Key được lưu trên máy này ở ~/.config/filmmaker/settings.json (chỉ tài khoản của bạn đọc được).")),
      h("div", { class: "card card-pad" }, h("h3", {}, "Giao diện"), h("label", { class: "field" }, h("span", { class: "lbl" }, "Chế độ màu"), theme))),
    buttons: [{ label: "Huỷ" }, { label: "Lưu", cls: "primary", onclick: async (c) => {
      try {
        await api("PUT", "/api/settings", { profile: profile.value.trim(), pause_min: +pmin.value, pause_max: +pmax.value, retries: +retries.value,
          anthropic_api_key: key.value.trim() || undefined, clear_anthropic_key: clearKey });
        S.env = await api("GET", "/api/env"); renderTopbar(); c(); toast("Đã lưu cài đặt", "ok");
      } catch (e) { toast(e.message, "error"); }
    } }],
  });
}

async function openCredits() {
  const body = h("div", {}, h("p", {}, h("span", { class: "spinner" }), " Đang mở Flow ngầm để đọc số dư… (khoảng 30–60 giây)"));
  modal({ title: "Credit Google Flow", body });
  try {
    const r = await api("POST", "/api/env/credits");
    if (r.ok) {
      const nums = [];
      const walk = (o, path) => {
        if (o && typeof o === "object") for (const [k, v] of Object.entries(o)) { if (k !== "status") walk(v, path ? `${path}.${k}` : k); }
        else if (typeof o === "number") nums.push([path, o]);
      };
      walk(r.data, "");
      put(body, 
        nums.length ? h("table", { class: "list" }, nums.map(([k, v]) => h("tr", {}, h("td", { class: "mono" }, k), h("td", { style: { fontWeight: 700, fontSize: "16px" } }, v.toLocaleString("vi-VN"))))) : null,
        h("details", {}, h("summary", { class: "small muted" }, "Dữ liệu gốc"), h("pre", { class: "box" }, JSON.stringify(r.data, null, 2))));
    } else {
      put(body, h("div", { class: "banner warn" }, "Không đọc được số dư: ", r.hint || ""), h("pre", { class: "box" }, r.error),
        h("p", { class: "small" }, "Bạn có thể xem trực tiếp trong menu tài khoản trên ", h("a", { href: "https://flow.google.com/", target: "_blank", rel: "noopener" }, "Google Flow ↗"), "."));
    }
  } catch (e) { put(body, h("div", { class: "banner err" }, e.message)); }
}

function applyTheme() {
  const t = localGet("theme", "auto");
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
}

applyTheme();
init();
