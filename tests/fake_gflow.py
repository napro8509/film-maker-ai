"""Stand-in for the real `gflow` binary used by the tests.

FAKE_GFLOW_LOG: file where each call's argv is appended (JSON lines).
FAKE_GFLOW_FAIL: comma list of "<substring>:<exit_code>:<retryable>[:<times>]";
  a call whose argv contains <substring> fails that many times (default always).
FAKE_GFLOW_MODELS: JSON file printed by `models --json` instead of the default list (6 video, 2 image).
FAKE_GFLOW_CATALOG: JSONL file printed by `data list images` (gflow's local media catalog); not logged.
FAKE_GFLOW_ENV_LOG: file where each call appends {GFLOW_CLI_LOG_LEVEL, GFLOW_CLI_LOG_FORMAT} (JSON lines).
FAKE_GFLOW_EVENTS: "migrated" | "uiauto" | path of a JSONL file: JSON log lines printed on stderr by `video`
  commands (real events mixed with unrelated ones, like gflow-cli at GFLOW_CLI_LOG_LEVEL=INFO).
FAKE_GFLOW_DELAY: seconds; image/video commands sleep 80% of it, print the events, then sleep the other 20%.
FAKE_GFLOW_CREDITS: the balance printed by `credits user` (default 1000).
FAKE_GFLOW_RAW_FAIL: comma list of "<substring>:<exit_code>": info log lines + "boom: real error" on stderr,
  then exit <exit_code> with no JSON payload.
"""
import json, os, subprocess, sys, time
from pathlib import Path

argv = sys.argv[1:]
log = os.environ.get("FAKE_GFLOW_LOG")
if argv[:3] == ["data", "list", "images"]:
    cat = os.environ.get("FAKE_GFLOW_CATALOG")
    if cat and Path(cat).exists():
        print(Path(cat).read_text())
    sys.exit(0)
if log:
    with open(log, "a") as f:
        f.write(json.dumps(argv) + "\n")
env_log = os.environ.get("FAKE_GFLOW_ENV_LOG")
if env_log:
    with open(env_log, "a") as f:
        f.write(json.dumps({k: os.environ.get(k) for k in ("GFLOW_CLI_LOG_LEVEL", "GFLOW_CLI_LOG_FORMAT")}) + "\n")

joined = " ".join(argv)
if argv[:2] == ["credits", "user"]:  # FAKE_GFLOW_CREDITS: the balance Flow reports (default 1000)
    print(json.dumps({"status": "ok", "authenticated": True, "credits": int(os.environ.get("FAKE_GFLOW_CREDITS", "1000"))}))
    sys.exit(0)


def info(event, **kw):
    print(json.dumps({"event": event, "level": "info", "logger": "gflow_cli", **kw}), file=sys.stderr, flush=True)


for rule in filter(None, os.environ.get("FAKE_GFLOW_RAW_FAIL", "").split(",")):
    sub, code = rule.rsplit(":", 1)
    if sub in joined:
        info("cli.start", argv=argv[:2])
        info("auth.session_loaded")
        print("boom: real error", file=sys.stderr, flush=True)
        info("cli.exit", code=int(code))
        sys.exit(int(code))

_OK = "MEDIA_GENERATION_STATUS_SUCCESSFUL"
EVENT_PRESETS = {
    "migrated": [
        {"event": "cli.start"}, {"event": "migrated.submit_clicked"}, {"event": "auth.cookie_refresh"},
        {"event": "migrated.submit_observed"},
        {"event": "migrated.status", "status": "MEDIA_GENERATION_STATUS_ACTIVE"},
        {"event": "migrated.status", "status": _OK}, {"event": "migrated.download", "path": "x.mp4"},
        {"event": "cli.exit"},
    ],
    "uiauto": [
        {"event": "cli.start"}, {"event": "ui_automation_video.generate_captured"},
        {"event": "ui_automation_video.poll_terminal", "status": "MEDIA_GENERATION_STATUS_ACTIVE"},
        {"event": "ui_automation_video.poll_terminal", "status": _OK},
        {"event": "ui_automation_video.video_saved", "path": "x.mp4"}, {"event": "cli.exit"},
    ],
}


def emit_events():
    """Print the progress events (video only), sleeping FAKE_GFLOW_DELAY around them."""
    delay = float(os.environ.get("FAKE_GFLOW_DELAY") or 0)
    time.sleep(delay * 0.8)
    spec = os.environ.get("FAKE_GFLOW_EVENTS")
    if spec and argv[0] == "video":
        if spec in EVENT_PRESETS:
            events = EVENT_PRESETS[spec]
        else:
            events = [json.loads(l) for l in Path(spec).read_text().splitlines() if l.strip()]
        for ev in events:
            ev = dict(ev)
            info(ev.pop("event"), **ev)
    time.sleep(delay * 0.2)

for rule in filter(None, os.environ.get("FAKE_GFLOW_FAIL", "").split(",")):
    sub, code, retry, *times = rule.split(":")
    if sub in joined:
        counter = Path(log + "." + sub.replace("/", "_") + ".count") if log else None
        n = int(counter.read_text()) if counter and counter.exists() else 0
        if not times or n < int(times[0]):
            if counter:
                counter.write_text(str(n + 1))
            print(json.dumps({"status": "fail", "error": {"title": "fake failure", "detail": f"fake {sub}",
                              "exit_code": int(code), "retryable": retry == "true",
                              "remediation_hint": os.environ.get("FAKE_GFLOW_HINT")}}))
            sys.exit(int(code))

def out_path():
    return Path(argv[argv.index("-o") + 1])

if argv[:2] == ["project", "create"]:
    print(json.dumps({"status": "ok", "project_id": "proj-123", "title": "t"}))
elif argv[:2] in (["image", "t2i"], ["image", "i2i"]):
    p = out_path(); p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x36",
                    "-frames:v", "1", str(p)], check=True)
    emit_events()
    print("some banner line")
    print(json.dumps({"status": "ok", "images": [{"local_path": str(p)}]}))
elif argv[0] == "video":
    p = out_path(); p.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:d=1:r=24"]
    no_audio = os.environ.get("FAKE_GFLOW_NO_AUDIO")
    if not (no_audio and no_audio in joined):
        cmd += ["-f", "lavfi", "-i", "sine=d=1", "-shortest"]
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)], check=True)
    emit_events()
    print(json.dumps({"status": "ok", "media_id": "m-" + p.stem, "local_path": str(p), "succeeded": True}))
elif argv[:2] == ["auth", "status"]:
    print("logged in")
elif argv[0] == "models":
    models_file = os.environ.get("FAKE_GFLOW_MODELS")
    if models_file:
        print(Path(models_file).read_text())
    else:
        video = [("veo-fast", 8), ("veo-quality", 8), ("veo-lite", 8), ("veo-lite-lp", 8),
                 ("omni-flash", 10), ("omni-1.2-flash", 15)]
        print(json.dumps({"video": {"models": [{"aliases": [a], "max_duration": d} for a, d in video]},
                          "image": {"models": [{"aliases": ["nano2"], "label": "Nano Banana 2 (default)"},
                                               {"aliases": ["nano-pro"], "label": "Nano Banana Pro"}]}}))
else:
    print(json.dumps({"status": "fail", "error": {"detail": "unknown", "exit_code": 2}})); sys.exit(2)
