""" Provide (basic) asynchronous control over a BMP responsible for controlling
a whole rack.
"""

import threading
import logging
from collections import namedtuple, deque

from spinnman.transceiver import create_transceiver_from_hostname
from spinnman.model import BMPConnectionData
from spinnman.constants import SCP_SCAMP_PORT

from .links import Links
import time

# The first BMP version with FPGA register support
_BMP_VER_MIN = 2

_N_FPGA_RETRIES = 3

_N_REQUEST_TRIES = 2

_SECONDS_BETWEEN_TRIES = 15


class AsyncBMPController(object):
    """ An object which provides an asynchronous interface to a power and link
    control commands of a SpiNNaker BMP.

    Since BMP commands, particularly power-on commands, take some time to
    complete, it is desirable for them to be executed asynchronously. This
    object uses a SpiNNMan :py:class:`~spinnman.transceiver.Transceiver` object
    to communicate with a BMP controlling a single frame of boards.

    Power and link configuration commands are queued and executed in a
    background thread. When a command completes, a user-supplied callback is
    called.

    Sequential power commands of the same type (on/off) are coalesced into a
    single power on command. When a power command is sent, all previous link
    configuration commands queued for that board are skipped. Additionally, all
    power commands are completed before link configuration commands are carried
    out.
    """

    def __init__(self, hostname, on_thread_start=None):
        """ Start a new asynchronous BMP Controller

        Parameters
        ----------
        hostname : str
            The hostname/IP of the BMP to connect to.
        on_thread_start : function() or None
            *Optional.* A function to be called by the controller's background
            thread before it starts. This can be used to ensure proper
            sequencing/handing-over between two AsyncBMPControllers connected
            to the same machine.
        """
        self._on_thread_start = on_thread_start

        self._transceiver = create_transceiver_from_hostname(
            None, 5, bmp_connection_data=[
                BMPConnectionData(0, 0, hostname, [0], SCP_SCAMP_PORT)])
        self._hostname = hostname

        self._stop = False

        # A lock which must be held when modifying the state of this object
        self._lock = threading.RLock()

        # An event fired whenever some new interaction with the BMP is
        # required.
        self._requests_pending = threading.Event()

        # A queue of requests to be done
        self._requests = deque()

        self._thread = threading.Thread(
            target=self._run,
            name="<BMP control thread for {}>".format(hostname))
        self._thread.start()

    def __enter__(self):
        """ When used as a context manager, make requests 'atomic'.
        """
        self._lock.acquire()

    def __exit__(self, _type=None, _value=None, _traceback=None):
        self._lock.release()
        return False

    def add_requests(self, atomic_requests):
        """ Add an atomic request to be performed
        """
        with self._lock:
            assert not self._stop
            self._requests.append(atomic_requests)
            self._requests_pending.set()

    def stop(self):
        """ Stop the background thread, as soon as possible after completing\
            all queued actions.
        """
        with self._lock:
            self._stop = True
            self._requests_pending.set()

    def join(self):
        """ Wait for the thread to actually stop.
        """
        self._thread.join()

    def _good_fpga(self, board, fpga):
        fpga_id = self._transceiver.read_fpga_register(
            fpga_num=fpga, register=_FPGA_FLAG_REGISTER_ADDRESS,
            board=board, cabinet=0, frame=0)
        ok = (fpga_id & _FPGA_FLAG_ID_MASK) == fpga
        if not ok:  # pragma: no cover
            logging.warn(
                "FPGA %d on board %d of %s has incorrect FPGA ID flag %d",
                fpga, board, self._hostname, fpga_id & _FPGA_FLAG_ID_MASK)
        return ok

    def _power_on_and_check(self, boards):
        # FPGAs are checked after power on - assume incorrect to start
        boards_to_power = boards
        for _try in range(_N_FPGA_RETRIES):
            # Power on - note don't need to power off if in subsequent
            # run of the loop as the BMP handles this correctly
            self._transceiver.power_on(
                boards=boards_to_power, frame=0, cabinet=0)

            # Check if the FPGA number is correct on each FPGA
            retry_boards = []
            for board in boards_to_power:
                # skip board if old BMP version
                vi = self._transceiver.read_bmp_version(
                    board=board, frame=0, cabinet=0)
                if vi.version_number[0] < _BMP_VER_MIN:
                    continue

                # check each FPGA on board
                if not all(self._good_fpga(board, fpga)
                           for fpga in range(_N_FPGAS)):
                    retry_boards.append(board)

            # try again with incorrect boards only
            if len(retry_boards):
                boards_to_power = retry_boards
            else:
                return
        else:  # pragma: no cover
            raise Exception(
                "Could not get correct FPGA ID after {} tries".format(
                    _N_FPGA_RETRIES))

    def _set_link_state(self, link, enable, board):
        """ Set the power state of a link.

        :param link: The link (direction) to set the enable-state of.
        :type link: value in Links enum
        :param state: What to set the state to. True for on, False for off.
        :type state: bool
        :param board: Which board or boards to set the link enable-state of.
        :type board: int or iterable
        """
        # skip FPGA link configuration if old BMP version
        vi = self._transceiver.read_bmp_version(
            board=board, frame=0, cabinet=0)
        if vi.version_number[0] < _BMP_VER_MIN:
            return

        fpga, addr = FPGA_LINK_STOP_REGISTERS[link]
        self._transceiver.write_fpga_register(
            fpga, addr, int(not enable), board=board, frame=0, cabinet=0)

    def _run(self):
        """ The background thread for interacting with the BMP.
        """
        try:
            if self._on_thread_start is not None:
                self._on_thread_start()

            while True:
                self._requests_pending.wait()

                if self._requests:
                    request = self._requests.popleft()

                    for n_tries in range(_N_REQUEST_TRIES):
                        try:
                            # Send any power on commands
                            if request.power_on_boards:
                                self._power_on_and_check(
                                    request.power_on_boards)

                            # Process link requests next
                            for link_request in request.link_requests:
                                # Set the link state, as required
                                self._set_link_state(
                                    link_request.link, link_request.enable,
                                    link_request.board)

                            # Finally send any power off commands
                            if request.power_off_boards:
                                self._transceiver.power_off(
                                    boards=request.power_off_boards,
                                    frame=0, cabinet=0)

                            # Exit the retry loop if the requests all worked
                            request.on_done(True, None)
                            break
                        except Exception as e:
                            if (n_tries + 1) == _N_REQUEST_TRIES:
                                reason = "Requests failed on BMP {}".format(
                                    self._hostname)
                                logging.exception(reason + ": " + str(e))
                                request.on_done(False, reason)
                                break
                            logging.exception(
                                "Retrying requests on BMP {} after {}"
                                " seconds: {}".format(
                                    self._hostname, _SECONDS_BETWEEN_TRIES,
                                    str(e)))
                            time.sleep(_SECONDS_BETWEEN_TRIES)

                # If nothing left in the queues, clear the request flag and
                # break out of queue-processing loop.
                with self._lock:
                    if not self._requests:
                        self._requests_pending.clear()

                        # If we've been told to stop, actually stop the thread
                        # now
                        if self._stop:  # pragma: no branch
                            return
        except Exception:  # pragma: no cover
            # If the thread crashes something has gone wrong with this program
            # (not the machine), setting _stop will cause set_power and
            # set_link_enable to fail, hopefully propagating news of this
            # crash..
            with self._lock:
                self._stop = True
            raise

    @property
    def hostname(self):
        return self._hostname


