import pytest

from mock import Mock

import random

from spalloc_server.pack_tree import PackTree, FreeError

def test_constructor():
    p = PackTree(1, 2, 3, 4)
    
    # Arguments should be kept
    assert p.x == 1
    assert p.y == 2
    assert p.width == 3
    assert p.height == 4
    
    # Default internal state should be set
    assert p.allocated is False
    assert p.children is None

@pytest.mark.parametrize("x,y,contains",
                         [(0, 0, False),
                          (1, 1, False),
                          (1, 2, True),
                          (3, 2, True),
                          (3, 5, True),
                          (4, 5, False),
                          (3, 6, False),
                          (4, 6, False),
                         ])
def test_contains(x, y, contains):
    p = PackTree(1, 2, 3, 4)
    assert ((x, y) in p) is contains


def test_hsplit():
    p = PackTree(1, 2, 3, 4)
    p.hsplit(y=3)
    
    assert p.children[0].x == 1
    assert p.children[0].y == 2
    assert p.children[0].width == 3
    assert p.children[0].height == 1
    
    assert p.children[1].x == 1
    assert p.children[1].y == 3
    assert p.children[1].width == 3
    assert p.children[1].height == 3


def test_vsplit():
    p = PackTree(1, 2, 3, 4)
    p.vsplit(x=3)
    
    assert p.children[0].x == 1
    assert p.children[0].y == 2
    assert p.children[0].width == 2
    assert p.children[0].height == 4
    
    assert p.children[1].x == 3
    assert p.children[1].y == 2
    assert p.children[1].width == 1
    assert p.children[1].height == 4


class TestFree(object):

    def test_first_one(self):
        p = PackTree(1, 2, 3, 4)
        p.allocated = True
        p.free(1, 2)
        assert p.allocated is False

    def test_first_one_but_not_allocated(self):
        p = PackTree(1, 2, 3, 4)
        with pytest.raises(FreeError):
            p.free(1, 2)

    @pytest.mark.parametrize("x,y", [(0, 0), (2, 3), (10, 10)])
    def test_no_children_no_match(self, x, y):
        # Should fail if we try to free something which isn't a leaf node
        p = PackTree(1, 2, 3, 4)
        with pytest.raises(FreeError):
            p.free(2, 3)
    
    def test_free_child(self):
        # Make sure we can match our children, even if they have the same
        # coordinate as us.
        p = PackTree(1, 2, 3, 4)
        p.hsplit(y=3)
        
        p.children[0].allocated = True
        p.children[1].allocated = True
        p.free(1, 2)
        assert p.children[0].allocated is False
        assert p.children[1].allocated is True
        
        p.children[0].allocated = True
        p.children[1].allocated = True
        p.free(1, 3)
        assert p.children[0].allocated is True
        assert p.children[1].allocated is False
    
    def test_cleanup(self):
        # Make sure that if a child is freed cleanup only occurs when empty
        
        # +----+----+
        # |    |    |
        # +----+    |
        # |    |    |
        # +----+----+
        p = PackTree(0, 0, 10, 10)
        p.vsplit(x=5)
        p.children[0].hsplit(y=5)
        
        p.children[0].children[0].allocated = True
        p.children[0].children[1].allocated = True
        p.children[1].allocated = True
        
        # If we free one of the grandchildren the tree should remain unchanged
        p.free(0, 0)
        assert p.children is not None
        assert p.children[0].children is not None
        assert p.children[0].children[0].children is None
        assert p.children[0].children[1].children is None
        assert p.children[1].children is None
        
        # If we free a top level child the tree should remain unchanged
        p.free(5, 0)
        assert p.children is not None
        assert p.children[0].children is not None
        assert p.children[0].children[0].children is None
        assert p.children[0].children[1].children is None
        assert p.children[1].children is None
        
        # If we free the remaining grandchild the whole tree should collapse
        p.free(0, 5)
        assert p.children is None


