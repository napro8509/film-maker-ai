"""scene-continuity T1: Location model, validation, Setting sentence, reference slots and fingerprints."""
import hashlib
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from filmmaker.storyboard import (MAX_REFS, Location, RefItem, Storyboard, StoryboardDraft, load_storyboard,
                                  save_storyboard)

ROOM = "long dark teak table, six chairs, brass ceiling lamp, window on the left."


def sb_dict(**over) -> dict:
    data = {
        "title": "T",
        "style": "warm film",
        "characters": [
            {"name": "Minh", "description": "stern man", "image": "minh.png"},
            {"name": "Lan", "description": "red lipstick", "image": "lan.png"},
            {"name": "Tuan", "description": "messy hair", "image": "tuan.png"},
        ],
        "locations": [{"name": "Phòng khách", "description": ROOM, "image": "room.png"}],
        "scenes": [
            {"id": "s01", "mode": "keyframe", "image_prompt": "Minh, Lan and Tuan at the table",
             "prompt": "they argue", "location": "phòng khách", "camera": {"shot": "wide shot"}},
            {"id": "s02", "mode": "t2v", "prompt": "Minh and Lan stand up", "location": "Phòng khách"},
            {"id": "s03", "mode": "i2v", "image": "/fixed/start.png", "prompt": "Lan smiles",
             "location": "Phòng khách"},
        ],
    }
    data.update(over)
    return data


def write_images(folder: Path, *, room: bytes | None = b"room-v1") -> None:
    for n in ("minh", "lan", "tuan"):
        (folder / f"{n}.png").write_bytes(f"{n}-pic".encode())
    if room is not None:
        (folder / "room.png").write_bytes(room)


def load(tmp_path: Path, data: dict, *, room: bytes | None = b"room-v1") -> Storyboard:
    write_images(tmp_path, room=room)
    p = tmp_path / "sb.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return load_storyboard(p)


def sha12(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:12]


# --- AC3: no new keys written ---------------------------------------------------------------------------

def test_roundtrip_no_new_keys(tmp_path):
    data = sb_dict()
    data.pop("locations")
    for s in data["scenes"]:
        s.pop("location")
    sb = load(tmp_path, data)
    out = tmp_path / "out.yaml"
    save_storyboard(sb, out)
    text = out.read_text(encoding="utf-8")
    saved = yaml.safe_load(text)
    assert "locations" not in saved and "continuity" not in saved
    for s in saved["scenes"]:
        assert "location" not in s and "continue_previous" not in s


def test_roundtrip_keeps_locations(tmp_path):
    sb = load(tmp_path, sb_dict(continuity=False))
    out = tmp_path / "out.yaml"
    save_storyboard(sb, out)
    keys = list(yaml.safe_load(out.read_text(encoding="utf-8")))
    assert keys.index("characters") + 1 == keys.index("locations") < keys.index("continuity") < keys.index("scenes")
    again = load_storyboard(out)
    assert again.continuity is False
    assert [loc.name for loc in again.locations] == ["Phòng khách"]
    assert again.scenes[0].location == "phòng khách"


# --- AC5 / AC6 / AC7: validation and paths ----------------------------------------------------------------

def test_location_validation_matches_case_insensitive(tmp_path):
    sb = load(tmp_path, sb_dict())
    assert sb.scene_location(sb.scenes[0]).name == "Phòng khách"
    assert sb.scene_location(sb.scenes[1]).name == "Phòng khách"


def test_location_validation_unknown_name():
    data = sb_dict()
    data["scenes"][1]["location"] = "Bếp"
    with pytest.raises(ValidationError, match=r"scene s02: unknown location 'Bếp'"):
        Storyboard.model_validate(data)


@pytest.mark.parametrize("locs, shown", [
    ([{"name": "Phòng khách"}, {"name": "PHÒNG KHÁCH "}], "'Phòng khách' (scenes s01, s02)"),
    ([{"name": "Phòng khách"}, {"name": "  "}], "''"),
])
def test_location_validation_duplicate_or_blank(locs, shown):
    """AC5: the message names the location and the scenes (s03 is i2v: it ignores its location)."""
    with pytest.raises(ValidationError, match="location names must be unique and non-empty") as e:
        Storyboard.model_validate(sb_dict(locations=locs))
    assert shown in str(e.value)
    assert "(scenes s01, s02 use one)" in str(e.value)


def test_location_validation_only_when_used():
    """Blank / duplicate names are fine while no keyframe/t2v scene uses a location (an i2v one does not count)."""
    data = sb_dict(locations=[{"name": ""}, {"name": "A"}, {"name": "a"}])
    data["scenes"][0].pop("location")
    data["scenes"][1].pop("location")
    data["scenes"][2]["location"] = "nowhere"  # i2v ignores it
    Storyboard.model_validate(data)


