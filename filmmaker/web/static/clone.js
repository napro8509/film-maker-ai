/* Clone video: the same clip (sound, gestures, expressions, camera) with a new cast.
   Uses the helpers of app.js (h, put, icon, api, field, toast, startJob, jobRunning…), loaded before this file. */

const CLONE_MODEL_LABEL = { "veo-lite": "Veo 3.1 Lite", "omni-1.2-flash": "Omni 1.2 Flash", "omni-flash": "Omni Flash", muse: "Meta Muse" };
/* A clone on Meta Muse (muse.ai) makes its cast pictures, start frames and clips there: nothing goes to Flow. */
const cloneOnMuse = (d) => d.model === "muse";
const CLONE_STATUS = { done: ["done", "Xong"], failed: ["failed", "Lỗi"], todo: ["todo", "Chưa render"] };

async function openCloner(id) {
  await flushSave();
  const [list, detail] = await Promise.allSettled([api("GET", "/api/clones"), id ? api("GET", `/api/clones/${encodeURIComponent(id)}`) : Promise.resolve(null)]);
  S.clones = list.status === "fulfilled" ? list.value : [];
  if (id && detail.status !== "fulfilled") toast(detail.reason?.message || "Không mở được bản clone", "error");
  S.clone = detail.status === "fulfilled" ? detail.value : null;
  S.cloneDirty = false;
  S.cloneView = true; S.ana = null; S.cur = null; S.crawlView = false; S.crawl = null;
  if (document.body.classList.contains("side-open")) toggleSidebar(false);
  const hash = S.clone ? "clone/" + S.clone.id : "clone";
  if (location.hash.slice(1) !== hash) history.replaceState(null, "", "#" + hash);
  renderSidebar(); renderMain();
  $("#main").scrollTop = 0;
}

function closeCloner() {
  S.cloneView = false; S.clone = null;
  history.replaceState(null, "", location.pathname);
  const last = localGet("lastProject"), pick = S.projects.some((p) => p.id === last) ? last : S.projects[0]?.id;
  if (pick) openProject(pick); else { renderSidebar(); renderMain(); }
}

function renderCloner() {
  return S.clone ? renderCloneDetail(S.clone) : renderCloneStart();
}

/* ---------- start page: paste a link, pick how ---------- */

