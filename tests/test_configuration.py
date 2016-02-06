import pytest

from spinn_partition.machine import Machine
from spinn_partition.configuration import Configuration


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
    assert c.port == 10203
    assert c.ip == ""
    assert c.timeout_check_interval == 5.0
    assert c.max_retired_jobs == 1200


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
