"use strict";
/* Film Maker — tab "Bảng" (ComfyUI Node-Graph Workflow Studio:
   FilmConfig / Character / Location → Keyframe (+ Continuity chain) → VideoSampler → SaveFilm).
   Loaded BEFORE app.js and runs nothing at load time: every reference to app.js globals
   (S, h, zone, changed, flushSave, runAction, …) only happens when a function is called. */

const B = {
  pid: null,
  loading: false, loaded: false, loadError: null,
  cards: {},             // last loaded/saved cards dict (serialized keys → {x, y})
  pos: new WeakMap(),    // character object → {x, y} (survives renames)
  lpos: new WeakMap(),   // location object → {x, y} (survives renames)
  spos: new Map(),       // "cfg:film" / "kf:<sid>" / "vid:<sid>" / "out:final" → {x, y}
  videos: new Set(),     // keyframe scenes whose video node is explicitly shown
  collapsed: new Set(),  // collapsed node keys (UI state)
  hideIdleVideos: false, // false = show full ComfyUI DAG end-to-end; true = hide unrendered keyframe video nodes
  showGroups: true,      // ComfyUI scene group frames
  showMinimap: true,     // ComfyUI bottom-right minimap
  showModelLinks: false, // optional MODEL bus wires from FilmConfig
  wireStyle: "spline",   // "spline" | "orthogonal" | "straight"
  view: null,            // {x, y, zoom} | null (null → "Vừa màn hình" on first draw)
  h: {},                 // measured node heights by DOM key
  slotY: {},             // measured socket Y offset inside each node: `${key}|${side}|${slot}` → number
  sel: null,             // selected link id
  selChar: null,         // character object of a selected "c<i>>…" link
  selLoc: null,          // location object of a selected "l<i>>…" link
  saveAfterMount: false,
  drag: null,            // current pointer interaction ("pan" | "card" | "link" | "minimap")
  sig: "",               // card keys of the rendered board (structure)
  savedJson: null, savedKeys: null, pending: null, timer: null, inflight: false,
  mc: null,              // boardModel() cached for the current microtask
  keyBound: false,
  paletteOpen: false,
  prefsLoaded: false,
};

const BOARD_ZOOMS = [0.25, 0.33, 0.5, 0.67, 0.8, 1, 1.25, 1.5, 2];
const BOARD_ORIGIN = 40, BOARD_GAP_COL = 112, BOARD_GAP_ROW = 28, BOARD_GRID = 8, BOARD_HEAD = 34;
const BOARD_W_CHAR = 236, BOARD_W_CFG = 244, BOARD_W_OUT = 256;
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

/* ---------- core pure helpers (shared with Node UI tests) ---------- */

const bKey = (n) => String(n ?? "").trim().toLowerCase(); // same as app.js charKey
function bScenePortrait() { return S.cur.storyboard.aspect === "9:16"; }
function bSceneW() { return bScenePortrait() ? 228 : 268; }
function bCardW(key) {
  if (key.startsWith("char:") || key.startsWith("loc:")) return BOARD_W_CHAR;
  if (key.startsWith("cfg:")) return BOARD_W_CFG;
  if (key.startsWith("out:")) return BOARD_W_OUT;
  return bSceneW();
}
function bFallbackH(key) {
  if (B.collapsed.has(key)) return 68;
  if (key.startsWith("cfg:")) return 236;
  if (key.startsWith("char:")) return 316;
  if (key.startsWith("loc:")) return 286;
  if (key.startsWith("out:")) return 230;
  return bScenePortrait() ? 450 : 310;
}
function bCardH(key) { return B.collapsed.has(key) ? (B.h[key] || 68) : (B.h[key] || bFallbackH(key)); }
function bSnap(v) { return Math.round(v / BOARD_GRID) * BOARD_GRID; }
function bClampZoom(z) { return Math.min(2, Math.max(0.25, z)); }
function bChars() { return S.cur.storyboard.characters || []; }
function bScenes() { return S.cur.storyboard.scenes || []; }
function bMode(sc) { return sc.mode || "keyframe"; }
function bCharIndex(name) { const k = bKey(name); return k ? bChars().findIndex((c) => bKey(c.name) === k) : -1; }
/* Like bCharIndex, but a name renamed since the last save resolves through S.charSaved. */
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
  return { desc: true, reason: "chưa có ảnh" };
}

function bVideoShown(sc) {
  if (bMode(sc) !== "keyframe") return true;
  if (!B.hideIdleVideos) return true;
  const st = S.cur.scene_state?.[sc.id] || {};
  return B.videos.has(sc.id) || !!st.clip_url || st.status === "interrupted" || bSceneRunning(sc, "vid");
}

/* Notes about the characters sent with a scene (kf card, or t2v video card). */
function bSceneCharMsgs(sc) {
  const kids = [];
  if (bMode(sc) === "i2v") return kids;
  const L = sceneLinks(sc), st = S.cur.scene_state?.[sc.id];
  for (const n of L.unknown) kids.push(h("div", { class: "scene-msg err" }, h("div", {}, `Không có nhân vật “${n}”`),
    h("div", { class: "row", style: { marginTop: "6px" } }, h("button", { class: "btn sm", onclick: () => bRemoveUnknown(sc, n) }, icon("x"), "Bỏ khỏi cảnh"))));
  if (st && S.cur.storyboard.use_character_refs !== false) {
    const linked = L.idx.map((i) => bChars()[i]).filter(Boolean);
    for (const c of linked) if (!charImageUrl(c)) kids.push(h("div", { class: "scene-msg warn" }, `${c.name} chưa có ảnh tham chiếu — chỉ dùng mô tả`));
  }
  return kids;
}

/* ---------- locations & continuity helpers on the board ---------- */

function bLocs() { return (S.cur.storyboard.locations || []); }
function bLocIndex(name) {
  const k = bKey(name);
  if (!k) return -1;
  const i = bLocs().findIndex((l) => l && !blankLocation(l) && bKey(l.name) === k);
  if (i >= 0) return i;
  const saved = S.locSaved;
  if (!saved || typeof saved.get !== "function") return -1;
  return bLocs().findIndex((l) => l && !blankLocation(l) && bKey(saved.get(l)) === k);
}

function bContinuityPrevSid(sceneIdx) {
  const sb = S.cur.storyboard, scenes = bScenes();
  if (sceneIdx <= 0 || sb.continuity === false) return null;
  const sc = scenes[sceneIdx];
  if (!sc || bMode(sc) !== "keyframe" || sc.continue_previous === false) return null;
  const li = bLocIndex(sc.location);
  if (li < 0) return null;
  const prev = scenes[sceneIdx - 1];
  if (!prev || bMode(prev) !== "keyframe") return null;
  if (bLocIndex(prev.location) !== li) return null;
  return prev.id;
}

function bLoadUiPrefsOnce() {
  if (B.prefsLoaded) return;
  B.prefsLoaded = true;
  const ws = localGet("boardWireStyle");
  if (ws === "spline" || ws === "orthogonal" || ws === "straight") B.wireStyle = ws;
  const sg = localGet("boardGroups");
  if (typeof sg === "boolean") B.showGroups = sg;
  const sm = localGet("boardMinimap");
  if (typeof sm === "boolean") B.showMinimap = sm;
  const ml = localGet("boardModelLinks");
  if (typeof ml === "boolean") B.showModelLinks = ml;
  const hi = localGet("boardHideIdleVid");
  if (typeof hi === "boolean") B.hideIdleVideos = hi;
}

/* ---------- layout keys & positions ---------- */

function bSerialCharKey(c, i, problems) {
  if (problems.has(i) || !String(c.name || "").trim()) return `char:#${i}`;
  const saved = S.charSaved && typeof S.charSaved.get === "function" ? S.charSaved.get(c) : null;
  const n = String(saved || "").trim() || c.name.trim();
  return `char:${n}`;
}

function bSerialLocKey(l, i, problems) {
  if (problems.has(i) || !String(l.name || "").trim()) return `loc:#${i}`;
  const saved = S.locSaved && typeof S.locSaved.get === "function" ? S.locSaved.get(l) : null;
  const n = String(saved || "").trim() || l.name.trim();
  return `loc:${n}`;
}

function bCharPos(c, i, problems = charNameProblems(S.cur.storyboard)) {
  if (B.pos.has(c)) return B.pos.get(c);
  const p = B.cards[bSerialCharKey(c, i, problems)] || B.cards[`char:#${i}`];
  if (p) B.pos.set(c, { x: p.x, y: p.y });
  return B.pos.get(c) || null;
}

function bLocPos(l, i, problems = locNameProblems(S.cur.storyboard)) {
  if (B.lpos.has(l)) return B.lpos.get(l);
  const p = B.cards[bSerialLocKey(l, i, problems)] || B.cards[`loc:#${i}`];
  if (p) B.lpos.set(l, { x: p.x, y: p.y });
  return B.lpos.get(l) || null;
}

function bPos(key) {
  if (key.startsWith("char:#")) { const i = +key.slice(6), c = bChars()[i]; return c ? bCharPos(c, i) : null; }
  if (key.startsWith("loc:#")) { const i = +key.slice(5), l = bLocs()[i]; return l ? bLocPos(l, i) : null; }
  return B.spos.get(key) || null;
}

function bSetPos(key, p) {
  if (key.startsWith("char:#")) { const c = bChars()[+key.slice(6)]; if (c) B.pos.set(c, p); }
  else if (key.startsWith("loc:#")) { const l = bLocs()[+key.slice(5)]; if (l) B.lpos.set(l, p); }
  else B.spos.set(key, p);
}

/* ---------- running / failed state ---------- */

function bSceneRunning(sc, type) {
  if (progOn()) return progRunning(`${type}:${sc.id}`);
  if (currentSceneInJob() !== sc.id) return false;
  const kfJob = S.job?.kind === "keyframes";
  return type === "kf" ? (kfJob || bMode(sc) === "keyframe" && !S.cur.scene_state?.[sc.id]?.keyframe_url) : !kfJob;
}

const PROG_FAILED_MSG = "Tạo lỗi — xem nhật ký job";

function bCharRunning(c) {
  if (progOn()) return progRunning(progCharKey(c));
  const j = S.job;
  if (!j || j.status !== "running" || j.kind !== "portraits" || j.project !== S.cur.id) return false;
  const m = /\(([^()]*)\)\s*$/.exec(j.title || "");
  if (m) return m[1].split(", ").some((n) => n === c.name);
  return !charImageUrl(c);
}
function bCharFailed(c) {
  const j = S.job;
  if (jobRunning() && progOn()) return progFailed(progCharKey(c)) ? (j.result?.failed?.[c.name] || PROG_FAILED_MSG) : null;
  if (!j || j.kind !== "portraits" || j.project !== S.cur.id || j.status === "running") return null;
  return j.result?.failed?.[c.name] || null;
}

function bLocRunning(l) {
  if (progOn()) return progRunning(progLocKey(l));
  const j = S.job;
  if (!j || j.status !== "running" || j.kind !== "locations" || j.project !== S.cur.id) return false;
  const m = /\(([^()]*)\)\s*$/.exec(j.title || "");
  if (m) return m[1].split(", ").some((n) => n === l.name);
  return !locImageUrl(l);
}
function bLocFailed(l) {
  const j = S.job;
  if (jobRunning() && progOn()) return progFailed(progLocKey(l)) ? (j.result?.failed?.[l.name] || PROG_FAILED_MSG) : null;
  if (!j || j.kind !== "locations" || j.project !== S.cur.id || j.status === "running") return null;
  return j.result?.failed?.[l.name] || null;
}

/* ---------- ComfyUI Node Graph Model ---------- */

function boardModel() {
  const sb = S.cur.storyboard;
  const problems = charNameProblems(sb);
  const locProblems = locNameProblems(sb);
  const cards = [], links = [];
  let nid = 1;

  cards.push({ key: "cfg:film", type: "cfg", nid: nid++ });
  bChars().forEach((c, i) => cards.push({ key: `char:#${i}`, type: "char", c, i, bad: problems.has(i), nid: nid++ }));
  bLocs().forEach((l, i) => {
    if (!blankLocation(l)) cards.push({ key: `loc:#${i}`, type: "loc", l, i, bad: locProblems.has(i), nid: nid++ });
  });

  bScenes().forEach((sc, row) => {
    const mode = bMode(sc);
    if (mode === "keyframe") cards.push({ key: `kf:${sc.id}`, type: "kf", sc, row, nid: nid++ });
    if (bVideoShown(sc)) cards.push({ key: `vid:${sc.id}`, type: "vid", sc, row, nid: nid++ });
  });

  const hasScenes = bScenes().length > 0;
  if (hasScenes) cards.push({ key: "out:final", type: "out", nid: nid++ });

  bScenes().forEach((sc, row) => {
    const mode = bMode(sc), vidShown = bVideoShown(sc);
    const primaryTarget = mode === "keyframe" ? `kf:${sc.id}` : `vid:${sc.id}`;

    if (B.showModelLinks) {
      links.push({ id: `m>${primaryTarget}`, kind: "model", dtype: "MODEL", from: "cfg:film", fromSlot: "MODEL", to: primaryTarget, toSlot: "model", sid: sc.id });
    }

    if (mode !== "i2v") {
      const L = sceneLinks(sc);
      for (const ci of L.idx) {
        const c = bChars()[ci];
        const k = linkKind(c.name, sc.id, ci);
        links.push({
          id: `c${ci}>${primaryTarget}`, kind: "char", dtype: "CHARACTER",
          from: `char:#${ci}`, fromSlot: "CHARACTER", to: primaryTarget, toSlot: "characters",
          ci, sid: sc.id, name: c.name, explicit: L.explicit, ...k,
        });
      }
      const li = bLocIndex(sc.location);
      if (li >= 0 && !blankLocation(bLocs()[li])) {
        const loc = bLocs()[li];
        links.push({
          id: `l${li}>${primaryTarget}`, kind: "loc", dtype: "LOCATION",
          from: `loc:#${li}`, fromSlot: "LOCATION", to: primaryTarget, toSlot: "location",
          li, sid: sc.id, name: loc.name,
        });
      }
    }

    if (mode === "keyframe") {
      const prevSid = bContinuityPrevSid(row);
      if (prevSid) {
        links.push({
          id: `cont:${prevSid}>${sc.id}`, kind: "cont", dtype: "IMAGE",
          from: `kf:${prevSid}`, fromSlot: "IMAGE", to: `kf:${sc.id}`, toSlot: "prev_image",
          fromSid: prevSid, sid: sc.id,
        });
      }
      if (vidShown) {
        links.push({
          id: `k>${sc.id}`, kind: "kfvid", dtype: "IMAGE",
          from: `kf:${sc.id}`, fromSlot: "IMAGE", to: `vid:${sc.id}`, toSlot: "start_image",
          sid: sc.id,
        });
      }
    }

    if (vidShown && hasScenes) {
      const st = S.cur.scene_state?.[sc.id] || {};
      links.push({
        id: `v>${sc.id}`, kind: "vidout", dtype: "VIDEO",
        from: `vid:${sc.id}`, fromSlot: "VIDEO", to: "out:final", toSlot: "clips",
        sid: sc.id, done: st.status === "done" && !!st.clip_url,
      });
    }
  });

  return { cards, links, problems, locProblems };
}

