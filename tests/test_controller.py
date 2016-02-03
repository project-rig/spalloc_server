import pytest

from mock import Mock

import threading

import time

from collections import deque, OrderedDict

from rig.links import Links

import pickle

from six import itervalues, iteritems

from spinn_partition.coordinates import board_down_link
from spinn_partition.controller import Controller, Machine, JobState


def simple_machine(name, width, height, tags=set(["default"]),
                   dead_boards=None, dead_links=None):
    """Construct a simple machine with nothing broken etc."""
    return Machine(name=name, tags=tags, width=width, height=height,
                   dead_boards=dead_boards or set(),
                   dead_links=dead_links or set(),
                   board_locations={(x, y, z): (x, y, z)
                                    for x in range(width)
                                    for y in range(height)
                                    for z in range(3)},
                   bmp_ips={(x, y): "10.1.{}.{}".format(x, y)
                            for x in range(width)
                            for y in range(height)},
                   spinnaker_ips={(x, y, z): "11.{}.{}.{}".format(x, y, z)
                                  for x in range(width)
                                  for y in range(height)
                                  for z in range(3)})


@pytest.fixture
def MockABC(monkeypatch):
    """This test fixture mocks-out the AsyncBMPController class with a shim
    which immediately calls the supplied callbacks via another thread (to
    ensure no inter-thread calling issues).
    """
    class MockAsyncBMPController():
        
        running_theads = 0
        
        num_created = 0
        
        def __init__(self, hostname, on_thread_start=None):
            MockAsyncBMPController.num_created += 1
            
            self.hostname = hostname
            self.on_thread_start = on_thread_start
            
            # Reports if the object was locked by its context manager
            self.locked = 0
            
            # A lock which must be held when handling a request (may be used to
            # simulate the machine taking some amount of time to respond)
            self.handler_lock = threading.Lock()
            
            self._lock = threading.RLock()
            self._event = threading.Event()
            self._request_queue = deque()
            self._stop = False
            
            self._thread = threading.Thread(target=self._run)
            MockAsyncBMPController.running_theads += 1
            self._thread.start()
            
            # Protected only by the GIL...
            self.set_power_calls = []
            self.set_link_enable_calls = []
        
        def _run(self):
            """Background thread, just take things from the request queue and
            call them.
            """
            try:
                if self.on_thread_start is not None:
                    self.on_thread_start()
                while not self._stop:
                    self._event.wait()
                    with self.handler_lock:
                        with self._lock:
                            while self._request_queue:
                                self._request_queue.popleft()()
                            self._event.clear()
            finally:
                MockAsyncBMPController.running_theads -= 1
        
        def __enter__(self):
            self._lock.acquire()
            self.locked += 1
        
        def __exit__(self, type=None, value=None, traceback=None):
            self.locked -= 1
            self._lock.release()
        
        def set_power(self, board, state, on_done):
            self.set_power_calls.append((board, state, on_done))
            with self._lock:
                assert not self._stop
                self._request_queue.append(on_done)
                self._event.set()
        
        def set_link_enable(self, board, link, enable, on_done):
            self.set_link_enable_calls.append((board, link, enable, on_done))
            with self._lock:
                
                assert not self._stop
                self._request_queue.append(on_done)
                self._event.set()
        
        def stop(self):
            with self._lock:
                self._stop = True
                self._event.set()
        
        def join(self):
            self._thread.join()
    
    import spinn_partition.controller
    monkeypatch.setattr(spinn_partition.controller, "AsyncBMPController",
                        MockAsyncBMPController)
    
    return MockAsyncBMPController

@pytest.fixture
def timeout_check_interval():
    """A list containing the timeout checking interval to use."""
    return [5.0]


@pytest.fixture
def timeout_0_2(timeout_check_interval):
    """Set the timeout interval to 0.2 seconds."""
    timeout_check_interval[0] = 0.2


