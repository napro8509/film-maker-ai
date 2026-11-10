"use strict";
/* Film Maker UI — vanilla JS, no build step. */

const TOKEN = document.querySelector('meta[name="token"]').content;
const S = {
  env: null, settings: null, models: { video: [], image: [] }, projects: [], cur: null, tab: "scenes",
  assets: { images: [], audio: [] }, job: null, jobSince: 0, jobOpen: false, pollT: null,
  saveTimer: null, saving: false, pendingSave: false, saveState: "saved", saveError: "", auth: null,
  zones: new Map(), playIdx: 0,
  charSaved: new WeakMap(), // character object → the name scenes[].characters use for it (last save)
};
const MODES = [
  ["keyframe", "Ảnh → Video", "Tạo ảnh mở đầu trước (miễn phí), duyệt rồi mới render. Giữ nhân vật đồng nhất nhất."],
  ["t2v", "Chữ → Video", "Tạo thẳng video từ mô tả."],
  ["i2v", "Ảnh của bạn → Video", "Làm chuyển động một ảnh bạn tải lên."],
];
const STATUS = {
  done: ["done", "Xong"], changed: ["changed", "Đã sửa · cần render lại"], interrupted: ["interrupted", "Bị ngắt khi render"],
  failed: ["failed", "Lỗi"], todo: ["todo", "Chưa làm"], running: ["running", "Đang làm"],
  queued: ["queued", "Đang chờ…"],
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
  cloud: '<path d="M7 18a4.5 4.5 0 0 1-.6-9A6 6 0 0 1 18 8.5a4.8 4.8 0 0 1-.5 9.5z"/>',
  board: '<rect x="3" y="4" width="7" height="6" rx="1.5"/><rect x="14" y="14" width="7" height="6" rx="1.5"/><path d="M10 7h2a2 2 0 0 1 2 2v6a2 2 0 0 0 2 2"/>',
  layout: '<rect x="3" y="4" width="5" height="16" rx="1.5"/><rect x="10" y="4" width="5" height="7" rx="1.5"/><rect x="10" y="13" width="5" height="7" rx="1.5"/><rect x="17" y="4" width="4" height="7" rx="1.5"/>',
  fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  minus: '<path d="M5 12h14"/>',
  arrowRight: '<path d="M5 12h14M13 6l6 6-6 6"/>',
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
    // detail may be a string, or an object {message, reason, ...} (409/428, e.g. reason "model_retired" carries retired, "needs_confirm" carries scenes)
    const msg = typeof d === "string" ? d : d && typeof d.message === "string" ? d.message : d ? JSON.stringify(d) : `Lỗi máy chủ (HTTP ${r.status})`;
    const err = new Error(msg);
    err.status = r.status; err.detail = d;
    throw err;
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

/* Spoken / subtitle language: stored as the English name ("Vietnamese"), "none" = no dialogue. */
const LANG_OTHER = "__other__";
const DIALOGUE_LANGS = [["Vietnamese", "Tiếng Việt"], ["English", "English"], ["Thai", "ไทย (Tiếng Thái)"],
                        ["none", "Không thoại (chỉ nhạc/âm thanh)"]];
const SUBTITLE_LANGS = [["Vietnamese", "Tiếng Việt"], ["English", "English"], ["Thai", "ไทย (Tiếng Thái)"]];
/* A valid language name with whitespace collapsed, or null (not a string, empty, has a line break, > 40 chars). */
function cleanLang(v) {
  if (typeof v !== "string" || /[\r\n]/.test(v)) return null;
  const s = v.replace(/\s+/g, " ").trim();
  if (/\p{C}/u.test(s)) return null; // control/format chars (zero-width, bidi…): same rule as the backend
  return s && s.length <= 40 ? s : null;
}
/* <select> of presets + "Khác…" (free text input shown only then); unset adds "Chưa đặt (Veo tự đoán)" = null. */
function langPicker(presets, value, { unset, noNone } = {}) {
  const fold = (s) => String(s).toLowerCase();
  const hit = typeof value === "string" && value.trim() ? presets.find(([v]) => fold(v) === fold(value.trim())) : null;
  const empty = value == null || (typeof value === "string" && !value.trim());
  const pick = hit ? hit[0] : empty ? (unset ? "" : presets[0][0]) : LANG_OTHER;
  const input = h("input", { type: "text", maxlength: 80, placeholder: "Ví dụ: Japanese", "aria-label": "Tên ngôn ngữ khác",
    value: pick === LANG_OTHER ? String(value) : "", hidden: pick !== LANG_OTHER });
  const select = h("select", { value: pick,
    onchange: () => { input.hidden = select.value !== LANG_OTHER; if (!input.hidden && !input.value.trim()) input.focus(); } },
    unset ? h("option", { value: "" }, "Chưa đặt (Veo tự đoán)") : null,
    presets.map(([v, l]) => h("option", { value: v }, l)),
    h("option", { value: LANG_OTHER }, "Khác…"));
  const read = () => {
    if (select.value === "") return { ok: true, value: null };
    if (select.value !== LANG_OTHER) return { ok: true, value: select.value };
    const v = cleanLang(input.value);
    if (!v) return { ok: false, msg: "hãy nhập tên ngôn ngữ (tối đa 40 ký tự, không có ký tự đặc biệt ẩn)" };
    if (noNone && v.toLowerCase() === "none") return { ok: false, msg: "phụ đề cần một ngôn ngữ cụ thể, không dùng “none”" };
    return { ok: true, value: v };
  };
  return { el: h("div", { class: "stack tight" }, select, input), select, input, read };
}
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

/* canClose(): optional guard for user closes (Esc, ×, backdrop, plain buttons); the close() handed to onclick always closes. */
function modal({ title, body, buttons = [], wide = false, onClose, canClose }) {
  const root = $("#modal-root");
  const close = () => {
    if (!ov.isConnected) return;
    ov.remove(); document.removeEventListener("keydown", onKey); onClose && onClose();
    if (prevFocus && prevFocus.isConnected && prevFocus.focus) prevFocus.focus();
  };
  const userClose = () => { if (!canClose || canClose()) close(); };
  const onKey = (e) => { if (e.key === "Escape") userClose(); };
  const foot = buttons.length ? h("div", { class: "modal-foot" }, buttons.map((b) =>
    h("button", { class: "btn " + (b.cls || ""), disabled: b.disabled, id: b.id, onclick: () => b.onclick ? b.onclick(close) : userClose() }, b.label))) : null;
  const prevFocus = document.activeElement;
  const ov = h("div", { class: "overlay", onmousedown: (e) => { if (e.target === ov) userClose(); } },
    h("div", { class: "modal" + (wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "modal-head" }, h("h2", {}, title), h("button", { class: "btn ghost icon", onclick: userClose, "aria-label": "Đóng", title: "Đóng (Esc)" }, icon("x"))),
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
    if (old.outerHTML !== el.outerHTML && !keepsOpenedClip(old, el)) old.replaceWith(el);
  }
}
function refreshZone(id) {
  const fn = S.zones.get(id), old = document.getElementById(id);
  if (!fn || !old) return;
  const el = fn(); el.id = id;
  if (old.outerHTML !== el.outerHTML && !keepsOpenedClip(old, el)) old.replaceWith(el);
}
/* A clip opened from lazyVideo() stays open (even paused) while the zone would only show it again as a poster. */
function keepsOpenedClip(old, el) {
  const v = old.querySelector("video[data-lazy-src]"), b = el.querySelector(".lazy-video");
  if (!v || !b || b.dataset.src !== v.dataset.lazySrc) return false;
  const a = old.cloneNode(true), c = el.cloneNode(true);
  a.querySelector("video[data-lazy-src]").replaceWith(c.querySelector(".lazy-video").cloneNode(true));
  return a.outerHTML === c.outerHTML; // nothing else in the zone changed
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
    if (id && (!S.cur || S.cur.id !== id) && S.projects.some((p) => p.id === id)) openProject(id).then((ok) => {
      if (ok === false && S.cur) history.replaceState(null, "", "#" + S.cur.id); // user chose to stay
    });
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
  await loadProjects();
  const want = decodeURIComponent(location.hash.slice(1)) || localGet("lastProject");
  const pick = S.projects.some((p) => p.id === want) ? want : S.projects[0]?.id;
  if (pick) await openProject(pick); else { renderMain(); loadModels(null); } // openProject loads /api/models itself
  const j = await api("GET", "/api/jobs/current").catch(() => null);
  if (j) {
    S.job = { ...j, lines: [] }; S.jobSince = 0; S.jobOpen = j.status === "running"; renderJobbar();
    refreshZones(); progUpdate({}, S.job.progress); // F5 mid-job: overlays now, not at the next phase change
    pollJob();
  }
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
  // Edits that could not be saved (blocked by an empty/duplicate character name, or a save error) would be lost silently.
  if (S.cur && S.cur.id !== id && S.pendingSave && saveFailed()) {
    const why = S.saveState === "blocked" ? "Có nhân vật tên trống hoặc trùng nên thay đổi gần đây chưa được lưu." : "Thay đổi gần đây chưa lưu được: " + S.saveError;
    if (!(await confirmModal("Bỏ thay đổi chưa lưu?", why + " Mở dự án khác thì các thay đổi này sẽ mất.", "Bỏ thay đổi và mở", true))) return false;
    clearTimeout(S.saveTimer); S.pendingSave = false;
  }
  loadModels(id); // in the background, not awaited
  try {
    const [d, a] = await Promise.all([api("GET", `/api/projects/${id}`), api("GET", `/api/projects/${id}/assets`)]);
    S.cur = d; S.assets = a; S.playIdx = 0;
    S.dlChanged = null; // the "N clips keep the old language" warning belongs to this visit only
  } catch (e) { toast(e.message, "error"); return false; }
  rememberCharNames();
  localSet("lastProject", id);
  toggleSidebar(false);
  if (location.hash.slice(1) !== id) history.replaceState(null, "", "#" + id);
  S.saveState = "saved";
  renderSidebar();
  renderMain();
  return true;
}

/* Refresh /api/models in the background (gflow may be slow): never delays drawing the project. */
function loadModels(id) {
  api("GET", "/api/models").then((m) => {
    S.models = m;
    if (!S.cur || S.cur.id !== id) return;
    const a = document.activeElement;
    if (a && a.closest && a.closest("#main") && a.matches("input, textarea, select")) return; // don't steal focus while typing
    renderMain();
  }).catch(() => { /* keep the previous S.models */ });
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
  const sb = S.cur.storyboard;
  const listed = (sb.scenes || []).some((s) => Array.isArray(s.characters));
  // (a) The server rejects empty/duplicate names once a scene lists characters: wait until the name is fixed.
  // pendingSave stays true; the next changed() tries again.
  if (listed && charsLocked(sb)) { clearTimeout(S.saveTimer); setSaveState("blocked", "tên nhân vật trống/trùng"); return; }
  // (b) Carry renamed characters into scenes[].characters, so typing a new name never breaks the links.
  if (listed) renameInScenes(sb);
  const sent = charNamePairs(sb);
  S.saving = true; S.pendingSave = false; setSaveState("saving");
  const pid = S.cur.id;
  try {
    const d = await api("PUT", `/api/projects/${pid}`, { storyboard: sb });
    if (S.cur && S.cur.id === pid) {
      if (S.cur.storyboard === sb) rememberCharNames(sent);
      applyMeta(d);
      if (typeof boardAfterSave === "function") { try { boardAfterSave(); } catch (err) { console.error(err); } }
    }
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
  const t = { saved: "Đã lưu", pending: "Chưa lưu…", saving: "Đang lưu…", error: "Lỗi lưu: " + S.saveError, blocked: "Chưa lưu: " + S.saveError }[S.saveState];
  const bad = saveFailed();
  return h("span", { class: "savestate" + (bad ? " error" : ""), role: bad ? "alert" : null,
    title: S.saveState === "blocked" ? "Có cảnh đang chọn nhân vật, nên tên nhân vật phải khác rỗng và không trùng. Sửa tên để lưu tiếp." : "Tự lưu khi bạn sửa · ⌘S để lưu ngay" },
    S.saveState === "saved" ? icon("check") : bad ? icon("alert") : S.saveState === "saving" ? h("span", { class: "spinner" }) : null, t);
}
/* The last save did not reach the disk: a server error, or blocked by an empty/duplicate character name. */
function saveFailed() { return S.saveState === "error" || S.saveState === "blocked"; }

/* ---------- character names (shared with board.js) ---------- */

/* Names match without case, like the server (casefold). */
function nameKey(n) { return String(n ?? "").trim().toLowerCase(); }
function charKey(c) { return nameKey(c && c.name); }
/* Indexes of characters whose name is empty or used by another character. */
function charNameProblems(sb) {
  const chars = (sb && sb.characters) || [], seen = new Map(), bad = new Set();
  chars.forEach((c, i) => {
    const k = charKey(c);
    if (!k) { bad.add(i); return; }
    if (seen.has(k)) { bad.add(i); bad.add(seen.get(k)); } else seen.set(k, i);
  });
  return bad;
}
function charsLocked(sb) { return charNameProblems(sb).size > 0; }
/* [character object, name] for every character whose name is valid right now. */
function charNamePairs(sb) {
  const bad = charNameProblems(sb);
  return ((sb && sb.characters) || []).map((c, i) => [c, c.name, i]).filter(([, , i]) => !bad.has(i)).map(([c, n]) => [c, n]);
}
/* Remember the names scenes[].characters refer to. Characters with an empty/duplicate name are not remembered,
   so they are never "renamed" in the scenes (they cannot be listed there anyway). */
function rememberCharNames(pairs) {
  S.charSaved = new WeakMap();
  const sb = S.cur && S.cur.storyboard;
  if (!sb) return;
  for (const [c, n] of pairs || charNamePairs(sb)) S.charSaved.set(c, n);
}
/* Replace old names with the new ones in every scenes[].characters (all at once, so swapping two names works). */
function renameInScenes(sb) {
  const map = new Map();
  for (const c of sb.characters || []) {
    if (!S.charSaved.has(c)) continue; // added since the last save: nothing refers to it yet
    const old = S.charSaved.get(c);
    if (!nameKey(old) || old === c.name) continue;
    map.set(nameKey(old), c.name.trim());
  }
  if (!map.size) return;
  for (const sc of sb.scenes || []) {
    if (!Array.isArray(sc.characters)) continue;
    const out = [], seen = new Set();
    for (const n of sc.characters) {
      const v = map.has(nameKey(n)) ? map.get(nameKey(n)) : n;
      if (seen.has(nameKey(v))) continue;
      seen.add(nameKey(v)); out.push(v);
    }
    sc.characters = out;
  }
  // The scenes now use the new names, even if this save fails.
  for (const c of sb.characters || []) if (S.charSaved.has(c) && map.has(nameKey(S.charSaved.get(c)))) S.charSaved.set(c, c.name.trim());
}
/* Scenes linked to a character: listed in scenes[].characters, or picked up from the prompts (server). */
function scenesWithChar(c) {
  const k = charKey(c);
  if (!k) return [];
  return (S.cur.storyboard.scenes || []).filter((sc) => {
    const names = Array.isArray(sc.characters) ? sc.characters : S.cur.scene_state?.[sc.id]?.characters || [];
    return names.some((n) => nameKey(n) === k);
  });
}
/* Delete character i (asks first). Its image files stay in assets/. Returns true when deleted. */
async function removeCharacter(i) {
  const sb = S.cur.storyboard, c = (sb.characters || [])[i];
  if (!c) return false;
  const twin = sb.characters.some((x) => x !== c && charKey(x) && charKey(x) === charKey(c)); // same name: links stay
  const n = twin ? 0 : scenesWithChar(c).length;
  if (c.name || c.description || n) {
    const text = `Xoá “${c.name || "nhân vật chưa đặt tên"}” khỏi storyboard. ` + (n ? `Đang nối vào ${n} cảnh; nhân vật sẽ được bỏ khỏi các cảnh đó. ` : "") + "File ảnh vẫn nằm trong thư mục assets.";
    if (!(await confirmModal("Xoá nhân vật?", text, "Xoá", true))) return false;
  }
  const at = sb.characters.indexOf(c); // the list may have changed while the dialog was open
  if (at < 0) return false;
  sb.characters.splice(at, 1);
  const k = charKey(c);
  // Another character with the same name keeps its links.
  if (k && !sb.characters.some((x) => charKey(x) === k)) {
    for (const sc of sb.scenes || []) if (Array.isArray(sc.characters)) sc.characters = sc.characters.filter((x) => nameKey(x) !== k);
  }
  changed();
  return true;
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
    ["board", "board", "Bảng"],
    ["scenes", "video", "Cảnh", S.cur.storyboard.scenes?.length || 0],
    ["final", "stitch", "Ghép phim & ảnh bìa"], ["yaml", "code", "YAML"]];
  put(m, h("div", { class: "main-inner" },
    renderHeader(),
    zone("errors-zone", renderErrors),
    zone("model-zone", renderModelWarning),
    h("div", { class: "tabs", role: "tablist" },
      tabs.map(([k, ic, l, n]) => h("button", { class: S.tab === k ? "on" : "", role: "tab", "aria-selected": String(S.tab === k), onclick: () => switchTab(k) },
        icon(ic), l, n != null ? h("span", { class: "count" }, n) : null))),
    h("div", { role: "tabpanel" }, S.tab === "scenes" ? renderScenes() : S.tab === "story" ? renderStory() : S.tab === "board" ? renderBoardTab()
      : S.tab === "final" ? renderFinal() : renderYaml()),
  ));
}

/* The board lives in board.js (loaded before this file). */
function renderBoardTab() {
  if (typeof renderBoard !== "function") return h("div", { class: "banner err flush", role: "alert" }, icon("alert"), h("div", { class: "b-body" }, "Không tải được tab Bảng (thiếu board.js). Tải lại trang thử lại."));
  try { return renderBoard(); }
  catch (e) {
    console.error(e);
    return h("div", { class: "banner err flush", role: "alert" }, icon("alert"), h("div", { class: "b-body" }, "Không vẽ được bảng: " + e.message));
  }
}

async function switchTab(k) {
  if (k === "yaml" || k === "board") { await flushSave(); await refreshCurrent(); }
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
    { label: "Backup lên Drive", icon: "cloud", disabled: jobRunning(), onclick: backupProject },
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
  ], [S.cur.path, S.cur.project_id ? "Flow: " + S.cur.project_id : null,
    S.cur.backup?.at ? "Backup: " + ago(Date.parse(S.cur.backup.at) / 1000) : "Chưa backup lên Drive"].filter(Boolean).join("\n"));
}