function renderCloneStart() {
  const MODES = [["regen", "sparkle", "Tạo lại bằng AI", "AI xem từng nửa giây rồi dựng lại cảnh với nhân vật mới. Dùng được cả khi clip có trẻ nhỏ. Khuyên dùng."],
    ["edit", "edit", "Sửa thẳng video gốc", "Omni thay người ngay trên video gốc: khớp từng khung hình nhất, nhưng Flow chặn clip có trẻ em."]];
  let mode = localGet("cloneMode") === "edit" ? "edit" : "regen";
  const MODELS = { regen: [["veo-lite", "Veo 3.1 Lite · đoạn 4–8s, ~5 credit/đoạn"], ["omni-1.2-flash", "Omni 1.2 Flash · một cảnh liền tới 15s (chặn trẻ em)"], ["muse", "Meta Muse · đoạn 10s có tiếng, ảnh + video trên muse.ai, không tốn credit Flow"]], edit: [["omni-flash", "Omni Flash"]] };
  const url = h("input", { type: "url", placeholder: "https://www.facebook.com/…/videos/…  ·  TikTok  ·  YouTube  ·  link .mp4", value: localGet("cloneUrl", "") });
  const model = h("select", {});
  const picker = h("div", { class: "choice", role: "radiogroup", "aria-label": "Cách clone" });
  const paint = () => {
    picker.replaceChildren(...MODES.map(([k, ic, t, d]) => h("button", { type: "button", class: mode === k ? "on" : "", role: "radio", "aria-checked": String(mode === k),
      onclick: () => { mode = k; localSet("cloneMode", k); paint(); } }, icon(ic), h("b", {}, t), h("span", {}, d))));
    const keep = model.value;
    model.replaceChildren(...MODELS[mode].map(([v, l]) => h("option", { value: v }, l)));
    if (MODELS[mode].some(([v]) => v === keep)) model.value = keep;
    else if (mode === "regen" && localGet("cloneModel") && MODELS.regen.some(([v]) => v === localGet("cloneModel"))) model.value = localGet("cloneModel");
  };
  model.onchange = () => { if (mode === "regen") localSet("cloneModel", model.value); };
  paint();
  let picked = null;  // a video file from this computer, used instead of the link
  const fileName = h("span", { class: "muted small ellipsis" }, "");
  const clearFile = h("button", { type: "button", class: "btn sm ghost icon hidden", title: "Bỏ file", "aria-label": "Bỏ file video đã chọn", onclick: () => pickFile(null) }, icon("x"));
  const fileInput = h("input", { type: "file", accept: "video/mp4,video/quicktime,video/webm,video/x-matroska,.mp4,.mov,.m4v,.webm,.mkv", class: "hidden",
    onchange: (e) => pickFile(e.target.files[0] || null) });
  const pickFile = (f) => {
    picked = f;
    if (!f) fileInput.value = "";
    fileName.textContent = f ? `${f.name} · ${(f.size / 1048576).toFixed(1)} MB` : "";
    clearFile.classList.toggle("hidden", !f);
    url.disabled = !!f;
  };
  const go = async (e) => {
    const v = url.value.trim();
    if (!picked && !/^https?:\/\//i.test(v)) { toast("Hãy dán một link http(s) tới video, hoặc chọn file video từ máy", "error"); url.focus(); return; }
    const btn = e.currentTarget; btn.disabled = true;
    try {
      if (picked) {
        const fd = new FormData();
        fd.append("file", picked); fd.append("mode", mode); fd.append("model", model.value);
        toast("Đang tải file video lên…", "", 4000);
        startJob(await api("POST", "/api/clones/upload", fd, true));
        pickFile(null);
      } else {
        localSet("cloneUrl", v);
        startJob(await api("POST", "/api/clones", { url: v, mode, model: model.value }));
        localSet("cloneUrl", "");
      }
      toast("Đang chia đoạn video và nhờ AI liệt kê nhân vật…", "", 6000);
    }
    catch (err) { toast(err.message, "error"); }
    btn.disabled = false;
  };
  const va = S.env?.video_analysis || {};
  return h("div", { class: "main-inner clone" },
    h("div", { class: "phead" },
      h("div", { class: "title-col" }, h("h1", {}, "Clone video"),
        h("p", { class: "muted" }, "Làm lại một clip giống hệt: giữ âm thanh gốc, cử chỉ, biểu cảm, tương tác, góc máy và bối cảnh, chỉ đổi nhân vật thành người mới do bạn thiết kế.")),
      h("div", { class: "actions" }, h("button", { class: "btn icon", title: "Đóng", "aria-label": "Đóng Clone video", onclick: closeCloner }, icon("x")))),
    h("section", { class: "card ana-card" }, h("div", { class: "card-head" }, h("h3", {}, "Clone một video mới")),
      h("div", { class: "stack" },
        field("Link video", url),
        h("div", { class: "row small" }, h("span", { class: "muted" }, "hoặc"),
          h("button", { type: "button", class: "btn sm", onclick: () => fileInput.click() }, icon("upload"), "Tải video từ máy"),
          fileName, clearFile, fileInput),
        picker,
        field("Model video", model),
        va.ytdlp === false ? h("div", { class: "banner warn flush" }, icon("alert"), h("div", { class: "b-body" }, h("div", {}, "Chưa cài yt-dlp nên chỉ tải được link thẳng tới file .mp4: ", h("code", {}, "brew install yt-dlp")))) : null,
        h("p", { class: "help" }, "Bước tiếp theo: kiểm tra nhân vật mới → tạo nhân vật (Flow, hoặc ảnh trên Muse nếu chọn Meta Muse) → AI mô tả hành động, bạn sửa nếu cần → render → ghép với âm thanh gốc. Muse cần đăng nhập một lần: filmmaker muse login"),
        h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "btn primary", disabled: jobRunning(), onclick: go }, icon("copy"), "Bắt đầu clone ", FREE())))),
    h("section", { class: "card ana-card" }, h("div", { class: "card-head" }, h("h3", {}, "Đã clone")),
      (S.clones || []).length ? h("div", { class: "stack", style: { gap: "6px" } }, S.clones.map((c) => h("div", { class: "row ana-item" },
        h("div", { class: "ana-thumb", style: c.thumb ? { backgroundImage: `url("${c.thumb}")` } : null }),
        h("div", { class: "grow", style: { minWidth: 0 } }, h("div", { class: "name" }, c.title),
          h("div", { class: "muted small ellipsis" }, [c.duration ? fmtDur(c.duration) : "", c.mode === "regen" ? "Tạo lại bằng AI" : "Sửa video gốc",
            CLONE_MODEL_LABEL[c.model] || c.model, `${c.done}/${c.segments} đoạn`, ago(c.created)].filter(Boolean).join(" · "))),
        c.has_final ? h("span", { class: "badge film mini" }, "Có video") : null,
        h("button", { class: "btn sm", onclick: () => openCloner(c.id) }, "Mở"),
        h("button", { class: "btn sm ghost icon", title: "Xoá bản clone", "aria-label": "Xoá bản clone", onclick: () => deleteClone(c) }, icon("trash")))))
        : h("p", { class: "muted small" }, "Chưa có bản clone nào.")));
}

async function deleteClone(c) {
  if (!(await confirmModal("Xoá bản clone?", `“${c.title}” cùng video gốc, nhân vật, các đoạn đã render và video ghép sẽ bị xoá khỏi máy.`, "Xoá", true))) return;
  try { await api("DELETE", `/api/clones/${c.id}`); await openCloner(null); }
  catch (err) { toast(err.message, "error"); }
}

/* ---------- one clone: cast → actions → render → stitch ---------- */

const cloneTime = (t) => (+t || 0).toFixed(1) + "s";

function cloneDirty() {
  if (S.cloneDirty) return;
  S.cloneDirty = true;
  document.querySelectorAll(".clone-save").forEach((b) => { b.disabled = false; b.classList.add("primary"); });
}

async function saveClone(quiet) {
  const d = S.clone;
  if (!S.cloneDirty) return true;
  try {
    const body = { cast: d.cast.map((c) => ({ name: c.name, old_name: c._old ?? c.name, role_vi: c.role_vi, replaces: c.replaces, new_look: c.new_look })),
      extra: d.extra || "", segments: d.segments.filter((s) => s.plan).map((s) => ({ id: s.id, plan: s.plan })) };
    S.clone = await api("PUT", `/api/clones/${d.id}`, body);
    S.cloneDirty = false;
    if (!quiet) toast("Đã lưu", "ok");
    renderMain();
    return true;
  } catch (err) { toast(err.message, "error"); return false; }
}

async function cloneAction(path, body, msg) {
  if (!(await saveClone(true))) return;
  try { startJob(await api("POST", `/api/clones/${S.clone.id}/${path}`, body || {})); if (msg) toast(msg, "", 5000); }
  catch (err) { toast(err.message, "error"); }
}

