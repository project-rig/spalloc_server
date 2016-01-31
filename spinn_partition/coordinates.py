"""Utilities for working with board/triad coordinates.

Boards are addressed by tuples (x, y, z) where (x, y) is the coordinate of the
triad the board belongs to and z is the index of the board within the triad as
follows::

     ___
    / 2 \___
    \___/ 1 \
    / 0 \___/
    \___/

These tile along the X axis like this::

     ___     ___     ___     ___
    / 2 \___/ 2 \___/ 2 \___/ 2 \___
    \___/ 1 \___/ 1 \___/ 1 \___/ 1 \
    / 0 \___/ 0 \___/ 0 \___/ 0 \___/
    \___/   \___/   \___/   \___/

And then along the Y axis like this:

     ___     ___     ___     ___
    / 2 \___/ 2 \___/ 2 \___/ 2 \___
    \___/ 1 \___/ 1 \___/ 1 \___/ 1 \
    / 0 \___/ 0 \___/ 0 \___/ 0 \___/
    \___/ 2 \___/ 2 \___/ 2 \___/ 2 \___
        \___/ 1 \___/ 1 \___/ 1 \___/ 1 \
        / 0 \___/ 0 \___/ 0 \___/ 0 \___/
        \___/ 2 \___/ 2 \___/ 2 \___/ 2 \___
            \___/ 1 \___/ 1 \___/ 1 \___/ 1 \
            / 0 \___/ 0 \___/ 0 \___/ 0 \___/
            \___/   \___/   \___/   \___/
"""

from six import iteritems

from rig.links import Links


"""A lookup from (z, :py:class:`rig.links.Links`) to (dx, dy, dz)."""
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

link_to_vector.update({
    (z + dz, link.opposite): (-dx, -dy, -dz)
    for (z, link), (dx, dy, dz) in iteritems(link_to_vector)
})


def board_down_link(x1, y1, z1, link, width, height):
    """Get the coordinates of the board down the specified link.
    
    Returns
    -------
    x, y, z, wrapped
    """
    dx, dy, dz = link_to_vector[(z1, link)]
    
    x2_ = (x1 + dx)
    y2_ = (y1 + dy)
    
    x2 = x2_ % width
    y2 = y2_ % height
    
    z2 = z1 + dz
    
    wrapped = not (x2_ == x2 and y2_ == y2)
    
    return (x2, y2, z2, wrapped)
