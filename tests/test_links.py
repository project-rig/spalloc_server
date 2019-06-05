import unittest
from spalloc_server.links import Links


class TestMulticastRoutingEntry(unittest.TestCase):
    def test_links_from_vector(self):
        # In all but the last of the following tests we assume we're in a 4x8
        # system.

        # Direct neighbours without wrapping
        self.assertEquals(Links.from_vector((+1, +0)), Links.east)
        self.assertEquals(Links.from_vector((-1, -0)), Links.west)
        self.assertEquals(Links.from_vector((+0, +1)), Links.north)
        self.assertEquals(Links.from_vector((-0, -1)), Links.south)
        self.assertEquals(Links.from_vector((+1, +1)), Links.north_east)
        self.assertEquals(Links.from_vector((-1, -1)), Links.south_west)

        # Direct neighbours with wrapping on X
        self.assertEquals(Links.from_vector((-3, -0)), Links.east)
        self.assertEquals(Links.from_vector((+3, +0)), Links.west)

        # Direct neighbours with wrapping on Y
        self.assertEquals(Links.from_vector((-0, -7)), Links.north)
        self.assertEquals(Links.from_vector((+0, +7)), Links.south)

        # Direct neighbours with wrapping on X & Y
        self.assertEquals(Links.from_vector((-3, +1)), Links.north_east)
        self.assertEquals(Links.from_vector((+3, -1)), Links.south_west)

        self.assertEquals(Links.from_vector((+1, -7)), Links.north_east)
        self.assertEquals(Links.from_vector((-1, +7)), Links.south_west)

        self.assertEquals(Links.from_vector((-3, -7)), Links.north_east)
        self.assertEquals(Links.from_vector((+3, +7)), Links.south_west)

        # Special case: 2xN or Nx2 system (N >= 2) "spiralling" around the Z
        # axis.
        self.assertEquals(Links.from_vector((1, -1)), Links.south_west)
        self.assertEquals(Links.from_vector((-1, 1)), Links.north_east)

    def test_links_to_vector(self):
        self.assertEquals((+1, +0), Links.east.to_vector())
        self.assertEquals((-1, -0), Links.west.to_vector())
        self.assertEquals((+0, +1), Links.north.to_vector())
        self.assertEquals((-0, -1), Links.south.to_vector())
        self.assertEquals((+1, +1), Links.north_east.to_vector())
        self.assertEquals((-1, -1), Links.south_west.to_vector())

    def test_links_opposite(self):
        self.assertEquals(Links.north.opposite, Links.south)
        self.assertEquals(Links.north_east.opposite, Links.south_west)
        self.assertEquals(Links.east.opposite, Links.west)
        self.assertEquals(Links.south.opposite, Links.north)
        self.assertEquals(Links.south_west.opposite, Links.north_east)
        self.assertEquals(Links.west.opposite, Links.east)
