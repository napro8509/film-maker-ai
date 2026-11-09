"""Editor agent: analysis, plan sanitizing, timeline, ffmpeg render, Claude plan, CLI and web."""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from filmmaker import editor
from filmmaker.assemble import probe_duration
from filmmaker.cli import main
from filmmaker.storyboard import Storyboard

from .test_web import client, run  # noqa: F401  (fixture)


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *map(str, args)], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    # s01: 0.5 s of black, then 3 s of picture, with a tone
    ff("-f", "lavfi", "-i", "color=black:s=160x90:d=0.5:r=24", "-f", "lavfi", "-i", "testsrc=s=160x90:d=3:r=24",
       "-f", "lavfi", "-i", "sine=f=440:d=3.5", "-filter_complex", "[0][1]concat=n=2:v=1:a=0[v]",
       "-map", "[v]", "-map", "2:a", "-shortest", d / "a.mp4")
    # s02: quiet tone
    ff("-f", "lavfi", "-i", "testsrc2=s=160x90:d=3:r=24", "-f", "lavfi", "-i", "sine=f=220:d=3",
       "-af", "volume=0.1", "-shortest", d / "b.mp4")
    # s03: still picture, no audio, other size and fps
    ff("-f", "lavfi", "-i", "smptebars=s=90x160:d=3:r=30", d / "c.mp4")
    ff("-f", "lavfi", "-i", "sine=f=600:d=2", d / "m.mp3")
    return d


def board():
    return Storyboard.model_validate({"title": "T", "scenes": [
        {"id": "s01", "prompt": "Lan walks in", "narration": "Một"},
        {"id": "s02", "prompt": "Lan sits", "narration": "Hai"},
        {"id": "s03", "prompt": "Empty room"}]})


def clips_of(media):
    return [("s01", media / "a.mp4"), ("s02", media / "b.mp4"), ("s03", media / "c.mp4")]


def info(sid, duration=4.0, **kw):
    return editor.ClipInfo(id=sid, path=f"/x/{sid}.mp4", duration=duration, has_audio=True, mean_volume=-20, **kw)


def test_analyze_finds_black_start_still_shot_and_loudness(media, tmp_path):
    infos = editor.analyze(clips_of(media), cache=tmp_path / "a.json", log=lambda *_: None)
    a, b, c = infos["s01"], infos["s02"], infos["s03"]
    assert 0.4 <= a.lead_black <= 0.6 and a.tail_black == 0
    assert b.mean_volume < a.mean_volume - 10
    assert not c.has_audio and c.tail_freeze > 2
    # Cached: second call does not run ffmpeg again.
    logs = []
    editor.analyze(clips_of(media), cache=tmp_path / "a.json", log=logs.append)
    assert logs == []


def test_auto_plan_trims_black_keeps_still_shot_and_evens_loudness(media, tmp_path):
    infos = editor.analyze(clips_of(media), cache=tmp_path / "a.json", log=lambda *_: None)
    plan = editor.auto_plan(board(), infos, ["s01", "s02", "s03"])
    c = {x.id: x for x in plan.clips}
    assert 0.4 <= c["s01"].trim_start <= 0.6
    assert c["s03"].trim_end == 0  # a still shot is not a frozen ending
    assert c["s02"].volume > 1 > c["s01"].volume  # quiet clip raised, loud clip lowered
    assert [t.type for t in plan.transitions] == ["crossfade", "crossfade"] and plan.source == "rules"


def test_sanitize_clamps_bad_plans_and_fills_gaps():
    infos = {s: info(s) for s in ("s01", "s02", "s03")}
    bad = editor.EditPlan(
        clips=[editor.ClipEdit(id="s02", trim_start=9, trim_end=9, volume=7), editor.ClipEdit(id="gone")],
        transitions=[editor.Transition(after="s01", type="fadeblack", duration=5)],
        fade_in=10, fade_out=10, even_loudness=False)
    p = editor.sanitize(bad, infos, ["s01", "s02", "s03"])
    assert [c.id for c in p.clips] == ["s01", "s02", "s03"]
    s02 = p.clips[1]
    assert s02.trim_start + s02.trim_end <= 4.0 - editor.MIN_KEEP + 1e-6 and s02.volume == 2.0
    assert [t.after for t in p.transitions] == ["s01", "s02"]
    assert p.transitions[0].duration <= 0.45 * (4.0 - s02.trim_start - s02.trim_end) + 1e-6
    assert p.transitions[1].type == "crossfade"  # missing one filled with the default
    assert p.fade_in <= 3 and p.fade_out <= 4


