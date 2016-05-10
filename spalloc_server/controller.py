"""A high-level control interface for scheduling and allocating jobs and
managing hardware in a collection of SpiNNaker machines.
"""

import threading

from enum import IntEnum

from collections import namedtuple, OrderedDict, defaultdict

from functools import partial

from six import itervalues, iteritems

import time

from rig.geometry import spinn5_chip_coord

from spalloc_server.coordinates import \
    board_to_chip, chip_to_board, triad_dimensions_to_chips, WrapAround
from spalloc_server.job_queue import JobQueue
from spalloc_server.async_bmp_controller import AsyncBMPController


class Controller(object):
    """An object which allocates jobs to machines and manages said machines'
    hardware.

    This object is intended to form the core of a server which manages the
    queueing and execution of jobs on several SpiNNaker machines at once using
    a :py:class:`~spalloc_server.job_queue.JobQueue` and interacts with
    the hardware of said machines using
    :py:class:`~spalloc_server.async_bmp_controller.AsyncBMPController`.

    'Jobs' may be created using the :py:meth:`.create_job` and are allocated a
    unique ID. Jobs are then queued, allocated and destroyed according to
    machine availability and user intervention. The state of a job may be
    queried using methods such as :py:meth:`.get_job_state`. When a job changes
    state it is added to the :py:attr:`.changed_jobs` set. If a job's state is
    changed due to a background process (rather than in response to calling a
    :py:class:`.Controller` method), :py:attr:`.on_background_state_change` is
    called.

    :py:class:`~spalloc_server.job_queue.JobQueue` calls callbacks in
    this object when queued jobs are allocated to machines
    (:py:meth:`._job_queue_on_allocate`), allocations are freed
    (:py:meth:`._job_queue_on_free`) or cancelled without being allocated
    (:py:meth:`._job_queue_on_cancel`). These callback functions implement the
    bulk of the functionality of this object by recording state changes in
    jobs and triggering the sending of power/link commands to SpiNNaker
    machines.

    Machines may be added, modified and removed at any time by modifying the
    :py:attr:`.machines` attribute. If a machine is removed or changes
    significantly, jobs running on the machine are cancelled, otherwise
    existing jobs should continue to execute or be scheduled on any new
    machines as appropriate.

    Finally, once the controller is shut down (and outstanding BMP commands are
    flushed) using :py:meth:`.stop` and :py:meth:`.join` methods, it may be
    :py:mod:`pickled <pickle>` and later unpickled to resume operation of the
    controller from where it left off before it was shut down.

    Users should, at a regular interval call :py:meth:`.destroy_timed_out_jobs`
    in order to destroy any queued or running jobs which have not been kept
    alive recently enough.

    Unless otherwise indicated, all methods are thread safe.

    Attributes
    ----------
    max_retired_jobs : int
        Maximum number of retired jobs to retain the state of.
    machines : {name: \
            :py:class:`~spalloc_server.configuration.Machine`, ...} \
            or similar OrderedDict
        Defines the machines now available to the controller.
    changed_jobs : set([job_id, ...])
        The set of job_ids whose state has changed since the last time this set
        was accessed. Reading this value clears it.
    changed_machines : set([machine_name, ...])
        The set of machine names whose state has changed since the last time
        this set was accessed. Reading this value clears it. For example,
        machines are marked as changed if their tags are changed, if they are
        added or removed or if a job is allocated or freed on them.
    on_background_state_change : function() or None
        A function which is called (from any thread) when any state changes
        occur in a background process and not as a direct result of calling a
        method of the controller.

        The callback function *must not* call any methods of the controller
        object.

        Note that this attribute is not pickled and unpicking a controller sets
        this attribute to None.
    """

    def __init__(self, next_id=1, max_retired_jobs=1200,
                 on_background_state_change=None):
        """
        Parameters
        ----------
        next_id : int
            The next Job ID to assign
        max_retired_jobs : int
            See attribute of same name.
        on_background_state_change : function
            See attribute of same name.
        """
        # The next job ID to assign
        self._next_id = next_id

        self._on_background_state_change = on_background_state_change

        # The job queue which manages the scheduling and
        # allocation of all jobs.
        self._job_queue = JobQueue(self._job_queue_on_allocate,
                                   self._job_queue_on_free,
                                   self._job_queue_on_cancel)

        # The machines available.
        # {name: Machine, ...}
        self._machines = OrderedDict()

        # The jobs which are currently queued or allocated.
        # {id: _Job, ...}
        self._jobs = OrderedDict()

        # Stores the reasons that jobs have been destroyed, e.g. freed or
        # killed. This may be periodically cleared. Up to
        # _max_retired_jobs jobs are retained (after which their
        # entry in this dict is removed).
        # {id: reason, ...}
        self._max_retired_jobs = max_retired_jobs
        self._retired_jobs = OrderedDict()

        # Underlying sets containing changed jobs and machines
        self._changed_jobs = set()
        self._changed_machines = set()

        # All the attributes set below are "dynamic state" and cannot be
        # pickled. They are initialised by calling to _init_dynamic_state and
        # cleared by calling _del_dynamic_state.

        # The lock which must be held when manipulating any internal state
        self._lock = None

        # The connections to BMPs in the system.
        # {machine_name: {(c, f): AsyncBMPController, ...}, ...}
        self._bmp_controllers = None

        self._init_dynamic_state()

    def __getstate__(self):
        """Called when pickling this object.

        This object may only be pickled once :py:meth:`.stop` and
        :py:meth:`.join` have returned.
        """
        state = self.__dict__.copy()

        # Do not keep the reference to any state-change callbacks
        state["_on_background_state_change"] = None

        # Do not keep references to unpickleable dynamic state
        state["_bmp_controllers"] = None
        state["_lock"] = None

        return state

    def __setstate__(self, state):
        """Called when unpickling this object.

        Note that though the object must be pickled when stopped, the unpickled
        object will start running immediately.
        """
        self.__dict__.update(state)

        # Restore callback function pointers in JobQueue (removed by JobQueue
        # when pickling as Python 2.7 cannot reliably pickle method
        # references).
        self._job_queue.on_allocate = self._job_queue_on_allocate
        self._job_queue.on_free = self._job_queue_on_free
        self._job_queue.on_cancel = self._job_queue_on_cancel

        self._init_dynamic_state()

    def stop(self):
        """Request that all background threads stop.

        This will cause all outstanding BMP commands to be flushed.

        .. warning::

            Apart from :py:meth:`.join`, no methods of this controller object
            may be called once this method has been called.

        See Also
        --------
        join: to wait for the threads to actually terminate.
        """
        # Stop the BMP controllers
        for machine in self._machines:
            for controller in itervalues(self._bmp_controllers[machine]):
                controller.stop()

    def join(self):
        """Block until all background threads have halted and all queued BMP
        commands completed.
        """
        # Wait for the BMP controller threads
        for controllers in itervalues(self._bmp_controllers):
            for controller in itervalues(controllers):
                controller.join()

    @property
    def on_background_state_change(self):
        with self._lock:
            return self._on_background_state_change

    @on_background_state_change.setter
    def on_background_state_change(self, value):
        with self._lock:
            self._on_background_state_change = value

    @property
    def max_retired_jobs(self):
        with self._lock:
            return self._max_retired_jobs

    @max_retired_jobs.setter
    def max_retired_jobs(self, value):
        with self._lock:
            self._max_retired_jobs = value
            while len(self._retired_jobs) > self._max_retired_jobs:
                self._retired_jobs.pop(next(iter(self._retired_jobs)))

    @property
    def machines(self):
        with self._lock:
            return self._machines.copy()

    @machines.setter
    def machines(self, machines):
        """Update the set of machines available to the controller.

        Attempt to update the information about available machines without
        destroying jobs where possible. Machines are matched with existing
        machines by name and are only recreated if dimensions or connectivity
        information is altered.

        Note that changing the tags, set of dead boards or set of dead links
        does not destroy any already-allocated jobs but will influence new
        ones.

        This function blocks while any removed machine's BMP controllers
        are shut down. This helps prevent collisions e.g. when renaming a
        machine.

        Parameters
        ----------
        machines : {name: \
                :py:class:`~spalloc_server.configuration.Machine`, \
                ...} or similar OrderedDict
            Defines the machines now available to the controller.
        """
        shut_down_controllers = list()
        with self._lock:
            before = set(self._machines)
            after = set(machines)

            # Match old machines with new ones by name
            added = after - before
            removed = before - after
            changed = before.intersection(after)

            # Filter the set of 'changed' machines, ignoring machines which
            # have not changed and marking machines with major changes for
            # re-creation.
            for name in changed.copy():
                old = self._machines[name]
                new = machines[name]
                if old == new:
                    # Machine has not changed, ignore it
                    changed.remove(name)
                elif (old.name != new.name or  # Not really needed
                      old.width != new.width or
                      old.height != new.height or
                      old.board_locations != new.board_locations or
                      old.bmp_ips != new.bmp_ips or
                      old.spinnaker_ips != new.spinnaker_ips):
                    # Machine has changed in a major way, recreate it
                    changed.remove(name)
                    removed.add(name)
                    added.add(name)

            # Make all changes to the job queue atomically to prevent jobs
            # getting scheduled on machines which then immediately change.
            with self._job_queue:
                # Remove all removed machines, accumulating a list of all the
                # AsyncBMPControllers which have been shut down.
                for name in removed:
                    # Remove the machine from the queue causing all jobs
                    # allocated on it to be freed and all boards powered down.
                    self._job_queue.remove_machine(name)

                    # Remove the board and its BMP connections
                    old = self._machines.pop(name)
                    shut_down_controllers.extend(
                        itervalues(self._bmp_controllers.pop(name)))

                # Shut-down the now defunct controllers
                for controller in shut_down_controllers:
                    controller.stop()

                def wait_for_old_controllers_to_shutdown():
                    # All new BMPControllers must wait for all the old
                    # controllers to shut-down first
                    for controller in shut_down_controllers:
                        controller.join()

                # Update changed machines
                for name in changed:
                    new = machines[name]
                    self._job_queue.modify_machine(name,
                                                   tags=new.tags,
                                                   dead_boards=new.dead_boards,
                                                   dead_links=new.dead_links)
                    self._machines[name] = new

                # Add new machines
                for name in added:
                    new = machines[name]

                    self._machines[name] = new
                    self._create_machine_bmp_controllers(
                        new, wait_for_old_controllers_to_shutdown)
                    self._job_queue.add_machine(name,
                                                width=new.width,
                                                height=new.height,
                                                tags=new.tags,
                                                dead_boards=new.dead_boards,
                                                dead_links=new.dead_links)

                # Re-order machines to match the specification
                for name in machines:
                    # Python 2.7 does not have move_to_end
                    m = self._machines.pop(name)
                    self._machines[name] = m

                    self._job_queue.move_machine_to_end(name)

            # Mark all effected machines as changed
            self._changed_machines.update(added)
            self._changed_machines.update(changed)
            self._changed_machines.update(removed)

    @property
    def changed_jobs(self):
        with self._lock:
            changed_jobs = self._changed_jobs
            self._changed_jobs = set()
            return changed_jobs

    @property
    def changed_machines(self):
        with self._lock:
            changed_machines = self._changed_machines
            self._changed_machines = set()
            return changed_machines

    def create_job(self, *args, **kwargs):
        """Create a new job (i.e. allocation of boards).

        This function is a wrapper around
        :py:meth:`JobQueue.create_job()
        <spalloc_server.job_queue.JobQueue.create_job>` which
        automatically selects (and returns) a new job_id. As such, the
        following *additional* (keyword) arguments are accepted:

        Parameters
        ----------
        owner : str
            **Required.** The name of the owner of this job.
        keepalive : float or None
            *Optional.* The maximum number of seconds which may elapse between
            a query on this job before it is automatically destroyed. If None,
            no timeout is used. (Default: 60.0)

        Returns
        -------
        job_id : int
            The Job ID assigned to the job.
        """
        with self._lock:
            # Extract non-allocator arguments
            owner = kwargs.pop("owner", None)
            if owner is None:
                raise TypeError("owner must be specified for all jobs.")
            keepalive = kwargs.pop("keepalive", 60.0)

            # Generate a job ID
            job_id = self._next_id
            self._next_id += 1

            kwargs["job_id"] = job_id

            # Create job and begin attempting to allocate it
            job = _Job(id=job_id, owner=owner,
                       keepalive=keepalive,
                       args=args, kwargs=kwargs)
            self._jobs[job_id] = job
            self._job_queue.create_job(*args, **kwargs)

            self._changed_jobs.add(job_id)

            return job_id

    def job_keepalive(self, job_id):
        """Reset the keepalive timer for the specified job.

        Note all other job-specific functions implicitly call this method.
        """
        with self._lock:
            job = self._jobs.get(job_id, None)
            if job is not None and job.keepalive is not None:
                job.keepalive_until = time.time() + job.keepalive

    def get_job_state(self, job_id):
        """Poll the state of a running job.

        Returns
        -------
        :py:class:`.JobStateTuple`
        """
        with self._lock:
            self.job_keepalive(job_id)

            job = self._jobs.get(job_id)
            if job is not None:
                # Job is live
                state = job.state
                power = job.power
                keepalive = job.keepalive
                reason = None
                start_time = job.start_time
            elif job_id in self._retired_jobs:
                # Job has been destroyed at some point
                state = JobState.destroyed
                power = None
                keepalive = None
                reason = self._retired_jobs[job_id]
                start_time = None
            else:
                # Job ID not recognised
                state = JobState.unknown
                power = None
                keepalive = None
                reason = None
                start_time = None

            return JobStateTuple(state, power, keepalive, reason, start_time)

    def get_job_machine_info(self, job_id):
        """Get information about the machine the job has been allocated.

        Returns
        -------
        :py:class:`.JobMachineInfoTuple`
        """
        with self._lock:
            self.job_keepalive(job_id)

            job = self._jobs.get(job_id, None)
            if job is not None and job.boards is not None:
                return JobMachineInfoTuple(
                    job.width, job.height,
                    job.connections,
                    job.allocated_machine.name,
                    job.boards)
            else:
                # Job doesn't exist or no boards allocated yet
                return JobMachineInfoTuple(None, None, None, None, None)

    def power_on_job_boards(self, job_id):
        """Power on (or reset if already on) boards associated with a job."""
        with self._lock:
            self.job_keepalive(job_id)

            job = self._jobs.get(job_id)
            if job is not None and job.boards is not None:
                self._set_job_power_and_links(
                    job, power=True, link_enable=False)

    def power_off_job_boards(self, job_id):
        """Power off boards associated with a job."""
        with self._lock:
            self.job_keepalive(job_id)

            job = self._jobs.get(job_id)
            if job is not None and job.boards is not None:
                self._set_job_power_and_links(
                    job, power=False, link_enable=None)

    def destroy_job(self, job_id, reason=None):
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
        with self._lock:
            job = self._jobs.get(job_id, None)
            if job is not None:
                # Free the boards used by the job (the JobQueue will then call
                # _job_queue_on_free which will trigger power-down and removal
                # of the job from self._jobs).
                self._job_queue.destroy_job(job_id, reason)

    def list_jobs(self):
        """Enumerate all current jobs.

        Returns
        -------
        jobs : [:py:class`.JobTuple`, ...]
            A list of allocated/queued jobs in order of creation from oldest
            (first) to newest (last).
        """
        with self._lock:
            job_list = []
            for job in itervalues(self._jobs):
                # Strip "job_id" which is only used internally
                kwargs = {k: v for k, v in iteritems(job.kwargs)
                          if k != "job_id"}

                # Machine may not exist
                allocated_machine_name = None
                if job.allocated_machine is not None:
                    allocated_machine_name = job.allocated_machine.name

                job_list.append(JobTuple(
                    job.id, job.owner, job.start_time, job.keepalive,
                    job.state, job.power, job.args, kwargs,
                    allocated_machine_name, job.boards))

            return job_list

    def list_machines(self):
        """Enumerates all machines known to the system.

        Returns
        -------
        machines : [:py:class:`.MachineTuple`, ...]
            The list of machines known to the system in order of priority from
            highest (first) to lowest (last).
        """
        with self._lock:
            return [
                MachineTuple(machine.name, machine.tags,
                             machine.width, machine.height,
                             machine.dead_boards, machine.dead_links)
                for machine in itervalues(self._machines)
            ]

    def get_board_position(self, machine_name, x, y, z):
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
        with self._lock:
            machine = self._machines.get(machine_name, None)
            if machine is None:
                return None
            else:
                return machine.board_locations.get((x, y, z), None)

    def get_board_at_position(self, machine_name, cabinet, frame, board):
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
        with self._lock:
            machine = self._machines.get(machine_name, None)
            if machine is None:
                return None
            else:
                # NB: Assuming this function is only called very rarely,
                # constructing and maintaining a reverse lookup is not worth
                # the trouble so instead we just search.
                for (x, y, z), (c, f, b) in iteritems(machine.board_locations):
                    if (c, f, b) == (cabinet, frame, board):
                        return (x, y, z)
                else:
                    # No board found
                    return None

    def where_is(self, **kwargs):
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
        with self._lock:
            # Initially, we normalise the input coordinate into:
            #
            #     machine_name, chip_x, chip_y
            #
            # and then convert this back into all the output formats required.
            # At various points, if we encounter a board/job/chip which doesn't
            # exist we'll drop out.

            keywords = set(kwargs)
            if keywords == set("machine x y z".split()):
                # Covert from logical position
                machine_name = kwargs["machine"]
                chip_x, chip_y = board_to_chip(
                    kwargs["x"], kwargs["y"], kwargs["z"])
            elif keywords == set("machine cabinet frame board".split()):
                # Covert from physical position (fail if location does not
                # exist)
                machine_name = kwargs["machine"]
                xyz = self.get_board_at_position(machine_name,
                                                 kwargs["cabinet"],
                                                 kwargs["frame"],
                                                 kwargs["board"])
                if xyz is None:
                    return None
                chip_x, chip_y = board_to_chip(*xyz)
            elif keywords == set("machine chip_x chip_y".split()):
                # Covert from chip location
                machine_name = kwargs["machine"]
                chip_x = kwargs["chip_x"]
                chip_y = kwargs["chip_y"]
            elif keywords == set("job_id chip_x chip_y".split()):
                # Covert from job-relative chip location
                job = self._jobs.get(kwargs["job_id"], None)
                if job is None or job.boards is None:
                    return None
                machine_name = job.allocated_machine.name
                job_x, job_y, job_z = map(min, zip(*job.boards))
                dx, dy = board_to_chip(job_x, job_y, job_z)
                chip_x = kwargs["chip_x"] + dx
                chip_y = kwargs["chip_y"] + dy

                # NB: We double-check later that this coordinate is actually a
                # board within the boards allocated to the job!
            else:
                raise TypeError(
                    "Invalid arguments: {}".format(", ".join(keywords)))

            # Get the actual Machine
            machine = self._machines.get(machine_name, None)
            if machine is None:
                return None

            # Compensate chip coordinates for wrap-around
            chip_w, chip_h = triad_dimensions_to_chips(
                self._machines[machine_name].width,
                self._machines[machine_name].height,
                WrapAround.both)
            chip_x %= chip_w
            chip_y %= chip_h

            # Determine the chip within the board
            # Workaround: spinn5_chip_coord (until at least Rig 0.13.2) returns
            # numpy integer types which are not JSON serialiseable.
            board_chip_x, board_chip_y = map(
                int, spinn5_chip_coord(chip_x, chip_y))

            # Determine the logical board coordinates (and compensate for
            # wrap-around)
            x, y, z = chip_to_board(chip_x, chip_y, chip_w, chip_h)

            # Determine the board's physical location (fail if board does not
            # exist)
            cfb = self.get_board_position(machine_name, x, y, z)
            if cfb is None:
                return None
            cabinet, frame, board = cfb

            # Determine what job is running on that board
            for job_id, job in iteritems(self._jobs):
                # NB: If machine is defined, boards must also be defined.
                if (job.allocated_machine == machine and
                        (x, y, z) in job.boards):
                    # Found the job
                    break
            else:
                # No job is allocated to the board
                job_id = None
                job = None

            # If selected by job, make sure the board found is actually running
            # that job (this won't be the case, e.g. if a user specifies a
            # board within their machine which is actually dead or allocated to
            # a neighbouring job)
            if "job_id" in kwargs and job_id != kwargs["job_id"]:
                return None

            # Determine chip coordinate within job
            if job is not None:
                # Determine the board coordinate within the job
                job_x, job_y, job_z = map(min, zip(*job.boards))
                job_x = x - job_x
                job_y = y - job_y
                job_z = z - job_z

                # Turn that into a chip coordinate and wrap-around according to
                # the boards actually available in the allocated machine
                job_chip_x, job_chip_y = board_to_chip(job_x, job_y, job_z)
                job_chip = ((job_chip_x + board_chip_x) % job.width,
                            (job_chip_y + board_chip_y) % job.height)
            else:
                job_chip = None

            return {
                "machine": machine_name,
                "logical": (x, y, z),
                "physical": (cabinet, frame, board),
                "chip": (chip_x, chip_y),
                "board_chip": (board_chip_x, board_chip_y),
                "job_id": job_id,
                "job_chip": job_chip,
            }

    def destroy_timed_out_jobs(self):
        """Destroy any jobs which have timed out."""
        with self._lock:
            now = time.time()
            for job in list(itervalues(self._jobs)):
                if (job.keepalive is not None and
                        job.keepalive_until < now):
                    # Job timed out, destroy it
                    self.destroy_job(job.id, "Job timed out.")

    def _bmp_on_request_complete(self, job, success):
        """Callback function called by an AsyncBMPController when it completes
        a previously issued request.

        This function sets the specified Job's state to JobState.ready when
        this function has been called job.bmp_requests_until_ready times.

        This function should be passed partially-called with the job the
        callback is associated it.

        Parameters
        ----------
        job : :py:class:`._Job`
            The job whose state should be set. (To be defined by wrapping this
            method in a partial).
        success : bool
            Command success indicator provided by the AsyncBMPController.
        """
        with self._lock:
            # If a BMP command failed, cancel the job
            if not success:
                self.destroy_job(
                    job.id,
                    "Machine configuration failed, please try again later.")

            # Count down the number of outstanding requests before the job is
            # ready
            job.bmp_requests_until_ready -= 1
            assert job.bmp_requests_until_ready >= 0
            if job.bmp_requests_until_ready == 0:
                job.state = JobState.ready

                # Report state changes for jobs which are still running
                if job.id in self._jobs:
                    self._changed_jobs.add(job.id)
                    if self._on_background_state_change is not None:
                        self._on_background_state_change()

    def _set_job_power_and_links(self, job, power, link_enable=None):
        """Power on/off and configure links for the boards associated with a
        specific job.

        Parameters
        ----------
        job : :py:class:`._Job`
            The job whose boards should be controlled.
        power : bool
            The power state to apply to the boards. True = on, False = off.
        link_enable : bool or None
            Whether to enable (True) or disable (False) peripheral links or
            leave them unchanged (None).
        """
        with self._lock:
            machine = job.allocated_machine

            on_done = partial(self._bmp_on_request_complete, job)

            # Group commands by the frame they interact with to allow all
            # commands within a frame to be sent atomically
            frame_commands = defaultdict(list)

            controllers = self._bmp_controllers[machine.name]

            # Power commands
            job.bmp_requests_until_ready += len(job.boards)
            for xyz in job.boards:
                c, f, b = machine.board_locations[xyz]
                controller = controllers[(c, f)]
                frame_commands[controller].append(
                    partial(controller.set_power, b, power, on_done))

            # Link state commands
            if link_enable is not None:
                job.bmp_requests_until_ready += len(job.periphery)
                for x, y, z, link in job.periphery:
                    c, f, b = machine.board_locations[(x, y, z)]
                    controller = controllers[(c, f)]
                    frame_commands[controller].append(
                        partial(controller.set_link_enable,
                                b, link, link_enable, on_done))

            # Send power/link commands atomically for each frame
            for controller, commands in iteritems(frame_commands):
                with controller:
                    for command in commands:
                        command()

            # Update job state
            job.state = JobState.power
            job.power = power
            self._changed_jobs.add(job.id)

    def _job_queue_on_allocate(self, job_id, machine_name, boards,
                               periphery, torus):
        """Called when a job is successfully allocated to a machine."""
        with self._lock:
            # Update job metadata
            job = self._jobs[job_id]
            job.allocated_machine = self._machines[machine_name]
            job.boards = boards
            job.periphery = periphery
            job.torus = torus
            self._changed_jobs.add(job.id)
            self._changed_machines.add(machine_name)

            # Compute dimensions of machine the job will run on. Note that the
            # formulae used below for converting from board to chip coordinates
            # is only valid when either 'oz' is zero or only a single board is
            # allocated. Since we only allocate multi-board regions by the
            # triad this will be the case.
            ox, oy, oz = min(job.boards)  # Origin
            bx, by, bz = max(job.boards)  # Top-right bound

            # Get system bounds in chips
            if len(job.boards) > 1:
                job.width, job.height = triad_dimensions_to_chips((bx-ox) + 1,
                                                                  (by-oy) + 1,
                                                                  job.torus)
            else:
                # Special case: single board allocations are always 8x8
                job.width = job.height = 8

            # Get SpiNNaker chip Ethernet IPs (enumerated in terms of chip
            # coordinates)
            job.connections = {
                board_to_chip(x-ox, y-oy, z-oz):
                job.allocated_machine.spinnaker_ips[(x, y, z)]
                for (x, y, z) in job.boards
            }

            # Initialise the boards
            self.power_on_job_boards(job_id)

    def _job_queue_on_free(self, job_id, reason):
        """Called when a job is freed."""
        self._changed_machines.add(self._jobs[job_id].allocated_machine.name)
        self._teardown_job(job_id, reason)

    def _job_queue_on_cancel(self, job_id, reason):
        """Called when a job is cancelled before having been allocated."""
        self._teardown_job(job_id, "Cancelled: {}".format(reason or ""))

    def _teardown_job(self, job_id, reason):
        """Called once job has been removed from the JobQueue.

        Powers down any hardware in use and finally removes the job from _jobs.
        """
        with self._lock:
            job = self._jobs.pop(job_id)
            self._retired_jobs[job_id] = reason
            self._changed_jobs.add(job.id)

            # Keep the number of retired jobs limited to prevent
            # accumulating memory consumption forever.
            if len(self._retired_jobs) > self._max_retired_jobs:
                self._retired_jobs.pop(next(iter(self._retired_jobs)))

            # Power-down any boards that were in use
            if job.boards is not None:
                self._set_job_power_and_links(job, power=False)

    def _create_machine_bmp_controllers(self, machine, on_thread_start=None):
        """Create BMP controllers for a machine."""
        with self._lock:
            controllers = {}
            for (c, f), hostname in iteritems(machine.bmp_ips):
                controllers[(c, f)] = AsyncBMPController(
                    hostname, on_thread_start)
            self._bmp_controllers[machine.name] = controllers

    def _init_dynamic_state(self):
        """Initialise all dynamic (non-pickleable) state.

        Specifically:

        * Creates the global controller lock
        * Creates connections to BMPs.
        * Reset keepalive_until on all existing jobs (e.g. allowing remote
          devices a chance to reconnect before terminating their jobs).
        """
        # Recreate the lock
        assert self._lock is None
        self._lock = threading.RLock()

        with self._lock:
            # Create connections to BMPs
            assert self._bmp_controllers is None
            self._bmp_controllers = {}
            for machine in itervalues(self._machines):
                self._create_machine_bmp_controllers(machine)

            # Reset keepalives to allow remote clients time to reconnect
            for job_id in self._jobs:
                self.job_keepalive(job_id)


