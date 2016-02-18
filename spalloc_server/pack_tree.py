"""An algorithm/datastructure for allocating/packing rectangles into a fixed 2D
space.

This algorithm is used to allocate triads of boards in SpiNNaker systems but is
otherwise a relatively generic 2D packing algorithm.
"""


from spalloc_server.area_to_rect import area_to_rect


class PackTree(object):
    r"""A tree-based datastructure for allocating/packing rectangles into a
    fixed 2D space.

    This tree structure is used to allocate/pack rectangular subregions of
    SpiNNaker machine in a fashion similar to `this lightmap packing algorithm
    <http://www.blackpawn.com/texts/lightmaps/default.html>`_. It is certainly
    not the most efficient or flexible packing algorithm available but due to
    time constraints it is ideal due to its simplicity.
    """

    def __init__(self, x, y, width, height):
        """Defines a region of which may be allocated and/or divided in two.

        Parameters
        ----------
        x, y : int
            The (absolute) location of the bottom left corner of the region.
        width, height : int
            The dimensions of the region.
        """

        self.x = x
        self.y = y
        self.width = width
        self.height = height

        # Has this region been allocated?
        self.allocated = False

        # Either None (leaf node) or a pair (PackTree, PackTree) giving the
        # children of this node.
        self.children = None

    def __contains__(self, xy):
        """Test whether a coordinate is inside this region."""
        x, y = xy
        return (self.x <= x < (self.x + self.width) and
                self.y <= y < (self.y + self.height))

    def hsplit(self, y):
        """Split this node along the X axis.

        The bottom half of split will be just before the "y" position.

        ::

               +-----------+
               |           |
            ---+-----------+---
               |           |
               +-----------+
        """
        assert self.children is None
        assert not self.allocated

        self.children = (PackTree(self.x, self.y,
                                  self.width, (y - self.y)),
                         PackTree(self.x, y,
                                  self.width, self.height - (y - self.y)))

    def vsplit(self, x):
        """Split this node along the Y axis.

        The left half of split will be just before the "x" position.

        ::

                  |
            +-----+-----+
            |     |     |
            |     |     |
            |     |     |
            +-----+-----+
                  |
        """
        assert self.children is None
        assert not self.allocated

        self.children = (PackTree(self.x, self.y,
                                  (x - self.x), self.height),
                         PackTree(x, self.y,
                                  self.width - (x - self.x), self.height))

    def alloc(self, width, height, candidate_filter=None):
        """Attempt to allocate a rectangular region of a specified size.

        Parameters
        ----------
        width, height : int
            The dimensions of the region to attempt to allocate. Must be
            strictly 1x1 or greater.
        candidate_filter : None or function(x, y, w, h) -> bool
            A function which will be called with candidate allocations. If the
            function returns False, the allocation is rejected and the
            allocator will attempt to find another. If the function returns
            True, the allocator will then create the allocation. This function
            may, for example, check that the suggested region is fully
            connected or does not have too many faults.

            If this argument is None (the default) the first candidate
            allocation found will be returned.

        Returns
        -------
        allocation : (x, y) or None
            If the allocation request was met, a tuple giving the position of
            the bottom-left corner of the allocation.

            If the request could not be met, None is returned and no allocation
            is made.
        """
        # If this node is already populated, give up
        if self.allocated:
            return None

        # Allocation simply can't fit fail fast
        if width > self.width or height > self.height:
            return None

        # If this node is split (i.e. has children), try inserting into the
        # children.
        if self.children is not None:
            # Try the smallest child first
            for child in sorted(self.children,
                                key=(lambda c: c.width * c.height)):
                allocation = child.alloc(width, height, candidate_filter)
                if allocation:
                    return allocation
            else:
                # No child could fit the allocation, fail
                return None

        # This node is an empty leaf with enough room. Try and find a corner
        # into which this allocation can fit which is acceptable to the caller.
        tried = set()
        for x, y in ((self.x + x, self.y + y)
                     for x in (0, self.width - width)
                     for y in (0, self.height - height)):
            if ((x, y) not in tried and
                    (candidate_filter is None or
                     candidate_filter(x, y, width, height))):
                break
            tried.add((x, y))
        else:
            # No acceptable subregion could be found, give up.
            return None

        # If the region fits exactly, just become allocated
        if width == self.width and height == self.height:
            self.allocated = True
            assert x == self.x and y == self.y  # Sanity check...
            return (self.x, self.y)

        # The region does not fit exactly, slice this region up.
        dw = self.width - width
        dh = self.height - height

        # Split this region along the axis  which preserves the largest free
        # space
        if dh > dw:
            self.hsplit(y if y != self.y else y + height)
            child = (self.children[0]
                     if y == self.y else
                     self.children[1])
        else:
            self.vsplit(x if x != self.x else x + width)
            child = (self.children[0]
                     if x == self.x else
                     self.children[1])

        # If the child region is not exactly the right size, split that one
        # last time. Note that we do this explicitly rather than recursively to
        # save additional calls to the candidate filter.
        if child.width != width:
            child.vsplit(x if x != child.x else child.x + width)
            grandchild = (child.children[0]
                          if x == child.x else
                          child.children[1])
            grandchild.allocated = True
            return (grandchild.x, grandchild.y)
        elif child.height != height:
            child.hsplit(y if y != child.y else child.y + height)
            grandchild = (child.children[0]
                          if y == child.y else
                          child.children[1])
            grandchild.allocated = True
            return (grandchild.x, grandchild.y)
        else:
            child.allocated = True
            return (child.x, child.y)

    def alloc_area(self, area, min_ratio=0.0, candidate_filter=None):
        """Attempt to allocate a rectangular region with at least the specified
        area which is 'at least as square' as the specified aspect ratio.

        Parameters
        ----------
        area : int
            The *minimum* area to allocate, must be at least 1.
        min_ratio : float
            The aspect ratio which the allocated region must be 'at least as
            square as'. Set to 0.0 for any allowable shape.
        candidate_filter : None or function(x, y, w, h) -> bool
            A function which will be called with candidate allocations. If the
            function returns False, the allocation is rejected and the
            allocator will attempt to find another. If the function returns
            True, the allocator will then create the allocation. This function
            may, for example, check that the suggested region is fully
            connected or does not have too many faults.

            If this argument is None (the default) the first candidate
            allocation found will be returned.

        Returns
        -------
        allocation : (x, y, w, h) or None
            If the allocation request was met, a tuple giving the position of
            the bottom-left corner, width and height of the allocation is
            returned.

            If the request could not be met, None is returned and no allocation
            is made.
        """
        # If this node is already populated, give up
        if self.allocated:
            return None

        # Allocation simply can't fit fail fast
        if area > self.width * self.height:
            return None

        # If this node is split (i.e. has children), try inserting into the
        # children.
        if self.children is not None:
            # Try the smallest child first
            for child in sorted(self.children,
                                key=(lambda c: c.width * c.height)):
                allocation = child.alloc_area(area, min_ratio,
                                              candidate_filter)
                if allocation:
                    return allocation
            else:
                # No child could fit the allocation, fail
                return None

        # This is a child node, try to work out a suitable size for the
        # allocation if possible
        rect = area_to_rect(area, self.width, self.height, min_ratio)
        if not rect:
            return None

        # Try allocating that size
        width, height = rect
        allocation = self.alloc(width, height, candidate_filter)
        if allocation:
            x, y = allocation
            return (x, y, width, height)
        else:
            return None

    def request(self, x, y):
        """Request the allocation of a specific 1x1 block.

        This function may be useful when, e.g., specific boards are required
        for testing.

        Returns
        -------
        allocation : (x, y) or None
            If the request request was met, the coordinates passed in are
            returned.

            If the request could not be met, None is returned and no allocation
            is made.
        """
        # Is the requested location in this region? If not, there's nothing we
        # can do.
        if (x, y) not in self:
            return None

        # If this node is not a leaf not we can't allocate anything. Find the
        # child which contains the requested location.
        if self.children:
            return (self.children[0].request(x, y) or
                    self.children[1].request(x, y))

        # We are a leaf containing the requested point. If we're already
        # allocated there's nothing we can do.
        if self.allocated:
            return None

        # If this node a 1x1 region just allocate itself
        if self.width == 1 and self.height == 1:
            self.allocated = True
            return (self.x, self.y)

        # At this point the requested point is somewhere in this region so we
        # must divide it up such that we can allocate a 1x1 piece. For example
        # if trying to divvy-up the space to fit something we look for the side
        # with the greatest amount of space on it to divide on first. This
        # procedure is then used recursively to divvy the world up until we
        # have a 1x1 allocation.
        #
        #     +---------------+
        #     |         a     |
        #     |<---l--->#<-r->|
        #     |         ^     |
        #     |         b     |
        #     |         v     |
        #     +---------------+
        l = x - self.x
        r = (self.x + self.width) - x - 1
        a = (self.y + self.height) - y - 1
        b = y - self.y

        largest = max(l, r, a, b)

        if l == largest:
            self.vsplit(x=x)
            return self.request(x, y)
        elif r == largest:
            self.vsplit(x=x + 1)
            return self.request(x, y)
        elif a == largest:
            self.hsplit(y=y + 1)
            return self.request(x, y)
        else:  # b == largest
            self.hsplit(y=y)
            return self.request(x, y)

    def free(self, x, y):
        """Free a previous allocation, allowing the space to be reused.

        Parameters
        ----------
        x, y : int
            The bottom-left corner of the allocation.
        """
        # If the region to be freed is this one, do so (but only if it is a
        # leaf!)
        if self.children is None and x == self.x and y == self.y:
            if not self.allocated:
                raise FreeError(
                    "Cannot free non-allocated region {}, {}.".format(x, y))
            self.allocated = False
            return

        # The region to be freed is not this one, try the children
        for child in self.children or tuple():
            if (x, y) in child:
                # The child contains the region to be freed, do so
                child.free(x, y)

                # If both of our children are now empty leaves, we can remove
                # them and make this node a leaf.
                if all(not c.allocated and c.children is None
                       for c in self.children):
                    self.children = None

                return
        else:
            # No child contains the location to be freed. Crash out!
            raise FreeError(
                "Cannot free {}, {} which is outside the region.".format(x, y))


class FreeError(Exception):
    """Thrown when attempting to free a region fails."""
    pass
