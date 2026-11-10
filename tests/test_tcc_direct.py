import sys
from pathlib import Path

import pytest

from filmmaker import gflow


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_framework_stub_runs_inner_python_app(tmp_path: Path):
    ver = tmp_path / "Python.framework" / "Versions" / "3.12"
    stub = ver / "bin" / "python3.12"
    inner = ver / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    for f in (stub, inner):
        f.parent.mkdir(parents=True)
        f.write_text("")
    venv_py = tmp_path / "venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.symlink_to(stub)
    args, env = gflow._tcc_direct([str(venv_py), "script.py", "i2v"])
    assert args == [str(inner.resolve()), "script.py", "i2v"]
    assert env["__PYVENV_LAUNCHER__"] == str(venv_py)


def test_other_python_unchanged(tmp_path: Path):
    py = tmp_path / "python"
    py.write_text("")
    args = [str(py), "x"]
    assert gflow._tcc_direct(args) == (args, None)
