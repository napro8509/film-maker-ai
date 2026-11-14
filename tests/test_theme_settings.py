"""Theme setting persistence across app restarts (dark / light / auto).

Covers:
1. Backend `/api/settings` and `GET /` (`index.html` SSR `data-theme` + `<meta name="theme">`) across new `create_app`
   instances on fresh random ports (simulating macOS desktop app restarts).
2. `desktop._window_background()` matching the saved theme.
3. Real `app.js` theme helpers + `openSettings()` executed under Node with an isolated `localStorage` per port,
   reproducing the exact desktop restart scenario where `localStorage` starts empty on a new port.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filmmaker import desktop  # noqa: E402
from filmmaker.web import server  # noqa: E402

APP_JS = Path(__file__).resolve().parent.parent / "filmmaker" / "web" / "static" / "app.js"
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def _make_client(ws: Path, settings_file: Path, monkeypatch, *, port: int = 8765, token: str = "t0k") -> TestClient:
    monkeypatch.setattr(server, "SETTINGS_PATH", settings_file)
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(settings_file))
    app = server.create_app(ws, port=port, token=token)
    c = TestClient(app, base_url=f"http://127.0.0.1:{port}")
    c.headers["X-Token"] = token
    return c


def test_theme_persists_across_app_restarts_on_different_ports(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    settings_file = tmp_path / "settings.json"

    # Launch 1 (port 51001): fresh install defaults to "auto" without forcing data-theme.
    c1 = _make_client(ws, settings_file, monkeypatch, port=51001)
    assert c1.get("/api/settings").json()["theme"] == "auto"
    html0 = c1.get("/").text
    assert '<html lang="vi">' in html0
    assert '<meta name="theme" content="">' in html0

    # User selects Dark mode ("dark") in Cài đặt.
    saved = c1.put("/api/settings", json={"theme": "dark"}).json()
    assert saved["theme"] == "dark"
    assert json.loads(settings_file.read_text(encoding="utf-8"))["theme"] == "dark"

    # Saving unrelated settings preserves the saved theme.
    saved2 = c1.put("/api/settings", json={"pause_min": 5, "pause_max": 10}).json()
    assert saved2["theme"] == "dark"

    # Launch 2 (port 51002 — simulates quitting and reopening Film Maker.app on a new random port).
    c2 = _make_client(ws, settings_file, monkeypatch, port=51002)
    assert c2.get("/api/settings").json()["theme"] == "dark"
    html_dark = c2.get("/").text
    assert '<html lang="vi" data-theme="dark">' in html_dark
    assert '<meta name="theme" content="dark">' in html_dark

    # Switch to Light mode ("light") and reopen on port 51003.
    assert c2.put("/api/settings", json={"theme": "light"}).json()["theme"] == "light"
    c3 = _make_client(ws, settings_file, monkeypatch, port=51003)
    assert c3.get("/api/settings").json()["theme"] == "light"
    html_light = c3.get("/").text
    assert '<html lang="vi" data-theme="light">' in html_light
    assert '<meta name="theme" content="light">' in html_light

    # Switch back to System ("auto") and reopen on port 51004: meta explicitly records "auto".
    assert c3.put("/api/settings", json={"theme": "auto"}).json()["theme"] == "auto"
    c4 = _make_client(ws, settings_file, monkeypatch, port=51004)
    assert c4.get("/api/settings").json()["theme"] == "auto"
    html_auto = c4.get("/").text
    assert '<html lang="vi">' in html_auto
    assert "data-theme=" not in html_auto
    assert '<meta name="theme" content="auto">' in html_auto


def test_invalid_theme_values_are_ignored_or_normalized(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    settings_file = tmp_path / "settings.json"
    c = _make_client(ws, settings_file, monkeypatch)

    c.put("/api/settings", json={"theme": "dark"})
    # Sending an unknown theme does not clobber the valid saved one.
    assert c.put("/api/settings", json={"theme": "neon-pink"}).json()["theme"] == "dark"

    # Hand-corrupted theme on disk falls back to "auto".
    settings_file.write_text(json.dumps({"theme": "broken"}), encoding="utf-8")
    assert c.get("/api/settings").json()["theme"] == "auto"
    html = c.get("/").text
    assert '<html lang="vi">' in html and "data-theme=" not in html


def test_desktop_window_background_follows_saved_theme(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(settings_file))

    assert desktop._window_background() == "#131211"
    settings_file.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    assert desktop._window_background() == "#131211"
    settings_file.write_text(json.dumps({"theme": "light"}), encoding="utf-8")
    assert desktop._window_background() == "#f5f5f3"
    settings_file.write_text(json.dumps({"theme": "auto"}), encoding="utf-8")
    assert desktop._window_background() == "#131211"


NODE_HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");

function cutFn(name) {
  const m = src.match(new RegExp("^(?:async )?function " + name + "\\(.*?^}\\n", "ms"));
  if (!m) throw new Error("missing function " + name);
  return m[0];
}

// Simulate a browser/WKWebView session with its own per-origin localStorage and server-rendered HTML state.
function createSession({ serverSettings, serverSavedTheme, initialLocal = {} }) {
  const store = { ...initialLocal };
  const putBodies = [];
  const docAttrs = {};
  if (serverSavedTheme === "light" || serverSavedTheme === "dark") docAttrs["data-theme"] = serverSavedTheme;
  const metaTheme = { content: serverSavedTheme || "" };

  class Node {}
  class Text extends Node { constructor(t) { super(); this.text = t; } }
  class El extends Node {
    constructor(tag) {
      super();
      this.tagName = tag.toUpperCase();
      this.children = [];
      this.attrs = {};
      this.listeners = {};
      this.style = {};
      this.className = "";
      this.checked = false;
      this.disabled = false;
      this._value = "";
      this._sel = undefined;
    }
    setAttribute(k, v) { this.attrs[k] = String(v); if (k === "id") byId.set(String(v), this); }
    addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
    append(...kids) { for (const k of kids) if (k != null) this.children.push(k); }
    replaceChildren(...kids) { this.children = []; this.append(...kids); }
    options() { return this.children.filter((c) => c instanceof El && c.tagName === "OPTION"); }
    get value() {
      if (this.tagName === "SELECT") {
        const o = this.options();
        return this._sel === undefined ? (o[0] ? o[0].value : "") : this._sel;
      }
      return this._value;
    }
    set value(v) {
      if (this.tagName === "SELECT") {
        const o = this.options().find((x) => x.value === String(v));
        this._sel = o ? o.value : "";
      } else {
        this._value = String(v);
      }
    }
    fire(t) { for (const f of (this.listeners[t] || [])) f({ target: this, preventDefault() {} }); }
    findAll(pred, out = []) {
      if (pred(this)) out.push(this);
      for (const c of this.children) if (c instanceof El) c.findAll(pred, out);
      return out;
    }
  }

  const byId = new Map();
  let lastModal = null;
  const localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  };
  const document = {
    documentElement: {
      setAttribute: (k, v) => { docAttrs[k] = String(v); },
      removeAttribute: (k) => { delete docAttrs[k]; },
      getAttribute: (k) => (k in docAttrs ? docAttrs[k] : null),
    },
    createElement: (t) => {
      if (t === "template") return { set innerHTML(_) {}, content: { firstChild: new El("svg") } };
      return new El(t);
    },
    createTextNode: (t) => new Text(t),
    querySelector: (sel) => (sel === 'meta[name="theme"]' ? metaTheme : null),
    getElementById: (id) => byId.get(id) || null,
  };
  const S = {
    env: { claude_code: {}, gemini_cli: {}, gemini_agy: {}, qc: {}, flow_ui: true },
    settings: null,
    cur: null,
    themeSave: null,
  };
  const api = async (method, url, body) => {
    if (method === "GET" && url === "/api/settings") return { ...serverSettings };
    if (method === "GET" && url === "/api/backup") return { available: false, configured: false, remote: "gdrive:FilmMaker" };
    if (method === "GET" && url === "/api/env") return S.env;
    if (method === "PUT" && url === "/api/settings") {
      putBodies.push(body);
      Object.assign(serverSettings, body);
      return { ...serverSettings };
    }
    throw new Error("unexpected api: " + method + " " + url);
  };
  const modal = (opts) => { lastModal = opts; return () => {}; };
  const toast = () => {};
  const renderTopbar = () => {};
  const loadPublish = () => {};
  const checkAuth = () => {};
  const startLogin = () => {};
  const startJob = () => {};

  const code = [
    "const ICONS = {};",
    cutFn("h"),
    cutFn("put"),
    cutFn("icon"),
    cutFn("field"),
    src.match(/^function localGet\(.*$/m)[0],
    src.match(/^function localSet\(.*$/m)[0],
    "const THEMES = [\"auto\", \"light\", \"dark\"];",
    src.match(/^function cleanTheme\(.*$/m)[0],
    src.match(/^function themeMeta\(.*$/m)[0],
    cutFn("currentTheme"),
    cutFn("applyTheme"),
    cutFn("saveTheme"),
    cutFn("syncThemeOnBoot"),
    cutFn("openSettings"),
    "globalThis.exported = { S, applyTheme, saveTheme, syncThemeOnBoot, openSettings, currentTheme };",
  ].join("\n");
  eval(code);
  const ex = globalThis.exported;

  return {
    S: ex.S,
    docAttrs,
    store,
    putBodies,
    metaTheme,
    boot: async () => {
      ex.applyTheme(); // runs synchronously at bottom of app.js before /api/settings resolves
      const atFirstPaint = docAttrs["data-theme"] || null;
      ex.S.settings = await api("GET", "/api/settings");
      ex.syncThemeOnBoot();
      if (ex.S.themeSave) await ex.S.themeSave;
      return { atFirstPaint, afterBoot: docAttrs["data-theme"] || null };
    },
    openSettings: async () => {
      await ex.openSettings();
      const selects = lastModal.body.findAll((el) => el.tagName === "SELECT" && el.options().some((o) => o.value === "dark"));
      return { themeSelect: selects[0], saveBtn: lastModal.buttons.find((b) => b.label === "Lưu") };
    },
  };
}

(async () => {
  const disk = { pause_min: 8, pause_max: 20, retries: 2, planner: "auto", gemini_backend: "auto",
                 flow_ui: true, flow_ui_max_credits: 0, flow_ui_window: "hidden",
                 backup_remote: "gdrive:FilmMaker", auto_backup: false, auto_qc: true, theme: "auto" };

  // 1. Port 1: user opens Cài đặt, selects Dark ("dark"), clicks Lưu.
  const s1 = createSession({ serverSettings: disk, serverSavedTheme: "" });
  const boot1 = await s1.boot();
  const ui1 = await s1.openSettings();
  const initialSelect = ui1.themeSelect.value;
  ui1.themeSelect.value = "dark";
  ui1.themeSelect.fire("change");
  const immediateAttr = s1.docAttrs["data-theme"];
  await ui1.saveBtn.onclick(() => {});

  // 2. Port 2 (app closed & reopened on a new port -> localStorage is completely empty!):
  const s2 = createSession({ serverSettings: disk, serverSavedTheme: disk.theme, initialLocal: {} });
  const boot2 = await s2.boot();
  const ui2 = await s2.openSettings();
  const reopenedSelect = ui2.themeSelect.value;

  // 3. Port 2: user changes to Light ("light") and closes modal WITHOUT clicking Lưu -> still saved!
  ui2.themeSelect.value = "light";
  ui2.themeSelect.fire("change");
  if (s2.S.themeSave) await s2.S.themeSave;

  // 4. Port 3 (reopened again with empty localStorage): starts in Light mode!
  const s3 = createSession({ serverSettings: disk, serverSavedTheme: disk.theme, initialLocal: {} });
  const boot3 = await s3.boot();
  const ui3 = await s3.openSettings();

  // 5. Switch back to "auto" even when localStorage on an old port still had "dark": server "auto" wins.
  disk.theme = "auto";
  const s4 = createSession({ serverSettings: disk, serverSavedTheme: "auto", initialLocal: { "fm.theme": '"dark"' } });
  const boot4 = await s4.boot();

  // 6. Legacy migration: settings.json has never stored theme (serverSavedTheme === ""), browser localStorage has "dark".
  const legacyDisk = { ...disk, theme: "auto" };
  const s5 = createSession({ serverSettings: legacyDisk, serverSavedTheme: "", initialLocal: { "fm.theme": '"dark"' } });
  const boot5 = await s5.boot();

  // 7. Rapid sequential changes ("dark" -> "light" -> "dark") serialize cleanly so the last choice always wins.
  const rapidDisk = { ...disk, theme: "auto" };
  const s6 = createSession({ serverSettings: rapidDisk, serverSavedTheme: "auto" });
  await s6.boot();
  const ui6 = await s6.openSettings();
  ui6.themeSelect.value = "dark"; ui6.themeSelect.fire("change");
  ui6.themeSelect.value = "light"; ui6.themeSelect.fire("change");
  ui6.themeSelect.value = "dark"; ui6.themeSelect.fire("change");
  if (s6.S.themeSave) await s6.S.themeSave;

  process.stdout.write(JSON.stringify({
    boot1,
    initialSelect,
    immediateAttr,
    s1PutBodies: s1.putBodies,
    boot2,
    reopenedSelect,
    boot3,
    lightSelect: ui3.themeSelect.value,
    boot4,
    s4LocalAfter: JSON.parse(s4.store["fm.theme"]),
    boot5,
    legacyDiskTheme: legacyDisk.theme,
    rapidPuts: s6.putBodies.map((b) => b.theme),
    rapidFinal: [rapidDisk.theme, s6.docAttrs["data-theme"], s6.S.settings.theme],
  }));
})();
"""


