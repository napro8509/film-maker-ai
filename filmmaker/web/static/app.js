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
  done: ["done", "Xong"], changed: ["changed", "Đã sửa · cần render lại"], interrupted: ["interrupted", "Bị ngắt khi render"],
  failed: ["failed", "Lỗi"], todo: ["todo", "Chưa làm"], running: ["running", "Đang làm"],
};

/* ---------- icons (inline SVG, 24px grid, stroke = currentColor) ---------- */

const ICONS = {
  film: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
  play: '<path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.8"/><path d="m21 16-5-5-9 9"/>',
  video: '<rect x="2.5" y="6" width="13.5" height="12" rx="2"/><path d="m16 10.5 5.5-3v9l-5.5-3"/>',
  stitch: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 12.5 9 5 9-5"/><path d="m3 17 9 5 9-5"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  more: '<circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.4" fill="currentColor" stroke="none"/>',
  settings: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  coins: '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  up: '<path d="m6 15 6-6 6 6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  upload: '<path d="M12 20V9M7 14l5-5 5 5M5 4h14"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.3-5.7L20 8.5M20 3.5v5h-5"/>',
  sparkle: '<path d="M11 3l1.8 5.2L18 10l-5.2 1.8L11 17l-1.8-5.2L4 10l5.2-1.8z"/><path d="M18.5 15l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
  template: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
  import: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M12 10v7M9 14l3 3 3-3"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  alert: '<path d="M12 3.5 2.5 20h19z"/><path d="M12 10v4.5M12 17.2v.3"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.8v.4"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5a6.5 6.5 0 0 1 3.5 5.5"/>',
  code: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" stroke="none"/>',
  log: '<path d="m5 8 4 4-4 4M12 16h7"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
};
function icon(name, cls = "") {
  const t = document.createElement("template");
  t.innerHTML = `<svg class="ico${cls ? " " + cls : ""}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
  return t.content.firstChild;
}
/* Cost tags: the only two labels used for "does this spend Flow credit?" */
const FREE = () => h("span", { class: "cost free" }, "miễn phí");
const PAID = () => h("span", { class: "cost paid" }, "credit");
function badge(key, text) { const [c, l] = STATUS[key] || [key, text]; return h("span", { class: "badge " + c }, text || l); }
function field(label, control, hint) {
  return h("label", { class: "field" }, h("span", { class: "lbl" }, label, hint ? h("span", { class: "hint" }, " · " + hint) : null), control);
}

/* In the macOS app (pywebview) the page can call native helpers. */
const NATIVE = () => window.pywebview && window.pywebview.api;

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
  const t = h("div", { class: "toast " + kind, role: kind === "error" ? "alert" : "status" },
    kind === "error" ? icon("alert") : kind === "ok" ? icon("check") : null, h("span", {}, msg));
  $("#toasts").append(t);
  setTimeout(() => t.remove(), ms);
}

function modal({ title, body, buttons = [], wide = false, onClose }) {
  const root = $("#modal-root");
  const close = () => {
    ov.remove(); document.removeEventListener("keydown", onKey); onClose && onClose();
    if (prevFocus && prevFocus.isConnected && prevFocus.focus) prevFocus.focus();
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const foot = buttons.length ? h("div", { class: "modal-foot" }, buttons.map((b) =>
    h("button", { class: "btn " + (b.cls || ""), disabled: b.disabled, id: b.id, onclick: () => b.onclick ? b.onclick(close) : close() }, b.label))) : null;
  const prevFocus = document.activeElement;
  const ov = h("div", { class: "overlay", onmousedown: (e) => { if (e.target === ov) close(); } },
    h("div", { class: "modal" + (wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "modal-head" }, h("h2", {}, title), h("button", { class: "btn ghost icon", onclick: close, "aria-label": "Đóng", title: "Đóng (Esc)" }, icon("x"))),
      h("div", { class: "modal-body" }, body), foot));
  root.append(ov);
  document.addEventListener("keydown", onKey);
  // Focus the first field (or the main button) so the keyboard works straight away.
  setTimeout(() => {
    if (ov.contains(document.activeElement)) return;
    const f = ov.querySelector(".modal-body input:not([type=hidden]):not(.hidden), .modal-body textarea, .modal-body select") || ov.querySelector(".modal-foot .btn.primary, .modal-foot .btn.danger, .modal-foot .btn");
    f && f.focus();
  }, 20);
  return close;
}

/* Small dropdown menu anchored to a button. items: {label, icon, onclick, danger, href, download, extra} | "-" */
function popMenu(anchor, items, head) {
  document.querySelector(".menu")?.remove();
  const done = () => { m.remove(); document.removeEventListener("mousedown", outside, true); document.removeEventListener("keydown", key, true); anchor.setAttribute("aria-expanded", "false"); };
  const outside = (e) => { if (!m.contains(e.target) && !anchor.contains(e.target)) done(); };
  const key = (e) => {
    if (e.key === "Escape") { e.stopPropagation(); done(); anchor.focus(); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const all = [...m.querySelectorAll(".m-item")], i = all.indexOf(document.activeElement);
      all[(i + (e.key === "ArrowDown" ? 1 : -1) + all.length) % all.length]?.focus();
    }
  };
  const m = h("div", { class: "menu", role: "menu" },
    head ? h("div", { class: "m-head" }, head) : null,
    items.map((it) => it === "-" ? h("div", { class: "m-sep" }) : h(it.href ? "a" : "button", {
      class: "m-item" + (it.danger ? " danger" : ""), role: "menuitem", href: it.href, download: it.download, disabled: it.disabled,
      onclick: (e) => { done(); if (it.onclick) { e.preventDefault(); it.onclick(); } } }, it.icon ? icon(it.icon) : null, h("span", {}, it.label), it.extra || null)));
  document.body.append(m);
  const r = anchor.getBoundingClientRect(), w = m.offsetWidth;
  m.style.top = Math.min(r.bottom + 4, innerHeight - m.offsetHeight - 8) + "px";
  m.style.left = Math.max(8, Math.min(r.right - w, innerWidth - w - 8)) + "px";
  anchor.setAttribute("aria-expanded", "true");
  document.addEventListener("mousedown", outside, true);
  document.addEventListener("keydown", key, true);
  m.querySelector(".m-item")?.focus();
}

function confirmModal(title, text, okLabel = "Đồng ý", danger = false) {
  return new Promise((resolve) => {
    let done = false;
    modal({
      title, body: h("p", {}, text), onClose: () => { if (!done) resolve(false); },
      buttons: [
        { label: "Huỷ" },
        { label: okLabel, cls: danger ? "danger solid" : "primary", onclick: (c) => { done = true; resolve(true); c(); } },
      ],
    });
  });
}

function lightbox(url) {
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const close = () => { ov.remove(); document.removeEventListener("keydown", onKey); };
  const ov = h("div", { class: "overlay lightbox", onclick: close, title: "Bấm để đóng" }, h("img", { src: url, alt: "" }));
  $("#modal-root").append(ov);
  document.addEventListener("keydown", onKey);
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
  $("#btn-settings").prepend(icon("settings"));
  $("#btn-credits").prepend(icon("coins"));
  $(".brand .logo").append(icon("play"));
  $("#btn-menu").append(icon("menu"));
  $("#btn-menu").onclick = () => toggleSidebar();
  $("#scrim").onclick = () => toggleSidebar(false);
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); flushSave().then(() => toast("Đã lưu", "ok", 1500)); }
  });
  document.addEventListener("click", (e) => {  // WKWebView can't do <a download>: use a native Save dialog
    const a = e.target.closest && e.target.closest("a[download]");
    if (!a || !NATIVE()) return;
    e.preventDefault();
    NATIVE().save_download(a.getAttribute("href"), a.getAttribute("download") || "")
      .then((path) => { if (path) toast("Đã lưu: " + path, "ok"); })
      .catch((err) => toast(String(err && err.message || err), "error"));
  });
  window.addEventListener("beforeunload", (e) => { if (S.pendingSave || S.saving) { save(); e.preventDefault(); e.returnValue = ""; } });
  window.addEventListener("hashchange", () => {
    const id = decodeURIComponent(location.hash.slice(1));
    if (id && (!S.cur || S.cur.id !== id) && S.projects.some((p) => p.id === id)) openProject(id);
  });

  try {
    [S.env, S.settings] = await Promise.all([api("GET", "/api/env"), api("GET", "/api/settings")]);
  } catch (e) {
    put($("#main"), h("div", { class: "empty" }, h("div", { class: "hero" }, icon("alert")), h("h1", {}, "Không kết nối được máy chủ"),
      h("p", { class: "muted" }, e.message), h("button", { class: "btn primary", onclick: () => location.reload() }, icon("refresh"), "Thử lại")));
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

function toggleSidebar(open) {
  const on = document.body.classList.toggle("side-open", open);
  $("#btn-menu").setAttribute("aria-expanded", String(on));
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
  toggleSidebar(false);
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
  const t = { saved: "Đã lưu", pending: "Chưa lưu…", saving: "Đang lưu…", error: "Lỗi lưu: " + S.saveError }[S.saveState];
  return h("span", { class: "savestate" + (S.saveState === "error" ? " error" : ""), title: "Tự lưu khi bạn sửa · ⌘S để lưu ngay" },
    S.saveState === "saved" ? icon("check") : S.saveState === "error" ? icon("alert") : S.saveState === "saving" ? h("span", { class: "spinner" }) : null, t);
}

/* ---------- top bar ---------- */

function renderTopbar() {
  const e = S.env, box = $("#envchips");
  put(box,
    authChip(),
    claudeChip(),
    h("span", { class: "pill " + (e.gflow ? "ok" : "bad"), title: e.gflow || "Chưa cài gflow-cli" }, e.gflow ? "gflow" : "Thiếu gflow"),
    h("span", { class: "pill " + (e.ffmpeg ? "ok" : "bad"), title: e.ffmpeg || "Chưa cài ffmpeg" }, e.ffmpeg ? "ffmpeg" : "Thiếu ffmpeg"),
  );
  renderSideEnv();
}
/* On narrow windows the top bar hides the pills; the sidebar shows the same pills instead. */
function renderSideEnv() {
  const el = document.getElementById("side-env");
  if (!el || !S.env) return;
  put(el, [...$("#envchips").children].map((n) => { const c = n.cloneNode(true); c.onclick = n.onclick; return c; }));
}

function claudeChip() {
  const e = S.env, cc = e.claude_code || {};
  if (e.planner === "claude_code" && cc.logged_in)
    return h("span", { class: "pill ok", title: "Viết storyboard bằng Claude Code, dùng gói Claude của bạn (không cần API key)" }, "Claude");
  if (e.planner === "api") return h("span", { class: "pill ok", title: "Viết storyboard bằng Anthropic API key" }, "Claude · API key");
  if (cc.available) return h("button", { class: "pill warn", onclick: claudeLoginHelp, title: "Claude Code chưa đăng nhập. Bấm để xem cách đăng nhập" }, "Claude chưa đăng nhập");
  return h("button", { class: "pill warn", onclick: openSettings, title: "Bấm để mở Cài đặt" }, "Claude chưa thiết lập");
}

function claudeLoginHelp() {
  modal({
    title: "Đăng nhập Claude Code",
    body: h("div", { class: "stack" },
      h("p", {}, "Để viết storyboard bằng gói Claude (Pro/Max) của bạn mà không cần API key, Claude Code cần đăng nhập một lần:"),
      h("pre", { class: "box" }, "claude\n/login"),
      h("p", { class: "small muted" }, "Mở Terminal, gõ lệnh trên, chọn đăng nhập bằng tài khoản Claude. Xong thì bấm “Kiểm tra lại”.")),
    buttons: [{ label: "Đóng" }, { label: "Kiểm tra lại", cls: "primary", onclick: async (c) => {
      S.env = await api("GET", "/api/env?refresh=true"); renderTopbar(); c();
      toast(S.env.planner ? "Đã sẵn sàng" : "Vẫn chưa đăng nhập", S.env.planner ? "ok" : "error");
    } }],
  });
}

function authChip() {
  const a = S.auth;
  if (a === "checking") return h("span", { class: "pill busy" }, h("span", { class: "spinner" }), "Đang kiểm tra Google…");
  if (!a) return h("button", { class: "pill", onclick: checkAuth, title: "Mở Flow ngầm để kiểm tra phiên đăng nhập" }, "Google · kiểm tra");
  const when = a.at ? `\nKiểm tra ${ago(a.at)}` : "";
  return h("button", { class: "pill " + (a.ok ? "ok" : "bad"), onclick: checkAuth, title: (a.text || "") + when + "\n\nBấm để kiểm tra lại" },
    a.ok ? (a.account || "Đã đăng nhập Google") : "Chưa đăng nhập Google");
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
    h("button", { class: "btn primary block", onclick: openNewProject }, icon("plus"), "Phim mới"),
    h("div", { class: "side-title" }, h("span", { class: "spacer" }, "Dự án"), h("span", {}, S.projects.length || "")),
    S.projects.length ? null : h("p", { class: "muted small", style: { padding: "0 8px" } }, "Chưa có dự án nào."),
    S.projects.map((p) => {
      const pct = p.scenes ? Math.round((p.done / p.scenes) * 100) : 0;
      const active = S.cur && S.cur.id === p.id;
      return h("button", { class: "proj" + (active ? " active" : ""), onclick: () => openProject(p.id), title: p.title, "aria-current": active ? "page" : null },
        h("div", { class: "thumb", style: p.thumb ? { backgroundImage: `url("${p.thumb}")` } : null }, p.thumb ? null : icon("film")),
        h("div", { class: "meta" },
          h("div", { class: "name" }, p.title),
          h("div", { class: "sub" },
            running === p.id ? h("span", { class: "badge running mini" }, "Đang chạy") : p.has_final ? h("span", { class: "badge film mini" }, "Có phim") : null,
            h("span", {}, `${p.done}/${p.scenes} cảnh`)),
          h("div", { class: "bar" }, h("i", { style: { width: pct + "%" } }))));
    }),
    h("div", { class: "side-foot" },
      h("div", { id: "side-env", class: "env-side" }),
      h("div", {}, "Thư mục dự án", h("span", { class: "mono" }, S.env ? S.env.workspace : ""))),
  );
  renderSideEnv();
}

/* ---------- main ---------- */

function renderMain() {
  const m = $("#main");
  S.zones.clear();
  if (!S.cur) { put(m, renderEmpty()); return; }
  // Tabs follow the workflow: set up the look & cast → build scenes → finish the film. YAML is the escape hatch.
  const tabs = [["story", "users", "Phong cách & nhân vật", (S.cur.storyboard.characters || []).length],
    ["scenes", "video", "Cảnh", S.cur.storyboard.scenes?.length || 0],
    ["final", "stitch", "Ghép phim & ảnh bìa"], ["yaml", "code", "YAML"]];
  put(m, h("div", { class: "main-inner" },
    renderHeader(),
    zone("errors-zone", renderErrors),
    h("div", { class: "tabs", role: "tablist" },
      tabs.map(([k, ic, l, n]) => h("button", { class: S.tab === k ? "on" : "", role: "tab", "aria-selected": String(S.tab === k), onclick: () => switchTab(k) },
        icon(ic), l, n != null ? h("span", { class: "count" }, n) : null))),
    h("div", { role: "tabpanel" }, S.tab === "scenes" ? renderScenes() : S.tab === "story" ? renderStory() : S.tab === "final" ? renderFinal() : renderYaml()),
  ));
}

async function switchTab(k) {
  if (k === "yaml") { await flushSave(); await refreshCurrent(); }
  S.tab = k;
  renderMain();
}

function renderEmpty() {
  return h("div", { class: "empty" },
    h("div", { class: "hero" }, icon("film")),
    h("h1", {}, "Làm phim bằng Google Flow"),
    h("p", { class: "muted" }, "Từ một ý tưởng ra một bộ phim ngắn: viết kịch bản, tạo từng cảnh, rồi ghép lại."),
    h("div", { class: "steps" },
      [["Tạo dự án", "Viết storyboard bằng AI từ một câu ý tưởng, hoặc tự viết từng cảnh."],
        ["Tạo ảnh keyframe", "Miễn phí. Xem trước ảnh mở đầu của từng cảnh, sửa prompt đến khi ưng."],
        ["Render video", "Tốn credit. Mỗi cảnh thành một clip. Cảnh đã xong sẽ không bị làm lại."],
        ["Ghép phim", "Nối các clip, thêm nhạc nền và phụ đề, xuất ra file MP4."]]
        .map(([t, d], i) => h("div", { class: "card step" }, h("div", { class: "n" }, i + 1), h("div", {}, h("b", {}, t), h("div", { class: "muted small" }, d))))),
    h("button", { class: "btn primary", onclick: openNewProject }, icon("plus"), "Tạo phim đầu tiên"));
}

function jobRunning() { return !!(S.job && S.job.status === "running"); }
function sceneDur(sc) { return Number(sc.duration) || 8; }

function renderHeader() {
  const sb = S.cur.storyboard;
  return h("div", { class: "phead" },
    h("div", { class: "title-col" },
      h("h1", {}, sb.title || "(chưa đặt tên)"),
      zone("stats-zone", () => {
        const total = sb.scenes?.length || 0, done = S.cur.done || 0;
        const len = (sb.scenes || []).reduce((a, s) => a + sceneDur(s), 0);
        return h("div", { class: "stats" },
          h("span", {}, h("span", { class: "bar" }, h("i", { style: { width: (total ? done / total * 100 : 0) + "%" } })), `${done}/${total} cảnh xong`),
          h("span", {}, `~${fmtDur(len)}`),
          h("span", {}, sb.aspect === "9:16" ? "Dọc 9:16" : "Ngang 16:9"),
          S.cur.has_final ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); switchTab("final"); } }, "Xem phim đã ghép") : null,
          h("span", { id: "savestate" }, renderSaveState()));
      })),
    zone("actions-zone", () => {
      const busy = jobRunning();
      // Pipeline order, one primary action (render). Dry-run and file actions live in the ⋯ menu.
      return h("div", { class: "actions" },
        h("button", { class: "btn", disabled: busy, onclick: () => runAction("keyframes"), title: "Tạo ảnh mở đầu cho các cảnh kiểu Ảnh → Video (miễn phí)" }, icon("image"), "Tạo keyframe", FREE()),
        h("button", { class: "btn primary", disabled: busy, onclick: () => runAction("video"), title: "Render các cảnh chưa xong (tốn credit, có bước xác nhận)" }, icon("video"), "Render video", PAID()),
        h("button", { class: "btn", disabled: busy, onclick: () => switchTab("final"), title: "Ghép các clip thành phim, thêm nhạc và ảnh bìa" }, icon("stitch"), "Ghép phim"),
        h("button", { class: "btn icon", title: "Thêm thao tác", "aria-label": "Thêm thao tác", "aria-haspopup": "menu", onclick: (e) => projectMenu(e.currentTarget) }, icon("more")));
    }));
}

function projectMenu(anchor) {
  const id = S.cur.id;
  popMenu(anchor, [
    { label: "Xem trước lệnh gửi Flow", icon: "eye", extra: FREE(), disabled: jobRunning(), onclick: () => runAction("dry") },
    "-",
    { label: "Mở thư mục dự án", icon: "folder", onclick: () => api("POST", `/api/projects/${id}/reveal`, {}).catch((e) => toast(e.message, "error")) },
    { label: "Nhân bản dự án", icon: "copy", onclick: async () => {
      try { await flushSave(); const r = await api("POST", `/api/projects/${id}/duplicate`); await loadProjects(); openProject(r.id); toast("Đã nhân bản (không gồm video đã render)"); }
      catch (e) { toast(e.message, "error"); }
    } },
    { label: "Tải storyboard.yaml", icon: "download", href: `/api/projects/${id}/file/storyboard.yaml`, download: `${id}.yaml` },
    "-",
    { label: "Xoá dự án…", icon: "trash", danger: true, onclick: async () => {
      if (!(await confirmModal("Xoá dự án?", "Dự án sẽ được chuyển vào thư mục .trash trong workspace (có thể khôi phục bằng tay).", "Xoá dự án", true))) return;
      try { await api("DELETE", `/api/projects/${id}`); S.cur = null; await loadProjects(); if (S.projects[0]) openProject(S.projects[0].id); else renderMain(); toast("Đã chuyển vào .trash"); }
      catch (e) { toast(e.message, "error"); }
    } },
  ], [S.cur.path, S.cur.project_id ? "Flow: " + S.cur.project_id : null].filter(Boolean).join("\n"));
}

function renderErrors() {
  const errs = S.cur.errors || [];
  const sb = S.cur.storyboard;
  const fb = !!sb.fallback_t2v;
  const i2v = S.cur.i2v_unsupported ? h("div", { class: "banner " + (S.env?.flow_ui ? "info" : "warn") }, icon(S.env?.flow_ui ? "info" : "alert"),
    h("div", { class: "b-body" },
      h("div", {}, h("b", {}, "gflow chưa tạo được video từ ảnh trên tài khoản Flow này. "),
        S.env?.flow_ui ? "Film Maker sẽ tự làm Ảnh → Video qua giao diện chat của Flow (tải ảnh lên, gõ prompt, bấm Approve) bằng trình duyệt đã đăng nhập của gflow." : fb ? "Bạn đã bật “Tự chuyển sang Chữ → Video”, nên các cảnh “Ảnh → Video” đang được render bằng Chữ → Video (ảnh keyframe chỉ để tham khảo)."
          : "Các cảnh “Ảnh → Video” sẽ dừng lại thay vì tự chuyển sang Chữ → Video. Lần thử vừa rồi bị từ chối trước khi gửi nên chưa trừ credit."),
      h("details", {}, h("summary", {}, "Vì sao và cách sửa"),
        h("div", { class: "small" },
          "Thường là do Google đã chuyển tài khoản sang giao diện Flow mới (flow.google.com, chỉ có ô chat), nơi gflow mới hỗ trợ Chữ → Ảnh và Chữ → Video. Cách sửa: cập nhật gflow-cli (",
          h("code", {}, "uv tool upgrade gflow-cli"), "), khởi động lại Film Maker rồi bấm “Thử lại Ảnh → Video”.",
          S.env?.gflow_version ? ` Bản gflow đang dùng: ${S.env.gflow_version}.` : ""),
        S.cur.i2v_error ? h("details", {}, h("summary", {}, "Lỗi gốc từ gflow"), h("pre", {}, S.cur.i2v_error)) : null),
      h("div", { class: "row" },
      h("button", { class: "btn sm", onclick: async () => {
        try { const d = await api("POST", `/api/projects/${S.cur.id}/reset-i2v`); applyMeta(d); renderMain(); toast("Lần render sau sẽ thử lại Ảnh → Video. Nếu vẫn bị từ chối thì dừng lại, không tốn credit."); }
        catch (e) { toast(e.message, "error"); }
      } }, "Thử lại Ảnh → Video"),
      h("button", { class: "btn sm", onclick: () => { sb.fallback_t2v = fb ? undefined : true; changed(); renderMain(); } },
        fb ? "Tắt tự chuyển sang Chữ → Video" : "Dùng Chữ → Video thay thế (tốn credit)")))) : null;
  if (!errs.length) return i2v || h("div");
  return h("div", {}, i2v, h("div", { class: "banner err", role: "alert" }, icon("alert"), h("div", { class: "b-body" }, h("b", {}, "Storyboard còn lỗi, chưa chạy được:"),
    h("ul", {}, errs.map((e) => h("li", {}, prettyError(e)))))));
}
function prettyError(e) {
  return e.replace(/^scenes\.(\d+)(?:\.|:\s*(?:scene [^:]+:\s*)?)/, (_, i) => `Cảnh ${+i + 1}: `).replace("Field required", "chưa điền")
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
  } }, icon("plus"), "Thêm cảnh"));
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
  const text = (key, label, ph, rows = 2, help) => field(label,
    h("textarea", { rows, placeholder: ph, value: sc[key] || "", oninput: (e) => { sc[key] = e.target.value; changed(); refreshZone(`chars-${i}`); } }), help);

  const cardId = "scene-" + i;
  return h("div", { class: "card scene", id: cardId },
    h("div", { class: "scene-head" },
      h("span", { class: "scene-num" }, i + 1),
      h("input", { type: "text", class: "scene-id", value: sc.id, "aria-label": "Mã cảnh", title: "Mã cảnh (đổi mã sẽ mất liên kết với clip đã render)",
        onchange: (e) => { const v = e.target.value.trim().replace(/[^A-Za-z0-9_-]/g, ""); if (!v) { e.target.value = sc.id; return; } sc.id = v; e.target.value = v; changed(); } }),
      h("div", { class: "seg", role: "group", "aria-label": "Kiểu cảnh" }, MODES.map(([k, l, t]) => h("button", { class: sc.mode === k ? "on" : "", title: t, "aria-pressed": String(sc.mode === k),
        onclick: () => { sc.mode = k; changed(); renderMain(); } }, l))),
      zone(`badge-${i}`, () => badge(STATUS[sceneStatusKey(sc)] ? sceneStatusKey(sc) : "todo")),
      h("div", { class: "scene-tools" },
      h("button", { class: "btn ghost icon sm", title: "Chuyển lên", "aria-label": "Chuyển lên", disabled: i === 0, onclick: () => move(-1) }, icon("up")),
      h("button", { class: "btn ghost icon sm", title: "Chuyển xuống", "aria-label": "Chuyển xuống", disabled: i === sb.scenes.length - 1, onclick: () => move(1) }, icon("down")),
      h("button", { class: "btn ghost icon sm", title: "Nhân bản cảnh", "aria-label": "Nhân bản cảnh", onclick: () => { sb.scenes.splice(i + 1, 0, { ...sc, id: nextSceneId() }); changed(); renderMain(); } }, icon("copy")),
      h("button", { class: "btn ghost icon sm danger", title: "Xoá cảnh", "aria-label": "Xoá cảnh", onclick: async () => {
        if (sb.scenes.length === 1) { toast("Phim cần ít nhất một cảnh", "error"); return; }
        const hasWork = S.cur.scene_state?.[sc.id]?.clip_url;
        if (hasWork && !(await confirmModal("Xoá cảnh?", `Cảnh ${sc.id} đã có clip. Clip vẫn nằm trong thư mục output nhưng sẽ không được ghép vào phim.`, "Xoá", true))) return;
        sb.scenes.splice(i, 1); changed(); renderMain();
      } }, icon("trash")))),
    h("div", { class: "scene-body" },
      h("div", { class: "media-col" }, zone(`media-${i}`, () => renderSceneMedia(sc)), zone(`sactions-${i}`, () => renderSceneActions(sc))),
      h("div", { class: "scene-fields" },
        sc.mode === "keyframe" ? text("image_prompt", "Ảnh mở đầu", "Mô tả khung hình đầu tiên như một bức ảnh: chủ thể, bối cảnh, ánh sáng, góc máy…", 3, "nên viết tiếng Anh") : null,
        sc.mode === "i2v" ? h("div", { class: "grid2" }, imagePicker(sc, "image", "Ảnh bắt đầu"), imagePicker(sc, "end_image", "Ảnh kết thúc (tuỳ chọn)", true)) : null,
        text("prompt", sc.mode === "t2v" ? "Nội dung video" : "Chuyển động / hành động", "Một hành động + một chuyển động máy quay, ví dụ: slow dolly-in, she smiles…", 3, "nên viết tiếng Anh"),
        zone(`chars-${i}`, () => {
          const text = `${sc.prompt || ""} ${sc.mode === "keyframe" ? sc.image_prompt || "" : ""}`;
          const found = (sb.characters || []).filter((c) => c.name && new RegExp(`\\b${c.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i").test(text));
          if (!found.length) return h("div");
          const refsOn = sb.use_character_refs !== false && sc.mode !== "i2v";
          let nRefs = 0;
          return h("div", { class: "char-chips" }, h("span", { class: "small muted", title: refsOn ? "Mô tả nhân vật được tự thêm vào prompt và ảnh nhân vật được gửi kèm làm tham chiếu" : "Mô tả nhân vật được tự thêm vào prompt" }, refsOn ? "Nhân vật · mô tả + ảnh:" : "Nhân vật · mô tả:"),
            found.map((c) => {
              const url = refsOn && c.image && nRefs < 3 ? charImageUrl(c) : null;
              if (url) nRefs++;
              return h("span", { class: "char-chip" + (url ? " has-img" : ""), title: url ? `${c.description}\n(gửi kèm ảnh nhân vật)` : c.description },
                url ? h("img", { src: url, alt: "" }) : null, c.name);
            }));
        }),
        h("div", { class: "grid3" },
          field("Độ dài", durationSelect(sc)),
          field("Model video",
            h("select", { value: sc.model || "", onchange: (e) => { sc.model = e.target.value || undefined; changed(); renderMain(); } },
              h("option", { value: "" }, `Theo phim (${sb.model || "mặc định"})`),
              videoModels().map((m) => h("option", { value: m }, m)))),
          field("Phụ đề / lời dẫn",
            h("input", { type: "text", value: sc.narration || "", placeholder: "Không bắt buộc", oninput: (e) => { sc.narration = e.target.value; changed(); } }))),
        zone(`preview-${i}`, () => {
          const st = S.cur.scene_state?.[sc.id];
          if (!st) return h("div");
          return h("details", { class: "prompt-preview", open: localGet("previewOpen", false) || null, ontoggle: (e) => localSet("previewOpen", e.target.open) },
            h("summary", {}, "Prompt thực tế gửi lên Flow"),
            st.image_prompt ? [h("div", { class: "small muted" }, "Ảnh"), h("pre", {}, st.image_prompt)] : null,
            h("div", { class: "small muted" }, "Video"), h("pre", {}, st.video_prompt));
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
      h("div", { class: "pthumb", title: url ? "Bấm để xem lớn" : null, style: url ? { backgroundImage: `url("${url}")`, cursor: "zoom-in" } : null, onclick: (e) => { if (url) { e.preventDefault(); lightbox(url); } } }),
      h("select", { value: val, onchange: (e) => { sc[attr] = e.target.value || undefined; changed(); renderMain(); } },
        h("option", { value: "" }, optional ? "— không dùng —" : "— chọn ảnh —"),
        isAsset ? null : h("option", { value: val }, val),
        S.assets.images.map((a) => h("option", { value: a.path }, a.path.replace("assets/", "")))),
      h("button", { class: "btn sm", onclick: (e) => { e.preventDefault(); fileInput.click(); } }, icon("upload"), "Tải lên"), fileInput));
}

function renderSceneMedia(sc) {
  const st = S.cur.scene_state?.[sc.id] || {};
  const portrait = S.cur.storyboard.aspect === "9:16";
  const box = h("div", { class: "media" + (portrait ? " portrait" : "") });
  const kf = st.keyframe_url;
  const startImg = sc.mode === "i2v" ? (assetUrl(sc.image) || st.image_url) : null;
  const stale = st.status === "changed";
  if (st.clip_url) {
    box.append(h("video", { src: st.clip_url, controls: true, preload: "metadata", poster: st.rendered_as === "t2v" || st.rendered_as === "r2v" ? null : (kf || startImg || null), playsinline: true }));
    if (stale) box.append(h("span", { class: "tag warn" }, "Clip cũ · prompt đã đổi"));
    else if (st.rendered_as === "t2v" && sc.mode === "keyframe") box.append(h("span", { class: "tag" }, "Render bằng Chữ → Video"));
    else if (st.rendered_as === "flow-ui") box.append(h("span", { class: "tag" }, "Render qua giao diện chat Flow"));
    else if (st.rendered_as === "r2v") box.append(h("span", { class: "tag" }, sc.mode === "keyframe" ? "Render bằng Chữ → Video + ảnh nhân vật" : "Có ảnh nhân vật"));
  } else if (kf) {
    box.append(h("img", { src: kf, onclick: () => lightbox(kf), alt: "keyframe" }));
    box.append(h("span", { class: "tag" + (st.keyframe_stale ? " warn" : "") }, st.keyframe_stale ? "Ảnh cũ · prompt đã đổi" : "Keyframe · chưa có video"));
  } else if (startImg) {
    box.append(h("img", { src: startImg, onclick: () => lightbox(startImg), alt: "ảnh bắt đầu" }));
    box.append(h("span", { class: "tag" }, "Ảnh của bạn"));
  } else {
    box.append(h("div", { class: "ph" }, icon(sc.mode === "t2v" ? "video" : "image"),
      h("span", {}, sc.mode === "keyframe" ? "Bấm “Tạo ảnh” để xem trước khung hình (miễn phí)" : sc.mode === "i2v" ? "Chọn hoặc tải ảnh bắt đầu" : "Video sẽ hiện ở đây sau khi render")));
  }
  return h("div", { class: "stack tight" }, box);
}

