SpiNNaker Machine Partitioning and Allocation Server (``spalloc_server``)
=========================================================================

.. image:: https://img.shields.io/pypi/v/spalloc_server.svg?style=flat
   :alt: PyPi version
   :target: https://pypi.python.org/pypi/spalloc_server/
.. image:: https://readthedocs.org/projects/spalloc_server/badge/?version=stable
   :alt: Documentation
   :target: http://spalloc_server.readthedocs.org/
.. image:: https://travis-ci.org/project-rig/spalloc_server.svg?branch=master
   :alt: Build Status
   :target: https://travis-ci.org/project-rig/spalloc_server
.. image:: https://coveralls.io/repos/project-rig/spalloc_server/badge.svg?branch=master
   :alt: Coverage Status
   :target: https://coveralls.io/r/project-rig/spalloc_server?branch=master

A SpiNNaker machine management application which manages the partitioning and
allocation of large SpiNNaker machines into smaller fragments for many
simultaneous users.

This package just contains a server which exposes a simple TCP interface to
clients to enable them to request hardware.

The `documentation <http://spalloc-server.readthedocs.org/>`_ describes the
process of configuring and running servers, the simple JSON-based client
protocol and an overview of the server's implementation.
