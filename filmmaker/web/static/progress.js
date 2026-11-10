"use strict";
/* Film Maker UI — per-item progress overlay on thumbnails (job.progress from /api/jobs/…).
   Loaded before board.js and app.js; nothing runs at load time. Uses h(), S and jobRunning() from app.js.

   data-prog convention (shared with board.js): the attribute holds a space-separated list of keys, each one passed
   through encodeURIComponent ("char:Bà Lan" → "char%3AB%C3%A0%20Lan"). Only progAttach writes it, only progKeys reads
   it, and elements are found only with [data-prog~="${CSS.escape(progEnc(key))}"]. Keys in S.job.progress and in the
   `keys` arguments are always the raw keys. */

const PROG_KINDS = new Set(["video", "make", "keyframes", "portraits"]);
const PROG_TEXT = { queued: "Đang chờ…", retry: "Chờ thử lại…", submitting: "Đang gửi…", generating: "Đang tạo…", downloading: "Đang tải về…" };
const PROG_EST_TIP = "Flow không báo % cho cách tạo này; số này đoán theo thời gian các lần trước";
const PROG_ACTIVE = new Set(["queued", "submitting", "generating", "downloading"]);
const PROG_RUNNING = new Set(["submitting", "generating", "downloading"]);

/* The job carries structured progress for the project on screen. Other jobs fall back to the log-line heuristics. */
function progOn() {
  const j = S.job;
  return !!(j && S.cur && j.project === S.cur.id && PROG_KINDS.has(j.kind) && j.progress && typeof j.progress === "object");
}
function progItem(key) { return progOn() ? (S.job.progress[key] || null) : null; }
/* Has an overlay (queued included). */
function progActive(key) { const it = progItem(key); return !!(jobRunning() && it && PROG_ACTIVE.has(it.phase)); }
/* Is being worked on now: "running" status, is-running, "Đang làm" badge (queued excluded). */
function progRunning(key) { const it = progItem(key); return !!(jobRunning() && it && PROG_RUNNING.has(it.phase)); }
function progQueued(key) { const it = progItem(key); return !!(jobRunning() && it && it.phase === "queued"); }
function progFailed(key) { return progItem(key)?.phase === "failed"; }
function progCharKey(c) { return "char:" + String(c?.name || "").trim(); }

function progEnc(key) { return encodeURIComponent(key); }
function progKeys(el) {
  return (el.dataset.prog || "").split(" ").filter(Boolean).map((k) => { try { return decodeURIComponent(k); } catch { return k; } });
}

/* The only builder of the overlay: progAttach (zone rebuilds) and progUpdate (polls) both call it, so the DOM after an
   update is identical to a fresh zone render and refreshZones() never swaps a zone just because a % moved. */
function progOverlay(keys) {
  const key = (keys || []).find(progActive);
  if (!key) return null;
  const it = S.job.progress[key];
  const queued = it.phase === "queued";
  const label = queued ? (Number(it.attempt) >= 2 ? PROG_TEXT.retry : PROG_TEXT.queued) : PROG_TEXT[it.phase];
  const pct = queued || it.percent == null || !Number.isFinite(Number(it.percent)) ? null
    : Math.max(0, Math.min(100, Math.round(Number(it.percent))));
  const est = it.source === "estimate";
  const kids = [queued ? null : h("span", { class: "spinner", "aria-hidden": "true" }), h("span", { class: "prog-label" }, label)];
  if (pct != null && (est || it.source === "flow")) {
    kids.push(
      h("span", { class: "prog-pct", title: est ? PROG_EST_TIP : null }, est ? `~${pct}% ` : `${pct}%`, est ? h("small", {}, "(ước tính)") : null),
      h("div", { class: "prog-bar" + (est ? " est" : ""), "aria-hidden": "true" }, h("i", { style: { width: pct + "%" } })));
    if (est && pct >= 95) kids.push(h("span", { class: "prog-slow" }, "lâu hơn thường lệ"));
  }
  return h("div", { class: "prog-ov", "data-phase": queued && Number(it.attempt) >= 2 ? "retry" : it.phase, role: "status" }, kids);
}

/* Tag a media frame with its progress keys and add the overlay (last child) if one of them is active. */
function progAttach(box, keys) {
  box.dataset.prog = (keys || []).map(progEnc).join(" ");
  const ov = progOverlay(keys);
  if (ov) box.append(ov);
  return box;
}

/* Call after S.job.progress = next. Rebuilds the overlay of every frame whose keys changed (add / replace / remove).
   Returns { phaseChanged, done, failed } (done/failed: keys that just reached that phase). */
function progUpdate(prev, next) {
  const t0 = performance.now();
  prev = prev || {}; next = next || {};
  const changed = [], done = [], failed = [];
  let phaseChanged = false;
  for (const k of new Set([...Object.keys(prev), ...Object.keys(next)])) {
    const a = prev[k], b = next[k];
    if (JSON.stringify(a) === JSON.stringify(b)) continue;
    changed.push(k);
    if (a?.phase !== b?.phase) {
      phaseChanged = true;
      if (b?.phase === "done") done.push(k);
      else if (b?.phase === "failed") failed.push(k);
    }
  }
  if (changed.length) {
    const els = new Set();
    for (const k of changed) for (const el of document.querySelectorAll(`[data-prog~="${CSS.escape(progEnc(k))}"]`)) els.add(el);
    for (const el of els) {
      for (const old of el.querySelectorAll(":scope > .prog-ov")) old.remove();
      const ov = progOverlay(progKeys(el));
      if (ov) el.append(ov);
    }
  }
  try {
    if (localStorage.getItem("fm.debugProgress")) console.log(`[progress] ${changed.length} khoá đổi, ${(performance.now() - t0).toFixed(2)} ms`);
  } catch { /* private mode */ }
  return { phaseChanged, done, failed };
}