function renderSceneActions(sc) {
  const st = S.cur.scene_state?.[sc.id] || {};
  const busy = jobRunning();
  const kids = [];
  if (st.status === "failed" && st.error) kids.push(h("div", { class: "scene-msg err" }, st.error));
  if (st.status === "interrupted") {
    kids.push(h("div", { class: "scene-msg warn" }, "Lần trước bị ngắt khi đang render, có thể Flow đã trừ credit. Hãy mở Flow xem clip đã có chưa trước khi gửi lại."));
  }
  const row = h("div", { class: "row" });
  if (sc.mode === "keyframe") {
    const hasKf = !!st.keyframe_url && !st.keyframe_stale;
    row.append(h("button", { class: "btn sm", disabled: busy, title: "Ảnh trên Flow miễn phí",
      onclick: () => runAction("keyframes", { only: [sc.id], force_keyframes: [sc.id] }) }, icon(hasKf ? "refresh" : "image"), hasKf ? "Tạo lại ảnh" : "Tạo ảnh", FREE()));
  }
  const done = st.status === "done";
  const label = done ? "Render lại" : st.status === "interrupted" ? "Gửi lại" : "Render";
  row.append(h("button", { class: "btn sm", disabled: busy,
    onclick: () => runAction("video", { only: [sc.id], force: done || st.status === "interrupted" ? [sc.id] : [] }) },
  icon(done ? "refresh" : "video"), label, PAID()));
  if (st.clip_url && st.keyframe_url) row.append(h("button", { class: "btn sm ghost icon", title: "Xem ảnh keyframe", "aria-label": "Xem ảnh keyframe", onclick: () => lightbox(st.keyframe_url) }, icon("image")));
  if (st.clip_url) row.append(h("a", { class: "btn sm ghost icon", href: st.clip_url.split("?")[0], download: `${sc.id}.mp4`, title: "Tải clip", "aria-label": "Tải clip" }, icon("download")));
  kids.push(row);
  return h("div", { class: "stack tight" }, kids);
}

/* ---------- story tab ---------- */

function renderStory() {
  const sb = S.cur.storyboard;
  sb.characters = sb.characters || [];
  const set = (k, v) => { sb[k] = v; changed(); };
  const imageModels = (S.models.image || []).length ? S.models.image : [{ alias: "nano2", label: "Nano Banana 2" }];
  return h("div", { class: "stack" },
    h("div", { class: "card card-pad" },
      h("div", { class: "card-head" }, h("h3", {}, "Thông tin phim")),
      h("div", { class: "stack" },
        field("Tên phim",
          h("input", { type: "text", value: sb.title || "", oninput: (e) => { set("title", e.target.value); const t = document.querySelector(".phead h1"); if (t) t.textContent = e.target.value || "(chưa đặt tên)"; } })),
        h("div", { class: "grid3" },
          h("div", { class: "field" }, h("span", { class: "lbl" }, "Khung hình"),
            h("div", { class: "seg", role: "group", "aria-label": "Khung hình" }, [["16:9", "Ngang 16:9"], ["9:16", "Dọc 9:16"]].map(([v, l]) =>
              h("button", { class: (sb.aspect || "16:9") === v ? "on" : "", "aria-pressed": String((sb.aspect || "16:9") === v), onclick: () => { set("aspect", v); renderMain(); } }, l)))),
          field("Model video",
            h("select", { value: sb.model || "", onchange: (e) => { set("model", e.target.value || undefined); } },
              h("option", { value: "" }, "Mặc định của Flow"), videoModels().map((m) => h("option", { value: m }, m)))),
          field("Model ảnh (keyframe)",
            h("select", { value: sb.image_model || "nano2", onchange: (e) => set("image_model", e.target.value) },
              imageModels.map((m) => h("option", { value: m.alias }, m.label ? `${m.label} (${m.alias})` : m.alias))))),
        h("p", { class: "help" }, "Mẹo: veo-fast rẻ và nhanh để thử; chuyển sang veo-quality khi đã ưng kịch bản. Đổi model sẽ làm các cảnh cần render lại."),
        field("Phong cách hình ảnh",
          h("textarea", { rows: 2, value: sb.style || "", placeholder: "cinematic, warm morning light, shallow depth of field, 35mm film grain",
            oninput: (e) => set("style", e.target.value) }), "thêm vào mọi prompt"),
        h("div", { class: "stack tight" },
          h("label", { class: "check" },
            h("input", { type: "checkbox", checked: sb.no_text !== false,
              onchange: (e) => { sb.no_text = e.target.checked ? undefined : false; changed(); } }),
            h("span", {}, "Không cho Flow chèn chữ, phụ đề, watermark vào ảnh và video", h("span", { class: "sub" }, "Phụ đề sẽ được thêm khi ghép phim."))),
          h("label", { class: "check" },
            h("input", { type: "checkbox", checked: !!sb.fallback_t2v,
              onchange: (e) => { sb.fallback_t2v = e.target.checked ? true : undefined; changed(); renderMain(); } }),
            h("span", {}, "Tự chuyển sang Chữ → Video khi tài khoản Flow không làm được Ảnh → Video", h("span", { class: "sub" }, "Mặc định: dừng lại để bạn quyết định.")))),
        h("details", {}, h("summary", {}, "Nâng cao"),
          field("Dùng lại project có sẵn trên Flow (project_id)",
            h("input", { type: "text", value: sb.project_id || "", placeholder: S.cur.project_id ? `Đang dùng: ${S.cur.project_id}` : "Để trống: tự tạo project mới",
              oninput: (e) => set("project_id", e.target.value || undefined) }))))),
    renderCharacters(sb));
}

function charImageUrl(c) {
  if (!c.image) return null;
  const i = (S.cur.storyboard.characters || []).indexOf(c);
  const cs = S.cur.characters_state?.[i];
  return assetUrl(c.image) || (cs && cs.name === c.name ? cs.image_url : null);
}

async function makePortraits(names, force = false) {
  await flushSave();
  if (S.saveState === "error") { toast("Chưa lưu được storyboard: " + S.saveError, "error"); return; }
  if (S.cur.errors?.length) { toast("Storyboard còn lỗi: " + prettyError(S.cur.errors[0]), "error"); return; }
  if (!S.env.gflow) { toast("Chưa cài gflow-cli", "error"); return; }
  try {
    startJob(await api("POST", `/api/projects/${S.cur.id}/characters/portraits`, { names, force }));
  } catch (e) { toast(e.message, "error"); }
}

function renderCharacters(sb) {
  const busy = jobRunning();
  const missing = sb.characters.filter((c) => c.name && c.description && !charImageUrl(c));
  const add = () => { sb.characters.push({ name: "", description: "" }); changed(); renderMain(); };
  return h("div", { class: "card card-pad" },
    h("div", { class: "card-head" }, h("h3", {}, "Nhân vật"), h("span", { class: "spacer" }),
      missing.length ? h("button", { class: "btn sm", disabled: busy, title: "Ảnh trên Flow miễn phí",
        onclick: () => makePortraits([]) }, icon("image"), `Tạo ảnh cho ${missing.length} nhân vật`, FREE()) : null,
      h("button", { class: "btn sm", onclick: add }, icon("plus"), "Thêm nhân vật"),
      h("p", { class: "help" }, "Mô tả ngoại hình một lần và tạo hoặc tải lên một ảnh. Khi tên nhân vật xuất hiện trong prompt của cảnh, mô tả được tự thêm vào và ảnh được gửi kèm làm tham chiếu (tối đa 3 nhân vật mỗi cảnh) để khuôn mặt giống nhau ở mọi cảnh.")),
    h("label", { class: "check", style: { marginBottom: "16px" } },
      h("input", { type: "checkbox", checked: sb.use_character_refs !== false,
        onchange: (e) => { sb.use_character_refs = e.target.checked ? undefined : false; changed(); renderMain(); } }),
      h("span", {}, "Gửi ảnh nhân vật làm tham chiếu khi tạo keyframe và video")),
    sb.characters.length ? sb.characters.map((c, i) => renderCharacter(sb, c, i, busy))
      : h("div", { class: "empty-inline" }, icon("users"), "Chưa có nhân vật.", h("button", { class: "btn sm", onclick: add }, icon("plus"), "Thêm nhân vật")));
}

function renderCharacter(sb, c, i, busy) {
  const url = charImageUrl(c);
  const cs = S.cur.characters_state?.[i];
  const portrait = (sb.aspect || "16:9") === "9:16";
  const fileInput = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", class: "hidden", onchange: async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f); fd.append("kind", "character");
    try {
      const r = await api("POST", `/api/projects/${S.cur.id}/upload`, fd, true);
      S.assets = await api("GET", `/api/projects/${S.cur.id}/assets`);
      c.image = r.path; changed(); renderMain();
    } catch (err) { toast(err.message, "error"); }
  } });
  const canMake = !!(c.name && c.description);
  const isAsset = !c.image || S.assets.images.some((a) => a.path === c.image);
  return h("div", { class: "char-row" },
    h("div", { class: "char-img" + (portrait ? " portrait" : ""), style: url ? { backgroundImage: `url("${url}")` } : { cursor: "default" },
      title: url ? "Bấm để xem lớn" : "", onclick: () => { if (url) lightbox(url); } },
      url ? null : h("span", {}, c.image && cs?.image_missing ? "Không thấy file ảnh" : "Chưa có ảnh")),
    h("div", { class: "stack tight", style: { minWidth: 0 } },
      h("input", { type: "text", value: c.name, placeholder: "Tên, ví dụ: Lan", "aria-label": "Tên nhân vật", oninput: (e) => { c.name = e.target.value; changed(); } }),
      h("textarea", { rows: 3, value: c.description, "aria-label": "Mô tả ngoại hình", placeholder: "a Vietnamese woman in her late 20s, shoulder-length black hair, cream linen shirt…",
        oninput: (e) => { c.description = e.target.value; changed(); } }),
      h("div", { class: "row" },
        h("button", { class: "btn sm", disabled: busy || !canMake, title: canMake ? "Ảnh trên Flow miễn phí" : "Cần nhập tên và mô tả trước",
          onclick: () => makePortraits([c.name], true) }, icon(url ? "refresh" : "image"), url ? "Tạo lại ảnh" : "Tạo ảnh", FREE()),
        h("button", { class: "btn sm", onclick: () => fileInput.click() }, icon("upload"), "Tải lên"),
        h("select", { class: "sm", value: c.image || "", title: "Chọn ảnh có sẵn trong dự án", "aria-label": "Ảnh nhân vật",
          onchange: (e) => { c.image = e.target.value || undefined; changed(); renderMain(); } },
          h("option", { value: "" }, "— không dùng ảnh —"),
          isAsset ? null : h("option", { value: c.image }, c.image),
          S.assets.images.map((a) => h("option", { value: a.path }, a.path.replace("assets/", "")))),
        fileInput),
      h("details", {}, h("summary", {}, "Prompt tạo ảnh nhân vật"),
        h("textarea", { rows: 3, value: c.image_prompt || "",
          placeholder: cs?.portrait_prompt || "Để trống: tự tạo từ mô tả (toàn thân, nhìn thẳng, nền trơn)",
          oninput: (e) => { c.image_prompt = e.target.value || undefined; changed(); } }),
        h("p", { class: "help" }, "Nên dùng ảnh rõ mặt, nền trơn. Đổi ảnh nhân vật sẽ làm các cảnh có nhân vật này cần tạo lại."))),
    h("button", { class: "btn ghost icon sm danger", title: "Xoá nhân vật", "aria-label": "Xoá nhân vật " + (c.name || ""), onclick: async () => {
      if ((c.name || c.description) && !(await confirmModal("Xoá nhân vật?", `Xoá “${c.name || "nhân vật chưa đặt tên"}” khỏi storyboard. File ảnh vẫn nằm trong thư mục assets.`, "Xoá", true))) return;
      sb.characters.splice(i, 1); changed(); renderMain();
    } }, icon("trash")));
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
        if (!S.cur.final_url) return h("div", { class: "card card-pad" }, h("div", { class: "card-head" }, h("h3", {}, "Phim hoàn chỉnh")),
          h("div", { class: "player" + (portrait ? " portrait" : "") }, h("div", { class: "ph" }, icon("film"), h("span", {}, "Chưa ghép phim. Render xong các cảnh rồi bấm “Ghép phim”."))));
        return h("div", { class: "card card-pad" },
          h("div", { class: "card-head" }, h("h3", {}, "Phim hoàn chỉnh"), h("span", { class: "spacer" }), S.cur.final_size ? h("span", { class: "small muted" }, fmtBytes(S.cur.final_size)) : null),
          h("div", { class: "player" + (portrait ? " portrait" : "") }, h("video", { src: S.cur.final_url, controls: true, preload: "metadata", playsinline: true })),
          h("div", { class: "row", style: { marginTop: "12px" } },
            h("a", { class: "btn primary sm", href: S.cur.final_url.split("?")[0], download: `${S.cur.id}.mp4` }, icon("download"), "Tải MP4"),
            S.cur.srt_url ? h("a", { class: "btn sm", href: S.cur.srt_url.split("?")[0], download: `${S.cur.id}.srt` }, icon("download"), "Phụ đề .srt") : null,
            h("button", { class: "btn sm ghost", onclick: () => api("POST", `/api/projects/${S.cur.id}/reveal`, { path: "output/storyboard/final.mp4" }).catch((e) => toast(e.message, "error")) }, icon("folder"), "Mở trong Finder")));
      }),
      zone("playlist-zone", renderPlaylist)),
    h("div", { class: "stack" },
      h("div", { class: "card card-pad" }, h("div", { class: "card-head" }, h("h3", {}, "Ghép phim")),
        h("div", { class: "stack" },
          h("div", { class: "field" }, h("span", { class: "lbl" }, "Nhạc nền"),
            h("div", { class: "row", style: { flexWrap: "nowrap" } },
              h("select", { style: { flex: "1" }, "aria-label": "Nhạc nền", value: p.music || "", onchange: (e) => { saveFinalPrefs({ ...finalPrefs(), music: e.target.value || null }); renderMain(); } },
                h("option", { value: "" }, "Không có nhạc"), S.assets.audio.map((a) => h("option", { value: a.path }, a.path.replace("assets/", "")))),
              h("button", { class: "btn sm", onclick: () => musicInput.click() }, icon("upload"), "Tải lên"), musicInput)),
          musicUrl ? h("audio", { src: musicUrl, controls: true, style: { width: "100%" } }) : null,
          p.music ? h("label", { class: "field" }, h("span", { class: "lbl" }, "Âm lượng nhạc · ", h("span", { id: "volv" }, Math.round(p.volume * 100) + "%")),
            h("input", { type: "range", min: 0, max: 1, step: 0.05, value: p.volume,
              oninput: (e) => { document.getElementById("volv").textContent = Math.round(e.target.value * 100) + "%"; },
              onchange: (e) => saveFinalPrefs({ ...finalPrefs(), volume: +e.target.value }) })) : null,
          h("label", { class: "check" }, h("input", { type: "checkbox", checked: p.burn_subtitles, onchange: (e) => saveFinalPrefs({ ...finalPrefs(), burn_subtitles: e.target.checked }) }),
            h("span", {}, "In phụ đề lên hình", h("span", { class: "sub" }, "Luôn xuất thêm file .srt riêng nếu có lời dẫn."))),
          h("label", { class: "check" }, h("input", { type: "checkbox", checked: p.allow_missing, onchange: (e) => saveFinalPrefs({ ...finalPrefs(), allow_missing: e.target.checked }) }),
            h("span", {}, "Ghép cả khi còn thiếu cảnh", h("span", { class: "sub" }, "Bỏ qua các cảnh chưa render để xem thử bản nháp."))),
          zone("final-actions", () => {
            const n = readyClips().length, busy = jobRunning();
            return h("div", { class: "stack tight" },
              h("div", { class: "divider" }),
              h("div", { class: "row" },
                h("button", { class: "btn primary", disabled: busy || !n, onclick: () => runAction("stitch") }, icon("stitch"), "Ghép phim", FREE()),
                h("button", { class: "btn", disabled: busy, onclick: () => runAction("make"), title: "Render các cảnh còn thiếu rồi ghép" }, "Render nốt + ghép", PAID())),
              h("div", { class: "help" }, `${n}/${total} cảnh đã có clip.`));
          }))),
      renderThumbnail(),
      h("div", { class: "card card-pad" }, h("div", { class: "card-head" }, h("h3", {}, "Lời dẫn / phụ đề")),
        (S.cur.storyboard.scenes || []).some((s) => s.narration)
          ? h("ol", { class: "narr-list" }, S.cur.storyboard.scenes.filter((s) => s.narration).map((s) => h("li", {}, h("span", { class: "mono muted" }, s.id + " "), s.narration)))
          : h("p", { class: "help" }, "Chưa có. Thêm “Phụ đề / lời dẫn” ở từng cảnh."))));
}

function renderThumbnail() {
  const sb = S.cur.storyboard;
  sb.thumbnail = sb.thumbnail || {};
  const set = (k, v) => { sb.thumbnail[k] = v || undefined; changed(); if (k === "text") refreshZone("thumb-suggestions"); };
  setTimeout(maybeAutoSuggest, 0);
  return h("div", { class: "card card-pad" },
    h("div", { class: "card-head" }, h("h3", {}, "Ảnh bìa (thumbnail)")),
    zone("thumb-view", thumbnailView),
    h("div", { class: "stack", style: { marginTop: "12px" } },
      h("div", { class: "field" }, h("label", { class: "lbl", for: "thumb-title" }, "Tiêu đề in trên ảnh"),
        h("div", { class: "row", style: { flexWrap: "nowrap" } },
          h("input", { type: "text", id: "thumb-title", style: { flex: "1" }, value: sb.thumbnail.text || "", placeholder: sb.title || "Tiêu đề video",
            oninput: (e) => set("text", e.target.value) }),
          zone("thumb-suggest-btn", () => h("button", { class: "btn", disabled: !!S.titleBusy || !S.env.planner,
            title: S.env.planner ? "Claude gợi ý vài tiêu đề dựa trên nội dung phim" : "Cần Claude Code hoặc API key (xem Cài đặt)",
            onclick: () => suggestTitles(true) }, S.titleBusy ? h("span", { class: "spinner" }) : icon("sparkle"), "Gợi ý"))),
        zone("thumb-suggestions", titleChips)),
      h("details", {}, h("summary", {}, "Mô tả ảnh bìa (tiếng Anh, không cần ghi chữ)"),
        h("textarea", { rows: 3, value: sb.thumbnail.prompt || "",
          placeholder: "Để trống: các nhân vật chính ở khoảnh khắc kịch tính nhất. Ví dụ: Mali crying while holding the gold necklace, Khun Ratana glaring behind her",
          oninput: (e) => set("prompt", e.target.value) }),
        zone("thumb-prompt", () => { const t = S.cur.thumbnail || {}; return t.prompt ? h("pre", { class: "box" }, t.prompt) : h("div"); })),
      zone("thumb-actions", thumbnailActions),
      h("p", { class: "help" }, "Ảnh bìa tạo trên Flow (miễn phí, không có chữ); tiêu đề được in sau nên luôn đúng chính tả và đổi được mà không cần tạo lại ảnh.")));
}

function titleChips() {
  const list = S.cur.thumbnail?.suggestions || [];
  if (!list.length) return h("div", { class: "small muted" }, S.titleBusy ? "Claude đang nghĩ tiêu đề…" : "");
  const cur = (S.cur.storyboard.thumbnail?.text || "").trim();
  return h("div", { class: "title-chips" }, h("span", { class: "small muted" }, "Gợi ý · bấm để dùng:"),
    list.map((t) => {
      const text = typeof t === "string" ? t : t.text, vi = typeof t === "string" ? "" : t.vi;
      return h("button", { class: "title-chip" + (text === cur ? " on" : ""), onclick: () => useTitle(text), title: vi ? "Nghĩa: " + vi : "" },
        h("span", {}, text), vi ? h("span", { class: "vi" }, vi) : null);
    }));
}

function useTitle(t) {
  const sb = S.cur.storyboard;
  sb.thumbnail = sb.thumbnail || {};
  sb.thumbnail.text = t;
  const el = document.getElementById("thumb-title");
  if (el) el.value = t;
  changed();
  refreshZone("thumb-suggestions");
}

async function suggestTitles(manual = false) {
  if (!S.cur || S.titleBusy) return;
  const pid = S.cur.id;
  S.titleBusy = true;
  refreshZone("thumb-suggest-btn"); refreshZone("thumb-suggestions");
  try {
    await flushSave();
    const r = await api("POST", `/api/projects/${pid}/thumbnail/titles`);
    if (!S.cur || S.cur.id !== pid) return;
    S.cur.thumbnail = { ...(S.cur.thumbnail || {}), suggestions: r.titles };
    const sb = S.cur.storyboard;
    if (!(sb.thumbnail?.text || "").trim() && r.titles.length) useTitle(r.titles[0].text);  // pre-fill, still editable
  } catch (e) { if (manual) toast(e.message, "error"); }
  finally {
    S.titleBusy = false;
    refreshZone("thumb-suggest-btn"); refreshZone("thumb-suggestions");
  }
}

function maybeAutoSuggest() {
  // First visit with no title and no ideas yet: write a few in the background (once per project per session).
  S.titleTried = S.titleTried || new Set();
  const sb = S.cur?.storyboard;
  if (!sb || S.titleTried.has(S.cur.id) || !S.env.planner) return;
  if ((sb.thumbnail?.text || "").trim() || (S.cur.thumbnail?.suggestions || []).length) return;
  S.titleTried.add(S.cur.id);
  suggestTitles(false);
}

function thumbnailView() {
  const t = S.cur.thumbnail || {};
  const portrait = S.cur.storyboard.aspect === "9:16";
  return h("div", { class: "thumb-box" + (portrait ? " portrait" : ""), style: t.url ? { backgroundImage: `url("${t.url}")`, cursor: "zoom-in" } : null,
    onclick: () => { if (t.url) lightbox(t.url); } },
    t.url ? null : h("div", { class: "ph" }, icon("image"), h("span", {}, "Chưa có ảnh bìa")));
}

function thumbnailActions() {
  const t = S.cur.thumbnail || {};
  const busy = jobRunning();
  const make = (remake) => runAction("thumbnail", { remake_thumbnail: remake });
  return h("div", { class: "stack tight" },
    t.refs?.length ? h("div", { class: "help" }, "Gửi kèm ảnh nhân vật: " + t.refs.join(", ")) : null,
    h("div", { class: "row" },
      t.picture_ready
        ? [h("button", { class: "btn sm", disabled: busy, title: "Dùng lại ảnh bìa, chỉ in lại tiêu đề", onclick: () => make(false) }, icon("edit"), "Cập nhật tiêu đề"),
           h("button", { class: "btn sm", disabled: busy, onclick: () => make(true) }, icon("refresh"), "Tạo ảnh mới", FREE())]
        : h("button", { class: "btn sm", disabled: busy || !S.env.gflow, onclick: () => make(false) }, icon("image"), "Tạo ảnh bìa", FREE()),
      t.url ? h("a", { class: "btn sm ghost", href: t.url.split("?")[0], download: `${S.cur.id}-thumbnail.jpg` }, icon("download"), "Tải JPG") : null));
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
    h("div", { class: "card-head" }, h("h3", {}, "Xem nhanh các clip"), h("span", { class: "spacer" }),
      h("span", { class: "small muted" }, `Cảnh ${S.playIdx + 1}/${clips.length} · ${cur.sc.id}`)),
    h("div", { class: "player" + (portrait ? " portrait" : "") }, video, sub),
    h("div", { class: "playlist" }, clips.map((c, i) => h("button", { class: i === S.playIdx ? "on" : "", onclick: () => {
      S.playIdx = i; const z = document.getElementById("playlist-zone"); const n = renderPlaylist(); n.id = "playlist-zone"; z.replaceWith(n); n.querySelector("video").play();
    } }, h("div", { class: "pl-img", style: c.st.keyframe_url ? { backgroundImage: `url("${c.st.keyframe_url}")` } : null }), h("div", { class: "pl-lbl" }, `${i + 1}. ${c.sc.id}`)))),
    h("p", { class: "help", style: { marginTop: "8px" } }, "Chỉ để xem thử thứ tự, chưa có nhạc. Bấm “Ghép phim” để xuất file MP4."));
}

/* ---------- yaml tab ---------- */

function renderYaml() {
  const ta = h("textarea", { class: "yaml", spellcheck: "false", value: S.cur.yaml });
  return h("div", { class: "stack" },
    h("div", { class: "banner info flush" }, icon("info"), h("div", { class: "b-body" }, "Sửa trực tiếp file storyboard.yaml rồi bấm “Lưu YAML”. Khi sửa bằng các ô nhập ở tab khác, chú thích (#) trong file sẽ bị bỏ.")),
    ta,
    h("div", { class: "row" },
      h("button", { class: "btn primary", onclick: async () => {
        try {
          const d = await api("PUT", `/api/projects/${S.cur.id}/yaml`, { text: ta.value });
          S.cur = d; toast(d.errors.length ? "Đã lưu, nhưng storyboard còn lỗi" : "Đã lưu YAML", d.errors.length ? "error" : "ok");
          renderMain(); loadProjects();
        } catch (e) { toast(e.message, "error"); }
      } }, "Lưu YAML"),
      h("button", { class: "btn ghost", onclick: () => { ta.value = S.cur.yaml; } }, "Hoàn tác")));
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
    if (!NATIVE() && "Notification" in window && Notification.permission === "default" && action !== "dry") Notification.requestPermission();
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
      est.new_keyframes.length ? h("p", { class: "help" }, `Sẽ tạo trước ${est.new_keyframes.length} ảnh keyframe chưa có (miễn phí): ${est.new_keyframes.join(", ")}. Muốn duyệt ảnh trước thì bấm Huỷ rồi dùng “Tạo keyframe”.`) : null,
      est.blocked.length ? h("div", { class: "banner warn flush" }, icon("alert"), h("div", { class: "b-body" },
        h("div", {}, `Cảnh ${est.blocked.join(", ")} bị ngắt khi render lần trước, có thể Flow đã trừ credit. Hãy kiểm tra trên Flow trước.`),
        h("label", { class: "check", style: { marginTop: "8px" } }, h("input", { type: "checkbox", onchange: (e) => { retry = e.target.checked; body.retry_unknown = retry; document.getElementById("paid-ok").textContent = okBtnLabel(); document.getElementById("paid-ok").disabled = !count(); } }),
          h("span", {}, "Tôi đã kiểm tra, gửi lại các cảnh này")))) : null,
      h("div", { class: "banner info flush" }, icon("coins"), h("div", { class: "b-body" }, "Mỗi clip sẽ trừ credit trên tài khoản Google Flow của bạn. Có thể bấm Dừng bất cứ lúc nào; cảnh đang render sẽ được làm nốt.")),
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
  toast(msg.replace(/^[✔⏸✘] /, ""), j.status === "done" ? "ok" : j.status === "failed" ? "error" : "", 6000);
  if (j.status === "done") setTimeout(() => { if (S.job === j && S.jobOpen) { S.jobOpen = false; document.getElementById("joblog")?.classList.add("hidden"); updateJobHead(); } }, 2500);
  if (NATIVE() && j.kind !== "dry" && !document.hasFocus()) NATIVE().notify(msg);
  else if ("Notification" in window && Notification.permission === "granted" && document.hidden && j.kind !== "dry") {
    try { new Notification("Film Maker", { body: msg }); } catch { /* ignore */ }
  }
  await loadProjects();
  if (j.kind === "plan" && j.result?.project) { await openProject(j.result.project); return; }
  if (j.kind === "portraits" && S.cur && S.cur.id === j.project && j.result?.images) {
    for (const c of S.cur.storyboard.characters || []) if (j.result.images[c.name]) c.image = j.result.images[c.name];
    if (Object.keys(j.result.images).length) changed(); // server already saved; this keeps a pending edit from dropping it
    try { S.assets = await api("GET", `/api/projects/${S.cur.id}/assets`); } catch { /* ignore */ }
  }
  if (j.kind === "thumbnail" && S.cur && S.cur.id === j.project && j.result?.title) {
    const sb = S.cur.storyboard;
    sb.thumbnail = sb.thumbnail || {};
    if (!(sb.thumbnail.text || "").trim()) { sb.thumbnail.text = j.result.title; changed(); }  // server saved it too
  }
  if (S.cur && S.cur.id === j.project) {
    await refreshCurrent();
    if (j.kind === "portraits" && S.tab === "story") renderMain();
    if (j.kind === "stitch" || (j.kind === "make" && j.result?.final)) { S.tab = "final"; renderMain(); }
    else if (j.kind === "thumbnail" && S.tab === "final") renderMain();
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
  const kind = { running: "running", done: "done", failed: "failed", stopped: "interrupted" }[j.status] || "todo";
  put(head,
    h("span", { class: "dot " + j.status }),
    h("span", { class: "title" }, j.title),
    h("span", { class: "badge mini " + kind }, statusText, " · ", h("span", { id: "elapsed" }, elapsedText())),
    h("span", { class: "last", title: last }, last),
    h("div", { class: "tools" },
      j.status === "running" ? h("button", { class: "btn sm danger", disabled: j.stop_requested, onclick: stopJob }, icon("stop"), j.stop_requested ? "Đang dừng…" : "Dừng") : null,
      h("button", { class: "btn sm ghost icon", title: "Chép log", "aria-label": "Chép log", onclick: () => { navigator.clipboard?.writeText(j.lines.join("\n")); toast("Đã chép log", "", 1500); } }, icon("copy")),
      h("button", { class: "btn sm ghost", "aria-expanded": String(!!S.jobOpen), onclick: () => { S.jobOpen = !S.jobOpen; document.getElementById("joblog").classList.toggle("hidden", !S.jobOpen); updateJobHead(); } }, icon(S.jobOpen ? "down" : "up"), S.jobOpen ? "Ẩn log" : "Xem log"),
      j.status !== "running" ? h("button", { class: "btn sm ghost icon", title: "Đóng", "aria-label": "Đóng thanh tiến trình", onclick: () => { S.job = null; renderJobbar(); } }, icon("x")) : null),
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
  let mode = S.env.planner ? "ai" : "template";
  const body = h("div");
  const render = () => {
    const choices = [["ai", "sparkle", "Từ ý tưởng", "Claude viết storyboard cho bạn"], ["template", "template", "Từ mẫu", "Phim mẫu 3 cảnh để làm quen"],
      ["blank", "file", "Trống", "Tự viết từng cảnh"], ["import", "import", "Nhập file", "Mở file storyboard .yaml có sẵn"]];
    const form = { ai: aiForm, template: () => titleForm("Ly cà phê đầu tiên"), blank: () => titleForm(""), import: importForm }[mode]();
    put(body, h("div", { class: "choice", role: "radiogroup", "aria-label": "Cách tạo phim" }, choices.map(([k, ic, t, d]) => h("button", { class: mode === k ? "on" : "", role: "radio", "aria-checked": String(mode === k), onclick: () => { mode = k; render(); } }, icon(ic), h("b", {}, t), h("span", {}, d)))), form);
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
    return h("div", { class: "stack" }, field("Tên phim", inp),
      h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "btn primary", onclick: go }, "Tạo dự án")));
  };
  const aiForm = () => {
    if (!S.env.planner) return h("div", { class: "banner warn flush" }, icon("alert"), h("div", { class: "b-body" }, h("div", {},
      S.env.claude_code?.available ? "Claude Code chưa đăng nhập. Mở Terminal, chạy “claude” rồi gõ /login bằng tài khoản Claude của bạn. "
        : "Cần cài Claude Code (dùng gói Claude của bạn) hoặc thêm Anthropic API key. ",
      h("a", { href: "#", onclick: (e) => { e.preventDefault(); close(); openSettings(); } }, "Mở Cài đặt"), ".")));
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
        close(); localSet("draftIdea", ""); startJob(j); toast("Claude đang viết storyboard, thường mất 1–3 phút…", "", 6000);
      } catch (err) { toast(err.message, "error"); e.target.disabled = false; }
    };
    return h("div", { class: "stack" },
      field("Ý tưởng phim", idea),
      h("div", { class: "grid3" }, field("Số cảnh", n), field("Khung hình", aspect), field("Ngôn ngữ phụ đề", lang)),
      h("p", { class: "help" }, "Prompt sẽ được viết bằng tiếng Anh cho Veo hiểu tốt nhất. Bạn xem và sửa lại trước khi tạo bất cứ thứ gì. ",
        S.env.planner === "claude_code" ? "Dùng Claude Code với gói Claude của bạn, không tốn thêm tiền API." : "Dùng Anthropic API key (tính phí theo lượt dùng)."),
      h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "btn primary", onclick: go }, icon("sparkle"), "Viết storyboard")));
  };
  const importForm = () => {
    const inp = h("input", { type: "file", accept: ".yaml,.yml" });
    return h("div", { class: "stack" }, field("File storyboard (.yaml)", inp),
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
  const flowUi = h("input", { type: "checkbox", checked: s.flow_ui !== false });
  const maxCredits = h("input", { type: "number", min: 0, step: 10, value: s.flow_ui_max_credits || 0 });
  const flowWin = h("select", { value: s.flow_ui_window || "hidden" },
    h("option", { value: "hidden" }, "Chạy ngầm — trình duyệt thật nhưng giấu khỏi màn hình (khuyên dùng)"),
    h("option", { value: "headless" }, "Ẩn hoàn toàn (headless) — có thể bị Google chặn"),
    h("option", { value: "show" }, "Hiện cửa sổ — để xem nó thao tác"));
  let clearKey = false;
  const cc = S.env.claude_code || {};
  const planner = h("select", { value: s.planner || "auto" },
    h("option", { value: "auto" }, "Tự động (ưu tiên Claude Code)"),
    h("option", { value: "claude_code" }, "Claude Code — dùng gói Claude Pro/Max của bạn"),
    h("option", { value: "api" }, "Anthropic API key — tính phí riêng"));
  const theme = h("select", { value: localGet("theme", "auto"), onchange: (e) => { localSet("theme", e.target.value); applyTheme(); } },
    h("option", { value: "auto" }, "Theo hệ thống"), h("option", { value: "light" }, "Sáng"), h("option", { value: "dark" }, "Tối"));
  modal({
    title: "Cài đặt", wide: true,
    body: h("div", { class: "stack", style: { gap: "16px" } },
      h("section", { class: "settings-sec" }, h("h3", {}, "Google Flow (gflow)"),
        h("div", { class: "stack" },
          field("gflow profile (tài khoản Google)", profile),
          h("div", { class: "row" },
            h("button", { class: "btn sm", onclick: () => checkAuth() }, "Kiểm tra đăng nhập"),
            h("button", { class: "btn sm", onclick: () => startLogin() }, "Đăng nhập lại bằng Chrome"),
            h("a", { class: "btn sm ghost", href: "https://flow.google.com/", target: "_blank", rel: "noopener" }, "Mở Google Flow", icon("external"))),
          h("label", { class: "check" }, flowUi, h("span", {}, "Làm Ảnh → Video qua giao diện chat của Flow khi gflow không làm được",
            h("span", { class: "sub" }, S.env.flow_ui ? "Sẵn sàng: dùng trình duyệt và tài khoản đã đăng nhập của gflow." : "Chưa sẵn sàng: không tìm thấy Python hoặc profile trình duyệt của gflow-cli."))),
          h("div", { class: "grid2" },
            field("Cửa sổ trình duyệt khi điều khiển Flow", flowWin),
            field("Giới hạn credit cho 1 clip", maxCredits, "0 = không giới hạn")))),
      h("section", { class: "settings-sec" }, h("h3", {}, "An toàn khi render"),
        h("div", { class: "grid3" },
          field("Nghỉ tối thiểu (giây)", pmin),
          field("Nghỉ tối đa (giây)", pmax),
          field("Số lần thử lại khi lỗi tạm", retries)),
        h("p", { class: "help", style: { marginTop: "8px" } }, "Nghỉ ngẫu nhiên giữa các lần gửi để Flow không coi là bot. Chỉ thử lại các lỗi mà gflow báo là tạm thời.")),
      h("section", { class: "settings-sec" }, h("h3", {}, "Claude AI (viết storyboard)"),
        h("div", { class: "stack" },
          field("Cách gọi Claude", planner),
          h("div", { class: "row small" },
            h("span", { class: "pill " + (cc.available ? (cc.logged_in ? "ok" : "warn") : "") },
              cc.available ? (cc.logged_in ? "Claude Code đã đăng nhập" : "Claude Code chưa đăng nhập") : "Chưa cài Claude Code"),
            h("span", { class: "muted" }, cc.available ? (cc.logged_in ? (cc.method === "claude.ai" ? "Tài khoản Claude, dùng gói của bạn." : cc.method || "")
              : "Mở Terminal, chạy “claude” rồi gõ /login.") : "Gói Claude Pro/Max chỉ dùng được qua Claude Code, không dùng cho API key.")),
          field("Anthropic API key", key, "không bắt buộc nếu dùng Claude Code"),
          h("div", { class: "row" },
            s.anthropic_key_set ? h("label", { class: "check" }, h("input", { type: "checkbox", onchange: (e) => { clearKey = e.target.checked; } }), h("span", { class: "small" }, "Xoá key đã lưu")) : null,
            h("span", { class: "spacer" }), h("a", { class: "small", href: "https://console.anthropic.com/settings/keys", target: "_blank", rel: "noopener" }, "Lấy API key ↗")),
          h("p", { class: "help" }, "Key được lưu trên máy này ở ~/.config/filmmaker/settings.json (chỉ tài khoản của bạn đọc được)."))),
      h("section", { class: "settings-sec" }, h("h3", {}, "Giao diện"), h("div", { class: "grid2" }, field("Chế độ màu", theme)))),
    buttons: [{ label: "Huỷ" }, { label: "Lưu", cls: "primary", onclick: async (c) => {
      try {
        await api("PUT", "/api/settings", { planner: planner.value, profile: profile.value.trim(), pause_min: +pmin.value, pause_max: +pmax.value, retries: +retries.value,
          flow_ui: flowUi.checked, flow_ui_max_credits: +maxCredits.value || 0, flow_ui_window: flowWin.value,
          anthropic_api_key: key.value.trim() || undefined, clear_anthropic_key: clearKey });
        S.env = await api("GET", "/api/env"); renderTopbar(); c(); toast("Đã lưu cài đặt", "ok");
      } catch (e) { toast(e.message, "error"); }
    } }],
  });
}

async function openCredits() {
  const body = h("div", {}, h("p", { class: "row" }, h("span", { class: "spinner" }), "Đang mở Flow ngầm để đọc số dư… (khoảng 30–60 giây)"));
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
        h("details", { style: { marginTop: "12px" } }, h("summary", {}, "Dữ liệu gốc"), h("pre", { class: "box" }, JSON.stringify(r.data, null, 2))));
    } else {
      put(body, h("div", { class: "banner warn" }, icon("alert"), h("div", { class: "b-body" }, "Không đọc được số dư: " + (r.hint || ""))), h("pre", { class: "box" }, r.error),
        h("p", { class: "small" }, "Bạn có thể xem trực tiếp trong menu tài khoản trên ", h("a", { href: "https://flow.google.com/", target: "_blank", rel: "noopener" }, "Google Flow ↗"), "."));
    }
  } catch (e) { put(body, h("div", { class: "banner err flush" }, icon("alert"), h("div", { class: "b-body" }, e.message))); }
}

function applyTheme() {
  const t = localGet("theme", "auto");
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
}

applyTheme();
init();
