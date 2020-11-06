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

###############################################################################
# This module creates mock functions and classes for Sphinx-autodoc to scan and
# document based on the commands implemented in the server object.
###############################################################################

"""The commands supported by the server are enumerated below and expressed in
the form of Python functions.
"""

from six import iteritems
# from functools import wraps
from inspect import formatargspec
try:
    from inspect import getfullargspec
except ImportError:
    # Python 2.7 hack
    from inspect import getargspec as getfullargspec


from spalloc_server.server import _COMMANDS
from spalloc_server.controller import JobState as _JobState

###############################################################################
# Document commands
###############################################################################

# This module contains fake methods corresponding with the commands in the
# server which sphinx-autodoc will pick-up and display as documentation.
for name, f in iteritems(_COMMANDS):
    # Create a fake (but unique) function to document
    globals()[name] = (lambda: 1)

    # Get the arguments of the command and strip out the method 'self' argument
    # and the internally used 'client' argument.
    argspec = getfullargspec(f)
    if "self" in argspec.args:
        argspec.args.remove("self")
    if "client" in argspec.args:
        argspec.args.remove("client")
    if "_client" in argspec.args:
        argspec.args.remove("_client")

    # Modify the docstring to include the modified function prototype and to
    # modify references to other commands from being method references to
    # function references.
    globals()[name].__doc__ = "{}{}\n{}".format(
        name, formatargspec(*argspec),
        f.__doc__.replace(":py:meth:`.", ":py:func:`.")
        .replace("`~spalloc_server.controller.JobState", "`.JobState")
    )


###############################################################################
# Document job states
###############################################################################

# A 'fake' JobState class which simply enumerates the job IDs in its docstring
_JobState_doc = """
A job may be in any of the following (numbered) states.

======  =====
Number  State
======  =====
"""
for state in _JobState:
    _JobState_doc += ("{:<6}  :py:attr:`{} "
                      "<spalloc_server.controller.JobState.{}>`\n"
                      "".format(int(state), state.name, state.name))
_JobState_doc += """
======  =====
"""


class JobState(object):
    __doc__ = _JobState_doc


###############################################################################
# Make sure Sphinx picks everything up
###############################################################################

__all__ = list(_COMMANDS) + ["JobState"]