@pytest.yield_fixture
def conn(MockABC, timeout_check_interval):
    """Auto-stop a controller."""
    conn = Controller(timeout_check_interval=timeout_check_interval[0],
                      max_retired_jobs=2)
    yield conn
    conn.stop()
    conn.join()

@pytest.fixture
def m(conn):
    """Add a simple 1x2 machine to the controller."""
    conn.set_machines({"m": simple_machine("m", 1, 2)})
    return "m"


@pytest.mark.timeout(1.0)
def test_mock_abc(MockABC):
    """Meta testing: make sure the MockAsyncBMPController works."""
    # Make sure callbacks are called from another thread
    threads = []
    
    def cb():
        threads.append(threading.current_thread())
    
    abc = MockABC("foo")
    
    assert abc.running_theads == 1
    
    with abc:
        # If made atomically, should all fire at the end...
        abc.set_power(None, None, cb)
        abc.set_link_enable(None, None, None, cb)
        
        assert abc.set_power_calls == [(None, None, cb)]
        assert abc.set_link_enable_calls == [(None, None, None, cb)]
        
        # Make sure the background thread gets some execution time
        time.sleep(0.05)
        
        assert threads == []
    
    # Make sure the background thread gets some execution time
    time.sleep(0.05)
    
    assert len(threads) == 2
    assert threads[0] != threading.current_thread()
    assert threads[1] != threading.current_thread()
    
    abc.stop()
    abc.join()
    
    assert abc.running_theads == 0


def test_controller_basic_stop_join(conn):
    """Make sure we can stop/join all threads started by a controller with no
    machines."""
    assert isinstance(conn, Controller)


def test_controller_basic_stop_join(conn):
    """Make sure we can stop/join all threads started by a controller with no
    machines."""
    assert isinstance(conn, Controller)


def test_controller_set_machines(conn, MockABC):
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
                    bmp_ips={(x, y): "10.1.{}.{}".format(x, y)
                             for x in range(1 + num)
                             for y in range(2)},
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
    conn.set_machines(machines)
    assert len(conn._machines) == 0
    assert len(conn._job_queue._machines) == 0
    assert len(conn._bmp_controllers) == 0
    assert MockABC.running_theads == 0
    
    # Try adding a pair of machines
    machines["m1"] = m1
    machines["m0"] = m0
    conn.set_machines(machines)
    
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
                assert controllers[(c, f)].hostname \
                    == "10.1.{}.{}".format(c, f)
    assert MockABC.running_theads \
        == MockABC.num_created \
        == ((m1.width * m1.height) + (m0.width * m0.height))
    
    # If we pass in the same machines in, nothing should get changed
    conn.set_machines(machines)
    assert conn._machines == machines
    assert list(conn._job_queue._machines) == list(machines)
    assert MockABC.running_theads \
        == MockABC.num_created \
        == ((m1.width * m1.height) + (m0.width * m0.height))
    
    # If we pass in the same machines in a different order, the order should
    # change but nothing should get spun up/down
    machines = OrderedDict()
    machines["m0"] = m0
    machines["m1"] = m1
    conn.set_machines(machines)
    assert conn._machines == machines
    assert list(conn._job_queue._machines) == list(machines)
    assert MockABC.running_theads \
        == MockABC.num_created \
        == ((m1.width * m1.height) + (m0.width * m0.height))
    
    # Adding a new machine should spin just one new machine up leaving the
    # others unchanged
    machines = OrderedDict()
    machines["m0"] = m0
    machines["m1"] = m1
    machines["m2"] = m2
    conn.set_machines(machines)
    assert conn._machines == machines
    assert list(conn._job_queue._machines) == list(machines)
    assert MockABC.running_theads \
        == MockABC.num_created \
        == ((m2.width * m2.height) +
            (m1.width * m1.height) +
            (m0.width * m0.height))
    
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
    conn.set_machines(machines)
    
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
    assert MockABC.running_theads \
        == MockABC.num_created \
        == ((m2.width * m2.height) +
            (m1.width * m1.height) +
            (m0.width * m0.height))
    
    # Removing a machine should result in things being spun down
    del machines["m0"]
    conn.set_machines(machines)
    
    # Machine list should be updated
    assert conn._machines == machines
    
    # Job queue should be updated
    assert list(conn._job_queue._machines) == list(machines)
    
    # Some BMPs should now be shut down
    time.sleep(0.05)
    assert MockABC.running_theads \
        == ((m2.width * m2.height) +
            (m1.width * m1.height))
    
    # Nothing new should be spun up
    assert MockABC.num_created \
        == ((m2.width * m2.height) +
            (m1.width * m1.height) +
            (m0.width * m0.height))
    
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
    
    conn.set_machines(machines)
    
    m1_alloc_after = conn._job_queue._machines["m1"].allocator
    m2_alloc_after = conn._job_queue._machines["m2"].allocator
    
    # Machine list should be updated
    assert conn._machines == machines
    
    # Job queue should be updated and a new allocator etc. made for the new
    # machine
    assert list(conn._job_queue._machines) == list(machines)
    assert m1_alloc_before is not m1_alloc_after
    assert m2_alloc_before is m2_alloc_after
    
    # Same number of BMPs should be up
    assert MockABC.running_theads \
        == ((m2.width * m2.height) +
            (m1.width * m1.height))
    
    # But a new M1 should be spun up
    assert MockABC.num_created \
        == ((m2.width * m2.height) +
            ((m1.width + 1) * m1.height) +
            (m1.width * m1.height) +
            (m0.width * m0.height))


