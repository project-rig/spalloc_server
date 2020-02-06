# Copyright (c) 2017-2019 The University of Manchester
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from collections import OrderedDict
from datetime import datetime
import pickle
import pytest
import threading
import time
from pytz import utc
from six import iteritems
from .mocker import Mock
from spalloc_server.coordinates import board_down_link
from spalloc_server.configuration import Machine
from spalloc_server.controller import Controller, JobState
from spalloc_server.links import Links
from .common import simple_machine
from spalloc_server.async_bmp_controller import AtomicRequests

pytestmark = pytest.mark.usefixtures("mock_abc")


@pytest.fixture
def on_background_state_change():
    """The callback called by on_background_state_change."""
    return Mock()


@pytest.yield_fixture
def conn(mock_abc, on_background_state_change):
    """Auto-stop a controller."""
    conn = Controller(max_retired_jobs=2,
                      on_background_state_change=on_background_state_change,
                      seconds_before_free=0.1)
    try:
        yield conn
    finally:
        conn.stop()
        conn.join()


@pytest.fixture
def m(conn):
    """Add a simple 1x2 machine to the controller."""
    conn.machines = {"m": simple_machine("m", 1, 2)}
    return "m"


@pytest.fixture
def big_m(conn):
    """Add a larger 4x2 machine to the controller."""
    conn.machines = {"big_m": simple_machine("big_m", 4, 2)}
    return "big_m"


@pytest.fixture
def big_m_with_hole(conn):
    """Add a larger 4x2 machine to the controller with board (2, 1, 0) dead."""
    conn.machines = {
        "big_m": Machine.with_standard_ips(
            name="big_m",
            dead_boards=set([(2, 1, 0)]),
            board_locations={
                (x, y, z): (x*10, y*10, z*10)
                for x in range(4)
                for y in range(2)
                for z in range(3)
                if (x, y, z) != (2, 1, 0)
            }),
    }
    return "big_m"


@pytest.mark.timeout(1.0)
@pytest.mark.parametrize("expected_success", [True, False])
def test_mock_abc(mock_abc, expected_success):
    """Meta testing: make sure the MockAsyncBMPController works."""
    # Make sure callbacks are called from another thread
    threads = []

    def cb(success):
        threads.append(threading.current_thread())
        assert success is expected_success

    abc = mock_abc("foo")
    abc.success = expected_success

    assert abc.running_theads == 1

    with abc:
        # If made atomically, should all fire at the end...
        requests = AtomicRequests(cb)
        requests.power(None, None)
        requests.link(None, None, None)
        abc.add_requests(requests)

        assert abc.add_requests_calls == [requests]

        # Make sure the background thread gets some execution time
        time.sleep(0.05)

        assert threads == []

    # Make sure the background thread gets some execution time
    time.sleep(0.05)

    assert len(threads) == 1
    assert threads[0] != threading.current_thread()

    abc.stop()
    abc.join()

    assert abc.running_theads == 0


def test_controller_basic_stop_join(conn):
    """Make sure we can stop/join all threads started by a controller with no
    machines."""
    assert isinstance(conn, Controller)


