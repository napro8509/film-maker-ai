"""CapCut export: the edit plan as a CapCut project (timeline, transitions, audio, subtitles), CLI and web."""
import json

import pytest
from click.testing import CliRunner

from filmmaker import capcut, editor
from filmmaker.assemble import AssembleError
from filmmaker.cli import main

from .test_editor import board, clips_of, info, media  # noqa: F401  (fixture)
from .test_web import client, run  # noqa: F401  (fixture)

US = capcut.US


def plan_of(order, infos, **kw):
    return editor.sanitize(editor.EditPlan(even_loudness=False, **kw), infos, order)


def doc_of(plan, infos, **kw):
    media_ = {sid: capcut.Media(f"/m/{sid}.mp4", i.duration, 1080, 1920, i.has_audio) for sid, i in infos.items()}
    return capcut.build_draft(board(), plan, infos, media_, name="T", **kw)


def tracks(doc, kind):
    return [t for t in doc["tracks"] if t["type"] == kind]


def test_catalog_ids_are_unique_and_every_basic_transition_maps():
    ids = [t.effect_id for t in capcut.CAPCUT_TRANSITIONS.values()]
    assert len(ids) == len(set(ids)) and all(i.isdigit() for i in ids)
    assert set(capcut.FROM_FFMPEG) == set(editor.TRANSITIONS)
    assert all(v is None or v in capcut.CAPCUT_TRANSITIONS for v in capcut.FROM_FFMPEG.values())
    assert {"id": "memory", "label": "Hồi ức"} in capcut.transition_catalog()
    assert '"memory"' in capcut.agent_guide()


