import pytest
from spalloc_server.area_to_rect import (
    area_to_rect, squarest_rectangle, rectangle_with_aspect_ratio)


@pytest.mark.parametrize("area,wh",
                         [(0, (0, 0)),
                          (1, (1, 1)),
                          (2, (2, 1)),
                          (3, (3, 1)),
                          (4, (2, 2)),
                          (5, (5, 1)),
                          (6, (3, 2)),
                          (7, (7, 1)),
                          (8, (4, 2)),
                          (9, (3, 3)),
                          (10, (5, 2)),
                          (11, (11, 1)),
                          (12, (4, 3)),
                          (13, (13, 1)),
                          (14, (7, 2)),
                          (15, (5, 3)),
                          (16, (4, 4))])
def test_squarest_rectangle(area, wh):
    assert squarest_rectangle(area) == wh


@pytest.mark.parametrize("area,ratio,wh",
                         [(0, 1.0, (0, 0)),
                          (0, 0.1, (0, 0)),
                          (1, 1.0, (1, 1)),
                          (1, 0.1, (1, 1)),
                          (2, 0.1, (2, 1)),
                          (2, 0.5, (2, 1)),
                          (2, 1.0, (2, 2)),
                          (3, 0.3333, (3, 1)),
                          (3, 0.5, (2, 2)),
                          (3, 1.0, (2, 2)),
                          (4, 1.0, (2, 2)),
                          (4, 0.5, (2, 2)),
                          (4, 0.25, (4, 1)),
                          (4, 0.1, (4, 1)),
                          (5, 1.0, (3, 3)),
                          (5, 0.75, (3, 2)),
                          (5, 0.5, (3, 2)),
                          (5, 0.3333, (3, 2)),
                          (5, 0.2, (5, 1)),
                          (5, 0.1, (5, 1)),
                          (6, 1.0, (3, 3)),
                          (6, 0.75, (3, 3)),
                          (6, 0.5, (3, 2)),
                          (6, 0.3, (3, 2)),
                          (6, 0.1, (6, 1)),
                          ])
def test_rectangle_with_aspect_ratio(area, ratio, wh):
    # Human generated cases which make sure the 'sane' thing is done
    assert rectangle_with_aspect_ratio(area, ratio) == wh


def test_rectangle_with_aspect_ratio_fuzz():
    # Fuzz test which checks that requirements are met for a wide range of
    # cases.
    for area in range(50):
        for ratio100 in range(1, 101):
            ratio = ratio100 / 100.0

            w, h = rectangle_with_aspect_ratio(area, ratio)

            # Area should be sufficient
            assert w * h >= area

            # Should be both zero or neither zero
            assert (w == 0) == (h == 0)

            # Aspect ratio should meet requirement
            if area > 0:
                assert 0.0 < (float(h) / float(w)) <= 1.0


@pytest.mark.parametrize("area,bound_width,bound_height,min_ratio",
                         [(0, 1, 1, 0.0),
                          (-1, 1, 1, 0.0),
                          (1, 0, 1, 0.0),
                          (1, -1, 1, 0.0),
                          (1, 1, 0, 0.0),
                          (1, 1, -1, 0.0),
                          (1, 1, 1, -0.1)])
def test_area_to_rect_bad(area, bound_width, bound_height, min_ratio):
    with pytest.raises(ValueError):
        area_to_rect(area, bound_width, bound_height, min_ratio)


@pytest.mark.parametrize("area,bound_width,bound_height,min_ratio,wh",
                         [(1, 1, 1, 0.0, (1, 1)),
                          (1, 2, 2, 0.0, (1, 1)),
                          (1, 1, 1, 1.0, (1, 1)),
                          (1, 2, 2, 1.0, (1, 1)),
                          # Should fail if no room
                          (2, 1, 1, 0.0, None),
                          # Should fit exactly either way up
                          (2, 2, 1, 0.0, (2, 1)),
                          (2, 1, 2, 0.0, (1, 2)),
                          # Should prefer wide to tall in general
                          (2, 3, 3, 0.0, (2, 1)),
                          # Should prefer wide in tall machines
                          (2, 3, 4, 0.0, (2, 1)),
                          # Should prefer tall in wide machines
                          (2, 4, 3, 0.0, (1, 2)),
                          # Should be expanded if ratio demands it
                          (2, 2, 2, 0.5, (2, 1)),
                          (2, 2, 2, 2.0, (2, 1)),
                          (2, 2, 2, 0.6, (2, 2)),
                          (2, 2, 2, 1.6, (2, 2)),
                          (2, 2, 2, 1.0, (2, 2)),
                          # Should fail if ratio cannot be met
                          (2, 1, 2, 1.0, None),
                          # Should squeeze to fit (and rotate to fill best)
                          (5, 3, 3, 0.0, (3, 2)),
                          (5, 3, 4, 0.0, (3, 2)),
                          (5, 4, 3, 0.0, (2, 3)),
                          # Should fail to squeeze if ratio violated
                          (5, 2, 3, 1.0, None),
                          (5, 3, 2, 1.0, None)])
def test_area_to_rect(area, bound_width, bound_height, min_ratio, wh):
    assert area_to_rect(area, bound_width, bound_height, min_ratio) == wh