def test_blank_scene_location_is_none():
    data = sb_dict()
    data["scenes"][0]["location"] = "   "
    sb = Storyboard.model_validate(data)
    assert sb.scenes[0].location is None


def test_draft_accepts_bad_locations():
    data = sb_dict(locations=[{"name": ""}, {"name": "A"}, {"name": "a"}])
    data["scenes"][1]["location"] = "Bếp"
    StoryboardDraft.model_validate(data)


def test_load_resolves_location_image(tmp_path):
    sb = load(tmp_path, sb_dict())
    assert sb.locations[0].image == str((tmp_path / "room.png").resolve())


# --- AC8 / AC9 / AC10: Setting sentence and i2v ----------------------------------------------------------

SETTING = "Setting: long dark teak table, six chairs, brass ceiling lamp, window on the left."


def test_setting_sentence_image_prompt(tmp_path):
    sb = load(tmp_path, sb_dict())
    s = sb.scenes[0]
    p = sb.image_prompt(s)
    assert SETTING in p
    assert p.index("Shot: wide shot.") < p.index(SETTING) < p.index("Minh: stern man.")
    assert "Phòng khách" not in p
    assert "Setting:" not in sb.video_prompt(s)


def test_setting_sentence_t2v_and_text_only(tmp_path):
    sb = load(tmp_path, sb_dict())
    assert SETTING in sb.video_prompt(sb.scenes[1])
    assert SETTING in sb.text_only_prompt(sb.scenes[0])


def test_empty_description_no_setting(tmp_path):
    sb = load(tmp_path, sb_dict(locations=[{"name": "Phòng khách", "description": "  "}]))
    for s in sb.scenes:
        assert "Setting:" not in sb.image_prompt(s) + sb.video_prompt(s) + sb.text_only_prompt(s)


def test_i2v_ignores_location(tmp_path):
    sb = load(tmp_path, sb_dict())
    s = sb.scenes[2]
    assert sb.scene_location(s) is None and sb.char_ref_limit(s) == MAX_REFS
    assert "Setting:" not in sb.image_prompt(s) + sb.video_prompt(s) + sb.text_only_prompt(s)
    assert sb.scene_refs(s) == [] and sb.refs_signature(s) == []
    fp = sb.scene_fingerprint(s)
    s.location = None
    assert sb.scene_fingerprint(s) == fp


def test_location_without_assignment_changes_nothing(tmp_path):
    data = sb_dict()
    for s in data["scenes"]:
        s.pop("location")
    with_locs = load(tmp_path, data)
    data.pop("locations")
    without = load(tmp_path, data)
    for a, b in zip(with_locs.scenes, without.scenes):
        assert with_locs.scene_fingerprint(a) == without.scene_fingerprint(b)
        assert with_locs.image_prompt(a) == without.image_prompt(b)


# --- AC11: location prompt ---------------------------------------------------------------------------------

def test_location_prompt(tmp_path):
    sb = load(tmp_path, sb_dict())
    p = sb.location_prompt(sb.locations[0])
    assert p.startswith("Location reference image: long dark teak table")
    assert "no people" in p and p.endswith("Style: warm film.")
    own = Location(name="X", description="d", image_prompt="An empty kitchen at dawn.")
    assert sb.location_prompt(own) == "An empty kitchen at dawn. Style: warm film."


# --- AC16 / AC18 / AC19 / AC20: reference slots ------------------------------------------------------------

def test_refs_with_location_three_characters(tmp_path):
    sb = load(tmp_path, sb_dict())
    s = sb.scenes[0]
    assert sb.char_ref_limit(s) == 2
    assert sb.scene_refs(s) == [("bối cảnh Phòng khách", (tmp_path / "room.png").resolve()),
                                ("Minh", (tmp_path / "minh.png").resolve()),
                                ("Lan", (tmp_path / "lan.png").resolve())]
    assert sb.dropped_char_refs(s) == ["Tuan"]
    assert [n for n, _ in sb.scene_char_refs(s)] == ["Minh", "Lan"]
    sig = sb.refs_signature(s)
    assert sig[0] == f"loc:{sha12(b'room-v1')}" and [x.split(':')[0] for x in sig[1:]] == ["Minh", "Lan"]


