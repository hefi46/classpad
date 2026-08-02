import ctypes
import os
import sys
from pathlib import Path

import pygame

sys.path.insert(0, str(Path(__file__).resolve().parent))
import documents  # noqa: E402
import text_editor as te  # noqa: E402

PR_SET_NAME = 15


def _set_process_name(name):
    # Without this, the OS-level process name (`comm`, what `pkill`/`ps` see)
    # is just "python3" — indistinguishable from the launcher's and bar's own
    # processes. See plugins/xylophone/app/xylophone.py for the original
    # fix; "wordprocessor" must also be added to process_manager.KILL_LIST
    # and recovery.sh's kill list for this rename to matter.
    try:
        libc = ctypes.CDLL(None)
        libc.prctl(PR_SET_NAME, name.encode(), 0, 0, 0)
    except OSError:
        pass


BAR_HEIGHT = 55  # must match bar/bar.py and launcher/main.py
FPS = 30
FONT_NAME = "DejaVu Sans"  # matches launcher/button.py

# ---- Palette — a fresh, "grown-up-but-friendly" look rather than reusing
# xylophone's saturated toy-primary palette (deliberate choice this session:
# the existing launcher/xylophone visuals aren't treated as the house style
# to match). Warm and rounded, not garish; big drawn icon glyphs rather than
# relying on text alone, per the "kid-friendly, simple, not ugly" brief. ----
BG_COLOR = (250, 246, 237)
CARD_COLOR = (255, 255, 255)
CARD_BORDER = (226, 219, 201)
TEXT_DARK = (51, 41, 33)
TEXT_MUTED = (150, 137, 118)
TOOLBAR_COLOR = (42, 130, 122)
TOOLBAR_TEXT = (255, 255, 255)
PAPER_COLOR = (255, 253, 248)
CURSOR_COLOR = (224, 105, 73)
DELETE_COLOR = (196, 92, 78)
OVERLAY_COLOR = (40, 33, 26, 150)
ACCENT_STRIPE_COLORS = [(42, 130, 122), (224, 105, 73), (224, 169, 55)]

# Editor screen only: fills the whole window before the toolbar/paper are
# drawn on top, so no stale pixels from the previously-drawn screen (e.g. the
# document list) ever show through around the edges.
EDITOR_BG_COLOR = (40, 42, 45)
FIELD_BG = (35, 108, 101)
FIELD_BG_FOCUS = (55, 138, 129)
FIELD_TEXT = (255, 255, 255)
FIELD_PLACEHOLDER = (190, 214, 210)
FIELD_PADDING = 14

FONT_SIZE_PRESETS = [32, 44, 56, 72]
DEFAULT_FONT_SIZE_INDEX = 1

EDIT_IDLE_FLUSH_MS = 1500
EDIT_MAX_FLUSH_MS = 5000


def _render_text(font, text, color):
    return font.render(text, True, color)


def _truncate_to_width(text, font, max_width):
    if font.size(text)[0] <= max_width:
        return text
    ellipsis = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.size(text[:mid] + ellipsis)[0] <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ellipsis


def draw_rounded_button(surface, rect, color, border_color=None):
    pygame.draw.rect(surface, color, rect, border_radius=16)
    if border_color:
        pygame.draw.rect(surface, border_color, rect, width=2, border_radius=16)


def draw_plus_icon(surface, rect, color, thickness=7):
    cx, cy = rect.center
    half = min(rect.width, rect.height) // 2 - 10
    pygame.draw.line(surface, color, (cx - half, cy), (cx + half, cy), thickness)
    pygame.draw.line(surface, color, (cx, cy - half), (cx, cy + half), thickness)


def draw_trash_icon(surface, rect, color, thickness=4):
    body = pygame.Rect(0, 0, int(rect.width * 0.55), int(rect.height * 0.5))
    body.centerx = rect.centerx
    body.top = rect.centery - rect.height * 0.05
    pygame.draw.rect(surface, color, body, width=thickness, border_radius=4)
    lid_y = body.top - 6
    pygame.draw.line(
        surface, color, (body.left - 6, lid_y), (body.right + 6, lid_y), thickness
    )
    handle = pygame.Rect(0, 0, int(body.width * 0.45), 8)
    handle.centerx = rect.centerx
    handle.bottom = lid_y
    pygame.draw.rect(surface, color, handle, width=thickness, border_radius=3)
    for frac in (0.3, 0.5, 0.7):
        x = body.left + body.width * frac
        pygame.draw.line(surface, color, (x, body.top + 6), (x, body.bottom - 6), 2)


def draw_back_arrow_icon(surface, rect, color, thickness=7):
    cx, cy = rect.center
    size = min(rect.width, rect.height) // 2 - 8
    points = [(cx + size, cy - size), (cx - size, cy), (cx + size, cy + size)]
    pygame.draw.lines(surface, color, False, points, thickness)


def friendly_mtime(mtime):
    # mtime is a real Unix timestamp (from Path.stat().st_mtime); "today" must
    # be compared against wall-clock time, not pygame.time.get_ticks() (which
    # is milliseconds since this process started, not a date at all).
    import datetime

    dt = datetime.datetime.fromtimestamp(mtime)
    if dt.date() == datetime.datetime.now().date():
        return dt.strftime("%-I:%M %p")
    return dt.strftime("%b %-d")


class DocumentListScreen:
    CARD_HEIGHT = 96
    CARD_GAP = 16
    ROW_PADDING = 32

    def __init__(self, fonts):
        self.fonts = fonts
        self.docs = []
        self.card_rects = []  # (open_rect, delete_rect, doc)
        self.scroll_offset = 0
        self.confirm_delete_doc = None
        self.new_button_rect = None
        self.refresh()

    def refresh(self):
        self.docs = documents.list_documents()

    def layout(self, width, height):
        self.new_button_rect = pygame.Rect(0, 0, 260, 64)
        self.new_button_rect.top = self.ROW_PADDING
        self.new_button_rect.right = width - self.ROW_PADDING

        list_top = self.ROW_PADDING + 64 + 24
        list_width = width - 2 * self.ROW_PADDING
        self.card_rects = []
        y = list_top
        for doc in self.docs:
            card_rect = pygame.Rect(self.ROW_PADDING, y, list_width, self.CARD_HEIGHT)
            delete_rect = pygame.Rect(0, 0, 56, 56)
            delete_rect.centery = card_rect.centery
            delete_rect.right = card_rect.right - 20
            open_rect = pygame.Rect(
                card_rect.left, card_rect.top, delete_rect.left - card_rect.left - 12, card_rect.height
            )
            self.card_rects.append((card_rect, open_rect, delete_rect, doc))
            y += self.CARD_HEIGHT + self.CARD_GAP
        self.list_top = list_top
        self.content_height = y - list_top
        self.visible_height = height - list_top

    def clamp_scroll(self):
        max_scroll = max(0, self.content_height - self.visible_height)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))

    def handle_mousewheel(self, event):
        self.scroll_offset -= event.y * 40
        self.clamp_scroll()

    def handle_click(self, pos):
        if self.confirm_delete_doc is not None:
            return None
        if self.new_button_rect.collidepoint(pos):
            return ("new", None)
        for card_rect, open_rect, delete_rect, doc in self.card_rects:
            shifted_open = open_rect.move(0, -self.scroll_offset)
            shifted_delete = delete_rect.move(0, -self.scroll_offset)
            if not card_rect.move(0, -self.scroll_offset).colliderect(
                pygame.Rect(0, self.list_top, 10_000, self.visible_height)
            ):
                continue
            if shifted_delete.collidepoint(pos):
                return ("delete", doc)
            if shifted_open.collidepoint(pos):
                return ("open", doc)
        return None

    def draw(self, surface, width, height):
        surface.fill(BG_COLOR)
        title_font = self.fonts["list_title"]
        surface.blit(_render_text(title_font, "My Stories", TEXT_DARK), (self.ROW_PADDING, self.ROW_PADDING + 12))

        draw_rounded_button(surface, self.new_button_rect, ACCENT_STRIPE_COLORS[0])
        plus_rect = pygame.Rect(0, 0, 40, 40)
        plus_rect.midleft = (self.new_button_rect.left + 16, self.new_button_rect.centery)
        draw_plus_icon(surface, plus_rect, TOOLBAR_TEXT, thickness=6)
        label = _render_text(self.fonts["button"], "New Story", TOOLBAR_TEXT)
        surface.blit(label, (plus_rect.right + 10, self.new_button_rect.centery - label.get_height() // 2))

        clip = pygame.Rect(0, self.list_top, width, self.visible_height)
        prev_clip = surface.get_clip()
        surface.set_clip(clip)

        if not self.docs:
            empty = _render_text(self.fonts["card_title"], "No stories yet — tap + New Story!", TEXT_MUTED)
            surface.blit(empty, empty.get_rect(center=(width // 2, self.list_top + 80)))

        for i, (card_rect, open_rect, delete_rect, doc) in enumerate(self.card_rects):
            shifted = card_rect.move(0, -self.scroll_offset)
            if shifted.bottom < self.list_top or shifted.top > height:
                continue
            pygame.draw.rect(surface, CARD_COLOR, shifted, border_radius=14)
            pygame.draw.rect(surface, CARD_BORDER, shifted, width=1, border_radius=14)
            stripe = pygame.Rect(shifted.left, shifted.top, 8, shifted.height)
            pygame.draw.rect(
                surface, ACCENT_STRIPE_COLORS[i % len(ACCENT_STRIPE_COLORS)], stripe,
                border_top_left_radius=14, border_bottom_left_radius=14,
            )

            text_left = shifted.left + 32
            max_title_width = shifted.width - 220
            title = _truncate_to_width(doc.title, self.fonts["card_title"], max_title_width)
            surface.blit(
                _render_text(self.fonts["card_title"], title, TEXT_DARK),
                (text_left, shifted.top + 18),
            )
            when = friendly_mtime(doc.mtime)
            surface.blit(
                _render_text(self.fonts["card_sub"], when, TEXT_MUTED),
                (text_left, shifted.top + 58),
            )

            shifted_delete = delete_rect.move(0, -self.scroll_offset)
            draw_trash_icon(surface, shifted_delete, DELETE_COLOR)

        surface.set_clip(prev_clip)

        if self.confirm_delete_doc is not None:
            self._draw_confirm_dialog(surface, width, height)

    def _draw_confirm_dialog(self, surface, width, height):
        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        overlay.fill(OVERLAY_COLOR)
        surface.blit(overlay, (0, 0))

        box = pygame.Rect(0, 0, 560, 240)
        box.center = (width // 2, height // 2)
        pygame.draw.rect(surface, CARD_COLOR, box, border_radius=20)

        msg = _render_text(self.fonts["card_title"], "Delete this story?", TEXT_DARK)
        surface.blit(msg, msg.get_rect(center=(box.centerx, box.top + 60)))

        yes_rect = pygame.Rect(0, 0, 220, 64)
        yes_rect.center = (box.centerx - 130, box.bottom - 70)
        no_rect = pygame.Rect(0, 0, 220, 64)
        no_rect.center = (box.centerx + 130, box.bottom - 70)

        draw_rounded_button(surface, yes_rect, DELETE_COLOR)
        draw_rounded_button(surface, no_rect, TOOLBAR_COLOR)
        yes_label = _render_text(self.fonts["button"], "Yes, delete", TOOLBAR_TEXT)
        no_label = _render_text(self.fonts["button"], "Cancel", TOOLBAR_TEXT)
        surface.blit(yes_label, yes_label.get_rect(center=yes_rect.center))
        surface.blit(no_label, no_label.get_rect(center=no_rect.center))

        self._confirm_yes_rect = yes_rect
        self._confirm_no_rect = no_rect

    def handle_confirm_click(self, pos):
        if self._confirm_yes_rect.collidepoint(pos):
            return True
        if self._confirm_no_rect.collidepoint(pos):
            return False
        return None


class TextField:
    """A single-line editable field for the title/author toolbar inputs.

    Deliberately separate from the body's wrap-aware cursor math in
    text_editor.py — a single line never wraps, so this only needs the flat
    string operations (insert/backspace/delete/move_left/move_right), plus
    its own horizontal scroll-to-keep-cursor-visible instead of the body's
    vertical one.
    """

    def __init__(self, placeholder):
        self.placeholder = placeholder
        self.text = ""
        self.cursor = 0
        self.focused = False
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.scroll_x = 0

    def layout(self, rect):
        self.rect = rect

    def set_text(self, text):
        self.text = text
        self.cursor = len(text)
        self.scroll_x = 0

    def handle_click(self, pos):
        hit = self.rect.collidepoint(pos)
        self.focused = hit
        if hit:
            self.cursor = len(self.text)
        return hit

    def handle_textinput(self, text):
        text = text.replace("\n", "")
        if not text:
            return
        self.text, self.cursor = te.insert_text(self.text, self.cursor, text)

    def handle_keydown(self, event):
        if event.key == pygame.K_BACKSPACE:
            self.text, self.cursor = te.backspace(self.text, self.cursor)
        elif event.key == pygame.K_DELETE:
            self.text, self.cursor = te.delete_forward(self.text, self.cursor)
        elif event.key == pygame.K_LEFT:
            self.cursor = te.move_left(self.text, self.cursor)
        elif event.key == pygame.K_RIGHT:
            self.cursor = te.move_right(self.text, self.cursor)
        elif event.key == pygame.K_HOME:
            self.cursor = 0
        elif event.key == pygame.K_END:
            self.cursor = len(self.text)
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
            self.focused = False

    def _clamp_scroll(self, font):
        cursor_x = font.size(self.text[: self.cursor])[0]
        inner_width = max(0, self.rect.width - 2 * FIELD_PADDING)
        if cursor_x - self.scroll_x > inner_width:
            self.scroll_x = cursor_x - inner_width
        if cursor_x - self.scroll_x < 0:
            self.scroll_x = cursor_x

    def draw(self, surface, font, now):
        bg = FIELD_BG_FOCUS if self.focused else FIELD_BG
        pygame.draw.rect(surface, bg, self.rect, border_radius=10)
        if self.focused:
            pygame.draw.rect(surface, CURSOR_COLOR, self.rect, width=2, border_radius=10)

        self._clamp_scroll(font)
        prev_clip = surface.get_clip()
        surface.set_clip(self.rect)
        text_y = self.rect.centery

        if self.text:
            surf = _render_text(font, self.text, FIELD_TEXT)
            surface.blit(
                surf, (self.rect.left + FIELD_PADDING - self.scroll_x, text_y - surf.get_height() // 2)
            )
        elif not self.focused:
            surf = _render_text(font, self.placeholder, FIELD_PLACEHOLDER)
            surface.blit(surf, (self.rect.left + FIELD_PADDING, text_y - surf.get_height() // 2))

        if self.focused and (now // 500) % 2 == 0:
            cursor_x = self.rect.left + FIELD_PADDING + font.size(self.text[: self.cursor])[0] - self.scroll_x
            pygame.draw.line(
                surface, CURSOR_COLOR, (cursor_x, self.rect.top + 8), (cursor_x, self.rect.bottom - 8), 2
            )

        surface.set_clip(prev_clip)


class EditorScreen:
    TOOLBAR_HEIGHT = 90
    TEXT_PADDING = 40
    # Gap between the paper's edge and where lines of text/the cursor
    # actually start — otherwise the cursor sits flush against the paper
    # edge on a brand-new document, which reads as a bug rather than a page.
    PAGE_INNER_MARGIN = 56

    def __init__(self, fonts):
        self.fonts = fonts
        self.font_size_index = DEFAULT_FONT_SIZE_INDEX
        self._font_cache = {}
        self.path = None
        self.text = ""
        self.cursor = 0
        self.scroll_offset_lines = 0
        self.last_saved_text = ""
        self.last_edit_tick = 0
        self.last_flush_tick = 0
        self._wrap_cache_key = None
        self._wrap_cache_lines = None

        self.title_field = TextField("Story name")
        self.author_field = TextField("Your name")
        self.by_label_rect = pygame.Rect(0, 0, 0, 0)

        self.back_rect = None
        self.smaller_rect = None
        self.bigger_rect = None
        self.text_area_rect = None
        self.content_rect = None

    def open(self, path, text):
        self.path = path
        title, author, body = documents.parse_content(text)
        self.title_field.set_text(title)
        self.author_field.set_text(author)
        self.text = body
        self.cursor = len(body)
        self.scroll_offset_lines = 0
        self.last_saved_text = text
        now = pygame.time.get_ticks()
        self.last_edit_tick = now
        self.last_flush_tick = now

    def current_font(self):
        size = FONT_SIZE_PRESETS[self.font_size_index]
        if size not in self._font_cache:
            self._font_cache[size] = pygame.font.SysFont(FONT_NAME, size)
        return self._font_cache[size]

    def layout(self, width, height):
        self.back_rect = pygame.Rect(20, 12, 64, 64)
        self.smaller_rect = pygame.Rect(0, 12, 64, 64)
        self.smaller_rect.right = width - 20 - 64 - 12
        self.bigger_rect = pygame.Rect(0, 12, 64, 64)
        self.bigger_rect.right = width - 20

        field_h = 44
        field_top = (self.TOOLBAR_HEIGHT - field_h) // 2
        field_area_left = self.back_rect.right + 20
        field_area_right = self.smaller_rect.left - 20
        field_area_width = max(0, field_area_right - field_area_left)

        by_label_w = self.fonts["field_label"].size("by")[0] + 20
        remaining = max(0, field_area_width - by_label_w - 24)
        title_w = int(remaining * 0.58)
        author_w = remaining - title_w

        title_rect = pygame.Rect(field_area_left, field_top, title_w, field_h)
        by_rect = pygame.Rect(title_rect.right + 12, field_top, by_label_w, field_h)
        author_rect = pygame.Rect(by_rect.right + 12, field_top, author_w, field_h)
        self.title_field.layout(title_rect)
        self.by_label_rect = by_rect
        self.author_field.layout(author_rect)

        self.text_area_rect = pygame.Rect(
            self.TEXT_PADDING,
            self.TOOLBAR_HEIGHT + self.TEXT_PADDING,
            width - 2 * self.TEXT_PADDING,
            height - self.TOOLBAR_HEIGHT - 2 * self.TEXT_PADDING,
        )
        self.content_rect = pygame.Rect(
            self.text_area_rect.left + self.PAGE_INNER_MARGIN,
            self.text_area_rect.top + self.PAGE_INNER_MARGIN,
            self.text_area_rect.width - 2 * self.PAGE_INNER_MARGIN,
            self.text_area_rect.height - 2 * self.PAGE_INNER_MARGIN,
        )

    def _lines(self):
        font = self.current_font()
        key = (self.text, self.font_size_index, self.content_rect.width)
        if key != self._wrap_cache_key:
            self._wrap_cache_lines = te.wrap_text(self.text, font, self.content_rect.width)
            self._wrap_cache_key = key
        return self._wrap_cache_lines

    def _line_height(self):
        return self.current_font().get_linesize()

    def _visible_lines(self):
        return max(1, self.content_rect.height // self._line_height())

    def _mutate(self, new_text, new_cursor):
        self.text, self.cursor = new_text, new_cursor
        self.last_edit_tick = pygame.time.get_ticks()
        self._follow_cursor()

    def _follow_cursor(self):
        lines = self._lines()
        line_index = te._line_index_for_cursor(self.cursor, lines)
        self.scroll_offset_lines = te.clamp_scroll_to_cursor(
            line_index, self.scroll_offset_lines, self._visible_lines()
        )

    def handle_textinput(self, text):
        if self.title_field.focused:
            self.title_field.handle_textinput(text)
            self.last_edit_tick = pygame.time.get_ticks()
            return
        if self.author_field.focused:
            self.author_field.handle_textinput(text)
            self.last_edit_tick = pygame.time.get_ticks()
            return
        self._mutate(*te.insert_text(self.text, self.cursor, text))

    def handle_keydown(self, event):
        if self.title_field.focused:
            self.title_field.handle_keydown(event)
            self.last_edit_tick = pygame.time.get_ticks()
            return
        if self.author_field.focused:
            self.author_field.handle_keydown(event)
            self.last_edit_tick = pygame.time.get_ticks()
            return
        font = self.current_font()
        line_height = self._line_height()
        lines = self._lines()
        if event.key == pygame.K_BACKSPACE:
            self._mutate(*te.backspace(self.text, self.cursor))
        elif event.key == pygame.K_DELETE:
            self._mutate(*te.delete_forward(self.text, self.cursor))
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._mutate(*te.insert_text(self.text, self.cursor, "\n"))
        elif event.key == pygame.K_LEFT:
            self._mutate(self.text, te.move_left(self.text, self.cursor))
        elif event.key == pygame.K_RIGHT:
            self._mutate(self.text, te.move_right(self.text, self.cursor))
        elif event.key == pygame.K_UP:
            self._mutate(self.text, te.move_up(self.text, self.cursor, lines, font, line_height))
        elif event.key == pygame.K_DOWN:
            self._mutate(self.text, te.move_down(self.text, self.cursor, lines, font, line_height))
        elif event.key == pygame.K_HOME:
            self._mutate(self.text, te.move_home(self.text, self.cursor, lines))
        elif event.key == pygame.K_END:
            self._mutate(self.text, te.move_end(self.text, self.cursor, lines))

    def handle_click(self, pos):
        title_hit = self.title_field.handle_click(pos)
        author_hit = self.author_field.handle_click(pos)
        if title_hit or author_hit:
            return True
        if self.content_rect.collidepoint(pos):
            font = self.current_font()
            lines = self._lines()
            local = (pos[0] - self.content_rect.left, pos[1] - self.content_rect.top)
            self.cursor = te.pixel_to_cursor(
                local, lines, font, self._line_height(), self.scroll_offset_lines * self._line_height()
            )
            self._follow_cursor()
            return True
        if self.smaller_rect.collidepoint(pos):
            self.font_size_index = max(0, self.font_size_index - 1)
            return True
        if self.bigger_rect.collidepoint(pos):
            self.font_size_index = min(len(FONT_SIZE_PRESETS) - 1, self.font_size_index + 1)
            return True
        if self.back_rect.collidepoint(pos):
            return "back"
        return False

    def handle_mousewheel(self, event):
        max_scroll = max(0, len(self._lines()) - self._visible_lines())
        self.scroll_offset_lines = max(0, min(self.scroll_offset_lines - event.y, max_scroll))

    def _composed_text(self):
        return documents.compose_content(self.title_field.text, self.author_field.text, self.text)

    def maybe_autosave(self, now):
        if self._composed_text() == self.last_saved_text:
            return
        idle_elapsed = now - self.last_edit_tick
        since_flush = now - self.last_flush_tick
        if idle_elapsed >= EDIT_IDLE_FLUSH_MS or since_flush >= EDIT_MAX_FLUSH_MS:
            self.flush(now)

    def flush(self, now=None):
        composed = self._composed_text()
        if composed != self.last_saved_text:
            documents.write_document(self.path, composed)
            self.last_saved_text = composed
        self.last_flush_tick = now if now is not None else pygame.time.get_ticks()

    def draw(self, surface, width, height, now):
        surface.fill(EDITOR_BG_COLOR)
        surface.fill(TOOLBAR_COLOR, pygame.Rect(0, 0, width, self.TOOLBAR_HEIGHT))

        pygame.draw.rect(surface, PAPER_COLOR, self.text_area_rect, border_radius=10)
        pygame.draw.rect(surface, CARD_BORDER, self.text_area_rect, width=1, border_radius=10)

        draw_rounded_button(surface, self.back_rect, (35, 108, 101))
        draw_back_arrow_icon(surface, self.back_rect, TOOLBAR_TEXT)

        draw_rounded_button(surface, self.smaller_rect, (35, 108, 101))
        draw_rounded_button(surface, self.bigger_rect, (35, 108, 101))
        a_small = _render_text(self.fonts["toolbar_small"], "A", TOOLBAR_TEXT)
        a_big = _render_text(self.fonts["toolbar_big"], "A", TOOLBAR_TEXT)
        surface.blit(a_small, a_small.get_rect(center=self.smaller_rect.center))
        surface.blit(a_big, a_big.get_rect(center=self.bigger_rect.center))

        self.title_field.draw(surface, self.fonts["field"], now)
        by_label = _render_text(self.fonts["field_label"], "by", TOOLBAR_TEXT)
        surface.blit(by_label, by_label.get_rect(center=self.by_label_rect.center))
        self.author_field.draw(surface, self.fonts["field"], now)

        font = self.current_font()
        line_height = self._line_height()
        lines = self._lines()
        visible = self._visible_lines()
        clip = self.content_rect
        prev_clip = surface.get_clip()
        surface.set_clip(clip)

        for i in range(self.scroll_offset_lines, min(len(lines), self.scroll_offset_lines + visible + 1)):
            line = lines[i]
            y = clip.top + (i - self.scroll_offset_lines) * line_height
            if line.text:
                surface.blit(_render_text(font, line.text, TEXT_DARK), (clip.left, y))

        body_focused = not self.title_field.focused and not self.author_field.focused
        if body_focused and (now // 500) % 2 == 0:
            cx, cy = te.cursor_to_pixel(self.cursor, lines, font, line_height)
            cursor_x = clip.left + cx
            cursor_y = clip.top + cy - self.scroll_offset_lines * line_height
            if clip.top <= cursor_y <= clip.bottom - line_height:
                pygame.draw.line(
                    surface, CURSOR_COLOR, (cursor_x, cursor_y), (cursor_x, cursor_y + line_height - 4), 3
                )

        surface.set_clip(prev_clip)


def build_fonts():
    return {
        "list_title": pygame.font.SysFont(FONT_NAME, 40, bold=True),
        "card_title": pygame.font.SysFont(FONT_NAME, 30, bold=True),
        "card_sub": pygame.font.SysFont(FONT_NAME, 20),
        "button": pygame.font.SysFont(FONT_NAME, 26, bold=True),
        "toolbar_small": pygame.font.SysFont(FONT_NAME, 22, bold=True),
        "toolbar_big": pygame.font.SysFont(FONT_NAME, 32, bold=True),
        "field": pygame.font.SysFont(FONT_NAME, 24),
        "field_label": pygame.font.SysFont(FONT_NAME, 22, bold=True),
    }


def main():
    _set_process_name("wordprocessor")
    os.environ.setdefault("SDL_VIDEO_WINDOW_POS", f"0,{BAR_HEIGHT}")

    pygame.init()
    screen_width = pygame.display.Info().current_w
    screen_height = pygame.display.Info().current_h
    window_height = screen_height - BAR_HEIGHT

    screen = pygame.display.set_mode((screen_width, window_height), pygame.NOFRAME)
    pygame.display.set_caption("My Story Writer")
    pygame.key.set_repeat(400, 40)
    pygame.key.start_text_input()

    fonts = build_fonts()
    list_screen = DocumentListScreen(fonts)
    editor_screen = EditorScreen(fonts)
    list_screen.layout(screen_width, window_height)
    editor_screen.layout(screen_width, window_height)

    mode = "list"
    clock = pygame.time.Clock()
    running = True

    while running:
        now = pygame.time.get_ticks()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif mode == "list":
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if list_screen.confirm_delete_doc is not None:
                        result = list_screen.handle_confirm_click(event.pos)
                        if result is True:
                            documents.delete_document(list_screen.confirm_delete_doc.path)
                            list_screen.confirm_delete_doc = None
                            list_screen.refresh()
                            list_screen.layout(screen_width, window_height)
                        elif result is False:
                            list_screen.confirm_delete_doc = None
                    else:
                        action = list_screen.handle_click(event.pos)
                        if action is not None:
                            kind, doc = action
                            if kind == "new":
                                path = documents.create_document()
                                editor_screen.open(path, "")
                                mode = "editor"
                            elif kind == "open":
                                editor_screen.open(doc.path, documents.read_document(doc.path))
                                mode = "editor"
                            elif kind == "delete":
                                list_screen.confirm_delete_doc = doc
                elif event.type == pygame.MOUSEWHEEL:
                    list_screen.handle_mousewheel(event)
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    if list_screen.confirm_delete_doc is not None:
                        list_screen.confirm_delete_doc = None
            elif mode == "editor":
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    result = editor_screen.handle_click(event.pos)
                    if result == "back":
                        editor_screen.flush(now)
                        list_screen.refresh()
                        list_screen.layout(screen_width, window_height)
                        mode = "list"
                elif event.type == pygame.MOUSEWHEEL:
                    editor_screen.handle_mousewheel(event)
                elif event.type == pygame.TEXTINPUT:
                    editor_screen.handle_textinput(event.text)
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        editor_screen.flush(now)
                        list_screen.refresh()
                        list_screen.layout(screen_width, window_height)
                        mode = "list"
                    else:
                        editor_screen.handle_keydown(event)

        if mode == "editor":
            editor_screen.maybe_autosave(now)
            editor_screen.draw(screen, screen_width, window_height, now)
        else:
            list_screen.draw(screen, screen_width, window_height)

        pygame.display.flip()
        clock.tick(FPS)

    if mode == "editor":
        editor_screen.flush()
    pygame.quit()


if __name__ == "__main__":
    main()
