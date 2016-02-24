r"""Utilities for working with board/triad coordinates.

This software assumes that all machines provided to it are interconnected in a
valid (subset of) a hexagonal torus topology. Boards locations are expressed
in one of several forms depending on circumstance.

* **Board coordinates** ``(x, y, z)`` giving the logical location of the board
  within a hexagonal torus configuration. This is the most frequently used
  coordinate system used by this software.
* **Physical coordinates** ``(cabinet, frame, board)`` giving the physical
  location of a board in a cabinet. Generally only used when dealing with board
  power management.
* **Ethernet chip coordinates** ``(x, y)`` giving the *chip* coordinates of the
  Ethernet connected chip at the bottom-left coordinate of a SpiNNaker board.
  Generally only used when relating information to a client.

To deal with these coordinate systems a selection of utility functions are
provided in the :py:mod:`spalloc_server.coordinates`.

Board coordinates
`````````````````

Board coordinates are given as a tuple ``(x, y, z)``.

Systems of SpiNNaker boards are defined in terms of 'triads' of boards. The
figure below shows a single triad. The 'z' part of a board coordinate comes
from the index of the board within its triad and are numbered as follows::

     ___
    / 2 \___
    \___/ 1 \
    / 0 \___/
    \___/

Larger systems are defined by replicating this pattern of triads. Triads are
indexed along the X axis as follows::

     ___     ___     ___     ___
    / 2 \___/ 2 \___/ 2 \___/ 2 \___
    \___/ 1 \___/ 1 \___/ 1 \___/ 1 \
    / 0 \___/ 0 \___/ 0 \___/ 0 \___/
    \___/   \___/   \___/   \___/

        0       1       2       3

And then along the Y axis thus::

     ___     ___     ___     ___
    / 2 \___/ 2 \___/ 2 \___/ 2 \___
    \___/ 1 \___/ 1 \___/ 1 \___/ 1 \   3
    / 0 \___/ 0 \___/ 0 \___/ 0 \___/
    \___/ 2 \___/ 2 \___/ 2 \___/ 2 \___
        \___/ 1 \___/ 1 \___/ 1 \___/ 1 \   2
        / 0 \___/ 0 \___/ 0 \___/ 0 \___/
        \___/ 2 \___/ 2 \___/ 2 \___/ 2 \___
            \___/ 1 \___/ 1 \___/ 1 \___/ 1 \   1
            / 0 \___/ 0 \___/ 0 \___/ 0 \___/
            \___/ 2 \___/ 2 \___/ 2 \___/ 2 \___
                \___/ 1 \___/ 1 \___/ 1 \___/ 1 \   0
                / 0 \___/ 0 \___/ 0 \___/ 0 \___/
                \___/   \___/   \___/   \___/

                    0       1       2       3

Physical coordinates
````````````````````

Physical coordinates are given as a tuple ``(cabinet, frame, board)``.

Physical coordinates give the positions of boards within a set of cabinets
containing several frames containing several boards. These are indexed as
illustrated below, *starting from the top-right corner*::

              2             1                0
    Cabinet --+-------------+----------------+
              |             |                |
    +-------------+  +-------------+  +-------------+    Frame
    |             |  |             |  |             |      |
    | +---------+ |  | +---------+ |  | +---------+ |      |
    | | : : : : | |  | | : : : : | |  | | : : : : |--------+ 0
    | | : : : : | |  | | : : : : | |  | | : : : : | |      |
    | +---------+ |  | +---------+ |  | +---------+ |      |
    | | : : : : | |  | | : : : : | |  | | : : : : |--------+ 1
    | | : : : : | |  | | : : : : | |  | | : : : : | |      |
    | +---------+ |  | +---------+ |  | +---------+ |      |
    | | : : : : | |  | | : : : : | |  | | : : : : |--------+ 2
    | | : : : : | |  | | : : : : | |  | | : : : : | |      |
    | +---------+ |  | +---------+ |  | +---------+ |      |
    | | : : : : | |  | | : : : : | |  | | : : : : |--------+ 3
    | | : : : : | |  | | : : : : | |  | | : : : : | |
    | +---------+ |  | +|-|-|-|-|+ |  | +---------+ |
    |             |  |  | | | | |  |  |             |
    +-------------+  +--|-|-|-|-|--+  +-------------+
                        | | | | |
             Board -----+-+-+-+-+
                        4 3 2 1 0

A mapping from board coordinates to physical coordinates is supplied by the
user and is unique to the machine being built. A tool such as SpiNNer_ may be
used to generate such mappings.

.. _SpiNNer: https://github.com/SpiNNakerManchester/SpiNNer


Ethernet chip coordinates
`````````````````````````

Ethernet chip coordinates are given as a tuple ``(x, y)``.

Ethernet chip coordinates give the chip coordinates of Ethernet connected chips
at the bottom-left corner of SpiNNaker boards.

Utilities
`````````

The following utilities are provided for working with the above coordinate
systems.
"""

from enum import IntEnum

from six import iteritems

