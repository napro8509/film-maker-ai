"""F30: a character the story dresses differently (lottery seller -> chairman) gets the new clothes in that scene."""
from filmmaker.storyboard import Storyboard

SB = {
    "title": "Khăn tay",
    "characters": [{"name": "Lert", "description": "Elderly man, short white hair, bucket hat, blue plaid shirt.",
                    "outfits": {"chairman": "cream silk shirt, grey trousers, bareheaded"}}],
    "scenes": [
        {"id": "s01", "mode": "keyframe", "image_prompt": "Lert sells lottery tickets in a soi", "prompt": "Lert waves"},
        {"id": "s02", "mode": "keyframe", "image_prompt": "Lert stands in a high-floor office", "prompt": "Lert nods",
         "outfits": {"Lert": "chairman"}},
        {"id": "s03", "mode": "keyframe", "image_prompt": "Lert at a wedding", "prompt": "Lert smiles",
         "outfits": {"lert": "a white wedding suit"}},
    ],
}


def test_scene_outfit_replaces_the_usual_clothes_in_the_prompt():
    sb = Storyboard.model_validate(SB)
    s1, s2, s3 = sb.scenes
    assert "wears" not in sb.image_prompt(s1)
    p2 = sb.image_prompt(s2)
    assert "In this shot Lert wears cream silk shirt, grey trousers, bareheaded" in p2
    assert "not the clothes described above" in p2
    assert "Lert wears a white wedding suit" in sb.image_prompt(s3)  # plain clothes text, any case of the name


def test_outfit_changes_the_fingerprint_only_where_it_is_used():
    plain = Storyboard.model_validate({**SB, "scenes": [{**sc, "outfits": None} for sc in SB["scenes"]]})
    sb = Storyboard.model_validate(SB)
    assert sb.scene_fingerprint(sb.scenes[0]) == plain.scene_fingerprint(plain.scenes[0])
    assert sb.scene_fingerprint(sb.scenes[1]) != plain.scene_fingerprint(plain.scenes[1])
