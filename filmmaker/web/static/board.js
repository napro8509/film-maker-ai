"use strict";
/* Film Maker — tab "Bảng" (storyboard board: nhân vật → keyframe → video).
   Loaded BEFORE app.js and runs nothing at load time: every reference to app.js globals
   (S, h, zone, changed, flushSave, runAction, …) only happens when a function is called. */

const B = {
  pid: null,
  loading: false, loaded: false, loadError: null,
  cards: {},             // last loaded/saved cards dict (serialized keys → {x, y}); fallback for lookups
  pos: new WeakMap(),    // character object → {x, y} (survives renames)
  spos: new Map(),       // "kf:<sid>" / "vid:<sid>" → {x, y}
  videos: new Set(),     // keyframe scenes whose video card the user showed
  view: null,            // {x, y, zoom} | null (null → "Vừa màn hình" on first draw)
  h: {},                 // measured card heights by DOM key
  sel: null,             // selected link id
  selChar: null,         // character object of a selected "c<i>>…" link: the index alone shifts when characters change
  saveAfterMount: false, // bShowVideo: PUT once bAfterMount has placed the new card
  drag: null,            // current pointer interaction
  sig: "",               // card keys of the rendered board (structure)
  savedJson: null, savedKeys: null, pending: null, timer: null, inflight: false,
  mc: null,              // boardModel() cached for the current task (see bModel)
  keyBound: false,
};

const BOARD_ZOOMS = [0.25, 0.33, 0.5, 0.67, 0.8, 1, 1.25, 1.5, 2];
const BOARD_ORIGIN = 32, BOARD_GAP_COL = 120, BOARD_GAP_ROW = 24, BOARD_GRID = 8, BOARD_HEAD = 36;
const BOARD_W_CHAR = 220;
const SVG_NS = "http://www.w3.org/2000/svg";

