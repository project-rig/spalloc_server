Server Management and Configuration
===================================

Installation and Requirements
-----------------------------

The server and its dependencies can be installed from PyPI like so::

    $ pip install spinn_partition_server

The server is currently only compatible with Linux due to its use of the
:py:func:`~select.poll` system call and inotify subsystem.


Operation
---------

The SpiNNaker partitioning server is started like so::

    $ spinn-partition-server configfile.cfg

The server is configured by a configuration file whose name is supplied as the
first command-line argument. See the :py:mod:`section below
<spinn_partition_server.configuration>` for an overview of the config file
format. If the config file is modified while the server is running the new
configuration will be loaded on-the-fly and, when possible, running jobs will
continue to execute without interruption and queued jobs will automatically be
enqueued on any newly added machines.

Stopping the server
-------------------

The server runs until it is terminated by ``SIGINT``, i.e. pressing ctrl+c.
When terminated the server attempts to gracefully shut-down, completing any
outstanding board power-management commands and saving its state to disk. When
the server is subsequently restarted, the saved state is restored and operation
may continue as if the server had never been shut-down. Alternatively a
cold-start may be enforced using the ``--cold-start`` argument when starting
the server.

When the server is terminated, machines allocated to running jobs are left
powered on meaning that user's jobs are not interrupted by the partitioning
server being restarted. If it is necessary to perform major maintenance and
fully shut-down the server and all controlled SpiNNaker machines, the
recommended approach is to add the following line to the bottom of the server's
config file::

    configuration = Configuration()

This causes the server to believe all machines have been removed resulting in
all queued and running jobs being cancelled and all previously allocated
SpiNNaker boards being powered-down. To maximise the chances of all clients
realising their jobs have been cancelled, the server should then be left
running for a few minutes before being finally shut down.

Configuration file format
-------------------------

.. automodule:: spinn_partition_server.configuration
    :members:
    :private-members:
    :special-members:

Coordinate systems
------------------

.. automodule:: spinn_partition_server.coordinates
    :members:
    :special-members:
