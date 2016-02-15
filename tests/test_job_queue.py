import pytest

from mock import Mock

from rig.links import Links

from spalloc_server.coordinates import board_down_link
from spalloc_server.job_queue import JobQueue


@pytest.fixture
def on_allocate():
    return Mock()


@pytest.fixture
def on_free():
    return Mock()


@pytest.fixture
def on_cancel():
    return Mock()


@pytest.fixture
def q(on_allocate, on_free, on_cancel):
    # A job queue with some mock callbacks
    return JobQueue(on_allocate, on_free, on_cancel)


@pytest.fixture
def m(q):
    # A 3x4 machine called "m", added to the job queue
    q.add_machine("m", 3, 4, tags=set(["default", "pie", "chips"]))
    return "m"


def test_add_machine(q, monkeypatch):
    _regenerate_queues = Mock(side_effect=q._regenerate_queues)
    monkeypatch.setattr(q, "_regenerate_queues", _regenerate_queues)

    # Make sure the add_machine function creates the correct internal state
    dead_boards = set([(3, 2, 1)])
    dead_links = set([(0, 0, 0, Links.north)])

    q.add_machine("foo", 4, 3, dead_boards=dead_boards, dead_links=dead_links)

    # Should have re-queued everything
    assert len(_regenerate_queues.mock_calls) == 1

    # Default tag should be set
    assert q._machines["foo"].tags == set(["default"])

    # Allocator should be sensible
    assert q._machines["foo"].allocator.width == 4
    assert q._machines["foo"].allocator.height == 3
    assert q._machines["foo"].allocator.dead_boards is dead_boards
    assert q._machines["foo"].allocator.dead_links is dead_links

    # Should be able to specify custom tags
    q.add_machine("bar", 1, 2, tags=set(["pie", "chips"]))
    assert q._machines["bar"].tags == set(["pie", "chips"])

    # Should have re-queued everything again
    assert len(_regenerate_queues.mock_calls) == 2

    # Should fail to create machine with same name
    with pytest.raises(ValueError):
        q.add_machine("foo", 1, 2)

    # Should have done nothing
    assert len(_regenerate_queues.mock_calls) == 2


def test_move_machine_to_end(q, on_allocate):
    # Create some machines in order
    q.add_machine("m0", 1, 1)
    q.add_machine("m1", 1, 1)
    q.add_machine("m2", 1, 1)

    # First job should go to first machine
    q.create_job(1, 1, job_id=10)
    assert len(on_allocate.mock_calls) == 1
    assert on_allocate.mock_calls[0][1][1] == "m0"

    # Reordering should make next job go on m2
    q.move_machine_to_end("m1")

    # First job should go to first machine
    on_allocate.reset_mock()
    q.create_job(1, 1, job_id=20)
    assert len(on_allocate.mock_calls) == 1
    assert on_allocate.mock_calls[0][1][1] == "m2"


def test_modify_machine(q, monkeypatch):
    _regenerate_queues = Mock(side_effect=q._regenerate_queues)
    monkeypatch.setattr(q, "_regenerate_queues", _regenerate_queues)

    # Create a machine to modify
    q.add_machine("foo", 4, 3)
    assert len(_regenerate_queues.mock_calls) == 1

    # Change the tags
    q.modify_machine("foo", tags=set(["pie", "chips"]))
    assert q._machines["foo"].tags == set(["pie", "chips"])
    assert q._machines["foo"].allocator.dead_boards == set()
    assert q._machines["foo"].allocator.dead_links == set()
    assert len(_regenerate_queues.mock_calls) == 2

    # Change the dead boards
    q.modify_machine("foo", dead_boards=set([(0, 0, 0)]))
    assert q._machines["foo"].tags == set(["pie", "chips"])
    assert q._machines["foo"].allocator.dead_boards == set([(0, 0, 0)])
    assert q._machines["foo"].allocator.dead_links == set()
    assert len(_regenerate_queues.mock_calls) == 3

    # Change the dead links
    q.modify_machine("foo", dead_links=set([(0, 0, 0, Links.north)]))
    assert q._machines["foo"].tags == set(["pie", "chips"])
    assert q._machines["foo"].allocator.dead_boards == set([(0, 0, 0)])
    assert q._machines["foo"].allocator.dead_links == \
        set([(0, 0, 0, Links.north)])
    assert len(_regenerate_queues.mock_calls) == 4

    # Change nothing!
    q.modify_machine("foo")
    assert q._machines["foo"].tags == set(["pie", "chips"])
    assert q._machines["foo"].allocator.dead_boards == set([(0, 0, 0)])
    assert q._machines["foo"].allocator.dead_links == \
        set([(0, 0, 0, Links.north)])
    assert len(_regenerate_queues.mock_calls) == 5


