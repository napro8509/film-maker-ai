"""Flow credit balance on the top bar: the last real reading, minus an estimate for every clip rendered since."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from filmmaker import model_meta
from filmmaker.web import server

from .test_web import client, run  # noqa: F401  (fixture)

APP = Path(__file__).resolve().parent.parent / "filmmaker" / "web" / "static" / "app.js"


def test_balance_is_read_then_each_rendered_clip_takes_its_estimated_price(client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("FAKE_GFLOW_CREDITS", "500")
    assert client.get("/api/credits").json() == {"balance": None} and client.get("/api/env").json()["credit_balance"] is None
    pid = client.post("/api/projects", json={"title": "Ly", "source": "template"}).json()["id"]
    run(client, pid, action="video")  # no reading yet: nothing to subtract from
    assert client.get("/api/credits").json() == {"balance": None}

    r = client.post("/api/env/credits").json()
    assert r["ok"] and r["balance"]["balance"] == 500 and r["balance"]["estimate"] == 500

    sb = client.get(f"/api/projects/{pid}").json()["storyboard"]
    first = sb["scenes"][0]["id"]
    # 10 credits a clip, except the first scene's clip whose model has no known price
    monkeypatch.setattr(model_meta.ModelMeta, "credits", lambda self, alias: None if alias == "no-price" else 10)
    sb["scenes"][0]["model"] = "no-price"
    monkeypatch.setattr(server, "load_storyboard", _with_model(server.load_storyboard, first))
    j = run(client, pid, action="video", force=[s["id"] for s in sb["scenes"]])
    assert j["status"] == "done", j["lines"]
    view = client.get("/api/credits").json()["balance"]
    n = len(sb["scenes"])
    priced = n - 1
    assert view["clips"] == n and view["unpriced"] == n - priced
    assert view["spent"] == 10 * priced and view["estimate"] == 500 - 10 * priced
    assert client.get("/api/env").json()["credit_balance"] == view

    # A new real reading starts over.
    monkeypatch.setenv("FAKE_GFLOW_CREDITS", "420")
    assert client.post("/api/env/credits").json()["balance"] == {**view, "balance": 420, "estimate": 420, "spent": 0,
                                                                  "clips": 0, "unpriced": 0,
                                                                  "checked_at": client.get("/api/credits").json()["balance"]["checked_at"]}


def _with_model(load, sid):
    """load_storyboard, but scene `sid` uses the unpriced model (the storyboard file stays valid)."""
    def wrapped(path):
        sb = load(path)
        for sc in sb.scenes:
            if sc.id == sid:
                sc.model = "no-price"
        return sb
    return wrapped


def test_spend_math_and_hand_downloaded_clips(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SETTINGS_PATH", tmp_path / "settings.json")
    server.record_spend(5)  # never read: ignored
    assert server.credit_view() is None
    server.record_balance(100)
    server.record_spend(7.5)
    server.record_spend(None)
    v = server.credit_view()
    assert (v["balance"], v["estimate"], v["spent"], v["clips"], v["unpriced"]) == (100, 92.5, 7.5, 2, 1)
    (tmp_path / "flow_credits.json").write_text("not json")
    assert server.credit_view() is None


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_credit_button_label(tmp_path):
    js = tmp_path / "h.js"
    js.write_text(r"""
const src = require("fs").readFileSync(process.argv[2], "utf8");
const fn = (n) => src.match(new RegExp("^function " + n + "\\(.*?^}\\n", "ms"))[0];
const label = { textContent: "" }, cls = new Set();
const btn = { title: "", querySelector: () => label, classList: { add: (c) => cls.add(c), remove: (c) => cls.delete(c) } };
const $ = () => btn; const ago = () => "5 phút trước";
let S = { env: {} };
eval(fn("renderCreditButton"));
const out = [];
renderCreditButton(); out.push([label.textContent, cls.has("has-balance")]);
S.env.credit_balance = { balance: 7472, estimate: 7472, spent: 0, clips: 0, unpriced: 0, checked_at: 1 };
renderCreditButton(); out.push([label.textContent, btn.title]);
S.env.credit_balance = { balance: 7472, estimate: 7452, spent: 20, clips: 3, unpriced: 1, checked_at: 1 };
renderCreditButton(); out.push([label.textContent, btn.title]);
console.log(JSON.stringify(out));
""", encoding="utf-8")
    res = subprocess.run(["node", str(js), str(APP)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    plain, read, spent = json.loads(res.stdout)
    assert plain == ["Credit", False]
    assert read[0] == "7.472 credit" and "Flow báo 7.472 credit (5 phút trước)" in read[1]
    assert spent[0] == "~7.452 credit" and "3 clip" in spent[1] and "1 clip chưa có giá" in spent[1]