/* createElementNS helper: h() uses createElement, which cannot make SVG nodes. */
function svgEl(tag, attrs, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) el.setAttribute(k, v === true ? "" : v);
  for (const c of kids.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

/* ---------- small helpers ---------- */

const bKey = (n) => String(n ?? "").trim().toLowerCase(); // same as app.js charKey
function bScenePortrait() { return S.cur.storyboard.aspect === "9:16"; }
function bSceneW() { return bScenePortrait() ? 192 : 256; }
function bCardW(key) { return key.startsWith("char:") ? BOARD_W_CHAR : bSceneW(); }
function bFallbackH(key) { return key.startsWith("char:") ? 300 : bScenePortrait() ? 430 : 260; }
function bCardH(key) { return B.h[key] || bFallbackH(key); }
function bSnap(v) { return Math.round(v / BOARD_GRID) * BOARD_GRID; }
function bClampZoom(z) { return Math.min(2, Math.max(0.25, z)); }
function bChars() { return S.cur.storyboard.characters || []; }
function bScenes() { return S.cur.storyboard.scenes || []; }
function bMode(sc) { return sc.mode || "keyframe"; }
function bCharIndex(name) { const k = bKey(name); return k ? bChars().findIndex((c) => bKey(c.name) === k) : -1; }
/* Like bCharIndex, but a name renamed since the last save (still the old name in scenes[].characters until
   app.js save() passes the new one on) resolves through S.charSaved, so arrows do not flicker to "unknown". */
function bResolve(name) {
  const i = bCharIndex(name);
  if (i >= 0) return i;
  const k = bKey(name), saved = S.charSaved;
  if (!k || !saved || typeof saved.get !== "function") return -1;
  return bChars().findIndex((c) => bKey(saved.get(c)) === k);
}
function bModeLabel(mode) { return MODES.find((m) => m[0] === mode)?.[1] || mode; }
function bTyping() {
  const a = document.activeElement;
  return !!(a && a.matches && a.matches("input, textarea, select, [contenteditable], [contenteditable] *"));
}

/* Layout key used in ui.json: char:<tên>, or char:#<i> for empty/duplicate names (G5).
   A name changed since the last successful save keeps its saved key, so ui.json matches storyboard.yaml until the save goes through. */
function bSerialCharKey(c, i, problems) {
  if (problems.has(i) || !String(c.name || "").trim()) return `char:#${i}`;
  const saved = S.charSaved && typeof S.charSaved.get === "function" ? S.charSaved.get(c) : null;
  const n = String(saved || "").trim() || c.name.trim();
  return `char:${n}`;
}

function bCharPos(c, i, problems = charNameProblems(S.cur.storyboard)) {
  if (B.pos.has(c)) return B.pos.get(c);
  const p = B.cards[bSerialCharKey(c, i, problems)];
  if (p) B.pos.set(c, { x: p.x, y: p.y });
  return B.pos.get(c) || null;
}
function bPos(key) {
  if (key.startsWith("char:#")) { const i = +key.slice(6), c = bChars()[i]; return c ? bCharPos(c, i) : null; }
  return B.spos.get(key) || null;
}
function bSetPos(key, p) {
  if (key.startsWith("char:#")) { const c = bChars()[+key.slice(6)]; if (c) B.pos.set(c, p); }
  else B.spos.set(key, p);
}

/* ---------- model ---------- */

/* Characters of a scene for the arrows. Explicit list wins; otherwise the server's auto set;
   storyboard invalid (scene_state empty) → only the raw explicit list, no auto arrows. */
function sceneLinks(sc) {
  const st = S.cur.scene_state?.[sc.id];
  let raw = [], explicit = false;
  if (Array.isArray(sc.characters)) { raw = sc.characters; explicit = true; }
  else if (st && Array.isArray(st.characters)) raw = st.characters;
  const names = [], idx = [], unknown = [], seen = new Set();
  for (const n of raw) {
    if (!bKey(n)) continue;
    const i = bResolve(n), k = i >= 0 ? "#" + i : bKey(n);
    if (seen.has(k)) continue;
    seen.add(k);
    if (i >= 0) { names.push(bChars()[i].name); idx.push(i); } else unknown.push(n);
  }
  return { names, idx, explicit, unknown };
}

/* Is the character's picture sent with this scene, or only its description? From scene_state.refs. */
function linkKind(name, sid, ci = bResolve(name)) {
  const sb = S.cur.storyboard;
  if (sb.use_character_refs === false) return { desc: true, reason: "đang tắt gửi ảnh nhân vật" };
  const st = S.cur.scene_state?.[sid];
  if (!st) return { desc: false, reason: "" };
  const refs = st.refs || [];
  const c = bChars()[ci];
  const old = c && S.charSaved && typeof S.charSaved.get === "function" ? S.charSaved.get(c) : null;
  if (refs.some((r) => bKey(r) === bKey(name) || (old && bKey(r) === bKey(old)))) return { desc: false, reason: "" };
  if (c && charImageUrl(c) && refs.length >= 3) return { desc: true, reason: `đã đủ 3 ảnh: ${refs.join(", ")}` };
  return { desc: true, reason: "chưa có ảnh" };
}

function bVideoShown(sc) {
  if (bMode(sc) !== "keyframe") return true;
  const st = S.cur.scene_state?.[sc.id] || {};
  return B.videos.has(sc.id) || !!st.clip_url || st.status === "interrupted" || bSceneRunning(sc, "vid");
}

function bSceneRunning(sc, type) {
  if (currentSceneInJob() !== sc.id) return false;
  const kfJob = S.job?.kind === "keyframes";
  return type === "kf" ? (kfJob || bMode(sc) === "keyframe" && !S.cur.scene_state?.[sc.id]?.keyframe_url) : !kfJob;
}

/* Names the running portraits job is drawing ("Tạo ảnh nhân vật (A, B)"); no list → every character without a picture. */
function bCharRunning(c) {
  const j = S.job;
  if (!j || j.status !== "running" || j.kind !== "portraits" || j.project !== S.cur.id) return false;
  const m = /\(([^()]*)\)\s*$/.exec(j.title || "");
  if (m) return m[1].split(", ").some((n) => n === c.name);
  return !charImageUrl(c);
}
function bCharFailed(c) {
  const j = S.job;
  if (!j || j.kind !== "portraits" || j.project !== S.cur.id || j.status === "running") return null;
  return j.result?.failed?.[c.name] || null;
}

function boardModel() {
  const sb = S.cur.storyboard;
  const problems = charNameProblems(sb);
  const cards = [], links = [];
  bChars().forEach((c, i) => cards.push({ key: `char:#${i}`, type: "char", c, i, bad: problems.has(i) }));
  bScenes().forEach((sc, row) => {
    const mode = bMode(sc);
    if (mode === "keyframe") cards.push({ key: `kf:${sc.id}`, type: "kf", sc, row });
    if (bVideoShown(sc)) cards.push({ key: `vid:${sc.id}`, type: "vid", sc, row });
  });
  for (const sc of bScenes()) {
    const mode = bMode(sc);
    if (mode === "i2v") continue;
    const L = sceneLinks(sc), to = mode === "keyframe" ? `kf:${sc.id}` : `vid:${sc.id}`;
    for (const ci of L.idx) {
      const c = bChars()[ci];
      const k = linkKind(c.name, sc.id, ci);
      links.push({ id: `c${ci}>${to}`, kind: "char", from: `char:#${ci}`, to, ci, sid: sc.id, name: c.name, explicit: L.explicit, ...k });
    }
    if (mode === "keyframe" && bVideoShown(sc)) links.push({ id: `k>${sc.id}`, kind: "kfvid", from: `kf:${sc.id}`, to: `vid:${sc.id}`, sid: sc.id });
  }
  return { cards, links, problems };
}

/* boardModel() for the current task: zones of one refresh (ports, arrows, selection) share one computation.
   Dropped at the end of the task and by bRefresh()/renderBoard() after an edit. */
function bModel() {
  if (!B.mc) { B.mc = boardModel(); queueMicrotask(() => { B.mc = null; }); }
  return B.mc;
}

/* ---------- layout ---------- */

function bIntersects(a, b, m = 12) {
  return a.x < b.x + b.w + m && a.x + a.w + m > b.x && a.y < b.y + b.h + m && a.y + a.h + m > b.y;
}
/* Pure: push rect down below whatever it overlaps (never moves the others). */
function placeFree(rect, rects) {
  const r = { ...rect };
  for (let guard = 0; guard < 500; guard++) {
    const hit = rects.find((o) => bIntersects(r, o));
    if (!hit) return r;
    r.y = hit.y + hit.h + BOARD_GAP_ROW;
  }
  return r;
}

/* Default grid slots (ui-proposal §7): characters | keyframe | video, one row per scene. */
function bGridSlots(cards) {
  const slots = {};
  const xKf = BOARD_ORIGIN + BOARD_W_CHAR + BOARD_GAP_COL, xVid = xKf + bSceneW() + BOARD_GAP_COL;
  let y = BOARD_ORIGIN;
  for (const cd of cards.filter((c) => c.type === "char")) { slots[cd.key] = { x: BOARD_ORIGIN, y }; y += bCardH(cd.key) + BOARD_GAP_ROW; }
  y = BOARD_ORIGIN;
  for (const sc of bScenes()) {
    const kf = `kf:${sc.id}`, vid = `vid:${sc.id}`, isKf = bMode(sc) === "keyframe";
    if (isKf) slots[kf] = { x: xKf, y };
    slots[vid] = { x: xVid, y };
    y += Math.max(isKf ? bCardH(kf) : 0, B.h[vid] || (isKf ? bCardH(kf) : bCardH(vid))) + BOARD_GAP_ROW;
  }
  return slots;
}

/* Give a position to every card that has none (all=true: to every card). Returns true if anything moved. */
function autoLayout(all = false) {
  const { cards } = boardModel();
  if (all) { B.spos.clear(); B.pos = new WeakMap(); B.cards = {}; }
  const slots = bGridSlots(cards);
  const rect = (key, p) => ({ x: p.x, y: p.y, w: bCardW(key), h: bCardH(key) });
  const placed = [];
  for (const cd of cards) { const p = bPos(cd.key); if (p) placed.push(rect(cd.key, p)); }
  let moved = false;
  for (const cd of cards) {
    if (bPos(cd.key)) continue;
    let slot = slots[cd.key];
    if (cd.type === "vid" && bMode(cd.sc) === "keyframe") { // next to its keyframe when that one was moved
      const kp = bPos(`kf:${cd.sc.id}`);
      if (kp) slot = { x: kp.x + bSceneW() + BOARD_GAP_COL, y: kp.y };
    }
    const r = placeFree(rect(cd.key, slot || { x: BOARD_ORIGIN, y: BOARD_ORIGIN }), placed);
    bSetPos(cd.key, { x: r.x, y: r.y });
    placed.push(r);
    moved = true;
  }
  return moved;
}

/* ---------- saving the layout (PUT /board) ---------- */

function bSerialize() {
  const sb = S.cur.storyboard, problems = charNameProblems(sb);
  const cards = {};
  bChars().forEach((c, i) => { const p = bCharPos(c, i, problems); if (p) cards[bSerialCharKey(c, i, problems)] = { x: p.x, y: p.y }; });
  const kfIds = new Set();
  for (const sc of bScenes()) {
    if (bMode(sc) === "keyframe") { kfIds.add(sc.id); const p = B.spos.get(`kf:${sc.id}`); if (p) cards[`kf:${sc.id}`] = { x: p.x, y: p.y }; }
    if (bVideoShown(sc)) { const p = B.spos.get(`vid:${sc.id}`); if (p) cards[`vid:${sc.id}`] = { x: p.x, y: p.y }; }
  }
  const videos = [...B.videos].filter((id) => kfIds.has(id));
  const view = B.view ? { x: Math.round(B.view.x), y: Math.round(B.view.y), zoom: Math.round(B.view.zoom * 1000) / 1000 } : null;
  return { cards, videos, view };
}
const bKeysOf = (body) => Object.keys(body.cards).sort().join("\n") + "|" + body.videos.join(",");

/* Debounced (400 ms) after a drop, zoom, re-layout, show/hide video. The body is taken now, so a project switch
   during the delay still saves the right project. */
function saveBoard(delay = 400) {
  if (!S.cur || B.pid !== S.cur.id || !B.loaded) return;
  if (B.loadError) { // never overwrite the saved layout we could not read (code-review #2)
    if (!B.loadErrorToast) { B.loadErrorToast = true; toast("Chưa tải được bố cục — tạm không lưu vị trí. Bấm “Thử lại” trên bảng.", "error", 6000); }
    return;
  }
  B.pending = { pid: B.pid, body: bSerialize() };
  clearTimeout(B.timer);
  B.timer = setTimeout(() => { B.timer = null; bFlushBoard(); }, delay);
}
/* One PUT at a time: a later layout waits in B.pending, so an old, slow request can never land after a newer one. */
async function bFlushBoard() {
  if (B.inflight) return; // sent when the running request ends
  const p = B.pending;
  B.pending = null;
  if (!p) return;
  const json = JSON.stringify(p.body);
  if (p.pid === B.pid && json === B.savedJson) return;
  B.inflight = true;
  try {
    await api("PUT", `/api/projects/${encodeURIComponent(p.pid)}/board`, p.body);
    if (p.pid === B.pid) { B.savedJson = json; B.savedKeys = bKeysOf(p.body); B.cards = p.body.cards; }
  } catch (e) {
    toast("Không lưu được bố cục bảng: " + e.message, "error");
  } finally {
    B.inflight = false;
    if (B.pending && !B.timer) bFlushBoard();
  }
}

/* Hook called by app.js after every successful storyboard save: renamed characters / new or removed scenes
   change the layout keys, so save them (positions follow the objects). */
function boardAfterSave() {
  if (!S.cur || B.pid !== S.cur.id || !B.loaded) return;
  if (bKeysOf(bSerialize()) !== B.savedKeys) saveBoard();
}

async function bLoad(pid) {
  let lay = null, err = null;
  try { lay = await api("GET", `/api/projects/${encodeURIComponent(pid)}/board`); }
  catch (e) { err = e.message || "lỗi mạng"; }
  if (B.pid !== pid) return;
  // Failed: draw the default layout but keep B.loadError set, so saveBoard() never PUTs it over the saved one.
  B.loadError = err; B.loadErrorToast = false;
  if (err) toast("Không tải được bố cục bảng — tạm dùng bố cục mặc định, chưa lưu vị trí", "error");
  lay = lay && typeof lay === "object" ? lay : {};
  B.cards = lay.cards && typeof lay.cards === "object" ? lay.cards : {};
  for (const [k, p] of Object.entries(B.cards)) if (!k.startsWith("char:") && p) B.spos.set(k, { x: +p.x, y: +p.y });
  B.videos = new Set(Array.isArray(lay.videos) ? lay.videos : []);
  B.view = lay.view && typeof lay.view === "object" ? { x: +lay.view.x || 0, y: +lay.view.y || 0, zoom: bClampZoom(+lay.view.zoom || 1) } : null;
  B.savedKeys = Object.keys(B.cards).sort().join("\n") + "|" + [...B.videos].join(",");
  B.loading = false; B.loaded = true;
  if (S.cur && S.cur.id === pid && S.tab === "board") rerenderBoard();
}

/* "Thử lại" on the load-error banner: read the layout again (positions moved meanwhile are replaced by the saved ones). */
function bRetryLoad() {
  if (!S.cur || B.pid !== S.cur.id) return;
  B.pid = null; // renderBoard() resets the board state and calls bLoad again
  rerenderBoard();
}

/* ---------- arrows: edit scenes[].characters ---------- */

/* add/remove: a character name. Writes the scene's explicit list (storyboard order); [] stays [] (AC28). */
async function editSceneChars(sc, add, remove) {
  const sb = S.cur.storyboard, pid = S.cur.id;
  if (charsLocked(sb)) { toast("Đổi tên nhân vật trống/trùng trước khi nối"); return false; }
  if (add && S.cur.errors?.length) { toast("Storyboard đang lỗi — chỉ xoá được mũi tên. Sửa lỗi ở khung phía trên trước.", "error"); return false; }
  if (bMode(sc) === "i2v") { toast(`Cảnh ${sc.id} kiểu “${bModeLabel("i2v")}” không dùng ảnh nhân vật`); return false; }
  let base;
  if (Array.isArray(sc.characters)) base = sc.characters;
  else {
    // First edit on this scene: start from the server's auto set, which must be fresh (design-review #2).
    await flushSave();
    if (!S.cur || S.cur.id !== pid || S.cur.storyboard !== sb || !sb.scenes.includes(sc)) return false;
    if (saveFailed()) { toast("Chưa lưu được storyboard nên chưa biết nhân vật tự động của cảnh: " + (S.saveError || "thử lại sau"), "error"); return false; }
    if (S.cur.errors?.length) { toast("Storyboard đang lỗi — sửa lỗi trước khi nối mũi tên", "error"); return false; }
    const st = S.cur.scene_state?.[sc.id];
    if (!st || !Array.isArray(st.characters)) { toast(`Chưa có danh sách nhân vật của cảnh ${sc.id} từ máy chủ — thử lại sau giây lát`, "error"); return false; }
    if (Array.isArray(sc.characters)) base = sc.characters; // set meanwhile (another edit)
    else base = st.characters.filter((n) => String(n || "").trim());
  }
  const cur = (n) => { const i = bResolve(n); return i >= 0 ? bKey(sb.characters[i].name) : bKey(n); };
  const set = new Set(base.map(cur));
  if (add) set.add(bKey(add));
  if (remove) set.delete(bKey(remove));
  const unknown = base.filter((n) => bKey(n) && bResolve(n) < 0); // kept; the user removes them with "Bỏ khỏi cảnh"
  sc.characters = [...sb.characters.map((c) => c.name).filter((n) => set.has(bKey(n))), ...unknown];
  changed();
  bRefresh();
  return true;
}

function bRemoveUnknown(sc, name) {
  if (!Array.isArray(sc.characters)) return;
  sc.characters = sc.characters.filter((n) => bKey(n) !== bKey(name));
  changed();
  bRefresh();
}

/* ---------- render ---------- */

function renderBoard() {
  if (!B.keyBound) { document.addEventListener("keydown", bOnKey); B.keyBound = true; } // once (design-review #4)
  if (B.pid !== S.cur.id) {
    clearTimeout(B.timer); B.timer = null;
    if (B.pending) bFlushBoard();
    Object.assign(B, { pid: S.cur.id, loading: true, loaded: false, loadError: null, cards: {}, pos: new WeakMap(), spos: new Map(),
      videos: new Set(), view: null, h: {}, sel: null, selChar: null, saveAfterMount: false, drag: null, sig: "", savedJson: null, savedKeys: null });
    bLoad(S.cur.id);
  }
  const sb = S.cur.storyboard;
  sb.characters = sb.characters || [];
  B.mc = null;
  const root = h("section", { class: "board" + (bScenePortrait() ? " portrait" : "") },
    bToolbar(),
    zone("board-notes", bNotes));
  // Custom properties: Object.assign(el.style, …) in h() ignores them (QA bug #1).
  root.style.setProperty("--bc-w-char", BOARD_W_CHAR + "px");
  root.style.setProperty("--bc-w-scene", bSceneW() + "px");
  const vp = h("div", { class: "board-viewport", tabindex: "-1" });
  root.append(vp);
  if (B.loading) {
    vp.append(h("div", { class: "loading" }, h("span", { class: "spinner" }), "Đang tải bảng…"));
    return root;
  }
  const model = boardModel();
  B.sig = model.cards.map((c) => c.key).join(",");
  const world = h("div", { class: "board-world" });
  world.append(zone("board-links", () => bLinksSvg()));
  const unplaced = [];
  for (const cd of model.cards) {
    const el = bCard(cd);
    const p = bPos(cd.key);
    if (p) { el.style.left = p.x + "px"; el.style.top = p.y + "px"; }
    else { unplaced.push(cd.key); el.style.visibility = "hidden"; }
    world.append(el);
  }
  bEmpties(world, model);
  world.append(svgEl("svg", { class: "board-draft", "aria-hidden": "true" }, svgEl("path", { class: "board-link draft", d: "" })));
  vp.append(world);
  bApplyView(vp);
  vp.addEventListener("pointerdown", bOnPointerDown);
  vp.addEventListener("pointermove", bOnPointerMove);
  vp.addEventListener("pointerup", bOnPointerUp);
  vp.addEventListener("pointercancel", bOnPointerUp);
  vp.addEventListener("wheel", bOnWheel, { passive: false });
  vp.addEventListener("click", bOnClick);
  vp.addEventListener("keydown", bOnLinkKey);
  requestAnimationFrame(() => bAfterMount(root, unplaced));
  return root;
}

/* Re-draw only the board (structure changed: a card appeared/disappeared). Keeps view and positions. */
function rerenderBoard() {
  const old = document.querySelector("#main .board");
  if (!old || S.tab !== "board") return;
  old.replaceWith(renderBoard());
}

/* Measure real heights (cards are attached but hidden), place the cards without a position, then fit if needed. */
function bAfterMount(root, unplaced) {
  if (!root.isConnected) return;
  root.querySelectorAll(".board-card").forEach((el) => { if (el.offsetHeight) B.h[el.dataset.key] = el.offsetHeight; });
  if (unplaced.length && autoLayout(false)) {
    for (const key of unplaced) {
      const el = root.querySelector(`.board-card[data-key="${CSS.escape(key)}"]`), p = bPos(key);
      if (el && p) { el.style.left = p.x + "px"; el.style.top = p.y + "px"; el.style.visibility = ""; }
    }
    refreshZone("board-links");
  }
  bPlaceEmpties(root);
  if (!B.view) bFit(false);
  if (B.saveAfterMount) { B.saveAfterMount = false; saveBoard(); }
  refreshZones(); // zone functions also sync the card frame flags (running/failed/invalid) now that cards are attached
}

function bApplyView(vp = document.querySelector(".board-viewport")) {
  if (!vp) return;
  const v = B.view || { x: 0, y: 0, zoom: 1 };
  // Set the values directly, not as inherited custom properties: changing a variable on the viewport makes
  // WebKit (the desktop app) restyle every card on each wheel/pan event (~230 ms with 20 scenes).
  const world = vp.querySelector(".board-world");
  if (world) world.style.transform = `translate(${v.x}px, ${v.y}px) scale(${v.zoom})`;
  vp.style.backgroundSize = `${16 * v.zoom}px ${16 * v.zoom}px`;
  vp.style.backgroundPosition = `${v.x}px ${v.y}px`;
  const lbl = document.querySelector(".board-zoom-val");
  if (lbl) lbl.textContent = Math.round(v.zoom * 100) + "%";
}

function bToolbar() {
  const zoomPct = Math.round((B.view?.zoom || 1) * 100) + "%";
  return h("div", { class: "board-toolbar", role: "toolbar", "aria-label": "Công cụ bảng" },
    h("button", { class: "btn sm board-add", title: "Thêm nhân vật", "aria-label": "Thêm nhân vật", onclick: bAddCharacter }, icon("plus"), h("span", { class: "lbl-text" }, "Nhân vật")),
    h("button", { class: "btn sm ghost", title: "Tự xếp lại", "aria-label": "Tự xếp lại", onclick: bRelayout }, icon("layout"), h("span", { class: "lbl-text" }, "Tự xếp lại")),
    h("span", { class: "board-sep", "aria-hidden": "true" }),
    h("div", { class: "board-zoom", role: "group", "aria-label": "Thu phóng" },
      h("button", { class: "btn sm ghost icon", title: "Thu nhỏ", "aria-label": "Thu nhỏ", onclick: () => bZoomStep(-1) }, icon("minus")),
      h("button", { class: "board-zoom-val mono", "aria-live": "polite", title: "Về 100%", onclick: () => bZoomTo(1) }, zoomPct),
      h("button", { class: "btn sm ghost icon", title: "Phóng to", "aria-label": "Phóng to", onclick: () => bZoomStep(1) }, icon("plus")),
      h("button", { class: "btn sm ghost", title: "Vừa màn hình", "aria-label": "Vừa màn hình", onclick: () => bFit(true) }, icon("fit"), h("span", { class: "lbl-text" }, "Vừa màn hình"))),
    h("span", { class: "spacer" }),
    zone("board-sel", bSelSlot),
    h("div", { class: "board-legend", "aria-label": "Chú thích" }, bLegendItems().map(([cls, label]) => h("span", { class: "board-legend-item" }, bLegendSample(cls), label))),
    h("button", { class: "btn sm ghost board-legend-btn", "aria-haspopup": "menu", title: "Chú thích", onclick: (e) => popMenu(e.currentTarget,
      bLegendItems().map(([cls, label]) => ({ label, extra: bLegendSample(cls) }))) }, icon("info"), h("span", { class: "lbl-text" }, "Chú thích")));
}

function bLegendItems() {
  return [["explicit", "Đã chọn nhân vật"], ["auto", "Tự động theo tên trong prompt"], ["explicit desc", "Chỉ thêm mô tả, không gửi ảnh"], ["kfvid", "Keyframe → video"]];
}
function bLegendSample(cls) {
  const desc = cls.includes("desc");
  return svgEl("svg", { class: "board-legend-sample", viewBox: "0 0 28 10", width: 28, height: 10, "aria-hidden": "true" },
    svgEl("g", { class: "board-link " + cls },
      svgEl("path", { class: "line", d: desc ? "M1 5H19" : "M1 5H21" }),
      desc ? svgEl("path", { class: "head-open", d: "M19 1.5L26 5L19 8.5z" }) : svgEl("path", { class: "head", d: "M20 1L27 5L20 9z" })));
}

function bNotes() {
  const sb = S.cur.storyboard;
  const notes = [];
  if (B.loadError && !B.loading) notes.push(h("div", { class: "banner err flush", role: "alert" }, icon("alert"),
    h("div", { class: "b-body" }, `Không tải được bố cục bảng (${B.loadError}) — đang dùng bố cục mặc định, vị trí chưa được lưu.`),
    h("button", { class: "btn sm", onclick: bRetryLoad }, icon("refresh"), "Thử lại")));
  if (charsLocked(sb)) notes.push(h("div", { class: "banner warn flush" }, icon("alert"), h("div", { class: "b-body" }, "Có nhân vật tên trống/trùng — đổi tên trước khi nối mũi tên.")));
  if (S.cur.errors?.length) notes.push(h("div", { class: "banner warn flush" }, icon("alert"), h("div", { class: "b-body" }, "Storyboard đang lỗi — chỉ xoá được mũi tên, nút tạo bị tắt.")));
  if (sb.use_character_refs === false) notes.push(h("div", { class: "banner info flush" }, icon("info"),
    h("div", { class: "b-body" }, "Đang tắt gửi ảnh nhân vật — mũi tên chỉ thêm mô tả."),
    h("button", { class: "btn sm ghost", onclick: () => switchTab("story") }, "Bật lại")));
  const touch = (window.matchMedia && matchMedia("(pointer: coarse)").matches) || innerWidth <= 720;
  if (touch && !localGet("boardTouchHint")) notes.push(h("div", { class: "banner info flush" }, icon("info"),
    h("div", { class: "b-body" }, "Kéo nối cần chuột/trackpad. Trên màn hình cảm ứng hãy sửa nhân vật của cảnh ở tab Cảnh."),
    h("button", { class: "btn sm ghost icon", title: "Ẩn", "aria-label": "Ẩn gợi ý", onclick: () => { localSet("boardTouchHint", 1); refreshZone("board-notes"); } }, icon("x"))));
  return h("div", { class: "board-notes" }, notes);
}

/* The selected link, or null. A character link stays selected only while index ci still holds the same character
   object: deleting/adding/reordering characters shifts the indexes, and the selection must never jump (QA r2 #2). */
function bSelLink(links) {
  if (!B.sel) return null;
  const L = links.find((l) => l.id === B.sel);
  if (L && (L.kind !== "char" || (B.selChar && bChars()[L.ci] === B.selChar))) return L;
  B.sel = null; B.selChar = null;
  return null;
}

function bSelSlot() {
  const L = bSelLink(bModel().links);
  if (!L) { return h("div", { class: "board-sel hidden" }); }
  const why = bDeleteBlocked(L);
  return h("div", { class: "board-sel" },
    h("span", { class: "muted small" }, L.kind === "char" ? `${L.name} → ${L.sid}` : `Keyframe ${L.sid} → video`),
    h("button", { class: "btn sm danger", disabled: !!why, title: why || "Xoá mũi tên (Delete)", onclick: () => bDeleteSelected() }, icon("trash"), "Xoá mũi tên"));
}

function bDeleteBlocked(L) {
  if (L.kind === "char") return charsLocked(S.cur.storyboard) ? "Đổi tên nhân vật trống/trùng trước khi sửa mũi tên" : "";
  const sc = bScenes().find((s) => s.id === L.sid), st = S.cur.scene_state?.[L.sid] || {};
  if (st.clip_url) return "Cảnh đã có clip — không ẩn được card video";
  if (sc && bSceneRunning(sc, "vid")) return "Cảnh đang render — không ẩn được card video";
  if (st.status === "interrupted") return "Cảnh bị ngắt khi render — không ẩn được card video";
  return "";
}

async function bDeleteSelected() {
  const L = bSelLink(boardModel().links);
  if (!L) { bSelect(null); return; }
  const why = bDeleteBlocked(L);
  if (why) { toast(why); return; }
  const sc = bScenes().find((s) => s.id === L.sid);
  if (!sc) return;
  if (L.kind === "char") {
    if (await editSceneChars(sc, null, L.name)) { B.sel = null; B.selChar = null; bRefresh(); }
  } else {
    B.videos.delete(sc.id);
    B.sel = null;
    rerenderBoard();
    saveBoard();
  }
}

function bSelect(id) {
  const m = /^c(\d+)>/.exec(id || "");
  B.sel = id || null;
  B.selChar = m ? bChars()[+m[1]] || null : null;
  document.querySelectorAll(".board-links .board-link").forEach((g) => {
    const on = g.dataset.id === id;
    g.classList.toggle("selected", on);
    const line = g.querySelector(".line");
    if (line) line.setAttribute("marker-end", `url(#${bMarker(g.classList.contains("desc"), on)})`);
  });
  refreshZone("board-sel");
}

/* ---------- SVG arrows ---------- */

function bMarker(desc, selected) { return selected ? "bm-arrow-sel" : desc ? "bm-open" : "bm-arrow"; }

function bAnchor(key, side) {
  const p = bPos(key);
  if (!p) return null;
  return side === "out" ? { x: p.x + bCardW(key) + 1, y: p.y + BOARD_HEAD / 2 } : { x: p.x - 8, y: p.y + BOARD_HEAD / 2 };
}
function bCurve(a, b) {
  const dx = Math.max(48, Math.abs(b.x - a.x) / 2);
  return `M${a.x} ${a.y}C${a.x + dx} ${a.y} ${b.x - dx} ${b.y} ${b.x} ${b.y}`;
}

function bLinksSvg() {
  const model = bModel();
  // A card appeared/disappeared because of a job or a save (e.g. a render started): redraw the board once.
  const sig = model.cards.map((c) => c.key).join(",");
  if (B.sig && sig !== B.sig && !bTyping() && !B.drag && !B.requeued) {
    B.requeued = true;
    setTimeout(() => { B.requeued = false; rerenderBoard(); }, 0);
  }
  let minX = 0, minY = 0, maxX = 800, maxY = 600;
  for (const cd of model.cards) {
    const p = bPos(cd.key);
    if (!p) continue;
    minX = Math.min(minX, p.x); minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x + bCardW(cd.key)); maxY = Math.max(maxY, p.y + bCardH(cd.key));
  }
  const M = 1500, x0 = Math.floor(minX - M), y0 = Math.floor(minY - M), w = Math.ceil(maxX - minX + 2 * M), hgt = Math.ceil(maxY - minY + 2 * M);
  const marker = (id, d, cls) => svgEl("marker", { id, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 10, markerHeight: 10, markerUnits: "userSpaceOnUse", orient: "auto" },
    svgEl("path", { d, class: cls }));
  const svg = svgEl("svg", { class: "board-links", "aria-label": "Mũi tên nối", viewBox: `${x0} ${y0} ${w} ${hgt}`, width: w, height: hgt,
    style: `left:${x0}px;top:${y0}px` },
    svgEl("defs", {},
      marker("bm-arrow", "M0 0L10 5L0 10z", "mk-arrow"),
      marker("bm-arrow-sel", "M0 0L10 5L0 10z", "mk-sel"),
      marker("bm-open", "M1 1L9 5L1 9z", "mk-open")));
  bSelLink(model.links);
  for (const L of model.links) {
    const a = bAnchor(L.from, "out"), b = bAnchor(L.to, "in");
    if (!a || !b) continue;
    const d = bCurve(a, b), sel = B.sel === L.id;
    const cls = ["board-link", L.kind === "kfvid" ? "kfvid" : L.explicit ? "explicit" : "auto", L.desc ? "desc" : "", sel ? "selected" : ""].filter(Boolean).join(" ");
    const tip = L.kind === "kfvid" ? "Video tạo từ keyframe"
      : [`${L.name} → ${L.sid}`, L.explicit ? null : "Tự động theo tên trong prompt", L.desc ? `Chỉ thêm mô tả — ${L.reason}` : null].filter(Boolean).join("\n");
    const label = L.kind === "kfvid" ? `Mũi tên keyframe ${L.sid} → video` : `Mũi tên ${L.name} → ${L.sid}, ${L.explicit ? "đã chọn" : "tự động"}${L.desc ? ", chỉ mô tả" : ""}`;
    svg.append(svgEl("g", { class: cls, "data-id": L.id, "data-from": L.from, "data-to": L.to, tabindex: "0", role: "button", "aria-label": label },
      svgEl("title", {}, tip),
      svgEl("path", { class: "halo", d }),
      svgEl("path", { class: "hit", d }),
      svgEl("path", { class: "line", d, "marker-end": `url(#${bMarker(L.desc, sel)})` })));
  }
  return svg;
}