function bModel() {
  if (!B.mc) { B.mc = boardModel(); queueMicrotask(() => { B.mc = null; }); }
  return B.mc;
}

/* ---------- DAG Auto-Layout ---------- */

function bIntersects(a, b, m = 14) {
  return a.x < b.x + b.w + m && a.x + a.w + m > b.x && a.y < b.y + b.h + m && a.y + a.h + m > b.y;
}

function placeFree(rect, rects) {
  const r = { ...rect };
  for (let guard = 0; guard < 600; guard++) {
    const hit = rects.find((o) => bIntersects(r, o));
    if (!hit) return r;
    r.y = bSnap(hit.y + hit.h + BOARD_GAP_ROW);
  }
  return r;
}

/* ComfyUI DAG columns: Config & Locations | Characters | Keyframe | VideoSampler | SaveFilm */
function bGridSlots(cards) {
  const slots = {};
  const cfgAndLocs = cards.filter((c) => c.type === "cfg" || c.type === "loc");
  const charCards = cards.filter((c) => c.type === "char");
  const splitSources = charCards.length > 0 && (cfgAndLocs.length + charCards.length > 2);

  const xCol0 = BOARD_ORIGIN;
  const xChar = splitSources ? (xCol0 + Math.max(BOARD_W_CFG, BOARD_W_CHAR) + 64) : xCol0;
  const xKf = xChar + (charCards.length || !splitSources ? Math.max(BOARD_W_CHAR, BOARD_W_CFG) : 0) + BOARD_GAP_COL;
  const xVid = xKf + bSceneW() + BOARD_GAP_COL;
  const xOut = xVid + bSceneW() + BOARD_GAP_COL;

  if (splitSources) {
    let y0 = BOARD_ORIGIN;
    for (const cd of cfgAndLocs) {
      slots[cd.key] = { x: xCol0, y: bSnap(y0) };
      y0 += bCardH(cd.key) + BOARD_GAP_ROW;
    }
    let y1 = BOARD_ORIGIN;
    for (const cd of charCards) {
      slots[cd.key] = { x: xChar, y: bSnap(y1) };
      y1 += bCardH(cd.key) + BOARD_GAP_ROW;
    }
  } else {
    let ySrc = BOARD_ORIGIN;
    for (const cd of cards.filter((c) => c.type === "cfg" || c.type === "char" || c.type === "loc")) {
      slots[cd.key] = { x: xCol0, y: bSnap(ySrc) };
      ySrc += bCardH(cd.key) + BOARD_GAP_ROW;
    }
  }

  let ySc = BOARD_ORIGIN + 16;
  for (const sc of bScenes()) {
    const kf = `kf:${sc.id}`, vid = `vid:${sc.id}`, isKf = bMode(sc) === "keyframe";
    if (isKf) slots[kf] = { x: xKf, y: bSnap(ySc) };
    slots[vid] = { x: isKf ? xVid : xKf, y: bSnap(ySc) };
    const rowH = Math.max(isKf ? bCardH(kf) : 0, bCardH(vid));
    ySc += rowH + BOARD_GAP_ROW + (B.showGroups ? 36 : 0);
  }

  if (cards.some((c) => c.type === "out")) {
    const midY = bScenes().length ? Math.max(BOARD_ORIGIN, (ySc - BOARD_GAP_ROW - bCardH("out:final")) / 2) : BOARD_ORIGIN;
    slots["out:final"] = { x: xOut, y: bSnap(midY) };
  }
  return slots;
}

function autoLayout(all = false) {
  const { cards } = boardModel();
  if (all) { B.spos.clear(); B.pos = new WeakMap(); B.lpos = new WeakMap(); B.cards = {}; }
  const slots = bGridSlots(cards);
  const rect = (key, p) => ({ x: p.x, y: p.y, w: bCardW(key), h: bCardH(key) });
  const placed = [];
  for (const cd of cards) { const p = bPos(cd.key); if (p) placed.push(rect(cd.key, p)); }
  let moved = false;
  for (const cd of cards) {
    if (bPos(cd.key)) continue;
    let slot = slots[cd.key];
    if (cd.type === "vid" && bMode(cd.sc) === "keyframe") {
      const kp = bPos(`kf:${cd.sc.id}`);
      if (kp) slot = { x: bSnap(kp.x + bSceneW() + BOARD_GAP_COL), y: kp.y };
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
  const sb = S.cur.storyboard, problems = charNameProblems(sb), locProbs = locNameProblems(sb);
  const cards = {};
  const pCfg = B.spos.get("cfg:film");
  if (pCfg) cards["cfg:film"] = { x: pCfg.x, y: pCfg.y };
  bChars().forEach((c, i) => { const p = bCharPos(c, i, problems); if (p) cards[bSerialCharKey(c, i, problems)] = { x: p.x, y: p.y }; });
  bLocs().forEach((l, i) => {
    if (blankLocation(l)) return;
    const p = bLocPos(l, i, locProbs);
    if (p) cards[bSerialLocKey(l, i, locProbs)] = { x: p.x, y: p.y };
  });
  const kfIds = new Set();
  for (const sc of bScenes()) {
    if (bMode(sc) === "keyframe") { kfIds.add(sc.id); const p = B.spos.get(`kf:${sc.id}`); if (p) cards[`kf:${sc.id}`] = { x: p.x, y: p.y }; }
    if (bVideoShown(sc)) { const p = B.spos.get(`vid:${sc.id}`); if (p) cards[`vid:${sc.id}`] = { x: p.x, y: p.y }; }
  }
  const pOut = B.spos.get("out:final");
  if (pOut && bScenes().length) cards["out:final"] = { x: pOut.x, y: pOut.y };
  const videos = [...B.videos].filter((id) => kfIds.has(id));
  const view = B.view ? { x: Math.round(B.view.x), y: Math.round(B.view.y), zoom: Math.round(B.view.zoom * 1000) / 1000 } : null;
  return { cards, videos, view };
}
const bKeysOf = (body) => Object.keys(body.cards).sort().join("\n") + "|" + body.videos.join(",");

function saveBoard(delay = 400) {
  if (!S.cur || B.pid !== S.cur.id || !B.loaded) return;
  if (B.loadError) {
    if (!B.loadErrorToast) { B.loadErrorToast = true; toast("Chưa tải được bố cục — tạm không lưu vị trí. Bấm “Thử lại” trên bảng.", "error", 6000); }
    return;
  }
  B.pending = { pid: B.pid, body: bSerialize() };
  clearTimeout(B.timer);
  B.timer = setTimeout(() => { B.timer = null; bFlushBoard(); }, delay);
}

async function bFlushBoard() {
  if (B.inflight) return;
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

function boardAfterSave() {
  if (!S.cur || B.pid !== S.cur.id || !B.loaded) return;
  if (bKeysOf(bSerialize()) !== B.savedKeys) saveBoard();
}

async function bLoad(pid) {
  let lay = null, err = null;
  try { lay = await api("GET", `/api/projects/${encodeURIComponent(pid)}/board`); }
  catch (e) { err = e.message || "lỗi mạng"; }
  if (B.pid !== pid) return;
  B.loadError = err; B.loadErrorToast = false;
  if (err) toast("Không tải được bố cục bảng — tạm dùng bố cục mặc định, chưa lưu vị trí", "error");
  lay = lay && typeof lay === "object" ? lay : {};
  B.cards = lay.cards && typeof lay.cards === "object" ? lay.cards : {};
  for (const [k, p] of Object.entries(B.cards)) {
    if (!k.startsWith("char:") && !k.startsWith("loc:") && p) B.spos.set(k, { x: +p.x, y: +p.y });
  }
  B.videos = new Set(Array.isArray(lay.videos) ? lay.videos : []);
  B.view = lay.view && typeof lay.view === "object" ? { x: +lay.view.x || 0, y: +lay.view.y || 0, zoom: bClampZoom(+lay.view.zoom || 1) } : null;
  B.savedKeys = Object.keys(B.cards).sort().join("\n") + "|" + [...B.videos].join(",");
  B.loading = false; B.loaded = true;
  if (S.cur && S.cur.id === pid && S.tab === "board") rerenderBoard();
}

function bRetryLoad() {
  if (!S.cur || B.pid !== S.cur.id) return;
  B.pid = null;
  rerenderBoard();
}

async function bQueueMain() {
  if (jobRunning()) { toast("Đang có việc chạy", "error"); return; }
  const sb = S.cur.storyboard;
  const missingChars = bChars().filter((c) => c.name && c.description && !charImageUrl(c));
  if (missingChars.length) { makePortraits([]); return; }
  const missingLocs = bLocs().filter((l) => !blankLocation(l) && locCanMake(l) && !locImageUrl(l));
  if (missingLocs.length) { makeLocationImages([]); return; }
  const missingKf = bScenes().filter((sc) => bMode(sc) === "keyframe" && !S.cur.scene_state?.[sc.id]?.keyframe_url);
  if (missingKf.length) { runAction("keyframes"); return; }
  runAction("video");
}

/* ---------- Graph Editing Actions (Characters, Locations, Continuity, Scenes) ---------- */

async function editSceneChars(sc, add, remove) {
  const sb = S.cur.storyboard, pid = S.cur.id;
  if (charsLocked(sb)) { toast("Đổi tên nhân vật trống/trùng trước khi nối"); return false; }
  if (add && S.cur.errors?.length) { toast("Storyboard đang lỗi — chỉ xoá được dây nối. Sửa lỗi ở khung phía trên trước.", "error"); return false; }
  if (bMode(sc) === "i2v") { toast(`Cảnh ${sc.id} kiểu “${bModeLabel("i2v")}” không dùng ảnh nhân vật`); return false; }
  let base;
  if (Array.isArray(sc.characters)) base = sc.characters;
  else {
    await flushSave();
    if (!S.cur || S.cur.id !== pid || S.cur.storyboard !== sb || !sb.scenes.includes(sc)) return false;
    if (saveFailed()) { toast("Chưa lưu được storyboard nên chưa biết nhân vật tự động của cảnh: " + (S.saveError || "thử lại sau"), "error"); return false; }
    if (S.cur.errors?.length) { toast("Storyboard đang lỗi — sửa lỗi trước khi nối dây", "error"); return false; }
    const st = S.cur.scene_state?.[sc.id];
    if (!st || !Array.isArray(st.characters)) { toast(`Chưa có danh sách nhân vật của cảnh ${sc.id} từ máy chủ — thử lại sau giây lát`, "error"); return false; }
    if (Array.isArray(sc.characters)) base = sc.characters;
    else base = st.characters.filter((n) => String(n || "").trim());
  }
  const cur = (n) => { const i = bResolve(n); return i >= 0 ? bKey(sb.characters[i].name) : bKey(n); };
  const set = new Set(base.map(cur));
  if (add) set.add(bKey(add));
  if (remove) set.delete(bKey(remove));
  const unknown = base.filter((n) => bKey(n) && bResolve(n) < 0);
  sc.characters = [...sb.characters.map((c) => c.name).filter((n) => set.has(bKey(n))), ...unknown];
  changed();
  bRefresh();
  return true;
}

async function editSceneLocation(sc, locName) {
  const sb = S.cur.storyboard;
  if (locsLocked(sb)) { toast("Đổi tên bối cảnh trống/trùng trước khi nối"); return false; }
  if (bMode(sc) === "i2v") { toast(`Cảnh ${sc.id} kiểu “${bModeLabel("i2v")}” không dùng bối cảnh`); return false; }
  const nextVal = locName ? String(locName).trim() : "";
  const nImpact = locationImpact(sb, S.cur.scene_state, { scene: sc, to: nextVal, kind: "assign" });
  if (!(await confirmLocImpact(nImpact, nextVal ? `Gán bối cảnh “${nextVal}” cho cảnh ${sc.id}` : `Bỏ bối cảnh khỏi cảnh ${sc.id}`))) return false;
  if (nextVal) sc.location = nextVal;
  else delete sc.location;
  changed();
  bRefresh();
  return true;
}

async function editSceneContinuity(fromSid, toSid, enable) {
  const sb = S.cur.storyboard, scenes = bScenes();
  const toIdx = scenes.findIndex((s) => s.id === toSid);
  if (toIdx <= 0 || scenes[toIdx - 1].id !== fromSid) {
    toast("Chỉ nối tiếp keyframe giữa 2 cảnh liền kề nhau theo thứ tự kịch bản");
    return false;
  }
  const prev = scenes[toIdx - 1], cur = scenes[toIdx];
  if (bMode(prev) !== "keyframe" || bMode(cur) !== "keyframe") {
    toast("Cả 2 cảnh liền kề phải ở chế độ Ảnh → Video (Keyframe) để nối tiếp");
    return false;
  }
  if (!enable) {
    cur.continue_previous = false;
    changed();
    bRefresh();
    return true;
  }
  const prevLoc = bLocIndex(prev.location), curLoc = bLocIndex(cur.location);
  if (prevLoc < 0 && curLoc < 0) {
    toast(`Gán cùng một Bối cảnh (Location) cho ${prev.id} và ${cur.id} trước khi nối tiếp keyframe`);
    return false;
  }
  if (prevLoc >= 0 && curLoc < 0) {
    if (!(await editSceneLocation(cur, bLocs()[prevLoc].name))) return false;
  } else if (curLoc >= 0 && prevLoc < 0) {
    if (!(await editSceneLocation(prev, bLocs()[curLoc].name))) return false;
  } else if (prevLoc !== curLoc) {
    toast(`${prev.id} và ${cur.id} đang ở 2 bối cảnh khác nhau — đổi về cùng bối cảnh để nối keyframe`);
    return false;
  }
  if (sb.continuity === false) delete sb.continuity;
  delete cur.continue_previous;
  changed();
  bRefresh();
  toast(`Đã bật nối tiếp keyframe ${fromSid} → ${toSid}`, "ok");
  return true;
}

function bRemoveUnknown(sc, name) {
  if (!Array.isArray(sc.characters)) return;
  sc.characters = sc.characters.filter((n) => bKey(n) !== bKey(name));
  changed();
  bRefresh();
}

/* ---------- Render ComfyUI Canvas ---------- */

function renderBoard() {
  bLoadUiPrefsOnce();
  if (!B.keyBound) { document.addEventListener("keydown", bOnKey); B.keyBound = true; }
  if (B.pid !== S.cur.id) {
    clearTimeout(B.timer); B.timer = null;
    if (B.pending) bFlushBoard();
    Object.assign(B, {
      pid: S.cur.id, loading: true, loaded: false, loadError: null,
      cards: {}, pos: new WeakMap(), lpos: new WeakMap(), spos: new Map(),
      videos: new Set(), collapsed: new Set(), view: null, h: {}, slotY: {},
      sel: null, selChar: null, selLoc: null, saveAfterMount: false, drag: null,
      sig: "", savedJson: null, savedKeys: null, paletteOpen: false,
    });
    bLoad(S.cur.id);
  }
  const sb = S.cur.storyboard;
  sb.characters = sb.characters || [];
  B.mc = null;
  const root = h("section", { class: "board" + (bScenePortrait() ? " portrait" : "") },
    bToolbar(),
    zone("board-notes", bNotes));
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
  world.append(zone("board-groups", () => bGroupsLayer(model)));
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
  if (B.showMinimap) vp.append(bMinimapEl(model));
  vp.append(h("div", { class: "cf-hud-hint" }, "Kéo nền: di chuyển · Ctrl+Cuộn: thu phóng · Nháy đúp: thêm/tìm Node · Del: xoá dây"));
  if (B.paletteOpen) vp.append(bPaletteEl(model));

  bApplyView(vp);
  vp.addEventListener("pointerdown", bOnPointerDown);
  vp.addEventListener("pointermove", bOnPointerMove);
  vp.addEventListener("pointerup", bOnPointerUp);
  vp.addEventListener("pointercancel", bOnPointerUp);
  vp.addEventListener("wheel", bOnWheel, { passive: false });
  vp.addEventListener("click", bOnClick);
  vp.addEventListener("dblclick", bOnDblClick);
  vp.addEventListener("contextmenu", bOnContextMenu);
  vp.addEventListener("keydown", bOnLinkKey);
  requestAnimationFrame(() => bAfterMount(root, unplaced));
  return root;
}

function rerenderBoard() {
  const old = document.querySelector("#main .board");
  if (!old || S.tab !== "board") return;
  old.replaceWith(renderBoard());
}

function bMeasureSlots(root = document.querySelector("#main .board")) {
  if (!root) return;
  const zoom = B.view?.zoom || 1;
  B.slotY = {};
  root.querySelectorAll(".board-card").forEach((cardEl) => {
    const key = cardEl.dataset.key;
    if (cardEl.offsetHeight) B.h[key] = cardEl.offsetHeight;
    const cr = cardEl.getBoundingClientRect();
    cardEl.querySelectorAll(".board-port[data-slot]").forEach((portEl) => {
      const pr = portEl.getBoundingClientRect();
      const side = portEl.dataset.port;
      const slot = portEl.dataset.slot;
      if (cr.height > 0 && pr.height > 0) {
        B.slotY[`${key}|${side}|${slot}`] = (pr.top + pr.height / 2 - cr.top) / zoom;
      }
    });
  });
}

function bAfterMount(root, unplaced) {
  if (!root.isConnected) return;
  bMeasureSlots(root);
  if (unplaced.length && autoLayout(false)) {
    for (const key of unplaced) {
      const el = root.querySelector(`.board-card[data-key="${CSS.escape(key)}"]`), p = bPos(key);
      if (el && p) { el.style.left = p.x + "px"; el.style.top = p.y + "px"; el.style.visibility = ""; }
    }
  }
  bMeasureSlots(root);
  refreshZone("board-groups");
  refreshZone("board-links");
  bPlaceEmpties(root);
  if (!B.view) bFit(false);
  else bUpdateMinimap();
  if (B.saveAfterMount) { B.saveAfterMount = false; saveBoard(); }
  refreshZones();
}

function bApplyView(vp = document.querySelector(".board-viewport")) {
  if (!vp) return;
  const v = B.view || { x: 0, y: 0, zoom: 1 };
  const world = vp.querySelector(".board-world");
  if (world) world.style.transform = `translate(${v.x}px, ${v.y}px) scale(${v.zoom})`;
  const major = 100 * v.zoom, minor = 20 * v.zoom;
  vp.style.backgroundSize = `${major}px ${major}px, ${major}px ${major}px, ${minor}px ${minor}px, ${minor}px ${minor}px`;
  vp.style.backgroundPosition = `${v.x}px ${v.y}px, ${v.x}px ${v.y}px, ${v.x}px ${v.y}px, ${v.x}px ${v.y}px`;
  const lbl = document.querySelector(".board-zoom-val");
  if (lbl) lbl.textContent = Math.round(v.zoom * 100) + "%";
  bUpdateMinimap(vp);
}

/* ---------- ComfyUI Scene Groups & Minimap & Search Palette ---------- */

function bGroupsLayer(model) {
  const wrap = h("div", { class: "cf-groups", "aria-hidden": "true" });
  if (!B.showGroups) return wrap;
  for (const sc of bScenes()) {
    const keys = [];
    if (bMode(sc) === "keyframe" && model.cards.some((c) => c.key === `kf:${sc.id}`)) keys.push(`kf:${sc.id}`);
    if (model.cards.some((c) => c.key === `vid:${sc.id}`)) keys.push(`vid:${sc.id}`);
    const rects = keys.map((k) => { const p = bPos(k); return p ? { x: p.x, y: p.y, w: bCardW(k), h: bCardH(k) } : null; }).filter(Boolean);
    if (!rects.length) continue;
    const padX = 16, padTop = 34, padBot = 14;
    const minX = Math.min(...rects.map((r) => r.x)) - padX;
    const minY = Math.min(...rects.map((r) => r.y)) - padTop;
    const maxX = Math.max(...rects.map((r) => r.x + r.w)) + padX;
    const maxY = Math.max(...rects.map((r) => r.y + r.h)) + padBot;
    const li = bLocIndex(sc.location);
    const locTxt = li >= 0 ? ` · ${bLocs()[li].name}` : ` · ${bModeLabel(bMode(sc))}`;
    const grp = h("div", { class: "cf-group", "data-sid": sc.id, style: { left: minX + "px", top: minY + "px", width: (maxX - minX) + "px", height: (maxY - minY) + "px" } },
      h("div", { class: "cf-group-head" }, `Cảnh ${sc.id}${locTxt}`));
    wrap.append(grp);
  }
  return wrap;
}

function bNodeColor(type) {
  return { cfg: "#6366f1", char: "#a855f7", loc: "#10b981", kf: "#38bdf8", vid: "#f97316", out: "#22c55e" }[type] || "#94a3b8";
}

function bMinimapBounds(cards) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const cd of cards) {
    const p = bPos(cd.key);
    if (!p) continue;
    minX = Math.min(minX, p.x); minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x + bCardW(cd.key)); maxY = Math.max(maxY, p.y + bCardH(cd.key));
  }
  if (!Number.isFinite(minX)) return { x: 0, y: 0, w: 1200, h: 800 };
  const pad = 120;
  return { x: minX - pad, y: minY - pad, w: Math.max(600, maxX - minX + 2 * pad), h: Math.max(400, maxY - minY + 2 * pad) };
}

