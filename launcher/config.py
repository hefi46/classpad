import math

from launcher.plugin_manager import scan_plugins

GRID_MARGIN = 40
TILE_PADDING = 24
MAX_TILE_SIZE = 260


def compute_grid_dimensions(count, area_width, area_height):
    best_cols, best_rows, best_tile_size = 1, count, 0
    for cols in range(1, count + 1):
        rows = math.ceil(count / cols)
        tile_size = min(area_width / cols, area_height / rows)
        if tile_size > best_tile_size:
            best_cols, best_rows, best_tile_size = cols, rows, tile_size
    return best_cols, best_rows


def build_button_grid(plugins, area_width, area_height):
    count = len(plugins)
    if count == 0:
        return []

    grid_width = area_width - 2 * GRID_MARGIN
    grid_height = area_height - 2 * GRID_MARGIN

    cols, rows = compute_grid_dimensions(count, grid_width, grid_height)

    cell_width = grid_width / cols
    cell_height = grid_height / rows
    tile_size = int(min(cell_width, cell_height, MAX_TILE_SIZE + TILE_PADDING) - TILE_PADDING)

    layout = []
    for index, plugin in enumerate(plugins):
        row, col = divmod(index, cols)
        cell_x = GRID_MARGIN + col * cell_width
        cell_y = GRID_MARGIN + row * cell_height
        tile_x = int(cell_x + (cell_width - tile_size) / 2)
        tile_y = int(cell_y + (cell_height - tile_size) / 2)
        layout.append((plugin, (tile_x, tile_y, tile_size, tile_size)))

    return layout
