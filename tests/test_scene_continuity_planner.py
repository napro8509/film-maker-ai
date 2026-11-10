"""scene-continuity T5: planner rules and cleaning of Claude's locations (AC32–AC34). No real Claude call."""
from filmmaker import planner
from filmmaker.storyboard import StoryboardDraft


def messy() -> dict:
    return {
        "title": "Họp gia đình",
        "locations": [
            {"name": "  ", "description": "blank name"},
            {"name": "Phòng khách", "description": "teak table", "image": "/tmp/invented.png"},
            {"name": "PHÒNG KHÁCH", "description": "a duplicate"},
            {"name": "Bếp", "description": "kitchen", "image_prompt": "An empty kitchen"},
            "not a dict",
        ],
        "scenes": [
            {"id": "s01", "mode": "keyframe", "prompt": "a", "location": "phòng khách", "continue_previous": False},
            {"id": "s02", "mode": "keyframe", "prompt": "b", "location": "Nhà kho"},
            {"id": "s03", "mode": "t2v", "prompt": "c", "location": "Bếp", "characters": ["X"]},
        ],
    }


def test_to_storyboard_cleans():
    data = messy()
    # the plan() path validates Claude's (schema-shaped) output first: blank / duplicate names, invented images
    # and unknown scene locations are no error there
    StoryboardDraft.model_validate({**data, "locations": [x for x in data["locations"] if isinstance(x, dict)]})
    sb = planner._to_storyboard(data)
    assert [(loc.name, loc.description, loc.image) for loc in sb.locations] == [
        ("Phòng khách", "teak table", None), ("Bếp", "kitchen", None)]
    assert sb.locations[1].image_prompt == "An empty kitchen"
    assert [s.location for s in sb.scenes] == ["phòng khách", None, "Bếp"]
    assert sb.scenes[0].continue_previous is False
    assert sb.scenes[2].characters is None
    assert data["locations"][1]["image"] == "/tmp/invented.png"  # the input is not modified


def test_to_storyboard_all_locations_bad():
    data = messy()
    data["locations"] = [{"name": ""}, {"description": "no name"}]
    sb = planner._to_storyboard(data)
    assert sb.locations is None and all(s.location is None for s in sb.scenes)


def test_to_storyboard_without_locations_unchanged():
    data = messy()
    data.pop("locations")
    for s in data["scenes"]:
        s.pop("location")
    sb = planner._to_storyboard(data)
    assert sb.locations is None and sb.continuity is None


def test_system_rules_keywords():
    s = planner.SYSTEM
    for phrase in ("once in `locations`", "set each scene's `location` to the exact location name",
                   "the time of day and the light source", "by exact name with their position",
                   "including people blurred in the background", "Never name a character who is not in the frame",
                   "each scene starts from the end state of the previous scene", "`continue_previous: false`",
                   "or `image` of locations"):
        assert phrase in s, phrase
    assert "Do not set project_id or image/end_image." not in s


def test_draft_schema_fields():
    schema = StoryboardDraft.model_json_schema()
    assert {"locations", "continuity"} <= set(schema["properties"])
    scene = next(d for d in schema["$defs"].values() if d.get("title") == "Scene")
    assert {"location", "continue_previous"} <= set(scene["properties"])
    assert "characters" not in scene["properties"]
    loc = next(d for d in schema["$defs"].values() if d.get("title") == "Location")
    assert set(loc["properties"]) == {"name", "description", "image", "image_prompt"}
