"""Algorithm/data structure for allocating boards in SpiNNaker machines at
the granularity of individual SpiNNaker boards and with awareness of the
functionality of a machine.
"""

from enum import Enum

from collections import deque

from math import ceil

from six import next

from rig.links import Links

from spalloc_server.pack_tree import PackTree
from spalloc_server.area_to_rect import area_to_rect
from spalloc_server.coordinates import board_down_link, WrapAround


class Allocator(object):
    """This object allows high-level allocation of SpiNNaker boards from a
    larger, possibly faulty, toroidal machine.

    Internally this object uses a
    :py:class:`spalloc_server.pack_tree.PackTree` to allocate
    rectangular blocks of triads in a machine. A :py:class:`._CandidateFilter`
    to restrict the allocations made by
    :py:class:`~spalloc_server.pack_tree.PackTree` to those which match
    the needs of the user (e.g. specific connectivity requirements).

    The allocator can allocate either rectangular blocks of triads or
    individual boards. When allocating individual boards, the allocator
    allocates a 1x1 triad block from the
    :py:class:`~spalloc_server.pack_tree.PackTree` and returns one of
    the boards from that block. Subsequent single-board allocations will use up
    spare boards left in triads allocated for single-board allocations before
    allocating new 1x1 triads.
    """

    def __init__(self, width, height, dead_boards=None, dead_links=None,
                 next_id=1):
        """
        Parameters
        ----------
        width, height : int
            Dimensions of the machine in triads.
        dead_boards : set([(x, y, z), ...])
            The set of boards which are dead and which must not be allocated.
        dead_links : set([(x, y, z, :py:class:`rig.links.Links`), ...])
            The set of links leaving boards which are known not to be working.
            Connections to boards in the set dead_boards are assumed to be
            dead and need not be included in this list. Note that both link
            directions must be flagged as dead (if the link is bidirectionally
            down).
        next_id : int
            The ID of the next allocation to be made.
        """
        self.width = width
        self.height = height
        self.dead_boards = dead_boards if dead_boards is not None else set()
        self.dead_links = dead_links if dead_links is not None else set()

        # Unique IDs are assigned to every new allocation. The next ID to be
        # allocated.
        self.next_id = next_id

        # A 2D tree at the granularity of triads used for board allocation.
        self.pack_tree = PackTree(0, 0, width, height)

        # Provides a lookup from (live) allocation IDs to the type of
        # allocation.
        self.allocation_types = {}

        # Lookup from allocation IDs to the bottom-left board in the allocation
        self.allocation_board = {}

        # Since we cannot allocate individual boards in the pack_tree, whenever
        # an individual board is requested a whole triad may be allocated and
        # one of the boards from the triad returned. This dictionary records
        # what triads have been allocated like this and which boards are
        # unused. These may then be used for future single-board allocations
        # rather than allocating another whole triad.

        # A dictionary containing any triads used for allocating individual
        # boards which still have some free and working boards.
        # {(x, y): [z, ...], ...}
        self.single_board_triads = {}

        # When all the boards in a triad in single_board_triads are used up the
        # triad is removed from that dictionary and placed into the set below.
        self.full_single_board_triads = set()

    def _alloc_triads_possible(self, width, height, max_dead_boards=None,
                               max_dead_links=None, require_torus=False,
                               min_ratio=0.0):
        """Is it guaranteed that the specified allocation *could* succeed if
        enough of the machine is free?

        This function may be conservative. If the specified request would fail
        when no resources have been allocated, we return False, even if some
        circumstances the allocation may succeed. For example, if one board in
        each of the four corners of the machine is dead, no allocation with
        max_dead_boards==0 can succeed when the machine is empty but may
        succeed if some other allocation has taken place.

        Parameters
        ----------
        width, height : int
            The size of the block to allocate, in triads.
        max_dead_boards : int or None
            The maximum number of broken or unreachable boards to allow in the
            allocated region. If None, any number of dead boards is permitted,
            as long as the board on the bottom-left corner is alive (Default:
            None).
        max_dead_links : int or None
            The maximum number of broken links allow in the allocated region.
            When require_torus is True this includes wrap-around links,
            otherwise peripheral links are not counted.  If None, any number of
            broken links is allowed. (Default: None).
        require_torus : bool
            If True, only allocate blocks with torus connectivity. In general
            this will only succeed for requests to allocate an entire machine
            (when the machine is otherwise not in use!). (Default: False)
        min_ratio : float
            Ignored.

        Returns
        -------
        bool

        See Also
        --------
        alloc_possible : The (public) wrapper which also supports checking
                         triad allocations.
        """
        # If too big, we can't fit
        if width > self.width or height > self.height:
            return False

        # Can't be a non-machine
        if width <= 0 or height <= 0:
            return False

        # If torus connectivity is required, we must be *exactly* the right
        # size otherwise we can't help...
        if require_torus and (width != self.width or height != self.height):
            return False

        # Test to see whether the allocation could succeed in the idle machine
        cf = _CandidateFilter(self.width, self.height,
                              self.dead_boards, self.dead_links,
                              max_dead_boards, max_dead_links, require_torus)
        for x, y in set([(0, 0),
                         (self.width - width, 0),
                         (0, self.height - height),
                         (self.width - width, self.height - height)]):
            if cf(x, y, width, height):
                return True

        # No possible allocation could be made...
        return False

    def _alloc_triads(self, width, height, max_dead_boards=None,
                      max_dead_links=None, require_torus=False,
                      min_ratio=0.0):
        """Allocate a rectangular block of triads of interconnected boards.

        Parameters
        ----------
        width, height : int
            The size of the block to allocate, in triads.
        max_dead_boards : int or None
            The maximum number of broken or unreachable boards to allow in the
            allocated region. If None, any number of dead boards is permitted,
            as long as the board on the bottom-left corner is alive (Default:
            None).
        max_dead_links : int or None
            The maximum number of broken links allow in the allocated region.
            When require_torus is True this includes wrap-around links,
            otherwise peripheral links are not counted.  If None, any number of
            broken links is allowed. (Default: None).
        require_torus : bool
            If True, only allocate blocks with torus connectivity. In general
            this will only succeed for requests to allocate an entire machine
            (when the machine is otherwise not in use!). (Default: False)
        min_ratio : float
            Ignored.

        Returns
        -------
        (allocation_id, boards, periphery, torus) or None
            If the allocation was successful a four-tuple is returned. If the
            allocation was not successful None is returned.

            The ``allocation_id`` is an integer which should be used to free
            the allocation with the :py:meth:`.free` method. ``boards`` is a
            set of (x, y, z) tuples giving the locations of the (working)
            boards in the allocation. ``periphery`` is a set of (x, y, z, link)
            tuples giving the links which leave the allocated region. ``torus``
            is True iff at least one torus link is working.

        See Also
        --------
        alloc : The (public) wrapper which also supports allocating individual
        boards.
        """
        # Special case: If a torus is required this is only deliverable when
        # the requirements match the size of the machine exactly.
        if require_torus and (width != self.width or height != self.height):
            return None

        # Sanity check: can't be a non-machine
        if width <= 0 or height <= 0:
            return None

        cf = _CandidateFilter(self.width, self.height,
                              self.dead_boards, self.dead_links,
                              max_dead_boards, max_dead_links, require_torus)

        xy = self.pack_tree.alloc(width, height,
                                  candidate_filter=cf)

        # If a block could not be allocated, fail
        if xy is None:
            return None

        # If a block was allocated, store the allocation
        allocation_id = self.next_id
        self.next_id += 1
        self.allocation_types[allocation_id] = _AllocationType.triads
        x, y = xy
        self.allocation_board[allocation_id] = (x, y, 0)

        return (allocation_id, cf.boards, cf.periphery, cf.torus)

    def _alloc_boards_possible(self, boards, min_ratio=0.0,
                               max_dead_boards=None, max_dead_links=None,
                               require_torus=False):
        """Is it guaranteed that the specified allocation *could* succeed if
        enough of the machine is free?

        This function may be conservative. If the specified request would fail
        when no resources have been allocated, we return False, even if some
        circumstances the allocation may succeed. For example, if one board in
        each of the four corners of the machine is dead, no allocation with
        max_dead_boards==0 can succeed when the machine is empty but may
        succeed if some other allocation has taken place.

        Parameters
        ----------
        boards : int
            The *minimum* number of boards, must be at least 1. Note that if
            only 1 board is required, :py:class:`._alloc_board` would be a more
            appropriate function since this function may return as many as
            three boards when only a single one is requested.
        min_ratio : float
            The aspect ratio which the allocated region must be 'at least as
            square as'. Set to 0.0 for any allowable shape.
        max_dead_boards : int or None
            The maximum number of broken or unreachable boards to allow in the
            allocated region. If None, any number of dead boards is permitted,
            as long as the board on the bottom-left corner is alive (Default:
            None).
        max_dead_links : int or None
            The maximum number of broken links allow in the allocated region.
            When require_torus is True this includes wrap-around links,
            otherwise peripheral links are not counted.  If None, any number of
            broken links is allowed. (Default: None).
        require_torus : bool
            If True, only allocate blocks with torus connectivity. In general
            this will only succeed for requests to allocate an entire machine
            (when the machine is otherwise not in use!). (Default: False)

        Returns
        -------
        bool

        See Also
        --------
        alloc_possible : The (public) wrapper which also supports checking
                         triad allocations.
        """
        # Convert number of boards to number of triads (rounding up...)
        triads = int(ceil(boards / 3.0))

        # Sanity check: can't be a non-machine
        if triads <= 0:
            return False

        # Special case: If a torus is required this is only deliverable when
        # the requirements match the size of the machine exactly.
        if require_torus and (triads != self.width * self.height):
            return False

        # If no region of the right shape can be made, just fail
        wh = area_to_rect(triads, self.width, self.height, min_ratio)
        if wh is None:
            return False
        width, height = wh

        # Test to see whether the allocation could succeed in the idle machine
        cf = _CandidateFilter(self.width, self.height,
                              self.dead_boards, self.dead_links,
                              max_dead_boards, max_dead_links, require_torus,
                              boards)
        for x, y in set([(0, 0),
                         (self.width - width, 0),
                         (0, self.height - height),
                         (self.width - width, self.height - height)]):
            if cf(x, y, width, height):
                return True

        # No possible allocation could be made...
        return False

    def _alloc_boards(self, boards, min_ratio=0.0, max_dead_boards=None,
                      max_dead_links=None, require_torus=False):
        """Allocate a rectangular block of triads with at least the specified
        number of boards which is 'at least as square' as the specified aspect
        ratio.

        Parameters
        ----------
        boards : int
            The *minimum* number of boards, must be at least 1. Note that if
            only 1 board is required, :py:class:`._alloc_board` would be a more
            appropriate function since this function may return as many as
            three boards when only a single one is requested.
        min_ratio : float
            The aspect ratio which the allocated region must be 'at least as
            square as'. Set to 0.0 for any allowable shape.
        max_dead_boards : int or None
            The maximum number of broken or unreachable boards to allow in the
            allocated region. If None, any number of dead boards is permitted,
            as long as the board on the bottom-left corner is alive (Default:
            None).
        max_dead_links : int or None
            The maximum number of broken links allow in the allocated region.
            When require_torus is True this includes wrap-around links,
            otherwise peripheral links are not counted.  If None, any number of
            broken links is allowed. (Default: None).
        require_torus : bool
            If True, only allocate blocks with torus connectivity. In general
            this will only succeed for requests to allocate an entire machine
            (when the machine is otherwise not in use!). (Default: False)

        Returns
        -------
        (allocation_id, boards, periphery, torus) or None
            If the allocation was successful a four-tuple is returned. If the
            allocation was not successful None is returned.

            The ``allocation_id`` is an integer which should be used to free
            the allocation with the :py:meth:`.free` method. ``boards`` is a
            set of (x, y, z) tuples giving the locations of the (working)
            boards in the allocation. ``periphery`` is a set of (x, y, z, link)
            tuples giving the links which leave the allocated region. ``torus``
            is a :py:class:`.WrapAround` value indicating torus connectivity
            when at least one torus may exist.

        See Also
        --------
        alloc : The (public) wrapper which also supports allocating individual
        boards.
        """
        # Convert number of boards to number of triads (rounding up...)
        triads = int(ceil(boards / 3.0))

        # Sanity check: can't be a non-machine
        if triads <= 0:
            return None

        # Special case: If a torus is required this is only deliverable when
        # the requirements match the size of the machine exactly.
        if require_torus and (triads != self.width * self.height):
            return None

        cf = _CandidateFilter(self.width, self.height,
                              self.dead_boards, self.dead_links,
                              max_dead_boards, max_dead_links, require_torus,
                              boards)

        xywh = self.pack_tree.alloc_area(triads, min_ratio,
                                         candidate_filter=cf)

        # If a block could not be allocated, fail
        if xywh is None:
            return None

        # If a block was allocated, store the allocation
        allocation_id = self.next_id
        self.next_id += 1
        self.allocation_types[allocation_id] = _AllocationType.triads
        x, y, w, h = xywh
        self.allocation_board[allocation_id] = (x, y, 0)

        return (allocation_id, cf.boards, cf.periphery, cf.torus)

    def _alloc_board_possible(self, x=None, y=None, z=None,
                              max_dead_boards=None, max_dead_links=None,
                              require_torus=False, min_ratio=0.0):
        """Is it guaranteed that the specified allocation *could* succeed if
        enough of the machine is free?

        Parameters
        ----------
        x, y, z : ints or None
            If specified, requests a specific board.
        max_dead_boards : int or None
            Ignored.
        max_dead_links : int or None
            Ignored.
        require_torus : bool
            Must be False.
        min_ratio : float
            Ignored.

        Returns
        -------
        bool

        See Also
        --------
        alloc_possible : The (public) wrapper which also supports checking
                         board allocations.
        """
        assert require_torus is False
        assert (((x is None) == (y is None) == (z is None)) or
                ((x == 1) == (y is None) == (z is None)))

        board_requested = y is not None

        # If the requested board is outside the dimensions of the machine, the
        # request definitely can't be met.
        if board_requested and not(0 <= x < self.width and
                                   0 <= y < self.height and
                                   0 <= z < 3):
            return False

        # If the requested board is dead, this should fail
        if board_requested and (x, y, z) in self.dead_boards:
            return False

        # If there are no working boards, we must also fail
        if len(self.dead_boards) >= (self.width * self.height * 3):
            return False

        # Should be possible!
        return True

    def _alloc_board(self, x=None, y=None, z=None,
                     max_dead_boards=None, max_dead_links=None,
                     require_torus=False, min_ratio=0.0):
        """Allocate a single board, optionally specifying a specific board to
        allocate.

        Parameters
        ----------
        x, y, z : ints or None
            If None, an arbitrary free board will be returned if possible. If
            all are defined, attempts to allocate the specific board requested
            if available and working.
        max_dead_boards : int or None
            Ignored.
        max_dead_links : int or None
            Ignored.
        require_torus : bool
            Must be False.
        min_ratio : float
            Ignored.

        Returns
        -------
        (allocation_id, boards, periphery, torus) or None
            If the allocation was successful a four-tuple is returned. If the
            allocation was not successful None is returned.

            The ``allocation_id`` is an integer which should be used to free
            the allocation with the :py:meth:`.free` method. ``boards`` is a
            set of (x, y, z) tuples giving the location of to allocated board.
            ``periphery`` is a set of (x, y, z, link) tuples giving the links
            which leave the board. ``torus`` is always
            :py:attr:`.WrapAround.none` for single boards.

        See Also
        --------
        alloc : The (public) wrapper which also supports allocating triads.
        """
        assert require_torus is False
        assert (((x is None) == (y is None) == (z is None)) or
                ((x == 1) == (y is None) == (z is None)))

        board_requested = y is not None

        # Special case: the desired board is dead: just give up
        if board_requested and (x, y, z) in self.dead_boards:
            return None

        # Try and return a board from an already allocated set of single-board
        # triads if possible
        if (self.single_board_triads and
                (not board_requested or
                 z in self.single_board_triads.get((x, y), set()))):
            if not board_requested:
                # No specific board requested, return any available
                x, y = next(iter(self.single_board_triads))
                available = self.single_board_triads[(x, y)]
                z = available.pop()
            else:
                # A specific board was requested (and is available), get that
                # one
                available = self.single_board_triads[(x, y)]
                available.remove(z)

            # If we used up the last board, move the traid to the full list
            if not available:
                del self.single_board_triads[(x, y)]
                self.full_single_board_triads.add((x, y))

            # Allocate the board
            allocation_id = self.next_id
            self.next_id += 1
            self.allocation_types[allocation_id] = _AllocationType.board
            self.allocation_board[allocation_id] = (x, y, z)
            return (allocation_id,
                    set([(x, y, z)]),
                    set((x, y, z, link) for link in Links),
                    WrapAround.none)

        # The desired board was not available in an already-allocated triad.
        # Attempt to request that triad.

        def has_at_least_one_working_board(x, y, width, height):
            num_dead = 0
            for z in range(3):
                if (x, y, z) in self.dead_boards:
                    num_dead += 1

            return num_dead < 3

        if board_requested:
            xy = self.pack_tree.request(x, y)
        else:
            xy = self.pack_tree.alloc(
                1, 1, candidate_filter=has_at_least_one_working_board)

        # If a triad could not be allocated, fail
        if xy is None:
            return None

        # If a triad was allocated, add it to the set of allocated triads for
        # single-boards
        self.single_board_triads[xy] = \
            set(z for z in range(3)
                if (xy[0], xy[1], z) not in self.dead_boards)

        # Recursing will return a board from the triad
        return self._alloc_board(x, y, z)

    def _alloc_type(self, x_or_num_or_width=None, y_or_height=None, z=None,
                    max_dead_boards=None, max_dead_links=None,
                    require_torus=False, min_ratio=0.0):
        """Returns the type of allocation the user is attempting to make (and
        fails if it is invalid.

        Usage::

            a.alloc()  # Allocate any single board
            a.alloc(1)  # Allocate any single board
            a.alloc(3, 2, 1)  # Allocate the specific board (3, 2, 1)
            a.alloc(4)  # Allocate at least 4 boards
            a.alloc(2, 3, **kwargs)  # Allocate a 2x3 triad machine

        Parameters
        ----------
        <nothing> OR num OR x, y, z OR width, height
            If nothing, allocate a single board.

            If num, allocate at least that number of boards. Special case: if
            1, allocate exactly 1 board.

            If x, y and z, allocate a specific board.

            If width and height, allocate a block of this size, in triads.
        max_dead_boards : int or None
            The maximum number of broken or unreachable boards to allow in the
            allocated region. If None, any number of dead boards is permitted,
            as long as the board on the bottom-left corner is alive (Default:
            None).
        max_dead_links : int or None
            The maximum number of broken links allow in the allocated region.
            When require_torus is True this includes wrap-around links,
            otherwise peripheral links are not counted.  If None, any number of
            broken links is allowed. (Default: None).
        require_torus : bool
            If True, only allocate blocks with torus connectivity. In general
            this will only succeed for requests to allocate an entire machine
            (when the machine is otherwise not in use!). Must be False when
            allocating boards. (Default: False)

        Returns
        -------
        :py:class:`._AllocationType`
        """
        # Work-around for Python 2's non-support for keyword-only arguments...
        args = []
        if x_or_num_or_width is not None:
            args.append(x_or_num_or_width)
            if y_or_height is not None:
                args.append(y_or_height)
                if z is not None:
                    args.append(z)

        # Select allocation type
        if len(args) == 0:
            alloc_type = _AllocationType.board
        elif len(args) == 1:
            if args[0] == 1:
                alloc_type = _AllocationType.board
            else:
                alloc_type = _AllocationType.boards
        elif len(args) == 2:
            alloc_type = _AllocationType.triads
        elif len(args) == 3:  # pragma: no branch
            alloc_type = _AllocationType.board

        # Validate arguments
        if alloc_type == _AllocationType.board:
            if require_torus:
                raise ValueError(
                    "require_torus must be False when allocating boards.")

        return alloc_type

    def alloc_possible(self, *args, **kwargs):
        """Is the specified allocation actually possible on this machine?

        Usage::

            a.alloc_possible()  # Can allocate a single board?
            a.alloc_possible(1)  # Can allocate a single board?
            a.alloc_possible(4)  # Can allocate at least 4 boards?
            a.alloc_possible(3, 2, 1)  # Can allocate a board (3, 2, 1)?
            a.alloc_possible(2, 3, **kwargs)  # Can allocate 2x3 triads?

        Parameters
        ----------
        <nothing> OR num OR x, y, z OR width, height
            If nothing, allocate a single board.

            If num, allocate at least that number of boards. Special case: if
            1, allocate exactly 1 board.

            If x, y and z, allocate a specific board.

            If width and height, allocate a block of this size, in triads.
        min_ratio : float
            The aspect ratio which the allocated region must be 'at least as
            square as'. Set to 0.0 for any allowable shape. Ignored when
            allocating single boards or specific rectangles of triads.
        max_dead_boards : int or None
            The maximum number of broken or unreachable boards to allow in the
            allocated region. If None, any number of dead boards is permitted,
            as long as the board on the bottom-left corner is alive (Default:
            None).
        max_dead_links : int or None
            The maximum number of broken links allow in the allocated region.
            When require_torus is True this includes wrap-around links,
            otherwise peripheral links are not counted.  If None, any number of
            broken links is allowed. (Default: None).
        require_torus : bool
            If True, only allocate blocks with torus connectivity. In general
            this will only succeed for requests to allocate an entire machine
            (when the machine is otherwise not in use!). Must be False when
            allocating boards. (Default: False)

        Returns
        -------
        bool
        """
        alloc_type = self._alloc_type(*args, **kwargs)
        if alloc_type is _AllocationType.board:
            return self._alloc_board_possible(*args, **kwargs)
        elif alloc_type is _AllocationType.boards:
            return self._alloc_boards_possible(*args, **kwargs)
        else:
            return self._alloc_triads_possible(*args, **kwargs)

    def alloc(self, *args, **kwargs):
        """Attempt to allocate a board or rectangular region of triads of
        boards.

        Usage::

            a.alloc()  # Allocate a single board
            a.alloc(1)  # Allocate a single board
            a.alloc(4)  # Allocate at least 4 boards
            a.alloc(3, 2, 1)  # Allocate a specific board (3, 2, 1)
            a.alloc(2, 3, **kwargs)  # Allocate a 2x3 triad machine

        Parameters
        ----------
        <nothing> OR num OR x, y, z OR width, height
            If all None, allocate a single board.

            If num, allocate at least that number of boards. Special case: if
            1, allocate exactly 1 board.

            If x, y and z, allocate a specific board.

            If width and height, allocate a block of this size, in triads.
        min_ratio : float
            The aspect ratio which the allocated region must be 'at least as
            square as'. Set to 0.0 for any allowable shape. Ignored when
            allocating single boards or specific rectangles of triads.
        max_dead_boards : int or None
            The maximum number of broken or unreachable boards to allow in the
            allocated region. If None, any number of dead boards is permitted,
            as long as the board on the bottom-left corner is alive (Default:
            None).
        max_dead_links : int or None
            The maximum number of broken links allow in the allocated region.
            When require_torus is True this includes wrap-around links,
            otherwise peripheral links are not counted.  If None, any number of
            broken links is allowed. (Default: None).
        require_torus : bool
            If True, only allocate blocks with torus connectivity. In general
            this will only succeed for requests to allocate an entire machine
            (when the machine is otherwise not in use!). Must be False when
            allocating boards. (Default: False)

        Returns
        -------
        (allocation_id, boards, periphery, torus) or None
            If the allocation was successful a four-tuple is returned. If the
            allocation was not successful None is returned.

            The ``allocation_id`` is an integer which should be used to free
            the allocation with the :py:meth:`.free` method. ``boards`` is a
            set of (x, y, z) tuples giving the locations of to allocated
            boards.  ``periphery`` is a set of (x, y, z, link) tuples giving
            the links which leave the allocated set of boards. ``torus`` is a
            :py:class:`.WrapAround` value indicating torus connectivity when at
            least one torus may exist.
        """
        alloc_type = self._alloc_type(*args, **kwargs)
        if alloc_type is _AllocationType.board:
            return self._alloc_board(*args, **kwargs)
        elif alloc_type is _AllocationType.boards:
            return self._alloc_boards(*args, **kwargs)
        else:
            return self._alloc_triads(*args, **kwargs)

    def free(self, allocation_id):
        """Free the resources consumed by the specified allocation.

        Parameters
        ----------
        allocation_id : int
            The ID of the allocation to free.
        """
        type = self.allocation_types.pop(allocation_id)
        x, y, z = self.allocation_board.pop(allocation_id)

        if type is _AllocationType.triads:
            # Simply free the allocation
            self.pack_tree.free(x, y)
        elif type is _AllocationType.board:
            # If the traid the board came from was full, it now isn't...
            if (x, y) in self.full_single_board_triads:
                self.full_single_board_triads.remove((x, y))
                self.single_board_triads[(x, y)] = set()

            # Return the board to the set available in that triad
            self.single_board_triads[(x, y)].add(z)

            # If all working boards have been freed in the triad, we must free
            # the triad.
            working = set(z for z in range(3)
                          if (x, y, z) not in self.dead_boards)
            if self.single_board_triads[(x, y)] == working:
                del self.single_board_triads[(x, y)]
                self.pack_tree.free(x, y)
        else:  # pragma: no cover
            assert False, "Unknown allocation type!"