class TestAlloc(object):
    
    @pytest.mark.parametrize("w,h", [(5, 10), (10, 5), (10, 10)])
    def test_too_large(self, w, h):
        p = PackTree(0, 0, 9, 9)
        assert p.alloc(w, h) is None
    
    def test_full(self):
        p = PackTree(0, 0, 9, 9)
        p.allocated = True
        assert p.alloc(1, 1) is None
    
    @pytest.mark.parametrize("candidate_filter",
                             [None, Mock(return_value=True)])
    def test_exact_match(self, candidate_filter):
        p = PackTree(1, 2, 3, 4)
        assert p.alloc(3, 4, candidate_filter) == (1, 2)
        assert p.allocated is True
        assert p.children is None
        if candidate_filter is not None:
            candidate_filter.assert_called_once_with(1, 2, 3, 4)
    
    def test_exact_match_blocked(self):
        # If a candidate filter blocks the only match, we should fail
        p = PackTree(1, 2, 3, 4)
        candidate_filter = Mock(return_value=False)
        assert p.alloc(3, 4, candidate_filter) is None
        assert p.allocated is False
        assert p.children is None
        candidate_filter.assert_called_once_with(1, 2, 3, 4)

    @pytest.mark.parametrize("candidate_filter",
                             [None, Mock(return_value=True)])
    def test_fit_left(self, candidate_filter):
        # If we can fit exactly by splitting and putting on the left, we should
        # do so.
        p = PackTree(1, 2, 3, 4)
        assert p.alloc(1, 4, candidate_filter) == (1, 2)
        assert p.allocated is False
        assert p.children is not None
        assert p.children[0].width == 1
        assert p.children[0].height == 4
        assert p.children[0].allocated is True
        assert p.children[1].width == 2
        assert p.children[1].height == 4
        assert p.children[1].allocated is False
        if candidate_filter is not None:
            candidate_filter.assert_called_once_with(1, 2, 1, 4)

    @pytest.mark.parametrize("candidate_filter",
                             [None, Mock(return_value=True)])
    def test_fit_bottom(self, candidate_filter):
        # If we can fit exactly by splitting and putting on the bottom, we should
        # do so.
        p = PackTree(1, 2, 3, 4)
        assert p.alloc(3, 1, candidate_filter) == (1, 2)
        assert p.allocated is False
        assert p.children is not None
        assert p.children[0].width == 3
        assert p.children[0].height == 1
        assert p.children[0].allocated is True
        assert p.children[1].width == 3
        assert p.children[1].height == 3
        assert p.children[1].allocated is False
        if candidate_filter is not None:
            candidate_filter.assert_called_once_with(1, 2, 3, 1)

    def test_fit_right(self):
        # If we can fit exactly by splitting and putting on the left, we should
        # do so.
        p = PackTree(1, 2, 3, 4)
        candidate_filter = Mock(side_effect=[False, True])
        assert p.alloc(1, 4, candidate_filter) == (3, 2)
        assert p.allocated is False
        assert p.children is not None
        assert p.children[0].width == 2
        assert p.children[0].height == 4
        assert p.children[0].allocated is False
        assert p.children[1].width == 1
        assert p.children[1].height == 4
        assert p.children[1].allocated is True

    def test_fit_top(self):
        # If we can fit exactly by splitting and putting on the top, we should
        # do so.
        p = PackTree(1, 2, 3, 4)
        candidate_filter = Mock(side_effect=[False, True])
        assert p.alloc(3, 1, candidate_filter) == (1, 5)
        assert p.allocated is False
        assert p.children is not None
        assert p.children[0].width == 3
        assert p.children[0].height == 3
        assert p.children[0].allocated is False
        assert p.children[1].width == 3
        assert p.children[1].height == 1
        assert p.children[1].allocated is True

    def test_fit_v0_then_h0_split(self):
        # Make sure that if two splits are required, they are performed
        p = PackTree(1, 2, 3, 4)
        candidate_filter = Mock(side_effect=[True])
        assert p.alloc(1, 3, candidate_filter) == (1, 2)
        assert p.allocated is False
        assert p.children is not None
        assert p.children[0].width == 1
        assert p.children[0].height == 4
        assert p.children[0].allocated is False
        assert p.children[0].children is not None
        
        assert p.children[0].children[0].width == 1
        assert p.children[0].children[0].height == 3
        assert p.children[0].children[0].allocated is True
        
        assert p.children[0].children[1].width == 1
        assert p.children[0].children[1].height == 1
        assert p.children[0].children[1].allocated is False
        
        assert p.children[1].width == 2
        assert p.children[1].height == 4
        assert p.children[1].children is None
        assert p.children[1].allocated is False

    def test_fit_v0_then_h1_split(self):
        # Make sure that if two splits are required, they are performed
        p = PackTree(1, 2, 3, 4)
        candidate_filter = Mock(side_effect=[False, True])
        assert p.alloc(1, 3, candidate_filter) == (1, 3)
        assert p.allocated is False
        assert p.children is not None
        assert p.children[0].width == 1
        assert p.children[0].height == 4
        assert p.children[0].allocated is False
        assert p.children[0].children is not None
        
        assert p.children[0].children[0].width == 1
        assert p.children[0].children[0].height == 1
        assert p.children[0].children[0].allocated is False
        
        assert p.children[0].children[1].width == 1
        assert p.children[0].children[1].height == 3
        assert p.children[0].children[1].allocated is True
        
        assert p.children[1].width == 2
        assert p.children[1].height == 4
        assert p.children[1].children is None
        assert p.children[1].allocated is False

    def test_fit_h0_then_v0_split(self):
        # Make sure that if two splits are required, they are performed
        p = PackTree(1, 2, 4, 3)
        candidate_filter = Mock(side_effect=[True])
        assert p.alloc(3, 1, candidate_filter) == (1, 2)
        assert p.allocated is False
        assert p.children is not None
        assert p.children[0].width == 4
        assert p.children[0].height == 1
        assert p.children[0].allocated is False
        assert p.children[0].children is not None
        
        assert p.children[0].children[0].width == 3
        assert p.children[0].children[0].height == 1
        assert p.children[0].children[0].allocated is True
        
        assert p.children[0].children[1].width == 1
        assert p.children[0].children[1].height == 1
        assert p.children[0].children[1].allocated is False
        
        assert p.children[1].width == 4
        assert p.children[1].height == 2
        assert p.children[1].children is None
        assert p.children[1].allocated is False

    def test_fit_h0_then_v1_split(self):
        # Make sure that if two splits are required, they are performed
        p = PackTree(1, 2, 4, 3)
        candidate_filter = Mock(side_effect=[False, False, True])
        assert p.alloc(3, 1, candidate_filter) == (2, 2)
        assert p.allocated is False
        assert p.children is not None
        assert p.children[0].width == 4
        assert p.children[0].height == 1
        assert p.children[0].allocated is False
        assert p.children[0].children is not None
        
        assert p.children[0].children[0].width == 1
        assert p.children[0].children[0].height == 1
        assert p.children[0].children[0].allocated is False
        
        assert p.children[0].children[1].width == 3
        assert p.children[0].children[1].height == 1
        assert p.children[0].children[1].allocated is True
        
        assert p.children[1].width == 4
        assert p.children[1].height == 2
        assert p.children[1].children is None
        assert p.children[1].allocated is False
    
    def test_try_children(self):
        # Try inserting into the children, try the smallest chlid first
        p = PackTree(0, 0, 3, 1)
        p.vsplit(x=2)
        
        assert p.alloc(1, 1) == (2, 0)
        
        assert p.allocated is False
        assert p.children[0].allocated is False
        assert p.children[1].allocated is True
    
    def test_try_children_impossible(self):
        # Try inserting into the children which don't have room
        p = PackTree(0, 0, 3, 1)
        p.vsplit(x=2)
        
        assert p.alloc(3, 1) is None


