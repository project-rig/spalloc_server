"""A high-level control interface for allocating and managing large SpiNNaker
machines.
"""

import threading

from enum import IntEnum

from contextlib import contextmanager

from collections import namedtuple, OrderedDict

from functools import partial

from six import itervalues, iteritems

import time

from spinn_partition.coordinates import board_to_chip
from spinn_partition.job_queue import JobQueue
from spinn_partition.async_bmp_controller import AsyncBMPController


class Controller(object):
    """An object which allocates jobs to machines and manages said machines.
    
    This object is intended to form the core of a server which manages the
    queueing and execution of jobs on several SpiNNaker machines at once. It is
    designed with the intention 
    
    Attributes
    ----------
    max_retired_jobs : int
        Maximum number of retired jobs to retain the state of.
    machines : {name: :py:class:`.Machine`, ...} or similar OrderedDict
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
        """Create a new controller.
        
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
        
        This object may only be pickled once .stop() and .join() have returned.
        """
        state = self.__dict__.copy()
        
        # Do not keep the reference to any state-change callbacks
        state["_on_background_state_change"] = None
        
        # Do not keep references to unpickleable dynamic state
        state["_bmp_controllers"] = None
        state["_lock"] = None
        
        return state
    
    def __setstate__(self, state):
        """Called when unpickling this object."""
        self.__dict__.update(state)
        self._init_dynamic_state()
    
    def stop(self):
        """Request that all background threads stop.
        
        The controller should no longer be used after calling this method.
        
        Use .join to wait for the threads to actually terminate.
        """
        with self._lock:
            # Stop the BMP controllers
            for machine in itervalues(self._machines):
                self._destroy_machine_bmp_controllers(machine)
    
    def join(self):
        """Block until all background threads have halted.
        
        .. warning::
            
            When using this function, no functions of the controller may be
            called between the stop() call and join() may be made.
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
        machines : {name: :py:class:`.Machine`, ...} or similar OrderedDict
            Defines the machines now available to the controller.
        """
        with self._lock:
            before = set(self._machines)
            after = set(machines)
            
            # Match old machines with new ones by name
            added = after - before
            removed = before - after
            changed = before.intersection(after)
            
            # Filter the set of 'changed' machines, ignoring machines which have
            # not changed and marking machines with major changes for re-creation.
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
            
            # Make all changes to the job queue atomically to prevent jobs getting
            # scheduled on machines which then immediately change.
            with self._job_queue:
                # Remove all removed machines, accumulating a list of all the
                # AsyncBMPControllers which have been shut down.
                shut_down_controllers = list()
                for name in removed:
                    # Remove the machine from the queue causing all jobs
                    # allocated on it to be freed and all boards powered down.
                    self._job_queue.remove_machine(name)
                    
                    # Remove the board and its BMP connections
                    old = self._machines.pop(name)
                    self._destroy_machine_bmp_controllers(old)
                    shut_down_controllers.extend(
                        itervalues(self._bmp_controllers.pop(name)))
                
                def wait_for_old_controllers_to_shutdown():
                    # All new BMPControllers must wait for all the old controllers
                    # to shut-down first
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
                    self._machines.move_to_end(name)
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
        
        This function should be called in one of the three following styles::
        
            job_id = c.create_job(owner="me")            # Any single board
            job_id = c.create_job(3, 2, 1, owner="me")   # Board x=3, y=2, z=1
            job_id = c.create_job(4, 5, owner="me")      # Any 4x5 triads
        
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
        with self._lock:
            self.job_keepalive(job_id)
            
            job = self._jobs.get(job_id)
            if job is not None:
                # Job is live
                state = job.state
                keepalive = job.keepalive
                reason = None
            elif job_id in self._retired_jobs:
                # Job has been destroyed at some point
                state = JobState.destroyed
                keepalive = None
                reason = self._retired_jobs[job_id]
            else:
                # Job ID not recognised
                state = JobState.unknown
                keepalive = None
                reason = None
            
            return (state, keepalive, reason)
    
    def get_job_ethernet_connections(self, job_id):
        """Get the list of Ethernet connections to the allocated machine.
        
        Returns
        -------
        connections : {(x, y): hostname, ...} or None
            A dictionary mapping from SpiNNaker Ethernet-connected chip
            coordinates in the machine to hostname.
            
            None if no boards are allocated to the job (e.g. it is still
            queued or has been destroyed).
        machine_name : str or None
            The name of the machine the job is allocated on or None if the job
            is not currently allocated.
        """
        with self._lock:
            self.job_keepalive(job_id)
            
            job = self._jobs.get(job_id, None)
            if job is not None and job.boards is not None:
                machine = job.allocated_machine
                machine_name = machine.name
                
                # Note that the formula used below for converting from board to
                # chip coordinates is only valid when either 'oz' is zero or
                # only a single board is allocated. Since we only allocate
                # multi-board regions by the triad this will be the case.
                ox, oy, oz = min(job.boards)
                connections = {
                    board_to_chip(x-ox, y-oy, z-oz):
                    machine.spinnaker_ips[(x, y, z)]
                    for (x, y, z) in job.boards
                }
            else:
                # Job doesn't exist or no boards allocated yet
                connections = None
                machine_name = None
            
            return (connections, machine_name)
    
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
            
            ``boards`` the set([(x, y, z), ...]) of boards allocated to the job.
            
            This tuple may be extended in future versions.
        """
        with self._lock:
            job_list = []
            for job in itervalues(self._jobs):
                # Strip "job_id" which is only used internally
                kwargs = {k: v for k, v in iteritems(job.kwargs) if k != "job_id"}
                
                # Machine may not exist
                allocated_machine_name = None
                if job.allocated_machine is not None:
                    allocated_machine_name = job.allocated_machine.name
                
                job_list.append((
                    job.id, job.owner, job.start_time, job.keepalive, job.state,
                    job.args, kwargs, allocated_machine_name, job.boards))
            
            return job_list
    
    def list_machines(self):
        """Enumerates all machines known to the system.
        
        Returns
        -------
        machines : [(name, tags, width, height, dead_boards, dead_links), ...]
            The list of machines known to the system in order of priority from
            highest (first) to lowest (last).
            
            ``name`` is the name of the machine.
            
            ``tags`` is the set(['tag', ...]) of tags the machine has.
            
            ``width`` and ``height`` are the dimensions of the machine in
            triads.
            
            ``dead_boards`` is a set([(x, y, z), ...]) giving the coordinates
            of known-dead boards.
            
            ``dead_links`` is a set([(x, y, z, links), ...]) giving the
            locations of known-dead links from the perspective of the sender.
            Links to dead boards may or may not be included in this list.
        """
        with self._lock:
            return [
                (machine.name, machine.tags, machine.width, machine.height,
                 machine.dead_boards, machine.dead_links)
                for machine in itervalues(self._machines)
            ]
    
    def destroy_timed_out_jobs(self):
        """Destroy any jobs which have timed out."""
        with self._lock:
            now = time.time()
            for job in list(itervalues(self._jobs)):
                if (job.keepalive is not None and
                        job.keepalive_until < now):
                    # Job timed out, destroy it
                    self.destroy_job(job.id, "Job timed out.")
    
    @contextmanager
    def _all_bmps_in_machine(self, machine):
        """Act atomically on all BMPs in a machine.
        
        Note: This is only safe when this is the only way AsyncBMPControllers
        are locked.
        """
        controllers = self._bmp_controllers[machine.name]
        for abc in itervalues(controllers):
            abc.__enter__()
        
        try:
            yield controllers
        finally:
            for abc in itervalues(controllers):
                abc.__exit__(None, None, None)
    
    def _bmp_on_request_complete(self, job):
        """Callback function called by an AsyncBMPController when it completes
        a previously issued request.
        
        This function sets the specified Job's state to JobState.ready when
        this function has been called job.bmp_requests_until_ready times.
        
        This function should be passed partially-called with the job the
        callback is associated it.
        
        Parameters
        ----------
        job : :py:class:`._Job`
            The job whose state should be set.
        """
        with self._lock:
            job.bmp_requests_until_ready -= 1
            assert job.bmp_requests_until_ready >= 0
            if job.bmp_requests_until_ready == 0:
                job.state = JobState.ready
                
                # Report state changes for jobs which are stlil running
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
            
            with self._all_bmps_in_machine(machine) as controllers:
                job.state = JobState.power
                self._changed_jobs.add(job.id)
                
                # Send power commands
                job.bmp_requests_until_ready += len(job.boards)
                for xyz in job.boards:
                    c, f, b = machine.board_locations[xyz]
                    controllers[(c, f)].set_power(b, power, on_done)
                
                # Send link state commands
                if link_enable is not None:
                    job.bmp_requests_until_ready += len(job.periphery)
                    for x, y, z, link in job.periphery:
                        c, f, b = machine.board_locations[(x, y, z)]
                        controllers[(c, f)].set_link_enable(b, link,
                                                            link_enable,
                                                            on_done)
    
    def _job_queue_on_allocate(self, job_id, machine_name, boards, periphery):
        """Called when a job is successfully allocated to a machine."""
        with self._lock:
            # Update job metadata
            job = self._jobs[job_id]
            job.allocated_machine = self._machines[machine_name]
            job.boards = boards
            job.periphery = periphery
            self._changed_jobs.add(job.id)
            self._changed_machines.add(machine_name)
            
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
    
    def _destroy_machine_bmp_controllers(self, machine):
        """Begin flushing all BMP controllers for a machine.
        
        Note: does not remove the AsyncBMPControllers from the
        self._bmp_controllers dictionary.
        """
        with self._lock:
            with self._all_bmps_in_machine(machine) as controllers:
                for controller in itervalues(controllers):
                    controller.stop()
    
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
    
    def _del_dynamic_state(self):
        """Flush and shut down all dynamic (non-pickleable) state.
        
        Once this function has been called, no further calls to functions in
        this object are permitted until ``_init_dynamic_state`` is called.
        
        Specifically:
        
        * Flush and remove all BMP controllers
        * Destroy the global controller lock
        """
        self.stop()
        self.join()
        
        # Finally, destroy the lock and all references to dynamic state
        self._bmp_controllers = None
        self._lock = None
    

class JobState(IntEnum):
    
    # The job ID requested was not recognised
    unknown = 0
    
    # The job is waiting in a queue for a suitable machine
    queued = 1
    
    # The boards allocated to the job are currently being powered on or powered
    # off.
    power = 2
    
    # The job has been allocated boards and the boards are not currently
    # powering on or powering off.
    ready = 3
    
    # The job has been destroyed
    destroyed = 4

class _Job(object):
    """The metadata for a job."""
    
    def __init__(self, id, owner,
                 start_time=None,
                 keepalive=60.0,
                 state=JobState.queued,
                 args=tuple(), kwargs={},
                 allocated_machine=None,
                 boards=None,
                 periphery=None,
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
        
        # Arguments for the allocator
        self.args = args
        self.kwargs = kwargs
        
        # The hardware allocated to this job (if any)
        self.allocated_machine = allocated_machine
        self.boards = boards
        self.periphery = periphery
        
        # The number of BMP requests which must complete before this job may
        # return to the ready state.
        self.bmp_requests_until_ready = bmp_requests_until_ready