class JobState(IntEnum):
    """All the possible states that a job may be in."""

    unknown = 0
    """The job ID requested was not recognised"""

    queued = 1
    """The job is waiting in a queue for a suitable machine"""

    power = 2
    """The boards allocated to the job are currently being powered on or
    powered off.
    """

    ready = 3
    """The job has been allocated boards and the boards are not currently
    powering on or powering off.
    """

    destroyed = 4
    """The job has been destroyed"""


class JobStateTuple(namedtuple("JobStateTuple",
                               "state,power,keepalive,reason,start_time")):
    """Tuple describing the state of a particular job, returned by
    :py:meth:`.Controller.get_job_state`.

    Parameters
    ----------
    state : :py:class:`.JobState`
        The current state of the queried job.
    power : bool or None
        If job is in the ready or power states, indicates whether the boards
        are power{ed,ing} on (True), or power{ed,ing} off (False). In other
        states, this value is None.
    keepalive : float or None
        The Job's keepalive value: the number of seconds between queries
        about the job before it is automatically destroyed. None if no
        timeout is active (or when the job has been destroyed).
    reason : str or None
        If the job has been destroyed, this may be a string describing the
        reason the job was terminated.
    start_time : float or None
        The Unix time at which the job was created.
    """

    # Python 3.4 Workaround: https://bugs.python.org/issue24931
    __slots__ = tuple()


