import pytest

import tempfile
import os

from spalloc_server.configuration import \
    Configuration, Machine, board_locations_from_spinner

from rig.links import Links


@pytest.yield_fixture
def spinner_ethernet_chips_csv():
    """Produces a CSV file containing the output of 'spinner-ethernet-chips -n
    6'.
    """
    fd, filename = tempfile.mkstemp(".csv")
    with open(filename, "w") as f:
        f.write("cabinet,frame,board,x,y\n"
                "0,0,0,0,0\n"
                "0,0,2,4,8\n"
                "0,0,4,8,4\n"
                "0,0,5,12,0\n"
                "0,0,3,16,8\n"
                "0,0,1,20,4\n")
    
    yield filename
    
    os.remove(filename)


def test_machine(name="m", bmp_prefix=None, spinnaker_prefix=None):
    """A minimal set of valid arguments for a Machine's constructor."""
    bmp_prefix = bmp_prefix or "bmp_{}".format(name)
    spinnaker_prefix = spinnaker_prefix or "spinn_{}".format(name)
    return Machine(
        name=name, tags=set("default"),
        width=2, height=1, dead_boards=set(), dead_links=set(),
        board_locations={(x, y, z): (x*10, y*10, z*10)
                         for x in range(2)
                         for y in range(1)
                         for z in range(3)},
        bmp_ips={(c*10, f*10): "{}_{}_{}".format(bmp_prefix, c, f)
                 for c in range(2)
                 for f in range(1)},
        spinnaker_ips={(x, y, z): "{}_{}_{}_{}".format(spinnaker_prefix,
                                                       x, y, z)
                       for x in range(2)
                       for y in range(1)
                       for z in range(3)},
    )


def test_sensible_defaults():
    c = Configuration()
    
    assert c.machines == []
    assert c.port == 22244
    assert c.ip == ""
    assert c.timeout_check_interval == 5.0
    assert c.max_retired_jobs == 1200


def test_machine_type():
    with pytest.raises(TypeError):
        Configuration(machines=["m"])


def test_no_collisions():
    machines = [test_machine()]
    c = Configuration(machines=machines)
    assert c.machines == machines
    
    machines = [test_machine("a"), test_machine("b")]
    c = Configuration(machines=machines)
    assert c.machines == machines


def test_name_collision():
    with pytest.raises(ValueError):
        machines = [test_machine("a"), test_machine("b"), test_machine("a")]
        Configuration(machines=machines)


def test_bmp_ip_collision():
    with pytest.raises(ValueError):
        machines = [test_machine("a", bmp_prefix="bmp"),
                    test_machine("b", bmp_prefix="bmp")]
        Configuration(machines=machines)


def test_spinnaker_ip_collision():
    with pytest.raises(ValueError):
        machines = [test_machine("a", spinnaker_prefix="bmp"),
                    test_machine("b", spinnaker_prefix="bmp")]
        Configuration(machines=machines)

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


def test_spinnaker_ips_defined(working_args):
    # All boards whose location is specified should have a BMP IP
    del working_args["spinnaker_ips"][(0, 0, 0)]
    with pytest.raises(ValueError):
        Machine(**working_args)

def test_infer_width_and_height(working_args):
    del working_args["width"]
    del working_args["height"]
    m = Machine(**working_args)
    assert m.width == 2
    assert m.height == 1

def test_bad_infer_width_and_height(working_args):
    a = working_args.copy()
    del a["width"]
    with pytest.raises(TypeError):
        Machine(**a)
    
    a = working_args.copy()
    del a["height"]
    with pytest.raises(TypeError):
        Machine(**a)

def test_single_board():
    m = Machine.single_board("m", set(["default"]), "bmp", "spinn")
    assert m.name == "m"
    assert m.tags == set(["default"])
    assert m.width == 1
    assert m.height == 1
    assert m.dead_boards == set([(0, 0, 1), (0, 0, 2)])
    assert m.dead_links == set()
    assert m.board_locations == {(0, 0, 0): (0, 0, 0)}
    assert m.bmp_ips == {(0, 0): "bmp"}
    assert m.spinnaker_ips == {(0, 0, 0): "spinn"}

def test_single_board_no_ip():
    with pytest.raises(TypeError):
        Machine.single_board("m", set(["default"]))
    with pytest.raises(TypeError):
        Machine.single_board("m", set(["default"]), bmp_ip="foo")
    with pytest.raises(TypeError):
        Machine.single_board("m", set(["default"]), spinnaker_ip="bar")


def test_with_standard_ips():
    board_locations = {(x, y, z): (x, y, z)
                       for x in range(2)
                       for y in range(2)
                       for z in range(3)}
    
    m = Machine.with_standard_ips("m", board_locations=board_locations)
    
    assert m.bmp_ips == {
        (0, 0): "192.168.0.0",
        (0, 1): "192.168.1.0",
        (1, 0): "192.168.5.0",
        (1, 1): "192.168.6.0",
    }
    
    assert m.spinnaker_ips == {
        (0, 0, 0): "192.168.0.1",
        (0, 0, 1): "192.168.0.9",
        (0, 0, 2): "192.168.0.17",
        
        (0, 1, 0): "192.168.1.1",
        (0, 1, 1): "192.168.1.9",
        (0, 1, 2): "192.168.1.17",
        
        (1, 0, 0): "192.168.5.1",
        (1, 0, 1): "192.168.5.9",
        (1, 0, 2): "192.168.5.17",
        
        (1, 1, 0): "192.168.6.1",
        (1, 1, 1): "192.168.6.9",
        (1, 1, 2): "192.168.6.17",
    }

def test_with_standard_ips_bad_ias():
    board_locations = {(x, y, z): (x, y, z)
                       for x in range(2)
                       for y in range(2)
                       for z in range(3)}
    
    # Not IPv4 address
    with pytest.raises(ValueError):
        Machine.with_standard_ips("m", board_locations=board_locations,
                                  base_ip="spinn-4")
    
    # Malformed IPv4 addresses
    with pytest.raises(ValueError):
        Machine.with_standard_ips("m", board_locations=board_locations,
                                  base_ip="1.2.3")
    with pytest.raises(ValueError):
        Machine.with_standard_ips("m", board_locations=board_locations,
                                  base_ip="-1.2.3.4")
    with pytest.raises(ValueError):
        Machine.with_standard_ips("m", board_locations=board_locations,
                                  base_ip="256.2.3.4")

def test_board_locations_from_spinner(spinner_ethernet_chips_csv):
    assert board_locations_from_spinner(spinner_ethernet_chips_csv) == {
        (0, 0, 0): (0, 0, 0),
        (0, 0, 1): (0, 0, 4),
        (0, 0, 2): (0, 0, 2),
        (1, 0, 0): (0, 0, 5),
        (1, 0, 1): (0, 0, 1),
        (1, 0, 2): (0, 0, 3),
    }
