import pytest

from rig.links import Links

from spalloc_server.coordinates import board_down_link, WrapAround
from spalloc_server.allocator import \
    _AllocationType, _CandidateFilter, Allocator


class TestCandidateFilter(object):

    def test_enumerate_boards(self):
        w, h = 10, 9
        dead_boards = set([
            (0, 0, 1),
        ])
        dead_links = set([
            (0, 0, 0, Links.north),
            (1, 2, 0, Links.north),
        ])

        cf = _CandidateFilter(w, h, dead_boards, dead_links, 0, 0, False)

        # From 0, 0 we should not be able to reach anything else unless we have
        # wrap-around links
        assert cf._enumerate_boards(0, 0, 1, 1) == set([(0, 0, 0)])
        assert cf._enumerate_boards(0, 0, 3, 4) == set([(0, 0, 0)])
        assert cf._enumerate_boards(0, 0, w, h) == set([  # pragma: no branch
            (x, y, z) for x in range(w) for y in range(h) for z in range(3)
            if (x, y, z) not in dead_boards])

        # From 1, 1 we should be able to reach everything despite being blocked
        # off one of the links from (1, 1, 0)
        assert cf._enumerate_boards(1, 2, 5, 4) == set([  # pragma: no branch
            (x, y, z)
            for x in range(1, 6)
            for y in range(2, 6)
            for z in range(3)])

    def test_classify_links(self):
        w, h = 10, 9
        dead_boards = set([
            (0, 0, 1),
        ])
        dead_links = set([
            (0, 0, 0, Links.north),
            (0, 0, 2, Links.south),
            (0, 0, 0, Links.north_east),
            (0, 0, 1, Links.south_west),
            (0, 0, 0, Links.east),
            (0, h-1, 2, Links.west),
            (0, 0, 2, Links.north_east),
            (1, 1, 0, Links.south_west),
        ])

        cf = _CandidateFilter(w, h, dead_boards, dead_links, 0, 0, False)

        # For 1x1 we should have just peripheral links all of which are listed
        # even if they're dead.
        (alive, wrap, dead, dead_wrap, periphery, wrap_around_type) =\
            cf._classify_links(set([(0, 0, 0)]))
        assert alive == set()
        assert wrap == set()
        assert dead == set()
        assert dead_wrap == set()
        assert periphery == set([(0, 0, 0, link) for link in Links])
        assert wrap_around_type is WrapAround.none

        # For a connected block, links to internal dead boards should still be
        # regarded as peripheral. NB: The block of chips is not fully connected
        # but provides exhibits examples of all types of connectivity.
        #
        #       (1, 1, 0) ->  ___
        #                 ___/ 0 \___
        #  (0, 0, 2) ->  / 2 x___/ 2 \  <- (1, 0, 2)
        #                \_x_/ X \___/
        #  (0, 0, 0) ->  / 0 x___/ 0 \  <- (1, 0, 0)
        #                \___x 2 \___/
        #    (0, h-1, 2) ->  \___/
        (alive, wrap, dead, dead_wrap, periphery, wrap_around_type) =\
            cf._classify_links(set([(0, 0, 0),
                                    (0, 0, 2),
                                    (1, 1, 0),
                                    (1, 0, 2),
                                    (1, 0, 0),
                                    (0, h-1, 2)]))
        assert alive == set([
            (1, 1, 0, Links.east),
            (1, 0, 2, Links.west),
            (1, 0, 2, Links.south),
            (1, 0, 0, Links.north),
        ])
        assert wrap == set([
            (1, 0, 0, Links.south_west),
            (0, h-1, 2, Links.north_east),
        ])
        assert dead == set([
            (0, 0, 0, Links.north),
            (0, 0, 2, Links.south),
            (0, 0, 2, Links.north_east),
            (1, 1, 0, Links.south_west),
        ])
        assert dead_wrap == set([
            (0, 0, 0, Links.east),
            (0, h-1, 2, Links.west),
        ])
        assert periphery == set([
            # Around the outside
            (0, 0, 0, Links.south),
            (0, 0, 0, Links.south_west),
            (0, 0, 0, Links.west),
            (0, 0, 2, Links.south_west),
            (0, 0, 2, Links.west),
            (0, 0, 2, Links.north),
            (1, 1, 0, Links.west),
            (1, 1, 0, Links.north),
            (1, 1, 0, Links.north_east),
            (1, 0, 2, Links.north),
            (1, 0, 2, Links.north_east),
            (1, 0, 2, Links.east),
            (1, 0, 0, Links.north_east),
            (1, 0, 0, Links.east),
            (1, 0, 0, Links.south),
            (0, h-1, 2, Links.east),
            (0, h-1, 2, Links.south),
            (0, h-1, 2, Links.south_west),
            # Around the inside
            (0, 0, 0, Links.north_east),
            (0, 0, 2, Links.east),
            (1, 1, 0, Links.south),
            (1, 0, 2, Links.south_west),
            (1, 0, 0, Links.west),
            (0, h-1, 2, Links.north),
        ])
        assert wrap_around_type == WrapAround.y

    def test_classify_links_wrap_around(self):
        w, h = 10, 10
        cf = _CandidateFilter(w, h, set(), set(), 0, 0, False)

        assert cf._classify_links(set([(0, 0, 0), (0, 0, 1)]))[5] == \
            WrapAround.none

        assert cf._classify_links(set([(0, 0, 0), (0, 0, 1),
                                       (0, h-1, 2)]))[5] == \
            WrapAround.y

        assert cf._classify_links(set([(0, 0, 0), (0, 0, 1),
                                       (w-1, 0, 1)]))[5] == \
            WrapAround.x

        assert cf._classify_links(set([(0, 0, 0), (0, 0, 1),
                                       (0, h-1, 2), (w-1, 0, 1)]))[5] == \
            WrapAround.both

    @pytest.mark.parametrize("w,h,max_dead_boards",
                             [(1, 1, 1), (2, 1, 4)])
    def test_call_too_many_dead(self, w, h, max_dead_boards):
        w, h = 10, 9
        dead_boards = set([
            (0, 0, 1),
        ])
        dead_links = set([
            (0, 0, 0, Links.north),
            (1, 2, 0, Links.north),
        ])

        cf = _CandidateFilter(w, h, dead_boards, dead_links,
                              max_dead_boards, 0, False)

        assert cf(0, 0, w, h) is False

    def test_not_a_torus(self):
        w, h = 10, 9
        cf = _CandidateFilter(w, h, set(), set(), 0, 0, True)

        assert cf(0, 0, w - 1, h - 1) is False
        assert cf(0, 0, w - 1, h) is True
        assert cf(0, 0, w, h - 1) is True
        assert cf(0, 0, w, h) is True

    @pytest.mark.parametrize("require_torus", [True, False])
    @pytest.mark.parametrize("dead_on_torus", [True, False])
    def test_call_too_many_dead_links(self, require_torus, dead_on_torus):
        w, h = 10, 9
        dead_boards = set()
        dead_links = set([
            (0, 0, 0, Links.west) if dead_on_torus else (0, 0, 0, Links.north),
        ])

        max_dead_links = 0
        cf = _CandidateFilter(w, h, dead_boards, dead_links,
                              0, max_dead_links, require_torus)

        expected = not require_torus and dead_on_torus

        if require_torus:
            assert cf(0, 0, w, h) is expected
        else:
            assert cf(0, 0, 1, 1) is expected

    @pytest.mark.parametrize("max_dead_boards", [0, 3, None])
    def test_call_no_bottom_left_board(self, max_dead_boards):
        # Should never allow allocation when bottom-left board is dead
        dead_boards = set([(0, 0, 0)])
        dead_links = set()
        cf = _CandidateFilter(1, 1, dead_boards, dead_links,
                              max_dead_boards, None, False)

        assert cf(0, 0, 1, 1) is False

    @pytest.mark.parametrize("have_dead_links", [True, False])
    @pytest.mark.parametrize("have_dead_boards", [True, False])
    def test_call_success(self, have_dead_links, have_dead_boards):
        # If "None" is provided for maximum links/boards we should always
        # succeed
        w, h = 10, 9
        dead_boards = set([0, 0, 1]) if have_dead_boards else set()
        dead_links = set([0, 0, 0, Links.north]) if have_dead_links else set()
        cf = _CandidateFilter(w, h, dead_boards, dead_links,
                              None, None, False)

        assert cf(0, 0, 1, 1) is True
        assert cf.boards == set((0, 0, z) for z in range(3))
        assert cf.periphery == set(  # pragma: no branch
            (0, 0, z, link)
            for z in range(3) for link in Links
            if board_down_link(0, 0, z, link, w, h)[:2] != (0, 0))
        assert cf.torus == WrapAround.none

    def test_torus(self):
        # Should notice if a torus is allocated
        w, h = 10, 9
        cf = _CandidateFilter(w, h, set(), set(), None, None, False)
        assert cf(0, 0, 1, 1) is True
        assert cf.torus is WrapAround.none

        assert cf(0, 0, w, 1) is True
        assert cf.torus is WrapAround.x
        assert cf(0, 0, 1, h) is True
        assert cf.torus is WrapAround.y
        assert cf(0, 0, w, h) is True
        assert cf.torus is WrapAround.both

    @pytest.mark.parametrize("expected_boards", [1, 2, 3])
    def test_expected_boards(self, expected_boards):
        dead_boards = set([(0, 0, 2)])
        dead_links = set()
        cf = _CandidateFilter(1, 1, dead_boards, dead_links,
                              0, None, False, expected_boards)

        assert cf(0, 0, 1, 1) == (expected_boards < 3)