def test_set_machines_sequencing(conn):
    """Correct sequencing must be observed between old machines being spun down
    and new machines being spun up.
    """
    m0 = simple_machine("m0", 1, 1)
    m1 = simple_machine("m1", 1, 1)
    
    # If m0 is taking a long time to do much...
    conn.set_machines({"m0": m0})
    controller_m0 = conn._bmp_controllers["m0"][(0, 0)]
    with controller_m0.handler_lock:
        conn.create_job(owner="me")
        
        # ... and we swap the machine out for another ...
        conn.set_machines({"m1": m1})
        
        # Our newly created job should be blocked in power-on until the
        # previous machine finishes shutting down.
        job_id = conn.create_job(owner="me")
        time.sleep(0.05)
        assert conn.get_job_state(job_id)[0] is JobState.power
    
    # The old machine machine's controller has now finally finished what it was
    # doing, this should unblock the new machine and in turn let our job start
    time.sleep(0.05)
    assert conn.get_job_state(job_id)[0] is JobState.ready


def test_create_job(conn, m):
    controller = conn._bmp_controllers[m][(0, 0)]
    
    with controller.handler_lock:
        # Make sure newly added jobs can start straight away and have the BMP block
        job_id1 = conn.create_job(1, 1, owner="me")
        
        # Sane default keepalive should be selected
        assert conn._jobs[job_id1].keepalive == 60.0
        
        # BMPs should have been told to power-on
        assert all(s is True for b, s, f in controller.set_power_calls)
        assert set(b for b, s, f in controller.set_power_calls) \
            == set(range(3))
        
        # Links around the allocation should have been disabled
        assert all(e is False for b, l, e, f in controller.set_link_enable_calls)
        assert set((b, l) for b, l, e, f in controller.set_link_enable_calls) \
            == set((b, l) for b in range(3) for l in Links
                   if board_down_link(0, 0, b, l, 1, 2)[:2] != (0, 0))
        
        # Job should be waiting for power-on since the BMP is blocked
        time.sleep(0.05)
        assert conn.get_job_state(job_id1)[0] is JobState.power
    
    # Job should be powered on once the BMP process returns
    time.sleep(0.05)
    assert conn.get_job_state(job_id1)[0] is JobState.ready
    
    # Adding another job which will be queued should result in a job in the
    # right state.
    job_id2 = conn.create_job(1, 2, owner="me", keepalive=10.0)
    assert conn._jobs[job_id2].keepalive == 10.0
    assert job_id1 != job_id2
    assert conn.get_job_state(job_id2)[0] is JobState.queued
    
    # Adding a job which cannot fit should come out immediately cancelled
    job_id3 = conn.create_job(2, 2, owner="me")
    assert job_id1 != job_id3 and job_id2 != job_id3
    assert conn.get_job_state(job_id3)[0] is JobState.destroyed
    assert conn.get_job_state(job_id3)[2] \
        == "Cancelled: No suitable machines available."