function bMinimapEl(model) {
  const b = bMinimapBounds(model.cards);
  const svg = svgEl("svg", { viewBox: `${b.x} ${b.y} ${b.w} ${b.h}`, preserveAspectRatio: "xMidYMid meet", "aria-hidden": "true" });
  for (const cd of model.cards) {
    const p = bPos(cd.key);
    if (!p) continue;
    svg.append(svgEl("rect", {
      "data-mm-key": cd.key, x: p.x, y: p.y, width: bCardW(cd.key), height: bCardH(cd.key), rx: 14,
      fill: bNodeColor(cd.type), "fill-opacity": "0.75",
    }));
  }
  svg.append(svgEl("rect", { class: "mm-lens", x: b.x, y: b.y, width: 200, height: 150, rx: 10 }));
  const wrap = h("div", { class: "cf-minimap", title: "Bản đồ node — bấm hoặc kéo để di chuyển khung nhìn" }, svg);
  return wrap;
}

function bUpdateMinimap(vp = bViewport()) {
  if (!vp || !B.showMinimap) return;
  const mm = vp.querySelector(".cf-minimap svg");
  if (!mm) return;
  const cards = bModel().cards;
  const b = bMinimapBounds(cards);
  mm.setAttribute("viewBox", `${b.x} ${b.y} ${b.w} ${b.h}`);
  const lens = mm.querySelector(".mm-lens");
  const seen = new Set();
  for (const cd of cards) {
    const p = bPos(cd.key);
    if (!p) continue;
    seen.add(cd.key);
    let r = mm.querySelector(`rect[data-mm-key="${CSS.escape(cd.key)}"]`);
    if (!r) {
      r = svgEl("rect", {
        "data-mm-key": cd.key, rx: 14,
        fill: bNodeColor(cd.type), "fill-opacity": "0.82",
      });
      if (lens) mm.insertBefore(r, lens);
      else mm.append(r);
    }
    r.setAttribute("x", p.x);
    r.setAttribute("y", p.y);
    r.setAttribute("width", bCardW(cd.key));
    r.setAttribute("height", bCardH(cd.key));
  }
  mm.querySelectorAll("rect[data-mm-key]").forEach((el) => {
    if (!seen.has(el.getAttribute("data-mm-key"))) el.remove();
  });
  if (lens && vp.clientWidth) {
    const v = B.view || { x: 0, y: 0, zoom: 1 };
    const lx = -v.x / v.zoom, ly = -v.y / v.zoom, lw = vp.clientWidth / v.zoom, lh = vp.clientHeight / v.zoom;
    lens.setAttribute("x", lx); lens.setAttribute("y", ly);
    lens.setAttribute("width", Math.max(40, lw)); lens.setAttribute("height", Math.max(30, lh));
  }
}

function bPanFromMinimap(e, mmEl) {
  const vp = bViewport(), svg = mmEl.querySelector("svg");
  if (!vp || !svg) return;
  const vb = (svg.getAttribute("viewBox") || "0 0 1200 800").split(" ").map(Number);
  const r = mmEl.getBoundingClientRect();
  const scale = Math.min(r.width / vb[2], r.height / vb[3]);
  const offX = (r.width - vb[2] * scale) / 2, offY = (r.height - vb[3] * scale) / 2;
  const wx = vb[0] + (e.clientX - r.left - offX) / scale;
  const wy = vb[1] + (e.clientY - r.top - offY) / scale;
  const z = B.view?.zoom || 1;
  B.view = { x: vp.clientWidth / 2 - wx * z, y: vp.clientHeight / 2 - wy * z, zoom: z };
  bApplyView(vp);
}

function bTogglePalette(open) {
  B.paletteOpen = open ?? !B.paletteOpen;
  const vp = bViewport();
  if (!vp) return;
  vp.querySelector(".cf-palette")?.remove();
  if (B.paletteOpen) {
    const pal = bPaletteEl(bModel());
    vp.append(pal);
    setTimeout(() => pal.querySelector("input")?.focus(), 10);
  }
}

function bPaletteEl(model) {
  let q = "";
  const list = h("div", { class: "cf-palette-list" });
  const actions = [
    { label: "Thêm nhân vật (CharacterLoader)", tag: "+ CHARACTER", run: () => { bTogglePalette(false); bAddCharacter(); } },
    { label: "Thêm bối cảnh (LocationLoader)", tag: "+ LOCATION", run: () => { bTogglePalette(false); bAddLocation(); } },
    { label: "Thêm cảnh Ảnh → Video (Keyframe + Sampler)", tag: "+ SCENE KF", run: () => { bTogglePalette(false); bAddScene("keyframe"); } },
    { label: "Thêm cảnh Chữ → Video (T2V Sampler)", tag: "+ SCENE T2V", run: () => { bTogglePalette(false); bAddScene("t2v"); } },
  ];
  for (const cd of model.cards) {
    const title = cd.type === "cfg" ? "FilmConfig · Cấu hình phim"
      : cd.type === "char" ? `Nhân vật · ${cd.c.name || "(chưa đặt tên)"}`
      : cd.type === "loc" ? `Bối cảnh · ${cd.l.name || "(chưa đặt tên)"}`
      : cd.type === "kf" ? `Keyframe · ${cd.sc.id}`
      : cd.type === "vid" ? `VideoSampler · ${cd.sc.id}`
      : "SaveFilm · Ghép phim";
    actions.push({ label: `Đi tới: ${title}`, tag: `#${cd.nid}`, run: () => { bTogglePalette(false); bFocusNode(cd.key); } });
  }
  const draw = () => {
    const k = q.trim().toLowerCase();
    const hits = actions.filter((a) => !k || a.label.toLowerCase().includes(k) || a.tag.toLowerCase().includes(k)).slice(0, 14);
    put(list, hits.length ? hits.map((a, idx) => h("button", {
      class: "cf-palette-item" + (idx === 0 ? " active" : ""),
      onclick: a.run,
    }, h("span", {}, a.label), h("span", { class: "cf-palette-tag" }, a.tag)))
      : h("div", { class: "small muted", style: { padding: "10px" } }, "Không tìm thấy node phù hợp."));
  };
  const inp = h("input", {
    type: "text", placeholder: "Tìm hoặc thêm node (Nhân vật, Bối cảnh, Cảnh, s01…)",
    oninput: (e) => { q = e.target.value; draw(); },
    onkeydown: (e) => {
      if (e.key === "Escape") { e.stopPropagation(); bTogglePalette(false); }
      else if (e.key === "Enter") { e.preventDefault(); list.querySelector(".cf-palette-item")?.click(); }
    },
  });
  draw();
  return h("div", { class: "cf-palette", onpointerdown: (e) => e.stopPropagation() },
    h("div", { class: "cf-palette-head" }, icon("plus"), inp,
      h("button", { class: "btn sm ghost icon", title: "Đóng", onclick: () => bTogglePalette(false) }, icon("x"))),
    list);
}

function bFocusNode(key) {
  bScrollTo(key);
  const el = document.querySelector(`.board-card[data-key="${CSS.escape(key)}"]`);
  if (el) el.focus({ preventScroll: true });
}

/* ---------- ComfyUI Toolbar & Legend ---------- */

