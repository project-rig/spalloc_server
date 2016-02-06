import pytest

from spinn_partition.machine import Machine

from rig.links import Links


@pytest.fixture
def working_args():
    """A minimal set of valid arguments for a Machine's constructor."""
    return dict(
        name="m",
        tags=set("default"),
        width=2, height=1,
        dead_boards=set([(0, 0, 1)]),
        dead_links=set([(1, 0, 0, Links.north)]),
        board_locations={(x, y, z): (x*10, y*10, z*10)
                         for x in range(2)
                         for y in range(1)
                         for z in range(3)
                         # Skip the dead board
                         if (x, y, z) != (0, 0, 1)},
        bmp_ips={(c*10, f*10): "10.0.{}.{}".format(c, f)
                 for c in range(2)
                 for f in range(2)},  # Include some extra BMPs...
        spinnaker_ips={(x, y, z): "11.{}.{}.{}".format(x, y, z)
                       for x in range(2)
                       for y in range(2)  # Include some extra boards...
                       for z in range(3)
                       # Skip the dead board
                       if (x, y, z) != (0, 0, 1)},
    )


def test_valid_args(working_args):
    # Should not fail to validate something valid
    Machine(**working_args)


def test_bad_tags(working_args):
    working_args["tags"] = ["foo"]
    with pytest.raises(TypeError):
        Machine(**working_args)


def test_bad_dead_boards_type(working_args):
    working_args["dead_boards"] = [(0, 0, 0)]
    with pytest.raises(TypeError):
        Machine(**working_args)


def test_bad_dead_links_type(working_args):
    working_args["dead_links"] = [(0, 0, 0, Links.north)]
    with pytest.raises(TypeError):
        Machine(**working_args)


@pytest.mark.parametrize("x,y,z", [(2, 0, 0),
                                   (0, 1, 0),
                                   (0, 0, 3)])
def test_bad_dead_boards(working_args, x, y, z):
    # If any boards are out of range, should fail
    working_args["dead_boards"].add((x, y, z))
    with pytest.raises(ValueError):
        Machine(**working_args)


@pytest.mark.parametrize("x,y,z", [(2, 0, 0),
                                   (0, 1, 0),
                                   (0, 0, 3)])
def test_bad_dead_links(working_args, x, y, z):
    # If any links are out of range, should fail
    working_args["dead_links"].add((x, y, z, Links.north))
    with pytest.raises(ValueError):
        Machine(**working_args)


@pytest.mark.parametrize("x,y,z", [(2, 0, 0),
                                   (0, 1, 0),
                                   (0, 0, 3)])
def test_board_locations_in_machine(working_args, x, y, z):
    # If any live board locations are given for boards outside the system, we
    # should fail
    working_args["board_locations"][(x, y, z)] = (100, 100, 100)
    with pytest.raises(ValueError):
        Machine(**working_args)

def test_board_locations_no_duplicates(working_args):
    # No two boards should have the same location
    working_args["board_locations"][(0, 0, 0)] = (0, 0, 0)
    working_args["board_locations"][(0, 0, 1)] = (0, 0, 0)
    with pytest.raises(ValueError):
        Machine(**working_args)


def test_board_locations_defined(working_args):
    # If any live board locations are not given, we should fail. We reomve a
    # dead board whose location is otherwise not set
    working_args["dead_boards"].clear()
    with pytest.raises(ValueError):
        Machine(**working_args)


def test_bmp_ips_defined(working_args):
    # All boards whose location is specified should have a BMP IP
    del working_args["bmp_ips"][(0, 0)]
    with pytest.raises(ValueError):
        Machine(**working_args)