/* Card drag starts: move the arrows touching it into a small SVG of their own, so the big one stays untouched.
   Changing any path in the big SVG makes WebKit (the desktop app) repaint every arrow (~200 ms a frame
   with 20 scenes when they were dashed). The drop rebuilds "board-links" and removes this one. */
function bLiftLinks(key) {
  const base = document.getElementById("board-links");
  if (!base) return;
  const live = base.cloneNode(false);
  live.removeAttribute("id");
  live.removeAttribute("aria-label");
  live.setAttribute("aria-hidden", "true");
  live.classList.add("live");
  base.querySelectorAll(`.board-link[data-from="${CSS.escape(key)}"], .board-link[data-to="${CSS.escape(key)}"]`).forEach((g) => live.append(g));
  base.after(live);
}

/* While dragging a card: update only the paths touching it (no re-render). */
function bUpdateLinksFor(key) {
  document.querySelectorAll(`.board-links .board-link[data-from="${CSS.escape(key)}"], .board-links .board-link[data-to="${CSS.escape(key)}"]`).forEach((g) => {
    const a = bAnchor(g.dataset.from, "out"), b = bAnchor(g.dataset.to, "in");
    if (!a || !b) return;
    const d = bCurve(a, b);
    g.querySelectorAll("path").forEach((p) => p.setAttribute("d", d));
  });
}

/* ---------- cards ---------- */

function bCardShell(cd, label, headKids, bodyKids, ports) {
  const el = h("article", { class: `card board-card ${cd.type}`, "data-key": cd.key, tabindex: "0", role: "group", "aria-label": label },
    ports.in ? zone(`bc-pin-${cd.key}`, ports.in) : null,
    h("header", { class: "board-card-head", title: "Kéo để di chuyển" }, headKids),
    h("div", { class: "board-card-body stack tight" }, bodyKids),
    ports.out ? zone(`bc-pout-${cd.key}`, ports.out) : null);
  return el;
}

/* Card frame classes live outside the zones (the frame holds inputs): the badge zone keeps them in sync. */
function bFlags(key, flags) {
  const el = document.querySelector(`.board-card[data-key="${CSS.escape(key)}"]`);
  if (!el) return;
  for (const [k, v] of Object.entries(flags)) el.classList.toggle(k, !!v);
}

function bMoreBtn(items, label) {
  return h("button", { class: "btn sm ghost icon", title: "Thêm thao tác", "aria-label": label, "aria-haspopup": "menu",
    onclick: (e) => popMenu(e.currentTarget, items()) }, icon("more"));
}

function bLinkedIn(key) { return bModel().links.some((l) => l.to === key); }

function bCard(cd) {
  return cd.type === "char" ? bCharCard(cd) : cd.type === "kf" ? bKfCard(cd) : bVidCard(cd);
}

function bCharCard(cd) {
  const sb = S.cur.storyboard, c = cd.c, i = cd.i, key = cd.key;
  const z = (part) => `bc-${part}-${key}`;
  const nameInput = h("input", { type: "text", class: "board-name", value: c.name || "", placeholder: "Tên, ví dụ: Lan", "aria-label": "Tên nhân vật",
    "aria-invalid": cd.bad ? "true" : null,
    oninput: (e) => { c.name = e.target.value; changed(); bRefreshNames(); } });
  const fileInput = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", class: "hidden", onchange: async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f); fd.append("kind", "character");
    try {
      const r = await api("POST", `/api/projects/${S.cur.id}/upload`, fd, true);
      S.assets = await api("GET", `/api/projects/${S.cur.id}/assets`);
      c.image = r.path; changed(); bRefresh();
    } catch (err) { toast(err.message, "error"); }
  } });
  const el = bCardShell(cd, `Nhân vật ${c.name || "chưa đặt tên"}`,
    [icon("users"), nameInput,
      zone(z("badge"), () => {
        const bad = charNameProblems(sb).has(i);
        const inp = document.querySelector(`.board-card[data-key="${CSS.escape(key)}"] .board-name`);
        if (inp) { if (bad) inp.setAttribute("aria-invalid", "true"); else inp.removeAttribute("aria-invalid"); }
        const running = bCharRunning(c), failed = bCharFailed(c);
        bFlags(key, { "is-invalid": bad, "is-running": running, "is-failed": !!failed && !running });
        return running ? h("span", { class: "badge mini running" }, "Đang tạo…") : failed ? h("span", { class: "badge mini failed" }, "Lỗi") : h("span");
      }),
      h("span", { class: "spacer" }),
      bMoreBtn(() => [
        { label: "Mở trong tab Nhân vật", icon: "users", onclick: () => switchTab("story") },
        { label: "Chọn ảnh có sẵn…", icon: "image", onclick: () => bPickImage(c) },
        "-",
        { label: "Xoá nhân vật", icon: "trash", danger: true, onclick: async () => { if (await removeCharacter(i)) renderMain(); } },
      ], `Thao tác nhân vật ${c.name || ""}`)],
    [zone(z("media"), () => {
      const url = charImageUrl(c), cs = S.cur.characters_state?.[i];
      const missing = !!(c.image && cs && cs.name === c.name && cs.image_missing);
      const box = h("div", { class: "media board-char-media" });
      if (bCharRunning(c)) box.append(h("div", { class: "ph" }, h("span", { class: "spinner" }), h("span", {}, "Đang tạo ảnh…")));
      else if (url) box.append(h("img", { src: url, alt: c.name || "", decoding: "async", onclick: () => lightbox(url) }));
      else box.append(h("div", { class: "ph" }, icon("image"), h("span", {}, "Chưa có ảnh")));
      if (missing) box.append(h("span", { class: "tag warn" }, "Không thấy file ảnh"));
      return box;
    }),
    h("textarea", { class: "board-desc", rows: 2, value: c.description || "", "aria-label": "Mô tả ngoại hình",
      placeholder: "a Vietnamese woman in her late 20s, shoulder-length black hair, cream linen shirt…",
      oninput: (e) => { c.description = e.target.value; changed(); refreshZone(z("actions")); } }),
    zone(z("msg"), () => {
      const cs = S.cur.characters_state?.[i], kids = [];
      if (charNameProblems(sb).has(i)) kids.push(h("div", { class: "scene-msg warn" }, "Tên trống/trùng — đổi tên trước khi nối"));
      if (c.image && cs && cs.name === c.name && cs.image_missing) kids.push(h("div", { class: "scene-msg warn" }, "Không thấy file ảnh nhân vật — tạo lại hoặc tải ảnh lên."));
      const failed = !bCharRunning(c) && bCharFailed(c);
      if (failed) kids.push(h("div", { class: "scene-msg err" }, failed));
      return h("div", { class: "stack tight" + (kids.length ? "" : " hidden") }, kids);
    }),
    zone(z("actions"), () => {
      const busy = jobRunning(), url = charImageUrl(c), canMake = !!(String(c.name || "").trim() && String(c.description || "").trim());
      const failed = !bCharRunning(c) && bCharFailed(c);
      const errs = S.cur.errors?.length;
      const title = busy ? "Đang có việc chạy" : errs ? "Storyboard đang lỗi" : canMake ? "Ảnh trên Flow miễn phí" : "Cần nhập tên và mô tả trước";
      return h("div", { class: "board-card-actions row" },
        h("button", { class: "btn sm", disabled: busy || !canMake || !!errs, title, onclick: () => makePortraits([c.name], !!url) },
          icon(url || failed ? "refresh" : "image"), failed ? "Thử lại" : url ? "Tạo lại" : "Tạo ảnh", FREE()),
        h("button", { class: "btn sm ghost icon", title: "Tải ảnh lên", "aria-label": "Tải ảnh lên", onclick: () => fileInput.click() }, icon("upload")),
        fileInput);
    })],
    { out: () => {
      const locked = charsLocked(sb), errs = S.cur.errors?.length;
      const why = charNameProblems(sb).has(i) ? "Tên trống/trùng — đổi tên trước khi nối" : locked ? "Đổi tên nhân vật trống/trùng trước khi nối"
        : errs ? "Storyboard đang lỗi — chỉ xoá được mũi tên" : "";
      return h("span", { class: "board-port out" + (why ? " disabled" : ""), "data-port": "out", title: why || "Kéo để nối vào keyframe của cảnh" });
    } });
  if (cd.bad) el.classList.add("is-invalid");
  return el;
}

function bPickImage(c) {
  const imgs = S.assets.images || [];
  const isAsset = !c.image || imgs.some((a) => a.path === c.image);
  const sel = h("select", { value: c.image || "", "aria-label": "Ảnh nhân vật" },
    h("option", { value: "" }, "— không dùng ảnh —"),
    isAsset ? null : h("option", { value: c.image }, c.image),
    imgs.map((a) => h("option", { value: a.path }, a.path.replace("assets/", ""))));
  modal({ title: `Ảnh của ${c.name || "nhân vật"}`, body: h("div", { class: "stack" }, field("Ảnh có sẵn trong dự án", sel),
    imgs.length ? null : h("p", { class: "help" }, "Dự án chưa có ảnh nào. Dùng nút Tải ảnh lên trên card.")),
    buttons: [{ label: "Huỷ" }, { label: "Dùng ảnh này", cls: "primary", onclick: (close) => { c.image = sel.value || undefined; changed(); close(); bRefresh(); } }] });
}

/* Notes about the characters sent with a scene (kf card, or t2v video card). */
function bSceneCharMsgs(sc) {
  const kids = [];
  if (bMode(sc) === "i2v") return kids;
  const L = sceneLinks(sc), st = S.cur.scene_state?.[sc.id];
  for (const n of L.unknown) kids.push(h("div", { class: "scene-msg err" }, h("div", {}, `Không có nhân vật “${n}”`),
    h("div", { class: "row", style: { marginTop: "6px" } }, h("button", { class: "btn sm", onclick: () => bRemoveUnknown(sc, n) }, icon("x"), "Bỏ khỏi cảnh"))));
  if (st && S.cur.storyboard.use_character_refs !== false) {
    const refs = st.refs || [];
    const linked = L.idx.map((i) => bChars()[i]).filter(Boolean);
    const withImg = linked.filter((c) => charImageUrl(c));
    if (withImg.length > 3 && refs.length) kids.push(h("div", { class: "scene-msg warn" }, `Chỉ gửi ảnh của 3 nhân vật: ${refs.join(", ")}`));
    for (const c of linked) if (!charImageUrl(c)) kids.push(h("div", { class: "scene-msg warn" }, `${c.name} chưa có ảnh tham chiếu — chỉ dùng mô tả`));
  }
  return kids;
}

function bKfCard(cd) {
  const sc = cd.sc, key = cd.key, sid = sc.id;
  const z = (part) => `bc-${part}-${key}`;
  const st = () => S.cur.scene_state?.[sc.id] || {};
  // Keyframe errors live in the last keyframes job result (failed[sid]); scene_state.error is for video renders,
  // shown on the keyframe card only when the scene still has no usable keyframe.
  const failedKf = () => {
    const j = S.job;
    if (j && j.kind === "keyframes" && j.project === S.cur.id && j.status !== "running" && j.result?.failed?.[sid]) return j.result.failed[sid];
    const s = st();
    return s.status === "failed" && s.error && (!s.keyframe_url || s.keyframe_stale) ? s.error : null;
  };
  return bCardShell(cd, `Keyframe cảnh ${sid}`,
    [icon("image"), h("b", { class: "mono" }, sid), h("span", { class: "muted small" }, "Keyframe"),
      zone(z("badge"), () => {
        const s = st(), running = bSceneRunning(sc, "kf"), failed = !running && !!failedKf();
        bFlags(key, { "is-running": running, "is-failed": failed });
        const b = running ? badge("running", "Đang tạo…") : failed ? badge("failed", "Lỗi") : !s.keyframe_url ? badge("todo", "Chưa có ảnh")
          : s.keyframe_stale ? badge("changed", "Ảnh cũ — cần tạo lại") : badge("done", "Xong");
        b.classList.add("mini");
        return b;
      }),
      h("span", { class: "spacer" }),
      bMoreBtn(() => [
        { label: "Sửa prompt…", icon: "edit", onclick: () => bEditPrompt(sc, key) },
        { label: "Xem ảnh lớn", icon: "eye", disabled: !st().keyframe_url, onclick: () => st().keyframe_url && lightbox(st().keyframe_url) },
        { label: "Mở trong tab Cảnh", icon: "video", onclick: () => switchTab("scenes") },
      ], `Thao tác keyframe ${sid}`)],
    [zone(z("media"), () => {
      const s = st(), box = h("div", { class: "media" + (bScenePortrait() ? " portrait" : "") });
      if (bSceneRunning(sc, "kf") && !s.keyframe_url) box.append(h("div", { class: "ph" }, h("span", { class: "spinner" }), h("span", {}, "Đang tạo keyframe…")));
      else if (s.keyframe_url) {
        box.append(h("img", { src: s.keyframe_url, alt: "keyframe " + sid, decoding: "async", onclick: () => lightbox(s.keyframe_url) }));
        if (s.keyframe_stale) box.append(h("span", { class: "tag warn", title: "Prompt, nhân vật nối vào hoặc ảnh nhân vật đã đổi từ khi tạo ảnh này" }, "Ảnh cũ · cần tạo lại"));
      } else box.append(h("div", { class: "ph" }, icon("image"), h("span", {}, "Bấm “Tạo keyframe” để xem trước (miễn phí)")));
      return box;
    }),
    zone(z("sum"), () => { const t = sc.image_prompt || sc.prompt || ""; return h("p", { class: "board-sum", title: t }, t || h("span", { class: "muted" }, "(chưa có prompt)")); }),
    zone(z("msg"), () => {
      const kids = bSceneCharMsgs(sc);
      if (failedKf() && !bSceneRunning(sc, "kf")) kids.unshift(h("div", { class: "scene-msg err" }, failedKf()));
      return h("div", { class: "stack tight" + (kids.length ? "" : " hidden") }, kids);
    }),
    zone(z("actions"), () => {
      const s = st(), busy = jobRunning(), errs = S.cur.errors?.length, hasKf = !!s.keyframe_url;
      const failed = !!failedKf() && !bSceneRunning(sc, "kf");
      return h("div", { class: "board-card-actions row" },
        h("button", { class: "btn sm", disabled: busy || !!errs, title: busy ? "Đang có việc chạy" : errs ? "Storyboard đang lỗi" : "Ảnh trên Flow miễn phí",
          onclick: () => runAction("keyframes", { only: [sid], force_keyframes: hasKf ? [sid] : [] }) },
          icon(hasKf || failed ? "refresh" : "image"), failed ? "Thử lại" : hasKf ? "Tạo lại" : "Tạo keyframe", FREE()),
        bVideoShown(sc) ? null : h("button", { class: "btn sm ghost", disabled: !!errs, title: errs ? "Storyboard đang lỗi — sửa lỗi trước" : "Hiện card video của cảnh này",
          onclick: () => bShowVideo(sc) }, icon("arrowRight"), "Video"));
    })],
    {
      in: () => h("span", { class: "board-port in" + (bLinkedIn(key) ? " linked" : ""), "data-port": "in", title: "Nhân vật nối vào cảnh này" }),
      out: () => {
        const why = S.cur.errors?.length && !bVideoShown(sc) ? "Storyboard đang lỗi — sửa lỗi trước khi tạo card video" : "";
        return h("span", { class: "board-port out" + (why ? " disabled" : ""), "data-port": "out", title: why || "Kéo ra chỗ trống để tạo card video" });
      },
    });
}

function bVidCard(cd) {
  const sc = cd.sc, key = cd.key, sid = sc.id, mode = bMode(sc);
  const z = (part) => `bc-${part}-${key}`;
  const st = () => S.cur.scene_state?.[sc.id] || {};
  return bCardShell(cd, `Video cảnh ${sid}`,
    [icon("video"), h("b", { class: "mono" }, sid), h("span", { class: "muted small" }, "Video"),
      zone(z("badge"), () => {
        const k = sceneStatusKey(sc), s = st();
        bFlags(key, { "is-running": k === "running", "is-failed": k === "failed" || (s.status === "failed" && !!s.error) });
        const b = badge(STATUS[k] ? k : "todo");
        b.classList.add("mini");
        return b;
      }),
      h("span", { class: "spacer" }),
      bMoreBtn(() => {
        const why = mode !== "keyframe" ? "Cảnh này luôn có card video" : bDeleteBlocked({ kind: "kfvid", sid });
        return [
          { label: "Mở trong tab Cảnh", icon: "video", onclick: () => switchTab("scenes") },
          { label: "Ẩn card video", icon: "eye", disabled: !!why, extra: why ? h("span", { class: "board-menu-why" }, why.replace(/ — .*$/, "")) : null,
            onclick: () => { B.videos.delete(sid); if (B.sel === `k>${sid}`) B.sel = null; rerenderBoard(); saveBoard(); } },
        ];
      }, `Thao tác video ${sid}`)],
    [zone(z("meta"), () => {
      const s = st(), model = s.model || sc.model || S.cur.storyboard.model;
      const parts = [model ? modelLabel(model) : "Model mặc định", sceneDur(sc) + "s"];
      if (mode === "t2v") parts.push(bModeLabel("t2v"));
      if (mode === "i2v") parts.push("Ảnh của bạn");
      return h("div", { class: "small muted board-meta" }, parts.join(" · "));
    }),
    zone(z("media"), () => {
      const el = renderSceneMedia(sc, { kfHint: "Bấm “Tạo keyframe” trên card keyframe để xem trước (miễn phí)", lazy: true });
      if (!st().clip_url) el.classList.add("board-vid-pending");
      return el;
    }),
    mode === "i2v" ? h("div", { class: "small muted" }, "Cảnh i2v không dùng ảnh nhân vật") : null,
    zone(z("msg"), () => { const kids = mode === "t2v" ? bSceneCharMsgs(sc) : []; return h("div", { class: "stack tight" + (kids.length ? "" : " hidden") }, kids); }),
    zone(z("actions"), () => h("div", { class: "board-card-actions" },
      renderSceneActions(sc, { keyframe: false, retry: true, blocked: S.cur.errors?.length ? "Storyboard đang lỗi — sửa lỗi ở khung phía trên trước" : "" })))],
    { in: mode === "i2v" ? null : () => h("span", { class: "board-port in" + (bLinkedIn(key) ? " linked" : ""), "data-port": "in",
      title: mode === "keyframe" ? "Video tạo từ keyframe của cảnh" : "Nhân vật nối vào cảnh này" }) });
}

function bEditPrompt(sc, key) {
  const z = `bc-sum-${key}`;
  const ta = (k, label, ph, rows = 3) => field(label, h("textarea", { rows, value: sc[k] || "", placeholder: ph,
    oninput: (e) => { sc[k] = e.target.value; changed(); refreshZone(z); } }), "nên viết tiếng Anh");
  modal({ title: `Cảnh ${sc.id}`, wide: true,
    body: h("div", { class: "stack" },
      ta("image_prompt", "Ảnh mở đầu", "Mô tả khung hình đầu tiên như một bức ảnh: chủ thể, ai đứng đâu, bối cảnh…"),
      ta("prompt", "Hành động", "Một hành động, ví dụ: Lan lifts the glass and smiles…"),
      field("Phụ đề / lời dẫn", h("input", { type: "text", value: sc.narration || "", placeholder: "Không bắt buộc",
        oninput: (e) => { sc.narration = e.target.value; changed(); } })),
      h("p", { class: "help" }, "Tự lưu khi bạn gõ. Sửa prompt có thể làm keyframe đã có thành “Ảnh cũ”.")),
    buttons: [{ label: "Xong", cls: "primary" }] });
}

/* Empty columns: "add a character" / "no scenes yet" placeholders. */
function bEmpties(world, model) {
  if (!model.cards.some((c) => c.type === "char")) world.append(h("div", { class: "board-empty empty-inline", "data-empty": "char" },
    icon("users"), "Thêm nhân vật để nối vào cảnh", h("button", { class: "btn sm", onclick: bAddCharacter }, icon("plus"), "Nhân vật")));
  if (!bScenes().length) world.append(h("div", { class: "board-empty empty-inline", "data-empty": "scene" },
    icon("video"), "Chưa có cảnh — thêm ở tab Cảnh", h("button", { class: "btn sm", onclick: () => switchTab("scenes") }, "Mở tab Cảnh")));
}
function bPlaceEmpties(root) {
  const xKf = BOARD_ORIGIN + BOARD_W_CHAR + BOARD_GAP_COL;
  root.querySelectorAll(".board-empty").forEach((el) => {
    el.style.left = (el.dataset.empty === "char" ? BOARD_ORIGIN : xKf) + "px";
    el.style.top = BOARD_ORIGIN + "px";
  });
}

/* Re-render status parts (zones) and resync the frame flags. */
function bRefresh() { B.mc = null; refreshZones(); }

/* Typing a character name: only what depends on names (character cards' badge/msg/actions/port, arrows, notes,
   selection). Scene cards catch up on the next save (applyMeta → refreshZones). */
function bRefreshNames() {
  B.mc = null;
  for (const id of [...S.zones.keys()]) {
    if (/^bc-(badge|msg|actions|pout)-char:/.test(id) || id === "board-links" || id === "board-notes" || id === "board-sel") refreshZone(id);
  }
}

/* ---------- toolbar actions ---------- */

async function bRelayout() {
  if (!(await confirmModal("Tự xếp lại bảng?", "Mọi card về lưới mặc định. Vị trí bạn đã kéo sẽ mất.", "Xếp lại"))) return;
  autoLayout(true);
  B.view = null;
  rerenderBoard();
  // bAfterMount fits the view; save once the measured layout is known
  setTimeout(() => saveBoard(0), 60);
}

function bShowVideo(sc) {
  B.videos.add(sc.id);
  B.saveAfterMount = true; // the new card gets its position in bAfterMount (next frame): save after that (QA r2 #3)
  rerenderBoard();
}

function bAddCharacter() {
  const sb = S.cur.storyboard;
  sb.characters = sb.characters || [];
  const name = h("input", { type: "text", placeholder: "Tên, ví dụ: Lan", "aria-label": "Tên nhân vật" });
  const desc = h("textarea", { rows: 3, placeholder: "a Vietnamese woman in her late 20s, shoulder-length black hair, cream linen shirt…", "aria-label": "Mô tả ngoại hình" });
  const msg = h("div", { class: "scene-msg err hidden", role: "alert" });
  const check = () => {
    const v = name.value.trim();
    const err = !v ? "Nhập tên nhân vật." : sb.characters.some((c) => bKey(c.name) === bKey(v)) ? `Đã có nhân vật “${v}” (tên không phân biệt hoa thường).` : "";
    msg.textContent = err; msg.classList.toggle("hidden", !err);
    if (err) name.setAttribute("aria-invalid", "true"); else name.removeAttribute("aria-invalid");
    return !err;
  };
  const go = (close) => {
    if (!check()) { name.focus(); return; }
    const c = { name: name.value.trim(), description: desc.value.trim() };
    const problems = charNameProblems(sb);
    let bottom = BOARD_ORIGIN - BOARD_GAP_ROW, placed = [];
    sb.characters.forEach((o, i) => { const p = bCharPos(o, i, problems); if (p) { bottom = Math.max(bottom, p.y + bCardH(`char:#${i}`)); } });
    for (const cd of boardModel().cards) { const p = bPos(cd.key); if (p) placed.push({ x: p.x, y: p.y, w: bCardW(cd.key), h: bCardH(cd.key) }); }
    const r = placeFree({ x: BOARD_ORIGIN, y: bottom + BOARD_GAP_ROW, w: BOARD_W_CHAR, h: bFallbackH("char:") }, placed);
    sb.characters.push(c);
    B.pos.set(c, { x: r.x, y: r.y });
    changed();
    close();
    rerenderBoard();
    bScrollTo(`char:#${sb.characters.length - 1}`);
    saveBoard();
    setTimeout(() => document.querySelector(`.board-card[data-key="char:#${sb.characters.length - 1}"] .board-desc`)?.focus(), 30);
  };
  name.addEventListener("input", () => { if (!msg.classList.contains("hidden")) check(); });
  name.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); desc.focus(); } });
  modal({ title: "Thêm nhân vật", body: h("div", { class: "stack" }, field("Tên", name, "bắt buộc, không trùng"), field("Mô tả ngoại hình", desc, "tiếng Anh"), msg,
    h("p", { class: "help" }, "Mô tả một lần, rồi tạo hoặc tải ảnh lên trên card. Kéo chấm tròn bên phải card vào keyframe để dùng nhân vật cho cảnh đó.")),
    buttons: [{ label: "Huỷ" }, { label: "Thêm", cls: "primary", onclick: go }] });
}

/* ---------- view: pan / zoom / fit ---------- */

function bViewport() { return document.querySelector("#main .board-viewport"); }

function bZoomAt(z, cx, cy) {
  const vp = bViewport();
  if (!vp) return;
  const v = B.view || { x: 0, y: 0, zoom: 1 };
  z = bClampZoom(z);
  if (cx == null) { cx = vp.clientWidth / 2; cy = vp.clientHeight / 2; }
  const wx = (cx - v.x) / v.zoom, wy = (cy - v.y) / v.zoom;
  B.view = { x: cx - wx * z, y: cy - wy * z, zoom: z };
  bApplyView(vp);
}
function bZoomTo(z) { bZoomAt(z); saveBoard(); }
function bZoomStep(dir) {
  const cur = B.view?.zoom || 1;
  const next = dir > 0 ? BOARD_ZOOMS.find((s) => s > cur + 0.001) : [...BOARD_ZOOMS].reverse().find((s) => s < cur - 0.001);
  bZoomTo(next ?? (dir > 0 ? 2 : 0.25));
}

/* Zoom so every card fits with a 32px margin, at most 100%. */
function bFit(save = true) {
  const vp = bViewport();
  if (!vp || !vp.clientWidth) return;
  const rects = [...vp.querySelectorAll(".board-card, .board-empty")].filter((el) => el.style.visibility !== "hidden").map((el) => ({
    x: parseFloat(el.style.left) || 0, y: parseFloat(el.style.top) || 0, w: el.offsetWidth, h: el.offsetHeight }));
  if (!rects.length) { B.view = { x: 0, y: 0, zoom: 1 }; bApplyView(vp); return; }
  const minX = Math.min(...rects.map((r) => r.x)), minY = Math.min(...rects.map((r) => r.y));
  const maxX = Math.max(...rects.map((r) => r.x + r.w)), maxY = Math.max(...rects.map((r) => r.y + r.h));
  const bw = maxX - minX, bh = maxY - minY, vw = vp.clientWidth, vh = vp.clientHeight;
  const z = bClampZoom(Math.min(1, (vw - 64) / bw, (vh - 64) / bh));
  B.view = { x: (vw - bw * z) / 2 - minX * z, y: Math.max(32, (vh - bh * z) / 2) - minY * z, zoom: z };
  bApplyView(vp);
  if (save) saveBoard();
}

function bScrollTo(key) {
  const vp = bViewport(), p = bPos(key);
  if (!vp || !p) return;
  const v = B.view || { x: 0, y: 0, zoom: 1 };
  const sx = p.x * v.zoom + v.x, sy = p.y * v.zoom + v.y, w = bCardW(key) * v.zoom, hh = bCardH(key) * v.zoom;
  if (sx >= 0 && sy >= 0 && sx + w <= vp.clientWidth && sy + hh <= vp.clientHeight) return;
  B.view = { ...v, x: vp.clientWidth / 2 - (p.x + bCardW(key) / 2) * v.zoom, y: Math.min(32, vp.clientHeight / 2 - (p.y + bCardH(key) / 2) * v.zoom) };
  bApplyView(vp);
}

function bWorldPoint(e) {
  const vp = bViewport(), r = vp.getBoundingClientRect(), v = B.view || { x: 0, y: 0, zoom: 1 };
  return { x: (e.clientX - r.left - v.x) / v.zoom, y: (e.clientY - r.top - v.y) / v.zoom };
}

function bOnWheel(e) {
  e.preventDefault();
  const vp = e.currentTarget, r = vp.getBoundingClientRect(), v = B.view || { x: 0, y: 0, zoom: 1 };
  if (e.ctrlKey || e.metaKey) {
    const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
    bZoomAt(v.zoom * Math.exp(-dy * 0.0025), e.clientX - r.left, e.clientY - r.top);
  } else {
    const k = e.deltaMode === 1 ? 16 : 1;
    B.view = { ...v, x: v.x - e.deltaX * k, y: v.y - e.deltaY * k };
    bApplyView(vp);
  }
  saveBoard();
}

/* ---------- pointer: pan, drag card, drag arrow ---------- */

function bOnPointerDown(e) {
  if (e.button !== 0 || B.drag) return;
  const vp = e.currentTarget, t = e.target;
  const port = t.closest(".board-port");
  if (port) {
    e.preventDefault();
    const card = port.closest(".board-card");
    if (port.classList.contains("disabled")) { toast(port.title); return; }
    if (!port.classList.contains("out") || !card) return;
    B.drag = { kind: "link", from: card.dataset.key, id: e.pointerId, target: null, ok: false };
    vp.setPointerCapture(e.pointerId);
    vp.classList.add("is-dragging");
    port.classList.add("active");
    bDraftTo(bWorldPoint(e));
    return;
  }
  if (t.closest(".board-link")) return; // click selects
  const head = t.closest(".board-card-head");
  if (head) {
    // The name input fills most of a character header: an unfocused one also drags; a click without moving focuses it.
    const nameInput = t.matches("input.board-name") && document.activeElement !== t ? t : null;
    if (!nameInput && t.closest("input, textarea, select, button, a")) return;
    const card = head.closest(".board-card"), p = bPos(card.dataset.key);
    if (!p) return;
    if (nameInput) e.preventDefault();
    B.drag = { kind: "card", key: card.dataset.key, el: card, id: e.pointerId, sx: e.clientX, sy: e.clientY, ox: p.x, oy: p.y, moved: false, focus: nameInput };
    vp.setPointerCapture(e.pointerId);
    return;
  }
  if (t.closest(".board-card, .board-empty")) return;
  const v = B.view || { x: 0, y: 0, zoom: 1 };
  B.drag = { kind: "pan", id: e.pointerId, sx: e.clientX, sy: e.clientY, ox: v.x, oy: v.y, moved: false };
  vp.setPointerCapture(e.pointerId);
}

function bOnPointerMove(e) {
  const d = B.drag;
  if (!d || d.id !== e.pointerId) return;
  if (d.kind === "pan") {
    const dx = e.clientX - d.sx, dy = e.clientY - d.sy;
    if (!d.moved && Math.hypot(dx, dy) < 4) return;
    if (!d.moved) { d.moved = true; e.currentTarget.classList.add("is-panning"); }
    B.view = { ...(B.view || { zoom: 1 }), x: d.ox + dx, y: d.oy + dy };
    bApplyView(e.currentTarget);
  } else if (d.kind === "card") {
    const z = B.view?.zoom || 1, dx = (e.clientX - d.sx) / z, dy = (e.clientY - d.sy) / z;
    if (!d.moved && Math.hypot(e.clientX - d.sx, e.clientY - d.sy) < 4) return;
    if (!d.moved) { d.moved = true; d.el.classList.add("dragging"); e.currentTarget.classList.add("is-dragging"); bLiftLinks(d.key); }
    d.nx = bSnap(d.ox + dx); d.ny = bSnap(d.oy + dy);
    if (!d.raf) d.raf = requestAnimationFrame(() => {
      d.raf = 0;
      d.el.style.left = d.nx + "px"; d.el.style.top = d.ny + "px";
      bSetPos(d.key, { x: d.nx, y: d.ny });
      bUpdateLinksFor(d.key);
    });
  } else if (d.kind === "link") {
    bDraftTo(bWorldPoint(e));
    const under = document.elementFromPoint(e.clientX, e.clientY);
    const card = under && under.closest && under.closest(".board-card");
    const key = card && card.dataset.key !== d.from ? card.dataset.key : null;
    if (key !== d.target) {
      document.querySelectorAll(".board-card.drop-ok, .board-card.drop-no").forEach((el) => el.classList.remove("drop-ok", "drop-no"));
      d.target = key;
      if (card && key) { d.ok = !bDropProblem(d.from, key); card.classList.add(d.ok ? "drop-ok" : "drop-no"); }
    }
  }
}

function bOnPointerUp(e) {
  const d = B.drag;
  if (!d || d.id !== e.pointerId) return;
  const vp = e.currentTarget;
  B.drag = null;
  vp.classList.remove("is-dragging", "is-panning");
  try { vp.releasePointerCapture(e.pointerId); } catch { /* already released */ }
  if (d.kind === "pan") {
    if (d.moved) saveBoard();
    else if (B.sel) bSelect(null);
  } else if (d.kind === "card") {
    if (d.raf) cancelAnimationFrame(d.raf);
    if (d.moved) {
      d.el.classList.remove("dragging");
      bSetPos(d.key, { x: d.nx, y: d.ny });
      d.el.style.left = d.nx + "px"; d.el.style.top = d.ny + "px";
      refreshZone("board-links");
      document.querySelectorAll("#main svg.board-links.live").forEach((el) => el.remove());
      saveBoard(); // once, after the drop (AC22)
    } else if (d.focus) { d.focus.focus(); const n = d.focus.value.length; try { d.focus.setSelectionRange(n, n); } catch { /* ignore */ } }
  } else if (d.kind === "link") {
    document.querySelectorAll(".board-port.active").forEach((el) => el.classList.remove("active"));
    document.querySelectorAll(".board-card.drop-ok, .board-card.drop-no").forEach((el) => el.classList.remove("drop-ok", "drop-no"));
    bDraftTo(null);
    if (e.type === "pointercancel") return;
    const under = document.elementFromPoint(e.clientX, e.clientY);
    const card = under && under.closest && under.closest(".board-card");
    if (card && card.dataset.key === d.from) return; // back onto its own card: cancel, no toast
    bDrop(d.from, card ? card.dataset.key : null, bWorldPoint(e), !!(under && under.closest && under.closest(".board-viewport")));
  }
}

function bOnClick(e) {
  const g = e.target.closest && e.target.closest(".board-link");
  if (g && g.dataset.id) { bSelect(B.sel === g.dataset.id ? null : g.dataset.id); g.focus({ preventScroll: true }); }
}
function bOnLinkKey(e) {
  const g = e.target.closest && e.target.closest(".board-link");
  if (g && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); bSelect(g.dataset.id); }
}

/* Document-level keys (bound once): Delete/Backspace deletes the selected arrow, Esc deselects / cancels a draft. */
function bOnKey(e) {
  if (S.tab !== "board" || !S.cur) return;
  if (document.querySelector("#modal-root .overlay")) return;
  if (bTyping()) return;
  if ((e.key === "Delete" || e.key === "Backspace") && B.sel) { e.preventDefault(); bDeleteSelected(); }
  else if (e.key === "Escape") {
    if (B.drag && B.drag.kind === "link") {
      B.drag = null; bDraftTo(null);
      document.querySelectorAll(".board-port.active").forEach((el) => el.classList.remove("active"));
      document.querySelectorAll(".board-card.drop-ok, .board-card.drop-no").forEach((el) => el.classList.remove("drop-ok", "drop-no"));
    } else if (B.sel) bSelect(null);
  }
}

function bDraftTo(pt) {
  const path = document.querySelector("#main .board-draft path");
  if (!path) return;
  const d = B.drag;
  if (!pt || !d || d.kind !== "link") { path.setAttribute("d", ""); path.removeAttribute("marker-end"); return; }
  const a = bAnchor(d.from, "out");
  if (!a) return;
  path.setAttribute("d", bCurve(a, pt));
  path.setAttribute("marker-end", "url(#bm-arrow-sel)");
}

/* Why dropping an arrow from `from` onto card `to` is refused ("" = allowed). */
function bDropProblem(from, to) {
  const toType = to.split(":")[0], sid = to.slice(to.indexOf(":") + 1);
  const sc = toType === "char" ? null : bScenes().find((s) => s.id === sid);
  if (from.startsWith("char:#")) {
    const c = bChars()[+from.slice(6)];
    if (!c) return "Không thấy nhân vật";
    if (toType === "char") return "Chỉ nối nhân vật vào keyframe hoặc video của cảnh";
    if (!sc) return "Không thấy cảnh";
    if (bMode(sc) === "i2v") return `Cảnh ${sc.id} kiểu “${bModeLabel("i2v")}” không dùng ảnh nhân vật`;
    if (sceneLinks(sc).idx.includes(+from.slice(6))) return `${c.name} đã nối vào cảnh ${sc.id}`;
    return "";
  }
  const fromSid = from.slice(3);
  if (toType === "vid" && sid === fromSid) return "Keyframe đã nối với video của cảnh này";
  return "Keyframe chỉ nối với video của cùng cảnh";
}

async function bDrop(from, to, pt, inViewport) {
  if (from.startsWith("char:#")) {
    if (!to) return; // dropped on empty space: nothing to do
    const why = bDropProblem(from, to);
    if (why) { toast(why); return; }
    const c = bChars()[+from.slice(6)], sid = to.slice(to.indexOf(":") + 1), sc = bScenes().find((s) => s.id === sid);
    const viaVideo = to.startsWith("vid:") && bMode(sc) === "keyframe";
    if (await editSceneChars(sc, c.name) && viaVideo) toast(`Đã nối vào keyframe của cảnh ${sid} — video tạo từ keyframe`);
    return;
  }
  // from a keyframe
  const sid = from.slice(3), sc = bScenes().find((s) => s.id === sid);
  if (!sc) return;
  if (to) { const why = bDropProblem(from, to); if (why) toast(why); return; }
  if (!inViewport) return;
  if (S.cur.errors?.length) { toast("Storyboard đang lỗi — sửa lỗi trước khi tạo card video", "error"); return; }
  if (bVideoShown(sc)) { toast(`Card video của cảnh ${sid} đã có trên bảng`); return; }
  B.videos.add(sid);
  bSetPos(`vid:${sid}`, { x: bSnap(pt.x + 8), y: bSnap(pt.y - BOARD_HEAD / 2) });
  rerenderBoard();
  saveBoard();
}