def test_clips_sit_back_to_back_with_trims_volume_and_transitions():
    infos = {s: info(s, 5.0) for s in ("s01", "s02", "s03")}
    plan = plan_of(["s01", "s02", "s03"], infos,
                   clips=[editor.ClipEdit(id="s01", trim_start=0.5, trim_end=0.5, volume=0.8)],
                   transitions=[editor.Transition(after="s01", type="crossfade", duration=0.6, capcut="memory"),
                                editor.Transition(after="s02", type="cut")])
    doc = doc_of(plan, infos)
    segs = tracks(doc, "video")[0]["segments"]
    assert [s["target_timerange"] for s in segs] == [
        {"start": 0, "duration": 4 * US}, {"start": 4 * US, "duration": 5 * US}, {"start": 9 * US, "duration": 5 * US}]
    assert segs[0]["source_timerange"] == {"start": US // 2, "duration": 4 * US} and segs[0]["volume"] == 0.8
    assert doc["duration"] == 14 * US  # a CapCut transition does not shorten the film
    assert doc["canvas_config"]["width"] == 1920 and doc["canvas_config"]["height"] == 1080

    mats = doc["materials"]
    assert [(t["name"], t["duration"]) for t in mats["transitions"]] == [("Memory", 600_000)]  # the cut has none
    assert mats["transitions"][0]["id"] in segs[0]["extra_material_refs"]
    assert len(mats["videos"]) == 3 and mats["videos"][0]["path"] == "/m/s01.mp4"
    # every reference of every segment points at a material that exists
    known = {m["id"] for group in mats.values() for m in group}
    for t in doc["tracks"]:
        for s in t["segments"]:
            assert s["material_id"] in known and set(s["extra_material_refs"]) <= known


def test_plan_without_capcut_choice_uses_the_closest_transition_and_sanitize_keeps_valid_ids():
    infos = {s: info(s) for s in ("s01", "s02", "s03")}
    plan = plan_of(["s01", "s02", "s03"], infos, transitions=[
        editor.Transition(after="s01", type="fadeblack", duration=0.8, capcut="nope"),
        editor.Transition(after="s02", type="cut", capcut="memory")])
    assert [t.capcut for t in plan.transitions] == [None, None]  # unknown id dropped; a cut has no effect
    assert [capcut.transition_for(t) for t in plan.transitions] == ["fade_black", None]
    assert [t["name"] for t in doc_of(plan, infos)["materials"]["transitions"]] == ["Black Fade"]
    kept = plan_of(["s01", "s02"], infos, transitions=[editor.Transition(after="s01", capcut="glitch")])
    assert kept.transitions[0].capcut == "glitch"


def test_fades_music_loop_and_subtitles():
    infos = {s: info(s, 4.0) for s in ("s01", "s02", "s03")}
    plan = plan_of(["s01", "s02", "s03"], infos, fade_in=0.5, fade_out=1.0,
                   transitions=[editor.Transition(after="s01", type="crossfade", duration=1.0)])
    doc = doc_of(plan, infos, music=capcut.Media("/m/music.mp3", 5.0), music_volume=0.3)
    mats = doc["materials"]
    anims = [a for m in mats["material_animations"] for a in m["animations"]]
    assert [(a["type"], a["start"], a["duration"]) for a in anims] == [
        ("in", 0, 500_000), ("out", 3 * US + 250_000, US)]  # s02 -> s03 is the default 0.5 s crossfade
    # A transition into the last clip: CapCut counts the fade from half a transition before the cut.
    led = plan_of(["s01", "s02", "s03"], infos, fade_out=1.0,
                  transitions=[editor.Transition(after="s01", type="cut"),
                               editor.Transition(after="s02", type="fadeblack", duration=1.0)])
    cut = plan_of(["s01", "s02", "s03"], infos, fade_out=1.0,
                  transitions=[editor.Transition(after="s01", type="cut"), editor.Transition(after="s02", type="cut")])
    starts = [[a["start"] for m in doc_of(p, infos)["materials"]["material_animations"] for a in m["animations"]
               if a["type"] == "out"] for p in (led, cut)]
    assert starts == [[3 * US + 500_000], [3 * US]]

    music = tracks(doc, "audio")[0]["segments"]  # 5 s of music under a 12 s film: 5 + 5 + 2
    assert [(s["target_timerange"]["start"], s["target_timerange"]["duration"]) for s in music] == [
        (0, 5 * US), (5 * US, 5 * US), (10 * US, 2 * US)]
    assert all(s["volume"] == 0.3 and s["source_timerange"]["start"] == 0 for s in music)
    fades = {f["id"]: f for f in mats["audio_fades"]}
    last = next(fades[r] for r in music[-1]["extra_material_refs"] if r in fades)
    assert last["fade_out_duration"] == US and last["fade_in_duration"] == 0  # at most half of the last piece

    text = tracks(doc, "text")[0]["segments"]  # s03 has no narration
    assert len(text) == 2 and [json.loads(m["content"])["text"] for m in mats["texts"]] == ["Một", "Hai"]
    assert text[0]["target_timerange"]["start"] == 200_000  # clear of the fade
    end = text[0]["target_timerange"]["start"] + text[0]["target_timerange"]["duration"]
    assert end == 4 * US - 500_000  # ends before the transition (half of 1 s)
    assert not tracks(doc_of(plan, infos, subtitles=False), "text")


def test_export_writes_project_and_protects_the_users_work(media, tmp_path):  # noqa: F811
    root = tmp_path / "drafts"
    with pytest.raises(AssembleError, match="CapCut"):
        capcut.export_draft(board(), clips_of(media), editor.EditPlan(), {}, root=root, log=lambda *_: None)
    root.mkdir()
    clips = clips_of(media)
    infos = editor.analyze(clips, log=lambda *_: None)
    plan = editor.auto_plan(board(), infos, [s for s, _ in clips])
    lines = []
    folder = capcut.export_draft(board(), clips, plan, infos, music=media / "m.mp3", root=root, name='A/B: "x"',
                                 log=lines.append)
    assert folder.parent == root and folder.name == "A B x"
    draft = folder / capcut.draft_file_name()
    doc = json.loads(draft.read_text(encoding="utf-8"))
    meta = json.loads((folder / "draft_meta_info.json").read_text(encoding="utf-8"))
    assert meta["draft_name"] == folder.name and meta["draft_fold_path"] == str(folder)
    assert meta["draft_id"] == doc["id"] and meta["tm_duration"] == doc["duration"]
    assert len(meta["draft_materials"][0]["value"]) == 4  # 3 clips + music
    for v in doc["materials"]["videos"] + doc["materials"]["audios"]:  # media lives inside the project
        assert v["path"].startswith(str(folder)) and (folder / capcut.MEDIA_DIR / v["path"].rsplit("/", 1)[1]).is_file()
    assert (doc["materials"]["videos"][2]["width"], doc["materials"]["videos"][2]["height"]) == (90, 160)
    assert doc["materials"]["videos"][2]["has_audio"] is False
    assert "3 cảnh" in lines[-1] and "2 chuyển cảnh (Hoà tan)" in lines[-1] and "có nhạc nền" in lines[-1]

    # Untouched export: the next one replaces it.
    assert capcut.export_draft(board(), clips, plan, infos, root=root, name="A B x", log=lines.append) == folder
    # Open in CapCut: kept, the new export gets another name.
    (folder / ".locked").write_text("")
    second = capcut.export_draft(board(), clips, plan, infos, root=root, name="A B x", log=lines.append)
    assert second.name == "A B x (2)" and "giữ nguyên" in lines[-2]
    # Saved by CapCut since (the user's edits): kept as well.
    (folder / ".locked").unlink()
    draft.write_text(draft.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert capcut.export_draft(board(), clips, plan, infos, root=root, name="A B x", log=lines.append) == second
    assert draft.read_text(encoding="utf-8").endswith(" ")
    # A project that is not ours is never touched.
    (root / "Mine").mkdir()
    assert capcut.export_draft(board(), clips, plan, infos, root=root, name="Mine", log=lines.append).name == "Mine (2)"
    assert list((root / "Mine").iterdir()) == []


def test_agent_gets_the_capcut_list_only_for_capcut(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kw):
        seen["system"] = cmd[cmd.index("--system-prompt") + 1]

        class P:
            returncode = 0
            stderr = ""
            stdout = json.dumps({"structured_output": {
                "clips": [{"id": "s01"}, {"id": "s02"}],
                "transitions": [{"after": "s01", "type": "crossfade", "duration": 0.6, "capcut": "ink"}]}})
        return P()

    monkeypatch.setattr("filmmaker.planner.claude_code_binary", lambda: "/bin/claude")
    monkeypatch.setattr(editor.subprocess, "run", fake_run)
    infos = {s: info(s) for s in ("s01", "s02")}
    plan = editor.ai_plan(board(), infos, ["s01", "s02"], has_music=False, backend="claude_code", capcut=True,
                          log=lambda *_: None)
    assert '"ink": Ink' in seen["system"] and plan.transitions[0].capcut == "ink"
    editor.ai_plan(board(), infos, ["s01", "s02"], has_music=False, backend="claude_code", log=lambda *_: None)
    assert "CapCut" not in seen["system"]


def test_cli_capcut(media, tmp_path):  # noqa: F811
    sbf = tmp_path / "film.yaml"
    sbf.write_text("title: Phim thử\nscenes:\n" + "".join(f"  - id: {s}\n    prompt: x\n" for s in ("s01", "s02", "s03")))
    out = tmp_path / "output" / "film"
    out.mkdir(parents=True)
    (out / "state.json").write_text(json.dumps({"scenes": {sid: {"status": "done", "video": str(p)}
                                                           for sid, p in clips_of(media)}}))
    root = tmp_path / "drafts"
    args = ["capcut", str(sbf), "--engine", "rules", "--capcut-dir", str(root)]
    r = CliRunner().invoke(main, args)
    assert r.exit_code != 0 and "CapCut" in r.output and not (out / "edit.json").exists()
    root.mkdir()
    r = CliRunner().invoke(main, args)
    assert r.exit_code == 0, r.output
    assert (root / "Phim thử" / capcut.draft_file_name()).is_file() and (out / "edit.json").is_file()
    assert "Dự án CapCut" in r.output and not (out / "final.mp4").exists()


def test_web_capcut_action(client, tmp_path, monkeypatch):  # noqa: F811
    pid = client.post("/api/projects", json={"title": "Dựng", "source": "template"}).json()["id"]
    run(client, pid, action="video")
    assert client.get("/api/env").json()["capcut"] is False
    j = run(client, pid, action="capcut", engine="rules")
    assert j["status"] == "failed" and "CapCut" in "\n".join(j["lines"])

    root = tmp_path / "drafts"
    root.mkdir()
    monkeypatch.setenv("FILMMAKER_CAPCUT_DRAFTS", str(root))
    assert client.get("/api/env").json()["capcut"] is True
    j = run(client, pid, action="capcut", engine="rules", burn_subtitles=True)
    assert j["status"] == "done", j["lines"]
    folder = root / j["result"]["capcut_project"]
    doc = json.loads((folder / capcut.draft_file_name()).read_text(encoding="utf-8"))
    assert len(doc["tracks"][0]["segments"]) == 3 and j["result"]["edit_source"] == "rules"
    assert not client.get(f"/api/projects/{pid}").json()["has_final"]  # CapCut exports the film, not us

    view = client.get(f"/api/projects/{pid}/edit-plan").json()
    assert {"id": "ink", "label": "Loang mực"} in view["capcut_transitions"]
    assert view["capcut_default"]["crossfade"] == "dissolve"
    plan = view["plan"]
    plan["transitions"][0]["capcut"] = "ink"
    assert client.put(f"/api/projects/{pid}/edit-plan", json=plan).json()["plan"]["transitions"][0]["capcut"] == "ink"
    j = run(client, pid, action="capcut")
    assert j["status"] == "done" and j["result"]["capcut_project"] == folder.name  # untouched: replaced in place
    doc = json.loads((folder / capcut.draft_file_name()).read_text(encoding="utf-8"))
    assert doc["materials"]["transitions"][0]["name"] == "Ink"


# --- finishing layer: colour, effects, camera moves, sound design, title ---------------------------------------


def pro_plan(infos, order=("s01", "s02", "s03"), **kw):
    S = editor.Sound
    clips = [editor.ClipEdit(id="s01", motion="push_in", music=1.0, sounds=[S(query="Door  SLAM!!", at=1.0, volume=0.8)]),
             editor.ClipEdit(id="s02", effect="胶片", filter="bw 2", music=0.3,
                             sounds=[S(query="heartbeat slow", at=99), S(query="rain"), S(query="third is dropped")]),
             editor.ClipEdit(id="s03", motion="pull_out", effect="no such effect", music=7)]
    trans = [editor.Transition(after="s01", type="smoothleft", duration=0.6, sound="soft whoosh"),
             editor.Transition(after="s02", type="cut")]
    return plan_of(list(order), infos, clips=clips, transitions=trans, look="冷蓝", look_strength=0.5, pro=True,
                   music_query="Sad piano, cinematic!", title="  Di   chúc ", title_in="渐显", subtitle_in="nope", **kw)


def test_sanitize_cleans_the_finishing_layer():
    infos = {s: info(s, 5.0) for s in ("s01", "s02", "s03")}
    plan = pro_plan(infos)
    a, b, c = plan.clips
    assert a.sounds[0].query == "door slam" and b.filter == "BW 2" and b.effect == "胶片"  # names forgiven, phrases cleaned
    assert c.effect is None and c.music == 1.0
    assert [x.query for x in b.sounds] == ["heartbeat slow", "rain"] and b.sounds[0].at == 4.5  # 2 per clip, inside it
    assert plan.music_query == "sad piano cinematic" and plan.title == "Di chúc"
    assert plan.title_in == "渐显" and plan.subtitle_in is None and plan.transitions[0].sound == "soft whoosh"
    many = plan_of(["s01"], infos, clips=[editor.ClipEdit(id="s01", sounds=[editor.Sound(query="a b")] * 2)])
    assert len(many.clips[0].sounds) == 2
    assert editor.MAX_SOUNDS == 12


def test_finishing_layer_on_the_timeline():
    infos = {s: info(s, 5.0) for s in ("s01", "s02", "s03")}
    plan = pro_plan(infos, fade_in=0.5)
    sounds = {"door slam": capcut.Media("/m/sfx01.mp3", 2.0), "soft whoosh": capcut.Media("/m/sfx02.mp3", 1.0),
              "heartbeat slow": capcut.Media("/m/sfx03.mp3", 30.0)}  # "rain" was not found: left out
    doc = doc_of(plan, infos, music=capcut.Media("/m/music.mp3", 60.0), music_volume=0.4, sounds=sounds)
    mats = doc["materials"]
    video = tracks(doc, "video")[0]["segments"]

    # camera moves: scale keyframes from the clip's first to its last frame
    kf = video[0]["common_keyframes"][0]
    assert kf["property_type"] == "KFTypeScaleX"
    assert [(k["time_offset"], k["values"]) for k in kf["keyframe_list"]] == [(0, [1.0]), (5 * US, [capcut.MOTION_SCALE])]
    assert video[2]["clip"]["scale"]["x"] == capcut.MOTION_SCALE and video[1]["common_keyframes"] == []

    # colour: the film's look, broken by the clip that has its own filter
    look = [(m["name"], m["value"]) for m in mats["effects"]]
    assert look == [("冷蓝", 0.5), ("BW 2", 0.8), ("冷蓝", 0.5)]
    assert [(s["target_timerange"]["start"], s["target_timerange"]["duration"]) for s in tracks(doc, "filter")[0]["segments"]] == [
        (0, 5 * US), (5 * US, 5 * US), (10 * US, 5 * US)]
    effect = tracks(doc, "effect")[0]["segments"]
    assert [m["name"] for m in mats["video_effects"]] == ["胶片"] and effect[0]["target_timerange"]["start"] == 5 * US
    assert [p["name"] for p in mats["video_effects"][0]["adjust_params"]] == ["effects_adjust_noise", "effects_adjust_speed"]

    # music dips under the dialogue scene and comes back
    music, *sfx = tracks(doc, "audio")
    assert capcut.music_envelope(plan, infos) == [(0.0, 1.0), (4.65, 1.0), (5.35, 0.3), (9.65, 0.3), (10.35, 1.0), (15.0, 1.0)]
    vol = music["segments"][0]["common_keyframes"][0]
    assert vol["property_type"] == "KFTypeVolume"
    assert [(k["time_offset"], k["values"][0]) for k in vol["keyframe_list"]] == [
        (0, 0.4), (4_650_000, 0.4), (5_350_000, 0.12), (9_650_000, 0.12), (10_350_000, 0.4), (15 * US, 0.4)]
    assert capcut.music_envelope(plan_of(["s01", "s02"], infos), infos) == []  # level never changes: no curve

    # sound effects: where the editor put them; overlapping ones go to another sound track
    placed = sorted((s["target_timerange"]["start"], s["target_timerange"]["duration"], s["volume"])
                    for t in sfx for s in t["segments"])
    assert placed == [(1 * US, 2 * US, 0.8), (4_750_000, 1 * US, 0.6), (9_500_000, 5_500_000, 0.6)]
    assert len(sfx) == 1 and all(t["name"] == "Tiếng động" for t in sfx)
    assert len([a for a in mats["audios"] if "sfx" in a["path"]]) == 3

    # title over the opening, with its entrance and a fade out
    title = next(t for t in tracks(doc, "text") if t["name"] == "Tiêu đề")["segments"][0]
    assert json.loads(mats["texts"][-1]["content"])["text"] == "Di chúc"
    anims = {a["id"]: a for a in mats["material_animations"]}
    kinds = [anims[r]["animations"][0]["type"] for r in title["extra_material_refs"]]
    assert kinds == ["in", "out"] and title["target_timerange"] == {"start": 300_000, "duration": 3_200_000}
    known = {m["id"] for group in mats.values() for m in group}
    for t in doc["tracks"]:
        for s in t["segments"]:
            assert s["material_id"] in known and set(s.get("extra_material_refs") or []) <= known


def test_plain_plan_has_no_finishing_tracks():
    infos = {s: info(s) for s in ("s01", "s02", "s03")}
    doc = doc_of(plan_of(["s01", "s02", "s03"], infos), infos, music=capcut.Media("/m/music.mp3", 60.0))
    assert [t["type"] for t in doc["tracks"]] == ["video", "audio", "text"]
    assert tracks(doc, "audio")[0]["segments"][0]["common_keyframes"] == []


def test_export_fetches_the_plans_sounds_and_music(media, tmp_path):  # noqa: F811
    from filmmaker import resources

    root = tmp_path / "drafts"
    root.mkdir()
    clips = clips_of(media)
    infos = editor.analyze(clips, log=lambda *_: None)
    plan = editor.sanitize(editor.EditPlan(
        music_query="sad piano", pro=True,
        clips=[editor.ClipEdit(id="s01", sounds=[editor.Sound(query="door slam"), editor.Sound(query="not found")])]),
        infos, [s for s, _ in clips])

    class Lib(resources.Library):
        asked = []

        def find(self, query, kind, want=None):
            self.asked.append((query, kind, round(want or 0)))
            if query == "not found":
                return None
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / f"{kind}.mp3").write_bytes((media / "m.mp3").read_bytes())
            return resources.Asset(query=query, kind=kind, file=f"{kind}.mp3", duration=2.0, title=query.title(),
                                   creator="ann", license="by", page="https://example/x")

    lines = []
    lib = Lib(tmp_path / "assets")
    folder = capcut.export_draft(board(), clips, plan, infos, root=root, library=lib, log=lines.append)
    doc = json.loads((folder / capcut.draft_file_name()).read_text(encoding="utf-8"))
    total = round(capcut.timeline(plan, infos)[1])
    assert lib.asked == [("sad piano", "music", total), ("door slam", "sfx", 0), ("not found", "sfx", 0)]
    assert [t["name"] for t in doc["tracks"] if t["type"] == "audio"] == ["Nhạc nền", "Tiếng động"]
    assert all((folder / capcut.MEDIA_DIR / a["path"].rsplit("/", 1)[1]).is_file() for a in doc["materials"]["audios"])
    credits = (tmp_path / "assets" / "CREDITS.txt").read_text(encoding="utf-8")
    assert "Nhạc: “Sad Piano” - ann" in credits and "Tiếng động: “Door Slam”" in credits
    assert "1 tiếng động" in lines[-2] and "CREDITS.txt" in lines[-1]
    # The user's own music wins over the editor's search.
    lib.asked.clear()
    capcut.export_draft(board(), clips, plan, infos, root=root, library=lib, music=media / "m.mp3", log=lines.append)
    assert ("sad piano", "music", total) not in lib.asked


def test_frame_sheets_show_every_scene(media, tmp_path):  # noqa: F811
    clips = clips_of(media) * 2  # 6 scenes -> 4 + 2 rows
    clips = [(f"s{i:02}", p) for i, (_, p) in enumerate(clips, 1)]
    infos = {sid: info(sid, 3.0) for sid, _ in clips}
    sheets = editor.frame_sheets(clips, infos, tmp_path / "frames", log=lambda *_: None)
    assert [(p.name, ids) for p, ids in sheets] == [("sheet01.jpg", ["s01", "s02", "s03", "s04"]),
                                                    ("sheet02.jpg", ["s05", "s06"])]
    out = editor.subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                                 str(sheets[1][0])], capture_output=True, text=True).stdout.strip()
    assert out == f"{3 * editor.SHEET_CELL},{2 * editor.SHEET_CELL}"