def test_job_keepalive(timeout_0_2, conn, m):
    job_id = conn.create_job(owner="me", keepalive=0.1)
    
    # This job should not get timed out even though we never prod it
    job_id_forever = conn.create_job(owner="me", keepalive=None)
    
    # Make sure that jobs can be kept alive on demand
    for _ in range(4):
        time.sleep(0.05)
        conn.job_keepalive(job_id)
    assert conn.get_job_state(job_id)[0] == JobState.ready
    
    # Make sure jobs are kept alive by checking their state
    for _ in range(4):
        time.sleep(0.05)
        assert conn.get_job_state(job_id)[0] == JobState.ready
    
    # Make sure jobs can timeout
    time.sleep(0.25)
    assert conn.get_job_state(job_id)[0] == JobState.destroyed
    assert conn.get_job_state(job_id)[2] == "Job timed out."
    
    # No amount polling should bring it back to life...
    conn.job_keepalive(job_id)
    assert conn.get_job_state(job_id)[0] == JobState.destroyed
    
    # Non-keepalive job should not have been destroyed
    assert conn.get_job_state(job_id_forever)[0] == JobState.ready

def test_get_job_state(conn, m):
    job_id1 = conn.create_job(owner="me", keepalive=123.0)
    
    # Allow Mock BMP time to respond
    time.sleep(0.05)
    
    # Status should be reported for jobs that are alive
    state, keepalive, reason = conn.get_job_state(job_id1)
    assert state is JobState.ready
    assert keepalive == 123.0
    assert reason is None
    
    # If the job is killed, this should be reported.
    conn.set_machines({})
    state, keepalive, reason = conn.get_job_state(job_id1)
    assert state is JobState.destroyed
    assert keepalive is None
    assert reason == "Machine removed."
    
    # Jobs which are not live should be kept but eventually forgotten
    job_id2 = conn.create_job(owner="me", keepalive=123.0)
    assert conn.get_job_state(job_id1)[0] is JobState.destroyed
    assert conn.get_job_state(job_id2)[0] is JobState.destroyed
    
    job_id3 = conn.create_job(owner="me", keepalive=123.0)
    assert conn.get_job_state(job_id1)[0] is JobState.unknown
    assert conn.get_job_state(job_id2)[0] is JobState.destroyed
    assert conn.get_job_state(job_id3)[0] is JobState.destroyed
    
    # Jobs which don't exist should report as unknown
    state, keepalive, reason = conn.get_job_state(1234)
    assert state is JobState.unknown
    assert keepalive is None
    assert reason is None


def test_get_job_ethernet_connections(conn, m):
    job_id = conn.create_job(1, 1, owner="me")
    connections, machine_name = conn.get_job_ethernet_connections(job_id)
    assert machine_name == m
    assert connections == {
        (0, 0): "11.0.0.0",
        (8, 4): "11.0.0.1",
        (4, 8): "11.0.0.2",
    }
    
    # Bad ID should just get Nones
    assert conn.get_job_ethernet_connections(1234) == (None, None)


def test_power_on_job_boards(conn, m):
    job_id = conn.create_job(owner="me")
    
    # Allow the boards time to power on
    time.sleep(0.05)
    
    controller = conn._bmp_controllers[m][(0, 0)]
    controller.set_power_calls = []
    controller.set_link_enable_calls = []
    
    conn.power_on_job_boards(job_id)
    
    # Should power cycle
    assert len(controller.set_power_calls) == 1
    assert controller.set_power_calls[0][0] == 0
    assert controller.set_power_calls[0][1] is True
    
    # Should re-set up the links
    assert len(controller.set_link_enable_calls) == 6
    assert all(e is False for b, l, e, f in controller.set_link_enable_calls)
    assert set((0, l) for b, l, e, f in controller.set_link_enable_calls) \
        == set((0, l) for l in Links)
    
    # Shouldn't crash for non-existent job
    conn.power_on_job_boards(1234)
    
    # Should not do anything for pending job
    job_id_pending = conn.create_job(1, 2, owner="me")
    conn.power_on_job_boards(job_id_pending)


def test_power_off_job_boards(conn, m):
    job_id = conn.create_job(owner="me")
    
    # Allow the boards time to power on
    time.sleep(0.05)
    
    controller = conn._bmp_controllers[m][(0, 0)]
    controller.set_power_calls = []
    controller.set_link_enable_calls = []
    
    conn.power_off_job_boards(job_id)
    
    # Should power cycle
    assert len(controller.set_power_calls) == 1
    assert controller.set_power_calls[0][0] == 0
    assert controller.set_power_calls[0][1] is False
    
    # Should not touch the links
    assert len(controller.set_link_enable_calls) == 0
    
    # Shouldn't crash for non-existent job
    conn.power_off_job_boards(1234)
    
    # Should not do anything for pending job
    job_id_pending = conn.create_job(1, 2, owner="me")
    conn.power_off_job_boards(job_id_pending)

def test_destroy_job(conn, m):
    controller0 = conn._bmp_controllers[m][(0, 0)]
    controller1 = conn._bmp_controllers[m][(0, 1)]
    
    job_id1 = conn.create_job(1, 2, owner = "me")
    job_id2 = conn.create_job(1, 2, owner = "me")
    
    controller0.set_power_calls = []
    controller1.set_power_calls = []
    controller0.set_link_enable_calls = []
    controller1.set_link_enable_calls = []
    
    # Should be able to kill queued jobs (and reasons should be prefixed to
    # indicate the job never started)
    conn.destroy_job(job_id2, reason="Because.")
    assert conn.get_job_state(job_id2)[0] is JobState.destroyed
    assert conn.get_job_state(job_id2)[2] == "Cancelled: Because."
    
    # ...without powering anything down
    assert controller0.set_power_calls == []
    assert controller1.set_power_calls == []
    assert controller0.set_link_enable_calls == []
    assert controller1.set_link_enable_calls == []
    
    # Should be able to kill live jobs
    conn.destroy_job(job_id1, reason="Because you too.")
    assert conn.get_job_state(job_id1)[0] is JobState.destroyed
    assert conn.get_job_state(job_id1)[2] == "Because you too."
    
    # ...powering anything down that was in use
    assert len(controller0.set_power_calls) == 3
    assert len(controller1.set_power_calls) == 3
    assert all(s is False for b, s, f in controller0.set_power_calls)
    assert all(s is False for b, s, f in controller1.set_power_calls)
    assert set(b for b, s, f in controller0.set_power_calls) == set(range(3))
    assert set(b for b, s, f in controller1.set_power_calls) == set(range(3))
    assert controller0.set_link_enable_calls == []
    assert controller1.set_link_enable_calls == []
    
    # Shouldn't fail on bad job ids
    conn.destroy_job(1234)


def test_list_jobs(conn, m):
    job_id1 = conn.create_job(owner="me")
    job_id2 = conn.create_job(1, 2, require_torus=True, owner="you",
                              keepalive=None)
    time.sleep(0.05)
    
    jobs = conn.list_jobs()
    
    # job_id
    assert jobs[0][0] == job_id1
    assert jobs[1][0] == job_id2
    
    # owner
    assert jobs[0][1] == "me"
    assert jobs[1][1] == "you"
    
    # start_time
    assert time.time() - 1.0 <= jobs[0][2] <= time.time()
    assert time.time() - 1.0 <= jobs[1][2] <= time.time()
    
    # keepalive
    assert jobs[0][3] == 60.0
    assert jobs[1][3] is None
    
    # state
    assert jobs[0][4] is JobState.ready
    assert jobs[1][4] is JobState.queued
    
    # args
    assert jobs[0][5] == tuple()
    assert jobs[1][5] == (1, 2)
    
    # kwargs
    assert jobs[0][6] == {}
    assert jobs[1][6] == {"require_torus": True}
    
    # allocated_machine_name
    assert jobs[0][7] == m
    assert jobs[1][7] is None
    
    # boards
    assert jobs[0][8] == set([(0, 0, 0)])
    assert jobs[1][8] is None

def test_list_machines(conn):
    machines = OrderedDict([
        ("m0", simple_machine("m0", 1, 1)),
        ("m1", simple_machine("m1", 1, 2, tags=set(["foo", "bar"]),
                              dead_boards=set([(0, 0, 1)]),
                              dead_links=set([(0, 0, 0, Links.west)]))),
    ])
    conn.set_machines(machines)
    
    machine_list = conn.list_machines()
    
    # name
    assert machine_list[0][0] == "m0"
    assert machine_list[1][0] == "m1"
    
    # tags
    assert machine_list[0][1] == set(["default"])
    assert machine_list[1][1] == set(["foo", "bar"])
    
    # width
    assert machine_list[0][2] == 1
    assert machine_list[1][2] == 1
    
    # height
    assert machine_list[0][3] == 1
    assert machine_list[1][3] == 2
    
    # dead_boards
    assert machine_list[0][4] == set()
    assert machine_list[1][4] == set([(0, 0, 1)])
    
    # dead_links
    assert machine_list[0][5] == set()
    assert machine_list[1][5] == set([(0, 0, 0, Links.west)])


def test_all_bmps_in_machine(MockABC, conn):
    # Context manager for grabbing locks on all BMPs in a machine should work
    machines = OrderedDict([
        ("m0", simple_machine("m0", 1, 2)),
        ("m1", simple_machine("m1", 1, 2)),
    ])
    conn.set_machines(machines)
    
    controller_m0_0 = conn._bmp_controllers["m0"][(0, 0)]
    controller_m0_1 = conn._bmp_controllers["m0"][(0, 1)]
    controller_m1_0 = conn._bmp_controllers["m1"][(0, 0)]
    controller_m1_1 = conn._bmp_controllers["m1"][(0, 1)]
    
    assert not controller_m0_0.locked
    assert not controller_m0_1.locked
    assert not controller_m1_0.locked
    assert not controller_m1_1.locked
    
    with pytest.raises(Exception):
        with conn._all_bmps_in_machine(conn._machines["m0"]):
            assert controller_m0_0.locked
            assert controller_m0_1.locked
            assert not controller_m1_0.locked
            assert not controller_m1_1.locked
            raise Exception()
    
    assert not controller_m0_0.locked
    assert not controller_m0_1.locked
    assert not controller_m1_0.locked
    assert not controller_m1_1.locked
    
    with conn._all_bmps_in_machine(conn._machines["m1"]):
        assert not controller_m0_0.locked
        assert not controller_m0_1.locked
        assert controller_m1_0.locked
        assert controller_m1_1.locked
    
    assert not controller_m0_0.locked
    assert not controller_m0_1.locked
    assert not controller_m1_0.locked
    assert not controller_m1_1.locked

def test_bmp_on_request_complete(MockABC, conn, m):
    job_id = conn.create_job(owner="me")
    job = conn._jobs[job_id]
    
    # Give the BMPs time to run (which use the bmp_requests_until_ready...)
    time.sleep(0.05)
    
    # Test the function while holding the lock to make sure we are the only one
    # interacting with the job
    with conn._lock:
        job.state = JobState.unknown
        
        # The function should simply decrement the counter until it reaches
        # zero at which point it should flag the object as "Ready"
        assert job.bmp_requests_until_ready == 0
        job.bmp_requests_until_ready = 5
        for _ in range(5):
            assert job.state == JobState.unknown
            conn._bmp_on_request_complete(job)
        
        assert job.state == JobState.ready