def test_controller_set_machines(conn, mock_abc):
    # Test the ability to add machines

    # Create a set of machines
    machines = OrderedDict()
    for num in range(3):
        m = Machine(name="m{}".format(num),
                    tags=set(["default", "num{}".format(num)]),
                    width=1 + num, height=2,
                    dead_boards=set([(0, 0, 1)]),
                    dead_links=set([(0, 0, 2, Links.north)]),
                    board_locations={(x, y, z): (x*10, y*10, z*10)
                                     for x in range(1 + num)
                                     for y in range(2)
                                     for z in range(3)},
                    bmp_ips={(c*10, f*10): "10.1.{}.{}".format(c, f)
                             for c in range(1 + num)
                             for f in range(2)},
                    spinnaker_ips={(x, y, z): "11.{}.{}.{}".format(x, y, z)
                                   for x in range(1 + num)
                                   for y in range(2)
                                   for z in range(3)})
        machines[m.name] = m
    m0 = machines["m0"]
    m1 = machines["m1"]
    m2 = machines["m2"]

    # Special case: Setting no machines should not break anything
    machines = OrderedDict()
    conn.machines = machines
    assert len(conn._machines) == 0
    assert len(conn._job_queue._machines) == 0
    assert len(conn._bmp_controllers) == 0
    assert mock_abc.running_theads == 0

    # Try adding a pair of machines
    machines["m1"] = m1
    machines["m0"] = m0
    conn.machines = machines

    # Check that the set of machines copies across
    assert conn._machines == machines

    # Make sure things are passed into the job queue correctly
    assert list(conn._job_queue._machines) == ["m1", "m0"]
    assert conn._job_queue._machines["m0"].tags == m0.tags
    assert conn._job_queue._machines["m0"].allocator.dead_boards \
        == m0.dead_boards
    assert conn._job_queue._machines["m0"].allocator.dead_links \
        == m0.dead_links

    assert conn._job_queue._machines["m1"].tags == m1.tags
    assert conn._job_queue._machines["m1"].allocator.dead_boards \
        == m1.dead_boards
    assert conn._job_queue._machines["m1"].allocator.dead_links \
        == m1.dead_links

    # Make sure BMP controllers are spun-up correctly
    assert len(conn._bmp_controllers) == 2
    assert len(conn._bmp_controllers["m0"]) == m0.width * m0.height
    assert len(conn._bmp_controllers["m1"]) == m1.width * m1.height
    for m_name, controllers in iteritems(conn._bmp_controllers):
        for c in range(machines[m_name].width):
            for f in range(machines[m_name].height):
                assert controllers[(c*10, f*10)].hostname \
                    == "10.1.{}.{}".format(c, f)
    assert mock_abc.running_theads == mock_abc.num_created == (
        (m1.width * m1.height) + (m0.width * m0.height))

    # If we pass in the same machines in, nothing should get changed
    conn.machines = machines
    assert conn._machines == machines
    assert list(conn._job_queue._machines) == list(machines)
    assert mock_abc.running_theads == mock_abc.num_created == (
        (m1.width * m1.height) + (m0.width * m0.height))

    # If we pass in the same machines in a different order, the order should
    # change but nothing should get spun up/down
    machines = OrderedDict()
    machines["m0"] = m0
    machines["m1"] = m1
    conn.machines = machines
    assert conn._machines == machines
    assert list(conn._job_queue._machines) == list(machines)
    assert mock_abc.running_theads == mock_abc.num_created == (
        (m1.width * m1.height) + (m0.width * m0.height))

    # Adding a new machine should spin just one new machine up leaving the
    # others unchanged
    machines = OrderedDict()
    machines["m0"] = m0
    machines["m1"] = m1
    machines["m2"] = m2
    conn.machines = machines
    assert conn._machines == machines
    assert list(conn._job_queue._machines) == list(machines)
    assert mock_abc.running_theads == mock_abc.num_created == (
        m2.width * m2.height + m1.width * m1.height + m0.width * m0.height)

    # Modifying a machine in minor ways: should not respin anything but the
    # change should be applied
    m0 = Machine(name=m0.name,
                 tags=set(["new tags"]),
                 width=m0.width, height=m0.height,
                 dead_boards=set([(0, 0, 0)]),
                 dead_links=set([(0, 0, 0, Links.south)]),
                 board_locations=m0.board_locations,
                 bmp_ips=m0.bmp_ips,
                 spinnaker_ips=m0.spinnaker_ips)
    machines["m0"] = m0
    conn.machines = machines

    # Machine list should be updated
    assert conn._machines == machines

    # Job queue should be updated
    assert list(conn._job_queue._machines) == list(machines)
    assert conn._job_queue._machines["m0"].tags == set(["new tags"])
    assert conn._job_queue._machines["m0"].allocator.dead_boards \
        == set([(0, 0, 0)])
    assert conn._job_queue._machines["m0"].allocator.dead_links \
        == set([(0, 0, 0, Links.south)])

    # Nothing should be spun up
    assert mock_abc.running_theads == mock_abc.num_created == (
        m2.width * m2.height + m1.width * m1.height + m0.width * m0.height)

    # Removing a machine should result in things being spun down
    del machines["m0"]
    conn.machines = machines

    # Machine list should be updated
    assert conn._machines == machines

    # Job queue should be updated
    assert list(conn._job_queue._machines) == list(machines)

    # Some BMPs should now be shut down
    time.sleep(0.05)
    assert mock_abc.running_theads == (
        (m2.width * m2.height) + (m1.width * m1.height))

    # Nothing new should be spun up
    assert mock_abc.num_created == (
        m2.width * m2.height + m1.width * m1.height + m0.width * m0.height)

    # Making any significant change to a machine should result in it being
    # re-spun.
    m1 = Machine(name=m1.name,
                 tags=m1.tags,
                 width=m1.width - 1,  # A significant change(!)
                 height=m1.height,
                 dead_boards=m1.dead_boards,
                 dead_links=m1.dead_links,
                 board_locations=m0.board_locations,
                 bmp_ips=m0.bmp_ips,
                 spinnaker_ips=m0.spinnaker_ips)
    machines["m1"] = m1

    m1_alloc_before = conn._job_queue._machines["m1"].allocator
    m2_alloc_before = conn._job_queue._machines["m2"].allocator

    conn.machines = machines

    m1_alloc_after = conn._job_queue._machines["m1"].allocator
    m2_alloc_after = conn._job_queue._machines["m2"].allocator

    # Machine list should be updated
    assert conn._machines == machines
    time.sleep(0.05)

    # Job queue should be updated and a new allocator etc. made for the new
    # machine
    assert list(conn._job_queue._machines) == list(machines)
    assert m1_alloc_before is not m1_alloc_after
    assert m2_alloc_before is m2_alloc_after

    # Same number of BMPs should be up
    assert mock_abc.running_theads == (
        (m2.width * m2.height) + (m1.width * m1.height))

    # But a new M1 should be spun up
    assert mock_abc.num_created == (
        (m2.width * m2.height) + ((m1.width + 1) * m1.height) +
        (m1.width * m1.height) + (m0.width * m0.height))


def test_set_machines_sequencing(conn):
    """Correct sequencing must be observed between old machines being spun down
    and new machines being spun up.
    """
    m0 = simple_machine("m0", 1, 1)
    m1 = simple_machine("m1", 1, 1)

    # If m0 is taking a long time to do much...
    conn.machines = {"m0": m0}
    controller_m0 = conn._bmp_controllers["m0"][(0, 0)]
    with controller_m0.handler_lock:
        conn.create_job(None, owner="me", keepalive=60.0)

        # ... and we swap the machine out for another ...
        conn.machines = {"m1": m1}

        # Our newly created job should be blocked in power-on until the
        # previous machine finishes shutting down.
        job_id = conn.create_job(None, owner="me", keepalive=60.0)
        time.sleep(0.1)
        assert conn.get_job_state(None, job_id).state is JobState.power

    # The old machine machine's controller has now finally finished what it was
    # doing, this should unblock the new machine and in turn let our job start
    time.sleep(0.05)
    assert conn.get_job_state(None, job_id).state is JobState.ready


