import pygame

ICON_SIZE = 128
FONT_NAME = "Quicksand"
LABEL_FONT_SIZE = 26
MIN_LABEL_FONT_SIZE = 16
LABEL_SIDE_PADDING = 10
DESC_FONT_SIZE = 15
DESC_MAX_LINES = 2
DESC_SIDE_PADDING = 14
DESC_GAP = 8
DESC_LINE_GAP = 2

# Soft-pastel-and-calm token set (2026-08-03 redesign) — see CLAUDE.md's
# "Launcher visual design" section for the full rationale. Kept as plain
# module constants rather than a shared tokens module: button.py/pager.py
# (pygame RGB tuples) and bar.py (Cairo 0-1 floats + hex strings) are
# separate processes on separate rendering stacks, so there's no real code
# to share — just the same named values expressed in each stack's format.
INK = (74, 67, 88)              # #4A4358 — warm charcoal-plum, not pure black
TILE_COLOR = (255, 252, 247)    # #FFFCF7 — warm paper cream, not stark white
TILE_HOVER_COLOR = (220, 235, 231)  # #DCEBE7 — pale sage, matches the accent
TILE_BORDER_COLOR = (201, 186, 194)  # #C9BAC2 — dusty mauve, softer than grey
TILE_BORDER_WIDTH = 2
TILE_BORDER_RADIUS = 26
LABEL_COLOR = INK
LABEL_CHIP_COLOR = (220, 235, 231, 180)  # translucent sage chip behind the label
DESC_COLOR = (138, 127, 141)  # 50/50 blend of INK/TILE_BORDER_COLOR — legible but clearly secondary to the label
SHADOW_COLOR = (74, 67, 88, 35)  # soft, unblurred translucent offset — cheap
SHADOW_OFFSET = (0, 7)
ICON_TOP_PADDING = 18
LABEL_GAP = 12


def _wrap_description(text, font, max_width, max_lines):
    """Greedy word-wrap, then hard-truncate to max_lines with an ellipsis on
    the last line if there was more text than fit. Truncation is
    character-by-character (not word-by-word) so a single word/token longer
    than max_width on its own (e.g. a URL in a website tile's description)
    still degrades gracefully instead of overflowing the tile.
    """
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if font.size(candidate)[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    if len(lines) <= max_lines:
        return lines

    lines = lines[:max_lines]
    last = lines[-1]
    while last and font.size(last + "…")[0] > max_width:
        last = last[:-1].rstrip()
    lines[-1] = last + "…"
    return lines


class Button:
    def __init__(self, plugin, rect):
        self.plugin = plugin
        self.rect = pygame.Rect(rect)
        self.icon = self._load_icon()
        self.label = self._render_label()
        self.description_lines = self._render_description()
        self.hovered = False
        self._tile_normal = self._render_tile(TILE_COLOR)
        self._tile_hovered = self._render_tile(TILE_HOVER_COLOR)
        self._shadow = self._render_shadow()

    def _load_icon(self):
        image = pygame.image.load(str(self.plugin.icon)).convert_alpha()
        return pygame.transform.smoothscale(image, (ICON_SIZE, ICON_SIZE))

    def _render_label(self):
        max_width = self.rect.width - 2 * LABEL_SIDE_PADDING
        font_size = LABEL_FONT_SIZE
        font = pygame.font.SysFont(FONT_NAME, font_size, bold=True)
        while font_size > MIN_LABEL_FONT_SIZE and font.size(self.plugin.name)[0] > max_width:
            font_size -= 2
            font = pygame.font.SysFont(FONT_NAME, font_size, bold=True)
        return font.render(self.plugin.name, True, LABEL_COLOR)

    def _render_description(self):
        text = (self.plugin.description or "").strip()
        if not text:
            return []
        max_width = self.rect.width - 2 * DESC_SIDE_PADDING
        font = pygame.font.SysFont(FONT_NAME, DESC_FONT_SIZE)
        lines = _wrap_description(text, font, max_width, DESC_MAX_LINES)
        return [font.render(line, True, DESC_COLOR) for line in lines]

    def _render_tile(self, fill_color):
        # Pre-rendered once per Button (fill + border only change on hover,
        # which toggles between two cached surfaces rather than re-issuing
        # draw calls every frame).
        surf = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        local_rect = surf.get_rect()
        pygame.draw.rect(surf, fill_color, local_rect, border_radius=TILE_BORDER_RADIUS)
        pygame.draw.rect(
            surf, TILE_BORDER_COLOR, local_rect, width=TILE_BORDER_WIDTH, border_radius=TILE_BORDER_RADIUS
        )
        return surf

    def _render_shadow(self):
        surf = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        pygame.draw.rect(surf, SHADOW_COLOR, surf.get_rect(), border_radius=TILE_BORDER_RADIUS)
        return surf

    def contains(self, pos):
        return self.rect.collidepoint(pos)

    def set_hovered(self, hovered):
        self.hovered = hovered

    def draw(self, surface, offset=(0, 0)):
        origin = self.rect.move(offset)
        surface.blit(self._shadow, origin.move(SHADOW_OFFSET))
        surface.blit(self._tile_hovered if self.hovered else self._tile_normal, origin)

        icon_rect = self.icon.get_rect(centerx=origin.centerx, top=origin.top + ICON_TOP_PADDING)
        surface.blit(self.icon, icon_rect)

        label_top = icon_rect.bottom + LABEL_GAP
        chip = pygame.Surface((self.label.get_width() + 16, self.label.get_height() + 6), pygame.SRCALPHA)
        pygame.draw.rect(chip, LABEL_CHIP_COLOR, chip.get_rect(), border_radius=8)
        surface.blit(chip, chip.get_rect(centerx=origin.centerx, top=label_top - 3))

        label_rect = self.label.get_rect(centerx=origin.centerx, top=label_top)
        surface.blit(self.label, label_rect)

        if self.hovered:
            desc_top = label_rect.bottom + DESC_GAP
            for line_surf in self.description_lines:
                line_rect = line_surf.get_rect(centerx=origin.centerx, top=desc_top)
                surface.blit(line_surf, line_rect)
                desc_top = line_rect.bottom + DESC_LINE_GAP