class TestRequest(object):
    
    def test_outside(self):
        # If outside the region, should fail
        p = PackTree(1, 2, 3, 4)
        assert p.request(0, 0) is None
    
    def test_already_allocated(self):
        # Should fail if already allocated
        p = PackTree(1, 2, 3, 4)
        p.allocated = True
        assert p.request(1, 2) is None
        assert p.request(2, 3) is None
        
        # No dividing should have occurred...
        assert p.children is None
    
    def test_perfect_fit(self):
        p = PackTree(1, 2, 1, 1)
        assert p.request(1, 2) == (1, 2)
        assert p.allocated is True
        assert p.children is None
    
    def test_try_children(self):
        p = PackTree(1, 2, 2, 1)
        p.vsplit(x=2)
        assert p.request(1, 2) == (1, 2)
        
        assert p.allocated is False
        assert p.children is not None
        assert p.children[0].allocated is True
        assert p.children[0].children is None
        assert p.children[1].allocated is False
        assert p.children[1].children is None
        
        assert p.request(2, 2) == (2, 2)
        
        assert p.allocated is False
        assert p.children is not None
        assert p.children[0].allocated is True
        assert p.children[0].children is None
        assert p.children[1].allocated is True
        assert p.children[1].children is None

    def test_left_gap_only(self):
        p = PackTree(1, 2, 3, 1)
        
        assert p.request(3, 2) == (3, 2)
        
        assert p.allocated is False
        assert p.children is not None
        
        assert p.children[0].allocated is False
        assert p.children[0].width == 2
        assert p.children[0].height == 1
        assert p.children[0].children is None
        
        assert p.children[1].allocated is True
        assert p.children[1].width == 1
        assert p.children[1].height == 1
        assert p.children[1].children is None

    def test_right_gap_only(self):
        p = PackTree(1, 2, 3, 1)
        
        assert p.request(1, 2) == (1, 2)
        
        assert p.allocated is False
        assert p.children is not None
        
        assert p.children[0].allocated is True
        assert p.children[0].width == 1
        assert p.children[0].height == 1
        assert p.children[0].children is None
        
        assert p.children[1].allocated is False
        assert p.children[1].width == 2
        assert p.children[1].height == 1
        assert p.children[1].children is None

    def test_above_gap_only(self):
        p = PackTree(1, 2, 1, 4)
        
        assert p.request(1, 2) == (1, 2)
        
        assert p.allocated is False
        assert p.children is not None
        
        assert p.children[0].allocated is True
        assert p.children[0].width == 1
        assert p.children[0].height == 1
        assert p.children[0].children is None
        
        assert p.children[1].allocated is False
        assert p.children[1].width == 1
        assert p.children[1].height == 3
        assert p.children[1].children is None

    def test_below_gap_only(self):
        p = PackTree(1, 2, 1, 4)
        
        assert p.request(1, 5) == (1, 5)
        
        assert p.allocated is False
        assert p.children is not None
        
        assert p.children[0].allocated is False
        assert p.children[0].width == 1
        assert p.children[0].height == 3
        assert p.children[0].children is None
        
        assert p.children[1].allocated is True
        assert p.children[1].width == 1
        assert p.children[1].height == 1
        assert p.children[1].children is None

    def test_all_gaps(self):
        p = PackTree(0, 0, 10, 10)
        
        assert p.request(8, 6) == (8, 6)
        
        assert p.allocated is False
        assert p.children is not None
        
        assert p.children[0].allocated is False
        assert p.children[0].x == 0
        assert p.children[0].y == 0
        assert p.children[0].width == 8
        assert p.children[0].height == 10
        assert p.children[0].children is None
        
        assert p.children[1].allocated is False
        assert p.children[1].x == 8
        assert p.children[1].y == 0
        assert p.children[1].width == 2
        assert p.children[1].height == 10
        assert p.children[1].children is not None
        
        p = p.children[1]
        
        assert p.children[0].allocated is False
        assert p.children[0].x == 8
        assert p.children[0].y == 0
        assert p.children[0].width == 2
        assert p.children[0].height == 6
        assert p.children[0].children is None
        
        assert p.children[1].allocated is False
        assert p.children[1].x == 8
        assert p.children[1].y == 6
        assert p.children[1].width == 2
        assert p.children[1].height == 4
        assert p.children[1].children is not None
        
        p = p.children[1]
        
        assert p.children[0].allocated is False
        assert p.children[0].x == 8
        assert p.children[0].y == 6
        assert p.children[0].width == 2
        assert p.children[0].height == 1
        assert p.children[0].children is not None
                
        assert p.children[1].allocated is False
        assert p.children[1].x == 8
        assert p.children[1].y == 7
        assert p.children[1].width == 2
        assert p.children[1].height == 3
        assert p.children[1].children is None
        
        p = p.children[0]
        
        assert p.children[0].allocated is True
        assert p.children[0].x == 8
        assert p.children[0].y == 6
        assert p.children[0].width == 1
        assert p.children[0].height == 1
        assert p.children[0].children is None
                
        assert p.children[1].allocated is False
        assert p.children[1].x == 9
        assert p.children[1].y == 6
        assert p.children[1].width == 1
        assert p.children[1].height == 1
        assert p.children[1].children is None