def test_get_machines(conn, m):
    # Should return a copy... No surprise changes allowed!
    assert conn.machines == conn._machines
    assert conn.machines is not conn._machines


def test_create_job(conn, m):
    controller = conn._bmp_controllers[m][(0, 0)]

    with controller.handler_lock:
        # Make sure newly added jobs can start straight away and have the BMP
        # block
        job_id1 = conn.create_job(None, 1, 1, owner="me", keepalive=60.0)

        # Sane default keepalive should be selected
        assert conn._jobs[job_id1].keepalive == 60.0

        # BMPs should have been told to power-on
        assert all(set(requests.power_on_boards) == set(range(3))
                   for requests in controller.add_requests_calls)

        # Links around the allocation should have been disabled
        assert all(_e is False
                   for requests in controller.add_requests_calls
                   for _b, _l, _e in requests.link_requests)
        assert (  # pragma: no branch
            set((_b, _l)
                for requests in controller.add_requests_calls
                for _b, _l, _e in requests.link_requests) ==
            set((_b, _l)
                for _b in range(3) for _l in Links
                if board_down_link(0, 0, _b, _l, 1, 2)[:2] != (0, 0))
        )

        # Job should be waiting for power-on since the BMP is blocked
        time.sleep(0.05)
        assert conn.get_job_state(None, job_id1).state is JobState.power

    # Job should be powered on once the BMP process returns
    time.sleep(0.05)
    assert conn.get_job_state(None, job_id1).state is JobState.ready

    # Adding another job which will be queued should result in a job in the
    # right state.
    job_id2 = conn.create_job(None, 1, 2, owner="me", keepalive=10.0)
    assert conn._jobs[job_id2].keepalive == 10.0
    assert job_id1 != job_id2
    assert conn.get_job_state(None, job_id2).state is JobState.queued

    # Adding a job which cannot fit should come out immediately cancelled
    job_id3 = conn.create_job(None, 2, 2, owner="me", keepalive=60.0)
    assert job_id1 != job_id3 and job_id2 != job_id3
    assert conn.get_job_state(None, job_id3).state is JobState.destroyed
    assert conn.get_job_state(None, job_id3).reason \
        == "Cancelled: No suitable machines available."


@pytest.mark.timeout(1.0)
def test_destroy_timed_out_jobs(conn, m):
    job_id = conn.create_job(None, owner="me", keepalive=0.1)

    # This job should not get timed out even though we never prod it
    job_id_forever = conn.create_job(None, owner="me", keepalive=None)

    # Make sure that jobs can be kept alive on demand
    for _ in range(4):
        time.sleep(0.05)
        conn.destroy_timed_out_jobs()
        conn.job_keepalive(None, job_id)
    assert conn.get_job_state(None, job_id).state == JobState.ready

    # Make sure jobs are kept alive by checking their state
    for _ in range(4):
        time.sleep(0.05)
        conn.destroy_timed_out_jobs()
        assert conn.get_job_state(None, job_id).state == JobState.ready

    # Make sure jobs can timeout
    time.sleep(0.15)
    conn.destroy_timed_out_jobs()
    assert conn.get_job_state(None, job_id).state == JobState.destroyed
    assert conn.get_job_state(None, job_id).reason == "Job timed out."

    # No amount polling should bring it back to life...
    conn.job_keepalive(None, job_id)
    assert conn.get_job_state(None, job_id).state == JobState.destroyed

    # Non-keepalive job should not have been destroyed
    assert conn.get_job_state(None, job_id_forever).state == JobState.ready


def test_get_job_state(conn, m):
    job_id1 = conn.create_job("1.2.3.4", owner="me", keepalive=123.0)
    conn._jobs[job_id1].start_time = 1234.5

    # Allow Mock BMP time to respond
    time.sleep(0.05)

    # Status should be reported for jobs that are alive
    job_state = conn.get_job_state(None, job_id1)
    assert job_state.state is JobState.ready
    assert job_state.power is True
    assert job_state.keepalive == 123.0
    assert job_state.reason is None
    assert job_state.start_time == 1234.5
    assert job_state.keepalivehost == "1.2.3.4"

    # If the job is killed, this should be reported.
    conn.machines = {}
    job_state = conn.get_job_state(None, job_id1)
    assert job_state.state is JobState.destroyed
    assert job_state.power is None
    assert job_state.keepalive is None
    assert job_state.reason == "Machine removed."
    assert job_state.start_time is None

    # Jobs which are not live should be kept but eventually forgotten
    job_id2 = conn.create_job(None, owner="me", keepalive=123.0)
    assert conn.get_job_state(None, job_id1).state is JobState.destroyed
    assert conn.get_job_state(None, job_id2).state is JobState.destroyed

    job_id3 = conn.create_job(None, owner="me", keepalive=123.0)
    assert conn.get_job_state(None, job_id1).state is JobState.unknown
    assert conn.get_job_state(None, job_id2).state is JobState.destroyed
    assert conn.get_job_state(None, job_id3).state is JobState.destroyed

    # Jobs which don't exist should report as unknown
    job_state = conn.get_job_state(None, 1234)
    assert job_state.state is JobState.unknown
    assert job_state.power is None
    assert job_state.keepalive is None
    assert job_state.reason is None
    assert job_state.start_time is None


