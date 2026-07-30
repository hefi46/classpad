import pygame

ICON_SIZE = 128
FONT_NAME = "DejaVu Sans"
LABEL_FONT_SIZE = 28
MIN_LABEL_FONT_SIZE = 16
LABEL_SIDE_PADDING = 10
LABEL_COLOR = (40, 40, 40)
TILE_COLOR = (255, 255, 255)
TILE_HOVER_COLOR = (255, 240, 190)
TILE_BORDER_COLOR = (90, 90, 90)
TILE_BORDER_WIDTH = 3
TILE_BORDER_RADIUS = 20
ICON_TOP_PADDING = 18
LABEL_GAP = 10


class Button:
    def __init__(self, plugin, rect):
        self.plugin = plugin
        self.rect = pygame.Rect(rect)
        self.icon = self._load_icon()
        self.label = self._render_label()
        self.hovered = False

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

    def contains(self, pos):
        return self.rect.collidepoint(pos)

    def set_hovered(self, hovered):
        self.hovered = hovered

    def draw(self, surface):
        color = TILE_HOVER_COLOR if self.hovered else TILE_COLOR
        pygame.draw.rect(surface, color, self.rect, border_radius=TILE_BORDER_RADIUS)
        pygame.draw.rect(
            surface, TILE_BORDER_COLOR, self.rect, width=TILE_BORDER_WIDTH, border_radius=TILE_BORDER_RADIUS
        )

        icon_rect = self.icon.get_rect(centerx=self.rect.centerx, top=self.rect.top + ICON_TOP_PADDING)
        surface.blit(self.icon, icon_rect)

        label_rect = self.label.get_rect(centerx=self.rect.centerx, top=icon_rect.bottom + LABEL_GAP)
        surface.blit(self.label, label_rect)
