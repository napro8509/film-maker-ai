"""CLI side of model-retirement: `check STORYBOARD`, blocking/warning in `run`/`make`, planner/template defaults.

Everything runs in-process (CliRunner) so the patched `model_meta.today` applies.
"""
import json
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from filmmaker import manual_clip, model_meta, planner
from filmmaker.cli import main
from filmmaker.gflow import Gflow
from filmmaker.pipeline import DONE, SUBMITTING, Pipeline
from filmmaker.storyboard import Storyboard, load_storyboard

FAKE = Path(__file__).parent / "fake_gflow.py"
ROOT = Path(__file__).parent.parent
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="needs ffmpeg/ffprobe")

STORY = """
title: Model film
aspect: "16:9"
model: veo-fast
scenes:
  - id: s01
    mode: t2v
    prompt: "one"
  - id: s02
    mode: t2v
    prompt: "two"
"""

# s01 done, s02 interrupted, s03 changed (old clip, prompt edited), s04 todo, s05 pinned to an active model.
GROUPS = """
title: Groups
aspect: "16:9"
model: veo-fast
scenes:
  - {id: s01, mode: t2v, prompt: "one"}
  - {id: s02, mode: t2v, prompt: "two"}
  - {id: s03, mode: t2v, prompt: "three"}
  - {id: s04, mode: t2v, prompt: "four"}
  - {id: s05, mode: t2v, prompt: "five", model: omni-1.2-flash}
"""