function renderCloneDetail(d) {
  const busy = jobRunning();
  const regen = d.mode === "regen";
  const segs = d.segments || [];
  const done = segs.filter((s) => s.status === "done").length;
  const castReady = d.cast.length && d.cast.every((c) => c.ref);
  const plansReady = !regen || segs.every((s) => s.plan);
  const muse = cloneOnMuse(d);
  const saveBtn = () => h("button", { class: "btn sm clone-save" + (S.cloneDirty ? " primary" : ""), disabled: !S.cloneDirty || busy, onclick: () => saveClone() }, icon("check"), "Lưu");
  const step = (n, title, ok, ...kids) => h("section", { class: "card ana-card clone-step" + (ok ? " ok" : "") },
    h("div", { class: "card-head" }, h("span", { class: "clone-n" }, ok ? icon("check") : n), h("h3", {}, title)), ...kids);

  return h("div", { class: "main-inner clone" },
    h("div", { class: "phead" },
      h("div", { class: "title-col" },
        h("div", { class: "muted small" }, "Clone video"),
        h("h1", {}, d.title || d.id),
        h("div", { class: "stats" },
          h("span", {}, fmtDur(d.duration || 0)), h("span", {}, d.aspect === "9:16" ? "Dọc 9:16" : "Ngang 16:9"),
          h("span", {}, regen ? "Tạo lại bằng AI" : "Sửa video gốc"), h("span", {}, CLONE_MODEL_LABEL[d.model] || d.model),
          h("span", {}, `${done}/${segs.length} đoạn xong`),
          /^https?:/.test(d.source || "") ? h("a", { href: d.source, target: "_blank", rel: "noopener noreferrer" }, "Video gốc ", icon("external")) : null)),
      h("div", { class: "actions" },
        h("button", { class: "btn", onclick: () => openCloner(null) }, "← Danh sách"),
        h("button", { class: "btn icon", title: "Xoá bản clone", "aria-label": "Xoá bản clone", disabled: busy, onclick: () => deleteClone(d) }, icon("trash")),
        h("button", { class: "btn icon", title: "Đóng", "aria-label": "Đóng Clone video", onclick: closeCloner }, icon("x")))),

    h("div", { class: "ana-top" },
      d.video_url ? h("video", { src: d.video_url, controls: true, preload: "metadata", class: "ana-video" + (d.aspect === "9:16" ? " tall" : "") }) : null,
      step(1, "Nhân vật mới", castReady,
        h("p", { class: "help" }, muse
          ? "Mỗi người trong clip gốc được thay bằng một nhân vật mới. Sửa ngoại hình cho khác hẳn người gốc rồi để Muse vẽ ảnh nhân vật; ảnh này được gắn vào mọi ảnh mở cảnh. Đổi ngoại hình thì cần vẽ lại."
          : "Mỗi người trong clip gốc được thay bằng một nhân vật mới. Sửa ngoại hình cho khác hẳn người gốc rồi tạo nhân vật Flow (miễn phí). Đổi ngoại hình thì cần tạo lại."),
        h("div", { class: "clone-cast" }, d.cast.map((c, i) => renderCloneCast(d, c, i, busy))),
        h("div", { class: "row" },
          h("button", { class: "btn sm", disabled: busy, onclick: () => { d.cast.push({ name: `Nhân vật ${d.cast.length + 1}`, _old: null, role_vi: "", replaces: "", new_look: "", ref: "" }); cloneDirty(); renderMain(); } }, icon("plus"), "Thêm nhân vật"),
          h("span", { class: "spacer" }), saveBtn(),
          h("button", { class: "btn sm" + (castReady ? "" : " primary"), disabled: busy || castReady || !d.cast.length, onclick: () => cloneAction("cast", null, muse ? "Muse đang vẽ ảnh nhân vật…" : "Đang tạo nhân vật trên Flow…") }, icon("users"), muse ? "Vẽ ảnh nhân vật (Muse) " : "Tạo nhân vật Flow ", FREE())))),

    regen ? step(2, "Hành động từng đoạn", plansReady && segs.length,
      h("p", { class: "help" }, "AI xem clip gốc từng nửa giây và ghi lại ai làm gì, biểu cảm ra sao. Video mới được dựng theo đúng mô tả này, nên hãy đọc lại và sửa những gì muốn đổi (ví dụ bỏ một hành động mà Flow hoặc Muse không cho phép). Đoạn nối tiếp sẽ bắt đầu từ khung cuối của đoạn trước."),
      plansReady ? null : h("div", { class: "row" }, h("span", { class: "spacer" }),
        h("button", { class: "btn primary", disabled: busy || !d.cast.length, onclick: () => cloneAction("describe", null, "AI đang xem clip từng nửa giây…") }, icon("scan"), "AI mô tả hành động ", FREE())),
      segs.map((s) => renderClonePlan(d, s, busy)),
      field("Ghi chú thêm cho mọi đoạn", h("textarea", { rows: 2, placeholder: "Tuỳ chọn, tiếng Anh. Ví dụ: Keep the red boots.", value: d.extra || "", disabled: busy, oninput: (e) => { d.extra = e.target.value; cloneDirty(); } })),
      h("div", { class: "row" }, h("span", { class: "spacer" }), saveBtn())) : null,

    renderCloneRender(d, regen ? 3 : 2, step, { busy, castReady, plansReady }),
    renderCloneFinal(d, regen ? 4 : 3, step, busy));
}

