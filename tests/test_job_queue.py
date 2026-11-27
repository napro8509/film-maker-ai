"""Hàng đợi: bấm việc mới khi đang có việc chạy thì xếp hàng; hàng đợi còn sau khi tắt/mở lại app."""
import threading
import time

import pytest
from fastapi import HTTPException

from filmmaker.web.server import Jobs


def wait(cond, seconds=5.0):
    end = time.time() + seconds
    while not cond():
        assert time.time() < end, "hết giờ chờ"
        time.sleep(0.01)


def blocked():
    gate = threading.Event()

    def fn(job):
        gate.wait(5)
        return {}

    return gate, fn


def test_queued_jobs_run_in_order_after_the_current_one():
    jobs, order = Jobs(), []
    gate, fn = blocked()
    first = jobs.start("auto", "A", None, fn, queue=True)
    second = jobs.start("auto", "B", None, lambda j: order.append("B") or {}, queue=True)
    third = jobs.start("auto", "C", None, lambda j: order.append("C") or {}, queue=True)
    assert first.status == "running" and second.status == third.status == "queued"
    assert [q["title"] for q in jobs.queued()] == ["B", "C"]
    gate.set()
    wait(lambda: third.status == "done")
    assert order == ["B", "C"] and jobs.current is third and jobs.queued() == []


def test_a_failed_job_does_not_stall_the_queue():
    jobs = Jobs()
    gate, _ = blocked()

    def boom(job):
        gate.wait(5)
        raise RuntimeError("Flow lỗi")

    first = jobs.start("auto", "A", None, boom, queue=True)
    second = jobs.start("auto", "B", None, lambda j: {}, queue=True)
    gate.set()
    wait(lambda: second.status == "done")
    assert first.status == "failed"


def test_every_kind_of_job_queues_and_the_same_job_is_not_queued_twice():
    """F14/F29: a render, a QC or a second step of the same film waits its turn instead of being refused."""
    jobs = Jobs()
    gate, fn = blocked()
    jobs.start("qc", "QC lại", "p1", fn)
    assert jobs.start("video", "Render", "p2", fn).status == "queued"
    assert jobs.start("auto", "Tạo tự động", "p1", fn).status == "queued"  # same film, other job: queued
    with pytest.raises(HTTPException) as exc:
        jobs.start("auto", "again", "p1", fn)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException):
        jobs.start("video", "busy", "p3", fn, queue=False)
    gate.set()


def test_running_and_queued_jobs_are_written_down_and_forgotten_when_done(tmp_path):
    from filmmaker.web.server import _REQUEST

    state = tmp_path / ".job-queue.json"
    jobs = Jobs(state)
    gate, fn = blocked()
    _REQUEST.set({"method": "POST", "path": "/api/projects/a/autopilot", "body": {"review": True}})
    first = jobs.start("auto", "A", "a", fn)
    _REQUEST.set({"method": "POST", "path": "/api/projects/b/run", "body": {"action": "video"}})
    jobs.start("video", "B", "b", lambda j: {})
    _REQUEST.set({"method": "POST", "path": "/api/projects/c/publish", "body": {}})
    jobs.start("publish", "C", "c", lambda j: {})  # never repeated by itself
    saved = jobs.saved()
    assert [d["path"] for d in saved] == ["/api/projects/a/autopilot", "/api/projects/b/run"]
    gate.set()
    wait(lambda: jobs.current and jobs.current.title == "C" and jobs.current.status == "done")
    assert not state.exists()
    _REQUEST.set(None)
    assert first.status == "done"


def test_a_forced_paid_render_is_not_sent_again_once_it_started(tmp_path):
    from filmmaker.web.server import _REQUEST

    jobs = Jobs(tmp_path / ".job-queue.json")
    gate, fn = blocked()
    _REQUEST.set({"method": "POST", "path": "/api/projects/a/run", "body": {"action": "video", "force": ["s01"]}})
    jobs.start("video", "Render lại s01", "a", fn)
    _REQUEST.set(None)
    assert jobs.saved() == []
    gate.set()


def test_the_app_starts_saved_jobs_again(tmp_path):
    """F31: quitting or restarting the app no longer loses the running job and the queue."""
    import json

    from filmmaker.web.server import create_app

    (tmp_path / "film").mkdir()
    (tmp_path / "film" / "storyboard.yaml").write_text(
        "title: Phim\nscenes:\n- id: s01\n  mode: t2v\n  prompt: a cat walks on a quiet street\n")
    (tmp_path / ".job-queue.json").write_text(json.dumps([
        {"method": "POST", "path": "/api/projects/film/run", "body": {"action": "dry"}, "title": "Xem trước", "kind": "dry"}]))
    app = create_app(tmp_path)
    jobs = app.state.jobs
    wait(lambda: any(j.project == "film" for j in jobs.jobs.values()), 20)
    job = next(j for j in jobs.jobs.values() if j.project == "film")
    wait(lambda: any("Khôi phục" in l for l in job.lines), 20)
    wait(lambda: job.status != "running", 30)


def test_cancel_takes_a_waiting_job_out():
    jobs, ran = Jobs(), []
    gate, fn = blocked()
    first = jobs.start("auto", "A", None, fn, queue=True)
    second = jobs.start("auto", "B", None, lambda j: ran.append(1) or {}, queue=True)
    assert jobs.cancel(second.id) and second.status == "stopped"
    assert not jobs.cancel(first.id)  # running: stopped the normal way
    gate.set()
    wait(lambda: first.status == "done")
    assert ran == [] and jobs.current is first
