"""Utility functions for working out sensible sizes of machine to allocate
given a minimum number of boards and worst-case aspect ratio.
"""

from math import sqrt, ceil


def area_to_rect(area, bound_width, bound_height, min_ratio=0.0):
    """Given an area requirement, select a rectangular subregion of the given
    width and height bounds whose aspect ratio (when rotated into a landscape
    orientation) is at least that specified.

    Parameters
    ----------
    area : int
        Lower-bound on area requirement.
    bound_width : int
        Upper-bound on the width of the rectangle.
    bound_height : int
        Upper-bound on the height of the rectangle.
    min_ratio : float
        Require that the area of the selected rectangle be 'at least as square'
        as the supplied aspect ratio (h/w). By convention this is specified in
        the range 0.0 <= min_ratio <= 1.0 but values > 1.0 are also accepted
        (and are internally flipped to the conventional range).

    Returns
    -------
    (width, height) or None
        The dimensions of a rectangle meeting the request or None if no
        such rectangle is possible. When sensible and possible, the rectangle
        allocated will have the same width and/or height as the bounds.
    """
    # Basic sanity check of inputs
    if area <= 0 or bound_width <= 0 or bound_height <= 0:
        raise ValueError("Cannot get negative- or zero-sized allocations.")
    if min_ratio < 0.0:
        raise ValueError("Aspect ratio cannot be negative.")

    # Immediately fail for impossible requests
    if area > bound_width * bound_height:
        return None

    # Rotate aspect ratio to landscape form
    if min_ratio > 1.0:
        min_ratio = 1.0 / min_ratio

    # Get 'ideal' dimensions
    w, h = squarest_rectangle(area)

    # If the aspect ratio of the ideal rectangle is too wide, find a rectangle
    # which over-allocates area which meets the aspect ratio requirement.
    if float(h) / float(w) < min_ratio:
        w, h = rectangle_with_aspect_ratio(area, min_ratio)

    # Rotate the rectangle such that it is in the opposite orientation to the
    # bounding box. This will ensure that if the rectangle selected will leave
    # the largest rectangular region of the bounding box unfilled as possible.
    if bound_width > bound_height and w > h:
        w, h = h, w

    # If everything fits, we're good to go
    if w <= bound_width and h <= bound_height:
        return (w, h)

    # If the selected rectangle does not fit within the bounding rectangle,
    # 'squish' it to make it fit
    if w > bound_width:
        w = bound_width
        h = int(ceil(float(area) / float(w)))
    elif h > bound_height:  # pragma: no branch
        h = bound_height
        w = int(ceil(float(area) / float(h)))

    # If squishing didn't violate the aspect ratio requirement, we're good.
    # Note: We know that the requested area fits within the bounds of the
    # system and thus squishing can never result in something which doesn't
    # fit.
    if float(min(w, h)) / float(max(w, h)) >= min_ratio:
        return (w, h)
    else:
        return None


def squarest_rectangle(area):
    """Given a specific area, calculate the squarest possible rectangle with
    exactly that area, preferring landscape rectangles.

    Returns
    -------
    (w, h)
        Width and height of a rectangle with the area specified where w and h
        are as similar as possible and w >= h.
    """
    assert area >= 0

    # Special case
    if area == 0:
        return (0, 0)

    # Find the largest pair of factors to discover the squarest rectangle
    # possible.
    for h in reversed(  # pragma: no branch
            range(1, int(sqrt(area)) + 1)):
        if area % h == 0:
            break

    w = area // h

    return (w, h)


def rectangle_with_aspect_ratio(area, ratio):
    """Return a (landscape) rectangle with at least the specified area and
    aspect ratio.

    Note that the returned rectangles are not at all guaranteed to be the
    'squarest' possible, just greater than the specified ratio. This is
    intended to generate alternatives to the very wide rectangles produced by
    squarest_rectangle when prime (or 'nearly prime') numbers of boards are
    requested.

    Parameters
    ----------
    area : int
        Minimum area the rectangle must cover.
    ratio : float
        The minimum aspect ratio (h/w) the rectangle should have. Must be in
        the range 0.0 < ratio <= 1.0.

    Returns
    -------
    (width, height)
        The dimensions of the rectangle selected. Rectangles are always square
        or landscape (i.e. w >= h).
    """
    assert area >= 0
    assert 0.0 < ratio <= 1.0

    # Special case
    if area <= 0:
        return (0, 0)

    # Work out the non-integer width that would get exactly the required area
    w = sqrt(float(area) / float(ratio))

    # Round up the corresponding non-integer height to get the height of an
    # integer-sized rectangle slightly larger than we really need.
    h = int(ceil(float(area) / w))

    # Find the minimum integer width for that height
    w = int(ceil(float(area) / float(h)))

    # Clamp aspect ratio at 1.0
    w = max(w, h)

    return (w, h)
