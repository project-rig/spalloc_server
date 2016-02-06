"""Defines a SpiNNaker machine on which jobs may be allocated."""

from collections import namedtuple

from six import iteritems


class Machine(namedtuple("Machine", "name,tags,width,height,"
                                    "dead_boards,dead_links,"
                                    "board_locations,"
                                    "bmp_ips,spinnaker_ips")):
    """Defines a SpiNNaker machine.
    
    Attributes
    ----------
    name : str
        The name of the machine.
    tags : set([str, ...])
        A set of tags which jobs may use to filter machines by.
    width, height : int
        The dimensions of the machine in triads of boards.
    dead_boards : set([(x, y, z), ...])
        The coordinates of all dead boards in the machine.
    dead_links : set([(x, y, z, link), ...])
        The coordinates of all dead links in the machine. Links to dead
        boards are implicitly dead and may or may not be included in this
        set.
    board_locations : {(x, y, z): (c, f, b), ...}
        Lookup from board coordinate to its physical in a SpiNNaker
        machine in terms of cabinet, frame and board position.
    bmp_ips : {(c, f): hostname, ...}
        The IP address of a BMP in every frame of the machine.
    spinnaker_ips : {(x, y, z): hostname, ...}
        For every board gives the IP address of the SpiNNaker board's
        Ethernet connected chip.
    """
    
    def __new__(cls, name, tags, width, height,
                dead_boards, dead_links, board_locations,
                bmp_ips, spinnaker_ips):
        
        # Make sure the set-type arguments are the correct type...
        if not isinstance(tags, set):
            raise TypeError("tags should be a set.")
        if not isinstance(dead_boards, set):
            raise TypeError("dead_boards should be a set.")
        if not isinstance(dead_links, set):
            raise TypeError("dead_links should be a set.")
        
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
                raise ValueError("Board location given for board not in system "
                                 "({}, {}, {}).".format(x, y, z))
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
