"""Flow nhận lời nhưng không hiện thẻ xác nhận: là lỗi tạm thời, không phải lỗi nội dung cảnh."""
import pytest

from filmmaker.flow_ui import promised_without_offer


@pytest.mark.parametrize("reply", [
    "I will generate 1 video using Veo 3.1 - Lite as requested, incorporating the scene reference image.",
    "I'll generate 1 video using Veo 3.1 - Lite with your attached image.",
])
def test_an_accepting_answer(reply):
    assert promised_without_offer(reply)


@pytest.mark.parametrize("reply", [
    "I cannot process your request as specified. The model supports a maximum of 3 image references.",
    "(không có)",
    "",
])
def test_anything_else_stays_a_decline(reply):
    assert not promised_without_offer(reply)
