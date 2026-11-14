"""scene-continuity T2: keyframe chaining, reference slots sent to gflow, state, logs and location images.

Every test uses tests/fake_gflow.py (GFLOW_BIN) or a dry-run Gflow; never the real Flow."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from filmmaker.gflow import Gflow
from filmmaker.pipeline import Pipeline, RunOptions, file_sha
from filmmaker.storyboard import load_storyboard

FAKE = Path(__file__).parent / "fake_gflow.py"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

ROOM = "long dark teak table, brass ceiling lamp"


def scene(sid: str, **kw) -> dict:
    d = {"id": sid, "mode": "keyframe", "image_prompt": f"Minh, Lan and Tuan at the table ({sid})",
         "prompt": "they argue", "location": "Phòng khách"}
    d.update(kw)
    return {k: v for k, v in d.items() if v is not None}


def three(**over) -> list[dict]:
    return [scene("s01"), scene("s02"), scene("s03"), *over.get("extra", [])]


class Env:
    def __init__(self, tmp_path: Path, monkeypatch):
        self.tmp = tmp_path
        self.log_file = tmp_path / "calls.log"
        self.lines: list[str] = []
        monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
        monkeypatch.setenv("FAKE_GFLOW_LOG", str(self.log_file))
        for var in ("FAKE_GFLOW_FAIL", "FAKE_GFLOW_CATALOG", "FAKE_GFLOW_NO_AUDIO", "FLOW_UI_CMD"):
            monkeypatch.delenv(var, raising=False)
        self.monkeypatch = monkeypatch
        for n in ("minh", "lan", "tuan"):
            (tmp_path / f"{n}.png").write_bytes(f"{n}-pic".encode())
        (tmp_path / "room.png").write_bytes(b"room-pic")

    def write(self, scenes: list[dict], **top) -> Path:
        data = {"title": "Chain", "aspect": "9:16", "model": "veo-lite", "image_model": "nano2",
                "characters": [{"name": n, "description": f"{n} look", "image": f"{n.lower()}.png"}
                               for n in ("Minh", "Lan", "Tuan")],
                "locations": [{"name": "Phòng khách", "description": ROOM, "image": "room.png"},
                              {"name": "Bếp", "description": "small kitchen"}],
                "scenes": scenes}
        data.update(top)
        p = self.tmp / "sb.yaml"
        p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return p

    def pipe(self, scenes: list[dict] | None = None, *, dry: bool = False, **top) -> Pipeline:
        p = self.write(scenes, **top) if scenes is not None else self.tmp / "sb.yaml"
        self.lines = []
        return Pipeline(load_storyboard(p), self.tmp / "out",
                        Gflow(log=self.lines.append, ui_enabled=False, dry_run=dry),
                        log=self.lines.append, sleep=lambda *_: None)

    def calls(self) -> list[list[str]]:
        lines = self.log_file.read_text().splitlines() if self.log_file.exists() else []
        self.log_file.unlink(missing_ok=True)
        root = [str(self.tmp.resolve()), str(self.tmp)]
        out = []
        for line in lines:
            c = json.loads(line)
            for r in root:
                c = [a.replace(r, "<tmp>") for a in c]
            out.append(c)
        return out

    def state(self, sid: str) -> dict:
        return json.loads((self.tmp / "out" / "state.json").read_text())["scenes"].get(sid, {})

    def catalog(self, rows: list[tuple[str, Path]]) -> None:
        cat = self.tmp / "catalog.jsonl"
        cat.write_text("\n".join(json.dumps({"media_id": m, "project_id": "proj-123", "local_path": str(p),
                                             "prompt": "x"}) for m, p in rows))
        self.monkeypatch.setenv("FAKE_GFLOW_CATALOG", str(cat))


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def image_call(calls, name: str) -> list[str]:
    found = [c for c in calls if c[0] == "image" and Path(c[c.index("-o") + 1]).name == name]
    assert len(found) == 1, found
    return found[0]


def refs(call: list[str]) -> list[str]:
    return [call[i + 1] for i, a in enumerate(call) if a == "--ref"]


def kf_only(pipe: Pipeline, **kw) -> None:
    report = pipe.run(RunOptions(pause=(0, 0), keyframes_only=True, **kw))
    assert report.ok, report


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# --- AC4 / AC17 / AC22 / AC25 / AC29 -------------------------------------------------------------------------

@needs_ffmpeg
def test_keyframes_only_order_and_chain(env):
    """AC4 (no continuity keys = on), AC17 (refs + log), AC22 (state), AC25 (order), AC29 (no video call)."""
    kf_only(env.pipe(three()))
    calls = env.calls()
    assert [Path(c[c.index("-o") + 1]).name for c in calls if c[0] == "image"] == ["s01.png", "s02.png", "s03.png"]
    assert refs(image_call(calls, "s01.png")) == ["<tmp>/room.png", "<tmp>/minh.png", "<tmp>/lan.png", "<tmp>/tuan.png"]
    assert refs(image_call(calls, "s02.png")) == ["<tmp>/out/keyframes/s01.png", "<tmp>/minh.png", "<tmp>/lan.png", "<tmp>/tuan.png"]
    assert refs(image_call(calls, "s03.png")) == ["<tmp>/out/keyframes/s02.png", "<tmp>/minh.png", "<tmp>/lan.png", "<tmp>/tuan.png"]
    assert "Setting: long dark teak table, brass ceiling lamp." in image_call(calls, "s02.png")[2]
    assert not any(c[0] == "video" for c in calls)
    log = "\n".join(env.lines)
    assert "  không nối keyframe cảnh trước: cảnh đầu; dùng ảnh bối cảnh" in env.lines
    assert "  ảnh tham chiếu: bối cảnh Phòng khách, Minh, Lan, Tuan" in env.lines
    assert "  nối keyframe s01 (giữ ánh sáng, vị trí người)" in env.lines
    assert "  ảnh tham chiếu: keyframe s01, Minh, Lan, Tuan" in env.lines
    assert not any("bỏ ảnh" in l for l in env.lines)  # no character picture is ever dropped now
    assert "ảnh tham chiếu nhân vật" not in log
    kfs = env.tmp / "out" / "keyframes"
    assert env.state("s01")["keyframe_from"] is None and env.state("s01")["keyframe_from_sha"] is None
    assert env.state("s02")["keyframe_from"] == "s01"
    assert env.state("s02")["keyframe_from_sha"] == sha(kfs / "s01.png")
    assert env.state("s03")["keyframe_from"] == "s02"
    assert env.state("s03")["keyframe_from_sha"] == sha(kfs / "s02.png")
    pipe = env.pipe()
    assert not any(pipe.continuity_outdated(s) for s in pipe.sb.scenes)


@needs_ffmpeg
def test_chain_media_id_on_new_pipeline(env):
    """AC17: on a later run, a keyframe already in the Flow project goes as its media id."""
    kf_only(env.pipe(three()))
    env.calls()
    flow_copy = env.tmp / "flow" / "s01.png"
    flow_copy.parent.mkdir()
    flow_copy.write_bytes((env.tmp / "out" / "keyframes" / "s01.png").read_bytes())
    env.catalog([("media-kf-s01-0001", flow_copy)])
    kf_only(env.pipe(), only={"s02"}, force_keyframes={"s02"})
    assert refs(image_call(env.calls(), "s02.png")) == ["media-kf-s01-0001", "<tmp>/minh.png", "<tmp>/lan.png", "<tmp>/tuan.png"]
    assert env.state("s02")["keyframe_from"] == "s01"


def test_dry_run_logs_will_chain(env):
    """AC25 dry-run: "sẽ nối keyframe s01 (nếu tạo xong)" for s02, keyframes/s01.png as ref, no state."""
    kf_only(env.pipe(three(), dry=True))
    assert "  sẽ nối keyframe s01 (nếu tạo xong)" in env.lines
    assert "  sẽ nối keyframe s02 (nếu tạo xong)" in env.lines
    assert "  ảnh tham chiếu: keyframe s01, Minh, Lan, Tuan" in env.lines
    i2i = [l for l in env.lines if " image i2i " in l]
    assert len(i2i) == 3 and str(env.tmp / "out" / "keyframes" / "s01.png") in i2i[1]
    assert not (env.tmp / "out" / "state.json").exists()


def test_dry_run_full_run_logs_will_chain(env):
    report = env.pipe(three(), dry=True).run(RunOptions(pause=(0, 0)))
    assert report.ok
    assert "  sẽ nối keyframe s01 (nếu tạo xong)" in env.lines


@needs_ffmpeg
def test_full_run_chains_in_render_scene(env):
    """`run` (not --keyframes-only): s01 image -> s01 video -> s02 i2i with keyframes/s01.png."""
    report = env.pipe(three()[:2]).run(RunOptions(pause=(0, 0)))
    assert report.ok, report
    calls = env.calls()
    kinds = [(c[0], c[1], Path(c[c.index("-o") + 1]).name) for c in calls if c[0] in ("image", "video")]
    assert kinds == [("image", "i2i", "s01.png"), ("video", "i2v", "s01.mp4"),
                     ("image", "i2i", "s02.png"), ("video", "i2v", "s02.mp4")]
    assert refs(image_call(calls, "s02.png"))[0] == "<tmp>/out/keyframes/s01.png"
    assert env.state("s02")["keyframe_from"] == "s01"


# --- AC23: no chaining --------------------------------------------------------------------------------------

def _rerun_s02(env, scenes, why: str, **top):
    """Chain s02 first, then change the storyboard and remake s02: it uses the location picture."""
    kf_only(env.pipe(three()[:2]))
    assert env.state("s02")["keyframe_from"] == "s01"
    env.calls()
    kf_only(env.pipe(scenes, **top), only={"s02"}, force_keyframes={"s02"})
    assert f"  không nối keyframe cảnh trước: {why}; dùng ảnh bối cảnh" in env.lines
    assert refs(image_call(env.calls(), "s02.png")) == ["<tmp>/room.png", "<tmp>/minh.png", "<tmp>/lan.png", "<tmp>/tuan.png"]
    assert env.state("s02")["keyframe_from"] is None and env.state("s02")["keyframe_from_sha"] is None


@needs_ffmpeg
def test_no_chain_other_location(env):
    _rerun_s02(env, [scene("s01", location="Bếp"), scene("s02")], "cảnh trước khác bối cảnh")


@needs_ffmpeg
def test_no_chain_prev_not_keyframe(env):
    (env.tmp / "start.png").write_bytes(b"start")
    _rerun_s02(env, [scene("s01", mode="i2v", image="start.png", image_prompt=None), scene("s02")],
               "cảnh trước không phải keyframe")


@needs_ffmpeg
def test_no_chain_prev_stale(env):
    _rerun_s02(env, [scene("s01", image_prompt="something else"), scene("s02")], "chưa có keyframe s01 mới nhất")


@needs_ffmpeg
def test_no_chain_scene_off(env):
    _rerun_s02(env, [scene("s01"), scene("s02", continue_previous=False)], "cảnh này tắt nối")


@needs_ffmpeg
def test_no_chain_film_off(env):
    _rerun_s02(env, three()[:2], "phim tắt nối", continuity=False)


@needs_ffmpeg
def test_no_chain_first(env):
    _rerun_s02(env, [scene("s02"), scene("s01")], "cảnh đầu")


def test_no_location_image_says_so(env):
    sc = [scene("s01", location="Bếp")]
    kf_only(env.pipe(sc, dry=True))
    assert "  không nối keyframe cảnh trước: cảnh đầu; bối cảnh chưa có ảnh" in env.lines
    assert "  ảnh tham chiếu: Minh, Lan, Tuan" in env.lines


@needs_ffmpeg
def test_only_does_not_make_prev(env):
    """AC26: --only s03 while s02 has no keyframe: s02 is not made, s03 uses the location picture."""
    kf_only(env.pipe(three()), only={"s03"})
    calls = env.calls()
    assert [Path(c[c.index("-o") + 1]).name for c in calls if c[0] == "image"] == ["s03.png"]
    assert refs(calls[-1])[0] == "<tmp>/room.png"
    assert "  không nối keyframe cảnh trước: chưa có keyframe s02 mới nhất; dùng ảnh bối cảnh" in env.lines


# --- AC24 / review 5: fallbacks -------------------------------------------------------------------------------

@needs_ffmpeg
def test_i2i_fail_no_keyframe_from(env):
    kf_only(env.pipe(three()[:1]))
    env.calls()
    env.monkeypatch.setenv("FAKE_GFLOW_FAIL", "image i2i:1:false")
    kf_only(env.pipe(three()[:2]), only={"s02"})
    calls = env.calls()
    assert [c[:2] for c in calls if c[0] == "image"] == [["image", "i2i"], ["image", "t2i"]]
    assert env.state("s02")["keyframe_from"] is None and env.state("s02")["keyframe_from_sha"] is None
    pipe = env.pipe()
    assert pipe.continuity_outdated(pipe.sb.scenes[1]) is True


@needs_ffmpeg
@pytest.mark.parametrize("ui_ok", [True, False])
def test_chain_through_flow_chat_ui(env, ui_ok):
    """i2i through the Flow chat page (state `i2i_ui`): keyframe_from is written only when it made the image."""
    from filmmaker.gflow import GflowError

    kf_only(env.pipe(three()[:1]))
    pipe = env.pipe(three()[:2])
    pipe.state.data["i2i_ui"] = True
    sent: list[list[Path]] = []

    def ui_i2i(prompt, files, *, out, **kw):
        sent.append(list(files))
        if not ui_ok:
            raise GflowError("ui broke", exit_code=1)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"ui-image")
        return Path(out)

    pipe.g.ui_available = lambda: True
    pipe.g.ui_image_to_image = ui_i2i
    kf_only(pipe, only={"s02"})
    kfs = env.tmp / "out" / "keyframes"
    assert sent == [[kfs / "s01.png", (env.tmp / "minh.png").resolve(), (env.tmp / "lan.png").resolve(),
                     (env.tmp / "tuan.png").resolve()]]
    st = env.state("s02")
    if ui_ok:
        assert st["keyframe_from"] == "s01" and st["keyframe_from_sha"] == sha(kfs / "s01.png")
    else:
        assert st["keyframe_from"] is None and st["keyframe_from_sha"] is None


# --- AC27: regenerating the previous keyframe ------------------------------------------------------------------

@needs_ffmpeg
def test_regen_prev_keeps_status(env):
    report = env.pipe(three()[:2]).run(RunOptions(pause=(0, 0)))
    assert report.ok
    env.calls()
    pipe = env.pipe()
    s01, s02 = pipe.sb.scenes
    fp = pipe.sb.scene_fingerprint(s02)
    kf_only(pipe, only={"s01"}, force_keyframes={"s01"})
    (env.tmp / "out" / "keyframes" / "s01.png").write_bytes(b"a newer keyframe")  # the fake always draws the same
    pipe = env.pipe()
    s02 = pipe.sb.scenes[1]
    assert pipe.sb.scene_fingerprint(s02) == fp
    assert pipe.scene_status(s02) == "done" and pipe.has_keyframe(s02)
    assert pipe.continuity_outdated(s02) is True
    assert not any(c[0] == "video" for c in env.calls())


# --- AC16 / AC20 / AC21: video refs and old log lines -------------------------------------------------------------

@needs_ffmpeg
def test_t2v_r2v_refs(env):
    """AC20: a t2v scene with a location sends [location, all its characters], never the previous keyframe."""
    report = env.pipe([scene("s01"), scene("s02", mode="t2v", image_prompt=None, prompt="Minh, Lan and Tuan stand")]
                      ).run(RunOptions(pause=(0, 0)))
    assert report.ok, report
    r2v = [c for c in env.calls() if c[:2] == ["video", "r2v"]]
    assert len(r2v) == 1
    # The chat page is off here (CLI), so gflow video r2v is trimmed to the Veo --ref cap (3); the uncapped
    # list still shows in the log line below. With the chat page on, every reference is attached by name.
    assert refs(r2v[0]) == ["<tmp>/room.png", "<tmp>/minh.png", "<tmp>/lan.png"]
    assert "Setting: long dark teak table, brass ceiling lamp." in r2v[0][2]
    assert env.lines.count("  ảnh tham chiếu: bối cảnh Phòng khách, Minh, Lan, Tuan") == 2


def test_old_log_line_kept(env):
    """AC21: scenes without a location keep the old log line and three characters."""
    kf_only(env.pipe([scene("s01", location=None)], dry=True))
    assert "  ảnh tham chiếu nhân vật: Minh, Lan, Tuan" in env.lines
    assert not any(l.startswith("  ảnh tham chiếu: ") or "nối keyframe" in l or "bỏ ảnh" in l for l in env.lines)


def test_thumbnail_log_unchanged(env):
    pipe = env.pipe(three(), dry=True, thumbnail={"prompt": "Minh and Lan on a poster"})
    pipe.make_thumbnail(RunOptions(pause=(0, 0)))
    assert "  ảnh tham chiếu nhân vật: Minh, Lan" in env.lines


def test_old_storyboard_state_has_no_new_keys(env, monkeypatch):
    """Keyframes of scenes without a location never write keyframe_from (state.json of old films unchanged)."""
    pipe = env.pipe([scene("s01", location=None)])
    pipe.g = Gflow(log=env.lines.append, ui_enabled=False, dry_run=True)
    monkeypatch.setattr(pipe.g, "dry_run", False)
    monkeypatch.setattr(pipe.g, "text_to_image", lambda *a, **k: None)

    def i2i(prompt, refs, *, out, **kw):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"img")
        return Path(out)

    monkeypatch.setattr(pipe.g, "image_to_image", i2i)
    monkeypatch.setattr(pipe.g, "catalog_images", lambda project: [])
    pipe.state.data["project_id"] = "proj-1"
    kf_only(pipe)
    assert "keyframe_from" not in env.state("s01") and "keyframe_from_sha" not in env.state("s01")


# --- file_sha ------------------------------------------------------------------------------------------------------

def test_file_sha_cache(tmp_path):
    f = tmp_path / "a.png"
    assert file_sha(f) is None
    f.write_bytes(b"one")
    assert file_sha(f) == hashlib.sha256(b"one").hexdigest()
    f.write_bytes(b"two-longer")
    assert file_sha(f) == hashlib.sha256(b"two-longer").hexdigest()


# --- location images ------------------------------------------------------------------------------------------------

@needs_ffmpeg
def test_generate_location_images(env):
    events = []
    pipe = env.pipe(three())
    pipe.rep = type(pipe.rep)(lambda key, item: events.append((key, item)))
    done = []
    locs = [(pipe.sb.locations[1], env.tmp / "assets" / "locations" / "bep.png")]
    report = pipe.generate_location_images(locs, RunOptions(pause=(0, 0)),
                                           on_done=lambda i, loc, dest: done.append((i, loc.name, dest.name)))
    assert report.ok and report.done == ["Bếp"]
    assert done == [(0, "Bếp", "bep.png")]
    assert (env.tmp / "assets" / "locations" / "bep.png").is_file()
    calls = env.calls()
    t2i = [c for c in calls if c[:2] == ["image", "t2i"]]
    assert len(t2i) == 1 and t2i[0][2].startswith("Location reference image: small kitchen.")
    assert not any(c[0] == "video" for c in calls)
    assert any(l.startswith("[Bếp] ảnh bối cảnh: Location reference image") for l in env.lines)
    assert [k for k, it in events if it and it["phase"] == "done"] == ["loc:Bếp"]
