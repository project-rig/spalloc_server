SpiNNaker Partitioning Server (``spinn_partition_server``)
==========================================================

The SpiNNaker Partitioning Server is a Python program built on Rig_ designed to
facilitate the sharing and partitioning of large SpiNNaker_ machines.

.. _SpiNNaker: http://apt.cs.manchester.ac.uk/projects/SpiNNaker/
.. _Rig: https://github.com/project-rig/rig

This server enables users to request SpiNNaker machines of various shapes and
sizes. These requests are queued and allocated to available SpiNNaker machines,
partitioning large SpiNNaker machines into smaller ones if possible. When faced
with the numerous research problems of optimal packing and scheduling of
allocations, this implementation uses the 'simplest mechanism that could
possibly work'. With this said, the server intends to be 'production ready' in
the sense that it is intended to be robust against real end users and routine
maintenance requirements.


Server Management and Configuration
-----------------------------------

Essential reading for system administrators. The following documentation
describes how to configure and manage a SpiNNaker Partitioning Server instance.

.. toctree::
    :maxdepth: 2
    
    management

Communication Protocol
----------------------

The following documentation describes the network protocol which clients must
use to communicate with the server.

.. toctree::
    :maxdepth: 2
    
    protocol

Internals Overview
------------------

Essential reading for developers and maintainers. The following documentation
describes the internal architecture of the SpiNNaker Partitioning Server and
should serve as a guide to those wishing to extend its functionality.

.. toctree::
    :maxdepth: 2
    
    internals

Indicies and Tables
-------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