async function clonePicture(c, file) {
  if (!(await saveClone(true))) return;
  try {
    if (file) {
      const fd = new FormData();
      fd.append("name", c.name); fd.append("file", file);
      S.clone = await api("POST", `/api/clones/${S.clone.id}/cast/picture`, fd, true);
      toast(`Đã dùng ảnh cho ${c.name}`, "ok");
    } else {
      S.clone = await api("DELETE", `/api/clones/${S.clone.id}/cast/picture?name=${encodeURIComponent(c.name)}`);
    }
    renderMain();
  } catch (err) { toast(err.message, "error"); }
}

function renderCloneCast(d, c, i, busy) {
  if (c._old === undefined) c._old = c.name;
  const picInput = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", class: "hidden",
    onchange: (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) clonePicture(c, f); } });
  const inp = (key, attrs = {}) => h(attrs.rows ? "textarea" : "input", { ...attrs, value: c[key] || "", disabled: busy, oninput: (e) => { c[key] = e.target.value; cloneDirty(); } });
  return h("div", { class: "clone-person" },
    h("div", { class: "clone-pic", style: c.picture_url ? { backgroundImage: `url("${c.picture_url}")` } : null }, c.picture_url ? null : icon("users")),
    h("div", { class: "grow stack", style: { gap: "6px", minWidth: 0 } },
      h("div", { class: "grid2" }, field("Tên", inp("name")), field("Vai", inp("role_vi", { placeholder: "mẹ, bố, em bé…" }))),
      field("Thay cho người này trong clip gốc", inp("replaces", { rows: 2, placeholder: "the young woman with a long black braid in the white dress" }), "tiếng Anh"),
      field("Ngoại hình mới", inp("new_look", { rows: 2, placeholder: "sky-blue linen dress, wavy chestnut bob" }), "tiếng Anh"),
      h("div", { class: "row small" },
        c.ref ? h("span", { class: "badge done mini" }, String(c.ref).startsWith("character:") ? "Đã có nhân vật Flow" : c.uploaded ? "Ảnh bạn tải lên" : "Đã có ảnh nhân vật") : h("span", { class: "badge todo mini" }, "Chưa có nhân vật"),
        h("span", { class: "spacer" }),
        picInput,
        h("button", { class: "btn sm", disabled: busy, title: "Dùng ảnh của bạn làm nhân vật mới (thay cho việc tạo từ mô tả)", onclick: () => picInput.click() }, icon("upload"), c.uploaded ? "Đổi ảnh" : "Tải ảnh lên"),
        c.uploaded ? h("button", { class: "btn sm ghost", disabled: busy, title: "Bỏ ảnh đã tải lên", onclick: () => clonePicture(c, null) }, icon("x"), "Bỏ ảnh") : null,
        h("button", { class: "btn sm ghost", disabled: busy, onclick: () => { d.cast.splice(i, 1); cloneDirty(); renderMain(); } }, icon("trash"), "Bỏ"))));
}

function renderClonePlan(d, s, busy) {
  const p = s.plan;
  const head = h("div", { class: "clone-seg-head" },
    s.thumb_url ? h("img", { src: s.thumb_url, alt: "", class: "clone-mini" }) : null,
    h("b", {}, s.id), h("span", { class: "muted" }, `${cloneTime(s.start)} – ${cloneTime(s.end)}`),
    s.chained ? h("span", { class: "badge mini" }, "nối tiếp đoạn trước") : null);
  if (!p) return h("div", { class: "clone-seg" }, head, h("p", { class: "muted small" }, "Chưa mô tả."));
  const ta = (key, rows, label, hint) => field(label, h("textarea", { rows, value: p[key] || "", disabled: busy, oninput: (e) => { p[key] = e.target.value; cloneDirty(); } }), hint);
  return h("details", { class: "clone-seg", open: d.segments.length === 1 || s.status !== "done" },
    h("summary", {}, head),
    h("div", { class: "stack", style: { gap: "8px" } },
      s.chained ? null : ta("first_frame", 3, "Khung hình mở đầu", "đổi thì ảnh mở cảnh được vẽ lại"),
      h("div", { class: "grid2" }, ta("camera", 3, "Máy quay"), ta("setting", 3, "Bối cảnh")),
      h("div", { class: "clone-beats" }, (p.beats || []).map((b) => h("div", { class: "clone-beat" },
        h("span", { class: "mono small muted" }, `${cloneTime(b.start)}–${cloneTime(b.end)}`),
        h("textarea", { rows: 2, value: b.action || "", disabled: busy, "aria-label": `Hành động ${cloneTime(b.start)}`, oninput: (e) => { b.action = e.target.value; cloneDirty(); } })))),
      ta("sounds", 2, "Âm thanh", "chỉ để model dựng cảnh; video ghép dùng âm thanh gốc")));
}

function renderCloneRender(d, n, step, { busy, castReady, plansReady }) {
  const regen = d.mode === "regen";
  const segs = d.segments || [];
  const todo = segs.filter((s) => s.status !== "done");
  const rights = h("input", { type: "checkbox" });
  const muse = cloneOnMuse(d);
  const credit = d.model === "veo-lite" ? ` · khoảng ${todo.length * 5} credit` : "";
  const blocker = !castReady ? (muse ? "Vẽ ảnh nhân vật hoặc tải ảnh lên trước." : "Tạo nhân vật Flow hoặc tải ảnh nhân vật lên trước.") : !plansReady ? "Cần mô tả hành động trước." : null;
  const COST = () => (muse ? FREE() : PAID());
  const run = (only) => {
    if (!regen && !rights.checked) { toast("Hãy xác nhận bạn có quyền dùng video gốc", "error"); return; }
    cloneAction("run", { only, agree_rights: !regen && rights.checked }, muse ? "Đang render trên Muse…" : "Đang render trên Flow…");
  };
  const redo = async (s, keyframe) => {
    const more = regen && segs.some((x) => x.chained && segs.indexOf(x) > segs.indexOf(s) && x.status === "done");
    if (!(await confirmModal(`Làm lại ${s.id}?`, "Bản đang có được giữ lại (đổi tên .vN)." + (more ? " Các đoạn nối tiếp sau nó cũng phải render lại để khớp chỗ nối." : "") + (muse ? " Bấm Render để Muse tạo bản mới." : " Bấm Render để tạo bản mới (tốn credit)."), "Làm lại"))) return;
    try { S.clone = await api("POST", `/api/clones/${d.id}/redo`, { segment: s.id, keyframe }); renderMain(); }
    catch (err) { toast(err.message, "error"); }
  };
  const media = (label, el) => h("figure", { class: "clone-media" }, el || h("div", { class: "clone-empty" }, "—"), h("figcaption", {}, label));
  const vid = (src) => src ? h("video", { src, controls: true, preload: "metadata", playsinline: true }) : null;
  return step(n, "Render từng đoạn", segs.length && !todo.length,
    h("p", { class: "help" }, regen
      ? (muse
        ? "Mỗi đoạn: Muse vẽ ảnh mở cảnh với ảnh nhân vật mới (đoạn nối tiếp lấy khung cuối của đoạn trước), rồi làm clip 10 giây theo mô tả hành động; lúc ghép, clip được tua cho khớp đúng độ dài đoạn gốc. Mỗi đoạn mất khoảng 2–4 phút."
        : "Mỗi đoạn: vẽ ảnh mở cảnh với nhân vật mới (đoạn nối tiếp lấy khung cuối của đoạn trước), rồi tạo video theo mô tả hành động. Flow hỏi xác nhận số credit trước khi render.")
      : "Omni sửa từng đoạn của video gốc: thay người, giữ nguyên mọi thứ khác."),
    regen ? null : h("label", { class: "check" }, rights, h("span", {}, "Tôi là chủ hoặc được phép dùng video gốc (Flow hỏi “Rights to use this video”)")),
    h("div", { class: "clone-render" }, segs.map((s) => {
      const [cls, label] = CLONE_STATUS[s.status] || CLONE_STATUS.todo;
      return h("div", { class: "clone-row" },
        h("div", { class: "clone-row-head" },
          h("b", {}, s.id), h("span", { class: "muted small" }, `${cloneTime(s.start)} – ${cloneTime(s.end)} · clip ${s.duration}s`),
          h("span", { class: "badge mini " + cls }, label), h("span", { class: "spacer" }),
          s.status === "done"
            ? h("button", { class: "btn sm", disabled: busy, onclick: () => redo(s, false) }, icon("refresh"), "Làm lại")
            : h("button", { class: "btn sm", disabled: busy || !!blocker, onclick: () => run([s.id]) }, icon("play"), "Render đoạn này ", COST()),
          s.status !== "done" && s.keyframe_url && !s.chained
            ? h("button", { class: "btn sm ghost", disabled: busy, title: "Bỏ ảnh mở cảnh hiện tại, vẽ lại khi render", onclick: () => redo(s, true) }, icon("image"), "Vẽ lại ảnh mở cảnh") : null),
        s.note && s.status !== "done" ? h("div", { class: "banner " + (s.status === "failed" ? "err" : "info") + " flush small" }, icon(s.status === "failed" ? "alert" : "info"), h("div", { class: "b-body" }, s.note.slice(0, 400))) : null,
        h("div", { class: "clone-medias" },
          media("Gốc", vid(s.piece_url)),
          regen ? media(s.chained ? "Ảnh mở cảnh (khung cuối đoạn trước)" : "Ảnh mở cảnh", s.keyframe_url ? h("a", { href: s.keyframe_url, target: "_blank" }, h("img", { src: s.keyframe_url, alt: "Ảnh mở cảnh " + s.id })) : null) : null,
          media("Clone", vid(s.render_url))));
    })),
    h("div", { class: "row" },
      blocker ? h("span", { class: "muted small" }, blocker) : null, h("span", { class: "spacer" }),
      h("button", { class: "btn primary", disabled: busy || !!blocker || !todo.length, onclick: () => run(todo.map((s) => s.id)) },
        icon("play"), todo.length ? `Render ${todo.length} đoạn chưa xong${credit} ` : "Đã render đủ ", todo.length ? COST() : null)));
}