@pytest.mark.timeout(1.0)
def test_get_job_machine_info(conn, m):
    job_id = conn.create_job(None, 1, 1, owner="me", keepalive=60.0)
    w, h, connections, machine_name, boards = \
        conn.get_job_machine_info(None, job_id)
    assert w == 12
    assert h == 16
    assert machine_name == m
    assert connections == {
        (0, 0): "11.0.0.0",
        (8, 4): "11.0.0.1",
        (4, 8): "11.0.0.2",
    }
    assert boards == set([(0, 0, 0), (0, 0, 1), (0, 0, 2)])

    # Bad ID should just get Nones
    assert conn.get_job_machine_info(None, 1234) == (
        None, None, None, None, None)


@pytest.mark.timeout(1.0)
@pytest.mark.parametrize("args,width,height",
                         [([], 8, 8),         # Single board
                          ([1, 1], 16, 16),   # Isolated triad
                          ([2, 1], 24, 16),   # A strip of torus
                          ([2, 3], 24, 36)])  # Torus
def test_get_job_machine_info_width_height(conn, args, width, height):
    conn.machines = {"m": simple_machine("m", 2, 3)}

    job_id = conn.create_job(None, *args, owner="me", keepalive=60.0)
    w, h, _connections, _machine_name, _boards = \
        conn.get_job_machine_info(None, job_id)

    assert w == width
    assert h == height


def test_power_on_job_boards(conn, m):
    job_id = conn.create_job(None, owner="me", keepalive=60.0)

    # Allow the boards time to power on
    time.sleep(0.05)

    controller = conn._bmp_controllers[m][(0, 0)]
    controller.add_requests_calls = []

    conn.power_on_job_boards(None, job_id)

    # Should make a request
    assert len(controller.add_requests_calls) == 1
    requests = controller.add_requests_calls[0]

    # Should power cycle
    assert requests.power_on_boards == [0]

    # Should re-set up the links
    assert len(requests.link_requests) == 6
    assert all(
        e is False for _b, _l, e in requests.link_requests)
    assert (  # pragma: no branch
        set((0, _l) for _b, _l, e in requests.link_requests) ==
        set((0, _l) for _l in Links)
    )

    # Shouldn't crash for non-existent job
    conn.power_on_job_boards(None, 1234)

    # Should not do anything for pending job
    job_id_pending = conn.create_job(None, 1, 2, owner="me", keepalive=60.0)
    conn.power_on_job_boards(None, job_id_pending)


def test_power_off_job_boards(conn, m):
    job_id = conn.create_job(None, owner="me", keepalive=60.0)

    # Allow the boards time to power on
    time.sleep(0.05)

    controller = conn._bmp_controllers[m][(0, 0)]
    controller.add_requests_calls = []

    conn.power_off_job_boards(None, job_id)

    # Should add a request
    assert len(controller.add_requests_calls) == 1
    requests = controller.add_requests_calls[0]

    # Should power cycle
    assert requests.power_off_boards == [0]

    # Should not touch the links
    assert len(requests.link_requests) == 0

    # Shouldn't crash for non-existent job
    conn.power_off_job_boards(None, 1234)

    # Should not do anything for pending job
    job_id_pending = conn.create_job(None, 1, 2, owner="me", keepalive=60.0)
    conn.power_off_job_boards(None, job_id_pending)


def test_destroy_job(conn, m):
    controller0 = conn._bmp_controllers[m][(0, 0)]
    controller1 = conn._bmp_controllers[m][(0, 1)]

    job_id1 = conn.create_job(None, 1, 2, owner="me", keepalive=60.0)
    job_id2 = conn.create_job(None, 1, 2, owner="me", keepalive=60.0)

    controller0.add_requests_calls = []
    controller1.add_requests_calls = []

    # Should be able to kill queued jobs (and reasons should be prefixed to
    # indicate the job never started)
    conn.destroy_job(None, job_id2, reason="Because.")
    assert conn.get_job_state(None, job_id2).state is JobState.destroyed
    assert conn.get_job_state(None, job_id2).reason == "Cancelled: Because."

    # ...without powering anything down
    assert controller0.add_requests_calls == []
    assert controller1.add_requests_calls == []

    # Should be able to kill live jobs
    conn.destroy_job(None, job_id1, reason="Because you too.")
    assert conn.get_job_state(None, job_id1).state is JobState.destroyed
    assert conn.get_job_state(None, job_id1).reason == "Because you too."

    # ...powering anything down that was in use
    assert len(controller0.add_requests_calls) == 1
    assert len(controller1.add_requests_calls) == 1
    assert len(controller0.add_requests_calls[0].power_on_boards) == 0
    assert len(controller1.add_requests_calls[0].power_on_boards) == 0
    assert (set(controller0.add_requests_calls[0].power_off_boards) ==
            set(range(3)))
    assert (set(controller1.add_requests_calls[0].power_off_boards) ==
            set(range(3)))
    assert controller0.add_requests_calls[0].link_requests == []
    assert controller1.add_requests_calls[0].link_requests == []

    # Shouldn't fail on bad job ids
    conn.destroy_job(None, 1234)


