# Copyright (c) 2017-2019 The University of Manchester
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from spalloc_server.configuration import Machine


def simple_machine(name, width=1, height=2, tags=set(["default"]),
                   dead_boards=None, dead_links=None, ip_prefix=""):
    """Construct a simple machine with nothing broken etc."""
    return Machine(name=name, tags=tags, width=width, height=height,
                   dead_boards=dead_boards or set(),
                   dead_links=dead_links or set(),
                   board_locations={(x, y, z): (x, y, z)
                                    for x in range(width)
                                    for y in range(height)
                                    for z in range(3)},
                   bmp_ips={(x, y): "{}10.1.{}.{}".format(ip_prefix, x, y)
                            for x in range(width)
                            for y in range(height)},
                   spinnaker_ips={(x, y, z): "{}11.{}.{}.{}".format(
                                      ip_prefix, x, y, z)
                                  for x in range(width)
                                  for y in range(height)
                                  for z in range(3)})
