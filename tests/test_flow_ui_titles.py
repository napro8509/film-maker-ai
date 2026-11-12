"""Flow chat driver: tile titles with accented letters, and the offer bubble is never a failure."""
from filmmaker.flow_ui import classify_replies, css_string


def test_css_string_keeps_accented_letters():
    # json.dumps gave "Boy receiving xôi", which CSS reads as "x" + "u00f4i": the tile never matched
    assert css_string("Boy receiving xôi from woman") == '"Boy receiving xôi from woman"'
    assert css_string('say "hi" \\ ok') == '"say \\"hi\\" \\\\ ok"'
    assert css_string("a\nb") == '"a\\a b"'


def test_offer_bubble_with_reasoning_is_not_a_failure():
    offer = ("Show thinking\nI must make sure nothing violates the policy.\n"
             "Would you like me to kick off this 1 video generation, costing 5 credits?")
    assert classify_replies([offer]) == (None, False)
    assert classify_replies([offer, "Sorry, the video generation failed."]) == ("failed", False)
    assert classify_replies(["Something went wrong. Please try again."]) == ("failed", False)