class TestReasonableUsage(object):
    """Tests which ensure easy requirements can be met."""
    
    @pytest.mark.parametrize("w,h", [(1, 1), (2, 5), (5, 2),
                                     (10, 10), (10, 20),
                                     (5, 1), (10, 1), (1, 5), (1, 20)])
    def test_alloc_perfect_pack(self, w, h):
        # Should be able to allocate lots of the same sized block which have a
        # trivial perfect packing.
        W, H = 10, 20
        p = PackTree(0, 0, W, H)
        
        allocations = set()
        for _ in range((W * H) // (w * h)):
            allocation = p.alloc(w, h)
            assert allocation is not None
            assert allocation not in allocations
            allocations.add(allocation)
        
        # After allocating everything, no more should fit
        assert p.alloc(w, h) is None
        
        # After freeing everything, we should have a full square
        for x, y in allocations:
            p.free(x, y)
        
        assert p.allocated is False
        assert p.children is None
    
    
    def test_request_everything(self):
        # Should be able to request every point individually, in a random
        # order, and get them.
        w, h = 10, 20
        p = PackTree(0, 0, w, h)
        
        locations = [(x, y) for x in range(w) for y in range(h)]
        random.shuffle(locations)
        
        # Request every point in some order
        for x, y in locations:
            # Should not be able to do this more than once...
            assert p.request(x, y) == (x, y)
            assert p.request(x, y) is None
        
        # After allocating everything, no spaces should remain
        assert p.alloc(1, 1) is None
        
        # After freeing everything, we should have a full square
        random.shuffle(locations)
        for x, y in locations:
            p.free(x, y)
        
        assert p.allocated is False
        assert p.children is None
