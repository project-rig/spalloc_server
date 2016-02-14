SpiNNaker Machine Partitioning and Allocation Server (``spalloc_server``)
=========================================================================

A SpiNNaker machine management application which manages the partitioning of
large SpiNNaker machines into smaller fragments.

This application is split into two parts: a server and a client. The server is
a long-running process which is responsible for managing a specific SpiNNaker
machine composed of a number of SpiNN-5 boards. The client application is used
by end-users to request machine resources.
