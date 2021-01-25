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

""" A data structure representing links in a SpiNNaker machine.
"""
from enum import IntEnum


class Links(IntEnum):
    """ Enumeration of links from a SpiNNaker chip.

    Note that the numbers chosen have two useful properties:

    * The integer values assigned are chosen to match the numbers used to\
      identify the links in the low-level software API and hardware registers.
    * The links are ordered consecutively in anticlockwise order meaning the\
      opposite link is `(link+3)%6`.

    Attributes
    ----------
    east = 0
    north_east = 1
    north = 2
    west = 3
    south_west = 4
    south = 5
    """

    east = 0
    north_east = 1
    north = 2
    west = 3
    south_west = 4
    south = 5

    @classmethod
    def from_vector(cls, vector):
        """ Given a vector from one node to a neighbour, get the link\
            direction.

        Note that any vector whose magnitude in any given dimension is greater\
        than 1 will be assumed to use a machine's wrap-around links.

        Note that this method assumes a system larger than 2x2. If a 2x2, 2xN\
        or Nx2 (for N > 2) system is provided the link selected will\
        arbitrarily favour either wrap-around or non-wrap-around links. This\
        function is not meaningful for 1x1 systems.

        :param vector: The vector from one node to its logical neighbour.
        :type vector: pair of ints
        :return: The link direction to travel in the direction indicated by\
            the vector.
        :rtype: member of Links enum
        """
        x, y = vector

        # Vectors must be mapped to a form (x, y) where x and y are -1, 0 or 1.
        # When a vector is between two neighbouring nodes which are not
        # connected by a wrap-around link this is already the case. When
        # wrapping around on a given dimension, however, the element of the
        # vector corresponding with that dimension will be outside this range.
        #
        # For example, in a 4x4 system, the vector between nodes (3, 1) and (0,
        # 1) comes out as (-3, 0). In this case we wrap around on the X axis
        # going from the right-hand-side to the left-hand-side. The logical
        # direction vector should just be (1, 0) since we're logically
        # travelling East. Notice that the sign of the wrapped-around element
        # is flipped and the magnitude forced to 1.
        if abs(x) > 1:
            x = -1 if x > 0 else 1
        if abs(y) > 1:
            y = -1 if y > 0 else 1

        lookup, _ = _LinksHelper.get_lookups()
        return lookup[(x, y)]  # pylint: disable=unsubscriptable-object

    def to_vector(self):
        """ Given a link direction, return the equivalent vector.

        :return: The vector for this link direction.
        :rtype: pair of int
        """
        _, lookup = _LinksHelper.get_lookups()
        return lookup[self]  # pylint: disable=unsubscriptable-object

    @property
    def opposite(self):
        """ Get the opposite link to the one given.
        """
        return Links((self + 3) % 6)


class _LinksHelper(object):
    """ Builds the bidirectional map between directions and links. Holds a\
        cache of it internally.
    """
    _link_direction_lookup = None
    _direction_link_lookup = None

    @classmethod
    def get_lookups(cls):
        """ Get (and possibly build) the pair of maps.

        :return: The map from directions to links, and from links to\
            directions.
        :rtype: pair of maps
        """
        if _LinksHelper._link_direction_lookup is None:
            ldl = _LinksHelper._link_direction_lookup = {
                (+1, +0): Links.east,
                (-1, +0): Links.west,
                (+0, +1): Links.north,
                (+0, -1): Links.south,
                (+1, +1): Links.north_east,
                (-1, -1): Links.south_west}
            _LinksHelper._direction_link_lookup = {
                l: v for (v, l) in ldl.items()}

            # Special case: Lets assume we've got a 2xN or Nx2 system (N >= 2)
            # where we can "spiral" around the Z axis to reach places which
            # normally wouldn't be accessible.
            #
            # (x+1, 0) <-> (x+0, 1)        (1, y+0) <-> (0, y+1)
            #           /                        |   |   |
            #     --+--/+---+--                  +---+---+
            #       | . |   |                    | . |   |/
            #     --+---+---+--                  /---+---/
            #       |   | . |                   /|   | . |
            #     --+---+/--+--                  +---+---+
            #           /                        |   |   |
            ldl[(+1, -1)] = Links.south_west
            ldl[(-1, +1)] = Links.north_east
        return _LinksHelper._link_direction_lookup, \
            _LinksHelper._direction_link_lookup
