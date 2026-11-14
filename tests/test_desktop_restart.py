"""Cmd+R in the macOS app: Bridge.restart() starts a helper that reopens the app once this process exits."""

import os
import subprocess
import sys
import time

from filmmaker import desktop


class _Win:
    destroyed = False

    def destroy(self):
        self.destroyed = True


def test_relaunch_command_outside_a_bundle_reruns_python():
    cmd = desktop.relaunch_command()
    assert cmd[0] in (sys.executable, "/usr/bin/open")
    if cmd[0] == "/usr/bin/open":
        assert cmd[1].endswith(".app") and not cmd[1].endswith("Python.app")


def test_restart_spawns_helper_and_closes_window(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "relaunch_command", lambda: ["/usr/bin/true", "x"])
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)))
    started = []
    monkeypatch.setattr(desktop.threading, "Thread", lambda target, daemon: type("T", (), {"start": lambda self: started.append(target)})())
    exits = []
    monkeypatch.setattr(desktop.os, "_exit", exits.append)
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    b = desktop.Bridge("http://127.0.0.1:1/")
    b.window = _Win()
    assert b.restart() is True
    assert b.restarting and not b.window.destroyed  # closed later, off the JS call
    started[0]()
    assert b.window.destroyed and exits == [0]
    args, kw = calls[0]
    assert args[0] == "/bin/sh" and args[3] == str(os.getpid()) and args[4:] == ["/usr/bin/true", "x"]
    assert kw["start_new_session"] is True


def test_helper_waits_for_the_old_process(tmp_path):
    """The shell snippet only runs the command after the watched pid is gone."""
    marker = tmp_path / "started"
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"])
    helper = subprocess.Popen(["/bin/sh", "-c", desktop.WAIT_THEN_RUN, str(old.pid), "/usr/bin/touch", str(marker)])
    time.sleep(0.4)
    assert not marker.exists()
    old.wait()
    helper.wait(timeout=5)
    assert marker.exists()
