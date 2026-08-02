import pytest

from launcher.pager import Pager

AREA_WIDTH = 1366
AREA_HEIGHT = 713


def make_pager(page_count):
    pager = Pager()
    pager.set_page_count(page_count)
    pager.layout(AREA_WIDTH, AREA_HEIGHT)
    return pager


def test_single_page_ignores_all_clicks():
    pager = make_pager(1)
    assert pager.handle_click(pager.left_arrow_rect.center) is False
    assert pager.handle_click(pager.right_arrow_rect.center) is False
    assert pager.page == 0


def test_right_arrow_advances_page():
    pager = make_pager(3)
    assert pager.handle_click(pager.right_arrow_rect.center) is True
    assert pager.page == 1


def test_left_arrow_disabled_on_first_page():
    pager = make_pager(3)
    assert pager.handle_click(pager.left_arrow_rect.center) is False
    assert pager.page == 0


def test_right_arrow_disabled_on_last_page():
    pager = make_pager(2)
    pager.handle_click(pager.right_arrow_rect.center)
    assert pager.page == 1
    assert pager.handle_click(pager.right_arrow_rect.center) is False
    assert pager.page == 1


def test_left_arrow_retreats_page():
    pager = make_pager(3)
    pager.page = 2
    pager.layout(AREA_WIDTH, AREA_HEIGHT)
    assert pager.handle_click(pager.left_arrow_rect.center) is True
    assert pager.page == 1


def test_dot_click_jumps_directly_to_that_page():
    pager = make_pager(4)
    assert pager.handle_click(pager.dot_rects[3].center) is True
    assert pager.page == 3


def test_clicking_the_current_page_dot_is_a_no_op():
    pager = make_pager(3)
    assert pager.handle_click(pager.dot_rects[0].center) is False
    assert pager.page == 0


def test_set_page_count_clamps_current_page_down():
    pager = make_pager(5)
    pager.page = 4
    pager.set_page_count(2)
    assert pager.page == 1


@pytest.mark.parametrize("count", [0, -1])
def test_set_page_count_floors_at_one(count):
    pager = Pager()
    pager.set_page_count(count)
    assert pager.page_count == 1


def test_dot_rects_are_one_per_page_in_order():
    pager = make_pager(5)
    assert len(pager.dot_rects) == 5
    xs = [rect.centerx for rect in pager.dot_rects]
    assert xs == sorted(xs)
