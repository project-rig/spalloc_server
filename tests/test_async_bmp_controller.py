import pytest

from mock import Mock, call

import threading

from spalloc_server.async_bmp_controller import AsyncBMPController
from spalloc_server.links import Links
from mock_bmp import MockBMP
from mock_bmp import SCPVerMessage


@pytest.yield_fixture
def abc():
    """Make an AsyncBMPController and stop it at the end."""
    bmp = MockBMP(responses=[SCPVerMessage(0, 0, "2.1.0")])
    bmp.start()
    abc = AsyncBMPController("localhost")
    yield abc
    print "Stopping"
    abc.stop()
    abc.join()
    print "ABC stopped"
    bmp.stop()
    bmp.join()
    print "BMP Stopped"


@pytest.fixture
def bc(abc, monkeypatch):
    """Mock out the Transceiver object."""
    bc = Mock()
    monkeypatch.setattr(abc, "_transceiver", bc)
    return bc


class OnDoneEvent(object):
    """An object which can be used as a dummy callback.

    The object is callable and expects to be called exactly once with a single
    argument: success or failure. This is recorded in the object's success
    attribute. Before the object is called this attribute is None.

    The object works like a :py:class:`threading.Event` which is set when the
    callback is called.
    """

    def __init__(self):
        self.event = threading.Event()
        self.success = None
        self.reason = None

    def set(self, *args, **kwargs):
        return self.event.set(*args, **kwargs)

    def wait(self, *args, **kwargs):
        return self.event.wait(*args, **kwargs)

    def __call__(self, success, reason):
        # Should be passed a valid success value
        assert (success is True) or (success is False)

        # Should not have been called before
        assert self.success is None

        self.success = success
        self.reason = reason

        self.set()


@pytest.mark.timeout(1.0)
@pytest.mark.parametrize("on_thread_start", [None, Mock()])
def test_start_and_stop(on_thread_start):
    # Make sure that if a BMP controller is started, we can stop it immediately
    bmp = MockBMP(responses=[SCPVerMessage(0, 0, "2.1.0")])
    bmp.start()
    try:
        abc = AsyncBMPController("localhost", on_thread_start=on_thread_start)
        assert abc._stop is False

        abc.stop()
        abc.join()
        assert abc._stop is True

        if on_thread_start is not None:
            on_thread_start.assert_called_once_with()
    finally:
        bmp.stop()
        bmp.join()


def mock_read_fpga_register(fpga_num, register, board, cabinet, frame):
    return fpga_num


class MockVersionNumber(object):
    def __init__(self, version):
        self._version = version

    @property
    def version_number(self):
        return self._version


def mock_read_bmp_version(board, frame, cabinet):
    return MockVersionNumber((2, 0, 0))


@pytest.mark.timeout(1.0)
@pytest.mark.parametrize("power_side_effect,success",
                         [(None, True),
                          (IOError("Fail."), False)])
def test_set_power(abc, bc, power_side_effect, success):
    # Make sure that the set power command works (and failure is reported)
    e = OnDoneEvent()
    bc.power_on.side_effect = power_side_effect
    bc.power_off.side_effect = power_side_effect
    abc.set_power(10, False, e)
    e.wait()
    assert e.success is success
    assert len(bc.power_on.mock_calls) == 0
    bc.power_off.assert_called_once_with(boards=[10], frame=0, cabinet=0)
    bc.power_off.reset_mock()

    e = OnDoneEvent()
    abc.set_power(11, True, e)
    bc.power_on.side_effect = power_side_effect
    bc.power_off.side_effect = power_side_effect
    bc.read_fpga_register.side_effect = mock_read_fpga_register
    bc.read_bmp_version.side_effect = mock_read_bmp_version
    e.wait()
    assert e.success is success
    bc.power_on.assert_called_once_with(boards=[11], frame=0, cabinet=0)
    bc.power_on.reset_mock()
    assert len(bc.power_off.mock_calls) == 0


@pytest.mark.timeout(1.0)
def test_set_power_blocks(abc, bc):
    # Make sure that the set power command can block
    event = threading.Event()
    bc.power_off.side_effect = (lambda *a, **k: event.wait())

    done_event = OnDoneEvent()
    abc.set_power(10, False, done_event)

    # Block for a short time to ensure the background thread gets chance to
    # execute
    assert done_event.wait(0.1) is False

    # We should be sure the power command is blocking on the BMP call
    bc.power_off.assert_called_once_with(boards=[10], frame=0, cabinet=0)

    # When the BMP call completes, so should the done_event!
    event.set()
    done_event.wait()
    assert done_event.success is True


@pytest.mark.timeout(1.0)
@pytest.mark.parametrize("power_side_effect,success",
                         [(None, True),
                          (IOError("Fail."), False)])
def test_set_power_merge(abc, bc, power_side_effect, success):
    bc.power_off.side_effect = power_side_effect

    # Make sure we can queue up several power commands which will get merged
    # (and any errors duplicated).
    events = [OnDoneEvent() for _ in range(3)]
    with abc:
        abc.set_power(10, False, events[0])
        abc.set_power(11, False, events[1])
        abc.set_power(13, False, events[2])

    for event in events:
        event.wait()
        assert event.success is success

    bc.power_off.assert_called_once_with(boards=[10, 11, 13], frame=0,
                                         cabinet=0)


@pytest.mark.timeout(1.0)
def test_set_power_dont_merge(abc, bc):
    # Make sure power commands are only merged with those of the same type
    events = [OnDoneEvent() for _ in range(3)]
    with abc:
        abc.set_power(10, False, events[0])
        abc.set_power(11, True, events[1])
        abc.set_power(12, False, events[2])

    for event in events:
        event.wait()

    assert bc.power_off.mock_calls == [
        call(boards=[10], frame=0, cabinet=0),
        call(boards=[12], frame=0, cabinet=0),
    ]
    assert bc.power_on.mock_calls == [
        call(boards=[11], frame=0, cabinet=0),
    ]


@pytest.mark.timeout(1.0)
@pytest.mark.parametrize("enable,value", [(True, 0), (False, 1)])
@pytest.mark.parametrize("link,fpga,addr",
                         [(Links.east, 0, 0x0000005C),
                          (Links.south, 0, 0x0001005C),
                          (Links.south_west, 1, 0x0000005C),
                          (Links.west, 1, 0x0001005C),
                          (Links.north, 2, 0x0000005C),
                          (Links.north_east, 2, 0x0001005C)])
@pytest.mark.parametrize("side_effect,success",
                         [(None, True),
                          (IOError("Fail."), False)])
def test_set_link_enable(abc, bc, link, fpga, addr, enable, value,
                         side_effect, success):
    # Make sure that the set link command works (and failure is reported)
    e = OnDoneEvent()
    bc.write_fpga_register.side_effect = side_effect
    bc.read_bmp_version.side_effect = mock_read_bmp_version
    abc.set_link_enable(10, link, enable, e)
    e.wait()
    assert e.success is success
    bc.write_fpga_register.assert_called_once_with(fpga, addr, value, board=10,
                                                   frame=0, cabinet=0)
    bc.write_fpga_register.reset_mock()


@pytest.mark.timeout(1.0)
def test_set_link_enable_blocks(abc, bc):
    # Make sure that the set power command can block
    event = threading.Event()
    bc.write_fpga_register.side_effect = (lambda *a, **k: event.wait())
    bc.read_bmp_version.side_effect = mock_read_bmp_version

    done_event = OnDoneEvent()
    abc.set_link_enable(10, Links.east, True, done_event)

    # Block for a short time to ensure the background thread gets chance to
    # execute
    assert done_event.wait(0.1) is False

    # We should be sure the power command is blocking on the BMP call
    bc.write_fpga_register.assert_called_once_with(0, 0x5C, False, board=10,
                                                   frame=0, cabinet=0)

    # When the BMP call completes, so should the done_event!
    event.set()
    done_event.wait()


