import itertools

import pytest

from launcher import button
from launcher.config import build_button_grid, compute_grid_dimensions


class DummyPlugin:
    def __init__(self, i):
        self.id = f"plugin-{i}"
        self.name = f"Plugin {i}"


AREA_WIDTH = 1366
AREA_HEIGHT = 713


@pytest.mark.parametrize("count", range(1, 13))
def test_grid_has_no_overlapping_tiles(count):
    plugins = [DummyPlugin(i) for i in range(count)]

    layout = build_button_grid(plugins, AREA_WIDTH, AREA_HEIGHT)

    assert len(layout) == count

    def as_bounds(rect):
        x, y, w, h = rect
        return x, y, x + w, y + h

    for (plugin_a, rect_a), (plugin_b, rect_b) in itertools.combinations(layout, 2):
        ax1, ay1, ax2, ay2 = as_bounds(rect_a)
        bx1, by1, bx2, by2 = as_bounds(rect_b)
        overlaps = ax1 < bx2 and bx1 < ax2 and ay1 < by2 and by1 < ay2
        assert not overlaps, f"{plugin_a.id} and {plugin_b.id} tiles overlap"


@pytest.mark.parametrize("count", range(1, 13))
def test_all_tiles_within_area(count):
    plugins = [DummyPlugin(i) for i in range(count)]

    layout = build_button_grid(plugins, AREA_WIDTH, AREA_HEIGHT)

    for _, (x, y, w, h) in layout:
        assert x >= 0
        assert y >= 0
        assert x + w <= AREA_WIDTH
        assert y + h <= AREA_HEIGHT


def test_rendered_icon_meets_minimum_size():
    assert button.ICON_SIZE >= 96


@pytest.mark.parametrize("count", range(1, 13))
def test_tiles_fit_the_rendered_icon(count):
    plugins = [DummyPlugin(i) for i in range(count)]

    layout = build_button_grid(plugins, AREA_WIDTH, AREA_HEIGHT)

    for _, (_, _, w, h) in layout:
        assert w >= button.ICON_SIZE
        assert h >= button.ICON_SIZE


def test_build_button_grid_empty_plugin_list():
    assert build_button_grid([], AREA_WIDTH, AREA_HEIGHT) == []


def test_compute_grid_dimensions_covers_all_items():
    for count in range(1, 13):
        cols, rows = compute_grid_dimensions(count, AREA_WIDTH, AREA_HEIGHT)
        assert cols * rows >= count