def test_remove_machine(q, on_free, on_cancel, monkeypatch):
    _regenerate_queues = Mock(side_effect=q._regenerate_queues)
    monkeypatch.setattr(q, "_regenerate_queues", _regenerate_queues)

    q.add_machine("empty", 1, 1)
    q.add_machine("no_queue", 1, 1)
    q.add_machine("with_queue", 1, 1)

    q.create_job(1, 1, machine="no_queue", job_id=10)
    q.create_job(1, 1, machine="with_queue", job_id=20)
    q.create_job(1, 1, machine="with_queue", job_id=30)

    job_30 = q._jobs[30]

    _regenerate_queues.reset_mock()

    # Removing a machine with nothing running or queued on it should be fine
    q.remove_machine("empty")
    assert len(_regenerate_queues.mock_calls) == 1
    assert len(on_free.mock_calls) == 0
    assert len(on_cancel.mock_calls) == 0
    assert set(q._machines) == set(["no_queue", "with_queue"])
    assert set(q._jobs) == set([10, 20, 30])
    assert list(q._machines["no_queue"].queue) == []
    assert list(q._machines["with_queue"].queue) == [job_30]

    # Removing a machine with a job on it should free the job
    q.remove_machine("no_queue")
    assert len(_regenerate_queues.mock_calls) == 2
    on_free.assert_called_once_with(10, "Machine removed.")
    on_free.reset_mock()
    assert len(on_cancel.mock_calls) == 0
    assert set(q._machines) == set(["with_queue"])
    assert set(q._jobs) == set([20, 30])
    assert list(q._machines["with_queue"].queue) == [job_30]

    # Removing a machine with a job and a queue on it should free the job and
    # cancel the queued entries
    q.remove_machine("with_queue")
    assert len(_regenerate_queues.mock_calls) == 3
    on_free.assert_called_once_with(20, "Machine removed.")
    on_cancel.assert_called_once_with(30, "No suitable machines available.")
    assert set(q._machines) == set()
    assert set(q._jobs) == set()


def test_atomic_machine_change(q, on_allocate, on_free, on_cancel):
    # When using the atomic machine-change context manager, machine changes
    # should be atomic and not result in jobs being cancelled due to
    # unfavourable intermediate states.
    q.add_machine("m0", 1, 1)
    q.create_job(1, 1, job_id=10)
    q.create_job(1, 1, job_id=20)
    q.create_job(1, 1, job_id=30)
    q.create_job(1, 1, job_id=40)

    on_allocate.reset_mock()

    # Atomically change the system (and make sure the context manager can be
    # nested)
    with q:
        with q:
            q.remove_machine("m0")
        q.add_machine("m1", 2, 1)

    # Only the running job should have been cancelled
    assert len(on_cancel.mock_calls) == 0
    on_free.assert_called_once_with(10, "Machine removed.")
    assert len(on_allocate.mock_calls) == 2
    assert on_allocate.mock_calls[0][1][0] == 20
    assert on_allocate.mock_calls[1][1][0] == 30


def test_create_job(q, m, monkeypatch):
    # Make sure job creation produces the appropriate internal state for the
    # job
    _enqueue_job = Mock(side_effect=q._enqueue_job)
    monkeypatch.setattr(q, "_enqueue_job", _enqueue_job)

    q.create_job(3, 4, require_torus=True, job_id=123)

    assert len(_enqueue_job.mock_calls) == 1

    assert q._jobs[123].id == 123

    assert q._jobs[123].machine_name is None
    assert q._jobs[123].tags == set(["default"])

    assert q._jobs[123].args == (3, 4)
    assert q._jobs[123].kwargs == {"require_torus": True}

    # Should be able to specify the tags to use
    q.create_job(tags=set(["pie", "chips"]), job_id=124)
    assert len(_enqueue_job.mock_calls) == 2
    assert q._jobs[124].machine_name is None
    assert q._jobs[124].tags == set(["pie", "chips"])

    # Should be able to specify the machine to use
    q.create_job(machine="m", job_id=125)
    assert len(_enqueue_job.mock_calls) == 3
    assert q._jobs[125].machine_name == "m"
    assert q._jobs[125].tags == set()

    # Should fail if job ID already in use
    with pytest.raises(ValueError):
        q.create_job(job_id=123)
    assert len(_enqueue_job.mock_calls) == 3

    # Should fail if no job ID is given
    with pytest.raises(TypeError):
        q.create_job()
    assert len(_enqueue_job.mock_calls) == 3

    # Should fail if both tags and a machine name is given
    with pytest.raises(TypeError):
        q.create_job(job_id=10, machine="m", tags=set(["pie"]))
    assert len(_enqueue_job.mock_calls) == 3