async function backupProject() {
  try { await flushSave(); startJob(await api("POST", `/api/projects/${S.cur.id}/backup`)); }
  catch (e) { toast(e.message, "error"); }
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
/* Retiring/retired models used by this project. Sentences are built by the server (model_warning.lines). */
function renderModelWarning() {
  const w = S.cur.model_warning;
  const metaErr = S.models.metadata_error;
  if (!w?.lines?.length && !metaErr) return h("div");
  const retired = (w?.models || []).some((m) => m.status === "retired");
  return h("div", {},
    w?.lines?.length ? h("div", { class: "banner " + (retired ? "err" : "warn"), role: retired ? "alert" : null }, icon("alert"),
      h("div", { class: "b-body" }, w.lines.map((l) => h("div", {}, l)),
        h("div", {}, h("button", { class: "btn sm", disabled: jobRunning(), onclick: () => openModelChange() }, icon("refresh"), MODEL_CHANGE_LABEL)))) : null,
    metaErr ? h("p", { class: "help", style: { marginBottom: "var(--sp-4)" } }, "File metadata model lỗi: " + metaErr) : null);
}
function prettyError(e) {
  return e.replace(/scene (\S+): unknown character '(.*)' in characters/, "Cảnh $1: không có nhân vật “$2” (mục nhân vật của cảnh)")
    .replace(/character names must be unique and non-empty when scenes list characters: (.*)/, "Tên nhân vật phải khác rỗng và không trùng khi cảnh có danh sách nhân vật: $1")
    .replace(/^scenes\.(\d+)(?:\.|:\s*(?:scene [^:]+:\s*)?)/, (_, i) => `Cảnh ${+i + 1}: `).replace("Field required", "chưa điền")
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
  if (progOn()) {
    for (const k of Object.keys(S.job.progress)) {
      const m = /^(?:vid|kf):(.+)$/.exec(k);
      if (m && progRunning(k)) return m[1];
    }
    return null;
  }
  for (let i = S.job.lines.length - 1; i >= 0; i--) {
    const m = /^\[([A-Za-z0-9_-]+)\]/.exec(S.job.lines[i]);
    if (m) return /\] (OK|LỖI|BỎ QUA|\(dry-run\))/.test(S.job.lines[i]) ? null : m[1];
  }
  return null;
}

function sceneStatusKey(sc) {
  if (progOn()) {
    if (progRunning("vid:" + sc.id) || progRunning("kf:" + sc.id)) return "running";
    if (progQueued("vid:" + sc.id) || progQueued("kf:" + sc.id)) return "queued";
    return S.cur.scene_state?.[sc.id]?.status || "todo";
  }
  if (currentSceneInJob() === sc.id) return "running";
  return S.cur.scene_state?.[sc.id]?.status || "todo";
}

/* Visual direction (scene.camera). Values are English film terms; the datalists suggest the usual ones
   (from /api/env camera_presets) but any text is accepted. The server adds them to the prompts. */
const CAMERA_FIELDS = [
  ["shot", "Cỡ cảnh", "close-up"], ["angle", "Góc máy", "low angle"],
  ["movement", "Chuyển động máy", "slow dolly-in"], ["lens", "Ống kính", "85mm portrait lens"],
  ["focus", "Lấy nét / độ sâu", "shallow depth of field"], ["composition", "Bố cục", "rule of thirds"],
  ["lighting", "Ánh sáng", "golden hour backlight"], ["speed", "Tốc độ", "slow motion"],
];
const CAMERA_MORE = ["focus", "composition", "lighting", "speed", "notes"];
function ensureCameraLists() {
  const presets = S.env?.camera_presets || {};
  for (const [k] of CAMERA_FIELDS) {
    if (document.getElementById("cam-" + k)) continue;
    document.body.append(h("datalist", { id: "cam-" + k }, (presets[k] || []).map((o) => h("option", { value: o.value }, o.label))));
  }
}
function renderCamera(sc) {
  ensureCameraLists();
  const cam = sc.camera || {};
  const set = (k, v) => {
    const c = { ...(sc.camera || {}) };
    if (v.trim()) c[k] = v; else delete c[k];
    sc.camera = Object.keys(c).length ? c : undefined;
    changed();
  };
  const input = (k, label, ph) => field(label, h("input", { type: "text", list: "cam-" + k, value: cam[k] || "", placeholder: ph,
    oninput: (e) => set(k, e.target.value) }));
  const main = CAMERA_FIELDS.filter(([k]) => !CAMERA_MORE.includes(k));
  const more = CAMERA_FIELDS.filter(([k]) => CAMERA_MORE.includes(k));
  const hint = sc.mode === "t2v" ? "tự thêm vào prompt video"
    : "cỡ cảnh, góc, ống kính, nét, bố cục, ánh sáng → ảnh mở đầu; chuyển động, tốc độ, nét → video";
  return h("div", { class: "camera-box" },
    h("div", { class: "lbl" }, "Chỉ đạo hình ảnh", h("span", { class: "hint" }, " · " + hint)),
    h("div", { class: "grid2" }, main.map(([k, l, ph]) => input(k, l, ph))),
    h("details", { open: CAMERA_MORE.some((k) => cam[k]) || null },
      h("summary", {}, "Lấy nét, bố cục, ánh sáng, tốc độ, ghi chú"),
      h("div", { class: "grid2" }, more.map(([k, l, ph]) => input(k, l, ph))),
      field("Ghi chú đạo diễn", h("textarea", { rows: 2, value: cam.notes || "", placeholder: "Ví dụ: camera stays behind her shoulder the whole time",
        oninput: (e) => set("notes", e.target.value) }), "tiếng Anh")));
}

/* Content direction + dialogue (scene.content_direction, scene.dialogue). The server adds them to the video
   prompt as "Name (delivery) says: words" lines, spoken one at a time, with a rule that nobody repeats a line. */