@needs_node
def test_app_js_syntax_and_theme_restart_flow(tmp_path):
    chk = subprocess.run(["node", "--check", str(APP_JS)], capture_output=True, text=True)
    assert chk.returncode == 0, chk.stderr

    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(harness), str(APP_JS)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)

    # Fresh install starts in auto (no data-theme attribute).
    assert out["boot1"] == {"atFirstPaint": None, "afterBoot": None}
    assert out["initialSelect"] == "auto"

    # Selecting "dark" immediately applies dark mode AND saves to /api/settings; clicking Lưu also includes theme: "dark".
    assert out["immediateAttr"] == "dark"
    assert out["s1PutBodies"][0] == {"theme": "dark"}
    assert out["s1PutBodies"][1]["theme"] == "dark"

    # Reopening the app on a brand-new port (empty localStorage) keeps dark mode from first paint onwards!
    assert out["boot2"] == {"atFirstPaint": "dark", "afterBoot": "dark"}
    assert out["reopenedSelect"] == "dark"

    # Changing to "light" without clicking Lưu also persists across restart on another new port.
    assert out["boot3"] == {"atFirstPaint": "light", "afterBoot": "light"}
    assert out["lightSelect"] == "light"

    # Switching back to "auto" overrides stale localStorage.
    assert out["boot4"] == {"atFirstPaint": None, "afterBoot": None}
    assert out["s4LocalAfter"] == "auto"

    # Legacy browser localStorage-only dark theme is preserved and migrated to /api/settings.
    assert out["boot5"] == {"atFirstPaint": "dark", "afterBoot": "dark"}
    assert out["legacyDiskTheme"] == "dark"

    # Rapid sequential changes serialize in order and end on the final choice.
    assert out["rapidPuts"] == ["dark", "light", "dark"]
    assert out["rapidFinal"] == ["dark", "dark", "dark"]
