import pytest

from rig.links import Links

from spalloc_server.coordinates import \
    link_to_vector, board_down_link, board_to_chip, chip_to_board, \
    triad_dimensions_to_chips


def test_link_to_vector():
    # ___     ___     ___     ___
    #/ 2 \___/ 2 \___/ 2 \___/ 2 \___
    #\___/ 1 \___/ 1 \___/ 1 \___/ 1 \
    #/ 0 \___/ 0 \___/ 0 \___/ 0 \___/
    #\___/ 2 \___/ 2 \___/ 2 \___/ 2 \___
    #    \___/ 1 \___/ 1 \___/ 1 \___/ 1 \
    #    / 0 \___/ 0 \___/ 0 \___/ 0 \___/
    #    \___/ 2 \___/ 2 \___/ 2 \___/ 2 \___
    #        \___/ 1 \___/ 1 \___/ 1 \___/ 1 \
    #        / 0 \___/ 0 \___/ 0 \___/ 0 \___/
    #        \___/   \___/   \___/   \___/
    
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
                         [# Should add vectors correctly
                          (0, 0, 0, Links.north, 1, 1,
                           0, 0, 2, False),
                          (3, 2, 1, Links.north, 10, 10,
                           4, 3, 0, False),
                          # Should detect wrap-around
                          (9, 8, 1, Links.north_east, 10, 10,
                           0, 8, 2, True),
                          # ...even on 1x1
                          (0, 0, 0, Links.south, 1, 1,
                           0, 0, 1, True),
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
                          ((2, 1, 2), (28, 20)),
                         ])
def test_board_to_chip(bxyz, cxy):
    assert board_to_chip(*bxyz) == cxy


@pytest.mark.parametrize("bxyz,cxy",
                         [((0, 0, 0), (0, 0)),
                          ((0, 0, 1), (8, 4)),
                          ((0, 0, 2), (4, 8)),
                          ((2, 1, 0), (24, 12)),
                          ((2, 1, 1), (32, 16)),
                          ((2, 1, 2), (28, 20)),
                         ])
def test_chip_to_board(bxyz, cxy):
    assert chip_to_board(*cxy) == bxyz

@pytest.mark.parametrize("wht,wh",
                         [((1, 1, False), (16, 16)),
                          ((1, 1, True), (12, 12)),
                          ((2, 1, False), (28, 16)),
                          ((2, 1, True), (24, 12)),
                          ((1, 2, False), (16, 28)),
                          ((1, 2, True), (12, 24)),
                         ])
def test_dimensions_to_chips(wht, wh):
    assert triad_dimensions_to_chips(*wht) == wh
