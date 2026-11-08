"""Stand-in for the real `gflow` binary used by the tests.

FAKE_GFLOW_LOG: file where each call's argv is appended (JSON lines).
FAKE_GFLOW_FAIL: comma list of "<substring>:<exit_code>:<retryable>[:<times>]";
  a call whose argv contains <substring> fails that many times (default always).
"""
import json, os, subprocess, sys
from pathlib import Path

argv = sys.argv[1:]
log = os.environ.get("FAKE_GFLOW_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps(argv) + "\n")

joined = " ".join(argv)
for rule in filter(None, os.environ.get("FAKE_GFLOW_FAIL", "").split(",")):
    sub, code, retry, *times = rule.split(":")
    if sub in joined:
        counter = Path(log + "." + sub.replace("/", "_") + ".count") if log else None
        n = int(counter.read_text()) if counter and counter.exists() else 0
        if not times or n < int(times[0]):
            if counter:
                counter.write_text(str(n + 1))
            print(json.dumps({"status": "fail", "error": {"title": "fake failure", "detail": f"fake {sub}",
                              "exit_code": int(code), "retryable": retry == "true"}}))
            sys.exit(int(code))

def out_path():
    return Path(argv[argv.index("-o") + 1])

if argv[:2] == ["project", "create"]:
    print(json.dumps({"status": "ok", "project_id": "proj-123", "title": "t"}))
elif argv[:2] == ["image", "t2i"]:
    p = out_path(); p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x36",
                    "-frames:v", "1", str(p)], check=True)
    print("some banner line")
    print(json.dumps({"status": "ok", "images": [{"local_path": str(p)}]}))
elif argv[0] == "video":
    p = out_path(); p.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:d=1:r=24"]
    no_audio = os.environ.get("FAKE_GFLOW_NO_AUDIO")
    if not (no_audio and no_audio in joined):
        cmd += ["-f", "lavfi", "-i", "sine=d=1", "-shortest"]
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)], check=True)
    print(json.dumps({"status": "ok", "media_id": "m-" + p.stem, "local_path": str(p), "succeeded": True}))
elif argv[:2] == ["auth", "status"]:
    print("logged in")
else:
    print(json.dumps({"status": "fail", "error": {"detail": "unknown", "exit_code": 2}})); sys.exit(2)