class AtomicRequests(object):
    """ A list of requests that need to be done atomically; these are carried
        out in order of power on, link enable or disable, power off
    """

    __slots__ = [
        "_on_done",
        "_power_on_boards",
        "_power_off_boards",
        "_link_requests"
    ]

    def __init__(self, on_done):
        self._on_done = on_done
        self._power_on_boards = list()
        self._power_off_boards = list()
        self._link_requests = list()

    def power(self, board, power):
        if power:
            self._power_on_boards.append(board)
        else:
            self._power_off_boards.append(board)

    def link(self, board, link, enable):
        self._link_requests.append(LinkRequest(board, link, enable))

    @property
    def on_done(self):
        return self._on_done

    @property
    def power_on_boards(self):
        return self._power_on_boards

    @property
    def power_off_boards(self):
        return self._power_off_boards

    @property
    def link_requests(self):
        return self._link_requests


class LinkRequest(namedtuple("LinkRequest", "board link enable")):
    """ Requests that a specific board should have its power state set to a
    particular value.

    Parameters
    ----------
    board : int
        Board whose link should be blocked/unblocked
    link : :py:class:`spalloc_server.links.Link`
        The link whose state should be changed
    enable : bool
        State of the link: Enabled (True), disabled (False).
    """

    # Python 3.4 Workaround: https://bugs.python.org/issue24931
    __slots__ = tuple()


# The number of FPGAs
_N_FPGAS = 3

# The FLAG register address in the FPGAs
_FPGA_FLAG_REGISTER_ADDRESS = 0x40004

# The FPGA ID field within the FLAG register value
_FPGA_FLAG_ID_MASK = 0x3

# Gives the FPGA number and register addresses for the STOP register (which
# disables outgoing traffic on a high-speed link) for each link direction.
# https://github.com/SpiNNakerManchester/spio/tree/master/designs/spinnaker_fpgas#spi-interface
_REG_STOP_OFFSET = 0x5C
FPGA_LINK_STOP_REGISTERS = {
    Links.east: (0, 0x00000000 + _REG_STOP_OFFSET),
    Links.south: (0, 0x00010000 + _REG_STOP_OFFSET),
    Links.south_west: (1, 0x00000000 + _REG_STOP_OFFSET),
    Links.west: (1, 0x00010000 + _REG_STOP_OFFSET),
    Links.north: (2, 0x00000000 + _REG_STOP_OFFSET),
    Links.north_east: (2, 0x00010000 + _REG_STOP_OFFSET),
}
