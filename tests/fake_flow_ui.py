"""Stand-in for filmmaker/flow_ui.py (the Flow chat-page driver) used by the tests.

FAKE_FLOW_UI_LOG: file where each call's argv is appended (JSON lines).
FAKE_FLOW_UI_FAIL: "<exit_code>:<spent true|false>" makes every call fail that way.
FAKE_FLOW_UI_PROGRESS: comma list of tokens printed as "@@progress <tok>" on stderr (before FAKE_FLOW_UI_FAIL),
  kept verbatim so invalid values ("abc", "150") can be tested.
FAKE_FLOW_UI_DELAY: seconds slept before each "@@progress" token.
"""
import json, os, subprocess, sys, time
from pathlib import Path

argv = sys.argv[1:]
log = os.environ.get("FAKE_FLOW_UI_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps(argv) + "\n")
print("  đang tải ảnh lên Flow: fake", file=sys.stderr)
for tok in filter(None, os.environ.get("FAKE_FLOW_UI_PROGRESS", "").split(",")):
    time.sleep(float(os.environ.get("FAKE_FLOW_UI_DELAY") or 0))
    print(f"@@progress {tok}", file=sys.stderr, flush=True)
fail = os.environ.get("FAKE_FLOW_UI_FAIL")
if fail:
    code, spent = fail.split(":")
    print(json.dumps({"status": "fail", "error": {"exit_code": int(code), "detail": f"fake ui {code}",
                                                  "retryable": False, "spent": spent == "true"}}))
    sys.exit(int(code))
out = Path(argv[argv.index("-o") + 1]); out.parent.mkdir(parents=True, exist_ok=True)
if argv[0] in ("i2i", "char"):  # one image
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=green:s=64x36",
                    "-frames:v", "1", str(out)], check=True)
else:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:d=1:r=24",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
media = os.environ.get("FAKE_FLOW_UI_MEDIA") or "m-" + out.stem
if argv[0] == "char":  # a Flow character: its entity id and the name it was given
    name = argv[argv.index("--name") + 1]
    print(json.dumps({"status": "ok", "local_path": str(out), "entity_id": "ent-" + out.stem, "name": name,
                      "media_id": media}))
    sys.exit(0)
print(json.dumps({"status": "ok", "local_path": str(out), "credits": 30, "succeeded": True, "media_id": media}))
