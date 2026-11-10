"""Golden output of `filmmaker run` for AC27 (flow-progress): the CLI prints exactly what it printed before the
progress feature (no `@@progress`, no gflow JSON log lines).

`produce(tmp, dry)` runs the CLI in a subprocess against tests/fake_gflow.py and tests/fake_flow_ui.py and returns
stdout + stderr + exit code with machine paths replaced. tests/test_progress_cli.py imports it; running this file
rewrites run_dry.txt and run_real.txt (see README.md in this folder: only do that with the gflow.py from before
the change).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parents[1]
REPO = TESTS.parent

STORY = """\
title: Golden film
aspect: "16:9"
model: veo-lite
style: "cinematic"
characters:
  - name: Lan
    description: "short black hair, red scarf"
scenes:
  - id: s01
    mode: keyframe
    image_prompt: "Lan at a cafe"
    prompt: "slow push-in"
    narration: "Xin chào"
  - id: s02
    mode: t2v
    prompt: "Lan walks in the rain"
  - id: s03
    mode: i2v
    image: start.png
    prompt: "pan left"
  - id: s04
    mode: t2v
    prompt: "the street at night"
"""


def produce(tmp: Path, dry: bool, extra_env: dict[str, str] | None = None) -> str:
    tmp = Path(tmp)
    (tmp / "start.png").write_bytes(b"png")
    sb = tmp / "story.yaml"
    sb.write_text(STORY, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FAKE_GFLOW", "FAKE_FLOW_UI", "GFLOW_CLI_"))}
    env.update({
        "FILMMAKER_SETTINGS": str(tmp / "cfg" / "settings.json"),
        "GFLOW_BIN": f"{sys.executable} {TESTS / 'fake_gflow.py'}",
        "FAKE_GFLOW_LOG": str(tmp / "calls.log"),
        # i2v is refused by gflow -> Flow chat driver; s04 fails with a plain gflow error
        "FAKE_GFLOW_FAIL": "video i2v:36:false,s04.mp4:1:false",
        "FLOW_UI_CMD": f"{sys.executable} {TESTS / 'fake_flow_ui.py'}",
        "PYTHONIOENCODING": "utf-8",
    })
    env.pop("FILMMAKER_MODELS", None)
    env.update(extra_env or {})
    args = [sys.executable, "-c", "from filmmaker.cli import main; main()", "run", str(sb), "--pause", "0",
            "--no-backup"] + (["--dry-run"] if dry else [])
    proc = subprocess.run(args, cwd=tmp, env=env, capture_output=True, text=True, timeout=300)
    calls = (tmp / "calls.log").read_text(encoding="utf-8") if (tmp / "calls.log").exists() else ""
    text = f"== exit {proc.returncode}\n== stdout\n{proc.stdout}== stderr\n{proc.stderr}== gflow argv\n{calls}"
    return normalize(text, tmp)


def normalize(text: str, tmp: Path) -> str:
    subs = [(sys.executable, "<PY>"), (str(Path(tmp).resolve()), "<TMP>"), (str(tmp), "<TMP>"),
            (str(REPO), "<REPO>")]
    for a, b in subs:
        text = text.replace(a, b)
    return text


if __name__ == "__main__":
    for name, dry in (("run_dry.txt", True), ("run_real.txt", False)):
        with tempfile.TemporaryDirectory() as d:
            (HERE / name).write_text(produce(Path(d), dry), encoding="utf-8")
        print("wrote", HERE / name)