def test_refs_with_location_chained_prev(tmp_path):
    sb = load(tmp_path, sb_dict())
    s = sb.scenes[0]
    kf = tmp_path / "keyframes" / "s00.png"
    items = sb.scene_ref_items(s, prev=("s00", kf))
    assert items[0] == RefItem("keyframe", "keyframe s00", kf)
    assert [it.label for it in items] == ["keyframe s00", "Minh", "Lan"]
    # t2v never sends a keyframe
    assert [it.kind for it in sb.scene_ref_items(sb.scenes[1], prev=("s00", kf))] == ["location", "character",
                                                                                     "character"]
    # the signature never depends on chaining
    assert sb.refs_signature(s)[0].startswith("loc:")


def test_refs_with_location_without_image(tmp_path):
    """AC18: slot 1 stays reserved (2 characters at most) and there is no loc: entry; adding the picture keeps
    the characters but adds loc:."""
    sb = load(tmp_path, sb_dict(), room=None)
    s = sb.scenes[0]
    assert [n for n, _ in sb.scene_refs(s)] == ["Minh", "Lan"]
    before = sb.refs_signature(s)
    assert not any(x.startswith("loc:") for x in before)
    (tmp_path / "room.png").write_bytes(b"room-v1")
    assert [n for n, _ in sb.scene_char_refs(s)] == ["Minh", "Lan"]
    assert sb.refs_signature(s) == [f"loc:{sha12(b'room-v1')}", *before]


def test_refs_with_location_character_refs_off(tmp_path):
    """AC19: use_character_refs=false keeps the location picture / previous keyframe."""
    sb = load(tmp_path, sb_dict(use_character_refs=False))
    s = sb.scenes[0]
    assert [n for n, _ in sb.scene_refs(s)] == ["bối cảnh Phòng khách"]
    assert [it.label for it in sb.scene_ref_items(s, prev=("s00", tmp_path / "x.png"))] == ["keyframe s00"]
    assert sb.dropped_char_refs(s) == []


def test_refs_without_location_unchanged(tmp_path):
    data = sb_dict()
    data["scenes"][0].pop("location")
    sb = load(tmp_path, data)
    s = sb.scenes[0]
    assert sb.char_ref_limit(s) == MAX_REFS
    assert [n for n, _ in sb.scene_refs(s)] == ["Minh", "Lan", "Tuan"]
    assert sb.scene_ref_items(s, prev=("s00", tmp_path / "x.png")) == [
        RefItem("character", n, p) for n, p in sb.scene_refs(s)]


# --- AC27 / AC28: fingerprints ------------------------------------------------------------------------------

def test_fingerprint_stable_cases(tmp_path):
    data = sb_dict()
    base = load(tmp_path, data)
    fps = [base.scene_fingerprint(s) for s in base.scenes]

    def fps_of(d):
        sb = Storyboard.model_validate(d)
        for loc in sb.locations or []:  # same absolute paths as `base`
            loc.image = str((tmp_path / loc.image).resolve()) if loc.image else None
        for c in sb.characters:
            c.image = str((tmp_path / c.image).resolve()) if c.image else None
        return [sb.scene_fingerprint(s) for s in sb.scenes]

    assert fps_of(data) == fps
    renamed = sb_dict(locations=[{"name": "Phòng khách nhà cũ", "description": ROOM, "image": "room.png"}])
    for s in renamed["scenes"]:
        s["location"] = "Phòng khách nhà cũ"
    assert fps_of(renamed) == fps
    cased = sb_dict()
    cased["scenes"][0]["location"] = "PHÒNG KHÁCH"
    assert fps_of(cased) == fps
    off = sb_dict(continuity=False)
    off["scenes"][0]["continue_previous"] = False
    assert fps_of(off) == fps


def test_fingerprint_changes_for_location_users_only(tmp_path):
    data = sb_dict()
    data["scenes"].append({"id": "s04", "mode": "keyframe", "prompt": "street"})
    base = load(tmp_path, data)
    fp = {s.id: base.scene_fingerprint(s) for s in base.scenes}

    def changed(sb):
        return sorted(s.id for s in sb.scenes if sb.scene_fingerprint(s) != fp[s.id])

    # description edited
    d2 = yaml.safe_load(yaml.safe_dump(data))
    d2["locations"][0]["description"] = "a different room"
    assert changed(load(tmp_path, d2)) == ["s01", "s02"]
    # picture changed / removed
    assert changed(load(tmp_path, data, room=b"room-v2")) == ["s01", "s02"]
    (tmp_path / "room.png").unlink()
    p = tmp_path / "sb.yaml"
    assert changed(load_storyboard(p)) == ["s01", "s02"]
    # assigning a location to s04
    d3 = yaml.safe_load(yaml.safe_dump(data))
    d3["scenes"][3]["location"] = "Phòng khách"
    assert changed(load(tmp_path, d3)) == ["s04"]
