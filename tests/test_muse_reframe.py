"""Muse zooms into the start picture, so a low first-frame match is not "another picture" there."""
from filmmaker import qc
from filmmaker.storyboard import Storyboard


def sb(**scene):
    return Storyboard.model_validate({
        "title": "t",
        "scenes": [{"id": "s01", "mode": "t2v", "prompt": "two people stand on a road", **scene}],
    })


def test_muse_film_without_character_refs_is_reframed():
    board = sb()
    board.model = "muse"
    assert qc.reframes_start(board, board.scenes[0])


def test_muse_scene_in_a_flow_film_is_reframed():
    board = sb(model="muse")
    assert qc.reframes_start(board, board.scenes[0])


def test_flow_clip_without_refs_keeps_the_first_frame_check():
    board = sb()
    assert not qc.reframes_start(board, board.scenes[0])