const DELIVERY_PRESETS = [
  ["calmly", "Bình thản"], ["softly, gently", "Nhẹ nhàng"], ["warmly, smiling", "Ấm áp, mỉm cười"],
  ["whispering", "Thì thầm"], ["hesitantly, unsure", "Ngập ngừng"], ["sadly, voice trembling", "Buồn, giọng run"],
  ["holding back tears", "Cố nén nước mắt"], ["sobbing", "Nức nở"], ["angrily", "Tức giận"],
  ["shouting angrily", "Quát lớn"], ["coldly, flat voice", "Lạnh lùng"], ["sarcastically", "Mỉa mai"],
  ["excitedly", "Hào hứng"], ["nervously, fast", "Lo lắng, nói nhanh"], ["firmly, determined", "Dứt khoát"],
  ["pleading", "Van nài"], ["laughing", "Vừa nói vừa cười"], ["shocked", "Sững sờ"],
];
function ensureDeliveryList() {
  if (document.getElementById("dl-delivery")) return;
  document.body.append(h("datalist", { id: "dl-delivery" }, DELIVERY_PRESETS.map(([v, l]) => h("option", { value: v }, l))));
}
function lineKey(t) { return (t || "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim(); }
function renderDirection(sc, i) {
  ensureDeliveryList();
  const sb = S.cur.storyboard;
  const noTalk = (sb.dialogue_language || "").toLowerCase() === "none";
  const rows = () => (sc.dialogue = Array.isArray(sc.dialogue) ? sc.dialogue : []);
  const names = (sb.characters || []).map((c) => c.name).filter(Boolean);
  const talkZone = `talk-${i}`;
  const redraw = () => { changed(); refreshZone(talkZone); refreshZone(`chars-${i}`); };
  const lang = sb.dialogue_language && !noTalk ? sb.dialogue_language : "";
  return h("div", { class: "camera-box talk-box" },
    h("div", { class: "lbl" }, "Chỉ đạo nội dung & thoại", h("span", { class: "hint" }, " · tự thêm vào prompt video; mỗi người nói lần lượt, không ai nói lặp câu của người khác")),
    field("Chỉ đạo nội dung", h("textarea", { rows: 2, value: sc.content_direction || "",
      placeholder: "Cảnh này cần khán giả hiểu/cảm thấy gì, từng nhân vật diễn thế nào. Ví dụ: Lan finally confronts her brother; Minh avoids her eyes, guilty",
      oninput: (e) => { sc.content_direction = e.target.value || undefined; changed(); } }), "nên viết tiếng Anh"),
    zone(talkZone, () => {
      const list = Array.isArray(sc.dialogue) ? sc.dialogue : [];
      // Live duplicate check inside this scene (the server also checks earlier scenes after saving).
      const seen = new Map();
      const dup = list.map((d, j) => { const k = lineKey(d.line); if (!k) return null; if (seen.has(k)) return seen.get(k); seen.set(k, j); return null; });
      const opts = (cur) => [h("option", { value: "" }, "— Ai nói —"), ...[...names, ...(cur && !names.includes(cur) ? [cur] : [])].map((n) => h("option", { value: n }, n))];
      return h("div", { class: "talk-list" },
        noTalk && list.length ? h("div", { class: "small warn-text" }, "Phim đang đặt “Không thoại”: lời thoại cảnh này sẽ không được gửi lên Flow.") : null,
        list.map((d, j) => h("div", { class: "talk-row" + (dup[j] != null ? " dup" : "") },
          h("span", { class: "talk-num mono muted" }, j + 1),
          h("select", { "aria-label": "Nhân vật nói", value: d.character || "", onchange: (e) => { d.character = e.target.value; redraw(); } }, opts(d.character)),
          h("input", { type: "text", class: "talk-line", "aria-label": "Lời thoại", value: d.line || "", placeholder: lang ? `Lời thoại (${lang})` : "Lời thoại",
            oninput: (e) => { d.line = e.target.value; changed(); }, onchange: () => refreshZone(talkZone) }),
          h("input", { type: "text", class: "talk-how", list: "dl-delivery", "aria-label": "Cách nói", value: d.delivery || "", placeholder: "Cách nói, ví dụ: whispering",
            oninput: (e) => { d.delivery = e.target.value || undefined; changed(); } }),
          h("div", { class: "talk-tools" },
            h("button", { class: "btn ghost icon sm", title: "Lên", "aria-label": "Chuyển câu lên", disabled: j === 0, onclick: () => { const r = rows(); [r[j - 1], r[j]] = [r[j], r[j - 1]]; redraw(); } }, icon("up")),
            h("button", { class: "btn ghost icon sm danger", title: "Xoá câu", "aria-label": "Xoá câu thoại", onclick: () => { const r = rows(); r.splice(j, 1); if (!r.length) delete sc.dialogue; redraw(); } }, icon("trash"))),
          dup[j] != null ? h("div", { class: "small warn-text talk-warn" }, `Trùng câu ${dup[j] + 1}: mỗi câu chỉ được nói một lần`) : null)),
        list.length >= 12 ? null : h("button", { class: "btn ghost sm", onclick: () => {
          const r = rows();
          // Suggest the next speaker: someone other than the last one (characters take turns).
          const inScene = S.cur.scene_state?.[sc.id]?.characters || names;
          const last = r.length ? r[r.length - 1].character : null;
          r.push({ character: inScene.find((n) => n !== last) || last || "", line: "" });
          redraw();
          setTimeout(() => document.querySelectorAll(`#${talkZone} .talk-line`)[r.length - 1]?.focus(), 30);
        } }, icon("plus"), "Thêm câu thoại"));
    }),
    zone(`talkwarn-${i}`, () => {
      const w = S.cur.scene_state?.[sc.id]?.dialogue_warnings || [];
      return w.length ? h("ul", { class: "small warn-text talk-warnings" }, w.map((m) => h("li", {}, m))) : h("div");
    }));
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
      h("button", { class: "btn ghost icon sm", title: "Nhân bản cảnh", "aria-label": "Nhân bản cảnh", onclick: () => { sb.scenes.splice(i + 1, 0, { ...sc, camera: sc.camera ? { ...sc.camera } : undefined, dialogue: Array.isArray(sc.dialogue) ? sc.dialogue.map((d) => ({ ...d })) : undefined, characters: Array.isArray(sc.characters) ? [...sc.characters] : undefined, id: nextSceneId() }); changed(); renderMain(); } }, icon("copy")),
      h("button", { class: "btn ghost icon sm danger", title: "Xoá cảnh", "aria-label": "Xoá cảnh", onclick: async () => {
        if (sb.scenes.length === 1) { toast("Phim cần ít nhất một cảnh", "error"); return; }
        const hasWork = S.cur.scene_state?.[sc.id]?.clip_url;
        if (hasWork && !(await confirmModal("Xoá cảnh?", `Cảnh ${sc.id} đã có clip. Clip vẫn nằm trong thư mục output nhưng sẽ không được ghép vào phim.`, "Xoá", true))) return;
        sb.scenes.splice(i, 1); changed(); renderMain();
      } }, icon("trash")))),
    h("div", { class: "scene-body" },
      h("div", { class: "media-col" }, zone(`media-${i}`, () => renderSceneMedia(sc)), zone(`sactions-${i}`, () => renderSceneActions(sc))),
      h("div", { class: "scene-fields" },
        sc.mode === "keyframe" ? text("image_prompt", "Ảnh mở đầu", "Mô tả khung hình đầu tiên như một bức ảnh: chủ thể, ai đứng đâu, bối cảnh…", 3, "nên viết tiếng Anh") : null,
        sc.mode === "i2v" ? h("div", { class: "grid2" }, imagePicker(sc, "image", "Ảnh bắt đầu"), imagePicker(sc, "end_image", "Ảnh kết thúc (tuỳ chọn)", true)) : null,
        text("prompt", sc.mode === "t2v" ? "Nội dung video" : "Hành động", "Một hành động, ví dụ: Lan lifts the glass and smiles… (góc máy chọn ở mục Chỉ đạo hình ảnh)", 3, "nên viết tiếng Anh"),
        renderCamera(sc),
        renderDirection(sc, i),
        zone(`chars-${i}`, () => {
          const speakers = (Array.isArray(sc.dialogue) ? sc.dialogue : []).filter((d) => d.line && d.character).map((d) => d.character).join(" ");
          const text = `${sc.prompt || ""} ${sc.mode === "keyframe" ? sc.image_prompt || "" : ""} ${speakers}`;
          // A list picked on the board (scenes[].characters) replaces finding names in the prompt.
          const picked = Array.isArray(sc.characters) ? new Set(sc.characters.map(nameKey)) : null;
          const found = (sb.characters || []).filter((c) => c.name && (picked ? picked.has(charKey(c))
            : new RegExp(`\\b${c.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i").test(text)));
          if (!found.length) return h("div");
          const refsOn = sb.use_character_refs !== false && sc.mode !== "i2v";
          let nRefs = 0;
          return h("div", { class: "char-chips" }, h("span", { class: "small muted", title: refsOn ? "Mô tả nhân vật được tự thêm vào prompt và ảnh nhân vật được gửi kèm làm tham chiếu" : "Mô tả nhân vật được tự thêm vào prompt" }, (refsOn ? "Nhân vật · mô tả + ảnh" : "Nhân vật · mô tả") + (picked ? " (chọn trên Bảng):" : ":")),
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
              h("option", { value: "" }, `Theo phim (${sb.model ? modelLabel(sb.model) : "mặc định"})`),
              videoModelOptions(sc.model).map((m) => h("option", { value: m.alias }, m.label)))),
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

/* Model status comes from the server (/api/models, or models_used for aliases gflow no longer lists). */
function modelStatus(alias) {
  if (!alias) return null;
  const m = (S.models.video || []).find((x) => x.alias === alias);
  if (m) return { status: m.status, retires_label: m.retires_label, known: m.info !== null };
  return S.cur?.models_used?.[alias] || null;
}
function modelLabel(alias) {
  const m = modelStatus(alias);
  if (!m) return alias;
  if (m.status === "retired") return `${alias} · đã ngừng`;
  if (m.status === "retiring") return `${alias} · sắp ngừng ${m.retires_label || ""}`.trim();
  if (m.known === false) return `${alias} · chưa có thông tin`;
  return alias;
}
function videoModelOptions(current) {
  let list = (S.models.video || []).map((m) => m.alias);
  if (!list.length) list = [S.models.default_video_model || "veo-lite"];
  if (current && !list.includes(current)) list = [...list, current];
  return list.map((alias) => ({ alias, label: modelLabel(alias) }));
}
function modelInfo(model) {
  return (S.models.video || []).find((x) => x.alias === model) || null;
}
function maxDuration(model) {
  return modelInfo(model)?.max_duration || 8;
}
function durationSelect(sc) {
  const model = sc.model || S.cur.storyboard.model;
  const allowed = modelInfo(model)?.info?.durations;
  const cur = sc.duration ? +sc.duration : null;
  let opts, note;
  if (allowed && allowed.length) {
    opts = [...allowed];
    note = (d) => (allowed.includes(d) ? "" : " (model không nhận)");
  } else {
    const max = maxDuration(model);
    opts = [4, 6, 8, 10, 12, 15].filter((d) => d <= max);
    note = (d) => (d > max ? " (quá giới hạn model)" : "");
  }
  if (cur && !opts.includes(cur)) opts.push(cur);
  return h("select", { value: cur ? String(cur) : "", onchange: (e) => { sc.duration = e.target.value ? +e.target.value : undefined; changed(); refreshZone("stats-zone"); } },
    h("option", { value: "" }, "Mặc định"), opts.map((d) => h("option", { value: String(d) }, `${d} giây${note(d)}`)));
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

/* A poster with a play button; the real <video> is made on click. Each <video> costs ~15 ms to insert in WebKit
   (the desktop app), so a board with 20 clips spent ~300 ms on every redraw. */
function lazyVideo(src, poster) {
  const btn = h("button", { type: "button", class: "lazy-video", title: "Phát clip", "aria-label": "Phát clip", "data-src": src },
    poster ? h("img", { src: poster, alt: "", loading: "lazy", decoding: "async" }) : null,
    h("span", { class: "lazy-play" }, icon("play")));
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const v = h("video", { src, controls: true, autoplay: true, playsinline: true, poster: poster || null, "data-lazy-src": src });
    btn.replaceWith(v);
    v.play?.().catch(() => { /* autoplay refused: the controls are there */ });
  });
  return btn;
}

/* opts.kfHint: placeholder for a keyframe scene with no picture yet (the board points at its own button).
   opts.lazy: show clips as a poster + play button (board). */
function renderSceneMedia(sc, { kfHint = null, lazy = false, prog = [`kf:${sc.id}`, `vid:${sc.id}`] } = {}) {
  const st = S.cur.scene_state?.[sc.id] || {};
  const portrait = S.cur.storyboard.aspect === "9:16";
  const box = h("div", { class: "media" + (portrait ? " portrait" : "") });
  const kf = st.keyframe_url;
  const startImg = sc.mode === "i2v" ? (assetUrl(sc.image) || st.image_url) : null;
  const stale = st.status === "changed";
  if (st.clip_url) {
    const poster = st.rendered_as === "t2v" || st.rendered_as === "r2v" ? null : (kf || startImg || null);
    box.append(lazy ? lazyVideo(st.clip_url, poster)
      : h("video", { src: st.clip_url, controls: true, preload: "metadata", poster, playsinline: true }));
    if (stale) box.append(h("span", { class: "tag warn" }, "Clip cũ · prompt đã đổi"));
    else if (st.rendered_as === "t2v" && sc.mode === "keyframe") box.append(h("span", { class: "tag" }, "Render bằng Chữ → Video"));
    else if (st.rendered_as === "flow-ui") box.append(h("span", { class: "tag" }, "Render qua giao diện chat Flow"));
    else if (st.rendered_as === "r2v") box.append(h("span", { class: "tag" }, sc.mode === "keyframe" ? "Render bằng Chữ → Video + ảnh nhân vật" : "Có ảnh nhân vật"));
  } else if (kf) {
    box.append(h("img", { src: kf, onclick: () => lightbox(kf), alt: "keyframe" }));
    box.append(h("span", { class: "tag" + (st.keyframe_stale ? " warn" : ""), title: st.keyframe_stale ? "Prompt, nhân vật hoặc ảnh nhân vật đã đổi từ khi tạo ảnh này" : null }, st.keyframe_stale ? "Ảnh cũ · cần tạo lại" : "Keyframe · chưa có video"));
  } else if (startImg) {
    box.append(h("img", { src: startImg, onclick: () => lightbox(startImg), alt: "ảnh bắt đầu" }));
    box.append(h("span", { class: "tag" }, "Ảnh của bạn"));
  } else {
    box.append(h("div", { class: "ph" }, icon(sc.mode === "t2v" ? "video" : "image"),
      h("span", {}, sc.mode === "keyframe" ? (kfHint || "Bấm “Tạo ảnh” để xem trước khung hình (miễn phí)") : sc.mode === "i2v" ? "Chọn hoặc tải ảnh bắt đầu" : "Video sẽ hiện ở đây sau khi render")));
  }
  progAttach(box, prog);
  return h("div", { class: "stack tight" }, box);
}

/* retry: a failed render shows "Thử lại" (board, AC40). blocked: reason the render button is off (storyboard invalid). */
/* Scene durations the model does not accept (rows from /estimate, /run 409, or scene_state[id].bad_duration). Pure helpers. */
function durationRowText(r) {
  return `${r.duration} giây — ${r.model} chỉ nhận ${(r.allowed || []).join(", ")}`;
}
function durationManualReason(r) {
  if (r.reason === "interrupted") return `bị ngắt khi render, có thể Flow đã trừ credit — app không tự đổi độ dài; kiểm tra Flow/tải clip về clips/${r.id}.mp4, hoặc sửa tay độ dài trong editor`;
  if (r.reason === "done") return "đã có clip — sửa tay độ dài trong editor rồi bấm Render lại";
  return "";
}
const upperFirst = (s) => s ? s[0].toUpperCase() + s.slice(1) : s;
function resendableBlocked(est) {
  const bad = new Set((est.blocked_bad_duration || []).map((r) => r.id));
  return (est.blocked || []).filter((id) => !bad.has(id));
}
function blockedBadDurationText(r) {
  return `Cảnh ${r.id}: ${durationRowText(r)}. Bị ngắt nên app không tự làm tròn: kiểm tra Flow / tải clip về clips/${r.id}.mp4, hoặc sửa tay độ dài trong editor rồi bấm lại.`;
}
function interruptedReasonText(err) {
  const s = String(err ?? "").replace(/^Flow \(giao diện chat\): /, "").trim();
  return s.length > 300 ? s.slice(0, 299) + "…" : s;
}

function renderSceneActions(sc, { keyframe = true, video = true, retry = false, blocked = "" } = {}) {
  const st = S.cur.scene_state?.[sc.id] || {};
  const busy = jobRunning();
  const kids = [];
  if (st.status === "failed" && st.error) kids.push(h("div", { class: "scene-msg err" }, st.error));
  if (st.status === "interrupted") {
    kids.push(h("div", { class: "scene-msg warn" }, "Lần trước bị ngắt khi đang render, có thể Flow đã trừ credit. Hãy mở Flow xem clip đã có chưa trước khi gửi lại."));
    const why = interruptedReasonText(st.error);
    if (why) kids.push(h("div", { class: "scene-msg small" }, "Lý do lần trước: " + why));
    const clipMsg = manualClipMsg(sc, st, busy);
    if (clipMsg) kids.push(clipMsg);
  }
  // Server sends null for done/valid scenes. A hand-downloaded clip that will be taken in makes the advice moot (minor 5).
  if (st.bad_duration && st.manual_clip !== "ok") {
    kids.push(h("div", { class: "scene-msg warn" }, durationRowText(st.bad_duration) + (st.status === "interrupted"
      ? " — bị ngắt nên app không tự làm tròn; sửa tay độ dài hoặc tải clip về"
      : " — bấm Render để làm tròn, hoặc chọn độ dài khác")));
  }
  if (st.status === "done" && st.model_status === "retiring") {
    kids.push(h("div", { class: "scene-msg warn" }, `Model này ngừng ngày ${st.model_retires_label}. Clip hiện tại vẫn dùng được, nhưng Render lại sau ngày đó sẽ lỗi; nên đổi model trước khi render lại (sẽ tốn credit).`));
  } else if (st.status === "done" && st.model_status === "retired") {
    kids.push(h("div", { class: "scene-msg warn" }, `Model này đã ngừng từ ${st.model_retires_label}. Clip hiện tại vẫn dùng được; muốn render lại phải đổi model.`));
  }
  const row = h("div", { class: "row" });
  if (keyframe && sc.mode === "keyframe") {
    const hasKf = !!st.keyframe_url && !st.keyframe_stale;
    row.append(h("button", { class: "btn sm", disabled: busy, title: "Ảnh trên Flow miễn phí",
      onclick: () => runAction("keyframes", { only: [sc.id], force_keyframes: [sc.id] }) }, icon(hasKf ? "refresh" : "image"), hasKf ? "Tạo lại ảnh" : "Tạo ảnh", FREE()));
  }
  const done = st.status === "done";
  const failed = retry && st.status === "failed";
  const label = done ? "Render lại" : st.status === "interrupted" ? "Gửi lại" : failed ? "Thử lại" : "Render";
  if (video) row.append(h("button", { class: "btn sm", disabled: busy || !!blocked, title: busy ? "Đang có việc chạy" : blocked || null,
    onclick: () => runAction("video", { only: [sc.id], force: done || st.status === "interrupted" ? [sc.id] : [] }, { scene: sc.id }) },
  icon(done || failed ? "refresh" : "video"), label, PAID()));
  if (st.clip_url && st.keyframe_url) row.append(h("button", { class: "btn sm ghost icon", title: "Xem ảnh keyframe", "aria-label": "Xem ảnh keyframe", onclick: () => lightbox(st.keyframe_url) }, icon("image")));
  if (st.clip_url) row.append(h("a", { class: "btn sm ghost icon", href: st.clip_url.split("?")[0], download: `${sc.id}.mp4`, title: "Tải clip", "aria-label": "Tải clip" }, icon("download")));
  kids.push(row);
  return h("div", { class: "stack tight" }, kids);
}

// What we know about the clip the user may have downloaded by hand into clips/<id>.mp4 (st.manual_clip from the server).
function manualClipMsg(sc, st, busy) {
  const texts = {
    ok: "Đã thấy clip bạn tải về; lần render tới sẽ dùng clip này, không tốn credit.",
    stale: ["Clip trong thư mục cũ hơn lần gửi, sẽ không được dùng. Nếu chắc đây là bản vừa tải từ Flow, chạy ", h("code", {}, `touch clips/${sc.id}.mp4`), " rồi tải lại trang."],
    invalid: "Clip trong thư mục không đọc được trọn (có thể tải dở).",
    unchecked: "Chưa cài ffprobe nên không kiểm được clip bạn tải về.",
  };
  if (texts[st.manual_clip]) return h("div", { class: "scene-msg" + (st.manual_clip === "ok" ? "" : " warn") }, texts[st.manual_clip]);
  if (st.manual_clip !== "unverified") return null;
  const pid = S.cur.id, skipKey = `adoptSkip.${pid}.${sc.id}`, stamp = st.updated_at || "none";
  if (localGet(skipKey) === stamp) return null; // skipped on this browser; shows again once the state changes
  let adopting = false;
  const adoptBtn = h("button", { class: "btn sm", disabled: busy, title: "Đánh dấu cảnh xong bằng clip trong thư mục (không gửi lên Flow)",
    onclick: async () => {
      if (adopting) return;
      adopting = true; adoptBtn.disabled = true;
      try {
        const d = await api("POST", `/api/projects/${pid}/scenes/${sc.id}/adopt-clip`);
        if (S.cur && S.cur.id === pid) { if (d && d.id === pid) applyMeta(d); else refreshCurrent(); }
        toast("Đã dùng clip bạn tải về", "ok");
      } catch (e) {
        toast(e.message, "error");
        adopting = false; adoptBtn.disabled = jobRunning();
      }
    } }, icon("check"), "Dùng clip này");
  return h("div", { class: "scene-msg warn" },
    h("div", {}, "Có clip trong thư mục nhưng chưa rõ là bản mới hay cũ."),
    h("div", { class: "row", style: { marginTop: "6px" } }, adoptBtn,
      h("button", { class: "btn sm ghost", onclick: () => { localSet(skipKey, stamp); refreshZones(); } }, "Bỏ qua")));
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
          h("div", { class: "field" }, h("label", { class: "lbl", for: "sb-model" }, "Model video"),
            h("select", { id: "sb-model", value: sb.model || "", onchange: (e) => { set("model", e.target.value || undefined); } },
              h("option", { value: "" }, "Mặc định của Flow"), videoModelOptions(sb.model).map((m) => h("option", { value: m.alias }, m.label))),
            zone("model-note", () => h("span", { class: "help", hidden: !S.cur.model_note }, S.cur.model_note || ""))),
          field("Model ảnh (keyframe)",
            h("select", { value: sb.image_model || "nano2", onchange: (e) => set("image_model", e.target.value) },
              imageModels.map((m) => h("option", { value: m.alias }, m.label ? `${m.label} (${m.alias})` : m.alias))))),
        h("div", { class: "row" },
          h("p", { class: "help", style: { flex: "1 1 320px" } }, "Mẹo: tạo keyframe (miễn phí) và duyệt trước khi render. Đổi model ở đây làm các cảnh đã xong phải render lại; muốn giữ clip đã có thì dùng “Đổi model cho các cảnh chưa render”."),
          zone("model-change-btn", () => h("button", { class: "btn sm", disabled: jobRunning(), title: "Chỉ đổi cảnh chưa có clip dùng model sắp/đã ngừng; không render, không tốn credit",
            onclick: () => openModelChange() }, icon("refresh"), MODEL_CHANGE_LABEL))),
        field("Phong cách hình ảnh",
          h("textarea", { rows: 2, value: sb.style || "", placeholder: "cinematic, warm morning light, shallow depth of field, 35mm film grain",
            oninput: (e) => set("style", e.target.value) }), "thêm vào mọi prompt"),
        renderDialogueLang(sb, set),
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

/* Key order as backend _dump_dict: language keys right after `aspect` (write_raw dumps in object order).
   Reorders `sb` in place, since other code holds references to it. */
function placeLangKeys(sb) {
  if (!("aspect" in sb)) return;
  const entries = Object.entries(sb), langs = ["dialogue_language", "subtitle_language"].filter((k) => k in sb);
  for (const [k] of entries) delete sb[k];
  for (const [k, v] of entries) {
    if (langs.includes(k)) continue;
    sb[k] = v;
    if (k === "aspect") for (const l of langs) sb[l] = entries.find(([x]) => x === l)[1];
  }
}

/* "Nhân vật nói": saved like other fields (autosave only, no job). Scenes that already have a clip keep the old language. */
function renderDialogueLang(sb, set) {
  const p = langPicker(DIALOGUE_LANGS, sb.dialogue_language, { unset: true });
  const save = (e) => {
    if (p.select.value === LANG_OTHER && !p.input.value.trim()) {
      if (e.target === p.select) return; // "Khác…" just picked: wait for the name
      // name erased: show the saved value again so the UI never differs from storyboard.yaml
      const cur = cleanLang(sb.dialogue_language), hit = cur && DIALOGUE_LANGS.find(([v]) => v.toLowerCase() === cur.toLowerCase());
      p.select.value = !cur ? "" : hit ? hit[0] : LANG_OTHER;
      p.input.value = cur && !hit ? cur : "";
      p.input.hidden = p.select.value !== LANG_OTHER;
      toast("Nhân vật nói: chưa nhập tên ngôn ngữ, giữ giá trị đã lưu", "error");
      return;
    }
    const r = p.read();
    if (!r.ok) { toast("Nhân vật nói: " + r.msg, "error"); return; }
    if ((r.value ?? null) === (cleanLang(sb.dialogue_language) ?? null)) return;
    const isNew = !("dialogue_language" in sb);
    set("dialogue_language", r.value ?? undefined); // undefined = key dropped from storyboard.yaml
    if (isNew) placeLangKeys(sb);
    S.dlChanged = S.cur.id;
    refreshZone("dl-warn");
  };
  p.select.addEventListener("change", save);
  p.input.addEventListener("change", save);
  return h("div", { class: "stack tight" },
    field("Nhân vật nói", p.el, "thêm vào mọi prompt video"),
    zone("dl-warn", () => {
      const n = S.cur.clips_rendered || 0, show = S.dlChanged === S.cur.id && n >= 1;
      return h("div", { class: "banner warn flush" + (show ? "" : " hidden"), hidden: !show }, icon("alert"), h("div", { class: "b-body" }, show
        ? `Chỉ áp dụng cho các cảnh render từ giờ. ${n} cảnh đã có clip vẫn giữ tiếng cũ. Muốn đổi tiếng thì bấm Render lại từng cảnh (tốn credit).` : ""));
    }));
}

function charImageUrl(c) {
  if (!c.image) return null;
  const i = (S.cur.storyboard.characters || []).indexOf(c);
  const cs = S.cur.characters_state?.[i];
  return assetUrl(c.image) || (cs && cs.name === c.name ? cs.image_url : null);
}

async function makePortraits(names, force = false) {
  await flushSave();
  if (saveFailed()) { toast("Chưa lưu được storyboard: " + S.saveError, "error"); return; }
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
  const badName = charNameProblems(sb).has(i);
  // A zone, so a portrait made mid-job (char:<name> → done) shows up without re-rendering the inputs.
  const charImg = () => {
    const u = charImageUrl(c), st = S.cur.characters_state?.[i];
    const div = h("div", { class: "char-img" + (portrait ? " portrait" : ""), style: u ? { backgroundImage: `url("${u}")` } : { cursor: "default" },
      title: u ? "Bấm để xem lớn" : "", onclick: () => { if (u) lightbox(u); } },
    u ? null : h("span", {}, c.image && st?.image_missing ? "Không thấy file ảnh" : "Chưa có ảnh"));
    return progAttach(div, [progCharKey(c)]);
  };
  return h("div", { class: "char-row" },
    zone(`charimg-${i}`, charImg),
    h("div", { class: "stack tight", style: { minWidth: 0 } },
      h("input", { type: "text", value: c.name, placeholder: "Tên, ví dụ: Lan", "aria-label": "Tên nhân vật", "data-char": i,
        "aria-invalid": badName ? "true" : null, title: badName ? "Tên trống hoặc trùng với nhân vật khác" : null,
        oninput: (e) => { c.name = e.target.value; markCharNames(); changed(); } }),
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
      if (await removeCharacter(i)) renderMain();
    } }, icon("trash")));
}
/* Mark empty/duplicate names while typing, without re-rendering the inputs. */
function markCharNames() {
  const bad = charNameProblems(S.cur.storyboard);
  for (const el of document.querySelectorAll(".char-row input[data-char]")) {
    const on = bad.has(+el.dataset.char);
    if (on) { el.setAttribute("aria-invalid", "true"); el.title = "Tên trống hoặc trùng với nhân vật khác"; }
    else { el.removeAttribute("aria-invalid"); el.removeAttribute("title"); }
  }
}

/* ---------- final tab ---------- */

function finalPrefs() {
  return { music: null, volume: 0.25, burn_subtitles: false, allow_missing: false, smart_edit: true, ...(S.cur.final_prefs || {}) };
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
                h("button", { class: "btn primary", disabled: busy || !n, onclick: () => runAction("edit"), title: "Agent dựng phim: cắt phần thừa, chuyển cảnh, cân âm lượng, nhạc nền → phim liền mạch" }, icon("sparkle"), "AI dựng phim", FREE()),
                h("button", { class: "btn", disabled: busy || !n, onclick: () => runAction("stitch"), title: "Nối thẳng các clip, không chuyển cảnh" }, icon("stitch"), "Ghép thẳng"),
                h("button", { class: "btn", disabled: busy, onclick: () => runAction("make"), title: "Render các cảnh còn thiếu rồi dựng phim" }, "Render nốt + dựng", PAID())),
              h("div", { class: "help" }, `${n}/${total} cảnh đã có clip.`));
          }))),
      renderEditPlan(),
      renderThumbnail(),
      h("div", { class: "card card-pad" }, h("div", { class: "card-head" }, h("h3", {}, "Lời dẫn / phụ đề")),
        (S.cur.storyboard.scenes || []).some((s) => s.narration)
          ? h("ol", { class: "narr-list" }, S.cur.storyboard.scenes.filter((s) => s.narration).map((s) => h("li", {}, h("span", { class: "mono muted" }, s.id + " "), s.narration)))
          : h("p", { class: "help" }, "Chưa có. Thêm “Phụ đề / lời dẫn” ở từng cảnh."))));
}

/* ---------- editor agent (edit plan) ---------- */

async function loadEditPlan(force = false) {
  if (!S.cur) return;
  const pid = S.cur.id;
  if (!force && S.edit && S.edit.pid === pid) return;
  try {
    const data = await api("GET", `/api/projects/${pid}/edit-plan`);
    if (S.cur?.id !== pid) return;
    S.edit = { pid, data, dirty: false };
  } catch (e) { S.edit = { pid, data: null, error: e.message }; }
  refreshZone("edit-zone");
}

function renderEditPlan() {
  if (!S.edit || S.edit.pid !== S.cur.id) setTimeout(() => loadEditPlan(), 0);
  return h("div", { class: "card card-pad" },
    h("div", { class: "card-head" }, h("h3", {}, "Bản dựng của agent"), h("span", { class: "spacer" }),
      h("span", { class: "help" }, "Agent xem kịch bản và phân tích từng clip (khung đen, đứng hình, âm lượng) rồi chọn chỗ cắt, kiểu chuyển cảnh và cách trộn âm để phim liền mạch. Sửa được từng chuyển cảnh rồi xuất lại miễn phí.")),
    zone("edit-zone", editPlanView));
}

function editPlanView() {
  const busy = jobRunning();
  const E = S.edit && S.edit.pid === S.cur.id ? S.edit : null;
  const plan = E?.data?.plan;
  if (!E) return h("div", { class: "help" }, "Đang tải…");
  if (!plan) return h("div", { class: "stack tight" },
    h("p", { class: "help" }, "Chưa có bản dựng. Bấm “AI dựng phim” để agent dựng phim từ các clip đã render."));
  const catalog = E.data.transitions || [];
  const scenes = Object.fromEntries((S.cur.storyboard.scenes || []).map((s) => [s.id, s]));
  const clipLen = (c) => { const d = E.data.clips?.[c.id]?.duration; return d ? Math.max(0, d - c.trim_start - c.trim_end) : null; };
  const touch = () => { E.dirty = true; refreshZone("edit-zone"); };
  const num = (val, min, max, step, onset, label) => h("input", { type: "number", min, max, step, value: val, "aria-label": label,
    style: { width: "68px" }, onchange: (e) => { onset(Math.min(max, Math.max(min, +e.target.value || 0))); touch(); } });
  const src = { ai: "Agent AI", rules: "Luật tự động", manual: "Bạn đã chỉnh" }[plan.source] || plan.source;
  const rows = [];
  plan.clips.forEach((c, i) => {
    const len = clipLen(c);
    rows.push(h("tr", {},
      h("td", { class: "mono" }, c.id),
      h("td", { class: "small muted", title: scenes[c.id]?.prompt || "" }, (scenes[c.id]?.narration || scenes[c.id]?.prompt || "").slice(0, 48)),
      h("td", {}, num(c.trim_start, 0, 3, 0.1, (v) => { c.trim_start = v; }, `Cắt đầu ${c.id}`)),
      h("td", {}, num(c.trim_end, 0, 3, 0.1, (v) => { c.trim_end = v; }, `Cắt cuối ${c.id}`)),
      h("td", {}, num(c.volume, 0, 2, 0.1, (v) => { c.volume = v; }, `Âm lượng ${c.id}`)),
      h("td", { class: "small muted" }, len != null ? len.toFixed(1) + "s" : "")));
    const t = plan.transitions[i];
    if (!t) return;
    rows.push(h("tr", { class: "edit-tr" },
      h("td", { colspan: 6 },
        h("div", { class: "row", style: { flexWrap: "nowrap", gap: "8px" } },
          h("span", { class: "muted" }, "↳"),
          h("select", { "aria-label": `Chuyển cảnh sau ${t.after}`, value: t.type, onchange: (e) => { t.type = e.target.value; if (t.type !== "cut" && !t.duration) t.duration = 0.5; t.reason = ""; touch(); } },
            catalog.map((o) => h("option", { value: o.id }, o.label))),
          t.type !== "cut" ? num(t.duration, 0.2, 2, 0.1, (v) => { t.duration = v; }, `Độ dài chuyển cảnh sau ${t.after}`) : null,
          t.type !== "cut" ? h("span", { class: "small muted" }, "s") : null,
          t.reason ? h("span", { class: "small muted", style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, title: t.reason }, t.reason) : null))));
  });
  const save = async (render) => {
    try {
      const data = await api("PUT", `/api/projects/${S.cur.id}/edit-plan`, plan);
      S.edit = { pid: S.cur.id, data, dirty: false };
      refreshZone("edit-zone");
      if (render) runAction("edit");
      else toast("Đã lưu bản dựng");
    } catch (e) { toast(e.message, "error"); }
  };
  return h("div", { class: "stack" },
    h("div", { class: "row" }, h("span", { class: "badge film" }, src),
      E.data.total ? h("span", { class: "small muted" }, "Độ dài phim · " + fmtDur(E.data.total)) : null,
      E.dirty ? h("span", { class: "badge changed" }, "Chưa lưu") : null),
    plan.notes ? h("p", { class: "small" }, plan.notes) : null,
    h("div", { style: { overflowX: "auto" } },
      h("table", { class: "list edit-table" },
        h("tr", {}, h("th", {}, "Cảnh"), h("th", {}, "Nội dung"), h("th", {}, "Cắt đầu"), h("th", {}, "Cắt cuối"), h("th", {}, "Âm lượng"), h("th", {}, "Còn")),
        rows)),
    h("div", { class: "row" },
      h("label", { class: "field" }, h("span", { class: "lbl" }, "Mở đầu mờ (s)"), num(plan.fade_in, 0, 3, 0.1, (v) => { plan.fade_in = v; }, "Mở đầu mờ")),
      h("label", { class: "field" }, h("span", { class: "lbl" }, "Kết mờ (s)"), num(plan.fade_out, 0, 4, 0.1, (v) => { plan.fade_out = v; }, "Kết mờ"))),
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: plan.even_loudness, onchange: (e) => { plan.even_loudness = e.target.checked; touch(); } }),
      h("span", {}, "Cân bằng âm lượng giữa các clip", h("span", { class: "sub" }, "Clip to/nhỏ khác nhau sẽ được kéo về cùng mức."))),
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: plan.duck_music, onchange: (e) => { plan.duck_music = e.target.checked; touch(); } }),
      h("span", {}, "Tự hạ nhạc nền khi có thoại", h("span", { class: "sub" }, "Chỉ có tác dụng khi chọn nhạc nền."))),
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: plan.normalize, onchange: (e) => { plan.normalize = e.target.checked; touch(); } }),
      h("span", {}, "Chuẩn hoá độ lớn cho mạng xã hội", h("span", { class: "sub" }, "-14 LUFS, như YouTube / TikTok / Facebook."))),
    h("div", { class: "row" },
      h("button", { class: "btn primary sm", disabled: busy, onclick: () => save(true) }, icon("film"), E.dirty ? "Lưu & xuất lại" : "Xuất lại theo bản này", FREE()),
      E.dirty ? h("button", { class: "btn sm", onclick: () => save(false) }, icon("check"), "Chỉ lưu") : null,
      E.dirty ? h("button", { class: "btn sm ghost", onclick: () => loadEditPlan(true) }, "Bỏ thay đổi") : null,
      h("span", { class: "spacer" }),
      h("button", { class: "btn sm ghost", disabled: busy, title: "Agent bỏ bản này và dựng lại từ đầu", onclick: () => runAction("edit", { replan: true }) }, icon("refresh"), "Dựng lại từ đầu")));
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
          S.cur = d; rememberCharNames(); toast(d.errors.length ? "Đã lưu, nhưng storyboard còn lỗi" : "Đã lưu YAML", d.errors.length ? "error" : "ok");
          renderMain(); loadProjects();
        } catch (e) { toast(e.message, "error"); }
      } }, "Lưu YAML"),
      h("button", { class: "btn ghost", onclick: () => { ta.value = S.cur.yaml; } }, "Hoàn tác")));
}

/* ---------- model change (retiring/retired models) ---------- */

const MODEL_CHANGE_LABEL = "Đổi model cho các cảnh chưa render";
const INTERRUPTED_NOTE = "Đổi model không xoá trạng thái bị ngắt: khi render cảnh này, bạn vẫn phải tick “Tôi đã kiểm tra, gửi lại các cảnh này” trong hộp xác nhận, kể cả khi bấm “Gửi lại” trên cảnh.";

/* Replace the whole project (storyboard included) with the server copy, so the next autosave cannot undo a model change. */
function reloadProject(d) {
  if (!S.cur || !d || S.cur.id !== d.id) return;
  clearTimeout(S.saveTimer);
  S.pendingSave = false;
  Object.assign(S.cur, d);
  rememberCharNames();
  S.saveState = "saved"; S.saveError = "";
  const p = S.projects.find((x) => x.id === d.id);
  if (p) { Object.assign(p, { title: d.title, scenes: d.scenes, done: d.done, has_final: d.has_final, thumb: d.thumb }); renderSidebar(); }
  renderMain();
}

function activeVideoModels() {
  return (S.models.video || []).filter((m) => m.status === "active" && m.info != null).map((m) => m.alias);
}
function creditText(pv) {
  if (pv.credits == null) return "chưa có thông tin giá";
  const n = Math.round(pv.credits * 10) / 10;
  return `${pv.credits_approx ? "~" : ""}${n.toLocaleString("vi-VN")} credit khi render`;
}

/* scenes: [id] → one-scene mode (from a scene button). Otherwise project mode; include = ids blocked at render time. */
async function openModelChange({ scenes = null, include = [], blockedCount = 0 } = {}) {
  if (!S.cur) return;
  await flushSave();
  if (saveFailed()) { toast("Chưa lưu được storyboard: " + S.saveError, "error"); return; }
  const targets = activeVideoModels();
  if (!targets.length) { toast("Chưa có model video nào đang hoạt động để đổi sang. Thử mở lại dự án sau.", "error", 7000); return; }
  const pid = S.cur.id, one = !!(scenes && scenes.length);
  const st = { target: targets.includes(S.models.default_video_model) ? S.models.default_video_model : targets[0],
    rounding: null, pv: null, loading: false, error: "", seq: 0, applying: false };
  const reqBody = () => ({ target: st.target, rounding: st.rounding, ...(one ? { scenes } : { include }) });
  const box = h("div", { class: "stack" });
  const okBtn = () => document.getElementById("mc-ok");
  const canApply = () => !!st.pv && !st.loading && !st.applying && st.pv.has_changes && !jobRunning() && !(st.pv.bad_duration.length && !st.rounding);
  const syncBtn = () => { const b = okBtn(); if (b) { b.disabled = !canApply(); b.textContent = st.applying ? "Đang đổi…" : "Đổi model"; } };

  const draw = () => {
    const pv = st.pv;
    if (st.loading && !pv) { put(box, h("div", { class: "row muted small" }, h("span", { class: "spinner" }), "Đang tính các cảnh sẽ đổi…")); syncBtn(); return; }
    if (st.error) {
      put(box, h("div", { class: "banner err flush", role: "alert" }, icon("alert"), h("div", { class: "b-body" },
        h("div", {}, "Không xem trước được: " + st.error), h("div", {}, h("button", { class: "btn sm", onclick: preview }, icon("refresh"), "Thử lại")))));
      syncBtn(); return;
    }
    if (!pv) { put(box); syncBtn(); return; }
    const n = pv.change.length;
    const lines = [];
    if (one) {
      if (pv.redo.length) lines.push(h("div", {}, `Cảnh này sẽ phải render lại, ${creditText(pv)}.`));
      else if (n) lines.push(h("div", {}, `Cảnh ${pv.change.join(", ")} sẽ đổi sang ${pv.target}, ${creditText(pv)}.`));
    } else {
      lines.push(h("div", {}, h("b", {}, `${n} cảnh sẽ đổi sang ${pv.target}`), `, ${creditText(pv)}.`,
        n ? h("span", { class: "mono" }, " (" + pv.change.join(", ") + ")") : null));
      if (pv.redo.length) lines.push(h("div", {}, `Trong đó ${pv.redo.length} cảnh đã có clip sẽ phải render lại: `, h("span", { class: "mono" }, pv.redo.join(", "))));
      if (pv.keep.length) lines.push(h("div", {}, `${pv.keep.length} cảnh đã có clip hoặc bị ngắt giữ nguyên clip và model cũ`,
        pv.pin.length ? ` (ghim model cũ cho ${pv.pin.length} cảnh)` : "", "."));
      if (pv.storyboard_model) lines.push(h("div", {}, "storyboard.model: ", h("span", { class: "mono" }, `${pv.storyboard_model.from} → ${pv.storyboard_model.to}`)));
    }
    if (!pv.has_changes) lines.push(h("div", { class: "muted" }, "Không có gì cần đổi."));
    const round = pv.bad_duration.length ? h("div", { class: "stack tight" },
      h("div", { class: "row" }, h("span", { class: "lbl" }, `${pv.bad_duration.length} cảnh có độ dài ${pv.target} không nhận:`),
        h("div", { class: "seg", role: "group", "aria-label": "Làm tròn độ dài" }, [["up", "Làm tròn lên"], ["down", "Làm tròn xuống"]].map(([v, l]) =>
          h("button", { class: st.rounding === v ? "on" : "", "aria-pressed": String(st.rounding === v), disabled: st.loading,
            onclick: () => { st.rounding = v; preview(); } }, l)))),
      h("table", { class: "list" },
        h("tr", {}, h("th", {}, "Cảnh"), h("th", {}, "Độ dài hiện tại"), h("th", {}, "Sau khi đổi")),
        pv.bad_duration.map((b) => h("tr", {}, h("td", { class: "mono" }, b.id), h("td", {}, b.duration + " giây"),
          h("td", {}, st.rounding ? `${st.rounding === "up" ? b.up : b.down} giây` : h("span", { class: "muted" }, `lên ${b.up} giây / xuống ${b.down} giây`))))),
      !st.rounding ? h("p", { class: "help" }, "Chọn làm tròn lên hoặc xuống thì mới đổi được.") : null) : null;
    const intr = pv.interrupted.length ? h("div", { class: "banner warn flush" }, icon("alert"), h("div", { class: "b-body" },
      h("div", {}, `Cảnh ${pv.interrupted.join(", ")} bị ngắt, có thể đã trừ credit, kiểm tra trên Flow trước.`),
      h("div", {}, INTERRUPTED_NOTE))) : null;
    put(box,
      h("div", { class: "stack tight" + (st.loading ? " muted" : "") }, lines),
      round, intr,
      jobRunning() ? h("p", { class: "help" }, "Đang có job chạy. Đợi job xong rồi mới đổi model.") : null,
      h("p", { class: "help" }, "Chỉ ghi storyboard, không render, không tốn credit. Muốn render thì bấm Render sau khi đổi."));
    syncBtn();
  };

  async function preview() {
    const my = ++st.seq;
    st.loading = true; st.error = ""; draw();
    await flushSave(); // AC44: preview what is on disk
    if (saveFailed()) { st.loading = false; st.error = "chưa lưu được storyboard: " + S.saveError; draw(); return; }
    try {
      const pv = await api("POST", `/api/projects/${pid}/model-change/preview`, reqBody());
      if (my !== st.seq) return;
      st.pv = pv;
    } catch (e) {
      if (my !== st.seq) return;
      st.pv = null; st.error = e.message;
    }
    st.loading = false; draw();
  }

  async function apply(close) {
    if (!canApply()) { if (jobRunning()) toast("Đang có job chạy. Đợi job xong rồi mới đổi model.", "error"); return; }
    st.applying = true; syncBtn();
    try {
      await flushSave();
      if (saveFailed()) throw new Error("Chưa lưu được storyboard: " + S.saveError);
      const r = await api("POST", `/api/projects/${pid}/model-change`, { ...reqBody(), base: st.pv.base });
      close();
      reloadProject(r.project);
      if (r.changed) toast("Đã đổi model. Bấm Render khi sẵn sàng.", "ok", 6000);
      else toast(r.message || "Không có gì thay đổi");
    } catch (e) {
      st.applying = false; syncBtn();
      if (e.status === 409) { close(); toast("Storyboard vừa bị sửa ở nơi khác, mở lại để xem trước lại", "error", 8000); return; }
      toast(e.message, "error", 7000);
    }
  }

  const sel = h("select", { id: "mc-target", value: st.target, onchange: (e) => { st.target = e.target.value; preview(); } },
    targets.map((a) => h("option", { value: a }, a)));
  const body = h("div", { class: "stack" },
    blockedCount ? h("div", { class: "banner err flush", role: "alert" }, icon("alert"), h("div", { class: "b-body" }, `Không thể render: ${blockedCount} cảnh dùng model đã ngừng`)) : null,
    h("p", { class: "help" }, one
      ? `Chỉ đổi model của cảnh ${scenes.join(", ")}. Model chung của dự án và các cảnh khác giữ nguyên.`
      : "Chỉ đổi các cảnh chưa có clip đang dùng model sắp/đã ngừng" + (include.length ? ", cùng các cảnh vừa bị chặn khi render" : "") + ". Cảnh đã chọn model còn hoạt động giữ nguyên."),
    h("div", { class: "field" }, h("label", { class: "lbl", for: "mc-target" }, "Đổi sang model"), sel,
      h("span", { class: "help" }, "Chỉ có các model đang hoạt động.")),
    box);
  modal({
    title: one ? `Đổi model cho cảnh ${scenes.join(", ")}` : MODEL_CHANGE_LABEL, body, wide: true,
    onClose: () => { st.seq++; },
    buttons: [{ label: "Huỷ" }, { label: "Đổi model", id: "mc-ok", cls: "primary", disabled: true, onclick: apply }],
  });
  preview();
}

/* ---------- run & jobs ---------- */

async function runAction(action, extra = {}, ctx = {}) {
  if (!S.cur) return;
  await flushSave();
  if (saveFailed()) { toast("Chưa lưu được storyboard: " + S.saveError, "error"); return; }
  if (S.cur.errors?.length) { toast("Storyboard còn lỗi: " + prettyError(S.cur.errors[0]), "error"); return; }
  if (action !== "dry" && action !== "stitch" && action !== "edit" && !S.env.gflow) { toast("Chưa cài gflow-cli", "error"); return; }
  if ((action === "stitch" || action === "edit" || action === "make") && !S.env.ffmpeg) { toast("Chưa cài ffmpeg nên không ghép phim được", "error"); return; }
  if (action === "edit" && S.edit?.dirty && !extra.replan && !(await confirmModal("Bỏ thay đổi chưa lưu?", "Bản dựng có chỉnh sửa chưa lưu. Bấm “Lưu & xuất lại” để giữ chúng.", "Vẫn dựng"))) return;
  const body = { action, only: [], force: [], force_keyframes: [], ...extra };
  if (action === "stitch" || action === "edit" || action === "make") Object.assign(body, finalPrefs(), extra);
  if (action === "video" || action === "make") {
    const ok = await confirmPaid(body, ctx);
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
  } catch (e) {
    // The server re-checks retired models (e.g. ticking "gửi lại" added an interrupted scene after /estimate).
    if (e.status === 409 && e.detail?.reason === "model_retired" && Array.isArray(e.detail.retired)) { openBlockedModelChange(e.detail.retired, ctx); return; }
    // Safety net: a scene with a duration the model rejects was about to be sent. Offer the fix; never re-run on our own.
    if (e.status === 409 && e.detail?.reason === "bad_duration" && Array.isArray(e.detail.bad_duration)) {
      const r = await openDurationFix(e.detail.bad_duration, ctx);
      if (r === "applied") toast("Đã làm tròn độ dài. Bấm Render khi sẵn sàng.", "ok", 6000);
      return;
    }
    // An interrupted scene (Flow may have billed it) was not confirmed: show why and reload, never resend on our own.
    if (e.status === 428 && e.detail?.reason === "needs_confirm") { toast(e.message, "error", 8000); refreshCurrent(); return; }
    toast(e.message, "error");
  }
}

function openBlockedModelChange(retired, ctx = {}) {
  const ids = retired.map((r) => r.id);
  if (ctx.scene) openModelChange({ scenes: [ctx.scene], blockedCount: ids.length });
  else openModelChange({ include: ids, blockedCount: ids.length });
}

/* "Độ dài không hợp lệ": rows = bad_duration from /estimate or a /run 409. Rounding covers every roundable scene of the film
   (durations/preview), called once; "Áp dụng" sends that preview's base. Resolves "applied" | "closed". */
async function openDurationFix(rows, ctx = {}, { again = false } = {}) {
  if (!S.cur) return "closed";
  const pid = S.cur.id;
  const roundable = rows.filter((r) => r.roundable), manual = rows.filter((r) => !r.roundable);
  if (roundable.length) {
    await flushSave();
    if (saveFailed()) { toast("Chưa lưu được storyboard: " + S.saveError, "error"); return "closed"; }
  }
  const st = { dir: null, pv: null, loading: false, error: "", stale: false, applying: false, seq: 0 };
  const box = h("div", { class: "stack" });
  const okBtn = () => document.getElementById("df-ok");
  const canApply = () => !!st.pv && !st.loading && !st.applying && !!st.dir && st.pv.scenes.length > 0 && !jobRunning();
  const syncBtn = () => { const b = okBtn(); if (b) { b.disabled = !canApply(); b.textContent = st.applying ? "Đang áp dụng…" : "Áp dụng"; b.style.display = st.error ? "none" : ""; }
    const c = document.getElementById("df-cancel"); if (c) c.disabled = st.applying; };

  const roundPart = () => {
    if (!roundable.length) return null;
    const pv = st.pv;
    if (st.loading && !pv) return h("div", { class: "row muted small" }, h("span", { class: "spinner" }), "Đang tính các cảnh sẽ làm tròn…");
    if (st.error) return h("div", { class: "banner err flush", role: "alert" }, icon("alert"), h("div", { class: "b-body" },
      h("div", {}, "Không xem trước được: " + st.error), h("div", {}, h("button", { class: "btn sm", onclick: preview }, icon("refresh"), "Thử lại"))));
    if (!pv) return null;
    const head = h("div", { class: "lbl" }, "Làm tròn được");
    if (!pv.scenes.length) return h("div", { class: "stack tight" }, head, h("p", { class: "muted" }, "Không còn cảnh nào cần làm tròn."));
    return h("div", { class: "stack tight" + (st.loading ? " muted" : "") }, head,
      st.stale ? h("div", { class: "banner warn flush", role: "alert" }, icon("alert"), h("div", { class: "b-body" }, "Storyboard vừa thay đổi — đã tải lại bảng, xem lại rồi bấm Áp dụng")) : null,
      h("div", { class: "row" }, h("span", {}, h("b", {}, `Áp dụng sẽ đổi độ dài ${pv.scenes.length} cảnh trong cả phim`)),
        h("div", { class: "seg", role: "group", "aria-label": "Hướng làm tròn" }, [["up", "Làm tròn lên"], ["down", "Làm tròn xuống"]].map(([v, l]) =>
          h("button", { class: st.dir === v ? "on" : "", "aria-pressed": String(st.dir === v), onclick: () => { st.dir = v; draw(); } }, l)))),
      h("table", { class: "list" },
        h("tr", {}, h("th", {}, "Cảnh"), h("th", {}, "Model"), h("th", {}, "Độ dài hiện tại"), h("th", {}, "Model nhận"), h("th", {}, "Sẽ thành")),
        pv.scenes.map((s) => h("tr", {}, h("td", { class: "mono" }, s.id), h("td", { class: "mono" }, s.model), h("td", {}, s.duration + " giây"),
          h("td", {}, (s.allowed || []).join(", ")),
          h("td", {}, st.dir ? `${st.dir === "up" ? s.up : s.down} giây` : h("span", { class: "muted" }, `lên ${s.up} / xuống ${s.down}`))))),
      // Kept in the layout once a direction is picked so the dialog does not shrink and shift the buttons (QA 2).
      h("p", { class: "help", style: st.dir ? "visibility:hidden" : null, "aria-hidden": st.dir ? "true" : null }, "Chọn làm tròn lên hoặc xuống thì mới áp dụng được."),
      ctx.scene ? h("p", { class: "help" }, `Dù bạn chỉ render cảnh ${ctx.scene}, áp dụng sẽ làm tròn mọi cảnh trong bảng.`) : null);
  };
  const manualPart = () => manual.length ? h("div", { class: "banner warn flush" }, icon("alert"), h("div", { class: "b-body" },
    h("div", {}, h("b", {}, "Cần bạn tự xử lý")),
    manual.map((r) => h("div", {}, h("span", { class: "mono" }, r.id), ": " + durationRowText(r) + ". " + upperFirst(durationManualReason(r)))))) : null;
  const draw = () => {
    put(box,
      again && !roundable.length ? h("p", {}, "Đã làm tròn xong, nhưng vẫn còn cảnh app không tự sửa được.") : null,
      roundPart(), manualPart(),
      roundable.length && jobRunning() ? h("p", { class: "help" }, "Đang có job chạy. Đợi job xong rồi mới áp dụng.") : null,
      h("p", { class: "help" }, roundable.length ? "Chỉ ghi storyboard, không render, không tốn credit." : "Sửa xong thì bấm Render lại."));
    syncBtn();
  };

  async function preview() {
    const my = ++st.seq;
    st.loading = true; st.error = ""; draw();
    try {
      const pv = await api("POST", `/api/projects/${pid}/durations/preview`);
      if (my !== st.seq) return;
      st.pv = pv;
    } catch (e) {
      if (my !== st.seq) return;
      st.pv = null; st.error = e.message;
    }
    st.loading = false; draw();
  }

  return new Promise((resolve) => {
    let result = "closed";
    async function apply(close) {
      if (!canApply()) { if (jobRunning()) toast("Đang có job chạy. Đợi job xong rồi mới áp dụng.", "error"); return; }
      st.applying = true; syncBtn();
      try {
        const r = await api("POST", `/api/projects/${pid}/durations/round`, { direction: st.dir, base: st.pv.base });
        result = "applied";
        close();
        reloadProject(r.project);
      } catch (e) {
        st.applying = false;
        if (e.status === 409 && e.detail?.reason === "stale_base") { st.stale = true; preview(); return; } // never re-round on our own
        syncBtn();
        toast(e.message, "error", 7000);
      }
    }
    modal({
      title: "Độ dài không hợp lệ", body: box, wide: true,
      onClose: () => { st.seq++; resolve(result); },
      canClose: () => !st.applying, // durations/round in flight: it may already have written the storyboard
      buttons: roundable.length
        ? [{ label: "Huỷ", id: "df-cancel" }, { label: "Áp dụng", id: "df-ok", cls: "primary", disabled: true, onclick: apply }]
        : [{ label: "Đóng" }],
    });
    draw();
    if (roundable.length) preview();
  });
}

async function confirmPaid(body, ctx = {}, again = false) {
  let est;
  // Only scenes the user ticks below are resent; retry_unknown is never sent from the web.
  delete body.retry_unknown;
  body.confirm_resend = [];
  try { est = await api("POST", `/api/projects/${S.cur.id}/estimate`, body); } catch (e) { toast(e.message, "error"); return false; }
  if (est.retired?.length) { // never send a retired model to Flow: offer the model change instead
    openBlockedModelChange(est.retired, ctx);
    return false;
  }
  if (est.bad_duration?.length) { // never send a duration the model rejects: offer rounding instead of the credit box
    if (again && est.bad_duration.some((r) => r.roundable)) {
      toast("Vẫn còn cảnh có độ dài model không nhận sau khi làm tròn — kiểm tra models.yaml/storyboard rồi thử lại", "error", 8000);
      return false;
    }
    const r = await openDurationFix(est.bad_duration, ctx, { again });
    if (r !== "applied") return false;
    return confirmPaid(body, ctx, true);
  }
  const adopt = est.adopt || [], unverified = est.unverified_clip || [];
  if (!est.renders.length && !est.blocked.length && adopt.length) {
    // Only hand-downloaded clips to take in: no credit spent, so no confirmation needed.
    adopt.forEach((id, i) => toast(est.adopt_lines?.[i] || `Cảnh ${id}: dùng clip bạn đã tải về clips/${id}.mp4 (không tốn credit)`, "ok", 6000));
    return true;
  }
  if (!est.renders.length && !est.blocked.length) {
    if (body.action === "make") return true; // nothing to render: go straight to stitching
    toast("Tất cả cảnh đã render xong. Muốn làm lại cảnh nào thì bấm “Render lại” ở cảnh đó.");
    return false;
  }
  return new Promise((resolve) => {
    let done = false, retry = false;
    const bbd = est.blocked_bad_duration || [], resend = resendableBlocked(est); // AC13: wrong-length interrupted scenes are never resent
    const count = () => est.renders.length + (retry ? resend.length : 0);
    const okBtnLabel = () => count() || !adopt.length ? `Render ${count()} clip` : "Dùng clip đã tải về";
    const okEnabled = () => count() > 0 || adopt.length > 0; // adopt-only runs spend no credit
    const body2 = h("div", { class: "stack" },
      est.renders.length ? h("table", { class: "list" },
        h("tr", {}, h("th", {}, "Cảnh"), h("th", {}, "Kiểu"), h("th", {}, "Model"), h("th", {}, "Độ dài"), h("th", {}, "Nội dung")),
        est.renders.map((r) => h("tr", {}, h("td", { class: "mono" }, r.id), h("td", {}, MODES.find((m) => m[0] === r.mode)?.[1] || r.mode),
          h("td", { class: "mono" }, r.model), h("td", {}, r.duration ? r.duration + "s" : "mặc định"), h("td", { class: "small" }, r.prompt)))) : null,
      est.new_keyframes.length ? h("p", { class: "help" }, `Sẽ tạo trước ${est.new_keyframes.length} ảnh keyframe chưa có (miễn phí): ${est.new_keyframes.join(", ")}. Muốn duyệt ảnh trước thì bấm Huỷ rồi dùng “Tạo keyframe”.`) : null,
      est.blocked.length ? h("div", { class: "banner warn flush" }, icon("alert"), h("div", { class: "b-body" },
        h("div", {}, `Cảnh ${est.blocked.join(", ")} bị ngắt khi render lần trước, có thể Flow đã trừ credit. Hãy kiểm tra trên Flow trước.`),
        est.blocked.filter((id) => unverified.includes(id)).map((id) =>
          h("div", { class: "small", style: { marginTop: "4px" } }, `Cảnh ${id}: có clip trong thư mục — dùng [Dùng clip này] trên thẻ cảnh thay vì gửi lại`)),
        bbd.map((r) => h("div", { class: "small", style: { marginTop: "4px" } }, blockedBadDurationText(r))),
        h("label", { class: "check", style: { marginTop: "8px" } }, h("input", { type: "checkbox", disabled: !resend.length, onchange: (e) => { retry = e.target.checked; body.confirm_resend = retry ? [...resend] : []; document.getElementById("paid-ok").textContent = okBtnLabel(); document.getElementById("paid-ok").disabled = !okEnabled(); } }),
          h("span", {}, "Tôi đã kiểm tra, gửi lại các cảnh này")))) : null,
      adopt.length ? h("div", { class: "banner info flush" }, icon("check"), h("div", { class: "b-body" },
        adopt.map((id) => h("div", {}, `Cảnh ${id} sẽ dùng clip bạn đã tải về (không tốn credit)`)))) : null,
      h("div", { class: "banner info flush" }, icon("coins"), h("div", { class: "b-body" }, "Mỗi clip sẽ trừ credit trên tài khoản Google Flow của bạn. Có thể bấm Dừng bất cứ lúc nào; cảnh đang render sẽ được làm nốt.")),
    );
    modal({
      title: "Xác nhận render video", body: body2, wide: true, onClose: () => { if (!done) resolve(false); },
      buttons: [{ label: "Huỷ" }, { label: okBtnLabel(), id: "paid-ok", cls: "primary", disabled: !okEnabled(), onclick: (c) => { done = true; resolve(true); c(); } }],
    });
  });
}

function startJob(j) {
  S.job = { ...j, lines: [...j.lines] };
  S.jobSince = j.total;
  S.jobOpen = true;
  renderJobbar();
  refreshZones();
  progUpdate({}, S.job.progress);
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
    const prev = S.job.progress || {};
    S.job.progress = d.progress && typeof d.progress === "object" ? d.progress : undefined; // no field (older server) → old way
    appendLog(fresh);
    updateJobHead();
    const pr = progUpdate(prev, S.job.progress);
    if (progOn()) {
      // Structured progress: a phase change redraws badges/buttons; an item reaching done/failed reloads its media.
      if (pr.phaseChanged) refreshZones();
      const ended = [...pr.done, ...pr.failed];
      if (ended.length && S.cur && S.cur.id === S.job.project) {
        const pid = S.cur.id, chars = pr.done.filter((k) => k.startsWith("char:"));
        refreshCurrent().then(() => { if (chars.length && S.cur && S.cur.id === pid) syncCharImages(chars); });
      }
    } else if (fresh.some((l) => /^\[[^\]]+\]/.test(l))) {
      refreshZones();
      if (fresh.some((l) => /\] OK -> /.test(l)) && S.cur && S.cur.id === S.job.project) refreshCurrent();
    }
    if (d.status === "running") S.pollT = setTimeout(pollJob, 1000);
    else if (wasRunning) onJobDone();
  } catch { S.pollT = setTimeout(pollJob, 3000); }
}

async function onJobDone() {
  document.querySelectorAll(".prog-ov").forEach((e) => e.remove()); // job over: no overlay left, even if no key changed
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
  if (j.kind === "restore" && j.status === "done" && j.result?.id) { await openProject(j.result.id); return; }
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
    if (j.kind === "portraits" && (S.tab === "story" || S.tab === "board")) renderMain();
    if (j.kind === "edit" || j.kind === "make") await loadEditPlan(true);
    if (j.kind === "stitch" || j.kind === "edit" || (j.kind === "make" && j.result?.final)) { S.tab = "final"; renderMain(); }
    else if (j.kind === "thumbnail" && S.tab === "final") renderMain();
  }
  refreshZones();
}

/* A portrait finished mid-job (char:<name> → done): the server already saved characters[i].image. Pull it in without
   renderMain() (no focus loss while typing); the end-of-job sync in onJobDone stays as the fallback. */
async function syncCharImages(keys) {
  if (!S.cur) return;
  const pid = S.cur.id;
  try {
    const d = await api("GET", `/api/projects/${pid}`);
    if (!S.cur || S.cur.id !== pid) return;
    const local = S.cur.storyboard.characters || [], remote = d.storyboard?.characters || [];
    let touched = false;
    for (const k of keys) {
      const name = k.slice("char:".length);
      const c = local.find((x) => String(x.name || "").trim() === name);
      const r = remote.find((x) => String(x?.name || "").trim() === name);
      if (c && r && r.image && c.image !== r.image) { c.image = r.image; touched = true; }
    }
    const a = await api("GET", `/api/projects/${pid}/assets`);
    if (!S.cur || S.cur.id !== pid) return;
    S.assets = a; // fresh ?v=mtime, so "Tạo lại ảnh" with the same file name shows the new picture
    if (touched) changed(); // server saved it; this keeps a pending edit from dropping it
    refreshZones();
  } catch { /* network error: onJobDone syncs at the end */ }
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
  if (/^\s+\$ /.test(l)) return "l-cmd"; // command lines carry the prompt, which may say "Error"
  if (/LỖI|✘|lỗi:|Error/.test(l)) return "l-err";
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
      ["blank", "file", "Trống", "Tự viết từng cảnh"], ["import", "import", "Nhập file", "Mở file storyboard .yaml có sẵn"],
      ["drive", "cloud", "Từ Drive", "Khôi phục dự án đã backup"]];
    const form = { ai: aiForm, template: () => titleForm("Ly cà phê đầu tiên"), blank: () => titleForm(""), import: importForm, drive: driveForm }[mode]();
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
    const speak = langPicker(DIALOGUE_LANGS, cleanLang(localGet("dialogueLang")) || "Vietnamese");
    const subSaved = cleanLang(localGet("subtitleLang"));
    const sub = langPicker(SUBTITLE_LANGS, subSaved && subSaved.toLowerCase() !== "none" ? subSaved : "Vietnamese", { noNone: true });
    setTimeout(() => idea.focus(), 30);
    const go = async (e) => {
      if (!idea.value.trim()) { toast("Hãy nhập ý tưởng", "error"); return; }
      const a = speak.read(), b = sub.read();
      if (!a.ok) { toast("Nhân vật nói: " + a.msg, "error"); speak.input.focus(); return; }
      if (!b.ok) { toast("Ngôn ngữ phụ đề: " + b.msg, "error"); sub.input.focus(); return; }
      const btn = e.currentTarget;
      localSet("dialogueLang", a.value); localSet("subtitleLang", b.value); // remembered even if the plan job fails
      btn.disabled = true;
      try {
        const j = await api("POST", "/api/projects/plan", { idea: idea.value, scenes: +n.value || 6, aspect: aspect.value, language: b.value, dialogue_language: a.value });
        close(); localSet("draftIdea", ""); startJob(j); toast("Claude đang viết storyboard, thường mất 1–3 phút…", "", 6000);
      } catch (err) { toast(err.message, "error"); btn.disabled = false; }
    };
    return h("div", { class: "stack" },
      field("Ý tưởng phim", idea),
      h("div", { class: "grid2" }, field("Số cảnh", n), field("Khung hình", aspect)),
      h("div", { class: "grid2" }, field("Nhân vật nói", speak.el), field("Ngôn ngữ phụ đề", sub.el)),
      h("p", { class: "help" }, "Câu “nhân vật nói …” được thêm vào mọi prompt video gửi Flow để cả phim cùng một thứ tiếng."),
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
  const driveForm = () => {
    const box = h("div", { class: "stack" }, h("p", { class: "row muted" }, h("span", { class: "spinner" }), "Đang đọc danh sách backup trên Drive…"));
    api("GET", "/api/backups").then((r) => {
      if (!r.items.length) { put(box, h("p", { class: "muted" }, `Chưa có bản backup nào ở ${r.remote}.`)); return; }
      put(box, h("p", { class: "help" }, `Các dự án ở ${r.remote}. Clip đã render được tải về, không phải render lại.`),
        h("div", { class: "stack", style: { gap: "6px" } }, r.items.map((it) => h("div", { class: "row" },
          icon("film"), h("span", { class: "mono" }, it.name), h("span", { class: "spacer" }),
          it.local ? h("span", { class: "muted small" }, "Đã có trên máy")
            : h("button", { class: "btn sm", onclick: async (e) => {
              e.target.disabled = true;
              try { startJob(await api("POST", "/api/backups/restore", { name: it.name })); close(); toast("Đang tải dự án từ Drive…"); }
              catch (err) { toast(err.message, "error"); e.target.disabled = false; }
            } }, icon("download"), "Khôi phục")))));
    }).catch((e) => put(box, h("div", { class: "banner warn flush" }, icon("alert"), h("div", { class: "b-body" }, h("div", {}, e.message, " ",
      h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); close(); openSettings(); } }, "Mở Cài đặt"), ".")))));
    return box;
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
  const remote = h("input", { type: "text", value: s.backup_remote || "gdrive:FilmMaker", placeholder: "gdrive:FilmMaker" });
  const autoBackup = h("input", { type: "checkbox", checked: !!s.auto_backup });
  const backupState = h("div", { class: "row small" }, h("span", { class: "spinner" }), h("span", { class: "muted" }, "Đang kiểm tra rclone…"));
  const checkBackup = () => api("GET", "/api/backup").then((b) => {
    const name = (b.remote.split(":")[0] || "gdrive");
    put(backupState,
      h("span", { class: "pill " + (b.available && b.configured ? "ok" : "warn") },
        !b.available ? "Chưa cài rclone" : b.configured ? "Sẵn sàng" : `Chưa có remote “${name}”`),
      h("span", { class: "muted" }, !b.available ? "Mở Terminal, chạy: brew install rclone"
        : b.configured ? (b.version || "rclone") : `Mở Terminal, chạy: rclone config → n → tên ${name} → Google Drive → đăng nhập Google.`));
  }).catch((e) => put(backupState, h("span", { class: "pill bad" }, "Lỗi"), h("span", { class: "muted" }, e.message)));
  checkBackup();
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
      h("section", { class: "settings-sec" }, h("h3", {}, "Backup lên Google Drive"),
        h("div", { class: "stack" },
          backupState,
          h("div", { class: "row", style: { alignItems: "flex-end" } }, h("div", { style: { flex: "1" } }, field("Nơi lưu (rclone remote:thư mục)", remote, "mặc định gdrive:FilmMaker")),
            h("button", { class: "btn sm", onclick: checkBackup }, "Kiểm tra lại")),
          h("label", { class: "check" }, autoBackup, h("span", {}, "Tự backup sau mỗi clip render xong và khi ghép phim",
            h("span", { class: "sub" }, "Chạy ngầm, không làm chậm việc render. Backup lỗi thì chỉ báo, video vẫn an toàn trên máy."))),
          h("p", { class: "help" }, "Chỉ gửi file mới hoặc đã đổi, không bao giờ xoá gì trên Drive. Clip bị render lại thì bản cũ được giữ trong thư mục .versions. Khôi phục: Phim mới → Từ Drive."))),
      h("section", { class: "settings-sec" }, h("h3", {}, "Giao diện"), h("div", { class: "grid2" }, field("Chế độ màu", theme)))),
    buttons: [{ label: "Huỷ" }, { label: "Lưu", cls: "primary", onclick: async (c) => {
      try {
        await api("PUT", "/api/settings", { planner: planner.value, profile: profile.value.trim(), pause_min: +pmin.value, pause_max: +pmax.value, retries: +retries.value,
          flow_ui: flowUi.checked, flow_ui_max_credits: +maxCredits.value || 0, flow_ui_window: flowWin.value,
          backup_remote: remote.value.trim(), auto_backup: autoBackup.checked,
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