function bToolbar() {
  const zoomPct = Math.round((B.view?.zoom || 1) * 100) + "%";
  const wireLabels = { spline: "Dây: Cong", orthogonal: "Dây: Vuông", straight: "Dây: Thẳng" };
  return h("div", { class: "board-toolbar", role: "toolbar", "aria-label": "Công cụ bảng" },
    h("div", { class: "cf-queue-group" },
      h("button", { class: "cf-queue-btn", disabled: jobRunning(), title: "Chạy bước tiếp theo trên Google Flow", onclick: bQueueMain },
        icon("play"), "Chạy Flow"),
      h("button", { class: "cf-queue-more", "aria-label": "Tuỳ chọn chạy Flow", onclick: (e) => popMenu(e.currentTarget, bQueueMenuItems()) }, icon("down"))),
    h("span", { class: "board-sep", "aria-hidden": "true" }),
    h("button", { class: "btn sm board-add", title: "Thêm hoặc tìm Node (nháy đúp nền canvas)", onclick: () => bTogglePalette() },
      icon("plus"), h("span", { class: "lbl-text" }, "Thêm Node")),
    h("button", { class: "btn sm ghost", title: "Tuỳ chọn hiển thị bảng", onclick: (e) => popMenu(e.currentTarget, [
      { label: wireLabels[B.wireStyle] + " (đổi kiểu dây)", icon: "board", onclick: bCycleWireStyle },
      { label: (B.showGroups ? "✓ " : "") + "Hiện khung nhóm từng cảnh", onclick: () => { B.showGroups = !B.showGroups; localSet("boardGroups", B.showGroups); rerenderBoard(); } },
      { label: (B.showMinimap ? "✓ " : "") + "Hiện bản đồ thu nhỏ (Minimap)", onclick: () => { B.showMinimap = !B.showMinimap; localSet("boardMinimap", B.showMinimap); rerenderBoard(); } },
      { label: (B.showModelLinks ? "✓ " : "") + "Hiện dây nối MODEL từ FilmConfig", onclick: () => { B.showModelLinks = !B.showModelLinks; localSet("boardModelLinks", B.showModelLinks); refreshZone("board-links"); } },
      { label: (B.hideIdleVideos ? "✓ " : "") + "Ẩn node Video chưa tạo", onclick: () => { B.hideIdleVideos = !B.hideIdleVideos; localSet("boardHideIdleVid", B.hideIdleVideos); rerenderBoard(); } },
      "-",
      { label: "Thu gọn mọi node", onclick: () => bCollapseAll(true) },
      { label: "Mở rộng mọi node", onclick: () => bCollapseAll(false) },
      { label: "Tự xếp lại DAG", icon: "layout", onclick: bRelayout },
    ], "Hiển thị Canvas") }, icon("layout"), h("span", { class: "lbl-text" }, "Bố cục")),
    h("div", { class: "board-zoom", role: "group", "aria-label": "Thu phóng" },
      h("button", { class: "btn sm ghost icon", title: "Thu nhỏ", "aria-label": "Thu nhỏ", onclick: () => bZoomStep(-1) }, icon("minus")),
      h("button", { class: "board-zoom-val mono", "aria-live": "polite", title: "Về 100%", onclick: () => bZoomTo(1) }, zoomPct),
      h("button", { class: "btn sm ghost icon", title: "Phóng to", "aria-label": "Phóng to", onclick: () => bZoomStep(1) }, icon("plus")),
      h("button", { class: "btn sm ghost", title: "Vừa màn hình", "aria-label": "Vừa màn hình", onclick: () => bFit(true) }, icon("fit"), h("span", { class: "lbl-text" }, "Vừa màn hình"))),
    h("span", { class: "spacer" }),
    zone("board-sel", bSelSlot),
    h("div", { class: "board-legend", "aria-label": "Chú thích cổng" },
      bLegendItems().map(([color, label]) => h("span", { class: "board-legend-item" }, h("i", { class: "cf-legend-dot", style: { background: color } }), label))),
    h("button", { class: "btn sm ghost board-legend-btn", "aria-haspopup": "menu", title: "Chú thích", onclick: (e) => popMenu(e.currentTarget,
      bLegendItems().map(([color, label]) => ({ label, extra: h("i", { class: "cf-legend-dot", style: { background: color, marginLeft: "auto" } }) }))) },
      icon("info"), h("span", { class: "lbl-text" }, "Chú thích")));
}

function bQueueMenuItems() {
  const busy = jobRunning();
  return [
    { label: "Tạo ảnh nhân vật chưa có (miễn phí)", icon: "users", disabled: busy, onclick: () => makePortraits([]) },
    { label: "Tạo ảnh bối cảnh chưa có (miễn phí)", icon: "image", disabled: busy, onclick: () => makeLocationImages([]) },
    { label: "Tạo tất cả Keyframe chưa có (miễn phí)", icon: "image", disabled: busy, onclick: () => runAction("keyframes") },
    { label: "Render tất cả Video clip (credit)", icon: "video", disabled: busy, onclick: () => runAction("video") },
    "-",
    { label: "Ghép phim hoàn chỉnh (final.mp4)", icon: "stitch", disabled: busy, onclick: () => runAction("stitch") },
  ];
}

function bLegendItems() {
  return [
    ["#a855f7", "Nhân vật"],
    ["#10b981", "Bối cảnh"],
    ["#38bdf8", "Ảnh / Nối KF"],
    ["#22c55e", "Video clip"],
  ];
}

function bCycleWireStyle() {
  const order = ["spline", "orthogonal", "straight"];
  B.wireStyle = order[(order.indexOf(B.wireStyle) + 1) % order.length];
  localSet("boardWireStyle", B.wireStyle);
  refreshZone("board-links");
}

function bCollapseAll(collapse) {
  if (collapse) {
    for (const cd of bModel().cards) B.collapsed.add(cd.key);
  } else {
    B.collapsed.clear();
  }
  rerenderBoard();
}

function bNotes() {
  const sb = S.cur.storyboard;
  const notes = [];
  if (B.loadError && !B.loading) notes.push(h("div", { class: "banner err flush", role: "alert" }, icon("alert"),
    h("div", { class: "b-body" }, `Không tải được bố cục bảng (${B.loadError}) — đang dùng bố cục mặc định, vị trí chưa được lưu.`),
    h("button", { class: "btn sm", onclick: bRetryLoad }, icon("refresh"), "Thử lại")));
  if (charsLocked(sb)) notes.push(h("div", { class: "banner warn flush" }, icon("alert"), h("div", { class: "b-body" }, "Có nhân vật tên trống/trùng — đổi tên trước khi nối dây.")));
  if (locsLocked(sb)) notes.push(h("div", { class: "banner warn flush" }, icon("alert"), h("div", { class: "b-body" }, "Có bối cảnh tên trống/trùng — đổi tên trước khi nối dây.")));
  if (S.cur.errors?.length) notes.push(h("div", { class: "banner warn flush" }, icon("alert"), h("div", { class: "b-body" }, "Storyboard đang lỗi — chỉ xoá được dây nối, nút tạo bị tắt.")));
  if (sb.use_character_refs === false) notes.push(h("div", { class: "banner info flush" }, icon("info"),
    h("div", { class: "b-body" }, "Đang tắt gửi ảnh nhân vật — dây CHARACTER chuyển màu vàng (chỉ gửi mô tả)."),
    h("button", { class: "btn sm ghost", onclick: () => { delete sb.use_character_refs; changed(); bRefresh(); } }, "Bật lại")));
  return h("div", { class: "board-notes" }, notes);
}

/* ---------- Selection & Wire Deletion ---------- */

function bSelLink(links) {
  if (!B.sel) return null;
  const L = links.find((l) => l.id === B.sel);
  if (!L) { B.sel = null; B.selChar = null; B.selLoc = null; return null; }
  if (L.kind === "char" && (!B.selChar || bChars()[L.ci] !== B.selChar)) { B.sel = null; B.selChar = null; return null; }
  if (L.kind === "loc" && (!B.selLoc || bLocs()[L.li] !== B.selLoc)) { B.sel = null; B.selLoc = null; return null; }
  return L;
}

function bSelSlot() {
  const L = bSelLink(bModel().links);
  if (!L) return h("div", { class: "board-sel hidden" });
  const why = bDeleteBlocked(L);
  const descText = L.kind === "char" ? `${L.name} → ${L.sid}`
    : L.kind === "loc" ? `${L.name} → ${L.sid}`
    : L.kind === "cont" ? `Nối KF ${L.fromSid} → ${L.sid}`
    : L.kind === "kfvid" ? `Keyframe ${L.sid} → Video`
    : `${L.sid} → Ghép phim`;
  return h("div", { class: "board-sel" },
    h("span", { class: "muted small" }, descText),
    h("button", { class: "btn sm danger", disabled: !!why, title: why || "Ngắt dây nối (Delete)", onclick: () => bDeleteSelected() }, icon("trash"), "Ngắt dây"));
}

function bDeleteBlocked(L) {
  if (L.kind === "char") return charsLocked(S.cur.storyboard) ? "Đổi tên nhân vật trống/trùng trước khi sửa dây" : "";
  if (L.kind === "loc") return locsLocked(S.cur.storyboard) ? "Đổi tên bối cảnh trống/trùng trước khi sửa dây" : "";
  if (L.kind === "cont") return "";
  if (L.kind === "vidout" || L.kind === "model") return "Dây hệ thống cố định trong luồng dựng phim";
  const sc = bScenes().find((s) => s.id === L.sid), st = S.cur.scene_state?.[L.sid] || {};
  if (st.clip_url) return "Cảnh đã có clip — không ẩn được node video";
  if (sc && bSceneRunning(sc, "vid")) return "Cảnh đang render — không ẩn được node video";
  if (st.status === "interrupted") return "Cảnh bị ngắt khi render — không ẩn được node video";
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
  } else if (L.kind === "loc") {
    if (await editSceneLocation(sc, "")) { B.sel = null; B.selLoc = null; bRefresh(); }
  } else if (L.kind === "cont") {
    if (await editSceneContinuity(L.fromSid, L.sid, false)) { B.sel = null; bRefresh(); }
  } else if (L.kind === "kfvid") {
    B.hideIdleVideos = true;
    localSet("boardHideIdleVid", true);
    B.videos.delete(sc.id);
    B.sel = null;
    rerenderBoard();
    saveBoard();
  }
}

function bSelect(id) {
  const mc = /^c(\d+)>/.exec(id || ""), ml = /^l(\d+)>/.exec(id || "");
  B.sel = id || null;
  B.selChar = mc ? bChars()[+mc[1]] || null : null;
  B.selLoc = ml ? bLocs()[+ml[1]] || null : null;
  document.querySelectorAll(".board-links .board-link").forEach((g) => {
    const on = g.dataset.id === id;
    g.classList.toggle("selected", on);
    const line = g.querySelector(".line");
    if (line) line.setAttribute("marker-end", `url(#${bMarker(g.dataset.dtype, g.classList.contains("desc"), on)})`);
  });
  refreshZone("board-sel");
}

/* ---------- ComfyUI Socket-to-Socket SVG Noodles ---------- */

function bMarker(dtype, desc, selected) {
  if (selected) return "bm-sel";
  if (desc) return "bm-open";
  return {
    CHARACTER: "bm-char",
    LOCATION: "bm-loc",
    IMAGE: "bm-image",
    VIDEO: "bm-video",
    MODEL: "bm-model",
  }[dtype] || "bm-arrow";
}

function bDefaultSlotY(key, side, slot) {
  if (B.collapsed.has(key)) return BOARD_HEAD / 2;
  const orderIn = { model: 0, start_image: 0, image: 0, characters: 0, location: 1, prev_image: 2, clips: 0 };
  if (key.startsWith("kf:")) {
    const idx = { characters: 0, location: 1, prev_image: 2, model: 3 }[slot] ?? 0;
    return BOARD_HEAD + 15 + idx * 22;
  }
  if (key.startsWith("vid:")) {
    const idx = { start_image: 0, image: 0, characters: 0, location: 1, model: 2 }[slot] ?? 0;
    return BOARD_HEAD + 15 + idx * 22;
  }
  const idx = (side === "in" ? orderIn[slot] : 0) ?? 0;
  return BOARD_HEAD + 15 + idx * 22;
}

function bAnchor(key, side, slot = "") {
  const p = bPos(key);
  if (!p) return null;
  const measured = slot ? B.slotY[`${key}|${side}|${slot}`] : undefined;
  const dy = measured !== undefined ? measured : bDefaultSlotY(key, side, slot);
  return side === "out" ? { x: p.x + bCardW(key) + 1, y: p.y + dy } : { x: p.x - 3, y: p.y + dy };
}

function bCurve(a, b) {
  if (B.wireStyle === "straight") {
    return `M${a.x} ${a.y}L${b.x} ${b.y}`;
  }
  if (B.wireStyle === "orthogonal") {
    const midX = a.x + (b.x - a.x) / 2;
    return `M${a.x} ${a.y}L${midX} ${a.y}L${midX} ${b.y}L${b.x} ${b.y}`;
  }
  const span = Math.abs(b.x - a.x);
  const dx = Math.max(54, span * 0.48);
  return `M${a.x} ${a.y}C${a.x + dx} ${a.y} ${b.x - dx} ${b.y} ${b.x} ${b.y}`;
}

function bLinksSvg() {
  const model = bModel();
  const sig = model.cards.map((c) => c.key).join(",");
  if (B.sig && sig !== B.sig && !bTyping() && !B.drag && !B.requeued) {
    B.requeued = true;
    setTimeout(() => { B.requeued = false; rerenderBoard(); }, 0);
  }
  let minX = 0, minY = 0, maxX = 1200, maxY = 800;
  for (const cd of model.cards) {
    const p = bPos(cd.key);
    if (!p) continue;
    minX = Math.min(minX, p.x); minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x + bCardW(cd.key)); maxY = Math.max(maxY, p.y + bCardH(cd.key));
  }
  const M = 1600, x0 = Math.floor(minX - M), y0 = Math.floor(minY - M), w = Math.ceil(maxX - minX + 2 * M), hgt = Math.ceil(maxY - minY + 2 * M);
  const marker = (id, d, cls) => svgEl("marker", { id, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 9, markerHeight: 9, markerUnits: "userSpaceOnUse", orient: "auto" },
    svgEl("path", { d, class: cls }));
  const svg = svgEl("svg", {
    class: "board-links", "aria-label": "Dây nối", viewBox: `${x0} ${y0} ${w} ${hgt}`, width: w, height: hgt,
    style: `left:${x0}px;top:${y0}px`,
  },
    svgEl("defs", {},
      marker("bm-arrow", "M0 1L10 5L0 9z", "mk-arrow"),
      marker("bm-char", "M0 1L10 5L0 9z", "mk-char"),
      marker("bm-loc", "M0 1L10 5L0 9z", "mk-loc"),
      marker("bm-image", "M0 1L10 5L0 9z", "mk-image"),
      marker("bm-video", "M0 1L10 5L0 9z", "mk-video"),
      marker("bm-model", "M0 1L10 5L0 9z", "mk-model"),
      marker("bm-sel", "M0 1L10 5L0 9z", "mk-sel"),
      marker("bm-open", "M1 1.5L9 5L1 8.5z", "mk-open")));
  bSelLink(model.links);
  for (const L of model.links) {
    const a = bAnchor(L.from, "out", L.fromSlot), b = bAnchor(L.to, "in", L.toSlot);
    if (!a || !b) continue;
    const d = bCurve(a, b), sel = B.sel === L.id;
    const cls = [
      "board-link",
      `dtype-${L.dtype}`,
      L.kind,
      L.kind === "char" ? (L.explicit ? "explicit" : "auto") : "",
      L.desc ? "desc" : "",
      L.kind === "vidout" && !L.done ? "vid-todo" : "",
      sel ? "selected" : "",
    ].filter(Boolean).join(" ");
    const tip = L.kind === "char"
      ? [`[CHARACTER] ${L.name} → ${L.sid}`, L.explicit ? "Đã nối thủ công" : "Tự động theo tên trong prompt", L.desc ? `Chỉ gửi mô tả (${L.reason})` : "Gửi kèm ảnh tham chiếu"].join("\n")
      : L.kind === "loc" ? `[LOCATION] Bối cảnh “${L.name}” → ${L.sid}`
      : L.kind === "cont" ? `[IMAGE] Nối tiếp keyframe ${L.fromSid} → ${L.sid} (cùng bối cảnh)`
      : L.kind === "kfvid" ? `[IMAGE] Keyframe ${L.sid} → VideoSampler ${L.sid}`
      : L.kind === "vidout" ? `[VIDEO] Clip ${L.sid} → SaveFilm (${L.done ? "đã có clip" : "chưa render"})`
      : `[MODEL] Cấu hình phim → ${L.sid}`;
    svg.append(svgEl("g", {
      class: cls, "data-id": L.id, "data-from": L.from, "data-from-slot": L.fromSlot || "",
      "data-to": L.to, "data-to-slot": L.toSlot || "", "data-dtype": L.dtype || "",
      tabindex: "0", role: "button", "aria-label": tip.replace(/\n/g, " · "),
    },
      svgEl("title", {}, tip),
      svgEl("path", { class: "halo", d }),
      svgEl("path", { class: "hit", d }),
      svgEl("path", { class: "line", d, "marker-end": `url(#${bMarker(L.dtype, L.desc, sel)})` })));
  }
  return svg;
}

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

function bUpdateLinksFor(key) {
  document.querySelectorAll(`.board-links .board-link[data-from="${CSS.escape(key)}"], .board-links .board-link[data-to="${CSS.escape(key)}"]`).forEach((g) => {
    const a = bAnchor(g.dataset.from, "out", g.dataset.fromSlot), b = bAnchor(g.dataset.to, "in", g.dataset.toSlot);
    if (!a || !b) return;
    const d = bCurve(a, b);
    g.querySelectorAll("path").forEach((p) => p.setAttribute("d", d));
  });
}

/* ---------- ComfyUI Node Shells & Cards ---------- */

function bSlotRowIn({ slot, dtype, label, valText, linked, title, onClear }) {
  return h("div", { class: "cf-slot-row" },
    h("span", {
      class: "board-port in" + (linked ? " linked" : ""),
      "data-port": "in", "data-slot": slot, "data-dtype": dtype,
      title: title || label,
    }),
    h("span", { class: "cf-slot-name" }, label),
    valText ? h("span", { class: "cf-slot-val", title: valText }, valText) : null,
    onClear ? h("button", { class: "cf-slot-clear", title: "Ngắt kết nối này", onclick: (e) => { e.stopPropagation(); onClear(); } }, "×") : null);
}

function bSlotRowOut({ slot, dtype, label, linked, disabledWhy, title }) {
  return h("div", { class: "cf-slot-row" },
    h("span", { class: "cf-slot-name" }, label),
    h("span", {
      class: "board-port out" + (linked ? " linked" : "") + (disabledWhy ? " disabled" : ""),
      "data-port": "out", "data-slot": slot, "data-dtype": dtype,
      title: disabledWhy || title || `Kéo cổng ${label} để nối`,
    }));
}

function bToggleCollapse(key) {
  if (B.collapsed.has(key)) B.collapsed.delete(key);
  else B.collapsed.add(key);
  const el = document.querySelector(`.board-card[data-key="${CSS.escape(key)}"]`);
  if (el) {
    el.classList.toggle("is-collapsed", B.collapsed.has(key));
    const btn = el.querySelector(".cf-collapse");
    if (btn) btn.textContent = B.collapsed.has(key) ? "▸" : "▾";
  }
  bMeasureSlots();
  refreshZone("board-groups");
  refreshZone("board-links");
  bUpdateMinimap();
}

function bCardShell(cd, label, headKids, slotsZoneFn, bodyKids) {
  const isCol = B.collapsed.has(cd.key);
  const el = h("article", {
    class: `card board-card ${cd.type}` + (isCol ? " is-collapsed" : ""),
    "data-key": cd.key, tabindex: "0", role: "group", "aria-label": label,
  },
    h("header", { class: "board-card-head", title: "Kéo tiêu đề để di chuyển node" },
      h("button", { class: "cf-collapse", title: "Thu gọn / Mở rộng node", onclick: (e) => { e.stopPropagation(); bToggleCollapse(cd.key); } }, isCol ? "▸" : "▾"),
      headKids,
      h("span", { class: "cf-nid" }, `#${cd.nid}`)),
    slotsZoneFn ? zone(`bc-slots-${cd.key}`, slotsZoneFn) : null,
    h("div", { class: "board-card-body stack tight" }, bodyKids));
  return el;
}

function bFlags(key, flags) {
  const el = document.querySelector(`.board-card[data-key="${CSS.escape(key)}"]`);
  if (!el) return;
  for (const [k, v] of Object.entries(flags)) el.classList.toggle(k, !!v);
}

function bMoreBtn(items, label) {
  return h("button", {
    class: "btn sm ghost icon", title: "Thêm thao tác", "aria-label": label, "aria-haspopup": "menu",
    onclick: (e) => popMenu(e.currentTarget, items()),
  }, icon("more"));
}

function bLinkedOut(key, slot) {
  return bModel().links.some((l) => l.from === key && (!slot || l.fromSlot === slot));
}

function bCard(cd) {
  if (cd.type === "cfg") return bCfgCard(cd);
  if (cd.type === "char") return bCharCard(cd);
  if (cd.type === "loc") return bLocCard(cd);
  if (cd.type === "kf") return bKfCard(cd);
  if (cd.type === "out") return bOutCard(cd);
  return bVidCard(cd);
}

/* 1. FilmConfig Node */
function bCfgCard(cd) {
  const sb = S.cur.storyboard;
  const imageModels = (S.models.image || []).length ? S.models.image : [{ alias: "nano2", label: "Nano Banana 2" }];
  const vidOpts = videoModelOptions(sb.model);
  return bCardShell(cd, "Cấu hình phim (FilmConfig)",
    [icon("settings"), h("span", { class: "cf-node-title" }, "FilmConfig"), h("span", { class: "spacer" }),
      bMoreBtn(() => [
        { label: "Mở tab Phong cách & nhân vật", icon: "users", onclick: () => switchTab("story") },
      ], "Thao tác FilmConfig")],
    () => h("div", { class: "cf-slots" },
      h("div", { class: "cf-slots-col in" }, h("span", { class: "cf-slot-val" }, sb.model === "muse" ? "Engine: Meta Muse" : "Engine: Google Flow")),
      h("div", { class: "cf-slots-col out" },
        bSlotRowOut({ slot: "MODEL", dtype: "MODEL", label: "MODEL", linked: bLinkedOut(cd.key, "MODEL"), title: "Cấu hình model & phong cách chung" }))),
    [
      h("div", { class: "cf-widgets" },
        h("div", { class: "cf-widget" }, h("span", { class: "cf-w-key" }, "aspect"),
          h("select", { value: sb.aspect || "16:9", onchange: (e) => { sb.aspect = e.target.value; changed(); rerenderBoard(); } },
            h("option", { value: "16:9" }, "16:9 (Ngang)"), h("option", { value: "9:16" }, "9:16 (Dọc)"))),
        h("div", { class: "cf-widget" }, h("span", { class: "cf-w-key" }, "video_model"),
          h("select", { value: sb.model || "", onchange: (e) => { sb.model = e.target.value || undefined; changed(); bRefresh(); } },
            h("option", { value: "" }, "Mặc định"),
            vidOpts.map((m) => h("option", { value: m.alias }, m.alias)))),
        h("div", { class: "cf-widget" }, h("span", { class: "cf-w-key" }, "image_model"),
          h("select", { value: sb.image_model || "nano2", onchange: (e) => { sb.image_model = e.target.value; changed(); } },
            imageModels.map((m) => h("option", { value: m.alias }, m.alias)))),
        h("div", { class: "cf-widget" }, h("span", { class: "cf-w-key" }, "continuity"),
          h("select", { value: sb.continuity === false ? "off" : "on", onchange: (e) => { sb.continuity = e.target.value === "off" ? false : undefined; changed(); bRefresh(); } },
            h("option", { value: "on" }, "Nối KF liên tiếp"), h("option", { value: "off" }, "Tắt nối KF")))),
      h("textarea", {
        class: "board-desc", rows: 2, value: sb.style || "", "aria-label": "Phong cách hình ảnh",
        placeholder: "style: cinematic, warm morning light, 35mm film grain…",
        oninput: (e) => { sb.style = e.target.value; changed(); },
      }),
    ]);
}

/* 2. CharacterLoader Node */
function bCharCard(cd) {
  const sb = S.cur.storyboard, c = cd.c, i = cd.i, key = cd.key;
  const z = (part) => `bc-${part}-${key}`;
  const nameInput = h("input", {
    type: "text", class: "board-name", value: c.name || "", placeholder: "Tên, vd: Lan", "aria-label": "Tên nhân vật",
    "aria-invalid": cd.bad ? "true" : null,
    oninput: (e) => { c.name = e.target.value; changed(); bRefreshNames(); },
  });
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
        return running ? h("span", { class: "badge mini running" }, "Đang tạo…") : failed ? h("span", { class: "badge mini failed" }, "Lỗi")
          : progQueued(progCharKey(c)) ? h("span", { class: "badge mini queued" }, "Chờ…") : h("span");
      }),
      bMoreBtn(() => [
        { label: "Mở trong tab Nhân vật", icon: "users", onclick: () => switchTab("story") },
        { label: "Chọn ảnh có sẵn…", icon: "image", onclick: () => bPickImage(c) },
        "-",
        { label: "Xoá nhân vật", icon: "trash", danger: true, onclick: async () => { if (await removeCharacter(i)) renderMain(); } },
      ], `Thao tác nhân vật ${c.name || ""}`)],
    () => {
      const locked = charsLocked(sb), errs = S.cur.errors?.length;
      const why = charNameProblems(sb).has(i) ? "Tên trống/trùng — đổi tên trước khi nối" : locked ? "Đổi tên nhân vật trống/trùng trước khi nối"
        : errs ? "Storyboard đang lỗi — chỉ xoá được dây nối" : "";
      const nScenes = bModel().links.filter((l) => l.from === key).length;
      return h("div", { class: "cf-slots" },
        h("div", { class: "cf-slots-col in" }, h("span", { class: "cf-slot-val" }, nScenes ? `Đang nối ${nScenes} cảnh` : "Chưa nối cảnh")),
        h("div", { class: "cf-slots-col out" },
          bSlotRowOut({ slot: "CHARACTER", dtype: "CHARACTER", label: "CHARACTER", linked: nScenes > 0, disabledWhy: why, title: "Kéo vào cổng characters của cảnh" })));
    },
    [
      zone(z("media"), () => {
        const url = charImageUrl(c), cs = S.cur.characters_state?.[i];
        const missing = !!(c.image && cs && cs.name === c.name && cs.image_missing);
        const box = h("div", { class: "media board-char-media" });
        if (bCharRunning(c) && !progOn()) box.append(h("div", { class: "ph" }, h("span", { class: "spinner" }), h("span", {}, "Đang tạo ảnh…")));
        else if (url) box.append(h("img", { src: url, alt: c.name || "", decoding: "async", onclick: () => lightbox(url) }));
        else box.append(h("div", { class: "ph" }, icon("image"), h("span", {}, "Chưa có ảnh nhân vật")));
        if (missing) box.append(h("span", { class: "tag warn" }, "Thiếu file ảnh"));
        return progAttach(box, [progCharKey(c)]);
      }),
      h("textarea", {
        class: "board-desc", rows: 2, value: c.description || "", "aria-label": "Mô tả ngoại hình",
        placeholder: "Vietnamese woman, late 20s, shoulder-length black hair…",
        oninput: (e) => { c.description = e.target.value; changed(); refreshZone(z("actions")); },
      }),
      zone(z("msg"), () => {
        const cs = S.cur.characters_state?.[i], kids = [];
        if (charNameProblems(sb).has(i)) kids.push(h("div", { class: "scene-msg warn" }, "Tên trống/trùng — đổi tên trước khi nối"));
        if (c.image && cs && cs.name === c.name && cs.image_missing) kids.push(h("div", { class: "scene-msg warn" }, "Không thấy file ảnh — tạo lại hoặc tải lên."));
        const failed = !bCharRunning(c) && bCharFailed(c);
        if (failed) kids.push(h("div", { class: "scene-msg err" }, failed));
        return h("div", { class: "stack tight" + (kids.length ? "" : " hidden") }, kids);
      }),
      zone(z("actions"), () => {
        const busy = jobRunning(), url = charImageUrl(c), canMake = !!(String(c.name || "").trim() && String(c.description || "").trim());
        const failed = !bCharRunning(c) && bCharFailed(c);
        const errs = S.cur.errors?.length;
        return h("div", { class: "board-card-actions row" },
          h("button", {
            class: "btn sm", disabled: busy || !canMake || !!errs,
            title: !canMake ? "Cần nhập tên và mô tả trước" : "Ảnh trên Flow miễn phí",
            onclick: () => makePortraits([c.name], !!url),
          }, icon(url || failed ? "refresh" : "image"), failed ? "Thử lại" : url ? "Tạo lại" : "Tạo ảnh", FREE()),
          h("button", { class: "btn sm ghost icon", title: "Tải ảnh lên", "aria-label": "Tải ảnh lên", onclick: () => fileInput.click() }, icon("upload")),
          fileInput);
      }),
    ]);
  if (cd.bad) el.classList.add("is-invalid");
  return el;
}

/* 3. LocationLoader Node */
function bLocCard(cd) {
  const sb = S.cur.storyboard, l = cd.l, i = cd.i, key = cd.key;
  const z = (part) => `bc-${part}-${key}`;
  const nameInput = h("input", {
    type: "text", class: "board-name", value: l.name || "", placeholder: "Bối cảnh, vd: Phòng khách", "aria-label": "Tên bối cảnh",
    "aria-invalid": cd.bad ? "true" : null,
    oninput: (e) => { l.name = e.target.value; changed(); bRefreshNames(); },
  });
  const fileInput = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", class: "hidden", onchange: async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f); fd.append("kind", "location");
    try {
      const r = await api("POST", `/api/projects/${S.cur.id}/upload`, fd, true);
      S.assets = await api("GET", `/api/projects/${S.cur.id}/assets`);
      const imp = locationImpact(sb, S.cur.scene_state, { location: l, kind: "image" });
      if (!(await confirmLocImpact(imp, "Dùng ảnh bối cảnh vừa tải lên"))) return;
      l.image = r.path; changed(); bRefresh();
    } catch (err) { toast(err.message, "error"); }
  } });

  const el = bCardShell(cd, `Bối cảnh ${l.name || "chưa đặt tên"}`,
    [icon("image"), nameInput,
      zone(z("badge"), () => {
        const bad = locNameProblems(sb).has(i);
        const inp = document.querySelector(`.board-card[data-key="${CSS.escape(key)}"] .board-name`);
        if (inp) { if (bad) inp.setAttribute("aria-invalid", "true"); else inp.removeAttribute("aria-invalid"); }
        const running = bLocRunning(l), failed = bLocFailed(l);
        bFlags(key, { "is-invalid": bad, "is-running": running, "is-failed": !!failed && !running });
        return running ? h("span", { class: "badge mini running" }, "Đang tạo…") : failed ? h("span", { class: "badge mini failed" }, "Lỗi")
          : progQueued(progLocKey(l)) ? h("span", { class: "badge mini queued" }, "Chờ…") : h("span");
      }),
      bMoreBtn(() => [
        { label: "Mở trong tab Bối cảnh", icon: "image", onclick: () => switchTab("story") },
        "-",
        { label: "Xoá bối cảnh", icon: "trash", danger: true, onclick: async () => {
          const n = locationImpact(sb, S.cur.scene_state, { location: l, kind: "delete" });
          if (!(await confirmModal("Xoá bối cảnh?", `Xoá “${l.name || "bối cảnh"}” khỏi storyboard?` + (n ? ` (${n} cảnh đã có clip sẽ chuyển Đã đổi)` : ""), "Xoá", true))) return;
          removeLocation(sb, i); changed(); renderMain();
        } },
      ], `Thao tác bối cảnh ${l.name || ""}`)],
    () => {
      const locked = locsLocked(sb), errs = S.cur.errors?.length;
      const why = locNameProblems(sb).has(i) ? "Tên trống/trùng — đổi tên trước khi nối" : locked ? "Đổi tên bối cảnh trống/trùng trước khi nối"
        : errs ? "Storyboard đang lỗi — chỉ xoá được dây nối" : "";
      const nScenes = bModel().links.filter((lk) => lk.from === key).length;
      return h("div", { class: "cf-slots" },
        h("div", { class: "cf-slots-col in" }, h("span", { class: "cf-slot-val" }, nScenes ? `Đang gắn ${nScenes} cảnh` : "Chưa gắn cảnh")),
        h("div", { class: "cf-slots-col out" },
          bSlotRowOut({ slot: "LOCATION", dtype: "LOCATION", label: "LOCATION", linked: nScenes > 0, disabledWhy: why, title: "Kéo vào cổng location của cảnh" })));
    },
    [
      zone(z("media"), () => {
        const url = locImageUrl(l), ls = S.cur.locations_state?.[i];
        const missing = !!(l.image && ls && ls.name === l.name && ls.image_missing);
        const box = h("div", { class: "media board-loc-media" });
        if (bLocRunning(l) && !progOn()) box.append(h("div", { class: "ph" }, h("span", { class: "spinner" }), h("span", {}, "Đang tạo bối cảnh…")));
        else if (url) box.append(h("img", { src: url, alt: l.name || "", decoding: "async", onclick: () => lightbox(url) }));
        else box.append(h("div", { class: "ph" }, icon("image"), h("span", {}, "Chưa có ảnh bối cảnh")));
        if (missing) box.append(h("span", { class: "tag warn" }, "Thiếu file ảnh"));
        return progAttach(box, [progLocKey(l)]);
      }),
      h("textarea", {
        class: "board-desc", rows: 2, value: l.description || "", "aria-label": "Mô tả bối cảnh",
        placeholder: "dim family living room, teak table, warm lamp light…",
        oninput: (e) => { l.description = e.target.value; changed(); refreshZone(z("actions")); },
      }),
      zone(z("msg"), () => {
        const failed = !bLocRunning(l) && bLocFailed(l);
        return h("div", { class: "stack tight" + (failed ? "" : " hidden") }, failed ? h("div", { class: "scene-msg err" }, failed) : null);
      }),
      zone(z("actions"), () => {
        const busy = jobRunning(), url = locImageUrl(l), canMake = locCanMake(l);
        const errs = S.cur.errors?.length;
        return h("div", { class: "board-card-actions row" },
          h("button", {
            class: "btn sm", disabled: busy || !canMake || !!errs,
            title: !canMake ? "Cần nhập tên và mô tả trước" : "Ảnh trên Flow miễn phí",
            onclick: async () => {
              const imp = locationImpact(sb, S.cur.scene_state, { location: l, kind: "image" });
              if (!(await confirmLocImpact(imp, url ? "Tạo lại ảnh bối cảnh" : "Tạo ảnh bối cảnh"))) return;
              makeLocationImages([l.name], true);
            },
          }, icon(url ? "refresh" : "image"), url ? "Tạo lại" : "Tạo ảnh", FREE()),
          h("button", { class: "btn sm ghost icon", title: "Tải ảnh bối cảnh lên", "aria-label": "Tải ảnh lên", onclick: () => fileInput.click() }, icon("upload")),
          fileInput);
      }),
    ]);
  if (cd.bad) el.classList.add("is-invalid");
  return el;
}

/* 4. KeyframeSampler Node */
function bKfCard(cd) {
  const sc = cd.sc, key = cd.key, sid = sc.id, row = cd.row;
  const z = (part) => `bc-${part}-${key}`;
  const st = () => S.cur.scene_state?.[sc.id] || {};
  const failedKf = () => {
    const j = S.job;
    if (jobRunning() && progOn() && progFailed(`kf:${sid}`)) return j.result?.failed?.[sid] || PROG_FAILED_MSG;
    if (j && j.kind === "keyframes" && j.project === S.cur.id && j.status !== "running" && j.result?.failed?.[sid]) return j.result.failed[sid];
    const s = st();
    return s.status === "failed" && s.error && (!s.keyframe_url || s.keyframe_stale) ? s.error : null;
  };
  const shots = S.env?.camera_presets?.shot || [];

  return bCardShell(cd, `Keyframe cảnh ${sid}`,
    [icon("image"), h("span", { class: "cf-node-title mono" }, sid), h("span", { class: "cf-node-sub" }, "Keyframe"),
      zone(z("badge"), () => {
        const s = st(), running = bSceneRunning(sc, "kf"), failed = !running && !!failedKf();
        bFlags(key, { "is-running": running, "is-failed": failed });
        const b = running ? badge("running", "Đang tạo…") : failed ? badge("failed", "Lỗi") : progQueued(`kf:${sid}`) ? badge("queued", "Chờ…")
          : !s.keyframe_url ? badge("todo", "Chưa có")
          : s.keyframe_stale ? badge("changed", "Ảnh cũ") : badge("done", "Xong");
        b.classList.add("mini");
        return b;
      }),
      h("span", { class: "spacer" }),
      bMoreBtn(() => [
        { label: "Sửa prompt & phụ đề…", icon: "edit", onclick: () => bEditPrompt(sc, key) },
        { label: "Xem ảnh lớn", icon: "eye", disabled: !st().keyframe_url, onclick: () => st().keyframe_url && lightbox(st().keyframe_url) },
        { label: "Mở trong tab Cảnh", icon: "video", onclick: () => switchTab("scenes") },
      ], `Thao tác keyframe ${sid}`)],
    () => {
      const L = sceneLinks(sc);
      const li = bLocIndex(sc.location);
      const locObj = li >= 0 ? bLocs()[li] : null;
      const prevSid = bContinuityPrevSid(row);
      const whyOut = S.cur.errors?.length && !bVideoShown(sc) ? "Storyboard đang lỗi — sửa lỗi trước" : "";
      return h("div", { class: "cf-slots" },
        h("div", { class: "cf-slots-col in" },
          bSlotRowIn({
            slot: "characters", dtype: "CHARACTER", label: "characters",
            valText: L.names.length ? `(${L.names.length})` : "",
            linked: L.idx.length > 0, title: L.names.length ? `Nhân vật: ${L.names.join(", ")}` : "Kéo cổng CHARACTER của nhân vật vào đây",
          }),
          bSlotRowIn({
            slot: "location", dtype: "LOCATION", label: "location",
            valText: locObj ? locObj.name : "",
            linked: !!locObj, title: locObj ? `Bối cảnh: ${locObj.name}` : "Kéo cổng LOCATION của bối cảnh vào đây",
            onClear: locObj ? () => editSceneLocation(sc, "") : null,
          }),
          bSlotRowIn({
            slot: "prev_image", dtype: "IMAGE", label: "prev_image",
            valText: prevSid ? `← ${prevSid}` : "",
            linked: !!prevSid, title: prevSid ? `Đang nối tiếp keyframe từ cảnh ${prevSid}` : "Kéo IMAGE từ Keyframe cảnh liền trước để nối tiếp",
            onClear: prevSid ? () => editSceneContinuity(prevSid, sid, false) : null,
          })),
        h("div", { class: "cf-slots-col out" },
          bSlotRowOut({
            slot: "IMAGE", dtype: "IMAGE", label: "IMAGE",
            linked: bLinkedOut(key, "IMAGE"), disabledWhy: whyOut,
            title: "Đầu ra Keyframe — kéo sang cảnh kế để nối tiếp hoặc hiện VideoSampler",
          })));
    },
    [
      zone(z("media"), () => {
        const s = st(), box = h("div", { class: "media" + (bScenePortrait() ? " portrait" : "") });
        if (bSceneRunning(sc, "kf") && !s.keyframe_url && !progOn()) box.append(h("div", { class: "ph" }, h("span", { class: "spinner" }), h("span", {}, "Đang tạo keyframe…")));
        else if (s.keyframe_url) {
          box.append(h("img", { src: s.keyframe_url, alt: "keyframe " + sid, decoding: "async", onclick: () => lightbox(s.keyframe_url) }));
          if (s.keyframe_stale) box.append(h("span", { class: "tag warn", title: "Prompt, nhân vật hoặc bối cảnh đã đổi" }, "Ảnh cũ · tạo lại"));
        } else box.append(h("div", { class: "ph" }, icon("image"), h("span", {}, "Chưa có keyframe")));
        return progAttach(box, [`kf:${sid}`]);
      }),
      h("div", { class: "cf-widgets" },
        h("div", { class: "cf-widget" }, h("span", { class: "cf-w-key" }, "shot"),
          h("select", {
            value: sc.camera?.shot || "",
            onchange: (e) => {
              sc.camera = sc.camera || {};
              if (e.target.value) sc.camera.shot = e.target.value; else delete sc.camera.shot;
              if (!Object.keys(sc.camera).length) delete sc.camera;
              changed();
            },
          },
            h("option", { value: "" }, "Tự động"),
            shots.map((o) => h("option", { value: o.value }, o.value))))),
      zone(z("sum"), () => {
        const t = sc.image_prompt || sc.prompt || "";
        return h("p", { class: "board-sum", title: "Bấm để sửa prompt mở đầu & hành động", onclick: () => bEditPrompt(sc, key) },
          t || h("span", { class: "muted" }, "(bấm để nhập image_prompt…)"));
      }),
      zone(z("msg"), () => {
        const kids = bSceneCharMsgs(sc);
        if (failedKf() && !bSceneRunning(sc, "kf")) kids.unshift(h("div", { class: "scene-msg err" }, failedKf()));
        return h("div", { class: "stack tight" + (kids.length ? "" : " hidden") }, kids);
      }),
      zone(z("actions"), () => {
        const s = st(), busy = jobRunning(), errs = S.cur.errors?.length, hasKf = !!s.keyframe_url;
        const failed = !!failedKf() && !bSceneRunning(sc, "kf");
        const kids = [h("div", { class: "board-card-actions row" },
          h("button", {
            class: "btn sm", disabled: busy || !!errs,
            title: busy ? "Đang có việc chạy" : errs ? "Storyboard đang lỗi" : "Ảnh trên Flow miễn phí",
            onclick: () => runAction("keyframes", { only: [sid], force_keyframes: hasKf ? [sid] : [] }),
          }, icon(hasKf || failed ? "refresh" : "image"), failed ? "Thử lại" : hasKf ? "Tạo lại" : "Tạo KF", FREE()),
          bVideoShown(sc) ? null : h("button", { class: "btn sm ghost", disabled: !!errs, onclick: () => bShowVideo(sc) }, icon("arrowRight"), "Video"))];
        if (hasKf && !s.keyframe_stale && typeof renderKeyframeQC === "function") kids.push(renderKeyframeQC(sc, s, busy));
        return h("div", { class: "stack tight" }, kids);
      }),
    ]);
}

/* 5. VideoSampler Node */
function bVidCard(cd) {
  const sc = cd.sc, key = cd.key, sid = sc.id, mode = bMode(sc);
  const z = (part) => `bc-${part}-${key}`;
  const st = () => S.cur.scene_state?.[sc.id] || {};
  const moves = S.env?.camera_presets?.movement || [];

  return bCardShell(cd, `VideoSampler cảnh ${sid}`,
    [icon("video"), h("span", { class: "cf-node-title mono" }, sid), h("span", { class: "cf-node-sub" }, "VideoSampler"),
      zone(z("badge"), () => {
        const k = sceneStatusKey(sc), s = st();
        bFlags(key, { "is-running": k === "running", "is-failed": k === "failed" || (s.status === "failed" && !!s.error) });
        const b = k === "queued" ? badge("queued", "Chờ…") : badge(STATUS[k] ? k : "todo");
        b.classList.add("mini");
        return b;
      }),
      h("span", { class: "spacer" }),
      bMoreBtn(() => {
        const why = mode !== "keyframe" ? "Cảnh này luôn hiện node VideoSampler" : bDeleteBlocked({ kind: "kfvid", sid });
        return [
          { label: "Sửa prompt & phụ đề…", icon: "edit", onclick: () => bEditPrompt(sc, key) },
          { label: "Mở trong tab Cảnh", icon: "video", onclick: () => switchTab("scenes") },
          {
            label: "Ẩn node Video này", icon: "eye", disabled: !!why,
            extra: why ? h("span", { class: "board-menu-why" }, why.replace(/ — .*$/, "")) : null,
            onclick: () => { B.hideIdleVideos = true; localSet("boardHideIdleVid", true); B.videos.delete(sid); if (B.sel === `k>${sid}`) B.sel = null; rerenderBoard(); saveBoard(); },
          },
        ];
      }, `Thao tác video ${sid}`)],
    () => {
      const inRows = [];
      if (mode === "keyframe") {
        inRows.push(bSlotRowIn({ slot: "start_image", dtype: "IMAGE", label: "start_image", valText: `kf:${sid}`, linked: true, title: "Ảnh mở đầu từ KeyframeSampler" }));
      } else if (mode === "t2v") {
        const L = sceneLinks(sc), li = bLocIndex(sc.location), locObj = li >= 0 ? bLocs()[li] : null;
        inRows.push(bSlotRowIn({ slot: "characters", dtype: "CHARACTER", label: "characters", valText: L.names.length ? `(${L.names.length})` : "", linked: L.idx.length > 0 }));
        inRows.push(bSlotRowIn({ slot: "location", dtype: "LOCATION", label: "location", valText: locObj ? locObj.name : "", linked: !!locObj, onClear: locObj ? () => editSceneLocation(sc, "") : null }));
      } else {
        inRows.push(bSlotRowIn({ slot: "image", dtype: "IMAGE", label: "image", valText: sc.image ? sc.image.split("/").pop() : "chưa chọn", linked: !!sc.image }));
      }
      return h("div", { class: "cf-slots" },
        h("div", { class: "cf-slots-col in" }, inRows),
        h("div", { class: "cf-slots-col out" },
          bSlotRowOut({ slot: "VIDEO", dtype: "VIDEO", label: "VIDEO", linked: true, title: "Clip đầu ra nối tới SaveFilm" })));
    },
    [
      h("div", { class: "cf-widgets" },
        h("div", { class: "cf-widget" }, h("span", { class: "cf-w-key" }, "mode"),
          h("select", { value: mode, onchange: (e) => { sc.mode = e.target.value; changed(); rerenderBoard(); } },
            MODES.map(([v, l]) => h("option", { value: v }, l)))),
        h("div", { class: "cf-widget" }, h("span", { class: "cf-w-key" }, "duration"),
          h("select", { value: String(sc.duration || 8), onchange: (e) => { sc.duration = +e.target.value; changed(); bRefresh(); } },
            [4, 6, 8].map((d) => h("option", { value: String(d) }, `${d} giây`)))),
        h("div", { class: "cf-widget" }, h("span", { class: "cf-w-key" }, "motion"),
          h("select", {
            value: sc.camera?.movement || "",
            onchange: (e) => {
              sc.camera = sc.camera || {};
              if (e.target.value) sc.camera.movement = e.target.value; else delete sc.camera.movement;
              if (!Object.keys(sc.camera).length) delete sc.camera;
              changed();
            },
          },
            h("option", { value: "" }, "Mặc định"),
            moves.map((o) => h("option", { value: o.value }, o.value))))),
      zone(z("media"), () => {
        const el = renderSceneMedia(sc, { kfHint: "Tạo keyframe ở node bên trái để xem trước", lazy: true, prog: [`vid:${sid}`] });
        if (!st().clip_url) el.classList.add("board-vid-pending");
        return el;
      }),
      zone(z("sum"), () => {
        const t = sc.prompt || "";
        return h("p", { class: "board-sum", title: "Bấm để sửa prompt chuyển động", onclick: () => bEditPrompt(sc, key) },
          t || h("span", { class: "muted" }, "(bấm để nhập prompt hành động…)"));
      }),
      zone(z("msg"), () => { const kids = mode === "t2v" ? bSceneCharMsgs(sc) : []; return h("div", { class: "stack tight" + (kids.length ? "" : " hidden") }, kids); }),
      zone(z("actions"), () => {
        return h("div", { class: "board-card-actions" },
          renderSceneActions(sc, { keyframe: false, retry: true, blocked: S.cur.errors?.length ? "Storyboard đang lỗi — sửa lỗi ở khung phía trên trước" : "" }));
      }),
    ]);
}