def test_list_jobs(conn, m):
    job_id1 = conn.create_job(None, owner="me", keepalive=60.0)
    job_id2 = conn.create_job(None, 1, 2, require_torus=True, owner="you",
                              keepalive=None)
    time.sleep(0.05)

    jobs = conn.list_jobs()

    assert jobs[0].job_id == job_id1
    assert jobs[1].job_id == job_id2

    assert jobs[0].owner == "me"
    assert jobs[1].owner == "you"

    now = datetime.now(utc)
    epoch = datetime(1970, 1, 1, tzinfo=utc)
    unixtime_now = (now - epoch).total_seconds()
    assert unixtime_now - 1.0 <= jobs[0].start_time <= unixtime_now
    assert unixtime_now - 1.0 <= jobs[1].start_time <= unixtime_now

    assert jobs[0].keepalive == 60.0
    assert jobs[1].keepalive is None

    assert jobs[0].state is JobState.ready
    assert jobs[1].state is JobState.queued

    assert jobs[0].args == tuple()
    assert jobs[1].args == (1, 2)

    assert jobs[0].kwargs == {}
    assert jobs[1].kwargs == {"require_torus": True}

    assert jobs[0].allocated_machine_name == m
    assert jobs[1].allocated_machine_name is None

    assert jobs[0].boards == set([(0, 0, 0)])
    assert jobs[1].boards is None


def test_list_machines(conn):
    machines = OrderedDict([
        ("m0", simple_machine("m0", 1, 1)),
        ("m1", simple_machine("m1", 1, 2, tags=set(["foo", "bar"]),
                              dead_boards=set([(0, 0, 1)]),
                              dead_links=set([(0, 0, 0, Links.west)]))),
    ])
    conn.machines = machines

    machine_list = conn.list_machines()

    assert machine_list[0].name == "m0"
    assert machine_list[1].name == "m1"

    assert machine_list[0].tags == set(["default"])
    assert machine_list[1].tags == set(["foo", "bar"])

    assert machine_list[0].width == 1
    assert machine_list[1].width == 1

    assert machine_list[0].height == 1
    assert machine_list[1].height == 2

    assert machine_list[0].dead_boards == set()
    assert machine_list[1].dead_boards == set([(0, 0, 1)])

    assert machine_list[0].dead_links == set()
    assert machine_list[1].dead_links == set([(0, 0, 0, Links.west)])


def test_get_board_position(conn):
    conn.machines = {"m": simple_machine("m", 1, 1)}

    assert conn.get_board_position("bad", 0, 0, 0) is None

    assert conn.get_board_position("m", 0, 0, 0) == (0, 0, 0)
    assert conn.get_board_position("m", 0, 0, 1) == (0, 0, 1)
    assert conn.get_board_position("m", 0, 0, 2) == (0, 0, 2)

    assert conn.get_board_position("m", 1, 0, 0) is None
    assert conn.get_board_position("m", 0, 1, 0) is None
    assert conn.get_board_position("m", 0, 0, 3) is None


def test_get_board_at_position(conn):
    conn.machines = {"m": simple_machine("m", 1, 1)}

    assert conn.get_board_at_position("bad", 0, 0, 0) is None

    assert conn.get_board_at_position("m", 0, 0, 0) == (0, 0, 0)
    assert conn.get_board_at_position("m", 0, 0, 1) == (0, 0, 1)
    assert conn.get_board_at_position("m", 0, 0, 2) == (0, 0, 2)

    assert conn.get_board_at_position("m", 1, 0, 0) is None
    assert conn.get_board_at_position("m", 0, 1, 0) is None
    assert conn.get_board_at_position("m", 0, 0, 3) is None


