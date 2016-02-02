"""Match incoming allocation requests to machines."""

from collections import deque, OrderedDict, namedtuple

from enum import Enum

from six import iteritems, itervalues

from spinn_partition.allocator import Allocator


class JobQueue(object):
    """A mechanism for matching incoming allocations (jobs) to a set of
    available machines.
    """
    
    def __init__(self, on_allocate, on_free, on_cancel):
        """Create a new (empty) job queue with no machines.
        
        Parameters
        ----------
        on_allocate : f(job_id, machine_name, boards, periphery)
            A callback function which is called when a job is successfully
            allocated resources on a machine.
            
            ``job_id`` is the job's unique identifier.
            
            ``machine_name`` is the name of the machine the job was allocated
            on.
            
            ``boards`` is a set([(x, y, z), ...]) enumerating the boards in the
            allocation.
            
            ``periphery`` is a set([(x, y, z, :py:class:`rig.links.Links`),
            ...]) enumerating the links which leave the set of allocated boards.
        
        on_free : f(job_id, reason)
            A callback called when a job which was previously allocated
            resources has had those resources withdrawn or freed them.
            
            ``job_id`` is the job's unique identifier.
            
            ``reason`` is a human readable string explaining why the job was
            freed or None if no reason was given.
        
        on_cancel : f(job_id, reason)
            A callback called when a job which was previously queued is removed
            from the queue. This may be due to a lack of a suitable machine or
            due to user action.
            
            ``job_id`` is the job's unique identifier.
            
            ``reason`` is a human readable string explaining why the job was
            cancelled or None if no reason was given.
        """
        self.on_allocate = on_allocate
        self.on_free = on_free
        self.on_cancel = on_cancel
        
        # The machines onto which jobs may be queued.
        # {name: Machine, ...}
        self._machines = OrderedDict()
        
        # The currently queued or allocated jobs
        # {job_id: Job, ...}
        self._jobs = OrderedDict()
        
        # When non-zero, queues are not changed. Set when using this object as
        # a context manager.
        self._postpone_queue_management = 0
    
    def __enter__(self):
        """This context manager will cause all changes to machines to be made
        atomically, without regenerating queues between each change.
        """
        self._postpone_queue_management += 1
    
    def __exit__(self, type=None, value=None, traceback=None):
        self._postpone_queue_management -= 1
        self._regenerate_queues()
    
    def _try_job(self, job, machine):
        """Attempt to allocate a job on a given machine.
        
        Returns
        -------
        job_allocated : bool
        """
        # Try to allocate and fail loudly if this will never fit
        allocation = machine.allocator.alloc(*job.args, **job.kwargs)
        if allocation is None:
            return False
        
        # Allocation succeeded!
        allocation_id, boards, periphery = allocation
        job.pending = False
        job.machine = machine
        job.allocation_id = allocation_id
        
        # Report this to the user
        self.on_allocate(job.id, machine.name, boards, periphery)
        
        return True
    
    def _enqueue_job(self, job):
        """Either allocate or enqueue a new job."""
        if self._postpone_queue_management:
            return
        
        # Get the list of machines the job would like to be executed on.
        if job.machine_name is not None:
            # A specific machine was given
            machine = self._machines.get(job.machine_name, None)
            machines = [machine] if machine is not None else []
        else:
            # Select machines according to the job's tags
            machines = [m for m in itervalues(self._machines)
                        if job.tags.issubset(m.tags)]
        
        # See if any of the selected machines can allocate the job immediately
        for machine in machines:
            if self._try_job(job, machine):
                # Job started!
                return
        
        # No machine was available for the job, queue it on all machines it is
        # compatible with
        found_machine = False
        for machine in machines:
            if machine.allocator.alloc_possible(*job.args, **job.kwargs):
                machine.queue.append(job)
                found_machine = True
        
        if not found_machine:
            # If no candidate machines were found, the job will never be run,
            # immediately cancel it
            self.destroy_job(job.id, "No suitable machines available.")
    
    def _process_queue(self):
        """Try and process any queued jobs."""
        if self._postpone_queue_management:
            return
        
        # Keep going until no more jobs can be started
        changed = True
        while changed:
            changed = False
            
            # For each machine, attempt to process the current head of their
            # job queue.
            for machine in itervalues(self._machines):
                while machine.queue:
                    if not machine.queue[0].pending:
                        # Skip queued jobs which have been allocated
                        # elsewhere/cancelled
                        machine.queue.popleft()
                        continue
                    elif self._try_job(machine.queue[0], machine):
                        # Try running the job
                        machine.queue.popleft()
                        changed = True
                    
                    break

    def _regenerate_queues(self):
        """If the set of available machines has changed, queued jobs must be
        re-evaluated since they may fit on a different set of machines.
        """
        if self._postpone_queue_management:
            return
        
        # Empty all job queues
        for machine in itervalues(self._machines):
            machine.queue.clear()
        
        # Re-allocate/queue all pending jobs
        for job in itervalues(self._jobs):
            if job.pending:
                self._enqueue_job(job)
    
    def add_machine(self, name, width, height, tags=None,
                    dead_boards=set(), dead_links=set()):
        """Add a new machine for processing jobs.
        
        Jobs are offered for allocation on machines in the order the machines
        are inserted to this list.
        
        Parameters
        ----------
        name : str
            The name which identifies the machine.
        width, height : int
            The dimensions of the machine in triads.
        tags : set([str, ...])
            The set of tags jobs may select to indicate they wish to use this
            machine. Note that the tag default is given to any job whose
            tags are not otherwise specified. Defaults to set(["default"])
        dead_boards : set([(x, y, z), ...])
            The boards in the machine which do not work.
        dead_links : set([(x, y, z, :py:class:`rig.links.Links`), ...])
            The board-to-board links in the machine which do not work.
        """
        if name in self._machines:
            raise ValueError("Machine name {} already in use.".format(name))
        
        allocator = Allocator(width, height, dead_boards, dead_links)
        self._machines[name] = _Machine(name, tags, allocator)
        
        self._regenerate_queues()
    
    def move_machine_to_end(self, name):
        """Move the specified machine to the end of the OrderedDict of
        machines.
        """
        self._machines.move_to_end(name)
        # NB: No queue regeneration required
    
    def modify_machine(self, name, tags=None,
                       dead_boards=None, dead_links=None):
        """Make minor modifications to the description of an existing machine.
        
        Note that any changes made will not impact already allocated jobs but
        may alter queued jobs.
        
        Parameters
        ----------
        name : str
            The name of the machine to change.
        tags : set([str, ...]) or None
            If not None, change the Machine's tags to match the supplied set.
        dead_boards : set([(x, y, z), ...])
            If not None, change the set of dead boards in the machine.
        dead_links : set([(x, y, z, :py:class:`rig.links.Links`), ...])
            If not None, change the set of dead links in the machine.
        """
        machine = self._machines[name]
        
        if tags is not None:
            machine.tags = tags
        
        if dead_boards is not None:
            machine.allocator.dead_boards = dead_boards
        
        if dead_links is not None:
            machine.allocator.dead_links = dead_links
        
        self._regenerate_queues()
    
    def remove_machine(self, name):
        """Remove a machine from the available set.
        
        All jobs allocated on that machine will be freed and then the machine
        will be removed.
        """
        machine = self._machines[name]
        
        # Regenerate the queues only after removing the machine
        with self:
            # Free all jobs allocated on the machine.
            for job in list(itervalues(self._jobs)):
                if job.machine is machine:
                    self.destroy_job(job.id, "Machine removed.")
        
            # Remove the machine from service
            del self._machines[name]
    
    def create_job(self, *args, **kwargs):
        """Attempt to create a new job.
        
        If no machine is immediately available to allocate the job the job is
        placed in the queues of all machines into which it can fit. The first
        machine to be able to allocate the job will get the job. This means
        that identical jobs are handled in a FIFO fashion but jobs which can
        only be executed on certain machines may be 'overtaken' by jobs which
        can run on machines the overtaken job cannot.
        
        Note that during this method call the job may be allocated or cancelled
        and the associated callbacks called. Callers should be prepared for
        this eventuality. If no callbacks are produced the job has been queued.
        
        Parameters
        ----------
        See :py:meth:`spinn_partition.allocator.Allocator.alloc`.
        
        Other Parameters
        ----------------
        job_id
            **Mandatory.** A unique identifier for the job.
        machine : None or "machine"
            If not None, require that the job is executed on the specified
            machine. Not valid when tags are given.
        tags : None or set([tag, ...])
            The set of tags which any machine running this job must have. Not
            valid when machine is given. If None is supplied, only machines
            with the "default" tag will be used (unless machine is
            specified).
        
        Returns
        -------
        job_id : int
            The job created.
        """
        job_id = kwargs.pop("job_id", None)
        machine_name = kwargs.pop("machine", None)
        tags = kwargs.pop("tags", None)
        
        # Sanity check arguments
        if job_id is None:
            raise TypeError("job_id must be specified")
        
        if job_id in self._jobs:
            raise ValueError("job_id {} is not unique".format(job_id))
        
        if machine_name is not None and tags is not None:
            raise TypeError(
                "Only one of machine and tags may be specified for a job.")
        
        # If a specific machine is selected, we must not filter on tags
        if machine_name is not None:
            tags = set()
        
        # Create the job
        job = _Job(id=job_id, pending=True,
                   machine_name=machine_name, tags=tags,
                   args=args, kwargs=kwargs)
        self._jobs[job.id] = job
        self._enqueue_job(job)
    
    def destroy_job(self, job_id, reason=None):
        """Destroy a queued or allocated job.
        
        Parameters
        ----------
        job_id
            The ID of the job to destroy.
        reason : str or None
            *Optional.* A human-readable reason that the job was destroyed.
        """
        job = self._jobs.pop(job_id)
        
        if job.pending:
            # Mark the job as no longer pending to prevent it being processed
            job.pending = False
            self.on_cancel(job.id, reason)
        else:
            # Job was allocated somewhere, deallocate it
            job.machine.allocator.free(job.allocation_id)
            self.on_free(job.id, reason)
        
        self._process_queue()