class TestAllocator(object):

    def test_alloc_triads_dead_boards(self):
        # Should not be able to allocate if too many boards are dead
        a = Allocator(3, 4, dead_boards=set([(0, 0, 0)]))
        assert a._alloc_triads(3, 4, max_dead_boards=0) is None
        assert a._alloc_boards(3*4*3, max_dead_boards=0) is None

    def test_alloc_triads_dead_links(self):
        # Should not be able to allocate if too many links are dead
        a = Allocator(3, 4, dead_links=set([(0, 0, 0, Links.north)]))
        assert a._alloc_triads(3, 4, max_dead_links=0) is None
        assert a._alloc_boards(3*4*3, max_dead_links=0) is None

    def test_alloc_triads_bad_torus(self):
        # Should not be able to allocate a torus unless requesting the full
        # machine
        a = Allocator(3, 4)
        assert a._alloc_triads(1, 2, require_torus=True) is None
        assert a._alloc_triads(3, 2, require_torus=True) is None
        assert a._alloc_triads(2, 4, require_torus=True) is None
        assert a._alloc_boards(1*2*3, require_torus=True) is None
        assert a._alloc_boards(3*2*3, require_torus=True) is None
        assert a._alloc_boards(2*4*3, require_torus=True) is None

    def test_alloc_triads_too_big(self):
        # Should fail if something too big is requested
        a = Allocator(3, 4)
        assert a._alloc_triads(4, 4) is None
        assert a._alloc_triads(3, 5) is None
        assert a._alloc_triads(4, 5) is None
        assert a._alloc_boards(3*4*3 + 1) is None

    def test_alloc_triads_empty(self):
        # Should fail if nothing or negative amounts requested
        a = Allocator(3, 4)
        assert a._alloc_triads(0, 1) is None
        assert a._alloc_triads(-1, 1) is None
        assert a._alloc_triads(1, -1) is None
        assert a._alloc_triads(1, 0) is None
        assert a._alloc_boards(0) is None
        assert a._alloc_boards(-1) is None

    def test_alloc_triads_single(self):
        # Should be able to allocate single blocks
        w, h = 3, 4
        next_id = 10
        a = Allocator(w, h, next_id=next_id)

        for _ in range(w * h):
            allocation_id, boards, periphery, torus = a._alloc_triads(1, 1)

            assert torus is WrapAround.none

            assert allocation_id == next_id
            next_id += 1

            assert len(boards) == 3
            xys = set((x, y) for (x, y, z) in boards)
            assert len(xys) == 1
            assert set(z for (x, y, z) in boards) == set(range(3))

            x, y = xys.pop()
            assert periphery == set((x, y, z, link)
                                    for z in range(3)
                                    for link in Links
                                    if (board_down_link(x, y, z,
                                                        link, w, h)[:2] !=
                                        (x, y)))

        # Should get full
        assert a._alloc_triads(1, 1) is None

    @pytest.mark.parametrize("require_torus", [True, False])
    def test_alloc_triads_torus(self, require_torus):
        # Should be able to allocate the full machine in one go
        w, h = 3, 4
        next_id = 10
        a = Allocator(w, h, next_id=next_id)

        allocation_id, boards, periphery, torus = a._alloc_triads(
            w, h, require_torus=require_torus)

        assert allocation_id == next_id
        assert boards == set((x, y, z)
                             for x in range(w)
                             for y in range(h)
                             for z in range(3))
        assert periphery == set()
        assert torus is WrapAround.both

        # Should get full
        assert a._alloc_triads(1, 1, require_torus=require_torus) is None

    def test_alloc_boards_round_up(self):
        # Should round up number of boards being allocated to a multiple of
        # three
        a = Allocator(3, 4)
        assert len(a._alloc_boards(1)[1]) == 3
        assert len(a._alloc_boards(2)[1]) == 3
        assert len(a._alloc_boards(3)[1]) == 3
        assert len(a._alloc_boards(4)[1]) == 6
        assert len(a._alloc_boards(5)[1]) == 6
        assert len(a._alloc_boards(6)[1]) == 6

    def test_alloc_boards_compensate_for_dead(self):
        # When over-allocating, extra boards in the allocation should
        # compensate for dead boards.
        a = Allocator(4, 2, dead_boards=set([(0, 0, 2)]))
        assert a._alloc_boards_possible(24, max_dead_boards=0) is False
        assert a._alloc_boards(24, max_dead_boards=0) is None

        assert a._alloc_boards_possible(23, max_dead_boards=0) is True
        assert len(a._alloc_boards(23, max_dead_boards=0)[1]) == 23

    def test_alloc_board_dead(self):
        # Should fail if a dead board is requested
        a = Allocator(3, 4, dead_boards=set([(3, 2, 1)]))
        assert a._alloc_board(3, 2, 1) is None

    def test_alloc_triads_non_machine(self):
        # Should fail if machine too small
        a = Allocator(3, 4)
        assert a._alloc_triads(0, 0) is None
        assert a._alloc_triads(0, 1) is None
        assert a._alloc_triads(1, 0) is None

    def test_alloc_existing_triad(self):
        # Attempt to allocate based on an already allocated triad.
        next_id = 10
        a = Allocator(1, 1, next_id=next_id)

        # Manually add a triad to the set available
        assert a.pack_tree.request(0, 0)
        a.single_board_triads[(0, 0)] = set(range(3))

        # Should get the three boards from the triad
        all_boards = set()
        for _ in range(3):
            assert (0, 0) in a.single_board_triads
            assert (0, 0) not in a.full_single_board_triads

            allocation_id, boards, periphery, torus = a._alloc_board()

            assert allocation_id == next_id
            next_id += 1

            assert len(boards) == 1
            x, y, z = next(iter(boards))
            assert x == y == 0
            assert 0 <= x < 3
            assert (x, y, z) not in all_boards
            all_boards.add((x, y, z))

            assert periphery == set((x, y, z, link) for link in Links)

            assert torus is WrapAround.none

        assert all_boards == set((0, 0, z) for z in range(3))

        # Once exhausted, the boards should be removed from the single board
        # triad set.
        assert (0, 0) not in a.single_board_triads
        assert (0, 0) in a.full_single_board_triads

    @pytest.mark.parametrize("last_remaining", [True, False])
    def test_alloc_existing_specific_board(self, last_remaining):
        # Attempt to allocate a specific board
        next_id = 10
        a = Allocator(1, 1, next_id=next_id)

        # Manually add a triad to the set available
        assert a.pack_tree.request(0, 0)
        a.single_board_triads[(0, 0)] = set(
            [1] if last_remaining else range(3))

        # Should be able to get the board we want!
        allocation_id, boards, periphery, torus = a._alloc_board(0, 0, 1)

        assert allocation_id == next_id

        assert len(boards) == 1
        assert next(iter(boards)) == (0, 0, 1)

        assert periphery == set((0, 0, 1, link) for link in Links)

        assert torus is WrapAround.none

        # If exhausted, the boards should be removed from the single board
        # triad set.
        if last_remaining:
            assert (0, 0) not in a.single_board_triads
            assert (0, 0) in a.full_single_board_triads
        else:
            assert (0, 0) in a.single_board_triads
            assert (0, 0) not in a.full_single_board_triads

    def test_alloc_existing_used_board(self):
        next_id = 10
        a = Allocator(1, 1, next_id=next_id)

        # Manually add a triad to the set available
        assert a.pack_tree.request(0, 0)
        a.single_board_triads[(0, 0)] = set([0, 2])

        # Shouldn't be able to get the board we want since it is already
        # allocated.
        assert a._alloc_board(0, 0, 1) is None

    def test_alloc_board(self):
        # If no single boards have been allocated yet, a whole triad should be
        # allocated and a (live) board from that set be returned.
        next_id = 10
        a = Allocator(2, 1, dead_boards=set(
            [(1, 0, 1)] + [(0, 0, z) for z in range(3)]), next_id=next_id)

        # Should get two boards in total
        all_boards = set()
        for _ in range(2):
            allocation_id, boards, periphery, torus = a._alloc_board()

            assert allocation_id == next_id
            next_id += 1

            assert len(boards) == 1
            x, y, z = next(iter(boards))
            assert (x, y, z) not in all_boards
            all_boards.add((x, y, z))

            assert periphery == set((x, y, z, link) for link in Links)

            assert torus is WrapAround.none

        assert all_boards == set([(1, 0, 0), (1, 0, 2)])

        # Should not be able to allocate any more!
        assert a._alloc_board() is None

    def test_alloc_board_specific(self):
        # If no triad containing the requested board is already allocated, we
        # should allocate it.
        next_id = 10
        a = Allocator(1, 1, next_id=next_id)

        # Should get two boards in total
        allocation_id, boards, periphery, torus = a._alloc_board(0, 0, 1)

        assert allocation_id == next_id

        assert len(boards) == 1
        assert next(iter(boards)) == (0, 0, 1)

        assert periphery == set((0, 0, 1, link) for link in Links)

        # Should not be able to allocate that board any more!
        assert a._alloc_board(0, 0, 1) is None

        assert torus is WrapAround.none

    def test_free_board(self):
        a = Allocator(2, 1, dead_boards=set([(0, 0, 1)]))

        # Allocate the two boards on triad 0, 0
        id00, _1, _2, _3 = a._alloc_board(0, 0, 0)
        id02, _1, _2, _3 = a._alloc_board(0, 0, 2)

        # Allocate the three boards on triad 1, 0
        id10, _1, _2, _3 = a._alloc_board(1, 0, 0)
        id11, _1, _2, _3 = a._alloc_board(1, 0, 1)
        id12, _1, _2, _3 = a._alloc_board(1, 0, 2)

        # No board triads should be available
        assert len(a.single_board_triads) == 0
        assert a.full_single_board_triads == set([(0, 0), (1, 0)])

        # The pack tree should be full
        assert a.pack_tree.allocated is False
        assert a.pack_tree.children is not None
        assert a.pack_tree.children[0].allocated is True
        assert a.pack_tree.children[1].allocated is True

        # Freeing a board should bring it back into the single boards triads
        # dictionary
        a.free(id00)
        assert a.single_board_triads == {(0, 0): set([0])}
        assert a.full_single_board_triads == set([(1, 0)])

        # The pack tree should still be full
        assert a.pack_tree.allocated is False
        assert a.pack_tree.children is not None
        assert a.pack_tree.children[0].allocated is True
        assert a.pack_tree.children[1].allocated is True

        # Freeing the only other working board in a triad should remove the
        # triad entirely
        a.free(id02)
        assert a.single_board_triads == {}
        assert a.full_single_board_triads == set([(1, 0)])

        # ...and the corresponding part of the pack_tree should be free too
        assert a.pack_tree.allocated is False
        assert a.pack_tree.children is not None
        assert a.pack_tree.children[0].allocated is False
        assert a.pack_tree.children[1].allocated is True

        # Freeing should move into the single boards dictionary
        a.free(id10)
        assert a.single_board_triads == {(1, 0): set([0])}
        assert a.full_single_board_triads == set()

        # The pack_tree should be unchanged.
        assert a.pack_tree.allocated is False
        assert a.pack_tree.children is not None
        assert a.pack_tree.children[0].allocated is False
        assert a.pack_tree.children[1].allocated is True

        # Freeing another board in the same triad (but not the last) should
        # just update the dictionary.
        a.free(id11)
        assert a.single_board_triads == {(1, 0): set([0, 1])}
        assert a.full_single_board_triads == set()

        # The pack_tree should be unchanged.
        assert a.pack_tree.allocated is False
        assert a.pack_tree.children is not None
        assert a.pack_tree.children[0].allocated is False
        assert a.pack_tree.children[1].allocated is True

        # Freeing the last board should remove it as before...
        a.free(id12)
        assert a.single_board_triads == {}
        assert a.full_single_board_triads == set()

        # The pack tree should now be empty
        assert a.pack_tree.allocated is False
        assert a.pack_tree.children is None

    def test_free_triad(self):
        a = Allocator(2, 1)

        id0, _0, _1, _2 = a._alloc_triads(1, 1)
        id1, _0, _1, _2 = a._alloc_triads(1, 1)

        # The pack tree should be full
        assert a.pack_tree.allocated is False
        assert a.pack_tree.children is not None
        assert a.pack_tree.children[0].allocated is True
        assert a.pack_tree.children[1].allocated is True

        # Triad should be freed
        a.free(id1)
        assert a.pack_tree.allocated is False
        assert a.pack_tree.children is not None
        assert a.pack_tree.children[0].allocated is True
        assert a.pack_tree.children[1].allocated is False

        # Full tree should be freed too
        a.free(id0)
        assert a.pack_tree.allocated is False
        assert a.pack_tree.children is None

    def test_all_triads_possible(self):
        a = Allocator(3, 4)

        # Fail too big
        assert a._alloc_triads_possible(4, 4) is False
        assert a._alloc_triads_possible(3, 5) is False
        assert a._alloc_triads_possible(4, 5) is False

        # Fail too small
        assert a._alloc_triads_possible(0, 0) is False
        assert a._alloc_triads_possible(0, 1) is False
        assert a._alloc_triads_possible(1, 0) is False

        # Fail torus wrong size
        assert a._alloc_triads_possible(2, 4, require_torus=True) is False
        assert a._alloc_triads_possible(3, 3, require_torus=True) is False
        assert a._alloc_triads_possible(2, 3, require_torus=True) is False

        # Fail due to (0, 0, 0) being dead
        a.dead_boards.add((0, 0, 0))
        assert a._alloc_triads_possible(3, 4) is False

        # Fail due to corners being dead and requiring nothing to be dead
        a.dead_boards.add((0, 3, 0))
        a.dead_boards.add((2, 0, 0))
        a.dead_boards.add((2, 3, 0))
        assert a._alloc_triads_possible(2, 2, max_dead_boards=0) is False

        # Fail due to all working links being required
        a.dead_boards = set()
        a.dead_links.add((0, 0, 0, Links.north))
        a.dead_links.add((0, 3, 0, Links.north))
        a.dead_links.add((2, 0, 0, Links.north))
        a.dead_links.add((2, 3, 0, Links.north))
        assert a._alloc_triads_possible(2, 2, max_dead_links=0) is False

        # Finally, should be possible to succeed when we relax the criteria
        assert a._alloc_triads_possible(2, 2) is True

    def test_all_boards_possible(self):
        a = Allocator(3, 4)

        # Fail too big
        assert a._alloc_boards_possible(4*4*3) is False

        # Fail too small
        assert a._alloc_boards_possible(0) is False
        assert a._alloc_boards_possible(-1) is False

        # Fail torus wrong size
        assert a._alloc_boards_possible(2*4*3, require_torus=True) is False

        # Fail due to (0, 0, 0) being dead
        a.dead_boards.add((0, 0, 0))
        assert a._alloc_boards_possible(3*4*3) is False

        # Fail due to corners being dead and requiring nothing to be dead
        a.dead_boards.add((0, 3, 0))
        a.dead_boards.add((2, 0, 0))
        a.dead_boards.add((2, 3, 0))
        assert a._alloc_boards_possible(2*2*3, max_dead_boards=0) is False

        # Fail due to all working links being required
        a.dead_boards = set()
        a.dead_links.add((0, 0, 0, Links.north))
        a.dead_links.add((0, 3, 0, Links.north))
        a.dead_links.add((2, 0, 0, Links.north))
        a.dead_links.add((2, 3, 0, Links.north))
        assert a._alloc_boards_possible(2*2*3, max_dead_links=0) is False

        # Finally, should be possible to succeed when we relax the criteria
        assert a._alloc_boards_possible(2*2*3) is True

    def test_alloc_board_possible(self):
        a = Allocator(2, 3)

        # Should fail if the board is outside the system
        assert a._alloc_board_possible(-1, 0, 0) is False
        assert a._alloc_board_possible(0, -1, 0) is False
        assert a._alloc_board_possible(-1, -1, 0) is False
        assert a._alloc_board_possible(0, 3, 0) is False
        assert a._alloc_board_possible(2, 0, 0) is False
        assert a._alloc_board_possible(2, 3, 0) is False
        assert a._alloc_board_possible(0, 0, -1) is False
        assert a._alloc_board_possible(0, 0, 3) is False

        # Should fail if the required board is dead
        a.dead_boards.add((0, 0, 1))
        assert a._alloc_board_possible(0, 0, 1) is False

        # Should fail if all the boards are dead
        a.dead_boards = set((x, y, z)
                            for x in range(2)
                            for y in range(3)
                            for z in range(3))
        assert a._alloc_board_possible() is False
        assert a._alloc_board_possible(0, 0, 0) is False

        a.dead_boards = set()

        # Otherwise should succeed
        assert a._alloc_board_possible() is True
        assert a._alloc_board_possible(0, 0, 0) is True

    @pytest.mark.parametrize("type", ["empty", "one", "specific"])
    @pytest.mark.parametrize("add_require_torus_false", [True, False])
    @pytest.mark.parametrize("max_dead_boards", [None, 0, 2])
    @pytest.mark.parametrize("max_dead_links", [None, 0, 2])
    def test_alloc_type_board(self, type, max_dead_boards, max_dead_links,
                              add_require_torus_false):
        a = Allocator(2, 3)

        if type == "empty":
            args = tuple()
        elif type == "one":
            args = (1, )
        else:  # type == "specific":
            args = (1, 1, 1)

        if add_require_torus_false:
            kwargs = {"require_torus": False}
        else:
            kwargs = {}

        assert a._alloc_type(*args,
                             max_dead_boards=max_dead_boards,
                             max_dead_links=max_dead_links,
                             **kwargs) is _AllocationType.board

    @pytest.mark.parametrize("specific", [True, False])
    @pytest.mark.parametrize("max_dead_boards", [None, 0, 2])
    @pytest.mark.parametrize("max_dead_links", [None, 0, 2])
    def test_alloc_type_board_bad(self, specific,
                                  max_dead_boards, max_dead_links):
        a = Allocator(2, 3)

        if specific:
            args = (3, 2, 1)
        else:
            args = tuple()

        with pytest.raises(ValueError):
            a._alloc_type(*args,
                          max_dead_boards=max_dead_boards,
                          max_dead_links=max_dead_links,
                          require_torus=True)

    @pytest.mark.parametrize("max_dead_boards", [None, 0, 2])
    @pytest.mark.parametrize("max_dead_links", [None, 0, 2])
    @pytest.mark.parametrize("require_torus", [True, False])
    def test_alloc_type_triads(self, max_dead_boards, max_dead_links,
                               require_torus):
        a = Allocator(2, 3)

        if require_torus:
            kwargs = {"require_torus": False}
        else:
            kwargs = {}

        assert a._alloc_type(1, 2,
                             max_dead_boards=max_dead_boards,
                             max_dead_links=max_dead_links,
                             **kwargs) \
            is _AllocationType.triads

    def test_alloc_possible(self):
        # Just make sure the wrapper calls the right functions...
        a = Allocator(9, 10, dead_boards=set([(3, 2, 1)]))

        # Allocating single boards
        assert a.alloc_possible() is True
        assert a.alloc_possible(1) is True
        assert a.alloc_possible(0, 0, 0) is True
        assert a.alloc_possible(3, 2, 1) is False

        # Allocating arbitrary numbers of boards
        assert a.alloc_possible(9*10*3) is True
        assert a.alloc_possible(9*10*3, min_ratio=1.0) is False
        assert a.alloc_possible(9*10*3 + 1) is False

        # Allocating triads
        assert a.alloc_possible(1, 1) is True
        assert a.alloc_possible(1, 1, require_torus=True) is False
        assert a.alloc_possible(9, 10, require_torus=True) is True

    def test_alloc(self):
        # Just make sure the wrapper calls the right functions...
        a = Allocator(9, 10)

        # Allocating boards
        assert len(a.alloc()[1]) == 1
        assert len(a.alloc(1)[1]) == 1
        assert len(a.alloc(5, 4, 0)[1]) == 1

        # Allocating numbers of boards
        assert len(a.alloc(2*3*3)[1]) == 2 * 3 * 3

        # Allocating triads
        assert len(a.alloc(2, 3)[1]) == 2 * 3 * 3
