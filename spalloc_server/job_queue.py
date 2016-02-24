"""A multi-machine and job queueing and allocation mechanism.
"""

from collections import deque, OrderedDict

from six import itervalues

from spalloc_server.allocator import Allocator


class JobQueue(object):
    """A mechanism for matching incoming allocation requests (jobs) to a set of
    available machines.

    For every :py:class:`._Machine` being managed this object contains a queue
    of outstanding jobs and an :py:class:`spalloc_server.allocator.Allocator`
    which manages allocation of jobs onto that machine. A simplistic scheduling
    mechanism is used (see :py:meth:`._enqueue_job`) which simultaneously
    enqueues all jobs to every candidate machine the job could possibly fit on.
    The first machine to accept the job is allocated the job (all other
    machines which subsequently encounter the job in their queues must skip
    it).

    Though this object is entirely single threaded (and not thread safe!),
    callbacks (:py:attr:`.on_allocate`, :py:attr:`.on_free` and
    :py:attr:`.on_cancel`) are used to indicate when a job is queued, allocated
    or cancelled.

    A new job may be considered 'queued' the moment the :py:meth:`.create_job`
    method is called. At some point in the future, possibly before
    :py:meth:`.create_job` returns, the :py:attr:`.on_allocate` or
    :py:attr:`.on_cancel` callbacks will be called to indicate the job has
    either successfully been allocated to a machine or been cancelled (e.g. due
    to unavailability of a suitable machine or :py:meth:`.destroy_job` being
    called).

    Once the :py:meth:`.on_allocate` callback has been called for a
    job, a job may be considered to be allocated to the specific machine
    indicated in the callback. This remains true until the :py:meth:`.on_free`
    callback is produced (as a result of calling :py:class:`.destroy_job`).
    """

    def __init__(self, on_allocate, on_free, on_cancel):
        """Create a new (empty) job queue with no machines.

        Parameters
        ----------
        on_allocate : f(job_id, machine_name, boards, periphery, torus)
            A callback function which is called when a job is successfully
            allocated resources on a machine.

            job_id : int
                The job's unique identifier.
            machine_name : str
                The name of the machine the job was allocated on.
            boards : set([(x, y, z), ...])
                Enumerates the boards in the allocation.
            periphery : set([(x, y, z, :py:class:`rig.links.Links`), ...])
                Enumerates the links which leave the set of allocated boards.
            torus : :py:class:`spalloc_server.coordinates.WrapAround`
                Specifies which types of wrap-around links may be present.

        on_free : f(job_id, reason)
            A callback called when a job which was previously allocated
            resources has had those resources withdrawn or freed them.

            job_id : int
                The job's unique identifier.
            reason : str or None
                A human readable string explaining why the job was freed or
                None if no reason was given.

        on_cancel : f(job_id, reason)
            A callback called when a job which was previously queued is removed
            from the queue. This may be due to a lack of a suitable machine or
            due to user action.

            job_id : int
                The job's unique identifier.
            reason : str or None
                A human readable string explaining why the job was cancelled or
                None if no reason was given.
        """
        self.on_allocate = on_allocate
        self.on_free = on_free
        self.on_cancel = on_cancel

        # The machines onto which jobs may be queued.
        # {name: _Machine, ...}
        self._machines = OrderedDict()

        # The currently queued or allocated jobs
        # {job_id: _Job, ...}
        self._jobs = OrderedDict()

        # When non-zero, queues are not changed. Set when using this object as
        # a context manager.
        self._postpone_queue_management = 0

    def __enter__(self):
        """This context manager will cause all changes to machines to be made
        atomically, without regenerating queues between each change.

        This is useful when modifying multiple machines at once or destroying
        multiple jobs at once since it prevents jobs being allocated and then
        immediately destroyed.

        Usage example::

            >> q = JobQueue(...)
            >> with q:
            ..     q.remove_machine("m")
            ..     q.add_machine(...)

        .. note::
            This context manager should be used sparingly as it causes all
            job queues to be regenerated from scratch when it exits.
        """
        self._postpone_queue_management += 1

    def __exit__(self, type=None, value=None, traceback=None):
        self._postpone_queue_management -= 1
        self._regenerate_queues()

    def __getstate__(self):
        """Called when pickling this object.

        In Python 2, references to methods cannot be pickled. Since this
        object maintains a number of function pointers as callbacks (which may
        often be methods) we must remove these before pickling. When
        unpickling, these pointers should be recreated externally.
        """
        state = self.__dict__.copy()

        # Do not keep the reference to any callbacks
        state["on_allocate"] = None
        state["on_free"] = None
        state["on_cancel"] = None

        return state

    def _try_job(self, job, machine):
        """Attempt to allocate a job on a given machine.

        Returns
        -------
        job_allocated : bool
            Was the job successfully allocated? If True, the internal metadata
            associated with the job is updated and the :py:attr:`.on_allocate`
            callback is called.
        """
        # Try to allocate the job
        allocation = machine.allocator.alloc(*job.args, **job.kwargs)
        if allocation is None:
            return False

        # Allocation succeeded!
        allocation_id, boards, periphery, torus = allocation
        job.pending = False
        job.machine = machine
        job.allocation_id = allocation_id

        # Report this to the user
        self.on_allocate(job.id, machine.name, boards, periphery, torus)

        return True

    def _enqueue_job(self, job):
        """Either allocate or enqueue a new job.

        Given a new job which is not yet running or enqueued use a simple
        scheduling mechanism to decided what to do with it:

        * Attempt to allocate the job on each machine matching the job's
          requirements in turn.
        * If no machine can allocate the job immediately, add the job to the
          queues of all machines which meet the job's requirements.

        Parameters
        ----------
        job : :py:class:`._Job`
            The job to attempt to enqueue or allocate.
        """
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

        # Queue the job on all suitable machines
        found_machine = False
        for machine in machines:
            if machine.allocator.alloc_possible(*job.args, **job.kwargs):
                machine.queue.append(job)
                found_machine = True

        # If no candidate machines were found, the job will never be run,
        # immediately cancel it.
        if not found_machine:
            self.destroy_job(job.id, "No suitable machines available.")

        # Advance the queues where possible.
        self._process_queue()

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
        """Regenerate all queues to account for any significant changes to the
        machines available.

        This function clears all machine queues and then reinserts the jobs
        using :py:class:`._enqueue_job`, potentially allocating the job to a
        running machine. Since the jobs are re-enqueued in the order they were
        supplied, the queueing priorities are unaffected.
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

        See Also
        --------
        __enter__ : A context manager to allow atomic changes to be made to
                    machines.
        move_machine_to_end : Modify machine priorities
        modify_machine : Modify certain machine parameters without removing and
                         then re-adding it.
        remove_machine : Remove a machine.
        """
        if name in self._machines:
            raise ValueError("Machine name {} already in use.".format(name))

        allocator = Allocator(width, height, dead_boards, dead_links)
        self._machines[name] = _Machine(name, tags, allocator)

        self._regenerate_queues()

    def move_machine_to_end(self, name):
        """Move the specified machine to the end of the OrderedDict of
        machines.

        Parameters
        ----------
        name : str
            The name of the machine to move.
        """
        # Python 2.7 does not have move_to_end
        m = self._machines.pop(name)
        self._machines[name] = m

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

        This function is a thin wrapper around
        :py:meth:`spalloc_server.allocator.Allocator.alloc` and thus
        accepts all arguments it accepts plus those named below.

        Parameters
        ----------
        job_id : int
            **Mandatory.** A unique identifier for the job, supplied by the
            caller.
        machine : None or str
            If not None, require that the job is executed on the specified
            machine. Not valid when tags are given.
        tags : None or set(['tag', ...])
            The set of tags which any machine running this job must have. Not
            valid when machine is given. If None is supplied, only machines
            with the "default" tag will be used (unless machine is specified).
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

        If the job is already allocated, this frees the job resulting in the
        :py:attr:`.on_free` callback being called.  If the job is queued, this
        removes the job from all queues and the :py:attr:`.on_cancel` callback
        being called.

        Parameters
        ----------
        job_id : int
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
    """The internal state representing a job.

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
    machine : :py:class:`._Machine` or None
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

    def __repr__(self):  # pragma: no cover
        return "<{} id={}>".format(self.__class__.__name__, self.id)


class _Machine(object):
    """Internal data which maintains state information about machine on which
    jobs may run.

    Attributes
    ----------
    name : str
        The name of the machine.
    tags : set([str, ...])
        The set of tags the machine has. For a job to be allocated on a machine
        all of its tags must also be tags of the machine.
    allocator : :py:class:`spalloc_server.allocator.Allocator`
        An allocator for boards in this machine.
    queue : deque([:py:class:`._Job`, ...])
        A queue for jobs tentatively scheduled for this machine. Note that a
        job may be present in many queues at once. The first machine to accept
        the job is the only one which may process it.
    """
    def __init__(self, name, tags, allocator, queue=None):
        self.name = name
        self.tags = tags if tags is not None else set(["default"])
        self.allocator = allocator
        self.queue = queue if queue is not None else deque()

    def __repr__(self):  # pragma: no cover
        return "<{} name={}>".format(self.__class__.__name__, self.name)