def test_atomic_create_job(q, on_allocate, on_free, on_cancel):
    # If inside the atomic context manager, jobs should not be scheduled until
    # we leave the context manager.
    with q:
        # Creating job with no machine would normally fail but since we're
        # postponing queue management until we've created the machine, this
        # won't be a problem
        q.create_job(job_id=10, machine="m1")
        assert len(on_allocate.mock_calls) == 0
        assert len(on_free.mock_calls) == 0
        assert len(on_cancel.mock_calls) == 0

        q.add_machine("m0", 1, 1)
        q.remove_machine("m0")
        q.add_machine("m1", 2, 1)

    on_allocate.assert_called_once_with(
        10, "m1", set([(0, 0, 0)]), set((0, 0, 0, link) for link in Links),
        False)
    assert len(on_free.mock_calls) == 0
    assert len(on_cancel.mock_calls) == 0


def test_enqueue_job(q, on_allocate, on_cancel):
    # Make sure that the _enqueue_job method does what it should (tested via
    # calls to create_job).

    q.add_machine("1x4", 1, 4)
    q.add_machine("1x2", 1, 2)
    q.add_machine("1x5", 1, 5)
    q.add_machine("1x2_pie", 1, 2, tags=set(["pie", "chips"]))
    q.add_machine("1x2_curry", 1, 2, tags=set(["curry", "chips"]))

    # If we create a 1x1 job we should get put on the first available machine.
    # Also verifies the call to on_allocate.
    q.create_job(1, 1, job_id=100)
    on_allocate.assert_called_once_with(
        100, "1x4",
        set((0, 0, z) for z in range(3)),
        set((0, 0, z, link)
            for z in range(3)
            for link in Links
            if board_down_link(0, 0, z, link, 1, 4)[:2] != (0, 0)),
        True)
    on_allocate.reset_mock()

    # We should make use of the first machine which fits and skip over anything
    # which is full or too small.
    q.create_job(1, 4, job_id=110)
    assert len(on_allocate.mock_calls) == 1
    assert on_allocate.mock_calls[0][1][0] == 110
    assert on_allocate.mock_calls[0][1][1] == "1x5"
    on_allocate.reset_mock()

    # We should still be able to fit small things in the first available
    # machine
    q.create_job(1, 3, job_id=120)
    assert len(on_allocate.mock_calls) == 1
    assert on_allocate.mock_calls[0][1][0] == 120
    assert on_allocate.mock_calls[0][1][1] == "1x4"
    on_allocate.reset_mock()

    # Should be able to specify a specific machine
    q.create_job(1, 1, job_id=130, machine="1x5")
    assert len(on_allocate.mock_calls) == 1
    assert on_allocate.mock_calls[0][1][0] == 130
    assert on_allocate.mock_calls[0][1][1] == "1x5"
    on_allocate.reset_mock()

    # Fill up the last available (default-tagged) free slot
    q.create_job(1, 2, job_id=140)
    assert len(on_allocate.mock_calls) == 1
    assert on_allocate.mock_calls[0][1][0] == 140
    assert on_allocate.mock_calls[0][1][1] == "1x2"
    on_allocate.reset_mock()

    # The next job created should get queued on all default-tagged machines
    q.create_job(1, 1, job_id=150)
    assert len(on_allocate.mock_calls) == 0
    assert list(q._machines["1x4"].queue) == [q._jobs[150]]
    assert list(q._machines["1x2"].queue) == [q._jobs[150]]
    assert list(q._machines["1x5"].queue) == [q._jobs[150]]
    assert list(q._machines["1x2_pie"].queue) == []
    assert list(q._machines["1x2_curry"].queue) == []

    # Should be able to create jobs on machines by tag, choosing the first one
    # when possible
    q.create_job(1, 1, job_id=160, tags=set(["chips"]))
    assert len(on_allocate.mock_calls) == 1
    assert on_allocate.mock_calls[0][1][0] == 160
    assert on_allocate.mock_calls[0][1][1] == "1x2_pie"
    on_allocate.reset_mock()

    # Should be able to create jobs on machines by tag, selecting by multiple
    # tags at once.
    q.create_job(1, 1, job_id=170, tags=set(["curry", "chips"]))
    assert len(on_allocate.mock_calls) == 1
    assert on_allocate.mock_calls[0][1][0] == 170
    assert on_allocate.mock_calls[0][1][1] == "1x2_curry"
    on_allocate.reset_mock()

    # Should be able to queue on tagged things
    q.create_job(1, 2, job_id=180, tags=set(["chips"]))
    assert list(q._machines["1x4"].queue) == [q._jobs[150]]
    assert list(q._machines["1x2"].queue) == [q._jobs[150]]
    assert list(q._machines["1x5"].queue) == [q._jobs[150]]
    assert list(q._machines["1x2_pie"].queue) == [q._jobs[180]]
    assert list(q._machines["1x2_curry"].queue) == [q._jobs[180]]

    # Should be able to queue on all things
    q.create_job(1, 2, job_id=190, tags=set())
    assert list(q._machines["1x4"].queue) == [q._jobs[150], q._jobs[190]]
    assert list(q._machines["1x2"].queue) == [q._jobs[150], q._jobs[190]]
    assert list(q._machines["1x5"].queue) == [q._jobs[150], q._jobs[190]]
    assert list(q._machines["1x2_pie"].queue) == [q._jobs[180], q._jobs[190]]
    assert list(q._machines["1x2_curry"].queue) == [q._jobs[180], q._jobs[190]]

    # Should fail if we specify a machine which doesn't exist.
    q.create_job(1, 1, job_id=200, machine="xxx")
    on_cancel.assert_called_once_with(200, "No suitable machines available.")
    on_cancel.reset_mock()

    # Should fail if we specify a tag doesn't exist.
    q.create_job(1, 1, job_id=210, tags=set(["xxx"]))
    on_cancel.assert_called_once_with(210, "No suitable machines available.")
    on_cancel.reset_mock()

    # Should fail if we specify impossible requirements for any machine
    q.create_job(10, 10, job_id=220)
    on_cancel.assert_called_once_with(220, "No suitable machines available.")
    on_cancel.reset_mock()


