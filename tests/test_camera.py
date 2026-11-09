"""Scene visual direction (camera): how it reaches the image / video prompts."""

from filmmaker.storyboard import CAMERA_PRESETS, Storyboard

CAM = {"shot": "close-up", "angle": "low angle", "movement": "slow dolly-in", "lens": "85mm portrait lens",
       "focus": "shallow depth of field", "lighting": "rim light", "speed": "slow motion",
       "notes": "camera stays behind her shoulder"}


def board(**scene):
    base = {"id": "s01", "image_prompt": "Lan at a cafe", "prompt": "Lan smiles"}
    return Storyboard.model_validate({"title": "t", "style": "cinematic",
                                      "characters": [{"name": "Lan", "description": "red scarf"}],
                                      "scenes": [{**base, **scene}]})


def test_keyframe_image_gets_framing_and_video_gets_motion():
    sb = board(camera=CAM)
    s = sb.scenes[0]
    img = sb.image_prompt(s)
    assert img.startswith("Lan at a cafe. Shot: close-up, low angle.")
    assert "Lens: 85mm portrait lens." in img and "Lighting: rim light." in img
    assert "dolly" not in img and "slow motion" not in img  # a still has no motion
    assert "red scarf" in img and img.endswith("Style: cinematic.")
    vid = sb.video_prompt(s)
    assert vid == ("Lan smiles. Camera movement: slow dolly-in. Speed: slow motion. "
                   "Focus: shallow depth of field. Direction: camera stays behind her shoulder.")


def test_t2v_and_text_only_get_everything():
    sb = board(mode="t2v", camera=CAM)
    t2v = sb.video_prompt(sb.scenes[0])
    for v in CAM.values():
        assert v in t2v
    assert t2v.index("Shot:") < t2v.index("red scarf") < t2v.index("Style:")
    kf = board(camera=CAM)
    text_only = kf.text_only_prompt(kf.scenes[0])
    assert text_only.startswith("Lan at a cafe. Lan smiles. Shot: close-up, low angle.") and "dolly-in" in text_only


def test_no_or_blank_camera_keeps_old_prompts_and_fingerprint():
    plain, blank = board(), board(camera={"shot": "  ", "movement": ""})
    assert blank.scenes[0].camera.is_empty()
    for f in ("image_prompt", "video_prompt", "text_only_prompt", "scene_fingerprint"):
        assert getattr(plain, f)(plain.scenes[0]) == getattr(blank, f)(blank.scenes[0])
    assert plain.video_prompt(plain.scenes[0]) == "Lan smiles"


def test_camera_change_marks_scene_for_rerender():
    a, b = board(camera={"shot": "wide shot"}), board(camera={"shot": "close-up"})
    assert a.scene_fingerprint(a.scenes[0]) != b.scene_fingerprint(b.scenes[0])


def test_presets_cover_every_field_and_dump_round_trips():
    assert set(CAMERA_PRESETS) == {"shot", "angle", "movement", "lens", "focus", "composition", "lighting", "speed"}
    sb = board(camera={"shot": "wide shot"})
    data = sb.model_dump(exclude_none=True)
    assert data["scenes"][0]["camera"] == {"shot": "wide shot"}
    assert Storyboard.model_validate(data).scenes[0].camera.shot == "wide shot"


def test_web_serves_presets_and_shows_camera_in_prompts(tmp_path, monkeypatch):
    from tests.test_web import client as make_client  # reuse the web test setup
    c = make_client.__wrapped__(tmp_path, monkeypatch)
    presets = c.get("/api/env").json()["camera_presets"]
    assert {"value": "close-up", "label": "Cận cảnh (CU)"} in presets["shot"]
    pid = c.post("/api/projects", json={"title": "Cam", "source": "template"}).json()["id"]
    sb = c.get(f"/api/projects/{pid}").json()["storyboard"]
    sb["scenes"][1]["camera"] = {"shot": "extreme close-up", "movement": "orbit around the subject", "lens": ""}
    d = c.put(f"/api/projects/{pid}", json={"storyboard": sb}).json()
    assert d["errors"] == []
    st = d["scene_state"][sb["scenes"][1]["id"]]
    assert "Shot: extreme close-up." in st["image_prompt"] and "orbit" not in st["image_prompt"]
    assert "Camera movement: orbit around the subject." in st["video_prompt"]
    assert d["storyboard"]["scenes"][1]["camera"] == {"shot": "extreme close-up", "movement": "orbit around the subject"}