def test_pro_agent_sees_the_footage_and_old_plans_are_upgraded(media, tmp_path, monkeypatch):  # noqa: F811
    seen = []

    def fake_run(cmd, **kw):
        if "-p" not in cmd:
            return real_run(cmd, **kw)
        seen.append({"system": cmd[cmd.index("--system-prompt") + 1], "tools": cmd[cmd.index("--tools") + 1],
                     "user": cmd[cmd.index("-p") + 1], "cwd": kw["cwd"]})

        class P:
            returncode = 0
            stderr = ""
            stdout = json.dumps({"structured_output": {
                "clips": [{"id": "s01", "motion": "push_in"}, {"id": "s02"}, {"id": "s03"}], "transitions": [],
                "look": "冷蓝", "music_query": "warm strings"}})
        return P()

    real_run = editor.subprocess.run
    monkeypatch.setattr("filmmaker.planner.claude_code_binary", lambda: "/bin/claude")
    monkeypatch.setattr(editor.subprocess, "run", fake_run)
    clips, out = clips_of(media), tmp_path / "out"
    plan, _ = editor.make_plan(board(), clips, out, engine="rules", capcut=True, pro=True, log=lambda *_: None)
    assert not plan.pro and seen == []  # rules: the basic cut, nothing to look at

    logs = []
    plan, _ = editor.make_plan(board(), clips, out, backend="claude_code", capcut=True, pro=True, log=logs.append)
    call = seen[-1]
    assert plan.pro and plan.source == "ai" and plan.look == "冷蓝" and plan.clips[0].motion == "push_in"
    assert any("chưa có lớp hoàn thiện" in line for line in logs)  # the saved rules plan was replaced
    assert call["tools"] == "Read" and call["cwd"] == str(out / "edit_frames") and (out / "edit_frames" / "sheet01.jpg").is_file()
    assert "sheet01.jpg: rows top to bottom = s01, s02, s03" in call["user"]
    assert "FINISHING PASS" in call["system"] and "FILTERS: BW 2 | " in call["system"] and "胶片" in call["system"]

    n = len(seen)
    editor.make_plan(board(), clips, out, backend="claude_code", capcut=True, pro=True, log=logs.append)
    assert len(seen) == n  # the full plan is reused
    manual = editor.load_plan(out).model_copy(update={"source": "manual", "pro": False})
    editor.save_plan(manual, out)
    editor.make_plan(board(), clips, out, backend="claude_code", capcut=True, pro=True, log=logs.append)
    assert len(seen) == n  # a plan the user edited is never thrown away
    editor.make_plan(board(), clips, out, backend="claude_code", log=logs.append)
    assert len(seen) == n
