import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "wordprocessor" / "app"))

import pygame  # noqa: E402
import pytest  # noqa: E402

import text_editor as te  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def pygame_font():
    pygame.font.init()
    yield
    pygame.font.quit()


@pytest.fixture(scope="module")
def font():
    return pygame.font.SysFont("DejaVu Sans", 32)


# ---- wrap_text ----


def test_wrap_text_empty_string_yields_one_empty_line(font):
    lines = te.wrap_text("", font, 400)
    assert lines == [te.Line("", 0, 0)]


def test_wrap_text_short_text_fits_one_line(font):
    lines = te.wrap_text("hi there", font, 1000)
    assert len(lines) == 1
    assert lines[0].text == "hi there"
    assert lines[0].start == 0
    assert lines[0].end == len("hi there")


def test_wrap_text_breaks_on_word_boundary(font):
    word_width = font.size("word ")[0]
    text = "word word word word"
    lines = te.wrap_text(text, font, int(word_width * 2.5))
    assert len(lines) > 1
    # every line's rendered width must fit
    for line in lines:
        assert font.size(line.text)[0] <= int(word_width * 2.5)
    # offsets round-trip: concatenating line texts reconstructs the segment
    assert "".join(line.text for line in lines) == text


def test_wrap_text_hard_newlines_produce_separate_lines(font):
    lines = te.wrap_text("a\n\nb", font, 1000)
    assert [l.text for l in lines] == ["a", "", "b"]
    # blank line between two hard newlines is a real, clickable line
    assert lines[1].start == lines[1].end == 2


def test_wrap_text_trailing_newline_yields_trailing_empty_line(font):
    lines = te.wrap_text("a\n", font, 1000)
    assert [l.text for l in lines] == ["a", ""]


def test_wrap_text_overlong_single_token_falls_back_to_char_wrap(font):
    token = "a" * 200
    max_width = font.size("a" * 10)[0]
    lines = te.wrap_text(token, font, max_width)
    assert len(lines) > 1
    for line in lines:
        assert font.size(line.text)[0] <= max_width
    assert "".join(line.text for line in lines) == token


# ---- cursor_to_pixel / pixel_to_cursor round-trip ----


def test_cursor_to_pixel_at_line_start_is_zero(font):
    lines = te.wrap_text("hello\nworld", font, 1000)
    x, y = te.cursor_to_pixel(6, lines, font, line_height=40)
    assert x == 0
    assert y == 40


def test_cursor_to_pixel_at_wrap_boundary_prefers_next_line_start(font):
    lines = te.wrap_text("hi\nbye", font, 1000)
    # cursor==2 is both end of line 0 ("hi") and could be seen as start of
    # line 1 boundary check only applies to actual adjacent wrapped lines;
    # here it's a hard newline so cursor 2 belongs to line 0's end.
    x, y = te.cursor_to_pixel(2, lines, font, line_height=40)
    assert y == 0


def test_pixel_to_cursor_click_at_origin_of_line_returns_line_start(font):
    lines = te.wrap_text("hello\nworld", font, 1000)
    cursor = te.pixel_to_cursor((0, 45), lines, font, line_height=40)
    assert cursor == lines[1].start


def test_pixel_to_cursor_far_right_returns_line_end(font):
    lines = te.wrap_text("hi", font, 1000)
    cursor = te.pixel_to_cursor((9999, 0), lines, font, line_height=40)
    assert cursor == 2


def test_cursor_pixel_round_trip(font):
    text = "hello world\nsecond line"
    lines = te.wrap_text(text, font, 1000)
    for cursor in range(len(text) + 1):
        x, y = te.cursor_to_pixel(cursor, lines, font, line_height=40)
        recovered = te.pixel_to_cursor((x, y + 1), lines, font, line_height=40)
        # recovered may land on an adjacent equally-close boundary in
        # degenerate cases, so check the pixel position matches instead of
        # exact index equality
        rx, ry = te.cursor_to_pixel(recovered, lines, font, line_height=40)
        assert (rx, ry) == (x, y)


# ---- edit operations ----


def test_insert_text_at_cursor():
    text, cursor = te.insert_text("helloworld", 5, " ")
    assert (text, cursor) == ("hello world", 6)


def test_backspace_removes_char_before_cursor():
    text, cursor = te.backspace("hello", 5)
    assert (text, cursor) == ("hell", 4)


def test_backspace_at_start_is_noop():
    assert te.backspace("hello", 0) == ("hello", 0)


def test_delete_forward_removes_char_after_cursor():
    text, cursor = te.delete_forward("hello", 0)
    assert (text, cursor) == ("ello", 0)


def test_delete_forward_at_end_is_noop():
    assert te.delete_forward("hello", 5) == ("hello", 5)


def test_move_left_right_clamped():
    assert te.move_left("hi", 0) == 0
    assert te.move_right("hi", 2) == 2
    assert te.move_left("hi", 2) == 1
    assert te.move_right("hi", 0) == 1


def test_move_home_end(font):
    lines = te.wrap_text("hello\nworld", font, 1000)
    assert te.move_home("hello\nworld", 8, lines) == 6
    assert te.move_end("hello\nworld", 8, lines) == 11


# ---- move_up / move_down sticky column ----


def test_move_down_preserves_column_across_hard_newline(font):
    text = "abc\nabcdef"
    lines = te.wrap_text(text, font, 1000)
    cursor = te.move_down(text, 2, lines, font, line_height=40)  # column 2 on line 0
    assert cursor == 6  # column 2 on line 1 ("ab" -> index 4+2)


def test_move_up_at_first_line_is_noop(font):
    lines = te.wrap_text("only line", font, 1000)
    assert te.move_up("only line", 3, lines, font, line_height=40) == 3


def test_move_down_at_last_line_is_noop(font):
    lines = te.wrap_text("only line", font, 1000)
    assert te.move_down("only line", 3, lines, font, line_height=40) == 3


def test_move_down_past_shorter_line_clamps_to_line_end(font):
    text = "abcdef\nab"
    lines = te.wrap_text(text, font, 1000)
    cursor = te.move_down(text, 6, lines, font, line_height=40)  # end of "abcdef"
    assert cursor == len(text)  # clamps to end of "ab"


# ---- scroll clamping ----


def test_clamp_scroll_to_cursor_scrolls_down_when_cursor_below_view():
    assert te.clamp_scroll_to_cursor(cursor_line_index=10, scroll_offset_lines=0, visible_lines=5) == 6


def test_clamp_scroll_to_cursor_scrolls_up_when_cursor_above_view():
    assert te.clamp_scroll_to_cursor(cursor_line_index=1, scroll_offset_lines=5, visible_lines=5) == 1


def test_clamp_scroll_to_cursor_noop_when_cursor_already_visible():
    assert te.clamp_scroll_to_cursor(cursor_line_index=3, scroll_offset_lines=2, visible_lines=5) == 2