def test_destroy_job(q, m, on_free, on_cancel):
    # Make sure job removal works, also tests _process_queue correctly advances
    # job queues for machines.

    # This job should end up getting allocated
    q.create_job(3, 4, job_id=10)
    # These jobs should end up getting queued
    q.create_job(3, 4, job_id=20)
    q.create_job(3, 4, job_id=30)
    q.create_job(3, 4, job_id=40)

    assert set(q._jobs) == set([10, 20, 30, 40])

    job_20 = q._jobs[20]
    job_30 = q._jobs[30]
    job_40 = q._jobs[40]

    # If we cancel job 30, it should be marked as not-pending (note that it
    # remains in the job queue since we only remove stale queue entries at the
    # head of the queue).
    q.destroy_job(30)
    on_cancel.assert_called_once_with(30, None)
    assert set(q._jobs) == set([10, 20, 40])
    assert list(q._machines[m].queue) == [job_20, job_30, job_40]
    assert job_30.pending is False
    on_cancel.reset_mock()

    # If we cancel job 20, _process_queue will remove 20 and 30 from the job
    # queues since they are at the head of the queue and marked as no-longer
    # pending.
    q.destroy_job(20)
    on_cancel.assert_called_once_with(20, None)
    assert set(q._jobs) == set([10, 40])
    assert list(q._machines[m].queue) == [job_40]
    assert job_20.pending is False
    on_cancel.reset_mock()

    # If we free job 10 it should be cleanly removed and job 40 should be
    # started.
    q.destroy_job(10)
    on_free.assert_called_once_with(10, None)
    on_free.reset_mock()
    assert list(q._machines[m].queue) == []
    assert set(q._jobs) == set([40])
    assert job_40.pending is False

    # Finally, freeing the last job should succeed.
    q.destroy_job(40)
    on_free.assert_called_once_with(40, None)
    on_free.reset_mock()
    assert list(q._machines[m].queue) == []
    assert set(q._jobs) == set([])


def test_regenerate_queues(q):
    # Make sure that queues are rebuilt correctly
    q.add_machine("m0", 1, 1)

    # Add jobs such that we have three queued jobs and one allocated.
    q.create_job(1, 1, job_id=10)
    q.create_job(1, 1, job_id=20)
    q.create_job(1, 1, job_id=30)
    q.create_job(1, 1, job_id=40)

    job_20 = q._jobs[20]
    job_30 = q._jobs[30]
    job_40 = q._jobs[40]

    assert list(q._machines["m0"].queue) == [job_20, job_30, job_40]

    # If we corrupt the job queue the _regenerate_queues command should fix
    # it. This is not a real use case but it validates that the command does
    # what it is supposed to.
    q._machines["m0"].queue.clear()
    q._machines["m0"].queue.append("foo")
    q._regenerate_queues()
    assert list(q._machines["m0"].queue) == [job_20, job_30, job_40]

    # If we add a new machine, the _regenerate_queues command should result in
    # a new job starting and the remaining jobs being posted to both queues.
    q.add_machine("m1", 1, 1)
    assert list(q._machines["m0"].queue) == [job_30, job_40]
    assert list(q._machines["m1"].queue) == [job_30, job_40]
