"use strict";
/* Film Maker UI — vanilla JS, no build step. */

const TOKEN = document.querySelector('meta[name="token"]').content;
const S = {
  env: null, settings: null, models: { video: [], image: [] }, projects: [], cur: null, tab: "scenes",
  assets: { images: [], audio: [] }, job: null, jobSince: 0, jobOpen: false, jobOnlyKey: false, pollT: null,
  saveTimer: null, saving: false, pendingSave: false, saveState: "saved", saveError: "", auth: null,
  zones: new Map(), playIdx: 0,
  charSaved: new WeakMap(), // character object → the name scenes[].characters use for it (last save)
  locSaved: new WeakMap(), // location object → the name scenes[].location use for it (last save)
  locDescBase: new WeakMap(), // location object → { desc, n } before the first edit of its description (warning)
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
  scan: '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><path d="M9.5 9v6l5-3z"/>',
  filter: '<path d="M4 5h16l-6 7v6l-4 2v-8z"/>',
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
const SUBTITLE_LANGS = [["Vietnamese", "Tiếng Việt"], ["English", "English"], ["Thai", "ไทย (Tiếng Thái)"],
                        ["none", "Không phụ đề"]];
/* "Không phụ đề": the film is made without subtitles (no tracks, no .srt, nothing burnt in). */
const noSubs = (sb) => normLine(sb?.subtitle_language).toLowerCase() === "none";

/* Every film always has three subtitle tracks. `narration` is the line in the primary track's language. */
const SUB_TRACKS = [["vi", "Tiếng Việt", "Vietnamese"], ["th", "ไทย", "Thai"], ["en", "English", "English"]];
const SUB_ALIASES = { vi: "vi", vie: "vi", vietnamese: "vi", "tiếng việt": "vi", th: "th", tha: "th", thai: "th", "ไทย": "th", en: "en", eng: "en", english: "en" };
const normLine = (v) => String(v || "").split(/\s+/).filter(Boolean).join(" ");
function primarySubCode(sb) {
  const c = SUB_ALIASES[normLine(sb?.subtitle_language).toLowerCase()];
  if (c) return c;
  for (const sc of sb?.scenes || []) {
    const n = normLine(sc.narration);
    const hit = n && sc.subtitles && SUB_TRACKS.find(([k]) => sc.subtitles[k] === n);
    if (hit) return hit[0];
  }
  return "vi";
}
/* The line shown on track `code`: a missing translation falls back to narration (same as the server). */
function subLine(sb, sc, code) {
  const n = normLine(sc.narration);
  if (code === primarySubCode(sb) && n) return n;
  return normLine(sc.subtitles?.[code]) || n;
}
function setSubLine(sb, sc, code, value) {
  const primary = primarySubCode(sb);
  const subs = { ...(sc.subtitles || {}) };
  if (normLine(value)) subs[code] = value; else delete subs[code];
  sc.subtitles = Object.keys(subs).length ? subs : undefined;
  if (code === primary) sc.narration = value || undefined;
}
/* Three inputs (vi / th / en) for one scene. */
function subtitleFields(sb, sc, onChange) {
  if (noSubs(sb)) return h("p", { class: "help" }, "Phim này không có phụ đề. ",
    h("a", { href: "#", onclick: (e) => { e.preventDefault(); delete sb.subtitle_language; onChange(); renderMain(); } }, "Bật phụ đề"));
  const primary = primarySubCode(sb);
  return h("div", { class: "grid3" }, SUB_TRACKS.map(([code, label]) => field(
    `Phụ đề ${label}${code === primary ? " · gốc" : ""}`,
    h("input", { type: "text", lang: code, value: code === primary ? (sc.narration || "") : (sc.subtitles?.[code] || ""),
      placeholder: code === primary ? "Không bắt buộc" : "Để trống: tự dịch khi ghép phim",
      oninput: (e) => { setSubLine(sb, sc, code, e.target.value); onChange(); } }))));
}
/* Basic films with dialogue: the subtitle is the speaker's line (never typed apart, so what is read on screen is
   what the video model is asked to say); only the other tracks' translations are editable. */
function dialogueSubFields(sb, sc, onChange) {
  const sp = sc.video_shot?.speaker, line = normLine(sp?.line);
  if (!line) return h("p", { class: "help" }, h("b", {}, "Không thoại"), " · cảnh này không có ai nói nên không có phụ đề. Muốn có thoại, mở “Người nói” ở trên và nhập câu thoại.");
  const who = normLine(sp.visual_tag) || normLine(sp.position) || "người nói";
  const primary = primarySubCode(sb);
  return h("div", { class: "stack tight" },
    h("p", { class: "help" }, h("b", {}, "Thoại"), ` · ${who}: “${line}”`, h("span", { class: "sub" }, " — cũng là phụ đề; sửa ở ô Câu thoại.")),
    noSubs(sb) ? null : h("div", { class: "grid2" }, SUB_TRACKS.filter(([code]) => code !== primary).map(([code, label]) => field(`Phụ đề ${label}`,
      h("input", { type: "text", lang: code, value: sc.subtitles?.[code] || "", placeholder: "Để trống: tự dịch khi ghép phim",
        oninput: (e) => { setSubLine(sb, sc, code, e.target.value); onChange(); } })))));
}
function subTrack() { const c = localGet("subTrack", "vi"); return SUB_TRACKS.some(([k]) => k === c) ? c : "vi"; }
/* A valid language name with whitespace collapsed, or null (not a string, empty, has a line break, > 40 chars). */
function cleanLang(v) {
  if (typeof v !== "string" || /[\r\n]/.test(v)) return null;
  const s = v.replace(/\s+/g, " ").trim();
  if (/\p{C}/u.test(s)) return null; // control/format chars (zero-width, bidi…): same rule as the backend
  return s && s.length <= 40 ? s : null;
}
/* <select> of presets + "Khác…" (free text input shown only then); unset adds "Chưa đặt (Veo tự đoán)" = null. */
function langPicker(presets, value, { unset } = {}) {
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
  const el = zoneEl(fn);
  el.id = id;
  return el;
}
/* A zone with nothing to show (fn returns null) is an empty hidden div, so it can still be refreshed later. */
function zoneEl(fn) { return fn() || h("div", { hidden: true }); }
function refreshZones() {
  for (const [id, fn] of S.zones) {
    const old = document.getElementById(id);
    if (!old) { S.zones.delete(id); continue; }
    // Do not interrupt a clip the user is watching.
    const v = old.querySelector("video");
    if (v && !v.paused) continue;
    const el = zoneEl(fn);
    el.id = id;
    if (old.outerHTML !== el.outerHTML && !keepsOpenedClip(old, el)) old.replaceWith(el);
  }
}
function refreshZone(id) {
  const fn = S.zones.get(id), old = document.getElementById(id);
  if (!fn || !old) return;
  const el = zoneEl(fn); el.id = id;
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
  document.body.classList.toggle("side-collapsed", !!localGet("sideCollapsed", false));
  S.jobOnlyKey = !!localGet("jobOnlyKey", false);
  window.matchMedia("(max-width: 720px)").addEventListener("change", syncMenuButton);
  syncMenuButton();
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
    if (id.startsWith("analysis/")) { const aid = id.slice(9); if (!S.ana || S.ana.id !== aid) openAnalysis(aid); return; }
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
  const want = decodeURIComponent(location.hash.slice(1)) || localGet("lastProject") || "";
  if (want.startsWith("analysis/")) { await openAnalysis(want.slice(9)); loadModels(null); }
  const pick = S.projects.some((p) => p.id === want) ? want : S.projects[0]?.id;
  if (S.ana) { /* an analysis is open (#analysis/<id>) */ } else if (pick) await openProject(pick); else { renderMain(); loadModels(null); } // openProject loads /api/models itself
  const j = await api("GET", "/api/jobs/current").catch(() => null);
  if (j) {
    S.job = { ...j, lines: [] }; S.jobSince = 0; S.jobOpen = j.status === "running"; renderJobbar();
    refreshZones(); progUpdate({}, S.job.progress); // F5 mid-job: overlays now, not at the next phase change
    pollJob();
  }
  setInterval(tickElapsed, 1000);
}

const NARROW = () => window.matchMedia("(max-width: 720px)").matches;

/* Narrow window: the project list is a drawer over the page. Wide window: it collapses to a rail of covers
   (remembered), so the work area gets the room. */
function toggleSidebar(open) {
  if (NARROW() || (open === false && document.body.classList.contains("side-open"))) {
    document.body.classList.toggle("side-open", open);
  } else {
    const collapsed = open === undefined ? !document.body.classList.contains("side-collapsed") : !open;
    document.body.classList.toggle("side-collapsed", collapsed);
    localSet("sideCollapsed", collapsed);
  }
  syncMenuButton();
}

function syncMenuButton() {
  const b = $("#btn-menu");
  const shown = NARROW() ? document.body.classList.contains("side-open") : !document.body.classList.contains("side-collapsed");
  b.setAttribute("aria-expanded", String(shown));
  const label = shown ? "Thu gọn danh sách dự án" : "Mở rộng danh sách dự án";
  b.setAttribute("aria-label", label);
  b.title = label;
}

async function loadProjects() {
  S.projects = await api("GET", "/api/projects");
  renderSidebar();
}

async function openProject(id) {
  await flushSave();
  // Edits that could not be saved (blocked by an empty/duplicate character name, or a save error) would be lost silently.
  if (S.cur && S.cur.id !== id && S.pendingSave && saveFailed()) {
    const why = S.saveState === "blocked" ? `Có ${/bối cảnh/.test(S.saveError) ? "bối cảnh" : "nhân vật"} tên trống hoặc trùng nên thay đổi gần đây chưa được lưu.` : "Thay đổi gần đây chưa lưu được: " + S.saveError;
    if (!(await confirmModal("Bỏ thay đổi chưa lưu?", why + " Mở dự án khác thì các thay đổi này sẽ mất.", "Bỏ thay đổi và mở", true))) return false;
    clearTimeout(S.saveTimer); S.pendingSave = false;
  }
  loadModels(id); // in the background, not awaited
  try {
    const [d, a] = await Promise.all([api("GET", `/api/projects/${id}`), api("GET", `/api/projects/${id}/assets`)]);
    S.cur = d; S.assets = a; S.playIdx = 0; S.ana = null;
    S.dlChanged = null; // the "N clips keep the old language" warning belongs to this visit only
  } catch (e) { toast(e.message, "error"); return false; }
  rememberCharNames(); rememberLocNames();
  localSet("lastProject", id);
  if (document.body.classList.contains("side-open")) toggleSidebar(false); // close the drawer; never collapse the rail
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
  // Same for locations: scenes[].location must match a location name, so wait while one is empty/duplicate.
  const located = (sb.scenes || []).some((s) => s.location != null && String(s.location).trim());
  if (located && locsLocked(sb)) { clearTimeout(S.saveTimer); setSaveState("blocked", "tên bối cảnh trống/trùng"); return; }
  if (located) renameLocationsInScenes(sb);
  const sent = charNamePairs(sb), sentLocs = locNamePairs(sb);
  S.saving = true; S.pendingSave = false; setSaveState("saving");
  const pid = S.cur.id;
  try {
    const d = await api("PUT", `/api/projects/${pid}`, { storyboard: withoutBlankLocations(sb) });
    if (S.cur && S.cur.id === pid) {
      if (S.cur.storyboard === sb) { rememberCharNames(sent); rememberLocNames(sentLocs); }
      applyMeta(d);
      if (typeof boardAfterSave === "function") { try { boardAfterSave(); } catch (err) { console.error(err); } }
    }
    setSaveState(S.pendingSave ? "pending" : "saved");
  } catch (e) {
    S.pendingSave = true;
    setSaveState("error", e.message);
  } finally { S.saving = false; }
}

/* A location row just added and still empty is not sent: storyboard.yaml never gets `locations: - {}`
   (which the server reports as an error). Returns `sb` itself when nothing is left out. */
function withoutBlankLocations(sb) {
  const locs = sb.locations;
  if (!Array.isArray(locs)) return sb;
  if (!locs.some(blankLocation)) return sb;
  const kept = locs.filter((l) => !blankLocation(l)), out = { ...sb };
  if (kept.length) out.locations = kept; else delete out.locations;
  return out;
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
    title: S.saveState === "blocked" ? (/bối cảnh/.test(S.saveError) ? "Có cảnh đang dùng bối cảnh, nên tên bối cảnh phải khác rỗng và không trùng. Sửa tên để lưu tiếp."
      : "Có cảnh đang chọn nhân vật, nên tên nhân vật phải khác rỗng và không trùng. Sửa tên để lưu tiếp.") : "Tự lưu khi bạn sửa · ⌘S để lưu ngay" },
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

/* ---------- locations (shared with board.js; pure helpers run under node in tests) ---------- */

/* Same key as character names. The server uses casefold(); toLowerCase() differs only for a few letters (ß…),
   the same small gap the character names already have. */
function locKey(n) { return nameKey(n); }
/* A location with nothing typed in it yet (name, description, image, image_prompt all empty). */
function blankLocation(l) { return !l || !["name", "description", "image", "image_prompt"].some((k) => String(l[k] ?? "").trim()); }
/* Indexes of locations whose name is empty or used by another location (blank rows aside). */
function locNameProblems(sb) {
  const locs = (sb && sb.locations) || [], seen = new Map(), bad = new Set();
  locs.forEach((l, i) => {
    if (blankLocation(l)) return; // a row just added: not sent (withoutBlankLocations), so it blocks nothing
    const k = locKey(l && l.name);
    if (!k) { bad.add(i); return; }
    if (seen.has(k)) { bad.add(i); bad.add(seen.get(k)); } else seen.set(k, i);
  });
  return bad;
}
function locsLocked(sb) { return locNameProblems(sb).size > 0; }
/* [location object, name] for every location whose name is valid right now. */
function locNamePairs(sb) {
  const bad = locNameProblems(sb);
  return ((sb && sb.locations) || []).map((l, i) => [l, l.name, i]).filter(([, , i]) => !bad.has(i)).map(([l, n]) => [l, n]);
}
/* Remember the names scenes[].location refer to (last save). Empty/duplicate names are not remembered.
   Without `pairs` (project opened / reloaded / YAML saved) the description baselines of the warning are reset too. */
function rememberLocNames(pairs) {
  S.locSaved = new WeakMap();
  if (!pairs) S.locDescBase = new WeakMap();
  const sb = S.cur && S.cur.storyboard;
  if (!sb) return;
  for (const [l, n] of pairs || locNamePairs(sb)) S.locSaved.set(l, n);
}
/* Replace old location names with the new ones in every scenes[].location (all at once, so swapping works). */
function renameLocationsInScenes(sb) {
  const map = new Map();
  for (const l of sb.locations || []) {
    if (!S.locSaved || !S.locSaved.has(l)) continue; // added since the last save: no scene uses it yet
    const old = S.locSaved.get(l);
    if (!locKey(old) || old === l.name) continue;
    map.set(locKey(old), String(l.name).trim());
  }
  if (!map.size) return;
  for (const sc of sb.scenes || []) {
    if (sc.location == null) continue;
    const k = locKey(sc.location);
    if (map.has(k)) sc.location = map.get(k);
  }
  for (const l of sb.locations || []) if (S.locSaved.has(l) && map.has(locKey(S.locSaved.get(l)))) S.locSaved.set(l, String(l.name).trim());
}
/* Delete location i (no question asked: the caller confirms). Scenes using it lose `location`, unless another
   location keeps the same name. Returns the ids of those scenes. */
function removeLocation(sb, i) {
  const locs = sb.locations || [], l = locs[i];
  if (!l) return [];
  locs.splice(i, 1);
  const k = locKey(l.name), ids = [];
  if (k && !locs.some((x) => locKey(x && x.name) === k)) {
    for (const sc of sb.scenes || []) if (sc.location != null && locKey(sc.location) === k) { delete sc.location; ids.push(sc.id); }
  }
  if (!locs.length) delete sb.locations;
  return ids;
}
/* The location a scene really uses (none for i2v or an unknown name), like Storyboard.scene_location. */
function sceneLoc(sb, sc) {
  if (!sc || sc.mode === "i2v" || sc.location == null || !locKey(sc.location)) return null;
  return ((sb && sb.locations) || []).find((l) => locKey(l && l.name) === locKey(sc.location)) || null;
}
function sceneHasLoc(sb, sc) { return !!sceneLoc(sb, sc); }
/* How many character pictures a scene sends: from the server, or 2 with a location / 3 without. */
function charRefLimit(sc) {
  const n = S.cur?.scene_state?.[sc && sc.id]?.char_ref_limit;
  return Number.isFinite(n) ? n : sceneHasLoc(S.cur && S.cur.storyboard, sc) ? 2 : 3;
}
/* Number of scenes with a clip ("done", not i2v) that an action turns "Đã đổi".
   kind "assign": scene gets location name `to` ("" = none); "edit" | "image" | "delete": the location object
   `location` (or its name) changes; "rename": never. */
function locationImpact(sb, sceneState, { scene, location, to, kind } = {}) {
  const done = (sc) => sc && sc.mode !== "i2v" && sceneState?.[sc.id]?.status === "done";
  if (kind === "assign") {
    if (!done(scene)) return 0;
    const old = sceneLoc(sb, scene), next = sceneLoc(sb, { ...scene, location: to || undefined });
    return locKey(old && old.name) !== locKey(next && next.name) ? 1 : 0;
  }
  if (kind !== "edit" && kind !== "image" && kind !== "delete") return 0;
  const name = typeof location === "string" ? location : location && location.name, k = locKey(name);
  if (!k) return 0;
  if (kind === "delete" && location && typeof location === "object"
    && ((sb && sb.locations) || []).some((x) => x !== location && locKey(x && x.name) === k)) return 0; // a twin keeps the links
  return ((sb && sb.scenes) || []).filter((sc) => done(sc) && sceneHasLoc(sb, sc) && locKey(sc.location) === k).length;
}

/* ---------- top bar ---------- */

/* Credit button: the Flow balance once it was read, minus an estimate for every clip rendered since. */
function renderCreditButton() {
  const b = $("#btn-credits"), c = S.env?.credit_balance;
  const label = b.querySelector(".lbl-text");
  if (!c) { label.textContent = "Credit"; b.title = "Xem số credit còn lại trên Google Flow"; b.classList.remove("has-balance"); return; }
  const est = Math.round(c.estimate), approx = c.spent > 0 || c.unpriced > 0;
  label.textContent = `${approx ? "~" : ""}${est.toLocaleString("vi-VN")} credit`;
  b.classList.add("has-balance");
  const when = c.checked_at ? ago(c.checked_at) : "";
  b.title = `Flow báo ${c.balance.toLocaleString("vi-VN")} credit${when ? " (" + when + ")" : ""}.`
    + (c.clips ? ` Đã trừ ước lượng ${c.spent.toLocaleString("vi-VN")} credit cho ${c.clips} clip render từ lúc đó`
      + (c.unpriced ? ` (${c.unpriced} clip chưa có giá nên chưa trừ)` : "") + "." : "")
    + " Bấm để đọc lại số dư thật.";
}

async function refreshCredits() {
  try { const r = await api("GET", "/api/credits"); if (S.env) { S.env.credit_balance = r.balance; renderCreditButton(); } } catch { /* keep the old figure */ }
}

function renderTopbar() {
  renderCreditButton();
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
    h("button", { class: "btn primary block", onclick: openNewProject, title: "Phim mới", "aria-label": "Phim mới" }, icon("plus"), h("span", { class: "new-label" }, "Phim mới")),
    h("button", { class: "btn block" + (S.ana ? " on" : ""), style: { marginTop: "8px" }, onclick: openAnalyzeVideo, "aria-label": "Phân tích video", title: "Dán link một video trên mạng: Film Maker phân tích cách dựng, màu, âm thanh… rồi làm phim mới cùng phong cách" }, icon("scan"), h("span", { class: "new-label" }, "Phân tích video")),
    h("div", { class: "side-title" }, h("span", { class: "spacer" }, "Dự án"), h("span", {}, S.projects.length || "")),
    S.projects.length ? null : h("p", { class: "muted small", style: { padding: "0 8px" } }, "Chưa có dự án nào."),
    S.projects.map((p) => {
      const pct = p.scenes ? Math.round((p.done / p.scenes) * 100) : 0;
      const active = S.cur && S.cur.id === p.id;
      return h("button", { class: "proj" + (active ? " active" : "") + (running === p.id ? " running" : ""), onclick: () => openProject(p.id), title: p.title, "aria-label": p.title, "aria-current": active ? "page" : null },
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
  if (S.ana) { put(m, renderAnalysis()); return; }
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
    zone("auto-zone", renderAutoSteps),
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
        h("button", { class: "btn", disabled: busy, onclick: () => openAutopilot(S.cur), title: "Chạy tự động mọi bước còn thiếu: nhân vật, keyframe, video, AI dựng phim, thumbnail, kèm agent review" }, icon("sparkle"), "Tạo tự động", PAID()),
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
  const i2vKey = `i2vHidden.${S.cur.id}`, i2vSig = `${S.cur.i2v_error || "x"}|${!!S.env?.flow_ui}|${fb}`;
  const i2vShown = S.cur.i2v_unsupported && localGet(i2vKey) !== i2vSig;
  const i2v = i2vShown ? h("div", { class: "banner dismissible " + (S.env?.flow_ui ? "info" : "warn") }, icon(S.env?.flow_ui ? "info" : "alert"),
    h("button", { class: "btn icon ghost sm banner-close", title: "Ẩn thông báo này", "aria-label": "Ẩn thông báo này",
      onclick: () => { localSet(i2vKey, i2vSig); renderMain(); } }, icon("x")),
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
  const list = h("div", { class: "scenes" }, zone("qc-summary", renderQCSummary), sb.scenes.map((sc, i) => renderScene(sc, i)));
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
        isBasic(sb) ? renderShot(sb, sc, i) : text("prompt", sc.mode === "t2v" ? "Nội dung video" : "Hành động", "Một hành động, ví dụ: Lan lifts the glass and smiles… (góc máy chọn ở mục Chỉ đạo hình ảnh)", 3, "nên viết tiếng Anh"),
        isBasic(sb) ? null : renderCamera(sc),
        isBasic(sb) ? null : renderDirection(sc, i),
        renderSceneLocation(sb, sc, i),
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
          const lim = charRefLimit(sc);
          return h("div", { class: "char-chips" }, h("span", { class: "small muted", title: refsOn ? "Mô tả nhân vật được tự thêm vào prompt và ảnh nhân vật được gửi kèm làm tham chiếu" : "Mô tả nhân vật được tự thêm vào prompt" }, (refsOn ? "Nhân vật · mô tả + ảnh" : "Nhân vật · mô tả") + (picked ? " (chọn trên Bảng):" : ":")),
            found.map((c) => {
              const url = refsOn && c.image && nRefs < lim ? charImageUrl(c) : null;
              if (url) nRefs++;
              return h("span", { class: "char-chip" + (url ? " has-img" : ""), title: url ? `${c.description}\n(gửi kèm ảnh nhân vật)` : c.description },
                url ? h("img", { src: url, alt: "" }) : null, c.name);
            }));
        }),
        h("div", { class: "grid2" },
          field("Độ dài", durationSelect(sc)),
          field("Model video",
            h("select", { value: sc.model || "", onchange: (e) => { sc.model = e.target.value || undefined; changed(); renderMain(); } },
              h("option", { value: "" }, `Theo phim (${sb.model ? modelLabel(sb.model) : "mặc định"})`),
              videoModelOptions(sc.model).map((m) => h("option", { value: m.alias }, m.label))))),
        isBasic(sb) && sb.audio === "dialogue" ? zone(`subs-${i}`, () => dialogueSubFields(sb, sc, changed)) : subtitleFields(sb, sc, changed),
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

/* "Bối cảnh" of a scene: picker (warns when a scene with a clip would turn "Đã đổi"), the per-scene chaining
   switch and the "a newer keyframe exists" note. i2v scenes never use a location. */
function renderSceneLocation(sb, sc, i) {
  const locs = (sb.locations || []).filter((l) => l && String(l.name || "").trim());
  const i2v = sc.mode === "i2v";
  if (!locs.length && !sc.location) return null; // no location in this film: keep the scene card as before
  // i2v ignores the location, but the picker still shows the one it names (locked), not "(không có trong danh sách)".
  const cur = i2v ? (sc.location != null && locKey(sc.location) ? locs.find((l) => locKey(l.name) === locKey(sc.location)) || null : null)
    : sceneLoc(sb, sc);
  const val = cur ? cur.name : sc.location || "";
  const sel = h("select", { value: val, disabled: i2v || null, "aria-label": "Bối cảnh của cảnh " + sc.id,
    onchange: async (e) => {
      const v = e.target.value;
      const n = locationImpact(sb, S.cur.scene_state, { scene: sc, to: v, kind: "assign" });
      if (n > 0 && !(await confirmModal("Đổi bối cảnh của cảnh đã có clip?",
        `Cảnh ${sc.id} đã có clip. Đổi bối cảnh làm cảnh này chuyển “Đã đổi”: keyframe tạo lại miễn phí, nhưng muốn áp dụng vào video thì phải render lại (tốn credit).`,
        "Đổi bối cảnh"))) { e.target.value = val; return; }
      sc.location = v || undefined;
      changed(); renderMain();
    } },
    h("option", { value: "" }, "— Không —"),
    cur || !sc.location ? null : h("option", { value: sc.location }, `${sc.location} (không có trong danh sách)`),
    locs.map((l) => h("option", { value: l.name }, l.name)));
  return h("div", { class: "stack tight" },
    field("Bối cảnh", sel, i2v ? "Ảnh → Video không dùng bối cảnh" : "mô tả + ảnh bối cảnh gửi kèm"),
    !i2v && sc.location ? h("label", { class: "check" },
      h("input", { type: "checkbox", checked: sc.continue_previous !== false,
        onchange: (e) => { sc.continue_previous = e.target.checked ? undefined : false; changed(); } }),
      h("span", {}, "Nối tiếp keyframe cảnh trước", h("span", { class: "sub" }, "Dùng keyframe cảnh liền trước cùng bối cảnh làm ảnh tham chiếu (miễn phí)."))) : null,
    zone(`locnote-${i}`, () => {
      const st = S.cur.scene_state?.[sc.id];
      const show = !!(st && st.continuity_outdated && st.continuity_source);
      return h("div", { class: "banner info flush" + (show ? "" : " hidden"), hidden: !show }, icon("info"), h("div", { class: "b-body" },
        show ? `Có keyframe ${st.continuity_source} mới hơn; có thể tạo lại keyframe này để nối (miễn phí)` : ""));
    }));
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
/* GET /assets lists every image; the location pickers only offer assets/locations/, the others leave them out. */
const LOC_ASSET_DIR = "assets/locations/";
function isLocAsset(path) { return String(path || "").startsWith(LOC_ASSET_DIR); }
function otherImageAssets() { return (S.assets.images || []).filter((a) => !isLocAsset(a.path)); }
function locImageAssets() { return (S.assets.images || []).filter((a) => isLocAsset(a.path)); }

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
  const imgs = otherImageAssets(); // start/end frames: not the empty rooms of assets/locations/
  const isAsset = !val || imgs.some((a) => a.path === val);
  return h("div", { class: "field" }, h("span", { class: "lbl" }, label),
    h("div", { class: "picker" },
      h("div", { class: "pthumb", title: url ? "Bấm để xem lớn" : null, style: url ? { backgroundImage: `url("${url}")`, cursor: "zoom-in" } : null, onclick: (e) => { if (url) { e.preventDefault(); lightbox(url); } } }),
      h("select", { value: val, onchange: (e) => { sc[attr] = e.target.value || undefined; changed(); renderMain(); } },
        h("option", { value: "" }, optional ? "— không dùng —" : "— chọn ảnh —"),
        isAsset ? null : h("option", { value: val }, val),
        imgs.map((a) => h("option", { value: a.path }, a.path.replace("assets/", "")))),
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

/* Flow refused the scene on policy grounds: sending the same prompt again fails the same way. */
const POLICY_RE = /prominent people|harmful content|audio generation failed|violate our polic|Flow xoá video|safety filter|bộ lọc an toàn/i;
function policyBlocked(st) {
  return st.status !== "done" && !!st.error && POLICY_RE.test(String(st.error));
}
const S_REWRITING = new Set();
async function rewritePrompt(sc, restage = false) {
  if (!S.cur || S_REWRITING.has(sc.id)) return;
  const pid = S.cur.id;
  S_REWRITING.add(sc.id); refreshZones();
  try {
    await flushSave();
    const d = await api("POST", `/api/projects/${pid}/scenes/${sc.id}/rewrite-prompt`, { restage });
    if (S.cur && S.cur.id === pid) {
      Object.assign(S.cur, d); // the server rewrote the storyboard: take its copy
      renderMain();
    }
    const r = d.rewrite || {};
    toast(r.changed ? `[${sc.id}] ${r.restaged ? "Đã đổi hẳn cách dựng cảnh (khung hình mới, không nối keyframe cảnh trước)" : "Đã viết lại prompt"} — bấm “${S.cur?.scene_state?.[sc.id]?.status === "interrupted" ? "Gửi lại" : "Thử lại"}” để tạo video`
      : `[${sc.id}] Claude không đổi gì trong prompt`, r.changed ? "ok" : "");
  } catch (e) { toast(e.message, "error"); }
  finally { S_REWRITING.delete(sc.id); refreshZones(); }
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
  if (policyBlocked(st)) {
    const busyRw = S_REWRITING.has(sc.id);
    const n = st.rewrites || 0;
    kids.push(h("div", { class: "scene-msg warn" },
      h("div", {}, n
        ? `Đã viết lại ${n} lần mà Flow vẫn từ chối: sửa chữ thôi chưa đủ, Flow đang chặn cả khung hình / khoảnh khắc. Hãy “Đổi hẳn cảnh quay”: góc máy và bố cục mới, ít người hơn, bỏ chi tiết nổi tiếng của sự kiện thật, không nối keyframe cảnh trước.`
        : "Flow từ chối vì vi phạm chính sách — gửi lại y nguyên sẽ lỗi tiếp. Nhờ Claude viết lại prompt (giữ nguyên ý cảnh, bỏ chi tiết dễ bị chặn) rồi tạo lại video."),
      h("div", { class: "row", style: { marginTop: "6px" } },
        h("button", { class: "btn sm" + (n ? " ghost" : ""), disabled: busy || busyRw, title: "Claude viết lại prompt, ảnh mở đầu và thoại của cảnh này. Không gửi gì lên Flow.",
          onclick: () => rewritePrompt(sc) }, icon(busyRw ? "refresh" : "sparkle"), busyRw ? "Đang viết lại…" : "Viết lại prompt", FREE()),
        h("button", { class: "btn sm" + (n ? "" : " ghost"), disabled: busy || busyRw, title: "Dựng lại cảnh: góc máy, bố cục, số người trong khung đều đổi; tắt nối keyframe cảnh trước. Không gửi gì lên Flow.",
          onclick: () => rewritePrompt(sc, true) }, icon("image"), "Đổi hẳn cảnh quay", FREE()))));
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
  if (done && st.clip_url) kids.push(renderQC(sc, st, busy));
  return h("div", { class: "stack tight" }, kids);
}

/* ---------- AI QC of a rendered clip ---------- */

const QC_LABEL = { PASS: ["ok", "QC đạt"], MINOR: ["warn", "QC: lỗi nhỏ"], FAIL: ["bad", "QC: cần làm lại"] };

async function runQC(only = [], force = false) {
  if (!S.cur) return;
  try {
    await flushSave();
    startJob(await api("POST", `/api/projects/${S.cur.id}/qc`, { only, force }));
  } catch (e) { toast(e.message, "error"); }
}

function renderQC(sc, st, busy) {
  const q = st.qc;
  if (!q) {
    return h("div", { class: "qc-box none" }, h("span", { class: "small muted" }, "Clip này chưa được AI QC."),
      h("button", { class: "btn sm ghost", disabled: busy, onclick: () => runQC([sc.id]) }, icon("sparkle"), "AI QC", FREE()));
  }
  const [cls, label] = QC_LABEL[q.verdict] || ["", q.verdict];
  const usePrompt = q.prompt && q.prompt.trim() !== (sc.prompt || "").trim()
    ? h("button", { class: "btn sm", title: "Thay ô “Hành động” bằng prompt AI gợi ý, rồi bấm Render lại (tốn credit)",
      onclick: () => { sc.prompt = q.prompt; changed(); renderMain(); toast("Đã thay prompt — bấm “Render lại” khi sẵn sàng", "ok"); } }, "Dùng prompt gợi ý")
    : null;
  return h("div", { class: "qc-box " + cls },
    h("div", { class: "row" },
      h("span", { class: "qc-tag " + cls }, label), h("span", { class: "small muted" }, `${q.score}/10`),
      q.match != null ? h("span", { class: "small muted", title: "Khung hình đầu clip so với keyframe (1 = trùng khớp)" }, `· khớp keyframe ${q.match.toFixed(2)}`) : null,
      h("span", { class: "spacer" }),
      h("button", { class: "btn sm ghost", disabled: busy, title: "Xem và nghe lại clip này (miễn phí)", onclick: () => runQC([sc.id], true) }, icon("refresh"), "QC lại")),
    q.issues?.length ? h("ul", { class: "rev-list bad" }, q.issues.map((x) => h("li", {}, x))) : null,
    q.picture ? h("div", { class: "small" }, h("b", {}, "Hình: "), q.picture) : null,
    q.sound ? h("div", { class: "small" }, h("b", {}, "Âm thanh: "), q.sound) : null,
    q.transcript != null ? h("details", { class: "small" }, h("summary", {}, `Nghe được${q.language ? ` (${q.language})` : ""}`),
      h("div", { class: "qc-transcript" }, q.transcript || "(không có lời nói)"))
      : (q.dialogue_checked === false ? h("div", { class: "small muted" }, "Chưa kiểm tra lời thoại (chưa có model nhận dạng giọng nói — xem Cài đặt → AI QC).") : null),
    q.prompt && q.verdict !== "PASS" ? h("details", { class: "small" }, h("summary", {}, "Prompt gợi ý để render lại"),
      h("div", { class: "qc-transcript" }, q.prompt), usePrompt) : null);
}

function renderQCSummary() {
  const sb = S.cur.storyboard;
  const rows = (sb.scenes || []).map((sc) => S.cur.scene_state?.[sc.id]).filter((st) => st && st.status === "done" && st.clip_url);
  if (!rows.length) return h("div", { hidden: true });
  const n = { PASS: 0, MINOR: 0, FAIL: 0 }; let none = 0;
  for (const st of rows) { if (st.qc) n[st.qc.verdict] = (n[st.qc.verdict] || 0) + 1; else none++; }
  const busy = jobRunning();
  const fails = (sb.scenes || []).filter((sc) => S.cur.scene_state?.[sc.id]?.qc?.verdict === "FAIL").map((sc) => sc.id);
  return h("div", { class: "card card-pad qc-summary" },
    h("div", { class: "row" },
      h("b", {}, "AI QC clip"),
      h("span", { class: "qc-tag ok" }, `${n.PASS} đạt`),
      n.MINOR ? h("span", { class: "qc-tag warn" }, `${n.MINOR} lỗi nhỏ`) : null,
      n.FAIL ? h("span", { class: "qc-tag bad", title: fails.join(", ") }, `${n.FAIL} cần làm lại: ${fails.join(", ")}`) : null,
      none ? h("span", { class: "small muted" }, `${none} chưa QC`) : null,
      h("span", { class: "spacer" }),
      none ? h("button", { class: "btn sm", disabled: busy, onclick: () => runQC([]) }, icon("sparkle"), `QC ${none} clip mới`, FREE()) : null,
      h("button", { class: "btn sm ghost", disabled: busy, onclick: () => runQC([], true) }, icon("refresh"), "QC lại tất cả")),
    S.env?.qc && !S.env.qc.model ? h("p", { class: "help" }, "Chưa có model nhận dạng giọng nói nên QC chưa kiểm tra lời thoại — Cài đặt → AI QC → Tải model.") : null);
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
        sb.edit_style != null ? field("Phong cách dựng (từ video mẫu)",
          h("textarea", { rows: 5, class: "mono", value: sb.edit_style || "", placeholder: "Trống: agent dựng phim tự chọn nhịp, chuyển cảnh, âm thanh",
            oninput: (e) => set("edit_style", e.target.value) }), "agent dựng phim làm theo; không gửi Flow") : null,
        isBasic(sb) ? formatSummary(sb) : renderDialogueLang(sb, set),
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
    renderCharacters(sb),
    renderLocations(sb));
}

/* Basic formats: the image-to-video prompt in parts (scene.video_shot), joined on the server with the house I2V
   templates: motion only, people by position + look (never names), one line of at most 12 words for one speaker,
   the others silent, no long negatives. Empty parts are dropped; an empty shot falls back to scene.prompt. */
const SHOT_MAX_WORDS = 12;
const wordCount = (t) => (String(t || "").match(/[\p{L}\p{N}'-]+/gu) || []).length;
function renderShot(sb, sc, i) {
  const shot = () => (sc.video_shot = sc.video_shot && typeof sc.video_shot === "object" ? sc.video_shot : {});
  const tidy = () => {
    const v = sc.video_shot;
    if (!v) return;
    if (v.speaker && !String(v.speaker.line || "").trim() && !String(v.speaker.visual_tag || "").trim()) delete v.speaker;
    if (Array.isArray(v.listeners) && !v.listeners.length) delete v.listeners;
    if (!["camera", "action", "environment", "audio"].some((k) => String(v[k] || "").trim()) && !v.speaker && !v.listeners) delete sc.video_shot;
  };
  const edit = () => { tidy(); changed(); refreshZone(`shotwarn-${i}`); refreshZone(`chars-${i}`); };
  const shotObj = (make) => (make ? shot() : sc.video_shot);
  const input = (obj, key, ph, rows = 1) => h(rows > 1 ? "textarea" : "input", { type: "text", rows, placeholder: ph, value: obj()?.[key] || "",
    oninput: (e) => { const o = obj(true); o[key] = e.target.value; if (key === "action" && obj === shotObj) sc.prompt = e.target.value || sc.prompt; edit(); } });
  const sp = (make) => { const v = make ? shot() : sc.video_shot; if (!v) return undefined; if (make && !v.speaker) v.speaker = {}; return v.speaker; };
  const talk = sb.audio === "dialogue";
  const lineCount = h("span", { class: "hint" });
  const showCount = () => { const n = wordCount(sc.video_shot?.speaker?.line); lineCount.textContent = ` · ${n}/${SHOT_MAX_WORDS} từ`; lineCount.classList.toggle("warn-text", n > SHOT_MAX_WORDS); };
  const lineInput = h("input", { type: "text", placeholder: "Một câu ngắn, không ngoặc kép, không viết tắt kiểu don't", value: sc.video_shot?.speaker?.line || "",
    oninput: (e) => {
      const s = sp(true), old = s.line || "";
      s.line = e.target.value;
      // a dialogue film's subtitle IS the spoken line (the server enforces it); old translations no longer match
      if (normLine(e.target.value) !== normLine(old)) { sc.narration = normLine(e.target.value) || undefined; delete sc.subtitles; }
      showCount(); edit(); refreshZone(`subs-${i}`);
    } });
  showCount();
  const listeners = () => { const v = shot(); v.listeners = Array.isArray(v.listeners) ? v.listeners : []; return v.listeners; };
  return h("div", { class: "shot-box stack tight" },
    h("div", { class: "lbl" }, "Prompt video (chuẩn I2V)", h("span", { class: "hint" }, " · chỉ tả chuyển động; gọi người bằng vị trí + ngoại hình, không dùng tên; viết tiếng Anh")),
    h("div", { class: "grid2" },
      field("Máy quay", input(shotObj, "camera", "Static locked-off medium shot, shallow depth of field")),
      field("Âm thanh", input(shotObj, "audio", "quiet room tone, soft footsteps (không nhạc)"))),
    field("Hành động", input(shotObj, "action", "In the center, the little girl in the yellow dress widens her eyes and steps back", 2),
      talk && String(sc.video_shot?.speaker?.line || "").trim() ? "cảnh có người nói: video dùng Cử chỉ khi nói + Người nghe, ô này chỉ dự phòng" : "1–2 nhịp, thì hiện tại"),
    field("Chuyển động phụ & ánh sáng", input(shotObj, "environment", "the curtains sway softly under warm daylight matching the starting frame")),
    talk ? h("details", { class: "shot-talk", open: !!sc.video_shot?.speaker || null },
      h("summary", {}, "Người nói (tuỳ chọn) — một người, một câu ≤ 12 từ"),
      h("div", { class: "stack tight" },
        h("div", { class: "grid2" },
          field("Vị trí trên khung", input(sp, "position", "frame left / frame right")),
          field("Đặc điểm nhận dạng", input(sp, "visual_tag", "the little girl in the pink dress"))),
        h("label", { class: "field" }, h("span", { class: "lbl" }, "Câu thoại", lineCount), lineInput),
        h("div", { class: "grid2" },
          field("Giọng", input(sp, "voice_style", "excited, clear")),
          field("Cử chỉ khi nói", input(sp, "action", "leans forward slightly"))),
        h("div", { class: "grid2" },
          field("Góc đầu", input(sp, "head_angle", "three-quarter profile / side profile")),
          field("Hướng nhìn", input(sp, "gaze", "frame right / off-screen left / tên người nghe"), "trống: tự nhìn về phía người nghe")),
        h("p", { class: "help" }, "Chống lỗi nhìn thẳng ống kính khi nói: máy khoá cứng, hướng nhìn theo tọa độ khung hình, tự khép miệng sau câu thoại. Không dùng camera / lens / viewer / his left."),
        h("div", { class: "lbl" }, "Người nghe", h("span", { class: "hint" }, " · tự thêm hướng mặt về người nói và “remaining completely silent with lips closed”")),
        (sc.video_shot?.listeners || []).map((l, j) => h("div", { class: "row shot-listener" },
          h("input", { type: "text", placeholder: "frame left", value: l.position || "", "aria-label": "Vị trí người nghe", oninput: (e) => { l.position = e.target.value; edit(); } }),
          h("input", { type: "text", placeholder: "the woman in the beige sweater", value: l.visual_tag || "", "aria-label": "Đặc điểm người nghe", oninput: (e) => { l.visual_tag = e.target.value; edit(); } }),
          h("input", { type: "text", placeholder: "listening with a gentle closed-mouth smile", value: l.silent_action || "", "aria-label": "Biểu cảm người nghe", oninput: (e) => { l.silent_action = e.target.value; edit(); } }),
          h("button", { class: "btn ghost icon sm danger", title: "Xoá người nghe", "aria-label": "Xoá người nghe", onclick: () => { listeners().splice(j, 1); tidy(); changed(); renderMain(); } }, icon("trash")))),
        h("div", {}, h("button", { class: "btn sm ghost", onclick: () => { listeners().push({ position: "", visual_tag: "" }); renderMain(); } }, icon("plus"), "Thêm người nghe")))) : null,
    zone(`shotwarn-${i}`, () => {
      const w = S.cur.scene_state?.[sc.id]?.dialogue_warnings || [];
      return w.length ? h("ul", { class: "small warn-text talk-warnings" }, w.map((m) => h("li", {}, m))) : h("div");
    }));
}

/* Film formats (Phim mới). Every format but the short drama uses the basic text -> image -> video flow:
   no content direction, camera or dialogue rows in the editor (locations are kept). */
function isBasic(sb) {
  const F = S.env?.formats;
  return !!(sb && sb.format && F && sb.format !== F.drama && F.formats.some((f) => f.key === sb.format));
}
function formatSummary(sb) {
  const F = S.env.formats, lab = (list, k) => (list.find((x) => x.key === k) || {}).label;
  const parts = [["Định dạng", lab(F.formats, sb.format)], ["Âm thanh / lời", lab(F.audio, sb.audio)], ["Hình ảnh", lab(F.visuals, sb.visual_style)],
    ["Thể loại", (sb.genres || []).map((g) => lab(F.genres, g)).filter(Boolean).join(", ")],
    ["Ngôn ngữ", normLine(sb.dialogue_language).toLowerCase() !== "none" && sb.dialogue_language ? sb.dialogue_language : noSubs(sb) ? "" : sb.subtitle_language]];
  return h("div", { class: "fmt-summary" }, parts.filter(([, v]) => v).map(([k, v]) => h("span", { class: "chip on static" }, h("span", { class: "muted" }, k + ": "), v)),
    h("p", { class: "help" }, "Luồng I2V: chữ → ảnh → video, bối cảnh giữ nguyên giữa các cảnh. Âm thanh / lời theo chuẩn prompt I2V."));
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
    startJob(await api("POST", `/api/projects/${S.cur.id}/characters/portraits`, { names: names.map((n) => String(n ?? "").trim()), force }));
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
      h("p", { class: "help" }, "Mô tả ngoại hình một lần và tạo hoặc tải lên một ảnh. Khi tên nhân vật xuất hiện trong prompt của cảnh, mô tả được tự thêm vào và ảnh được gửi kèm làm tham chiếu (tối đa 3 nhân vật mỗi cảnh; 2 nếu cảnh có bối cảnh) để khuôn mặt giống nhau ở mọi cảnh.")),
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
  const imgs = otherImageAssets(); // portraits and other images, not the empty rooms of assets/locations/
  const isAsset = !c.image || imgs.some((a) => a.path === c.image);
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
          imgs.map((a) => h("option", { value: a.path }, a.path.replace("assets/", "")))),
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
/* ---------- locations card ---------- */

function locImageUrl(l) {
  if (!l || !l.image) return null;
  const i = (S.cur.storyboard.locations || []).indexOf(l);
  const ls = S.cur.locations_state?.[i];
  return assetUrl(l.image) || (ls && ls.name === l.name ? ls.image_url : null);
}
function locCanMake(l) { return !!(l && String(l.name || "").trim() && (String(l.description || "").trim() || String(l.image_prompt || "").trim())); }
/* Ask before an action that turns scenes with a clip "Đã đổi". Resolves true when there is nothing to ask. */
async function confirmLocImpact(n, what) {
  if (n <= 0) return true;
  return confirmModal("Cảnh đã có clip sẽ chuyển “Đã đổi”?",
    `${what} làm ${n} cảnh đã có clip dùng bối cảnh này chuyển “Đã đổi”. Keyframe tạo lại miễn phí; video phải render lại mới áp dụng (tốn credit).`, "Tiếp tục");
}

async function makeLocationImages(names, force = false) {
  await flushSave();
  if (saveFailed()) { toast("Chưa lưu được storyboard: " + S.saveError, "error"); return; }
  if (S.cur.errors?.length) { toast("Storyboard còn lỗi: " + prettyError(S.cur.errors[0]), "error"); return; }
  if (!S.env.gflow) { toast("Chưa cài gflow-cli", "error"); return; }
  try {
    startJob(await api("POST", `/api/projects/${S.cur.id}/locations/images`, { names: names.map((n) => String(n ?? "").trim()), force }));
  } catch (e) { toast(e.message, "error"); }
}

function renderLocations(sb) {
  const locs = sb.locations || [];
  const missingNow = () => (sb.locations || []).filter((l) => locCanMake(l) && !locImageUrl(l));
  // A blank row is not saved yet (save() leaves empty locations out), so "Thêm bối cảnh" alone writes nothing.
  const add = () => { (sb.locations = sb.locations || []).push({ name: "", description: "" }); renderMain(); };
  const makeMissing = async () => {
    const missing = missingNow();
    const n = missing.reduce((a, l) => a + locationImpact(sb, S.cur.scene_state, { location: l, kind: "image" }), 0);
    if (!(await confirmLocImpact(n, `Tạo ảnh cho ${missing.length} bối cảnh`))) return;
    makeLocationImages([]);
  };
  return h("div", { class: "card card-pad" },
    h("div", { class: "card-head" }, h("h3", {}, "Bối cảnh"), h("span", { class: "spacer" }),
      // A zone, so typing a name + description shows the button at once (refreshLocMake).
      zone("locmake-all", () => {
        const missing = missingNow();
        return missing.length ? h("button", { class: "btn sm", disabled: jobRunning(), title: "Ảnh trên Flow miễn phí", onclick: makeMissing },
          icon("image"), `Tạo ảnh cho ${missing.length} bối cảnh`, FREE()) : h("span", { hidden: true });
      }),
      h("button", { class: "btn sm", onclick: add }, icon("plus"), "Thêm bối cảnh"),
      h("p", { class: "help" }, "Mô tả mỗi nơi quay một lần (bố cục, đồ đạc, màu, nguồn sáng; nên viết tiếng Anh) và tạo hoặc tải lên một ảnh. Cảnh chọn bối cảnh sẽ có mô tả này trong prompt và ảnh bối cảnh được gửi kèm, để căn phòng giống nhau ở mọi cảnh.")),
    h("label", { class: "check", style: { marginBottom: "16px" } },
      h("input", { type: "checkbox", checked: sb.continuity !== false,
        onchange: (e) => { sb.continuity = e.target.checked ? undefined : false; changed(); } }),
      h("span", {}, "Nối keyframe cảnh liền trước cùng bối cảnh (miễn phí)",
        h("span", { class: "sub" }, "Cảnh sau lấy keyframe cảnh trước làm ảnh tham chiếu, nên ánh sáng và chỗ đứng của nhân vật liền mạch."))),
    locs.length ? locs.map((l, i) => renderLocation(sb, l, i))
      : h("div", { class: "empty-inline" }, icon("image"), "Chưa có bối cảnh.", h("button", { class: "btn sm", onclick: add }, icon("plus"), "Thêm bối cảnh")));
}

function renderLocation(sb, l, i) {
  const ls = S.cur.locations_state?.[i];
  const portrait = (sb.aspect || "16:9") === "9:16";
  const impact = (kind) => locationImpact(sb, S.cur.scene_state, { location: l, kind });
  const fileInput = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", class: "hidden", onchange: async (e) => {
    const f = e.target.files[0]; if (!f) return;
    e.target.value = "";
    const fd = new FormData(); fd.append("file", f); fd.append("kind", "location");
    try {
      const r = await api("POST", `/api/projects/${S.cur.id}/upload`, fd, true);
      S.assets = await api("GET", `/api/projects/${S.cur.id}/assets`);
      // The file is in assets/locations either way; only using it changes scenes.
      if (!(await confirmLocImpact(impact("image"), "Dùng ảnh vừa tải lên"))) { renderMain(); return; }
      l.image = r.path; changed(); renderMain();
    } catch (err) { toast(err.message, "error"); }
  } });
  const imgs = locImageAssets(); // only assets/locations/
  const isAsset = !l.image || imgs.some((a) => a.path === l.image);
  const badName = locNameProblems(sb).has(i);
  const locImg = () => {
    const u = locImageUrl(l), st = S.cur.locations_state?.[i];
    const div = h("div", { class: "char-img loc-img" + (portrait ? " portrait" : ""), style: u ? { backgroundImage: `url("${u}")` } : { cursor: "default" },
      title: u ? "Bấm để xem lớn" : "", onclick: () => { if (u) lightbox(u); } },
    u ? null : h("span", {}, l.image && st?.image_missing ? "Không thấy file ảnh" : "Chưa có ảnh"));
    return progAttach(div, [progLocKey(l)]);
  };
  return h("div", { class: "char-row loc-row" },
    zone(`locimg-${i}`, locImg),
    h("div", { class: "stack tight", style: { minWidth: 0 } },
      h("input", { type: "text", value: l.name || "", placeholder: "Tên, ví dụ: Phòng khách nhà cũ", "aria-label": "Tên bối cảnh", "data-loc": i,
        "aria-invalid": badName ? "true" : null, title: badName ? "Tên trống hoặc trùng với bối cảnh khác" : null,
        oninput: (e) => { l.name = e.target.value; markLocNames(); changed(); refreshLocMake(i); } }),
      h("textarea", { rows: 3, value: l.description || "", "aria-label": "Mô tả bối cảnh",
        placeholder: "long dark teak table, six chairs, brass ceiling lamp, window on the left, warm evening light…",
        oninput: (e) => {
          // Baseline before the first edit: the count of scenes with a clip that this edit turns "Đã đổi" (after the
          // autosave they are no longer "done", so the number is taken now and kept).
          if (!S.locDescBase.has(l)) S.locDescBase.set(l, { desc: l.description || "", n: impact("edit") });
          l.description = e.target.value; changed(); refreshZone(`locwarn-${i}`); refreshLocMake(i);
        } }),
      zone(`locwarn-${i}`, () => {
        const b = S.locDescBase.get(l), n = b && (l.description || "") !== b.desc ? b.n : 0;
        return h("div", { class: "banner warn flush" + (n ? "" : " hidden"), hidden: !n }, icon("alert"), h("div", { class: "b-body" },
          n ? `${n} cảnh đã có clip dùng bối cảnh này sẽ chuyển Đã đổi (render lại tốn credit)` : ""));
      }),
      h("div", { class: "row" },
        zone(`locmake-${i}`, () => {
          const canMake = locCanMake(l), url = locImageUrl(l);
          return h("button", { class: "btn sm", disabled: jobRunning() || !canMake, title: canMake ? "Ảnh trên Flow miễn phí" : "Cần nhập tên và mô tả trước",
            onclick: async () => {
              if (!(await confirmLocImpact(impact("image"), url ? "Tạo lại ảnh bối cảnh" : "Tạo ảnh bối cảnh"))) return;
              makeLocationImages([l.name], true);
            } }, icon(url ? "refresh" : "image"), url ? "Tạo lại ảnh" : "Tạo ảnh", FREE());
        }),
        h("button", { class: "btn sm", onclick: () => fileInput.click() }, icon("upload"), "Tải lên"),
        h("select", { class: "sm", value: l.image || "", title: "Chọn ảnh có sẵn trong dự án", "aria-label": "Ảnh bối cảnh",
          onchange: async (e) => {
            const v = e.target.value || undefined;
            if (v === l.image) return;
            if (!(await confirmLocImpact(impact("image"), v ? "Đổi ảnh bối cảnh" : "Bỏ ảnh bối cảnh"))) { e.target.value = l.image || ""; return; }
            l.image = v; changed(); renderMain();
          } },
          h("option", { value: "" }, "— không dùng ảnh —"),
          isAsset ? null : h("option", { value: l.image }, l.image),
          imgs.map((a) => h("option", { value: a.path }, a.path.replace("assets/locations/", "")))),
        fileInput),
      h("details", {}, h("summary", {}, "Prompt tạo ảnh bối cảnh"),
        h("textarea", { rows: 3, value: l.image_prompt || "",
          placeholder: ls?.image_prompt || "Để trống: tự tạo từ mô tả (cảnh trống, không người, góc rộng ngang tầm mắt)",
          oninput: (e) => { l.image_prompt = e.target.value || undefined; changed(); refreshLocMake(i); } }),
        h("p", { class: "help" }, "Ảnh bối cảnh nên là cảnh trống, không có người. Đổi ảnh hoặc mô tả làm các cảnh dùng bối cảnh này cần tạo lại."))),
    h("button", { class: "btn ghost icon sm danger", title: "Xoá bối cảnh", "aria-label": "Xoá bối cảnh " + (l.name || ""), onclick: async () => {
      const k = locKey(l.name);
      const twin = (sb.locations || []).some((x) => x !== l && k && locKey(x && x.name) === k);
      const using = twin || !k ? 0 : (sb.scenes || []).filter((sc) => sceneHasLoc(sb, sc) && locKey(sc.location) === k).length;
      const n = locationImpact(sb, S.cur.scene_state, { location: l, kind: "delete" });
      const text = `Xoá “${l.name || "bối cảnh chưa đặt tên"}” khỏi storyboard. `
        + (using ? `Đang dùng ở ${using} cảnh; các cảnh đó sẽ bỏ bối cảnh. ` : "")
        + (n ? `${n} cảnh đã có clip sẽ chuyển “Đã đổi” (render lại tốn credit). ` : "")
        + "File ảnh vẫn nằm trong thư mục assets.";
      if (!(await confirmModal("Xoá bối cảnh?", text, "Xoá", true))) return;
      const at = (sb.locations || []).indexOf(l); // the list may have changed while the dialog was open
      if (at < 0) return;
      removeLocation(sb, at);
      changed(); renderMain();
    } }, icon("trash")));
}
/* "Tạo ảnh" of row i and "Tạo ảnh cho N bối cảnh" follow the name/description being typed. */
function refreshLocMake(i) { refreshZone(`locmake-${i}`); refreshZone("locmake-all"); }
/* Mark empty/duplicate location names while typing, without re-rendering the inputs. */
function markLocNames() {
  const bad = locNameProblems(S.cur.storyboard);
  for (const el of document.querySelectorAll(".loc-row input[data-loc]")) {
    const on = bad.has(+el.dataset.loc);
    if (on) { el.setAttribute("aria-invalid", "true"); el.title = "Tên trống hoặc trùng với bối cảnh khác"; }
    else { el.removeAttribute("aria-invalid"); el.removeAttribute("title"); }
  }
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

/* VI / TH / EN: which subtitle track the final tab shows (remembered per browser). */
function subTrackSwitch() {
  const cur = subTrack();
  return h("div", { class: "seg sm", role: "group", "aria-label": "Ngôn ngữ phụ đề" }, SUB_TRACKS.map(([code, label]) =>
    h("button", { class: cur === code ? "on" : "", title: `Phụ đề ${label}`, "aria-pressed": String(cur === code),
      onclick: () => { localSet("subTrack", code); refreshZone("final-zone"); refreshZone("playlist-zone"); refreshZone("narr-zone"); } }, code.toUpperCase())));
}
function parseSrt(text) {
  const t = (x) => { const [h, m, rest] = x.split(":"); const [sec, ms] = rest.split(","); return +h * 3600 + +m * 60 + +sec + +ms / 1000; };
  return text.replace(/\r/g, "").split(/\n\s*\n/).map((block) => {
    const lines = block.trim().split("\n");
    const i = lines.findIndex((l) => l.includes("-->"));
    if (i < 0) return null;
    const [a, b] = lines[i].split("-->").map((x) => x.trim());
    return { start: t(a), end: t(b), text: lines.slice(i + 1).join("\n") };
  }).filter(Boolean);
}
/* The final film with the three .srt files as text tracks, the chosen one showing (not in the DOM, so zone refreshes stay cheap). */
function finalVideo() {
  const video = h("video", { src: S.cur.final_url, controls: true, preload: "metadata", playsinline: true });
  const want = subTrack();
  // final.mp4 also carries the three subtitle tracks. WebKit (the desktop app) exposes them and may show one by
  // itself (macOS "Automatic" captions pick the system language), whatever VI / TH / EN says: keep only ours.
  const ours = new Set();
  const muteEmbedded = () => {
    for (const t of video.textTracks) if (!ours.has(t) && t.mode !== "disabled") t.mode = "disabled";
  };
  video.textTracks.addEventListener("addtrack", muteEmbedded);
  video.textTracks.addEventListener("change", muteEmbedded);
  video.addEventListener("loadedmetadata", muteEmbedded);
  SUB_TRACKS.forEach(async ([code, label]) => {
    const url = S.cur.srt_urls?.[code];
    if (!url) return;
    try {
      const r = await fetch(url);
      if (!r.ok) return;
      const track = video.addTextTrack("subtitles", label, code);
      ours.add(track);
      for (const c of parseSrt(await r.text())) track.addCue(new VTTCue(c.start, c.end, c.text));
      track.mode = code === want ? "showing" : "hidden";
    } catch { /* subtitles are optional in the preview */ }
  });
  return video;
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
          h("div", { class: "card-head" }, h("h3", {}, "Phim hoàn chỉnh"), h("span", { class: "spacer" }),
            S.cur.final_size ? h("span", { class: "small muted" }, fmtBytes(S.cur.final_size)) : null, subTrackSwitch()),
          h("div", { class: "player" + (portrait ? " portrait" : "") }, finalVideo()),
          h("div", { class: "row", style: { marginTop: "12px" } },
            h("a", { class: "btn primary sm", href: S.cur.final_url.split("?")[0], download: `${S.cur.id}.mp4` }, icon("download"), "Tải MP4"),
            SUB_TRACKS.map(([code, label]) => S.cur.srt_urls?.[code]
              ? h("a", { class: "btn sm", href: S.cur.srt_urls[code].split("?")[0], download: `${S.cur.id}.${code}.srt`, title: `Phụ đề ${label}` }, icon("download"), `.srt ${code.toUpperCase()}`)
              : null),
            !SUB_TRACKS.some(([c]) => S.cur.srt_urls?.[c]) && S.cur.srt_url ? h("a", { class: "btn sm", href: S.cur.srt_url.split("?")[0], download: `${S.cur.id}.srt` }, icon("download"), "Phụ đề .srt") : null,
            h("button", { class: "btn sm ghost", onclick: () => api("POST", `/api/projects/${S.cur.id}/reveal`, { path: "output/storyboard/final.mp4" }).catch((e) => toast(e.message, "error")) }, icon("folder"), "Mở trong Finder")));
      }),
      S.cur.final_url ? renderAsk() : null,
      S.cur.final_url ? renderSocial() : null,
      S.cur.final_url ? renderPublish() : null,
      zone("playlist-zone", renderPlaylist)),
    h("div", { class: "stack" },
      zone("auto-review-zone", renderAutoReview),
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
          noSubs(S.cur.storyboard) ? null : h("label", { class: "check" }, h("input", { type: "checkbox", checked: p.burn_subtitles, onchange: (e) => saveFinalPrefs({ ...finalPrefs(), burn_subtitles: e.target.checked }) }),
            h("span", {}, "In phụ đề lên hình", h("span", { class: "sub" }, "Phim luôn có 3 track phụ đề Việt / Thái / Anh (bật/tắt trong trình phát) và 3 file .srt. Bản dịch còn thiếu được Claude tự dịch khi ghép. Khi dựng bằng CapCut, bật mục này để phụ đề thành track chữ sửa được trong CapCut."))),
          p.burn_subtitles && !noSubs(S.cur.storyboard) ? h("label", { class: "field" }, h("span", { class: "lbl" }, "Track in lên hình"),
            h("select", { value: p.burn_language || primarySubCode(S.cur.storyboard), onchange: (e) => saveFinalPrefs({ ...finalPrefs(), burn_language: e.target.value }) },
              SUB_TRACKS.map(([code, label]) => h("option", { value: code }, label)))) : null,
          h("label", { class: "check" }, h("input", { type: "checkbox", checked: p.allow_missing, onchange: (e) => saveFinalPrefs({ ...finalPrefs(), allow_missing: e.target.checked }) }),
            h("span", {}, "Ghép cả khi còn thiếu cảnh", h("span", { class: "sub" }, "Bỏ qua các cảnh chưa render để xem thử bản nháp."))),
          zone("final-actions", () => {
            const n = readyClips().length, busy = jobRunning();
            return h("div", { class: "stack tight" },
              h("div", { class: "divider" }),
              h("div", { class: "row" },
                S.env.capcut
                  ? h("button", { class: "btn primary", disabled: busy || !n, onclick: () => runAction("capcut"),
                      title: "Agent dựng phim như một editor: xem hình từng cảnh, chọn chỗ cắt, chuyển cảnh, màu phim, hiệu ứng, chuyển động máy trong kho CapCut; tự tìm nhạc nền và tiếng động trên mạng; hạ nhạc dưới thoại. Kết quả là dự án CapCut để xem, chỉnh và xuất phim." }, icon("sparkle"), "AI dựng phim (CapCut)", FREE())
                  : h("button", { class: "btn primary", disabled: busy || !n, onclick: () => runAction("edit"), title: "Agent dựng phim: cắt phần thừa, chuyển cảnh, cân âm lượng, nhạc nền → phim liền mạch. Cài CapCut bản máy tính (mở nó một lần) để agent dựng đầy đủ màu phim, hiệu ứng và âm thanh." }, icon("sparkle"), "AI dựng phim", FREE()),
                S.env.capcut ? h("button", { class: "btn", disabled: busy || !n, onclick: () => runAction("edit"), title: "Dựng nhanh ra final.mp4 bằng ffmpeg: cắt, chuyển cảnh cơ bản, cân âm, nhạc nền. Không có màu phim, hiệu ứng và tiếng động." }, icon("film"), "Dựng nhanh (ffmpeg)", FREE()) : null,
                h("button", { class: "btn", disabled: busy || !n, onclick: () => runAction("stitch"), title: "Nối thẳng các clip, không chuyển cảnh" }, icon("stitch"), "Ghép thẳng"),
                h("button", { class: "btn", disabled: busy, onclick: () => runAction("make"), title: "Render các cảnh còn thiếu rồi dựng phim" }, "Render nốt + dựng", PAID())),
              h("div", { class: "help" }, `${n}/${total} cảnh đã có clip.`));
          }))),
      renderEditPlan(),
      renderThumbnail(),
      zone("narr-zone", () => {
        const sb = S.cur.storyboard, code = subTrack(), primary = primarySubCode(sb);
        const rows = (sb.scenes || []).filter((sc) => normLine(sc.narration));
        return h("div", { class: "card card-pad" }, h("div", { class: "card-head" }, h("h3", {}, "Phụ đề"), h("span", { class: "spacer" }), subTrackSwitch()),
          rows.length
            ? h("ol", { class: "narr-list" }, rows.map((sc) => {
              const own = code === primary || normLine(sc.subtitles?.[code]);
              return h("li", { lang: code }, h("span", { class: "mono muted" }, sc.id + " "), subLine(sb, sc, code),
                own ? null : h("span", { class: "small muted" }, " (chưa dịch — tự dịch khi ghép)"));
            }))
            : h("p", { class: "help" }, noSubs(sb) ? "Phim này không có phụ đề." : "Chưa có. Thêm phụ đề ở từng cảnh."));
      })));
}

/* ---------- ask AI about the finished film ---------- */

const ASK_STARTERS = ["Viết caption + hashtag để đăng TikTok/Shorts", "Cảnh nào nên làm lại, prompt mới ra sao?",
  "Gợi ý ý tưởng cho tập tiếp theo", "Tóm tắt phim và chi phí đã dùng"];

/* Tiny, safe Markdown: text is escaped first, then **bold**, `code`, headings, lists and paragraphs. */
function mdToHtml(src) {
  const inline = (t) => esc(t).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<em>$2</em>");
  const out = []; let list = null, code = null;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const line of String(src || "").split("\n")) {
    if (line.trim().startsWith("```")) {
      if (code) { out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`); code = null; } else { close(); code = []; }
      continue;
    }
    if (code) { code.push(line); continue; }
    let m;
    if ((m = line.match(/^\s*>\s?(.*)$/))) {
      if (list !== "blockquote") { close(); out.push("<blockquote>"); list = "blockquote"; }
      if (m[1].trim()) out.push(`<p>${inline(m[1])}</p>`);
    } else if ((m = line.match(/^\s*[-*•]\s+(.*)$/)) || (m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      const kind = /^\s*\d/.test(line) ? "ol" : "ul";
      if (list !== kind) { close(); out.push(`<${kind}>`); list = kind; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^#{1,4}\s+(.*)$/))) { close(); out.push(`<h4>${inline(m[1])}</h4>`); }
    else if (!line.trim()) close();
    else { close(); out.push(`<p>${inline(line)}</p>`); }
  }
  if (code) out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
  close();
  return out.join("");
}

async function loadAsk(force = false) {
  if (!S.cur) return;
  const pid = S.cur.id;
  if (!force && S.ask && S.ask.pid === pid && S.ask.data) return;
  try {
    const data = await api("GET", `/api/projects/${pid}/ask`);
    if (S.cur?.id !== pid) return;
    S.ask = { ...(S.ask?.pid === pid ? S.ask : { pid, draft: "" }), data, error: null };
  } catch (e) { S.ask = { pid, draft: "", data: null, error: e.message }; }
  refreshZone("ask-zone");
}

async function sendAsk(question) {
  const A = S.ask, pid = S.cur?.id;
  question = (question || "").trim();
  if (!A || !pid || A.busy || !question) return;
  A.busy = true; A.pending = question; A.draft = "";
  const box = document.getElementById("ask-input"); if (box) box.value = "";
  refreshZone("ask-zone");
  try {
    await flushSave();
    const data = await api("POST", `/api/projects/${pid}/ask`, { question, pictures: localGet("askPictures", true) });
    if (S.ask?.pid === pid) A.data = data;
  } catch (e) {
    toast(e.message, "error");
    if (S.ask?.pid === pid && !A.draft) { A.draft = question; const b = document.getElementById("ask-input"); if (b) b.value = question; }
  } finally {
    A.busy = false; A.pending = null;
    refreshZone("ask-zone");
    const list = document.querySelector("#ask-zone .ask-msgs"); if (list) list.scrollTop = list.scrollHeight;
  }
}

async function clearAsk() {
  const pid = S.cur?.id;
  if (!pid || !S.ask || S.ask.busy) return;
  if (!confirm("Xoá toàn bộ cuộc trò chuyện về phim này?")) return;
  try { S.ask.data = await api("DELETE", `/api/projects/${pid}/ask`); } catch (e) { toast(e.message, "error"); }
  refreshZone("ask-zone");
}

function renderAsk() {
  if (!S.ask || S.ask.pid !== S.cur.id || (!S.ask.data && !S.ask.error)) setTimeout(() => loadAsk(), 0);
  const input = h("textarea", { id: "ask-input", rows: 2, placeholder: "Hỏi bất cứ điều gì về phim này… (Enter để gửi, Shift+Enter xuống dòng)",
    "aria-label": "Câu hỏi cho AI", value: S.ask?.pid === S.cur.id ? S.ask.draft || "" : "",
    oninput: (e) => { if (S.ask) S.ask.draft = e.target.value; },
    onkeydown: (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendAsk(e.target.value); } } });
  const pics = h("input", { type: "checkbox", checked: localGet("askPictures", true), onchange: (e) => localSet("askPictures", e.target.checked) });
  return h("div", { class: "card card-pad ask-card" },
    h("div", { class: "card-head" }, h("h3", {}, icon("sparkle"), " Hỏi AI về phim"), h("span", { class: "spacer" }),
      h("span", { class: "small muted" }, "Không tốn credit Flow")),
    zone("ask-zone", () => {
      const A = S.ask?.pid === S.cur?.id ? S.ask : null;
      if (!A || (!A.data && !A.error)) return h("div", { class: "help" }, "Đang tải…");
      if (A.error) return h("div", { class: "help" }, A.error);
      const msgs = A.data.messages || [], ctx = A.data.context || [];
      const last = msgs[msgs.length - 1];
      const chips = A.busy ? [] : msgs.length ? (last?.role === "assistant" ? last.follow_ups || [] : []) : ASK_STARTERS;
      const kb = Math.round(ctx.reduce((n, c) => n + c.chars, 0) / 1000);
      return h("div", { class: "stack tight" },
        h("details", { class: "ask-ctx" }, h("summary", { class: "small muted" }, `AI đọc ${ctx.length} mục tư liệu của phim (~${kb}K ký tự)` + (localGet("askPictures", true) ? " + ảnh nhân vật, bìa, contact sheet" : "")),
          h("ul", { class: "small muted" }, ctx.map((c) => h("li", {}, c.title)))),
        msgs.length || A.pending ? h("div", { class: "ask-msgs", role: "log", "aria-live": "polite" },
          msgs.map((m) => m.role === "user" ? h("div", { class: "ask-msg user" }, m.content)
            : (() => { const d = h("div", { class: "ask-msg ai md" }); d.innerHTML = mdToHtml(m.content); return d; })()),
          A.pending ? [h("div", { class: "ask-msg user" }, A.pending), h("div", { class: "ask-msg ai typing" }, "AI đang đọc tư liệu và trả lời…")] : null) : null,
        chips.length ? h("div", { class: "ask-chips" }, chips.map((q) => h("button", { class: "btn sm ghost ask-chip", onclick: () => sendAsk(q) }, q))) : null,
        msgs.length && !A.busy ? h("div", { class: "row" }, h("span", { class: "spacer" }),
          h("button", { class: "btn sm ghost", onclick: clearAsk }, icon("trash"), "Xoá cuộc trò chuyện")) : null);
    }),
    h("div", { class: "ask-form" }, input,
      h("div", { class: "row" },
        h("label", { class: "check small" }, pics, h("span", {}, "Cho AI xem ảnh (chậm hơn một chút)")),
        h("span", { class: "spacer" }),
        h("button", { class: "btn primary sm", onclick: () => sendAsk(input.value) }, "Gửi"))));
}

/* ---------- posts for social networks ---------- */

const SOCIAL_PLATFORMS = [["tiktok", "TikTok"], ["facebook", "Facebook"], ["instagram", "Instagram"]];
const SOCIAL_LANGS = [["Vietnamese", "Tiếng Việt"], ["English", "English"], ["Thai", "ไทย (Thái)"]];

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const t = h("textarea", { style: { position: "fixed", opacity: "0" } }, text);
    document.body.append(t); t.select(); document.execCommand("copy"); t.remove();
  }
  toast("Đã chép", "", 1500);
}

function postText(p) {
  return [p.title, p.caption, (p.hashtags || []).map((t) => "#" + t).join(" ")].filter((x) => x && x.trim()).join("\n\n");
}

async function loadSocial(force = false) {
  if (!S.cur) return;
  const pid = S.cur.id;
  if (!force && S.social && S.social.pid === pid && S.social.data) return;
  try {
    const data = await api("GET", `/api/projects/${pid}/social`);
    if (S.cur?.id !== pid) return;
    S.social = { ...(S.social?.pid === pid ? S.social : { pid }), data, error: null };
  } catch (e) { S.social = { pid, data: null, error: e.message }; }
  refreshZone("social-zone");
}

async function makeSocial() {
  const SO = S.social, pid = S.cur?.id;
  if (!SO || !pid || SO.busy) return;
  const platforms = localGet("socialPlatforms", ["tiktok", "facebook", "instagram"]);
  const languages = localGet("socialLangs", ["Vietnamese"]);
  if (!platforms.length || !languages.length) { toast("Chọn ít nhất một mạng xã hội và một ngôn ngữ.", "error"); return; }
  SO.busy = true; refreshZone("social-zone");
  try {
    await flushSave();
    const note = document.getElementById("social-note")?.value || "";
    const data = await api("POST", `/api/projects/${pid}/social`, { platforms, languages, note, pictures: true });
    if (S.social?.pid === pid) { SO.data = data; SO.lang = null; }
    if (data.result?.missing?.length) toast("AI chưa viết: " + data.result.missing.join(", "), "", 6000);
  } catch (e) { toast(e.message, "error"); }
  finally { SO.busy = false; refreshZone("social-zone"); }
}

function toggleChoice(key, value, on, fallback) {
  const cur = new Set(localGet(key, fallback));
  if (on) cur.add(value); else cur.delete(value);
  localSet(key, [...cur]);
}

function renderSocial() {
  if (!S.social || S.social.pid !== S.cur.id || (!S.social.data && !S.social.error)) setTimeout(() => loadSocial(), 0);
  const plats = localGet("socialPlatforms", ["tiktok", "facebook", "instagram"]), langs = localGet("socialLangs", ["Vietnamese"]);
  return h("div", { class: "card card-pad social-card" },
    h("div", { class: "card-head" }, h("h3", {}, "Nội dung đăng mạng xã hội"), h("span", { class: "spacer" }),
      h("span", { class: "small muted" }, "Không tốn credit Flow")),
    h("div", { class: "stack tight" },
      h("div", { class: "social-opts" },
        h("span", { class: "lbl" }, "Mạng"),
        SOCIAL_PLATFORMS.map(([k, l]) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: plats.includes(k), onchange: (e) => toggleChoice("socialPlatforms", k, e.target.checked, plats) }), h("span", {}, l)))),
      h("div", { class: "social-opts" },
        h("span", { class: "lbl" }, "Ngôn ngữ"),
        SOCIAL_LANGS.map(([k, l]) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: langs.includes(k), onchange: (e) => toggleChoice("socialLangs", k, e.target.checked, langs) }), h("span", {}, l)))),
      h("input", { type: "text", id: "social-note", placeholder: "Yêu cầu thêm (tuỳ chọn): giọng hài hước, nhắc tập 2, tên kênh…", "aria-label": "Yêu cầu thêm" })),
    zone("social-zone", () => {
      const SO = S.social?.pid === S.cur?.id ? S.social : null;
      const res = SO?.data?.result;
      const btn = h("button", { class: "btn primary sm", disabled: !SO || SO.busy, onclick: makeSocial }, icon("sparkle"),
        SO?.busy ? "AI đang viết…" : res ? "Viết lại" : "Tạo nội dung");
      if (!SO) return h("div", { class: "row", style: { marginTop: "10px" } }, btn);
      if (SO.error) return h("div", { class: "help" }, SO.error);
      const head = h("div", { class: "row", style: { marginTop: "10px" } }, btn,
        SO.busy ? h("span", { class: "help" }, "Khoảng 1–2 phút.") : res ? h("span", { class: "help" }, "Viết lúc " + new Date(res.at).toLocaleString()) : null);
      if (!res) return h("div", {}, head);
      const langsDone = SOCIAL_LANGS.filter(([k]) => res.posts.some((p) => p.language === k));
      const lang = langsDone.some(([k]) => k === SO.lang) ? SO.lang : langsDone[0]?.[0];
      return h("div", { class: "stack tight" }, head,
        langsDone.length > 1 ? h("div", { class: "seg", role: "tablist" }, langsDone.map(([k, l]) =>
          h("button", { class: "btn sm" + (k === lang ? " primary" : " ghost"), role: "tab", "aria-selected": k === lang ? "true" : "false",
            onclick: () => { SO.lang = k; refreshZone("social-zone"); } }, l))) : null,
        res.posts.filter((p) => p.language === lang).map((p) => h("div", { class: "social-post" },
          h("div", { class: "row" }, h("strong", {}, SOCIAL_PLATFORMS.find(([k]) => k === p.platform)?.[1] || p.platform), h("span", { class: "spacer" }),
            h("button", { class: "btn sm", onclick: () => copyText(postText(p)) }, icon("copy"), "Chép bài")),
          p.hook ? h("div", { class: "small" }, h("span", { class: "muted" }, "Chữ trên video 3s đầu: "), h("b", {}, p.hook)) : null,
          p.title ? h("div", { class: "social-title" }, p.title) : null,
          h("div", { class: "social-caption" }, p.caption),
          p.hashtags?.length ? h("div", { class: "social-tags" }, p.hashtags.map((t) => "#" + t).join(" ")) : null,
          p.first_comment ? h("div", { class: "small" }, h("span", { class: "muted" }, "Bình luận ghim: "), p.first_comment,
            " ", h("button", { class: "btn sm ghost icon", title: "Chép bình luận", "aria-label": "Chép bình luận", onclick: () => copyText(p.first_comment) }, icon("copy"))) : null)));
    }));
}

/* ---------- auto-publish to Facebook Reels & YouTube ---------- */

const PUBLISH_PLATFORMS = [["facebook_reel", "Facebook Reels"], ["youtube", "YouTube (Shorts / Video)"]];

async function loadPublish(force = false) {
  if (!S.cur) return;
  const pid = S.cur.id;
  if (!force && S.publish && S.publish.pid === pid && S.publish.data) return;
  try {
    const data = await api("GET", `/api/projects/${pid}/publish`);
    if (S.cur?.id !== pid) return;
    S.publish = { ...(S.publish?.pid === pid ? S.publish : { pid }), data, error: null };
  } catch (e) { S.publish = { pid, data: null, error: e.message }; }
  refreshZone("publish-zone");
}

async function startPublish(force = false) {
  const pid = S.cur?.id;
  if (!pid || jobRunning()) return;
  const platforms = localGet("publishPlatforms", ["facebook_reel", "youtube"]);
  if (!platforms.length) { toast("Chọn ít nhất một nền tảng (Facebook Reels hoặc YouTube).", "error"); return; }
  const privacy = document.getElementById("pub-privacy")?.value || "public";
  const title = document.getElementById("pub-title")?.value || "";
  const description = document.getElementById("pub-desc")?.value || "";
  try {
    await flushSave();
    const j = await api("POST", `/api/projects/${pid}/publish`, { platforms, privacy, title, description, force });
    startJob(j);
  } catch (e) { toast(e.message, "error"); }
}

function renderPublish() {
  if (!S.publish || S.publish.pid !== S.cur.id || (!S.publish.data && !S.publish.error)) setTimeout(() => loadPublish(), 0);
  const plats = localGet("publishPlatforms", ["facebook_reel", "youtube"]);
  return h("div", { class: "card card-pad publish-card" },
    h("div", { class: "card-head" }, h("h3", {}, icon("upload"), " Tự động đăng Facebook Reels & YouTube"), h("span", { class: "spacer" }),
      h("button", { class: "btn sm ghost", onclick: openSettings }, icon("settings"), "Cấu hình kênh")),
    zone("publish-zone", () => {
      const PU = S.publish?.pid === S.cur?.id ? S.publish : null;
      if (!PU || (!PU.data && !PU.error)) return h("div", { class: "help" }, "Đang tải…");
      if (PU.error) return h("div", { class: "help" }, PU.error);
      const d = PU.data, cfg = d.configured || {}, st = d.state?.platforms || {}, pv = d.preview?.youtube || d.preview?.facebook_reel || {};
      const busy = jobRunning();
      return h("div", { class: "stack tight" },
        h("div", { class: "social-opts" },
          h("span", { class: "lbl" }, "Nền tảng"),
          PUBLISH_PLATFORMS.map(([k, l]) => h("label", { class: "check" },
            h("input", { type: "checkbox", checked: plats.includes(k), onchange: (e) => toggleChoice("publishPlatforms", k, e.target.checked, plats) }),
            h("span", {}, l, " ", h("span", { class: "pill " + (cfg[k] ? "ok" : "warn"), style: { fontSize: "11px", padding: "1px 6px" } }, cfg[k] ? "Đã kết nối" : "Chưa cấu hình"))))),
        h("div", { class: "grid2" },
          field("Tiêu đề đăng (để trống = tự lấy từ bài AI / tên phim)", h("input", { type: "text", id: "pub-title", placeholder: pv.title || S.cur.storyboard.title || "" })),
          field("Chế độ hiển thị YouTube", h("select", { id: "pub-privacy", value: d.yt_privacy || "public" },
            h("option", { value: "public" }, "Công khai (Public)"),
            h("option", { value: "unlisted" }, "Không công khai (Unlisted)"),
            h("option", { value: "private" }, "Riêng tư (Private)")))),
        h("details", {}, h("summary", { class: "small muted" }, "Chỉnh sửa mô tả / caption thủ công (tuỳ chọn)"),
          h("textarea", { id: "pub-desc", rows: 3, placeholder: pv.description || "Để trống để tự động dùng caption và hashtag từ mục Nội dung mạng xã hội." })),
        Object.keys(st).length ? h("div", { class: "stack tight", style: { marginTop: "6px" } },
          PUBLISH_PLATFORMS.map(([k, l]) => {
            const p = st[k];
            if (!p) return null;
            return h("div", { class: "row small" },
              h("span", { class: "badge " + (p.status === "published" ? "done" : "failed") }, p.status === "published" ? "Đã đăng" : "Lỗi"),
              h("strong", {}, l),
              p.url ? h("a", { href: p.url, target: "_blank", rel: "noopener" }, p.url, " ↗") : null,
              p.error ? h("span", { class: "warn-text" }, p.error) : null);
          })) : null,
        h("div", { class: "row", style: { marginTop: "8px" } },
          h("button", { class: "btn primary sm", disabled: busy, onclick: () => startPublish(false) }, icon("upload"), "Đăng ngay"),
          Object.values(st).some((x) => x?.status === "published") ? h("button", { class: "btn sm ghost", disabled: busy, onclick: () => startPublish(true) }, icon("refresh"), "Đăng lại (Force)") : null,
          h("span", { class: "help" }, "Tự động gắn #Shorts cho video dọc 9:16 và đính kèm ảnh bìa thumbnail.")));
    }));
}

/* ---------- autopilot ---------- */

const AUTO_STEPS = [["storyboard", "Storyboard"], ["review_storyboard", "Review kịch bản"], ["characters", "Nhân vật"],
  ["keyframes", "Keyframe"], ["review_keyframes", "Review keyframe"], ["videos", "Video"], ["review_clips", "AI QC clip"],
  ["edit", "AI dựng phim"], ["thumbnail", "Thumbnail"], ["review_film", "Review cả phim"]];

function autoCostText(scenes, review) {
  return `Chạy liền: viết storyboard → ${review ? "agent review → " : ""}ảnh nhân vật → keyframe → ${review ? "review keyframe → " : ""}`
    + `render ${scenes} clip video → ${review ? "AI QC từng clip (chỉ đánh dấu, không tự render lại) → " : ""}AI dựng phim → thumbnail${review ? " → review cả phim" : ""}. `
    + `Video TỐN CREDIT (veo-lite khoảng 10 credit/clip ≈ ${scenes * 10} credit). `
    + "Ảnh và dựng phim miễn phí. Có thể bấm Dừng bất cứ lúc nào; bấm “Tạo tự động” lần nữa để làm tiếp từ chỗ dừng.";
}

async function openAutopilot(cur) {
  const review = h("input", { type: "checkbox", checked: localGet("autoReview", true) });
  const subs = h("input", { type: "checkbox", checked: localGet("autoSubs", true) });
  const autoPub = h("input", { type: "checkbox", checked: localGet("autoPublishAfter", !!S.settings?.auto_publish) });
  const left = (cur.storyboard.scenes || []).filter((s) => cur.scene_state?.[s.id]?.status !== "done").length;
  modal({
    title: "Tạo tự động", body: h("div", { class: "stack" },
      h("p", {}, left ? `Làm mọi bước còn thiếu cho “${cur.storyboard.title}”: ${left} cảnh chưa có clip.` : "Mọi cảnh đã có clip: agent review, dựng lại phim và làm thumbnail nếu cần."),
      h("p", { class: "help" }, autoCostText(left, review.checked)),
      h("label", { class: "check" }, review, h("span", {}, "Agent review từng bước")),
      h("label", { class: "check" }, subs, h("span", {}, "In phụ đề lên video")),
      h("label", { class: "check" }, autoPub, h("span", {}, "Tự động đăng lên Facebook Reels & YouTube sau khi tạo phim xong"))),
    buttons: [{ label: "Huỷ" }, { label: "Bắt đầu", cls: "primary", onclick: async (c) => {
      localSet("autoReview", review.checked); localSet("autoSubs", subs.checked); localSet("autoPublishAfter", autoPub.checked);
      const pubPlats = autoPub.checked ? (S.settings?.auto_publish_platforms?.length ? S.settings.auto_publish_platforms : ["facebook_reel", "youtube"]) : [];
      try {
        await flushSave();
        startJob(await api("POST", `/api/projects/${cur.id}/autopilot`, { review: review.checked, burn_subtitles: subs.checked, music: finalPrefs().music, publish_platforms: pubPlats }));
        c();
      } catch (e) { toast(e.message, "error"); }
    } }],
  });
}

function autoStageMap() {
  const saved = Object.fromEntries((S.cur?.autopilot?.stages || []).map((s) => [s.key, s]));
  const j = S.job, live = j && j.kind === "auto" && j.project === S.cur?.id && j.status === "running" ? j.stage : null;
  return { saved, live };
}

function renderAutoSteps() {
  if (!S.cur) return null;
  const { saved, live } = autoStageMap();
  if (!live && !S.cur.autopilot) return null;
  const liveIdx = live ? AUTO_STEPS.findIndex(([k]) => k === live.key) : -1;
  return h("div", { class: "auto-steps", role: "list", "aria-label": "Các bước tạo tự động" },
    AUTO_STEPS.map(([k, label], i) => {
      const st = saved[k] || {};
      let cls = st.status === "done" ? "done" : st.status === "partial" || st.status === "failed" ? "warn" : st.status === "skipped" ? "skip" : "";
      if (live && i === liveIdx && live.status === "running") cls = "running";
      return h("div", { class: "auto-step " + cls, role: "listitem", title: [label, st.note].filter(Boolean).join(" — ") },
        h("span", { class: "n" }, cls === "done" ? "✓" : i + 1), h("span", { class: "l" }, label));
    }));
}

function renderAutoReview() {
  const a = S.cur?.autopilot;
  if (!a) return null;
  const f = a.film_review, sbr = a.storyboard_review;
  const stage = (k) => (a.stages || []).find((s) => s.key === k) || {};
  const rows = [["Kịch bản", sbr ? `${sbr.score}/10` : stage("review_storyboard").note],
    ["Keyframe", stage("review_keyframes").note], ["Clip", stage("review_clips").note]].filter(([, v]) => v);
  return h("div", { class: "card card-pad" },
    h("div", { class: "card-head" }, h("h3", {}, "Agent review"), h("span", { class: "spacer" }),
      f ? h("span", { class: "score " + (f.score >= 8 ? "ok" : f.score >= 6 ? "mid" : "bad") }, `${f.score}/10`) : null),
    f ? h("div", { class: "stack tight" },
      h("p", {}, f.summary),
      f.strengths?.length ? h("ul", { class: "rev-list ok" }, f.strengths.map((x) => h("li", {}, x))) : null,
      f.issues?.length ? h("ul", { class: "rev-list bad" }, f.issues.map((x) => h("li", {}, x))) : null,
      f.suggestions?.length ? h("ul", { class: "rev-list next" }, f.suggestions.map((x) => h("li", {}, x))) : null,
      f.thumbnail ? h("p", { class: "small muted" }, "Ảnh bìa: " + f.thumbnail) : null)
      : h("p", { class: "help" }, "Agent sẽ chấm cả phim khi tạo tự động xong."),
    rows.length ? h("table", { class: "list small", style: { marginTop: "8px" } }, rows.map(([k, v]) => h("tr", {}, h("td", {}, k), h("td", {}, v)))) : null);
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
    h("p", { class: "help" }, "Chưa có bản dựng. Bấm “AI dựng phim” để agent xem các clip đã render và dựng phim."));
  const catalog = E.data.transitions || [];
  const capcutCatalog = E.data.capcut_transitions || [], capcutDefault = E.data.capcut_default || {};
  const capcutLabel = Object.fromEntries(capcutCatalog.map((o) => [o.id, o.label]));
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
    if (plan.pro) rows.push(h("tr", { class: "edit-tr" },
      h("td", { colspan: 6 },
        h("div", { class: "row", style: { gap: "8px" } },
          h("span", { class: "muted" }, "✦"),
          h("select", { "aria-label": `Chuyển động máy ${c.id}`, value: c.motion || "none", title: "Chuyển động máy thêm lúc dựng", onchange: (e) => { c.motion = e.target.value; touch(); } },
            [["none", "Máy đứng yên"], ["push_in", "Đẩy máy vào"], ["pull_out", "Kéo máy ra"]].map(([v, l]) => h("option", { value: v }, l))),
          h("select", { "aria-label": `Hiệu ứng hình ${c.id}`, value: c.effect || "", title: "Hiệu ứng hình CapCut phủ lên cảnh này", style: { maxWidth: "150px" }, onchange: (e) => { c.effect = e.target.value || null; touch(); } },
            h("option", { value: "" }, "Không hiệu ứng"), (E.data.capcut_effects || []).map((n) => h("option", { value: n }, n))),
          h("select", { "aria-label": `Màu riêng ${c.id}`, value: c.filter || "", title: "Màu riêng cho cảnh này (khác màu chung của phim)", style: { maxWidth: "150px" }, onchange: (e) => { c.filter = e.target.value || null; touch(); } },
            h("option", { value: "" }, "Theo màu phim"), (E.data.capcut_filters || []).map((n) => h("option", { value: n }, n))),
          h("span", { class: "small muted" }, "Nhạc"),
          num(Math.round((c.music ?? 1) * 100), 0, 100, 5, (v) => { c.music = v / 100; }, `Mức nhạc nền ${c.id}`),
          h("span", { class: "small muted" }, "%"),
          (c.sounds || []).map((x, k) => h("span", { class: "badge", title: `Tiếng động tìm trên mạng, bắt đầu ở giây ${x.at}. Bấm để bỏ.`, style: { cursor: "pointer" },
            onclick: () => { c.sounds.splice(k, 1); touch(); } }, "♪ " + x.query + " ×")),
          c.note ? h("span", { class: "small muted", style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, title: c.note }, c.note) : null))));
    const t = plan.transitions[i];
    if (!t) return;
    rows.push(h("tr", { class: "edit-tr" },
      h("td", { colspan: 6 },
        h("div", { class: "row", style: { flexWrap: "nowrap", gap: "8px" } },
          h("span", { class: "muted" }, "↳"),
          h("select", { "aria-label": `Chuyển cảnh sau ${t.after}`, value: t.type, onchange: (e) => { t.type = e.target.value; if (t.type !== "cut" && !t.duration) t.duration = 0.5; t.reason = ""; t.capcut = null; touch(); } },
            catalog.map((o) => h("option", { value: o.id }, o.label))),
          t.type !== "cut" ? num(t.duration, 0.2, 2, 0.1, (v) => { t.duration = v; }, `Độ dài chuyển cảnh sau ${t.after}`) : null,
          t.type !== "cut" ? h("span", { class: "small muted" }, "s") : null,
          t.type !== "cut" && capcutCatalog.length ? h("select", { "aria-label": `Chuyển cảnh CapCut sau ${t.after}`, value: t.capcut || "",
            title: "Hiệu ứng dùng khi dựng bằng CapCut", onchange: (e) => { t.capcut = e.target.value || null; touch(); } },
            h("option", { value: "" }, "CapCut: " + (capcutLabel[capcutDefault[t.type]] || "tự chọn")),
            capcutCatalog.map((o) => h("option", { value: o.id }, "CapCut: " + o.label))) : null,
          t.reason ? h("span", { class: "small muted", style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, title: t.reason }, t.reason) : null))));
  });
  const save = async (render) => {
    try {
      const data = await api("PUT", `/api/projects/${S.cur.id}/edit-plan`, plan);
      S.edit = { pid: S.cur.id, data, dirty: false };
      refreshZone("edit-zone");
      if (render) runAction(render === "capcut" ? "capcut" : "edit");
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
    plan.pro ? h("div", { class: "stack tight" },
      h("div", { class: "row" },
        h("label", { class: "field" }, h("span", { class: "lbl" }, "Màu phim (CapCut)"),
          h("select", { "aria-label": "Màu phim", value: plan.look || "", onchange: (e) => { plan.look = e.target.value || null; touch(); } },
            h("option", { value: "" }, "Giữ màu gốc"), (E.data.capcut_filters || []).map((n) => h("option", { value: n }, n)))),
        h("label", { class: "field" }, h("span", { class: "lbl" }, "Độ đậm màu (%)"),
          num(Math.round((plan.look_strength ?? 0.6) * 100), 10, 100, 5, (v) => { plan.look_strength = v / 100; }, "Độ đậm màu phim")),
        h("label", { class: "field", style: { flex: "1" } }, h("span", { class: "lbl" }, "Tiêu đề mở đầu"),
          h("input", { type: "text", "aria-label": "Tiêu đề mở đầu", value: plan.title || "", placeholder: "Không có", onchange: (e) => { plan.title = e.target.value.trim() || null; touch(); } }))),
      h("label", { class: "field" }, h("span", { class: "lbl" }, "Nhạc nền agent tìm trên mạng (khi chưa chọn nhạc ở trên)"),
        h("input", { type: "text", "aria-label": "Từ khoá tìm nhạc nền", value: plan.music_query || "", placeholder: "vd: sad piano cinematic (tiếng Anh)", onchange: (e) => { plan.music_query = e.target.value.trim() || null; touch(); } })),
      E.data.credits ? h("details", {}, h("summary", { class: "small" }, "Nguồn nhạc / tiếng động (ghi nguồn khi đăng)"),
        h("pre", { class: "small", style: { whiteSpace: "pre-wrap", userSelect: "text" } }, E.data.credits)) : null) : null,
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: plan.even_loudness, onchange: (e) => { plan.even_loudness = e.target.checked; touch(); } }),
      h("span", {}, "Cân bằng âm lượng giữa các clip", h("span", { class: "sub" }, "Clip to/nhỏ khác nhau sẽ được kéo về cùng mức."))),
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: plan.duck_music, onchange: (e) => { plan.duck_music = e.target.checked; touch(); } }),
      h("span", {}, "Tự hạ nhạc nền khi có thoại", h("span", { class: "sub" }, "Chỉ có tác dụng khi chọn nhạc nền."))),
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: plan.normalize, onchange: (e) => { plan.normalize = e.target.checked; touch(); } }),
      h("span", {}, "Chuẩn hoá độ lớn cho mạng xã hội", h("span", { class: "sub" }, "-14 LUFS, như YouTube / TikTok / Facebook."))),
    h("div", { class: "row" },
      h("button", { class: "btn primary sm", disabled: busy, onclick: () => save(true) }, icon("film"), E.dirty ? "Lưu & xuất lại" : "Xuất lại theo bản này", FREE()),
      S.env.capcut ? h("button", { class: "btn sm", disabled: busy, title: "Xuất bản dựng này thành dự án CapCut", onclick: () => save("capcut") }, icon("sparkle"), E.dirty ? "Lưu & mở bằng CapCut" : "Mở bằng CapCut") : null,
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
  const sub = h("div", { class: "subtitle", lang: subTrack() }, subLine(S.cur.storyboard, cur.sc, subTrack()) || "");
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
          S.cur = d; rememberCharNames(); rememberLocNames(); toast(d.errors.length ? "Đã lưu, nhưng storyboard còn lỗi" : "Đã lưu YAML", d.errors.length ? "error" : "ok");
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
  rememberCharNames(); rememberLocNames();
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
  if (action !== "dry" && action !== "stitch" && action !== "edit" && action !== "capcut" && !S.env.gflow) { toast("Chưa cài gflow-cli", "error"); return; }
  if ((action === "stitch" || action === "edit" || action === "capcut" || action === "make") && !S.env.ffmpeg) { toast("Chưa cài ffmpeg nên không ghép phim được", "error"); return; }
  if ((action === "edit" || action === "capcut") && S.edit?.dirty && !extra.replan && !(await confirmModal("Bỏ thay đổi chưa lưu?", "Bản dựng có chỉnh sửa chưa lưu. Bấm “Lưu & xuất lại” để giữ chúng.", "Vẫn dựng"))) return;
  const body = { action, only: [], force: [], force_keyframes: [], ...extra };
  if (action === "stitch" || action === "edit" || action === "capcut" || action === "make") Object.assign(body, finalPrefs(), extra);
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
    const prevStage = S.job.stage || {};
    Object.assign(S.job, { status: d.status, result: d.result, finished: d.finished, stop_requested: d.stop_requested,
      title: d.title, stage: d.stage || {} });
    if (d.project && S.job.project !== d.project) {
      S.job.project = d.project;
      if (S.job.kind === "auto") { await loadProjects(); await openProject(d.project); }
    }
    if (S.job.kind === "auto" && (prevStage.key !== S.job.stage.key || prevStage.status !== S.job.stage.status)) {
      refreshZone("auto-zone");
      if (S.cur && S.cur.id === S.job.project) refreshCurrent();
    }
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
        const pid = S.cur.id, chars = pr.done.filter((k) => k.startsWith("char:")), locs = pr.done.filter((k) => k.startsWith("loc:"));
        refreshCurrent().then(() => {
          if (chars.length && S.cur && S.cur.id === pid) syncCharImages(chars);
          if (locs.length && S.cur && S.cur.id === pid) syncLocImages(locs);
        });
      }
    } else if (fresh.some((l) => /^\[[^\]]+\]/.test(l))) {
      refreshZones();
      if (fresh.some((l) => /\] OK -> /.test(l)) && S.cur && S.cur.id === S.job.project) refreshCurrent();
    }
    if (fresh.some((l) => /\] OK -> /.test(l))) refreshCredits(); // a clip was rendered: the estimate went down
    if (d.status === "running") S.pollT = setTimeout(pollJob, 1000);
    else if (wasRunning) onJobDone();
  } catch { S.pollT = setTimeout(pollJob, 3000); }
}

async function onJobDone() {
  refreshCredits();
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
  if (j.kind === "analyze" && j.status === "done" && j.result?.analysis) { await openAnalysis(j.result.analysis); return; }
  if (j.kind === "auto" && j.result?.project) {
    if (!S.cur || S.cur.id !== j.result.project) await openProject(j.result.project);
    else await refreshCurrent();
    await loadEditPlan(true);
    if (j.result.final) { S.tab = "final"; }
    renderMain();
    return;
  }
  if (j.kind === "restore" && j.status === "done" && j.result?.id) { await openProject(j.result.id); return; }
  if (j.kind === "qc_model") { try { S.env = await api("GET", "/api/env"); } catch { /* ignore */ } }
  if (j.kind === "portraits" && S.cur && S.cur.id === j.project && j.result?.images) {
    for (const c of S.cur.storyboard.characters || []) if (j.result.images[c.name]) c.image = j.result.images[c.name];
    if (Object.keys(j.result.images).length) changed(); // server already saved; this keeps a pending edit from dropping it
    try { S.assets = await api("GET", `/api/projects/${S.cur.id}/assets`); } catch { /* ignore */ }
  }
  if (j.kind === "locations" && S.cur && S.cur.id === j.project && j.result?.images) {
    for (const l of S.cur.storyboard.locations || []) {
      const rel = j.result.images[l.name] || j.result.images[String(l.name || "").trim()];
      if (rel) l.image = rel;
    }
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
    if (j.kind === "publish") await loadPublish(true);
    if ((j.kind === "portraits" || j.kind === "locations") && (S.tab === "story" || S.tab === "board")) renderMain();
    if (j.kind === "edit" || j.kind === "capcut" || j.kind === "make") await loadEditPlan(true);
    if (j.kind === "capcut" && j.result?.capcut_project) toast(`Đã tạo dự án CapCut “${j.result.capcut_project}”. Mở CapCut để xem, chỉnh và xuất phim.`);
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

/* A location image finished mid-job (loc:<name> → done): same as syncCharImages, for locations[i].image. */
async function syncLocImages(keys) {
  if (!S.cur) return;
  const pid = S.cur.id;
  try {
    const d = await api("GET", `/api/projects/${pid}`);
    if (!S.cur || S.cur.id !== pid) return;
    const local = S.cur.storyboard.locations || [], remote = d.storyboard?.locations || [];
    let touched = false;
    for (const k of keys) {
      const name = k.slice("loc:".length);
      const l = local.find((x) => String(x?.name || "").trim() === name);
      const r = remote.find((x) => String(x?.name || "").trim() === name);
      if (l && r && r.image && l.image !== r.image) { l.image = r.image; touched = true; }
    }
    const a = await api("GET", `/api/projects/${pid}/assets`);
    if (!S.cur || S.cur.id !== pid) return;
    S.assets = a;
    if (touched) changed();
    refreshZones();
  } catch { /* network error: onJobDone syncs at the end */ }
}

function renderJobbar() {
  const bar = $("#jobbar");
  if (!S.job) { bar.classList.add("hidden"); return; }
  bar.classList.remove("hidden");
  const log = h("pre", { class: "joblog" + (S.jobOpen ? "" : " hidden") + (S.jobOnlyKey ? " only-key" : ""), id: "joblog" });
  put(bar, h("div", { class: "job-head", id: "jobhead" }), log);
  appendLog(S.job.lines, true);
  updateJobHead();
}

/* A log line is classed by importance so the UI can highlight what matters and dim the noise.
   The classes, loud → quiet: l-err (errors), l-ok (success / scores / finished milestones),
   l-warn (warnings, review & QC flags, refused scenes, stops), l-head (phase headers & step
   markers) and l-dim / l-cmd (verbose stdout & command echoes — the only lines the "Chỉ việc
   quan trọng" filter hides). Markers follow the backend's real conventions — symbols (✔ ✘ ⚠ ✎ ⏸),
   line prefixes ("=== …", "  ! …", "  ~ …", "[id] …") and whole phrases — instead of loose
   keywords, so a "!" or the word "Error" sitting inside a prompt or line of dialogue is never
   mistaken for a warning or an error. */
function lineClass(l) {
  const s = String(l || "");
  if (/^\s+\$ /.test(s)) return "l-cmd"; // "  $ flow_ui …": command echo carrying the raw prompt ("!", "Error"…)
  if (/^\s*={2,}/.test(s)) return "l-head"; // phase header: "=== Bước 1/10: … ===", "== XEM TRƯỚC: … =="
  if (/^\s*(Xong|XEM TRƯỚC):/.test(s)) { // end-of-stage summary — red only when it actually counted errors
    const m = s.match(/Lỗi:\s*(\d+)/);
    return m ? (Number(m[1]) > 0 ? "l-err" : "l-ok") : "l-head";
  }
  if (/✘|(^|\s)LỖI:/.test(s)) return "l-err"; // "[s01] LỖI: …", "  ✘ s01: …"
  if (/✔|🎬|OK ->|^\s*Ghép \d|^\s*Điểm |^\s*⭐|^\s*🖼|^\s*☁ Đã/.test(s)) return "l-ok"; // success, scores, milestones
  if (/⚠|⏸|✎|^\s*!\s|^\s*~\s|KHÔNG GỬI:|Đã dừng theo yêu cầu|Có cảnh lỗi/.test(s)) return "l-warn"; // flags & stops
  if (/^\s*\[[^\]]+\]/.test(s)) return "l-head"; // step / scene marker, e.g. "[s01] t2v: …", "[portrait Anna]"
  return "l-dim";
}

/* The "only important" filter keeps everything except raw stdout (l-dim) and command echoes (l-cmd). */
function isKeyLine(cls) { return cls !== "l-dim" && cls !== "l-cmd"; }

/* When the filter is on but nothing important has been logged yet (e.g. the opening minutes of
   "Viết storyboard", or a Backup/Restore whose milestone line hasn't arrived), the box would look
   blank and feel hung. Show a note so it's clear the lines are hidden, not missing. */
function syncLogFilterNote() {
  const el = document.getElementById("joblog");
  if (!el || !S.job) return;
  const note = el.querySelector(".joblog-empty");
  const anyKey = S.job.lines.some((l) => isKeyLine(lineClass(l)));
  if (S.jobOnlyKey && !anyKey) {
    if (!note) el.append(h("div", { class: "joblog-empty" }, "Đang ẩn các dòng phụ (lệnh, nhật ký chi tiết). Bấm nút lọc để xem tất cả."));
  } else if (note) { note.remove(); }
}

/* Toggle "only important lines": hides dimmed command / verbose output, keeps errors,
   successes, warnings and section headers. Remembered across sessions. */
function toggleJobFilter() {
  S.jobOnlyKey = !S.jobOnlyKey;
  localSet("jobOnlyKey", S.jobOnlyKey);
  const el = document.getElementById("joblog");
  if (el) { el.classList.toggle("only-key", S.jobOnlyKey); syncLogFilterNote(); el.scrollTop = el.scrollHeight; }
  updateJobHead();
  toast(S.jobOnlyKey ? "Chỉ hiện log quan trọng" : "Hiện tất cả log", "", 1500);
}

function appendLog(lines, reset = false) {
  const el = document.getElementById("joblog");
  if (!el) return;
  if (reset) put(el);
  const note = el.querySelector(".joblog-empty");
  if (note) note.remove(); // keep it last; re-added by syncLogFilterNote once the new lines are in
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  for (const l of lines) el.append(h("div", { class: lineClass(l) }, l || " "));
  syncLogFilterNote();
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
    j.kind === "auto" && j.stage?.label && j.status === "running" ? h("span", { class: "badge mini running" }, `Bước ${j.stage.index || "?"}/${j.stage.total || 10}: ${j.stage.label}`) : null,
    h("span", { class: "badge mini " + kind }, statusText, " · ", h("span", { id: "elapsed" }, elapsedText())),
    h("span", { class: "last", title: last }, last),
    h("div", { class: "tools" },
      j.status === "running" ? h("button", { class: "btn sm danger", disabled: j.stop_requested, onclick: stopJob }, icon("stop"), j.stop_requested ? "Đang dừng…" : "Dừng") : null,
      S.jobOpen ? h("button", { class: "btn sm ghost icon" + (S.jobOnlyKey ? " on" : ""), "aria-pressed": String(!!S.jobOnlyKey),
        title: S.jobOnlyKey ? "Đang chỉ hiện việc quan trọng — bấm để xem tất cả" : "Chỉ hiện việc quan trọng",
        "aria-label": "Lọc log quan trọng", onclick: toggleJobFilter }, icon("filter")) : null,
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
    const F = S.env.formats;
    if (!F) return dramaForm(null);
    const box = h("div", { class: "stack" });
    // The old short drama flow is deprecated: a new film never starts on it (not even when it was the last pick).
    const saved = F.formats.find((f) => f.key === localGet("filmFormat") && !f.deprecated);
    let fmt = saved ? saved.key : (F.default || F.formats.find((f) => !f.deprecated).key);
    const draw = () => {
      const info = F.formats.find((f) => f.key === fmt);
      put(box, h("div", { class: "field" }, h("span", { class: "lbl" }, "Định dạng", h("span", { class: "hint" }, " · quyết định số cảnh, nhịp kể và cách chia tập")),
        h("div", { class: "choice fmt-choice", role: "radiogroup", "aria-label": "Định dạng phim" }, F.formats.map((f) => h("button", {
          class: (fmt === f.key ? "on" : "") + (f.deprecated ? " deprecated" : ""), role: "radio", "aria-checked": String(fmt === f.key),
          onclick: () => { fmt = f.key; localSet("filmFormat", fmt); draw(); } }, h("b", {}, f.label), h("span", {}, f.desc))))),
        info.deprecated ? h("div", { class: "banner warn flush", role: "note" }, icon("alert"), h("div", { class: "b-body" }, F.deprecated_note)) : null,
        info.basic ? basicForm(info) : dramaForm(fmt));
    };
    draw();
    return box;
  };
  /* Formats other than the drama: text -> image -> video, without the drama's direction, camera or dialogue rows. */
  const basicForm = (info) => {
    const F = S.env.formats;
    const idea = h("textarea", { rows: 4, placeholder: "Ví dụ: Một chú mèo lạc đường trong thành phố tìm cách về nhà trước khi trời mưa…", value: localGet("draftIdea", ""), oninput: (e) => localSet("draftIdea", e.target.value) });
    const n = h("input", { type: "number", min: 1, max: 80, value: info.scenes });
    const minutes = h("span", { class: "help" });
    const showMin = () => { const k = +n.value || 0, sec = k * 8; minutes.textContent = `≈ ${sec < 120 ? sec + " giây" : Math.round(sec / 60) + " phút"} phim (mỗi cảnh ~8 giây).` + (info.key === "feature" || info.key === "series" ? " Tối đa 80 cảnh mỗi lần; phim dài hơn hãy làm thành nhiều phần / nhiều tập." : ""); };
    n.oninput = showMin; showMin();
    let audio = F.audio.some((a) => a.key === localGet("filmAudio")) ? localGet("filmAudio") : "silent";
    let visual = F.visuals.some((v) => v.key === localGet("filmVisual")) ? localGet("filmVisual") : "realistic";
    const genres = new Set((localGet("filmGenres", []) || []).filter((g) => F.genres.some((x) => x.key === g)));
    const custom = h("input", { type: "text", maxlength: 300, placeholder: F.other_style_hint, value: localGet("filmCustomStyle", ""), oninput: (e) => localSet("filmCustomStyle", e.target.value) });
    const aspect = h("select", {}, F.aspects.map((a) => h("option", { value: a.value, disabled: !a.enabled }, a.label)));
    aspect.value = ["9:16", "16:9"].includes(localGet("filmAspect")) ? localGet("filmAspect") : "9:16";
    aspect.onchange = () => localSet("filmAspect", aspect.value);
    const lang = langPicker(SUBTITLE_LANGS.filter(([v]) => v !== "none"), cleanLang(localGet("filmLang")) || "Vietnamese");
    const burn = h("input", { type: "checkbox", checked: localGet("autoSubs", true) });
    const audioBox = h("div"), styleBox = h("div"), langBox = h("div");
    const drawAudio = () => {
      const a = F.audio.find((x) => x.key === audio);
      put(audioBox, h("div", { class: "choice audio-choice", role: "radiogroup", "aria-label": "Âm thanh / lời" }, F.audio.map((x) => h("button", {
        class: audio === x.key ? "on" : "", role: "radio", "aria-checked": String(audio === x.key),
        onclick: () => { audio = x.key; localSet("filmAudio", audio); drawAudio(); } }, h("b", {}, x.label), h("span", {}, x.desc)))));
      put(langBox, field(a.spoken ? "Ngôn ngữ lời nói" : a.text ? "Ngôn ngữ chữ trên màn hình" : "Ngôn ngữ tên phim", lang.el),
        a.text ? h("label", { class: "check" }, burn, h("span", {}, "In chữ lên video", h("span", { class: "sub" }, "Luôn có thêm file .srt riêng."))) : null);
    };
    const drawStyle = () => put(styleBox,
      h("select", { "aria-label": "Phong cách hình ảnh", onchange: (e) => { visual = e.target.value; localSet("filmVisual", visual); drawStyle(); } },
        F.visuals.map((v) => h("option", { value: v.key, selected: v.key === visual }, v.label))),
      visual === "other" ? custom : null);
    const genreChips = h("div", { class: "chips", role: "group", "aria-label": "Thể loại" }, F.genres.map((g) => h("button", {
      class: "chip" + (genres.has(g.key) ? " on" : ""), "aria-pressed": String(genres.has(g.key)),
      onclick: (e) => { genres.has(g.key) ? genres.delete(g.key) : genres.add(g.key); localSet("filmGenres", [...genres]);
        e.currentTarget.classList.toggle("on", genres.has(g.key)); e.currentTarget.setAttribute("aria-pressed", String(genres.has(g.key))); } }, g.label)));
    drawAudio(); drawStyle();
    setTimeout(() => idea.focus(), 30);
    const payload = () => {
      if (!idea.value.trim()) { toast("Hãy nhập ý tưởng", "error"); return null; }
      if (visual === "other" && !custom.value.trim()) { toast("Hãy mô tả phong cách hình ảnh", "error"); custom.focus(); return null; }
      const l = lang.read();
      if (!l.ok) { toast("Ngôn ngữ: " + l.msg, "error"); lang.input.focus(); return null; }
      localSet("filmLang", l.value);
      return { idea: idea.value, scenes: Math.max(1, Math.min(80, +n.value || info.scenes)), aspect: aspect.value, language: l.value,
        format: info.key, audio, visual_style: visual, custom_style: visual === "other" ? custom.value : "", genres: [...genres] };
    };
    const planOnly = async (e) => {
      const body = payload(); if (!body) return;
      const btn = e.currentTarget; btn.disabled = true;
      try { const j = await api("POST", "/api/projects/plan", body); close(); localSet("draftIdea", ""); startJob(j); toast("Claude đang viết kịch bản, thường mất 1–3 phút…", "", 6000); }
      catch (err) { toast(err.message, "error"); btn.disabled = false; }
    };
    const planAll = async (e) => {
      const body = payload(); if (!body) return;
      const btn = e.currentTarget;
      localSet("autoSubs", burn.checked);
      if (!(await confirmModal("Tạo tự động toàn bộ?", autoCostText(body.scenes, false), "Bắt đầu"))) return;
      btn.disabled = true;
      try {
        const j = await api("POST", "/api/projects/autopilot", { ...body, review: false, burn_subtitles: burn.checked });
        close(); localSet("draftIdea", ""); startJob(j); toast("Đang tạo phim tự động. Bạn có thể làm việc khác.", "", 8000);
      } catch (err) { toast(err.message, "error"); btn.disabled = false; }
    };
    return h("div", { class: "stack" },
      field("Ý tưởng phim", idea),
      h("div", { class: "field" }, h("span", { class: "lbl" }, "Âm thanh / lời", h("span", { class: "hint" }, " · quyết định kịch bản có viết lời hay không")), audioBox),
      h("div", { class: "grid2" }, h("div", { class: "field" }, h("span", { class: "lbl" }, "Phong cách hình ảnh"), styleBox), field("Khung hình", aspect)),
      h("div", { class: "field" }, h("span", { class: "lbl" }, "Thể loại", h("span", { class: "hint" }, " · chọn nhiều")), genreChips),
      h("div", { class: "grid2" }, h("div", { class: "field" }, h("span", { class: "lbl" }, "Số cảnh"), n, minutes), h("div", { class: "stack tight" }, langBox)),
      h("p", { class: "help" }, "Luồng I2V: mỗi cảnh Claude viết một ảnh mở đầu và một chuyển động theo chuẩn prompt I2V → tạo ảnh → ảnh thành video. Bối cảnh được mô tả một lần và giữ nguyên giữa các cảnh. Không có chỉ đạo nội dung, camera hay thoại từng dòng như Short drama."),
      h("div", { class: "row" }, h("span", { class: "spacer" }),
        h("button", { class: "btn", onclick: planOnly, title: "Chỉ viết kịch bản để bạn xem và sửa trước" }, "Chỉ viết kịch bản"),
        h("button", { class: "btn primary", onclick: planAll, title: "Kịch bản → ảnh nhân vật → ảnh cảnh → video → dựng phim → thumbnail" }, icon("sparkle"), "Tạo tự động toàn bộ", PAID())));
  };
  /* Short drama: the original form, unchanged (`fmt` is sent so the project records its format). */
  const dramaForm = (fmt) => {
    const idea = h("textarea", { rows: 4, placeholder: "Ví dụ: Một cô gái mở quán cà phê nhỏ ở Hà Nội, buổi sáng đầu tiên đón vị khách đầu tiên là ông cụ hàng xóm…", value: localGet("draftIdea", "") , oninput: (e) => localSet("draftIdea", e.target.value) });
    const n = h("input", { type: "number", min: 1, max: 80, value: localGet("autoScenes", 10) });
    const autoReview = h("input", { type: "checkbox", checked: localGet("autoReview", true) });
    const autoSubs = h("input", { type: "checkbox", checked: localGet("autoSubs", true) });
    const aspect = h("select", {}, h("option", { value: "16:9" }, "Ngang 16:9"), h("option", { value: "9:16" }, "Dọc 9:16 (TikTok/Shorts)"));
    const speak = langPicker(DIALOGUE_LANGS, cleanLang(localGet("dialogueLang")) || "Vietnamese");
    const subSaved = cleanLang(localGet("subtitleLang"));
    const sub = langPicker(SUBTITLE_LANGS, subSaved || "Vietnamese");
    setTimeout(() => idea.focus(), 30);
    const goAuto = async (e) => {
      if (!idea.value.trim()) { toast("Hãy nhập ý tưởng", "error"); return; }
      const a = speak.read(), b = sub.read();
      if (!a.ok) { toast("Nhân vật nói: " + a.msg, "error"); speak.input.focus(); return; }
      if (!b.ok) { toast("Ngôn ngữ phụ đề: " + b.msg, "error"); sub.input.focus(); return; }
      const btn = e.currentTarget, scenes = +n.value || 10;
      localSet("dialogueLang", a.value); localSet("subtitleLang", b.value); localSet("autoScenes", scenes);
      localSet("autoReview", autoReview.checked); localSet("autoSubs", autoSubs.checked);
      if (!(await confirmModal("Tạo tự động toàn bộ?", autoCostText(scenes, autoReview.checked), "Bắt đầu"))) return;
      btn.disabled = true;
      try {
        const j = await api("POST", "/api/projects/autopilot", { idea: idea.value, scenes, aspect: aspect.value, language: b.value,
          dialogue_language: a.value, review: autoReview.checked, burn_subtitles: autoSubs.checked, ...(fmt ? { format: fmt } : {}) });
        close(); localSet("draftIdea", ""); startJob(j); toast("Đang tạo phim tự động — thường mất 40–90 phút cho 10 cảnh. Bạn có thể làm việc khác.", "", 8000);
      } catch (err) { toast(err.message, "error"); btn.disabled = false; }
    };
    const go = async (e) => {
      if (!idea.value.trim()) { toast("Hãy nhập ý tưởng", "error"); return; }
      const a = speak.read(), b = sub.read();
      if (!a.ok) { toast("Nhân vật nói: " + a.msg, "error"); speak.input.focus(); return; }
      if (!b.ok) { toast("Ngôn ngữ phụ đề: " + b.msg, "error"); sub.input.focus(); return; }
      const btn = e.currentTarget;
      localSet("dialogueLang", a.value); localSet("subtitleLang", b.value); // remembered even if the plan job fails
      btn.disabled = true;
      try {
        const j = await api("POST", "/api/projects/plan", { idea: idea.value, scenes: +n.value || 6, aspect: aspect.value, language: b.value, dialogue_language: a.value, ...(fmt ? { format: fmt } : {}) });
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
      h("div", { class: "auto-opts" },
        h("label", { class: "check" }, autoReview, h("span", {}, "Agent review từng bước", h("span", { class: "sub" }, "Review storyboard, keyframe (tự tạo lại ảnh lỗi), clip (render lại clip hỏng, tối đa 3) và cả phim."))),
        h("label", { class: "check" }, autoSubs, h("span", {}, "In phụ đề lên video", h("span", { class: "sub" }, "Luôn có thêm file .srt riêng.")))),
      h("div", { class: "row" }, h("span", { class: "spacer" }),
        h("button", { class: "btn", onclick: go, title: "Chỉ viết storyboard để bạn xem và sửa trước" }, "Chỉ viết storyboard"),
        h("button", { class: "btn primary", onclick: goAuto, title: "Storyboard → nhân vật → keyframe → video → AI dựng phim → thumbnail, có agent review" }, icon("sparkle"), "Tạo tự động toàn bộ", PAID())));
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

/* ---------- video analysis (Phân tích video) ---------- */

function openAnalyzeVideo() {
  const va = S.env?.video_analysis || {};
  const url = h("input", { type: "url", placeholder: "https://www.youtube.com/shorts/…  ·  TikTok  ·  Facebook  ·  link .mp4", value: localGet("anaUrl", "") });
  const rea = h("input", { type: "checkbox", checked: !!va.rea_video && localGet("anaRea", true) !== false, disabled: !va.rea_video });
  const list = h("div", { class: "stack", style: { gap: "6px" } }, h("p", { class: "row muted small" }, h("span", { class: "spinner" }), "Đang tải các bản phân tích…"));
  let close;
  const go = async (e) => {
    const v = url.value.trim();
    if (!/^https?:\/\//i.test(v)) { toast("Hãy dán một link http(s) tới video", "error"); url.focus(); return; }
    if (!S.env?.planner) { toast("Cần Claude Code (đã đăng nhập) hoặc Anthropic API key để phân tích", "error"); return; }
    const btn = e.currentTarget; btn.disabled = true;
    localSet("anaUrl", v); localSet("anaRea", rea.checked);
    try { startJob(await api("POST", "/api/analyses", { url: v, use_rea: rea.checked })); close(); localSet("anaUrl", ""); toast("Đang phân tích video, thường mất 3–10 phút…", "", 6000); }
    catch (err) { toast(err.message, "error"); btn.disabled = false; }
  };
  url.onkeydown = (e) => { if (e.key === "Enter") go({ currentTarget: e.target.closest(".modal").querySelector(".btn.primary") }); };
  const body = h("div", { class: "stack" },
    field("Link video", url),
    h("p", { class: "help" }, "Film Maker tải video về máy, tìm từng điểm cắt, đo âm thanh, cho Claude xem ảnh từng shot rồi viết bản phân tích: nhịp dựng, chuyển cảnh, góc máy, màu, nhạc & âm thanh, chữ trên hình, nhân vật, bối cảnh, nhịp truyện. Sau đó bạn tạo phim mới cùng phong cách bằng một nút."),
    va.ytdlp ? null : h("div", { class: "banner warn flush" }, icon("alert"), h("div", { class: "b-body" }, h("div", {}, "Chưa cài yt-dlp nên chỉ tải được link thẳng tới file video (.mp4). Link YouTube / TikTok / Facebook cần: ", h("code", {}, "brew install yt-dlp"), "."))),
    h("label", { class: "check" }, rea, h("span", {}, "Dùng thêm rea-video: tách nhạc / giọng / tiếng động, BPM, đọc chữ trên hình (chậm hơn vài phút)",
      va.rea_video ? null : h("span", { class: "help" }, " — chưa cài (xem README, mục Phân tích video)"))),
    h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "btn primary", onclick: go }, icon("scan"), "Phân tích")),
    h("div", { class: "side-title", style: { margin: "8px 0 0" } }, "Đã phân tích"), list);
  close = modal({ title: "Phân tích video", body, wide: true });
  api("GET", "/api/analyses").then((items) => {
    if (!items.length) { put(list, h("p", { class: "muted small" }, "Chưa có bản phân tích nào.")); return; }
    put(list, items.map((a) => h("div", { class: "row ana-item" },
      h("div", { class: "ana-thumb", style: a.thumb ? { backgroundImage: `url("${a.thumb}")` } : null }),
      h("div", { class: "grow", style: { minWidth: 0 } }, h("div", { class: "name" }, a.title), h("div", { class: "muted small ellipsis" }, [a.duration ? fmtDur(a.duration) : "", a.aspect || "", ago(a.created), a.url].filter(Boolean).join(" · "))),
      h("button", { class: "btn sm", onclick: () => { close(); openAnalysis(a.id); } }, "Xem"),
      h("button", { class: "btn sm ghost icon", title: "Xoá bản phân tích", "aria-label": "Xoá bản phân tích", onclick: async (e) => {
        if (!(await confirmModal("Xoá bản phân tích?", `“${a.title}” cùng video đã tải sẽ bị xoá khỏi máy. Các phim đã tạo theo nó vẫn giữ nguyên.`, "Xoá", true))) return;
        try { await api("DELETE", `/api/analyses/${a.id}`); e.target.closest(".ana-item").remove(); if (S.ana?.id === a.id) closeAnalysis(); }
        catch (err) { toast(err.message, "error"); }
      } }, icon("trash")))));
  }).catch((e) => put(list, h("p", { class: "muted small" }, e.message)));
}

async function openAnalysis(id) {
  try { S.ana = await api("GET", `/api/analyses/${id}`); }
  catch (e) { toast(e.message, "error"); return; }
  await flushSave();
  S.cur = null;
  toggleSidebar(false);
  if (location.hash.slice(1) !== "analysis/" + id) history.replaceState(null, "", "#analysis/" + id);
  renderSidebar(); renderMain();
  $("#main").scrollTop = 0;
}

function closeAnalysis() {
  S.ana = null;
  history.replaceState(null, "", location.pathname);
  const last = localGet("lastProject"), pick = S.projects.some((p) => p.id === last) ? last : S.projects[0]?.id;
  if (pick) openProject(pick); else { renderSidebar(); renderMain(); }
}

const SHOT_COLORS = ["#c2410c", "#1d4ed8", "#15803d", "#7c3aed", "#b45309", "#0e7490"];
const anaTime = (s) => { s = Math.max(0, +s || 0); return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`; };

function renderAnalysis() {
  const d = S.ana, ai = d.ai || {}, m = d.measured || {}, st = m.stats || {}, src = d.source || {};
  const notes = Object.fromEntries((ai.shots || []).map((s) => [s.n, s]));
  const dur = m.duration || 1;
  let video;
  const seek = (t) => { if (video) { video.currentTime = t; video.play().catch(() => {}); video.scrollIntoView({ block: "nearest", behavior: "smooth" }); } };
  const p = (t) => t ? h("p", {}, t) : null;
  const card = (title, ...kids) => h("section", { class: "card ana-card" }, h("div", { class: "card-head" }, h("h3", {}, title)), h("div", { class: "card-body stack" }, ...kids));
  const kv = (k, v) => v ? h("div", { class: "kv" }, h("b", {}, k), h("span", {}, v)) : null;

  // shot strip: width ∝ shot length; click = jump the video there
  const strip = h("div", { class: "shot-strip", role: "list", "aria-label": "Các shot theo thời gian" },
    (m.shots || []).map((s, i) => {
      const n = notes[s.n] || {};
      return h("button", { class: "shot-seg", role: "listitem", style: { flexGrow: s.length, background: SHOT_COLORS[i % SHOT_COLORS.length] },
        title: `#${s.n} · ${anaTime(s.start)} · ${s.length.toFixed(1)}s\n${[n.size, n.angle, n.movement].filter(Boolean).join(", ")}\n${n.subject || ""}${n.transition_in && n.transition_in !== "cut" ? "\nVào bằng: " + n.transition_in : ""}`,
        onclick: () => seek(s.start) }, s.length >= dur / 40 ? String(s.n) : "");
    }));
  const beats = h("div", { class: "beat-strip" }, (ai.beats || []).map((b) => h("button", { class: "beat-seg", style: { flexGrow: Math.max(0.1, b.end - b.start) }, title: `${b.label}: ${b.what_vi}`, onclick: () => seek(b.start) }, b.label)));

  // loudness curve with the sound moments
  const curve = m.loudness || [];
  let sound = null;
  if (curve.length > 1) {
    const W = 600, H = 70, lo = -60, hi = Math.max(-5, ...curve);
    const x = (t) => (t / Math.max(1, curve.length - 1)) * W, y = (v) => H - ((Math.max(lo, v) - lo) / (hi - lo)) * H;
    const pts = curve.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const marks = ((ai.sound || {}).moments || []).map((mo) => `<line x1="${x(mo.at)}" x2="${x(mo.at)}" y1="0" y2="${H}" class="mk"><title>${esc(anaTime(mo.at) + " " + mo.what_vi)}</title></line>`).join("");
    const t = document.createElement("template");
    t.innerHTML = `<svg class="loud" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Độ to âm thanh theo thời gian"><polygon points="0,${H} ${pts} ${W},${H}" class="fill"/><polyline points="${pts}" class="line"/>${marks}</svg>`;
    sound = t.content.firstChild;
  }
  const look = ai.look || {}, snd = ai.sound || {}, txt = ai.text || {};
  const film = renderAnalysisFilmForm(d);

  return h("div", { class: "main-inner ana" },
    h("div", { class: "phead" },
      h("div", { class: "title-col" },
        h("div", { class: "muted small" }, "Phân tích video"),
        h("h1", {}, ai.title_vi || src.title || d.id),
        h("div", { class: "stats" },
          h("span", {}, fmtDur(dur)), h("span", {}, (m.aspect === "9:16" ? "Dọc " : "Ngang ") + (m.ratio || m.aspect || "16:9")),
          h("span", {}, `${st.shots || 0} shot · TB ${st.average || 0}s/shot · ${st.cuts_per_minute || 0} cắt/phút`),
          src.url ? h("a", { href: src.url, target: "_blank", rel: "noopener noreferrer" }, "Video gốc ", icon("external")) : null,
          d.rea?.used ? h("span", { class: "badge done mini" }, "rea-video") : null)),
      h("div", { class: "actions" },
        h("button", { class: "btn primary", onclick: () => film.scrollIntoView({ behavior: "smooth", block: "start" }) }, icon("sparkle"), "Tạo phim từ video này"),
        d.rea_report ? h("a", { class: "btn", href: d.rea_report, target: "_blank", rel: "noopener" }, "Báo cáo rea-video") : null,
        h("button", { class: "btn icon", title: "Đóng", "aria-label": "Đóng bản phân tích", onclick: closeAnalysis }, icon("x")))),
    h("div", { class: "ana-top" },
      d.video_url ? (video = h("video", { src: d.video_url, controls: true, preload: "metadata", class: "ana-video" + (m.aspect === "9:16" ? " tall" : "") })) : null,
      card("Tóm tắt", p(ai.summary_vi), kv("Thể loại", ai.genre_vi), kv("Mở đầu (hook)", ai.hook_vi), kv("Vì sao hiệu quả", ai.why_it_works_vi))),
    card("Nhịp dựng", strip, beats.childNodes.length ? beats : null,
      h("div", { class: "chips" }, [["Shot", st.shots], ["Trung bình", st.average + "s"], ["Trung vị", st.median + "s"], ["Ngắn nhất", st.shortest + "s"], ["Dài nhất", st.longest + "s"], ["Cắt/phút", st.cuts_per_minute], ["Shot trong 3s đầu", st.first_3s_shots]]
        .map(([k, v]) => h("span", { class: "chip" }, k + ": ", h("b", {}, v)))),
      p(ai.rhythm_vi)),
    h("div", { class: "grid2 ana-grid" },
      card("Nhịp truyện", h("ol", { class: "ana-list" }, (ai.beats || []).map((b) => h("li", {}, h("a", { href: "#", onclick: (e) => { e.preventDefault(); seek(b.start); } }, `${anaTime(b.start)}–${anaTime(b.end)}`), " ", h("b", {}, b.label), ": ", b.what_vi)))),
      card("Chuyển cảnh", h("ul", { class: "ana-list" }, (ai.transitions || []).map((t) => h("li", {}, h("b", {}, `${t.type} ×${t.count}`), t.where_vi ? ": " + t.where_vi : "")))),
      card("Máy quay & khung hình", p(ai.camera_vi)),
      card("Màu & ánh sáng", kv("Màu", look.palette_vi), kv("Chỉnh màu", look.grade_vi), kv("Ánh sáng", look.lighting_vi), kv("Chất hình", look.texture_vi),
        look.style_prompt ? h("div", { class: "mono small ana-pre" }, look.style_prompt) : null),
      card("Âm thanh", sound, kv("Nhạc", snd.music_vi), kv("Giọng", snd.voice_vi), kv("Hiệu ứng", snd.sfx_vi),
        (snd.moments || []).length ? h("ul", { class: "ana-list" }, snd.moments.map((mo) => h("li", {}, h("a", { href: "#", onclick: (e) => { e.preventDefault(); seek(mo.at); } }, anaTime(mo.at)), " ", mo.what_vi))) : null,
        snd.music_query ? kv("Tìm nhạc tương tự", snd.music_query) : null),
      card("Chữ trên hình", kv("Phụ đề", txt.subtitles_vi), kv("Tiêu đề / nhãn", txt.titles_vi)),
      card("Nhân vật", h("ul", { class: "ana-list" }, (ai.characters || []).map((c) => h("li", {}, h("b", {}, c.name), c.role_vi ? ` (${c.role_vi})` : "", ": ", c.look)))),
      card("Bối cảnh", h("ul", { class: "ana-list" }, (ai.settings || []).map((c) => h("li", {}, h("b", {}, c.name), ": ", c.look))))),
    card("Danh sách shot", h("div", { class: "table-wrap" }, h("table", { class: "ana-table" },
      h("thead", {}, h("tr", {}, ["#", "Lúc", "Dài", "Cỡ cảnh", "Góc", "Máy", "Vào bằng", "Nội dung", "Chữ"].map((x) => h("th", {}, x)))),
      h("tbody", {}, (m.shots || []).map((s) => { const n = notes[s.n] || {}; return h("tr", { onclick: () => seek(s.start), title: n.purpose || "" },
        h("td", {}, s.n), h("td", {}, anaTime(s.start)), h("td", {}, s.length.toFixed(1) + "s"), h("td", {}, n.size || ""), h("td", {}, n.angle || ""),
        h("td", {}, n.movement || ""), h("td", {}, n.transition_in || ""), h("td", {}, n.subject || ""), h("td", {}, n.text_on_screen || "")); })))),
      (d.sheets || []).length ? h("details", {}, h("summary", {}, `Ảnh các shot (${d.sheets.length} bảng)`), h("div", { class: "ana-sheets" }, d.sheets.map((sh) => h("a", { href: sh.url, target: "_blank" }, h("img", { src: sh.url, alt: "Shot " + sh.shots.join(", "), loading: "lazy" }))))) : null),
    card("Hướng dẫn dựng cho agent dựng phim", h("p", { class: "help" }, "Được lưu vào storyboard phim mới (edit_style). Agent dựng phim (Dựng phim / CapCut) làm theo nhịp, chuyển cảnh, màu và âm thanh này."),
      h("pre", { class: "ana-pre" }, d.edit_style || ai.edit_style || "")),
    card("Cách làm phim tương tự", d.recipe_timing_vi ? h("p", { class: "help" }, h("b", {}, "Thời lượng: "), d.recipe_timing_vi) : null, p(ai.film_recipe_vi)),
    film);
}

function renderAnalysisFilmForm(d) {
  const ai = d.ai || {};
  const MODES = [["same", "copy", "Làm giống video gốc", "Giữ nguyên truyện, cảnh, góc máy, nhịp; chỉ tạo nhân vật mới"],
    ["style", "sparkle", "Phong cách tương tự", "Câu chuyện và nhân vật mới, cùng thể loại và nhịp dựng"]];
  let mode = localGet("anaFilmMode") === "same" ? "same" : "style";
  const PH = { style: "Để trống: Claude tự nghĩ câu chuyện mới cùng thể loại. Gợi ý: " + (ai.idea_suggestion_vi || ""),
    same: "Tuỳ chọn, ví dụ: nhân vật là gia đình người Việt, chồng khoảng 35 tuổi, vợ tóc ngắn…" };
  const idea = h("textarea", { rows: 4 });
  const ideaField = field("Ý tưởng phim mới", idea, "tuỳ chọn");
  const ideaLabel = ideaField.querySelector(".lbl");
  const hint = ai.idea_suggestion_vi ? h("p", { class: "help" }, "Gợi ý: ", ai.idea_suggestion_vi, " ", h("a", { href: "#", onclick: (e) => { e.preventDefault(); idea.value = ai.idea_suggestion_vi; idea.focus(); } }, "Dùng gợi ý")) : null;
  const n = h("input", { type: "number", min: 1, max: 20, value: d.suggested_scenes || 6 });
  const timing = h("p", { class: "help ana-timing" }, d.recipe_timing_vi || "");
  let tmr = 0, seq = 0;
  const refreshTiming = () => {
    clearTimeout(tmr);
    tmr = setTimeout(async () => {
      const k = Math.max(1, Math.min(20, +n.value || 0)), my = ++seq;
      if (!k) return;
      try { const b = await api("GET", `/api/analyses/${d.id}/budget?n=${k}`); if (my === seq) timing.textContent = b.text; } catch { /* keep the last preview */ }
    }, 250);
  };
  n.addEventListener("input", refreshTiming);
  const reset = h("a", { href: "#", onclick: (e) => { e.preventDefault(); n.value = d.suggested_scenes || 6; refreshTiming(); } }, `Gợi ý: ${d.suggested_scenes || 6} cảnh`);
  const speak = langPicker(DIALOGUE_LANGS, cleanLang(localGet("dialogueLang")) || "Vietnamese");
  const subSaved = cleanLang(localGet("subtitleLang"));
  const sub = langPicker(SUBTITLE_LANGS, subSaved || "Vietnamese");
  const about = h("p", { class: "help" });
  const goLabel = h("span", {});
  const picker = h("div", { class: "choice", role: "radiogroup", "aria-label": "Cách làm phim từ video mẫu" });
  const paint = () => {
    picker.replaceChildren(...MODES.map(([k, ic, t, desc]) => h("button", { type: "button", class: mode === k ? "on" : "", role: "radio", "aria-checked": String(mode === k),
      onclick: () => { mode = k; localSet("anaFilmMode", k); paint(); } }, icon(ic), h("b", {}, t), h("span", {}, desc))));
    idea.placeholder = PH[mode];
    if (ideaLabel && ideaLabel.firstChild && ideaLabel.firstChild.nodeType === 3) ideaLabel.firstChild.textContent = mode === "same" ? "Ghi chú về nhân vật mới " : "Ý tưởng phim mới ";
    if (hint) hint.hidden = mode === "same";
    about.textContent = mode === "same"
      ? "Storyboard làm lại đúng từng cảnh của video mẫu (truyện, hành động, góc máy, bối cảnh, màu, nhịp) nhưng nhân vật là người mới do app tạo. Mỗi cảnh có thời lượng riêng (có thể chỉ 2s); clip render ngắn nhất model cho phép rồi agent dựng cắt đúng độ dài, tổng bằng video gốc."
      : "Storyboard mới lấy nhịp truyện, góc máy, màu (style) của video mẫu nhưng câu chuyện, nhân vật là mới. Mỗi cảnh có thời lượng riêng để tổng bằng video gốc; agent dựng phim cắt theo cùng nhịp.";
    goLabel.textContent = mode === "same" ? "Tạo phim giống video mẫu" : "Tạo phim theo phong cách này";
  };
  const go = async (e) => {
    const a = speak.read(), b = sub.read();
    if (!a.ok) { toast("Nhân vật nói: " + a.msg, "error"); return; }
    if (!b.ok) { toast("Ngôn ngữ phụ đề: " + b.msg, "error"); return; }
    const btn = e.currentTarget; btn.disabled = true;
    localSet("dialogueLang", a.value); localSet("subtitleLang", b.value);
    try { startJob(await api("POST", `/api/analyses/${d.id}/film`, { idea: idea.value, scenes: +n.value || null, language: b.value, dialogue_language: a.value, mode })); toast(mode === "same" ? "Claude đang viết storyboard giống video mẫu, nhân vật mới…" : "Claude đang viết storyboard theo phong cách video mẫu…", "", 6000); }
    catch (err) { toast(err.message, "error"); }
    btn.disabled = false;
  };
  paint();
  return h("section", { class: "card ana-card ana-film" }, h("div", { class: "card-head" }, h("h3", {}, "Tạo phim từ video mẫu")),
    h("div", { class: "card-body stack" },
      picker,
      ideaField, hint,
      h("div", { class: "grid2" }, h("div", { class: "field" }, h("span", { class: "lbl" }, "Số cảnh ", h("span", { class: "hint" }, "· ", reset)), n), field("Khung hình", h("input", { type: "text", value: (d.measured?.aspect === "9:16" ? "Dọc 9:16" : "Ngang 16:9") + " (như video mẫu)", disabled: true }))),
      timing,
      h("div", { class: "grid2" }, field("Nhân vật nói", speak.el), field("Ngôn ngữ phụ đề", sub.el)),
      about,
      h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "btn primary", disabled: jobRunning(), onclick: go }, icon("sparkle"), goLabel))));
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
  const autoQC = h("input", { type: "checkbox", checked: s.auto_qc !== false });
  const qcEnv = S.env?.qc || {};
  const qcModel = qcEnv.model
    ? h("p", { class: "help" }, "✓ Đã có model nhận dạng giọng nói: QC kiểm tra cả lời thoại (đúng câu, đúng ngôn ngữ, không lặp).")
    : h("div", { class: "row" }, h("span", { class: "help", style: { flex: "1" } }, qcEnv.whisper
      ? "Chưa có model nhận dạng giọng nói: QC chỉ xem hình và mức âm thanh, chưa kiểm tra lời thoại."
      : "Chưa có whisper-cli (chạy “brew install whisper-cpp” trong Terminal) nên QC chưa kiểm tra được lời thoại."),
      qcEnv.whisper ? h("button", { class: "btn sm", onclick: async (e) => {
        try { startJob(await api("POST", "/api/qc/model")); e.target.disabled = true; } catch (err) { toast(err.message, "error"); }
      } }, icon("download"), "Tải model (~550 MB)") : null);
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
      h("section", { class: "settings-sec" }, h("h3", {}, "AI QC clip"),
        h("div", { class: "stack" },
          h("label", { class: "check" }, autoQC, h("span", {}, "Tự QC mỗi clip vừa render",
            h("span", { class: "sub" }, "AI xem hình (so với keyframe, nhân vật, lỗi hình, chữ trên hình) và nghe âm thanh, lời thoại. Miễn phí, chỉ đánh dấu — không tự render lại."))),
          qcModel)),
      (() => {
        const fbPage = h("input", { type: "text", id: "set-fb-page", value: s.fb_page_id || "", placeholder: "Ví dụ: 1029384756123" });
        const fbToken = h("input", { type: "password", id: "set-fb-token", placeholder: s.fb_access_token_set ? `Đã lưu (${s.fb_access_token_hint}) · nhập để thay` : "EAAB…", autocomplete: "off" });
        const ytClient = h("input", { type: "text", id: "set-yt-client", value: s.yt_client_id || "", placeholder: "xxxx.apps.googleusercontent.com" });
        const ytSecret = h("input", { type: "password", id: "set-yt-secret", placeholder: s.yt_client_secret_set ? `Đã lưu (${s.yt_client_secret_hint}) · nhập để thay` : "GOCSPX-…", autocomplete: "off" });
        const ytRefresh = h("input", { type: "password", id: "set-yt-refresh", placeholder: s.yt_refresh_token_set ? `Đã lưu (${s.yt_refresh_token_hint}) · nhập để thay` : "1//0g…", autocomplete: "off" });
        const ytPriv = h("select", { id: "set-yt-priv", value: s.yt_privacy || "public" },
          h("option", { value: "public" }, "Công khai (Public)"),
          h("option", { value: "unlisted" }, "Không công khai (Unlisted)"),
          h("option", { value: "private" }, "Riêng tư (Private)"));
        const autoPub = h("input", { type: "checkbox", id: "set-auto-pub", checked: !!s.auto_publish });
        const curPlats = s.auto_publish_platforms || [];
        const platFb = h("input", { type: "checkbox", id: "set-pub-fb", checked: curPlats.includes("facebook_reel") });
        const platYt = h("input", { type: "checkbox", id: "set-pub-yt", checked: curPlats.includes("youtube") });
        return h("section", { class: "settings-sec" }, h("h3", {}, "Đăng tự động Facebook Reels & YouTube"),
          h("div", { class: "stack" },
            h("label", { class: "check" }, autoPub, h("span", {}, "Tự động đăng video sau khi tạo phim xong (Autopilot / Make)")),
            h("div", { class: "row" },
              h("label", { class: "check" }, platFb, h("span", {}, "Facebook Reels")),
              h("label", { class: "check" }, platYt, h("span", {}, "YouTube (Shorts / Video)"))),
            h("div", { class: "grid2" },
              field("Facebook Page ID", fbPage),
              field("Facebook Page Access Token", fbToken)),
            h("div", { class: "grid3" },
              field("YouTube OAuth Client ID", ytClient),
              field("YouTube Client Secret", ytSecret),
              field("YouTube Refresh Token", ytRefresh)),
            field("Chế độ hiển thị YouTube mặc định", ytPriv),
            h("p", { class: "help" }, "Token được lưu bảo mật ở ~/.config/filmmaker/settings.json (chỉ tài khoản máy bạn đọc được).")));
      })(),
      h("section", { class: "settings-sec" }, h("h3", {}, "Giao diện"), h("div", { class: "grid2" }, field("Chế độ màu", theme)))),
    buttons: [{ label: "Huỷ" }, { label: "Lưu", cls: "primary", onclick: async (c) => {
      try {
        const pubPlats = [];
        if (document.getElementById("set-pub-fb")?.checked) pubPlats.push("facebook_reel");
        if (document.getElementById("set-pub-yt")?.checked) pubPlats.push("youtube");
        S.settings = await api("PUT", "/api/settings", { planner: planner.value, profile: profile.value.trim(), pause_min: +pmin.value, pause_max: +pmax.value, retries: +retries.value,
          flow_ui: flowUi.checked, flow_ui_max_credits: +maxCredits.value || 0, flow_ui_window: flowWin.value,
          backup_remote: remote.value.trim(), auto_backup: autoBackup.checked, auto_qc: autoQC.checked,
          fb_page_id: document.getElementById("set-fb-page")?.value.trim() || "",
          fb_access_token: document.getElementById("set-fb-token")?.value.trim() || undefined,
          yt_client_id: document.getElementById("set-yt-client")?.value.trim() || "",
          yt_client_secret: document.getElementById("set-yt-secret")?.value.trim() || undefined,
          yt_refresh_token: document.getElementById("set-yt-refresh")?.value.trim() || undefined,
          yt_privacy: document.getElementById("set-yt-priv")?.value || "public",
          auto_publish: !!document.getElementById("set-auto-pub")?.checked,
          auto_publish_platforms: pubPlats,
          anthropic_api_key: key.value.trim() || undefined, clear_anthropic_key: clearKey });
        S.env = await api("GET", "/api/env"); if (S.cur) loadPublish(true); renderTopbar(); c(); toast("Đã lưu cài đặt", "ok");
      } catch (e) { toast(e.message, "error"); }
    } }],
  });
}

async function openCredits() {
  const body = h("div", {}, h("p", { class: "row" }, h("span", { class: "spinner" }), "Đang mở Flow ngầm để đọc số dư… (khoảng 30–60 giây)"));
  modal({ title: "Credit Google Flow", body });
  try {
    const r = await api("POST", "/api/env/credits");
    if (r.ok && r.balance && S.env) { S.env.credit_balance = r.balance; renderCreditButton(); }
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