@pytest.mark.parametrize("power", [True, False])
@pytest.mark.parametrize("link_enable", [True, False, None])
def test_set_job_power_and_links(MockABC, conn, m, power, link_enable):
    job_id = conn.create_job(owner="me")
    job = conn._jobs[job_id]
    
    # Allow BMPs time to power on the board...
    time.sleep(0.05)
    
    controller = conn._bmp_controllers[m][(0, 0)]
    controller.set_power_calls = []
    controller.set_link_enable_calls = []
    
    # Send the command while holding the lock to make sure we see the number of
    # BMP requests expected before a BMP gets chance to decrement the counter.
    with conn._lock:
        # Send the command
        conn._set_job_power_and_links(job, power, link_enable)
        
        # Job should be placed in power state
        assert job.state is JobState.power
        
        # Make sure the correct number of completions is requested
        expected_requests = 1  # Power command always sent
        if link_enable is not None:
            expected_requests += 6
        assert job.bmp_requests_until_ready == expected_requests
    
        # Make sure the correct power commands were sent
        assert len(controller.set_power_calls) == 1
        assert controller.set_power_calls[0][0] == 0
        assert controller.set_power_calls[0][1] == power
        
        if link_enable is not None:
            assert len(controller.set_link_enable_calls) == 6
            assert all(b == 0 for b, l, e, f in
                       controller.set_link_enable_calls)
            assert all(e is link_enable for b, l, e, f in
                       controller.set_link_enable_calls)
            assert set(l for b, l, e, f in
                       controller.set_link_enable_calls) == set(Links)
        else:
            assert len(controller.set_link_enable_calls) == 0


def test_dynamic_state_control(MockABC, timeout_0_2, conn, m):
    # Should be able to shut-down and restart all dynamic state safely
    
    # For a single machine we should have two background threads running BMP
    # Connections.
    assert MockABC.running_theads == 2
    assert MockABC.num_created == 2
    
    # We'll create a job with a short timeout which we'll let almost expire and
    # hopefully find that after restoring the keepalive is reset.
    job_id = conn.create_job(owner="me", keepalive=0.3)
    time.sleep(0.2)
    
    conn._del_dynamic_state()
    
    # All background threads and dynamic stuff should now be down and
    # dereferenced
    assert MockABC.running_theads == 0
    assert MockABC.num_created == 2
    assert conn._lock is None
    assert conn._bmp_controllers is None
    assert conn._timeout_thread is None
    assert conn._stop_timeout_thread is None
    
    
    conn._init_dynamic_state()
    
    # Restarting should restore everything and our job should not find itself
    # timed out!
    assert MockABC.running_theads == 2
    assert MockABC.num_created == 4
    assert conn._lock is not None
    assert conn._bmp_controllers is not None
    assert conn._timeout_thread is not None
    assert conn._stop_timeout_thread is not None
    
    time.sleep(0.25)
    assert conn.get_job_state(job_id)[0] is JobState.ready


def test_pickle(MockABC):
    # Create a controller, with a running job and some threads etc.
    conn = Controller()
    conn.set_machines({"m": simple_machine("m", 1, 2)})
    job_id = conn.create_job(owner="me")
    
    assert MockABC.running_theads == 2
    
    # Pickling the controller should succeed
    pickled_conn = pickle.dumps(conn)
    
    assert MockABC.running_theads == 2
    
    # Should be able to stop the conn (since it should not have been left in a
    # stopped state)
    conn.stop()
    conn.join()
    del conn
    
    assert MockABC.running_theads == 0
    
    # Unpickling should succeed
    conn2 = pickle.loads(pickled_conn)
    
    # And some BMP connections should be running again
    assert MockABC.running_theads == 2
    
    # And our job should still be there
    assert conn2.get_job_state(job_id)[0] == JobState.ready
    
    conn2.stop()
    conn2.join()
