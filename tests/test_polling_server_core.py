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

import socket
import time
from spalloc_server.polling_server_core import PollingServerCore


def test_ready_channels_timeout():
    polling_server_core = PollingServerCore()
    server_socket = polling_server_core._open_server_socket("localhost", 0)
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_socket.connect(server_socket.getsockname())
    client, _ = server_socket.accept()
    polling_server_core.register_channel(client)
    start_time = time.time()
    channels = list(polling_server_core.ready_channels(1.0))
    assert len(channels) == 0
    end_time = time.time()
    assert int(end_time - start_time) == 1