class JobMachineInfoTuple(namedtuple("JobMachineInfoTuple",
                                     "width,height,connections,"
                                     "machine_name,boards")):
    """Tuple describing the machine alloated to a job, returned by
    :py:meth:`.Controller.get_job_machine_info`.

    Parameters
    ----------
    width, height : int or None
        The dimensions of the machine in *chips* or None if no machine
        allocated.
    connections : {(x, y): hostname, ...} or None
        A dictionary mapping from SpiNNaker Ethernet-connected chip coordinates
        in the machine to hostname or None if no machine allocated.
    machine_name : str or None
        The name of the machine the job is allocated on or None if no machine
        allocated.
    boards : set([(x, y, z), ...]) or None
        The boards allocated to the job.
    """

    # Python 3.4 Workaround: https://bugs.python.org/issue24931
    __slots__ = tuple()


class JobTuple(namedtuple("JobTuple",
                          "job_id,owner,start_time,keepalive,state,power,"
                          "args,kwargs,allocated_machine_name,boards")):
    """Tuple describing a job in the list of jobs returned by
    :py:meth:`.Controller.list_jobs`.

    Parameters
    ----------
    job_id : int
        The ID of the job.
    owner : str
        The string giving the name of the Job's owner.
    start_time : float
        The time the job was created (Unix time)
    keepalive : float or None
        The maximum time allowed between queries for this job before it is
        automatically destroyed (or None if the job can remain allocated
        indefinitely).
    machine : str or None
        The name of the machine the job was specified to run on (or None if not
        specified).
    state : :py:class:`.JobState`
        The current state of the job.
    power : bool or None
        If job is in the ready or power states, indicates whether the boards
        are power{ed,ing} on (True), or power{ed,ing} off (False). In other
        states, this value is None.
    args, kwargs
        The arguments to the alloc function which specifies the type/size of
        allocation requested and the restrictions on dead boards, links and
        torus connectivity.
    allocated_machine_name : str or None
        The name of the machine the job has been allocated to run on (or None
        if not allocated yet).
    boards : set([(x, y, z), ...])
        The boards allocated to the job.
    """

    # Python 3.4 Workaround: https://bugs.python.org/issue24931
    __slots__ = tuple()


