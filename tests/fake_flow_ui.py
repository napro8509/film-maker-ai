"""Stand-in for filmmaker/flow_ui.py (the Flow chat-page driver) used by the tests.

FAKE_FLOW_UI_LOG: file where each call's argv is appended (JSON lines).
FAKE_FLOW_UI_FAIL: "<exit_code>:<spent true|false>" makes every call fail that way.
"""
import json, os, subprocess, sys
from pathlib import Path

argv = sys.argv[1:]
log = os.environ.get("FAKE_FLOW_UI_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps(argv) + "\n")
print("  đang tải ảnh lên Flow: fake", file=sys.stderr)
fail = os.environ.get("FAKE_FLOW_UI_FAIL")
if fail:
    code, spent = fail.split(":")
    print(json.dumps({"status": "fail", "error": {"exit_code": int(code), "detail": f"fake ui {code}",
                                                  "retryable": False, "spent": spent == "true"}}))
    sys.exit(int(code))
out = Path(argv[argv.index("-o") + 1]); out.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:d=1:r=24",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
media = os.environ.get("FAKE_FLOW_UI_MEDIA") or "m-" + out.stem
print(json.dumps({"status": "ok", "local_path": str(out), "credits": 30, "succeeded": True, "media_id": media}))
