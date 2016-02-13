Internals Guide
===============

This guide attempts (in some logical order) to explain the structure of the
SpiNNaker Partitioning Server code base and development process.

This guide starts with a brief development installation guide and then proceeds
to introduce the structure and internals of the SpiNNaker Partitioning Server.
We then introduce the coordinate systems used throughout the code base and
descend the hierarchy working down from the server logic through job
scheduling/queueing logic and finishing with the underlying board allocation
logic.

The 'meat' of the server can be broken down into the following principle
modules:

:py:mod:`spinn_partition_server.server`
    The top-level server implementation. Contains all server logic, command
    handling etc. Internally uses a
    :py:class:`~spinn_partition_server.controller.Controller` for scheduling and
    allocating jobs and managing SpiNNaker machines.
:py:mod:`spinn_partition_server.controller`
    Schedules and allocates jobs to machines (using
    :py:class:`~spinn_partition_server.job_queue.JobQueue`) and accordingly
    configures SpiNNaker hardware (using
    :py:class:`~spinn_partition_server.async_bmp_controller.AsyncBMPController`).
:py:mod:`spinn_partition_server.job_queue`
    Schedules and allocates jobs to machines. Attempts to allocate jobs to
    machines (using :py:class:`~spinn_partition_server.allocator.Allocator`\ s)
    and queues up and schedules jobs for which no machines are available.
:py:mod:`spinn_partition_server.allocator`
    Provides an 'alloc' like mechanism for allocating boards in
    a single SpiNNaker machine. Allocations occur at the granularity of
    individual SpiNNaker boards and high-level constraints on the allocations
    may be made (e.g. requiring a specific proportion of an allocation's links
    or boards to be functional). Internally the
    :py:class:`~spinn_partition_server.pack_tree.PackTree` 2D packing algorithm
    is used pack allocations at the coarser granularity of triads of boards.
:py:mod:`spinn_partition_server.pack_tree`
    A simple 'online' 2D packing algorithm/data structure. This is used to
    manage the allocation and freeing of rectangular regions of SpiNNaker
    machine at the granularity of triads of boards.
:py:mod:`spinn_partition_server.async_bmp_controller`
    An object which allows power and link configuration commands to be
    asynchronously queued, coalesced and executed on SpiNNaker board BMPs.

If you enjoy the MVC_ design pattern, the major pieces above can be categorised
approximately as:

* *Model*: :py:mod:`~spinn_partition_server.job_queue`,
  :py:mod:`~spinn_partition_server.allocator` and
  :py:mod:`~spinn_partition_server.pack_tree`.
* *View*: :py:mod:`~spinn_partition_server.server`
* *Controller*: :py:mod:`~spinn_partition_server.controller` and
  :py:mod:`~spinn_partition_server.async_bmp_controller`.

.. _MVC: https://en.wikipedia.org/wiki/Model%E2%80%93view%E2%80%93controller

A number of other smaller modules also exist as follows:

:py:mod:`spinn_partition_server.coordinates`
    Utility functions for working with the coordinate systems used to describe
    the locations of boards in large machines.
:py:mod:`spinn_partition_server.configuration`
    Objects used to define a configuration of the server, constructed by the
    user's config file.

The documentation below is presented in a recommended skimming/reading order
for new developers who wish to understand the code base.

Development Installation
------------------------

A development install is produced from the checked out Git repository as
usual::

    $ # Setup virtual env...
    $ python setup.py develop

Tests are run using pytest_::

    $ pip install -r requirements-test.txt
    $ py.test tests

.. _pytest: http://pytest.org/latest/

Documentation is built using Sphinx_::

    $ pip install -r requirements-docs.txt
    $ cd docs
    $ make html

.. _Sphinx: http://www.sphinx-doc.org/en/stable/

Server logic (:py:mod:`spinn_partition_server.server`)
------------------------------------------------------

.. automodule:: spinn_partition_server.server
    :members:
    :special-members:
    :private-members:

Top-level scheduling, allocation and hardware control logic (:py:mod:`spinn_partition_server.controller`)
---------------------------------------------------------------------------------------------------------

.. automodule:: spinn_partition_server.controller
    :members:
    :private-members:
    :special-members:

Job scheduling and allocation logic (:py:mod:`spinn_partition_server.job_queue`)
--------------------------------------------------------------------------------

.. automodule:: spinn_partition_server.job_queue
    :members:
    :private-members:
    :special-members:

Machine resource allocation at the board-granularity (:py:mod:`spinn_partition_server.allocator`)
-------------------------------------------------------------------------------------------------

.. automodule:: spinn_partition_server.allocator
    :members:
    :private-members:
    :special-members:

Machine resource allocation at the triad-granularity (:py:mod:`spinn_partition_server.pack_tree`)
-------------------------------------------------------------------------------------------------

.. automodule:: spinn_partition_server.pack_tree
    :members:
    :private-members:
    :special-members:

Asynchronous BMP Controller (:py:mod:`spinn_partition_server.async_bmp_controller`)
-----------------------------------------------------------------------------------

.. automodule:: spinn_partition_server.async_bmp_controller
    :members:
    :private-members:
    :special-members:
