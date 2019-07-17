# Copyright (c) 2017-2019 The University of Manchester
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import unittest
from spalloc_server.links import Links


class TestMulticastRoutingEntry(unittest.TestCase):
    def test_links_from_vector(self):
        # In all but the last of the following tests we assume we're in a 4x8
        # system.

        # Direct neighbours without wrapping
        self.assertEqual(Links.from_vector((+1, +0)), Links.east)
        self.assertEqual(Links.from_vector((-1, -0)), Links.west)
        self.assertEqual(Links.from_vector((+0, +1)), Links.north)
        self.assertEqual(Links.from_vector((-0, -1)), Links.south)
        self.assertEqual(Links.from_vector((+1, +1)), Links.north_east)
        self.assertEqual(Links.from_vector((-1, -1)), Links.south_west)

        # Direct neighbours with wrapping on X
        self.assertEqual(Links.from_vector((-3, -0)), Links.east)
        self.assertEqual(Links.from_vector((+3, +0)), Links.west)

        # Direct neighbours with wrapping on Y
        self.assertEqual(Links.from_vector((-0, -7)), Links.north)
        self.assertEqual(Links.from_vector((+0, +7)), Links.south)

        # Direct neighbours with wrapping on X & Y
        self.assertEqual(Links.from_vector((-3, +1)), Links.north_east)
        self.assertEqual(Links.from_vector((+3, -1)), Links.south_west)

        self.assertEqual(Links.from_vector((+1, -7)), Links.north_east)
        self.assertEqual(Links.from_vector((-1, +7)), Links.south_west)

        self.assertEqual(Links.from_vector((-3, -7)), Links.north_east)
        self.assertEqual(Links.from_vector((+3, +7)), Links.south_west)

        # Special case: 2xN or Nx2 system (N >= 2) "spiralling" around the Z
        # axis.
        self.assertEqual(Links.from_vector((1, -1)), Links.south_west)
        self.assertEqual(Links.from_vector((-1, 1)), Links.north_east)

    def test_links_to_vector(self):
        self.assertEqual((+1, +0), Links.east.to_vector())
        self.assertEqual((-1, -0), Links.west.to_vector())
        self.assertEqual((+0, +1), Links.north.to_vector())
        self.assertEqual((-0, -1), Links.south.to_vector())
        self.assertEqual((+1, +1), Links.north_east.to_vector())
        self.assertEqual((-1, -1), Links.south_west.to_vector())

    def test_links_opposite(self):
        self.assertEqual(Links.north.opposite, Links.south)
        self.assertEqual(Links.north_east.opposite, Links.south_west)
        self.assertEqual(Links.east.opposite, Links.west)
        self.assertEqual(Links.south.opposite, Links.north)
        self.assertEqual(Links.south_west.opposite, Links.north_east)
        self.assertEqual(Links.west.opposite, Links.east)