/* 6. SaveFilm (Final Stitch Output) Node */
function bOutCard(cd) {
  const key = cd.key;
  const z = (part) => `bc-${part}-${key}`;
  return bCardShell(cd, "SaveFilm · Ghép phim hoàn chỉnh",
    [icon("stitch"), h("span", { class: "cf-node-title" }, "SaveFilm"),
      zone(z("badge"), () => {
        const hasFinal = !!S.cur.final_url;
        return hasFinal ? h("span", { class: "badge mini done" }, "Có phim") : h("span", { class: "badge mini todo" }, "Chờ clip");
      }),
      h("span", { class: "spacer" }),
      bMoreBtn(() => [
        { label: "Mở tab Ghép phim & ảnh bìa", icon: "stitch", onclick: () => switchTab("final") },
      ], "Thao tác SaveFilm")],
    () => {
      const total = bScenes().length;
      const done = bScenes().filter((s) => S.cur.scene_state?.[s.id]?.status === "done" && S.cur.scene_state?.[s.id]?.clip_url).length;
      return h("div", { class: "cf-slots" },
        h("div", { class: "cf-slots-col in" },
          bSlotRowIn({ slot: "clips", dtype: "VIDEO", label: "clips", valText: `${done}/${total} xong`, linked: total > 0, title: "Nhận clip từ tất cả các cảnh" })),
        h("div", { class: "cf-slots-col out" }));
    },
    [
      zone(z("progress"), () => {
        const total = Math.max(1, bScenes().length);
        const done = bScenes().filter((s) => S.cur.scene_state?.[s.id]?.status === "done" && S.cur.scene_state?.[s.id]?.clip_url).length;
        const pct = Math.round((done / total) * 100);
        return h("div", { class: "stack tight" },
          h("div", { class: "cf-widget" }, h("span", { class: "cf-w-key" }, "ready"), h("span", { class: "cf-w-val" }, `${done}/${bScenes().length} cảnh (${pct}%)`)),
          h("div", { class: "cf-film-meter" }, h("i", { style: { width: pct + "%" } })));
      }),
      zone(z("media"), () => {
        if (S.cur.final_url) {
          return h("div", { class: "media" + (bScenePortrait() ? " portrait" : "") },
            h("video", { src: S.cur.final_url, controls: true, preload: "metadata" }));
        }
        return h("div", { class: "media board-loc-media" },
          h("div", { class: "ph" }, icon("film"), h("span", {}, "Ghép tất cả clip thành final.mp4")));
      }),
      zone(z("actions"), () => {
        const busy = jobRunning();
        const ready = bScenes().some((s) => S.cur.scene_state?.[s.id]?.clip_url);
        return h("div", { class: "board-card-actions row" },
          h("button", { class: "btn sm primary", disabled: busy || !ready, title: ready ? "Ghép các clip đã xong thành final.mp4" : "Cần ít nhất 1 clip đã render", onclick: () => runAction("stitch") },
            icon("stitch"), "Ghép phim", FREE()),
          S.env?.capcut ? h("button", { class: "btn sm", disabled: busy || !ready, onclick: () => runAction("capcut") }, "CapCut") : null,
          h("button", { class: "btn sm ghost", onclick: () => switchTab("final") }, "Chi tiết"));
      }),
    ]);
}

function bPickImage(c) {
  const imgs = otherImageAssets();
  const isAsset = !c.image || imgs.some((a) => a.path === c.image);
  const sel = h("select", { value: c.image || "", "aria-label": "Ảnh nhân vật" },
    h("option", { value: "" }, "— không dùng ảnh —"),
    isAsset ? null : h("option", { value: c.image }, c.image),
    imgs.map((a) => h("option", { value: a.path }, a.path.replace("assets/", ""))));
  modal({
    title: `Ảnh của ${c.name || "nhân vật"}`,
    body: h("div", { class: "stack" }, field("Ảnh có sẵn trong dự án", sel),
      imgs.length ? null : h("p", { class: "help" }, "Dự án chưa có ảnh nào. Dùng nút Tải ảnh lên trên node.")),
    buttons: [{ label: "Huỷ" }, { label: "Dùng ảnh này", cls: "primary", onclick: (close) => { c.image = sel.value || undefined; changed(); close(); bRefresh(); } }],
  });
}

function bEditPrompt(sc, key) {
  const z = `bc-sum-${key}`;
  const ta = (k, label, ph, rows = 3) => field(label, h("textarea", {
    rows, value: sc[k] || "", placeholder: ph,
    oninput: (e) => { sc[k] = e.target.value; changed(); refreshZone(z); },
  }), "nên viết tiếng Anh");
  modal({
    title: `Node Cảnh ${sc.id}`, wide: true,
    body: h("div", { class: "stack" },
      bMode(sc) === "keyframe" ? ta("image_prompt", "Ảnh mở đầu (image_prompt)", "Mô tả khung hình đầu tiên: chủ thể, vị trí đứng, bối cảnh, ánh sáng…") : null,
      ta("prompt", "Hành động / Chuyển động (prompt)", "Một hành động rõ ràng, ví dụ: Lan lifts the glass and smiles…"),
      subtitleFields(S.cur.storyboard, sc, changed),
      h("p", { class: "help" }, "Tự lưu khi bạn gõ. Sửa prompt có thể đánh dấu keyframe/clip đã có thành “Đã sửa”.")),
    buttons: [{ label: "Xong", cls: "primary" }],
  });
}

/* Empty placeholders when project has no scenes yet */
function bEmpties(world) {
  if (!bScenes().length) {
    world.append(h("div", { class: "board-empty empty-inline", "data-empty": "scene" },
      icon("video"), "Chưa có cảnh nào trên bảng",
      h("button", { class: "btn sm primary", onclick: () => bAddScene("keyframe") }, icon("plus"), "Thêm cảnh đầu tiên")));
  }
}
function bPlaceEmpties(root) {
  const xKf = BOARD_ORIGIN + BOARD_W_CFG + BOARD_GAP_COL;
  root.querySelectorAll(".board-empty").forEach((el) => {
    el.style.left = xKf + "px";
    el.style.top = BOARD_ORIGIN + "px";
  });
}

function bRefresh() {
  B.mc = null;
  refreshZones();
  requestAnimationFrame(() => {
    bMeasureSlots();
    refreshZone("board-groups");
    refreshZone("board-links");
    bUpdateMinimap();
  });
}

function bRefreshNames() {
  B.mc = null;
  for (const id of [...S.zones.keys()]) {
    if (/^bc-(badge|msg|actions|slots)-(char|loc):/.test(id) || id === "board-links" || id === "board-notes" || id === "board-sel") refreshZone(id);
  }
}

/* ---------- Node Creation Modals (Character, Location, Scene) ---------- */

async function bRelayout() {
  if (!(await confirmModal("Tự xếp lại bố cục DAG?", "Sắp xếp lại mọi node theo luồng từ trái sang phải (Sources → Keyframe → VideoSampler → SaveFilm).", "Xếp lại"))) return;
  autoLayout(true);
  B.view = null;
  rerenderBoard();
  setTimeout(() => saveBoard(0), 60);
}

function bShowVideo(sc) {
  B.videos.add(sc.id);
  B.saveAfterMount = true;
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
    const err = !v ? "Nhập tên nhân vật." : sb.characters.some((c) => bKey(c.name) === bKey(v)) ? `Đã có nhân vật “${v}”.` : "";
    msg.textContent = err; msg.classList.toggle("hidden", !err);
    if (err) name.setAttribute("aria-invalid", "true"); else name.removeAttribute("aria-invalid");
    return !err;
  };
  const go = (close) => {
    if (!check()) { name.focus(); return; }
    const c = { name: name.value.trim(), description: desc.value.trim() };
    const placed = [];
    let bottom = BOARD_ORIGIN;
    for (const cd of boardModel().cards) {
      const p = bPos(cd.key);
      if (p) {
        placed.push({ x: p.x, y: p.y, w: bCardW(cd.key), h: bCardH(cd.key) });
        if (cd.type === "cfg" || cd.type === "char") bottom = Math.max(bottom, p.y + bCardH(cd.key));
      }
    }
    const r = placeFree({ x: BOARD_ORIGIN, y: bSnap(bottom + BOARD_GAP_ROW), w: BOARD_W_CHAR, h: bFallbackH("char:") }, placed);
    sb.characters.push(c);
    B.pos.set(c, { x: r.x, y: r.y });
    changed();
    close();
    rerenderBoard();
    bScrollTo(`char:#${sb.characters.length - 1}`);
    saveBoard();
  };
  name.addEventListener("input", () => { if (!msg.classList.contains("hidden")) check(); });
  name.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); desc.focus(); } });
  modal({
    title: "Thêm Node Nhân vật (CharacterLoader)",
    body: h("div", { class: "stack" }, field("Tên nhân vật", name, "bắt buộc, không trùng"), field("Mô tả ngoại hình", desc, "tiếng Anh"), msg,
      h("p", { class: "help" }, "Kéo cổng CHARACTER (màu tím) bên phải node vào cổng characters của cảnh để gắn nhân vật.")),
    buttons: [{ label: "Huỷ" }, { label: "Thêm Node", cls: "primary", onclick: go }],
  });
}

function bAddLocation() {
  const sb = S.cur.storyboard;
  sb.locations = sb.locations || [];
  const name = h("input", { type: "text", placeholder: "Ví dụ: Quán cà phê phố cổ", "aria-label": "Tên bối cảnh" });
  const desc = h("textarea", { rows: 3, placeholder: "cozy Hanoi street-side cafe, low wooden stools, warm morning sunlight…", "aria-label": "Mô tả bối cảnh" });
  const msg = h("div", { class: "scene-msg err hidden", role: "alert" });
  const check = () => {
    const v = name.value.trim();
    const err = !v ? "Nhập tên bối cảnh." : sb.locations.some((l) => !blankLocation(l) && bKey(l.name) === bKey(v)) ? `Đã có bối cảnh “${v}”.` : "";
    msg.textContent = err; msg.classList.toggle("hidden", !err);
    return !err;
  };
  const go = (close) => {
    if (!check()) { name.focus(); return; }
    const loc = { name: name.value.trim(), description: desc.value.trim() };
    const placed = [];
    let bottom = BOARD_ORIGIN;
    for (const cd of boardModel().cards) {
      const p = bPos(cd.key);
      if (p) {
        placed.push({ x: p.x, y: p.y, w: bCardW(cd.key), h: bCardH(cd.key) });
        if (cd.type === "cfg" || cd.type === "char" || cd.type === "loc") bottom = Math.max(bottom, p.y + bCardH(cd.key));
      }
    }
    const r = placeFree({ x: BOARD_ORIGIN, y: bSnap(bottom + BOARD_GAP_ROW), w: BOARD_W_CHAR, h: bFallbackH("loc:") }, placed);
    sb.locations.push(loc);
    B.lpos.set(loc, { x: r.x, y: r.y });
    changed();
    close();
    rerenderBoard();
    bScrollTo(`loc:#${sb.locations.length - 1}`);
    saveBoard();
  };
  name.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); desc.focus(); } });
  modal({
    title: "Thêm Node Bối cảnh (LocationLoader)",
    body: h("div", { class: "stack" }, field("Tên bối cảnh", name, "bắt buộc, không trùng"), field("Mô tả không gian cố định", desc, "tiếng Anh"), msg,
      h("p", { class: "help" }, "Kéo cổng LOCATION (màu xanh ngọc) vào cảnh để cố định không gian & ánh sáng giữa nhiều cảnh.")),
    buttons: [{ label: "Huỷ" }, { label: "Thêm Node", cls: "primary", onclick: go }],
  });
}

function bNextSceneId() {
  const used = new Set(bScenes().map((s) => s.id));
  for (let n = bScenes().length + 1; n < 999; n++) {
    const id = "s" + String(n).padStart(2, "0");
    if (!used.has(id)) return id;
  }
  return "s" + Date.now().toString().slice(-4);
}

function bAddScene(mode = "keyframe") {
  const sb = S.cur.storyboard;
  sb.scenes = sb.scenes || [];
  const sid = bNextSceneId();
  const prev = sb.scenes[sb.scenes.length - 1];
  const sc = mode === "keyframe"
    ? { id: sid, mode: "keyframe", image_prompt: "", prompt: "", duration: 8, ...(prev?.location ? { location: prev.location } : {}) }
    : { id: sid, mode: "t2v", prompt: "", duration: 8, ...(prev?.location ? { location: prev.location } : {}) };
  sb.scenes.push(sc);
  B.videos.add(sid);
  B.saveAfterMount = true;
  changed();
  rerenderBoard();
  setTimeout(() => bScrollTo(mode === "keyframe" ? `kf:${sid}` : `vid:${sid}`), 40);
  toast(`Đã thêm cảnh ${sid} lên Bảng`, "ok");
}

/* ---------- View: Pan / Zoom / Fit ---------- */

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

function bFit(save = true) {
  const vp = bViewport();
  if (!vp || !vp.clientWidth) return;
  const rects = [...vp.querySelectorAll(".board-card, .board-empty")].filter((el) => el.style.visibility !== "hidden").map((el) => ({
    x: parseFloat(el.style.left) || 0, y: parseFloat(el.style.top) || 0, w: el.offsetWidth, h: el.offsetHeight,
  }));
  if (!rects.length) { B.view = { x: 0, y: 0, zoom: 1 }; bApplyView(vp); return; }
  const minX = Math.min(...rects.map((r) => r.x)), minY = Math.min(...rects.map((r) => r.y));
  const maxX = Math.max(...rects.map((r) => r.x + r.w)), maxY = Math.max(...rects.map((r) => r.y + r.h));
  const bw = maxX - minX, bh = maxY - minY, vw = vp.clientWidth, vh = vp.clientHeight;
  const z = bClampZoom(Math.min(1, (vw - 72) / bw, (vh - 72) / bh));
  B.view = { x: (vw - bw * z) / 2 - minX * z, y: Math.max(28, (vh - bh * z) / 2) - minY * z, zoom: z };
  bApplyView(vp);
  if (save) saveBoard();
}