from rig.links import Links
from rig.geometry import spinn5_local_eth_coord


link_to_vector = {
    (0, Links.north): (0, 0, 2),
    (0, Links.north_east): (0, 0, 1),
    (0, Links.east): (0, -1, 2),

    (1, Links.north): (1, 1, -1),
    (1, Links.north_east): (1, 0, 1),
    (1, Links.east): (1, 0, -1),

    (2, Links.north): (0, 1, -1),
    (2, Links.north_east): (1, 1, -2),
    (2, Links.east): (0, 0, -1),
}
"""A lookup from (z, :py:class:`rig.links.Links`) to (dx, dy, dz)."""

link_to_vector.update({
    (z + dz, link.opposite): (-dx, -dy, -dz)
    for (z, link), (dx, dy, dz) in iteritems(link_to_vector)
})


def board_down_link(x1, y1, z1, link, width, height):
    """Get the coordinates of the board down the specified link.

    Parameters
    ----------
    x1, y1, z1 : int
        The board coordinates from which a link will be traversed.
    link : :py:class:`rig.links.Link`
        The link to follow.
    width, height : int
        The dimensions of the system in triads.

    Returns
    -------
    x, y, z : int
        The coordinates of the board down the specified link.
    wrapped : :py:class:`.WrapAround`
        In what way did we wrap-around when following that link?
    """
    dx, dy, dz = link_to_vector[(z1, link)]

    x2_ = (x1 + dx)
    y2_ = (y1 + dy)

    x2 = x2_ % width
    y2 = y2_ % height

    z2 = z1 + dz

    wrapped = WrapAround((WrapAround.x if x2_ != x2 else 0) |
                         (WrapAround.y if y2_ != y2 else 0))

    return (x2, y2, z2, wrapped)


def board_to_chip(x, y, z):
    """Convert a board coordinate into a chip coordinate.

    Assumes a regular torus composed of SpiNN-5 boards.

    Parameters
    ----------
    x, y, z : int
        Board coordinates.

    Returns
    -------
    x, y : int
        Chip coordinates.
    """

    x *= 12
    y *= 12

    if z == 1:
        x += 8
        y += 4
    elif z == 2:
        x += 4
        y += 8

    return (x, y)


def chip_to_board(x, y, w, h):
    """Convert a chip coordinate into a board coordinate.

    Assumes a regular torus composed of SpiNN-5 boards.

    Parameters
    ----------
    x, y : int
        Chip coordinates.
    w, h : int
        Dimensions of the system, in chips.

    Returns
    -------
    x, y, z : int
        Board coordinates.
    """
    # Convert to coordinate of chip at the bottom-left-corner of the board
    # Workaround: spinn5_chip_coord (until at least Rig 0.13.2) returns
    # numpy integer types which are not JSON serialiseable.
    x, y = map(int, spinn5_local_eth_coord(x, y, w, h))

    # The coordinates of the chip within its triad
    tx = x % 12
    ty = y % 12

    x //= 12
    y //= 12

    if tx == ty == 0:
        z = 0
    elif tx == 8 and ty == 4:
        z = 1
    elif tx == 4 and ty == 8:
        z = 2
    else:  # pragma: no cover
        assert False

    return (x, y, z)


def triad_dimensions_to_chips(w, h, torus):
    """Convert the dimensions of a system from numbers of triads to numbers of
    chips in the underlying network.

    Assumes a regular torus composed of SpiNN-5 boards.

    Parameters
    ----------
    w, h : int
        Dimensions of the system in triads.
    torus : :py:class`.WrapAround`
        What wrap-around connections are present?

    Returns
    -------
    w, h : int
        Dimensions of the SpiNNaker chip network in the specified machine, e.g.
        for booting.
    """

    w *= 12
    h *= 12

    # If not a torus topology, the pieces of boards which would wrap-around and
    # "tuck in" to the opposing sides of the network will be left poking out.
    # Compensate for this.
    if not (torus & WrapAround.x):
        w += 4
    if not (torus & WrapAround.y):
        h += 4

    return (w, h)


class WrapAround(IntEnum):
    """Defines what type of wrap-around links a torus has, if any.

    Values chosen have the following useful properties::

        >>> # Can be meaningfully cast to bool
        >>> assert bool(WrapAround.none) is False
        >>> assert bool(WrapAround.x) is True
        >>> assert bool(WrapAround.y) is True
        >>> assert bool(WrapAround.both) is True

        >>> # Bit-operations make sense
        >>> assert bool(WrapAround.both & WrapAround.x) is True
        >>> assert bool(WrapAround.both & WrapAround.y) is True
        >>> assert bool(WrapAround.x & WrapAround.x) is True
        >>> assert bool(WrapAround.x & WrapAround.y) is False
    """

    none = 0b00
    """No wrap-around links."""

    x = 0b01
    """Has wrap around links around X-axis."""

    y = 0b10
    """Has wrap around links around Y-axis."""

    both = 0b11
    """Has wrap around links on X and Y axes."""
