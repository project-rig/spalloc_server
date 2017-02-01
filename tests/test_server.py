import pytest

from mock import Mock, call

import threading
import tempfile
import shutil
import os
import os.path
import logging
import time
import socket
import signal
import json

from six import itervalues

from rig.links import Links

from spalloc_server.controller import JobState
from spalloc_server.server import Server, main
from spalloc_server.configuration import Configuration

from spalloc_server import __version__

from common import simple_machine


pytestmark = pytest.mark.usefixtures("MockABC")

logging.basicConfig(level=logging.INFO)


class SimpleClient(object):  # pragma: no cover
    """A simple line receiving and sending client."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET,
                                  socket.SOCK_STREAM)
        self.sock.connect(("127.0.0.1", 22244))
        self.buf = b""
        self.notifications = []

    def recv_line(self, length=1024):
        while b"\n" not in self.buf:
            data = self.sock.recv(length)
            if not data:
                raise Exception("Socket disconnected!")
            self.buf += data
        line, _, self.buf = self.buf.partition(b"\n")
        return line

    def close(self):
        self.sock.close()

    def send_call(self, cmd, *args, **kwargs):
        call = json.dumps({"command": cmd,
                           "args": args,
                           "kwargs": kwargs})
        self.sock.send(call.encode("utf-8") + b"\n")

    def get_return(self):
        while True:
            line = self.recv_line()
            try:
                resp = json.loads(line.decode("utf-8"))
            except:
                print("Bad line: {}".format(repr(line)))
                raise
            if "return" in resp:
                return resp["return"]
            else:
                self.notifications.append(resp)

    def call(self, cmd, *args, **kwargs):
        self.send_call(cmd, *args, **kwargs)
        return self.get_return()

    def get_notification(self):
        if self.notifications:
            return self.notifications.pop(0)
        else:
            line = self.recv_line()
            return json.loads(line.decode("utf-8"))


@pytest.yield_fixture
def config_dir():
    # Directory for configuration files etc.
    dirname = tempfile.mkdtemp()

    yield dirname

    # Cleanup
    shutil.rmtree(dirname)


@pytest.fixture
def config_file(config_dir):
    # The config filename used by the server
    return os.path.join(config_dir, "test_config.cfg")


@pytest.fixture
def state_file(config_dir):
    # The filename of the state filename
    return os.path.join(
        config_dir,
        ".test_config.cfg.state.{}".format(__version__))


@pytest.fixture
def simple_config(config_file):
    # A simple config file which defines a single machine.
    with open(config_file, "w") as f:
        f.write(
            "configuration = Configuration(\n"
            "    machines=[\n"
            "        Machine('m', set(['default']), 1, 2,\n"
            "                set(), set(),\n"
            "                {(x, y, z): (x*10, y*10, z*10)\n"
            "                 for x in range(1)\n"
            "                 for y in range(2)\n"
            "                 for z in range(3)},\n"
            "                {(c*10, f*10): '10.0.{}.{}'.format(c, f)\n"
            "                 for c in range(1)\n"
            "                 for f in range(2)},\n"
            "                {(x, y, z): '11.{}.{}.{}'.format(x, y, z)\n"
            "                 for x in range(1)\n"
            "                 for y in range(2)\n"
            "                 for z in range(3)})\n"
            "    ]\n"
            ")\n")

    return config_file


@pytest.fixture
def fast_keepalive_config(config_file):
    # A simple config file which has a fast polling interval of 0.1 seconds
    with open(config_file, "w") as f:
        f.write(
            "configuration = Configuration(\n"
            "    timeout_check_interval=0.1,\n"
            "    machines=[\n"
            "        Machine('m', set(['default']), 1, 2,\n"
            "                set(), set(),\n"
            "                {(x, y, z): (x*10, y*10, z*10)\n"
            "                 for x in range(1)\n"
            "                 for y in range(2)\n"
            "                 for z in range(3)},\n"
            "                {(c*10, f*10): '10.0.{}.{}'.format(c, f)\n"
            "                 for c in range(1)\n"
            "                 for f in range(2)},\n"
            "                {(x, y, z): '11.{}.{}.{}'.format(x, y, z)\n"
            "                 for x in range(1)\n"
            "                 for y in range(2)\n"
            "                 for z in range(3)})\n"
            "    ]\n"
            ")\n")

    return config_file


@pytest.fixture
def double_config(config_file):
    # A simple two-machine config file which defines a two machines.
    with open(config_file, "w") as f:
        f.write(
            "configuration = Configuration(\n"
            "    machines=[\n"
            "        Machine('m0', set(['default']), 1, 2,\n"
            "                set(), set(),\n"
            "                {(x, y, z): (x*10, y*10, z*10)\n"
            "                 for x in range(1)\n"
            "                 for y in range(2)\n"
            "                 for z in range(3)},\n"
            "                {(c*10, f*10): '10.0.{}.{}'.format(c, f)\n"
            "                 for c in range(1)\n"
            "                 for f in range(2)},\n"
            "                {(x, y, z): '11.{}.{}.{}'.format(x, y, z)\n"
            "                 for x in range(1)\n"
            "                 for y in range(2)\n"
            "                 for z in range(3)}),\n"
            "        Machine('m1', set(['default']), 3, 4,\n"
            "                set([(0, 0, 1)]), \n"
            "                set([(1, 1, 1, Links.north)]),\n"
            "                {(x, y, z): (x*10, y*10, z*10)\n"
            "                 for x in range(3)\n"
            "                 for y in range(4)\n"
            "                 for z in range(3)},\n"
            "                {(c*10, f*10): '12.0.{}.{}'.format(c, f)\n"
            "                 for c in range(3)\n"
            "                 for f in range(4)},\n"
            "                {(x, y, z): '13.{}.{}.{}'.format(x, y, z)\n"
            "                 for x in range(3)\n"
            "                 for y in range(4)\n"
            "                 for z in range(3)}),\n"
            "    ]\n"
            ")\n")

    return config_file


@pytest.yield_fixture
def s(MockABC, config_file):
    # A server which is created and shut down with each use.
    s = Server(config_file)

    yield s

    s.stop_and_join()


@pytest.yield_fixture
def c():
    c = SimpleClient()
    yield c
    c.close()


@pytest.mark.timeout(1.0)
def test_startup_shutdown(simple_config, s):
    pass


@pytest.mark.timeout(1.0)
def test_join(MockABC, simple_config):
    # Tests join, stop_and_join and is_alive
    s = Server(simple_config)
    assert s.is_alive() is True

    joining_thread = threading.Thread(target=s.join)
    joining_thread.start()

    # The server should still be running...
    time.sleep(0.05)
    assert joining_thread.is_alive()
    assert s.is_alive() is True

    # When server is stopped, the joining thread should be complete
    s.stop_and_join()
    assert s.is_alive() is False
    joining_thread.join()


@pytest.mark.timeout(1.0)
def test_stop_and_join_disconnects(MockABC, simple_config):
    # Clients should be disconnected when doing stop and join
    s = Server(simple_config)
    c = SimpleClient()
    c.call("version")

    s.stop_and_join()
    time.sleep(0.05)
    assert c.sock.recv(1024) == b""


@pytest.mark.timeout(1.0)
@pytest.mark.parametrize("cold_start", [True, False])
@pytest.mark.parametrize("corrupt_state", [True, False])
@pytest.mark.parametrize("delete_state", [True, False])
def test_hot_start(MockABC, simple_config, state_file, cold_start,
                   corrupt_state, delete_state):
    # Initially start up the server without a state file. Should be cold
    # started.
    assert not os.path.lexists(state_file)
    s = Server(simple_config, cold_start)

    try:
        job_id = s.create_job(None, owner="me")
        time.sleep(0.05)
        assert s.get_job_state(None, job_id)["state"] == JobState.ready
    finally:
        s.stop_and_join()

    # State should be dumped
    assert os.path.lexists(state_file)

    # Corrupt the state if required
    if corrupt_state:
        # Just leave the file empty...
        open(state_file, "w").close()

    # Delete the state if required
    if delete_state:
        # Just leave the file empty...
        os.remove(state_file)

    # Start a new server
    s = Server(simple_config, cold_start)
    try:
        # Should have the same state as before, if doing a hot start
        if cold_start or corrupt_state or delete_state:
            assert s.get_job_state(None, job_id)["state"] == JobState.unknown
        else:
            assert s.get_job_state(None, job_id)["state"] == JobState.ready
    finally:
        s.stop_and_join()


@pytest.mark.timeout(1.0)
@pytest.mark.parametrize("missing", [True, False])
def test_no_initial_config_file(MockABC, config_file, missing):
    # Should fail if config file is not valid/missing first time

    if missing:
        pass  # Don't create a file!
    else:
        with open(config_file, "w") as f:
            f.write("foo=123")

    with pytest.raises(Exception):
        Server(config_file)


@pytest.mark.timeout(2.0)
def test_read_config_file(simple_config, s):
    # Should fail if config file is missing
    os.remove(simple_config)
    assert s._read_config_file() is False

    # Should fail if config file is syntactically invalid
    with open(simple_config, "w") as f:
        f.write("1_2_3")
    assert s._read_config_file() is False

    # Should fail if no configuration variable is set
    with open(simple_config, "w") as f:
        f.write("config = 123")
    assert s._read_config_file() is False

    # Should succeed otherwise!
    with open(simple_config, "w") as f:
        # Make a simple config file
        f.write("configuration = {}".format(repr(Configuration())))
    assert s._read_config_file() is True

    # Should pass on parameters to controller
    with open(simple_config, "w") as f:
        f.write("configuration = {}".format(repr(
            Configuration(max_retired_jobs=123))))
    assert s._read_config_file() is True
    assert s._controller.max_retired_jobs == 123

    # Should pass on machines to controller in right order
    with open(simple_config, "w") as f:
        f.write("configuration = {}".format(repr(
            Configuration(machines=[simple_machine("m0", ip_prefix="0"),
                                    simple_machine("m1", ip_prefix="1"),
                                    simple_machine("m2", ip_prefix="2"),
                                    simple_machine("m3", ip_prefix="3"),
                                    simple_machine("m4", ip_prefix="4")]))))
    assert s._read_config_file() is True
    assert list(s._controller.machines) == "m0 m1 m2 m3 m4".split()


@pytest.mark.timeout(1.0)
def test_reread_config_file(simple_config, s):
    # Make sure config re-reading works
    assert list(s._controller.machines) == ["m"]

    # Modify config file
    with open(simple_config, "w") as f:
        f.write("configuration = {}".format(repr(Configuration())))
    os.kill(os.getpid(), signal.SIGHUP)
    time.sleep(0.2)

    # Configuration should have changed accordingly
    assert list(s._controller.machines) == []


@pytest.mark.timeout(1.0)
def test_bad_command(simple_config, s, monkeypatch):
    # If a bad command is sent, the server should just disconnect the client
    c = SimpleClient()
    c.send_call("does not exist")
    assert c.sock.recv(1024) == b""


@pytest.mark.timeout(1.0)
def test_handle_commands_bad_recv(simple_config, s, monkeypatch):
    monkeypatch.setattr(s, "_disconnect_client", Mock())

    client = Mock()
    client.recv.side_effect = OSError()
    s._handle_commands(client)
    s._disconnect_client.assert_called_once_with(client)


@pytest.mark.timeout(1.0)
def test_bad_disconnect(simple_config, s, monkeypatch):
    client = Mock()
    client.fileno.return_value = 1
    client.getpeername.side_effect = OSError()
    monkeypatch.setattr(s, "_fdmap", {1: client})
    monkeypatch.setattr(s, "_client_buffers", {client: b""})
    monkeypatch.setattr(s, "_poll", Mock())

    s._disconnect_client(client)


@pytest.mark.timeout(1.0)
def test_bad_send_change_notifications(monkeypatch, simple_config, s):
    client0 = Mock()
    client0.send.side_effect = OSError()
    client1 = Mock()
    client1.send.side_effect = OSError()

    monkeypatch.setattr(s, "_disconnect_client", Mock())

    # Monkeypatch in a client which will fail to send notifications
    conn = s._controller
    s._client_job_watches[client0] = {1}
    s._client_machine_watches[client1] = {"m"}
    s._controller = Mock()
    s._controller.changed_jobs = {1}
    s._controller.changed_machines = {"m"}

    s._send_change_notifications()

    # Restore patched state
    s._controller = conn
    s._client_job_watches.pop(client0)
    s._client_machine_watches.pop(client1)

    assert s._disconnect_client.mock_calls == [
        call(client0),
        call(client1),
    ]


@pytest.mark.timeout(1.0)
def test_version_command(simple_config, s, c):
    # First basic test of calling a remote method
    assert c.call("version") == __version__


@pytest.mark.timeout(1.0)
def test_job_management(simple_config, s, c):
    # First more complete test of calling a remote method with complex
    # arguments

    # Should get allocated
    job_id0 = c.call("create_job", tags=["default"], owner="me")
    s._controller._jobs[job_id0].start_time = 1234.5

    # Should be queued
    job_id1 = c.call("create_job", 1, 2, owner="me", require_torus=True)
    s._controller._jobs[job_id1].start_time = 5432.0

    # Should be impossible
    job_id2 = c.call("create_job", 2, 2, owner="me")

    # Allow time for jobs to start
    time.sleep(0.1)

    assert job_id0 != job_id1
    assert job_id0 != job_id2
    assert job_id1 != job_id2

    # Keepalive should work
    c.call("job_keepalive", job_id0)
    c.call("job_keepalive", job_id1)
    c.call("job_keepalive", job_id2)

    # State should be visible
    assert c.call("get_job_state", job_id0) == {
        "state": JobState.ready, "power": True,
        "keepalive": 60.0, "reason": None, "start_time": 1234.5}
    assert c.call("get_job_state", job_id1) == {
        "state": JobState.queued, "power": None,
        "keepalive": 60.0, "reason": None, "start_time": 5432.0}
    assert c.call("get_job_state", job_id2) == {
        "state": JobState.destroyed,  "power": None,
        "keepalive": None,
        "reason": "Cancelled: No suitable machines available.",
        "start_time": None}

    # Ethernet connections should be visible, where defined
    assert c.call("get_job_machine_info", job_id0) == {
        "width": 8, "height": 8,
        "connections": [[[0, 0], "11.0.0.0"]],
        "machine_name": "m",
        "boards": [[0, 0, 0]]}
    assert c.call("get_job_machine_info", job_id1) == {
        "width": None, "height": None,
        "connections": None, "machine_name": None, "boards": None}
    assert c.call("get_job_machine_info", job_id2) == {
        "width": None, "height": None,
        "connections": None, "machine_name": None, "boards": None}

    # Power commands should work
    c.call("power_on_job_boards", job_id0)
    c.call("power_on_job_boards", job_id1)
    c.call("power_on_job_boards", job_id2)
    c.call("power_off_job_boards", job_id0)
    c.call("power_off_job_boards", job_id1)
    c.call("power_off_job_boards", job_id2)

    # Job listing should work
    jobs = c.call("list_jobs")
    assert len(jobs) == 2

    assert jobs[0]["job_id"] == job_id0
    assert jobs[1]["job_id"] == job_id1

    assert jobs[0]["owner"] == "me"
    assert jobs[1]["owner"] == "me"

    assert jobs[0]["keepalive"] == 60.0
    assert jobs[1]["keepalive"] == 60.0

    assert jobs[0]["state"] == JobState.ready
    assert jobs[1]["state"] == JobState.queued

    assert jobs[0]["args"] == []
    assert jobs[1]["args"] == [1, 2]

    assert jobs[0]["kwargs"] == {"tags": ["default"]}
    assert jobs[1]["kwargs"] == {"require_torus": True}

    assert jobs[0]["allocated_machine_name"] == "m"
    assert jobs[1]["allocated_machine_name"] is None

    assert jobs[0]["boards"] == [[0, 0, 0]]
    assert jobs[1]["boards"] is None

    # Destroying jobs should work
    c.call("destroy_job", job_id0, "Test reason...")
    time.sleep(0.05)
    assert c.call("get_job_state", job_id0) == {
        "state": JobState.destroyed, "power": None,
        "keepalive": None,
        "reason": "Test reason...",
        "start_time": None}
    assert c.call("get_job_state", job_id1) == {
        "state": JobState.ready, "power": True,
        "keepalive": 60.0, "reason": None,
        "start_time": 5432.0}


@pytest.mark.timeout(2.0)
def test_keepalive_expiration(fast_keepalive_config, s, c):
    job_id = c.call("create_job", keepalive=0.15, owner="me")

    # Should be alive for a bit
    time.sleep(0.05)
    assert s._controller.get_job_state(job_id).state != JobState.destroyed

    # Should get killed
    time.sleep(0.25)
    assert s._controller.get_job_state(job_id).state == JobState.destroyed


@pytest.mark.timeout(1.0)
def test_list_machines(double_config, s, c):
    machines = c.call("list_machines")

    assert len(machines) == 2

    assert machines[0]["name"] == "m0"
    assert machines[1]["name"] == "m1"

    assert machines[0]["tags"] == ["default"]
    assert machines[1]["tags"] == ["default"]

    assert machines[0]["width"] == 1
    assert machines[1]["width"] == 3

    assert machines[0]["height"] == 2
    assert machines[1]["height"] == 4

    assert machines[0]["dead_boards"] == []
    assert machines[1]["dead_boards"] == [[0, 0, 1]]

    assert machines[0]["dead_links"] == []
    assert machines[1]["dead_links"] == [[1, 1, 1, Links.north]]


@pytest.mark.timeout(1.0)
def test_where_is(double_config, s, c):
    assert c.call("create_job", 1, 1, owner="me") == 1

    assert c.call("where_is", machine="bad", x=0, y=0, z=0) is None

    assert c.call("where_is", job_id=1, chip_x=5, chip_y=9) == {
        "machine": "m0",
        "logical": [0, 0, 2],
        "physical": [0, 0, 20],
        "chip": [5, 9],
        "board_chip": [1, 1],
        "job_id": 1,
        "job_chip": [5, 9],
    }

    assert c.call("where_is", machine="m1", x=2, y=1, z=1) == {
        "machine": "m1",
        "logical": [2, 1, 1],
        "physical": [20, 10, 10],
        "chip": [32, 16],
        "board_chip": [0, 0],
        "job_id": None,
        "job_chip": None,
    }


@pytest.mark.timeout(1.0)
def test_get_board_position(simple_config, s, c):
    assert c.call("get_board_position", "bad", 0, 0, 0) is None
    assert c.call("get_board_position", "m", 0, 0, 2) == [0, 0, 20]


@pytest.mark.timeout(1.0)
def test_get_board_at_position(simple_config, s, c):
    assert c.call("get_board_at_position", "bad", 0, 0, 0) is None
    assert c.call("get_board_at_position", "m", 0, 0, 21) is None
    assert c.call("get_board_at_position", "m", 0, 0, 0) == [0, 0, 0]
    assert c.call("get_board_at_position", "m", 0, 0, 20) == [0, 0, 2]


@pytest.mark.timeout(1.0)
def test_job_notifications(simple_config, s):
    c0 = SimpleClient()
    c1 = SimpleClient()

    # Listen for *all* job changes
    c1.call("notify_job")

    # Should be notified new jobs being created and powered on
    with s._controller._bmp_controllers["m"][(0, 0)].handler_lock:
        job_id0 = c0.call("create_job", owner="me")
        assert c1.get_notification() == {"jobs_changed": [job_id0]}
    assert c1.get_notification() == {"jobs_changed": [job_id0]}

    # c0 should subscribe to its own job
    c0.call("notify_job", job_id0)

    # New job being created should not go to clients not listening to a
    # specific job
    job_id1 = c0.call("create_job", 1, 2, owner="me")
    assert c1.get_notification() == {"jobs_changed": [job_id1]}

    # Job being dequeued should result in an event on that job and the other
    # job being queued and powered on. c0 should only see dequed job.
    with s._controller._bmp_controllers["m"][(0, 0)].handler_lock:
        c0.call("destroy_job", job_id0)
        assert c0.get_notification() == {"jobs_changed": [job_id0]}
        assert c1.get_notification() in ({"jobs_changed": [job_id0, job_id1]},
                                         {"jobs_changed": [job_id1, job_id0]})
    assert c1.get_notification() == {"jobs_changed": [job_id1]}


@pytest.mark.timeout(1.0)
def test_machine_notifications(double_config, s):
    c0 = SimpleClient()
    c1 = SimpleClient()

    c0.call("notify_machine", "m0")
    c1.call("notify_machine")

    job_id0 = c0.call("create_job", 1, 2, owner="me")
    time.sleep(0.05)

    # Should be notified new jobs being scheduled
    assert c0.get_notification() == {"machines_changed": ["m0"]}
    assert c1.get_notification() == {"machines_changed": ["m0"]}

    job_id1 = c0.call("create_job", owner="me")
    time.sleep(0.05)

    # Make sure filtering works
    assert c1.get_notification() == {"machines_changed": ["m1"]}

    # Should be notified on job completion
    c0.call("destroy_job", job_id0)
    assert c0.get_notification() == {"machines_changed": ["m0"]}
    assert c1.get_notification() == {"machines_changed": ["m0"]}

    # Should be notified on job completion
    c1.call("destroy_job", job_id1)
    assert c1.get_notification() == {"machines_changed": ["m1"]}

    # Make sure machine changes get announced
    with open(double_config, "w") as f:
        f.write("configuration = {}".format(repr(Configuration())))
    os.kill(os.getpid(), signal.SIGHUP)

    assert c0.get_notification() == {"machines_changed": ["m0"]}
    assert c1.get_notification() in ({"machines_changed": ["m0", "m1"]},
                                     {"machines_changed": ["m1", "m0"]})


@pytest.mark.timeout(1.0)
def test_job_notify_register_unregister(simple_config, s):
    # Make sure the registration/unregistration commands for job notifications
    # work correctly

    c0 = SimpleClient()
    c1 = SimpleClient()

    # Make sure the clients are connected
    assert c0.call("version") == __version__
    assert c1.call("version") == __version__

    # Get the sockets connected to the clients
    s0 = s1 = None
    for sock in itervalues(s._fdmap):
        try:
            peer = sock.getpeername()
        except:
            continue
        if peer == c0.sock.getsockname():
            s0 = sock
        elif peer == c1.sock.getsockname():
            s1 = sock
    assert s0 is not None and s1 is not None

    # Initially no matches should be present
    assert s._client_job_watches == {}

    # Notification on all
    c0.call("notify_job")
    assert s._client_job_watches == {s0: None}

    # Notification on just a specific job ID
    c1.call("notify_job", 123)
    assert s._client_job_watches == {s0: None, s1: set([123])}

    # Adding more jobs to a notify-all should result in no change
    c0.call("notify_job", 321)
    assert s._client_job_watches == {s0: None, s1: set([123])}

    # Adding more jobs otherwise should add to the set
    c1.call("notify_job", 321)
    assert s._client_job_watches == {s0: None, s1: set([123, 321])}

    # Removing jobs from a notify-all should do nothing
    c0.call("no_notify_job", 321)
    assert s._client_job_watches == {s0: None, s1: set([123, 321])}

    # Removing jobs which aren't matched should do nothing
    c1.call("no_notify_job", 0)
    assert s._client_job_watches == {s0: None, s1: set([123, 321])}

    # Removing jobs which are watched should remove them
    c1.call("no_notify_job", 123)
    assert s._client_job_watches == {s0: None, s1: set([321])}

    # Removing the last job should remove the watch entirely
    c1.call("no_notify_job", 321)
    assert s._client_job_watches == {s0: None}

    c1.call("notify_job", 123)
    assert s._client_job_watches == {s0: None, s1: set([123])}

    # Removing all should work on notify-all
    c0.call("no_notify_job")
    assert s._client_job_watches == {s1: set([123])}

    # Removing all should work on individual jobs
    c1.call("no_notify_job")
    assert s._client_job_watches == {}

    # Removing when never watching should not fail
    c1.call("no_notify_job")
    assert s._client_job_watches == {}


@pytest.mark.timeout(1.0)
def test_machine_notify_register_unregister(simple_config, s):
    # Make sure the registration/unregistration commands for machine
    # notifications work correctly

    c0 = SimpleClient()
    c1 = SimpleClient()

    # Make sure the clients are connected
    assert c0.call("version") == __version__
    assert c1.call("version") == __version__

    # Get the sockets connected to the clients
    s0 = s1 = None
    for sock in itervalues(s._fdmap):
        try:
            peer = sock.getpeername()
        except:
            continue
        if peer == c0.sock.getsockname():
            s0 = sock
        elif peer == c1.sock.getsockname():
            s1 = sock
    assert s0 is not None and s1 is not None

    # Initially no matches should be present
    assert s._client_machine_watches == {}

    # Notification on all
    c0.call("notify_machine")
    assert s._client_machine_watches == {s0: None}

    # Notification on just a specific machine
    c1.call("notify_machine", "m0")
    assert s._client_machine_watches == {s0: None, s1: set(["m0"])}

    # Adding more machines to a notify-all should result in no change
    c0.call("notify_machine", "m1")
    assert s._client_machine_watches == {s0: None, s1: set(["m0"])}

    # Adding more machines otherwise should add to the set
    c1.call("notify_machine", "m1")
    assert s._client_machine_watches == {s0: None, s1: set(["m0", "m1"])}

    # Removing machines from a notify-all should do nothing
    c0.call("no_notify_machine", "m1")
    assert s._client_machine_watches == {s0: None, s1: set(["m0", "m1"])}

    # Removing machines which aren't matched should do nothing
    c1.call("no_notify_machine", "m")
    assert s._client_machine_watches == {s0: None, s1: set(["m0", "m1"])}

    # Removing machines which are watched should remove them
    c1.call("no_notify_machine", "m0")
    assert s._client_machine_watches == {s0: None, s1: set(["m1"])}

    # Removing the last machines should remove the watch entirely
    c1.call("no_notify_machine", "m1")
    assert s._client_machine_watches == {s0: None}

    c1.call("notify_machine", "m0")
    assert s._client_machine_watches == {s0: None, s1: set(["m0"])}

    # Removing all should work on notify-all
    c0.call("no_notify_machine")
    assert s._client_machine_watches == {s1: set(["m0"])}

    # Removing all should work on individual machines
    c1.call("no_notify_machine")
    assert s._client_machine_watches == {}

    # Removing when never watching should not fail
    c1.call("no_notify_machine")
    assert s._client_machine_watches == {}


@pytest.mark.timeout(2.0)
@pytest.mark.parametrize("args,cold_start",
                         [("{}", False),
                          ("{} -q", False),
                          ("{} --cold-start", True),
                          ("{} -q --cold-start", True)])
def test_commandline(monkeypatch, config_file, args, cold_start):
    server = Mock()
    Server = Mock(return_value=server)
    server.is_alive.return_value = False
    import spalloc_server.server
    monkeypatch.setattr(spalloc_server.server,
                        "Server", Server)

    main(args.format(config_file).split())

    Server.assert_called_once_with(config_filename=config_file,
                                   cold_start=cold_start, port=22244)


@pytest.mark.timeout(2.0)
def test_keyboard_interrupt(monkeypatch, config_file):
    s = Mock()
    Server = Mock(return_value=s)
    import spalloc_server.server
    monkeypatch.setattr(spalloc_server.server,
                        "Server", Server)

    s.is_alive.side_effect = KeyboardInterrupt
    main([config_file])

    Server.assert_called_once_with(config_filename=config_file,
                                   cold_start=False, port=22244)
    s.is_alive.assert_called_once_with()
    s.stop_and_join.assert_called_once_with()


@pytest.mark.timeout(1.0)
@pytest.mark.parametrize("args", ["", "--cold-start" "-c"])
def test_bad_args(monkeypatch, args):
    server = Mock()
    Server = Mock(return_value=server)
    server.is_alive.return_value = False
    import spalloc_server.server
    monkeypatch.setattr(spalloc_server.server,
                        "Server", Server)

    with pytest.raises(SystemExit):
        main(args.split())

    assert len(Server.mock_calls) == 0