class MachineTuple(namedtuple("MachineTuple",
                              "name,tags,width,height,"
                              "dead_boards,dead_links")):
    """Tuple describing a machine in the list of machines returned by
    :py:meth:`.Controller.list_machines`.

    Parameters
    ----------
    name : str
        The name of the machine.
    tags : set(['tag', ...])
        The tags the machine has.
    width, height : int
        The dimensions of the machine in triads.
    dead_boards : set([(x, y, z), ...])
        The coordinates of known-dead boards.
    dead_links : set([(x, y, z, :py:class:`rig.links.Links`), ...])
        The locations of known-dead links from the perspective of the sender.
        Links to dead boards may or may not be included in this list.
    """

    # Python 3.4 Workaround: https://bugs.python.org/issue24931
    __slots__ = tuple()


class _Job(object):
    """The metadata, used internally, associated with a non-destroyed job.

    Attributes
    ----------
    id : int
        The ID of the job.
    owner : str
        The job's owner.
    start_time : float
        The time the job was created (Unix time)
    keepalive : float or None
        The maximum time allowed between queries for this job before it is
        automatically destroyed (or None if the job can remain allocated
        indefinitely).
    keepalive_until : float or None
        The time at which this job will become timed out (or None if no
        timeout required).
    state : :py:class:`.JobState`
        The current state of the job.
    power : bool or None
        If job is in the ready or power states, indicates whether the boards
        are power{ed,ing} on (True), or power{ed,ing} off (False). In other
        states, this value is None.
    args, kwargs
        The arguments to the alloc function which specifies the type/size of
        allocation requested and the restrictions on dead boards, links and
        torus connectivity.
    allocated_machine : \
            :py:class:`spalloc_server.configuration.Machine` or None
        The machine the job has been allocated to run on (or None if not
        allocated yet).
    boards : set([(x, y, z), ...]) or None
        The boards allocated to the job or None if not allocated.
    periphery : set([(x, y, z, :py:class:`rig.links.Links`), ...]) or None
        The links around the periphery of the job or None if not allocated.
    torus : :py:class:`spalloc_server.coordinates.WrapAround` or None
        Does the allocated set of boards have wrap-around links? None if
        not allocated.
    width, height : int or None
        The dimensions of the SpiNNaker network in the allocated boards or None
        if not allocated any boards.
    connections : {(x, y): hostname, ...} or None
        If boards are allocated, gives the mapping from chip coordinate to
        Ethernet connection hostname.
    bmp_requests_until_ready : int
        A counter incremented whenever a BMP command is started and
        decremented when the command completes. When this counter reaches
        zero, the user sets the state of the job to
        :py:class:`.JobState.ready`.
    """

    def __init__(self, id, owner,
                 start_time=None,
                 keepalive=60.0,
                 state=JobState.queued,
                 power=None,
                 args=tuple(), kwargs={},
                 allocated_machine=None,
                 boards=None,
                 periphery=None,
                 torus=None,
                 width=None,
                 height=None,
                 connections=None,
                 bmp_requests_until_ready=0):

        self.id = id

        self.owner = owner
        self.start_time = start_time if start_time is not None else time.time()

        # If None, never kill this job due to inactivity. Otherwise, stop the
        # job if the time exceeds this value. It is the allocator's
        # responsibility to update this periodically.
        self.keepalive = keepalive
        if self.keepalive is not None:
            self.keepalive_until = time.time() + self.keepalive
        else:
            self.keepalive_until = None

        # The current life-cycle state of the job
        self.state = state

        # False
        self.power = power

        # Arguments for the allocator
        self.args = args
        self.kwargs = kwargs

        # The hardware allocated to this job (if any)
        self.allocated_machine = allocated_machine
        self.boards = boards
        self.periphery = periphery
        self.torus = torus
        self.width = width
        self.height = height

        # IP address lookup for allocated boards
        self.connections = connections

        # The number of BMP requests which must complete before this job may
        # return to the ready state.
        self.bmp_requests_until_ready = bmp_requests_until_ready
