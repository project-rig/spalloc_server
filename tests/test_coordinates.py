import pytest

from rig.links import Links

from spalloc_server.coordinates import \
    link_to_vector, board_down_link, board_to_chip, chip_to_board, \
    triad_dimensions_to_chips, WrapAround


def test_link_to_vector():
    assert link_to_vector[(0, Links.east)] == (0, -1, 2)
    assert link_to_vector[(0, Links.north_east)] == (0, 0, 1)
    assert link_to_vector[(0, Links.north)] == (0, 0, 2)
    assert link_to_vector[(0, Links.west)] == (-1, 0, 1)
    assert link_to_vector[(0, Links.south_west)] == (-1, -1, 2)
    assert link_to_vector[(0, Links.south)] == (-1, -1, 1)

    assert link_to_vector[(1, Links.east)] == (1, 0, -1)
    assert link_to_vector[(1, Links.north_east)] == (1, 0, 1)
    assert link_to_vector[(1, Links.north)] == (1, 1, -1)
    assert link_to_vector[(1, Links.west)] == (0, 0, 1)
    assert link_to_vector[(1, Links.south_west)] == (0, 0, -1)
    assert link_to_vector[(1, Links.south)] == (0, -1, 1)

    assert link_to_vector[(2, Links.east)] == (0, 0, -1)
    assert link_to_vector[(2, Links.north_east)] == (1, 1, -2)
    assert link_to_vector[(2, Links.north)] == (0, 1, -1)
    assert link_to_vector[(2, Links.west)] == (0, 1, -2)
    assert link_to_vector[(2, Links.south_west)] == (-1, 0, -1)
    assert link_to_vector[(2, Links.south)] == (0, 0, -2)


@pytest.mark.parametrize("x1,y1,z1,link,width,height,x2,y2,z2,wrapped",
                         [  # Should add vectors correctly
                          (0, 0, 0, Links.north, 1, 1,
                           0, 0, 2, WrapAround.none),
                          (3, 2, 1, Links.north, 10, 10,
                           4, 3, 0, WrapAround.none),
                          # Should detect (and classify) wrap-around
                          (9, 8, 1, Links.north_east, 10, 10,
                           0, 8, 2, WrapAround.x),
                          (8, 9, 2, Links.north_east, 10, 10,
                           9, 0, 0, WrapAround.y),
                          (9, 9, 2, Links.north_east, 10, 10,
                           0, 0, 0, WrapAround.both),
                          # ...even on 1x1
                          (0, 0, 0, Links.south, 1, 1,
                           0, 0, 1, WrapAround.both),
                         ])
def test_board_down_link(x1, y1, z1, link, width, height, x2, y2, z2, wrapped):
    assert (board_down_link(x1, y1, z1, link, width, height) ==
            (x2, y2, z2, wrapped))


@pytest.mark.parametrize("bxyz,cxy",
                         [((0, 0, 0), (0, 0)),
                          ((0, 0, 1), (8, 4)),
                          ((0, 0, 2), (4, 8)),
                          ((2, 1, 0), (24, 12)),
                          ((2, 1, 1), (32, 16)),
                          ((2, 1, 2), (28, 20))])
def test_board_to_chip(bxyz, cxy):
    assert board_to_chip(*bxyz) == cxy


@pytest.mark.parametrize("bxyz,cxy",
                         [((0, 0, 0), (0, 0)),
                          ((0, 0, 1), (8, 4)),
                          ((0, 0, 2), (4, 8)),
                          ((2, 1, 0), (24, 12)),
                          ((2, 1, 1), (32, 16)),
                          ((2, 1, 2), (28, 20))])
def test_chip_to_board(bxyz, cxy):
    assert chip_to_board(*cxy) == bxyz


@pytest.mark.parametrize("wht,wh",
                         [((1, 1, WrapAround.none), (16, 16)),
                          ((1, 1, WrapAround.x), (12, 16)),
                          ((1, 1, WrapAround.y), (16, 12)),
                          ((1, 1, WrapAround.both), (12, 12)),
                          ((2, 1, WrapAround.none), (28, 16)),
                          ((2, 1, WrapAround.x), (24, 16)),
                          ((2, 1, WrapAround.y), (28, 12)),
                          ((2, 1, WrapAround.both), (24, 12)),
                          ((1, 2, WrapAround.none), (16, 28)),
                          ((1, 2, WrapAround.x), (12, 28)),
                          ((1, 2, WrapAround.y), (16, 24)),
                          ((1, 2, WrapAround.both), (12, 24))])
def test_dimensions_to_chips(wht, wh):
    assert triad_dimensions_to_chips(*wht) == wh


def test_wrap_around():
    # Can be meaningfully cast to bool
    assert bool(WrapAround.none) is False
    assert bool(WrapAround.x) is True
    assert bool(WrapAround.y) is True
    assert bool(WrapAround.both) is True

    # Bit-operations make sense
    assert bool(WrapAround.both & WrapAround.x) is True
    assert bool(WrapAround.both & WrapAround.y) is True
    assert bool(WrapAround.x & WrapAround.x) is True
    assert bool(WrapAround.x & WrapAround.y) is False