class _Job(object):
    """Defines a job.
    
    Attributes
    ----------
    id : int
        A unique ID assigned to the job.
    pending : bool
        If True, the job is currently queued for execution, if False the job
        has been allocated.
    machine_name : str or None
        The machine this job must be executed on or None if any machine with
        matching tags is sufficient.
    tags : set([str, ...]) or None
        The set of tags required of any machine the job is to be executed on.
        If None, only machines with the "default" tag will be used.
    args, kwargs : tuple, dict
        The arguments to the alloc function for this job.
    machine : :py:class:`.Machine` or None
        The machine the job has been allocated on.
    allocation_id : int or None
        The allocation ID for the Job's allocation.
    """
    def __init__(self, id,
                 pending=True,
                 machine_name=None,
                 tags=set(),
                 args=tuple(), kwargs={},
                 machine=None,
                 allocation_id=None):
        self.id = id
        self.pending = pending
        self.machine_name = machine_name
        self.tags = tags if tags is not None else set(["default"])
        self.args = args
        self.kwargs = kwargs
        self.machine = machine
        self.allocation_id = allocation_id

    def __hash__(self):
        return hash(id(self))
    
    def __repr__(self):
        return "<{} id={}>".format(self.__class__.__name__, self.id)


class _Machine(object):
    """Defines a machine on which jobs may run.
    
    Attributes
    ----------
    name : str
        The name of the machine.
    tags : set([str, ...])
        The set of tags the machine has. For a job to be allocated on a machine
        all of its tags must also be tags of the machine.
    allocator : :py:class:`spinn_partition.allocator.Allocator`
        An allocator for boards in this machine.
    queue : deque([:py:class:`.Job`, ...])
        A queue for jobs tentatively scheduled for this machine. Note that a
        job may be present in many queues at once. The first machine to accept
        the job is the only one which may process it.
    """
    def __init__(self, name, tags, allocator, queue=None):
        self.name = name
        self.tags = tags if tags is not None else set(["default"])
        self.allocator = allocator
        self.queue = queue if queue is not None else deque()

    def __hash__(self):
        return hash(id(self))
    
    def __repr__(self):
        return "<{} name={}>".format(self.__class__.__name__, self.name)
