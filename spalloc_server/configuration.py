"""A configuration file is used to describe the machines which are to be
managed.  Configuration files are Python scripts which define a global
``configuration`` variable which is an instance of the
:py:class:`.Configuration` class.

.. note::

    Everything in :py:mod:`spalloc_server.configuration` and
    :py:mod:`spalloc_server.coordinates` modules is implicitly imported
    into the namespace of the config file.

A minimal (though useless) configuration file would look like so::

    configuration = Configuration()

This causes the server to listen on all interfaces on the default port but does
not define any machines for the server to manage. As a result, this server will
cancel all jobs sent to it for lack of a suitable machine. See the
:py:class:`.Configuration` class for a description of all available
configuration options and default values.

Machines are defined using :py:class:`.Machine` objects. These specify the
dimensions, broken boards and links, physical layout and IP addresses of a
SpiNNaker machine. All machines are presumed to be interconnected in a valid
hexagonal torus topology constructed from a rectangular array of triads of
boards. (See also :py:mod:`spalloc_server.coordinates` for details of
the coordinate systems used when referring to boards.)

Defining Machines
`````````````````

Since defining machines completely by hand can be quite verbose (see example
below), a some convenience functions are provided to deal with the common case
of machines constructed in the standard manner.

To define an isolated single-board machine, the
:py:meth:`.Machine.single_board` constructor may be used as follows::

    m = Machine.single_board("my-board",
                             bmp_ip="spinn-board-bmp",
                             spinnaker_ip="spinn-board")
    configuration = Configuration(machines=[m])

Most multi-board systems follow a standardised IP addressing scheme and have
their physical layout defined by `SpiNNer
<http://spinner.readthedocs.org/en/stable>`_. The
:py:func:`.board_locations_from_spinner` function reads CSV files produced by
`spinner-ethernet-chips
<http://spinner.readthedocs.org/en/stable/spinner-ethernet-chips.html>`_
describing machine layouts and the :py:meth:`.Machine.with_standard_ips`
constructor produces :py:class:`.Machine`\ s with IP addresses based on the
standard IP addressing scheme. These may be used together like so::

    # spinner-ethernet-chips -n 1200 > ethernet_chips.csv
    m = Machine.with_standard_ips(
        "million-core-machine",
        board_locations=board_locations_from_spinner("ethernet_chips.csv"),
        base_ip="10.2.0.0",
    )
    configuration = Configuration(machines=[m])

If neither of the above convenience functions apply to your machine, you can
also explicitly define your machine's parameters. (Be sure to read about the
:py:mod:`~spalloc_server.coordinates` used when referring to boards.)
For example, a desktop 3-board machine may look something like::

    m = Machine(name="my-three-board-machine",
                board_locations={
                    #X  Y  Z    C  F  B
                    (0, 0, 0): (0, 0, 0),
                    (0, 0, 1): (0, 0, 2),
                    (0, 0, 2): (0, 0, 5),
                },
                # Just one BMP
                bmp_ips={
                    #C  F
                    (0, 0): "192.168.240.0",
                },
                # Each SpiNNaker board has an IP
                spinnaker_ips={
                    #X  Y  Z
                    (0, 0, 0): "192.168.240.1",
                    (0, 0, 1): "192.168.240.17",
                    (0, 0, 2): "192.168.240.41",
                })
    configuration = Configuration(machines=[m])

Remember, since the configuration file is just a normal Python file, you can
use any code you like to pragmatically specify machines, etc. which you use.

Configuration File API Reference
````````````````````````````````
"""

from collections import namedtuple

import re
import csv

from itertools import chain

from six import iteritems, itervalues

from spalloc_server.coordinates import chip_to_board


class Configuration(namedtuple("Configuration",
                               "machines,port,ip,timeout_check_interval,"
                               "max_retired_jobs")):
    """Defines the configuration of a server.

    Parameters
    ----------
    machines : [:py:class:`~.Machine`, ...]
        The list of machines, highest priority first, the server is to
        manage. (Default: [])
    port : int
        The port number the server should listen on. (Default: 22244)
    ip : str
        The IP the server should listen on. (Default: "", i.e. all interfaces)
    timeout_check_interval : float
        The number of seconds between the server's checks for job timeouts.
        (Default: 5.0)
    max_retired_jobs : int
        The number of retired jobs to keep records of. (Default: 1200)
    """

    def __new__(cls, machines=[], port=22244, ip="",
                timeout_check_interval=5.0,
                max_retired_jobs=1200):
        # Validate machine definitions
        used_names = set()
        used_bmp_ips = set()
        used_spinnaker_ips = set()
        for m in machines:
            # Typecheck...
            if not isinstance(m, Machine):
                raise TypeError("All machines must be of type Machine.")

            # Machine names must be unique
            if m.name in used_names:
                raise ValueError("Machine name '{}' used multiple "
                                 "times.".format(m.name))
            used_names.add(m.name)

            # All BMP IPs must be unique
            for bmp_ip in itervalues(m.bmp_ips):
                if bmp_ip in used_bmp_ips:
                    raise ValueError("BMP IP '{}' used multiple "
                                     "times.".format(bmp_ip))
                used_bmp_ips.add(bmp_ip)

            # All SpiNNaker IPs must be unique
            for spinnaker_ip in itervalues(m.spinnaker_ips):
                if spinnaker_ip in used_spinnaker_ips:
                    raise ValueError("SpiNNaker IP '{}' used multiple "
                                     "times.".format(spinnaker_ip))
                used_spinnaker_ips.add(spinnaker_ip)

        return super(Configuration, cls).__new__(cls, machines, port, ip,
                                                 timeout_check_interval,
                                                 max_retired_jobs)


