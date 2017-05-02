Server Management and Configuration
===================================

Installation and Requirements
-----------------------------

The server and its dependencies can be installed from PyPI like so::

    $ pip install spalloc_server

The server is currently only compatible with Linux due to its use of the
:py:func:`~select.poll` system call.


Operation
---------

The SpiNNaker partitioning server is started like so::

    $ spalloc_server configfile.cfg

The server is configured by a configuration file whose name is supplied as the
first command-line argument. See the :py:mod:`section below
<spalloc_server.configuration>` for an overview of the config file format.
You can trigger the server to reread its config file by sending a ``SIGHUP``
signal to the server process. When you do this (and where it is possible),
running jobs will continue to execute without interruption and queued jobs will
automatically be enqueued on any newly added machines.

Command-line usage
------------------

There is one mandatory argument, the name of the file holding the configuration
definitions, and a number of options that may be specified. 

    spalloc_server [OPTIONS] CONFIG_FILE [OPTIONS]

The options that may be given are:

  --version     Print the version of ``spalloc_server`` and quit.
  --quiet       Hide non-error output.
  --cold-start  Force a cold start, discarding any existing saved state.
  --port PORT   The network port that the service should listen on. Defaults
                to 22244. (The older style of setting the port through the
                configuration file is deprecated.)

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

.. automodule:: spalloc_server.configuration
    :members:
    :private-members:
    :special-members:

Coordinate systems
------------------

.. automodule:: spalloc_server.coordinates
    :members:
    :special-members:
