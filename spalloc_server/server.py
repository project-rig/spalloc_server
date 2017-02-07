"""A TCP server which exposes a public interface for allocating boards.

This module is essentially the 'top level' module for the functionality of the
SpiNNaker Partitioning Server, containing the :py:func:`function <.main>` which
is mapped to the ``spalloc-server`` command-line tool.
"""

from os import path
import logging
import pickle
import socket
import select
import signal
import threading
import json
import argparse
import time

from collections import OrderedDict

from six import iteritems, string_types

from spalloc_server import __version__, coordinates, configuration
from spalloc_server.configuration import Configuration
from spalloc_server.controller import Controller

BUFFER_SIZE = 1024

_COMMANDS = {}
"""A dictionary from command names to (unbound) methods of the
:py:class:`.Server` class.
"""


def spalloc_command(f):
    """Decorator which marks a class function of :py:class:`.Server` as a
    command which may be called by a client.
    """
    _COMMANDS[f.__name__] = f
    return f


class PollingServerCore(object):
    """Wrapper around the operating system's poll() call.
    """
    def __init__(self):
        # The poll object used for listening for connections
        self._poll = select.poll()  # @UndefinedVariable
        if self._poll is None:  # pragma: no cover
            raise Exception("total failure to make a poller")
        # Used to map file descriptors to channels
        self._fdmap = {}
        # This socket pair is used by background threads to interrupt the main
        # event loop.
        self._notify_send, self._notify_recv = socket.socketpair()
        self.register_channel(self._notify_recv)

    def register_channel(self, channel):
        """Register a channel (file, socket, etc.) for being reported via the
        :py:meth:`.ready_channels` method.
        """
        self._poll.register(channel.fileno(),
                            select.POLLIN)  # @UndefinedVariable
        self._fdmap[channel.fileno()] = channel

    def unregister_channel(self, channel):
        """Unregister a channel (file, socket, etc.) for being reported via
        the :py:meth:`.ready_channels` method.
        """
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
        events = self._poll.poll(timeout_check_interval)
        for fd, _ in events:
            if fd == self._notify_recv.fileno():
                self._notify_recv.recv(BUFFER_SIZE)
            else:
                yield self._fdmap[fd]

    def wake(self):
        """Notify the waiting thread that something has happened.

        Calling this method simply wakes up the waiting thread (which will be
        waiting in :py:meth:`.ready_channels`) causing it to perform all its
        usual checks and processing steps.
        """
        self._notify_send.send(b"x")


class Server(PollingServerCore):
    """A TCP server which manages, power, partitioning and scheduling of jobs
    on SpiNNaker machines.

    Once constructed the server starts a background thread
    (:py:attr:`._server_thread`, :py:meth:`._run`) which implements the main
    server logic and handles communication with clients, monitoring of
    asynchronous board control events (e.g. board power-on completion) and
    watches the config file for changes. All members of this object are assumed
    to be accessed only from this thread while it is running. The thread is
    stopped, and its completion awaited by calling :py:meth:`.stop_and_join`,
    stopping the server.

    The server uses a :py:class:`~spalloc_server.Controller` object to
    implement scheduling, allocation and machine management functionality. This
    object is :py:mod:`pickled <pickle>` when the server shuts down in order to
    preserve the state of all managed machines (e.g. allocated jobs etc.).

    To allow the interruption of the server thread on asynchronous events from
    the Controller a :py:func:`~socket.socketpair` (:py:attr:`._notify_send`
    and :py:attr:`._notify_send`) is used which monitored along with client
    connections and config file changes.

    A number of callable commands are implemented by the server in the form of
    a subset of the :py:class:`.Server`'s methods indicated by the
    :py:func:`.spalloc_command` decorator. These may be called by a client by
    sending a line ``{"command": "...", "args": [...], "kwargs": {...}}``. If
    the function throws an exception, the client is disconnected. If the
    function returns, it is packed as a JSON line ``{"return": ...}``.
    """

    def __init__(self, config_filename, cold_start=False, port=22244):
        """
        Parameters
        ----------
        config_filename : str
            The filename of the config file for the server which describes the
            machines to be controlled.
        cold_start : bool, optional
            If False (the default), the server will attempt to restore its
            previous state, if True, the server will start from scratch.
        port : int, optional
            Which port to listen on. Defaults to 22244.
        """
        PollingServerCore.__init__(self)

        self._config_filename = config_filename
        self._cold_start = cold_start
        self._port = port

        # Should the background thread terminate?
        self._stop = False

        # The background thread in which the server will run
        self._server_thread = threading.Thread(target=self._run,
                                               name="Server Thread")

        # Currently open sockets to clients. Once server started, should only
        # be accessed from the server thread.
        self._server_socket = None

        # Buffered data received from each socket
        # {fd: buf, ...}
        self._client_buffers = {}

        # For each client, contains a set() of job IDs and machine names that
        # the client is watching for changes or None if all changes are to be
        # monitored.
        # {socket: set or None, ...}
        self._client_job_watches = {}
        self._client_machine_watches = {}

        # The current server configuration options. Once server started, should
        # only be accessed from the server thread.
        self._configuration = Configuration()

        # Infer the saved-state location
        self._state_filename = self._get_state_filename(self._config_filename)

        # Attempt to restore saved state if required
        self._controller = None
        if not self._cold_start and path.isfile(self._state_filename):
            try:
                with open(self._state_filename, "rb") as f:
                    self._controller = pickle.load(f)
                logging.info("Server warm-starting from %s.",
                             self._state_filename)
            except:
                # Some other error occurred during unpickling.
                logging.exception(
                    "Server state could not be unpacked from %s.",
                    self._state_filename)

        # Perform cold-start if no saved state was loaded
        if self._controller is None:
            logging.info("Server cold-starting.")
            self._controller = Controller()

        # Notify the background thread when something changes in the background
        # of the controller (e.g. power state changes).
        self._controller.on_background_state_change = self.wake

        # Read configuration file. This must succeed when the server is first
        # being started.
        if not self._read_config_file():
            raise Exception("Config file could not be loaded.")

        # Set up SIGHUP signal handler for config file reloading
        signal.signal(signal.SIGHUP, self._sighup_handler)

        # Start the server
        self._server_thread.start()

        # Flag for checking if the server is still alive
        self._running = True

    def _get_state_filename(self, cfg):
        """How to get the name of the state file from the name of another file
        (expected to be the config file). Assumes that the config file is in a
        directory that can be written to.

        :param str cfg: The name of the file to use as a base.
        """
        # Factored out for ease of reading.
        dirname = path.dirname(cfg)
        basename = path.basename(cfg)
        filename = ".{}.state.{}".format(basename, __version__)
        return path.join(dirname, filename)

    def _sighup_handler(self, signum, frame):
        """Handler for SIGHUP. If such a signal is delivered, will trigger a
        reread of the configuration file.

        Parameters
        ----------
        signum : int
        frame :
        """
        if signum == signal.SIGHUP:
            self._reload_config = True
            self.wake()

    def _read_config_file(self):
        """(Re-)read the server configuration.

        If reading of the config file fails, the current configuration is
        retained, unchanged.

        Returns
        -------
        bool
            True if reading succeded, False otherwise.
        """
        self._reload_config = False
        try:
            with open(self._config_filename, "r") as f:
                config_script = f.read()
        except (IOError, OSError):  # pragma: no cover
            logging.exception("Could not read config file %s",
                              self._config_filename)
            return False

        # The environment in which the configuration script is executed (and
        # where the script will store its options.)
        try:
            g = {}
            g.update(configuration.__dict__)
            g.update(coordinates.__dict__)
            exec(config_script, g)
        except:
            # Executing the config file failed, don't update any settings
            logging.exception("Error while evaluating config file %s",
                              self._config_filename)
            return False

        # Make sure a configuration object is specified
        new = g.get("configuration", None)
        if not isinstance(new, Configuration):
            logging.error("'configuration' must be a Configuration object " +
                          "in config file %s", self._config_filename)
            return False

        # Update the configuration
        old = self._configuration
        self._configuration = new

        # Restart the server if the port or IP has changed (or if the server
        # has not yet been started...)
        if (new.ip != old.ip or
                self._server_socket is None):
            # Close all open connections
            if self._close():  # pragma: no cover
                time.sleep(0.25)  # Ugly hack; fully release socket now

            # Create a new server socket
            self._open(new.ip)

        # Update the controller
        self._controller.max_retired_jobs = new.max_retired_jobs
        self._controller.machines = OrderedDict((m.name, m)
                                                for m in new.machines)

        logging.info("Config file %s read successfully.",
                     self._config_filename)
        return True

    def _open(self, ipaddress):
        """Create a new server socket.

        :param str ipaddress: The local address of the socket.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((ipaddress, self._port))
        sock.listen(5)
        self.register_channel(sock)
        self._server_socket = sock

    def _close(self):
        """Close all server sockets and disconnect all client connections."""
        result = False
        if self._server_socket is not None:
            self.unregister_channel(self._server_socket)
            self._server_socket.close()
            result = True
            self._server_socket = None
        for client_socket in list(self._client_buffers.keys()):
            self._disconnect_client(client_socket)
        return result

    def _disconnect_client(self, client):
        """Disconnect a client.

        Parameters
        ----------
        client : :py:class:`socket.Socket`
        """
        try:
            logging.info("Client %s disconnected.", client.getpeername())
        except:
            logging.info("Client %s disconnected.", client)

        # Clear input buffer
        del self._client_buffers[client]

        # Clear any watches
        self._client_job_watches.pop(client, None)
        self._client_machine_watches.pop(client, None)

        # Stop watching the client's socket for data
        self.unregister_channel(client)

        # Disconnect the client
        client.close()

    def _accept_client(self):
        """Accept a new client."""
        try:
            client, addr = self._server_socket.accept()
        except IOError as e:  # pragma: no cover
            logging.warn("problem when accepting connection", exc_info=e)
            return
        logging.info("New client connected from %s", addr)

        # Watch the client's socket for data
        self.register_channel(client)

        # Create a buffer for data sent by the client
        self._client_buffers[client] = b""

    def _msg_client(self, client, message):
        """Low-level way to send a message to a client.

        Parameters
        ----------
        client : :py:class:`socket.Socket`
            The client that we are sending a message to.
        message :
            The object or array to send. Will be converted to JSON.
        """
        try:
            client.send(json.dumps(message).encode("utf-8") + b"\n")
        except:
            self._disconnect_client(client)
            raise

    def _handle_command(self, client, line):
        """Dispatch a single command.

        Parameters
        ----------
        client : :py:class:`socket.Socket`
            The client that made the request, used to provide a session
            context where relevant.
        line : string
            The line parsed from the socket. Should be a complete JSON object
            with at least a 'command' key.
        """
        cmd_obj = json.loads(line.decode("utf-8"))
        if "command" not in cmd_obj:
            raise IOError("no command name given")
        commandName = cmd_obj["command"]
        if not isinstance(commandName, string_types):
            raise IOError("parsed gibberish from user")
        if commandName not in _COMMANDS:
            logging.info("lookup failure: %s", commandName)
            raise IOError("unrecognised command name")
        command = _COMMANDS[commandName]
        if command is None:  # pragma: no cover
            # Should be unreachable
            logging.critical("unexpected lookup failure: %s", commandName)
            raise IOError("unrecognised command name")
        args = cmd_obj["args"] if "args" in cmd_obj else []
        if not isinstance(args, list):
            raise IOError("bad args; must be JSON array")
        kwargs = cmd_obj["kwargs"] if "kwargs" in cmd_obj else {}
        if not isinstance(kwargs, dict):
            raise IOError("bad kwargs: must be JSON dictionary")
        elif "client" in kwargs:
            del kwargs["client"]
        # Execute the specified command
        return command(self, client, *args, **kwargs)

    def _handle_commands(self, client):
        """Handle incoming commands from a client.

        Parameters
        ----------
        client : :py:class:`socket.Socket`
        """
        try:
            data = client.recv(BUFFER_SIZE)
        except (OSError, IOError):
            data = b""

        # Did the client disconnect?
        if len(data) == 0:
            self._disconnect_client(client)
            return

        peer = client.getpeername()
        self._client_buffers[client] += data

        try:
            # Process any complete commands (whole lines)
            while b"\n" in self._client_buffers[client]:
                line, _, self._client_buffers[client] = \
                    self._client_buffers[client].partition(b"\n")
                # Note that we skip blank lines
                if len(line) > 0:
                    self._msg_client(client, {
                        "return": self._handle_command(client, line)
                    })
        except:
            # If any of the above fails for any reason (e.g. invalid JSON,
            # unrecognised command, command crashes, etc.), just disconnect
            # the client.
            logging.exception("Client %s sent bad command %r, disconnecting",
                              peer, line)
            self._disconnect_client(client)

    def _send_notifications(self, label, changes, watches):
        """How to actually send requested notifications."""
        if changes:
            for client, items in list(iteritems(watches)):
                if items is None or not items.isdisjoint(changes):
                    try:
                        self._msg_client(client, {
                            label: list(changes) if items is None else
                            list(changes.intersection(items))})
                    except (OSError, IOError):
                        logging.exception("Could not send notification.")

    def _send_change_notifications(self):
        """Send any registered change notifications to clients.

        Sends notifications of the forms ``{"jobs_changed": [job_id, ...]}``
        and ``{"machines_changed": [machine_name, ...]}`` to clients who have
        subscribed to be notified of changes to jobs or machines.
        """
        # Notify clients about jobs which have changed
        self._send_notifications("jobs_changed",
                                 self._controller.changed_jobs,
                                 self._client_job_watches)
        # Notify clients about machines which have changed
        self._send_notifications("machines_changed",
                                 self._controller.changed_machines,
                                 self._client_machine_watches)

    def _run(self):
        """The main server thread.

        This 'infinite' loop runs in a background thread and waits for and
        processes events such as the :py:meth:`.PollingServerCore.wake` method
        being called, the config file changing, clients sending commands or
        new clients connecting. It also periodically calls
        :py:meth:`.controller.Controller.destroy_timed_out_jobs` on the
        controller.
        """
        logging.info("Server running.")
        while not self._stop:
            # Wait for a connection to get opened/closed, a command to arrive,
            # the config file to change or the timeout to elapse.
            channels = self.ready_channels(
                self._configuration.timeout_check_interval)

            # Cull any jobs which have timed out
            self._controller.destroy_timed_out_jobs()

            for channel in channels:
                if channel is None:  # pragma: no cover
                    continue
                if channel == self._server_socket:
                    # New client connected
                    self._accept_client()
                else:
                    # Incoming data from client
                    self._handle_commands(channel)

            # Send any job/machine change notifications out
            self._send_change_notifications()

            # Config file changed, re-read it
            if self._reload_config:
                if not self._read_config_file():  # pragma: no cover
                    logging.warning("failed to reread configuration file")

    def is_alive(self):
        """Is the server running?"""
        return self._running

    def join(self):
        """Wait for the server to completely shut down."""
        self._server_thread.join()
        self._controller.join()

    def stop_and_join(self):
        """Stop the server and wait for it to shut down completely."""
        logging.info("Server shutting down, please wait...")

        # Shut down server thread
        self._stop = True
        self.wake()
        self._server_thread.join()

        # Close all connections
        logging.info("Closing connections...")
        self._close()

        # Shut down the controller and flush all BMP commands
        logging.info("Waiting for all queued BMP commands...")
        self._controller.stop()
        self._controller.join()

        # Dump controller state to file
        with open(self._state_filename, "wb") as f:
            pickle.dump(self._controller, f)

        logging.info("Server shut down.")

        self._running = False

    @spalloc_command
    def version(self, client):
        """
        Returns
        -------
        str
            The server's version number."""
        return __version__

    @spalloc_command
    def create_job(self, client, *args, **kwargs):
        """Create a new job (i.e. allocation of boards).

        This function should be called in one of the following styles::

            # Any single (SpiNN-5) board
            job_id = create_job(owner="me")
            job_id = create_job(1, owner="me")

            # Board x=3, y=2, z=1 on the machine named "m"
            job_id = create_job(3, 2, 1, machine="m", owner="me")

            # Any machine with at least 4 boards
            job_id = create_job(4, owner="me")

            # Any 7-or-more board machine with an aspect ratio at least as
            # square as 1:2
            job_id = create_job(7, min_ratio=0.5, owner="me")

            # Any 4x5 triad segment of a machine (may or may-not be a
            # torus/full machine)
            job_id = create_job(4, 5, owner="me")

            # Any torus-connected (full machine) 4x2 machine
            job_id = create_job(4, 2, require_torus=True, owner="me")

        The 'other parameters' enumerated below may be used to further restrict
        what machines the job may be allocated onto.

        Jobs for which no suitable machines are available are immediately
        destroyed (and the reason given).

        Once a job has been created, it must be 'kept alive' by a simple
        watchdog_ mechanism. Jobs may be kept alive by periodically calling the
        :py:meth:`.job_keepalive` command or by calling any other job-specific
        command. Jobs are culled if no keep alive message is received for
        ``keepalive`` seconds. If absolutely necessary, a job's keepalive value
        may be set to None, disabling the keepalive mechanism.

        .. _watchdog: https://en.wikipedia.org/wiki/Watchdog_timer

        Once a job has been allocated some boards, these boards will be
        automatically powered on and left unbooted ready for use.

        Parameters
        ----------
        owner : str
            **Required.** The name of the owner of this job.
        keepalive : float or None, optional
            The maximum number of seconds which may elapse between a query on
            this job before it is automatically destroyed. If None, no timeout
            is used. (Default: 60.0)

        Other Parameters
        ----------------
        machine : str or None, optional
            Specify the name of a machine which this job must be executed on.
            If None, the first suitable machine available will be used,
            according to the tags selected below. Must be None when tags are
            given. (Default: None)
        tags : [str, ...] or None, optional
            The set of tags which any machine running this job must have. If
            None is supplied, only machines with the "default" tag will be
            used. If machine is given, this argument must be None.
            (Default: None)
        min_ratio : float, optional
            The aspect ratio (h/w) which the allocated region must be 'at least
            as square as'. Set to 0.0 for any allowable shape, 1.0 to be
            exactly square. Ignored when allocating single boards or specific
            rectangles of triads.
        max_dead_boards : int or None, optional
            The maximum number of broken or unreachable boards to allow in the
            allocated region. If None, any number of dead boards is permitted,
            as long as the board on the bottom-left corner is alive (Default:
            None).
        max_dead_links : int or None, optional
            The maximum number of broken links allow in the allocated region.
            When require_torus is True this includes wrap-around links,
            otherwise peripheral links are not counted.  If None, any number of
            broken links is allowed. (Default: None).
        require_torus : bool, optional
            If True, only allocate blocks with torus connectivity. In general
            this will only succeed for requests to allocate an entire machine
            (when the machine is otherwise not in use!). Must be False when
            allocating boards. (Default: False)

        Returns
        -------
        int
            The job ID given to the newly allocated job.
        """
        if kwargs.get("tags", None) is not None:
            kwargs["tags"] = set(kwargs["tags"])
        owner = kwargs.get("owner", None)
        if owner is None:
            raise TypeError("owner must be specified for all jobs")
        kwargs["owner"] = str(owner)
        keepalive = kwargs.get("keepalive", 60.0)
        kwargs["keepalive"] = (None if keepalive is None else float(keepalive))
        return self._controller.create_job(*args, **kwargs)

    @spalloc_command
    def job_keepalive(self, client, job_id):
        """Reset the keepalive timer for the specified job.

        Note all other job-specific commands implicitly do this.

        Parameters
        ----------
        job_id : int
            A job ID to be kept alive.
        """
        self._controller.job_keepalive(job_id)

    @spalloc_command
    def get_job_state(self, client, job_id):
        """Poll the state of a running job.

        Parameters
        ----------
        job_id : int
            A job ID to get the state of.

        Returns
        -------
        {"state": state, "power": power, \
         "keepalive": keepalive, "reason": reason}
            Where:

            state : :py:class:`~spalloc_server.controller.JobState`
                The current state of the queried job.
            power : bool or None
                If job is in the ready or power states, indicates whether the
                boards are power{ed,ing} on (True), or power{ed,ing} off
                (False). In other states, this value is None.
            keepalive : float or None
                The Job's keepalive value: the number of seconds between
                queries about the job before it is automatically destroyed.
                None if no timeout is active (or when the job has been
                destroyed).
            reason : str or None
                If the job has been destroyed, this may be a string describing
                the reason the job was terminated.
            start_time : float or None
                For queued and allocated jobs, gives the Unix time (UTC) at
                which the job was created (or None otherwise).
        """
        out = self._controller.get_job_state(job_id)._asdict()
        out["state"] = int(out["state"])
        return out

    @spalloc_command
    def get_job_machine_info(self, client, job_id):
        """Get the list of Ethernet connections to the allocated machine.

        Parameters
        ----------
        job_id : int
            A job ID to get the machine info for.

        Returns
        -------
        {"width": width, "height": height, \
         "connections": connections, "machine_name": machine_name}
            Where:

            width, height : int or None
                The dimensions of the machine in chips, e.g. for booting.

                None if no boards are allocated to the job.
            connections : [[[x, y], hostname], ...] or None
                A list giving Ethernet-connected chip coordinates in the
                machine to hostname.

                None if no boards are allocated to the job.
            machine_name : str or None
                The name of the machine the job is allocated on.

                None if no boards are allocated to the job.
            boards : [[x, y, z], ...] or None
                All the boards allocated to the job or None if no boards
                allocated.
        """
        width, height, connections, machine_name, boards = \
            self._controller.get_job_machine_info(job_id)

        if connections is not None:
            connections = list(iteritems(connections))
        if boards is not None:
            boards = list(boards)

        return {"width": width, "height": height,
                "connections": connections,
                "machine_name": machine_name,
                "boards": boards}

    @spalloc_command
    def power_on_job_boards(self, client, job_id):
        """Power on (or reset if already on) boards associated with a job.

        Once called, the job will enter the 'power' state until the power state
        change is complete, this may take some time.

        Parameters
        ----------
        job_id : int
            A job ID to turn boards on for.
        """
        self._controller.power_on_job_boards(job_id)

    @spalloc_command
    def power_off_job_boards(self, client, job_id):
        """Power off boards associated with a job.

        Once called, the job will enter the 'power' state until the power state
        change is complete, this may take some time.

        Parameters
        ----------
        job_id : int
            A job ID to turn boards off for.
        """
        self._controller.power_off_job_boards(job_id)

    @spalloc_command
    def destroy_job(self, client, job_id, reason=None):
        """Destroy a job.

        Call when the job is finished, or to terminate it early, this function
        releases any resources consumed by the job and removes it from any
        queues.

        Parameters
        ----------
        job_id : int
            A job ID to destroy.
        reason : str, optional
            A human-readable string describing the reason for the job's
            destruction.
        """
        self._controller.destroy_job(job_id, reason)

    def _register_for_notifications(self, client, watchset,
                                    id):  # @ReservedAssignment
        """Helper method that handles the protocol for registration for
        notifications."""
        if id is None:
            watchset[client] = None
        elif client not in watchset:
            watchset[client] = set([id])
        elif watchset[client] is not None:
            watchset[client].add(id)
        else:
            # Client is already notified about all changes, do nothing!
            pass

    def _unregister_for_notifications(self, client, watchset,
                                      id):  # @ReservedAssignment
        """Helper method that handles the protocol for unregistration for
        notifications."""
        if client not in watchset:
            return
        if id is None:
            del watchset[client]
            return
        watches = watchset[client]
        if watches is not None:
            watches.discard(id)
            if len(watches) == 0:
                del watchset[client]

    @spalloc_command
    def notify_job(self, client, job_id=None):
        r"""Register to be notified about changes to a specific job ID.

        Once registered, a client will be asynchronously be sent notifications
        form ``{"jobs_changed": [job_id, ...]}\n`` enumerating job IDs which
        have changed. Notifications are sent when a job changes state, for
        example when created, queued, powering on/off, powered on and
        destroyed. The specific nature of the change is not reflected in the
        notification.

        Parameters
        ----------
        job_id : int or None, optional
            A job ID to be notified of or None if all job state changes should
            be reported.

        See Also
        --------
        no_notify_job : Stop being notified about a job.
        notify_machine : Register to be notified about changes to machines.
        """
        self._register_for_notifications(client, self._client_job_watches,
                                         job_id)

    @spalloc_command
    def no_notify_job(self, client, job_id=None):
        """Stop being notified about a specific job ID.

        Once this command returns, no further notifications for the specified
        ID will be received.

        Parameters
        ----------
        job_id : id or None, optional
            A job ID to no longer be notified of or None to not be notified of
            any jobs. Note that if all job IDs were registered for
            notification, this command only has an effect if the specified
            job_id is None.

        See Also
        --------
        notify_job : Register to be notified about changes to a specific job.
        """
        self._unregister_for_notifications(client, self._client_job_watches,
                                           job_id)

    @spalloc_command
    def notify_machine(self, client, machine_name=None):
        r"""Register to be notified about a specific machine name.

        Once registered, a client will be asynchronously be sent notifications
        of the form ``{"machines_changed": [machine_name, ...]}\n`` enumerating
        machine names which have changed. Notifications are sent when a machine
        changes state, for example when created, change, removed, allocated a
        job or an allocated job is destroyed.

        Parameters
        ----------
        machine_name : machine or None, optional
            A machine name to be notified of or None if all machine state
            changes should be reported.

        See Also
        --------
        no_notify_machine : Stop being notified about a machine.
        notify_job : Register to be notified about changes to jobs.
        """
        self._register_for_notifications(client,
                                         self._client_machine_watches,
                                         machine_name)

    @spalloc_command
    def no_notify_machine(self, client, machine_name=None):
        """Unregister to be notified about a specific machine name.

        Once this command returns, no further notifications for the specified
        ID will be received.

        Parameters
        ----------
        machine_name : name or None, optional
            A machine name to no longer be notified of or None to not be
            notified of any machines. Note that if all machines were registered
            for notification, this command only has an effect if the specified
            machine_name is None.

        See Also
        --------
        notify_machine : Register to be notified about changes to a machine.
        """
        self._unregister_for_notifications(client,
                                           self._client_machine_watches,
                                           machine_name)

    @spalloc_command
    def list_jobs(self, client):
        """Enumerate all non-destroyed jobs.

        Returns
        -------
        jobs : [{...}, ...]
            A list of allocated/queued jobs in order of creation from oldest
            (first) to newest (last). Each job is described by a dictionary
            with the following keys:

            "job_id" is the ID of the job.

            "owner" is the string giving the name of the Job's owner.

            "start_time" is the time the job was created (Unix time, UTC).

            "keepalive" is the maximum time allowed between queries for this
            job before it is automatically destroyed (or None if the job can
            remain allocated indefinitely).

            "state" is the current
            :py:class:`~spalloc_server.controller.JobState` of the job.

            "power" indicates whether the boards are powered on or not. If job
            is in the ready or power states, indicates whether the boards are
            power{ed,ing} on (True), or power{ed,ing} off (False). In other
            states, this value is None.

            "args" and "kwargs" are the arguments to the alloc function
            which specifies the type/size of allocation requested and the
            restrictions on dead boards, links and torus connectivity.

            "allocated_machine_name" is the name of the machine the job has
            been allocated to run on (or None if not allocated yet).

            "boards" is a list [(x, y, z), ...] of boards allocated to the job.
        """
        out = []
        for job in self._controller.list_jobs():
            job = job._asdict()
            job["state"] = int(job["state"])
            if job["boards"] is not None:
                job["boards"] = list(job["boards"])
            if job["kwargs"].get("tags", None) is not None:
                job["kwargs"]["tags"] = list(job["kwargs"]["tags"])
            out.append(job)
        return out

    @spalloc_command
    def list_machines(self, client):
        """Enumerates all machines known to the system.

        Returns
        -------
        machines : [{...}, ...]
            The list of machines known to the system in order of priority from
            highest (first) to lowest (last). Each machine is described by a
            dictionary with the following keys:

            "name" is the name of the machine.

            "tags" is the list ['tag', ...] of tags the machine has.

            "width" and "height" are the dimensions of the machine in
            triads.

            "dead_boards" is a list([(x, y, z), ...]) giving the coordinates
            of known-dead boards.

            "dead_links" is a list([(x, y, z, link), ...]) giving the
            locations of known-dead links from the perspective of the sender.
            Links to dead boards may or may not be included in this list.
        """
        out = []
        for machine in self._controller.list_machines():
            machine = machine._asdict()
            machine["tags"] = list(machine["tags"])
            machine["dead_boards"] = list(machine["dead_boards"])
            machine["dead_links"] = [(x, y, z, int(link))
                                     for x, y, z, link
                                     in machine["dead_links"]]
            out.append(machine)
        return out

    @spalloc_command
    def get_board_position(self, client, machine_name, x, y, z):
        """Get the physical location of a specified board.

        Parameters
        ----------
        machine_name : str
            The name of the machine containing the board.
        x, y, z : int
            The logical board location within the machine.

        Returns
        -------
        (cabinet, frame, board) or None
            The physical location of the board at the specified location or
            None if the machine/board are not recognised.
        """
        return self._controller.get_board_position(machine_name, x, y, z)

    @spalloc_command
    def get_board_at_position(self, client, machine_name, x, y, z):
        """Get the logical location of a board at the specified physical
        location.

        Parameters
        ----------
        machine_name : str
            The name of the machine containing the board.
        cabinet, frame, board : int
            The physical board location within the machine.

        Returns
        -------
        (x, y, z) or None
            The logical location of the board at the specified location or None
            if the machine/board are not recognised.
        """
        return self._controller.get_board_at_position(machine_name, x, y, z)

    @spalloc_command
    def where_is(self, client, **kwargs):
        """Find out where a SpiNNaker board or chip is located, logically and
        physically.

        May be called in one of the following styles::

            >>> # Query by logical board coordinate within a machine.
            >>> where_is(machine=..., x=..., y=..., z=...)

            >>> # Query by physical board location within a machine.
            >>> where_is(machine=..., cabinet=..., frame=..., board=...)

            >>> # Query by chip coordinate (as if the machine were booted as
            >>> # one large machine).
            >>> where_is(machine=..., chip_x=..., chip_y=...)

            >>> # Query by chip coordinate, within the boards allocated to a
            >>> # job.
            >>> where_is(job_id=..., chip_x=..., chip_y=...)

        Returns
        -------
        {"machine": ..., "logical": ..., "physical": ..., "chip": ..., \
                "board_chip": ..., "job_chip": ..., "job_id": ...} or None
            If a board exists at the supplied location, a dictionary giving the
            location of the board/chip, supplied in a number of alternative
            forms. If the supplied coordinates do not specify a specific chip,
            the chip coordinates given are those of the Ethernet connected chip
            on that board.

            If no board exists at the supplied position, None is returned
            instead.

            ``machine`` gives the name of the machine containing the board.

            ``logical`` the logical board coordinate, (x, y, z) within the
            machine.

            ``physical`` the physical board location, (cabinet, frame, board),
            within the machine.

            ``chip`` the coordinates of the chip, (x, y), if the whole machine
            were booted as a single machine.

            ``board_chip`` the coordinates of the chip, (x, y), within its
            board.

            ``job_id`` is the job ID of the job currently allocated to the
            board identified or None if the board is not allocated to a job.

            ``job_chip`` the coordinates of the chip, (x, y), within its
            job, if a job is allocated to the board or None otherwise.
        """
        return self._controller.where_is(**kwargs)


def main(args=None):
    """Command-line launcher for the server.

    The server may be cleanly terminated using a keyboard interrupt (e.g.,
    ctrl+c), and may be told to reload its configuration via SIGHUP.

    Parameters
    ----------
    args : [arg, ...], optional
        The command-line arguments passed to the program.
    """
    parser = argparse.ArgumentParser(
        description="Start a SpiNNaker machine partitioning server.")
    parser.add_argument("--version", "-V", action="version",
                        version="%(prog)s {}".format(__version__))
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="hide non-error output")
    parser.add_argument("config", type=str,
                        help="configuration filename to use for the server.")
    parser.add_argument("--cold-start", action="store_true",
                        default=False,
                        help="force a cold start, erasing any existing "
                             "saved state")
    parser.add_argument("--port", "-p", type=int, default=22244,
                        help="port to run the service on")
    args = parser.parse_args(args)

    if not args.quiet:
        logging.basicConfig(level=logging.INFO)

    server = Server(config_filename=args.config,
                    cold_start=args.cold_start, port=args.port)
    try:
        # NB: Originally this loop was replaced with a call to server.join
        # however in Python 2, such blocking calls are not interruptible so we
        # use this rather ugly workaround instead.
        while server.is_alive():  # pragma: no cover
            time.sleep(0.1)
    except KeyboardInterrupt:
        server.stop_and_join()

    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