class Machine(namedtuple("Machine", "name,tags,width,height,"
                                    "dead_boards,dead_links,"
                                    "board_locations,"
                                    "bmp_ips,spinnaker_ips")):
    """Defines a SpiNNaker machine.

    Parameters
    ----------
    name : str
        The name of the machine.
    tags : set([str, ...])
        A set of tags which jobs may use to filter machines by. Note that by
        default jobs are assigned the 'default' tag and thus machines probably
        ought have this tag too.
    width, height : int
        The dimensions of the machine in triads of boards. If omitted, these
        are inferred from the boards defined in board_locations and
        dead_boards.
    dead_boards : set([(x, y, z), ...])
        The board coordinates of all dead boards in the machine.
    dead_links : set([(x, y, z, :py:class:`~rig.links.Links`), ...])
        The board coordinates of all dead links in the machine. Links to dead
        boards are implicitly dead and may or may not be included in this set.
    board_locations : {(x, y, z): (c, f, b), ...}
        Lookup from board coordinate to its physical in a SpiNNaker
        machine in terms of cabinet, frame and board position. Must give the
        coordinates of *all* working boards.
    bmp_ips : {(c, f): hostname, ...}
        The IP address of a BMP in every frame of the machine which contains
        working boards.
    spinnaker_ips : {(x, y, z): hostname, ...}
        For every working board gives the IP address of the SpiNNaker board's
        Ethernet connected chip.
    """

    def __new__(cls, name, tags=set(["default"]),
                width=None, height=None,
                dead_boards=set(), dead_links=set(),
                board_locations={},
                bmp_ips={},
                spinnaker_ips={}):

        # Make sure the set-type arguments are the correct type...
        if not isinstance(tags, set):
            raise TypeError("tags should be a set.")
        if not isinstance(dead_boards, set):
            raise TypeError("dead_boards should be a set.")
        if not isinstance(dead_links, set):
            raise TypeError("dead_links should be a set.")

        # If not specified, infer the dimensions of the system
        if width is None and height is None:
            width, height, _ = map(max, zip(*chain(board_locations,
                                                   dead_boards)))
            width += 1
            height += 1
        if width is None or height is None:
            raise TypeError(
                "Both or neither of width and height must be specified.")

        # All dead boards and links should be within the size of the system
        for x, y, z in dead_boards:
            if not (0 <= x < width and
                    0 <= y < height and
                    0 <= z < 3):
                raise ValueError("Dead board ({}, {}, {}) "
                                 "outside system.".format(x, y, z))
        for x, y, z, link in dead_links:
            if not (0 <= x < width and
                    0 <= y < height and
                    0 <= z < 3):
                raise ValueError("Dead link ({}, {}, {}) "
                                 "outside system.".format(x, y, z))

        # All board locations must be sensible
        locations = set()
        for (x, y, z), (c, f, b) in iteritems(board_locations):
            # Board should be within system
            if not (0 <= x < width and
                    0 <= y < height and
                    0 <= z < 3):
                raise ValueError("Board location given for board "
                                 "not in system ({}, {}, {}).".format(x, y, z))
            # No two boards should be in the same location
            if (c, f, b) in locations:
                raise ValueError("Multiple boards given location "
                                 "c:{}, f:{}, b:{}.".format(c, f, b))
            locations.add((c, f, b))

        # All boards must have their locations specified, unless they are
        # dead (in which case this is optional)
        live_bords = set((x, y, z)
                         for x in range(width)
                         for y in range(height)
                         for z in range(3)
                         if (x, y, z) not in dead_boards)
        missing_boards = live_bords - set(board_locations)
        if missing_boards:
            raise ValueError(
                "Board locations missing for {}".format(missing_boards))

        # BMP IPs should be given for all frames which have been used
        frames = set((c, f) for c, f, b in locations)
        missing_bmp_ips = frames - set(bmp_ips)
        if missing_bmp_ips:
            raise ValueError(
                "BMP IPs not given for frames {}".format(missing_bmp_ips))

        # SpiNNaker IPs should be given for all live boards
        missing_ips = live_bords - set(spinnaker_ips)
        if missing_ips:
            raise ValueError(
                "SpiNNaker IPs not given for boards {}".format(missing_ips))

        return super(Machine, cls).__new__(cls, name, tags, width, height,
                                           dead_boards, dead_links,
                                           board_locations,
                                           bmp_ips, spinnaker_ips)

    @classmethod
    def single_board(cls, name, tags=set(["default"]),
                     bmp_ip=None, spinnaker_ip=None):
        """Convenience constructor. Construct a :py:class:`.Machine`
        representing a single SpiNNaker board.

        Parameters
        ----------
        name : str
            The name of the machine
        tags : set([tag, ...])
            The tags to assign to the machine.
        bmp_ip : str
            The hostname of the BMP controlling the board.
        spinnaker_ip : str
            The hostname of the SpiNNaker board.
        """
        if bmp_ip is None:
            raise TypeError("bmp_ip must be given.")
        if spinnaker_ip is None:
            raise TypeError("spinnaker_ip must be given.")

        return cls(name, tags, 1, 1,
                   dead_boards=set([(0, 0, 1), (0, 0, 2)]), dead_links=set(),
                   board_locations={(0, 0, 0): (0, 0, 0)},
                   bmp_ips={(0, 0): bmp_ip},
                   spinnaker_ips={(0, 0, 0): spinnaker_ip})

    @classmethod
    def with_standard_ips(cls, name, tags=set(["default"]),
                          width=None, height=None,
                          dead_boards=set(), dead_links=set(),
                          board_locations={},
                          base_ip="192.168.0.0",
                          cabinet_stride="0.0.5.0",
                          frame_stride="0.0.1.0",
                          board_stride="0.0.0.8",
                          bmp_offset="0.0.0.0",
                          spinnaker_offset="0.0.0.1"):
        """Convenience constructor. Construct a :py:class:`.Machine` which
        infers IP addresses of the form conventionally used by SpiNNaker
        installations.

        In standard SpiNNaker installations, IP addresses are allocated in a
        regular fashion as described below.

        IP addresses for a particular machine are allocated within an address
        range, e.g. 192.168.0.0 - 192.168.255.255.

        This address range is then subdivided into address ranges for each
        frame, for example:

        * Cabinet 0, Frame 0: 192.168.0.0 - 192.168.0.255
        * Cabinet 0, Frame 1: 192.168.1.0 - 192.168.1.255
        * Cabinet 0, Frame 2: 192.168.2.0 - 192.168.2.255
        * Cabinet 0, Frame 3: 192.168.3.0 - 192.168.3.255
        * Cabinet 0, Frame 4: 192.168.4.0 - 192.168.4.255
        * Cabinet 1, Frame 0: 192.168.5.0 - 192.168.5.255
        * ...

        Boards within a frame are each allocated their own range of IPs, for
        example:

        * Cabinet 0, Frame 0, Board 0: 192.168.0.0 - 192.168.0.7
        * Cabinet 0, Frame 0, Board 1: 192.168.0.8 - 192.168.0.15
        * Cabinet 0, Frame 0, Board 2: 192.168.0.16 - 192.168.0.23
        * ...

        Finally, the IP address of the BMP and Ethernet-connected SpiNNaker
        chip of each board is at some fixed offset within this range, for
        example:

        * Cabinet 0, Frame 0, Board 0, BMP: 192.168.0.0
        * Cabinet 0, Frame 0, Board 0, SpiNNaker: 192.168.0.1
        * Cabinet 0, Frame 0, Board 1, BMP: 192.168.0.8
        * Cabinet 0, Frame 0, Board 1, SpiNNaker: 192.168.0.9

        Finally, we assume that board 0's BMP is to be used as the BMP for
        controlling all boards in its frame.

        Parameters
        ----------
        name : str
            The name of the machine.
        tags : set([str, ...])
            A set of tags which jobs may use to filter machines by. Note that
            by default jobs are assigned the 'default' tag and thus machines
            probably ought have this tag too.
        width, height : int
            The dimensions of the machine in triads of boards. If omitted,
            these are inferred from the boards defined in board_locations and
            dead_boards.
        dead_boards : set([(x, y, z), ...])
            The board coordinates of all dead boards in the machine.
        dead_links : set([(x, y, z, :py:class:`~rig.links.Links`), ...])
            The board coordinates of all dead links in the machine. Links to
            dead boards are implicitly dead and may or may not be included in
            this set.
        board_locations : {(x, y, z): (c, f, b), ...}
            Lookup from board coordinate to its physical in a SpiNNaker machine
            in terms of cabinet, frame and board position. Must give the
            coordinates of *all* working boards.
        base_ip : str
            The IPv4 address from which the IP address range assigned to the
            machine starts.
        cabinet_stride : str
            The stride in IP addresses between individual cabinets, expressed
            as an IPv4 address.
        frame_stride : str
            The stride in IP addresses between individual frames within a
            cabinet, expressed as an IPv4 address.
        board_stride : str
            The stride in IP addresses between individual boards within a
            frame, expressed as an IPv4 address.
        bmp_offset : str
            The offset of a board's BMP IP from the start of a board's IP
            address range, expressed as an IPv4 address.
        spinnaker_offset : str
            The offset of a board's Ethernet-connected SpiNNaker chip IP from
            the start of a board's IP address range, expressed as an IPv4
            address.
        """

        def ip_to_int(ip):
            """Convert from string-based IP to a 32-bit integer."""
            match = re.match("^(\d+).(\d+).(\d+).(\d+)$", ip)
            if not match:
                raise ValueError("Malformed IPv4 address '{}'".format(ip))

            ip_int = 0
            for group in map(int, match.groups()):
                if group & ~0xFF:
                    raise ValueError("Malformed IPv4 address '{}'".format(ip))
                ip_int <<= 8
                ip_int |= group

            return ip_int

        def int_to_ip(ip_int):
            """Convert from 32-bit integer to string-based IP address."""
            return ".".join(str((ip_int >> b) & 0xFF)
                            for b in range(24, -8, -8))

        base_ip = ip_to_int(base_ip)
        cabinet_stride = ip_to_int(cabinet_stride)
        frame_stride = ip_to_int(frame_stride)
        board_stride = ip_to_int(board_stride)
        bmp_offset = ip_to_int(bmp_offset)
        spinnaker_offset = ip_to_int(spinnaker_offset)

        # Generate IP addresses for BMPs
        cabinets_and_frames = set((c, f) for c, f, b in
                                  itervalues(board_locations))
        bmp_ips = {(c, f): int_to_ip(base_ip +
                                     (cabinet_stride * c) +
                                     (frame_stride * f) +
                                     bmp_offset)
                   for (c, f) in cabinets_and_frames}

        # Generate IP addresses for SpiNNaker boards
        spinnaker_ips = {(x, y, z): int_to_ip(base_ip +
                                              (cabinet_stride * c) +
                                              (frame_stride * f) +
                                              (board_stride * b) +
                                              spinnaker_offset)
                         for (x, y, z), (c, f, b)
                         in iteritems(board_locations)}

        return cls(name, tags, width, height,
                   dead_boards=dead_boards, dead_links=dead_links,
                   board_locations=board_locations,
                   bmp_ips=bmp_ips, spinnaker_ips=spinnaker_ips)