@pytest.mark.timeout(1.0)
def test_power_priority(abc, bc):
    # Make sure that power queue has higher priority
    power_on_event = threading.Event()
    power_off_event = threading.Event()
    link_event = threading.Event()
    bc.power_on.side_effect = (lambda *a, **k: power_on_event.wait(1.0))
    bc.power_off.side_effect = (lambda *a, **k: power_off_event.wait(1.0))
    bc.write_fpga_register.side_effect = (lambda *a, **k: link_event.wait(1.0))
    bc.read_bmp_version.side_effect = mock_read_bmp_version

    with abc:
        e1, e2, e3 = (OnDoneEvent() for _ in range(3))
        abc.set_power(10, True, e1)
        abc.set_link_enable(11, Links.east, True, e2)
        abc.set_power(12, False, e3)

    # Block for a short time to ensure the background thread gets chance to
    # execute
    assert e1.wait(0.1) is False

    # Make sure just the power command has been called
    bc.power_on.assert_called_once_with(boards=[10], frame=0, cabinet=0)
    bc.power_on.reset_mock()
    assert len(bc.power_off.mock_calls) == 0
    assert len(bc.write_fpga_register.mock_calls) == 0

    # Let the first power command complete
    power_on_event.set()
    e1.wait(1.0)

    # Block for a short time to ensure background thread gets chance to execute
    assert e3.wait(0.1) is False

    # Make sure just the power command has been called a second time (and not
    # the link setting command)
    bc.power_off.assert_called_once_with(boards=[12], frame=0, cabinet=0)
    bc.power_off.reset_mock()
    assert len(bc.power_on.mock_calls) == 0
    assert len(bc.write_fpga_register.mock_calls) == 0

    # Let the second power command complete
    power_off_event.set()
    e3.wait(1.0)

    # Block for a short time to ensure background thread gets chance to execute
    assert e2.wait(0.1) is False

    # We should be sure the power command is blocking on the BMP call
    assert len(bc.power_on.mock_calls) == 0
    assert len(bc.power_off.mock_calls) == 0
    bc.write_fpga_register.assert_called_once_with(0, 0x5C, False, board=11,
                                                   frame=0, cabinet=0)

    # Make BMP call complete and the last event finish
    link_event.set()
    e2.wait(1.0)


@pytest.mark.timeout(1.0)
def test_power_removes_link_enables(abc, bc):
    # Make sure link enable requests are removed for boards with newly added
    # power commands.
    bc.read_fpga_register.side_effect = mock_read_fpga_register
    bc.read_bmp_version.side_effect = mock_read_bmp_version
    with abc:
        e1, e2, e3, e4 = (OnDoneEvent() for _ in range(4))
        abc.set_power(10, True, e1)
        abc.set_link_enable(10, Links.east, True, e2)
        abc.set_link_enable(11, Links.east, True, e3)
        abc.set_power(11, False, e4)

    # Wait for the commands to complete
    e1.wait()
    e2.wait()
    e3.wait()
    e4.wait()

    # All commands should have finished (but the link enable on board 11 should
    # have failed)
    assert e1.success is True
    assert e2.success is True
    assert e3.success is False
    assert e4.success is True

    # Make sure both power commands were sent
    assert len(bc.power_on.mock_calls) == 1
    assert len(bc.power_off.mock_calls) == 1

    # But only one link command should be around
    bc.write_fpga_register.assert_called_once_with(0, 0x5C, False, board=10,
                                                   frame=0, cabinet=0)


@pytest.mark.timeout(1.0)
def test_stop_drains(abc, bc):
    # Make sure that the queues are emptied before the stop command is
    # processed
    set_power_done = OnDoneEvent()
    set_link_enable_done = OnDoneEvent()
    bc.read_bmp_version.side_effect = mock_read_bmp_version
    with abc:
        abc.set_power(10, False, set_power_done)
        abc.set_link_enable(11, Links.east, False, set_link_enable_done)
        abc.stop()

    # Both of these should be carried out
    set_power_done.wait()
    set_link_enable_done.wait()
    assert set_power_done.success is True
    assert set_link_enable_done.success is True

    # And the loop should stop!
    abc.join()