class TestWhereIs(object):

    def test_bad_arguments(self, conn):
        with pytest.raises(TypeError):
            conn.where_is()
        with pytest.raises(TypeError):
            conn.where_is(machine="m", x=0, y=0)  # z missing

    def test_failed_search(self, conn, big_m):
        # Searches that don't find anything useful should return None
        bad = "bad"
        assert conn.where_is(machine=bad, chip_x=0, chip_y=0) is None
        assert conn.where_is(machine=bad, cabinet=0, frame=0, board=0) is None
        assert conn.where_is(machine=bad, x=0, y=0, z=0) is None
        assert conn.where_is(machine=big_m, cabinet=9001, frame=9001,
                             board=9001) is None

    @pytest.mark.parametrize("chip_xy,logical",
                             [((0, 0), (0, 0, 0)),
                              ((1, 1), (0, 0, 0)),
                              ((3, 3), (0, 0, 0)),
                              ((7, 7), (0, 0, 0)),
                              ((0, 4), (3, 0, 1)),
                              ((44, 4), (3, 0, 1)),
                              ((3, 11), (3, 0, 1)),
                              ((5, 0), (0, 1, 2)),
                              ((4, 20), (0, 1, 2)),
                              ((0, 12), (0, 1, 0)),
                              ((36, 12), (3, 1, 0)),
                              ((-1, -1), (3, 1, 2))])
    def test_return_logical_board(self, conn, big_m, chip_xy, logical):
        x, y = chip_xy
        assert conn.where_is(machine=big_m,
                             chip_x=x, chip_y=y)["logical"] == logical

    def test_return_machine(self, conn, big_m):
        assert conn.where_is(machine=big_m,
                             chip_x=0, chip_y=0)["machine"] == big_m

    @pytest.mark.parametrize("chip_xy_in,chip_xy_out",
                             [((0, 0), (0, 0)),
                              ((32, 10), (32, 10)),
                              ((-1, 0), (47, 0)),
                              ((0, -1), (0, 23)),
                              ((0, 24), (0, 0)),
                              ((48, 0), (0, 0))])
    def test_return_chip_xy(self, conn, big_m, chip_xy_in, chip_xy_out):
        x, y = chip_xy_in
        assert conn.where_is(machine=big_m,
                             chip_x=x, chip_y=y)["chip"] == chip_xy_out

    @pytest.mark.parametrize("chip_xy,board_chip_xy",
                             [((0, 0), (0, 0)),
                              ((4, 7), (4, 7)),
                              ((7, 7), (7, 7)),
                              ((7, 4), (7, 4)),
                              ((8, 4), (0, 0)),
                              ((4, 8), (0, 0)),
                              ((-1, -1), (7, 3))])
    def test_return_board_chip_xy(self, conn, big_m, chip_xy, board_chip_xy):
        x, y = chip_xy
        assert conn.where_is(machine=big_m,
                             chip_x=x, chip_y=y)["board_chip"] == board_chip_xy

    def test_missing_physical_board(self, conn, big_m_with_hole):
        assert conn.where_is(machine=big_m_with_hole,
                             chip_x=24, chip_y=12) is None

    @pytest.mark.parametrize("chip_xy,physical",
                             [((0, 0), (0, 0, 0)),
                              ((1, 1), (0, 0, 0)),
                              ((3, 3), (0, 0, 0)),
                              ((7, 7), (0, 0, 0)),
                              ((0, 4), (30, 0, 10)),
                              ((44, 4), (30, 0, 10)),
                              ((3, 11), (30, 0, 10)),
                              ((5, 0), (0, 10, 20)),
                              ((4, 20), (0, 10, 20)),
                              ((0, 12), (0, 10, 0)),
                              ((36, 12), (30, 10, 0)),
                              ((-1, -1), (30, 10, 20))])
    def test_return_physical_board(self, conn, big_m_with_hole,
                                   chip_xy, physical):
        x, y = chip_xy
        assert conn.where_is(machine=big_m_with_hole,
                             chip_x=x, chip_y=y)["physical"] == physical

    @pytest.mark.parametrize("chip_xy,job_id",
                             [((0, 0), 1),
                              ((0, 12), 1),
                              ((8, 23), 1),
                              ((19, 19), 1),
                              ((8, 2), 1),
                              ((0, 4), None),
                              ((3, 23), None),
                              ((40, 20), 2),
                              ((-1, -1), 2),
                              ((-1, 0), 2),
                              ((-1, 4), None)])
    def test_return_job_id(self, conn, big_m, chip_xy, job_id):
        x, y = chip_xy
        assert conn.create_job(None, 2, 2, owner="me", keepalive=60.0) == 1
        assert conn.create_job(None, 3, 1, 2, owner="me", keepalive=60.0) == 2
        assert conn.where_is(machine=big_m,
                             chip_x=x, chip_y=y)["job_id"] == job_id

    @pytest.mark.parametrize("chip_xy,job_chip",
                             [((0, 0), (0, 0)),
                              ((1, 1), (1, 1)),
                              ((4, 8), (4, 8)),
                              ((8, 4), (8, 4)),
                              ((8, 2), (8, 2)),
                              ((23, 23), (23, 23)),
                              ((27, 23), (27, 23)),
                              ((28, 23), None),
                              ((24, 0), None),
                              ((0, 4), None),
                              ((24, 12), None),
                              ((40, 20), (0, 0)),
                              ((-1, -1), (7, 3)),
                              ((41, 0), (1, 4))])
    def test_return_job_chip(self, conn, big_m, chip_xy, job_chip):
        x, y = chip_xy
        conn.create_job(None, 2, 2, owner="me", keepalive=60.0)
        conn.create_job(None, 3, 1, 2, owner="me", keepalive=60.0)
        assert conn.where_is(machine=big_m,
                             chip_x=x, chip_y=y)["job_chip"] == job_chip

    @pytest.mark.parametrize("logical,chip_xy",
                             [((0, 0, 0), (0, 0)),
                              ((0, 0, 1), (8, 4)),
                              ((0, 0, 2), (4, 8)),
                              ((3, 1, 2), (40, 20)),
                              ((-1, -1, 2), (40, 20))])
    def test_input_logical(self, conn, big_m, logical, chip_xy):
        x, y, z = logical
        assert conn.where_is(machine=big_m,
                             x=x, y=y, z=z)["chip"] == chip_xy

    def test_input_missing_board(self, conn, big_m_with_hole):
        assert conn.where_is(machine=big_m_with_hole,
                             cabinet=20, frame=10, board=0) is None

    @pytest.mark.parametrize("physical,chip_xy",
                             [((0, 0, 0), (0, 0)),
                              ((0, 0, 10), (8, 4)),
                              ((0, 0, 20), (4, 8)),
                              ((30, 10, 20), (40, 20))])
    def test_input_physical(self, conn, big_m_with_hole, physical, chip_xy):
        c, f, b = physical
        assert conn.where_is(machine=big_m_with_hole,
                             cabinet=c,
                             frame=f,
                             board=b)["chip"] == chip_xy

    @pytest.mark.parametrize("job_id,job_chip_xy",
                             [(3, (0, 0)),  # No job
                              (1, (24, 12)),  # Out of range
                              (1, (0, 4))])  # Neighbouring board
    def test_input_bad_job_chip(self, conn, big_m_with_hole,
                                job_id, job_chip_xy):
        assert conn.create_job(None, 2, 2, owner="me", keepalive=60.0) == 1
        assert conn.create_job(None, 2, 1, owner="me", keepalive=60.0) == 2

        x, y = job_chip_xy
        assert conn.where_is(job_id=job_id, chip_x=x, chip_y=y) is None

    @pytest.mark.parametrize("job_id,job_chip_xy,chip_xy",
                             [(1, (0, 0), (0, 0)),
                              (1, (27, 23), (27, 23)),
                              (2, (0, 0), (24, 0)),
                              (2, (4, 8), (28, 8)),
                              (2, (11, 15), (35, 15))])
    def test_input_job_chip(self, conn, big_m_with_hole,
                            job_id, job_chip_xy, chip_xy):
        assert conn.create_job(None, 2, 2, owner="me", keepalive=60.0) == 1
        assert conn.create_job(None, 2, 1, owner="me", keepalive=60.0) == 2

        x, y = job_chip_xy
        loc = conn.where_is(job_id=job_id, chip_x=x, chip_y=y)
        assert loc["machine"] == big_m_with_hole
        assert loc["chip"] == chip_xy