function bScrollTo(key) {
  const vp = bViewport(), p = bPos(key);
  if (!vp || !p) return;
  const v = B.view || { x: 0, y: 0, zoom: 1 };
  const sx = p.x * v.zoom + v.x, sy = p.y * v.zoom + v.y, w = bCardW(key) * v.zoom, hh = bCardH(key) * v.zoom;
  if (sx >= 0 && sy >= 0 && sx + w <= vp.clientWidth && sy + hh <= vp.clientHeight) return;
  B.view = { ...v, x: vp.clientWidth / 2 - (p.x + bCardW(key) / 2) * v.zoom, y: Math.min(36, vp.clientHeight / 2 - (p.y + bCardH(key) / 2) * v.zoom) };
  bApplyView(vp);
}

function bWorldPoint(e) {
  const vp = bViewport(), r = vp.getBoundingClientRect(), v = B.view || { x: 0, y: 0, zoom: 1 };
  return { x: (e.clientX - r.left - v.x) / v.zoom, y: (e.clientY - r.top - v.y) / v.zoom };
}

function bOnWheel(e) {
  if (e.target.closest?.(".cf-palette")) return;
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

/* ---------- Pointer: Pan, Drag Node, Drag Wire, Minimap ---------- */

function bHighlightCompatiblePorts(fromKey, dtype, on) {
  document.querySelectorAll(".board-port.slot-compatible").forEach((el) => el.classList.remove("slot-compatible"));
  if (!on || !dtype) return;
  document.querySelectorAll(`.board-port.in[data-dtype="${CSS.escape(dtype)}"]`).forEach((portEl) => {
    const card = portEl.closest(".board-card");
    if (card && card.dataset.key !== fromKey && !bDropProblem(fromKey, card.dataset.key, portEl.dataset.slot)) {
      portEl.classList.add("slot-compatible");
    }
  });
}

function bOnPointerDown(e) {
  if (e.button !== 0 || B.drag) return;
  const vp = e.currentTarget, t = e.target;
  if (t.closest(".cf-palette")) return;

  const mm = t.closest(".cf-minimap");
  if (mm) {
    e.preventDefault();
    B.drag = { kind: "minimap", id: e.pointerId, mm };
    vp.setPointerCapture(e.pointerId);
    bPanFromMinimap(e, mm);
    return;
  }

  const port = t.closest(".board-port");
  if (port) {
    e.preventDefault();
    const card = port.closest(".board-card");
    if (port.classList.contains("disabled")) { toast(port.title); return; }
    if (!port.classList.contains("out") || !card) return;
    bMeasureSlots();
    B.drag = {
      kind: "link", from: card.dataset.key, fromSlot: port.dataset.slot || "",
      dtype: port.dataset.dtype || "", id: e.pointerId, target: null, ok: false,
    };
    vp.setPointerCapture(e.pointerId);
    vp.classList.add("is-dragging");
    port.classList.add("active");
    bHighlightCompatiblePorts(card.dataset.key, port.dataset.dtype, true);
    bDraftTo(bWorldPoint(e));
    return;
  }

  if (t.closest(".board-link")) return;

  const head = t.closest(".board-card-head");
  if (head) {
    const nameInput = t.matches("input.board-name") && document.activeElement !== t ? t : null;
    if (!nameInput && t.closest("input, textarea, select, button, a")) return;
    const card = head.closest(".board-card"), p = bPos(card.dataset.key);
    if (!p) return;
    if (nameInput) e.preventDefault();
    bMeasureSlots();
    B.drag = { kind: "card", key: card.dataset.key, el: card, id: e.pointerId, sx: e.clientX, sy: e.clientY, ox: p.x, oy: p.y, moved: false, focus: nameInput };
    vp.setPointerCapture(e.pointerId);
    return;
  }

  if (t.closest(".board-card, .board-empty")) return;
  if (B.paletteOpen) bTogglePalette(false);
  const v = B.view || { x: 0, y: 0, zoom: 1 };
  B.drag = { kind: "pan", id: e.pointerId, sx: e.clientX, sy: e.clientY, ox: v.x, oy: v.y, moved: false };
  vp.setPointerCapture(e.pointerId);
}

function bOnPointerMove(e) {
  const d = B.drag;
  if (!d || d.id !== e.pointerId) return;
  if (d.kind === "minimap") {
    bPanFromMinimap(e, d.mm);
  } else if (d.kind === "pan") {
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
      refreshZone("board-groups");
      bUpdateMinimap();
    });
  } else if (d.kind === "link") {
    bDraftTo(bWorldPoint(e));
    const under = document.elementFromPoint(e.clientX, e.clientY);
    const card = under?.closest?.(".board-card");
    const slotEl = under?.closest?.(".board-port.in");
    const key = card && card.dataset.key !== d.from ? card.dataset.key : null;
    const toSlot = slotEl?.dataset?.slot || "";
    const sig = `${key || ""}|${toSlot}`;
    if (sig !== d.targetSig) {
      document.querySelectorAll(".board-card.drop-ok, .board-card.drop-no").forEach((el) => el.classList.remove("drop-ok", "drop-no"));
      d.targetSig = sig;
      d.target = key;
      d.toSlot = toSlot;
      if (card && key) { d.ok = !bDropProblem(d.from, key, toSlot); card.classList.add(d.ok ? "drop-ok" : "drop-no"); }
    }
  }
}

function bOnPointerUp(e) {
  const d = B.drag;
  if (!d || d.id !== e.pointerId) return;
  const vp = e.currentTarget;
  B.drag = null;
  vp.classList.remove("is-dragging", "is-panning");
  try { vp.releasePointerCapture(e.pointerId); } catch { /* released */ }
  if (d.kind === "minimap") {
    saveBoard();
  } else if (d.kind === "pan") {
    if (d.moved) saveBoard();
    else if (B.sel) bSelect(null);
  } else if (d.kind === "card") {
    if (d.raf) cancelAnimationFrame(d.raf);
    if (d.moved) {
      d.el.classList.remove("dragging");
      bSetPos(d.key, { x: d.nx, y: d.ny });
      d.el.style.left = d.nx + "px"; d.el.style.top = d.ny + "px";
      bMeasureSlots();
      refreshZone("board-groups");
      refreshZone("board-links");
      bUpdateMinimap();
      document.querySelectorAll("#main svg.board-links.live").forEach((el) => el.remove());
      saveBoard();
    } else if (d.focus) {
      d.focus.focus();
      const n = d.focus.value.length;
      try { d.focus.setSelectionRange(n, n); } catch { /* ignore */ }
    }
  } else if (d.kind === "link") {
    document.querySelectorAll(".board-port.active, .board-port.slot-compatible").forEach((el) => el.classList.remove("active", "slot-compatible"));
    document.querySelectorAll(".board-card.drop-ok, .board-card.drop-no").forEach((el) => el.classList.remove("drop-ok", "drop-no"));
    bDraftTo(null);
    if (e.type === "pointercancel") return;
    const under = document.elementFromPoint(e.clientX, e.clientY);
    const card = under?.closest?.(".board-card");
    const slotEl = under?.closest?.(".board-port.in");
    if (card && card.dataset.key === d.from) return;
    bDrop(d.from, card ? card.dataset.key : null, slotEl?.dataset?.slot || "", bWorldPoint(e), !!under?.closest?.(".board-viewport"));
  }
}

function bOnClick(e) {
  const g = e.target.closest?.(".board-link");
  if (g && g.dataset.id) { bSelect(B.sel === g.dataset.id ? null : g.dataset.id); g.focus({ preventScroll: true }); }
}

function bOnDblClick(e) {
  if (e.target.closest?.(".board-card, .board-link, .cf-minimap, .cf-palette")) return;
  e.preventDefault();
  bTogglePalette(true);
}

function bOnContextMenu(e) {
  if (e.target.closest?.("input, textarea, select")) return;
  if (e.target.closest?.(".board-card, .cf-palette, .cf-minimap")) return;
  e.preventDefault();
  bTogglePalette(true);
}

function bOnLinkKey(e) {
  const g = e.target.closest?.(".board-link");
  if (g && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); bSelect(g.dataset.id); }
}

function bOnKey(e) {
  if (S.tab !== "board" || !S.cur) return;
  if (document.querySelector("#modal-root .overlay")) return;
  if (bTyping()) return;
  if ((e.key === "Delete" || e.key === "Backspace") && B.sel) { e.preventDefault(); bDeleteSelected(); }
  else if (e.key === "/" || (e.key === " " && !B.drag)) { e.preventDefault(); bTogglePalette(true); }
  else if (e.key === "f" || e.key === "F") { e.preventDefault(); bFit(true); }
  else if (e.key === "Escape") {
    if (B.paletteOpen) { bTogglePalette(false); return; }
    if (B.drag && B.drag.kind === "link") {
      B.drag = null; bDraftTo(null);
      document.querySelectorAll(".board-port.active, .board-port.slot-compatible").forEach((el) => el.classList.remove("active", "slot-compatible"));
      document.querySelectorAll(".board-card.drop-ok, .board-card.drop-no").forEach((el) => el.classList.remove("drop-ok", "drop-no"));
    } else if (B.sel) bSelect(null);
  }
}

function bDraftTo(pt) {
  const path = document.querySelector("#main .board-draft path");
  if (!path) return;
  const d = B.drag;
  if (!pt || !d || d.kind !== "link") { path.setAttribute("d", ""); path.removeAttribute("marker-end"); return; }
  const a = bAnchor(d.from, "out", d.fromSlot);
  if (!a) return;
  path.setAttribute("d", bCurve(a, pt));
  path.setAttribute("marker-end", "url(#bm-sel)");
}

/* ---------- Wire Drop Validation & Connection Execution ---------- */

function bDropProblem(from, to) {
  const toType = to.split(":")[0], sid = to.slice(to.indexOf(":") + 1);
  const sc = (toType === "kf" || toType === "vid") ? bScenes().find((s) => s.id === sid) : null;

  if (from.startsWith("char:#")) {
    const c = bChars()[+from.slice(6)];
    if (!c) return "Không thấy nhân vật";
    if (toType !== "kf" && toType !== "vid") return "Chỉ nối nhân vật vào node Keyframe hoặc VideoSampler của cảnh";
    if (!sc) return "Không thấy cảnh";
    if (bMode(sc) === "i2v") return `Cảnh ${sc.id} kiểu “${bModeLabel("i2v")}” không dùng ảnh nhân vật`;
    if (sceneLinks(sc).idx.includes(+from.slice(6))) return `${c.name} đã nối vào cảnh ${sc.id}`;
    return "";
  }

  if (from.startsWith("loc:#")) {
    const l = bLocs()[+from.slice(5)];
    if (!l) return "Không thấy bối cảnh";
    if (toType !== "kf" && toType !== "vid") return "Chỉ nối bối cảnh vào node Keyframe hoặc VideoSampler của cảnh";
    if (!sc) return "Không thấy cảnh";
    if (bMode(sc) === "i2v") return `Cảnh ${sc.id} kiểu “${bModeLabel("i2v")}” không dùng bối cảnh`;
    if (bLocIndex(sc.location) === +from.slice(5)) return `Cảnh ${sc.id} đã gắn bối cảnh “${l.name}”`;
    return "";
  }

  if (from.startsWith("cfg:")) {
    if (toType === "kf" || toType === "vid") return "";
    return "Cổng MODEL chỉ nối vào các node cảnh";
  }

  if (from.startsWith("kf:")) {
    const fromSid = from.slice(3);
    if (toType === "kf") {
      const scenes = bScenes();
      const iFrom = scenes.findIndex((s) => s.id === fromSid), iTo = scenes.findIndex((s) => s.id === sid);
      if (iTo !== iFrom + 1) return "Chỉ nối tiếp Keyframe vào cảnh liền kề ngay sau đó";
      if (bContinuityPrevSid(iTo) === fromSid) return `Keyframe ${fromSid} đã nối tiếp sang ${sid}`;
      return "";
    }
    if (toType === "vid" && sid === fromSid) return bVideoShown(sc) ? "Keyframe đã nối với VideoSampler của cảnh này" : "";
    return "Keyframe chỉ nối sang VideoSampler cùng cảnh hoặc prev_image của cảnh kế tiếp";
  }

  if (from.startsWith("vid:")) {
    if (to === "out:final") return "Clip của cảnh đã tự nối vào SaveFilm";
    return "Cổng VIDEO đầu ra đã nối tới SaveFilm";
  }

  return "Không thể nối hai cổng này";
}

async function bDrop(from, to, _toSlot, pt, inViewport) {
  if (from.startsWith("char:#")) {
    if (!to) return;
    const why = bDropProblem(from, to);
    if (why) { toast(why); return; }
    const c = bChars()[+from.slice(6)], sid = to.slice(to.indexOf(":") + 1), sc = bScenes().find((s) => s.id === sid);
    const viaVideo = to.startsWith("vid:") && bMode(sc) === "keyframe";
    if (await editSceneChars(sc, c.name) && viaVideo) toast(`Đã nối nhân vật vào Keyframe của cảnh ${sid}`);
    return;
  }

  if (from.startsWith("loc:#")) {
    if (!to) return;
    const why = bDropProblem(from, to);
    if (why) { toast(why); return; }
    const l = bLocs()[+from.slice(5)], sid = to.slice(to.indexOf(":") + 1), sc = bScenes().find((s) => s.id === sid);
    await editSceneLocation(sc, l.name);
    return;
  }

  if (from.startsWith("cfg:")) {
    if (!to) return;
    const why = bDropProblem(from, to);
    if (why) { toast(why); return; }
    B.showModelLinks = true;
    localSet("boardModelLinks", true);
    refreshZone("board-links");
    toast("Đã bật hiển thị dây nối MODEL cho toàn bộ cảnh", "ok");
    return;
  }

  if (from.startsWith("kf:")) {
    const sid = from.slice(3), sc = bScenes().find((s) => s.id === sid);
    if (!sc) return;
    if (to) {
      const why = bDropProblem(from, to);
      if (why) { toast(why); return; }
      if (to.startsWith("kf:")) {
        await editSceneContinuity(sid, to.slice(3), true);
        return;
      }
    }
    if (!inViewport) return;
    if (S.cur.errors?.length) { toast("Storyboard đang lỗi — sửa lỗi trước khi hiện node VideoSampler", "error"); return; }
    if (bVideoShown(sc)) { toast(`Node VideoSampler của cảnh ${sid} đã có trên bảng`); return; }
    B.videos.add(sid);
    bSetPos(`vid:${sid}`, { x: bSnap(pt.x + 8), y: bSnap(pt.y - BOARD_HEAD / 2) });
    rerenderBoard();
    saveBoard();
  }
}