def set_today(monkeypatch, y, m, d):
    monkeypatch.setattr(model_meta, "today", lambda: date(y, m, d))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fake gflow that is logged in, settings/override in tmp, today = 09/11/2026."""
    log = tmp_path / "calls.log"
    monkeypatch.setenv("GFLOW_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(log))
    monkeypatch.delenv("FAKE_GFLOW_FAIL", raising=False)
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("FILMMAKER_MODELS", raising=False)
    set_today(monkeypatch, 2026, 11, 9)

    class E:
        root = tmp_path
        override = tmp_path / "models.yaml"  # <settings dir>/models.yaml

        @staticmethod
        def calls():
            return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

        @staticmethod
        def video_calls():
            return [c for c in E.calls() if c and c[0] == "video"]

        @staticmethod
        def storyboard(text=STORY):
            p = tmp_path / "sb.yaml"
            p.write_text(text)
            return p

    return E


def cli(*args):
    return CliRunner().invoke(main, [str(a) for a in args])


def pipe_for(sb_path: Path) -> Pipeline:
    return Pipeline(load_storyboard(sb_path), sb_path.parent / "output" / sb_path.stem,
                    Gflow(dry_run=True, log=lambda *_: None), log=lambda *_: None)


def put_state(pipe: Pipeline, sid: str, status: str, *, good_fp=True, clip_bytes=None, submitted_at=None):
    s = next(x for x in pipe.sb.scenes if x.id == sid)
    if clip_bytes is not None:
        clip = pipe.clip_path(s)
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.write_bytes(b"\0" * clip_bytes)
        t = manual_clip.parse_stamp(submitted_at)
        if t is not None:  # saved after the submission
            os.utime(clip, (t + 5, t + 5))
    fields = {"status": status}
    if submitted_at is not None:
        fields["submitted_at"] = submitted_at
    if status == DONE:
        video = pipe.out / "done" / f"{sid}.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"\0" * 20_000)
        fields.update(video=str(video), fingerprint=pipe.sb.scene_fingerprint(s) if good_fp else "old")
    pipe.state.update(sid, **fields)


def groups_project(env) -> Path:
    sb = env.storyboard(GROUPS)
    pipe = pipe_for(sb)
    put_state(pipe, "s01", DONE)
    put_state(pipe, "s02", SUBMITTING)
    put_state(pipe, "s03", DONE, good_fp=False)  # "changed" -> counts as no clip
    return sb


def model_lines(text: str) -> list[str]:
    return [l for l in text.splitlines() if re.match(r"\s+s\d\d\s", l)]


# --- check -----------------------------------------------------------------

@needs_ffmpeg
def test_cli_check_without_args_unchanged(env):
    env.override.write_text("models: [broken\n")  # even a broken override must not show up (AC8 vs AC20)
    res = cli("check")
    assert res.exit_code == 0, res.output
    assert "⚠" not in res.output
    assert "model" not in res.output.lower()
    lines = res.output.splitlines()
    assert lines[0].startswith("✔ gflow: ")
    assert lines[1:] == ["logged in", f"✔ ffmpeg: {shutil.which('ffmpeg')}", f"✔ ffprobe: {shutil.which('ffprobe')}"]
    # `check STORYBOARD` prints the same tool section first.
    sb = env.storyboard()
    env.override.unlink()
    res2 = cli("check", sb)
    assert res2.output.splitlines()[:4] == lines


def test_cli_check_lists_groups(env):
    sb = groups_project(env)
    res = cli("check", sb)
    out = res.output
    rows = model_lines(out)
    assert rows == ["  s03  veo-fast  ngừng ngày 05/12", "  s04  veo-fast  ngừng ngày 05/12",
                    "  s02  veo-fast  ngừng ngày 05/12", "  s01  veo-fast  ngừng ngày 05/12"]
    i_todo, i_int, i_done = out.index("Chưa có clip"), out.index("Bị ngắt"), out.index("Đã có clip")
    assert i_todo < out.index("s03") < i_int < out.index("s02") < i_done < out.index("s01")
    assert "s05" not in out  # pinned to an active model


def test_cli_check_default_out_and_explicit_out(env):
    sb = env.storyboard()
    other = env.root / "elsewhere"
    p = Pipeline(load_storyboard(sb), other, Gflow(dry_run=True, log=lambda *_: None), log=lambda *_: None)
    put_state(p, "s01", DONE)
    assert "  s01  veo-fast  ngừng ngày 05/12" in cli("check", sb).output
    out = cli("check", sb, "--out", other).output
    assert out.index("Chưa có clip") < out.index("s02") < out.index("Đã có clip") < out.index("s01")


def test_cli_check_storyboard_without_gflow(env, monkeypatch):
    monkeypatch.setenv("GFLOW_BIN", str(env.root / "no-such-gflow"))
    sb = env.storyboard()
    res = cli("check", sb)
    assert res.exit_code == 1  # gflow missing, as before
    assert "✘ Không thấy gflow" in res.output
    assert model_lines(res.output) == ["  s01  veo-fast  ngừng ngày 05/12", "  s02  veo-fast  ngừng ngày 05/12"]


def test_cli_check_no_old_models(env):
    sb = env.storyboard(STORY.replace("model: veo-fast", "model: veo-lite"))
    res = cli("check", sb)
    assert "✔ Không cảnh nào dùng model sắp/đã ngừng" in res.output


def test_cli_check_bad_storyboard(env):
    sb = env.storyboard("title: x\nscenes:\n  - {id: 'bad id', prompt: p}\n")
    res = cli("check", sb)
    assert res.exit_code == 1
    assert "✘ Storyboard lỗi" in res.output
    assert res.exception is None or isinstance(res.exception, SystemExit)


@needs_ffmpeg
def test_cli_check_exit_codes(env, monkeypatch):
    sb = groups_project(env)
    res = cli("check", sb)  # retiring: warning only
    assert res.exit_code == 0, res.output
    set_today(monkeypatch, 2026, 12, 5)
    res = cli("check", sb)  # s03 (changed) and s04 (todo) on a retired model
    assert res.exit_code == 1, res.output
    assert "đã ngừng từ 05/12" in res.output
    # Only done / interrupted scenes on the retired model -> 0.
    raw = yaml.safe_load(sb.read_text())
    for sc in raw["scenes"]:
        if sc["id"] in ("s03", "s04"):
            sc["model"] = "veo-lite"
    sb.write_text(yaml.safe_dump(raw, sort_keys=False))
    res = cli("check", sb)
    assert res.exit_code == 0, res.output
    assert "  s01  veo-fast  đã ngừng từ 05/12" in res.output
    assert "  s02  veo-fast  đã ngừng từ 05/12" in res.output


@needs_ffmpeg
def test_cli_check_uses_override_retired_veo_lite(env):
    sb = env.storyboard(STORY.replace("model: veo-fast", "model: veo-lite"))
    env.override.write_text("models:\n  veo-lite:\n    retires: 2026-11-01\n")
    res = cli("check", sb)
    assert "  s01  veo-lite  đã ngừng từ 01/11" in res.output
    assert res.exit_code == 1


@needs_ffmpeg
def test_cli_warns_on_bad_override_exit_code_unchanged(env):
    sb = env.storyboard()
    base_check = cli("check", sb)
    base_dry = cli("run", sb, "--dry-run")
    env.override.write_text("models: [broken\n")
    for args, base in ((("check", sb), base_check), (("run", sb, "--dry-run"), base_dry)):
        res = cli(*args)
        warn = [l for l in res.output.splitlines() if l.startswith("⚠ File metadata model lỗi:")]
        assert len(warn) == 1, res.output
        assert str(env.override) in warn[0] and warn[0].endswith("; dùng bản mặc định.")
        assert res.exit_code == base.exit_code
    assert "⚠ File metadata model lỗi" not in cli("check").output


@pytest.mark.parametrize("override", ["models:\n  veo-fast: 2026-12-05\n",
                                      "models:\n  veo-lite:\n    price: 5\n"])
def test_wrong_type_override_no_crash(env, override):
    sb = env.storyboard()
    env.override.write_text(override)
    for args in (("check", sb), ("run", sb, "--dry-run")):
        res = cli(*args)
        assert res.exception is None or isinstance(res.exception, SystemExit), res.output
        assert "⚠ File metadata model lỗi" in res.output
        assert "Traceback" not in res.output


@needs_ffmpeg
def test_broken_package_metadata_cli_warns_and_does_not_block(env, monkeypatch):
    bad = env.root / "pkg-models.yaml"
    bad.write_text("plan: [oops\n")
    monkeypatch.setattr(model_meta, "PACKAGE_FILE", bad)
    set_today(monkeypatch, 2026, 12, 6)
    sb = env.storyboard()
    res = cli("check", sb)
    assert "⚠ File metadata model lỗi" in res.output
    assert res.exit_code == 0, res.output
    assert model_lines(res.output) == []


# --- run / make ------------------------------------------------------------

@pytest.mark.parametrize("cmd", ["run", "make"])
def test_cli_run_blocked_after_retirement(env, monkeypatch, cmd):
    set_today(monkeypatch, 2026, 12, 5)
    sb = env.storyboard()
    res = cli(cmd, sb, "--pause", "0")
    assert res.exit_code != 0
    assert "s01: veo-fast (đã ngừng từ 05/12)" in res.output
    assert "s02: veo-fast" in res.output
    assert "đổi model trong UI hoặc sửa `model:` trong storyboard" in res.output
    assert env.calls() == []
    assert not (env.root / "output" / "sb" / "state.json").exists()


def test_cli_run_not_blocked_by_done_scene(env, monkeypatch):
    """A done scene on a retired model is not sent, so it does not block (only s02 is sent, on veo-lite)."""
    sb = env.storyboard(STORY + "    model: veo-lite\n")
    put_state(pipe_for(sb), "s01", DONE)
    set_today(monkeypatch, 2026, 12, 5)
    res = cli("run", sb, "--dry-run")
    assert "đã ngừng" not in res.output and "⚠ Model" not in res.output


@needs_ffmpeg
@pytest.mark.parametrize("cmd", ["run", "make"])
def test_cli_run_warns_before_retirement(env, cmd):
    sb = env.storyboard()
    res = cli(cmd, sb, "--pause", "0-0")
    assert res.exit_code == 0, res.output
    warn = [l for l in res.output.splitlines() if l.startswith("⚠ Model sắp ngừng/đã ngừng:")]
    assert warn == ["⚠ Model sắp ngừng/đã ngừng: s01 (veo-fast, ngừng ngày 05/12), "
                    "s02 (veo-fast, ngừng ngày 05/12)"]
    assert len(env.video_calls()) == 2


@needs_ffmpeg
def test_cli_dry_run_and_keyframes_only_warn_only(env, monkeypatch):
    set_today(monkeypatch, 2026, 12, 6)
    sb = env.storyboard(STORY.replace('mode: t2v\n    prompt: "two"', 'mode: keyframe\n    prompt: "two"'))
    res = cli("run", sb, "--dry-run")
    assert res.exit_code == 0, res.output
    assert "⚠ Model sắp ngừng/đã ngừng: s01 (veo-fast, đã ngừng từ 05/12)" in res.output
    assert "DRY RUN: 2 cảnh sẽ được tạo" in res.output
    assert "KHÔNG GỬI" not in res.output  # refuse is off for dry runs
    assert env.calls() == []

    res = cli("run", sb, "--keyframes-only", "--pause", "0-0")
    assert res.exit_code == 0, res.output
    assert "⚠ Model sắp ngừng/đã ngừng:" in res.output
    assert env.video_calls() == []
    assert any(c[:2] == ["image", "t2i"] for c in env.calls())


@needs_ffmpeg
def test_cli_retry_unknown_adopts_manual_clip_after_retirement(env, monkeypatch, fake_clip_check):
    sb = env.storyboard(STORY + "    model: veo-lite\n")  # s02 is sent on an active model
    put_state(pipe_for(sb), "s01", SUBMITTING, clip_bytes=20_000, submitted_at=manual_clip.stamp_now())
    set_today(monkeypatch, 2026, 12, 6)
    res = cli("run", sb, "--retry-unknown", "--pause", "0-0")
    assert res.exit_code == 0, res.output
    vids = env.video_calls()
    assert len(vids) == 1 and "s02" in " ".join(vids[0])
    st = json.loads((env.root / "output" / "sb" / "state.json").read_text())
    assert st["scenes"]["s01"]["status"] == DONE


@needs_ffmpeg
def test_cli_make_stops_sending_at_midnight(env, monkeypatch):
    set_today(monkeypatch, 2026, 12, 4)
    real = Pipeline._render_scene

    def render_then_midnight(self, *a, **kw):
        try:
            return real(self, *a, **kw)
        finally:
            set_today(monkeypatch, 2026, 12, 5)

    monkeypatch.setattr(Pipeline, "_render_scene", render_then_midnight)
    sb = env.storyboard()
    res = cli("make", sb, "--pause", "0-0")
    assert res.exit_code != 0
    assert len(env.video_calls()) == 1
    assert "[s02] KHÔNG GỬI: model veo-fast đã ngừng từ 05/12" in res.output
    st = json.loads((env.root / "output" / "sb" / "state.json").read_text())
    assert st["scenes"]["s01"]["status"] == DONE
    assert "s02" not in st["scenes"] or st["scenes"]["s02"].get("status") != DONE


def test_refuse_not_used_for_dry_run(env, monkeypatch):
    set_today(monkeypatch, 2026, 12, 6)
    res = cli("run", env.storyboard(), "--dry-run")
    assert res.exit_code == 0, res.output
    assert "KHÔNG GỬI" not in res.output


# --- new storyboards: planner / template / descriptions ----------------------

def _planned(model, scene_models):
    return Storyboard.model_validate({
        "title": "AI", "model": model,
        "scenes": [{"id": f"s0{i + 1}", "mode": "t2v", "prompt": "p", **({"model": m} if m else {})}
                   for i, m in enumerate(scene_models)],
    })


@pytest.mark.parametrize("model", ["veo-fast", None, "veo-lite"])
def test_planner_output_normalized_cli(env, monkeypatch, model):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(planner, "plan",
                        lambda idea, **kw: _planned(model, ["veo-quality", "omni-1.2-flash", None]))
    out = env.root / "planned.yaml"
    res = cli("plan", "ý tưởng", "-o", out, "--backend", "api", "--format", "short_drama")
    assert res.exit_code == 0, res.output
    raw = yaml.safe_load(out.read_text())
    assert raw["model"] == "veo-lite"
    assert "model" not in raw["scenes"][0]
    assert raw["scenes"][1]["model"] == "omni-1.2-flash"
    assert "model" not in raw["scenes"][2]


def test_plan_auto_normalizes_claude_code_backend(env, monkeypatch):
    monkeypatch.setattr(planner, "plan_with_claude_code", lambda idea, **kw: _planned("omni-flash", ["veo-fast"]))
    sb, used = planner.plan_auto("x", backend="claude_code")
    assert used == "claude_code"
    assert sb.model == "veo-lite" and sb.scenes[0].model is None


def test_cli_template_veo_lite(env):
    out = env.root / "t.yaml"
    res = cli("template", out)
    assert res.exit_code == 0, res.output
    assert yaml.safe_load(out.read_text())["model"] == "veo-lite"


def test_no_retiring_defaults_in_cli_owned_files():
    retiring = ("veo-fast", "veo-quality", "omni-flash")
    for rel in ("filmmaker/storyboard.py", "filmmaker/templates/storyboard.yaml"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not any(m in text for m in retiring), rel
    readme_model = [l for l in (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
                    if l.startswith("model:")]
    assert readme_model and all("veo-lite" in l and not any(m in l for m in retiring) for l in readme_model)
    from filmmaker.storyboard import Scene
    desc = Scene.model_fields["model"].description
    assert desc and not any(m in desc for m in retiring)
    assert "veo-lite" in Storyboard.model_fields["model"].description