@pytest.mark.parametrize("success,mid_state,end_state",
                         [(True, JobState.unknown, JobState.ready),
                          (False, JobState.destroyed, JobState.destroyed)])
def test_bmp_on_request_complete(mock_abc, conn, m,
                                 success, mid_state, end_state):
    job_id = conn.create_job(None, owner="me", keepalive=60.0)
    job = conn._jobs[job_id]

    # Give the BMPs time to run (which use the bmp_requests_until_ready...)
    time.sleep(0.05)

    # Test the function while holding the lock to make sure we are the only one
    # interacting with the job
    with conn._lock:
        job.state = JobState.unknown

        # The function should simply decrement the counter until it reaches
        # zero at which point it should flag the object as "Ready" unless
        # something has gone wrong in which case the job should immediately
        # become destroyed.
        assert job.bmp_requests_until_ready == 0
        job.bmp_requests_until_ready = 5
        for request_num in range(5):
            if request_num == 0:
                # Should initially be unknown
                assert conn.get_job_state(
                    None, job_id).state == JobState.unknown
            else:
                assert conn.get_job_state(None, job_id).state == mid_state
            conn._bmp_on_request_complete(job, success)

        assert conn.get_job_state(None, job_id).state == end_state


@pytest.mark.parametrize("power", [True, False])
@pytest.mark.parametrize("link_enable", [True, False, None])
def test_set_job_power_and_links(mock_abc, conn, m, power, link_enable):
    job_id = conn.create_job(None, owner="me", keepalive=60.0)
    job = conn._jobs[job_id]

    # Allow BMPs time to power on the board...
    time.sleep(0.05)

    controller = conn._bmp_controllers[m][(0, 0)]
    controller.add_requests_calls = []

    # Send the command while holding the lock to make sure we see the number of
    # BMP requests expected before a BMP gets chance to decrement the counter.
    with conn._lock:
        # Send the command
        conn._set_job_power_and_links(job, power, link_enable)

        # Job should be placed in power state
        assert job.state is JobState.power
        assert job.power is power

        # Make sure the correct number of completions is requested
        assert job.bmp_requests_until_ready == 1

        # Make sure the correct power commands were sent
        assert len(controller.add_requests_calls) == 1
        if power:
            assert controller.add_requests_calls[0].power_on_boards == [0]
        else:
            assert controller.add_requests_calls[0].power_off_boards == [0]

        if link_enable is not None:
            assert len(controller.add_requests_calls[0].link_requests) == 6
            assert all(b == 0 for b, l, e in
                       controller.add_requests_calls[0].link_requests)
            assert all(e is link_enable for b, l, e in
                       controller.add_requests_calls[0].link_requests)
            assert set(l for b, l, e in
                       controller.add_requests_calls[0].link_requests) ==\
                set(Links)
        else:
            assert len(controller.add_requests_calls[0].link_requests) == 0


def test_pickle(mock_abc):
    # Create a controller, with a running job and some threads etc.
    conn = Controller()
    try:
        conn.machines = {"m": simple_machine("m", 1, 2)}
        job_id = conn.create_job(None, owner="me", keepalive=60.0)
        time.sleep(0.05)
        assert conn.get_job_state(None, job_id).state == JobState.ready

        assert mock_abc.running_theads == 2
    finally:
        # Pickling the controller should succeed
        conn.stop()
        conn.join()

    assert mock_abc.running_theads == 0

    pickled_conn = pickle.dumps(conn)
    del conn

    # Unpickling should succeed
    conn2 = pickle.loads(pickled_conn)
    try:

        # And some BMP connections should be running again
        assert mock_abc.running_theads == 2

        # And our job should still be there
        assert conn2.get_job_state(None, job_id).state == JobState.ready
    finally:
        conn2.stop()
        conn2.join()


