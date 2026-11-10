# Golden output of `filmmaker run` (AC27, flow-progress)

`run_dry.txt` and `run_real.txt` are what `filmmaker run <story> --pause 0 --no-backup` (with and without `--dry-run`)
printed **before** the progress feature changed `filmmaker/gflow.py` (captured 2026-11-09). Each file has the exit
code, stdout, stderr and the argv that `tests/fake_gflow.py` recorded. Machine paths are replaced by `<PY>`
(interpreter), `<TMP>` (temp project dir) and `<REPO>`.

`tests/test_progress_cli.py` reruns the same scenario through `capture.produce()` (with gflow JSON log events and
`@@progress` lines turned on in the fakes) and compares against these files.

Recapture only when the CLI output is meant to change (e.g. another feature changes `filmmaker run`'s messages),
and only with a `gflow.py` that is known to print the same as before:

```bash
cd /Users/mac/Desktop/Projects/film-maker-ai
.venv/bin/python tests/fixtures/cli_golden/capture.py
```

**Out of date (flow-render-fixes, 2026-11-10):** `run_real.txt` still has the old one-line `  $ … i2v --image <img> …`
log of the Flow chat-page call. That line now shows every option and a shortened `--prompt` (see `ui_log_line` in
`filmmaker/gflow.py`). No test reads `run_real.txt` today; recapture with `capture.py` before using it again.