def board_locations_from_spinner(filename):
    """Utility function which converts a CSV file produced by
    the `spinner-ethernet-chips
    <http://spinner.readthedocs.org/en/stable/spinner-ethernet-chips.html>`_
    utility into a ``board_locations`` dictionary suitable for defining
    :py:class:`.Machine` objects.

    Parameters
    ----------
    filename : str
        The name of a CSV file produced by spinner-ethernet-chips defining the
        relationship between Ethernet connected chip coordinates and physical
        board locations.

        This file is expected to have five columns (named in the first line of
        the CSV) named 'board', 'cabinet', 'frame', 'x', and 'y'.

    Returns
    -------
    {(x, y, z): (c, f, b), ...}
        The mapping from board coordinates to physical locations.
    """
    # Extract lookup from Ethernet connected chips to locations
    chip_locations = {}
    with open(filename, "r") as f:
        for entry in csv.DictReader(f):
            cfb = tuple(map(int, (entry["cabinet"],
                                  entry["frame"],
                                  entry["board"])))

            chip_xy = (int(entry["x"]), int(entry["y"]))

            assert chip_xy not in chip_locations
            chip_locations[chip_xy] = cfb

    # Infer machine dimensions
    max_x, max_y = map(max, zip(*chip_locations))
    width_triads = (max_x // 12) + 1
    height_triads = (max_y // 12) + 1

    # Convert from chip to board coordinates
    return {
        chip_to_board(chip_x, chip_y, width_triads * 12, height_triads * 12):
        cfb
        for (chip_x, chip_y), cfb in iteritems(chip_locations)
    }
