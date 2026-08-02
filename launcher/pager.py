import pygame

from launcher.config import ARROW_ZONE_WIDTH, PAGE_INDICATOR_HEIGHT

FONT_NAME = "DejaVu Sans"
ARROW_SIZE = 56
ARROW_COLOR = (255, 255, 255)
ARROW_BG = (70, 150, 220)
ARROW_BG_DISABLED = (200, 210, 220)
ARROW_CHEVRON_COLOR = (255, 255, 255)
ARROW_CHEVRON_COLOR_DISABLED = (150, 160, 170)

DOT_RADIUS = 20
DOT_GAP = 56
DOT_COLOR_ACTIVE = (70, 150, 220)
DOT_COLOR_INACTIVE = (255, 255, 255)
DOT_BORDER_COLOR = (90, 90, 90)
DOT_LABEL_COLOR_ACTIVE = (255, 255, 255)
DOT_LABEL_COLOR_INACTIVE = (60, 60, 60)


class Pager:
    """Left/right arrows + numbered page indicators, iPad-home-screen style.

    Pure layout/hit-testing lives here alongside drawing (unlike
    button.py/config.py's split) since there's no reuse pressure to keep
    them apart for a single small widget — main.py owns the one instance.
    """

    def __init__(self):
        self.page = 0
        self.page_count = 1
        self.left_arrow_rect = pygame.Rect(0, 0, ARROW_SIZE, ARROW_SIZE)
        self.right_arrow_rect = pygame.Rect(0, 0, ARROW_SIZE, ARROW_SIZE)
        self.dot_rects = []
        self._font = None

    def _font_obj(self):
        if self._font is None:
            self._font = pygame.font.SysFont(FONT_NAME, 22, bold=True)
        return self._font

    def set_page_count(self, page_count):
        self.page_count = max(1, page_count)
        self.page = min(self.page, self.page_count - 1)

    def layout(self, area_width, area_height):
        content_bottom = area_height - PAGE_INDICATOR_HEIGHT
        center_y = content_bottom // 2

        self.left_arrow_rect.center = (ARROW_ZONE_WIDTH // 2, center_y)
        self.right_arrow_rect.center = (area_width - ARROW_ZONE_WIDTH // 2, center_y)

        self.dot_rects = []
        total_width = (self.page_count - 1) * DOT_GAP
        start_x = area_width // 2 - total_width // 2
        dot_y = content_bottom + PAGE_INDICATOR_HEIGHT // 2
        for i in range(self.page_count):
            rect = pygame.Rect(0, 0, DOT_GAP, PAGE_INDICATOR_HEIGHT)
            rect.center = (start_x + i * DOT_GAP, dot_y)
            self.dot_rects.append(rect)

    def handle_click(self, pos):
        """Returns True if the click changed the current page."""
        if self.page_count <= 1:
            return False
        if self.page > 0 and self.left_arrow_rect.collidepoint(pos):
            self.page -= 1
            return True
        if self.page < self.page_count - 1 and self.right_arrow_rect.collidepoint(pos):
            self.page += 1
            return True
        for i, rect in enumerate(self.dot_rects):
            if rect.collidepoint(pos) and i != self.page:
                self.page = i
                return True
        return False

    def draw(self, surface):
        if self.page_count <= 1:
            return

        left_enabled = self.page > 0
        pygame.draw.circle(
            surface, ARROW_BG if left_enabled else ARROW_BG_DISABLED, self.left_arrow_rect.center, ARROW_SIZE // 2
        )
        self._draw_chevron(surface, self.left_arrow_rect, -1, left_enabled)

        right_enabled = self.page < self.page_count - 1
        pygame.draw.circle(
            surface, ARROW_BG if right_enabled else ARROW_BG_DISABLED, self.right_arrow_rect.center, ARROW_SIZE // 2
        )
        self._draw_chevron(surface, self.right_arrow_rect, 1, right_enabled)

        font = self._font_obj()
        for i, rect in enumerate(self.dot_rects):
            active = i == self.page
            color = DOT_COLOR_ACTIVE if active else DOT_COLOR_INACTIVE
            pygame.draw.circle(surface, color, rect.center, DOT_RADIUS)
            pygame.draw.circle(surface, DOT_BORDER_COLOR, rect.center, DOT_RADIUS, width=2)
            label_color = DOT_LABEL_COLOR_ACTIVE if active else DOT_LABEL_COLOR_INACTIVE
            label = font.render(str(i + 1), True, label_color)
            surface.blit(label, label.get_rect(center=rect.center))

    def _draw_chevron(self, surface, rect, direction, enabled):
        color = ARROW_CHEVRON_COLOR if enabled else ARROW_CHEVRON_COLOR_DISABLED
        cx, cy = rect.center
        half = ARROW_SIZE // 5
        points = [
            (cx - direction * half, cy - half * 2),
            (cx + direction * half, cy),
            (cx - direction * half, cy + half * 2),
        ]
        pygame.draw.lines(surface, color, False, points, 5)