function renderCloneFinal(d, n, step, busy) {
  const segs = d.segments || [];
  const missing = segs.filter((s) => s.status !== "done").length;
  const allow = h("input", { type: "checkbox", checked: false });
  const vid = (src, label) => src ? h("figure", { class: "clone-media big" }, h("video", { src, controls: true, preload: "metadata", playsinline: true }),
    h("figcaption", {}, label, " · ", h("a", { href: src, download: `${d.id}-${label === "Bản clone" ? "final" : "compare"}.mp4` }, icon("download"), "Tải về"))) : null;
  return step(n, "Ghép với âm thanh gốc", !!d.final_url,
    h("p", { class: "help" }, "Ghép các đoạn, chỉnh tốc độ từng đoạn cho khớp nhịp, lồng ÂM THANH GỐC, rồi làm bản so sánh gốc | clone để soát lại từng khoảnh khắc."),
    missing ? h("label", { class: "check" }, allow, h("span", {}, `Còn ${missing} đoạn chưa render: tạm dùng cảnh gốc cho các đoạn đó`)) : null,
    h("div", { class: "row" }, h("span", { class: "spacer" }),
      h("button", { class: "btn" + (missing ? "" : " primary"), disabled: busy, onclick: () => {
        if (missing && !allow.checked) { toast("Render đủ các đoạn, hoặc tick ô tạm dùng cảnh gốc", "error"); return; }
        cloneAction("stitch", { allow_missing: missing > 0 }, "Đang ghép…");
      } }, icon("stitch"), d.final_url ? "Ghép lại " : "Ghép video ", FREE())),
    d.final_url ? h("div", { class: "clone-medias" }, vid(d.final_url, "Bản clone"), vid(d.compare_url, "So sánh gốc | clone")) : null,
    d.folder ? h("p", { class: "muted small" }, "Thư mục: ", h("span", { class: "mono" }, d.folder)) : null);
}