def test_max_retired_jobs(conn):
    # Should be able to access the number of retired jobs
    assert conn.max_retired_jobs == 2
    conn.max_retired_jobs = 3
    assert conn.max_retired_jobs == 3

    # Should have an effect. Create a number of impossible jobs which will be
    # immediately retired.
    job_id0 = conn.create_job(None, owner="me", keepalive=60.0)
    job_id1 = conn.create_job(None, owner="me", keepalive=60.0)
    job_id2 = conn.create_job(None, owner="me", keepalive=60.0)
    job_id3 = conn.create_job(None, owner="me", keepalive=60.0)

    assert conn.get_job_state(None, job_id0).state == JobState.unknown
    assert conn.get_job_state(None, job_id1).state == JobState.destroyed
    assert conn.get_job_state(None, job_id2).state == JobState.destroyed
    assert conn.get_job_state(None, job_id3).state == JobState.destroyed

    # Should be able to change the value and have some jobs be forgotten
    # disappear
    conn.max_retired_jobs = 1
    assert conn.get_job_state(None, job_id0).state == JobState.unknown
    assert conn.get_job_state(None, job_id1).state == JobState.unknown
    assert conn.get_job_state(None, job_id2).state == JobState.unknown
    assert conn.get_job_state(None, job_id3).state == JobState.destroyed

    # Special case: 0
    conn.max_retired_jobs = 0
    assert conn.get_job_state(None, job_id0).state == JobState.unknown
    assert conn.get_job_state(None, job_id1).state == JobState.unknown
    assert conn.get_job_state(None, job_id2).state == JobState.unknown
    assert conn.get_job_state(None, job_id3).state == JobState.unknown

    conn.create_job(None, owner="me", keepalive=60.0)
    assert conn.get_job_state(None, job_id3).state == JobState.unknown


def test_changed_jobs(conn, m):
    # Make sure changes to job state are tracked
    assert conn.changed_jobs == set()

    controller = conn._bmp_controllers[m][(0, 0)]

    with controller.handler_lock:
        # Job allocation (pre-power on)
        job_id0 = conn.create_job(None, 1, 2, owner="me", keepalive=60.0)
        assert conn.changed_jobs == set([job_id0])
        assert conn.changed_jobs == set()

    time.sleep(0.05)

    # Power-on complete
    assert conn.changed_jobs == set([job_id0])
    assert conn.changed_jobs == set()

    # Job queued
    job_id1 = conn.create_job(None, owner="me", keepalive=60.0)
    assert conn.changed_jobs == set([job_id1])
    assert conn.changed_jobs == set()

    # Job freed, another job un-queued but awaiting power-on
    conn.destroy_job(None, job_id0)

    # Sleep to allow job to process
    time.sleep(0.2)

    with controller.handler_lock:
        # Update free and check
        conn.check_free()
        assert conn.changed_jobs == set([job_id0, job_id1])
        assert conn.changed_jobs == set()

    time.sleep(0.05)

    # Job now powered-on
    assert conn.changed_jobs == set([job_id1])
    assert conn.changed_jobs == set()

    with controller.handler_lock:
        # Job power-control should result in event
        conn.power_off_job_boards(None, job_id1)
        assert conn.changed_jobs == set([job_id1])
        assert conn.changed_jobs == set()

    time.sleep(0.05)

    # Post power-off...
    assert conn.changed_jobs == set([job_id1])
    assert conn.changed_jobs == set()

    # Impossible job should just fail
    job_id2 = conn.create_job(None, 2, 3, owner="me", keepalive=60.0)
    assert conn.changed_jobs == set([job_id2])
    assert conn.changed_jobs == set()


def test_changed_machines(conn):
    # Make sure changes to machines are tracked
    assert conn.changed_jobs == set()

    # Adding machines
    conn.machines = {"m0": simple_machine("m0", 1, 2),
                     "m1": simple_machine("m1", 1, 2)}
    assert conn.changed_machines == set(["m0", "m1"])
    assert conn.changed_machines == set()

    # Changing machines slightly
    conn.machines = {"m0": simple_machine("m0", 1, 2, tags=set(["foo"])),
                     "m1": simple_machine("m1", 1, 2)}
    assert conn.changed_machines == set(["m0"])
    assert conn.changed_machines == set()

    # Changing machines significantly
    conn.machines = {"m0": simple_machine("m0", 1, 2, tags=set(["foo"])),
                     "m1": simple_machine("m1", 2, 2)}
    assert conn.changed_machines == set(["m1"])
    assert conn.changed_machines == set()

    # Remvoing machines
    conn.machines = {"m1": simple_machine("m1", 2, 2)}
    assert conn.changed_machines == set(["m0"])
    assert conn.changed_machines == set()

    # Allocating jobs
    job_id = conn.create_job(None, owner="me", keepalive=60.0)
    assert conn.changed_machines == set(["m1"])
    assert conn.changed_machines == set()

    # Not power cycling jobs...
    conn.power_off_job_boards(None, job_id)
    time.sleep(0.05)
    assert conn.changed_machines == set()

    # Destroying jobs
    conn.destroy_job(None, job_id)
    assert conn.changed_machines == set(["m1"])
    assert conn.changed_machines == set()


def test_on_background_state_change(conn, m, on_background_state_change):
    controller0 = conn._bmp_controllers[m][(0, 0)]
    controller1 = conn._bmp_controllers[m][(0, 1)]
    assert len(on_background_state_change.mock_calls) == 0

    with controller0.handler_lock:
        with controller1.handler_lock:
            job_id = conn.create_job(None, 1, 2, owner="me", keepalive=60.0)

            # Should not get called because not powered on yet
            time.sleep(0.05)
            assert len(on_background_state_change.mock_calls) == 0

        # Should not get called because still not powered on yet
        time.sleep(0.05)
        assert len(on_background_state_change.mock_calls) == 0

    # Should now be called since power on should have finished
    time.sleep(0.05)
    assert len(on_background_state_change.mock_calls) == 1

    # Should be able to unregister said callback
    assert conn.on_background_state_change is on_background_state_change
    conn.on_background_state_change = None
    assert conn.on_background_state_change is None

    # Shouldn't break anything
    conn.destroy_job(None, job_id)
