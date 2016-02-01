SpiNN-Partition
===============

A SpiNNaker machine management application which manages the partitioning of
large SpiNNaker machines into smaller fragments.

This application is split into two parts: a server and a client. The server is
a long-running process which is responsible for managing a specific SpiNNaker
machine composed of a number of SpiNN-5 boards. The client application is used
by end-users to request machine resources.

XXX
---

A large SpiNNaker machine is treated as a rectangular (square) grid of
SpiNNaker boards. Since SpiNNaker boards do not tile a 2D space in a
regular, complete pattern, the grid is actually in terms of triads of
boards. A triad of boards is three SpiNN-5 boards arranged like this::

     ___
    /   \___
    \___/   \
    /   \___/
    \___/

These tile along the X axis like this::

     ___     ___     ___     ___
    /   \___/   \___/   \___/   \___
    \___/   \___/   \___/   \___/   \
    /   \___/   \___/   \___/   \___/
    \___/   \___/   \___/   \___/

And then along the Y axis like this:

     ___     ___     ___     ___
    /   \___/   \___/   \___/   \___
    \___/   \___/   \___/   \___/   \
    /   \___/   \___/   \___/   \___/
    \___/ 2 \___/ 2 \___/   \___/   \___
        \___/ 1 \___/ 1 \___/   \___/   \
        / 0 \___/ 0 \___/   \___/   \___/
        \___/ 2 \___/ 2 \___/   \___/   \___
            \___/ 1 \___/ 1 \___/   \___/   \
            / 0 \___/ 0 \___/   \___/   \___/
            \___/   \___/   \___/   \___/

This shape is sheared to end up with a regular square grid tiling of
triads. All allocations are made at the granularity of triads.
Any finer-grained allocation must be carried out as a post-processing step.
