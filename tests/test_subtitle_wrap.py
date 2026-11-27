"""Phụ đề không có dấu cách (Thái...) phải được ngắt dòng trước khi in lên hình, nếu không bị cắt hai mép."""
from types import SimpleNamespace

from filmmaker.assemble import _columns, _words, burn_track, wrap_unspaced

LINE = "จันโทรมาบอกแม่ ว่าที่ไล่ลูกไปก็เพราะอยากให้ลูกกลับบ้าน"


def test_long_thai_line_is_broken_and_nothing_is_lost():
    rows = wrap_unspaced(LINE, 18).split("\n")
    assert len(rows) >= 2 and all(_columns(r) <= 18 for r in rows)
    assert "".join(rows).replace(" ", "") == LINE.replace(" ", "")


def test_words_keep_every_character():
    assert "".join(_words(LINE)) == LINE


def test_spaced_languages_and_timestamps_are_left_alone():
    for line in ("Một câu phụ đề tiếng Việt khá dài để ffmpeg tự xuống dòng", "00:00:55,790 --> 00:01:03,020", "7"):
        assert wrap_unspaced(line, 18) == line


def test_only_the_burned_copy_is_wrapped(tmp_path):
    srt = tmp_path / "final.th.srt"
    text = f"1\n00:00:00,000 --> 00:00:03,000\n{LINE}\n"
    srt.write_text(text, encoding="utf-8")
    sb = SimpleNamespace(aspect="9:16", primary_subtitle_code=lambda: "th")
    burned = burn_track(sb, {"th": srt}, None)
    assert burned.name == "final.th.burn.srt" and srt.read_text(encoding="utf-8") == text
    assert burned.read_text(encoding="utf-8").count("\n") > text.count("\n")
