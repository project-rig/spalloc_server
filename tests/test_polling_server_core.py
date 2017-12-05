from spalloc_server.polling_server_core import PollingServerCore
import socket
import time


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
