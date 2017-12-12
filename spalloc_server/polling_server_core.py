from errno import EWOULDBLOCK
import logging as log
import select
import socket

BUFFER_SIZE = 1024


def _simulated_socketpair():  # pragma: no cover
    """Create your own socket pair."""
    temp_srv = socket.socket()
    try:
        temp_srv.setblocking(True)
        temp_srv.bind(('localhost', 0))
        port = temp_srv.getsockname()[1]
        temp_srv.listen(1)
        notify_send = socket.socket()
        notify_send.setblocking(False)
        try:
            notify_send.connect(('localhost', port))
        except socket.error as err:
            # EWOULDBLOCK is not an error, as the socket is non-blocking
            if err.errno != EWOULDBLOCK:
                raise
        notify_recv, _ = temp_srv.accept()
        return (notify_send, notify_recv)
    finally:
        temp_srv.close()


class PollingServerCore(object):
    """Wrapper around the operating system's poll() call. Also knows the\
    basics of making sockets.
    """
    def __init__(self):
        # The poll object used for listening for connections
        self._poll = None
        if hasattr(select, "poll"):
            self._poll = select.poll()  # @UndefinedVariable

        # Used to map file descriptors to channels
        self._fdmap = {}

        # This socket pair is used by background threads to interrupt the main
        # event loop.
        self._notify_send, self._notify_recv = None, None
        if hasattr(socket, "socketpair"):
            self._notify_send, self._notify_recv = socket.socketpair()
        else:  # pragma: no cover
            # Create your own socket pair
            self._notify_send, self._notify_recv = _simulated_socketpair()
        self.register_channel(self._notify_recv)

    def register_channel(self, channel):
        """Register a channel (file, socket, etc.) for being reported via the
        :py:meth:`.ready_channels` method.
        """
        if self._poll is not None:
            self._poll.register(
                channel.fileno(), select.POLLIN)  # @UndefinedVariable
        self._fdmap[channel.fileno()] = channel

    def unregister_channel(self, channel):
        """Unregister a channel (file, socket, etc.) for being reported via
        the :py:meth:`.ready_channels` method.
        """
        if self._poll is not None:
            self._poll.unregister(channel)
        if channel.fileno() in self._fdmap:
            del self._fdmap[channel.fileno()]

    def ready_channels(self, timeout_check_interval):
        """Waits up to timeout_check_interval milliseconds for a channel to
        become readable. Multiple channels may become readable at once.

        :return: An iterable listing the channels that are currently
                 readable. Can be empty if the poller is woken via the
                 :py:meth:`.wake` call.
        """
        if self._poll is not None:
            events = self._poll.poll(timeout_check_interval)
            for fd, _ in events:
                if fd == self._notify_recv.fileno():
                    self._notify_recv.recv(BUFFER_SIZE)
                else:
                    yield self._fdmap[fd]
        else:  # pragma: no cover
            channels = self._fdmap.values()
            readable, _, _ = select.select(
                channels, [], [], timeout_check_interval)
            for channel in readable:
                if channel == self._notify_recv:
                    self._notify_recv.recv(BUFFER_SIZE)
                else:
                    yield channel

    def wake(self):
        """Notify the waiting thread that something has happened.

        Calling this method simply wakes up the waiting thread (which will be
        waiting in :py:meth:`.ready_channels`) causing it to perform all its
        usual checks and processing steps.
        """
        self._notify_send.send(b"x")

    def _open_server_socket(self, ipaddress, port):
        """Create a new server socket.

        :param str ipaddress: The local address of the socket.
        :param int port: The local port of the socket.
        """
        log.info("opening server socket for %s", (ipaddress, port))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((ipaddress, port))
        sock.listen(5)
        self.register_channel(sock)
        return sock