class _AllocationType(Enum):
    """Type identifiers for allocations."""

    triads = 0
    """A rectangular block of triads."""

    board = 1
    """A single board."""

    boards = 2
    """Two or more boards, to be allocated as triads.

    This type only returned by :py:meth:`.Allocator._alloc_type` and is never
    used as an allocation type.
    """


class _CandidateFilter(object):
    """A callable object which, given a rectangular region of triads will check
    to see if it meets some set of criteria.

    If any candidate is accepted the following attributes are set according to
    the last accepted candidate.

    Attributes
    ----------
    boards : set([(x, y, z), ...])
        The working boards present in the accepted candidate. None if no
        candidate has been accepted.
    periphery : set([(x, y, z, :py:class:`rig.links.Links`), ...])
        The links around the periphery of the selection of boards which should
        be disabled to isolate the system. None if no candidate has been
        accepted.
    torus : :py:class:`.WrapAround`
        Describes the types of wrap-around links the candidate has.
    """

    def __init__(self, width, height, dead_boards, dead_links,
                 max_dead_boards, max_dead_links, require_torus,
                 expected_boards=None):
        """Create a new candidate filter.

        Parameters
        ----------
        width, height : int
            Dimensions (in triads) of the system within which candidates are
            being chosen.
        dead_boards : set([(x, y, z), ...])
            The set of boards which are dead and which must not be allocated.
        dead_links : set([(x, y, z, :py:class:`rig.links.Links`), ...])
            The set of links leaving boards which are known not to be working.
            Connections to boards in the set dead_boards are assumed to be
            dead and need not be included in this list. Note that both link
            directions must be flagged as dead (if the link is bidirectionally
            down).
        max_dead_boards : int or None
            The maximum number of broken or unreachable boards to allow in the
            allocated region. If None, any number of dead boards is permitted,
            as long as the board on the bottom-left corner is alive (Default:
            None).
        max_dead_links : int or None
            The maximum number of broken links allow in the allocated region.
            When require_torus is True this includes wrap-around links,
            otherwise peripheral links are not counted.  If None, any number of
            broken links is allowed. (Default: None).
        require_torus : bool
            If True, only allocatae blocks with torus connectivity. In general
            this will only succeed for requests to allocate an entire machine
            (when the machine is otherwise not in use!). (Default: False)
        expected_boards : int or None
            If given, specifies the number of boards which are expected to be
            in a candidate. This ensures that when an over-allocation is made,
            the max_dead_boards figure is offset by any over-allocation.

            If None, assumes the candidate width * candidate height * 3.
        """
        self.width = width
        self.height = height
        self.dead_boards = dead_boards
        self.dead_links = dead_links
        self.max_dead_boards = max_dead_boards
        self.max_dead_links = max_dead_links
        self.require_torus = require_torus
        self.expected_boards = expected_boards

        self.boards = None
        self.periphery = None
        self.torus = None

    def _enumerate_boards(self, x, y, width, height):
        """Starting from board (x, y, 0), enumerate as many reachable and
        working boards as possible within the rectangle width x height triads.

        Returns
        -------
        set([(x, y, z), ...])
        """
        # The set of visited (and working) boards
        boards = set()

        to_visit = deque([(x, y, 0)])
        while to_visit:
            x1, y1, z1 = to_visit.popleft()

            # Skip dead boards and boards we've seen before
            if ((x1, y1, z1) in self.dead_boards or
                    (x1, y1, z1) in boards):
                continue

            boards.add((x1, y1, z1))

            # Visit neighbours which are within the range
            for link in Links:
                # Skip dead links
                if (x1, y1, z1, link) in self.dead_links:
                    continue

                x2, y2, z2, _ = board_down_link(x1, y1, z1, link,
                                                self.width, self.height)

                # Skip links to boards outside the specified range
                if not (x <= x2 < x + width and
                        y <= y2 < y + height):
                    continue

                to_visit.append((x2, y2, z2))

        # Return the set of boards we could reach
        return boards

    def _classify_links(self, boards):
        """Get a list of links of various classes connected to the supplied set
        of boards.

        Parameters
        ----------
        boards : set([(x, y, z), ...])
            A set of fully-connected, alive boards.

        Returns
        -------
        alive : set([(x, y, z, :py:class:`rig.links.Links`), ...])
            Links which are working and connect one board
            in the set to another.
        wrap : set([(x, y, z, :py:class:`rig.links.Links`), ...])
            Working links between working boards in the set which wrap-around
            the toroid.
        dead : set([(x, y, z, :py:class:`rig.links.Links`), ...])
            Links which are not working and connect one board in the set to
            another.
        dead_wrap : set([(x, y, z, :py:class:`rig.links.Links`), ...])
            Dead links between working boards in the set which wrap-around the
            toroid.
        periphery : set([(x, y, z, :py:class:`rig.links.Links`), ...])
            Links are those which connect from one board in the set to a board
            outside the set. These links may be dead or alive.
        wrap_around_type : :py:class:`~spalloc_server.coordinates.WrapAround`
            What types of wrap-around links are present (making no distinction
            between dead and alive links)?
        """
        alive = set()
        wrap = set()
        dead = set()
        dead_wrap = set()
        periphery = set()
        wrap_around_type = WrapAround.none

        for x1, y1, z1 in boards:
            for link in Links:
                is_dead = (x1, y1, z1, link) in self.dead_links
                x2, y2, z2, wrapped = board_down_link(x1, y1, z1, link,
                                                      self.width, self.height)
                in_set = (x2, y2, z2) in boards

                if in_set:
                    wrap_around_type |= wrapped

                    if wrapped:
                        if is_dead:
                            dead_wrap.add((x1, y1, z1, link))
                        else:
                            wrap.add((x1, y1, z1, link))
                    else:
                        if is_dead:
                            dead.add((x1, y1, z1, link))
                        else:
                            alive.add((x1, y1, z1, link))
                else:
                    periphery.add((x1, y1, z1, link))

        return (alive, wrap, dead, dead_wrap, periphery,
                WrapAround(wrap_around_type))

    def __call__(self, x, y, width, height):
        """Test whether the region specified meets the stated requirements.

        If True is returned, the set of boards in the region is stored in
        self.boards and the set of links on the periphery are stored in
        self.periphery.
        """
        boards = self._enumerate_boards(x, y, width, height)

        # Make sure the maximum dead boards limit isn't exceeded
        if self.max_dead_boards is not None:
            if self.expected_boards is not None:
                expected_boards = self.expected_boards
            else:
                expected_boards = width * height * 3
            alive = len(boards)
            dead = expected_boards - alive
            if alive == 0 or dead > self.max_dead_boards:
                return False
        else:
            if len(boards) == 0:
                return False

        # Make sure the maximum dead links limit isn't exceeded (and that torus
        # links exist if requested)
        (alive, wrap, dead, dead_wrap, periphery, wrap_around_type) = \
            self._classify_links(boards)
        if self.require_torus and wrap_around_type == WrapAround.none:
            return False
        if self.max_dead_links is not None:
            if self.require_torus:
                dead_links = len(dead) + len(dead_wrap)
            else:
                dead_links = len(dead)
            if dead_links > self.max_dead_links:
                return False

        # All looks good, accept this region and keep the enumerated boards and
        # peripheral links
        self.boards = boards
        self.periphery = periphery
        self.torus = wrap_around_type
        return True
