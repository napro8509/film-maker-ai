"""Back up film projects to Google Drive (or any cloud rclone supports) and restore them.

A backup only adds or updates files on the remote (`rclone copy`), it never deletes
anything there. A file that changed since the last backup (a re-rendered clip, a new
state.json) is first moved to <remote>/.versions/<name>/<time>/, so older renders survive.

What is backed up:
- a UI project (folder with storyboard.yaml, e.g. ~/FilmMaker/<name>/): the whole folder;
- any other storyboard: the storyboard file, the files it uses (character images,
  i2v frames) and its output folder.

state.json stores absolute paths, so `restore` rewrites them to the new location;
otherwise the tool would think the clips are missing and render (pay for) them again.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

MANIFEST = ".filmmaker-backup.json"  # written to <remote>/<name>/ after every backup
LOCAL_RECORD = "backup.json"  # in the output folder: when/where this project was last backed up
VERSIONS = ".versions"
SKIP_NAMES = {".DS_Store", MANIFEST, LOCAL_RECORD}
SKIP_SUFFIXES = {".tmp", ".part", ".partial"}
INSTALL_HINT = "cài rclone: brew install rclone, rồi chạy: rclone config (tạo remote tên gdrive, kiểu Google Drive)"


class BackupError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _join(remote: str, *parts: str) -> str:
    """rclone path join: 'gdrive:' + 'a' -> 'gdrive:a'; 'gdrive:Film' + 'a' -> 'gdrive:Film/a'."""
    base = remote if remote.endswith((":", "/")) else remote + "/"
    return base + "/".join(p.strip("/") for p in parts)


def check_remote(remote: str) -> str:
    remote = (remote or "").strip()
    if not remote:
        raise BackupError("chưa chọn nơi backup (ví dụ gdrive:FilmMaker)")
    if remote.startswith("-") or "\n" in remote:
        raise BackupError(f"nơi backup không hợp lệ: {remote!r}")
    return remote


def check_name(name: str) -> str:
    if not name or name.startswith(".") or "/" in name or "\\" in name or name in ("..",):
        raise BackupError(f"tên bản backup không hợp lệ: {name!r}")
    return name


@dataclass
class ProjectFiles:
    name: str  # folder name on the remote
    root: Path  # local folder the relative paths start from
    files: list[str]  # posix paths relative to root
    storyboard: str  # relative to root
    out_dir: str  # relative to root
    whole_folder: bool
    skipped: list[str]  # referenced files outside root (not backed up)


def _wanted(p: Path) -> bool:
    return p.is_file() and not p.is_symlink() and p.name not in SKIP_NAMES and p.suffix not in SKIP_SUFFIXES


def collect(storyboard: Path, out_dir: Path) -> ProjectFiles:
    storyboard = storyboard.resolve()
    out_dir = out_dir.resolve()
    root = storyboard.parent
    if root != out_dir and root not in out_dir.parents:
        raise BackupError(f"thư mục kết quả {out_dir} phải nằm trong {root} mới backup được")
    whole = storyboard.name == "storyboard.yaml"  # the UI's layout: one folder per film
    name = root.name if whole else storyboard.stem
    found: set[Path] = set()
    skipped: list[str] = []
    if whole:
        for p in root.rglob("*"):
            rel = p.relative_to(root)
            if any(part.startswith(".") for part in rel.parts[:-1]):  # .trash, .git ...
                continue
            if _wanted(p):
                found.add(p)
    else:
        found.add(storyboard)
        if out_dir.is_dir():
            found.update(p for p in out_dir.rglob("*") if _wanted(p))
        try:
            from .storyboard import load_storyboard
            sb = load_storyboard(storyboard)
            refs = [c.image for c in sb.characters] + [x for s in sb.scenes for x in (s.image, s.end_image)]
        except Exception:  # a broken storyboard can still be backed up as a file
            refs = []
        for ref in filter(None, refs):
            p = Path(ref).resolve()
            if not p.is_file():
                continue
            if root in p.parents:
                found.add(p)
            else:
                skipped.append(str(p))
    files = sorted(p.relative_to(root).as_posix() for p in found)
    return ProjectFiles(name=check_name(name), root=root, files=files,
                        storyboard=storyboard.relative_to(root).as_posix(),
                        out_dir=out_dir.relative_to(root).as_posix() if out_dir != root else ".",
                        whole_folder=whole, skipped=skipped)


def last_backup(out_dir: Path) -> dict[str, Any] | None:
    try:
        return json.loads((out_dir / LOCAL_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def rewrite_paths(data: Any, old_root: str, new_root: str) -> Any:
    """Point absolute paths saved under old_root at new_root (recursively)."""
    old = old_root.rstrip("/") + "/"
    if isinstance(data, str):
        return new_root.rstrip("/") + "/" + data[len(old):] if data.startswith(old) else data
    if isinstance(data, dict):
        return {k: rewrite_paths(v, old_root, new_root) for k, v in data.items()}
    if isinstance(data, list):
        return [rewrite_paths(v, old_root, new_root) for v in data]
    return data


class Rclone:
    """Thin wrapper around the rclone binary (RCLONE_BIN overrides it, e.g. in tests)."""

    def __init__(self, remote: str, binary: str | None = None, log: Callable[[str], None] = print):
        self.remote = remote
        self.binary = binary or os.environ.get("RCLONE_BIN", "rclone")
        self.log = log

    def _cmd(self) -> list[str]:
        return shlex.split(self.binary)

    def available(self) -> bool:
        return bool(shutil.which(self._cmd()[0]))

    def _run(self, *args: str, stream: bool = False) -> str:
        """Run rclone; with stream=True, log each uploaded/downloaded file as it happens."""
        if not self.available():
            raise BackupError(f"không thấy lệnh rclone; {INSTALL_HINT}")
        cmd = [*self._cmd(), *args]
        if not stream:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise BackupError(self._explain(res.stderr))
            return res.stdout
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        tail: list[str] = []
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip()
            m = re.search(r"(?:INFO|NOTICE)\s*:\s*(.+?): Copied \(([^)]*)\)", line)
            if m:
                self.log(f"  ☁ {m.group(1)}" + (" (bản cũ giữ ở .versions)" if "replaced" in m.group(2) else ""))
            elif "ERROR" in line:
                self.log(f"  ! {line}")
            tail = (tail + [line])[-20:]
        if proc.wait() != 0:
            raise BackupError(self._explain("\n".join(tail)))
        return ""

    def _explain(self, stderr: str) -> str:
        err = stderr.strip()
        remote_name = self.remote.split(":", 1)[0] if ":" in self.remote else ""
        if "didn't find section in config file" in err or "not found in config file" in err:
            return (f"rclone chưa có remote “{remote_name}”. Chạy: rclone config → n (new remote) → tên {remote_name} "
                    "→ chọn Google Drive → đăng nhập Google trong trình duyệt.")
        if "invalid_grant" in err or "token expired" in err.lower() or "couldn't fetch token" in err:
            return f"phiên đăng nhập Google Drive đã hết hạn. Chạy: rclone config reconnect {remote_name}:"
        if "directory not found" in err:
            return "không thấy bản backup này trên Drive"
        lines = [l for l in err.splitlines() if l.strip()]
        return "rclone lỗi: " + (" | ".join(lines[-3:]) or "không rõ lý do")

    def version(self) -> str | None:
        try:
            out = self._run("version")
        except (BackupError, OSError):
            return None
        return out.splitlines()[0].strip() if out else None

    def remotes(self) -> list[str]:
        return [l.strip() for l in self._run("listremotes").splitlines() if l.strip()]

    # --- backup / restore ----------------------------------------------------

    def backup(self, storyboard: Path, out_dir: Path) -> dict[str, Any]:
        remote = check_remote(self.remote)
        pf = collect(storyboard, out_dir)
        for s in pf.skipped:
            self.log(f"  ! bỏ qua (nằm ngoài thư mục dự án): {s}")
        dest = _join(remote, pf.name)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        size = sum((pf.root / f).stat().st_size for f in pf.files)
        self.log(f"☁ Backup {len(pf.files)} file ({size / 1e6:.1f} MB) lên {dest} — chỉ gửi file mới hoặc đã đổi")
        with tempfile.TemporaryDirectory() as tmp:
            listing = Path(tmp) / "files.txt"
            listing.write_text("\n".join(pf.files) + "\n", encoding="utf-8")
            self._run("copy", str(pf.root), dest, "--files-from-raw", str(listing),
                      "--backup-dir", _join(remote, VERSIONS, pf.name, stamp), "-v", "--stats", "0", stream=True)
            manifest = {
                "format": 1, "name": pf.name, "at": _now(), "root": str(pf.root), "storyboard": pf.storyboard,
                "out_dir": pf.out_dir, "whole_folder": pf.whole_folder, "files": len(pf.files), "bytes": size,
            }
            mpath = Path(tmp) / MANIFEST
            mpath.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
            self._run("copyto", str(mpath), _join(dest, MANIFEST))  # written last: marks a complete backup
        record = {"at": manifest["at"], "remote": dest, "files": len(pf.files), "bytes": size}
        out = pf.root / pf.out_dir
        out.mkdir(parents=True, exist_ok=True)
        (out / LOCAL_RECORD).write_text(json.dumps(record, indent=2), encoding="utf-8")
        self.log(f"☁ Đã backup xong: {dest}")
        return record

    def list_backups(self) -> list[str]:
        remote = check_remote(self.remote)
        try:
            out = self._run("lsf", remote, "--dirs-only")
        except BackupError as exc:
            if "không thấy bản backup" in str(exc):  # the FilmMaker folder does not exist yet
                return []
            raise
        return sorted(n.rstrip("/") for n in out.splitlines() if n.strip() and not n.startswith("."))

    def manifest(self, name: str) -> dict[str, Any]:
        src = _join(check_remote(self.remote), check_name(name))
        try:
            data = json.loads(self._run("cat", _join(src, MANIFEST)))
        except ValueError as exc:
            raise BackupError(f"bản backup {name} không đọc được ({MANIFEST} hỏng)") from exc
        if not data:
            raise BackupError(f"không thấy bản backup {name} trên {self.remote}")
        return data

    def restore(self, name: str, target: Path, *, overwrite: bool = False) -> dict[str, Any]:
        """Download a backup into `target` and fix the paths in state.json. Returns the manifest."""
        m = self.manifest(name)
        storyboard = target / m["storyboard"]
        if storyboard.exists() and not overwrite:
            raise BackupError(f"{storyboard} đã có trên máy; chọn thư mục khác hoặc cho phép ghi đè")
        src = _join(check_remote(self.remote), check_name(name))
        target.mkdir(parents=True, exist_ok=True)
        self.log(f"☁ Tải {m.get('files', '?')} file từ {src} về {target}")
        self._run("copy", src, str(target), "--exclude", f"/{MANIFEST}", "-v", "--stats", "0", stream=True)
        out_dir = (target / m.get("out_dir", ".")).resolve()
        state = out_dir / "state.json"
        if state.is_file() and m.get("root"):
            data = json.loads(state.read_text(encoding="utf-8"))
            state.write_text(json.dumps(rewrite_paths(data, m["root"], str(target.resolve())), indent=2,
                                        ensure_ascii=False), encoding="utf-8")
        out_dir.mkdir(parents=True, exist_ok=True)  # a project backed up before its first render has no output yet
        (out_dir / LOCAL_RECORD).write_text(json.dumps(
            {"at": m.get("at"), "remote": src, "files": m.get("files"), "bytes": m.get("bytes"), "restored": _now()},
            indent=2), encoding="utf-8")
        self.log(f"☁ Đã khôi phục: {storyboard}")
        return m


class AutoBackup:
    """Back up in a background thread while rendering. Requests that arrive while a
    backup runs are merged into one more run; a failed backup only logs a warning."""

    def __init__(self, run: Callable[[], Any], log: Callable[[str], None] = print):
        self._run = run
        self.log = log
        self._lock = threading.Lock()
        self._pending = False
        self._thread: threading.Thread | None = None
        self.errors = 0

    def trigger(self) -> None:
        with self._lock:
            self._pending = True
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, daemon=True)
                self._thread.start()

    def _loop(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._thread = None
                    return
                self._pending = False
            try:
                self._run()
            except Exception as exc:  # never break a render because the backup failed
                self.errors += 1
                self.log(f"⚠ Backup lỗi (video vẫn an toàn trên máy): {exc}")

    def finish(self) -> None:
        """One last backup, then wait for it."""
        self.trigger()
        with self._lock:
            t = self._thread
        if t:
            t.join()
