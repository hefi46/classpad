"""Pure text-editing engine: word-wrap, cursor math, and edit operations.

Deliberately independent of pygame's display/event loop (only pygame.font is
used, for real glyph-width measurement) so this can be unit tested headlessly
with SDL_VIDEODRIVER=dummy and no real window. wordprocessor.py's render/event
loop is a thin shell around these functions.

Text model: a single string (with literal "\\n" for line breaks) plus an
integer cursor offset into that string. Wrapped lines are always a *derived*
view recomputed from (text, font, width) — never a separate source of truth —
so there's nothing that can drift out of sync with the underlying text.
"""

import re
from collections import namedtuple

Line = namedtuple("Line", "text start end")

_TOKEN_RE = re.compile(r"\S+\s*|\s+")


def wrap_text(text, font, max_width):
    """Split text into visual Lines that each fit within max_width pixels.

    Each Line's start/end are exact offsets into the original text, so this
    is invertible in both directions (cursor -> line, line+x -> cursor).
    """
    lines = []
    offset = 0
    for segment in text.split("\n"):
        segment_start = offset
        if segment == "":
            lines.append(Line("", segment_start, segment_start))
        else:
            lines.extend(_wrap_segment(segment, segment_start, font, max_width))
        offset += len(segment) + 1  # +1 for the '\n' consumed by split()
    return lines


def _wrap_segment(segment, segment_start, font, max_width):
    lines = []
    line_start = segment_start
    current = ""
    for token in _TOKEN_RE.findall(segment):
        if current and font.size(current + token)[0] > max_width:
            lines.append(Line(current, line_start, line_start + len(current)))
            line_start += len(current)
            current = ""
        if font.size(token)[0] > max_width:
            # A single token alone overflows max_width (no spaces to break
            # on, e.g. mashed keys) — fall back to character-by-character
            # wrapping so this can never overflow or loop forever.
            for ch in token:
                if current and font.size(current + ch)[0] > max_width:
                    lines.append(Line(current, line_start, line_start + len(current)))
                    line_start += len(current)
                    current = ""
                current += ch
        else:
            current += token
    lines.append(Line(current, line_start, line_start + len(current)))
    return lines


def _line_index_for_cursor(cursor, lines):
    for i, line in enumerate(lines):
        if line.start <= cursor <= line.end:
            # A cursor exactly at a wrap boundary belongs to the *start* of
            # the next line (standard editor convention), unless this is the
            # last line.
            if cursor == line.end and i + 1 < len(lines) and lines[i + 1].start == cursor:
                continue
            return i
    return max(0, len(lines) - 1)


def cursor_to_pixel(cursor, lines, font, line_height):
    i = _line_index_for_cursor(cursor, lines)
    line = lines[i]
    x = font.size(line.text[: cursor - line.start])[0]
    return x, i * line_height


def _nearest_index_in_line(x, line_text, font):
    best_i, best_dist = 0, abs(x)
    for i in range(1, len(line_text) + 1):
        dist = abs(font.size(line_text[:i])[0] - x)
        if dist < best_dist:
            best_i, best_dist = i, dist
    return best_i


def pixel_to_cursor(pos, lines, font, line_height, scroll_offset_px=0):
    if not lines:
        return 0
    x, y = pos
    line_index = (y + scroll_offset_px) // line_height
    line_index = max(0, min(line_index, len(lines) - 1))
    line = lines[line_index]
    return line.start + _nearest_index_in_line(x, line.text, font)


def insert_text(text, cursor, s):
    return text[:cursor] + s + text[cursor:], cursor + len(s)


def backspace(text, cursor):
    if cursor == 0:
        return text, cursor
    return text[: cursor - 1] + text[cursor:], cursor - 1


def delete_forward(text, cursor):
    if cursor >= len(text):
        return text, cursor
    return text[:cursor] + text[cursor + 1 :], cursor


def move_left(text, cursor):
    return max(0, cursor - 1)


def move_right(text, cursor):
    return min(len(text), cursor + 1)


def move_home(text, cursor, lines):
    return lines[_line_index_for_cursor(cursor, lines)].start


def move_end(text, cursor, lines):
    return lines[_line_index_for_cursor(cursor, lines)].end


def move_up(text, cursor, lines, font, line_height):
    i = _line_index_for_cursor(cursor, lines)
    if i == 0:
        return cursor
    x, _ = cursor_to_pixel(cursor, lines, font, line_height)
    target = lines[i - 1]
    return target.start + _nearest_index_in_line(x, target.text, font)


def move_down(text, cursor, lines, font, line_height):
    i = _line_index_for_cursor(cursor, lines)
    if i >= len(lines) - 1:
        return cursor
    x, _ = cursor_to_pixel(cursor, lines, font, line_height)
    target = lines[i + 1]
    return target.start + _nearest_index_in_line(x, target.text, font)


def clamp_scroll_to_cursor(cursor_line_index, scroll_offset_lines, visible_lines):
    if cursor_line_index < scroll_offset_lines:
        return cursor_line_index
    if cursor_line_index >= scroll_offset_lines + visible_lines:
        return cursor_line_index - visible_lines + 1
    return scroll_offset_lines
