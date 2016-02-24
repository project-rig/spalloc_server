"""A TCP server which exposes a public interface for allocating boards.

This module is essentially the 'top level' module for the functionality of the
SpiNNaker Partitioning Server, containing the :py:func:`function <.main>` which
is mapped to the ``spalloc-server`` command-line tool.
"""

import os
import os.path
import logging
import pickle
import socket
import select
import threading
import json
import argparse
import time

from collections import OrderedDict

from inotify_simple import INotify
from inotify_simple import flags as inotify_flags

from six import itervalues, iteritems

from spalloc_server import __version__, coordinates, configuration
from spalloc_server.configuration import Configuration
from spalloc_server.controller import Controller


_COMMANDS = OrderedDict()
"""A dictionary from command names to (unbound) methods of the
:py:class:`.Server` class.
"""


def _command(f):
    """Decorator which marks a class function of :py:class:`.Server` as a
    command which may be called by a client.
    """
    _COMMANDS[f.__name__] = f
    return f


class Server(object):
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
    and :py:attr:`._notify_send`) is used which monitored allong with client
    connections and config file changes.

    A number of callable commands are implemented by the server in the form of
    a subset of the :py:class:`.Server`'s methods indicated by the
    :py:func:`._command` decorator. These may be called by a client by sending
    a line ``{"command": "...", "args": [...], "kwargs": {...}}``. If the
    function throws an exception, the client is disconnected. If the function
    returns, it is packed as a JSON line ``{"return": ...}``.
    """

    def __init__(self, config_filename, cold_start=False):
        """
        Parameters
        ----------
        config_filename : str
            The filename of the config file for the server which describes the
            machines to be controlled.
        cold_start : bool
            If False (the default), the server will attempt to restore its
            previous state, if True, the server will start from scratch.
        """
        self._config_filename = config_filename
        self._cold_start = cold_start

        # Should the background thread terminate?
        self._stop = False

        # The background thread in which the server will run
        self._server_thread = threading.Thread(target=self._run,
                                               name="Server Thread")

        # The poll object used for listening for connections
        self._poll = select.poll()

        # This socket pair is used by background threads to interrupt the main
        # event loop.
        self._notify_send, self._notify_recv = socket.socketpair()
        self._poll.register(self._notify_recv, select.POLLIN)

        # Currently open sockets to clients. Once server started, should only
        # be accessed from the server thread.
        self._server_socket = None
        # {fd: socket, ...}
        self._client_sockets = {}

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
        self._state_filename = os.path.join(
            os.path.dirname(self._config_filename),
            ".{}.state.{}".format(os.path.basename(self._config_filename),
                                  __version__)
        )

        # Attempt to restore saved state if required
        self._controller = None
        if not self._cold_start:
            if os.path.isfile(self._state_filename):
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
        self._controller.on_background_state_change = self._notify

        # Read configuration file. This must succeed when the server is first
        # being started.
        if not self._read_config_file():
            raise Exception("Config file could not be loaded.")

        # Set up inotify watcher for config file changes
        self._config_inotify = INotify()
        self._poll.register(self._config_inotify.fd, select.POLLIN)
        self._watch_config_file()

        # Start the server
        self._server_thread.start()

        # Flag for checking if the server is still alive
        self._running = True

    def _notify(self):
        """Notify the background thread that something has happened.

        Calling this method simply wakes up the server thread causing it to
        perform all its usual checks and processing steps.
        """
        self._notify_send.send(b"x")

    def _watch_config_file(self):
        """Create an inotify watch on the config file.

        This watch is monitored by the main server threead and if the config
        file is changed, the config file is re-read.
        """
        # A one-shot watch is used since some editors cause a delete event to
        # be produced when the file is saved, removing the watch anyway. Using
        # a one-shot watch simplifies implementation as it requires the watch
        # to *always* be recreated, rather than just 'sometimes'.
        self._config_inotify.add_watch(self._config_filename,
                                       inotify_flags.MODIFY |
                                       inotify_flags.ATTRIB |
                                       inotify_flags.CLOSE_WRITE |
                                       inotify_flags.MOVED_TO |
                                       inotify_flags.CREATE |
                                       inotify_flags.DELETE |
                                       inotify_flags.DELETE_SELF |
                                       inotify_flags.MOVE_SELF |
                                       inotify_flags.ONESHOT)

    def _read_config_file(self):
        """(Re-)read the server configuration.

        If reading of the config file fails, the current configuration is
        retained, unchanged.

        Returns
        -------
        bool
            True if reading succeded, False otherwise.
        """
        try:
            with open(self._config_filename, "r") as f:
                config_script = f.read()  # pragma: no branch
        except (IOError, OSError):
            logging.exception("Could not read "
                              "config file %s", self._config_filename)
            return False

        # The environment in which the configuration script is exexcuted (and
        # where the script will store its options.)
        try:
            g = {}
            g.update(configuration.__dict__)
            g.update(coordinates.__dict__)
            exec(config_script, g)
        except:
            # Executing the config file failed, don't update any settings
            logging.exception("Error while evaluating "
                              "config file %s", self._config_filename)
            return False

        # Make sure a configuration object is specified
        new = g.get("configuration", None)
        if not isinstance(new, Configuration):
            logging.error("'configuration' must be a Configuration object "
                          "in config file %s", self._config_filename)
            return False

        # Update the configuration
        old = self._configuration
        self._configuration = new

        # Restart the server if the port or IP has changed (or if the server
        # has not yet been started...)
        if (new.port != old.port or
                new.ip != old.ip or
                self._server_socket is None):
            # Close all open connections
            self._close()

            # Create a new server socket
            self._server_socket = socket.socket(socket.AF_INET,
                                                socket.SOCK_STREAM)
            self._server_socket.setsockopt(socket.SOL_SOCKET,
                                           socket.SO_REUSEADDR, 1)
            self._server_socket.bind((new.ip, new.port))
            self._server_socket.listen(5)
            self._poll.register(self._server_socket,
                                select.POLLIN)

        # Update the controller
        self._controller.max_retired_jobs = new.max_retired_jobs
        self._controller.machines = OrderedDict((m.name, m)
                                                for m in new.machines)

        logging.info("Config file %s read successfully.",
                     self._config_filename)
        return True

    def _close(self):
        """Close all server sockets and disconnect all client connections."""
        if self._server_socket is not None:
            self._poll.unregister(self._server_socket)
            self._server_socket.close()
        for client_socket in list(itervalues(self._client_sockets)):
            self._disconnect_client(client_socket)

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

        # Remove from the client list
        del self._client_sockets[client.fileno()]

        # Clear input buffer
        del self._client_buffers[client]

        # Clear any watches
        self._client_job_watches.pop(client, None)
        self._client_machine_watches.pop(client, None)

        # Stop watching the client's socket for data
        self._poll.unregister(client)

        # Disconnect the client
        client.close()

    def _accept_client(self):
        """Accept a new client."""
        client, addr = self._server_socket.accept()
        logging.info("New client connected from %s", addr)

        # Watch the client's socket for datat
        self._poll.register(client, select.POLLIN)

        # Keep a reference to the socket
        self._client_sockets[client.fileno()] = client

        # Create a buffer for data sent by the client
        self._client_buffers[client] = b""

    def _handle_commands(self, client):
        """Handle an incomming command from a client.

        Parameters
        ----------
        client : :py:class:`socket.Socket`
        """
        try:
            data = client.recv(1024)
        except (OSError, IOError):
            data = b""

        # Did the client disconnect?
        if len(data) == 0:
            self._disconnect_client(client)
            return

        self._client_buffers[client] += data

        # Process any complete commands (whole lines)
        while b"\n" in self._client_buffers[client]:
            line, _, self._client_buffers[client] = \
                self._client_buffers[client].partition(b"\n")

            try:
                cmd_obj = json.loads(line.decode("utf-8"))

                # Execute the specified command
                ret_val = _COMMANDS[cmd_obj["command"]](
                    self, client, *cmd_obj["args"], **cmd_obj["kwargs"])

                # Return the response
                client.send(json.dumps({"return": ret_val}).encode("utf-8") +
                            b"\n")
            except:
                # If any of the above fails for any reason (e.g. invalid JSON,
                # unrecognised command, command crashes, etc.), just disconnect
                # the client.
                logging.exception("Client %s sent bad command %r, "
                                  "disconnecting",
                                  client.getpeername(), line)
                self._disconnect_client(client)
                return

    def _send_change_notifications(self):
        """Send any registered change notifications to clients.

        Sends notifications of the forms ``{"jobs_changed": [job_id, ...]}``
        and ``{"machines_changed": [machine_name, ...]}`` to clients who have
        subscribed to be notified of changes to jobs or machines.
        """
        # Notify clients about jobs which have changed
        changed_jobs = self._controller.changed_jobs
        if changed_jobs:
            for client, jobs in list(iteritems(self._client_job_watches)):
                if jobs is None or not jobs.isdisjoint(changed_jobs):
                    try:
                        client.send(
                            json.dumps(
                                {"jobs_changed":
                                 list(changed_jobs)
                                 if jobs is None else
                                 list(changed_jobs.intersection(jobs))}
                            ).encode("utf-8") + b"\n")
                    except (OSError, IOError):
                        logging.exception(
                            "Could not send notification.")
                        self._disconnect_client(client)

        # Notify clients about machines which have changed
        changed_machines = self._controller.changed_machines
        if changed_machines:
            for client, machines in list(
                    iteritems(self._client_machine_watches)):
                if (machines is None or
                        not machines.isdisjoint(changed_machines)):
                    try:
                        client.send(
                            json.dumps(
                                {"machines_changed":
                                 list(changed_machines)
                                 if machines is None else
                                 list(changed_machines.intersection(machines))}
                            ).encode("utf-8") + b"\n")
                    except (OSError, IOError):
                        logging.exception(
                            "Could not send notification.")
                        self._disconnect_client(client)

    def _run(self):
        """The main server thread.

        This 'infinate' loop runs in a background thread and waits for and
        processes events such as the :py:meth:`._notify` method being called,
        the config file changing, clients sending commands or new clients
        connecting. It also periodically calls destroy_timed_out_jobs on the
        controller.
        """
        logging.info("Server running.")
        while not self._stop:
            # Wait for a connection to get opened/closed, a command to arrive,
            # the config file to change or the timeout to ellapse.
            events = self._poll.poll(
                self._configuration.timeout_check_interval)

            # Cull any jobs which have timed out
            self._controller.destroy_timed_out_jobs()

            for fd, event in events:
                if fd == self._notify_recv.fileno():
                    # _notify was called
                    self._notify_recv.recv(1024)
                elif fd == self._server_socket.fileno():
                    # New client connected
                    self._accept_client()
                elif fd in self._client_sockets:
                    # Incoming data from client
                    self._handle_commands(self._client_sockets[fd])
                elif fd == self._config_inotify.fd:
                    # Config file changed, re-read it
                    time.sleep(0.1)
                    self._config_inotify.read()
                    self._watch_config_file()
                    self._read_config_file()
                else:  # pragma: no cover
                    # Should not get here...
                    assert False

            # Send any job/machine change notifications out
            self._send_change_notifications()

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
        self._notify()
        self._server_thread.join()

        # Stop watching config file
        self._config_inotify.close()

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

    @_command
    def version(self, client):
        """
        Returns
        -------
        str
            The server's version number."""
        return __version__

    @_command
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
        keepalive : float or None
            *Optional.* The maximum number of seconds which may elapse between
            a query on this job before it is automatically destroyed. If None,
            no timeout is used. (Default: 60.0)

        Other Parameters
        ----------------
        machine : str or None
            *Optional.* Specify the name of a machine which this job must be
            executed on. If None, the first suitable machine available will be
            used, according to the tags selected below. Must be None when tags
            are given. (Default: None)
        tags : [str, ...] or None
            *Optional.* The set of tags which any machine running this job must
            have. If None is supplied, only machines with the "default" tag
            will be used. If machine is given, this argument must be None.
            (Default: None)
        min_ratio : float
            The aspect ratio (h/w) which the allocated region must be 'at least
            as square as'. Set to 0.0 for any allowable shape, 1.0 to be
            exactly square. Ignored when allocating single boards or specific
            rectangles of triads.
        max_dead_boards : int or None
            The maximum number of broken or unreachable boards to allow in the
            allocated region. If None, any number of dead boards is permitted,
            as long as the board on the bottom-left corner is alive (Default:
            None).
        max_dead_links : int or None
            The maximum number of broken links allow in the allocated region.
            When require_torus is True this includes wrap-around links,
            otherwise peripheral links are not counted.  If None, any number of
            broken links is allowed. (Default: None).
        require_torus : bool
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
        return self._controller.create_job(*args, **kwargs)

    @_command
    def job_keepalive(self, client, job_id):
        """Reset the keepalive timer for the specified job.

        Note all other job-specific commands implicitly do this.
        """
        self._controller.job_keepalive(job_id)

    @_command
    def get_job_state(self, client, job_id):
        """Poll the state of a running job.

        Returns
        -------
        {"state": state, "power": power
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
                For queued and allocated jobs, gives the Unix time at which the
                job was created (or None otherwise).
        """
        out = self._controller.get_job_state(job_id)._asdict()
        out["state"] = int(out["state"])
        return out

    @_command
    def get_job_machine_info(self, client, job_id):
        """Get the list of Ethernet connections to the allocated machine.

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

    @_command
    def power_on_job_boards(self, client, job_id):
        """Power on (or reset if already on) boards associated with a job.

        Once called, the job will enter the 'power' state until the power state
        change is complete, this may take some time.
        """
        self._controller.power_on_job_boards(job_id)

    @_command
    def power_off_job_boards(self, client, job_id):
        """Power off boards associated with a job.

        Once called, the job will enter the 'power' state until the power state
        change is complete, this may take some time.
        """
        self._controller.power_off_job_boards(job_id)

    @_command
    def destroy_job(self, client, job_id, reason=None):
        """Destroy a job.

        Call when the job is finished, or to terminate it early, this function
        releases any resources consumed by the job and removes it from any
        queues.

        Parameters
        ----------
        reason : str or None
            *Optional.* A human-readable string describing the reason for the
            job's destruction.
        """
        self._controller.destroy_job(job_id, reason)

    @_command
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
        job_id : int or None
            A job ID to be notified of or None if all job state changes should
            be reported.

        See Also
        --------
        no_notify_job : Stop being notified about a job.
        notify_machine : Register to be notified about changes to machines.
        """
        if job_id is None:
            self._client_job_watches[client] = None
        else:
            if client not in self._client_job_watches:
                self._client_job_watches[client] = set([job_id])
            elif self._client_job_watches[client] is not None:
                self._client_job_watches[client].add(job_id)
            else:
                # Client is already notified about all changes, do nothing!
                pass

    @_command
    def no_notify_job(self, client, job_id=None):
        """Stop being notified about a specific job ID.

        Once this command returns, no further notifications for the specified
        ID will be received.

        Parameters
        ----------
        job_id : id or None
            A job ID to no longer be notified of or None to not be notified of
            any jobs. Note that if all job IDs were registered for
            notification, this command only has an effect if the specified
            job_id is None.

        See Also
        --------
        notify_job : Register to be notified about changes to a specific job.
        """
        if client not in self._client_job_watches:
            return

        if job_id is None:
            del self._client_job_watches[client]
        else:
            watches = self._client_job_watches[client]
            if watches is not None:
                watches.discard(job_id)
                if len(watches) == 0:
                    del self._client_job_watches[client]

    @_command
    def notify_machine(self, client, machine_name=None):
        r"""Register to be notified about a specific machine name.

        Once registered, a client will be asynchronously be sent notifications
        of the form ``{"machines_changed": [machine_name, ...]}\n`` enumerating
        machine names which have changed. Notifications are sent when a machine
        changes state, for example when created, change, removed, allocated a
        job or an allocated job is destroyed.

        Parameters
        ----------
        machine_name : machine or None
            A machine name to be notified of or None if all machine state
            changes should be reported.

        See Also
        --------
        no_notify_machine : Stop being notified about a machine.
        notify_job : Register to be notified about changes to jobs.
        """
        if machine_name is None:
            self._client_machine_watches[client] = None
        else:
            if client not in self._client_machine_watches:
                self._client_machine_watches[client] = set([machine_name])
            elif self._client_machine_watches[client] is not None:
                self._client_machine_watches[client].add(machine_name)
            else:
                # Client is already notified about all changes, do nothing!
                pass

    @_command
    def no_notify_machine(self, client, machine_name=None):
        """Unregister to be notified about a specific machine name.

        Once this command returns, no further notifications for the specified
        ID will be received.

        Parameters
        ----------
        machine_name : name or None
            A machine name to no longer be notified of or None to not be
            notified of any machines. Note that if all machines were registered
            for notification, this command only has an effect if the specified
            machine_name is None.

        See Also
        --------
        notify_machine : Register to be notified about changes to a machine.
        """
        if client not in self._client_machine_watches:
            return

        if machine_name is None:
            del self._client_machine_watches[client]
        else:
            watches = self._client_machine_watches[client]
            if watches is not None:
                watches.discard(machine_name)
                if len(watches) == 0:
                    del self._client_machine_watches[client]

    @_command
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

            "start_time" is the time the job was created.

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

    @_command
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

    @_command
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

    @_command
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

    @_command
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

    The server may be cleanly terminated using a keyboard interrupt (e.g.
    ctrl+c).

    Parameters
    ----------
    args : [arg, ...]
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
    args = parser.parse_args(args)

    if not args.quiet:
        logging.basicConfig(level=logging.INFO)

    server = Server(config_filename=args.config,
                    cold_start=args.cold_start)
    try:
        # NB: Originally this loop was replaced with a call to server.join
        # however in Python 2, such blocking calls are not interruptable so we
        # use this rather ugly workaround instead.
        while server.is_alive():  # pragma: no cover
            time.sleep(0.1)
    except KeyboardInterrupt:
        server.stop_and_join()

    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
