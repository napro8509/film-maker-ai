"""F3: "Google · kiểm tra" said signed out when only gflow's own session probe failed."""
import subprocess

from fastapi.testclient import TestClient

from filmmaker import gflow
from filmmaker.web.server import create_app

PROBE_FAIL = ("Probing Flow session...\nCould not verify the Flow session. Check network connectivity and retry; "
              "re-login is only needed if the session is actually dead.")


def client(tmp_path, monkeypatch, credits):
    monkeypatch.setattr(gflow.Gflow, "available", lambda self: True)
    monkeypatch.setattr(gflow.Gflow, "auth_status",
                        lambda self: subprocess.CompletedProcess([], 0, stdout=PROBE_FAIL, stderr=""))

    def run_json(self, *args):
        if credits is None:
            raise gflow.GflowError("not signed in", exit_code=8)
        return {"credits": credits}

    monkeypatch.setattr(gflow.Gflow, "run_json", run_json)
    app = create_app(tmp_path)
    return TestClient(app, headers={"x-token": app.state.token})


def test_probe_error_but_credits_readable_is_signed_in(tmp_path, monkeypatch):
    r = client(tmp_path, monkeypatch, 5790).post("/api/env/auth").json()
    assert r["ok"] is True and "5790" in r["text"]


def test_probe_error_and_no_credits_is_signed_out(tmp_path, monkeypatch):
    assert client(tmp_path, monkeypatch, None).post("/api/env/auth").json()["ok"] is False
