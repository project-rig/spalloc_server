"""A TCP server which exposes a public interface for allocating boards.
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

from rig.links import Links

from six import itervalues, iteritems

from spinn_partition import __version__
from spinn_partition.configuration import Configuration
from spinn_partition.machine import Machine
from spinn_partition.controller import Controller


class Server(object):
    
    def __init__(self, config_filename="spinn_partition.cfg", cold_start=False):
        """Create a TCP server.
        
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
            ".{}.state".format(os.path.basename(self._config_filename))
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
                except (OSError, IOError):
                    logging.exception(
                        "Could not read saved server state file %s.",
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
        
        # Read configuration file, fail if this fails first time around
        if not self._read_config_file():
            raise Exception("Config file could not be loaded.")
        
        # Set up inotify watcher for config file changes
        self._config_inotify = INotify()
        self._poll.register(self._config_inotify.fd, select.POLLIN)
        self._watch_config_file()
        
        # Start the server
        self._server_thread.start()
    
    
    def _watch_config_file(self):
        """Create a watch on the config file."""
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
        retained unchanged.
        
        Returns True if reading succeded.
        """
        try:
            with open(self._config_filename, "r") as f:
                config_script = f.read()
        except (IOError, OSError):
            logging.exception("Could not read "
                              "config file %s", self._config_filename)
            return False
        
        # The environment in which the configuration script is exexcuted (and
        # where the script will store its options.)
        try:
            g = {"Configuration": Configuration,
                 "Machine": Machine,
                 "Links": Links}
            exec(config_script, g)
        except:
            # Executing the config file failed, don't update any settings
            logging.exception("Error while evaluating "
                              "config file %s", self._config_filename)
            return False
        
        # Make sure a configuration object is specified
        configuration = g.get("configuration", None)
        if not isinstance(configuration, Configuration):
            logging.error("'configuration' must be a Configuration object "
                          "in config file %s", self._config_filename)
            return False
        
        # Update the configuration
        old = self._configuration
        new = configuration
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
        """Close all connections."""
        if self._server_socket is not None:
            self._poll.unregister(self._server_socket)
            self._server_socket.close()
        for client_socket in list(itervalues(self._client_sockets)):
            self._disconnect_client(client_socket)
    
    def _disconnect_client(self, client):
        """Disconnect a client."""
        logging.info("Client %s disconnected.", client.getpeername())
        
        del self._client_sockets[client.fileno()]
        del self._client_buffers[client]
        self._client_job_watches.pop(client, None)
        self._client_machine_watches.pop(client, None)
        
        self._poll.unregister(client)
        client.close()
    
    def _accept_client(self):
        """Accept a new client."""
        client, addr = self._server_socket.accept()
        logging.info("New client connected from %s", addr)
        
        self._poll.register(client, select.POLLIN)
        self._client_sockets[client.fileno()] = client
        self._client_buffers[client] = b""
    
    def _handle_message(self, client):
        """Handle an incomming message from a client."""
        data = client.recv(1024)
        
        # Did the client disconnect?
        if len(data) == 0:
            self._disconnect_client(client)
            return
        
        # Have we received a complete command?
        self._client_buffers[client] += data
        while b"\n" in self._client_buffers[client]:
            # Handle all commands...
            line, _, self._client_buffers[client] = \
                self._client_buffers[client].partition(b"\n")
            
            try:
                cmd_obj = json.loads(line.decode("utf-8"))
                
                # Execute the command
                ret_val = self.COMMAND_HANDLERS[cmd_obj["command"]](
                    self, client, *cmd_obj["args"], **cmd_obj["kwargs"])
                
                # Return the response
                client.send(json.dumps({"return": ret_val}).encode("utf-8") +
                            b"\n")
            except:
                logging.exception("Client %s sent bad command %r, disconnecting",
                                  client.getpeername(), line)
                self._disconnect_client(client)
                return

    def _send_change_notifications(self):
        """Send any registered change notifications to clients."""
        # Notify on jobs changed
        changed_jobs = self._controller.changed_jobs
        if changed_jobs:
            for client, jobs in iteritems(self._client_job_watches):
                if jobs is None or not jobs.isdisjoint(changed_jobs):
                    client.send(
                        json.dumps(
                            {"jobs_changed":
                             list(changed_jobs)
                             if jobs is None else
                             list(changed_jobs.intersection(jobs))}
                        ).encode("utf-8") + b"\n")
        
        # Notify on machines changed
        changed_machines = self._controller.changed_machines
        if changed_machines:
            for client, machines in iteritems(self._client_machine_watches):
                if machines is None or not machines.isdisjoint(changed_machines):
                    client.send(
                        json.dumps(
                            {"machines_changed":
                             list(changed_machines)
                             if machines is None else
                             list(changed_machines.intersection(machines))}
                        ).encode("utf-8") + b"\n")

    def _notify(self):
        """Notify the background thread that something has happened."""
        self._notify_send.send(b"x")

    def _run(self):
        """The main server thread."""
        logging.info("Server running.")
        while not self._stop:
            # Wait for a connection to get opened/closed, a command to arrive,
            # or 
            events = self._poll.poll(
                self._configuration.timeout_check_interval)
            
            # Handle all network events
            for fd, event in events:
                if fd == self._server_socket.fileno():
                    # New client connected
                    self._accept_client()
                elif fd == self._notify_recv.fileno():
                    # Event receieved
                    self._notify_recv.recv(1024)
                elif fd in self._client_sockets:
                    # Incoming message from client
                    self._handle_message(self._client_sockets[fd])
                elif fd == self._config_inotify.fd:
                    # Config file changed
                    time.sleep(0.1)
                    self._config_inotify.read()
                    self._watch_config_file()
                    self._read_config_file()
            
            # Check for timeouts
            self._controller.destroy_timed_out_jobs()
            
            # Send pending change notifications
            self._send_change_notifications()
    
    
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
        
        # Dump controller state to file
        logging.info("Waiting for all queued BMP commands...")
        with open(self._state_filename, "wb") as f:
            pickle.dump(self._controller, f)
        self._controller.stop()
        self._controller.join()
        
        logging.info("Server shut down.")
    
    
    def version(self, client):
        """Return server version number."""
        return __version__
    
    def notify_job(self, client, job_id=None):
        """Register to be notified about a specific job ID.
        
        Parameters
        ----------
        job_id : id or None
            A job IDs to be notified of or None if all job state changes should
            be reported.
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
    
    def no_notify_job(self, client, job_id=None):
        """Unregister to be notified about a specific job ID.
        
        Parameters
        ----------
        job_id : id or None
            A job IDs to nolonger be notified of or None to not be notified of
            any jobs.
        """
        if job_id is None:
            del self._client_job_watches[client]
        elif client in self._client_job_watches:
            watches = self._client_job_watches[client]
            if watches is not None:
                watches.discard(job_id)
                if len(watches) == 0:
                    del self._client_job_watches[client]
    
    def notify_machine(self, client, machine_name=None):
        """Register to be notified about a specific machine name.
        
        Parameters
        ----------
        machine_name : machine or None
            A machine name to be notified of or None if all machine state
            changes should be reported.
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
    
    def no_notify_machine(self, client, machine_name=None):
        """Unregister to be notified about a specific machine name.
        
        Parameters
        ----------
        machine_name : name or None
            A machine name to nolonger be notified of or None to not be notified of
            any machines.
        """
        if machine_name is None:
            del self._client_machine_watches[client]
        elif client in self._client_machine_watches:
            watches = self._client_machine_watches[client]
            if watches is not None:
                watches.discard(machine_name)
                if len(watches) == 0:
                    del self._client_machine_watches[client]
    
    def create_job(self, client, *args, **kwargs):
        """Create a new job (i.e. allocation of boards).
        
        This function should be called in one of the three following styles::
        
            job_id = create_job(owner="me")            # Any single board
            job_id = create_job(3, 2, 1, owner="me")   # Board x=3, y=2, z=1
            job_id = create_job(4, 5, owner="me")      # Any 4x5 triads
        
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
        tags : set([str, ...]) or None
            *Optional.* The set of tags which any machine running this job must
            have. If None is supplied, only machines with the "default" tag
            will be used. If machine is given, this argument must be None.
            (Default: None)
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
        return self._controller.create_job(*args, **kwargs)
    
    def job_keepalive(self, client, job_id):
        """Reset the keepalive timer for the specified job.
        
        Note all other job-specific commands implicitly do this.
        """
        self._controller.job_keepalive(job_id)
    
    def get_job_state(self, client, job_id):
        """Poll the state of a running job.
        
        Returns
        -------
        state : :py:class:`.JobState`
            The current state of the queried job.
        keepalive : float or None
            The Job's keepalive value: the number of seconds between queries
            about the job before it is automatically destroyed. None if no
            timeout is active (or when the job has been destroyed).
        reason : str or None
            If the job has been destroyed, this may be a string describing the
            reason the job was terminated.
        """
        return self._controller.get_job_state(job_id)
    
    def get_job_ethernet_connections(self, client, job_id):
        """Get the list of Ethernet connections to the allocated machine.
        
        Returns
        -------
        connections : [((x, y), hostname), ...] or None
            A list giving Ethernet-connected chip coordinates in the machine to
            hostname.
            
            None if no boards are allocated to the job (e.g. it is still
            queued or has been destroyed).
        machine_name : str or None
            The name of the machine the job is allocated on or None if the job
            is not currently allocated.
        """
        connections, machine_name = \
            self._controller.get_job_ethernet_connections(job_id)
        
        if connections is not None:
            connections = list(iteritems(connections))
        
        return connections, machine_name
    
    def power_on_job_boards(self, client, job_id):
        """Power on (or reset if already on) boards associated with a job."""
        self._controller.power_on_job_boards(job_id)
    
    def power_off_job_boards(self, client, job_id):
        """Power off boards associated with a job."""
        self._controller.power_off_job_boards(job_id)
    
    def destroy_job(self, client, job_id, reason=None):
        """Destroy a job.
        
        When the job is finished, or to terminate it early, this function
        releases any resources consumed by the job and removes it from any
        queues.
        
        Parameters
        ----------
        reason : str or None
            *Optional.* A human-readable string describing the reason for the
            job's destruction.
        """
        self._controller.destroy_job(job_id, reason)
    
    def list_jobs(self, client):
        """Enumerate all current jobs.
        
        Returns
        -------
        jobs : [(job_id, owner, start_time, keepalive, state, \
                 args, kwargs, allocated_machine_name, boards), ...]
            A list of allocated/queued jobs in order of creation from oldest
            (first) to newest (last).
            
            ``job_id`` is the ID of the job.
            
            ``owner`` is the string giving the name of the Job's owner.
            
            ``start_time`` is the time the job was created.
            
            ``keepalive`` is the maximum time allowed between queries for this
            job before it is automatically destroyed (or None if the job can
            remain allocated indefinitely).
            
            ``machine`` is the name of the machine the job was specified to run
            on (or None if not specified).
            
            ``tags`` is the set of tags the job was specified to require
            (value unspecified if machine is not None).
            
            ``state`` is the current :py:class:`.JobState` of the job.
            
            ``args`` and ``kwargs`` are the arguments to the alloc function
            which specifies the type/size of allocation requested and the
            restrictions on dead boards, links and torus connectivity.
            
            ``allocated_machine_name`` is the name of the machine the job has
            been allocated to run on (or None if not allocated yet).
            
            ``boards`` is a list [(x, y, z), ...] of boards allocated to the job.
            
            This tuple may be extended in future versions.
        """
        return [
            [job_id, owner, start_time, keepalive, state,
             args, kwargs, allocated_machine_name,
             list(boards) if boards is not None else None]
            for job_id, owner, start_time, keepalive, state,
                args, kwargs, allocated_machine_name, boards
            in self._controller.list_jobs()
        ]
    
    def list_machines(self, client):
        """Enumerates all machines known to the system.
        
        Returns
        -------
        machines : [(name, tags, width, height, dead_boards, dead_links), ...]
            The list of machines known to the system in order of priority from
            highest (first) to lowest (last).
            
            ``name`` is the name of the machine.
            
            ``tags`` is the list ['tag', ...] of tags the machine has.
            
            ``width`` and ``height`` are the dimensions of the machine in
            triads.
            
            ``dead_boards`` is a list([(x, y, z), ...]) giving the coordinates
            of known-dead boards.
            
            ``dead_links`` is a list([(x, y, z, link), ...]) giving the
            locations of known-dead links from the perspective of the sender.
            Links to dead boards may or may not be included in this list.
        """
        return [
            [name, list(tags), width, height, list(dead_boards), list(dead_links)]
            for name, tags, width, height, dead_boards, dead_links
            in self._controller.list_machines()
        ]
    
    """A dictionary from command names to (unbound) methods of this class."""
    COMMAND_HANDLERS = {f.__name__: f for f in (version,
                                                notify_job,
                                                no_notify_job,
                                                notify_machine,
                                                no_notify_machine,
                                                create_job,
                                                job_keepalive,
                                                get_job_state,
                                                get_job_ethernet_connections,
                                                power_on_job_boards,
                                                power_off_job_boards,
                                                destroy_job,
                                                list_jobs,
                                                list_machines)}


def main(args=None):
    parser = argparse.ArgumentParser(
        description="Start a SpiNNaker machine partitioning server.")
    parser.add_argument("--version", "-V", action="version",
                        version="%(prog)s {}".format(__version__))
    parser.add_argument("--verbose", "-v", action="count", default=0,
                        help="increase verbosity")
    parser.add_argument("config", type=str,
                        help="configuration filename to use for the server.")
    parser.add_argument("--cold-start", "-c", action="store_true",
                        default=False,
                        help="force a cold start, erasing any existing "
                             "saved state")
    args = parser.parse_args(args)
    
    if args.verbose >= 3:
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose >= 2:
        logging.basicConfig(level=logging.INFO)
    elif args.verbose >= 1:
        logging.basicConfig(level=logging.WARNING)
    
    server = Server(config_filename=args.config,
                    cold_start=args.cold_start)
    try:
        server.join()
    except KeyboardInterrupt:
        server.stop_and_join()
    
    return 0


if __name__=="__main__":  # pragma: no cover
    import sys
    sys.exit(main())