def test_timeline_and_subtitles_follow_overlaps():
    infos = {s: info(s) for s in ("s01", "s02")}
    plan = editor.EditPlan(clips=[editor.ClipEdit(id="s01", trim_end=1), editor.ClipEdit(id="s02")],
                           transitions=[editor.Transition(after="s01", type="crossfade", duration=1)])
    rows, total = editor.timeline(plan, infos)
    assert rows == [("s01", 0.0, 3.0), ("s02", 2.0, 4.0)] and total == 6.0
    sb = Storyboard.model_validate({"title": "x", "scenes": [{"id": "s01", "prompt": "a", "narration": "A"},
                                                             {"id": "s02", "prompt": "b", "narration": "B"}]})
    subs = editor.subtitle_entries(sb, plan, infos)
    assert subs == [(0.2, 2.5, "A"), (2.5, 5.8, "B")]  # they meet in the middle of the crossfade


def test_cut_uses_concat_not_xfade():
    infos = {s: info(s) for s in ("s01", "s02", "s03")}
    plan = editor.EditPlan(clips=[editor.ClipEdit(id=s) for s in infos],
                           transitions=[editor.Transition(after="s01", type="cut"),
                                        editor.Transition(after="s02", type="fadeblack", duration=0.8)])
    plan = editor.sanitize(plan, infos, list(infos))
    graph, v, a = editor.build_filtergraph(plan, infos, width=320, height=180, music_input=3, music_volume=0.3,
                                           subtitles=None)
    assert graph.count("xfade=") == 1 and "transition=fadeblack" in graph and "offset=7.200" in graph
    assert "concat=n=2:v=1:a=0" in graph and "sidechaincompress" in graph and "loudnorm" in graph


def test_render_real_film_with_music_and_subtitles(media, tmp_path):
    sb = board()
    out, plan = editor.edit_film(sb, clips_of(media), tmp_path, music=media / "m.mp3", burn_subtitles=True,
                                 engine="rules", log=lambda *_: None)
    _, expected = editor.timeline(plan, editor.analyze(clips_of(media), cache=tmp_path / "edit_analysis.json"))
    assert abs(probe_duration(out) - expected) < 0.15
    assert (tmp_path / "edit.json").is_file() and "Một" in (tmp_path / "final.srt").read_text()
    # The saved plan is reused (hand edits survive), unless replan.
    saved = json.loads((tmp_path / "edit.json").read_text())
    saved["transitions"][0]["type"] = "cut"
    saved["source"] = "manual"
    (tmp_path / "edit.json").write_text(json.dumps(saved))
    _, again = editor.edit_film(sb, clips_of(media), tmp_path, engine="rules", log=lambda *_: None)
    assert again.transitions[0].type == "cut" and again.source == "manual"
    _, fresh = editor.edit_film(sb, clips_of(media), tmp_path, engine="rules", replan=True, log=lambda *_: None)
    assert fresh.transitions[0].type == "crossfade"


def fake_claude(tmp_path, plan: dict) -> Path:
    script = tmp_path / "claude"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, sys
        assert "--json-schema" in sys.argv and "film editor" in sys.argv[sys.argv.index("--system-prompt") + 1]
        print(json.dumps({{"is_error": False, "structured_output": {plan!r}}}))
        """))
    script.chmod(0o755)
    return script


def test_ai_plan_through_claude_code(media, tmp_path, monkeypatch):
    ai = {"clips": [{"id": "s01", "trim_start": 0.5, "trim_end": 0.2}, {"id": "s02"}, {"id": "s03"}],
          "transitions": [{"after": "s01", "type": "cut", "reason": "Hành động liên tục"},
                          {"after": "s02", "type": "fadeblack", "duration": 0.9, "reason": "Nhảy thời gian"}],
          "fade_in": 0.5, "fade_out": 1.5, "notes": "Nhịp chậm rãi."}
    monkeypatch.setenv("CLAUDE_BIN", str(fake_claude(tmp_path, ai)))
    plan, _ = editor.make_plan(board(), clips_of(media), tmp_path, engine="ai", log=lambda *_: None)
    assert plan.source == "ai" and [t.type for t in plan.transitions] == ["cut", "fadeblack"]
    assert plan.transitions[1].reason == "Nhảy thời gian" and plan.notes == "Nhịp chậm rãi."


def test_auto_engine_falls_back_to_rules_when_claude_fails(media, tmp_path, monkeypatch):
    bad = tmp_path / "claude"
    bad.write_text(f"#!{sys.executable}\nprint('not json')\n")
    bad.chmod(0o755)
    monkeypatch.setenv("CLAUDE_BIN", str(bad))
    logs = []
    plan, _ = editor.make_plan(board(), clips_of(media), tmp_path, engine="auto", log=logs.append)
    assert plan.source == "rules" and any("luật mặc định" in l for l in logs)
    with pytest.raises(RuntimeError):
        editor.make_plan(board(), clips_of(media), tmp_path, engine="ai", replan=True, log=lambda *_: None)


def test_web_edit_action_and_plan_editing(client):  # noqa: F811
    pid = client.post("/api/projects", json={"title": "Dựng", "source": "template"}).json()["id"]
    assert client.get(f"/api/projects/{pid}/edit-plan").json()["plan"] is None
    run(client, pid, action="video")
    j = run(client, pid, action="edit", engine="rules")
    assert j["status"] == "done", j["lines"]
    assert j["result"]["edit_source"] == "rules" and client.get(f"/api/projects/{pid}").json()["has_final"]
    view = client.get(f"/api/projects/{pid}/edit-plan").json()
    plan = view["plan"]
    assert len(plan["clips"]) == 3 and len(plan["transitions"]) == 2 and view["total"]
    assert {"id": "fadeblack", "label": "Mờ qua đen"} in view["transitions"]

    plan["transitions"][0].update(type="fadeblack", duration=9)
    saved = client.put(f"/api/projects/{pid}/edit-plan", json=plan).json()["plan"]
    assert saved["source"] == "manual" and saved["transitions"][0]["type"] == "fadeblack"
    assert saved["transitions"][0]["duration"] < 1  # clamped to the 1 s test clips
    assert client.put(f"/api/projects/{pid}/edit-plan", json={"transitions": [{"after": "s01", "type": "boom"}]}).status_code == 400

    j = run(client, pid, action="edit")  # renders the hand-edited plan, no AI needed
    assert j["status"] == "done" and j["result"]["edit_source"] == "manual"
    assert client.post(f"/api/projects/{pid}/run", json={"action": "edit", "engine": "x"}).status_code == 400
    assert client.delete(f"/api/projects/{pid}/edit-plan").json()["plan"] is None


def test_cli_edit_plan_only_then_render(media, tmp_path):
    sbf = tmp_path / "film.yaml"
    sbf.write_text("title: T\nscenes:\n" + "".join(f"  - id: {s}\n    prompt: x\n" for s in ("s01", "s02", "s03")))
    out = tmp_path / "output" / "film"
    out.mkdir(parents=True)
    (out / "state.json").write_text(json.dumps({"scenes": {sid: {"status": "done", "video": str(p)}
                                                           for sid, p in clips_of(media)}}))
    r = CliRunner().invoke(main, ["edit", str(sbf), "--engine", "rules", "--plan-only"])
    assert r.exit_code == 0, r.output
    assert (out / "edit.json").is_file() and not (out / "final.mp4").exists()
    r = CliRunner().invoke(main, ["edit", str(sbf), "--engine", "rules"])
    assert r.exit_code == 0, r.output
    assert (out / "final.mp4").is_file() and "Phim đã dựng" in r.output
